"""Tests for :mod:`tokenbudget.policy`.

``remaining()`` is ``max(0, limit - reserved - used)`` and ``admits()`` is true
exactly when ``incoming <= remaining(used)``, so these tests pin down the zero
clamp, the inclusive admission boundary, and the three constructor rejections
the contract names.
"""

from __future__ import annotations

import pytest

from tokenbudget.policy import Budget, BudgetExceeded


def test_remaining_is_limit_minus_reserved_minus_used():
    budget = Budget(1_000, reserved=250)
    assert budget.remaining(0) == 750
    assert budget.remaining(100) == 650
    assert budget.remaining(750) == 0


def test_remaining_clamps_at_zero_on_overspend():
    budget = Budget(100, reserved=10)
    assert budget.remaining(90) == 0
    assert budget.remaining(91) == 0
    assert budget.remaining(10_000) == 0


def test_admits_is_inclusive_at_the_boundary():
    budget = Budget(100, reserved=10)
    assert budget.admits(0, 90) is True
    assert budget.admits(0, 91) is False
    assert budget.admits(40, 50) is True
    assert budget.admits(40, 51) is False


def test_admits_zero_is_always_true_even_after_overspending():
    budget = Budget(100, reserved=10)
    assert budget.admits(0, 0) is True
    assert budget.admits(90, 0) is True
    assert budget.admits(10_000, 0) is True
    assert budget.admits(10_000, 1) is False


def test_a_zero_limit_budget_admits_only_nothing():
    budget = Budget(0)
    assert budget.remaining(0) == 0
    assert budget.admits(0, 0) is True
    assert budget.admits(0, 1) is False


def test_reserving_the_whole_limit_leaves_nothing_spendable():
    budget = Budget(500, reserved=500)
    assert budget.remaining(0) == 0
    assert budget.admits(0, 1) is False
    assert budget.admits(0, 0) is True


def test_default_reserved_is_zero():
    budget = Budget(200)
    assert budget.reserved == 0
    assert budget.remaining(0) == 200
    assert budget.admits(0, 200) is True


def test_rejects_a_negative_limit():
    with pytest.raises(ValueError):
        Budget(-1)
    with pytest.raises(ValueError):
        Budget(-1_000, reserved=0)


def test_rejects_negative_reserved():
    with pytest.raises(ValueError):
        Budget(100, reserved=-1)


def test_rejects_reserving_more_than_the_limit():
    with pytest.raises(ValueError):
        Budget(100, reserved=101)
    with pytest.raises(ValueError):
        Budget(0, reserved=1)


def test_budget_exceeded_is_an_exception_subclass():
    assert issubclass(BudgetExceeded, Exception)
    with pytest.raises(BudgetExceeded):
        raise BudgetExceeded("over budget")


def test_admits_returns_a_real_bool_not_a_truthy_value():
    budget = Budget(10)
    assert isinstance(budget.admits(0, 5), bool)
    assert isinstance(budget.admits(0, 50), bool)


@pytest.mark.parametrize("limit", [0, 1, 17, 1_000])
@pytest.mark.parametrize("reserved_frac", [0.0, 0.5, 1.0])
@pytest.mark.parametrize("used", [0, 1, 5, 1_000, 10_000])
def test_admits_agrees_with_remaining_across_the_parameter_space(limit, reserved_frac, used):
    reserved = int(limit * reserved_frac)
    budget = Budget(limit, reserved=reserved)
    left = budget.remaining(used)

    assert left >= 0
    assert budget.admits(used, left) is True
    assert budget.admits(used, left + 1) is False
