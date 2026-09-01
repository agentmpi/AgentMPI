"""Conformance suite for the Abstract Device Interface.

Every assertion here is a semantic obligation stated in specification section S13.
A transport that passes this file can carry the whole protocol; one that does not
will fail somewhere far away and much later, which is the reason the suite exists.

The tests are deliberately blunt about the two properties that cannot be recovered
above the waist: ``match`` must be atomic under concurrency, and ``cas`` must be a
genuine compare-and-swap.  Everything else in AgentMPI is built from those two,
and a device that merely *usually* gets them right produces a harness that merely
usually works.
"""

from __future__ import annotations

import concurrent.futures
import json
import multiprocessing as mp

import pytest

from ampi.device import Ge, Gt, In, IsNull, Ne, NotIn, NotNull

from .fixtures import SKIP_VOLATILE, device_ids, make_device

pytestmark = pytest.mark.device


@pytest.fixture(params=device_ids())
def dev(request, tmp_path):
    d = make_device(request.param, str(tmp_path / "job"))
    yield d
    d.close()


# --------------------------------------------------------------------------
# 1. append: durable, totally ordered
# --------------------------------------------------------------------------


def test_append_returns_increasing_sequence_numbers(dev):
    seqs = [dev.append("event", {"rank": i, "kind": "test"}) for i in range(20)]
    assert seqs == sorted(seqs), "sequence numbers must increase"
    assert len(set(seqs)) == 20, "sequence numbers must be unique"


def test_append_round_trips_indexed_and_body_fields(dev):
    dev.append("msg", {"comm": "world", "src": 1, "dst": 2, "tag": 7, "state": "posted",
                       "body_text": "hello", "nested": {"a": [1, 2]}})
    (rec,) = dev.scan("msg", {"dst": 2})
    assert rec["comm"] == "world"
    assert rec["src"] == 1
    assert rec["tag"] == 7
    assert rec["body_text"] == "hello"
    assert rec["nested"] == {"a": [1, 2]}
    assert "seq" in rec and "ts" in rec


def test_append_preserves_order_within_a_stream(dev):
    for i in range(50):
        dev.append("msg", {"comm": "world", "src": 0, "dst": 1, "tag": 0, "state": "posted", "i": i})
    got = [r["i"] for r in dev.scan("msg", {"dst": 1})]
    assert got == list(range(50)), "scan must return records in append order"


# --------------------------------------------------------------------------
# 2. match: atomic first-fit claim
# --------------------------------------------------------------------------


def test_match_claims_the_earliest_matching_record(dev):
    for i in range(5):
        dev.append("msg", {"comm": "world", "src": 0, "dst": 1, "tag": 0, "state": "posted", "i": i})
    got = dev.match("msg", {"dst": 1, "state": "posted"}, {"state": "claimed"})
    assert got is not None and got["i"] == 0
    assert got["state"] == "claimed", "the update must be visible in the returned record"


def test_match_does_not_return_a_record_twice(dev):
    dev.append("msg", {"comm": "world", "src": 0, "dst": 1, "tag": 0, "state": "posted"})
    first = dev.match("msg", {"dst": 1, "state": "posted"}, {"state": "claimed"})
    second = dev.match("msg", {"dst": 1, "state": "posted"}, {"state": "claimed"})
    assert first is not None
    assert second is None, "a claimed record must no longer satisfy the posted predicate"


def test_match_returns_none_when_nothing_matches(dev):
    assert dev.match("msg", {"dst": 99, "state": "posted"}, {"state": "claimed"}) is None


def test_match_is_atomic_under_concurrent_claimants(dev):
    """The property the whole matching chapter rests on.

    Two ranks posting wildcard receives must never be handed the same message.
    This is the duplicated-work bug that ad-hoc agent harnesses hit, and it cannot
    be fixed above the device.
    """
    n = 40
    for i in range(n):
        dev.append("msg", {"comm": "world", "src": 0, "dst": 1, "tag": 0, "state": "posted", "i": i})

    def claim(worker: int) -> list[int]:
        got = []
        while True:
            rec = dev.match("msg", {"dst": 1, "state": "posted"}, {"state": "claimed", "by": worker})
            if rec is None:
                return got
            got.append(rec["i"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))

    claimed = [i for r in results for i in r]
    assert sorted(claimed) == list(range(n)), "every record claimed exactly once"


def test_match_honours_ordering_and_predicates(dev):
    for i, tag in enumerate([3, 1, 2]):
        dev.append("msg", {"comm": "world", "src": 0, "dst": 1, "tag": tag, "state": "posted", "i": i})
    got = dev.match("msg", {"dst": 1, "state": "posted", "tag": In([1, 2])}, {"state": "claimed"})
    assert got is not None and got["tag"] == 1, "first-fit is by sequence, restricted by predicate"


# --------------------------------------------------------------------------
# 3. scan: the predicate language
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "predicate,expect",
    [
        ({"rank": 2}, {2}),
        ({"rank": In([1, 3])}, {1, 3}),
        ({"rank": NotIn([0, 1, 2])}, {3, 4}),
        ({"rank": Ne(0)}, {1, 2, 3, 4}),
        ({"rank": Gt(2)}, {3, 4}),
        ({"rank": Ge(3)}, {3, 4}),
        ({"kind": NotNull()}, {0, 1, 2, 3, 4}),
        ({"comm": IsNull()}, {0, 1, 2, 3, 4}),
    ],
)
def test_scan_predicate_language(dev, predicate, expect):
    for i in range(5):
        dev.append("event", {"rank": i, "kind": "k"})
    got = {r["rank"] for r in dev.scan("event", predicate)}
    assert got == expect


def test_scan_supports_body_fields_not_only_indexed_ones(dev):
    for i in range(5):
        dev.append("event", {"rank": i, "kind": "k", "note": "odd" if i % 2 else "even"})
    got = {r["rank"] for r in dev.scan("event", {"note": "odd"})}
    assert got == {1, 3}


def test_scan_limit_and_direction(dev):
    for i in range(10):
        dev.append("event", {"rank": i, "kind": "k"})
    assert [r["rank"] for r in dev.scan("event", {}, limit=3)] == [0, 1, 2]
    assert [r["rank"] for r in dev.scan("event", {}, descending=True, limit=3)] == [9, 8, 7]


def test_update_patches_in_place(dev):
    seq = dev.append("event", {"rank": 1, "kind": "k", "note": "before"})
    assert dev.update("event", seq, {"note": "after", "kind": "k2"}) is True
    (rec,) = dev.scan("event", {"rank": 1})
    assert rec["note"] == "after" and rec["kind"] == "k2"
    assert dev.update("event", 10 ** 9, {"note": "x"}) is False


# --------------------------------------------------------------------------
# 4. cas: the RMA substrate
# --------------------------------------------------------------------------


def test_cas_creates_reads_and_versions(dev):
    assert dev.read("w/win", "k") is None
    ok, cell = dev.cas("w/win", "k", 0, {"v": 1}, writer=3)
    assert ok and cell.version == 1 and cell.writer == 3
    ok, cell = dev.cas("w/win", "k", 1, {"v": 2}, writer=4)
    assert ok and cell.version == 2
    assert dev.read("w/win", "k").value == {"v": 2}
    assert dev.read("w/win", "k", version=1).value == {"v": 1}


def test_cas_rejects_a_stale_expectation_and_returns_the_winner(dev):
    dev.cas("w/win", "k", 0, "a", writer=1)
    ok, current = dev.cas("w/win", "k", 0, "b", writer=2)
    assert ok is False, "creating a cell that exists must fail"
    assert current.value == "a", "the loser must be shown the current value, so it can retry"
    assert current.version == 1


def test_cas_none_expectation_is_an_unconditional_write(dev):
    dev.cas("w/win", "k", 0, "a", writer=1)
    ok, cell = dev.cas("w/win", "k", None, "b", writer=2)
    assert ok and cell.version == 2


def test_cas_is_atomic_under_contention(dev):
    """Exactly one of N concurrent claimants may win an unclaimed cell.

    This is how work is claimed in a harness --- a task cell holds ``unclaimed``
    and whichever executor swaps it wins.  Unlike a lock it cannot be held by a
    dead executor, which is why the protocol prefers it.
    """
    dev.cas("w/tasks", "t0", 0, "unclaimed", writer=-1)

    def claim(worker: int) -> bool:
        ok, _ = dev.cas("w/tasks", "t0", 1, f"claimed-by-{worker}", writer=worker)
        return ok

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        wins = list(pool.map(claim, range(16)))
    assert sum(wins) == 1, "compare-and-swap must admit exactly one winner"


def test_keys_enumerates_without_bodies(dev):
    for i in range(3):
        dev.cas("w/win", f"note/{i}", 0, "x" * 100, writer=i, meta={"tokens": 25})
    dev.cas("w/win", "other", 0, "y", writer=9)
    cells = dev.keys("w/win", prefix="note/")
    assert [c.key for c in cells] == ["note/0", "note/1", "note/2"]
    assert all(c.value is None for c in cells), "enumeration must not deliver bodies"
    assert all(c.meta.get("tokens") == 25 for c in cells), "metadata survives"


def test_history_is_newest_first_and_attributed(dev):
    for i in range(4):
        dev.cas("w/win", "k", i, f"v{i}", writer=i)
    hist = dev.history("w/win", "k")
    assert [c.version for c in hist] == [4, 3, 2, 1]
    assert [c.writer for c in hist] == [3, 2, 1, 0]
    assert [c.value for c in hist] == ["v3", "v2", "v1", "v0"]


# --------------------------------------------------------------------------
# 5. lease: leased locks with fencing tokens
# --------------------------------------------------------------------------


def test_lease_excludes_a_second_holder(dev):
    a = dev.lease("w/win", "k", holder=1, ttl=60)
    assert a is not None
    assert dev.lease("w/win", "k", holder=2, ttl=60) is None


def test_lease_tokens_are_monotone_across_holders(dev):
    a = dev.lease("w/win", "k", holder=1, ttl=60)
    dev.release(a.lock_id, 1)
    b = dev.lease("w/win", "k", holder=2, ttl=60)
    assert b.token > a.token, "a fencing token must never go backwards"


def test_expired_lease_is_reclaimable_and_bumps_the_token(dev):
    a = dev.lease("w/win", "k", holder=1, ttl=-1)  # already expired
    b = dev.lease("w/win", "k", holder=2, ttl=60)
    assert b is not None, "an expired lease must not wedge the cell forever"
    assert b.token > a.token


def test_reacquiring_your_own_lock_renews_rather_than_fails(dev):
    a = dev.lease("w/win", "k", holder=1, ttl=60)
    b = dev.lease("w/win", "k", holder=1, ttl=120)
    assert b is not None and b.lock_id == a.lock_id
    assert b.expires_at > a.expires_at


def test_shared_leases_coexist_but_exclude_exclusive(dev):
    assert dev.lease("w/win", "k", holder=1, mode="shared", ttl=60) is not None
    assert dev.lease("w/win", "k", holder=2, mode="shared", ttl=60) is not None
    assert dev.lease("w/win", "k", holder=3, mode="exclusive", ttl=60) is None


def test_release_requires_the_holder(dev):
    a = dev.lease("w/win", "k", holder=1, ttl=60)
    assert dev.release(a.lock_id, 2) is False
    assert dev.release(a.lock_id, 1) is True
    assert dev.leases("w/win") == []


def test_leases_enumerates_live_locks(dev):
    dev.lease("w/a", "k", holder=1, ttl=60)
    dev.lease("w/b", "k", holder=2, ttl=60)
    assert len(dev.leases()) == 2
    assert len(dev.leases("w/a")) == 1


# --------------------------------------------------------------------------
# 6. clock
# --------------------------------------------------------------------------


def test_clock_is_monotone_and_shared(dev):
    a = dev.clock()
    b = dev.clock()
    assert b >= a
    assert a > 1_600_000_000, "the clock must be comparable across ranks, so it is absolute"


# --------------------------------------------------------------------------
# durability across processes
# --------------------------------------------------------------------------


def _child_append(args) -> int:
    name, root, i = args
    d = make_device(name, root)
    seq = d.append("event", {"rank": i, "kind": "child"})
    d.close()
    return seq


def _child_drain(args) -> list[int]:
    name, root, worker = args
    d = make_device(name, root)
    got = []
    while True:
        rec = d.match("msg", {"dst": 1, "state": "posted"}, {"state": "claimed", "by": worker})
        if rec is None:
            break
        got.append(rec["i"])
    d.close()
    return got


def test_durable_devices_survive_a_process_boundary(dev):
    """A rank is normally a whole OS process --- often one per executor turn.

    A device that only works within one interpreter cannot carry a real agent
    job, so durability is checked by actually forking.
    """
    if not dev.durable:
        pytest.skip(SKIP_VOLATILE)
    root = str(dev.root)
    ctx = mp.get_context("spawn")
    with ctx.Pool(2) as pool:
        pool.map(_child_append, [(dev.name, root, i) for i in range(4)])
    ranks = {r["rank"] for r in dev.scan("event", {"kind": "child"})}
    assert ranks == {0, 1, 2, 3}, "records written by other processes must be visible"


def test_durable_devices_serialise_concurrent_claims_across_processes(dev):
    """Atomic matching must hold across processes, not merely across threads."""
    if not dev.durable:
        pytest.skip(SKIP_VOLATILE)
    n = 24
    for i in range(n):
        dev.append("msg", {"comm": "world", "src": 0, "dst": 1, "tag": 0, "state": "posted", "i": i})
    ctx = mp.get_context("spawn")
    with ctx.Pool(4) as pool:
        results = pool.map(_child_drain, [(dev.name, str(dev.root), w) for w in range(4)])
    claimed = [i for r in results for i in r]
    assert sorted(claimed) == list(range(n)), "every message claimed exactly once across processes"


def test_object_store_round_trip(dev):
    dev.put_object("deadbeef", json.dumps({"a": 1}))
    assert json.loads(dev.get_object("deadbeef")) == {"a": 1}
    assert dev.get_object("nope") is None


def test_wipe_removes_state(dev):
    dev.append("event", {"rank": 1, "kind": "k"})
    dev.wipe()
    dev.initialize()
    assert dev.scan("event", {}) == []


def test_stats_names_the_device(dev):
    st = dev.stats()
    assert st["device"] == dev.name
    assert "durable" in st


def test_indexed_integer_fields_round_trip_as_integers(dev):
    """A device must not silently change a value's type.

    SQLite applies type affinity, so an integer written to a column declared TEXT
    is returned as a string.  Every predicate above the waist is written against
    Python values, so a device that coerces produces failures that look like
    protocol bugs and are not.  This is the assertion that keeps the three devices
    interchangeable.
    """
    dev.append("recvq", {"comm": "c", "dst": 3, "src_want": -1, "tag_want": -1,
                         "state": "open", "run": "r", "reqid": "abc"})
    (rec,) = dev.scan("recvq", {"reqid": "abc"})
    for field in ("dst", "src_want", "tag_want"):
        assert isinstance(rec[field], int), f"{field} came back as {type(rec[field]).__name__}"
    assert rec["src_want"] == -1
    assert dev.scan("recvq", {"src_want": -1}), "an integer predicate must match"
