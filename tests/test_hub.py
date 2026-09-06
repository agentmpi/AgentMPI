"""The hub device: authentication, one clock, idempotent resend, and no contest.

The conformance suite already proves the hub implements the waist correctly;
these are the properties it has *because* it is one authoritative process
reachable over a network, which is what the git transports could not be.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from ampi.device.hub import DEFAULT_PORT, HubDevice, HubServer, address_file, parse_addr
from ampi.runtime import Ampi

pytestmark = pytest.mark.slow


def _serve(root, **kw) -> tuple[HubServer, threading.Thread]:
    hub = HubServer(root, host="127.0.0.1", port=0, **kw)
    t = threading.Thread(target=hub.serve, daemon=True)
    t.start()
    deadline = time.time() + 30
    while time.time() < deadline and not hub.port:
        time.sleep(0.02)
    # serve() resolves the ephemeral port before it publishes the address file.
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", hub.port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.05)
    return hub, t


def test_addresses_parse_in_every_form_an_operator_writes():
    assert parse_addr("10.42.1.9:7411") == ("10.42.1.9", 7411)
    assert parse_addr("10.42.1.9") == ("10.42.1.9", DEFAULT_PORT)
    assert parse_addr(":7000") == ("127.0.0.1", 7000)
    assert parse_addr("[::1]:7000") == ("::1", 7000)
    assert parse_addr(" 10.0.0.1:7411\n") == ("10.0.0.1", 7411)


def test_a_client_without_the_token_is_refused(tmp_path):
    root = tmp_path / "job"
    hub, _ = _serve(root, token="s3cret")
    try:
        good = HubDevice(root, addr=f"127.0.0.1:{hub.port}", token="s3cret", spawn=False)
        good.initialize()
        assert good.append("event", {"kind": "k", "rank": 0, "run": "r"}) > 0
        good.close()

        bad = HubDevice(root, addr=f"127.0.0.1:{hub.port}", token="wrong", spawn=False)
        with pytest.raises(RuntimeError):
            bad.initialize()
    finally:
        hub._stop.set()


def test_the_clock_every_rank_reads_is_the_hubs(tmp_path):
    """A lease is a time comparison and a conviction is a judgement about
    someone else's lease, so a fleet whose machines disagree about the time
    convicts the living.  Every rank must read one clock, and it is the hub's.

    The skew is put on the *hub's* side rather than the client's: both run in
    this one process, so patching ``time.time`` would move them together and
    prove nothing.  Here the hub reports a clock ten minutes ahead of local
    time, and the client is expected to carry that difference.
    """
    root = tmp_path / "job"
    hub, _ = _serve(root)
    ahead = 600.0
    inner = hub.handle

    def skewed(op, args):
        got = inner(op, args)
        if op == "clock":
            return got + ahead
        if op == "hello":
            return {**got, "clock": got["clock"] + ahead}
        return got

    hub.handle = skewed
    try:
        dev = HubDevice(root, addr=f"127.0.0.1:{hub.port}", spawn=False)
        dev.initialize()          # the greeting takes the first sample
        assert abs(dev.clock() - (time.time() + ahead)) < 2.0, \
            "the rank reported its own clock, not the hub's"
        # And it costs nothing between samples: no round trip, no drift of its
        # own beyond the local clock's.
        before = dev.calls
        for _ in range(100):
            dev.clock()
        assert dev.calls == before, "clock() went to the hub on every call"
    finally:
        hub.handle = inner
        hub._stop.set()


def test_a_mutation_resent_after_a_dropped_connection_is_applied_once(tmp_path):
    """The reply cache is what makes a network safe to retry over.

    Over a Unix socket an ambiguous reply is rare; over a VPC link a dropped
    connection between the mutation landing and the reply arriving is an
    ordinary event, and a re-applied ``append`` is a duplicate record in the
    trace that nothing downstream can tell from a real one.
    """
    root = tmp_path / "job"
    hub, _ = _serve(root)
    try:
        dev = HubDevice(root, addr=f"127.0.0.1:{hub.port}", spawn=False)
        dev.initialize()
        dev.append("event", {"kind": "k", "rank": 0, "run": "r"})

        # Replay the exact request the client would resend: same client id, same
        # request id, which is what makes it a resend rather than a new call.
        import json

        s = socket.create_connection(("127.0.0.1", hub.port))
        f = s.makefile("rb")
        req = {"id": dev._ids, "client": dev._client, "op": "append",
               "args": {"stream": "event", "record": {"kind": "k", "rank": 0, "run": "r"}}}
        s.sendall((json.dumps(req) + "\n").encode())
        reply = json.loads(f.readline())
        s.close()
        assert reply["ok"]
        assert hub.resends == 1
        assert len(dev.scan("event", {})) == 1, "the resend was applied a second time"
    finally:
        hub._stop.set()


def test_many_clients_write_at_once_without_a_contest(tmp_path):
    """The property the git device could not have at this width.

    Eight daemons on one git ref landed about one push in ten; here the
    serialisation point is a mutex in one process, so every writer's record
    lands and none of them retries.
    """
    root = tmp_path / "job"
    hub, _ = _serve(root)
    try:
        errors: list[str] = []

        def writer(i: int) -> None:
            try:
                dev = HubDevice(root, addr=f"127.0.0.1:{hub.port}", spawn=False)
                dev.initialize()
                for j in range(10):
                    dev.append("event", {"kind": "t", "rank": i, "j": j, "run": "r"})
                dev.close()
            except Exception as exc:  # noqa: BLE001 - reported, not raised, per thread
                errors.append(f"{i}: {exc}")

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors, errors

        dev = HubDevice(root, addr=f"127.0.0.1:{hub.port}", spawn=False)
        dev.initialize()
        rows = dev.scan("event", {})
        assert len(rows) == 160
        seqs = [r["seq"] for r in rows]
        assert len(set(seqs)) == 160 and seqs == sorted(seqs)
    finally:
        hub._stop.set()


def test_trace_appends_are_acknowledged_before_they_land(tmp_path):
    """A rank's program never waits on its own evidence (spec S13)."""
    root = tmp_path / "job"
    hub, _ = _serve(root)
    try:
        dev = HubDevice(root, addr=f"127.0.0.1:{hub.port}", spawn=False)
        dev.initialize()
        for i in range(50):
            dev.append_nowait("event", {"kind": "trace", "rank": 0, "i": i, "run": "r"})
        assert hub.async_ops == 50
        dev.flush()          # the ordered writer has taken them by the time this returns
        assert len(dev.scan("event", {})) == 50
    finally:
        hub._stop.set()


def test_a_job_runs_over_the_hub(tmp_path, monkeypatch):
    """The whole point: the runtime does not know the transport changed.

    The address reaches the device through the environment, which is exactly
    how it reaches a rank on a worker instance: ``/etc/ampi.node`` sets
    ``AMPI_HUB_ADDR`` and every rank process on the node inherits it.
    """
    root = tmp_path / "job"
    hub, _ = _serve(root)
    monkeypatch.setenv("AMPI_HUB_ADDR", f"127.0.0.1:{hub.port}")
    try:
        Ampi.create(str(root), 4, device="hub", force=True).close()
        ranks = [Ampi(str(root), rank=r) for r in range(4)]
        for a in ranks:
            a.init()
        results: dict[int, object] = {}

        def body(a: Ampi, r: int) -> None:
            a.barrier("start", timeout=60)
            results[r] = a.allreduce("g", payload={f"k{r}": r}, op="union", timeout=60)

        threads = [threading.Thread(target=body, args=(a, r)) for r, a in enumerate(ranks)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=180)
        assert len(results) == 4
        for r in range(4):
            assert results[r] is not None
        for a in ranks:
            a.close()
    finally:
        hub._stop.set()


def test_a_client_told_an_address_never_starts_a_second_authority(tmp_path):
    """Two hubs for one job would be two states.

    The gitd client starts a daemon when none is listening, which is right when
    the daemon is a local cache of a shared remote.  It is wrong here: a hub
    holds the state, so a client that could not reach the configured one must
    say so rather than quietly become the authority for a job of its own.
    """
    root = tmp_path / "job"
    dev = HubDevice(root, addr="127.0.0.1:1", spawn=True)
    with pytest.raises(RuntimeError, match="no hub answering"):
        dev.initialize()


def test_a_spawned_hub_publishes_where_it_listens(tmp_path):
    """The single-machine path the conformance suite uses."""
    root = tmp_path / "job"
    dev = HubDevice(root)
    dev.initialize()
    try:
        assert dev.append("event", {"kind": "k", "rank": 0, "run": "r"}) > 0
        host, port = parse_addr(open(address_file(root)).read())
        assert host == "127.0.0.1" and port > 0
        assert dev.stats()["device"] == "hub"
    finally:
        dev.shutdown_hub()


def test_a_clock_sample_never_swallows_a_pipelined_error(tmp_path):
    """A sampling clock read inside a pipeline block hides a failed write.

    ``Ampi.create`` writes one cell per rank inside ``pipeline()``, where a
    mutation is sent now and its reply collected at the end.  The cas path fills
    a timestamp into the placeholder cell it returns; if that timestamp came
    from a *sampling* clock read, the sample's request would go out mid-burst
    and read back the reply of an earlier pipelined mutation.  Request and reply
    counts still balance, so nothing looks wrong --- but if the reply it
    consumed was an *error*, that error is swallowed by the suppression around
    the sample, and a write that failed is reported as a job created cleanly.

    The window is a client older than the sample interval, which no conformance
    test is: they create a job within a second of connecting.  Here the interval
    is zero, so every clock read would sample.
    """
    root = tmp_path / "job"
    hub, _ = _serve(root)
    try:
        dev = HubDevice(root, addr=f"127.0.0.1:{hub.port}", spawn=False)
        dev.initialize()
        dev._clock_interval = 0.0
        with pytest.raises(RuntimeError):
            with dev.pipeline():
                dev.append("no_such_stream", {"x": 1})   # queued, and will fail
                dev.cas("rank", "0", None, {"rank": 0}, writer=-1)
    finally:
        hub._stop.set()
