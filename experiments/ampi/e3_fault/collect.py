"""Collect E3: what did the survivors manage to do after two agents were killed?

Three questions, and only the trace can answer them honestly:

* did the survivors *detect* the failures, and how long did it take them?
* did they repair the communicator --- revoke, shrink, agree --- and did they
  all end up on the same one?
* how much of the dead agents' work survived, and was it adopted from the
  window or redone?

The last is the one that matters most for the design argument. Window writes
are durable and outlive their author, so a survivor should be able to pick up a
dead peer's finished section rather than rewriting it. If instead the
survivors rewrote sections that already existed, the durability claim is empty.
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
        failures = dev.query(
            "SELECT rank, detected_at, detected_by, reason FROM failure WHERE job_id=? "
            "ORDER BY detected_at", (job_id,))
        killed = sorted({int(f["rank"]) for f in failures
                         if "injected" in (f["reason"] or "")})

        events = dev.query(
            "SELECT rank, ts, op, meta FROM event WHERE job_id=? AND op IN "
            "('AMPI_Comm_revoke','AMPI_Comm_shrink','AMPI_Comm_agree') ORDER BY ts",
            (job_id,))
        kill_ts = min([f["detected_at"] for f in failures], default=None)
        recovery = [{"rank": e["rank"], "op": e["op"],
                     "seconds_after_kill": round(e["ts"] - kill_ts, 1) if kill_ts else None,
                     "detail": util.loads(e["meta"], {})}
                    for e in events]

        comms = dev.query("SELECT name, members, meta FROM comm WHERE job_id=?", (job_id,))
        shrunk = [c for c in comms if util.loads(c["meta"], {}).get("shrunk_from")]

        cells = dev.query("SELECT * FROM win_cell", ())
        sections = {c["key"].split("/", 1)[1]: c for c in cells
                    if c["key"].startswith("section/")}
        by_dead = {k: c for k, c in sections.items() if int(c["updated_by"]) in killed}
        rewritten = {k: c for k, c in by_dead.items() if int(c["version"]) > 1}
        claims = [c["key"] for c in cells if c["key"].startswith("claim/")]

        return {
            "job": job_id,
            "world_size": len(ranks),
            "killed_ranks": killed,
            "survivors": sorted(int(r["rank"]) for r in ranks if int(r["rank"]) not in killed),
            "failure_records": [dict(f) for f in failures],
            "recovery_sequence": recovery,
            "revocations": sum(1 for e in events if e["op"] == "AMPI_Comm_revoke"),
            "shrinks": sum(1 for e in events if e["op"] == "AMPI_Comm_shrink"),
            "agreements": sum(1 for e in events if e["op"] == "AMPI_Comm_agree"),
            "shrunk_communicators": [
                {"name": c["name"], "members": util.loads(c["members"], []),
                 "excluded": util.loads(c["meta"], {}).get("excluded")} for c in shrunk],
            "survivors_agree_on_one_communicator": len({c["name"] for c in shrunk}) == 1,
            "sections_expected": len(ranks),
            "sections_present": len(sections),
            "sections_authored_by_killed_ranks": sorted(by_dead),
            "orphaned_sections_adopted": sorted(set(by_dead) - set(rewritten)),
            "orphaned_sections_rewritten": sorted(rewritten),
            "work_items_claimed_for_rewrite": claims,
            "work_lost": sorted(
                set(s for s in ("abstract", "motivation", "model", "detector", "recovery",
                                "durability", "evaluation", "conclusion")[:len(ranks)])
                - set(sections)),
            "trace_summary": summarise(job_dir),
        }
    finally:
        dev.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", default=os.path.join(os.path.dirname(__file__), "runs", "job"))
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..",
                                                      "results", "ampi_e3_fault.json"))
    args = parser.parse_args()
    payload = collect(args.job)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    brief = {k: v for k, v in payload.items()
             if k not in ("trace_summary", "failure_records", "recovery_sequence")}
    print(json.dumps(brief, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
