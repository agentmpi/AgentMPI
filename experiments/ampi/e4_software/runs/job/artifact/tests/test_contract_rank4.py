"""Per-module contract tests for :mod:`tokenbudget`, written by rank 4.

Rank 4 won no module in the claim round, so this file is the spare's
contribution. It deliberately does not duplicate the cross-module pipeline
tests in ``test_integration_7.py``; instead it pins down, module by module,
the edge cases the published contract calls out by name -- empty input, zero,
negative, the "always keeps the last message" rule, the exact per-message
overhead, the boundary between admitting and refusing -- and then checks the
package-wide rules that no single module's own test file can see, such as
every module having a docstring and ``estimate`` staying a leaf.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from tokenbudget import compact, estimate, ledger, planner, policy
from tokenbudget.cli import main

ELISION = "\n...[elided]...\n"
PER_MESSAGE_OVERHEAD = 4
MODULE_NAMES = ("cli", "compact", "estimate", "ledger", "planner", "policy")

STDLIB_ROOTS = frozenset(sys.stdlib_module_names)

PACKAGE_DIR = Path(estimate.__file__).parent


def run_cli(argv: list[str]) -> int:
    """Call ``cli.main`` and return its status, even if argparse exits."""
    try:
        return main(argv)
    except SystemExit as exc:
        return 2 if exc.code is None else int(exc.code)


def parse_module(name: str) -> ast.Module:
    """The AST of one package module, read from source."""
    return ast.parse((PACKAGE_DIR / f"{name}.py").read_text(encoding="utf-8"))


def make_messages(n: int, size: int = 50) -> list[dict]:
    """A conversation of ``n`` messages with distinct contents."""
    return [{"role": "user", "content": f"m{i} " + "word " * size} for i in range(n)]


# --------------------------------------------------------------------------
# estimate: the leaf. Deterministic, zero on empty, monotone, never negative.
# --------------------------------------------------------------------------


def test_count_tokens_is_zero_for_empty_and_none_input():
    assert estimate.count_tokens("") == 0
    assert estimate.count_tokens(None) == 0


def test_count_tokens_is_deterministic_and_never_negative():
    samples = ["", " ", "a", "hello world", "\n\n", "ünïcodé ", "x" * 5000, "a b c " * 100]
    for text in samples:
        first = estimate.count_tokens(text)
        assert first == estimate.count_tokens(text), f"not deterministic for {text[:20]!r}"
        assert isinstance(first, int)
        assert first >= 0, f"negative count for {text[:20]!r}"


def test_count_tokens_is_monotone_non_decreasing_in_length():
    text = "the quick brown fox jumps over the lazy dog, and then does it again "
    counts = [estimate.count_tokens(text[:i]) for i in range(len(text) + 1)]
    assert counts == sorted(counts), "count_tokens must not shrink as text grows"
    assert counts[0] == 0
    assert counts[-1] > 0


def test_estimate_messages_is_the_sum_of_contents_plus_four_per_message():
    messages = make_messages(5)
    expected = sum(estimate.count_tokens(m["content"]) for m in messages)
    expected += PER_MESSAGE_OVERHEAD * len(messages)
    assert estimate.estimate_messages(messages) == expected


def test_estimate_messages_of_empty_input_is_zero_and_empty_contents_cost_only_overhead():
    assert estimate.estimate_messages([]) == 0
    blank = [{"role": "user", "content": ""}, {"role": "assistant", "content": ""}]
    assert estimate.estimate_messages(blank) == 2 * PER_MESSAGE_OVERHEAD


# --------------------------------------------------------------------------
# ledger: balances never go below zero, usage() is a copy, negatives raise.
# --------------------------------------------------------------------------


def test_charge_and_release_return_the_new_balance():
    book = ledger.Ledger()
    assert book.charge("a", 100) == 100
    assert book.charge("a", 50) == 150
    assert book.release("a", 30) == 120
    assert book.usage()["a"] == 120


def test_balances_never_go_below_zero_when_releasing_too_much():
    book = ledger.Ledger()
    book.charge("a", 10)
    assert book.release("a", 999) == 0
    assert book.usage()["a"] == 0
    assert book.total() == 0
    # Releasing against an agent that was never charged must also floor at zero.
    assert book.release("never-seen", 5) == 0
    assert book.total() == 0


def test_charge_with_negative_tokens_raises_value_error_and_leaves_the_books_alone():
    book = ledger.Ledger()
    book.charge("a", 7)
    with pytest.raises(ValueError):
        book.charge("a", -1)
    assert book.usage()["a"] == 7
    assert book.total() == 7


def test_charging_zero_tokens_is_allowed_and_changes_nothing():
    book = ledger.Ledger()
    book.charge("a", 12)
    assert book.charge("a", 0) == 12
    assert book.total() == 12


def test_usage_returns_a_copy_that_cannot_corrupt_the_ledger():
    book = ledger.Ledger()
    book.charge("a", 5)
    snapshot = book.usage()
    snapshot["a"] = 10**9
    snapshot["injected"] = 1
    assert book.usage() == {"a": 5}
    assert book.total() == 5


def test_a_fresh_ledger_is_empty_and_total_sums_every_agent():
    book = ledger.Ledger()
    assert book.usage() == {}
    assert book.total() == 0
    book.charge("a", 3)
    book.charge("b", 4)
    assert book.total() == 7
    assert book.total() == sum(book.usage().values())


# --------------------------------------------------------------------------
# planner: n_agents shares, evenly, remainder to the earliest, validated.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "n_agents", "reserve_frac"),
    [(100, 3, 0.1), (1000, 7, 0.0), (10, 4, 0.5), (99, 1, 0.25), (1, 3, 0.1)],
)
def test_plan_fanout_shares_are_even_non_negative_and_within_the_reserve(
    total: int, n_agents: int, reserve_frac: float
):
    shares = planner.plan_fanout(total, n_agents, reserve_frac=reserve_frac)

    assert len(shares) == n_agents
    assert all(isinstance(share, int) and share >= 0 for share in shares)
    assert sum(shares) <= total * (1 - reserve_frac) + 1e-9
    assert max(shares) - min(shares) <= 1, "shares must be as even as possible"
    assert shares == sorted(shares, reverse=True), "remainder goes to the earliest agents"


def test_plan_fanout_of_a_zero_budget_gives_every_agent_zero():
    assert planner.plan_fanout(0, 4) == [0, 0, 0, 0]


def test_plan_fanout_rejects_fewer_than_one_agent_and_a_negative_budget():
    with pytest.raises(ValueError):
        planner.plan_fanout(100, 0)
    with pytest.raises(ValueError):
        planner.plan_fanout(100, -1)
    with pytest.raises(ValueError):
        planner.plan_fanout(-1, 3)


def test_plan_fanout_reserving_everything_leaves_nothing_to_spend():
    assert sum(planner.plan_fanout(500, 5, reserve_frac=1.0)) == 0


# --------------------------------------------------------------------------
# policy: remaining() floors at zero, admits() is an inclusive boundary.
# --------------------------------------------------------------------------


def test_remaining_is_limit_minus_reserved_minus_used_floored_at_zero():
    budget = policy.Budget(1000, reserved=200)
    assert budget.remaining(0) == 800
    assert budget.remaining(300) == 500
    assert budget.remaining(800) == 0
    assert budget.remaining(5000) == 0, "remaining must never go negative"


def test_admits_is_true_exactly_up_to_the_remaining_budget():
    budget = policy.Budget(100, reserved=10)
    assert budget.admits(0, 0) is True
    assert budget.admits(0, 90) is True
    assert budget.admits(0, 91) is False
    assert budget.admits(90, 0) is True
    assert budget.admits(90, 1) is False
    # Once used has overrun the limit, nothing further is admitted.
    assert budget.admits(1000, 1) is False


def test_budget_rejects_negative_limit_negative_reserved_and_over_reserving():
    with pytest.raises(ValueError):
        policy.Budget(-1)
    with pytest.raises(ValueError):
        policy.Budget(100, reserved=-1)
    with pytest.raises(ValueError):
        policy.Budget(100, reserved=101)


def test_a_zero_limit_budget_admits_only_nothing():
    budget = policy.Budget(0)
    assert budget.remaining(0) == 0
    assert budget.admits(0, 0) is True
    assert budget.admits(0, 1) is False


def test_budget_exceeded_is_a_raisable_exception():
    assert issubclass(policy.BudgetExceeded, Exception)
    with pytest.raises(policy.BudgetExceeded):
        raise policy.BudgetExceeded("over")


# --------------------------------------------------------------------------
# compact: unchanged when it fits, elided when it does not, last message kept.
# --------------------------------------------------------------------------


def test_head_tail_returns_text_unchanged_when_it_already_fits():
    text = "short enough to keep"
    budget = estimate.count_tokens(text)
    assert compact.head_tail(text, budget) == text
    assert compact.head_tail(text, budget + 1000) == text
    assert compact.head_tail("", 10) == ""


def test_head_tail_keeps_a_prefix_and_a_suffix_of_the_original_within_budget():
    text = "alpha beta gamma delta epsilon " * 200
    budget = 100
    assert estimate.count_tokens(text) > budget

    result = compact.head_tail(text, budget)

    assert ELISION in result
    assert estimate.count_tokens(result) <= budget
    head, tail = result.split(ELISION, 1)
    assert text.startswith(head), "the head must be a real prefix of the input"
    assert text.endswith(tail), "the tail must be a real suffix of the input"


def test_head_tail_gives_more_room_to_the_head_as_head_frac_grows():
    text = "one two three four five six seven eight nine ten " * 100
    budget = 120

    small_head = compact.head_tail(text, budget, head_frac=0.2).split(ELISION, 1)[0]
    large_head = compact.head_tail(text, budget, head_frac=0.8).split(ELISION, 1)[0]

    assert len(large_head) >= len(small_head)


def test_drop_oldest_returns_empty_for_empty_input_and_leaves_a_fitting_list_alone():
    assert compact.drop_oldest([], 100) == []
    assert compact.drop_oldest([], 0) == []

    messages = make_messages(3, size=2)
    budget = estimate.estimate_messages(messages)
    assert compact.drop_oldest(messages, budget) == messages


def test_drop_oldest_drops_from_the_front_and_no_more_than_necessary():
    messages = make_messages(20)
    budget = estimate.estimate_messages(messages) // 3

    kept = compact.drop_oldest(messages, budget)

    assert kept == messages[len(messages) - len(kept):], "must drop from the front only"
    assert estimate.estimate_messages(kept) <= budget
    assert len(kept) < len(messages)
    one_more = messages[len(messages) - len(kept) - 1:]
    assert estimate.estimate_messages(one_more) > budget, "dropped more than needed"


def test_drop_oldest_always_keeps_the_last_message_even_when_it_alone_overflows():
    messages = make_messages(4, size=100)
    kept = compact.drop_oldest(messages, 1)
    assert kept == [messages[-1]]
    assert estimate.estimate_messages(kept) > 1

    # Zero and a negative budget must still leave the final message standing.
    assert compact.drop_oldest(messages, 0) == [messages[-1]]
    assert compact.drop_oldest(messages, -5) == [messages[-1]]


def test_drop_oldest_does_not_mutate_the_caller_s_list():
    messages = make_messages(12)
    original = list(messages)
    compact.drop_oldest(messages, estimate.estimate_messages(messages) // 2)
    assert messages == original


# --------------------------------------------------------------------------
# cli: JSON on stdout, 0 on success, 2 on a usage error.
# --------------------------------------------------------------------------


def test_cli_count_reads_text_and_files_and_agrees_with_estimate(tmp_path, capsys):
    text = "the quick brown fox"
    assert run_cli(["count", "--text", text]) == 0
    payload = json.loads(capsys.readouterr().out)
    counted = payload["tokens"] if isinstance(payload, dict) else payload
    assert counted == estimate.count_tokens(text)

    source = tmp_path / "in.txt"
    source.write_text(text, encoding="utf-8")
    assert run_cli(["count", "--file", str(source)]) == 0
    from_file = json.loads(capsys.readouterr().out)
    assert (from_file["tokens"] if isinstance(from_file, dict) else from_file) == counted


def test_cli_plan_prints_a_json_list_matching_the_planner(capsys):
    assert run_cli(["plan", "--total", "1000", "--agents", "4"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert isinstance(printed, list)
    assert printed == planner.plan_fanout(1000, 4)


def test_cli_compact_prints_text_that_fits_the_budget(tmp_path, capsys):
    source = tmp_path / "ctx.txt"
    source.write_text("alpha beta gamma delta " * 300, encoding="utf-8")
    budget = 100

    assert run_cli(["compact", "--file", str(source), "--budget", str(budget)]) == 0
    printed = capsys.readouterr().out

    assert ELISION in printed
    assert estimate.count_tokens(printed.rstrip("\n")) <= budget


@pytest.mark.parametrize(
    "argv",
    [
        ["count"],  # neither --text nor --file
        ["plan", "--total", "1000"],  # missing --agents
        ["plan", "--total", "1000", "--agents", "0"],  # planner rejects it
        ["compact", "--budget", "10"],  # missing --file
        ["no-such-subcommand"],
    ],
)
def test_cli_returns_two_on_a_usage_error(argv: list[str]):
    assert run_cli(argv) == 2


# --------------------------------------------------------------------------
# Package-wide rules from the contract, checked against the source itself.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", MODULE_NAMES)
def test_every_module_has_a_docstring(name: str):
    docstring = ast.get_docstring(parse_module(name))
    assert docstring, f"{name}.py needs a module docstring"
    assert docstring.strip(), f"{name}.py has an empty docstring"


@pytest.mark.parametrize("name", MODULE_NAMES)
def test_public_functions_are_type_annotated(name: str):
    tree = parse_module(name)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_") and node.name != "__init__":
            continue
        args = node.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        for arg in params:
            if arg.arg in {"self", "cls"}:
                continue
            assert arg.annotation is not None, f"{name}.{node.name}({arg.arg}) is unannotated"
        if node.name != "__init__":
            assert node.returns is not None, f"{name}.{node.name} has no return annotation"


def test_estimate_is_a_leaf_and_imports_no_other_package_module():
    for node in ast.walk(parse_module("estimate")):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("tokenbudget"), "estimate must stay a leaf"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("tokenbudget"), "estimate must stay a leaf"
            assert node.level == 0, "estimate must not use relative imports"


@pytest.mark.parametrize("name", MODULE_NAMES)
def test_no_module_imports_another_module_s_private_names(name: str):
    for node in ast.walk(parse_module(name)):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            private = alias.name.startswith("_") and not alias.name.startswith("__")
            assert not private, f"{name}.py imports the private name {alias.name}"


def test_the_package_declares_no_third_party_dependencies():
    stdlib_or_package = set(MODULE_NAMES) | {"tokenbudget"}
    for name in MODULE_NAMES:
        for node in ast.walk(parse_module(name)):
            roots = []
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = [node.module.split(".")[0]]
            for root in roots:
                assert root in stdlib_or_package or root in STDLIB_ROOTS, (
                    f"{name}.py imports {root}, which is not in the standard library"
                )
