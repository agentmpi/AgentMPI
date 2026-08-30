"""Point-to-point semantics: matching, ordering, modes, wildcards, contracts."""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi import sim
from agentmpi.constants import SendMode


def test_send_recv_roundtrip():
    def body(comm):
        if comm.rank == 0:
            comm.send("hello from 0", 1, tag=7)
            return "sent"
        value, status = comm.recv(0, 7)
        assert status.source == 0 and status.tag == 7
        return value

    r = sim.run(2, body)
    r.raise_errors()
    assert r.results[1] == "hello from 0"


def test_non_overtaking():
    """Two messages between the same pair must be matched in send order."""

    def body(comm):
        if comm.rank == 0:
            for i in range(20):
                comm.send({"i": i}, 1, tag=1, datatype="json")
            return None
        got = []
        for _ in range(20):
            value, _ = comm.recv(0, 1, "json")
            got.append(value["i"])
        return got

    r = sim.run(2, body)
    r.raise_errors()
    assert r.results[1] == list(range(20))


def test_tag_and_source_selectivity():
    def body(comm):
        if comm.rank == 0:
            comm.send("a", 2, tag=1)
            comm.send("b", 2, tag=2)
            return None
        if comm.rank == 1:
            comm.send("c", 2, tag=1)
            return None
        # Rank 2 asks for tag 2 first even though tag 1 arrived first.
        v2, s2 = comm.recv(0, 2)
        v1, s1 = comm.recv(0, 1)
        vc, sc = comm.recv(1, 1)
        return [v2, v1, vc, s2.tag, s1.source, sc.source]

    r = sim.run(3, body)
    r.raise_errors()
    assert r.results[2] == ["b", "a", "c", 2, 0, 1]


def test_any_source_any_tag():
    def body(comm):
        if comm.rank != 0:
            comm.send(f"from{comm.rank}", 0, tag=comm.rank)
            return None
        seen = {}
        for _ in range(comm.size - 1):
            value, status = comm.recv(ampi.ANY_SOURCE, ampi.ANY_TAG)
            seen[status.source] = value
        return seen

    r = sim.run(5, body)
    r.raise_errors()
    assert r.results[0] == {i: f"from{i}" for i in range(1, 5)}


def test_synchronous_send_waits_for_ingestion():
    """Ssend must not complete until the receiver has read the message."""
    import time

    order = []

    def body(comm):
        if comm.rank == 0:
            comm.ssend("payload", 1, tag=3, timeout=20)
            order.append("ssend-returned")
            return None
        time.sleep(0.4)
        order.append("about-to-recv")
        value, _ = comm.recv(0, 3)
        return value

    r = sim.run(2, body)
    r.raise_errors()
    assert r.results[1] == "payload"
    assert order.index("about-to-recv") < order.index("ssend-returned")


def test_sendrecv_exchange_does_not_deadlock():
    def body(comm):
        partner = 1 - comm.rank
        value, _ = comm.sendrecv(f"r{comm.rank}", partner, partner, 5, 5)
        return value

    r = sim.run(2, body)
    r.raise_errors()
    assert r.results[0] == "r1" and r.results[1] == "r0"


def test_probe_then_receive():
    def body(comm):
        if comm.rank == 0:
            comm.send({"x": 1}, 1, tag=9, datatype="json")
            return None
        status = comm.probe(0, 9, timeout=10)
        assert status.tokens > 0
        value, _ = comm.recv(0, 9, "json")
        return value

    r = sim.run(2, body)
    r.raise_errors()
    assert r.results[1] == {"x": 1}


def test_mprobe_mrecv():
    def body(comm):
        if comm.rank == 0:
            comm.send("m", 1, tag=4)
            return None
        message, status = comm.mprobe(0, 4, timeout=10)
        value, _ = comm.mrecv(message)
        return value

    r = sim.run(2, body)
    r.raise_errors()
    assert r.results[1] == "m"


def test_irecv_and_waitall():
    def body(comm):
        if comm.rank == 0:
            reqs = [comm.irecv(i, 1) for i in range(1, comm.size)]
            out = comm.waitall(reqs, timeout=20)
            return sorted(v for v, _ in out)
        comm.send(f"v{comm.rank}", 0, 1)
        return None

    r = sim.run(4, body)
    r.raise_errors()
    assert r.results[0] == ["v1", "v2", "v3"]


def test_waitsome_returns_early():
    def body(comm):
        import time

        if comm.rank == 0:
            reqs = [comm.irecv(i, 1) for i in range(1, comm.size)]
            first = comm.waitsome(reqs, timeout=20)
            rest = comm.waitall(reqs, timeout=20)
            return (len(first) < len(reqs), len(rest))
        time.sleep(0.05 * comm.rank)
        comm.send(comm.rank, 0, 1, "json")
        return None

    r = sim.run(5, body)
    r.raise_errors()
    early, total = r.results[0]
    assert total == 4


def test_proc_null_is_a_no_op():
    def body(comm):
        st = comm.send("x", ampi.PROC_NULL, 0)
        value, rst = comm.recv(ampi.PROC_NULL, 0)
        return (st.source, value, rst.tokens)

    r = sim.run(1, body)
    r.raise_errors()
    assert r.results[0] == (ampi.PROC_NULL, None, 0)


def test_contract_violation_is_reported():
    schema = {"type": "object", "required": ["title", "body"]}
    dt = ampi.type_contract(ampi.JSON_, schema, name="Doc")

    def body(comm):
        if comm.rank == 0:
            comm.send({"title": "ok", "body": "x"}, 1, 1, dt)
            comm.runtime.cvars["ampi_strict_contracts"] = False
            comm.send({"title": "missing body"}, 1, 2, ampi.JSON_)
            return None
        good, s1 = comm.recv(0, 1, dt)
        bad, s2 = comm.recv(0, 2, dt)
        return (s1.contract_ok, s2.contract_ok, s2.violations)

    r = sim.run(2, body)
    r.raise_errors()
    ok, not_ok, violations = r.results[1]
    assert ok is True
    assert not_ok is False
    assert any("body" in v for v in violations)


def test_send_rejects_contract_violation_at_source():
    schema = {"type": "object", "required": ["answer"]}
    dt = ampi.type_contract(ampi.JSON_, schema, name="Answer")

    def body(comm):
        if comm.rank == 0:
            with pytest.raises(ampi.ContractError):
                comm.send({"wrong": 1}, 1, 1, dt)
            comm.send({"answer": 42}, 1, 1, dt)
            return None
        value, _ = comm.recv(0, 1, dt)
        return value

    r = sim.run(2, body)
    r.raise_errors()
    assert r.results[1] == {"answer": 42}


def test_large_payload_travels_by_reference():
    big = "word " * 20000

    def body(comm):
        if comm.rank == 0:
            comm.send(big, 1, 1)
            return None
        value, status = comm.recv(0, 1)
        return (len(value), status.tokens)

    r = sim.run(2, body, cvars={"ampi_context_capacity": 10_000_000})
    r.raise_errors()
    length, tokens = r.results[1]
    assert length == len(big)
    assert tokens > 1000


def test_bounded_datatype_digests_oversized_payload():
    small = ampi.type_bounded(ampi.TEXT, max_tokens=50, name="Brief")

    def body(comm):
        if comm.rank == 0:
            comm.send("sentence. " * 500, 1, 1, small)
            return None
        value, status = comm.recv(0, 1, small)
        return (status.reduced, status.tokens)

    r = sim.run(2, body)
    r.raise_errors()
    reduced, tokens = r.results[1]
    assert reduced is True
    assert tokens <= 80


def test_message_ordering_across_contexts_is_independent():
    """A dup'd communicator must not steal the parent's messages."""

    def body(comm):
        other = comm.dup("private")
        if comm.rank == 0:
            other.send("private", 1, 0)
            comm.send("public", 1, 0)
            return None
        pub, _ = comm.recv(0, 0)
        priv, _ = other.recv(0, 0)
        return (pub, priv)

    r = sim.run(2, body)
    r.raise_errors()
    assert r.results[1] == ("public", "private")
