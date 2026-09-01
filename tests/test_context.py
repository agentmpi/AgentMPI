"""Context safety: the AgentMPI analogue of MPI's "safe program" discipline.

MPI's standard says a program that depends on buffering is *unsafe*: it may work
on one implementation and deadlock on another, and the standard declines to
guarantee it.  The canonical unsafe program is a cycle of blocking sends posted
before the matching receives.

AgentMPI has the same discipline with a different scarce resource.  An all-eager
program depends on the receiver's unexpected-message budget, which is a proxy for
its context window.  The tests here establish the three claims the paper makes:

1. An all-eager exchange cycle stalls once the aggregate payload exceeds the
   receivers' budgets, and the stall is *reported* rather than silent.
2. The same program with rendezvous transport completes, because only handles are
   in flight.
3. ``probe``-then-``View`` lets a rank stay inside a budget far smaller than the
   artifacts it processes.
"""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi.constants import Mode


def _big(tokens: int) -> str:
    return "word " * tokens


def test_all_eager_cycle_stalls_and_is_attributed(tmp_path):
    """The unsafe program: every rank sends before any rank receives."""
    payload = _big(3000)

    def rank_main(comm):
        p, r = comm.size, comm.rank
        with pytest.raises(ampi.AmpiContextOverflow):
            comm.send(payload, (r + 1) % p, "cycle", mode=Mode.EAGER, timeout=3.0)
        return "stalled"

    job = ampi.launch(
        rank_main,
        size=4,
        root=tmp_path / "unsafe",
        eager_limit=1_000_000,      # allow the mode, so the *budget* is what bites
        unexpected_limit=2000,      # smaller than one payload
    )
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert all(o.value == "stalled" for o in job.outcomes)


def test_eager_backpressure_stalls_then_recovers(tmp_path):
    """Back-pressure, not failure: a receiver draining its mailbox unblocks the sender."""
    n_msgs = 6
    payload = _big(500)

    def rank_main(comm):
        if comm.rank == 0:
            for i in range(n_msgs):
                comm.send(payload, 1, f"m{i}", mode=Mode.EAGER, timeout=30.0)
            return "all sent"
        got = 0
        for i in range(n_msgs):
            msg = comm.recv(source=0, tag=f"m{i}", timeout=30.0, admit=False)
            got += msg.tokens
        return got

    job = ampi.launch(
        rank_main, size=2, root=tmp_path / "bp", eager_limit=1_000_000, unexpected_limit=1200
    )
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert job.value(0) == "all sent"
    fabric = ampi.Fabric(tmp_path / "bp")
    stalls = fabric.events(kinds=["transport.credit_stall"])
    grants = fabric.events(kinds=["transport.credit_granted"])
    assert stalls, "the sender should have had to wait for credit"
    assert len(grants) == len(stalls), "every stall must be followed by a grant"


def test_rendezvous_makes_the_same_program_safe(tmp_path):
    """The safe formulation: exchange handles, materialise on demand."""
    payload = _big(3000)

    def rank_main(comm):
        p, r = comm.size, comm.rank
        comm.send(payload, (r + 1) % p, "cycle", mode=Mode.RENDEZVOUS, timeout=30.0)
        msg = comm.recv(source=(r - 1) % p, tag="cycle", timeout=30.0)
        assert msg.payload is None
        assert comm.rt.context.used == 0
        head = comm.fetch(msg, view=ampi.View.head(100))
        assert comm.rt.context.used <= 120
        return len(head)

    job = ampi.launch(
        rank_main, size=4, root=tmp_path / "safe", unexpected_limit=2000, context_budget=4000
    )
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_gather_of_large_artifacts_does_not_overflow_the_root(tmp_path):
    """The most common fan-in mistake, and the protocol's answer to it.

    Sixteen ranks each produce a 4k-token artifact.  Materialising all sixteen at
    the root needs 64k tokens; the root here has 8k.  Because ``gather`` defaults
    to ``admit=False`` and AUTO transport, the root receives handles and stays in
    budget while still being able to reduce over narrow views.
    """
    p = 16

    def rank_main(comm):
        mine = f"section {comm.rank}: " + _big(4000)
        handles = comm.gather(mine, root=0)
        if comm.rank != 0:
            return None
        assert handles is not None and len(handles) == p
        assert comm.rt.context.used == 0, comm.rt.context.snapshot()
        # Reduce over first lines only: p * ~10 tokens, comfortably in budget.
        heads = [h.splitlines()[0][:60] for h in handles]
        assert len(heads) == p
        return comm.rt.context.snapshot()

    job = ampi.launch(
        rank_main, size=p, root=tmp_path / "fanin", context_budget=lambda r: 8000 if r == 0 else 128_000
    )
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert job.value(0)["rejections"] == 0


def test_view_projections_are_the_derived_datatype_analogue(tmp_path):
    """A strided view gives rank r every p-th element without moving the rest."""
    doc = "\n\n".join(f"chapter {i}" for i in range(24))

    def rank_main(comm):
        if comm.rank == 0:
            for d in range(1, comm.size):
                comm.send(doc, d, "book", mode=Mode.RENDEZVOUS)
            return None
        msg = comm.recv(source=0, tag="book")
        view = ampi.View.vector(offset=comm.rank - 1, count=24, stride=comm.size - 1)
        mine = comm.fetch(msg, view=view)
        chapters = [int(line.split()[1]) for line in mine.split("\n\n")]
        assert chapters == list(range(comm.rank - 1, 24, comm.size - 1)), chapters
        return chapters

    job = ampi.launch(rank_main, size=5, root=tmp_path / "view")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    covered = sorted(c for o in job.outcomes if o.value for c in o.value)
    assert covered == list(range(24)), "the block-cyclic views must tile the document"


def test_release_frees_context(tmp_path):
    def rank_main(comm):
        if comm.rank == 0:
            comm.send(_big(500), 1, "a", mode=Mode.EAGER)
            comm.send(_big(500), 1, "b", mode=Mode.EAGER)
            return None
        a = comm.recv(source=0, tag="a")
        used_one = comm.rt.context.used
        assert used_one > 400
        comm.release(a)
        assert comm.rt.context.used == 0
        b = comm.recv(source=0, tag="b")
        assert comm.rt.context.used == pytest.approx(used_one, rel=0.2)
        return comm.rt.context.snapshot()

    job = ampi.launch(rank_main, size=2, root=tmp_path / "rel", eager_limit=100_000, context_budget=2000)
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    snap = job.value(1)
    assert snap["evictions"] == 1


def test_identical_artifacts_are_admitted_once(tmp_path):
    """Content addressing makes duplicate admission free.

    Sixteen ranks that all quote the same specification back cost the root one
    artifact's worth of context, not sixteen.  This falls out of keying the
    working set by digest, and it is a real saving: broadcast-then-echo is a
    pervasive pattern in agent harnesses.
    """
    same = _big(400)

    def rank_main(comm):
        if comm.rank == 0:
            for i in range(6):
                comm.send(same, 1, f"m{i}", mode=Mode.EAGER)
            return None
        for i in range(6):
            comm.recv(source=0, tag=f"m{i}")
        snap = comm.rt.context.snapshot()
        assert snap["n_items"] == 1, snap
        assert snap["used"] < 700, snap
        return snap

    job = ampi.launch(rank_main, size=2, root=tmp_path / "dedup", eager_limit=100_000, context_budget=8000)
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_compaction_is_traced(tmp_path):
    def rank_main(comm):
        if comm.rank == 0:
            for i in range(4):
                comm.send(f"message {i}: " + _big(300), 1, f"m{i}", mode=Mode.EAGER)
            return None
        for i in range(4):
            comm.recv(source=0, tag=f"m{i}")
        assert comm.rt.context.used > 1000, comm.rt.context.snapshot()
        dropped = comm.rt.compact(keep=1)
        assert dropped > 0
        assert len(comm.rt.context.working_set) == 1
        return dropped

    job = ampi.launch(rank_main, size=2, root=tmp_path / "comp", eager_limit=100_000, context_budget=8000)
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    fabric = ampi.Fabric(tmp_path / "comp")
    assert fabric.events(kinds=["rank.compact"]), "compaction must appear in the trace"


def test_deferred_token_accounting(tmp_path):
    """The headline number for the transport experiment: tokens a rendezvous did not push.

    ``tokens_deferred`` is a send-side, rendezvous-only quantity, so it belongs to the sender
    alone. The receiver's corresponding measurement is ``tokens_unadmitted``: content that
    arrived and was never taken into context.

    This test previously asserted that the *receiver* also had ``tokens_deferred > 4000``, which
    is how the conflation survived. Both quantities were accumulating into one field --- the
    sender on rendezvous, the receiver on any non-admitted arrival --- so the field exceeded
    ``tokens_sent`` on real runs, which is impossible for a subset of traffic, and a harness
    passing ``admit=False`` everywhere had every eager arrival reported as a rendezvous saving.
    """
    payload = _big(5000)

    def rank_main(comm):
        if comm.rank == 0:
            comm.send(payload, 1, "d", mode=Mode.RENDEZVOUS)
            return comm.rt.cost.snapshot()
        comm.recv(source=0, tag="d")
        return comm.rt.cost.snapshot()

    job = ampi.launch(rank_main, size=2, root=tmp_path / "def")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]

    sender, receiver = job.value(0), job.value(1)
    assert sender["tokens_deferred"] > 4000
    assert sender["tokens_deferred"] <= sender["tokens_sent"], "deferred must be a subset of sent"

    # The receiver deferred nothing: it sent nothing.
    assert receiver["tokens_deferred"] == 0
    assert receiver["tokens_unadmitted"] > 4000, "the arrival was never admitted"
    assert receiver["tokens_recv"] == 0


def test_deferred_never_exceeds_sent_when_nothing_is_admitted(tmp_path):
    """The condition under which the two quantities used to be summed into one.

    Every experiment harness in this repository receives with ``admit=False``, so under the old
    accounting each rendezvous message was counted twice and each eager one once, under a name
    that says rendezvous. Eager traffic is included here deliberately: it must contribute to
    ``tokens_unadmitted`` and nothing at all to ``tokens_deferred``.
    """
    payload = _big(3000)

    def rank_main(comm):
        if comm.rank == 0:
            comm.send(payload, 1, "eager", mode=Mode.EAGER)
            comm.send(payload, 1, "rdv", mode=Mode.RENDEZVOUS)
        else:
            comm.recv(source=0, tag="eager", admit=False)
            comm.recv(source=0, tag="rdv", admit=False)
        return comm.rt.cost.snapshot()

    job = ampi.launch(rank_main, size=2, root=tmp_path / "mixed", eager_limit=10_000_000)
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    sender, receiver = job.value(0), job.value(1)

    # Exactly one of the sender's two messages was rendezvous.
    assert 0 < sender["tokens_deferred"] < sender["tokens_sent"]
    assert receiver["tokens_deferred"] == 0
    # Both arrivals went unadmitted, so the receiver's figure covers eager traffic too.
    assert receiver["tokens_unadmitted"] > sender["tokens_deferred"]

    totals = job.totals()
    assert totals["tokens_deferred"] <= totals["tokens_sent"], totals
