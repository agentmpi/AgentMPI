"""Fault tolerance: detection, barrier policies, revoke/shrink/agree, supervision."""

from __future__ import annotations

import time

import pytest

import agentmpi as ampi
from agentmpi.constants import BarrierPolicy, FailureClass, RestartPolicy
from agentmpi.ft import shrink_in_place


def test_barrier_proceed_names_the_absentees(tmp_path):
    """A partial barrier: continue with whoever arrived, and say who did not.

    MPI's barrier has no such option, and its absence is why agent harnesses
    hang.  ``PROCEED`` is the policy a degradation-tolerant phase wants.
    """
    absentee = 2

    def rank_main(comm):
        if comm.rank == absentee:
            return "left early"
        res = comm.barrier(timeout=2.0, policy=BarrierPolicy.PROCEED, label="phase1")
        assert not res.complete
        assert absentee in res.absent, res
        return res.absent

    job = ampi.launch(rank_main, size=4, root=tmp_path / "pb")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert job.value(0) == (absentee,)


def test_barrier_raise_reports_timeout(tmp_path):
    def rank_main(comm):
        if comm.rank == 1:
            return None
        with pytest.raises(ampi.AmpiTimeout):
            comm.barrier(timeout=1.0, policy=BarrierPolicy.RAISE)
        return "raised"

    job = ampi.launch(rank_main, size=3, root=tmp_path / "br")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_revoke_releases_blocked_peers(tmp_path):
    """The primitive whose necessity is least obvious.

    Rank 1 never arrives.  Rank 0 notices and revokes.  Ranks 2 and 3, already
    blocked inside a receive, must be released with ``ERR_REVOKED`` rather than
    waiting forever.  Without ``revoke`` the discovering rank has no way to free
    the others, because they are all waiting on the dead peer.
    """

    def rank_main(comm):
        if comm.rank == 1:
            return "gone"
        if comm.rank == 0:
            time.sleep(0.5)
            ampi.declare_failed(comm, 1, kind=FailureClass.FAIL_STOP, detail="test")
            ampi.revoke(comm)
            return "revoked"
        with pytest.raises(ampi.AmpiRevoked):
            comm.recv(source=1, tag="never", timeout=30.0)
        return "released"

    job = ampi.launch(rank_main, size=4, root=tmp_path / "rv")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert job.value(2) == "released" and job.value(3) == "released"


def test_shrink_builds_a_survivor_communicator(tmp_path):
    dead = 3

    def rank_main(comm):
        if comm.rank == dead:
            return "gone"
        if comm.rank == 0:
            ampi.declare_failed(comm, dead, kind=FailureClass.FAIL_STOP, detail="test")
        comm.barrier(timeout=3.0, policy=BarrierPolicy.PROCEED, label="detect")
        new = ampi.shrink(comm)
        assert new.size == comm.size - 1, (new.size, comm.size)
        assert dead not in new.members
        total = new.allreduce(1, ampi.SUM)
        assert total == new.size
        return {"size": new.size, "members": list(new.members), "total": total}

    job = ampi.launch(rank_main, size=5, root=tmp_path / "sh")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    out = job.value(0)
    assert out["size"] == 4 and out["total"] == 4


def test_shrink_in_place_keeps_rank_identity(tmp_path):
    """FT-MPI's BLANK mode: exclude the dead, do not renumber the living.

    Preferred when a rank's identity is baked into the work assignment, which is
    the normal case for agents ("rank 7 owns the parser").
    """
    dead = 1

    def rank_main(comm):
        if comm.rank == dead:
            return "gone"
        if comm.rank == 0:
            ampi.declare_failed(comm, dead, kind=FailureClass.FAIL_STOP)
        res = comm.barrier(timeout=3.0, policy=BarrierPolicy.SHRINK, label="integrate")
        assert dead in res.absent
        comm.refresh()
        return {"members": list(comm.members), "gen": comm.generation}

    job = ampi.launch(rank_main, size=4, root=tmp_path / "sip")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    out = job.value(0)
    assert out["members"] == [0, 1, 2, 3], "world ranks must stay stable"
    assert out["gen"] >= 1


def test_agree_reaches_a_consistent_decision(tmp_path):
    def rank_main(comm):
        vote = comm.rank != 2
        return ampi.agree(comm, vote, timeout=15.0)

    job = ampi.launch(rank_main, size=5, root=tmp_path / "ag")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    results = [o.value for o in job.outcomes]
    assert all(r is False for r in results), results


def test_agree_excludes_failed_ranks(tmp_path):
    """``agree`` must reach a decision over the survivors once a rank is known failed.

    Ranks 1 and 2 wait for the declaration to become visible before voting. Without that they
    race rank 0: whichever of them enters ``agree`` first may still see rank 3 as live, wait for
    a vote that never comes, and return False on timeout --- so the test failed intermittently for
    a reason that had nothing to do with what it was checking. The wait makes the precondition
    explicit rather than assumed, which is the difference between testing ``agree`` and testing
    the scheduler.

    The wait is on the ``failures`` table via ``get_failed``, not on the rank's state, because
    ``declare_failed`` will not overwrite a ``finalized`` state --- and rank 3 here returns
    immediately, so it usually *has* finalized by then. Polling the state would therefore hang on
    a condition that never becomes true even though the declaration succeeded, which is a neat
    illustration of why the two are recorded separately: the failure record is the communicator's
    view of a peer, and the rank state is that peer's own lifecycle.
    """
    failed_rank = 3

    def rank_main(comm):
        if comm.rank == failed_rank:
            return "gone"
        if comm.rank == 0:
            ampi.declare_failed(comm, failed_rank, kind=FailureClass.FAIL_STOP)
        else:
            deadline = time.time() + 60.0
            while time.time() < deadline:
                if failed_rank in ampi.get_failed(comm):
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("the failure declaration never became visible")
        # Generous, because the assertion is about the decision `agree` reaches and not about how
        # quickly it reaches it. A tight bound turns ordinary contention from the rest of the suite
        # into a false failure, which is what happened with 15 s: the test passed alone and failed
        # under load, the least useful shape a test can have.
        return ampi.agree(comm, True, timeout=60.0)

    job = ampi.launch(rank_main, size=4, root=tmp_path / "ag2")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert all(o.value is True for o in job.outcomes if o.rank != failed_rank)


def test_health_detects_lease_expiry(tmp_path):
    fabric = ampi.create_job(tmp_path / "h", 3)
    session = ampi.init(tmp_path / "h", rank=0, size=3, lease_seconds=0.1)
    time.sleep(0.3)
    reports = {r.rank: r for r in ampi.health(fabric)}
    assert reports[0].suspected is FailureClass.FAIL_STOP
    session.rt.heartbeat()
    reports = {r.rank: r for r in ampi.health(fabric)}
    assert reports[0].suspected is None
    session.finalize()


def test_replicate_and_compare_detects_disagreement():
    """n-modular redundancy: the only defence against a plausible-but-wrong answer."""
    agree = ampi.replicate_and_compare(["A", "A", "A"], key=lambda x: x)
    assert agree.agreed and agree.consensus == 1.0 and agree.n_distinct == 1

    split = ampi.replicate_and_compare(["A", "B", "A"], key=lambda x: x)
    assert not split.agreed
    assert split.n_distinct == 2
    assert split.chosen == "A"
    assert abs(split.consensus - 2 / 3) < 1e-9

    # The key projection is what makes comparison meaningful for prose: two
    # differently-worded but equivalent answers must compare equal.
    normalised = ampi.replicate_and_compare(
        ["Result: 42.", "the result is 42", "42"], key=lambda s: "".join(c for c in s if c.isdigit())
    )
    assert normalised.agreed and normalised.n_distinct == 1


def test_verify_orders_validators_by_cost():
    calls: list[str] = []

    def schema_check(_payload):
        calls.append("schema")
        return True, ""

    def oracle_check(_payload):
        calls.append("oracle")
        return False, "wrong answer"

    cheap = ampi.Validator(name="schema", fn=schema_check, cost_tokens=0)
    expensive = ampi.Validator(name="oracle", fn=oracle_check, cost_tokens=5000, classifies="fail_plausible")
    ok, cls, detail = ampi.verify({"x": 1}, [expensive, cheap])
    assert calls == ["schema", "oracle"], calls
    assert not ok and cls is FailureClass.FAIL_PLAUSIBLE and "wrong answer" in detail


def test_supervisor_restart_policies(tmp_path):
    fabric = ampi.create_job(tmp_path / "sup", 4)
    started: list[int] = []
    sup = ampi.Supervisor(fabric=fabric, policy=RestartPolicy.ONE_FOR_ONE, max_restarts=10)
    for r in range(4):
        sup.add(ampi.ChildSpec(rank=r, start=lambda inc, r=r: started.append(r), order=r))

    assert sup.handle_failure(2, kind=FailureClass.FAIL_STOP) == [2]
    assert started == [2]

    started.clear()
    sup.policy = RestartPolicy.ONE_FOR_ALL
    assert sup.handle_failure(2, kind=FailureClass.FAIL_STOP) == [0, 1, 2, 3]

    started.clear()
    sup.policy = RestartPolicy.REST_FOR_ONE
    assert sup.handle_failure(2, kind=FailureClass.FAIL_STOP) == [2, 3]


def test_supervisor_escalates_past_restart_intensity(tmp_path):
    fabric = ampi.create_job(tmp_path / "sup2", 2)
    sup = ampi.Supervisor(fabric=fabric, max_restarts=2, max_seconds=60.0)
    sup.add(ampi.ChildSpec(rank=0, start=lambda inc: None))
    sup.handle_failure(0, kind=FailureClass.FAIL_STOP)
    sup.handle_failure(0, kind=FailureClass.FAIL_STOP)
    with pytest.raises(ampi.AmpiProcFailed):
        sup.handle_failure(0, kind=FailureClass.FAIL_STOP)


def test_straggler_threshold_is_robust_to_the_tail():
    from agentmpi.ft import straggler_threshold

    normal = [10.0, 11.0, 12.0, 10.5, 11.5, 9.5, 10.2, 11.1]
    thr = straggler_threshold(normal, k=2.0)
    assert 11.0 < thr < 15.0, thr
    assert thr < 60.0, "a single outlier must not inflate the threshold"
    with_outlier = [*normal, 300.0]
    assert straggler_threshold(with_outlier, k=2.0) < 100.0


def test_runtime_version_mismatch_is_traced(tmp_path, monkeypatch):
    """A population split across two runtime builds must be visible in the trace.

    The protocol keeps its *state* outside the agents and durable, and says nothing
    about the runtime *code*, which is shared mutable state. Editing an editable
    install while a live population executes against it puts half the ranks on a
    different build, and the resulting failures look like heisenbugs. A mismatch is
    traced rather than raised, because refusing to start would strand a population
    mid-run over what is usually a benign upgrade -- but it must not be silent.
    """
    import agentmpi.rank as rank_mod

    original = rank_mod.RUNTIME_VERSION
    fabric = ampi.create_job(tmp_path / "v", 2)
    s1 = ampi.init(tmp_path / "v", rank=0, size=2)
    assert fabric.get_meta("runtime_version") == original
    s1.finalize()
    assert not fabric.events(kinds=["rank.version_mismatch"])

    monkeypatch.setattr(rank_mod, "RUNTIME_VERSION", "9.9.9+schema99")
    s2 = ampi.init(tmp_path / "v", rank=1, size=2)
    s2.finalize()
    events = fabric.events(kinds=["rank.version_mismatch"])
    assert len(events) == 1, events
    assert events[0]["payload"]["worker_version"] == "9.9.9+schema99"
    assert events[0]["payload"]["job_version"] == original


def test_registration_is_refused_outside_the_world(tmp_path):
    """A rank index the job never declared must not be able to join.

    Registration previously accepted any index unconditionally, and a shared worker pool leaked
    ranks into three translation runs: the world-2 run ended with rank rows for 2--8 and 14, two
    of them still renewing leases half an hour after the job had finished. Collectives were
    unaffected because they resolve through `comm_members`, so nothing failed loudly and the leak
    was only visible by counting rows in the fabric.
    """
    root = tmp_path / "world"
    job = ampi.launch(lambda comm: comm.rank, size=2, root=root)
    assert job.ok

    fabric = ampi.Fabric(root)
    assert fabric.get_meta("world_size") == "2"

    stray = ampi.RankRuntime(fabric=fabric, wrank=7)
    with pytest.raises(ampi.AmpiUsageError):
        stray.register(executor_name="worker")

    rows = fabric.query("SELECT rank FROM ranks ORDER BY rank")
    assert [int(r["rank"]) for r in rows] == [0, 1], "a refused registration must leave no row"


def test_registration_inside_the_world_still_reattaches(tmp_path):
    """The check must not break the mechanism it sits next to.

    A rank is a durable role and a fresh agent taking it over is the normal case, so an in-range
    index must still be accepted and must bump the incarnation rather than be rejected as a
    duplicate.
    """
    root = tmp_path / "reattach"
    job = ampi.launch(lambda comm: comm.rank, size=2, root=root)
    assert job.ok

    fabric = ampi.Fabric(root)
    again = ampi.RankRuntime(fabric=fabric, wrank=1)
    again.register(executor_name="worker")
    row = fabric.query_one("SELECT incarnation FROM ranks WHERE rank=1")
    assert int(row["incarnation"]) > 1, "reattaching must bump the incarnation"


def test_a_send_to_a_dead_rank_is_recorded_as_orphaned(tmp_path):
    """A message nobody will ever read must be visible as such in the trace.

    This is the p=16 translation failure in miniature. Three ranks whose agents never arrived
    eventually produced degraded contributions and sent them into the mailboxes of peers that had
    abandoned the collective 3412 s earlier. The sends succeeded, the messages were never received,
    and nothing in the log said so -- the fact had to be recovered by differencing 27 sends against
    24 receives and rebuilding the reduction tree by hand.

    The condition is cheap and entirely local: at delivery time the fabric knows the destination's
    state. It needs no reasoning about timeouts or configuration, and it generalises past this
    cause, because any degraded contribution can arrive after its group has moved on.
    """
    def rank_main(comm):
        if comm.rank == 1:
            return "left early"
        # Wait until rank 1 has finalised, then send to it anyway.
        deadline = time.time() + 15.0
        while time.time() < deadline:
            row = comm.fabric.query_one("SELECT state FROM ranks WHERE rank=1")
            if row is not None and row["state"] == "finalized":
                break
            time.sleep(0.02)
        else:
            raise AssertionError("rank 1 never finalised")
        comm.send("too late", 1, "late")
        return "sent"

    root = tmp_path / "orphan"
    job = ampi.launch(rank_main, size=2, root=root)
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]

    events = ampi.Fabric(root).events()
    orphans = [e for e in events if e["kind"] == "msg.orphaned"]
    assert len(orphans) == 1, [e["kind"] for e in events]
    payload = orphans[0]["payload"]
    assert payload["wdst"] == 1
    assert payload["dst_state"] == "finalized"
    assert payload["tag"] == "late"
    assert payload["tokens"] > 0


def test_ordinary_traffic_is_never_marked_orphaned(tmp_path):
    """The detector must not fire on a healthy run, or it is noise rather than a signal."""
    def rank_main(comm):
        if comm.rank == 0:
            comm.send("hello", 1, "greet")
        else:
            assert comm.recv(source=0, tag="greet", timeout=15.0).payload == "hello"
        comm.barrier(policy="wait")
        return True

    root = tmp_path / "healthy"
    job = ampi.launch(rank_main, size=2, root=root)
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert [e for e in ampi.Fabric(root).events() if e["kind"] == "msg.orphaned"] == []


def test_in_place_shrink_reaches_one_generation_across_all_survivors(tmp_path):
    """Every survivor calls shrink_in_place, so it must converge on one generation.

    The generation was incremented per call. Because each rank runs it independently, seven
    survivors drove a communicator from generation 0 to 7 and each cached a different value --- and
    since a collective is keyed on (ctx, generation, epoch, op), the `agree` that followed split
    into six collectives of one or two voters, none reached quorum, and every rank blocked to the
    timeout. That was the whole observed cost of the shrink barrier policy.
    """
    size = 6
    dead = 5

    def rank_main(comm):
        if comm.rank == dead:
            return "gone"
        ampi.declare_failed(comm, dead, kind=FailureClass.FAIL_STOP)
        shrink_in_place(comm, [dead])
        return comm.generation

    root = tmp_path / "shrink-gen"
    job = ampi.launch(rank_main, size=size, root=root)
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]

    generations = {o.value for o in job.outcomes if o.rank != dead}
    assert generations == {1}, f"survivors disagree on the generation: {sorted(generations)}"

    fabric = ampi.Fabric(root)
    row = fabric.query_one("SELECT generation FROM comms WHERE ctx=0")
    assert int(row["generation"]) == 1, f"communicator generation is {row['generation']}, not 1"


def test_in_place_shrink_is_idempotent(tmp_path):
    """Calling it twice for the same departure must not advance the generation again."""
    def rank_main(comm):
        if comm.rank == 3:
            return "gone"
        ampi.declare_failed(comm, 3, kind=FailureClass.FAIL_STOP)
        shrink_in_place(comm, [3])
        shrink_in_place(comm, [3])
        return comm.generation

    root = tmp_path / "shrink-idem"
    job = ampi.launch(rank_main, size=4, root=root)
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert {o.value for o in job.outcomes if o.rank != 3} == {1}
