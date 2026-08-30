"""Collect E4 from its trace, including the outcome when it did not finish.

A negative result needs its evidence committed as much as a positive one does,
and more carefully: the claim in the paper is that a collective stalled because
the executor pool was smaller than the communicator, and that is only checkable
if the trace showing which rank was late, and for how long, is in the artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from ampi import util  # noqa: E402
from ampi.analysis import summarise  # noqa: E402
from ampi.device import SqliteDevice  # noqa: E402


def collect(job_dir: str) -> dict:
    job_dir = os.path.abspath(job_dir)
    dev = SqliteDevice(os.path.join(job_dir, "job.db"))
    dev.initialize()
    job_id = os.path.basename(job_dir.rstrip("/"))
    try:
        ranks = dev.query("SELECT * FROM rank WHERE job_id=? ORDER BY rank", (job_id,))
        colls = dev.query("SELECT * FROM coll WHERE job_id=? ORDER BY created_at", (job_id,))
        comm = dev.query_one("SELECT * FROM comm WHERE job_id=? AND name='world'", (job_id,))

        stalls = []
        for c in colls:
            if c["state"] == "complete":
                continue
            laggards = []
            for local in range(len(util.loads(comm["members"], []))):
                row = dev.query_one("SELECT value FROM counter WHERE job_id=? AND name=?",
                                    (job_id, f"collseq:{c['comm_id']}:{local}"))
                if int(row["value"] if row else 0) < int(c["seq"]):
                    laggards.append(local)
            last = dev.query_one("SELECT MAX(ts) AS t FROM event WHERE job_id=?", (job_id,))
            stalls.append({
                "collective": c["op"], "seq": c["seq"], "state": c["state"],
                "expected": c["expected"],
                "ranks_that_never_entered": laggards,
                "open_for_seconds": round((last["t"] or c["created_at"]) - c["created_at"], 1),
            })

        timeouts = dev.query(
            "SELECT rank, op, COUNT(*) AS n FROM event WHERE job_id=? AND ok=0 "
            "GROUP BY rank, op ORDER BY rank", (job_id,))
        joins = [r for r in ranks if r["started_at"]]
        join_spread = (max(r["started_at"] for r in joins) -
                       min(r["started_at"] for r in joins)) if len(joins) > 1 else 0.0

        artifact = os.path.join(job_dir, "artifact")
        modules = sorted(
            os.path.relpath(os.path.join(dirpath, name), artifact)
            for dirpath, _, names in os.walk(artifact) for name in names
            if name.endswith(".py"))
        claims = dev.query(
            "SELECT key, value FROM win_cell WHERE key LIKE 'module/%' AND key NOT LIKE "
            "'%summary%'", ())

        return {
            "job": job_id,
            "world_size": len(ranks),
            "ranks_that_joined": len(joins),
            "join_spread_seconds": round(join_spread, 1),
            "collectives": [{"op": c["op"], "seq": c["seq"], "state": c["state"]}
                            for c in colls],
            "stalled_collectives": stalls,
            "failed_operations": [dict(t) for t in timeouts],
            "module_claims": [{"key": c["key"],
                               "owner": (util.loads(c["value"], {}) or {}).get("owner")}
                              for c in claims],
            "artifact_files": modules,
            "trace_summary": summarise(job_dir),
        }
    finally:
        dev.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", default=os.path.join(os.path.dirname(__file__), "runs", "job"))
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..",
                                                      "results", "ampi_e4_software.json"))
    args = parser.parse_args()
    payload = collect(args.job)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    print(json.dumps({k: v for k, v in payload.items() if k != "trace_summary"}, indent=2)[:2200])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
