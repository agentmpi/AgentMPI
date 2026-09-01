"""Shared semantic roles and colours for trace visualisations."""

from __future__ import annotations

from dataclasses import dataclass

BACKGROUND = "#111827"
PANEL = "#172033"
PANEL_ALT = "#1e293b"
FOREGROUND = "#e5e7eb"
MUTED = "#9ca3af"
LINE = "#374151"

ROLE_ORDER = (
    "work",
    "message",
    "collective",
    "rma",
    "lifecycle",
    "fault",
    "recovery",
    "context",
    "other",
)
ROLE_COLOR = {
    "work": "#60a5fa",
    "message": "#22d3ee",
    "collective": "#a78bfa",
    "rma": "#f59e0b",
    "lifecycle": "#94a3b8",
    "fault": "#f87171",
    "recovery": "#34d399",
    "context": "#fbbf24",
    "other": "#6b7280",
}
ROLE_LABEL = {
    "work": "broker work",
    "message": "point-to-point",
    "collective": "collective",
    "rma": "window / lock",
    "lifecycle": "lifecycle",
    "fault": "fault",
    "recovery": "recovery",
    "context": "context pressure",
    "other": "other",
}

FAULT_KINDS = {
    "broker.giveup",
    "broker.reject",
    "failure.convict",
    "failure.kill",
    "rank.error",
    "runtime.changed",
    "win.stale",
}
RECOVERY_KINDS = {
    "broker.requeue",
    "comm.shrink",
    "failure.ack",
    "failure.retract",
    "recover",
    "respawn",
}
LIFECYCLE_KINDS = {"finalize", "init", "init.heartbeat", "job.create"}


@dataclass(frozen=True)
class EventStyle:
    role: str
    color: str
    glyph: str
    label: str


def role_of(kind: str) -> str:
    """Return the semantic role of a current-runtime event kind."""
    if kind in FAULT_KINDS or kind.startswith("failure.") and kind not in RECOVERY_KINDS:
        return "fault"
    if kind in RECOVERY_KINDS:
        return "recovery"
    if kind in LIFECYCLE_KINDS:
        return "lifecycle"
    if kind.startswith("broker."):
        return "work"
    if kind in {"send", "recv", "send.duplicate-suppressed"}:
        return "message"
    if kind == "coll.join" or kind in {
        "allgather",
        "allreduce",
        "alltoall",
        "barrier",
        "barrier.proceed",
        "bcast",
        "exscan",
        "gather",
        "neighbor_allgather",
        "neighbor_alltoall",
        "reduce",
        "scan",
        "scatter",
    }:
        return "collective"
    if kind.startswith("win."):
        return "rma"
    if kind.startswith("ctx."):
        return "context"
    return "other"


def style_for(kind_or_role: str) -> EventStyle:
    """Resolve either an event kind or an already classified role."""
    role = kind_or_role if kind_or_role in ROLE_COLOR else role_of(kind_or_role)
    if role == "work":
        glyph = "bar"
    elif role in {"message", "collective"}:
        glyph = "tick"
    else:
        glyph = "diamond"
    return EventStyle(role, ROLE_COLOR[role], glyph, ROLE_LABEL[role])
