"""Collective algorithms: correctness across algorithms, roots and sizes.

The central invariant tested here is the one that makes the design defensible:
for an *exact* operator, every algorithm must produce the result of the serial
left fold (:meth:`agentmpi.ops.Op.fold`).  If a tree algorithm and the reference
fold disagree, either the algorithm is wrong or the operator was mis-declared,
and both are bugs worth failing loudly on.  The tests also check that the
measured message counts agree with the closed-form cost formulas in
:mod:`agentmpi.cost`, which is how an implementation/model mismatch gets caught.
"""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi import algorithms
from agentmpi.constants import Associativity

SIZES = [1, 2, 3, 4, 5, 7, 8, 11, 16]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["dissemination", "linear", "central"])
def test_barrier_completes(tmp_path, size, algorithm):
    def rank_main(comm):
        res = comm.barrier(algorithm=algorithm, timeout=60.0, policy="wait" if algorithm != "central" else "proceed")
        assert res.complete, res
        return res.algorithm

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"b{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["flat", "binomial", "chain"])
@pytest.mark.parametrize("root", [0, 1])
def test_bcast_delivers_identical_content(tmp_path, size, algorithm, root):
    """Broadcast must be byte-identical at every rank, for any root or tree."""
    if root >= size:
        pytest.skip("root outside communicator")
    payload = {"spec": "translate faithfully", "terms": ["Hoefler", "Gropp"], "n": 7}

    def rank_main(comm):
        got = comm.bcast(payload if comm.rank == root else None, root=root, algorithm=algorithm)
        assert got == payload, (comm.rank, got)
        return got

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"bc{size}{algorithm}{root}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert all(o.value == payload for o in job.outcomes)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["linear", "binomial"])
@pytest.mark.parametrize("root", [0, 2])
def test_scatter_gather_roundtrip(tmp_path, size, algorithm, root):
    if root >= size:
        pytest.skip("root outside communicator")
    items = [{"unit": i, "text": f"chunk {i}"} for i in range(size)]

    def rank_main(comm):
        mine = comm.scatter(items if comm.rank == root else None, root=root, algorithm=algorithm)
        assert mine == items[comm.rank], (comm.rank, mine)
        out = comm.gather({"unit": mine["unit"], "done": True}, root=root, algorithm=algorithm)
        if comm.rank == root:
            assert out is not None and len(out) == size
            assert [o["unit"] for o in out] == list(range(size)), out
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"sg{size}{algorithm}{root}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["ring", "bruck", "recursive_doubling", "gather_bcast"])
def test_allgather_rank_ordered(tmp_path, size, algorithm):
    def rank_main(comm):
        out = comm.allgather({"r": comm.rank}, algorithm=algorithm)
        assert out == [{"r": i} for i in range(comm.size)], (comm.rank, out)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"ag{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["chain", "flat", "binomial"])
def test_reduce_matches_serial_fold(tmp_path, size, algorithm):
    """Every reduce algorithm must equal the reference fold for an exact op."""
    expected = ampi.SUM.fold(range(size))

    def rank_main(comm):
        out = comm.reduce(comm.rank, ampi.SUM, root=0, algorithm=algorithm)
        if comm.rank == 0:
            assert out == expected, (algorithm, out, expected)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"rd{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["chain", "flat", "binomial"])
def test_reduce_preserves_order_for_noncommutative_op(tmp_path, size, algorithm):
    """CONCAT is associative but not commutative: order must be rank order.

    This is the property MPI guarantees for user operators declared
    non-commutative, and it is the property a tree implementation is most likely
    to break, because a naive tree combines whichever child arrives first.
    """
    expected = "".join(f"[{i}]" for i in range(size))

    def rank_main(comm):
        out = comm.reduce(f"[{comm.rank}]", ampi.CONCAT, root=0, algorithm=algorithm)
        if comm.rank == 0:
            assert out == expected, (algorithm, out, expected)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"cc{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["reduce_bcast", "recursive_doubling"])
def test_allreduce_agrees_everywhere(tmp_path, size, algorithm):
    expected = sum(range(size))

    def rank_main(comm):
        out = comm.allreduce(comm.rank, ampi.SUM, algorithm=algorithm)
        assert out == expected, (comm.rank, algorithm, out, expected)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"ar{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
def test_allreduce_union_merges_glossaries(tmp_path, size):
    """UNION is exact, commutative and idempotent: the well-behaved merge."""

    def rank_main(comm):
        mine = {f"term{comm.rank}": f"rendering{comm.rank}", "shared": "agreed"}
        out = comm.allreduce(mine, ampi.UNION)
        assert out["shared"] == "agreed"
        assert len(out) == comm.size + 1, out
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"un{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    values = [o.value for o in job.outcomes]
    assert all(v == values[0] for v in values), "UNION allreduce must not diverge across ranks"


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["chain", "recursive_doubling"])
def test_scan_inclusive_and_exclusive(tmp_path, size, algorithm):
    def rank_main(comm):
        inc = comm.scan(comm.rank + 1, ampi.SUM, algorithm=algorithm)
        assert inc == sum(range(1, comm.rank + 2)), (comm.rank, algorithm, inc)
        exc = comm.scan(comm.rank + 1, ampi.SUM, exclusive=True, algorithm=algorithm)
        assert exc == sum(range(1, comm.rank + 1)), (comm.rank, algorithm, exc)
        return inc

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"sc{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [2, 3, 4, 5, 8])
@pytest.mark.parametrize("algorithm", ["pairwise", "linear", "bruck"])
def test_alltoall(tmp_path, size, algorithm):
    def rank_main(comm):
        send = [f"{comm.rank}->{j}" for j in range(comm.size)]
        got = comm.alltoall(send, algorithm=algorithm)
        assert got == [f"{i}->{comm.rank}" for i in range(comm.size)], (comm.rank, algorithm, got)
        return got

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"a2a{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [2, 3, 4, 6])
def test_reduce_scatter_partitions_the_fan_in(tmp_path, size):
    def rank_main(comm):
        send = [comm.rank * 10 + j for j in range(comm.size)]
        got = comm.reduce_scatter(send, ampi.SUM)
        expected = sum(i * 10 + comm.rank for i in range(comm.size))
        assert got == expected, (comm.rank, got, expected)
        return got

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"rs{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_nonassociative_op_rejects_tree_algorithms(tmp_path):
    """A declared-non-associative operator may only use the serial chain."""
    first_wins = ampi.Op(
        "FIRST", lambda a, b, ctx: a, commutative=False, associativity=Associativity.NONE
    )

    def rank_main(comm):
        with pytest.raises(ampi.AmpiUsageError):
            comm.reduce(comm.rank, first_wins, root=0, algorithm="binomial")
        out = comm.reduce(comm.rank, first_wins, root=0)  # defaults to chain
        return out

    job = ampi.launch(rank_main, size=4, root=tmp_path / "na")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [4, 8, 16])
def test_fold_depth_is_logarithmic_for_trees(tmp_path, size):
    """The fidelity-relevant statistic: tree depth vs chain depth."""
    depths: dict[str, int] = {}

    def make(algorithm):
        def rank_main(comm):
            comm.reduce(comm.rank, ampi.SUM, root=0, algorithm=algorithm)
            st = algorithms.LAST_STATS.get(comm.rt.wrank)
            return st.fold_depth if st else None

        return rank_main

    for algorithm, expected in (("chain", size - 1), ("binomial", max(1, (size - 1).bit_length()))):
        job = ampi.launch(make(algorithm), size=size, root=tmp_path / f"fd{size}{algorithm}")
        assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
        depths[algorithm] = job.value(0)
        assert job.value(0) == expected, (algorithm, size, job.value(0), expected)
    assert depths["binomial"] < depths["chain"] or size <= 2


@pytest.mark.parametrize("size", [4, 8])
def test_message_counts_match_cost_formulas(tmp_path, size):
    """Implementation and cost model must agree on message counts."""
    from agentmpi.cost import FORMULAS

    cases = [
        ("bcast", "flat"),
        ("bcast", "binomial"),
        ("bcast", "chain"),
        ("reduce", "flat"),
        ("reduce", "binomial"),
        ("reduce", "chain"),
        ("alltoall", "pairwise"),
        ("barrier", "dissemination"),
    ]
    for op, alg in cases:
        totals: list[int] = []

        def rank_main(comm, op=op, alg=alg):
            if op == "bcast":
                comm.bcast("x" if comm.rank == 0 else None, root=0, algorithm=alg)
            elif op == "reduce":
                comm.reduce(1, ampi.SUM, root=0, algorithm=alg)
            elif op == "alltoall":
                comm.alltoall([f"{comm.rank}->{j}" for j in range(comm.size)], algorithm=alg)
            else:
                comm.barrier(algorithm=alg, policy="wait")
            st = algorithms.LAST_STATS.get(comm.rt.wrank)
            return st.messages_sent if st else 0

        job = ampi.launch(rank_main, size=size, root=tmp_path / f"mc{size}{op}{alg}")
        assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
        measured = sum(o.value or 0 for o in job.outcomes)
        _, predicted, _, _ = FORMULAS[(op, alg)](size, 1)
        assert measured == int(predicted), f"{op}/{alg} p={size}: measured {measured} != model {predicted}"


def test_collectives_are_isolated_from_user_traffic(tmp_path):
    """A pending user message with a wildcard tag must not be eaten by a collective."""

    def rank_main(comm):
        if comm.rank == 1:
            comm.send("user-payload", 0, "user")
        comm.barrier(policy="wait")
        comm.bcast("spec" if comm.rank == 0 else None, root=0)
        total = comm.allreduce(1, ampi.SUM)
        assert total == comm.size
        if comm.rank == 0:
            msg = comm.recv(source=1, tag="user", timeout=30)
            assert msg.payload == "user-payload"
        return True

    job = ampi.launch(rank_main, size=4, root=tmp_path / "iso")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
