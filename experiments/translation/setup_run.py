#!/usr/bin/env python3
"""Set up one translation run: create the job, emit per-rank prompts.

Splitting setup from launch is the process-manager boundary described in
:mod:`agentmpi.launch`.  This script produces everything a launcher needs --
a run directory and one prompt per rank -- and says nothing about how the
ranks are started.  In our experiments they are started as Cursor subagents;
they could equally be containers, or people.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

sys.path.insert(0, str(REPO / "src"))

from agentmpi.launch import write_launch_plan  # noqa: E402
from agentmpi.runtime import RUN_MANIFEST, RunManifest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="run name, e.g. trans-glossary")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--mode", default="glossary",
                    choices=["glossary", "baseline", "chain"])
    ap.add_argument("--capacity", type=int, default=200_000)
    ap.add_argument("--base", default=str(REPO / "runs"))
    args = ap.parse_args()

    base = Path(args.base) / args.name
    root = base / "ampi"
    size = args.workers + 1

    subprocess.run(
        [sys.executable, "-m", "agentmpi.cli", "init", "--root", str(root),
         "--ranks", str(size), "--label", args.name,
         "--capacity", str(args.capacity),
         "--cvar", "ampi_failure_timeout_s=1800",
         "--cvar", "ampi_stall_timeout_s=3600",
         "--cvar", "ampi_gap_timeout_s=600"],
        check=True, cwd=str(REPO),
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
    )

    manifest = RunManifest.load(root / RUN_MANIFEST)
    manifest.ranks[0]["role"] = "coordinator"
    for entry in manifest.ranks[1:]:
        entry["role"] = "translator"
    (root / RUN_MANIFEST).write_text(manifest.to_json(), encoding="utf-8")

    program = HERE / ("program_baseline.md" if args.mode == "baseline"
                      else "program_glossary.md")
    out = write_launch_plan(root, manifest, program=str(program),
                            out_dir=base / "prompts")

    plan = json.loads((Path(out) / "plan.json").read_text())
    print(json.dumps({
        "run": args.name, "root": str(root), "size": size,
        "workers": args.workers, "mode": args.mode,
        "prompts": out,
        "worker_prompts": [p["prompt"] for p in plan if p["rank"] != 0],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
