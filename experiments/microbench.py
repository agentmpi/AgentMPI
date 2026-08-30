#!/usr/bin/env python3
"""Protocol microbenchmarks: the NAS-style kernel suite for AgentMPI.

Measures barrier, broadcast, allreduce, allgather, and point-to-point
latency/bandwidth across communicator sizes. These numbers characterize
the filesystem transport, not LLM time — they are the analog of MPI
ping-pong and IMB (Intel MPI Benchmarks).
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentmpi.types import Op
from experiments.common import run_spmd, write_result


def _repeat(n: int, inner):
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        inner()
        samples.append(time.perf_counter() - t0)
    return {
        "min_s": min(samples),
        "median_s": statistics.median(samples),
        "mean_s": statistics.mean(samples),
        "max_s": max(samples),
    }


def bench_barrier(iters: int):
    def fn(comm):
        comm.barrier(timeout_s=30)
        stats = _repeat(iters, lambda: comm.barrier(timeout_s=30))
        return stats if comm.rank == 0 else None

    return fn


def bench_bcast(iters: int, payload):
    def fn(comm):
        comm.barrier(timeout_s=30)
        stats = _repeat(iters, lambda: comm.bcast(payload if comm.rank == 0 else None, root=0, timeout_s=30))
        return stats if comm.rank == 0 else None

    return fn


def bench_allreduce(iters: int):
    def fn(comm):
        comm.barrier(timeout_s=30)
        stats = _repeat(iters, lambda: comm.allreduce(1, op=Op.SUM, timeout_s=30))
        return stats if comm.rank == 0 else None

    return fn


def bench_allgather(iters: int):
    def fn(comm):
        comm.barrier(timeout_s=30)
        stats = _repeat(iters, lambda: comm.allgather(comm.rank, timeout_s=30))
        return stats if comm.rank == 0 else None

    return fn


def bench_pingpong(iters: int, payload):
    def fn(comm):
        if comm.size < 2:
            return None
        comm.barrier(timeout_s=30)

        def once():
            if comm.rank == 0:
                comm.send(payload, dest=1, tag=1)
                comm.recv(source=1, tag=2, timeout_s=30)
            elif comm.rank == 1:
                x = comm.recv(source=0, tag=1, timeout_s=30)
                comm.send(x, dest=0, tag=2)

        stats = _repeat(iters, once)
        return stats if comm.rank == 0 else None

    return fn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="experiments/results/microbench.json")
    parser.add_argument("--iters", type=int, default=8)
    args = parser.parse_args()
    sizes = [2, 4, 8, 16]
    rows = []
    payload_small = {"k": "v"}
    payload_large = {"blob": "x" * 20_000}
    for n in sizes:
        home = Path("experiments/results/.ampi") / f"micro-{n}"
        for name, factory in [
            ("barrier", bench_barrier(args.iters)),
            ("bcast_small", bench_bcast(args.iters, payload_small)),
            ("bcast_large", bench_bcast(args.iters, payload_large)),
            ("allreduce_sum", bench_allreduce(args.iters)),
            ("allgather", bench_allgather(args.iters)),
            ("pingpong_small", bench_pingpong(args.iters, payload_small)),
            ("pingpong_large", bench_pingpong(args.iters, payload_large)),
        ]:
            results, summary = run_spmd(home / name, n, factory)
            rows.append({"kernel": name, "n": n, "timing": results[0], **{k: summary[k] for k in ("elapsed_s", "sends", "bytes", "eager", "rendezvous")}})
            print(f"n={n:3d} {name:16s} median={results[0]['median_s']*1000:.2f} ms  sends={summary['sends']}")
    write_result(Path(args.out), {"suite": "microbench", "rows": rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
