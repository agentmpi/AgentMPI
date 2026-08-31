"""Unit tests for tokenbudget.planner."""

import pytest

from tokenbudget.planner import plan_fanout


def test_returns_one_slice_per_agent() -> None:
    assert len(plan_fanout(1000, 7)) == 7


def test_slices_stay_within_the_unreserved_pool() -> None:
    assert sum(plan_fanout(1000, 3, reserve_frac=0.1)) <= int(1000 * 0.9)


def test_remainder_goes_to_the_earliest_agents() -> None:
    assert plan_fanout(100, 4, reserve_frac=0.0) == [25, 25, 25, 25]
    assert plan_fanout(102, 4, reserve_frac=0.0) == [26, 26, 25, 25]


def test_slices_are_as_even_as_possible() -> None:
    slices = plan_fanout(1000, 7)
    assert max(slices) - min(slices) <= 1


def test_slices_are_never_negative() -> None:
    assert plan_fanout(0, 3) == [0, 0, 0]
    assert plan_fanout(2, 5, reserve_frac=0.5) == [1, 0, 0, 0, 0]


@pytest.mark.parametrize(
    ("total", "agents", "reserve"),
    [(100, 0, 0.1), (100, -1, 0.1), (-1, 3, 0.1), (100, 3, 1.5), (100, 3, -0.1)],
)
def test_invalid_arguments_raise(total: int, agents: int, reserve: float) -> None:
    with pytest.raises(ValueError):
        plan_fanout(total, agents, reserve)
