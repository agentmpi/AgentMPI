"""Split a total token budget across a fan-out of agents.

The planner is deliberately pessimistic: it holds back ``reserve_frac`` of
the total for the caller's own overhead before dividing what is left, and it
rounds the usable pool down, so the slices it hands out can be spent in full
without breaching the total.
"""


def plan_fanout(total_budget: int, n_agents: int, reserve_frac: float = 0.1) -> list[int]:
    """Return ``n_agents`` slices of ``total_budget`` minus a reserve.

    The slices are non-negative, as even as possible, and sum to at most
    ``total_budget * (1 - reserve_frac)``. Any remainder from the division
    goes to the earliest agents.
    """
    if n_agents < 1:
        raise ValueError(f"n_agents must be at least 1, got {n_agents}")
    if total_budget < 0:
        raise ValueError(f"total_budget must not be negative, got {total_budget}")
    if not 0.0 <= reserve_frac <= 1.0:
        raise ValueError(f"reserve_frac must be between 0 and 1, got {reserve_frac}")

    usable = int(total_budget * (1.0 - reserve_frac))
    base, remainder = divmod(usable, n_agents)
    return [base + 1 if i < remainder else base for i in range(n_agents)]
