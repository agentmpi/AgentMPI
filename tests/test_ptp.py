"""Point-to-point semantics: matching, ordering, contracts, transport modes."""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi.constants import ANY_SOURCE, ANY_TAG, Mode


def test_send_recv_roundtrip(tmp_path):
    def rank_main(comm):
        if comm.rank == 0:
            comm.send({"hello": "world"}, 1, "greet")
            return "sent"
        msg = comm.recv(source=0, tag="greet")
        assert msg.payload == {"hello": "world"}
        assert msg.source == 0
        return msg.payload

    job = ampi.launch(rank_main, size=2, root=tmp_path / "j")
    assert job.ok, job.outcomes
    assert job.value(1) == {"hello": "world"}


def test_non_overtaking(tmp_path):
    """Two messages on the same (src, dst, comm, tag) arrive in send order.

    MPI's non-overtaking guarantee.  Without it, a harness that streams a
    sequence of revisions to a reviewer would silently reorder them.
    """
    n = 25

    def rank_main(comm):
        if comm.rank == 0:
            for i in range(n):
                comm.send({"i": i}, 1, "stream")
            return None
        got = [comm.recv(source=0, tag="stream").payload["i"] for _ in range(n)]
        assert got == list(range(n)), got
        return got

    job = ampi.launch(rank_main, size=2, root=tmp_path / "j")
    assert job.ok, job.outcomes


def test_tag_and_source_selectivity(tmp_path):
    def rank_main(comm):
        if comm.rank == 0:
            comm.send("A", 2, "alpha")
            comm.send("B", 2, "beta")
            return None
        if comm.rank == 1:
            comm.send("C", 2, "beta")
            return None
        # Tag selectivity must skip the earlier non-matching message.
        first = comm.recv(source=ANY_SOURCE, tag="beta")
        second = comm.recv(source=0, tag="alpha")
        third = comm.recv(source=ANY_SOURCE, tag=ANY_TAG)
        return sorted([first.payload, second.payload, third.payload])

    job = ampi.launch(rank_main, size=3, root=tmp_path / "j")
    assert job.ok, job.outcomes
    assert job.value(2) == ["A", "B", "C"]


def test_internal_tags_are_reserved(tmp_path):
    def rank_main(comm):
        if comm.rank == 0:
            with pytest.raises(ampi.AmpiError):
                comm.send("x", 1, "_ampi:bcast:0:1")
            comm.send("ok", 1, "user")
        else:
            comm.recv(source=0, tag="user")
        return True

    job = ampi.launch(rank_main, size=2, root=tmp_path / "j")
    assert job.ok, job.outcomes


def test_contract_matching_and_violation(tmp_path):
    good = ampi.Contract(name="Chunk", kind="json", required=("text", "index"))
    wrong = ampi.Contract(name="Other", kind="json", required=("text",))

    def rank_main(comm):
        if comm.rank == 0:
            with pytest.raises(ampi.AmpiValidationError):
                comm.send({"text": "hi"}, 1, "work", contract=good)
            comm.send({"text": "hi", "index": 1}, 1, "work", contract=good)
            comm.send({"text": "hi", "index": 2}, 1, "work2", contract=good)
            return None
        msg = comm.recv(source=0, tag="work", contract=good)
        assert msg.payload["index"] == 1
        with pytest.raises(ampi.AmpiTypeError):
            comm.recv(source=0, tag="work2", contract=wrong)
        return True

    job = ampi.launch(rank_main, size=2, root=tmp_path / "j")
    assert job.ok, job.outcomes


def test_rendezvous_defers_context_admission(tmp_path):
    """A large payload must not enter the receiver's context by default."""
    big = "paragraph text " * 4000  # far above the eager limit

    def rank_main(comm):
        if comm.rank == 0:
            comm.send(big, 1, "doc", mode=Mode.RENDEZVOUS)
            return None
        st = comm.probe(source=0, tag="doc")
        assert st.is_rendezvous
        assert st.tokens > 2048
        msg = comm.recv(source=0, tag="doc")
        assert msg.payload is None, "rendezvous receive must not materialise by default"
        assert comm.rt.context.used == 0
        # A narrow view materialises only a prefix and charges only that.
        head = comm.fetch(msg, view=ampi.View.head(200))
        assert 0 < comm.rt.context.used <= 220
        return len(head)

    job = ampi.launch(rank_main, size=2, root=tmp_path / "j")
    assert job.ok, job.outcomes


def test_eager_admission_charges_context_and_can_truncate(tmp_path):
    payload = "x " * 900  # ~900 tokens

    def rank_main(comm):
        if comm.rank == 0:
            comm.send(payload, 1, "d", mode=Mode.EAGER)
            return None
        with pytest.raises(ampi.AmpiTruncateError):
            comm.recv(source=0, tag="d")
        return True

    job = ampi.launch(
        rank_main,
        size=2,
        root=tmp_path / "j",
        context_budget=lambda r: 400 if r == 1 else 128_000,
        eager_limit=100_000,
    )
    assert job.ok, job.outcomes


def test_probe_then_view_keeps_program_in_budget(tmp_path):
    """The recommended defence: probe, see the size, narrow the view."""
    payload = "\n\n".join(f"paragraph {i} " + "word " * 60 for i in range(40))

    def rank_main(comm):
        if comm.rank == 0:
            comm.send(payload, 1, "d", mode=Mode.EAGER)
            return None
        st = comm.probe(source=0, tag="d")
        budget = comm.rt.context.free
        view = None if st.tokens <= budget else ampi.View.head(budget // 2)
        msg = comm.recv(source=0, tag="d", view=view)
        assert comm.rt.context.used <= comm.rt.context.budget
        return msg.tokens

    job = ampi.launch(
        rank_main, size=2, root=tmp_path / "j", context_budget=lambda r: 600 if r == 1 else 128_000, eager_limit=100_000
    )
    assert job.ok, job.outcomes


def test_sendrecv_ring_does_not_deadlock(tmp_path):
    def rank_main(comm):
        p, r = comm.size, comm.rank
        msg = comm.sendrecv(f"from{r}", dest=(r + 1) % p, source=(r - 1) % p, sendtag="ring", recvtag="ring")
        assert msg.payload == f"from{(r - 1) % p}"
        return msg.payload

    job = ampi.launch(rank_main, size=6, root=tmp_path / "j")
    assert job.ok, job.outcomes


def test_irecv_waitany_pattern(tmp_path):
    """Nonblocking fan-in: process results as they land, not in rank order."""

    def rank_main(comm):
        if comm.rank == 0:
            reqs = [comm.irecv(source=s, tag="res") for s in range(1, comm.size)]
            seen = []
            while reqs:
                progressed = False
                for req in list(reqs):
                    msg = req.test()
                    if msg is not None:
                        seen.append(msg.source)
                        reqs.remove(req)
                        progressed = True
                if not progressed:
                    import time

                    time.sleep(0.005)
            return sorted(seen)
        comm.send(f"r{comm.rank}", 0, "res")
        return None

    job = ampi.launch(rank_main, size=5, root=tmp_path / "j")
    assert job.ok, job.outcomes
    assert job.value(0) == [1, 2, 3, 4]


def test_mprobe_mrecv_claims_exactly_one(tmp_path):
    def rank_main(comm):
        if comm.rank == 0:
            comm.send("one", 1, "t")
            return None
        st = comm.mprobe(source=0, tag="t")
        assert comm.iprobe(source=0, tag="t") is None, "matched message must be invisible to a later probe"
        msg = comm.mrecv(st)
        return msg.payload

    job = ampi.launch(rank_main, size=2, root=tmp_path / "j")
    assert job.ok, job.outcomes
    assert job.value(1) == "one"
