#!/usr/bin/env python3
"""Fault-injection experiments.

Ranks are killed for real: the executor stops, stops heartbeating, and says
nothing. Survivors learn about it the only way anything ever learns about a
crash, from the absence of further evidence.

Executors here are scripted rather than agents, deliberately. The question
is what the *protocol* does when a participant disappears, and answering it
needs many repetitions at several failure counts, which is not something to
pay a frontier model for. The corresponding evidence from real agents is in
Section 8.2: two agents in our translation run left the protocol of their
own accord, and the mechanisms exercised here are the ones that detected it.

Configurations
--------------
``none``
    No fault tolerance. The harness calls a collective and waits. This is
    what every agent framework does today, and it establishes that the
    failure is fatal without the protocol.
``detect``
    Failure detection only: the survivors notice and report, but do not
    recover. Measures detection latency.
``shrink``
    ULFM recovery: revoke, agree on the survivor set, shrink, and complete
    the computation over the survivors.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import agentmpi as ampi  # noqa: E402
from agentmpi import sim  # noqa: E402
from agentmpi.errors import AmpiError, ProcFailedError, RevokedError  # noqa: E402
from agentmpi.ft import comm_agree, comm_revoke, comm_shrink  # noqa: E402

FAST = {
    "ampi_heartbeat_s": 0.25,
    "ampi_failure_timeout_s": 3.0,
    "ampi_stall_timeout_s": 8.0,
    "ampi_gap_timeout_s": 3.0,
    "ampi_coll_mismatch_grace_s": 1.0,
}


def victims(p: int, k: int) -> list[int]:
    """Spread the failures over the rank space rather than clustering them."""
    if k <= 0:
        return []
    step = max(p // (k + 1), 1)
    return [min((i + 1) * step, p - 1) for i in range(k)]


def run_case(p: int, k: int, mode: str, kill_delay: float = 0.6) -> dict:
    dead = victims(p, k)
    t0 = time.time()

    def body(comm):
        # Phase 1: the job starts healthy and every rank receives the spec.
        comm.bcast("spec" if comm.rank == 0 else None, 0, timeout=20)

        # Phase 2: a turn of work, during which the victims die. Without this
        # the collectives would finish before the kill fires and the
        # experiment would measure a healthy run.
        time.sleep(kill_delay + 1.2)

        detect_t0 = time.time()
        if mode == "none":
            # No recovery: just try the collective and see what happens.
            try:
                total = comm.allreduce(1, ampi.SUM, timeout=25)
                return {"outcome": "completed", "value": total,
                        "detect_s": None, "survivors": comm.size}
            except AmpiError as exc:
                return {"outcome": type(exc).__name__, "value": None,
                        "detect_s": round(time.time() - detect_t0, 2),
                        "survivors": None}

        # Detect: drive the failure detector until it fires.
        deadline = time.time() + 30
        while time.time() < deadline:
            comm.runtime.check_failures(comm)
            if comm.failed:
                break
            time.sleep(0.2)
        detect_s = round(time.time() - detect_t0, 2)
        if not comm.failed:
            return {"outcome": "not_detected", "detect_s": None,
                    "survivors": None}
        if mode == "detect":
            return {"outcome": "detected", "detect_s": detect_s,
                    "failed": sorted(comm.failed), "survivors": None}

        # Recover: agree on the survivors, shrink, and finish the job.
        try:
            comm.failure_ack()
            shrunk = comm_shrink(comm, timeout=45)
            total = shrunk.allreduce(1, ampi.SUM, timeout=45)
            return {"outcome": "recovered", "detect_s": detect_s,
                    "value": total, "survivors": shrunk.size,
                    "epoch": shrunk.epoch}
        except AmpiError as exc:
            return {"outcome": f"recovery_failed:{type(exc).__name__}",
                    "detect_s": detect_s, "survivors": None,
                    "detail": str(exc)[:200]}

    result = sim.run(p, body, cvars=FAST,
                     kill={r: kill_delay for r in dead}, timeout=150)
    wall = time.time() - t0

    outcomes = [v for r, v in sorted(result.results.items()) if r not in dead]
    kinds = [o["outcome"] for o in outcomes if o]
    detects = [o["detect_s"] for o in outcomes if o and o.get("detect_s")]
    survivor_counts = {o.get("survivors") for o in outcomes
                       if o and o.get("survivors") is not None}
    values = {o.get("value") for o in outcomes if o and o.get("value") is not None}

    expected_survivors = p - k
    return {
        "p": p, "failures": k, "mode": mode, "killed": dead,
        "wall_s": round(wall, 2),
        "survivors_reporting": len(outcomes),
        "outcomes": {kind: kinds.count(kind) for kind in sorted(set(kinds))},
        "detect_p50_s": round(statistics.median(detects), 2) if detects else None,
        "detect_max_s": round(max(detects), 2) if detects else None,
        "agreed_survivor_count": sorted(survivor_counts),
        "consistent_survivor_view": len(survivor_counts) <= 1,
        "allreduce_values": sorted(values),
        "correct": (mode == "shrink"
                    and values == {expected_survivors}
                    and survivor_counts == {expected_survivors}),
        "crashed_ranks": sorted(set(result.errors) - set(dead)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "experiments" / "results" / "faults.json"))
    ap.add_argument("--sizes", default="8,16")
    ap.add_argument("--failures", default="1,2,3")
    ap.add_argument("--repeats", type=int, default=2)
    args = ap.parse_args()

    sizes = [int(x) for x in args.sizes.split(",")]
    fails = [int(x) for x in args.failures.split(",")]
    rows: list[dict] = []
    for p in sizes:
        for k in fails:
            if k >= p - 1:
                continue
            for mode in ("none", "detect", "shrink"):
                for rep in range(args.repeats if mode != "none" else 1):
                    row = run_case(p, k, mode)
                    row["repeat"] = rep
                    rows.append(row)
                    print(f"p={p:>3} k={k} {mode:<7} -> {row['outcomes']} "
                          f"detect_p50={row['detect_p50_s']}s "
                          f"survivors={row['agreed_survivor_count']} "
                          f"correct={row['correct']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"cvars": FAST, "rows": rows}, indent=2),
                   encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
