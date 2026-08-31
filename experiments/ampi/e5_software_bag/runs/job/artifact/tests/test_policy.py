"""Unit tests for tokenbudget.policy."""

import pytest

from tokenbudget.policy import Budget, BudgetExceeded


def test_remaining_subtracts_reserve_and_usage() -> None:
    assert Budget(limit=100, reserved=20).remaining(30) == 50


def test_remaining_never_goes_negative() -> None:
    assert Budget(limit=100, reserved=20).remaining(500) == 0


def test_admits_exactly_what_remains() -> None:
    budget = Budget(limit=100, reserved=20)
    assert budget.admits(30, 50)
    assert not budget.admits(30, 51)


def test_zero_is_always_admitted() -> None:
    assert Budget(limit=10).admits(1000, 0)


@pytest.mark.parametrize(
    ("limit", "reserved"),
    [(-1, 0), (10, -1), (10, 11)],
)
def test_invalid_construction_raises(limit: int, reserved: int) -> None:
    with pytest.raises(ValueError):
        Budget(limit=limit, reserved=reserved)


def test_budget_exceeded_is_an_exception() -> None:
    assert issubclass(BudgetExceeded, Exception)
