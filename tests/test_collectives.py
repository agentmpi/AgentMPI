"""Collective correctness across sizes and algorithms.

Every collective is checked against a sequential reference at several
process counts, including non-powers of two, which is where tree and
recursive-doubling algorithms traditionally break.
"""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi import sim
from agentmpi.constants import CollAlgorithm

SIZES = [1, 2, 3, 4, 5, 7, 8, 11, 16]


@pytest.mark.parametrize("p", SIZES)
def test_barrier(p):
    import threading

    inside = []
    lock = threading.Lock()

    def body(comm):
        with lock:
            inside.append(("before", comm.rank))
        comm.barrier(timeout=30)
        with lock:
            inside.append(("after", comm.rank))
        return True

    r = sim.run(p, body, timeout=60)
    r.raise_errors()
    # No rank may leave the barrier before every rank has entered it.
    last_before = max(i for i, (phase, _) in enumerate(inside) if phase == "before")
    first_after = min(i for i, (phase, _) in enumerate(inside) if phase == "after")
    assert first_after > last_before - 1


@pytest.mark.parametrize("p", SIZES)
@pytest.mark.parametrize("algorithm", [CollAlgorithm.AUTO, CollAlgorithm.FLAT,
                                       CollAlgorithm.BINOMIAL, CollAlgorithm.CHAIN])
def test_bcast(p, algorithm):
    def body(comm):
        value = {"payload": "spec-v1", "from": 0} if comm.rank == 0 else None
        got = comm.bcast(value, root=0, datatype="json", algorithm=algorithm, timeout=60)
        return got

    r = sim.run(p, body, timeout=90)
    r.raise_errors()
    assert all(v == {"payload": "spec-v1", "from": 0} for v in r.ordered())


@pytest.mark.parametrize("p", [2, 3, 4, 5, 8])
def test_bcast_nonzero_root(p):
    root = p - 1

    def body(comm):
        value = "from-the-end" if comm.rank == root else None
        return comm.bcast(value, root=root, timeout=60)

    r = sim.run(p, body, timeout=90)
    r.raise_errors()
    assert all(v == "from-the-end" for v in r.ordered())


@pytest.mark.parametrize("p", SIZES)
def test_scatter_gather_roundtrip(p):
    def body(comm):
        chunks = [f"chunk{i}" for i in range(comm.size)] if comm.rank == 0 else None
        mine = comm.scatter(chunks, root=0, timeout=60)
        result = comm.gather(mine.upper(), root=0, timeout=60)
        return result

    r = sim.run(p, body, timeout=90)
    r.raise_errors()
    assert r.results[0] == [f"CHUNK{i}" for i in range(p)]
    assert all(r.results[i] is None for i in range(1, p))


@pytest.mark.parametrize("p", [2, 4, 8])
def test_gather_binomial_matches_flat(p):
    def make(alg):
        def body(comm):
            return comm.gather(f"v{comm.rank}", root=0, algorithm=alg, timeout=60)
        return body

    flat = sim.run(p, make(CollAlgorithm.FLAT), timeout=90)
    tree = sim.run(p, make(CollAlgorithm.BINOMIAL), timeout=90)
    flat.raise_errors()
    tree.raise_errors()
    assert flat.results[0] == tree.results[0] == [f"v{i}" for i in range(p)]


@pytest.mark.parametrize("p", SIZES)
@pytest.mark.parametrize("algorithm", [CollAlgorithm.AUTO, CollAlgorithm.FLAT,
                                       CollAlgorithm.RING, CollAlgorithm.BRUCK])
def test_allgather(p, algorithm):
    def body(comm):
        return comm.allgather(f"v{comm.rank}", algorithm=algorithm, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    expected = [f"v{i}" for i in range(p)]
    for rank, got in r.results.items():
        assert got == expected, f"rank {rank} got {got}"


@pytest.mark.parametrize("p", [2, 4, 8, 16])
def test_allgather_recursive_doubling(p):
    def body(comm):
        return comm.allgather(f"v{comm.rank}", algorithm=CollAlgorithm.RECURSIVE_DOUBLING,
                              timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    assert all(got == [f"v{i}" for i in range(p)] for got in r.ordered())


@pytest.mark.parametrize("p", SIZES)
def test_reduce_sum_flat(p):
    def body(comm):
        return comm.reduce(comm.rank, ampi.SUM, root=0, algorithm=CollAlgorithm.FLAT,
                           timeout=60)

    r = sim.run(p, body, timeout=90)
    r.raise_errors()
    assert r.results[0] == sum(range(p))


@pytest.mark.parametrize("p", SIZES)
def test_reduce_knomial_tree(p):
    def body(comm):
        return comm.reduce(comm.rank, ampi.SUM, root=0, algorithm=CollAlgorithm.KNOMIAL,
                           timeout=60)

    r = sim.run(p, body, timeout=90)
    r.raise_errors()
    assert r.results[0] == sum(range(p))
    assert all(r.results[i] is None for i in range(1, p))


@pytest.mark.parametrize("p", [2, 3, 4, 7, 8])
def test_reduce_nonzero_root(p):
    root = p - 1

    def body(comm):
        return comm.reduce(comm.rank + 1, ampi.SUM, root=root, timeout=60)

    r = sim.run(p, body, timeout=90)
    r.raise_errors()
    assert r.results[root] == sum(range(1, p + 1))


@pytest.mark.parametrize("p", SIZES)
def test_allreduce(p):
    def body(comm):
        return comm.allreduce(comm.rank, ampi.SUM, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    assert all(v == sum(range(p)) for v in r.ordered())


@pytest.mark.parametrize("p", [2, 4, 8, 16])
def test_allreduce_recursive_doubling_matches_flat(p):
    def make(alg):
        def body(comm):
            return comm.allreduce(comm.rank * 3, ampi.SUM, algorithm=alg, timeout=60)
        return body

    a = sim.run(p, make(CollAlgorithm.RECURSIVE_DOUBLING), timeout=120)
    b = sim.run(p, make(CollAlgorithm.FLAT), timeout=120)
    a.raise_errors()
    b.raise_errors()
    assert set(a.ordered()) == {sum(range(p)) * 3}
    assert b.results[0] == sum(range(p)) * 3


@pytest.mark.parametrize("p", [2, 3, 4, 5, 8])
def test_allreduce_union_is_order_insensitive(p):
    def body(comm):
        return comm.allreduce({f"k{comm.rank}": [comm.rank]}, ampi.UNION, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    expected = {f"k{i}": [i] for i in range(p)}
    assert all(v == expected for v in r.ordered())


@pytest.mark.parametrize("p", SIZES)
def test_scan_inclusive(p):
    def body(comm):
        return comm.scan(comm.rank, ampi.SUM, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    for rank in range(p):
        assert r.results[rank] == sum(range(rank + 1)), f"rank {rank}"


@pytest.mark.parametrize("p", SIZES)
def test_exscan(p):
    def body(comm):
        return comm.exscan(comm.rank + 1, ampi.SUM, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    for rank in range(p):
        expected = sum(range(1, rank + 1))
        assert r.results[rank] == expected, f"rank {rank}: {r.results[rank]} != {expected}"


@pytest.mark.parametrize("p", [2, 3, 4, 5, 8])
def test_exscan_glossary_prefix(p):
    """The motivating use: chapter i sees the glossary of chapters < i."""

    def body(comm):
        mine = {f"term{comm.rank}": [f"gloss{comm.rank}"]}
        return comm.exscan(mine, ampi.UNION, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    for rank in range(p):
        expected = {f"term{i}": [f"gloss{i}"] for i in range(rank)}
        assert (r.results[rank] or {}) == expected, f"rank {rank}"


@pytest.mark.parametrize("p", [2, 3, 4, 5, 8])
def test_scan_chain_matches_parallel_prefix(p):
    def make(alg):
        def body(comm):
            return comm.scan(comm.rank + 1, ampi.SUM, algorithm=alg, timeout=60)
        return body

    chain = sim.run(p, make(CollAlgorithm.CHAIN), timeout=120)
    fast = sim.run(p, make(CollAlgorithm.RECURSIVE_DOUBLING), timeout=120)
    chain.raise_errors()
    fast.raise_errors()
    assert chain.ordered() == fast.ordered()


@pytest.mark.parametrize("p", [2, 3, 4, 5, 8])
def test_alltoall(p):
    def body(comm):
        return comm.alltoall([f"{comm.rank}->{j}" for j in range(comm.size)], timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    for rank in range(p):
        assert r.results[rank] == [f"{i}->{rank}" for i in range(p)]


@pytest.mark.parametrize("p", [2, 3, 4, 8])
def test_reduce_scatter(p):
    def body(comm):
        return comm.reduce_scatter([comm.rank * 10 + j for j in range(comm.size)],
                                   ampi.SUM, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    for rank in range(p):
        assert r.results[rank] == sum(i * 10 + rank for i in range(p))


def test_non_associative_op_forced_flat():
    """A vote must not be evaluated in a tree; the runtime must refuse."""
    from agentmpi.ops import check_op_for_tree
    import agentmpi.errors as errors

    with pytest.raises(errors.OpError):
        check_op_for_tree(ampi.VOTE, depth=3)


@pytest.mark.parametrize("p", [3, 5, 8])
def test_vote_allreduce_reaches_consensus(p):
    def body(comm):
        # A minority disagrees.
        answer = "B" if comm.rank == 0 else "A"
        return comm.allreduce(answer, ampi.VOTE, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    for rank in range(p):
        assert r.results[rank]["value"] == "A"
        assert r.results[rank]["votes"] == p - 1


@pytest.mark.parametrize("p", [2, 4, 6])
def test_collectives_in_sequence_do_not_interfere(p):
    def body(comm):
        a = comm.bcast("one" if comm.rank == 0 else None, 0, timeout=60)
        b = comm.allreduce(comm.rank, ampi.SUM, timeout=60)
        c = comm.allgather(comm.rank, timeout=60)
        comm.barrier(timeout=60)
        d = comm.bcast("two" if comm.rank == 0 else None, 0, timeout=60)
        return (a, b, c, d)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    for rank in range(p):
        a, b, c, d = r.results[rank]
        assert a == "one" and d == "two"
        assert b == sum(range(p))
        assert c == list(range(p))


@pytest.mark.parametrize("p", list(range(2, 18)))
def test_binomial_scatter_delivers_exactly_one_item_per_rank(p):
    """Every size, not just the usual suspects.

    A rank in our 13-rank translation run reported that its scatter slice
    held two chunks rather than one, which looked like a distribution bug.
    It was not: recursive halving has interior nodes hold their subtree's
    block and forward the remainder, so a mid-tree rank transiently holds
    more than its own item and keeps the first. This test pins the invariant
    that matters -- every rank ends with exactly its own item -- at every
    size from 2 to 17, since the tree's shape is most irregular at sizes that
    are neither powers of two nor small.
    """

    def body(comm):
        values = [f"c{i}" for i in range(comm.size)] if comm.rank == 0 else None
        return comm.scatterv(values, 0, datatype="json",
                             algorithm=CollAlgorithm.BINOMIAL, timeout=60)

    r = sim.run(p, body, timeout=120)
    r.raise_errors()
    assert [r.results.get(i) for i in range(p)] == [f"c{i}" for i in range(p)]
