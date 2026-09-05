"""Randomised interleavings, checking the invariants a formal model would state.

A TLA+ model of this protocol would assert a handful of safety properties and
check them over a tiny state space. These tests assert the same properties and
check them over real interleavings of the real implementation, at sizes the model
checker could not reach. That is a different kind of evidence --- no exhaustive
coverage, but no refinement gap either --- and for a system whose bugs have so far
all been in the implementation rather than the design, it is the more useful kind.

Each test states its invariant in the docstring, drives many randomised
concurrent operation sequences, and checks the invariant on the resulting journal
rather than on what the operations returned. Checking the journal matters: an
operation that returns success and leaves inconsistent state is exactly the bug
class worth looking for.
"""

from __future__ import annotations

import concurrent.futures
import random
from collections import Counter

import pytest

from ampi import Ampi
from ampi.constants import ANY_SOURCE, ANY_TAG
from ampi.errors import AmpiError

SEEDS = [11, 12345, 20260831]


@pytest.fixture
def job(tmp_path):
    def make(size: int, device: str = "sqlite", **kw):
        root = str(tmp_path / f"j{size}{device}")
        Ampi.create(root, size, device=device, allow_volatile=True, force=True, **kw)
        ranks = [Ampi(root, rank=r, allow_volatile=True) for r in range(size)]
        for r in ranks:
            r.init()
        return ranks

    return make


def race(fns):
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(2, len(fns))) as pool:
        futures = [pool.submit(f) for f in fns]
        return [f.result(timeout=120) for f in futures]


# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_no_message_is_delivered_twice(job, seed):
    """INV1. Every posted message is claimed by at most one receive.

    The property the whole matching chapter rests on, and the one that cannot be
    recovered above the device.
    """
    rng = random.Random(seed)
    size = 6
    ranks = job(size)
    sends = []
    for i in range(60):
        src, dst = rng.randrange(size), rng.randrange(size)
        if src == dst:
            continue
        tag = rng.randrange(3)
        ranks[src].send(dst, {"i": i, "src": src}, tag=tag)
        sends.append((src, dst, tag, i))

    def drain(r):
        got = []
        while True:
            try:
                out = ranks[r].recv(ANY_SOURCE, tag=ANY_TAG, timeout=0.4, materialize=True)
            except AmpiError:
                return got
            got.append(out["body"]["i"])

    received = [i for part in race([lambda r=r: drain(r) for r in range(size)]) for i in part]
    assert len(received) == len(set(received)), "a message was delivered twice"
    assert set(received) == {s[3] for s in sends}, "a message was lost"


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_non_overtaking_holds_per_pair(job, seed):
    """INV2. Messages matching one receive are matched in send order."""
    rng = random.Random(seed)
    ranks = job(4)
    plan = [(rng.randrange(1, 4), rng.randrange(2)) for _ in range(40)]
    counters: Counter = Counter()
    for dst, tag in plan:
        counters[(dst, tag)] += 1
        ranks[0].send(dst, {"n": counters[(dst, tag)]}, tag=tag)

    for dst in range(1, 4):
        for tag in range(2):
            want = counters[(dst, tag)]
            got = []
            for _ in range(want):
                got.append(ranks[dst].recv(0, tag=tag, timeout=5, materialize=True)["body"]["n"])
            assert got == sorted(got) == list(range(1, want + 1)), (
                f"non-overtaking violated for dst={dst} tag={tag}: {got}"
            )


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_a_cell_has_exactly_one_winner_per_version(job, seed):
    """INV3. Versions are dense, monotone, and each is written by exactly one rank."""
    size = 8
    ranks = job(size)
    ranks[0].win_create("w")

    def hammer(r):
        wins = 0
        for _ in range(12):
            cell = ranks[r].device.read(ranks[r]._space("w"), "k")
            try:
                ranks[r].put("w", "k", {"by": r}, expect_version=cell.version if cell else 0)
                wins += 1
            except AmpiError:
                pass
        return wins

    race([lambda r=r: hammer(r) for r in range(size)])
    history = ranks[0].device.history(ranks[0]._space("w"), "k")
    versions = [c.version for c in history]
    assert versions == sorted(versions, reverse=True), "versions must be monotone"
    assert len(versions) == len(set(versions)), "two writers cannot share a version"
    assert versions[0] == len(versions), "versions must be dense from 1"


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_a_task_is_claimed_by_exactly_one_rank(job, seed):
    size = 8
    tasks = 30
    ranks = job(size)
    ranks[0].win_create("q")
    for t in range(tasks):
        ranks[0].put("q", f"t/{t}", "unclaimed")

    def grab(r):
        order = list(range(tasks))
        random.Random(seed + r).shuffle(order)
        return [t for t in order if ranks[r].claim("q", f"t/{t}")["claimed"]]

    claimed = race([lambda r=r: grab(r) for r in range(size)])
    flat = [t for part in claimed for t in part]
    assert sorted(flat) == list(range(tasks)), "each task claimed exactly once"


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_accumulate_loses_no_contribution(job, seed):
    """INV4. An atomic combine is lossless under any interleaving.

    The property that makes ``accumulate`` the right answer where a
    read-modify-write is a race.
    """
    size = 8
    ranks = job(size)
    ranks[0].win_create("f")

    def contribute(r):
        for i in range(6):
            ranks[r].accumulate("f", "all", {f"r{r}i{i}": r}, op="union")

    race([lambda r=r: contribute(r) for r in range(size)])
    from ampi.core.ops import value_of

    final = value_of(ranks[0].get("f", "all")["value"])
    assert len(final) == size * 6, f"lost contributions: {size * 6 - len(final)}"


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_a_fenced_executor_can_change_nothing(job, seed):
    """INV5. No operation by a stale epoch has any effect.

    What makes a zombie harmless. We check the journal rather than the return
    value, because an operation that raises after writing is the bug.
    """
    ranks = job(4)
    ranks[0].win_create("w")
    ranks[0].put("w", "k", "before")
    ranks[0].fence_rank(2)
    zombie = ranks[2]
    for attempt in (
        lambda: zombie.put("w", "k", "after"),
        lambda: zombie.send(0, "after"),
        lambda: zombie.barrier("late", timeout=1),
    ):
        with pytest.raises(AmpiError) as e:
            attempt()
        assert e.value.cls_name == "AMPI_ERR_FENCED"
    assert ranks[0].get("w", "k")["value"] == "before"
    assert ranks[0].device.scan("msg", {"src": 2}) == []


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_concurrent_shrinks_agree(job, seed):
    """INV6. Every rank that shrinks obtains the same communicator."""
    size = 8
    ranks = job(size)
    dead = [1, 5]
    for d in dead:
        ranks[0].kill(d)
    survivors = [r for r in range(size) if r not in dead]
    out = race([lambda r=r: ranks[r].comm_shrink("world", timeout=60) for r in survivors])
    assert len({o["name"] for o in out}) == 1
    assert len({tuple(o["members"]) for o in out}) == 1
    assert out[0]["members"] == survivors


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_the_ledger_never_exceeds_its_budget(job, seed):
    """INV7. No sequence of deliveries takes a rank past its budget.

    Degradation is the mechanism, and the invariant is what it exists to keep.
    """
    rng = random.Random(seed)
    budget = 4000
    ranks = job(3, ctx_budget=budget)
    for i in range(30):
        ranks[0].send(1, "word " * rng.randrange(50, 1500), tag=i % 8)
    for i in range(30):
        try:
            ranks[1].recv(0, tag=i % 8, timeout=2, materialize=True)
        except AmpiError:
            break
        led = ranks[1].ledger()
        assert led.used <= led.budget, f"ledger exceeded its budget: {led.used}/{led.budget}"
    assert ranks[1].ledger().used <= budget


@pytest.mark.parametrize("device", ["sqlite", "journal", "memory"])
def test_invariant_the_same_program_gives_the_same_result_on_every_device(job, device):
    """INV8. The portable layer is portable.

    Not a formal property so much as the claim the whole architecture rests on: a
    program's observable result must not depend on which transport is underneath.
    """
    size = 6
    ranks = job(size, device=device)
    out = race([
        lambda r=r: ranks[r].allreduce(
            "g", payload={"shared": f"v{r % 2}", f"own{r}": r}, op="union", timeout=60
        )
        for r in range(size)
    ])
    values = {tuple(sorted(o["value"].keys())) for o in out}
    conflicts = {tuple(sorted(o.get("conflicts", {}))) for o in out}
    assert len(values) == 1
    assert conflicts == {("shared",)}
    assert out[0]["fold_depth"] >= 1


def test_a_read_charges_the_ledger_without_writing_the_rank_row(job, monkeypatch):
    """Reads must not become device mutations.

    Every delivery charges the ledger, and the ledger lives in the rank row; but
    the row is written when something *else* writes it, not once per read.  On
    the git device each row write is a group commit, and a rank reading many
    cells in a row was paying one commit per cell.
    """
    ranks = job(2, device="memory")
    ranks[0].win_create("w")
    for i in range(6):
        ranks[0].put("w", f"k{i}", {"body": "word " * 40})
    before = ranks[1].ledger().used
    writes: list[str] = []
    real_cas = ranks[1].device.cas

    def counting_cas(space, key, *a, **kw):
        if space == "rank":
            writes.append(key)
        return real_cas(space, key, *a, **kw)

    monkeypatch.setattr(ranks[1].device, "cas", counting_cas)
    for i in range(6):
        ranks[1].get("w", f"k{i}")
    charged = ranks[1].ledger().used - before
    assert charged > 0                       # the ledger was charged, locally
    assert writes == []                      # and no rank row was written for it
    # a peer reading the row from the device sees the old ledger until a flush
    assert ranks[0].ledger(1).used == before
    ranks[1].heartbeat()                     # any own-row write carries the charge
    assert writes == ["1"]
    assert ranks[0].ledger(1).used == before + charged
    assert ranks[1].ledger().used == before + charged
    # eager credit a sender reserved in the row survives the receiver's flush
    ranks[1].get("w", "k0")
    ranks[0]._reserve_eager(1, 17)
    ranks[1].heartbeat()
    assert ranks[0].ledger(1).unexpected_used == 17
    assert ranks[0].ledger(1).used == ranks[1].ledger().used


def test_a_successor_starts_with_an_empty_ledger(job):
    """A respawned rank is a new executor: it inherits the budget, not the
    predecessor's consumption."""
    ranks = job(2, device="memory", ctx_budget=5000)
    ranks[0].send(1, "word " * 400, tag=1)
    ranks[1].recv(0, tag=1, timeout=2, materialize=True)
    used = ranks[1].ledger().used
    assert used > 0
    ranks[1].heartbeat()                       # flush the local charge to the row
    assert ranks[0].ledger(1).used == used
    ranks[0].kill(1, reason="test")
    out = ranks[0].respawn(1)
    assert out["epoch"] == 2
    led = ranks[0].ledger(1)
    assert led.used == 0 and led.peak == 0 and led.budget == 5000
