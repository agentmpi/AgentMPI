"""Fan-out planning: divide a token budget among cooperating agents.

A caller with one overall token budget and a set of agents to run needs to
decide, up front, how many tokens each agent may spend.  :func:`plan_fanout`
answers that question: it holds back a fraction of the budget as a reserve for
the caller itself and splits what is left as evenly as it can, handing any
indivisible remainder to the earliest agents.

The reserve is what makes the plan safe to enforce: the shares always sum to
*at most* the spendable pool, so a :class:`tokenbudget.policy.Budget` built
with ``reserved = total_budget - sum(shares)`` admits every share in turn and
is exactly exhausted when all of them have been spent.
"""

from __future__ import annotations

import math

__all__ = ["plan_fanout"]


def plan_fanout(total_budget: int, n_agents: int, reserve_frac: float = 0.1) -> list[int]:
    """Split ``total_budget`` into one share per agent.

    Args:
        total_budget: The whole budget, in tokens.  Must not be negative.
        n_agents: How many agents to plan for.  Must be at least 1.
        reserve_frac: The fraction of ``total_budget`` held back from the
            agents, between ``0.0`` (hand out everything) and ``1.0`` (hand out
            nothing).

    Returns:
        Exactly ``n_agents`` non-negative integers, summing to at most
        ``total_budget * (1 - reserve_frac)``, as even as an integer split
        allows.  The remainder of the division goes to the earliest agents, so
        the result is non-increasing and no two shares differ by more than one.

    Raises:
        ValueError: If ``n_agents`` is less than 1, if ``total_budget`` is
            negative, or if ``reserve_frac`` lies outside ``[0.0, 1.0]``
            (including NaN), which could otherwise only be answered with
            negative shares.
    """
    if n_agents < 1:
        raise ValueError(f"n_agents must be at least 1, got {n_agents}")
    if total_budget < 0:
        raise ValueError(f"total_budget must not be negative, got {total_budget}")
    if math.isnan(reserve_frac) or not 0.0 <= reserve_frac <= 1.0:
        raise ValueError(f"reserve_frac must be between 0.0 and 1.0, got {reserve_frac}")

    # Floor rather than round, so the shares can never sum to more than the
    # spendable pool even when the product lands just above an integer.
    spendable = math.floor(total_budget * (1.0 - reserve_frac))
    spendable = max(0, min(spendable, total_budget))

    share, remainder = divmod(spendable, n_agents)
    return [share + 1 if i < remainder else share for i in range(n_agents)]
