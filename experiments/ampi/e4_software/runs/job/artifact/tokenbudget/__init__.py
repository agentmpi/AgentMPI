"""Plan and enforce token budgets across a set of cooperating LLM agents.

The public surface of the package is the union of the names exported by the
``estimate``, ``policy``, ``compact``, ``ledger``, ``planner`` and ``cli``
modules, as fixed by the project contract.

Attribute access is resolved lazily (PEP 562) so that importing the package
does not require every submodule to be present: modules are written
independently and any one of them may still be missing while the others are
usable.
"""

from __future__ import annotations

from typing import Any

__version__ = "0.1.0"

_EXPORTS: dict[str, str] = {
    "count_tokens": "estimate",
    "estimate_messages": "estimate",
    "Budget": "policy",
    "BudgetExceeded": "policy",
    "head_tail": "compact",
    "drop_oldest": "compact",
    "Ledger": "ledger",
    "plan_fanout": "planner",
    "main": "cli",
}

__all__ = sorted(_EXPORTS) + ["__version__"]


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = __import__(f"{__name__}.{module_name}", fromlist=[name])
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
