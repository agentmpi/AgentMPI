"""The visual vocabulary for AgentMPI traces: one palette, two renderers.

The trace viewer (``viz/``) and the analysis plots (``scripts/analyze_run.py``) draw the same
events, and a reader moves between them constantly --- finding a shape in the dashboard and
then citing it in a document. If the two disagree about what red means, that reader is
actively misled, which is worse than either being ugly.

So the taxonomy lives here, and ``tests/test_trace_style.py`` asserts that the TypeScript
table in ``viz/src/types.ts`` agrees with it. Neither side is generated from the other ---
codegen would put a build step between an edit and a working viewer --- but they cannot
silently diverge either.

Two independent axes, deliberately:

**Colour encodes role.** What kind of thing happened: work, a message, a one-sided window
operation, a lifecycle transition, trouble, or recovery from trouble. Trouble is red
everywhere, so it is findable at a glance in a lane otherwise dense with green message ticks.

**Glyph encodes duration.** A bar has real extent in time; a tick and a diamond are instants.
Keeping this separate from colour is what lets a reader answer "how long did that take" and
"what was it" independently, rather than having to remember a combined legend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Role = Literal["work", "message", "rma", "lifecycle", "trouble", "recovery"]
Glyph = Literal["bar", "tick", "diamond"]

#: The dashboard's own colours, so a figure dropped into a document beside a screenshot of
#: the viewer does not look like it came from a different tool.
BACKGROUND = "#0b1020"
PANEL = "#131a2e"
PANEL_ALT = "#1a2338"
LINE = "#263352"
FOREGROUND = "#e8edf7"
MUTED = "#94a3b8"
ACCENT = "#60a5fa"
STALE = "#dc2626"

ROLE_COLOR: dict[str, str] = {
    "work": "#3b82f6",
    "message": "#10b981",
    "rma": "#f59e0b",
    "lifecycle": "#64748b",
    "trouble": "#ef4444",
    "recovery": "#a78bfa",
}

ROLE_LABEL: dict[str, str] = {
    "work": "agent working",
    "message": "messages",
    "rma": "one-sided window ops",
    "lifecycle": "rank lifecycle",
    "trouble": "failure / timeout",
    "recovery": "fault recovery",
}

#: Order roles appear in legends and stacked summaries: what a rank was doing, then who it
#: talked to, then what went wrong, then what the harness did about it.
ROLE_ORDER: tuple[str, ...] = ("work", "message", "rma", "lifecycle", "trouble", "recovery")


@dataclass(frozen=True)
class Style:
    color: str
    glyph: str
    role: str


#: Colour, glyph, and role per event kind. Must match ``STYLE`` in ``viz/src/types.ts``.
STYLE: dict[str, Style] = {
    "work": Style(ROLE_COLOR["work"], "bar", "work"),
    "agent.call": Style("#2563eb", "bar", "work"),
    "msg.send": Style(ROLE_COLOR["message"], "tick", "message"),
    "msg.recv": Style("#34d399", "tick", "message"),
    "msg.fetch": Style("#6ee7b7", "tick", "message"),
    "win.put": Style(ROLE_COLOR["rma"], "diamond", "rma"),
    "win.get": Style("#fbbf24", "diamond", "rma"),
    "win.accumulate": Style("#a78bfa", "diamond", "rma"),
    "win.cas": Style("#c4b5fd", "diamond", "rma"),
    "win.lock": Style("#fb923c", "diamond", "rma"),
    "win.unlock": Style("#fdba74", "diamond", "rma"),
    "win.sync": Style("#fcd34d", "tick", "rma"),
    "win.flush": Style("#fcd34d", "tick", "rma"),
    "rank.init": Style(ROLE_COLOR["lifecycle"], "tick", "lifecycle"),
    "rank.finalize": Style(ROLE_COLOR["lifecycle"], "tick", "lifecycle"),
    "rank.compact": Style("#94a3b8", "diamond", "lifecycle"),
    "proc.spawn": Style("#38bdf8", "diamond", "lifecycle"),
    "sup.restart": Style("#38bdf8", "diamond", "lifecycle"),
    "rank.error": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "rank.stuck": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "rank.version_mismatch": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "barrier.timeout": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "win.lock_timeout": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "broker.expire": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "broker.fail": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "broker.reclaim": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "agent.contract_violation": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "transport.credit_stall": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "msg.orphaned": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "harness.degraded": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "ft.declare_failed": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "ft.agree_timeout": Style(ROLE_COLOR["trouble"], "diamond", "trouble"),
    "ft.agree": Style(ROLE_COLOR["recovery"], "diamond", "recovery"),
    "ft.revoke": Style(ROLE_COLOR["recovery"], "diamond", "recovery"),
    "ft.shrink": Style(ROLE_COLOR["recovery"], "diamond", "recovery"),
    "ft.shrink_in_place": Style(ROLE_COLOR["recovery"], "diamond", "recovery"),
    "ft.failure_ack": Style(ROLE_COLOR["recovery"], "diamond", "recovery"),
    "sup.escalate": Style(ROLE_COLOR["recovery"], "diamond", "recovery"),
    "transport.credit_granted": Style(ROLE_COLOR["recovery"], "diamond", "recovery"),
}

#: An unrecognised kind stays visible rather than vanishing; a trace that silently omits an
#: event is indistinguishable from one where the event did not happen.
UNKNOWN = Style(MUTED, "tick", "lifecycle")


def style_for(kind: str) -> Style:
    return STYLE.get(kind, UNKNOWN)


def role_of(kind: str) -> str:
    return style_for(kind).role
