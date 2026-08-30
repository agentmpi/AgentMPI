"""Tests for :mod:`tokenbudget.ledger`."""

from __future__ import annotations

import pytest

from tokenbudget.ledger import Ledger


def test_charge_returns_new_balance() -> None:
    ledger = Ledger()
    assert ledger.charge("a", 100) == 100
    assert ledger.charge("a", 50) == 150


def test_release_returns_new_balance() -> None:
    ledger = Ledger()
    ledger.charge("a", 100)
    assert ledger.release("a", 40) == 60
    assert ledger.release("a", 10) == 50


def test_balance_never_goes_below_zero() -> None:
    ledger = Ledger()
    ledger.charge("a", 10)
    assert ledger.release("a", 999) == 0
    assert ledger.usage()["a"] == 0
    assert ledger.total() == 0


def test_release_for_unknown_agent_stays_at_zero() -> None:
    ledger = Ledger()
    assert ledger.release("ghost", 25) == 0
    assert ledger.total() == 0


def test_usage_returns_a_copy() -> None:
    ledger = Ledger()
    ledger.charge("a", 7)
    snapshot = ledger.usage()
    snapshot["a"] = 10_000
    snapshot["b"] = 1
    assert ledger.usage() == {"a": 7}
    assert ledger.usage() is not ledger.usage()


def test_charge_with_negative_tokens_raises_value_error() -> None:
    ledger = Ledger()
    ledger.charge("a", 5)
    with pytest.raises(ValueError):
        ledger.charge("a", -1)
    assert ledger.usage() == {"a": 5}


def test_release_with_negative_tokens_raises_value_error() -> None:
    ledger = Ledger()
    ledger.charge("a", 5)
    with pytest.raises(ValueError):
        ledger.release("a", -1)
    assert ledger.usage() == {"a": 5}


def test_charging_zero_is_allowed_and_registers_the_agent() -> None:
    ledger = Ledger()
    assert ledger.charge("a", 0) == 0
    assert ledger.usage() == {"a": 0}


def test_total_sums_every_agent() -> None:
    ledger = Ledger()
    ledger.charge("a", 10)
    ledger.charge("b", 32)
    ledger.charge("c", 0)
    assert ledger.total() == 42
    assert ledger.usage() == {"a": 10, "b": 32, "c": 0}


def test_agents_are_tracked_independently() -> None:
    ledger = Ledger()
    ledger.charge("a", 100)
    ledger.charge("b", 100)
    ledger.release("a", 100)
    assert ledger.balance("a") == 0
    assert ledger.balance("b") == 100
    assert ledger.total() == 100


def test_non_integer_tokens_raise_type_error() -> None:
    ledger = Ledger()
    with pytest.raises(TypeError):
        ledger.charge("a", 1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ledger.release("a", "10")  # type: ignore[arg-type]
