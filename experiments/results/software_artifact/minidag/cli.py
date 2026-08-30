"""Command-line interface for :mod:`minidag`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from minidag.executor import execute
from minidag.parser import load


def _positive_jobs(value: str) -> int:
    try:
        jobs = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if jobs < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return jobs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="minidag",
        description="Execute a dependency graph from a JSON file.",
    )
    parser.add_argument(
        "graph",
        type=Path,
        metavar="GRAPH",
        help="path to the graph JSON file",
    )
    parser.add_argument(
        "--jobs",
        type=_positive_jobs,
        default=1,
        metavar="N",
        help="maximum number of tasks to run concurrently (default: 1)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the minidag command-line interface and return its exit status."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        graph = load(args.graph)
        result = execute(graph, jobs=args.jobs)
    except (OSError, ValueError) as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return 2

    return 0 if result.success else 1
