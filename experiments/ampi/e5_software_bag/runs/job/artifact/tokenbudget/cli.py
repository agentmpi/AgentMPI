"""Command line entry point for tokenbudget.

The CLI is a thin shell over the library: every subcommand delegates to the
module that owns the behaviour, so the numbers it prints are exactly the
numbers a caller would get in process. ``count`` and ``plan`` print JSON so
that shell scripts can pipe them onward; ``compact`` prints the text itself.
"""

import argparse
import json
import sys
from pathlib import Path

from .compact import head_tail
from .estimate import count_tokens
from .planner import plan_fanout

USAGE_ERROR = 2
"""Exit code for anything the user could fix by retyping the command."""


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokenbudget",
        description="Plan and enforce token budgets across cooperating agents.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    count = subcommands.add_parser("count", help="print the token count of some text")
    source = count.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="count this literal string")
    source.add_argument("--file", help="count the contents of this file")

    plan = subcommands.add_parser("plan", help="print a per-agent budget split")
    plan.add_argument("--total", type=int, required=True, help="total token budget")
    plan.add_argument("--agents", type=int, required=True, help="number of agents")

    compact = subcommands.add_parser("compact", help="print text shrunk to fit a budget")
    compact.add_argument("--file", required=True, help="file to compact")
    compact.add_argument("--budget", type=int, required=True, help="token budget to fit")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the tokenbudget command line and return a process exit code."""
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_ERROR if exc.code else 0

    try:
        match args.command:
            case "count":
                text = args.text if args.text is not None else _read(args.file)
                print(json.dumps(count_tokens(text)))
            case "plan":
                print(json.dumps(plan_fanout(args.total, args.agents)))
            case "compact":
                print(head_tail(_read(args.file), args.budget))
            case _:
                parser.print_usage(sys.stderr)
                return USAGE_ERROR
    except (OSError, ValueError) as exc:
        print(f"tokenbudget: {exc}", file=sys.stderr)
        return USAGE_ERROR

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
