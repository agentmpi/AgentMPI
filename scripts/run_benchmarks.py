"""Run deterministic protocol microbenchmarks and emit raw JSON samples."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from agentmpi import ReduceOp, Runtime
from agentmpi.runtime import estimate_tokens


def summarize(samples_ns: list[int]) -> dict[str, float | int]:
    ordered = sorted(samples_ns)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    return {
        "n": len(samples_ns),
        "median_us": statistics.median(samples_ns) / 1_000,
        "mean_us": statistics.fmean(samples_ns) / 1_000,
        "p95_us": p95 / 1_000,
        "min_us": min(samples_ns) / 1_000,
        "max_us": max(samples_ns) / 1_000,
    }


def make_runtimes(
    root: Path,
    size: int,
    *,
    inline_token_limit: int = 2_048,
    context_budget: int = 1_000_000,
) -> tuple[Path, list[Runtime]]:
    db = root / "run.db"
    Runtime.initialize(
        db,
        size=size,
        session_id="bench",
        inline_token_limit=inline_token_limit,
        context_budget=context_budget,
        heartbeat_ttl=600,
    )
    runtimes = [
        Runtime.attach(db, "bench", rank, heartbeat_ttl=600) for rank in range(size)
    ]
    for runtime in runtimes:
        runtime.poll_interval = 0.0005
    return db, runtimes


def close_all(runtimes: list[Runtime]) -> None:
    for runtime in runtimes:
        runtime.close()


def ping_pong(iterations: int, payload_bytes: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db, runtimes = make_runtimes(root, 2)
        left, right = runtimes
        payload = "x" * payload_bytes

        def echo() -> None:
            for _ in range(iterations + 2):
                received = right.recv(source=0, tag="ping", timeout=30)
                right.send(received.payload, 0, tag="pong", timeout=30)

        with ThreadPoolExecutor(max_workers=1) as pool:
            echo_future = pool.submit(echo)
            for _ in range(2):
                left.send(payload, 1, tag="ping", timeout=30)
                left.recv(source=1, tag="pong", timeout=30)
            samples: list[int] = []
            artifact_spills = 0
            for _ in range(iterations):
                started = time.perf_counter_ns()
                status = left.send(payload, 1, tag="ping", timeout=30)
                left.recv(source=1, tag="pong", timeout=30)
                samples.append(time.perf_counter_ns() - started)
                artifact_spills += status.artifact_ref is not None
            echo_future.result()
        result = {
            "payload_bytes": payload_bytes,
            "estimated_tokens": estimate_tokens(payload),
            "artifact_spills": artifact_spills,
            "summary": summarize(samples),
            "samples_ns": samples,
            "database_bytes": db.stat().st_size,
        }
        close_all(runtimes)
        return result


def collective(
    iterations: int,
    size: int,
    operation: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        db, runtimes = make_runtimes(root, size)

        def invoke(pair: tuple[int, Runtime]) -> Any:
            rank, runtime = pair
            if operation == "barrier":
                return runtime.barrier(timeout=30)
            if operation == "allreduce":
                return runtime.allreduce(rank + 1, op=ReduceOp.SUM, timeout=30)
            raise AssertionError(f"unknown operation {operation}")

        samples: list[int] = []
        with ThreadPoolExecutor(max_workers=size) as pool:
            pool.map(invoke, enumerate(runtimes))
            for _ in range(iterations):
                started = time.perf_counter_ns()
                values = list(pool.map(invoke, enumerate(runtimes)))
                samples.append(time.perf_counter_ns() - started)
                if operation == "allreduce":
                    expected = size * (size + 1) // 2
                    if values != [expected] * size:
                        raise AssertionError("allreduce returned inconsistent values")
        result = {
            "operation": operation,
            "ranks": size,
            "summary": summarize(samples),
            "samples_ns": samples,
            "database_bytes": db.stat().st_size,
        }
        close_all(runtimes)
        return result


def context_externalization() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _, runtimes = make_runtimes(
            root,
            2,
            inline_token_limit=64,
            context_budget=100_000,
        )
        sender, receiver = runtimes
        value = {"text": "bulk context " * 5_000}
        status = sender.send(value, 1, tag="bulk")
        received = receiver.recv(source=0, tag="bulk", timeout=30)
        materialized = receiver.get_artifact(
            str(status.artifact_ref),
            charge_context=False,
        )
        result = {
            "original_estimated_tokens": estimate_tokens(value),
            "envelope_estimated_tokens": estimate_tokens(received.payload),
            "artifact": status.artifact_ref is not None,
            "resolved_bytes_equal": json.loads(materialized) == value,
            "artifact_bytes": len(materialized),
        }
        close_all(runtimes)
        return result


def failure_recovery(iterations: int) -> dict[str, Any]:
    samples: list[int] = []
    all_survivors: list[list[int]] = []
    for _ in range(iterations):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, runtimes = make_runtimes(root, 8)
            started = time.perf_counter_ns()
            runtimes[0].fail_rank(5, reason="benchmark")
            repaired = runtimes[0].shrink(runtimes[0].world)
            samples.append(time.perf_counter_ns() - started)
            all_survivors.append(list(repaired.members))
            close_all(runtimes)
    return {
        "old_ranks": 8,
        "failed_rank": 5,
        "survivors_consistent": all(
            members == [0, 1, 2, 3, 4, 6, 7] for members in all_survivors
        ),
        "summary": summarize(samples),
        "samples_ns": samples,
    }


def git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def run(iterations: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "timestamp_unix": time.time(),
        "commit": git_commit(),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
        },
        "parameters": {"iterations": iterations, "warmups": 2},
        "ping_pong": [
            ping_pong(iterations, payload_bytes)
            for payload_bytes in (16, 1_024, 8_192, 65_536)
        ],
        "collectives": [
            collective(iterations, size, operation)
            for operation in ("barrier", "allreduce")
            for size in (2, 4, 8, 16, 32)
        ],
        "context_externalization": context_externalization(),
        "failure_recovery": failure_recovery(iterations),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run(arguments.iterations)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

