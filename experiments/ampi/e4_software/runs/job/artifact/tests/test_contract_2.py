"""Contract-conformance tests for :mod:`tokenbudget`.

These tests come from the published contract rather than from any one
implementation. They fall into two groups: package-wide rules checked by
reading the source (module docstrings, standard library only, ``estimate`` as
the leaf module, no imports of another module's private names), and per-module
edge cases the contract calls out by name -- empty input, zero, negative
values, boundary admission, and the "always keeps the last message" rule.

Modules are imported through :func:`load`, so a module that has not landed yet
-- or one whose own dependency has not landed yet -- turns into a skip instead
of breaking the whole suite.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "tokenbudget"
MODULES = ("cli", "compact", "estimate", "ledger", "planner", "policy")
ELISION = "\n...[elided]...\n"


def load(name: str) -> ModuleType:
    """Import ``tokenbudget.<name>``, skipping the test if it is not written."""
    try:
        return importlib.import_module(f"tokenbudget.{name}")
    except ImportError as exc:
        pytest.skip(f"tokenbudget.{name} is not importable yet: {exc}")


def source_files() -> dict[str, Path]:
    """The module files that currently exist, keyed by module name."""
    found = {name: PACKAGE_ROOT / f"{name}.py" for name in MODULES}
    return {name: path for name, path in found.items() if path.is_file()}


def parsed_sources() -> dict[str, tuple[Path, ast.Module]]:
    """Parse every existing module, or skip if none has been written yet."""
    files = source_files()
    if not files:
        pytest.skip("no tokenbudget modules have been written yet")
    return {name: (path, ast.parse(path.read_text(encoding="utf-8"))) for name, path in files.items()}


def imported_roots(tree: ast.Module) -> set[str]:
    """The top-level package name of every import in ``tree``."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


# --------------------------------------------------------------------------
# Package rules
# --------------------------------------------------------------------------


def test_every_module_has_a_module_docstring():
    """Rule: every module says what it is for."""
    missing = [name for name, (_, tree) in parsed_sources().items() if not ast.get_docstring(tree)]
    assert missing == [], f"modules without a docstring: {missing}"


def test_modules_import_only_the_standard_library_and_the_package_itself():
    """Rule: standard library only, no third-party dependencies."""
    allowed = set(sys.stdlib_module_names) | {"tokenbudget", "__future__"}
    offenders: dict[str, set[str]] = {}
    for name, (_, tree) in parsed_sources().items():
        outside = imported_roots(tree) - allowed
        if outside:
            offenders[name] = outside
    assert offenders == {}, f"non-stdlib imports: {offenders}"


def test_estimate_is_the_leaf_module():
    """Rule: estimate.py must not import any other package module."""
    sources = parsed_sources()
    if "estimate" not in sources:
        pytest.skip("estimate.py has not been written yet")

    _, tree = sources["estimate"]
    siblings = {name for name in MODULES if name != "estimate"}
    pulled_in: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "tokenbudget" and len(parts) > 1:
                    pulled_in.add(parts[1])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("tokenbudget") or node.level:
                pulled_in.update({alias.name for alias in node.names} & siblings)
                tail = module.split(".")
                if len(tail) > 1:
                    pulled_in.add(tail[1])

    assert pulled_in & siblings == set(), f"estimate imports package modules: {sorted(pulled_in)}"


def test_no_module_imports_another_modules_private_names():
    """Rule: no module reaches into another module's private names."""
    offenders: dict[str, list[str]] = {}
    for name, (_, tree) in parsed_sources().items():
        bad: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if not (module.startswith("tokenbudget") or node.level):
                continue
            bad.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith("_") and not alias.name.startswith("__")
            )
        if bad:
            offenders[name] = bad
    assert offenders == {}, f"private cross-module imports: {offenders}"


@pytest.mark.parametrize("module_name", MODULES)
def test_public_callables_are_type_annotated(module_name):
    """Rule: public functions have type annotations."""
    module = load(module_name)
    unannotated: list[str] = []

    def check(func, label: str) -> None:
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):  # pragma: no cover - builtins only
            return
        for parameter in signature.parameters.values():
            if parameter.name in {"self", "cls"} or parameter.kind is parameter.VAR_KEYWORD:
                continue
            if parameter.annotation is inspect.Parameter.empty:
                unannotated.append(f"{label}({parameter.name})")
        if signature.return_annotation is inspect.Signature.empty:
            unannotated.append(f"{label}(-> ?)")

    for name, obj in vars(module).items():
        if name.startswith("_") or getattr(obj, "__module__", None) != module.__name__:
            continue
        if inspect.isfunction(obj):
            check(obj, f"{module_name}.{name}")
        elif inspect.isclass(obj) and not issubclass(obj, BaseException):
            for attr, method in vars(obj).items():
                if inspect.isfunction(method) and (attr == "__init__" or not attr.startswith("_")):
                    check(method, f"{module_name}.{name}.{attr}")

    assert unannotated == [], f"missing annotations: {unannotated}"


# --------------------------------------------------------------------------
# estimate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("empty", ["", None])
def test_count_tokens_returns_zero_for_empty_and_none_input(empty):
    """Contract: count_tokens returns 0 for empty or None input."""
    assert load("estimate").count_tokens(empty) == 0


def test_count_tokens_is_deterministic_non_negative_and_monotone():
    """Contract: deterministic, never negative, non-decreasing in len(text)."""
    estimate = load("estimate")
    text = "the quick brown fox jumps over the lazy dog. " * 20

    assert estimate.count_tokens(text) == estimate.count_tokens(text)

    previous = 0
    for length in range(0, len(text) + 1, 7):
        current = estimate.count_tokens(text[:length])
        assert current >= 0
        assert current >= previous, f"count dropped at len={length}"
        previous = current


def test_estimate_messages_adds_four_tokens_of_overhead_per_message():
    """Contract: sum of count_tokens over 'content' plus 4 tokens per message."""
    estimate = load("estimate")
    contents = ["hello there", "a much longer reply than the question was", ""]
    messages = [{"role": "user", "content": text} for text in contents]

    assert estimate.estimate_messages([]) == 0
    for text in contents:
        assert estimate.estimate_messages([{"role": "user", "content": text}]) == (
            estimate.count_tokens(text) + 4
        )
    assert estimate.estimate_messages(messages) == sum(
        estimate.count_tokens(text) for text in contents
    ) + 4 * len(messages)


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------


def test_budget_remaining_clamps_at_zero_and_admits_exactly_at_the_boundary():
    """Contract: remaining() is max(0, limit - reserved - used)."""
    policy = load("policy")
    budget = policy.Budget(1_000, reserved=250)

    assert budget.remaining(0) == 750
    assert budget.remaining(750) == 0
    assert budget.remaining(10_000) == 0, "remaining() must never go negative"

    assert budget.admits(0, 750) is True, "incoming == remaining must be admitted"
    assert budget.admits(0, 751) is False
    assert budget.admits(0, 0) is True
    assert budget.admits(750, 1) is False
    assert budget.admits(10_000, 0) is True


@pytest.mark.parametrize(
    ("limit", "reserved"),
    [(-1, 0), (0, -1), (100, -5), (100, 101), (0, 1)],
)
def test_budget_rejects_negative_or_oversized_reservations(limit, reserved):
    """Contract: negative limit or reserved, and reserved > limit, are ValueError."""
    with pytest.raises(ValueError):
        load("policy").Budget(limit, reserved=reserved)


def test_zero_budget_admits_nothing_and_budget_exceeded_is_an_exception():
    """Edge case: a limit of zero, and the shape of BudgetExceeded."""
    policy = load("policy")
    budget = policy.Budget(0)

    assert budget.remaining(0) == 0
    assert budget.admits(0, 0) is True
    assert budget.admits(0, 1) is False

    assert issubclass(policy.BudgetExceeded, Exception)
    with pytest.raises(policy.BudgetExceeded):
        raise policy.BudgetExceeded("over")


# --------------------------------------------------------------------------
# planner
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "n_agents", "reserve_frac"),
    [
        (0, 1, 0.1),
        (0, 5, 0.0),
        (1, 4, 0.0),
        (10, 3, 0.1),
        (100, 7, 0.25),
        (1_000, 1, 0.5),
        (99_991, 6, 0.1),
        (1_000, 3, 1.0),
    ],
)
def test_plan_fanout_shape_sum_and_evenness(total, n_agents, reserve_frac):
    """Contract: n_agents non-negative ints, capped by the reserve, even, remainder first."""
    shares = load("planner").plan_fanout(total, n_agents, reserve_frac=reserve_frac)

    assert len(shares) == n_agents
    assert all(isinstance(share, int) for share in shares)
    assert all(share >= 0 for share in shares)
    assert sum(shares) <= total * (1 - reserve_frac) + 1e-9

    assert max(shares) - min(shares) <= 1, "shares must be as even as possible"
    assert shares == sorted(shares, reverse=True), "the remainder goes to the earliest agents"


def test_plan_fanout_gives_the_remainder_to_the_earliest_agents():
    """Contract: a remainder that cannot be split evenly lands at the front."""
    shares = load("planner").plan_fanout(10, 4, reserve_frac=0.0)

    assert sum(shares) == 10
    assert shares == [3, 3, 2, 2]


@pytest.mark.parametrize(
    ("total", "n_agents"),
    [(1_000, 0), (1_000, -1), (-1, 4), (-1_000, 1)],
)
def test_plan_fanout_rejects_bad_arguments(total, n_agents):
    """Contract: ValueError if n_agents < 1 or total_budget < 0."""
    with pytest.raises(ValueError):
        load("planner").plan_fanout(total, n_agents)


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------


def test_ledger_charge_and_release_return_the_new_balance():
    """Contract: charge and release both return the agent's new balance."""
    book = load("ledger").Ledger()

    assert book.charge("writer", 100) == 100
    assert book.charge("writer", 50) == 150
    assert book.charge("reader", 20) == 20
    assert book.release("writer", 50) == 100
    assert book.total() == 120


def test_ledger_balances_never_go_below_zero():
    """Contract: balances never go below zero, however much is released."""
    book = load("ledger").Ledger()
    book.charge("writer", 30)

    assert book.release("writer", 1_000) == 0
    assert book.total() == 0
    assert book.release("never-charged", 5) == 0
    assert all(balance >= 0 for balance in book.usage().values())


def test_ledger_usage_returns_a_copy_and_totals_stay_consistent():
    """Contract: usage() returns a copy, not the internal dict."""
    book = load("ledger").Ledger()
    book.charge("a", 10)
    book.charge("b", 25)

    first = book.usage()
    assert first is not book.usage(), "usage() must hand back a fresh dict"

    first["a"] = 10**6
    first["injected"] = 7
    assert book.usage().get("a") == 10
    assert "injected" not in book.usage()
    assert book.total() == 35 == sum(book.usage().values())


def test_ledger_rejects_negative_charges_and_accepts_zero():
    """Contract: charge with negative tokens raises ValueError."""
    book = load("ledger").Ledger()
    book.charge("writer", 10)

    with pytest.raises(ValueError):
        book.charge("writer", -1)
    with pytest.raises(ValueError):
        book.charge("fresh", -100)

    assert book.charge("writer", 0) == 10, "a zero charge is a no-op, not an error"
    assert book.total() == 10, "a rejected charge must leave the books untouched"


def test_empty_ledger_starts_at_zero():
    """Edge case: a ledger nobody has charged."""
    book = load("ledger").Ledger()

    assert book.total() == 0
    assert book.usage() == {}


# --------------------------------------------------------------------------
# compact
# --------------------------------------------------------------------------


def test_head_tail_returns_text_unchanged_when_it_already_fits():
    """Contract: text that fits is returned unchanged, marker and all absent."""
    compact, estimate = load("compact"), load("estimate")
    text = "a short sentence that costs very little"
    budget = estimate.count_tokens(text)

    assert compact.head_tail(text, budget) == text
    assert compact.head_tail("", budget) == ""
    assert ELISION not in compact.head_tail(text, budget)


@pytest.mark.parametrize("budget", [40, 120, 400])
def test_head_tail_elides_the_middle_and_fits_the_budget(budget):
    """Contract: an over-budget text keeps a head and a tail joined by the marker."""
    compact, estimate = load("compact"), load("estimate")
    text = " ".join(f"word{i:04d}" for i in range(2_000))
    assert estimate.count_tokens(text) > budget

    result = compact.head_tail(text, budget)

    assert ELISION in result, "the elision marker must show where content was cut"
    assert estimate.count_tokens(result) <= budget

    head, _, tail = result.partition(ELISION)
    assert text.startswith(head), "the head must be a real prefix of the input"
    assert text.endswith(tail), "the tail must be a real suffix of the input"
    assert len(result) < len(text)


def test_head_tail_head_frac_controls_how_much_of_the_head_survives():
    """Contract: head_frac selects the prefix share of what is kept."""
    compact = load("compact")
    text = " ".join(f"word{i:04d}" for i in range(2_000))

    mostly_head = compact.head_tail(text, 200, head_frac=0.9)
    mostly_tail = compact.head_tail(text, 200, head_frac=0.1)

    head_of_head = mostly_head.partition(ELISION)[0]
    head_of_tail = mostly_tail.partition(ELISION)[0]
    assert len(head_of_head) > len(head_of_tail)


def test_drop_oldest_handles_empty_input_and_lists_that_already_fit():
    """Edge cases: an empty conversation, and one that needs no trimming."""
    compact, estimate = load("compact"), load("estimate")

    assert compact.drop_oldest([], 100) == []

    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    fits = estimate.estimate_messages(messages)
    assert compact.drop_oldest(messages, fits) == messages


def test_drop_oldest_removes_from_the_front_until_the_list_fits():
    """Contract: messages come off the front, and the result fits the budget."""
    compact, estimate = load("compact"), load("estimate")
    messages = [{"role": "user", "content": f"m{i} " + "filler " * 40} for i in range(20)]
    budget = estimate.estimate_messages(messages) // 4

    kept = compact.drop_oldest(messages, budget)

    assert kept, "at least the last message must survive"
    assert kept == messages[len(messages) - len(kept):], "only leading messages may be dropped"
    assert estimate.estimate_messages(kept) <= budget
    assert len(kept) < len(messages)


@pytest.mark.parametrize("budget", [0, 1, 10])
def test_drop_oldest_always_keeps_the_last_message(budget):
    """Contract: the last message is kept even if it alone exceeds the budget."""
    compact, estimate = load("compact"), load("estimate")
    messages = [{"role": "user", "content": "sentence " * 100} for _ in range(3)]

    kept = compact.drop_oldest(messages, budget)

    assert kept == [messages[-1]]
    assert estimate.estimate_messages(kept) > budget, "the survivor really is over budget"

    single = [{"role": "user", "content": "sentence " * 100}]
    assert compact.drop_oldest(single, budget) == single


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def status_of(main, argv: list[str]) -> int:
    """Run ``main`` and report its status whether it returns or exits."""
    try:
        return main(argv)
    except SystemExit as exc:
        return 2 if exc.code is None else int(exc.code)


def test_cli_count_prints_json_and_exits_zero(capsys):
    """Contract: 'count' prints JSON matching estimate.count_tokens, exit 0."""
    main, estimate = load("cli").main, load("estimate")
    text = "the quick brown fox jumps over the lazy dog"

    assert status_of(main, ["count", "--text", text]) == 0
    printed = json.loads(capsys.readouterr().out)
    value = printed["tokens"] if isinstance(printed, dict) else printed
    assert value == estimate.count_tokens(text)


def test_cli_count_reads_a_file(tmp_path, capsys):
    """Contract: 'count' accepts --file as well as --text."""
    main, estimate = load("cli").main, load("estimate")
    source = tmp_path / "context.txt"
    source.write_text("alpha beta gamma delta epsilon", encoding="utf-8")

    assert status_of(main, ["count", "--file", str(source)]) == 0
    printed = json.loads(capsys.readouterr().out)
    value = printed["tokens"] if isinstance(printed, dict) else printed
    assert value == estimate.count_tokens(source.read_text(encoding="utf-8"))


def test_cli_plan_prints_the_planner_output_as_json(capsys):
    """Contract: 'plan' prints a JSON list, exit 0."""
    main, planner = load("cli").main, load("planner")

    assert status_of(main, ["plan", "--total", "30000", "--agents", "3"]) == 0
    printed = json.loads(capsys.readouterr().out)

    assert isinstance(printed, list)
    assert printed == planner.plan_fanout(30_000, 3)


def test_cli_compact_prints_text_that_fits_the_budget(tmp_path, capsys):
    """Contract: 'compact' prints the compacted text, exit 0."""
    main, estimate = load("cli").main, load("estimate")
    source = tmp_path / "context.txt"
    source.write_text("alpha beta gamma delta " * 300, encoding="utf-8")

    assert status_of(main, ["compact", "--file", str(source), "--budget", "120"]) == 0
    printed = capsys.readouterr().out

    assert ELISION in printed
    assert estimate.count_tokens(printed.rstrip("\n")) <= 120


@pytest.mark.parametrize(
    "argv",
    [
        ["count"],
        ["plan", "--total", "1000", "--agents", "0"],
        ["plan", "--total", "-1", "--agents", "2"],
        ["compact", "--budget", "100"],
        ["nonsense"],
        [],
    ],
)
def test_cli_reports_usage_errors_as_status_two(argv):
    """Contract: exit 0 on success, 2 on a usage error."""
    assert status_of(load("cli").main, argv) == 2
