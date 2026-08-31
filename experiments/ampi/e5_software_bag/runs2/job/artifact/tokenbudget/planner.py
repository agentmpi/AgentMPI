"""Fan-out planning: split a total token budget across cooperating agents.

The planner decides how many tokens each agent in a fan-out may spend. A
fraction of the total is held back as a reserve so that the coordinator keeps
headroom for retries and final summarisation.
"""

from __future__ import annotations

__all__ = ["plan_fanout"]


def plan_fanout(
    total_budget: int, n_agents: int, reserve_frac: float = 0.1
) -> list[int]:
    """Split ``total_budget`` across ``n_agents`` as evenly as possible.

    The returned list always has exactly ``n_agents`` non-negative integers and
    sums to at most ``total_budget * (1 - reserve_frac)``. When the spendable
    amount does not divide evenly, the remainder goes to the earliest agents,
    so the shares are non-increasing.

    Raises:
        ValueError: if ``n_agents`` is less than 1, if ``total_budget`` is
            negative, or if ``reserve_frac`` lies outside ``[0.0, 1.0]``.
    """
    if n_agents < 1:
        raise ValueError(f"n_agents must be at least 1, got {n_agents}")
    if total_budget < 0:
        raise ValueError(f"total_budget must not be negative, got {total_budget}")
    if not 0.0 <= reserve_frac <= 1.0:
        raise ValueError(f"reserve_frac must be within [0.0, 1.0], got {reserve_frac}")

    spendable = int(total_budget * (1.0 - reserve_frac))
    # Guard against floating point drift pushing the product just past the
    # exact bound in either direction.
    if spendable > total_budget:
        spendable = total_budget
    if spendable < 0:
        spendable = 0

    base, remainder = divmod(spendable, n_agents)
    return [base + 1 if i < remainder else base for i in range(n_agents)]
