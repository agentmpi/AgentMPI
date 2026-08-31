"""E1 --- Protocol microbenchmarks: what does AgentMPI itself cost?

The ranks here are scripted OS processes, not language models, and that is the
point.  An agent turn costs seconds and varies by an order of magnitude, so a
measurement taken with model-driven ranks tells you about the model, not about
the protocol.  To characterise the protocol we need ranks that do nothing but
call it.  These numbers therefore bound the overhead AgentMPI adds; the
agent-driven experiments then show what happens when that overhead is placed
underneath something whose per-step cost is a thousand times larger.

Four kernels, following the shape of the OSU micro-benchmark suite:

``pingpong``   half round-trip latency between two ranks, sweeping payload
               size, which fits the alpha and beta terms of the cost model and
               exposes the eager/rendezvous crossover.
``barrier``    barrier latency against communicator size, for each barrier
               algorithm, which exposes the lg p versus p separation.
``allreduce``  allreduce latency and *peak context residency* against
               communicator size and payload size, for every admissible
               algorithm.  This is the measurement the paper's feasibility
               argument rests on.
``window``     one-sided operation rates under contention: uncontended put,
               contended compare-and-swap, atomic counter, and lock
               acquisition, which is what the coordination patterns cost.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "src"))

from ampi.launch import create_job, launch_processes, wait_for  # noqa: E402

WORKER = os.path.join(HERE, "worker.py")


def _run_job(root: str, name: str, world: int, args: list[str], ctx_limit: int = 4_000_000,
             timeout: float = 900.0) -> tuple[dict, float]:
    job_dir = os.path.join(root, name)
    shutil.rmtree(job_dir, ignore_errors=True)  # never reuse a job: window state persists
    create_job(job_dir, world, ctx_limit=ctx_limit, meta={"kernel": name})
    started = time.time()
    procs = launch_processes(job_dir, world, [sys.executable, WORKER, *args])
    codes = wait_for(procs, timeout)
    wall = time.time() - started
    if any(c != 0 for c in codes):
        tail = ""
        log = os.path.join(job_dir, "ranks", "0", "stdout.log")
        if os.path.exists(log):
            tail = open(log, encoding="utf-8").read()[-1500:]
        raise SystemExit(f"{name}: ranks exited {codes}\n{tail}")
    results = {}
    for rank in range(world):
        path = os.path.join(job_dir, "ranks", str(rank), "result.json")
        if os.path.exists(path):
            results[rank] = json.load(open(path, encoding="utf-8"))
    return {"job_dir": job_dir, "ranks": results}, wall


def _dist(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    idx = lambda q: ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]  # noqa: E731
    return {"n": len(ordered), "min": round(ordered[0] * 1000, 3),
            "p50": round(statistics.median(ordered) * 1000, 3),
            "p95": round(idx(0.95) * 1000, 3), "max": round(ordered[-1] * 1000, 3),
            "mean": round(statistics.fmean(ordered) * 1000, 3)}


def bench_pingpong(root: str, sizes: list[int], iters: int) -> list[dict]:
    out = []
    for size in sizes:
        job, wall = _run_job(root, f"pingpong-{size}", 2,
                             ["pingpong", str(size), str(iters)])
        rtts = job["ranks"][0]["rtts"]
        modes = job["ranks"][0]["modes"]
        out.append({"payload_tokens": job["ranks"][0]["body_tokens"],
                    "transferred_tokens": job["ranks"][0]["payload_tokens"],
                    "requested_chars": size, "iterations": iters,
                    "half_roundtrip_ms": _dist([r / 2 for r in rtts]),
                    "mode": modes[0] if modes else None, "wall_s": round(wall, 3)})
        print(f"  pingpong {size:>7} chars "
              f"({out[-1]['payload_tokens']:>6} tok, {out[-1]['mode']:>11}): "
              f"p50 {out[-1]['half_roundtrip_ms']['p50']:.2f} ms")
    return out


def bench_barrier(root: str, worlds: list[int], algos: list[str], iters: int) -> list[dict]:
    out = []
    for world in worlds:
        for algo in algos:
            job, wall = _run_job(root, f"barrier-{algo}-{world}", world,
                                 ["barrier", algo, str(iters)])
            durations = [d for r in job["ranks"].values() for d in r["durations"]]
            out.append({"p": world, "algo": algo, "iterations": iters,
                        "latency_ms": _dist(durations), "wall_s": round(wall, 3),
                        "steps_per_rank": job["ranks"][0]["steps"]})
            print(f"  barrier p={world:<3} {algo:<14} steps={out[-1]['steps_per_rank']:<3} "
                  f"p50 {out[-1]['latency_ms']['p50']:.1f} ms")
    return out


def bench_allreduce(root: str, worlds: list[int], algos: list[str], entries: int,
                    iters: int) -> list[dict]:
    out = []
    for world in worlds:
        for algo in algos:
            if algo == "rabenseifner" and (world & (world - 1)) != 0:
                continue
            name = f"allreduce-{algo}-{world}-{entries}"
            try:
                job, wall = _run_job(root, name, world,
                                     ["allreduce", algo, str(entries), str(iters)])
            except SystemExit as exc:
                print(f"  allreduce p={world} {algo}: FAILED ({exc})")
                continue
            durations = [d for r in job["ranks"].values() for d in r["durations"]]
            peaks = [r["ctx_peak"] for r in job["ranks"].values()]
            sends = sum(r["sends"] for r in job["ranks"].values())
            tokens = sum(r["tokens_sent"] for r in job["ranks"].values())
            out.append({"p": world, "algo": algo, "entries": entries,
                        "payload_tokens": job["ranks"][0]["payload_tokens"],
                        "latency_ms": _dist(durations), "messages_total": sends,
                        "tokens_moved_total": tokens,
                        "peak_resident_tokens": max(peaks) if peaks else None,
                        "steps_per_rank": job["ranks"][0]["steps"],
                        "correct": all(r["correct"] for r in job["ranks"].values()),
                        "wall_s": round(wall, 3)})
            print(f"  allreduce p={world:<3} {algo:<18} steps={out[-1]['steps_per_rank']:<4} "
                  f"msgs={sends:<5} p50 {out[-1]['latency_ms']['p50']:>8.1f} ms  "
                  f"peak {out[-1]['peak_resident_tokens']:>7} tok  "
                  f"{'ok' if out[-1]['correct'] else 'WRONG'}")
    return out


def bench_residency(root: str, worlds: list[int], entry_counts: list[int],
                    iters: int) -> list[dict]:
    out = []
    for world in worlds:
        for entries in entry_counts:
            name = f"residency-{world}-{entries}"
            try:
                job, wall = _run_job(root, name, world, ["residency", str(entries), str(iters)])
            except SystemExit as exc:
                print(f"  residency p={world} entries={entries}: FAILED ({exc})")
                continue
            per_rank = list(job["ranks"].values())
            merged: dict[str, int] = {}
            for r in per_rank:
                for algo, stats in r["algorithms"].items():
                    merged[algo] = max(merged.get(algo, 0), stats["peak_resident_tokens"])
            entry = {"p": world, "entries_per_rank": entries,
                     "contribution_tokens": per_rank[0]["contribution_tokens"],
                     "vector_tokens": per_rank[0]["vector_tokens_total"],
                     "peak_resident_tokens": merged,
                     "seconds": {a: per_rank[0]["algorithms"][a]["seconds"]
                                 for a in per_rank[0]["algorithms"]},
                     "wall_s": round(wall, 3)}
            n = entry["vector_tokens"]
            entry["peak_over_n"] = {a: round(v / n, 4) for a, v in merged.items()}
            out.append(entry)
            print(f"  residency p={world:<3} n={n:>7} tok  " + "  ".join(
                f"{a.split('/')[0][:9]}/{a.split('/')[1][:9]}={v:>7}({entry['peak_over_n'][a]:.2f}n)"
                for a, v in sorted(merged.items())))
    return out


def bench_window(root: str, worlds: list[int], iters: int) -> list[dict]:
    out = []
    for world in worlds:
        job, wall = _run_job(root, f"window-{world}", world, ["window", str(iters)])
        merged: dict[str, list[float]] = {}
        claims = 0
        cas_retries = 0
        for r in job["ranks"].values():
            for key, values in r["ops"].items():
                merged.setdefault(key, []).extend(values)
            claims += r["claims"]
            cas_retries += r["cas_retries"]
        out.append({"p": world, "iterations": iters,
                    "latency_ms": {k: _dist(v) for k, v in sorted(merged.items())},
                    "successful_claims": claims, "cas_retries": cas_retries,
                    "wall_s": round(wall, 3)})
        print(f"  window p={world:<3} claims={claims:<4} cas_retries={cas_retries:<5} "
              + "  ".join(f"{k} p50 {v['p50']:.1f}ms" for k, v in
                          sorted(out[-1]['latency_ms'].items())))
    return out


def model_fit(pingpong: list[dict]) -> dict:
    """Least-squares fit of the alpha-beta model to the ping-pong sweep."""
    xs = [p["payload_tokens"] for p in pingpong]
    ys = [p["half_roundtrip_ms"]["p50"] / 1000.0 for p in pingpong]
    n = len(xs)
    if n < 2:
        return {}
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    beta = (
        sum(
            (x - mean_x) * (y - mean_y)
            for x, y in zip(xs, ys, strict=True)
        )
        / denom
        if denom
        else 0.0
    )
    alpha = mean_y - beta * mean_x
    return {"alpha_seconds": round(alpha, 6), "beta_seconds_per_token": round(beta, 9),
            "note": "half round-trip = alpha + beta * tokens, fitted over the payload sweep"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.join(HERE, "runs"))
    parser.add_argument("--out", default=os.path.join(HERE, "..", "..", "results",
                                                      "ampi_microbench.json"))
    parser.add_argument("--iters", type=int, default=20)
    parser.add_argument("--worlds", default="2,4,8,16,32")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.root, exist_ok=True)
    worlds = [int(w) for w in args.worlds.split(",")]
    sizes = [16, 256, 2048, 16384, 131072] if not args.quick else [16, 2048, 131072]

    print("ping-pong latency and bandwidth")
    pingpong = bench_pingpong(args.root, sizes, args.iters)
    print("barrier scaling")
    barrier = bench_barrier(args.root, worlds, ["linear", "dissemination"], args.iters)
    print("allreduce algorithms")
    allreduce = bench_allreduce(
        args.root, worlds,
        ["linear", "binomial", "recursive_doubling", "ring", "rabenseifner"],
        entries=64, iters=max(3, args.iters // 4))
    print("peak context residency by algorithm")
    residency = bench_residency(args.root, [w for w in worlds if w >= 4],
                                [16, 64, 256] if not args.quick else [32], max(1, args.iters // 8))
    print("one-sided operations under contention")
    window = bench_window(args.root, [w for w in worlds if w <= 32], args.iters)

    payload = {
        "kernel_ranks": "scripted OS processes, one per rank (not language models)",
        "device": "sqlite",
        "host": {"cpus": os.cpu_count()},
        "pingpong": pingpong,
        "alpha_beta_fit": model_fit(pingpong),
        "barrier": barrier,
        "allreduce": allreduce,
        "residency": residency,
        "window": window,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {args.out}")
    print(json.dumps(payload["alpha_beta_fit"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
