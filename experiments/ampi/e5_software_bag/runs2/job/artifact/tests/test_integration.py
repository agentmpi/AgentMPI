"""Cross-module integration tests for the tokenbudget package.

Every test here drives at least two modules against each other. The unit test
files already pin down each module on its own; what these check is that the
modules agree on the numbers they hand back and forth -- that a share produced
by the planner can be spent through the ledger, enforced by the policy, and
that whatever compaction returns really does fit when the estimator is asked
again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenbudget import estimate as estimate_module
from tokenbudget.cli import main
from tokenbudget.compact import ELISION_MARKER, drop_oldest, head_tail
from tokenbudget.estimate import count_tokens, estimate_messages
from tokenbudget.ledger import Ledger
from tokenbudget.planner import plan_fanout
from tokenbudget.policy import Budget

CONTRACT_EXPORTS = (
    "Budget",
    "BudgetExceeded",
    "Ledger",
    "count_tokens",
    "drop_oldest",
    "estimate_messages",
    "head_tail",
    "main",
    "plan_fanout",
)


def _turns(count: int, size: int) -> list[dict]:
    """Build a chat history of ``count`` turns whose contents are ``size`` chars."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "z" * size}
        for i in range(count)
    ]


def test_fanout_share_is_charged_then_compacted_to_what_remains():
    """planner -> policy -> ledger -> compact -> estimate, on one agent's share."""
    shares = plan_fanout(total_budget=4000, n_agents=4, reserve_frac=0.1)
    assert len(shares) == 4
    assert sum(shares) <= int(4000 * 0.9)

    agent = "agent-0"
    budget = Budget(limit=shares[0])
    ledger = Ledger()

    system_prompt = "You are a careful assistant. " * 12
    ledger.charge(agent, count_tokens(system_prompt))

    remaining = budget.remaining(ledger.balance(agent))
    assert remaining == shares[0] - count_tokens(system_prompt)

    history = _turns(20, 200)
    assert estimate_messages(history) > remaining, "history must not already fit"

    kept = drop_oldest(history, remaining)

    # The whole point: re-measure the compacted list and it genuinely fits.
    assert estimate_messages(kept) <= remaining
    assert budget.admits(ledger.balance(agent), estimate_messages(kept))
    assert kept[-1] == history[-1], "the newest turn must survive"
    assert kept == history[len(history) - len(kept):], "only a suffix is kept"


def test_head_tail_fits_the_headroom_the_ledger_left_behind():
    """A long document compacted to the policy's remaining allowance really fits."""
    shares = plan_fanout(2000, 2)
    budget = Budget(limit=shares[0], reserved=50)
    ledger = Ledger()
    ledger.charge("summariser", 120)

    remaining = budget.remaining(ledger.balance("summariser"))
    assert remaining == shares[0] - 50 - 120

    document = "sentence. " * 500
    assert count_tokens(document) > remaining, "document must not already fit"

    compacted = head_tail(document, remaining)

    assert count_tokens(compacted) <= remaining
    assert budget.admits(ledger.balance("summariser"), count_tokens(compacted))
    assert ELISION_MARKER in compacted
    assert document.startswith(compacted[: compacted.index(ELISION_MARKER)])
    assert document.endswith(compacted[compacted.index(ELISION_MARKER) + len(ELISION_MARKER):])


def test_every_agent_in_a_fanout_fits_and_the_whole_plan_fits_the_total():
    """Each agent compacts to its own share; the sum never breaks the global budget."""
    total, n_agents, reserve = 6000, 5, 0.2
    shares = plan_fanout(total, n_agents, reserve_frac=reserve)
    ledger = Ledger()

    for index, share in enumerate(shares):
        agent = f"agent-{index}"
        transcript = f"log line for {agent}. " * (60 * (index + 1))
        compacted = head_tail(transcript, share)
        spent = count_tokens(compacted)

        assert spent <= share, f"{agent} overspent its share"
        ledger.charge(agent, spent)

    assert set(ledger.usage()) == {f"agent-{i}" for i in range(n_agents)}
    assert ledger.total() <= sum(shares) <= int(total * (1 - reserve))

    # The reserve really is still there once every agent has been charged.
    global_budget = Budget(limit=total, reserved=total - sum(shares))
    assert global_budget.remaining(ledger.total()) >= 0
    assert global_budget.admits(ledger.total(), 0)


def test_releasing_tokens_readmits_a_message_the_policy_had_rejected():
    """ledger.release restores headroom that policy and compact both then honour."""
    ledger = Ledger()
    budget = Budget(limit=plan_fanout(1000, 1)[0])
    message = [{"role": "user", "content": "y" * 400}]
    cost = estimate_messages(message)

    ledger.charge("agent-0", 880)
    assert not budget.admits(ledger.balance("agent-0"), cost)

    # Compaction cannot help: a single message is kept even when it does not fit.
    starved = budget.remaining(ledger.balance("agent-0"))
    assert drop_oldest(message, starved) == message
    assert estimate_messages(message) > starved

    snapshot = ledger.usage()
    ledger.release("agent-0", 500)

    assert snapshot["agent-0"] == 880, "usage() must be a copy, not a live view"
    assert budget.admits(ledger.balance("agent-0"), cost)
    assert estimate_messages(drop_oldest(message, budget.remaining(ledger.balance("agent-0")))) <= (
        budget.remaining(ledger.balance("agent-0"))
    )


def test_compaction_drops_no_more_than_the_estimator_demands():
    """compact and estimate agree on the boundary: nothing is thrown away needlessly."""
    history = _turns(12, 120)
    budget = estimate_messages(history) // 2
    kept = drop_oldest(history, budget)

    assert estimate_messages(kept) <= budget
    assert len(kept) < len(history), "this budget must force a drop"

    one_more = history[len(history) - len(kept) - 1:]
    assert estimate_messages(one_more) > budget, "an extra turn would have fitted"

    # Headroom for exactly one turn must drop every older turn, not stop short.
    newest_only = estimate_messages(history[-1:])
    assert drop_oldest(history, newest_only) == history[-1:]
    assert drop_oldest(history, newest_only - 1) == history[-1:]

    # head_tail respects the same boundary: exactly-fitting text is untouched.
    exact = "q" * (budget * 4)
    assert count_tokens(exact) == budget
    assert head_tail(exact, budget) == exact

    over = exact + "q"
    assert count_tokens(over) == budget + 1
    assert head_tail(over, budget) != over
    assert count_tokens(head_tail(over, budget)) <= budget


def test_cli_plan_and_compact_agree_with_the_library_they_wrap(tmp_path, capsys):
    """The CLI is a faithful front end: its plan and compact match direct calls."""
    assert main(["plan", "--total", "3000", "--agents", "4"]) == 0
    shares = json.loads(capsys.readouterr().out)
    assert shares == plan_fanout(3000, 4)

    document = "alpha beta gamma delta " * 300
    path = tmp_path / "transcript.txt"
    path.write_text(document, encoding="utf-8")

    assert main(["compact", "--file", str(path), "--budget", str(shares[0])]) == 0
    printed = capsys.readouterr().out

    assert printed.rstrip("\n") == head_tail(document, shares[0])
    assert count_tokens(printed.rstrip("\n")) <= shares[0]

    assert main(["count", "--text", printed.rstrip("\n")]) == 0
    assert json.loads(capsys.readouterr().out)["tokens"] <= shares[0]


@pytest.mark.xfail(
    strict=True,
    reason="defect INT-1 (rank 6): cli compact appends a newline to output that "
    "head_tail had already sized to exactly fill the budget, so piping compact "
    "into count reports budget + 1",
)
def test_cli_compact_piped_into_cli_count_stays_within_the_budget(tmp_path, capsys):
    """compact | count is the advertised pipeline; the byte stream must fit."""
    document = "alpha beta gamma delta " * 300
    source = tmp_path / "transcript.txt"
    source.write_text(document, encoding="utf-8")
    budget = 675

    assert main(["compact", "--file", str(source), "--budget", str(budget)]) == 0
    piped = tmp_path / "compacted.txt"
    piped.write_text(capsys.readouterr().out, encoding="utf-8")

    assert main(["count", "--file", str(piped)]) == 0
    assert json.loads(capsys.readouterr().out)["tokens"] <= budget


def test_package_reexports_the_contract_and_estimate_stays_the_leaf():
    """The public surface matches the contract and the dependency rule still holds."""
    import tokenbudget

    for name in CONTRACT_EXPORTS:
        assert hasattr(tokenbudget, name), f"tokenbudget.{name} is not re-exported"

    assert tokenbudget.count_tokens is count_tokens
    assert tokenbudget.plan_fanout is plan_fanout
    assert tokenbudget.Ledger is Ledger

    source = Path(estimate_module.__file__).read_text(encoding="utf-8")
    assert "from tokenbudget" not in source
    assert "import tokenbudget" not in source
