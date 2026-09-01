from __future__ import annotations

import pytest

from ampi.trace_style import ROLE_COLOR, ROLE_LABEL, role_of, style_for


@pytest.mark.parametrize(
    "kind, expected",
    [
        ("broker.claim", "work"),
        ("broker.submit", "work"),
        ("send", "message"),
        ("recv", "message"),
        ("coll.join", "collective"),
        ("allreduce", "collective"),
        ("win.put", "rma"),
        ("win.stale", "fault"),
        ("ctx.stall", "context"),
        ("init", "lifecycle"),
        ("finalize", "lifecycle"),
        ("failure.suspect", "fault"),
        ("failure.retract", "recovery"),
        ("respawn", "recovery"),
        ("memo", "other"),
    ],
)
def test_role_of_current_trace_kinds(kind: str, expected: str) -> None:
    assert role_of(kind) == expected


def test_style_for_role_and_event_is_consistent() -> None:
    event_style = style_for("broker.claim")
    role_style = style_for("work")

    assert event_style == role_style
    assert event_style.glyph == "bar"
    assert event_style.color == ROLE_COLOR["work"]
    assert event_style.label == ROLE_LABEL["work"]


def test_instants_have_distinct_glyphs() -> None:
    assert style_for("send").glyph == "tick"
    assert style_for("failure.convict").glyph == "diamond"
