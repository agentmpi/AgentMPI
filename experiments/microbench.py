"""Experiment 3: microbenchmarks.

These are the AgentMPI equivalents of the benchmarks an MPI paper is expected to
report, and they exist to establish the quantitative claims the design rests on:

``pingpong``
    The classical latency/bandwidth benchmark, run twice: once with the fabric
    alone and once with real agent ranks.  It yields α and β for the cost model
    and, more importantly, the *ratio* between the fabric's per-operation cost and
    an agent's.  That ratio is the entire justification for putting protocol state
    in a durable external store: if the fabric cost were comparable to the agent
    cost the design would be indefensible, and if it is four orders of magnitude
    smaller it is free.

``collectives``
    Measured message counts, rounds and volume for every implemented algorithm at
    every population size, checked against the closed-form cost formulas in
    :mod:`agentmpi.cost`.  This is a *validation* benchmark: it catches the case
    where the implementation and the model disagree, which is the failure mode
    that makes published collective-tuning results wrong.

``fidelity``
    The headline result about semantic reduction.  Fact retention as a function of
    reduction algorithm and hence of fold depth, measured objectively by counting
    identified factual items that survive to the root.

``faults``
    Time to detect a failed rank, cost of shrink and agree, and whether the
    population completes.  Reported for each failure class.

``transport``
    Eager versus rendezvous under growing payloads: the context-safety claim.

``scaling``
    Calibrated simulation out to large populations, with the validation error
    against measured runs at the sizes where both exist.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agentmpi as ampi
from agentmpi import algorithms, cost, sim
from agentmpi.constants import Associativity, BarrierPolicy, FailureClass, Mode
from common import (  # noqa: E402
    FACT_ID,
    fact_retention,
    make_executor_factory,
    make_fact_report,
    provenance,
    value_retention,
    write_result,
)

# ============================================================================
# ping-pong
# ============================================================================

PING_SIZES = (16, 64, 256, 1024, 4096, 16384)


def bench_pingpong(cfg: argparse.Namespace) -> dict[str, Any]:
    """Round-trip time between two ranks as a function of payload size.

    With ``--executor function`` this measures the *fabric*: one send plus one
    receive plus one blob write and read.  With ``--executor broker`` each leg
    additionally invokes an agent, so the difference between the two curves is the
    agent's contribution and their ratio is the protocol overhead.
    """
    reps = cfg.reps
    rows: list[dict[str, Any]] = []

    for n_tokens in PING_SIZES:
        payload = "word " * n_tokens

        def rank_main(comm: ampi.Communicator, payload: str = payload, n: int = n_tokens) -> Any:
            samples: list[float] = []
            for i in range(reps):
                if comm.rank == 0:
                    t0 = time.perf_counter()
                    comm.send(payload, 1, f"ping{i}", mode=Mode.EAGER, timeout=600)
                    comm.recv(source=1, tag=f"pong{i}", timeout=600, admit=False)
                    samples.append(time.perf_counter() - t0)
                else:
                    msg = comm.recv(source=0, tag=f"ping{i}", timeout=600, admit=False)
                    body = comm.fetch(msg, admit=False)
                    if cfg.executor == "broker":
                        body = comm.agent(
                            f"Echo back exactly the first 20 words of the following text, nothing else.\n\n{body[:4000]}",
                            label=f"echo{i}",
                        )
                    comm.send(body if isinstance(body, str) else json.dumps(body), 0, f"pong{i}", mode=Mode.EAGER, timeout=600)
            return samples

        root = Path(cfg.root) / f"pingpong-{n_tokens}"
        job = ampi.launch(
            rank_main,
            size=2,
            root=root,
            executor_factory=make_executor_factory(
                cfg.executor, fabric_root=root, fn=_echo_fn, seed=cfg.seed
            )
            if cfg.executor != "broker"
            else make_executor_factory("broker", fabric_root=_ensure(root, 2)),
            eager_limit=10**9,
            strict_context=False,
            timeout=cfg.timeout,
        )
        samples = [s for s in (job.value(0) or []) if s > 0]
        rows.append(
            {
                "n_tokens": n_tokens,
                "reps": len(samples),
                "rtt_p50_s": round(statistics.median(samples), 6) if samples else None,
                "rtt_min_s": round(min(samples), 6) if samples else None,
                "rtt_p95_s": round(sorted(samples)[int(0.95 * (len(samples) - 1))], 6) if samples else None,
                "ok": job.ok,
            }
        )

    # Fit the Hockney model to the half-round-trip times.
    fit = _fit_alpha_beta([(r["n_tokens"], r["rtt_p50_s"] / 2) for r in rows if r["rtt_p50_s"]])
    return {"rows": rows, "fit": fit, "executor": cfg.executor}


def _echo_fn(prompt: str, **_meta: Any) -> str:
    return " ".join(prompt.split()[-20:])


def _fit_alpha_beta(points: list[tuple[int, float]]) -> dict[str, Any]:
    if len(points) < 3:
        return {}
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    sxy = sum((x - mx) * (y - my) for x, y in points)
    beta = sxy / sxx if sxx else 0.0
    alpha = my - beta * mx
    ss_tot = sum((y - my) ** 2 for _, y in points)
    ss_res = sum((y - (alpha + beta * x)) ** 2 for x, y in points)
    return {
        "alpha_s": round(alpha, 8),
        "beta_s_per_token": round(beta, 10),
        "tokens_per_s": round(1 / beta, 1) if beta > 0 else None,
        "alpha_beta_crossover_tokens": round(alpha / beta, 1) if beta > 0 else None,
        "r2": round(1 - ss_res / ss_tot, 5) if ss_tot else None,
    }


def _ensure(root: Path, size: int) -> Path:
    if not (root / "fabric.sqlite").exists():
        ampi.create_job(root, size, label="microbench")
    return root


# ============================================================================
# collective structure: measured vs closed-form model
# ============================================================================

COLL_CASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("barrier", ("dissemination", "linear")),
    ("bcast", ("flat", "binomial", "chain")),
    ("scatter", ("linear", "binomial")),
    ("gather", ("linear", "binomial")),
    ("allgather", ("ring", "bruck", "recursive_doubling")),
    ("reduce", ("chain", "flat", "binomial")),
    ("allreduce", ("reduce_bcast", "recursive_doubling")),
    ("scan", ("chain", "recursive_doubling")),
    ("alltoall", ("pairwise", "linear", "bruck")),
)


def bench_collectives(cfg: argparse.Namespace) -> dict[str, Any]:
    """Measure the communication structure of every algorithm and check the model."""
    sizes = [int(s) for s in cfg.sizes.split(",")]
    rows: list[dict[str, Any]] = []
    for op, algs in COLL_CASES:
        for alg in algs:
            if (op, alg) not in cost.FORMULAS:
                continue
            for p in sizes:
                if p < 2:
                    continue
                if alg == "recursive_doubling" and (p & (p - 1)) != 0 and op == "allgather":
                    continue
                measured = _run_collective(cfg, op, alg, p)
                if measured is None:
                    rows.append({"op": op, "algorithm": alg, "p": p, "ok": False})
                    continue
                r_pred, m_pred, v_pred, d_pred = cost.FORMULAS[(op, alg)](p, 1)
                rows.append(
                    {
                        "op": op,
                        "algorithm": alg,
                        "p": p,
                        "ok": True,
                        "messages_measured": measured["messages"],
                        "messages_model": int(m_pred),
                        "messages_match": measured["messages"] == int(m_pred),
                        "rounds_measured": measured["rounds_max"],
                        "rounds_model": int(r_pred),
                        "fold_depth_measured": measured["fold_depth_max"],
                        "fold_depth_model": int(d_pred),
                        "fold_depth_match": measured["fold_depth_max"] == int(d_pred),
                        "wall_s": measured["wall_s"],
                        "fabric_s_per_message": (
                            round(measured["wall_s"] / measured["messages"], 6) if measured["messages"] else None
                        ),
                    }
                )
    n_checked = sum(1 for r in rows if r.get("ok"))
    n_msg_ok = sum(1 for r in rows if r.get("messages_match"))
    n_depth_ok = sum(1 for r in rows if r.get("fold_depth_match"))
    return {
        "rows": rows,
        "n_configurations": n_checked,
        "message_count_agreement": round(n_msg_ok / n_checked, 4) if n_checked else 0.0,
        "fold_depth_agreement": round(n_depth_ok / n_checked, 4) if n_checked else 0.0,
        "mismatches": [r for r in rows if r.get("ok") and not (r["messages_match"] and r["fold_depth_match"])],
    }


def _run_collective(cfg: argparse.Namespace, op: str, alg: str, p: int) -> dict[str, Any] | None:
    """Execute one collective with a trivial payload and collect its statistics."""

    def rank_main(comm: ampi.Communicator) -> Any:
        t0 = time.perf_counter()
        if op == "barrier":
            comm.barrier(algorithm=alg, policy=BarrierPolicy.WAIT, timeout=300)
        elif op == "bcast":
            comm.bcast("x" if comm.rank == 0 else None, root=0, algorithm=alg, timeout=300)
        elif op == "scatter":
            comm.scatter([f"u{i}" for i in range(comm.size)] if comm.rank == 0 else None, root=0, algorithm=alg, timeout=300)
        elif op == "gather":
            comm.gather(f"r{comm.rank}", root=0, algorithm=alg, timeout=300)
        elif op == "allgather":
            comm.allgather(f"r{comm.rank}", algorithm=alg, timeout=300)
        elif op == "reduce":
            comm.reduce(1, ampi.SUM, root=0, algorithm=alg, timeout=300)
        elif op == "allreduce":
            comm.allreduce(1, ampi.SUM, algorithm=alg, timeout=300)
        elif op == "scan":
            comm.scan(1, ampi.SUM, algorithm=alg, timeout=300)
        elif op == "alltoall":
            comm.alltoall([f"{comm.rank}->{j}" for j in range(comm.size)], algorithm=alg, timeout=300)
        else:
            raise ValueError(op)
        st = algorithms.LAST_STATS.get(comm.rt.wrank)
        return {
            "messages": comm.rt.cost.n_messages_sent,
            "rounds": st.rounds if st else 0,
            "fold_depth": st.fold_depth if st else 0,
            "wall_s": time.perf_counter() - t0,
        }

    root = Path(cfg.root) / f"coll-{op}-{alg}-{p}"
    job = ampi.launch(
        rank_main,
        size=p,
        root=root,
        eager_limit=10**9,
        strict_context=False,
        timeout=min(600.0, cfg.timeout),
    )
    if not job.ok:
        return None
    vals = [o.value for o in job.outcomes if o.value]
    return {
        "messages": sum(v["messages"] for v in vals),
        "rounds_max": max(v["rounds"] for v in vals),
        "fold_depth_max": max(v["fold_depth"] for v in vals),
        "wall_s": round(max(v["wall_s"] for v in vals), 6),
    }


# ============================================================================
# reduction fidelity
# ============================================================================

MERGE_PROMPT = """Merge the two reports below into a single consolidated report.

Requirements:
- Preserve every distinct factual item. Each item carries a bracketed identifier
  such as [F-3-2]. Keep every identifier that appears in either input, exactly as
  written, attached to its item.
- You may compress wording, but you may not drop an item.
- Your output must be at most {budget} tokens. If you cannot fit everything at full
  length, shorten the wording of items rather than removing any of them.

Return ONLY a JSON object: {{"title": "<short>", "findings": ["[F-x-y] <item>", ...]}}

--- REPORT A ---
{left}

--- REPORT B ---
{right}"""

#: The variadic form. This is what makes a wide reduction tree pay: k reports are
#: merged in ONE application rather than k-1, so the fold depth of a k-ary tree is
#: log_k p rather than log2 p. MPI has no use for such an operator because its
#: processes are single-ported; an agent prompt carries k inputs as easily as two.
MERGE_PROMPT_K = """Merge the {n} reports below into a single consolidated report.

Requirements:
- Preserve every distinct factual item. Each item carries a bracketed identifier
  such as [F-3-2]. Keep every identifier that appears in ANY input, exactly as
  written, attached to its item.
- You may compress wording, but you may not drop an item.
- Your output must be at most {budget} tokens. If you cannot fit everything at full
  length, shorten the wording of items rather than removing any of them.

Return ONLY a JSON object: {{"title": "<short>", "findings": ["[F-x-y] <item>", ...]}}

{inputs}"""

def merge_contract(budget: int) -> ampi.Contract:
    """The merge contract, with the token budget *enforced* rather than advised.

    An earlier version omitted ``max_tokens``, so the budget existed only as a sentence
    in the prompt. It was therefore not a constraint, and the population treated it as
    one: measured across ten merges, eight exceeded the stated 450-token budget, by up to
    55%. Combined with re-encoding (see :func:`agentmpi.experiments.common.make_fact_report`),
    that is the full explanation for why retention never fell -- an operator that may both
    compress *and* overflow is never forced to discard anything.

    With ``max_tokens`` on the contract the runtime checks the output, rejects it, and
    retries with the diagnosis appended --- the ordinary failure-class-F3 path. Only then
    is the budget a budget. The mechanism was there the whole time and the harness did not
    use it, which is the same mistake this paper accuses agent frameworks of making.
    """
    return ampi.Contract(
        name="MergedReport",
        kind="json",
        required=("title", "findings"),
        nonempty=("findings",),
        max_tokens=budget,
        semantics=(
            "findings must contain complete, verbatim items. If they do not all fit in "
            "max_tokens, omit whole items rather than abbreviating or re-encoding any."
        ),
    )


#: Kept for call sites that do not enforce a budget.
MERGE_CONTRACT = ampi.Contract(
    name="MergedReport", kind="json", required=("title", "findings"), nonempty=("findings",)
)


def bench_fidelity(cfg: argparse.Namespace) -> dict[str, Any]:
    """Fact retention of a semantic reduction as a function of the algorithm.

    Each rank contributes a report holding ``--facts`` uniquely identified items.
    The reduction operator is a merge performed by an agent.  Retention at the root
    is counted mechanically.

    Three algorithms are compared, and they differ in exactly the two quantities
    the design predicts matter:

    ==================  =========  ============  =======================
    algorithm           rounds     fold depth    operator applications
    ==================  =========  ============  =======================
    ``chain``           p-1        p-1           p-1, one per rank
    ``binomial``        log2 p     log2 p        p-1, spread over a tree
    ``flat``            1          p-1           p-1, all at the root
    ==================  =========  ============  =======================

    If retention were governed by the number of operator applications, all three
    would score the same.  If it is governed by fold *depth* -- the number of times
    an item is re-summarised on its way to the root -- the tree should win
    decisively.  That is the prediction under test.

    The ``--weighted`` variant additionally tells the operator how many leaves its
    accumulator represents, so it can allocate its output budget proportionally.
    Without it a tree compresses 2 inputs and 16 inputs into the same length, which
    systematically favours whichever subtree merged last.
    """
    n_facts = cfg.facts
    algs = [a for a in cfg.algorithms.split(",") if a]
    rows: list[dict[str, Any]] = []

    for alg in algs:
        op = ampi.semantic_op(
            "MERGE_REPORTS",
            MERGE_PROMPT,
            commutative=False,
            associativity=Associativity.APPROX,
            output_tokens=cfg.merge_budget,
            contract=merge_contract(cfg.merge_budget),
            # Only the k-ary algorithm can use the variadic kernel, but attaching it
            # unconditionally keeps the operator identical across algorithms so the
            # comparison isolates the tree shape.
            variadic_prompt=MERGE_PROMPT_K,
        )
        if not cfg.weighted:
            # Fixed budget regardless of subtree size: the naive implementation. It
            # still needs the variadic kernel, or the k-ary tree silently degrades to
            # a left fold and the comparison measures the wrong thing.
            op = ampi.Op(
                name="MERGE_REPORTS_FIXED",
                fn=_fixed_budget_merge(cfg.merge_budget),
                commutative=False,
                associativity=Associativity.APPROX,
                cost_tokens=cfg.merge_budget,
                variadic=_fixed_budget_merge_k(cfg.merge_budget),
            )

        def rank_main(comm: ampi.Communicator, op: ampi.Op = op, alg: str = alg) -> Any:
            report = make_fact_report(comm.rank, n_facts, incompressible=cfg.incompressible)
            t0 = time.perf_counter()
            # For the k-ary tree the fan-in is derived from the context budget if the
            # caller did not fix it, which is the policy the spec recommends.
            k = cfg.fanin or ampi.ops.optimal_fanin(cfg.context_budget, cfg.merge_budget)
            out = comm.reduce(report, op, root=0, algorithm=alg, timeout=cfg.timeout, fanin=k)
            st = algorithms.LAST_STATS.get(comm.rt.wrank)
            return {
                "result": out,
                "wall_s": time.perf_counter() - t0,
                "fold_depth": st.fold_depth if st else 0,
                "rounds": st.rounds if st else 0,
                "fanin": (st.extra.get("fanin") if st else None) or (k if alg == "kary" else None),
                "variadic": st.extra.get("variadic") if st else None,
                "agent_calls": comm.rt.cost.n_agent_calls,
                "tokens_out": comm.rt.cost.tokens_out,
            }

        root = Path(cfg.root) / (
            f"fidelity-{alg}-{'w' if cfg.weighted else 'f'}-k{cfg.fanin or 0}"
            f"{'-inc' if cfg.incompressible else ''}"
        )
        factory = (
            make_executor_factory("broker", fabric_root=_ensure(root, cfg.ranks), timeout=cfg.timeout)
            if cfg.executor == "broker"
            else make_executor_factory(
                cfg.executor, fabric_root=root, fn=_surrogate_merge(cfg.drop_rate, cfg.seed), seed=cfg.seed
            )
        )
        job = ampi.launch(
            rank_main,
            size=cfg.ranks,
            root=root,
            executor_factory=factory,
            eager_limit=10**9,
            strict_context=False,
            timeout=cfg.timeout,
        )
        head = job.value(0) or {}
        retention = fact_retention(head.get("result"), cfg.ranks, n_facts)
        if cfg.incompressible:
            # Identifier retention alone cannot distinguish "carried through" from
            # "label kept, payload lost", and the second is the failure that matters.
            retention |= value_retention(head.get("result"), cfg.ranks, n_facts)
        rows.append(
            {
                "algorithm": alg,
                "weighted_budget": cfg.weighted,
                "p": cfg.ranks,
                "facts_per_rank": n_facts,
                "_budget": cfg.merge_budget,
                "incompressible": cfg.incompressible,
                "ok": job.ok,
                "fold_depth": head.get("fold_depth"),
                "rounds": head.get("rounds"),
                "fanin": head.get("fanin"),
                "variadic": head.get("variadic"),
                "wall_s": round(head.get("wall_s", 0.0), 2),
                "agent_calls_total": sum((o.value or {}).get("agent_calls", 0) for o in job.outcomes if o.value),
                "tokens_out_total": sum((o.value or {}).get("tokens_out", 0) for o in job.outcomes if o.value),
                **retention,
            }
        )
    return {"rows": rows, "executor": cfg.executor, "merge_budget": cfg.merge_budget}


def _fixed_budget_merge(budget: int) -> Any:
    def _fn(a: Any, b: Any, ctx: ampi.ReduceContext) -> Any:
        if ctx.agent is None:
            raise ampi.AmpiUsageError("needs an executor")
        return ctx.agent(
            MERGE_PROMPT.format(left=json.dumps(a, ensure_ascii=False), right=json.dumps(b, ensure_ascii=False), budget=budget),
            label=f"merge:d{ctx.depth}",
            contract=merge_contract(budget),
            max_tokens=budget,
            retries=3,
        )

    return _fn


def _fixed_budget_merge_k(budget: int) -> Any:
    """Variadic form of the fixed-budget merge: k inputs, one application."""

    def _fn(values: list[Any], ctx: ampi.ReduceContext) -> Any:
        if ctx.agent is None:
            raise ampi.AmpiUsageError("needs an executor")
        inputs = "\n\n".join(
            f"--- INPUT {i} ---\n{json.dumps(v, ensure_ascii=False)}" for i, v in enumerate(values)
        )
        return ctx.agent(
            MERGE_PROMPT_K.format(inputs=inputs, n=len(values), budget=budget, depth=ctx.depth, weight=ctx.weight),
            label=f"merge:k{len(values)}:d{ctx.depth}",
            contract=merge_contract(budget),
            max_tokens=budget,
            retries=3,
        )

    return _fn


def _surrogate_merge(drop_rate: float, seed: int) -> Any:
    """A deterministic caricature of a lossy merge, for CI and for plumbing checks.

    **This is not evidence about real operators, and must not be read as such.** It
    implements the paper's loss model -- each application independently drops each
    item with probability ``drop_rate``, so retention is ``(1-r)^depth`` -- by
    construction. Running it therefore *reproduces* the analytic curve rather than
    testing it, and its only legitimate uses are checking that the wide tree actually
    reaches the fold depth it claims and keeping the benchmark runnable without an
    agent. The evidence about whether real reduction loss behaves this way comes from
    the broker-executed run.

    An earlier version made the surviving capacity proportional to the input size,
    which had the perverse effect of making a serial chain lossless: merging two
    items at a time never exceeds a proportional budget. That is worth recording
    because it is the same mistake a careless real experiment would make -- if each
    merge is given a budget scaled to its input, depth costs nothing and the effect
    under study disappears.
    """
    import random as _random

    def _fn(prompt: str, **_meta: Any) -> Any:
        rng = _random.Random(seed + len(prompt))
        # Collect the identified items from every input block, whichever prompt form
        # was used (the binary form labels blocks REPORT, the variadic form INPUT).
        items: list[str] = []
        seen: set[str] = set()
        for line in prompt.splitlines():
            line = line.strip().strip('",')
            m = FACT_ID.search(line)
            if m and line not in seen:
                seen.add(line)
                items.append(line)
        # Independent per-item loss, once per application. This is the mechanism the
        # depth model describes: an item is at risk every time it passes through the
        # operator, so surviving d applications has probability (1-r)^d. Later items
        # are favoured slightly, reflecting the recency bias models exhibit, which is
        # what produces the positional unfairness the metric reports.
        n = len(items)
        kept = [
            item
            for i, item in enumerate(items)
            if rng.random() > drop_rate * (1.0 - 0.4 * (i / max(1, n - 1)))
        ]
        if not kept and items:
            kept = items[-1:]
        return {"title": "merged", "findings": kept}

    return _fn


# ============================================================================
# fault injection
# ============================================================================


def bench_faults(cfg: argparse.Namespace) -> dict[str, Any]:
    """Detection latency and recovery cost for each mitigation strategy.

    Ranks in ``victims`` return early, which is indistinguishable from a crashed
    agent session.  The surviving ranks then run the same phase under four
    policies, and the benchmark reports whether the population completed, how long
    detection took, and what the recovery cost.  ``WAIT`` is included precisely
    because it is MPI's only option and it never completes -- which is the point.
    """
    p = cfg.ranks
    victims = sorted({int(v) for v in cfg.victims.split(",") if v.strip()} & set(range(1, p)))
    rows: list[dict[str, Any]] = []

    for policy in ("wait", "raise", "proceed", "shrink"):
        def rank_main(comm: ampi.Communicator, policy: str = policy) -> Any:
            if comm.rank in victims:
                return {"role": "victim"}
            t0 = time.perf_counter()
            detected: list[int] = []
            completed = False
            error = ""
            try:
                res = comm.barrier(timeout=cfg.detect_timeout, policy=BarrierPolicy(policy), label="phase")
                detected = list(res.absent)
                completed = True
            except ampi.AmpiError as exc:
                error = f"{getattr(exc, 'cls_name', type(exc).__name__)}: {exc}"
                detected = list(getattr(exc, "failed", ()) or ampi.get_failed(comm))
            detect_s = time.perf_counter() - t0
            shrink_s = None
            survivors = None
            agreed = None
            if policy == "shrink" and completed:
                t1 = time.perf_counter()
                comm.refresh()
                survivors = [r for r in range(comm.size) if r not in set(detected)]
                shrink_s = time.perf_counter() - t1
                try:
                    agreed = ampi.agree(comm, True, timeout=60)
                except ampi.AmpiError:
                    agreed = None
            return {
                "role": "survivor",
                "completed": completed,
                "error": error,
                "detected": detected,
                "detect_s": round(detect_s, 3),
                "shrink_s": None if shrink_s is None else round(shrink_s, 3),
                "survivors": survivors,
                "agreed": agreed,
            }

        root = Path(cfg.root) / f"faults-{policy}"
        job = ampi.launch(
            rank_main, size=p, root=root, strict_context=False, timeout=cfg.detect_timeout + 120
        )
        survivors = [o.value for o in job.outcomes if o.value and o.value.get("role") == "survivor"]
        rows.append(
            {
                "policy": policy,
                "p": p,
                "victims": victims,
                "n_survivors": len(survivors),
                "all_completed": bool(survivors) and all(s.get("completed") for s in survivors),
                "detect_p50_s": round(statistics.median([s["detect_s"] for s in survivors]), 3) if survivors else None,
                "detected_correctly": bool(survivors)
                and all(sorted(s.get("detected") or []) == victims for s in survivors),
                "shrink_p50_s": (
                    round(statistics.median([s["shrink_s"] for s in survivors if s.get("shrink_s") is not None]), 3)
                    if any(s.get("shrink_s") is not None for s in survivors)
                    else None
                ),
                "agree_consistent": (
                    len({s.get("agreed") for s in survivors if s.get("agreed") is not None}) <= 1
                ),
                "example_error": next((s["error"] for s in survivors if s.get("error")), ""),
            }
        )
    return {"rows": rows, "detect_timeout_s": cfg.detect_timeout}


# ============================================================================
# transport safety
# ============================================================================


def bench_transport(cfg: argparse.Namespace) -> dict[str, Any]:
    """Eager versus rendezvous under growing payloads.

    The program is a ring exchange in which every rank sends before it receives --
    MPI's canonical *unsafe* program.  Under eager transport it must stall once the
    aggregate payload exceeds the receivers' unexpected-message budget; under
    rendezvous it must complete at every size, because only a handle is in flight.
    """
    rows: list[dict[str, Any]] = []
    budget = cfg.unexpected_limit
    for n_tokens in (128, 512, 2048, 8192, 32768):
        for mode in (Mode.EAGER, Mode.RENDEZVOUS):
            payload = "word " * n_tokens

            def rank_main(comm: ampi.Communicator, mode: Mode = mode, payload: str = payload) -> Any:
                p, r = comm.size, comm.rank
                t0 = time.perf_counter()
                try:
                    comm.send(payload, (r + 1) % p, "ring", mode=mode, timeout=cfg.stall_timeout)
                    msg = comm.recv(source=(r - 1) % p, tag="ring", timeout=cfg.stall_timeout)
                except ampi.AmpiError as exc:
                    return {
                        "ok": False,
                        "error_class": getattr(exc, "cls_name", type(exc).__name__),
                        "wall_s": round(time.perf_counter() - t0, 2),
                        "context_used": comm.rt.context.used,
                    }
                return {
                    "ok": True,
                    "wall_s": round(time.perf_counter() - t0, 3),
                    "context_used": comm.rt.context.used,
                    "materialised": msg.payload is not None,
                    "tokens_deferred": comm.rt.cost.tokens_deferred,
                }

            root = Path(cfg.root) / f"transport-{mode.value}-{n_tokens}"
            job = ampi.launch(
                rank_main,
                size=cfg.ranks,
                root=root,
                eager_limit=10**9,
                unexpected_limit=budget,
                context_budget=cfg.context_budget,
                strict_context=True,
                timeout=cfg.stall_timeout + 60,
            )
            vals = [o.value for o in job.outcomes if o.value]
            rows.append(
                {
                    "mode": mode.value,
                    "n_tokens": n_tokens,
                    "p": cfg.ranks,
                    "unexpected_limit": budget,
                    "context_budget": cfg.context_budget,
                    "completed": bool(vals) and all(v.get("ok") for v in vals),
                    "n_failed": sum(1 for v in vals if not v.get("ok")),
                    "error_classes": sorted({v.get("error_class") for v in vals if v.get("error_class")}),
                    "max_context_used": max((v.get("context_used", 0) for v in vals), default=0),
                    "tokens_deferred": sum(v.get("tokens_deferred", 0) for v in vals),
                    "wall_p50_s": round(statistics.median([v["wall_s"] for v in vals]), 3) if vals else None,
                }
            )
    return {"rows": rows}


# ============================================================================
# calibrated simulated scaling
# ============================================================================


def bench_scaling(cfg: argparse.Namespace) -> dict[str, Any]:
    """Extrapolate collective cost with a simulator calibrated on measured runs."""
    params = cost.CostParams(
        alpha_s=cfg.alpha, beta_s_per_token=1.0 / cfg.tokens_per_s, alpha_p50=cfg.alpha, alpha_p99=cfg.alpha * cfg.tail
    )
    sp = sim.from_cost_params(params)
    sizes = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
    out: dict[str, Any] = {"sim_params": sp.__dict__, "studies": {}}
    for op, algs in (
        ("barrier", ("dissemination", "linear")),
        ("bcast", ("flat", "binomial", "chain")),
        ("allgather", ("ring", "bruck")),
        ("reduce", ("chain", "flat", "binomial")),
        ("allreduce", ("reduce_bcast", "recursive_doubling")),
        ("alltoall", ("pairwise", "bruck")),
    ):
        out["studies"][op] = sim.scaling_study(
            op, algs, sizes, cfg.payload_tokens, sp, trials=cfg.trials, op_cost_tokens=cfg.payload_tokens
        )
    # Crossover table: which algorithm the model recommends where.
    crossovers: list[dict[str, Any]] = []
    for op in ("bcast", "reduce", "allreduce", "allgather", "alltoall", "barrier"):
        for p in sizes:
            for n in (64, 512, 4096, 32768):
                try:
                    crossovers.append(
                        {
                            "op": op,
                            "p": p,
                            "n_tokens": n,
                            "best_time": cost.best_algorithm(op, p, n, params, objective="time"),
                            "best_volume": cost.best_algorithm(op, p, n, params, objective="volume"),
                            "best_fidelity": cost.best_algorithm(op, p, n, params, objective="fidelity"),
                        }
                    )
                except KeyError:
                    continue
    out["crossovers"] = crossovers
    return out


# ============================================================================
# main
# ============================================================================

BENCHES = {
    "pingpong": bench_pingpong,
    "collectives": bench_collectives,
    "fidelity": bench_fidelity,
    "faults": bench_faults,
    "transport": bench_transport,
    "scaling": bench_scaling,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AgentMPI microbenchmarks")
    ap.add_argument("--bench", action="append", choices=[*BENCHES, "all"], default=None)
    ap.add_argument("--ranks", type=int, default=8)
    ap.add_argument("--executor", choices=["function", "simulated", "broker"], default="function")
    ap.add_argument("--root", default="runs/microbench")
    ap.add_argument("--label", default="microbench")
    ap.add_argument("--sizes", default="2,3,4,5,7,8,12,16,24,32")
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--facts", type=int, default=4)
    ap.add_argument("--merge-budget", type=int, default=900)
    ap.add_argument("--algorithms", default="binomial,chain,flat")
    ap.add_argument("--fanin", type=int, default=None, help="k for the kary reduce; default from the context budget")
    ap.add_argument(
        "--incompressible",
        action="store_true",
        help="give each item a high-entropy payload so a token budget genuinely forces loss",
    )
    ap.add_argument("--weighted", action="store_true", help="give the merge operator a subtree-proportional budget")
    ap.add_argument("--drop-rate", type=float, default=0.15, help="surrogate merge loss per application")
    ap.add_argument("--victims", default="3")
    ap.add_argument("--detect-timeout", type=float, default=20.0)
    ap.add_argument("--stall-timeout", type=float, default=15.0)
    ap.add_argument("--unexpected-limit", type=int, default=4096)
    ap.add_argument("--context-budget", type=int, default=32768)
    ap.add_argument("--alpha", type=float, default=25.0)
    ap.add_argument("--tokens-per-s", type=float, default=45.0)
    ap.add_argument("--tail", type=float, default=4.0, help="alpha_p99 / alpha_p50")
    ap.add_argument("--payload-tokens", type=int, default=800)
    ap.add_argument("--trials", type=int, default=32)
    ap.add_argument("--timeout", type=float, default=5400.0)
    ap.add_argument("--seed", type=int, default=0)
    cfg = ap.parse_args(argv)

    wanted = cfg.bench or ["collectives"]
    if "all" in wanted:
        wanted = list(BENCHES)
    Path(cfg.root).mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {"provenance": provenance(experiment="microbench"), "config": vars(cfg), "benches": {}}
    for name in wanted:
        t0 = time.time()
        print(f"[microbench] {name} ...", flush=True)
        results["benches"][name] = BENCHES[name](cfg) | {"_wall_s": round(time.time() - t0, 2)}
        write_result(f"{cfg.label}-{name}", {**results, "benches": {name: results["benches"][name]}}, subdir="microbench")
        print(f"[microbench] {name} done in {time.time() - t0:.1f}s", flush=True)

    path = write_result(f"{cfg.label}-all", results, subdir="microbench")
    print(json.dumps({"result": str(path), "benches": list(results["benches"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
