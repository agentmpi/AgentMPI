#!/usr/bin/env python3
"""Protocol microbenchmarks.

These measure the protocol, not the agents.  Each rank runs a scripted
executor with a controllable synthetic think time, so the numbers isolate
what the collective schedules cost in *rounds* and in *peak context* -- the
two quantities that a harness designer can actually control -- from the
enormous, noisy, and largely irreducible cost of inference.

That separation is the same one the OSU micro-benchmarks make for MPI: you
do not learn what ``MPI_Allreduce`` costs by running a climate model, you
learn it by running allreduce.  The agent runs in Sections 7.2-7.4 of the
paper then tell us whether the microbenchmark's predictions hold when the
executor is a real model.

Benchmarks
----------
``rounds``
    Communication rounds on the critical path for every collective and every
    algorithm, from p = 2 to p = 128.  This is the quantity that multiplies
    by the per-turn cost, so it is the one that decides wall-clock.
``scan``
    Sequential chain versus parallel prefix, with a realistic per-turn cost.
    The headline scaling result.
``allreduce``
    Ring versus recursive doubling versus tree, showing that the HPC
    crossover at large message sizes does not appear here.
``context``
    The feasibility frontier: at what p does each collective stop fitting in
    a fixed context budget, and what does a contracting reduction operator
    buy.
``fanout``
    Measured versus predicted peak ingest for capacity-aware reduction trees.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import agentmpi as ampi  # noqa: E402
from agentmpi import sim  # noqa: E402
from agentmpi.constants import CollAlgorithm  # noqa: E402
from agentmpi.context import (  # noqa: E402
    peak_ingest_allgather,
    plan_reduction,
    safe_fanout,
)
from agentmpi.ops import op_create  # noqa: E402

SIZES = [2, 4, 8, 16, 32, 64, 128]


# --------------------------------------------------------------------------
# 1. Round counts
# --------------------------------------------------------------------------

def bench_rounds(sizes: list[int]) -> list[dict]:
    """Count communication rounds on the critical path.

    We count rounds rather than time because rounds are the invariant: a
    round is one agent turn on the critical path, and the wall-clock cost is
    rounds times the per-turn latency, which varies by model and by day.  An
    HPC reader should read a "round" here the way they read an ``alpha`` term.
    """
    out: list[dict] = []
    for p in sizes:
        events: list[dict] = []

        def body(comm):
            comm.barrier(algorithm=CollAlgorithm.DISSEMINATION, timeout=120)
            comm.bcast("x" if comm.rank == 0 else None, 0,
                       algorithm=CollAlgorithm.BINOMIAL, timeout=120)
            comm.bcast("x" if comm.rank == 0 else None, 0,
                       algorithm=CollAlgorithm.FLAT, timeout=120)
            comm.allreduce(1, ampi.SUM, algorithm=CollAlgorithm.RECURSIVE_DOUBLING,
                           timeout=120)
            comm.scan(1, ampi.SUM, algorithm=CollAlgorithm.RECURSIVE_DOUBLING,
                      timeout=120)
            comm.scan(1, ampi.SUM, algorithm=CollAlgorithm.CHAIN, timeout=120)
            return True

        result = sim.run(p, body, timeout=300)
        result.raise_errors()
        events = result.events
        per_op: dict[tuple[str, str], list[int]] = {}
        for e in events:
            if e.get("kind") != "coll":
                continue
            key = (e.get("op", "?"), e.get("algorithm", "?"))
            per_op.setdefault(key, []).append(int((e.get("detail") or {}).get("steps", 0)))
        for (op, alg), steps in sorted(per_op.items()):
            out.append({
                "p": p, "collective": op, "algorithm": alg,
                "max_rounds": max(steps), "mean_rounds": round(statistics.mean(steps), 2),
                "log2p": round(math.log2(p), 2) if p > 1 else 0,
            })
        print(f"  rounds p={p}: " + ", ".join(
            f"{op}/{alg}={max(s)}" for (op, alg), s in sorted(per_op.items())))
    return out


# --------------------------------------------------------------------------
# 2. Scan: chain versus parallel prefix
# --------------------------------------------------------------------------

def bench_scan(sizes: list[int], think_s: float) -> list[dict]:
    """The carried-dependency benchmark.

    A harness that threads shared state through its workers one at a time
    pays p-1 turns; the same dependency expressed as a prefix scan costs
    ceil(log2 p).  With a per-turn cost of tens of seconds, the difference
    between 63 and 6 rounds is the difference between a job that finishes and
    one that does not.
    """
    out: list[dict] = []
    for p in sizes:
        row: dict[str, float | int] = {"p": p, "think_s": think_s}
        for alg in (CollAlgorithm.CHAIN, CollAlgorithm.RECURSIVE_DOUBLING):
            def body(comm, alg=alg):
                # A turn of "thinking" before contributing, as an agent would.
                time.sleep(think_s)
                t0 = time.time()
                comm.exscan({f"t{comm.rank}": [comm.rank]}, ampi.FIRST,
                            algorithm=alg, timeout=600)
                return time.time() - t0

            t0 = time.time()
            result = sim.run(p, body, timeout=900)
            result.raise_errors()
            wall = time.time() - t0
            steps = max(
                int((e.get("detail") or {}).get("steps", 0))
                for e in result.events if e.get("kind") == "coll"
            )
            row[f"{alg.value}_wall_s"] = round(wall, 3)
            row[f"{alg.value}_collective_s"] = round(max(result.ordered()), 3)
            row[f"{alg.value}_rounds"] = steps
        row["round_speedup"] = round(
            row["chain_rounds"] / max(row["recursive_doubling_rounds"], 1), 2)
        row["wall_speedup"] = round(
            row["chain_collective_s"] / max(row["recursive_doubling_collective_s"], 1e-6), 2)
        out.append(row)
        print(f"  scan p={p}: chain {row['chain_rounds']} rounds / "
              f"{row['chain_collective_s']}s, prefix "
              f"{row['recursive_doubling_rounds']} rounds / "
              f"{row['recursive_doubling_collective_s']}s "
              f"({row['wall_speedup']}x)")
    return out


# --------------------------------------------------------------------------
# 3. Allreduce algorithms
# --------------------------------------------------------------------------

def bench_allreduce(sizes: list[int], payload_tokens: int) -> list[dict]:
    """Ring versus recursive doubling versus tree.

    In MPI the ring wins at large messages because it is bandwidth optimal.
    Here bandwidth is nearly free and every round is an agent turn, so the
    ring's 2(p-1) rounds are a straight loss at every size.  The benchmark
    exists to show that the crossover really does disappear rather than
    merely being asserted.
    """
    payload = "w " * max(payload_tokens, 1)
    out: list[dict] = []
    for p in sizes:
        row: dict = {"p": p, "payload_tokens": payload_tokens}
        for alg in (CollAlgorithm.RECURSIVE_DOUBLING, CollAlgorithm.RING,
                    CollAlgorithm.FLAT):
            def body(comm, alg=alg):
                t0 = time.time()
                comm.allreduce({f"r{comm.rank}": [payload]}, ampi.UNION,
                               algorithm=alg, timeout=900)
                return time.time() - t0

            try:
                result = sim.run(p, body, timeout=900,
                                 cvars={"ampi_context_capacity": 100_000_000})
                result.raise_errors()
                steps = max(int((e.get("detail") or {}).get("steps", 0))
                            for e in result.events if e.get("kind") == "coll")
                row[f"{alg.value}_rounds"] = steps
                row[f"{alg.value}_s"] = round(max(result.ordered()), 3)
            except Exception as exc:  # pragma: no cover - recorded, not fatal
                row[f"{alg.value}_error"] = str(exc)[:120]
        out.append(row)
        print(f"  allreduce p={p}: " + ", ".join(
            f"{k}={v}" for k, v in row.items() if k.endswith("_rounds")))
    return out


# --------------------------------------------------------------------------
# 4. Context feasibility frontier
# --------------------------------------------------------------------------

def bench_context(sizes: list[int], budget: int, item_tokens: int) -> list[dict]:
    """Where each collective stops fitting.

    This is the analysis MPI never has to do.  An MPI collective is always
    *feasible*; it may be slow, but no rank is unable to hold its share.
    With agents, feasibility is the binding constraint, and it is a property
    of the collective's definition, not of the implementation: allgather
    requires every rank to hold everything, so it fails at
    ``p > budget/item``, whatever algorithm you choose.
    """
    out: list[dict] = []
    contracting = op_create(lambda a, b: {"digest": "merged"}, name="bounded_merge",
                            associative=True, output_tokens=item_tokens)
    for p in sizes:
        allgather_peak = peak_ingest_allgather(p, item_tokens)
        flat_reduce_peak = item_tokens * (p - 1)
        plan = plan_reduction(p, item_tokens, budget, output_tokens=item_tokens)
        out.append({
            "p": p,
            "budget_tokens": budget,
            "item_tokens": item_tokens,
            "allgather_peak_tokens": allgather_peak,
            "allgather_feasible": allgather_peak <= budget,
            "flat_reduce_peak_tokens": flat_reduce_peak,
            "flat_reduce_feasible": flat_reduce_peak <= budget,
            "tree_reduce_peak_tokens": plan.peak_ingest,
            "tree_reduce_feasible": plan.feasible,
            "tree_fanout": plan.fanout,
            "tree_rounds": plan.rounds,
            "bcast_peak_tokens": item_tokens,
        })
        print(f"  context p={p}: allgather {allgather_peak} tok "
              f"({'ok' if allgather_peak <= budget else 'INFEASIBLE'}), "
              f"tree reduce {plan.peak_ingest} tok in {plan.rounds} rounds")
    return out


# --------------------------------------------------------------------------
# 5. Predicted versus measured peak ingest
# --------------------------------------------------------------------------

def bench_fanout(sizes: list[int], budget: int, item_tokens: int) -> list[dict]:
    """Validate the capacity model against what the runtime actually ingests."""
    from agentmpi.tokens import count_tokens

    out: list[dict] = []
    payload = {"text": "z " * max(item_tokens, 1)}
    # The model is stated in tokens, so it must be validated against the
    # payload's *actual* token cost, not its nominal one; and the runtime's
    # usable budget must be the same number the planner was given, or the
    # comparison measures the mismatch rather than the model.
    actual_item = count_tokens(json.dumps(payload, ensure_ascii=False,
                                          indent=2, sort_keys=True))
    capacity = int(budget / (1.0 - 0.35) * 1.10)
    summarise = op_create(
        lambda a, b: {"text": "z " * max(item_tokens, 1)},
        name="bounded_summary", associative=True, output_tokens=actual_item,
    )
    for p in sizes:
        predicted = plan_reduction(p, actual_item, budget, output_tokens=actual_item)

        def body(comm):
            comm.reduce(payload, summarise, 0, algorithm=CollAlgorithm.KNOMIAL,
                        timeout=600)
            return comm.runtime.budget.ingested

        result = sim.run(p, body, timeout=600,
                         cvars={"ampi_context_capacity": capacity})
        result.raise_errors()
        measured = max(v for v in result.ordered() if v is not None)
        out.append({
            "p": p, "item_tokens": actual_item, "nominal_item_tokens": item_tokens,
            "budget": budget, "rank_capacity": capacity,
            "predicted_fanout": predicted.fanout,
            "predicted_rounds": predicted.rounds,
            "predicted_peak_tokens": predicted.peak_ingest,
            "measured_peak_tokens": measured,
            "error_pct": round(100 * (measured - predicted.peak_ingest)
                              / max(predicted.peak_ingest, 1), 1),
            "within_prediction": measured <= predicted.peak_ingest * 1.05,
        })
        print(f"  fanout p={p}: predicted peak {predicted.peak_ingest}, "
              f"measured {measured} (k={predicted.fanout}, "
              f"{predicted.rounds} rounds)")
    return out


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "experiments" / "results" / "microbench.json"))
    ap.add_argument("--max-p", type=int, default=128)
    ap.add_argument("--think", type=float, default=0.05)
    ap.add_argument("--budget", type=int, default=12_000)
    ap.add_argument("--item-tokens", type=int, default=1000)
    ap.add_argument("--only", default="", help="comma-separated subset")
    args = ap.parse_args()

    sizes = [p for p in SIZES if p <= args.max_p]
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    report: dict = {"sizes": sizes, "think_s": args.think,
                    "budget_tokens": args.budget, "item_tokens": args.item_tokens,
                    "generated_at": time.time()}

    def want(name: str) -> bool:
        return not only or name in only

    if want("rounds"):
        print("[rounds]")
        report["rounds"] = bench_rounds(sizes)
    if want("scan"):
        print("[scan: chain vs parallel prefix]")
        report["scan"] = bench_scan([p for p in sizes if p <= 64], args.think)
    if want("allreduce"):
        print("[allreduce algorithms]")
        report["allreduce"] = bench_allreduce([p for p in sizes if p <= 32], 200)
    if want("context"):
        print("[context feasibility]")
        report["context"] = bench_context(sizes, args.budget, args.item_tokens)
    if want("fanout"):
        print("[capacity-aware reduction]")
        report["fanout"] = bench_fanout([p for p in sizes if p <= 64],
                                        args.budget, args.item_tokens)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
