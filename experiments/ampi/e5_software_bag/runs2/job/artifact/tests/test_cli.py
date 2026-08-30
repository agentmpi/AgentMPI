"""Tests for tokenbudget.cli, the command line front end."""

from __future__ import annotations

import json

import pytest

from tokenbudget.cli import main
from tokenbudget.estimate import count_tokens


def test_count_text_prints_json_and_exits_zero(capsys):
    assert main(["count", "--text", "hello world"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"tokens": count_tokens("hello world")}


def test_count_file_matches_count_text(tmp_path, capsys):
    text = "the quick brown fox jumps over the lazy dog\n" * 4
    path = tmp_path / "sample.txt"
    path.write_text(text, encoding="utf-8")

    assert main(["count", "--file", str(path)]) == 0
    from_file = json.loads(capsys.readouterr().out)

    assert main(["count", "--text", text]) == 0
    from_text = json.loads(capsys.readouterr().out)

    assert from_file == from_text


def test_count_requires_exactly_one_source(capsys):
    assert main(["count"]) == 2
    assert "error" in capsys.readouterr().err
    assert main(["count", "--text", "a", "--file", "b"]) == 2
    assert "error" in capsys.readouterr().err


def test_count_missing_file_is_a_usage_error(tmp_path, capsys):
    missing = tmp_path / "nope.txt"
    assert main(["count", "--file", str(missing)]) == 2
    assert str(missing) in capsys.readouterr().err


def test_plan_prints_a_json_list_of_shares(capsys):
    assert main(["plan", "--total", "100", "--agents", "3"]) == 0
    shares = json.loads(capsys.readouterr().out)
    assert isinstance(shares, list)
    assert len(shares) == 3
    assert all(isinstance(share, int) and share >= 0 for share in shares)
    assert sum(shares) <= 100


def test_plan_rejects_zero_agents(capsys):
    assert main(["plan", "--total", "100", "--agents", "0"]) == 2
    assert "error" in capsys.readouterr().err


def test_plan_rejects_a_non_numeric_total(capsys):
    assert main(["plan", "--total", "lots", "--agents", "2"]) == 2
    assert "error" in capsys.readouterr().err


def test_compact_leaves_small_text_alone(tmp_path, capsys):
    path = tmp_path / "small.txt"
    path.write_text("short enough\n", encoding="utf-8")

    assert main(["compact", "--file", str(path), "--budget", "1000"]) == 0
    assert capsys.readouterr().out == "short enough\n"


def test_compact_output_fits_the_budget(tmp_path, capsys):
    path = tmp_path / "big.txt"
    path.write_text("alpha beta gamma delta epsilon " * 200, encoding="utf-8")
    budget = 40

    assert main(["compact", "--file", str(path), "--budget", str(budget)]) == 0
    out = capsys.readouterr().out
    assert count_tokens(out.rstrip("\n")) <= budget


def test_compact_rejects_a_negative_budget(tmp_path, capsys):
    path = tmp_path / "any.txt"
    path.write_text("data", encoding="utf-8")
    assert main(["compact", "--file", str(path), "--budget", "-5"]) == 2
    assert "error" in capsys.readouterr().err


def test_no_subcommand_is_a_usage_error(capsys):
    assert main([]) == 2
    assert "subcommand" in capsys.readouterr().err


def test_unknown_subcommand_is_a_usage_error(capsys):
    assert main(["explode"]) == 2
    assert "error" in capsys.readouterr().err


def test_main_reads_sys_argv_when_argv_is_none(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["tokenbudget", "count", "--text", "abc"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {"tokens": count_tokens("abc")}


@pytest.mark.parametrize("agents", [1, 2, 5, 7])
def test_plan_share_count_follows_agents_flag(agents, capsys):
    assert main(["plan", "--total", "64", "--agents", str(agents)]) == 0
    assert len(json.loads(capsys.readouterr().out)) == agents
