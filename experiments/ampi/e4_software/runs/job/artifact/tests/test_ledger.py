"""Tests for :mod:`tokenbudget.ledger`.

The contract is that ``charge`` and ``release`` return the agent's new balance,
that balances never go below zero, that ``usage()`` hands back a copy, and that
a negative charge is a ``ValueError``.  These tests cover each of those plus
the empty ledger and the over-release edge cases.
"""

from __future__ import annotations

import pytest

from tokenbudget.ledger import Ledger


def test_a_fresh_ledger_is_empty():
    book = Ledger()
    assert book.usage() == {}
    assert book.total() == 0


def test_charge_returns_the_new_running_balance():
    book = Ledger()
    assert book.charge("writer", 100) == 100
    assert book.charge("writer", 50) == 150
    assert book.charge("reader", 20) == 20
    assert book.usage() == {"writer": 150, "reader": 20}


def test_release_returns_the_new_balance():
    book = Ledger()
    book.charge("writer", 150)
    assert book.release("writer", 50) == 100
    assert book.release("writer", 100) == 0
    assert book.usage()["writer"] == 0


def test_balances_never_go_below_zero():
    book = Ledger()
    book.charge("writer", 30)
    assert book.release("writer", 1_000) == 0
    assert book.total() == 0
    assert all(balance >= 0 for balance in book.usage().values())


def test_releasing_an_agent_that_was_never_charged_is_zero_and_adds_nothing():
    book = Ledger()
    book.charge("known", 5)
    assert book.release("never-seen", 500) == 0
    assert book.usage() == {"known": 5}
    assert book.total() == 5


def test_charging_zero_is_a_no_op_that_still_reports_the_balance():
    book = Ledger()
    book.charge("writer", 12)
    assert book.charge("writer", 0) == 12
    assert book.total() == 12
    assert Ledger().charge("fresh", 0) == 0


def test_releasing_zero_leaves_the_balance_alone():
    book = Ledger()
    book.charge("writer", 12)
    assert book.release("writer", 0) == 12
    assert book.total() == 12


def test_charge_with_negative_tokens_raises_and_leaves_the_books_untouched():
    book = Ledger()
    book.charge("writer", 10)

    with pytest.raises(ValueError):
        book.charge("writer", -1)
    with pytest.raises(ValueError):
        book.charge("fresh", -100)

    assert book.usage() == {"writer": 10}, "a rejected charge must not create an entry"
    assert book.total() == 10


def test_release_with_negative_tokens_raises_and_leaves_the_books_untouched():
    book = Ledger()
    book.charge("writer", 10)

    with pytest.raises(ValueError):
        book.release("writer", -1)

    assert book.total() == 10


def test_usage_returns_a_fresh_copy_each_call():
    book = Ledger()
    book.charge("a", 10)

    first = book.usage()
    assert first is not book.usage()

    first["a"] = 10**9
    first["injected"] = 7
    assert book.usage() == {"a": 10}
    assert book.total() == 10


def test_total_is_the_sum_of_every_balance():
    book = Ledger()
    for i in range(6):
        book.charge(f"agent-{i}", i * 100)

    assert book.total() == sum(i * 100 for i in range(6))
    assert book.total() == sum(book.usage().values())

    book.release("agent-5", 500)
    assert book.total() == sum(book.usage().values())


def test_charges_and_releases_interleave_consistently():
    book = Ledger()
    expected: dict[str, int] = {}
    script = [
        ("charge", "a", 30),
        ("charge", "b", 70),
        ("charge", "a", 5),
        ("release", "b", 1_000),
        ("charge", "a", 0),
        ("release", "a", 12),
        ("charge", "c", 8),
    ]

    for op, agent, delta in script:
        if op == "charge":
            balance = book.charge(agent, delta)
            expected[agent] = expected.get(agent, 0) + delta
        else:
            balance = book.release(agent, delta)
            expected[agent] = max(0, expected[agent] - delta)
        assert balance == expected[agent]

    assert book.usage() == expected
    assert book.total() == sum(expected.values())
