"""Plan and enforce token budgets across a set of cooperating LLM agents.

The package is deliberately small and dependency-free:

* :mod:`tokenbudget.estimate` counts tokens in text and in message lists.
* :mod:`tokenbudget.policy` models a budget and decides what it admits.
* :mod:`tokenbudget.compact` shrinks text and message history to fit a budget.
* :mod:`tokenbudget.ledger` tracks per-agent token spend.
* :mod:`tokenbudget.planner` splits a total budget across agents.
* :mod:`tokenbudget.cli` exposes the above as a command line tool.
"""

from .estimate import count_tokens, estimate_messages
from .policy import Budget, BudgetExceeded
from .compact import head_tail, drop_oldest
from .ledger import Ledger
from .planner import plan_fanout
from .cli import main

__all__ = [
    "Budget",
    "BudgetExceeded",
    "Ledger",
    "count_tokens",
    "drop_oldest",
    "estimate_messages",
    "head_tail",
    "main",
    "plan_fanout",
]
