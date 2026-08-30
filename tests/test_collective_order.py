"""Detection of a desynchronised collective sequence.

This test exists because we observed the failure in a live run. A translator
agent decided that receiving its work assignment via ``ampi scatter`` was
unnecessary, inferred the assignment instead (incorrectly), and moved on.
Its collective counter was then one behind every peer's, so every subsequent
collective it issued carried a tag nobody was listening for. Eleven ranks
blocked forever, each of them correctly waiting for a message that was never
going to be sent, and nothing in any rank's local state explained why.

MPI has the same hazard and treats it as a programmer error, because in MPI
the caller is a compiled program that either contains the call or does not.
When the caller is a language model, a skipped collective is an ordinary
runtime event, and the protocol has to turn the hang into a diagnosis.
"""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi import sim
from agentmpi.errors import CollectiveMismatchError

FAST = {"ampi_heartbeat_s": 0.2, "ampi_failure_timeout_s": 600.0,
        "ampi_gap_timeout_s": 600.0}


def test_skipped_collective_is_diagnosed_not_deadlocked():
    """One rank skips a barrier; the others must report, not hang."""

    def body(comm):
        comm.bcast("spec" if comm.rank == 0 else None, 0, timeout=30)
        if comm.rank != 2:
            # Every rank but 2 performs a barrier here.
            try:
                comm.barrier(timeout=25)
            except CollectiveMismatchError as exc:
                return ("mismatch", comm.rank, exc.context.get("local_op"),
                        exc.context.get("peer_op"),
                        exc.context.get("local_is_minority"))
            except ampi.TimeoutError_:
                return ("timeout", comm.rank, None, None, None)
        # ... and then everyone tries an allreduce.
        try:
            comm.allreduce(1, ampi.SUM, timeout=25)
        except CollectiveMismatchError as exc:
            return ("mismatch", comm.rank, exc.context.get("local_op"),
                    exc.context.get("peer_op"),
                    exc.context.get("local_is_minority"))
        except ampi.TimeoutError_:
            return ("timeout", comm.rank, None, None, None)
        return ("completed", comm.rank, None, None, None)

    r = sim.run(4, body, cvars=FAST, timeout=180)
    outcomes = [r.results.get(i) for i in range(4)]
    kinds = [o[0] if o else "crashed" for o in outcomes]

    # Somebody must name the problem rather than merely time out.
    assert "mismatch" in kinds, f"no rank diagnosed the desynchronisation: {outcomes}"
    diagnosed = [o for o in outcomes if o and o[0] == "mismatch"]
    for _, rank, local_op, peer_op, minority in diagnosed:
        assert local_op != peer_op
        assert {local_op, peer_op} == {"barrier", "allreduce"}, (rank, local_op, peer_op)
        # Rank 2 is the one that skipped, so it -- and only it -- should be
        # told that it is in the minority and must resynchronise.
        if minority:
            assert rank == 2, f"rank {rank} wrongly told it was the deviant"

    assert any(o[0] == "mismatch" and o[1] == 2 and o[4] for o in diagnosed), (
        f"the skipping rank was never identified as the minority: {diagnosed}")


def test_matched_collective_sequences_do_not_false_positive():
    """A rank merely running ahead is legal and must not be reported."""
    import time

    def body(comm):
        if comm.rank == 1:
            time.sleep(0.5)          # rank 1 lags; rank 0 gets ahead
        comm.bcast("a" if comm.rank == 0 else None, 0, timeout=30)
        comm.allreduce(comm.rank, ampi.SUM, timeout=30)
        comm.barrier(timeout=30)
        return "ok"

    r = sim.run(4, body, cvars=FAST, timeout=120)
    r.raise_errors()
    assert all(v == "ok" for v in r.ordered())


def test_collective_log_is_bounded():
    """The diagnosis log must not grow with the number of collectives."""

    def body(comm):
        for _ in range(80):
            comm.barrier(timeout=30)
        return len(comm.runtime.coll_log.get(comm.context, {}))

    r = sim.run(2, body, cvars=FAST, timeout=180)
    r.raise_errors()
    for size in r.ordered():
        assert size <= 64, f"collective log grew to {size}"


def test_collective_counter_is_durable_before_the_collective(tmp_path):
    """A rank killed mid-collective must not replay it.

    The counter that separates one collective's traffic from the next must be
    persisted before the messages it labels are sent. If it is written only
    at exit, a process killed part way through a collective -- an agent's
    shell timing out is the common cause -- reuses the same number in its
    next process, replays a collective its peers have already completed, and
    is one behind them from then on.
    """
    import json
    import os
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor

    root = tmp_path / "run"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMPI_")}
    ampi = [sys.executable, "-m", "agentmpi.cli"]
    subprocess.run(ampi + ["init", "--root", str(root), "--ranks", "2"],
                   check=True, capture_output=True, env=env, timeout=60)

    def call(rank: int, *args: str, timeout: float = 30):
        return subprocess.run(
            ampi + list(args), capture_output=True, text=True, timeout=timeout,
            env={**env, "AMPI_ROOT": str(root), "AMPI_RANK": str(rank)})

    # Rank 0 enters a barrier that can never complete and is killed. Its
    # counter increment must already be on disk.
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(call, 0, "barrier", "--timeout", "2")
        fut.result()

    state = json.loads((root / "kv" / "pstate%2f0").read_text())
    assert state["coll_counter"]["world"] == 1, (
        "the collective counter was not durable across the killed process")
    assert state["coll_log"]["world"]["1"] == "barrier"

    # The rank's next process must therefore issue collective #2, not #1.
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(call, 0, "barrier", "--timeout", "20")
        b = pool.submit(call, 1, "barrier", "--timeout", "20")
        b.result()
        a.result()
    after = json.loads((root / "kv" / "pstate%2f0").read_text())
    assert after["coll_counter"]["world"] == 2


def test_state_from_a_recycled_run_directory_is_discarded(tmp_path):
    """A rank must not inherit an earlier run's counters from the same path.

    Epoch numbers protect against stale messages within a run; they do
    nothing when the run *directory* is recycled. A rank joining a freshly
    created run at a path an earlier run used would otherwise inherit the
    earlier run's collective counter, number every collective one too high,
    and match the wrong message -- a broadcast returning a scatter's payload,
    which surfaces as a type error a long way from its cause. MPI never has
    to consider this because a job's transport is process-scoped and cannot
    outlive the job; a directory can.
    """
    import json
    import os
    import subprocess
    import sys

    root = tmp_path / "run"
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMPI_")}
    ampi = [sys.executable, "-m", "agentmpi.cli"]

    def call(rank, *args, timeout=30):
        return subprocess.run(ampi + list(args), capture_output=True, text=True,
                              timeout=timeout,
                              env={**env, "AMPI_ROOT": str(root),
                                   "AMPI_RANK": str(rank)})

    # First run: rank 0 abandons a barrier, leaving a durable counter of 1.
    subprocess.run(ampi + ["init", "--root", str(root), "--ranks", "2",
                           "--label", "first"],
                   check=True, capture_output=True, env=env, timeout=60)
    call(0, "barrier", "--timeout", "2")
    assert json.loads((root / "kv" / "pstate%2f0").read_text())["coll_counter"]["world"] == 1

    # The directory is recycled for a different run, as ours was.
    import shutil

    shutil.rmtree(root / "inbox", ignore_errors=True)
    subprocess.run(ampi + ["init", "--root", str(root), "--ranks", "2",
                           "--label", "second"],
                   check=True, capture_output=True, env=env, timeout=60)

    # Rank 0's first collective in the new run must be #1, not #2.
    call(0, "barrier", "--timeout", "2")
    state = json.loads((root / "kv" / "pstate%2f0").read_text())
    assert state["run_id"] == "second"
    assert state["coll_counter"]["world"] == 1, (
        "the rank inherited a recycled run's collective counter")


def test_cli_reports_a_deleted_run_instead_of_crashing(tmp_path):
    """A rank that outlives its run must diagnose it, not traceback."""
    import json
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if not k.startswith("AMPI_")}
    missing = tmp_path / "gone"
    missing.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "agentmpi.cli", "barrier"],
        capture_output=True, text=True, timeout=60,
        env={**env, "AMPI_ROOT": str(missing), "AMPI_RANK": "0"})
    assert proc.returncode != 0
    payload = json.loads((proc.stdout + proc.stderr).strip().splitlines()[-1])
    assert payload["error"] == "ERR_NO_RUN"
    assert "deleted" in payload["message"] or "does not exist" in payload["message"]
