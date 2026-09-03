"""E0: protocol microbenchmarks.

No language model is involved in this experiment, deliberately.  Its job is to
measure the *protocol's* cost so that the agent experiments can attribute their
time to the right place, and to check the closed-form cost model against reality.
An implementation whose measured message counts disagree with its own model has a
bug in one of the two, and having both is how the discrepancy gets found.

Four questions:

Q0.1  What are alpha and beta for each device?  Fitted by ping-pong regression over
      payload sizes, which is how the Hockney parameters are measured in HPC and
      for the same reason: the intercept is the per-operation cost and the slope is
      the per-token cost.

Q0.2  Do the collective schedules' predicted message counts match what the runtime
      actually performs?  The model is only useful if it is checked.

Q0.3  How does peak context residency scale with p, by delivery mode?  This is the
      quantity with no MPI analogue, because it decides feasibility rather than
      speed.

Q0.4  How much does the protocol cost relative to an executor turn?  If the answer
      is "nothing", then every selection rule that trades protocol operations for
      operator applications is settled before it is argued.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from ampi import Ampi
from ampi.core.algorithms import CATALOGUE, build_schedule, cost_of
from ampi.core.context import ResidencyModel
from ampi.tokens import counter_name
from ampitools.harness import Harness

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))]


# --------------------------------------------------------------------------
# Q0.1 -- ping-pong: alpha and beta
# --------------------------------------------------------------------------


def pingpong(device: str, sizes: list[int], reps: int, root: Path) -> dict[str, Any]:
    rows = []
    for n_tokens in sizes:
        job_root = root / f"pp-{device}-{n_tokens}"
        Ampi.create(str(job_root), 2, device=device, allow_volatile=True, force=True)
        a, b = Ampi(str(job_root), rank=0, allow_volatile=True), Ampi(str(job_root), rank=1, allow_volatile=True)
        a.init()
        b.init()
        body = "word " * max(1, n_tokens)
        samples = []
        for i in range(reps):
            t0 = time.perf_counter()
            a.send(1, body, tag=i % 100, delivery="eager", idempotency_key=f"pp{i}")
            b.recv(0, tag=i % 100, timeout=30, materialize=True)
            samples.append(time.perf_counter() - t0)
            b.ctx_release()
        rows.append({
            "tokens": n_tokens,
            "median_s": statistics.median(samples),
            "p95_s": _pct(samples, 0.95),
            "min_s": min(samples),
            "reps": reps,
        })
        a.close()
        b.close()

    # Least squares on the medians: T = alpha + beta*n.
    xs = [r["tokens"] for r in rows]
    ys = [r["median_s"] for r in rows]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs) or 1.0
    beta = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denom
    alpha = mean_y - beta * mean_x
    return {
        "device": device,
        "samples": rows,
        "alpha_s": alpha,
        "beta_s_per_token": beta,
        # The half-bandwidth point: the payload at which per-token cost equals the
        # fixed per-operation cost.  MPI's n_1/2 in tokens.
        "n_half": (alpha / beta) if beta > 0 else None,
        "counter": counter_name(),
    }


# --------------------------------------------------------------------------
# Q0.2 -- do the schedules' predictions match the runtime?
# --------------------------------------------------------------------------


def verify_model(device: str, sizes: list[int], root: Path) -> dict[str, Any]:
    """Compare predicted collective message counts against the event trace."""
    rows = []
    for p in sizes:
        job_root = root / f"model-{device}-{p}"
        h = Harness(root=str(job_root), size=p, device=device, force=True)
        h.create()

        def rank_main(amp, rank):
            amp.barrier("b", timeout=120)
            amp.allreduce("r", payload=rank, op="sum", timeout=120)
            amp.allgather("g", payload={"r": rank}, timeout=120)
            return True

        results = h.run(rank_main, timeout=600)
        job = h.attach(0)
        events = job.events()
        observed = {k: sum(1 for e in events if e["kind"] == k)
                    for k in ("barrier", "allreduce", "allgather", "coll.join")}
        predicted = {
            coll: build_schedule(coll, CATALOGUE[coll][0], p, tokens=20, inline=False).to_dict()
            for coll in ("barrier", "allreduce", "allgather")
        }
        rows.append({
            "p": p,
            "ok": all(r.ok for r in results),
            "observed_events": observed,
            # Every rank joins each of the three collectives exactly once: the
            # participation record is the ground truth the model is checked against.
            "coll_joins_expected": 3 * p,
            "coll_joins_observed": observed["coll.join"],
            "predicted": predicted,
            "wall_s": round(max(r.seconds for r in results), 4),
            "context_peak": max(r.context_used for r in results),
        })
        job.close()
    return {"device": device, "rows": rows}


# --------------------------------------------------------------------------
# Q0.3 -- context residency
# --------------------------------------------------------------------------


def residency(ps: list[int], n_tokens: int) -> dict[str, Any]:
    """Closed-form peak residency, and the schedules' agreement with it."""
    rows = []
    for p in ps:
        model = ResidencyModel(p=p, n=n_tokens)
        inline = build_schedule("allgather", "ring", p, tokens=n_tokens, inline=True)
        handle = build_schedule("allgather", "ring", p, tokens=n_tokens, inline=False)
        rows.append({
            "p": p,
            "n": n_tokens,
            "model": model.as_table(),
            "schedule_inline_peak": inline.peak_resident(),
            "schedule_handle_peak": handle.peak_resident(),
            "ratio": inline.peak_resident() / max(1, handle.peak_resident()),
        })
    return {"n_tokens": n_tokens, "rows": rows}


# --------------------------------------------------------------------------
# Q0.4 -- protocol cost against an executor turn
# --------------------------------------------------------------------------


def relative_cost(alpha_s: float, beta: float, gammas: list[float]) -> dict[str, Any]:
    rows = []
    for p in (8, 32, 128):
        for gamma in gammas:
            sched = build_schedule("allreduce", "reduce_bcast", p, tokens=4000, inline=False)
            c = cost_of(sched, gamma_s=gamma, alpha_s=alpha_s, beta_s_per_token=beta)
            rows.append({
                "p": p,
                "gamma_s": gamma,
                "protocol_s": round(c.protocol_seconds, 6),
                "operator_s": round(c.operator_seconds, 3),
                "protocol_fraction": round(
                    c.protocol_seconds / max(1e-12, c.total_seconds), 8
                ),
            })
    return {"rows": rows}


# --------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="AgentMPI protocol microbenchmarks")
    ap.add_argument("--devices", default="sqlite,journal,memory")
    ap.add_argument("--reps", type=int, default=25)
    ap.add_argument("--scale", default="2,4,8,16")
    ap.add_argument("--work", default="/tmp/ampi-e0")
    ap.add_argument("--out", default=str(RESULTS / "e0_micro.json"))
    a = ap.parse_args()

    root = Path(a.work)
    root.mkdir(parents=True, exist_ok=True)
    devices = [d.strip() for d in a.devices.split(",") if d.strip()]
    scale = [int(x) for x in a.scale.split(",")]
    sizes = [1, 10, 50, 200, 800, 3200, 12800]

    out: dict[str, Any] = {
        "experiment": "e0_micro",
        "started_at": time.time(),
        "counter": counter_name(),
        "pingpong": {},
        "model_check": {},
    }
    for device in devices:
        print(f"[e0] ping-pong on {device} ...", flush=True)
        out["pingpong"][device] = pingpong(device, sizes, a.reps, root)
        print(f"[e0]   alpha={out['pingpong'][device]['alpha_s'] * 1e3:.3f} ms "
              f"beta={out['pingpong'][device]['beta_s_per_token'] * 1e6:.3f} us/token", flush=True)
    for device in devices:
        print(f"[e0] model check on {device} ...", flush=True)
        out["model_check"][device] = verify_model(device, scale, root)

    ref = out["pingpong"].get("sqlite") or next(iter(out["pingpong"].values()))
    out["residency"] = residency([8, 16, 32, 64, 128], 4000)
    out["relative_cost"] = relative_cost(ref["alpha_s"], ref["beta_s_per_token"], [0.0, 1.0, 30.0])
    out["finished_at"] = time.time()

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[e0] wrote {a.out}")


if __name__ == "__main__":
    main()
