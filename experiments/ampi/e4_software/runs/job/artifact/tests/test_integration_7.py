"""Cross-module integration tests for :mod:`tokenbudget`.

Each test here drives several modules together along a path a real caller
would take -- plan a fan-out, hand each agent a budget, compact its context to
fit, charge the ledger for what was actually spent -- and asserts the
invariants that only hold when the modules agree with each other. Single
module behaviour is covered by the per-module test files.
"""

from __future__ import annotations

import json

import pytest

from tokenbudget import compact, estimate, ledger, planner, policy
from tokenbudget.cli import main

ELISION = "\n...[elided]...\n"


def make_messages(n: int, size: int = 200) -> list[dict]:
    """A conversation of ``n`` messages whose contents are all distinct."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "word " * size}
        for i in range(n)
    ]


def test_fanout_shares_are_admitted_by_a_budget_reserving_the_remainder():
    """planner and policy must agree on what the reserve leaves spendable."""
    total = 100_000
    shares = planner.plan_fanout(total, 6, reserve_frac=0.1)

    assert len(shares) == 6
    assert all(share >= 0 for share in shares)

    spendable = sum(shares)
    reserved = total - spendable
    budget = policy.Budget(total, reserved=reserved)

    assert budget.remaining(0) == spendable
    assert budget.admits(0, spendable) is True
    assert budget.admits(0, spendable + 1) is False

    # Each agent's share must be admissible once every earlier agent has spent
    # its own share in full.
    used = 0
    for share in shares:
        assert budget.admits(used, share) is True
        used += share
    assert budget.remaining(used) == 0


def test_ledger_charges_against_plan_never_exceed_the_planned_total():
    """planner -> ledger -> policy: spending the plan exhausts it exactly."""
    total = 60_000
    shares = planner.plan_fanout(total, 4, reserve_frac=0.25)
    spendable = sum(shares)
    budget = policy.Budget(total, reserved=total - spendable)
    book = ledger.Ledger()

    for i, share in enumerate(shares):
        name = f"agent-{i}"
        assert budget.admits(book.total(), share) is True
        assert book.charge(name, share) == share

    assert book.total() == spendable
    assert budget.remaining(book.total()) == 0
    assert budget.admits(book.total(), 1) is False

    # Releasing one agent's charge must hand exactly that much back to the
    # budget, and the ledger's usage view must stay a copy.
    freed = shares[0]
    assert book.release("agent-0", freed) == 0
    assert budget.remaining(book.total()) == freed

    usage = book.usage()
    usage["agent-0"] = 10**9
    assert book.usage()["agent-0"] == 0
    assert book.total() == spendable - freed


def test_compacted_message_list_fits_the_share_the_planner_handed_out():
    """planner -> compact -> estimate: the compacted list really does fit."""
    total = 20_000
    shares = planner.plan_fanout(total, 5, reserve_frac=0.2)
    share = shares[0]

    messages = make_messages(40)
    assert estimate.estimate_messages(messages) > share

    kept = compact.drop_oldest(messages, share)

    assert kept, "drop_oldest must always keep at least the final message"
    assert kept[-1] == messages[-1]
    assert kept == messages[len(messages) - len(kept):], "must drop from the front only"
    assert estimate.estimate_messages(kept) <= share


def test_compaction_against_the_budget_left_after_the_ledger_is_charged():
    """policy + ledger decide the budget; compact + estimate must respect it."""
    budget = policy.Budget(4_000, reserved=500)
    book = ledger.Ledger()
    book.charge("writer", 1_200)

    remaining = budget.remaining(book.total())
    assert remaining == 4_000 - 500 - 1_200

    text = "sentence number one. " * 400
    assert estimate.count_tokens(text) > remaining

    shortened = compact.head_tail(text, remaining)
    cost = estimate.count_tokens(shortened)

    assert ELISION in shortened
    assert cost <= remaining
    assert budget.admits(book.total(), cost) is True

    # Charging the compacted text must stay inside the budget.
    book.charge("writer", cost)
    assert budget.remaining(book.total()) >= 0
    assert book.total() == 1_200 + cost


def test_full_fanout_round_trip_stays_within_the_overall_budget():
    """The whole pipeline: plan, compact per agent, charge, verify."""
    total = 50_000
    n_agents = 4
    shares = planner.plan_fanout(total, n_agents, reserve_frac=0.1)
    budget = policy.Budget(total, reserved=total - sum(shares))
    book = ledger.Ledger()

    for i, share in enumerate(shares):
        agent = f"agent-{i}"
        messages = make_messages(30 + i * 5)
        kept = compact.drop_oldest(messages, share)
        cost = estimate.estimate_messages(kept)

        assert cost <= share
        assert budget.admits(book.total(), cost) is True
        book.charge(agent, cost)

    assert book.total() <= sum(shares)
    assert budget.remaining(book.total()) == sum(shares) - book.total()
    assert sorted(book.usage()) == [f"agent-{i}" for i in range(n_agents)]
    assert sum(book.usage().values()) == book.total()


def test_overspend_is_visible_to_policy_and_can_be_signalled():
    """ledger + policy: an agent that ignores its share is caught."""
    shares = planner.plan_fanout(10_000, 2, reserve_frac=0.0)
    budget = policy.Budget(10_000)
    book = ledger.Ledger()

    def charge_or_raise(agent: str, tokens: int) -> int:
        if not budget.admits(book.total(), tokens):
            raise policy.BudgetExceeded(f"{agent} would exceed the budget")
        return book.charge(agent, tokens)

    charge_or_raise("greedy", shares[0])
    overspend = shares[1] + 1

    assert budget.admits(book.total(), overspend) is False
    with pytest.raises(policy.BudgetExceeded):
        charge_or_raise("greedy", overspend)

    with pytest.raises(ValueError):
        book.charge("greedy", -1)

    # A rejected charge must leave the books untouched.
    assert book.total() == shares[0]


def test_single_oversized_message_is_kept_and_the_caller_can_see_it_overflows():
    """compact + estimate + policy on the documented drop_oldest exception."""
    budget_tokens = 50
    messages = make_messages(4, size=300)
    kept = compact.drop_oldest(messages, budget_tokens)

    assert kept == [messages[-1]], "only the final message may survive"
    cost = estimate.estimate_messages(kept)
    assert cost > budget_tokens

    budget = policy.Budget(budget_tokens)
    assert budget.admits(0, cost) is False

    # head_tail is the escape hatch: it must bring the content itself under
    # the budget even when drop_oldest could not.
    trimmed = compact.head_tail(kept[-1]["content"], budget_tokens)
    assert estimate.count_tokens(trimmed) <= budget_tokens


def test_cli_plan_and_count_agree_with_the_library(capsys):
    """cli must be a thin shell over planner and estimate."""
    assert main(["plan", "--total", "30000", "--agents", "3"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == planner.plan_fanout(30_000, 3)

    text = "the quick brown fox jumps over the lazy dog"
    assert main(["count", "--text", text]) == 0
    counted = json.loads(capsys.readouterr().out)
    counted_value = counted["tokens"] if isinstance(counted, dict) else counted
    assert counted_value == estimate.count_tokens(text)

    # A usage error must surface as status 2, whether main returns it or
    # argparse raises SystemExit on the way out.
    try:
        status = main(["plan", "--total", "1000", "--agents", "0"])
    except SystemExit as exc:
        status = exc.code
    assert status == 2


def test_cli_compact_output_fits_the_budget_it_was_given(tmp_path, capsys):
    """cli -> compact -> estimate: what the CLI prints must fit."""
    budget_tokens = 120
    source = tmp_path / "context.txt"
    source.write_text("alpha beta gamma delta " * 300, encoding="utf-8")

    assert main(["compact", "--file", str(source), "--budget", str(budget_tokens)]) == 0
    printed = capsys.readouterr().out

    assert estimate.count_tokens(printed.rstrip("\n")) <= budget_tokens
    assert ELISION in printed
