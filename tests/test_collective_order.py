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
