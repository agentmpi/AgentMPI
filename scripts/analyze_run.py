"""Write trace metrics and, when available, visualisations for one run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from plot_run import MATPLOTLIB_AVAILABLE, render_all, resolve_trace  # noqa: E402

from ampi.analysis import analyse_path  # noqa: E402


def analyse_run(input_path: str | Path, output: Path | None = None) -> tuple[Path, list[Path]]:
    trace_path = resolve_trace(input_path)
    output_dir = output or trace_path.parent / "analysis"
    analysis = analyse_path(trace_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(analysis.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    figures: list[Path] = []
    if MATPLOTLIB_AVAILABLE:
        figures = sorted(render_all(analysis, output_dir / "figures").values())
    return metrics_path, figures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="run directory or harness.trace.jsonl")
    parser.add_argument("--output", type=Path, help="analysis output directory")
    args = parser.parse_args(argv)
    try:
        metrics, figures = analyse_run(args.run, args.output)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"analyze_run: {exc}\n")
    print(f"wrote {metrics}")
    if figures:
        print(f"wrote {len(figures)} figure(s) to {figures[0].parent}")
    else:
        print("matplotlib unavailable; metrics written without figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
