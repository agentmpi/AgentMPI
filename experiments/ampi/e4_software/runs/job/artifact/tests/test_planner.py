"""Tests for :mod:`tokenbudget.planner`.

The contract for :func:`tokenbudget.planner.plan_fanout` is that it returns
exactly ``n_agents`` non-negative integers summing to at most
``total_budget * (1 - reserve_frac)``, as evenly as possible, with the
remainder going to the earliest agents.  These tests cover that split, the
boundaries of the reserve, and the documented ``ValueError`` cases.
"""

from __future__ import annotations

import math

import pytest

from tokenbudget.planner import plan_fanout


def spendable(total_budget: int, reserve_frac: float) -> int:
    """The pool the plan is allowed to hand out, computed independently."""
    return math.floor(total_budget * (1.0 - reserve_frac))


def test_even_split_when_the_pool_divides_exactly():
    assert plan_fanout(1_000, 4, reserve_frac=0.0) == [250, 250, 250, 250]
    assert plan_fanout(100_000, 6, reserve_frac=0.1) == [15_000] * 6


def test_remainder_goes_to_the_earliest_agents():
    # 100 spendable over 3 agents is 33 each with 1 left over.
    assert plan_fanout(100, 3, reserve_frac=0.0) == [34, 33, 33]
    # 10 over 4 is 2 each with 2 left over.
    assert plan_fanout(10, 4, reserve_frac=0.0) == [3, 3, 2, 2]


def test_default_reserve_frac_holds_back_a_tenth():
    shares = plan_fanout(30_000, 3)
    assert shares == [9_000, 9_000, 9_000]
    assert sum(shares) == 27_000


def test_zero_total_budget_gives_every_agent_zero():
    assert plan_fanout(0, 5) == [0, 0, 0, 0, 0]
    assert plan_fanout(0, 1, reserve_frac=0.0) == [0]


def test_reserve_of_the_whole_budget_gives_every_agent_zero():
    assert plan_fanout(10_000, 4, reserve_frac=1.0) == [0, 0, 0, 0]


def test_zero_reserve_hands_out_the_entire_budget():
    shares = plan_fanout(9_999, 7, reserve_frac=0.0)
    assert sum(shares) == 9_999


def test_single_agent_receives_the_whole_spendable_pool():
    assert plan_fanout(1_000, 1, reserve_frac=0.25) == [750]
    assert plan_fanout(7, 1, reserve_frac=0.0) == [7]


def test_more_agents_than_tokens_starves_the_latest_agents():
    # Three tokens cannot be split five ways; the earliest agents take them.
    assert plan_fanout(3, 5, reserve_frac=0.0) == [1, 1, 1, 0, 0]


def test_rejects_fewer_than_one_agent():
    for n_agents in (0, -1, -100):
        with pytest.raises(ValueError):
            plan_fanout(1_000, n_agents)


def test_rejects_a_negative_total_budget():
    with pytest.raises(ValueError):
        plan_fanout(-1, 4)
    with pytest.raises(ValueError):
        plan_fanout(-10_000, 1, reserve_frac=0.0)


def test_rejects_a_reserve_fraction_outside_the_unit_interval():
    for reserve_frac in (-0.5, -0.0001, 1.0001, 2.0, float("nan")):
        with pytest.raises(ValueError):
            plan_fanout(1_000, 4, reserve_frac=reserve_frac)


@pytest.mark.parametrize("total_budget", [0, 1, 7, 99, 1_000, 65_537, 1_000_000])
@pytest.mark.parametrize("n_agents", [1, 2, 3, 5, 8, 64])
@pytest.mark.parametrize("reserve_frac", [0.0, 0.05, 0.1, 0.2, 0.25, 0.5, 0.9, 1.0])
def test_invariants_hold_across_the_parameter_space(total_budget, n_agents, reserve_frac):
    shares = plan_fanout(total_budget, n_agents, reserve_frac=reserve_frac)

    assert len(shares) == n_agents
    assert all(isinstance(share, int) and share >= 0 for share in shares)
    assert sum(shares) <= total_budget * (1.0 - reserve_frac)
    assert sum(shares) == spendable(total_budget, reserve_frac)
    # "As evenly as possible", with the remainder to the front.
    assert max(shares) - min(shares) <= 1
    assert shares == sorted(shares, reverse=True)


def test_is_deterministic():
    first = plan_fanout(123_457, 9, reserve_frac=0.15)
    for _ in range(5):
        assert plan_fanout(123_457, 9, reserve_frac=0.15) == first


def test_plan_is_enforceable_as_a_reserve_plus_shares():
    """The shares plus the reserve must never exceed the original budget."""
    total_budget = 50_000
    shares = plan_fanout(total_budget, 7, reserve_frac=0.1)
    reserve = total_budget - sum(shares)

    assert reserve >= 0
    assert sum(shares) + reserve == total_budget
    # The reserve really is at least the requested fraction.
    assert reserve >= total_budget * 0.1
