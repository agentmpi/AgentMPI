"""Microbenchmarks with stub executors.

HPC papers separate two kinds of measurement, and this file is the first kind.
``osu_latency`` does not run a real application; it runs the smallest program
that isolates one cost term, so that the model's parameters can be fitted and
the algorithm selection rules checked. The second kind -- real applications at
scale -- lives in ``experiments/``, driven by actual LLM agents.

The stub executor here is a thread that calls the AgentMPI runtime directly,
with its own journal connection, and optionally sleeps for a configured interval
wherever a real agent would think. That gives us three things a real-agent run
cannot: hundreds of ranks, exact repeatability, and the ability to sweep payload
size and algorithm without spending model calls. What it deliberately does *not*
give us is any claim about agent behaviour -- every number in this file is a
statement about the *protocol runtime*, and the paper labels it as such.

Suites
------
``latency``
    Ping-pong at increasing payload size. Fits the alpha (per-message) and beta
    (per-token) terms of the cost model, and reports the context charged, which
    is where the eager/rendezvous crossover shows up.
``collectives``
    Every collective, every algorithm, sweeping P. Reports completion time,
    messages, and -- for reductions -- the number of operator applications on the
    critical path, which is the quantity the algorithm choice actually controls.
``context``
    Total context tokens consumed by broadcast and gather under each delivery
    discipline (inline, handle, budgeted view). This is the suite behind the
    paper's central context-cost claim.
``scaling``
    Barrier and allreduce from P=2 to P=1024.
``matching``
    Receive cost as a function of unexpected-queue depth: the agent analogue of
    the MPI match-list-length studies.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import collectives, p2p
from .core import Config, Ctx, bind, init_rank
from .journal import Journal
from .launcher import create as launcher_create
from .trace import _dist, summarize

# --------------------------------------------------------------------------
# Stub executor plumbing
# --------------------------------------------------------------------------


@dataclass
class RunResult:
    wall_s: float
    per_rank: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


def _job(workdir: Path, np: int, label: str, cfg: Optional[Config] = None) -> Path:
    root = workdir / label
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    launcher_create(root, np=np, label=label, cfg=cfg or Config(), fresh=True)
    return root


def _run_ranks(
    root: Path,
    np: int,
    body: Callable[[Ctx, Dict[str, Any]], None],
    *,
    comm: str = "world",
    max_threads: Optional[int] = None,
) -> RunResult:
    """Run ``body`` once per rank, concurrently, each with its own connection."""
    results: Dict[int, Dict[str, Any]] = {}
    errors: List[str] = []
    lock = threading.Lock()

    def worker(rank: int) -> None:
        out: Dict[str, Any] = {}
        try:
            j = Journal(root)
            init_rank(j, rank, agent_id=f"stub-{rank}")
            ctx = bind(j, rank=rank, comm=comm)
            t0 = time.perf_counter()
            body(ctx, out)
            out["wall_s"] = time.perf_counter() - t0
            j.close()
        except Exception as exc:  # noqa: BLE001 - benchmarks report, never crash
            with lock:
                errors.append(f"rank {rank}: {type(exc).__name__}: {exc}")
            out["error"] = str(exc)
            out.setdefault("traceback", traceback.format_exc(limit=4))
        with lock:
            results[rank] = out

    threads = [threading.Thread(target=worker, args=(r,), daemon=True) for r in range(np)]
    t0 = time.perf_counter()
    limit = max_threads or np
    started: List[threading.Thread] = []
    for t in threads:
        while sum(1 for x in started if x.is_alive()) >= limit:
            time.sleep(0.002)
        t.start()
        started.append(t)
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0
    j = Journal(root)
    s = summarize(j)
    j.close()
    return RunResult(wall_s=wall, per_rank=results, errors=errors, summary=s)


def _payload(tokens: int) -> str:
    """A payload of approximately ``tokens`` tokens of realistic English prose.

    Using prose rather than repeated characters matters: the token estimator and
    any real tokeniser behave differently on repetitive input, and a benchmark
    that measures a degenerate case is worse than no benchmark.
    """
    unit = (
        "The reduction operator must be applied at every internal node of the tree, "
        "and the schedule therefore determines how many applications lie on the "
        "critical path rather than how many occur in total. "
    )
    from . import tokens as tokmod

    per = max(1, tokmod.count(unit))
    reps = max(1, math.ceil(tokens / per))
    text = unit * reps
    return tokmod.truncate_to_tokens(text, tokens, marker="") if tokens > 4 else text[: tokens * 4]


# --------------------------------------------------------------------------
# Suite: latency (ping-pong)
# --------------------------------------------------------------------------


def suite_latency(a: argparse.Namespace, workdir: Path) -> Dict[str, Any]:
    sizes = [int(x) for x in a.sizes.split(",")]
    reps = int(a.reps)
    rows: List[Dict[str, Any]] = []
    for n in sizes:
        # Two configurations: one where n is below the eager threshold and one
        # where it is forced above it, so the crossover is measured rather than
        # assumed.
        for mode, eager in (("eager", 10 ** 9), ("rendezvous", 1)):
            cfg = Config(eager_tokens=eager, ctx_budget=10 ** 9)
            root = _job(workdir, 2, f"lat_{n}_{mode}", cfg)
            payload = _payload(n)

            take_body = mode == "eager"

            def body(ctx: Ctx, out: Dict[str, Any], take_body=take_body,
                     payload=payload) -> None:
                lat: List[float] = []
                for i in range(reps):
                    if ctx.crank == 0:
                        t0 = time.perf_counter()
                        p2p.send(ctx, 1, 1, payload, idem=f"pp{i}")
                        p2p.recv(ctx, 1, 2, timeout_ns=30 * 10 ** 9, materialize=take_body)
                        lat.append((time.perf_counter() - t0) / 2.0)
                    else:
                        env = p2p.recv(ctx, 0, 1, timeout_ns=30 * 10 ** 9, materialize=take_body)
                        p2p.send(ctx, 0, 2, payload, idem=f"pr{i}")
                        out.setdefault("charged", []).append(env["context_charged"])
                out["half_roundtrip_s"] = lat

            res = _run_ranks(root, 2, body)
            lat = res.per_rank.get(0, {}).get("half_roundtrip_s") or []
            charged = res.per_rank.get(1, {}).get("charged") or []
            rows.append(
                {
                    "payload_tokens": n,
                    "delivery": mode,
                    "latency_s": _dist(lat),
                    "context_charged_per_msg": _dist([float(x) for x in charged]),
                    "errors": res.errors,
                }
            )
    fit = _fit_alpha_beta([(r["payload_tokens"], r["latency_s"]["p50"]) for r in rows
                           if r["delivery"] == "eager" and r["latency_s"]["p50"]])
    return {"suite": "latency", "reps": reps, "rows": rows, "fit_eager": fit}


def _fit_alpha_beta(points: Sequence[Tuple[int, float]]) -> Dict[str, Any]:
    """Least-squares fit of ``T = alpha + beta * n`` (the Hockney model)."""
    pts = [(float(n), float(t)) for n, t in points if t is not None]
    if len(pts) < 2:
        return {"alpha_s": None, "beta_s_per_token": None, "n": len(pts)}
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    beta = (sum((x - mx) * (y - my) for x, y in pts) / denom) if denom else 0.0
    alpha = my - beta * mx
    n_half = (alpha / beta) if beta > 0 else None
    return {
        "alpha_s": round(alpha, 6),
        "beta_s_per_token": round(beta, 9),
        "n_half_tokens": (round(n_half, 1) if n_half else None),
        "n": len(pts),
    }


# --------------------------------------------------------------------------
# Suite: collectives
# --------------------------------------------------------------------------

COLL_PLAN: List[Tuple[str, str]] = [
    ("barrier", "central"),
    ("barrier", "dissemination"),
    ("barrier", "linear"),
    ("bcast", "flat"),
    ("bcast", "binomial"),
    ("bcast", "chain"),
    ("allreduce", "flat"),
    ("allreduce", "reduce_bcast"),
    ("allreduce", "recursive_doubling"),
    ("reduce", "flat"),
    ("reduce", "binomial"),
    ("reduce", "chain"),
    ("allgather", "flat"),
    ("allgather", "ring"),
    ("allgather", "recursive_doubling"),
]


def suite_collectives(a: argparse.Namespace, workdir: Path) -> Dict[str, Any]:
    """Sweep every collective, every algorithm, over a range of P.

    Sweeping P rather than fixing it is the point: the paper's claim is that the
    algorithm *ranking* differs from MPI's, and a ranking is only meaningful as a
    function of scale. Message and token counts are reported alongside time,
    because in AgentMPI the token counts are the durable result -- wall time on a
    stub executor says little about a real agent run, but "this schedule moves
    Theta(P log P) tokens and that one moves Theta(P)" holds regardless.
    """
    ps = [p for p in (4, 8, 16, 32, 64, 128) if p <= int(a.np)] or [int(a.np)]
    payload_tokens = 200
    payload = _payload(payload_tokens)
    rows: List[Dict[str, Any]] = []
    for np in ps:
        for op, algo in COLL_PLAN:
            if algo == "recursive_doubling" and (np & (np - 1)):
                continue
            root = _job(workdir, np, f"coll_{op}_{algo}_{np}",
                        Config(eager_tokens=10 ** 9, ctx_budget=10 ** 9, timeout_ns=300 * 10 ** 9))

            def body(ctx: Ctx, out: Dict[str, Any], op=op, algo=algo) -> None:
                t0 = time.perf_counter()
                if op == "barrier":
                    collectives.barrier(ctx, label="b", algo=algo, timeout_ns=300 * 10 ** 9)
                elif op == "bcast":
                    collectives.bcast(ctx, root=0, text=(payload if ctx.crank == 0 else None),
                                      label="b", algo=algo, timeout_ns=300 * 10 ** 9,
                                      materialize=True)
                elif op in ("reduce", "allreduce"):
                    collectives.reduce_(ctx, op="concat", text=payload, root=0, label="b",
                                        algo=algo, all_=(op == "allreduce"),
                                        timeout_ns=300 * 10 ** 9, materialize=False)
                elif op == "allgather":
                    collectives.gather(ctx, text=payload, all_=True, label="b", algo=algo,
                                       timeout_ns=300 * 10 ** 9)
                out["t_s"] = time.perf_counter() - t0

            res = _run_ranks(root, np, body, max_threads=a.procs or min(np, 128))
            j = Journal(root)
            nmsg = int(j.scalar("SELECT COUNT(*) FROM msg WHERE job=?", (j.job_id,), 0))
            ntok = int(j.scalar("SELECT COALESCE(SUM(tokens),0) FROM msg WHERE job=?", (j.job_id,), 0))
            ctx_tok = int(j.scalar(
                "SELECT COALESCE(SUM(value),0) FROM counter WHERE job=? AND name='ctx_tokens'",
                (j.job_id,), 0))
            j.close()
            rows.append(
                {
                    "op": op,
                    "algo": algo,
                    "P": np,
                    "wall_s": round(res.wall_s, 4),
                    "per_rank_s": _dist([v.get("t_s", 0.0) for v in res.per_rank.values() if "t_s" in v]),
                    "messages": nmsg,
                    "message_tokens": ntok,
                    "context_tokens": ctx_tok,
                    "errors": res.errors[:3],
                }
            )
    return {"suite": "collectives", "P_sweep": ps, "payload_tokens": payload_tokens, "rows": rows}


# --------------------------------------------------------------------------
# Suite: reduction critical path (the algorithm-selection result)
# --------------------------------------------------------------------------


def suite_scaling(a: argparse.Namespace, workdir: Path) -> Dict[str, Any]:
    """Barrier, allreduce and agent-operator reduction as P grows.

    The agent-operator part is the interesting one. With a simulated per-merge
    cost of ``--merge-cost`` seconds, the measured makespan should track
    ``ceil(log2 P) * merge_cost`` for the binomial tree and ``(P-1) *
    merge_cost`` for the chain, which is the quantitative form of the paper's
    claim that MPI's tree algorithms matter *more* for agents than for bytes.
    """
    merge_cost = float(a.merge_cost)
    ps = [p for p in (2, 4, 8, 16, 32, 64, 128, 256, 512, 1024) if p <= int(a.np)]
    payload = _payload(120)
    out_rows: List[Dict[str, Any]] = []
    for P in ps:
        cfg = Config(eager_tokens=10 ** 9, ctx_budget=10 ** 9, timeout_ns=600 * 10 ** 9)
        for op, algo in (("barrier", "central"), ("barrier", "dissemination"),
                         ("allreduce", "flat")):
            root = _job(workdir, P, f"scale_{op}_{algo}_{P}", cfg)

            def body(ctx: Ctx, out: Dict[str, Any], op=op, algo=algo) -> None:
                t0 = time.perf_counter()
                if op == "barrier":
                    collectives.barrier(ctx, label="s", algo=algo, timeout_ns=600 * 10 ** 9)
                else:
                    collectives.reduce_(ctx, op="sum", text="1", root=0, label="s", algo=algo,
                                        all_=True, timeout_ns=600 * 10 ** 9, materialize=False)
                out["t_s"] = time.perf_counter() - t0

            res = _run_ranks(root, P, body, max_threads=a.procs or min(P, 256))
            j = Journal(root)
            nmsg = int(j.scalar("SELECT COUNT(*) FROM msg WHERE job=?", (j.job_id,), 0))
            j.close()
            out_rows.append({"op": op, "algo": algo, "P": P, "wall_s": round(res.wall_s, 4),
                             "messages": nmsg, "errors": res.errors[:2]})
    agent_rows: List[Dict[str, Any]] = []
    if merge_cost > 0:
        for P in [p for p in ps if p <= 64]:
            for algo in ("binomial", "chain"):
                agent_rows.append(_agent_reduce_run(workdir, P, algo, merge_cost, payload))
    return {"suite": "scaling", "rows": out_rows, "agent_reduce": agent_rows,
            "merge_cost_s": merge_cost}


def _agent_reduce_run(
    workdir: Path, P: int, algo: str, merge_cost: float, payload: str
) -> Dict[str, Any]:
    """Drive an agent-operator reduction with a simulated merge cost.

    Each stub rank plays the continuation protocol faithfully: it receives a
    merge directive, sleeps for ``merge_cost`` seconds (standing in for the
    model call), writes a merged result, and commits. So the measured makespan
    includes every real runtime cost and differs from a real-agent run only in
    what happens during the sleep.
    """
    cfg = Config(eager_tokens=10 ** 9, ctx_budget=10 ** 9, timeout_ns=900 * 10 ** 9)
    root = _job(workdir, P, f"agentred_{algo}_{P}", cfg)
    merges_by_rank: Dict[int, int] = {}

    def body(ctx: Ctx, out: Dict[str, Any]) -> None:
        res = collectives.reduce_(
            ctx, op="agent:merge", text=f"[r{ctx.crank}]", root=0, label="ar", algo=algo,
            all_=False, timeout_ns=900 * 10 ** 9, materialize=False,
        )
        merges = 0
        while res.get("action_required") == "merge":
            merges += 1
            left = Path(res["left_file"]).read_text(encoding="utf-8")
            right = Path(res["right_file"]).read_text(encoding="utf-8")
            time.sleep(merge_cost)
            outp = Path(res["suggested_out"])
            outp.write_text(left.strip() + right.strip(), encoding="utf-8")
            res = collectives.reduce_commit(
                ctx, res["step"], outp.read_text(encoding="utf-8"), timeout_ns=900 * 10 ** 9
            )
        out["merges"] = merges
        out["complete"] = bool(res.get("complete"))

    r = _run_ranks(root, P, body, max_threads=min(P, 128))
    for rank, v in r.per_rank.items():
        merges_by_rank[rank] = int(v.get("merges", 0))
    total = sum(merges_by_rank.values())
    # Two different quantities, and conflating them is a mistake we made once.
    # `max_merges_per_rank` is how much work the busiest executor does. The
    # *serialised depth* is the length of the longest dependency chain, which is
    # what actually sets the makespan: a linear chain gives every rank exactly one
    # merge, yet all P-1 of them are strictly ordered.
    max_per_rank = max(merges_by_rank.values()) if merges_by_rank else 0
    predicted_depth = math.ceil(math.log2(P)) if algo == "binomial" else max(0, P - 1)
    return {
        "P": P,
        "algo": algo,
        "wall_s": round(r.wall_s, 3),
        "merges_total": total,
        "max_merges_per_rank": max_per_rank,
        "predicted_serial_depth": predicted_depth,
        "effective_serial_depth": (round(r.wall_s / merge_cost, 1) if merge_cost else None),
        "merge_cost_s": merge_cost,
        "errors": r.errors[:2],
    }


# --------------------------------------------------------------------------
# Suite: context cost
# --------------------------------------------------------------------------


def suite_context(a: argparse.Namespace, workdir: Path) -> Dict[str, Any]:
    """How many tokens enter agent context windows, per delivery discipline.

    This is the suite that quantifies the protocol's central claim. A broadcast
    of an ``n``-token payload to ``P`` ranks costs ``Theta(nP)`` context if every
    rank inlines it, ``Theta(n + P)`` if ranks take handles, and
    ``Theta(n + Pb)`` if they take ``b``-token views. Gather is worse: inlining
    costs the root ``Theta(nP)`` in a single window, which is the concrete
    mechanism behind the "context exhaustion" failure that agent harnesses hit
    at modest P.
    """
    np = int(a.np)
    n = 4000
    payload = _payload(n)
    rows: List[Dict[str, Any]] = []
    for disc, kwargs in (
        ("inline", {"eager": 10 ** 9, "materialize": True, "budget": None}),
        ("handle", {"eager": 1, "materialize": False, "budget": None}),
        ("view400", {"eager": 1, "materialize": False, "budget": 400}),
    ):
        for op in ("bcast", "allgather"):
            cfg = Config(eager_tokens=int(kwargs["eager"]), ctx_budget=10 ** 9,
                         timeout_ns=180 * 10 ** 9)
            root = _job(workdir, np, f"ctx_{op}_{disc}", cfg)

            def body(ctx: Ctx, out: Dict[str, Any], op=op, kw=kwargs) -> None:
                if op == "bcast":
                    r = collectives.bcast(
                        ctx, root=0, text=(payload if ctx.crank == 0 else None), label="c",
                        algo="flat", timeout_ns=180 * 10 ** 9,
                        materialize=bool(kw["materialize"]), budget=kw["budget"],
                    )
                else:
                    r = collectives.gather(
                        ctx, text=payload, all_=True, label="c", algo="flat",
                        timeout_ns=180 * 10 ** 9, materialize=bool(kw["materialize"]),
                        budget=kw["budget"],
                    )
                out["charged"] = r.get("context_charged", 0)

            # All P ranks must be concurrent: an allgather cannot complete with
            # half of them held back. Capping concurrency below P here made our
            # own harness reproduce the agent host's concurrency limit, and the
            # resulting rows silently under-reported the totals because half the
            # ranks timed out before charging their context.
            res = _run_ranks(root, np, body, max_threads=np)
            j = Journal(root)
            total_ctx = int(j.scalar(
                "SELECT COALESCE(SUM(value),0) FROM counter WHERE job=? AND name='ctx_tokens'",
                (j.job_id,), 0))
            hwm = [int(r0["ctx_hwm"]) for r0 in j.q("SELECT ctx_hwm FROM rank WHERE job=?", (j.job_id,))]
            j.close()
            rows.append(
                {
                    "op": op,
                    "discipline": disc,
                    "P": np,
                    "payload_tokens": n,
                    "total_context_tokens": total_ctx,
                    "per_rank_hwm": _dist([float(x) for x in hwm]),
                    "naive_upper_bound": (n * np if op == "bcast" else n * np * np),
                    "errors": res.errors[:2],
                }
            )
    return {"suite": "context", "P": np, "rows": rows}


# --------------------------------------------------------------------------
# Suite: matching cost
# --------------------------------------------------------------------------


def suite_matching(a: argparse.Namespace, workdir: Path) -> Dict[str, Any]:
    """Receive latency as a function of unexpected-queue depth.

    MPI implementations pay a linear scan of the match list per arriving message,
    and several papers measure exactly this. AgentMPI's match list is a SQL index,
    so the expectation is a much flatter curve; measuring it justifies the
    journal-as-network design choice rather than merely asserting it.
    """
    depths = [0, 8, 32, 128, 512, 2048]
    rows: List[Dict[str, Any]] = []
    for d in depths:
        cfg = Config(eager_tokens=10 ** 9, ctx_budget=10 ** 9)
        root = _job(workdir, 2, f"match_{d}", cfg)
        j = Journal(root)
        init_rank(j, 0)
        init_rank(j, 1)
        ctx0 = bind(j, rank=0)
        ctx1 = bind(j, rank=1)
        for i in range(d):
            # Fill rank 1's unexpected queue with messages it will never take.
            p2p.send(ctx0, 1, 1000 + i, "filler", idem=f"f{i}")
        lat: List[float] = []
        for k in range(int(a.reps)):
            p2p.send(ctx0, 1, 7, "target", idem=f"t{k}")
            t0 = time.perf_counter()
            p2p.recv(ctx1, 0, 7, timeout_ns=30 * 10 ** 9, materialize=True)
            lat.append(time.perf_counter() - t0)
        j.close()
        rows.append({"queue_depth": d, "recv_s": _dist(lat)})
    return {"suite": "matching", "rows": rows}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

SUITES: Dict[str, Callable[[argparse.Namespace, Path], Dict[str, Any]]] = {
    "latency": suite_latency,
    "collectives": suite_collectives,
    "context": suite_context,
    "scaling": suite_scaling,
    "matching": suite_matching,
}


def run(a: argparse.Namespace) -> Dict[str, Any]:
    workdir = Path(a.workdir or (Path.cwd() / "runs" / "bench")).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    names = list(SUITES) if a.suite == "all" else [a.suite]
    out: Dict[str, Any] = {
        "runtime": __import__("ampi").__version__,
        "token_estimator": __import__("ampi.tokens", fromlist=["x"]).estimator_name(),
        "host": {"python": __import__("platform").python_version(),
                 "cpu_count": __import__("os").cpu_count()},
        "suites": {},
    }
    for nm in names:
        t0 = time.perf_counter()
        out["suites"][nm] = SUITES[nm](a, workdir)
        out["suites"][nm]["elapsed_s"] = round(time.perf_counter() - t0, 2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"written": a.out, "suites": names}
    return out
