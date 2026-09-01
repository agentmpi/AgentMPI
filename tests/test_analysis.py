"""Invariants the run analysis must satisfy, checked against the whole committed archive.

The analysis produces the numbers that five hundred documents argue from, so an error here is
not a wrong figure in one place --- it is a wrong figure repeated five hundred times, in prose
written by someone who trusted it.

The specific failure that motivated this file: ``coordination_share`` summed each collective's
*maximum* per-rank blocking time and divided by wall time. That is not a share of anything. It
charged one rank's wait inside a reduce and another rank's concurrent wait inside the following
broadcast as two separate costs, and additionally counted composed collectives alongside the
constituents they delegate to. On a translation ablation it reported 137%. Nothing caught it,
because nothing asserted the one property its name promises: that it is a proportion.

So these tests assert bounds and identities rather than values. Values change when a run is
re-measured; a proportion that exceeds one is wrong in any run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentmpi import analysis as an

REPO = Path(__file__).resolve().parent.parent
EVENTS = REPO / "traces" / "events"

pytestmark = pytest.mark.skipif(not EVENTS.exists(), reason="no trace archive in this checkout")


def load(name: str) -> list[dict]:
    with (EVENTS / f"{name}.jsonl").open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


@pytest.fixture(scope="module")
def analyses() -> list[an.Analysis]:
    """Every run in the archive. Slow enough to build once, cheap enough to check exhaustively."""
    out = []
    for path in sorted(EVENTS.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        if events:
            out.append(an.analyse(events, path.name[: -len(".jsonl")]))
    assert len(out) > 400, f"expected the full archive, got {len(out)} runs"
    return out


# -- quantities that are proportions must behave like proportions ----------------------


@pytest.mark.parametrize(
    "attribute",
    [
        "coordination_share",
        "coordination_span_share",
    ],
)
def test_shares_are_bounded_by_one(analyses: list[an.Analysis], attribute: str) -> None:
    for a in analyses:
        value = getattr(a, attribute)
        assert 0.0 <= value <= 1.0 + 1e-9, f"{a.name}: {attribute} = {value}"


def test_concurrency_shares_are_bounded(analyses: list[an.Analysis]) -> None:
    for a in analyses:
        c = a.concurrency
        assert 0.0 <= c.parallel_efficiency <= 1.0 + 1e-9, f"{a.name}: efficiency {c.parallel_efficiency}"
        assert 0.0 <= c.idle_fraction <= 1.0 + 1e-9, f"{a.name}: idle {c.idle_fraction}"
        assert 0.0 <= c.serial_fraction_of_busy <= 1.0 + 1e-9, a.name
        assert c.max_busy <= max(a.world_size, a.n_ranks_seen), f"{a.name}: {c.max_busy} busy"


def test_context_occupancy_is_a_fraction(analyses: list[an.Analysis]) -> None:
    for a in analyses:
        for rank, p in a.ranks.items():
            assert 0.0 <= p.context_occupancy <= 1.0 + 1e-9, f"{a.name} rank {rank}"


# -- additive quantities must not double count ----------------------------------------


def test_composed_collectives_are_excluded_from_coordination_cost(analyses: list[an.Analysis]) -> None:
    """A delegating collective spans its constituents, so counting both charges the same wait twice."""
    for a in analyses:
        composed = [c for c in a.collectives if c.is_composed]
        if not composed:
            continue
        assert a.collective_rank_seconds == pytest.approx(
            sum(c.rank_wall_s for c in a.collectives if not c.is_composed)
        ), a.name
        assert all(c not in a.primitive_collectives for c in composed), a.name


def test_unioned_span_never_exceeds_summed_rank_seconds(analyses: list[an.Analysis]) -> None:
    """The wall-clock union of blocking intervals cannot exceed the rank-seconds that produced it."""
    for a in analyses:
        assert a.collective_span_s <= a.collective_rank_seconds + 1e-6, a.name


def test_span_never_exceeds_wall_time(analyses: list[an.Analysis]) -> None:
    for a in analyses:
        assert a.collective_span_s <= a.wall_s + 1e-6, f"{a.name}: span {a.collective_span_s} > wall {a.wall_s}"


def test_invocation_rank_wall_is_at_least_its_critical_path(analyses: list[an.Analysis]) -> None:
    """Summed per-rank blocking must be at least the maximum, and equal it only at p=1."""
    for a in analyses:
        for c in a.collectives:
            assert c.rank_wall_s >= c.wall_s - 1e-9, f"{a.name}: {c.op}/{c.algorithm}"


# -- model checking must not be confused by runs that did not finish -------------------


def test_incomplete_collectives_are_never_model_failures(analyses: list[an.Analysis]) -> None:
    for a in analyses:
        for c in a.collectives:
            if not c.complete:
                assert c.messages_agree is None, f"{a.name}: {c.op} judged with {c.n_participants}/{c.size} ranks"


def test_the_model_agrees_with_logged_traffic_everywhere(analyses: list[an.Analysis]) -> None:
    """The archive-wide claim: every checkable collective sent what its cost formula predicts."""
    disagreements = [
        (a.name, c.op, c.algorithm, c.size, c.logged_messages, c.predicted_messages)
        for a in analyses
        for c in a.collectives
        if c.messages_agree is False
    ]
    assert disagreements == [], disagreements


def test_recursive_doubling_accounting_gap_is_confined_to_the_archive(analyses: list[an.Analysis]) -> None:
    """The known under-count is present in recorded traces and only at non-power-of-two sizes.

    Pinned rather than ignored. The traces predate the fix, so the gap is a historical fact about
    the archive; if it ever appears at a power of two, or in a different algorithm, the cause is
    something new.
    """
    for a in analyses:
        for c in a.misreported_collectives:
            if c.op in ("halo_exchange", "neighbor_allgather", "neighbor_alltoall"):
                continue  # neighbourhood collectives reported no count at all before the fix
            assert (c.op, c.algorithm) == ("allreduce", "recursive_doubling"), f"{a.name}: {c.op}/{c.algorithm}"
            assert c.size & (c.size - 1) != 0, f"{a.name}: gap at power-of-two size {c.size}"


# -- structural sanity ----------------------------------------------------------------


def test_participants_never_exceed_communicator_size(analyses: list[an.Analysis]) -> None:
    for a in analyses:
        for c in a.collectives:
            assert c.n_participants <= c.size, f"{a.name}: {c.op} had {c.n_participants} of {c.size}"


def test_stray_ranks_are_outside_the_world_or_inert(analyses: list[an.Analysis]) -> None:
    for a in analyses:
        for rank in a.stray_ranks:
            p = a.ranks[rank]
            inert = p.sent == 0 and p.recv == 0 and p.n_work == 0 and p.n_agent_calls == 0
            assert rank >= a.world_size or inert, f"{a.name}: rank {rank} judged stray but participated"


def test_as_dict_is_json_serialisable(analyses: list[an.Analysis]) -> None:
    """The documents read metrics.json, so anything unserialisable is a silent data loss."""
    for a in analyses[:40]:
        json.dumps(a.as_dict())
