"""Trace analysis and visualisation for AgentMPI runs.

An agent run is expensive and it is not reproducible.  Nobody will repeat a
sixteen-hour, sixty-four-rank job to settle an argument about what happened in
it, so the trace is not a debugging convenience --- it is the whole of the
evidence, and a claim the trace does not support is a claim the paper cannot
make.  That is why tracing in AgentMPI is unconditional and why this package
takes the event log as its only input.

Four modules, layered so each can be used without the ones above it:

``style``    the shared taxonomy: colour encodes role, glyph encodes duration.
``model``    the event log to measurements, including straggler attribution.
``figures``  six matplotlib figures, each answering a distinct question.
``report``   terminal, markdown and LaTeX renderings of one analysis.

Writing this package changed the runtime.  Two things a reader needs turned out
not to be in the log at all: a broadcast emitted no event, so it was invisible,
and no collective recorded how long its caller had been blocked, so coordination
cost could not be separated from work.  Both are now recorded.  An analysis tool
that cannot be built is a specification of what the runtime forgot to say.
"""

from __future__ import annotations

from .model import (
    Analysis,
    CollectiveInvocation,
    ConcurrencyProfile,
    RankProfile,
    analyse,
    load_events,
)
from .report import findings, latex, markdown, summary, write_all

__all__ = [
    "Analysis",
    "CollectiveInvocation",
    "ConcurrencyProfile",
    "RankProfile",
    "analyse",
    "load_events",
    "findings",
    "latex",
    "markdown",
    "summary",
    "write_all",
]
