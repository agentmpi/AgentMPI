"""Failure detection, revoke/shrink/agree, replacement, and recovery.

Failures here are genuine: the simulator stops a rank's thread and deletes
its heartbeat without any graceful shutdown, so the survivors learn about it
only the way a real harness would, through the absence of evidence.
"""

from __future__ import annotations

import json
import time

import pytest

import agentmpi as ampi
from agentmpi import sim
from agentmpi.constants import RankState
from agentmpi.ft import RestartPolicy, Supervisor, comm_agree, comm_revoke, comm_shrink

FAST = {"ampi_heartbeat_s": 0.2, "ampi_failure_timeout_s": 2.0,
        "ampi_stall_timeout_s": 4.0, "ampi_gap_timeout_s": 2.0}


def test_failure_detector_finds_a_dead_rank():
    def body(comm):
        comm.barrier(timeout=30)
        deadline = time.time() + 12
        while time.time() < deadline:
            comm.runtime.heartbeat(force=True)
            comm.runtime.check_failures(comm)
            if comm.failed:
                return sorted(comm.failed)
            time.sleep(0.2)
        return sorted(comm.failed)

    r = sim.run(4, body, cvars=FAST, kill={2: 0.5}, timeout=60)
    survivors = [r.results.get(i) for i in (0, 1, 3)]
    assert all(s == [2] for s in survivors), survivors


def test_recv_from_a_dead_rank_times_out_rather_than_hanging():
    def body(comm):
        comm.barrier(timeout=30)
        if comm.rank == 0:
            with pytest.raises(ampi.TimeoutError_):
                comm.recv(1, 7, timeout=6)
            return "did-not-hang"
        return None

    r = sim.run(2, body, cvars=FAST, kill={1: 0.5}, timeout=60)
    assert r.results[0] == "did-not-hang"


def test_revoke_unblocks_a_waiting_rank():
    """Revocation must convert a permanent block into a recoverable error."""
    import threading

    def body(comm):
        comm.barrier(timeout=30)
        if comm.rank == 1:
            time.sleep(0.6)
            comm_revoke(comm)
            return "revoked"
        try:
            comm.recv(1, 3, timeout=20)
        except ampi.RevokedError:
            return "saw-revocation"
        except ampi.TimeoutError_:
            return "timed-out"
        return "received"

    r = sim.run(2, body, cvars=FAST, timeout=60)
    r.raise_errors()
    assert r.results[0] == "saw-revocation"


def test_agree_reaches_the_same_decision_everywhere():
    def body(comm):
        return comm_agree(comm, comm.rank % 2 == 0, op="ampi_lor", timeout=30)

    r = sim.run(5, body, cvars=FAST, timeout=60)
    r.raise_errors()
    assert set(r.ordered()) == {True}


def test_agree_converges_despite_a_failure():
    def body(comm):
        comm.barrier(timeout=30)
        time.sleep(3.0)  # let the detector notice rank 3
        comm.runtime.check_failures(comm)
        return comm_agree(comm, comm.rank, op="ampi_max", timeout=40)

    r = sim.run(4, body, cvars=FAST, kill={3: 0.4}, timeout=90)
    survivors = {i: r.results.get(i) for i in (0, 1, 2)}
    assert len(set(survivors.values())) == 1, survivors
    assert None not in survivors.values()


def test_shrink_produces_a_consistent_survivor_group():
    def body(comm):
        comm.barrier(timeout=30)
        time.sleep(3.0)
        comm.runtime.check_failures(comm)
        shrunk = comm_shrink(comm, timeout=40)
        return {"size": shrunk.size, "members": list(shrunk.group.members),
                "epoch": shrunk.epoch}

    r = sim.run(5, body, cvars=FAST, kill={4: 0.4}, timeout=90)
    results = [r.results.get(i) for i in range(4)]
    assert all(x is not None for x in results), results
    assert all(x["members"] == results[0]["members"] for x in results)
    assert 4 not in results[0]["members"]
    assert results[0]["size"] == 4
    assert results[0]["epoch"] == 1


def test_survivors_can_collectively_continue_after_a_shrink():
    def body(comm):
        comm.barrier(timeout=30)
        time.sleep(3.0)
        comm.runtime.check_failures(comm)
        shrunk = comm_shrink(comm, timeout=40)
        return shrunk.allreduce(1, ampi.SUM, timeout=40)

    r = sim.run(4, body, cvars=FAST, kill={3: 0.4}, timeout=120)
    for rank in (0, 1, 2):
        assert r.results.get(rank) == 3, f"rank {rank}: {r.results.get(rank)}"


def test_checkpoint_restore_roundtrip():
    from agentmpi.ft import checkpoint, restore

    def body(comm):
        checkpoint(comm, {"chapter": comm.rank, "phase": "drafted"})
        cp = restore(comm)
        return cp.state

    r = sim.run(3, body, root=None, timeout=30,
                device=sim.JournalDevice.__call__ if False else None)
    # The in-process device does not persist state across runtimes, so this
    # exercises the round-trip within a rank's lifetime.
    r.raise_errors()
    assert r.results[1]["chapter"] == 1


def test_with_retry_repairs_a_contract_violation():
    from agentmpi.ft import with_retry

    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        return {"answer": 1} if attempts["n"] >= 3 else {"wrong": 1}

    def validate(value):
        return () if "answer" in value else ("missing 'answer'",)

    got = with_retry(flaky, validate=validate, retries=3, backoff_s=0.01)
    assert got == {"answer": 1}
    assert attempts["n"] == 3


def test_with_retry_gives_up_and_reports_violations():
    from agentmpi.ft import with_retry

    with pytest.raises(ampi.ContractError) as excinfo:
        with_retry(lambda: {"bad": 1},
                   validate=lambda v: ("still wrong",), retries=1, backoff_s=0.01)
    assert "still wrong" in excinfo.value.violations


def test_supervisor_enforces_a_restart_budget():
    sup = Supervisor(RestartPolicy(max_restarts=2, within_s=60))
    assert sup.should_restart(3)
    sup.record(3)
    assert sup.should_restart(3)
    sup.record(3)
    assert not sup.should_restart(3)
    assert sup.should_restart(4)


def test_supervisor_strategies():
    ranks = [0, 1, 2, 3]
    assert Supervisor(RestartPolicy(strategy="one_for_one")).affected(2, ranks) == [2]
    assert Supervisor(RestartPolicy(strategy="one_for_all")).affected(2, ranks) == ranks
    assert Supervisor(RestartPolicy(strategy="rest_for_one")).affected(2, ranks) == [2, 3]


def test_restart_policy_backoff_is_exponential():
    policy = RestartPolicy(backoff_s=2.0, backoff_factor=3.0)
    assert policy.next_delay(1) == 2.0
    assert policy.next_delay(2) == 6.0
    assert policy.next_delay(3) == 18.0


def test_stale_epoch_messages_are_discarded():
    """After a shrink, pre-shrink traffic must not be delivered."""

    def body(comm):
        if comm.rank == 0:
            comm.send("stale", 1, 1)
            time.sleep(0.3)
            comm.runtime.matching.bump_epoch(comm.context, 5)
            return None
        time.sleep(0.6)
        comm.runtime.matching.bump_epoch(comm.context, 5)
        with pytest.raises(ampi.TimeoutError_):
            comm.recv(0, 1, timeout=2)
        return "discarded"

    r = sim.run(2, body, cvars=FAST, timeout=60)
    r.raise_errors()
    assert r.results[1] == "discarded"


def test_lock_lease_is_stolen_from_a_dead_holder(tmp_path):
    """A lock held by a dead agent must not wedge the run."""
    from agentmpi.transport import JournalDevice

    device = JournalDevice(tmp_path / "run", owner="rank0")
    held = device.lock("resource", lease_s=0.5, timeout_s=10)
    held.acquire()

    contender = JournalDevice(tmp_path / "run", owner="rank1")
    time.sleep(0.8)  # the lease expires without rank0 releasing
    second = contender.lock("resource", lease_s=5, timeout_s=10)
    second.acquire()
    assert second.stolen_from == "rank0"
    second.release()


def test_a_rank_that_never_joined_is_detected_on_the_roll_call_deadline(tmp_path):
    """A launcher shortfall must not cost a full failure timeout.

    Two distinct conditions look identical from a peer's view: a rank that
    registered and went quiet, and a rank that was never launched. The first
    may come back and deserves the generous failure timeout an agent's long
    turn requires. The second never will, and waiting for it is pure loss --
    we watched eight ranks block for twenty minutes on a broadcast whose root
    had not started, because a launcher silently fulfilled only part of a
    request. The roll-call deadline is short, separate, and measured from the
    run's creation so a late joiner does not restart everyone's clock.

    This is set up the way it actually happens: a manifest declaring four
    ranks, and a launcher that starts only one of them.
    """
    import json
    import os
    import subprocess
    import sys

    root = tmp_path / "run"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMPI_")}
    ampi = [sys.executable, "-m", "agentmpi.cli"]
    subprocess.run(
        ampi + ["init", "--root", str(root), "--ranks", "4",
                "--cvar", "ampi_roll_call_s=1",
                "--cvar", "ampi_failure_timeout_s=600"],
        check=True, capture_output=True, env=env, timeout=60)

    time.sleep(1.5)   # only rank 0 is ever launched
    proc = subprocess.run(
        ampi + ["ft", "detect"], capture_output=True, text=True, timeout=60,
        env={**env, "AMPI_ROOT": str(root), "AMPI_RANK": "0"})
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["failed"] == [1, 2, 3], (
        f"ranks that were never launched went unnoticed: {report}")


def test_a_registered_rank_that_goes_quiet_still_gets_the_failure_timeout(tmp_path):
    """The roll call must not shorten the deadline for a rank that did join."""
    import json
    import os
    import subprocess
    import sys

    root = tmp_path / "run"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMPI_")}
    ampi = [sys.executable, "-m", "agentmpi.cli"]
    subprocess.run(
        ampi + ["init", "--root", str(root), "--ranks", "4",
                "--cvar", "ampi_roll_call_s=0.1",
                "--cvar", "ampi_failure_timeout_s=600"],
        check=True, capture_output=True, env=env, timeout=60)

    # Every rank joins once and exits, so all four are registered and none
    # is heartbeating any more.
    for rank in range(4):
        subprocess.run(ampi + ["rank"], capture_output=True, timeout=60,
                       env={**env, "AMPI_ROOT": str(root), "AMPI_RANK": str(rank)})
    time.sleep(1.5)

    proc = subprocess.run(
        ampi + ["ft", "detect"], capture_output=True, text=True, timeout=60,
        env={**env, "AMPI_ROOT": str(root), "AMPI_RANK": "0"})
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["failed"] == [], (
        "a registered rank was declared dead on the roll-call deadline "
        f"instead of the failure timeout: {report}")
