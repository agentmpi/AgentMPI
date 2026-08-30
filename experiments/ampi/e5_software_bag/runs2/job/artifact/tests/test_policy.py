"""Tests for tokenbudget.policy."""

import pytest

from tokenbudget.policy import Budget, BudgetExceeded


def test_remaining_subtracts_reserve_and_usage():
    budget = Budget(1000, reserved=200)
    assert budget.remaining(0) == 800
    assert budget.remaining(300) == 500
    assert budget.remaining(800) == 0


def test_remaining_clamps_to_zero_when_overspent():
    budget = Budget(100, reserved=10)
    assert budget.remaining(90) == 0
    assert budget.remaining(10_000) == 0


def test_reserved_defaults_to_zero():
    budget = Budget(50)
    assert budget.reserved == 0
    assert budget.remaining(20) == 30


def test_admits_accepts_exactly_the_remaining_tokens():
    budget = Budget(100, reserved=25)
    assert budget.admits(0, 75) is True
    assert budget.admits(0, 76) is False


def test_admits_agrees_with_remaining():
    budget = Budget(64, reserved=8)
    for used in range(0, 80, 7):
        room = budget.remaining(used)
        assert budget.admits(used, room) is True
        assert budget.admits(used, room + 1) is False


def test_admits_zero_tokens_even_when_exhausted():
    budget = Budget(10, reserved=10)
    assert budget.remaining(0) == 0
    assert budget.admits(0, 0) is True
    assert budget.admits(0, 1) is False


def test_negative_limit_rejected():
    with pytest.raises(ValueError):
        Budget(-1)


def test_negative_reserved_rejected():
    with pytest.raises(ValueError):
        Budget(100, reserved=-5)


def test_reserved_above_limit_rejected():
    with pytest.raises(ValueError):
        Budget(100, reserved=101)


def test_reserved_equal_to_limit_is_allowed():
    budget = Budget(100, reserved=100)
    assert budget.remaining(0) == 0


def test_zero_limit_is_allowed():
    budget = Budget(0)
    assert budget.remaining(0) == 0
    assert budget.admits(0, 0) is True


def test_budget_exceeded_is_an_exception():
    assert issubclass(BudgetExceeded, Exception)
    with pytest.raises(BudgetExceeded):
        raise BudgetExceeded("no room left")
