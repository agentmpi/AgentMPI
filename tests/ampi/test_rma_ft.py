"""One-sided operations, locking, failure mitigation, and semantic upcalls."""

from __future__ import annotations

import threading

import pytest

from ampi.constants import LOCK_EXCLUSIVE, LOCK_SHARED, RANK_FAILED
from ampi.core import collectives as coll
from ampi.core.collectives import SemanticUpcall
from ampi.errors import AmpiProcFailed, AmpiRevoked, AmpiTimeout


# ---------------------------------------------------------------------------
# One-sided operations
# ---------------------------------------------------------------------------


def test_window_put_get(make_job):
    job = make_job(3)

    def body(rt, rank):
        rt.win_create("world", "board")
        coll.barrier(rt, "world", timeout=30)
        rt.win_put("board", f"note/{rank}", {"by": rank, "text": f"hello {rank}"})
        coll.barrier(rt, "world", timeout=30)
        return [rt.win_get("board", f"note/{r}")["value"]["by"] for r in range(3)]

    out = job.run_ranks(body)
    for values in out.values():
        assert values == [0, 1, 2]


def test_compare_and_swap_rejects_stale_writes(make_job):
    """Version-checked put is the lost-update defence.

    Two agents that read the same cell and both write back must not silently
    clobber one another; the loser is told to re-read and retry.
    """
    job = make_job(2)

    def body(rt, rank):
        rt.win_create("world", "board")
        coll.barrier(rt, "world", timeout=30)
        if rank == 0:
            rt.win_put("board", "design", {"v": "initial"})
        coll.barrier(rt, "world", timeout=30)
        version = rt.win_get("board", "design")["version"]
        coll.barrier(rt, "world", timeout=30)
        # Both ranks now try to write over the same version.
        return rt.win_put("board", "design", {"v": f"edit-by-{rank}"},
                          expected_version=version)["ok"]

    out = job.run_ranks(body)
    assert sorted(out.values()) == [False, True]


def test_fetch_and_op_hands_out_each_work_item_once(make_job):
    """The atomic counter is what stops two agents doing the same task."""
    p = 6
    job = make_job(p)

    def body(rt, rank):
        rt.win_create("world", "queue")
        coll.barrier(rt, "world", timeout=30)
        taken = []
        while True:
            item = int(rt.win_fetch_and_op("queue", "next", 1.0)["old"])
            if item >= 24:
                break
            taken.append(item)
        return taken

    out = job.run_ranks(body)
    everything = sorted(x for taken in out.values() for x in taken)
    assert everything == list(range(24))


def test_claim_is_exclusive(make_job):
    p = 5
    job = make_job(p)

    def body(rt, rank):
        rt.win_create("world", "tasks")
        coll.barrier(rt, "world", timeout=30)
        return rt.win_claim("tasks", "task/hot")["claimed"]

    out = job.run_ranks(body)
    assert sum(1 for v in out.values() if v) == 1


def test_exclusive_lock_serialises_writers(make_job):
    p = 4
    job = make_job(p)

    def body(rt, rank):
        rt.win_create("world", "doc")
        coll.barrier(rt, "world", timeout=30)
        lock = rt.win_lock("doc", "section/1", mode=LOCK_EXCLUSIVE, ttl=30, timeout=60)
        current = rt.win_get("doc", "section/1")["value"] or []
        rt.win_put("doc", "section/1", current + [rank])
        rt.win_unlock(lock["lock_id"])
        return True

    job.run_ranks(body)
    rt = job.runtime(0)
    final = rt.win_get("doc", "section/1")["value"]
    assert sorted(final) == list(range(p)), f"lost updates: {final}"


def test_shared_locks_do_not_block_each_other(make_job):
    job = make_job(3)

    def body(rt, rank):
        rt.win_create("world", "ro")
        coll.barrier(rt, "world", timeout=30)
        lock = rt.win_lock("ro", "cell", mode=LOCK_SHARED, ttl=20, timeout=15)
        coll.barrier(rt, "world", timeout=30)
        rt.win_unlock(lock["lock_id"])
        return True

    assert all(job.run_ranks(body).values())


def test_expired_lease_does_not_wedge_the_window(make_job):
    """An agent that dies holding a lock must not block the job forever."""
    job = make_job(2)
    rt0 = job.runtime(0)
    rt0.init(0)
    rt0.win_create("world", "w")
    rt0.win_lock("w", "k", mode=LOCK_EXCLUSIVE, ttl=0.2, timeout=5)
    import time

    time.sleep(0.4)
    rt1 = job.runtime(1)
    rt1.init(1)
    got = rt1.win_lock("w", "k", mode=LOCK_EXCLUSIVE, ttl=5, timeout=5)
    assert got["lock_id"]


# ---------------------------------------------------------------------------
# Failure mitigation
# ---------------------------------------------------------------------------


def test_revoke_unblocks_a_waiting_rank(make_job):
    """The reason ULFM has revoke: knowledge of failure is not uniform.

    Rank 1 is blocked waiting for a peer that will never answer.  Nothing rank
    1 can observe will release it, so a third party revokes the communicator
    and every outstanding operation on it fails at once.
    """
    job = make_job(3)
    ready = threading.Event()

    def body(rt, rank):
        if rank == 1:
            ready.set()
            with pytest.raises((AmpiRevoked, AmpiTimeout)) as excinfo:
                rt.recv("world", 2, 1, timeout=40)
            return type(excinfo.value).__name__
        if rank == 0:
            ready.wait(10)
            import time

            time.sleep(3)
            rt.comm_revoke("world")
            return "revoked"
        return "idle"

    out = job.run_ranks(body, timeout=90)
    assert out[0] == "revoked"
    assert out[1] == "AmpiRevoked"


def test_shrink_excludes_failed_ranks_and_renumbers(make_job):
    job = make_job(5)
    rt = job.runtime(0)
    rt.init(0)
    rt.declare_failed(2, "test injection")
    rt.declare_failed(4, "test injection")
    shrunk = rt.comm_shrink("world", "survivors")
    assert shrunk["members"] == [0, 1, 3]
    assert shrunk["size"] == 3
    comm = rt.comms.get("survivors")
    assert comm.rank_of(3) == 2, "survivors must be renumbered densely"


def test_agree_ignores_failed_ranks(make_job):
    """Agreement must terminate over the survivors, not wait for the dead."""
    job = make_job(4)
    rt_setup = job.runtime(0)
    rt_setup.init(0)
    rt_setup.declare_failed(3, "test injection")

    def body(rt, rank):
        return rt.comm_agree("world", rank != 1, timeout=30)

    out = job.run_ranks(body, ranks=[0, 1, 2], timeout=90)
    for rank, result in out.items():
        assert result["agreed"] is False, f"rank {rank}"
        assert 3 not in result["participants"]
        assert 3 in result["failed"]


def test_agree_reaches_true_when_all_survivors_agree(make_job):
    job = make_job(3)
    out = job.run_ranks(lambda rt, r: rt.comm_agree("world", True, timeout=30), timeout=60)
    assert all(v["agreed"] for v in out.values())


def test_respawn_resets_generation_and_context(make_job):
    job = make_job(2)
    rt = job.runtime(0)
    rt.init(0)
    rt.declare_failed(1, "died")
    assert rt.rank_row(1)["state"] == RANK_FAILED
    fresh = rt.respawn(1)
    assert fresh["generation"] == 1
    assert rt.rank_row(1)["ctx_used"] == 0


def test_inbox_replay_survives_death(make_job):
    """A replacement agent reconstructs its predecessor's inbound history.

    Message logging: because delivery does not destroy the record, recovery
    needs no cooperation from the senders.
    """
    job = make_job(3)
    rt0, rt1, rt2 = job.runtime(0), job.runtime(1), job.runtime(2)
    for rt, r in ((rt0, 0), (rt1, 1), (rt2, 2)):
        rt.init(r)
    rt0.send("world", 2, 1, "spec from zero")
    rt1.send("world", 2, 2, "review from one")
    rt2.recv("world", 0, 1, timeout=10)
    rt0.declare_failed(2, "died mid task")
    rt0.respawn(2)
    replay = rt0.replay_inbox(2)
    assert [m["src"] for m in replay] == [0, 1]
    assert [m["state"] for m in replay] == ["delivered", "posted"]


def test_checkpoint_and_restore(make_job):
    job = make_job(1)
    rt = job.runtime(0)
    rt.init(0)
    rt.checkpoint({"done": ["a", "b"], "next": "c"}, label="phase1")
    restored = rt.restore(label="phase1")
    assert restored is not None
    import json

    assert json.loads(restored["state"])["next"] == "c"


# ---------------------------------------------------------------------------
# Semantic operators
# ---------------------------------------------------------------------------


def test_semantic_reduction_suspends_and_resumes(make_job):
    """A semantic operator unwinds to the caller and resumes exactly.

    The rank playing the model here is a deterministic stub, so the test checks
    the *mechanism*: that the collective suspends, that the operands offered
    are the right ones, and that re-entering the identical call after
    submitting a result continues from the same step rather than restarting.
    """
    p = 4
    job = make_job(p)

    def body(rt, rank):
        upcalls = 0
        while True:
            try:
                result = coll.reduce_(rt, "world", 0, f"draft-{rank}", "AMPI_SYNTHESIZE",
                                      algo="linear", timeout=90)
            except SemanticUpcall as upcall:
                upcalls += 1
                merged = " + ".join(str(o) for o in upcall.operands)
                with rt.device.write_tx():
                    rt.device.execute(
                        "UPDATE pending_op SET state='done', result=? WHERE op_token=?",
                        (f'"{merged}"', upcall.op_token),
                    )
                continue
            return {"result": result["result"], "upcalls": upcalls}

    out = job.run_ranks(body, timeout=180)
    assert out[0]["upcalls"] == p - 1, "root evaluates p-1 operator steps in a linear reduction"
    assert out[0]["result"] == "draft-0 + draft-1 + draft-2 + draft-3"
    for rank in range(1, p):
        assert out[rank]["upcalls"] == 0, "non-root ranks never evaluate the operator"


def test_semantic_tree_reduction_has_logarithmic_depth(make_job):
    """Declaring a semantic operator associative buys a tree, and the tree's
    depth is what the harness pays for in wall-clock latency."""
    p = 8
    job = make_job(p)

    def body(rt, rank):
        upcalls = 0
        while True:
            try:
                result = coll.reduce_(rt, "world", 0, [f"note-{rank}"], "AMPI_SUMMARIZE",
                                      algo="binomial", timeout=120)
            except SemanticUpcall as upcall:
                upcalls += 1
                merged = [x for operand in upcall.operands for x in operand]
                import json as _json

                with rt.device.write_tx():
                    rt.device.execute(
                        "UPDATE pending_op SET state='done', result=? WHERE op_token=?",
                        (_json.dumps(merged), upcall.op_token),
                    )
                continue
            return {"result": result["result"], "upcalls": upcalls}

    out = job.run_ranks(body, timeout=240)
    total = sum(v["upcalls"] for v in out.values())
    assert total == p - 1, "a tree performs p-1 operator evaluations in total"
    assert out[0]["upcalls"] == 3, "but only lg p of them lie on the root's critical path"
    assert sorted(out[0]["result"]) == sorted(f"note-{i}" for i in range(p))


def test_failure_detector_widens_after_a_false_condemnation(make_job):
    """An eventually-perfect detector must stop arguing with a slow rank.

    A fixed timeout against heavy-tailed turn latency oscillates: condemn,
    retract, condemn again.  Each retraction must widen that rank's timeout so
    the detector converges on its actual latency instead.
    """
    job = make_job(2)
    rt = job.runtime(0)
    rt.init(0)
    rt.failure_timeout = 0.05
    rt.max_failure_timeout = 1e6

    victim = job.runtime(1)
    victim.init(1)
    victim.failure_timeout = 0.05

    import time

    time.sleep(0.1)
    assert 1 in rt.suspected()
    rt.declare_failed(1, "slow")
    assert rt.rank_row(1)["state"] == RANK_FAILED

    victim._touch()  # the "dead" rank speaks: direct evidence it is alive
    row = rt.rank_row(1)
    assert row["state"] == "alive"
    assert row["suspicions"] == 1
    assert row["retractions"] == 1

    # With one suspicion recorded the timeout has doubled, so the same pause
    # that condemned it before no longer does.
    time.sleep(0.06)
    assert 1 not in rt.suspected()


def test_declared_idle_period_only_extends_the_lease(make_job):
    """Declaring a quiet period must never make a rank easier to condemn.

    The first version honoured the declaration verbatim, so a rank that
    announced five minutes and then blocked for forty was condemned at minute
    five and stayed condemned however often it called the library. Declaring a
    short idle period was strictly worse than declaring none.
    """
    job = make_job(2)
    rt = job.runtime(0)
    rt.init(0)
    rt.failure_timeout = 30.0

    victim = job.runtime(1)
    victim.init(1)
    victim.heartbeat(expect_idle=0.05)  # a deliberately too-short declaration

    import time

    time.sleep(0.15)
    assert 1 not in rt.suspected(), (
        "a lapsed declaration must fall back to the heartbeat lease, not override it"
    )


def test_a_suspicion_does_not_fail_a_peers_send(make_job):
    """Only a confirmed death may fail another rank's operation.

    A timeout is a guess, and guesses about LLM ranks are usually wrong. If a
    guess could fail a send, one slow agent would be enough to revoke a
    communicator and lose the job -- which is exactly what happened before this
    distinction existed.
    """
    job = make_job(2)
    sender = job.runtime(0)
    sender.init(0)
    other = job.runtime(1)
    other.init(1)

    sender.declare_failed(1, "timed out", confirmed=False)
    result = sender.send("world", 1, 1, "this must still be accepted")
    assert result["msg_id"]

    sender.declare_failed(1, "administratively killed", confirmed=True)
    with pytest.raises(AmpiProcFailed):
        sender.send("world", 1, 1, "this must not be")


def test_a_returning_rank_clears_its_confirmation(make_job):
    job = make_job(2)
    rt = job.runtime(0)
    rt.init(0)
    victim = job.runtime(1)
    victim.init(1)
    rt.declare_failed(1, "timed out", confirmed=True)
    victim._touch()
    row = rt.rank_row(1)
    assert row["state"] == "alive"
    assert row["failure_confirmed"] == 0
