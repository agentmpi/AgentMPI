"""One scripted AgentMPI rank, used by the E1 microbenchmarks.

Deliberately calls the Python binding rather than the CLI: the CLI adds an
interpreter start-up per operation, which is real cost for an agent (whose turn
dwarfs it) but pure noise when the quantity being measured is the protocol's
own latency.  The agent-driven experiments use the CLI binding.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "..", "src"))

from ampi.core import collectives as coll  # noqa: E402
from ampi.core.runtime import Runtime  # noqa: E402
from ampi.device import open_device  # noqa: E402

JOB_DIR = os.environ["AMPI_JOB_DIR"]
RANK = int(os.environ["AMPI_RANK"])
JOB_ID = os.path.basename(JOB_DIR.rstrip("/"))
SCRATCH = os.path.join(JOB_DIR, "ranks", str(RANK))


def connect() -> Runtime:
    device = open_device(os.path.join(JOB_DIR, "job.db"))
    rt = Runtime(device, JOB_ID, RANK, poll_interval=0.005, failure_timeout=1e9)
    rt.deadlock_grace = 1e9  # the benchmark is the only writer; no detection needed
    rt.init(RANK, role="bench")
    return rt


def finish(rt: Runtime, payload: dict) -> None:
    with open(os.path.join(SCRATCH, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    rt.finalize("bench done")
    rt.device.close()


def kernel_pingpong(rt: Runtime, chars: int, iters: int) -> None:
    body = "lorem ipsum dolor sit amet " * max(1, chars // 27)
    body = body[:chars] if chars > 0 else "x"
    coll.barrier(rt, "world", timeout=300)
    rtts: list[float] = []
    modes: list[str] = []
    if RANK == 0:
        for _ in range(iters):
            started = time.time()
            sent = rt.send("world", 1, 1, body)
            rt.recv("world", 1, 2, timeout=300, deref=True, charge_context=False)
            rtts.append(time.time() - started)
            modes.append(sent["mode"])
            payload_tokens = sent["tokens"]
        from ampi import util as _u
        finish(rt, {"rank": RANK, "rtts": rtts, "modes": modes,
                    "payload_tokens": payload_tokens,
                    "body_tokens": _u.count_tokens(body)})
    else:
        for _ in range(iters):
            rt.recv("world", 0, 1, timeout=300, deref=True, charge_context=False)
            rt.send("world", 0, 2, body)
        finish(rt, {"rank": RANK, "rtts": [], "modes": [], "payload_tokens": 0,
                    "body_tokens": 0})


def kernel_barrier(rt: Runtime, algo: str, iters: int) -> None:
    coll.barrier(rt, "world", algo=algo, timeout=600)
    durations: list[float] = []
    steps = 0
    for _ in range(iters):
        started = time.time()
        result = coll.barrier(rt, "world", algo=algo, timeout=600)
        durations.append(time.time() - started)
        steps = result["steps"]
    finish(rt, {"rank": RANK, "durations": durations, "steps": steps})


def kernel_allreduce(rt: Runtime, algo: str, entries: int, iters: int) -> None:
    # A sparse keyed vector: each rank contributes its own block of the key
    # space plus a shared block, so the reduction has real work to do and the
    # correct answer is known in closed form.
    world = rt.comms.get("world").size
    contribution = {f"k{RANK}-{i}": f"v{RANK}-{i}" for i in range(entries)}
    contribution["shared"] = "same-everywhere"
    expected = {"shared": "same-everywhere"}
    for r in range(world):
        for i in range(entries):
            expected[f"k{r}-{i}"] = f"v{r}-{i}"

    coll.barrier(rt, "world", timeout=600)
    durations: list[float] = []
    correct = True
    steps = 0
    peak = 0
    for _ in range(iters):
        started = time.time()
        result = coll.allreduce(rt, "world", contribution, "AMPI_MERGE_JSON", algo=algo,
                                timeout=900, datatype="vector")
        durations.append(time.time() - started)
        steps = result["steps"]
        peak = max(peak, result.get("peak_resident_tokens") or 0)
        correct = correct and result["result"] == expected
        coll.barrier(rt, "world", timeout=600)
    row = rt.rank_row()
    from ampi import util

    finish(rt, {"rank": RANK, "durations": durations, "steps": steps, "correct": correct,
                "ctx_peak": peak, "charged_ctx_peak": row["ctx_peak"],
                "tokens_sent": row["tokens_sent"],
                "payload_tokens": util.count_tokens(util.dumps(contribution)),
                "sends": rt.device.query_one(
                    "SELECT COUNT(*) AS n FROM message WHERE job_id=? AND src=?",
                    (JOB_ID, RANK))["n"]})


def kernel_residency(rt: Runtime, entries: int, iters: int) -> None:
    """Peak context residency of the reduction, per algorithm.

    Each rank contributes a keyed vector; we run the same reduction under every
    admissible algorithm and record the largest number of tokens the rank had
    to hold simultaneously.  The number the feasibility argument turns on is
    the reduce-scatter one, because it is the only family whose reduction phase
    residency falls with p.
    """
    from ampi import util

    contribution = {f"k{RANK}-{i}": ("v" * 40) + f"{RANK}-{i}" for i in range(entries)}
    n_tokens = util.count_tokens(util.dumps(contribution))
    world = rt.comms.get("world").size
    out: dict[str, dict] = {}

    for algo in ("recursive_doubling", "binomial", "linear"):
        coll.barrier(rt, "world", timeout=600)
        peak, dur = 0, 0.0
        for _ in range(iters):
            started = time.time()
            r = coll.allreduce(rt, "world", contribution, "AMPI_MERGE_JSON", algo=algo,
                               timeout=900, datatype="vector")
            dur += time.time() - started
            peak = max(peak, r.get("peak_resident_tokens") or 0)
        out[f"allreduce/{algo}"] = {"peak_resident_tokens": peak,
                                    "seconds": round(dur / iters, 4)}

    for algo in ("recursive_doubling", "ring"):
        if algo == "recursive_doubling" and (world & (world - 1)) != 0:
            continue
        coll.barrier(rt, "world", timeout=600)
        peak, dur = 0, 0.0
        for _ in range(iters):
            started = time.time()
            r = coll.reduce_scatter(rt, "world", contribution, "AMPI_MERGE_JSON", algo=algo,
                                    timeout=900, datatype="vector")
            dur += time.time() - started
            peak = max(peak, r.get("peak_resident_tokens") or 0)
        out[f"reduce_scatter/{algo}"] = {"peak_resident_tokens": peak,
                                         "seconds": round(dur / iters, 4)}

    coll.barrier(rt, "world", timeout=600)
    finish(rt, {"rank": RANK, "contribution_tokens": n_tokens, "p": world,
                "vector_tokens_total": n_tokens * world, "algorithms": out})


def kernel_window(rt: Runtime, iters: int) -> None:
    rt.win_create("world", "bench")
    coll.barrier(rt, "world", timeout=600)
    ops: dict[str, list[float]] = {"put": [], "get": [], "fetch_add": [], "cas": [], "lock": []}
    claims = 0
    cas_retries = 0

    for i in range(iters):
        started = time.time()
        rt.win_put("bench", f"private/{RANK}/{i}", {"i": i, "by": RANK})
        ops["put"].append(time.time() - started)

        started = time.time()
        rt.win_get("bench", f"private/{RANK}/{i}")
        ops["get"].append(time.time() - started)

        started = time.time()
        rt.win_fetch_and_op("bench", "counter", 1.0)
        ops["fetch_add"].append(time.time() - started)

        # Contended compare-and-swap on one shared cell: read, modify, retry.
        started = time.time()
        while True:
            current = rt.win_get("bench", "hot")
            new = (current["value"] or 0) + 1 if current["found"] else 1
            if rt.win_put("bench", "hot", new,
                          expected_version=current["version"])["ok"]:
                break
            cas_retries += 1
        ops["cas"].append(time.time() - started)

        started = time.time()
        lock = rt.win_lock("bench", "shared-doc", ttl=30, timeout=120)
        rt.win_unlock(lock["lock_id"])
        ops["lock"].append(time.time() - started)

        if rt.win_claim("bench", f"task/{i}")["claimed"]:
            claims += 1

    coll.barrier(rt, "world", timeout=600)
    finish(rt, {"rank": RANK, "ops": ops, "claims": claims, "cas_retries": cas_retries})


def main() -> int:
    kernel = sys.argv[1]
    rt = connect()
    if kernel == "pingpong":
        kernel_pingpong(rt, int(sys.argv[2]), int(sys.argv[3]))
    elif kernel == "barrier":
        kernel_barrier(rt, sys.argv[2], int(sys.argv[3]))
    elif kernel == "allreduce":
        kernel_allreduce(rt, sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    elif kernel == "residency":
        kernel_residency(rt, int(sys.argv[2]), int(sys.argv[3]))
    elif kernel == "window":
        kernel_window(rt, int(sys.argv[2]))
    else:
        raise SystemExit(f"unknown kernel {kernel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
