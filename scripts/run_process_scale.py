"""Process-scale AgentMPI benchmark with machine-readable observations."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import platform
import statistics
import tempfile
import time
from pathlib import Path
from queue import Empty
from typing import Any

from agentmpi import ANY_SOURCE, ReduceOp, Runtime


def worker(
    db: str,
    session: str,
    rank: int,
    size: int,
    iterations: int,
    result_queue: mp.Queue[dict[str, Any]],
) -> None:
    runtime = Runtime.attach(db, session, rank, heartbeat_ttl=300)
    runtime.barrier(timeout=120)
    allreduce_start = time.perf_counter()
    totals = [
        runtime.allreduce(rank + 1, op=ReduceOp.SUM, timeout=120)
        for _ in range(iterations)
    ]
    allreduce_elapsed = time.perf_counter() - allreduce_start
    runtime.barrier(timeout=120)
    fanin_start = time.perf_counter()
    if rank == 0:
        values = [
            runtime.recv(source=ANY_SOURCE, tag="fanin", timeout=120).payload
            for _ in range(size - 1)
        ]
    else:
        runtime.send({"rank": rank, "value": rank * rank}, 0, tag="fanin")
        values = []
    runtime.barrier(timeout=120)
    fanin_elapsed = time.perf_counter() - fanin_start
    result_queue.put(
        {
            "rank": rank,
            "allreduce_seconds": allreduce_elapsed,
            "fanin_seconds": fanin_elapsed,
            "allreduce_correct": all(
                total == size * (size + 1) // 2 for total in totals
            ),
            "fanin_count": len(values),
        }
    )
    runtime.finalize()
    runtime.close()


def run_scale(size: int, iterations: int, directory: Path) -> dict[str, Any]:
    db = directory / f"scale-{size}.db"
    session = f"scale-{size}"
    Runtime.initialize(
        db,
        size=size,
        session_id=session,
        context_budget=8_000,
        mailbox_bytes=16 * 1024 * 1024,
        inline_token_limit=1_024,
        heartbeat_ttl=300,
    )
    queue: mp.Queue[dict[str, Any]] = mp.Queue()
    processes = [
        mp.Process(
            target=worker,
            args=(str(db), session, rank, size, iterations, queue),
        )
        for rank in range(size)
    ]
    wall_start = time.perf_counter()
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=240)
    wall_elapsed = time.perf_counter() - wall_start
    exitcodes = [process.exitcode for process in processes]
    for process in processes:
        if process.is_alive():
            process.kill()
            process.join()
    observations: list[dict[str, Any]] = []
    while True:
        try:
            observations.append(queue.get_nowait())
        except Empty:
            break
    allreduce_times = [
        float(item["allreduce_seconds"]) / iterations for item in observations
    ]
    fanin_times = [float(item["fanin_seconds"]) for item in observations]
    trace_runtime = Runtime(db, session, 0)
    trace = trace_runtime.trace()
    trace_runtime.close()
    return {
        "size": size,
        "iterations": iterations,
        "successful": len(observations) == size
        and all(code == 0 for code in exitcodes),
        "process_exitcodes": exitcodes,
        "observations_received": len(observations),
        "wall_seconds": wall_elapsed,
        "allreduce_seconds_per_iteration": {
            "median": statistics.median(allreduce_times) if allreduce_times else None,
            "p95": _percentile(allreduce_times, 0.95),
            "max": max(allreduce_times) if allreduce_times else None,
        },
        "fanin_seconds": {
            "median": statistics.median(fanin_times) if fanin_times else None,
            "p95": _percentile(fanin_times, 0.95),
            "max": max(fanin_times) if fanin_times else None,
        },
        "allreduce_correct": all(
            bool(item["allreduce_correct"]) for item in observations
        ),
        "root_fanin_count": next(
            (
                int(item["fanin_count"])
                for item in observations
                if int(item["rank"]) == 0
            ),
            None,
        ),
        "trace_event_count": len(trace),
        "raw": sorted(observations, key=lambda item: int(item["rank"])),
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * quantile))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="2,4,8,16,32,64,100")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/process_scale.json"),
    )
    args = parser.parse_args()
    sizes = [int(value) for value in args.sizes.split(",")]
    with tempfile.TemporaryDirectory(prefix="agentmpi-scale-") as temporary:
        directory = Path(temporary)
        runs = [run_scale(size, args.iterations, directory) for size in sizes]
    result = {
        "generated_at": time.time(),
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "processor": platform.processor(),
            "cpu_count": mp.cpu_count(),
            "transport": "SQLite WAL on local filesystem",
        },
        "runs": runs,
        "method": (
            "Each rank is an independent OS process. Timed region contains "
            "repeated strict allreduce plus one all-to-one fan-in; process startup "
            "is reported separately in wall_seconds."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    mp.set_start_method("spawn")
    main()
