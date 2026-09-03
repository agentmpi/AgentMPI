"""Seal a finished run into committable evidence.

A run's live job directory is not tracked: its write-ahead log can reach hundreds
of megabytes, and git cannot reliably hash a file a running population is writing.
This script produces what *is* tracked, once the population is gone.

What it writes, under ``runs/<name>/evidence/``:

``journal.db``      the SQLite journal with its write-ahead log checkpointed in,
                    so the whole protocol history is one stable file
``trace.jsonl``     the event trace, exported in append order
``summary.json``    counts a reader wants without opening the database: which
                    ranks reached which state, which collectives closed, which
                    executors claimed which tasks, and where the tokens went

Why bother, rather than committing an aggregate.  An aggregate assembled after
the fact from whatever came back cannot be distinguished from one process writing
every entry, and a scale claim without per-executor evidence is an assertion.  The
sealed journal lets anyone recompute every number in the paper, and it names the
executor that produced each artifact.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"


def checkpoint(src: Path, dst: Path) -> dict[str, Any]:
    """Fold the write-ahead log into the database, then copy it."""
    conn = sqlite3.connect(src)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.commit()
    finally:
        conn.close()
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"bytes": dst.stat().st_size}


def summarise(job_root: Path) -> dict[str, Any]:
    import sys

    sys.path.insert(0, str(ROOT))
    from ampi import Ampi

    amp = Ampi(str(job_root), rank=0, allow_volatile=True)
    ranks = [amp._rankview(r) for r in range(amp.size)]
    tasks = amp.device.scan("task", {})
    events = amp.events()
    by_kind = Counter(e["kind"] for e in events)
    out = {
        "job": amp.manifest.job_id,
        "size": amp.size,
        "device": amp.device.name,
        "created_at": amp.manifest.created_at,
        "token_counter": amp.manifest.token_counter,
        "rank_states": dict(Counter(v.state for v in ranks)),
        "ranks": [
            {"rank": v.rank, "state": v.state, "epoch": v.epoch, "restarts": v.restarts,
             "failure_kind": v.failure_kind, "context_used": (v.ctx or {}).get("used", 0)}
            for v in ranks
        ],
        "collectives": amp.coll_status(),
        "events": dict(by_kind),
        "event_count": len(events),
        "tasks": {
            "total": len(tasks),
            "by_state": dict(Counter(t["state"] for t in tasks)),
            "by_executor": dict(Counter(t.get("worker_id") or "unattributed" for t in tasks)),
            "result_tokens": sum(t.get("result_tokens", 0) for t in tasks),
            "requeued": sum(1 for t in tasks if t.get("requeued")),
        },
        "executors": sorted({t["worker_id"] for t in tasks if t.get("worker_id")}),
        "context": {
            "total": sum((v.ctx or {}).get("used", 0) for v in ranks),
            "peak": max(((v.ctx or {}).get("used", 0) for v in ranks), default=0),
            "degradations": sum((v.ctx or {}).get("degradations", 0) for v in ranks),
        },
    }
    from ampitools.doctor import diagnose

    out["diagnosis"] = diagnose(amp)
    trace = [e for e in events]
    amp.close()
    return out, trace


def seal(name: str) -> dict[str, Any]:
    run_dir = RUNS / name
    job_root = run_dir / "job"
    if not job_root.exists():
        raise SystemExit(f"no job directory at {job_root}")
    evidence = run_dir / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)

    summary, trace = summarise(job_root)
    db = job_root / "journal.db"
    if db.exists():
        summary["journal"] = checkpoint(db, evidence / "journal.db")
    with open(evidence / "trace.jsonl", "w", encoding="utf-8") as fh:
        for e in trace:
            fh.write(json.dumps(e, default=str) + "\n")
    summary["trace_events"] = len(trace)
    (evidence / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    a = ap.parse_args()
    for name in a.runs:
        s = seal(name)
        print(f"{name}: {s['size']} ranks, {s['trace_events']} events, "
              f"{len(s['executors'])} executors, verdict {s['diagnosis']['verdict']}, "
              f"journal {s.get('journal', {}).get('bytes', 0) // 1024} KiB")


if __name__ == "__main__":
    main()
