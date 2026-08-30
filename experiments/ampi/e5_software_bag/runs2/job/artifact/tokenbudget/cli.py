"""Command line front end for the tokenbudget package.

Exposes the three user facing operations of the library as subcommands:
``count`` (estimate the tokens in some text), ``plan`` (split a budget across
agents) and ``compact`` (shrink text to fit a budget). ``count`` and ``plan``
print JSON on stdout; ``compact`` prints the compacted text itself.

The entry point returns an exit status instead of raising ``SystemExit`` so it
can be driven directly from tests: 0 on success, 2 on any usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, NoReturn, Sequence

from tokenbudget.compact import head_tail
from tokenbudget.estimate import count_tokens
from tokenbudget.planner import plan_fanout

__all__ = ["main"]

EXIT_OK = 0
EXIT_USAGE = 2


class _UsageError(Exception):
    """Raised for any bad invocation, so that main can return 2."""


class _Parser(argparse.ArgumentParser):
    """Argument parser that reports errors as exceptions, never SystemExit."""

    def error(self, message: str) -> NoReturn:
        raise _UsageError(message)


def _build_parser() -> _Parser:
    parser = _Parser(prog="tokenbudget", description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command")

    count = subcommands.add_parser("count", help="count the tokens in some text")
    source = count.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="text to count")
    source.add_argument("--file", help="path to a file to count")

    plan = subcommands.add_parser("plan", help="split a budget across agents")
    plan.add_argument("--total", type=int, required=True, help="total token budget")
    plan.add_argument("--agents", type=int, required=True, help="number of agents")

    compact = subcommands.add_parser("compact", help="shrink a file to fit a budget")
    compact.add_argument("--file", required=True, help="path to the file to compact")
    compact.add_argument("--budget", type=int, required=True, help="token budget")

    return parser


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        raise _UsageError(f"cannot read {path}: {exc.strerror or exc}") from exc


def _run_count(args: argparse.Namespace, stdout: IO[str]) -> int:
    text = args.text if args.text is not None else _read_file(args.file)
    json.dump({"tokens": count_tokens(text)}, stdout)
    stdout.write("\n")
    return EXIT_OK


def _run_plan(args: argparse.Namespace, stdout: IO[str]) -> int:
    try:
        shares = plan_fanout(args.total, args.agents)
    except ValueError as exc:
        raise _UsageError(str(exc)) from exc
    json.dump(shares, stdout)
    stdout.write("\n")
    return EXIT_OK


def _run_compact(args: argparse.Namespace, stdout: IO[str]) -> int:
    if args.budget < 0:
        raise _UsageError("--budget must not be negative")
    try:
        compacted = head_tail(_read_file(args.file), args.budget)
    except ValueError as exc:
        raise _UsageError(str(exc)) from exc
    stdout.write(compacted)
    if not compacted.endswith("\n"):
        stdout.write("\n")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Run the tokenbudget command line, returning a process exit status."""
    arguments: Sequence[str] = sys.argv[1:] if argv is None else argv
    parser = _build_parser()
    try:
        args = parser.parse_args(list(arguments))
        if args.command is None:
            raise _UsageError("a subcommand is required")
        if args.command == "count":
            return _run_count(args, sys.stdout)
        if args.command == "plan":
            return _run_plan(args, sys.stdout)
        if args.command == "compact":
            return _run_compact(args, sys.stdout)
        raise _UsageError(f"unknown command {args.command!r}")
    except _UsageError as exc:
        print(f"{parser.prog}: error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
