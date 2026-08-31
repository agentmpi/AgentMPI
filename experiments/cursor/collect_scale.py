#!/usr/bin/env python3
"""Gather Cursor rank outputs into a single experiment record."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    home = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "experiments/results/.ampi/cursor-scale")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    out_dir = home / "out"
    items = []
    missing = []
    for r in range(n):
        p = out_dir / f"rank{r}.json"
        if not p.exists():
            missing.append(r)
            continue
        items.append(json.loads(p.read_text()))
    payload = {
        "experiment": "cursor-scale-aesop-es",
        "n": n,
        "completed": len(items),
        "missing_ranks": missing,
        "items": items,
    }
    dest = ROOT / "experiments/results/cursor_scale.json"
    dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"completed {len(items)}/{n} -> {dest}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
