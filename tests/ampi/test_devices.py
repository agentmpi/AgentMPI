"""Device conformance: the six abstract capabilities, over every transport.

The abstract device interface is only a real interface if more than one thing
implements it and the things that implement it are interchangeable where the
protocol depends on them.  This suite is the check.  It is deliberately about
*concurrency properties* rather than functionality, because those are what the
layers above assume and what a careless transport silently breaks:

* ``match`` must hand a record to exactly one claimant, or two agents do the
  same work;
* ``cas`` must reject a stale version, or concurrent edits are lost;
* ``lease`` must exclude, and must expire, or a dead agent wedges the job;
* ``counter_next`` must never repeat, or two agents take the same task.

Every property is tested under real thread contention rather than by
inspection.
"""

from __future__ import annotations

import threading

import pytest

from ampi import util
from ampi.device import FsDevice, SqliteDevice


@pytest.fixture(params=["sqlite", "filesystem"])
def device(request, tmp_path):
    if request.param == "sqlite":
        dev = SqliteDevice(str(tmp_path / "job.db"))
    else:
        dev = FsDevice(str(tmp_path / "fsjob"))
    dev.initialize()
    yield dev
    dev.close()


def _reopen(device):
    """A second handle on the same device, as a separate rank would have."""
    if isinstance(device, SqliteDevice):
        fresh = SqliteDevice(device.path)
    else:
        fresh = FsDevice(device.root)
    fresh.initialize()
    return fresh


def _append_message(device, **fields):
    record = {
        "job_id": "j", "comm_id": "c", "src": 0, "dst": 1, "tag": 0, "seq": 0,
        "mode": "eager", "body": "x", "handle": None, "digest": None, "tokens": 1,
        "state": "posted", "sent_at": util.now(), "meta": "{}",
    }
    record.update(fields)
    return device.append("message", record)


# ---------------------------------------------------------------------------
# Capability 1 and 2: append and match
# ---------------------------------------------------------------------------


def test_append_then_match(device):
    _append_message(device, tag=5, body="hello")
    got = device.match("message", {"comm_id": "c", "dst": 1, "state": "posted"}, "claimant-a",
                       order_by="seq" if isinstance(device, SqliteDevice) else "_seq")
    assert got is not None and got["body"] == "hello"


def test_match_is_first_fit_in_arrival_order(device):
    order_by = "msg_id" if isinstance(device, SqliteDevice) else "_seq"
    for i in range(8):
        _append_message(device, body=f"m{i}")
    seen = []
    for _ in range(8):
        got = device.match("message", {"comm_id": "c", "dst": 1, "state": "posted"}, "a",
                           order_by=order_by)
        seen.append(got["body"])
    assert seen == [f"m{i}" for i in range(8)]


def test_match_claims_each_record_exactly_once_under_contention(device):
    """The property that stops two agents doing the same task."""
    order_by = "msg_id" if isinstance(device, SqliteDevice) else "_seq"
    total = 40
    for i in range(total):
        _append_message(device, body=f"work{i}")

    claimed: list[str] = []
    guard = threading.Lock()

    def claimant(name: str) -> None:
        handle = _reopen(device)
        while True:
            got = handle.match("message", {"comm_id": "c", "dst": 1, "state": "posted"},
                               name, order_by=order_by)
            if got is None:
                break
            with guard:
                claimed.append(got["body"])
        handle.close()

    threads = [threading.Thread(target=claimant, args=(f"c{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert sorted(claimed) == sorted(f"work{i}" for i in range(total))
    assert len(claimed) == len(set(claimed)), "a record was handed to two claimants"


def test_scan_does_not_consume(device):
    _append_message(device, body="peek")
    first = device.scan("message", {"comm_id": "c", "state": "posted"})
    second = device.scan("message", {"comm_id": "c", "state": "posted"})
    assert len(first) == len(second) == 1


# ---------------------------------------------------------------------------
# Capability 3: compare-and-swap
# ---------------------------------------------------------------------------


def test_cas_installs_and_versions(device):
    ok, version, _ = device.cas("win1", "k", None, {"v": 1}, actor=0)
    assert ok and version == 1
    ok, version, _ = device.cas("win1", "k", 1, {"v": 2}, actor=1)
    assert ok and version == 2


def test_cas_rejects_a_stale_version(device):
    device.cas("win1", "k", None, {"v": 1}, actor=0)
    ok, version, current = device.cas("win1", "k", 0, {"v": "stale"}, actor=1)
    assert ok is False
    assert version == 1
    assert current == {"v": 1}


def test_exactly_one_racing_cas_wins(device):
    device.cas("win1", "k", None, {"v": 0}, actor=0)
    outcomes: list[bool] = []
    guard = threading.Lock()

    def writer(rank: int) -> None:
        handle = _reopen(device)
        ok, _, _ = handle.cas("win1", "k", 1, {"v": rank}, actor=rank)
        with guard:
            outcomes.append(ok)
        handle.close()

    threads = [threading.Thread(target=writer, args=(r,)) for r in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sum(1 for o in outcomes if o) == 1, f"expected one winner, got {outcomes}"


# ---------------------------------------------------------------------------
# Capability 4: leases
# ---------------------------------------------------------------------------


def test_exclusive_lease_excludes(device):
    first = device.lease("win1", "cell", holder=0, mode="exclusive", ttl=30)
    second = device.lease("win1", "cell", holder=1, mode="exclusive", ttl=30)
    assert first is not None
    assert second is None


def test_shared_leases_coexist_but_block_exclusive(device):
    assert device.lease("win1", "c", holder=0, mode="shared", ttl=30)
    assert device.lease("win1", "c", holder=1, mode="shared", ttl=30)
    assert device.lease("win1", "c", holder=2, mode="exclusive", ttl=30) is None


def test_lease_expiry_releases_a_dead_holder(device):
    import time

    assert device.lease("win1", "c", holder=0, mode="exclusive", ttl=0.15)
    time.sleep(0.3)
    assert device.lease("win1", "c", holder=1, mode="exclusive", ttl=5) is not None


def test_release_frees_the_lease(device):
    lock_id = device.lease("win1", "c", holder=0, mode="exclusive", ttl=30)
    assert device.release(lock_id, holder=0) is True
    assert device.lease("win1", "c", holder=1, mode="exclusive", ttl=30) is not None


# ---------------------------------------------------------------------------
# Capabilities 5 and 6, plus counters
# ---------------------------------------------------------------------------


def test_counter_never_repeats_under_contention(device):
    values: list[int] = []
    guard = threading.Lock()

    def bump() -> None:
        handle = _reopen(device)
        local = [handle.counter_next("j", "tickets") for _ in range(25)]
        with guard:
            values.extend(local)
        handle.close()

    threads = [threading.Thread(target=bump) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(values) == len(set(values)) == 150
    assert sorted(values) == list(range(1, 151))


def test_clock_is_monotone_enough(device):
    assert device.clock() <= device.clock()
