"""Export run traces to static JSON so the viewer works from a fresh clone.

The run directories are large --- content-addressed artifacts and SQLite fabrics run to
hundreds of megabytes --- and they are gitignored, so a reader who clones the repository
gets a viewer with nothing in it. That is the wrong default for the one artifact whose
whole purpose is to make a run legible.

This writes the *derived* view --- the same payloads ``scripts/trace_server.py`` serves ---
into ``viz/public/traces/``, which is a few hundred kilobytes and is committed. The viewer
tries the live API first and falls back to these files, so it behaves identically whether
or not a trace server is running.

What is exported is a projection, not the run: timelines, collective summaries, per-rank
health, and the calibrated cost model. The artifacts themselves are not, which is the
right cut --- the traces are what a reader wants to look at, and the artifacts are what a
reader would want to re-generate.

    python3 scripts/export_traces.py --min-events 50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import trace_server as ts  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(REPO / "runs"))
    ap.add_argument("--out", default=str(REPO / "viz" / "public" / "traces"))
    ap.add_argument("--min-events", type=int, default=50, help="skip trivial runs")
    ap.add_argument("--exclude", action="append", default=["smoke"], help="substring filter on run names")
    ap.add_argument("--max-spans", type=int, default=4000, help="cap spans per lane so a file stays readable")
    ap.add_argument(
        "--clean",
        action="store_true",
        help="remove existing recorded traces instead of merging new exports into the index",
    )
    cfg = ap.parse_args()

    ts.RUNS = Path(cfg.runs)
    out = Path(cfg.out)
    out.mkdir(parents=True, exist_ok=True)
    if cfg.clean:
        for stale in out.glob("*.json"):
            stale.unlink()

    index_path = out / "index.json"
    existing: list[dict[str, Any]] = []
    if not cfg.clean and index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, list) else []
        except (OSError, json.JSONDecodeError):
            existing = []
    by_name = {entry["name"]: entry for entry in existing if entry.get("name")}
    for entry in ts.list_runs():
        if entry.get("error") or (entry.get("n_events") or 0) < cfg.min_events:
            continue
        if any(pat in entry["name"] for pat in cfg.exclude):
            continue
        name = entry["name"]
        try:
            detail = ts.run_detail(name)
        except Exception as exc:
            print(f"  skip {name}: {exc!r}")
            continue
        # Cap per-lane spans. A run with tens of thousands of events would produce a file
        # nobody can load, and the shape of a timeline is legible from a prefix.
        truncated = False
        for rank, spans in detail["lanes"].items():
            if len(spans) > cfg.max_spans:
                detail["lanes"][rank] = spans[: cfg.max_spans]
                truncated = True
        detail["truncated"] = truncated
        (out / f"{name}.json").write_text(json.dumps(detail, default=str), encoding="utf-8")
        by_name[name] = {k: entry[k] for k in entry if k != "path"}
        size = (out / f"{name}.json").stat().st_size
        print(f"  {name}: {entry['n_events']} events, {size // 1024} KiB{' (truncated)' if truncated else ''}")

    index = sorted(by_name.values(), key=lambda e: -(e.get("n_events") or 0))
    index_path.write_text(json.dumps(index, indent=1, default=str), encoding="utf-8")
    total = sum(p.stat().st_size for p in out.glob("*.json"))
    print(f"exported {len(index)} traces to {out} ({total // 1024} KiB total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
