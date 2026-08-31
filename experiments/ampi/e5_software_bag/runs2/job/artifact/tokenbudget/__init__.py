"""A library for planning and enforcing token budgets across a set of cooperating LLM agents."""

from tokenbudget.cli import main
from tokenbudget.compact import drop_oldest, head_tail
from tokenbudget.estimate import count_tokens, estimate_messages
from tokenbudget.ledger import Ledger
from tokenbudget.planner import plan_fanout
from tokenbudget.policy import Budget, BudgetExceeded

__all__ = [
    "Budget",
    "BudgetExceeded",
    "count_tokens",
    "drop_oldest",
    "estimate_messages",
    "head_tail",
    "Ledger",
    "main",
    "plan_fanout",
]
