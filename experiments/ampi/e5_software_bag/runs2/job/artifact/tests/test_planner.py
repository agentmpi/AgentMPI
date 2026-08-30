"""Tests for tokenbudget.planner."""

from __future__ import annotations

import pytest

from tokenbudget.planner import plan_fanout


def test_returns_exactly_n_agents_shares() -> None:
    for n_agents in (1, 2, 3, 7, 64):
        assert len(plan_fanout(1000, n_agents)) == n_agents


def test_even_split_when_divisible() -> None:
    assert plan_fanout(1000, 3, reserve_frac=0.1) == [300, 300, 300]


def test_remainder_goes_to_earliest_agents() -> None:
    shares = plan_fanout(100, 3, reserve_frac=0.0)
    assert shares == [34, 33, 33]
    assert sum(shares) == 100


def test_shares_are_non_increasing() -> None:
    shares = plan_fanout(1001, 7, reserve_frac=0.0)
    assert shares == sorted(shares, reverse=True)
    assert max(shares) - min(shares) <= 1


def test_sum_never_exceeds_budget_after_reserve() -> None:
    for total in (0, 1, 3, 7, 10, 99, 100, 12345):
        for n_agents in (1, 2, 5, 13):
            for reserve_frac in (0.0, 0.1, 0.25, 0.5, 0.99, 1.0):
                shares = plan_fanout(total, n_agents, reserve_frac)
                assert sum(shares) <= total * (1.0 - reserve_frac)
                assert sum(shares) <= total


def test_shares_are_never_negative() -> None:
    for total in (0, 1, 2, 5):
        shares = plan_fanout(total, 8, reserve_frac=0.9)
        assert all(share >= 0 for share in shares)


def test_zero_budget_gives_all_zeros() -> None:
    assert plan_fanout(0, 4) == [0, 0, 0, 0]


def test_full_reserve_leaves_nothing_to_spend() -> None:
    assert plan_fanout(500, 5, reserve_frac=1.0) == [0, 0, 0, 0, 0]


def test_budget_smaller_than_agent_count() -> None:
    shares = plan_fanout(2, 5, reserve_frac=0.0)
    assert shares == [1, 1, 0, 0, 0]


def test_rejects_non_positive_agent_count() -> None:
    with pytest.raises(ValueError):
        plan_fanout(100, 0)
    with pytest.raises(ValueError):
        plan_fanout(100, -3)


def test_rejects_negative_budget() -> None:
    with pytest.raises(ValueError):
        plan_fanout(-1, 4)


def test_rejects_out_of_range_reserve_frac() -> None:
    with pytest.raises(ValueError):
        plan_fanout(100, 4, reserve_frac=-0.1)
    with pytest.raises(ValueError):
        plan_fanout(100, 4, reserve_frac=1.5)


def test_is_deterministic() -> None:
    assert plan_fanout(777, 6, 0.15) == plan_fanout(777, 6, 0.15)
