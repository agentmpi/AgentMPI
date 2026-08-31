"""Tests for the static harness analyses.

Each case here is a real bug shape observed in a multi-agent run, reduced to the
smallest program that exhibits it.  A checker that catches these catches the class.
"""

from __future__ import annotations

from ampi.core.safety import (
    Coll,
    Local,
    Program,
    Recv,
    Send,
    analyse,
    check_collective_agreement,
    check_context_safety,
    peak_residency,
)


def test_pairwise_exchange_with_receive_first_is_safe():
    p = Program(2)
    p.rank(0, Send(1, tokens=100), Recv(1))
    p.rank(1, Recv(0), Send(0, tokens=100))
    assert check_context_safety(p).safe


def test_ring_where_every_rank_sends_first_is_unsafe():
    """The textbook unsafe program, and the one agent harnesses write by default.

    Every rank pushes its draft to its neighbour, then reads its neighbour's.  It
    works in testing at p=2 and degrades invisibly at p=32, because the thing
    absorbing the unmatched messages is each executor's context window.
    """
    n = 8
    p = Program(n)
    for r in range(n):
        p.rank(r, Send((r + 1) % n, tokens=4000), Recv((r - 1) % n))
    report = check_context_safety(p)
    assert not report.safe
    assert len(report.cycle) == n, "the whole ring is the cycle"
    assert "rendezvous" in report.repair
    assert report.completed == 0


def test_the_ring_is_repaired_by_declaring_rendezvous():
    n = 8
    p = Program(n)
    for r in range(n):
        p.rank(r, Send((r + 1) % n, mode="rendezvous", tokens=4000), Recv((r - 1) % n))
    assert check_context_safety(p).safe


def test_odd_even_ordering_is_safe_without_rendezvous():
    n = 8
    p = Program(n)
    for r in range(n):
        peer = r + 1 if r % 2 == 0 else r - 1
        if r % 2 == 0:
            p.rank(r, Send(peer, tokens=4000), Recv(peer))
        else:
            p.rank(r, Recv(peer), Send(peer, tokens=4000))
    assert check_context_safety(p).safe


def test_conditional_send_with_unconditional_receive_deadlocks():
    """Lesson L14, observed in a live run.

    Rank 1 only sends when it has something to say; rank 0 always waits.  The
    protocol can only bound the damage with a timeout, so the check has to catch
    it statically, and the advice is the one the harness author needs: send
    unconditionally, possibly empty.
    """
    p = Program(2)
    p.rank(0, Recv(1))
    p.rank(1, Local("decided it had nothing to report"))
    report = check_context_safety(p)
    assert not report.safe
    assert "send unconditionally" in report.repair


def test_a_rank_that_skips_a_collective_is_named():
    p = Program(4)
    for r in range(4):
        if r == 2:
            p.rank(r, Local("crashed out of its own main function"))
        else:
            p.rank(r, Coll("phase-1"))
    report = check_context_safety(p)
    assert not report.safe
    assert "[2]" in report.repair
    assert "phase-1" in report.repair


def test_ranks_in_different_collectives_are_diagnosed_by_label():
    p = Program(4)
    for r in range(4):
        p.rank(r, Coll("review" if r < 3 else "merge"))
    report = check_context_safety(p)
    assert not report.safe
    assert "disagree about which collective" in report.repair
    assert "'merge'" in report.repair and "'review'" in report.repair


def test_collective_agreement_catches_a_reordering_before_it_runs():
    p = Program(3)
    p.rank(0, Coll("draft"), Coll("review"))
    p.rank(1, Coll("draft"), Coll("review"))
    p.rank(2, Coll("review"), Coll("draft"))  # an executor that reordered two steps
    report = check_collective_agreement(p)
    assert not report.safe
    assert 2 in report.blocked


def test_collective_agreement_passes_a_well_formed_harness():
    p = Program(4)
    for r in range(4):
        p.rank(r, Coll("draft"), Coll("glossary", kind="allreduce"), Coll("done"))
    assert check_collective_agreement(p).safe


def test_sub_communicator_members_are_the_only_required_participants():
    p = Program(6).comm("team", [0, 1, 2])
    for r in (0, 1, 2):
        p.rank(r, Coll("team-sync", comm="team"))
    for r in (3, 4, 5):
        p.rank(r, Local("working alone"))
    assert check_context_safety(p).safe
    assert check_collective_agreement(p).safe


def test_peak_residency_counts_eager_bodies_and_handles_differently():
    p = Program(3)
    p.rank(0, Send(1, tokens=4000), Send(2, tokens=4000, mode="rendezvous"))
    p.rank(1, Recv(0))
    p.rank(2, Recv(0))
    peak = peak_residency(p)
    assert peak[1] == 4000, "an eager body is charged in full"
    assert peak[2] == 40, "a rendezvous delivery charges only the envelope"


def test_analyse_reports_infeasibility_against_a_budget():
    """The allgather that kills naive harnesses, caught before it is paid for."""
    n = 16
    p = Program(n)
    for r in range(n):
        for other in range(n):
            if other != r:
                p.rank(r, Send(other, mode="rendezvous", tokens=4000))
        p.rank(r, Coll("allgather", kind="allgather", tokens_out=(n - 1) * 4000))
    report = analyse(p, budget=32_000)
    assert not report["feasible"]
    assert report["peak_residency_max"] >= 15 * 4000


def test_analyse_accepts_a_handle_based_allgather():
    n = 16
    p = Program(n)
    for r in range(n):
        p.rank(r, Coll("allgather", kind="allgather", tokens_out=(n - 1) * 40))
    report = analyse(p, budget=32_000)
    assert report["ok"] and report["feasible"]
