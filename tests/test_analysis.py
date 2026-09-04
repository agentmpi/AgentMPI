"""The analysis package is a claim about what the trace contains.

These tests are less about the arithmetic than about the contract between the
runtime and the tooling.  Two of them --- ``test_bcast_is_traced`` and
``test_every_collective_records_blocking`` --- exist because the runtime once
failed them: a broadcast emitted no event at all, so it was invisible to any
analysis, and no collective recorded how long its caller had been blocked, so
coordination cost could not be separated from work.  Both defects were found by
writing the analysis and are the reason it lives in the package rather than in a
script.
"""

from __future__ import annotations

import json

import pytest

from ampitools.analysis import analyse, load_events
from ampitools.analysis import style as st
from ampitools.analysis.figures import render_all
from ampitools.analysis.report import findings, latex, markdown, summary, write_all
from ampitools.harness import Harness


def _run(tmp_path, size=6, device="sqlite"):
    h = Harness(root=str(tmp_path / "job"), size=size, device=device, force=True)
    h.create()

    def rank_main(amp, rank):
        amp.barrier("start", timeout=30)
        amp.memo("phase", "propose")
        mine = amp.scatter(
            "assign",
            payload=[{"rank": i, "unit": i} for i in range(size)] if rank == 0 else None,
            root=0,
            timeout=30,
        )["body"]
        out = amp.allreduce(
            "glossary",
            payload={"shared": f"v{rank % 2}", f"own{rank}": rank},
            op="union",
            timeout=30,
        )
        amp.memo("phase", "assemble")
        if rank == 0:
            amp.bcast("agreed", payload={"ok": True}, root=0, timeout=30)
        else:
            amp.bcast("agreed", root=0, timeout=30, materialize=True)
        amp.gather("final", payload={"rank": rank}, root=0, timeout=30)
        return {"unit": mine["unit"], "conflicts": len(out.get("conflicts") or [])}

    results = h.run(rank_main, timeout=120)
    job = h.attach(0)
    events = job.events()
    job.close()
    return h, results, events


def test_analysis_reads_a_real_run(tmp_path):
    _h, results, events = _run(tmp_path)
    assert all(r.ok for r in results)

    a = analyse(events, name="unit")
    assert a.world_size == 6
    assert a.n_ranks_seen == 6
    assert a.wall_s > 0
    assert not a.inert_ranks
    assert not a.degraded


def test_bcast_is_traced(tmp_path):
    """A collective that emits no event is indistinguishable from one that never ran."""
    _h, _r, events = _run(tmp_path)
    kinds = {e["kind"] for e in events}
    for expected in ("barrier", "bcast", "scatter", "gather", "allreduce"):
        assert expected in kinds, f"{expected} left no trace"


def test_every_collective_records_blocking(tmp_path):
    """Coordination cost is the measurement; it cannot come from a timestamp alone."""
    _h, _r, events = _run(tmp_path)
    for e in events:
        if st.is_collective(e["kind"]):
            assert "waited_s" in e, f"{e['kind']} did not record how long it blocked"
            assert e.get("size"), f"{e['kind']} did not record its communicator size"


def test_collectives_are_grouped_into_invocations(tmp_path):
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")
    labels = {(c.op, c.label) for c in a.collectives}
    assert ("allreduce", "glossary") in labels
    assert ("bcast", "agreed") in labels
    for c in a.collectives:
        assert len(set(c.participants)) == len(c.participants), "a rank appeared twice"
        assert c.complete, f"{c.op}:{c.label} saw {c.n_participants} of {c.size}"


def test_straggler_is_attributed(tmp_path):
    """A skew figure says a barrier cost four minutes; it does not say who owed them."""
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")
    named = [c for c in a.collectives if c.straggler is not None]
    assert named, "no collective attributed its last arrival"
    for c in named:
        assert c.straggler in c.participants
        assert c.arrival_skew_s >= 0


def test_coordination_share_is_a_proportion(tmp_path):
    """A quantity that can exceed one cannot be read as a share of anything."""
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")
    assert 0.0 <= a.coordination_share <= 1.0
    assert 0.0 <= a.coordination_span_share <= 1.0
    assert a.collective_span_s <= a.collective_rank_seconds + 1e-6


def test_conflicts_are_counted(tmp_path):
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")
    # Every rank proposes `shared` with one of two values, so the union operator
    # must lift at least one disagreement rather than let a branch decide it.
    assert a.conflicts_lifted >= 1


def test_phases_come_from_the_harness_memos(tmp_path):
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")
    names = [p.name for p in a.phases]
    assert "propose" in names and "assemble" in names
    assert all(p.duration_s >= 0 for p in a.phases)


def test_cost_model_is_attached(tmp_path):
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")
    costed = a.costed_collectives
    assert costed, "no collective was costed against a closed form"
    for c in costed:
        assert c.predicted_rounds is not None and c.predicted_rounds >= 0
        assert c.predicted_messages is not None


def test_wasted_submissions_are_identified():
    """Work the population finished and the harness threw away.

    The signature of a deadline set without knowing the executor supply, and the
    one measure that says the population was *capable* of finishing while the
    configuration prevented it. Distinct from a failure and from slowness, and
    invisible in both a task count and a wall-clock summary.
    """
    events = [
        {"kind": "job.create", "ts": 0.0, "rank": -1, "size": 2, "seq": 1},
        {"kind": "init", "ts": 1.0, "rank": 0, "seq": 2},
        {"kind": "init", "ts": 1.0, "rank": 1, "seq": 3},
        # Rank 0 submits before anything went wrong: not wasted.
        {"kind": "broker.submit", "ts": 2.0, "rank": 0, "aid": "a", "label": "t0", "seq": 4},
        {"kind": "rank.error", "ts": 3.0, "rank": 1, "error": "AMPI_ERR_TIMEOUT", "seq": 5},
        # Rank 1's worker finishes anyway, after the rank gave up.
        {"kind": "broker.submit", "ts": 9.0, "rank": 1, "aid": "b", "label": "t1", "seq": 6},
    ]
    a = analyse(events, name="wasted")
    wasted = a.wasted_submissions
    assert [w["rank"] for w in wasted] == [1]
    assert wasted[0]["label"] == "t1"
    assert wasted[0]["late_by_s"] == 6.0

    flagged = [f for f in findings(a) if "already failed" in f["text"]]
    assert flagged and flagged[0]["level"] == "error"


def test_no_wasted_submissions_on_a_healthy_run(tmp_path):
    _h, _r, events = _run(tmp_path)
    assert analyse(events, name="unit").wasted_submissions == []


def test_report_renderings(tmp_path):
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")

    text = summary(a)
    assert "world size" in text and "findings" in text

    md = markdown(a)
    assert md.startswith("# Run `unit`")
    assert "## Collectives" in md and "## Ranks" in md

    tex = latex(a, prefix="Unit")
    assert "\\newcommand{\\UnitSize}{6}" in tex
    assert "_" not in tex.split("% generated")[1].split("\n")[1]

    flags = findings(a)
    assert flags and all({"level", "text"} <= set(f) for f in flags)


def test_figures_render(tmp_path):
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")
    made = render_all(a, tmp_path / "figs", fmt="png")
    assert "timeline" in made
    for path in made.values():
        assert path.exists() and path.stat().st_size > 1000


def test_write_all_is_self_contained(tmp_path):
    _h, _r, events = _run(tmp_path)
    a = analyse(events, name="unit")
    written = write_all(a, tmp_path / "out", tex_prefix="Unit", fmt="png")
    assert (tmp_path / "out" / "metrics.json").exists()
    assert (tmp_path / "out" / "report.md").exists()
    assert (tmp_path / "out" / "generated.tex").exists()
    metrics = json.loads((tmp_path / "out" / "metrics.json").read_text())
    assert metrics["world_size"] == 6
    # Every figure the report links must exist beside it, or the committed report
    # is a document with broken images the moment it leaves this machine.
    body = (tmp_path / "out" / "report.md").read_text()
    for name in ("timeline", "waterfall"):
        if f"figures/{name}.png" in body:
            assert (tmp_path / "out" / "figures" / f"{name}.png").exists()
    assert written["report"].exists()


def test_load_events_tolerates_a_truncated_line(tmp_path):
    """A long run is read while it is still writing; refusing is not an option."""
    path = tmp_path / "t.trace.jsonl"
    path.write_text(
        json.dumps({"kind": "job.create", "ts": 1.0, "rank": -1, "size": 2, "seq": 1})
        + "\n"
        + json.dumps({"kind": "init", "ts": 2.0, "rank": 0, "seq": 2})
        + '\n{"kind": "init", "ts": 3.0, "ra',
        encoding="utf-8",
    )
    events = load_events(path)
    assert len(events) == 2
    assert analyse(events, name="partial").world_size == 2


def test_empty_log_is_refused():
    with pytest.raises(ValueError):
        analyse([], name="empty")


def test_style_covers_every_kind_the_runtime_emits(tmp_path):
    """An unstyled kind still renders, but a *collective* must never be unstyled.

    The collective set drives grouping, not just colour: a collective kind missing
    from it is silently excluded from every coordination measure in the report.
    """
    _h, _r, events = _run(tmp_path)
    for e in events:
        kind = e["kind"]
        assert st.style_for(kind) is not None
        if kind in ("barrier", "bcast", "scatter", "gather", "allgather", "reduce",
                    "allreduce", "scan", "exscan", "alltoall"):
            assert st.is_collective(kind)


def test_analysis_is_device_independent(tmp_path):
    """The same program on two transports must yield the same structural analysis."""
    _h, _r, sqlite_events = _run(tmp_path / "a", device="sqlite")
    _h2, _r2, journal_events = _run(tmp_path / "b", device="journal")
    a = analyse(sqlite_events, name="sqlite")
    b = analyse(journal_events, name="journal")
    assert a.world_size == b.world_size
    assert {(c.op, c.label) for c in a.collectives} == {(c.op, c.label) for c in b.collectives}
    assert a.conflicts_lifted == b.conflicts_lifted


def test_a_retry_that_waits_for_peers_is_not_a_replay(tmp_path):
    """Only re-entry into a collective that had already released is a replay."""
    import threading

    from ampi.errors import AmpiError
    from ampi.runtime import Ampi

    root = tmp_path / "job"
    Ampi.create(str(root), 2, device="sqlite", force=True).close()
    a0, a1 = Ampi(str(root), rank=0), Ampi(str(root), rank=1)
    a0.init()
    a1.init()
    try:
        with pytest.raises(AmpiError):
            a0.barrier("gate", timeout=0.3)          # nobody else has arrived
        t = threading.Thread(target=lambda: a1.barrier("gate", timeout=30))
        t.start()
        a0.barrier("gate", timeout=30)               # the retry: it waits for rank 1
        t.join()
        mine = [e for e in a0.events() if e["kind"] == "barrier" and e["rank"] == 0]
        assert len(mine) == 1 and not mine[0].get("replayed")
        a0.barrier("gate", timeout=30)               # a re-entry into a closed collective
        mine = [e for e in a0.events() if e["kind"] == "barrier" and e["rank"] == 0]
        assert len(mine) == 2 and mine[1].get("replayed") is True
        a = analyse(a0.events(), name="unit")
        gates = [c for c in a.collectives if c.label == "gate"]
        assert len(gates) == 1 and gates[0].complete
    finally:
        a0.close()
        a1.close()


def test_collectives_on_different_communicators_are_not_merged(tmp_path):
    """The same kind and label on two communicators are two invocations."""
    from ampi.runtime import Ampi

    root = tmp_path / "job"
    Ampi.create(str(root), 4, device="sqlite", force=True).close()
    amps = [Ampi(str(root), rank=r) for r in range(4)]
    for a in amps:
        a.init()
    try:
        import threading

        def go(a: Ampi) -> None:
            name = "lo" if a.rank < 2 else "hi"
            a.comm_create(name, [0, 1] if a.rank < 2 else [2, 3])
            a.barrier("sync", comm=name, timeout=30)

        ts = [threading.Thread(target=go, args=(a,)) for a in amps]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        an = analyse(amps[0].events(), name="unit")
        syncs = [c for c in an.collectives if c.label == "sync"]
        assert len(syncs) == 2 and all(c.n_participants == 2 and c.complete for c in syncs)
    finally:
        for a in amps:
            a.close()
