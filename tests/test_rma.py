"""One-sided shared state: lost updates, accumulate, locks, staleness, epochs."""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi.constants import LockType


def test_put_get_versions(tmp_path):
    def rank_main(comm):
        win = ampi.win_create(comm, "spec")
        if comm.rank == 0:
            win.put("interfaces", {"parse": "str -> AST"})
        comm.barrier(policy="wait")
        win.sync()
        got = win.get("interfaces", admit=False)
        assert got == {"parse": "str -> AST"}
        assert win.state("interfaces").version == 1
        return got

    job = ampi.launch(rank_main, size=4, root=tmp_path / "w")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_concurrent_put_loses_updates_and_is_reported(tmp_path):
    """The canonical multi-agent data race, made visible.

    Every rank reads the slot, adds its own key, and writes the result back.  The
    final value must contain fewer than ``p`` keys — that is the lost-update bug
    — and the runtime must have counted the stale writes that caused it.  This
    test asserts the *failure*, because the protocol's contribution here is
    detection, and a change that silently fixed the race by serialising every put
    would be a different (and worse) design.
    """
    p = 8

    def rank_main(comm):
        win = ampi.win_create(comm, "board")
        if comm.rank == 0:
            win.put("doc", {"seed": True})
        comm.barrier(policy="wait")
        win.sync()
        doc = dict(win.get("doc", default={}, admit=False) or {})
        comm.barrier(policy="wait")  # force everyone to read before anyone writes
        doc[f"r{comm.rank}"] = comm.rank
        win.put("doc", doc)
        comm.barrier(policy="wait")
        return win.get("doc", admit=False)

    job = ampi.launch(rank_main, size=p, root=tmp_path / "race")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    final = job.value(0)
    assert len(final) < p + 1, "expected lost updates from concurrent blind puts"
    report = ampi.rma.contention_report(ampi.Fabric(tmp_path / "race"), "board")
    assert report["n_stale_writes"] > 0, report
    assert report["stale_write_rate"] > 0


def test_accumulate_never_loses_updates(tmp_path):
    """The fix: an exact, atomic, associative combine needs no lock."""
    p = 8

    def rank_main(comm):
        win = ampi.win_create(comm, "gloss")
        win.accumulate("terms", {f"term{comm.rank}": f"rendering{comm.rank}"}, ampi.UNION)
        comm.barrier(policy="wait")
        win.sync()
        return win.get("terms", admit=False)

    job = ampi.launch(rank_main, size=p, root=tmp_path / "acc")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    final = job.value(0)
    assert len(final) == p, final
    report = ampi.rma.contention_report(ampi.Fabric(tmp_path / "acc"), "gloss")
    assert report["n_stale_writes"] == 0
    assert report["n_accumulates"] == p


def test_accumulate_refuses_lossy_operator(tmp_path):
    lossy = ampi.semantic_op("MERGE", "merge {left} and {right}")

    def rank_main(comm):
        win = ampi.win_create(comm, "w2")
        with pytest.raises(ampi.AmpiUsageError):
            win.accumulate("x", "value", lossy)
        return True

    job = ampi.launch(rank_main, size=1, root=tmp_path / "lossy")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_critical_section_serialises_semantic_updates(tmp_path):
    """lock/get/modify/put is the only safe way to let an agent revise shared state."""
    p = 6

    import time

    def rank_main(comm):
        win = ampi.win_create(comm, "iface")
        if comm.rank == 0:
            win.put("doc", {"n": 0, "authors": []})
        # Two barriers, so every rank attempts the lock at the same moment. Without
        # this the ranks can trivially serialise and contention never occurs, which
        # made an earlier version of this test depend on scheduling luck.
        comm.barrier(policy="wait")
        comm.barrier(policy="wait")
        with win.critical("doc"):
            doc = win.get("doc", admit=False)
            # Hold the lock long enough that concurrent attempts must queue. This is
            # what a semantic update looks like in cost terms -- an agent call inside
            # the critical section -- so the test measures the real shape.
            time.sleep(0.05)
            doc = {"n": doc["n"] + 1, "authors": [*doc["authors"], comm.rank]}
            win.put("doc", doc, expect_version=win.state("doc").version)
        comm.barrier(policy="wait")
        win.sync()
        return win.get("doc", admit=False)

    job = ampi.launch(rank_main, size=p, root=tmp_path / "crit")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    final = job.value(0)
    # The invariant that matters, and the one that fails without the lock: every rank's
    # contribution survives, and no write was issued from a stale view.
    assert final["n"] == p, final
    assert sorted(final["authors"]) == list(range(p))
    report = ampi.rma.contention_report(ampi.Fabric(tmp_path / "crit"), "iface")
    assert report["n_stale_writes"] == 0
    # With p ranks contending for a held lock, all but the winner must have waited, so
    # the serialisation is visible as wall time rather than inferred.
    assert report["n_contended"] >= p - 1, report
    assert report["total_lock_wait_s"] > 0.0, report


def test_compare_and_swap(tmp_path):
    def rank_main(comm):
        win = ampi.win_create(comm, "cas")
        if comm.rank == 0:
            win.put("v", {"state": "draft"})
        comm.barrier(policy="wait")
        win.sync()
        ok, observed = win.compare_and_swap("v", {"state": "draft"}, {"state": f"claimed-by-{comm.rank}"})
        comm.barrier(policy="wait")
        return ok

    job = ampi.launch(rank_main, size=6, root=tmp_path / "cas")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    winners = [o.rank for o in job.outcomes if o.value]
    assert len(winners) == 1, f"exactly one rank must win the CAS, got {winners}"


def test_fetch_and_op_gives_dynamic_self_scheduling(tmp_path):
    """A shared work counter: every rank claims indices until they run out."""
    n_items = 40

    def rank_main(comm):
        win = ampi.win_create(comm, "sched", initial={"next": 0} if comm.rank == 0 else None)
        comm.barrier(policy="wait")
        mine = []
        while True:
            with win.critical("next"):
                cur = win.get("next", default=0, admit=False)
                if cur >= n_items:
                    break
                win.put("next", cur + 1)
            mine.append(cur)
        return mine

    job = ampi.launch(rank_main, size=5, root=tmp_path / "sched")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    claimed = sorted(i for o in job.outcomes for i in (o.value or []))
    assert claimed == list(range(n_items)), "every item claimed exactly once"


def test_exclusive_lock_excludes(tmp_path):
    import threading

    order: list[tuple[int, str]] = []
    lock = threading.Lock()

    def rank_main(comm):
        win = ampi.win_create(comm, "excl")
        win.lock("s", mode=LockType.EXCLUSIVE, timeout=30)
        with lock:
            order.append((comm.rank, "enter"))
        import time

        time.sleep(0.05)
        with lock:
            order.append((comm.rank, "exit"))
        win.unlock("s")
        return True

    job = ampi.launch(rank_main, size=4, root=tmp_path / "excl")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    # No two enters may be adjacent: entries must strictly alternate enter/exit.
    kinds = [k for _, k in order]
    assert kinds == ["enter", "exit"] * 4, order


def test_expired_lock_is_reclaimed(tmp_path):
    """A lock held by a vanished agent must not deadlock the window forever."""

    def rank_main(comm):
        win = ampi.win_create(comm, "lease")
        if comm.rank == 0:
            win.lock("s", lease=0.2)  # acquired then abandoned
            comm.barrier(policy="wait")
            return "abandoned"
        comm.barrier(policy="wait")
        import time

        time.sleep(0.4)
        win.lock("s", timeout=10)
        win.unlock("s")
        return "reclaimed"

    job = ampi.launch(rank_main, size=2, root=tmp_path / "lease")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert job.value(1) == "reclaimed"


def test_fence_invalidates_private_copies(tmp_path):
    """Under the SEPARATE model, a fence forces a genuine re-read.

    Note the epoch discipline: reads and writes never share an epoch.  Putting a
    write in the same epoch as other ranks' reads is precisely the bug MPI's
    access/exposure epochs exist to forbid, and doing it here would make the test
    racy -- some ranks would observe the new value in what was supposed to be the
    old epoch.  Structuring the phases as write / fence / read / fence / write /
    fence / read is not ceremony; it is the guarantee.
    """

    def rank_main(comm):
        win = ampi.win_create(comm, "sep")
        if comm.rank == 0:
            win.put("k", {"v": 1})
        win.fence(label="after-first-write")

        first = win.get("k", admit=False)
        assert first == {"v": 1}, first
        assert win.staleness("k").seen_version == 1
        win.fence(label="after-first-read")

        if comm.rank == 1:
            win.put("k", {"v": 2})
        win.fence(label="after-second-write")

        assert win.staleness("k").seen_version == 0, "fence must clear the recorded view"
        second = win.get("k", admit=False)
        assert second == {"v": 2}, second
        return second

    job = ampi.launch(rank_main, size=3, root=tmp_path / "fence")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
