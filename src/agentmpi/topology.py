"""Virtual topologies and neighbourhood collectives.

MPI lets a program attach a *virtual topology* to a communicator — a Cartesian
grid or an arbitrary graph — and then communicate with "neighbours" rather than
with rank numbers.  Two things are bought: expressiveness (a stencil program
says ``up``/``down`` instead of computing ``rank ± nx``) and the possibility of
the implementation *reordering* ranks onto hardware so neighbours are close.

For agents the second benefit evaporates: there is no interconnect to be near.
The first, however, becomes far more valuable than it is in HPC, because the
communication *pattern* is the harness's design and the pattern determines the
cost.  A topology makes explicit which agents may talk to which, and that
constraint is the main lever on the quadratic blow-up that kills multi-agent
systems: an all-to-all group chat over *p* agents costs Θ(p²) messages and
Θ(p²n) tokens, while the same information flowing over a ring or a tree costs
Θ(p) and Θ(pn).  Restricting to a topology is how a harness buys scalability,
and the restriction is exactly what a "group chat" architecture refuses to make.

The two patterns that matter most:

**Ring / Cartesian halo exchange.**  A decomposition with boundary dependence —
translate chapter *i* consistently with its neighbours, implement module *i*
against the interfaces of the modules it borders — is a stencil computation, and
its idiom is ``neighbor_alltoall`` on a Cartesian topology: exchange *boundary*
information only.  This is the single most useful non-obvious transfer from HPC
to agent harnesses, because it turns a problem that looks sequential (each part
depends on its neighbours) into one barrier-separated parallel step.

**Dist-graph review structures.**  A code-review topology is a directed graph:
who reviews whom.  ``dist_graph_create`` plus ``neighbor_allgather`` expresses
"send my artifact to my reviewers, receive the artifacts I must review" in one
collective, with the fan-out fixed by the graph rather than by the population
size.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .constants import Mode
from .errors import AmpiUsageError

if TYPE_CHECKING:  # pragma: no cover
    from .comm import Communicator

#: Sentinel neighbour meaning "no neighbour in that direction", the analogue of
#: ``MPI_PROC_NULL``.  Communication with it is a no-op, which is what lets a
#: stencil loop be written without boundary special-casing.
PROC_NULL = -2


@dataclass
class CartTopology:
    """A Cartesian virtual topology over a communicator.

    ``dims`` gives the extent in each dimension, ``periods`` whether each
    dimension wraps.  A 1-D periodic topology is a ring; a 1-D non-periodic one
    is a chain (a pipeline); a 2-D one is a mesh, which is the natural shape for
    "each agent owns one (module, aspect) cell".
    """

    comm: Communicator
    dims: tuple[int, ...]
    periods: tuple[bool, ...]
    coords: tuple[int, ...] = field(default_factory=tuple)

    @property
    def ndim(self) -> int:
        return len(self.dims)

    def rank_of(self, coords: Sequence[int]) -> int:
        """Row-major coordinates to communicator rank, honouring periodicity."""
        idx = 0
        for d, (c, extent, periodic) in enumerate(zip(coords, self.dims, self.periods, strict=True)):
            if periodic:
                c %= extent
            elif not 0 <= c < extent:
                return PROC_NULL
            idx = idx * extent + c
        return idx if idx < self.comm.size else PROC_NULL

    def coords_of(self, rank: int) -> tuple[int, ...]:
        out: list[int] = []
        rem = rank
        for extent in reversed(self.dims):
            out.append(rem % extent)
            rem //= extent
        return tuple(reversed(out))

    def shift(self, dim: int, disp: int = 1) -> tuple[int, int]:
        """``MPI_Cart_shift``: ``(source, destination)`` for a shift along ``dim``.

        Returns ``PROC_NULL`` at a non-periodic boundary, so the caller writes
        one unconditional ``sendrecv`` rather than four cases.
        """
        up = list(self.coords)
        up[dim] += disp
        down = list(self.coords)
        down[dim] -= disp
        return self.rank_of(down), self.rank_of(up)

    def neighbors(self) -> list[int]:
        """All 2·ndim orthogonal neighbours, ``PROC_NULL`` where absent."""
        out: list[int] = []
        for d in range(self.ndim):
            src, dst = self.shift(d, 1)
            out.extend([src, dst])
        return out

    def sub(self, remain: Sequence[bool], *, name: str | None = None) -> Communicator | None:
        """``MPI_Cart_sub``: a communicator per slice of the grid.

        A 2-D mesh of agents indexed by (module, aspect) splits into
        "all agents working on this module" and "all agents doing this kind of
        review", and each slice can then run its own collectives.  This is the
        row/column communicator idiom from dense linear algebra, and it maps
        directly onto matrix-organised agent teams.
        """
        colour = 0
        stride = 1
        for d in reversed(range(self.ndim)):
            if not remain[d]:
                colour += self.coords[d] * stride
                stride *= self.dims[d]
        return self.comm.split(colour, name=name or f"{self.comm.name}.cartsub")


def dims_create(nnodes: int, ndims: int) -> tuple[int, ...]:
    """``MPI_Dims_create``: a balanced factorisation of ``nnodes`` into ``ndims``.

    Prefers near-square grids, which minimises the perimeter and hence the
    boundary-exchange volume — the same reason it matters for a stencil solver
    matters here, since the "boundary" is the context each agent must hold about
    its neighbours' work.
    """
    if ndims == 1:
        return (nnodes,)
    best: tuple[int, ...] | None = None
    best_score = float("inf")
    for a in range(1, int(math.isqrt(nnodes)) + 1):
        if nnodes % a:
            continue
        b = nnodes // a
        if ndims == 2:
            cand = (a, b)
        else:
            cand = (a, *dims_create(b, ndims - 1))
        score = sum(cand) - min(cand)
        if score < best_score:
            best_score, best = score, cand
    return best or (nnodes,) + (1,) * (ndims - 1)


def cart_create(
    comm: Communicator,
    dims: Sequence[int] | None = None,
    periods: Sequence[bool] | None = None,
    *,
    ndims: int = 1,
) -> CartTopology:
    """Attach a Cartesian topology to ``comm``."""
    dims_t = tuple(dims) if dims else dims_create(comm.size, ndims)
    if math.prod(dims_t) < comm.size:
        raise AmpiUsageError("cartesian dims do not cover the communicator", dims=dims_t, size=comm.size)
    periods_t = tuple(periods) if periods is not None else (False,) * len(dims_t)
    topo = CartTopology(comm=comm, dims=dims_t, periods=periods_t)
    topo.coords = topo.coords_of(comm.rank)
    comm.fabric.emit(
        "topo.cart_create", rank=comm.rt.wrank, ctx=comm.ctx, dims=list(dims_t), periods=list(periods_t), coords=list(topo.coords)
    )
    return topo


@dataclass
class GraphTopology:
    """A distributed graph topology: explicit in- and out-neighbour lists.

    ``MPI_Dist_graph_create_adjacent``'s model, which is the one worth having:
    every rank declares who it receives from and who it sends to, and the
    protocol never needs a global view.  For a review structure that means each
    author declares its reviewers and each reviewer its authors, and the graph is
    consistent by construction if the harness derives both from one edge list.
    """

    comm: Communicator
    sources: tuple[int, ...]
    destinations: tuple[int, ...]

    @property
    def indegree(self) -> int:
        return len(self.sources)

    @property
    def outdegree(self) -> int:
        return len(self.destinations)


def dist_graph_create(comm: Communicator, edges: Sequence[tuple[int, int]]) -> GraphTopology:
    """Build a graph topology from a global edge list ``(from, to)``."""
    r = comm.rank
    srcs = tuple(sorted({a for a, b in edges if b == r}))
    dsts = tuple(sorted({b for a, b in edges if a == r}))
    comm.fabric.emit(
        "topo.graph_create", rank=comm.rt.wrank, ctx=comm.ctx, sources=list(srcs), destinations=list(dsts), n_edges=len(edges)
    )
    return GraphTopology(comm=comm, sources=srcs, destinations=dsts)


def ring_edges(p: int, *, bidirectional: bool = True) -> list[tuple[int, int]]:
    out = [(i, (i + 1) % p) for i in range(p)]
    if bidirectional:
        out += [((i + 1) % p, i) for i in range(p)]
    return out


def review_edges(p: int, *, fanout: int = 2, offset: int = 1) -> list[tuple[int, int]]:
    """A regular review graph: each rank is reviewed by ``fanout`` peers.

    A circulant graph, chosen because it gives every rank exactly ``fanout``
    reviewers and exactly ``fanout`` review assignments with no coordinator and
    no rank reviewing itself.  Regularity matters: an irregular review
    assignment makes some agent's turn much longer than the rest, and the
    integration barrier waits for it.
    """
    if fanout >= p:
        raise AmpiUsageError("fanout must be smaller than the population", fanout=fanout, p=p)
    return [(i, (i + offset + k) % p) for i in range(p) for k in range(fanout)]


# ---------------------------------------------------------------- collectives


def neighbor_allgather(
    topo: CartTopology | GraphTopology,
    payload: Any,
    *,
    timeout: float | None = 900.0,
    mode: Mode | str = Mode.AUTO,
    admit: bool = False,
    label: str = "",
) -> list[Any]:
    """Send ``payload`` to every neighbour, receive one from each.

    ``MPI_Neighbor_allgather``.  Cost is Θ(degree) rather than Θ(p), which is
    the entire point: it is how a harness gets the coordination benefit of an
    allgather at the price of a constant fan-out.  For a ring the degree is 2, so
    boundary consistency across *p* work units costs 2*p* messages instead of
    *p*².
    """
    comm = topo.comm
    epoch = comm._next_epoch("neighbor_allgather")
    itag = comm._itag("nbr_ag", epoch)
    srcs, dsts = _neighbor_lists(topo)
    t0 = _now()
    for d in dsts:
        if d != PROC_NULL:
            comm._csend(payload, d, itag, mode=mode, timeout=timeout)
    out: list[Any] = []
    for s in srcs:
        if s == PROC_NULL:
            out.append(None)
            continue
        out.append(comm._crecv(s, itag, timeout=timeout, admit=admit))
    comm.fabric.emit(
        "coll.neighbor_allgather",
        rank=comm.rt.wrank,
        ctx=comm.ctx,
        indegree=len(srcs),
        outdegree=len(dsts),
        wall_s=round(_now() - t0, 4),
        label=label,
    )
    return out


def neighbor_alltoall(
    topo: CartTopology | GraphTopology,
    payloads: Sequence[Any],
    *,
    timeout: float | None = 900.0,
    mode: Mode | str = Mode.AUTO,
    admit: bool = False,
    label: str = "",
) -> list[Any]:
    """Send a *different* payload to each neighbour.

    ``MPI_Neighbor_alltoall``.  The halo-exchange primitive: rank *i* sends its
    left boundary leftward and its right boundary rightward.  For the
    book-translation harness that is "here is how I rendered the last paragraph
    of my section, and here is the terminology I introduced", exchanged only with
    the two sections that actually abut mine.
    """
    comm = topo.comm
    srcs, dsts = _neighbor_lists(topo)
    if len(payloads) != len(dsts):
        raise AmpiUsageError("one payload per out-neighbour required", got=len(payloads), outdegree=len(dsts))
    epoch = comm._next_epoch("neighbor_alltoall")
    itag = comm._itag("nbr_a2a", epoch)
    t0 = _now()
    for d, pl in zip(dsts, payloads, strict=True):
        if d != PROC_NULL:
            comm._csend(pl, d, f"{itag}:{d}", mode=mode, timeout=timeout)
    out: list[Any] = []
    for s in srcs:
        if s == PROC_NULL:
            out.append(None)
            continue
        out.append(comm._crecv(s, f"{itag}:{comm.rank}", timeout=timeout, admit=admit))
    comm.fabric.emit(
        "coll.neighbor_alltoall",
        rank=comm.rt.wrank,
        ctx=comm.ctx,
        indegree=len(srcs),
        outdegree=len(dsts),
        wall_s=round(_now() - t0, 4),
        label=label,
    )
    return out


def halo_exchange(
    topo: CartTopology,
    left_boundary: Any,
    right_boundary: Any,
    *,
    dim: int = 0,
    timeout: float | None = 900.0,
    label: str = "",
) -> tuple[Any, Any]:
    """One-dimensional halo exchange: returns ``(from_left, from_right)``.

    Written with ``sendrecv`` in both directions, which is the deadlock-free
    formulation.  The naive "send both, then receive both" version deadlocks
    under bounded eager credit for exactly the reason it does in MPI, and the
    transport-safety experiment demonstrates it.
    """
    comm = topo.comm
    epoch = comm._next_epoch("halo")
    itag = comm._itag("halo", epoch)
    left, right = topo.shift(dim, 1)

    from_left: Any = None
    from_right: Any = None
    # Rightward shift, then leftward.  Each half uses sendrecv when both peers
    # exist so the exchange is safe under any buffering; the one-sided cases
    # occur only at non-periodic boundaries and cannot deadlock.
    if right != PROC_NULL and left != PROC_NULL:
        msg = comm.sendrecv(
            right_boundary, dest=right, source=left, sendtag=f"{itag}:R", recvtag=f"{itag}:R",
            timeout=timeout, admit=False, _internal=True,
        )
        from_left = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
        msg = comm.sendrecv(
            left_boundary, dest=left, source=right, sendtag=f"{itag}:L", recvtag=f"{itag}:L",
            timeout=timeout, admit=False, _internal=True,
        )
        from_right = msg.payload if msg.payload is not None else comm.fabric.blobs.get(msg.digest, msg.kind)
    else:
        if right != PROC_NULL:
            comm._csend(right_boundary, right, f"{itag}:R", timeout=timeout)
            from_right = comm._crecv(right, f"{itag}:L", timeout=timeout, admit=False)
        if left != PROC_NULL:
            from_left = comm._crecv(left, f"{itag}:R", timeout=timeout, admit=False)
            comm._csend(left_boundary, left, f"{itag}:L", timeout=timeout)
    comm.fabric.emit(
        "coll.halo_exchange", rank=comm.rt.wrank, ctx=comm.ctx, left=left, right=right, label=label
    )
    return from_left, from_right


def _neighbor_lists(topo: CartTopology | GraphTopology) -> tuple[list[int], list[int]]:
    if isinstance(topo, GraphTopology):
        return list(topo.sources), list(topo.destinations)
    nbrs = topo.neighbors()
    return list(nbrs), list(nbrs)


def _now() -> float:
    return time.time()
