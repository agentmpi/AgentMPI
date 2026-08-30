"""Collect E5: did the claim-based idiom actually coordinate the build?

The questions this answers are about coordination, not about code quality:

* was every module claimed exactly once, or did two agents write the same one?
* how many claims were refused, and did the refused agent go on to take a
  different module rather than writing one it did not own?
* did the lease on the shared ``__init__.py`` actually serialise its editors?
* did the completion counter carry the phase transition that a barrier would
  otherwise have carried, without any rank blocking another?
* and, independently of all that, does the package pass its tests?

The last one is the only judge of whether the agents wrote good software; the
rest are the judge of whether the protocol did its job.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from ampi import util  # noqa: E402
from ampi.analysis import summarise  # noqa: E402
from ampi.device import SqliteDevice  # noqa: E402


def run_tests(pkg_dir: str) -> dict:
    if not os.path.isdir(pkg_dir):
        return {"ran": False, "reason": "no artifact directory"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=no"],
        cwd=pkg_dir, capture_output=True, text=True, timeout=600)
    tail = (proc.stdout or "").strip().splitlines()[-6:]
    summary = next((ln for ln in reversed(tail) if "passed" in ln or "failed" in ln
                    or "error" in ln), "")
    return {"ran": True, "exit_code": proc.returncode, "summary": summary,
            "tail": tail}


def collect(job_dir: str) -> dict:
    job_dir = os.path.abspath(job_dir)
    pkg_dir = os.path.join(job_dir, "artifact")
    dev = SqliteDevice(os.path.join(job_dir, "job.db"))
    dev.initialize()
    job_id = os.path.basename(job_dir.rstrip("/"))
    try:
        ranks = dev.query("SELECT * FROM rank WHERE job_id=? ORDER BY rank", (job_id,))
        cells = dev.query("SELECT * FROM win_cell", ())
        by_key = {c["key"]: c for c in cells}

        claims = {}
        for key, cell in sorted(by_key.items()):
            if key.startswith("module/") and key.count("/") == 1:
                value = util.loads(cell["value"], {}) or {}
                claims[key.split("/", 1)[1]] = {
                    "owner": value.get("owner"), "version": cell["version"]}

        refused = [e for e in dev.query(
            "SELECT rank, meta FROM event WHERE job_id=? AND op='AMPI_Claim'", (job_id,))
            if util.loads(e["meta"], {}).get("ok") is False]
        granted = [e for e in dev.query(
            "SELECT rank, meta FROM event WHERE job_id=? AND op='AMPI_Claim'", (job_id,))
            if util.loads(e["meta"], {}).get("ok") is True]

        locks = dev.query("SELECT * FROM win_lock ORDER BY acquired_at", ())
        initpy_locks = [lk for lk in locks if lk["key"] == "initpy"]
        overlaps = 0
        for i, a in enumerate(initpy_locks):
            for b in initpy_locks[i + 1:]:
                a_end = a["released_at"] or a["expires_at"]
                b_end = b["released_at"] or b["expires_at"]
                if a["acquired_at"] < b_end and b["acquired_at"] < a_end:
                    overlaps += 1

        counter = util.loads((by_key.get("done") or {}).get("value"), 0)
        summaries = {k: util.loads(v["value"], {}) for k, v in by_key.items()
                     if k.endswith("/summary")}

        joined = [r for r in ranks if r["started_at"]]
        join_times = sorted(r["started_at"] for r in joined)
        spread = round(join_times[-1] - join_times[0], 1) if len(join_times) > 1 else 0.0

        blocked = dev.query(
            "SELECT COUNT(*) AS n FROM event WHERE job_id=? AND op IN "
            "('AMPI_Barrier','AMPI_Bcast','AMPI_Reduce','AMPI_Allreduce')", (job_id,))

        modules = sorted(
            os.path.relpath(os.path.join(d, n), pkg_dir)
            for d, _, names in os.walk(pkg_dir) for n in names if n.endswith(".py")
        ) if os.path.isdir(pkg_dir) else []

        return {
            "job": job_id,
            "world_size": len(ranks),
            "ranks_joined": len(joined),
            "ranks_finalized": sum(1 for r in ranks if r["state"] == "finalized"),
            "executor_admission_spread_s": spread,
            "collective_calls": blocked[0]["n"],
            "module_claims": claims,
            "modules_claimed": sum(1 for c in claims.values() if c["owner"] is not None),
            "claims_granted": len(granted),
            "claims_refused": len(refused),
            "double_claims": [k for k, c in claims.items() if c["version"] > 1],
            "initpy_lock_acquisitions": len(initpy_locks),
            "initpy_lock_overlaps": overlaps,
            "completion_counter": counter,
            "module_summaries": summaries,
            "artifact_files": modules,
            "tests": run_tests(pkg_dir),
            "trace_summary": summarise(job_dir),
        }
    finally:
        dev.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", default=os.path.join(os.path.dirname(__file__), "runs", "job"))
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "..",
                                                      "results", "ampi_e5_software.json"))
    args = parser.parse_args()
    payload = collect(args.job)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    brief = {k: v for k, v in payload.items()
             if k not in ("trace_summary", "module_summaries")}
    print(json.dumps(brief, indent=2)[:2600])
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
