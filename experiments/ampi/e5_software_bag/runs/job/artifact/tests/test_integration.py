"""Cross-module integration tests for the tokenbudget package.

Every test here drives at least two modules against each other; the
per-module unit tests live beside their modules. Assertions are written
against the published contract only, never against a particular tokenizer
or rounding strategy, so any conforming implementation passes.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import tokenbudget
from tokenbudget import cli, compact, estimate, ledger, planner, policy

LOREM = (
    "The quick brown fox jumps over the lazy dog while the agent plans its "
    "next move and the supervisor counts every single token it spends. "
) * 40

MESSAGES = [
    {"role": "system", "content": "You are a careful assistant."},
    {"role": "user", "content": "Summarise the following report in detail. " * 8},
    {"role": "assistant", "content": "Here is the summary you asked for. " * 8},
    {"role": "user", "content": "Now do the same for the second report. " * 8},
    {"role": "assistant", "content": "Certainly, here it is. " * 8},
]


def test_planned_slice_bounds_what_the_ledger_may_charge() -> None:
    """A fan-out plan, spent through the ledger, never breaks the budget."""
    total = 4000
    n_agents = 4
    slices = planner.plan_fanout(total, n_agents, reserve_frac=0.1)
    assert len(slices) == n_agents
    assert sum(slices) <= int(total * 0.9)

    book = ledger.Ledger()
    for i, slice_budget in enumerate(slices):
        agent = f"agent{i}"
        budget = policy.Budget(limit=slice_budget)
        spend = estimate.estimate_messages(MESSAGES[:2])
        if budget.admits(book.usage().get(agent, 0), spend):
            book.charge(agent, spend)
        assert book.usage().get(agent, 0) <= slice_budget

    assert book.total() <= sum(slices)


def test_compacted_text_fits_the_budget_left_after_charging() -> None:
    """head_tail must fit whatever policy says is still available."""
    budget = policy.Budget(limit=600, reserved=50)
    book = ledger.Ledger()
    book.charge("writer", 200)

    remaining = budget.remaining(book.total())
    trimmed = compact.head_tail(LOREM, remaining)

    assert estimate.count_tokens(trimmed) <= remaining
    assert budget.admits(book.total(), estimate.count_tokens(trimmed))


def test_drop_oldest_result_fits_the_planned_slice() -> None:
    """drop_oldest must respect the same estimator the planner is sized in."""
    slices = planner.plan_fanout(900, 3)
    slice_budget = slices[0]

    kept = compact.drop_oldest(MESSAGES, slice_budget)

    assert kept, "drop_oldest must always keep the last message"
    assert kept[-1] == MESSAGES[-1]
    assert kept == MESSAGES[len(MESSAGES) - len(kept):]
    if len(kept) > 1:
        assert estimate.estimate_messages(kept) <= slice_budget


def test_releasing_a_charge_restores_admission() -> None:
    """The ledger and the policy agree on when a request can be admitted."""
    budget = policy.Budget(limit=500)
    book = ledger.Ledger()
    big = 400

    book.charge("agent0", big)
    assert not budget.admits(book.total(), 200)

    book.release("agent0", big)
    assert book.total() == 0
    assert budget.admits(book.total(), 200)


def test_full_fanout_pipeline_stays_inside_the_total_budget() -> None:
    """Plan, compact per agent, charge, and never exceed the global budget."""
    total = 3000
    slices = planner.plan_fanout(total, 4, reserve_frac=0.2)
    global_budget = policy.Budget(limit=total)
    book = ledger.Ledger()

    for i, slice_budget in enumerate(slices):
        agent = f"agent{i}"
        kept = compact.drop_oldest(MESSAGES, slice_budget)
        cost = estimate.estimate_messages(kept)
        if global_budget.admits(book.total(), cost):
            book.charge(agent, cost)

    assert book.total() <= total
    assert set(book.usage()) <= {f"agent{i}" for i in range(4)}
    assert sum(book.usage().values()) == book.total()


def test_cli_plan_agrees_with_the_planner() -> None:
    """The CLI is a thin shell over planner.plan_fanout."""
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["plan", "--total", "1200", "--agents", "3"])

    assert code == 0
    assert json.loads(out.getvalue()) == planner.plan_fanout(1200, 3)


def test_cli_count_agrees_with_the_estimator(tmp_path) -> None:
    """The CLI counts a file with exactly the estimator's tokenizer."""
    sample = tmp_path / "sample.txt"
    sample.write_text(LOREM, encoding="utf-8")

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["count", "--file", str(sample)])

    assert code == 0
    assert json.loads(out.getvalue()) == estimate.count_tokens(LOREM)


def test_cli_compact_output_fits_the_requested_budget(tmp_path) -> None:
    """The CLI's compact subcommand honours the estimator's notion of size."""
    sample = tmp_path / "long.txt"
    sample.write_text(LOREM, encoding="utf-8")

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["compact", "--file", str(sample), "--budget", "80"])

    assert code == 0
    assert estimate.count_tokens(out.getvalue().rstrip("\n")) <= 80


def test_ledger_usage_snapshot_does_not_leak_into_the_policy_check() -> None:
    """usage() hands back a copy, so a caller cannot corrupt the accounting."""
    budget = policy.Budget(limit=300, reserved=100)
    book = ledger.Ledger()
    book.charge("agent0", estimate.count_tokens("hello world"))

    snapshot = book.usage()
    snapshot["agent0"] = 10_000

    assert book.total() != 10_000
    assert budget.admits(book.total(), budget.remaining(book.total()))


def test_package_reexports_the_public_surface() -> None:
    """Callers should not need to know which module a name lives in."""
    for name in (
        "count_tokens",
        "estimate_messages",
        "Budget",
        "BudgetExceeded",
        "head_tail",
        "drop_oldest",
        "Ledger",
        "plan_fanout",
    ):
        assert hasattr(tokenbudget, name), f"tokenbudget.{name} is not re-exported"


def test_estimate_is_the_leaf_module() -> None:
    """estimate must not depend on any other package module."""
    source = estimate.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    for other in ("policy", "compact", "ledger", "planner", "cli"):
        assert f"import {other}" not in text
        assert f"from tokenbudget.{other}" not in text


def test_cli_usage_error_reports_two() -> None:
    """A usage error is a 2, not a traceback, so the shell can branch on it."""
    try:
        code = cli.main(["plan", "--total", "not-a-number", "--agents", "3"])
    except SystemExit as exc:  # argparse exits rather than returning
        code = exc.code
    assert code == 2
