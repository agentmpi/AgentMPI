"""Inject executor death during a collective and exercise revoke/shrink."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import tempfile
import time
from pathlib import Path
from queue import Empty
from typing import Any

from agentmpi import CommunicatorRevoked, ReduceOp, Runtime


def survivor(
    db: str,
    session: str,
    rank: int,
    size: int,
    victim: int,
    start: mp.Event,
    ready: mp.Queue[int],
    results: mp.Queue[dict[str, Any]],
) -> None:
    runtime = Runtime.attach(db, session, rank, heartbeat_ttl=300)
    ready.put(rank)
    start.wait(timeout=30)
    if rank == victim:
        runtime.close()
        os._exit(99)
    old_world = runtime.world
    if rank == 0:
        time.sleep(0.1)
        runtime.fail_rank(victim, reason="experiment_injected_exit")
    saw_revoke = False
    try:
        runtime.barrier(comm=old_world, timeout=30)
    except CommunicatorRevoked:
        saw_revoke = True
    repaired = runtime.shrink(old_world)
    total = runtime.allreduce(
        rank,
        op=ReduceOp.SUM,
        comm=repaired,
        timeout=60,
    )
    results.put(
        {
            "rank": rank,
            "saw_revoke": saw_revoke,
            "repaired_members": list(repaired.members),
            "reduced_rank_sum": total,
        }
    )
    runtime.finalize()
    runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=16)
    parser.add_argument("--victim", type=int, default=7)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/failure_recovery.json"),
    )
    args = parser.parse_args()
    if args.victim <= 0 or args.victim >= args.size:
        raise ValueError("victim must be a non-root rank")

    with tempfile.TemporaryDirectory(prefix="agentmpi-failure-") as temporary:
        db = Path(temporary) / "failure.db"
        session = "failure"
        Runtime.initialize(
            db,
            size=args.size,
            session_id=session,
            heartbeat_ttl=300,
        )
        start = mp.Event()
        ready: mp.Queue[int] = mp.Queue()
        results: mp.Queue[dict[str, Any]] = mp.Queue()
        processes = [
            mp.Process(
                target=survivor,
                args=(
                    str(db),
                    session,
                    rank,
                    args.size,
                    args.victim,
                    start,
                    ready,
                    results,
                ),
            )
            for rank in range(args.size)
        ]
        wall_start = time.perf_counter()
        for process in processes:
            process.start()
        ready_ranks = sorted(ready.get(timeout=60) for _ in range(args.size))
        start.set()
        for process in processes:
            process.join(timeout=120)
        wall_seconds = time.perf_counter() - wall_start
        exitcodes = [process.exitcode for process in processes]
        observations: list[dict[str, Any]] = []
        while True:
            try:
                observations.append(results.get_nowait())
            except Empty:
                break
        trace_runtime = Runtime(db, session, 0)
        trace = trace_runtime.trace()
        trace_runtime.close()

    expected_members = [rank for rank in range(args.size) if rank != args.victim]
    expected_sum = sum(expected_members)
    result = {
        "generated_at": time.time(),
        "size": args.size,
        "victim": args.victim,
        "ready_ranks": ready_ranks,
        "process_exitcodes": exitcodes,
        "victim_exitcode": exitcodes[args.victim],
        "survivor_observations": sorted(observations, key=lambda item: int(item["rank"])),
        "all_survivors_completed": len(observations) == args.size - 1,
        "all_survivors_saw_revoke": all(item["saw_revoke"] for item in observations),
        "consistent_repaired_membership": all(
            item["repaired_members"] == expected_members for item in observations
        ),
        "correct_post_repair_allreduce": all(
            item["reduced_rank_sum"] == expected_sum for item in observations
        ),
        "recovery_wall_seconds": wall_seconds,
        "trace_event_count": len(trace),
        "trace": trace,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    mp.set_start_method("spawn")
    main()
