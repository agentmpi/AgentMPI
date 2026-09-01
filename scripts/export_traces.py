"""Derive the committed, human-readable trace archive from the run directories.

``runs/`` is committed, so the SQLite fabrics and their content-addressed artifacts --- the
primary evidence --- are already in the repository. This script adds the two derived forms
that make that evidence usable without running anything.

``traces/events/<name>.jsonl``
    The event log as plain text, one JSON object per line, ordered. Same content the
    fabric's ``events`` table holds, in a form that ``grep``, ``jq``, and a diff can read.
    Every measurement in the paper is recomputable from these files alone;
    ``scripts/verify_traces.py`` does exactly that as a standing check.

``viz/public/traces/<name>.json``
    The derived payload the viewer renders: per-rank timelines, collective summaries with
    algorithm and fold depth, rank health, and the calibrated cost model. Byte-identical to
    what ``scripts/trace_server.py`` serves, so the viewer shows the same thing whether or
    not a live server is running --- which is what makes a fresh clone viewable with
    nothing but ``npm install && npm run dev``.

Output is byte-reproducible: re-exporting unchanged runs produces identical files, so this
never shows up as spurious churn.

    python3 scripts/export_traces.py            # everything
    python3 scripts/export_traces.py --curated  # skip validation sweeps and smoke runs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import agentmpi as ampi  # noqa: E402
import trace_server as ts  # noqa: E402

#: Families excluded by --curated. They are real measured runs and worth keeping by
#: default; the flag exists only for someone who wants a smaller checkout.
CURATED_EXCLUDE = ("smoke", "__coll-", "__transport-", "__pingpong")


def write_events(fabric: ampi.Fabric, dest: Path) -> tuple[int, str]:
    """Write the complete event log as plain JSONL. Returns (count, sha256)."""
    events = fabric.events()
    # sort_keys so the bytes depend only on content, never on dict insertion order.
    body = "".join(json.dumps(e, default=str, sort_keys=True) + "\n" for e in events)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    return len(events), hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(REPO / "runs"))
    ap.add_argument("--views", default=str(REPO / "viz" / "public" / "traces"))
    ap.add_argument("--events", default=str(REPO / "traces" / "events"))
    ap.add_argument("--manifest", default=str(REPO / "traces" / "manifest.json"))
    ap.add_argument("--min-events", type=int, default=1)
    ap.add_argument("--curated", action="store_true", help="omit validation sweeps and smoke runs")
    ap.add_argument("--max-spans", type=int, default=4000, help="cap timeline spans per lane")
    ap.add_argument("--quiet", action="store_true")
    cfg = ap.parse_args()

    ts.RUNS = Path(cfg.runs)
    views = Path(cfg.views)
    events_dir = Path(cfg.events)
    views.mkdir(parents=True, exist_ok=True)
    events_dir.mkdir(parents=True, exist_ok=True)
    for stale in (*views.glob("*.json"), *events_dir.glob("*.jsonl")):
        stale.unlink()

    index: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    skipped = 0
    for entry in ts.list_runs():
        name = entry.get("name", "")
        if entry.get("error") or (entry.get("n_events") or 0) < cfg.min_events:
            skipped += 1
            continue
        if cfg.curated and any(pat in name for pat in CURATED_EXCLUDE):
            skipped += 1
            continue
        root = Path(cfg.runs) / name.replace(ts.NEST, "/")
        try:
            fabric = ampi.Fabric(root)
            n_events, digest = write_events(fabric, events_dir / f"{name}.jsonl")
            detail = ts.run_detail(name)
        except Exception as exc:
            print(f"  skip {name}: {exc!r}")
            skipped += 1
            continue

        truncated = False
        for rank, spans in detail["lanes"].items():
            if len(spans) > cfg.max_spans:
                detail["lanes"][rank] = spans[: cfg.max_spans]
                truncated = True
        detail["truncated"] = truncated
        (views / f"{name}.json").write_text(json.dumps(detail, default=str, sort_keys=True), encoding="utf-8")

        index.append({k: v for k, v in entry.items() if k != "path"})
        manifest.append(
            {
                "name": name,
                "experiment": entry.get("experiment") or "",
                "label": entry.get("label") or "",
                "world_size": entry.get("world_size"),
                "n_ranks": entry.get("n_ranks"),
                "n_events": n_events,
                "events_sha256": digest,
                "events": f"traces/events/{name}.jsonl",
                "view": f"viz/public/traces/{name}.json",
                "fabric": f"runs/{name.replace(ts.NEST, '/')}/fabric.sqlite",
                "truncated": truncated,
            }
        )
        if not cfg.quiet:
            print(f"  {name}: {n_events} events")

    index.sort(key=lambda e: -(e.get("n_events") or 0))
    manifest.sort(key=lambda e: -(e["n_events"]))
    (views / "index.json").write_text(json.dumps(index, indent=1, default=str), encoding="utf-8")
    Path(cfg.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.manifest).write_text(
        json.dumps(
            {
                "n_runs": len(manifest),
                "n_events": sum(m["n_events"] for m in manifest),
                "note": (
                    "Derived from the committed run directories. traces/events holds each run's "
                    "complete event log as plain JSONL; viz/public/traces holds the viewer "
                    "payloads so a fresh clone renders every trace with no server running; "
                    "runs/<name>/ holds the primary SQLite fabric and its content-addressed "
                    "artifacts. Regenerate with scripts/export_traces.py, check with "
                    "scripts/verify_traces.py."
                ),
                "runs": manifest,
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    ev_bytes = sum(p.stat().st_size for p in events_dir.glob("*.jsonl"))
    vw_bytes = sum(p.stat().st_size for p in views.glob("*.json"))
    print(
        f"exported {len(manifest)} runs, {sum(m['n_events'] for m in manifest):,} events "
        f"({skipped} skipped)\n"
        f"  event logs: {ev_bytes / 1e6:.1f} MB in {events_dir}\n"
        f"  viewer data: {vw_bytes / 1e6:.1f} MB in {views}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
