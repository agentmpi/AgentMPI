"""The visual vocabulary for AgentMPI traces: one taxonomy, several renderers.

Figures, the terminal report and the HTML viewer all draw the same events, and a
reader moves between them constantly --- finding a shape in the timeline and then
citing it in prose.  If the three disagree about what red means, the reader is
actively misled, which is worse than any of them being ugly.  So the taxonomy
lives here and everything that draws a trace imports it.

Two axes, kept deliberately independent:

**Colour encodes role** --- what kind of thing happened.  Trouble is red
everywhere, so a failure is findable at a glance in a lane otherwise dense with
collective ticks.

**Glyph encodes duration** --- a bar has extent in time; a tick and a diamond are
instants.  Separating this from colour is what lets a reader answer "how long did
that take" and "what was it" without holding a combined legend in their head.

An unrecognised kind is styled rather than dropped.  A trace that silently omits
an event it did not recognise is indistinguishable from one where the event did
not happen, and the second is a much stronger claim than the tool is entitled to
make.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ROLE_COLOR",
    "ROLE_LABEL",
    "ROLE_ORDER",
    "STYLE",
    "COLLECTIVE_KINDS",
    "Style",
    "style_for",
    "role_of",
    "is_collective",
]

BACKGROUND = "#0b1020"
PANEL = "#131a2e"
PANEL_ALT = "#1a2338"
LINE = "#263352"
FOREGROUND = "#e8edf7"
MUTED = "#94a3b8"

ROLE_COLOR: dict[str, str] = {
    "work": "#3b82f6",
    "collective": "#10b981",
    "message": "#22d3ee",
    "rma": "#f59e0b",
    "consensus": "#e879f9",
    "lifecycle": "#64748b",
    "trouble": "#ef4444",
    "recovery": "#a78bfa",
}

ROLE_LABEL: dict[str, str] = {
    "work": "agent working",
    "collective": "collective",
    "message": "point-to-point",
    "rma": "window operation",
    "consensus": "reduction / arbitration",
    "lifecycle": "rank lifecycle",
    "trouble": "failure / rejection",
    "recovery": "fault recovery",
}

#: Legend and stacked-summary order: what a rank was doing, then how it
#: coordinated, then what went wrong, then what the harness did about it.
ROLE_ORDER: tuple[str, ...] = (
    "work", "collective", "message", "rma", "consensus",
    "lifecycle", "trouble", "recovery",
)

#: Event kinds that are collective completions.  These are the kinds
#: :func:`ampi.analysis.model.analyse` groups into invocations, and the set is
#: named here rather than inferred from a prefix because AgentMPI's collectives
#: are traced under their own names --- ``bcast``, not ``coll.bcast`` --- so a
#: prefix test would silently pick up ``coll.join`` and ``coll.dropped``, which
#: are participation records rather than completions.
COLLECTIVE_KINDS: frozenset[str] = frozenset({
    "barrier", "bcast", "scatter", "gather", "allgather", "alltoall",
    "reduce", "allreduce", "scan", "exscan", "neighbor_allgather",
})


@dataclass(frozen=True)
class Style:
    color: str
    glyph: str
    role: str


def _s(role: str, glyph: str = "tick", color: str | None = None) -> Style:
    return Style(color or ROLE_COLOR[role], glyph, role)


STYLE: dict[str, Style] = {
    # -- work -----------------------------------------------------------
    "broker.claim": _s("work", "bar"),
    "broker.submit": _s("work", "tick", "#60a5fa"),
    "broker.publish": _s("lifecycle", "tick"),
    # A model executor's task is claim and submit in one process.
    "task.start": _s("work", "bar"),
    "task.done": _s("work", "tick", "#60a5fa"),
    "task.tool": _s("work", "tick", "#93c5fd"),
    # -- collectives ----------------------------------------------------
    "coll.join": _s("collective", "tick", "#065f46"),
    "barrier": _s("collective", "diamond"),
    "bcast": _s("collective", "tick", "#34d399"),
    "scatter": _s("collective", "tick", "#34d399"),
    "gather": _s("collective", "tick", "#6ee7b7"),
    "allgather": _s("collective", "tick", "#6ee7b7"),
    "alltoall": _s("collective", "tick", "#6ee7b7"),
    "neighbor_allgather": _s("collective", "tick", "#5eead4"),
    # -- consensus ------------------------------------------------------
    "reduce": _s("consensus", "diamond"),
    "allreduce": _s("consensus", "diamond"),
    "scan": _s("consensus", "diamond", "#f0abfc"),
    "exscan": _s("consensus", "diamond", "#f0abfc"),
    "op.plan": _s("consensus", "tick", "#c026d3"),
    "op.directive": _s("consensus", "tick", "#c026d3"),
    "op.commit": _s("consensus", "tick", "#d946ef"),
    "op.complete": _s("consensus", "diamond", "#d946ef"),
    "op.arbitrate": _s("consensus", "diamond", "#f5d0fe"),
    # -- point-to-point --------------------------------------------------
    "send": _s("message"),
    "recv": _s("message", "tick", "#67e8f9"),
    # -- one-sided -------------------------------------------------------
    "win.create": _s("rma", "diamond", "#fbbf24"),
    "win.put": _s("rma", "diamond"),
    "win.accumulate": _s("rma", "diamond", "#a78bfa"),
    "win.cas": _s("rma", "diamond", "#fcd34d"),
    "win.fence": _s("rma", "tick", "#fcd34d"),
    "win.lock": _s("rma", "diamond", "#fb923c"),
    "win.unlock": _s("rma", "diamond", "#fdba74"),
    # -- lifecycle -------------------------------------------------------
    "job.create": _s("lifecycle", "diamond"),
    "launch.node": _s("lifecycle", "diamond", "#94a3b8"),
    "launch.exit": _s("lifecycle", "diamond", "#94a3b8"),
    "init": _s("lifecycle"),
    "init.heartbeat": _s("lifecycle"),
    "finalize": _s("lifecycle"),
    "memo": _s("lifecycle", "tick", "#475569"),
    "fence": _s("lifecycle", "diamond"),
    "comm.create": _s("lifecycle", "diamond", "#94a3b8"),
    "comm.dup": _s("lifecycle", "diamond", "#94a3b8"),
    "comm.split": _s("lifecycle", "diamond", "#94a3b8"),
    "cart.create": _s("lifecycle", "diamond", "#94a3b8"),
    "graph.create": _s("lifecycle", "diamond", "#94a3b8"),
    "iface.publish": _s("lifecycle", "diamond", "#cbd5e1"),
    "iface.verify": _s("lifecycle", "diamond", "#cbd5e1"),
    # -- trouble ---------------------------------------------------------
    "rank.error": _s("trouble", "diamond"),
    "coll.dropped": _s("trouble", "diamond"),
    "barrier.proceed": _s("trouble", "diamond", "#f87171"),
    "op.invariant": _s("trouble", "diamond"),
    "failure.suspect": _s("trouble", "diamond", "#fca5a5"),
    "failure.convict": _s("trouble", "diamond"),
    "failure.kill": _s("trouble", "diamond"),
    "broker.reject": _s("trouble", "diamond", "#f87171"),
    "task.retry": _s("trouble", "diamond", "#f87171"),
    "task.fallback": _s("recovery", "diamond", "#c4b5fd"),
    "task.fail": _s("trouble", "diamond"),
    "broker.giveup": _s("trouble", "diamond"),
    "ctx.stall": _s("trouble", "diamond", "#fb7185"),
    "ctx.degrade": _s("trouble", "diamond", "#fda4af"),
    "win.stale": _s("trouble", "diamond", "#fda4af"),
    "send.duplicate-suppressed": _s("trouble", "tick", "#fda4af"),
    # -- recovery ---------------------------------------------------------
    "recover": _s("recovery", "diamond"),
    "respawn": _s("recovery", "diamond"),
    "broker.requeue": _s("recovery", "diamond"),
    "failure.ack": _s("recovery", "diamond", "#c4b5fd"),
    "failure.retract": _s("recovery", "diamond", "#c4b5fd"),
    "comm.revoke": _s("recovery", "diamond", "#ddd6fe"),
    "comm.shrink": _s("recovery", "diamond", "#ddd6fe"),
    "comm.agree": _s("recovery", "diamond", "#ddd6fe"),
    "ctx.stall.end": _s("recovery", "tick", "#c4b5fd"),
    "ctx.release": _s("recovery", "tick", "#c4b5fd"),
}

UNKNOWN = Style(MUTED, "tick", "lifecycle")


def style_for(kind: str) -> Style:
    return STYLE.get(kind, UNKNOWN)


def role_of(kind: str) -> str:
    return style_for(kind).role


def is_collective(kind: str) -> bool:
    return kind in COLLECTIVE_KINDS
