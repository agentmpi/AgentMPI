"""The committed trace archive is evidence, so it is tested like code.

Every number in the paper is derived from the runs committed under ``runs/`` and exported to
``traces/``. That makes the archive part of the contract of this repository rather than a
convenience: if it silently drifts --- a run executed but never exported, an exporter change
that alters the derived view, a hand-edited log --- then the paper cites something that is no
longer in the repository, and nothing else in the test suite would notice.

These tests are skipped when the archive is absent so that a checkout without runs (or a
fork that strips them) still has a green suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentmpi import cost

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "traces" / "manifest.json"
EVENTS = REPO / "traces" / "events"
VIEWS = REPO / "viz" / "public" / "traces"

pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="no trace archive in this checkout")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def read_log(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_every_fabric_is_exported(manifest: dict) -> None:
    """A run that was executed but never exported is invisible to a reader."""
    on_disk = {
        str(db.parent.relative_to(REPO / "runs")).replace("/", "__")
        for db in (REPO / "runs").rglob("fabric.sqlite")
    }
    exported = {r["name"] for r in manifest["runs"]}
    assert on_disk - exported == set(), "fabrics present in runs/ but missing from the archive"
    assert exported - on_disk == set(), "archive references runs that are not in runs/"


def test_manifest_digests_match_the_logs(manifest: dict) -> None:
    """Catches a hand-edited or truncated log."""
    import hashlib

    for run in manifest["runs"]:
        body = (REPO / run["events"]).read_bytes()
        assert hashlib.sha256(body).hexdigest()[:16] == run["events_sha256"], run["name"]


def test_event_counts_match_the_manifest(manifest: dict) -> None:
    total = 0
    for run in manifest["runs"]:
        n = len(read_log(REPO / run["events"]))
        assert n == run["n_events"], f"{run['name']}: {n} events, manifest says {run['n_events']}"
        total += n
    assert total == manifest["n_events"]


def test_viewer_payloads_parse_and_have_lanes(manifest: dict) -> None:
    """The viewer must render every committed run, not just index them."""
    for run in manifest["runs"]:
        payload = json.loads((REPO / run["view"]).read_text(encoding="utf-8"))
        assert payload["lanes"], f"{run['name']} has no per-rank lanes to draw"


def test_index_covers_every_exported_run(manifest: dict) -> None:
    index = json.loads((VIEWS / "index.json").read_text(encoding="utf-8"))
    assert {e["name"] for e in index} == {r["name"] for r in manifest["runs"]}


def test_collective_validation_is_recomputable_from_logs_alone(manifest: dict) -> None:
    """The strongest property: the logs suffice to re-derive the paper's model validation.

    For every measured collective configuration, the number of ``msg.send`` events recorded in
    the exported log must equal the closed-form message count. This is the same check the
    microbenchmarks make against a live fabric, re-run here against the committed text --- so
    it fails if the archive stops being sufficient, independently of whether ``runs/`` is
    readable.
    """
    import re

    pattern = re.compile(r"coll-([a-z]+)-(.+)-(\d+)$")
    checked = 0
    for log in sorted(EVENTS.glob("*.jsonl")):
        tail = log.name[: -len(".jsonl")].split("__", 1)[-1]
        m = pattern.match(tail)
        if not m:
            continue
        op, alg, p = m.group(1), m.group(2), int(m.group(3))
        formula = cost.FORMULAS.get((op, alg))
        assert formula is not None, f"measured {op}/{alg} has no closed-form cost entry"
        measured = sum(1 for e in read_log(log) if e.get("kind") == "msg.send")
        _rounds, messages, _volume, _depth = formula(p, 1000)
        assert int(messages) == measured, f"{tail}: closed form {int(messages)}, log {measured}"
        checked += 1
    assert checked > 100, f"only {checked} collective configurations recovered from the archive"


def test_summarise_accepts_an_exported_log(manifest: dict) -> None:
    """Exported logs summarise through the same code path as a live fabric."""
    run = max(manifest["runs"], key=lambda r: r["n_events"])
    summary = cost.summarise(read_log(REPO / run["events"]))
    assert summary.n_messages > 0
    assert summary.wall_s >= 0.0
