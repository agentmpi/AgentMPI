"""The viewer and the analysis plots must agree on what a colour means.

``src/agentmpi/trace_style.py`` and the ``STYLE`` table in ``viz/src/types.ts`` describe the
same taxonomy for two renderers. Neither generates the other, so nothing but a test stops
them drifting --- and drift here is not cosmetic: a reader who finds a red diamond in the
dashboard and then cites the corresponding figure would be reading two different claims about
what happened.

The TypeScript is parsed rather than executed because adding a Node dependency to the Python
test suite costs more than a regex over a table that is, by construction, one flat literal.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentmpi import trace_style

TYPES_TS = Path(__file__).resolve().parent.parent / "viz" / "src" / "types.ts"

pytestmark = pytest.mark.skipif(not TYPES_TS.exists(), reason="viewer sources not present")

ENTRY = re.compile(
    r'^\s*"?([a-z][a-z._]*)"?:\s*\{\s*color:\s*(?:ROLE_COLOR\.(\w+)|"(#[0-9a-fA-F]{6})")\s*,'
    r'\s*glyph:\s*"(\w+)"\s*,\s*role:\s*"(\w+)"\s*\}',
    re.MULTILINE,
)


@pytest.fixture(scope="module")
def ts_table() -> dict[str, trace_style.Style]:
    src = TYPES_TS.read_text(encoding="utf-8")
    body = src.split("export const STYLE", 1)
    assert len(body) == 2, "could not find the STYLE table in types.ts"
    # Stop at the closing brace of the object literal so later declarations are not scanned.
    literal = body[1].split("\n};", 1)[0]

    out: dict[str, trace_style.Style] = {}
    for kind, role_ref, literal_color, glyph, role in ENTRY.findall(literal):
        color = trace_style.ROLE_COLOR[role_ref] if role_ref else literal_color
        out[kind] = trace_style.Style(color.lower(), glyph, role)
    assert out, "parsed no entries from the STYLE table"
    return out


def test_the_two_tables_cover_the_same_event_kinds(ts_table: dict) -> None:
    py = set(trace_style.STYLE)
    ts = set(ts_table)
    assert py - ts == set(), "kinds styled in Python but missing from the viewer"
    assert ts - py == set(), "kinds styled in the viewer but missing from Python"


def test_colour_glyph_and_role_agree(ts_table: dict) -> None:
    for kind, py in trace_style.STYLE.items():
        ts = ts_table[kind]
        assert (py.color.lower(), py.glyph, py.role) == (ts.color, ts.glyph, ts.role), kind


def test_role_palette_and_labels_agree_with_the_viewer() -> None:
    src = TYPES_TS.read_text(encoding="utf-8")
    for role, color in trace_style.ROLE_COLOR.items():
        assert re.search(rf'{role}:\s*"{color}"', src, re.IGNORECASE), f"ROLE_COLOR.{role}"
    for role, label in trace_style.ROLE_LABEL.items():
        assert f'"{label}"' in src, f"ROLE_LABEL.{role} ({label!r}) not in types.ts"


def test_every_styled_kind_is_actually_drawable() -> None:
    """A styled kind the server never puts in a lane is dead weight; the reverse is a hole."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import trace_server as ts

    drawn = set(ts.LANE_KINDS) | {"work"}
    drawn.discard("broker.claim")  # consumed to build a `work` span, never drawn directly
    drawn.discard("broker.complete")
    styled = set(trace_style.STYLE)
    assert drawn - styled == set(), "server draws kinds that have no style"
    assert styled - drawn == set(), "styles exist for kinds the server never draws"
