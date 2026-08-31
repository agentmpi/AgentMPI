"""Command line front end for :mod:`tokenbudget`.

Three subcommands expose the library to shell scripts and to humans poking at
a budget by hand:

``count``
    Report what a piece of text costs, from ``--text`` or from ``--file``.
``plan``
    Split a ``--total`` budget between ``--agents`` agents.
``compact``
    Shrink the contents of ``--file`` to fit ``--budget`` tokens.

``count`` and ``plan`` print JSON on stdout -- an object with a ``tokens``
field and a list of shares respectively -- so their output can be piped into
another tool. ``compact`` prints the compacted text itself, since that is what
a caller wants to feed back to a model.

The status code is the whole error protocol: 0 when the command did what it
was asked, 2 for any usage error, whether that is argparse rejecting the
arguments, a value the library refuses (a plan for zero agents, a negative
budget), or a ``--file`` that cannot be read. Nothing here is printed to
stdout unless the command succeeded; diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tokenbudget import compact, estimate, planner

__all__ = ["EXIT_OK", "EXIT_USAGE", "main"]

#: Status returned when the command succeeded.
EXIT_OK = 0

#: Status returned for any usage error.
EXIT_USAGE = 2


def _cmd_count(args: argparse.Namespace) -> None:
    """Print the token count of ``--text`` or of the contents of ``--file``."""
    text = args.text if args.text is not None else args.file.read_text(encoding="utf-8")
    print(json.dumps({"tokens": estimate.count_tokens(text)}))


def _cmd_plan(args: argparse.Namespace) -> None:
    """Print the per-agent shares of ``--total`` as a JSON list."""
    if args.reserve_frac is None:
        shares = planner.plan_fanout(args.total, args.agents)
    else:
        shares = planner.plan_fanout(args.total, args.agents, args.reserve_frac)
    print(json.dumps(shares))


def _cmd_compact(args: argparse.Namespace) -> None:
    """Print the contents of ``--file`` shortened to fit ``--budget``."""
    text = args.file.read_text(encoding="utf-8")
    if args.head_frac is None:
        print(compact.head_tail(text, args.budget))
    else:
        print(compact.head_tail(text, args.budget, args.head_frac))


def _build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the whole command line."""
    parser = argparse.ArgumentParser(
        prog="tokenbudget",
        description="Plan and enforce token budgets across cooperating agents.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    count = subcommands.add_parser("count", help="count the tokens in some text")
    source = count.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="the text to measure")
    source.add_argument("--file", type=Path, help="a file whose contents to measure")
    count.set_defaults(handler=_cmd_count)

    plan = subcommands.add_parser("plan", help="split a budget between agents")
    plan.add_argument("--total", type=int, required=True, help="the whole budget, in tokens")
    plan.add_argument("--agents", type=int, required=True, help="how many agents to plan for")
    plan.add_argument(
        "--reserve-frac",
        type=float,
        default=None,
        dest="reserve_frac",
        help="fraction of the budget held back (default: the planner's own)",
    )
    plan.set_defaults(handler=_cmd_plan)

    compact_cmd = subcommands.add_parser("compact", help="shrink a file to fit a budget")
    compact_cmd.add_argument("--file", type=Path, required=True, help="the file to compact")
    compact_cmd.add_argument(
        "--budget", type=int, required=True, help="the token budget to fit into"
    )
    compact_cmd.add_argument(
        "--head-frac",
        type=float,
        default=None,
        dest="head_frac",
        help="share of the kept text taken from the front (default: compact's own)",
    )
    compact_cmd.set_defaults(handler=_cmd_compact)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand and return its status.

    Args:
        argv: The arguments to parse, without the program name. ``None`` means
            take them from :data:`sys.argv`.

    Returns:
        :data:`EXIT_OK` if the command succeeded, :data:`EXIT_USAGE` for any
        usage error. argparse's own exits are caught and turned into a return
        value, so a caller never has to handle :class:`SystemExit`.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # --help and --version exit 0 and have already printed what they owe.
        return EXIT_OK if exc.code in (0, None) else EXIT_USAGE

    try:
        args.handler(args)
    except (ValueError, OSError) as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
