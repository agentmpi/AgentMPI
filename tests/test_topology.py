"""Virtual topologies and neighbourhood collectives."""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi.topology import PROC_NULL, dims_create


def test_dims_create_prefers_balanced_grids():
    assert dims_create(16, 2) == (4, 4)
    assert dims_create(12, 2) == (3, 4)
    assert dims_create(7, 1) == (7,)
    assert sorted(dims_create(24, 2)) == [4, 6]


@pytest.mark.parametrize("size", [2, 4, 5, 8])
def test_ring_halo_exchange(tmp_path, size):
    """Boundary context flows only between abutting work units: 2p messages."""

    def rank_main(comm):
        topo = ampi.cart_create(comm, dims=[comm.size], periods=[True])
        left, right = topo.shift(0, 1)
        assert left == (comm.rank - 1) % comm.size
        assert right == (comm.rank + 1) % comm.size
        got_left, got_right = ampi.halo_exchange(
            topo, left_boundary=f"L{comm.rank}", right_boundary=f"R{comm.rank}"
        )
        assert got_left == f"R{(comm.rank - 1) % comm.size}", (comm.rank, got_left)
        assert got_right == f"L{(comm.rank + 1) % comm.size}", (comm.rank, got_right)
        return (got_left, got_right)

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"halo{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [3, 4, 6])
def test_chain_topology_has_proc_null_boundaries(tmp_path, size):
    """A non-periodic 1-D topology is a pipeline with PROC_NULL at the ends."""

    def rank_main(comm):
        topo = ampi.cart_create(comm, dims=[comm.size], periods=[False])
        left, right = topo.shift(0, 1)
        if comm.rank == 0:
            assert left == PROC_NULL
        if comm.rank == comm.size - 1:
            assert right == PROC_NULL
        got_left, got_right = ampi.halo_exchange(topo, f"L{comm.rank}", f"R{comm.rank}")
        assert (got_left is None) == (comm.rank == 0)
        assert (got_right is None) == (comm.rank == comm.size - 1)
        return True

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"chain{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_2d_mesh_and_cart_sub(tmp_path):
    """A (module x aspect) mesh splits into row and column communicators."""

    def rank_main(comm):
        topo = ampi.cart_create(comm, dims=[3, 4], periods=[False, False])
        assert topo.coords == topo.coords_of(comm.rank)
        assert topo.rank_of(topo.coords) == comm.rank
        row = topo.sub([False, True], name="row")   # vary the second dim: one comm per row
        col = topo.sub([True, False], name="col")
        assert row is not None and col is not None
        assert row.size == 4 and col.size == 3
        rsum = row.allreduce(1, ampi.SUM)
        csum = col.allreduce(1, ampi.SUM)
        return (rsum, csum)

    job = ampi.launch(rank_main, size=12, root=tmp_path / "mesh")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert all(o.value == (4, 3) for o in job.outcomes)


@pytest.mark.parametrize("size,fanout", [(4, 1), (6, 2), (8, 3)])
def test_review_graph_is_regular(tmp_path, size, fanout):
    """Each rank reviews and is reviewed by exactly ``fanout`` peers."""
    edges = ampi.review_edges(size, fanout=fanout)

    def rank_main(comm):
        topo = ampi.dist_graph_create(comm, edges)
        assert topo.outdegree == fanout, (comm.rank, topo.destinations)
        assert topo.indegree == fanout, (comm.rank, topo.sources)
        assert comm.rank not in topo.destinations
        received = ampi.neighbor_allgather(topo, f"artifact-{comm.rank}")
        assert sorted(received) == sorted(f"artifact-{s}" for s in topo.sources)
        return len(received)

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"rev{size}{fanout}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert all(o.value == fanout for o in job.outcomes)


def test_neighbor_alltoall_sends_distinct_payloads(tmp_path):
    size, fanout = 6, 2
    edges = ampi.review_edges(size, fanout=fanout)

    def rank_main(comm):
        topo = ampi.dist_graph_create(comm, edges)
        payloads = [f"{comm.rank}->{d}" for d in topo.destinations]
        got = ampi.neighbor_alltoall(topo, payloads)
        assert sorted(got) == sorted(f"{s}->{comm.rank}" for s in topo.sources), (comm.rank, got)
        return got

    job = ampi.launch(rank_main, size=size, root=tmp_path / "na2a")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_neighborhood_beats_alltoall_in_message_count(tmp_path):
    """The scalability argument, measured.

    A full alltoall over p ranks costs p(p-1) messages; a degree-2 review graph
    costs 2p.  The gap is the reason a harness should express review as a
    topology rather than as a group conversation.
    """
    size = 8

    def full(comm):
        before = comm.rt.cost.n_messages_sent
        comm.alltoall([f"{comm.rank}->{j}" for j in range(comm.size)], algorithm="pairwise")
        return comm.rt.cost.n_messages_sent - before

    def neighborhood(comm):
        topo = ampi.dist_graph_create(comm, ampi.review_edges(comm.size, fanout=2))
        before = comm.rt.cost.n_messages_sent
        ampi.neighbor_allgather(topo, f"artifact-{comm.rank}")
        return comm.rt.cost.n_messages_sent - before

    j1 = ampi.launch(full, size=size, root=tmp_path / "full")
    j2 = ampi.launch(neighborhood, size=size, root=tmp_path / "nbr")
    assert j1.ok and j2.ok
    total_full = sum(o.value for o in j1.outcomes)
    total_nbr = sum(o.value for o in j2.outcomes)
    assert total_full == size * (size - 1), total_full
    assert total_nbr == 2 * size, total_nbr
    assert total_full > 3 * total_nbr
