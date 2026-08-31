"""Unit tests for tokenbudget.ledger."""

import pytest

from tokenbudget.ledger import Ledger


def test_charge_returns_the_new_balance() -> None:
    book = Ledger()
    assert book.charge("a", 10) == 10
    assert book.charge("a", 5) == 15


def test_release_returns_the_new_balance() -> None:
    book = Ledger()
    book.charge("a", 10)
    assert book.release("a", 4) == 6


def test_balances_are_clamped_at_zero() -> None:
    book = Ledger()
    book.charge("a", 10)
    assert book.release("a", 999) == 0
    assert book.total() == 0


def test_usage_returns_a_copy() -> None:
    book = Ledger()
    book.charge("a", 10)
    snapshot = book.usage()
    snapshot["a"] = 10_000
    assert book.usage()["a"] == 10


def test_total_sums_every_agent() -> None:
    book = Ledger()
    book.charge("a", 10)
    book.charge("b", 32)
    assert book.total() == 42


@pytest.mark.parametrize("method", ["charge", "release"])
def test_negative_amounts_raise(method: str) -> None:
    book = Ledger()
    with pytest.raises(ValueError):
        getattr(book, method)("a", -1)
