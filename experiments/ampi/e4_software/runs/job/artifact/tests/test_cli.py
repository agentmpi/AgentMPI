"""Tests for :mod:`tokenbudget.cli`.

The command line is a thin shell, so these tests check the two things only it
is responsible for: that each subcommand's stdout is exactly what the contract
promises (JSON for ``count`` and ``plan``, plain text for ``compact``), and
that every way of getting the arguments wrong comes back as status 2 rather
than a traceback.
"""

from __future__ import annotations

import json

import pytest

from tokenbudget import compact, estimate, planner
from tokenbudget.cli import EXIT_OK, EXIT_USAGE, main

ELISION = "\n...[elided]...\n"


def run(argv: list[str] | None) -> int:
    """Call :func:`main` and report its status even if argparse exits."""
    try:
        return main(argv)
    except SystemExit as exc:  # pragma: no cover - main is meant to catch these
        return EXIT_USAGE if exc.code is None else int(exc.code)


def test_count_text_prints_the_estimate_as_json(capsys):
    text = "the quick brown fox jumps over the lazy dog"

    assert run(["count", "--text", text]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {"tokens": estimate.count_tokens(text)}


def test_count_of_empty_text_is_zero(capsys):
    """The contract's zero case: empty input costs nothing, and is not an error."""
    assert run(["count", "--text", ""]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["tokens"] == 0


def test_count_file_agrees_with_count_text(tmp_path, capsys):
    text = "alpha beta gamma delta epsilon\nzeta eta theta\n"
    source = tmp_path / "context.txt"
    source.write_text(text, encoding="utf-8")

    assert run(["count", "--file", str(source)]) == EXIT_OK
    from_file = json.loads(capsys.readouterr().out)

    assert run(["count", "--text", text]) == EXIT_OK
    assert from_file == json.loads(capsys.readouterr().out)


def test_count_of_an_empty_file_is_zero(tmp_path, capsys):
    source = tmp_path / "empty.txt"
    source.write_text("", encoding="utf-8")

    assert run(["count", "--file", str(source)]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["tokens"] == 0


def test_plan_prints_a_json_list_matching_the_planner(capsys):
    assert run(["plan", "--total", "30000", "--agents", "3"]) == EXIT_OK
    printed = json.loads(capsys.readouterr().out)

    assert isinstance(printed, list)
    assert printed == planner.plan_fanout(30_000, 3)


def test_plan_defers_to_the_planner_default_reserve(capsys):
    """Omitting --reserve-frac must give the same answer as omitting it in code."""
    assert run(["plan", "--total", "1000", "--agents", "7"]) == EXIT_OK
    default = json.loads(capsys.readouterr().out)

    assert run(["plan", "--total", "1000", "--agents", "7", "--reserve-frac", "0.1"]) == EXIT_OK
    explicit = json.loads(capsys.readouterr().out)

    assert default == explicit == planner.plan_fanout(1000, 7)


def test_plan_of_a_zero_total_is_all_zero_shares(capsys):
    assert run(["plan", "--total", "0", "--agents", "4"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == [0, 0, 0, 0]


def test_plan_for_a_single_agent_hands_it_the_whole_spendable_pool(capsys):
    assert run(["plan", "--total", "1000", "--agents", "1", "--reserve-frac", "0"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == [1000]


def test_compact_prints_elided_text_that_fits_the_budget(tmp_path, capsys):
    source = tmp_path / "context.txt"
    source.write_text("alpha beta gamma delta " * 300, encoding="utf-8")

    assert run(["compact", "--file", str(source), "--budget", "120"]) == EXIT_OK
    printed = capsys.readouterr().out

    assert ELISION in printed
    assert estimate.count_tokens(printed.rstrip("\n")) <= 120


def test_compact_leaves_text_that_already_fits_alone(tmp_path, capsys):
    text = "short enough already"
    source = tmp_path / "small.txt"
    source.write_text(text, encoding="utf-8")

    assert run(["compact", "--file", str(source), "--budget", "1000"]) == EXIT_OK
    printed = capsys.readouterr().out

    assert printed == text + "\n"
    assert ELISION not in printed


def test_compact_honours_an_explicit_head_frac(tmp_path, capsys):
    text = "alpha beta gamma delta " * 200
    source = tmp_path / "context.txt"
    source.write_text(text, encoding="utf-8")

    assert run(["compact", "--file", str(source), "--budget", "80", "--head-frac", "1.0"]) == 0
    expected = compact.head_tail(text, 80, 1.0)

    assert capsys.readouterr().out == expected + "\n"
    assert expected.endswith(ELISION), "head_frac 1.0 keeps no tail"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["nonsense"],
        ["count"],
        ["count", "--text", "hi", "--file", "hi.txt"],
        ["plan", "--total", "1000"],
        ["plan", "--agents", "2"],
        ["plan", "--total", "1000", "--agents", "0"],
        ["plan", "--total", "1000", "--agents", "-3"],
        ["plan", "--total", "-1", "--agents", "2"],
        ["plan", "--total", "1000", "--agents", "2", "--reserve-frac", "1.5"],
        ["plan", "--total", "not-a-number", "--agents", "2"],
        ["compact", "--budget", "100"],
        ["compact", "--file", "context.txt"],
    ],
)
def test_usage_errors_come_back_as_status_two(argv: list[str]):
    assert run(argv) == EXIT_USAGE


def test_a_negative_budget_is_a_usage_error(tmp_path):
    source = tmp_path / "context.txt"
    source.write_text("alpha beta gamma", encoding="utf-8")

    assert run(["compact", "--file", str(source), "--budget", "-1"]) == EXIT_USAGE


@pytest.mark.parametrize("command", ["count", "compact"])
def test_an_unreadable_file_is_a_usage_error_not_a_traceback(tmp_path, command: str):
    missing = tmp_path / "does-not-exist.txt"
    argv = [command, "--file", str(missing)]
    if command == "compact":
        argv += ["--budget", "100"]

    assert run(argv) == EXIT_USAGE


def test_failures_print_nothing_on_stdout(capsys):
    assert run(["plan", "--total", "1000", "--agents", "0"]) == EXIT_USAGE
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "error" in captured.err


def test_help_succeeds(capsys):
    assert run(["--help"]) == EXIT_OK
    assert "count" in capsys.readouterr().out


def test_argv_defaults_to_sys_argv(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["tokenbudget", "count", "--text", "abcd"])

    assert main() == EXIT_OK
    assert json.loads(capsys.readouterr().out) == {"tokens": estimate.count_tokens("abcd")}
