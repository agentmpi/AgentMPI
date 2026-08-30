"""Unit tests for tokenbudget.cli."""

import io
import json
from contextlib import redirect_stdout, redirect_stderr

from tokenbudget import cli
from tokenbudget.estimate import count_tokens
from tokenbudget.planner import plan_fanout

LONG = "the agent plans its next move and the supervisor counts tokens. " * 30


def _run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        code = cli.main(argv)
    return code, out.getvalue()


def test_count_text_prints_json() -> None:
    code, out = _run(["count", "--text", "hello world"])
    assert code == 0
    assert json.loads(out) == count_tokens("hello world")


def test_count_file_prints_json(tmp_path) -> None:
    sample = tmp_path / "s.txt"
    sample.write_text(LONG, encoding="utf-8")
    code, out = _run(["count", "--file", str(sample)])
    assert code == 0
    assert json.loads(out) == count_tokens(LONG)


def test_plan_prints_json_matching_the_planner() -> None:
    code, out = _run(["plan", "--total", "1200", "--agents", "3"])
    assert code == 0
    assert json.loads(out) == plan_fanout(1200, 3)


def test_compact_prints_text_that_fits(tmp_path) -> None:
    sample = tmp_path / "s.txt"
    sample.write_text(LONG, encoding="utf-8")
    code, out = _run(["compact", "--file", str(sample), "--budget", "60"])
    assert code == 0
    assert count_tokens(out.rstrip("\n")) <= 60


def test_no_subcommand_is_a_usage_error() -> None:
    assert _run([])[0] == 2


def test_count_without_a_source_is_a_usage_error() -> None:
    assert _run(["count"])[0] == 2


def test_count_with_both_sources_is_a_usage_error() -> None:
    assert _run(["count", "--text", "hi", "--file", "x.txt"])[0] == 2


def test_non_numeric_total_is_a_usage_error() -> None:
    assert _run(["plan", "--total", "not-a-number", "--agents", "3"])[0] == 2


def test_bad_plan_arguments_are_a_usage_error() -> None:
    assert _run(["plan", "--total", "100", "--agents", "0"])[0] == 2


def test_missing_file_is_a_usage_error(tmp_path) -> None:
    assert _run(["count", "--file", str(tmp_path / "nope.txt")])[0] == 2


def test_help_exits_zero() -> None:
    assert _run(["--help"])[0] == 0
