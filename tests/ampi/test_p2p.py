"""Point-to-point semantics: matching, ordering, wildcards, rendezvous."""

from __future__ import annotations

import pytest

from ampi.constants import AMPI_ANY_SOURCE, AMPI_ANY_TAG, MODE_EAGER, MODE_RENDEZVOUS
from ampi.errors import AmpiContextExhausted, AmpiDeadlock, AmpiTimeout


def test_send_recv_roundtrip(make_job):
    job = make_job(2)

    def body(rt, rank):
        if rank == 0:
            rt.send("world", 1, 7, "hello from zero")
            return rt.recv("world", 1, 8, timeout=30)["payload"]
        rt.send("world", 0, 8, "hello from one")
        return rt.recv("world", 0, 7, timeout=30)["payload"]

    out = job.run_ranks(body)
    assert out[0] == "hello from one"
    assert out[1] == "hello from zero"


def test_non_overtaking_order(make_job):
    """Messages with the same envelope are matched in the order they were sent."""
    job = make_job(2)

    def body(rt, rank):
        if rank == 0:
            for i in range(12):
                rt.send("world", 1, 3, f"msg-{i}")
            return None
        return [rt.recv("world", 0, 3, timeout=30)["payload"] for _ in range(12)]

    out = job.run_ranks(body)
    assert out[1] == [f"msg-{i}" for i in range(12)]


def test_tag_selectivity_allows_overtaking(make_job):
    """A receive for one tag may legally skip earlier messages with other tags."""
    job = make_job(2)

    def body(rt, rank):
        if rank == 0:
            rt.send("world", 1, 1, "first-tag-one")
            rt.send("world", 1, 2, "second-tag-two")
            return None
        first = rt.recv("world", 0, 2, timeout=30)["payload"]
        second = rt.recv("world", 0, 1, timeout=30)["payload"]
        return [first, second]

    out = job.run_ranks(body)
    assert out[1] == ["second-tag-two", "first-tag-one"]


def test_any_source_matches_exactly_once(make_job):
    """Two concurrent wildcard receives must never be handed the same message.

    This is the duplicated-work failure mode of ad-hoc agent harnesses; the
    device's atomic match is what rules it out.
    """
    job = make_job(4)

    def body(rt, rank):
        if rank < 2:
            rt.send("world", 2 + rank, 5, f"work-{rank}")
            return None
        return rt.recv("world", AMPI_ANY_SOURCE, AMPI_ANY_TAG, timeout=30)["payload"]

    out = job.run_ranks(body)
    assert sorted([out[2], out[3]]) == ["work-0", "work-1"]


def test_rendezvous_is_receiver_driven(make_job):
    """A large payload arrives by reference; the digest is free, the body is not."""
    job = make_job(2, ctx_limit=50_000)
    big = "\n".join(f"line {i} of a long artifact" for i in range(4000))

    def body(rt, rank):
        if rank == 0:
            return rt.send("world", 1, 1, big)
        got = rt.recv("world", 0, 1, timeout=30)
        assert got["mode"] == MODE_RENDEZVOUS
        assert got["payload"] is None and got["handle"]
        cheap = rt.rank_row()["ctx_used"]
        full = rt.deref(got["handle"])
        return {"digest_tokens": cheap, "full_tokens": full["tokens"],
                "matches": full["payload"] == big}

    out = job.run_ranks(body)
    assert out[0]["mode"] == MODE_RENDEZVOUS
    assert out[1]["matches"]
    assert out[1]["digest_tokens"] < out[1]["full_tokens"] / 10


def test_small_message_is_eager(make_job):
    job = make_job(2)

    def body(rt, rank):
        if rank == 0:
            return rt.send("world", 1, 1, "short")
        return rt.recv("world", 0, 1, timeout=30)

    out = job.run_ranks(body)
    assert out[0]["mode"] == MODE_EAGER
    assert out[1]["payload"] == "short"


def test_context_exhaustion_is_an_error_not_a_truncation(make_job):
    job = make_job(2, ctx_limit=600)
    payload = "word " * 2000

    def body(rt, rank):
        if rank == 0:
            rt.send("world", 1, 1, payload, force_mode="eager")
            return None
        with pytest.raises(AmpiContextExhausted):
            rt.recv("world", 0, 1, timeout=20)
        return "refused"

    out = job.run_ranks(body)
    assert out[1] == "refused"


def test_deadlock_is_detected(make_job):
    """Two ranks each waiting for the other is reported, not hung."""
    job = make_job(2)

    def body(rt, rank):
        peer = 1 - rank
        with pytest.raises((AmpiDeadlock, AmpiTimeout)) as excinfo:
            rt.recv("world", peer, 1, timeout=12)
        return type(excinfo.value).__name__

    out = job.run_ranks(body, timeout=60)
    assert "AmpiDeadlock" in out.values()


def test_sendrecv_does_not_deadlock(make_job):
    job = make_job(2)

    def body(rt, rank):
        peer = 1 - rank
        return rt.sendrecv("world", peer, 9, f"from-{rank}", peer, 9,
                           timeout=30)["received"]["payload"]

    out = job.run_ranks(body)
    assert out[0] == "from-1" and out[1] == "from-0"
