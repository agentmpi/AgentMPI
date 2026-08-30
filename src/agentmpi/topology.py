"""Virtual topologies.

MPI lets a program attach a topology to a communicator -- a Cartesian grid
for a stencil code, or an arbitrary graph -- and then address peers by
*position* rather than by rank.  Two things follow: the code becomes
readable (``up``/``down`` instead of ``rank - width``), and the
implementation gains the freedom to remap ranks onto hardware so that
neighbours are physically close.

For agents, the topology *is* the organisation chart, and declaring it has
three payoffs.  It makes the communication pattern explicit and therefore
analysable -- a harness whose graph has a diameter of 8 will have a critical
path of at least 8 turns, and you can see that before you spend a dollar.
It enables neighbourhood collectives, which are the difference between an
``O(p)`` review round and an ``O(p^2)`` one.  And it lets the runtime detect
structural pathologies: a cycle in a dependency graph is a deadlock, a hub
rank is a context bottleneck, and both are visible in the adjacency
structure without running anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .constants import PROC_NULL
from .errors import ArgError, TopologyError


@dataclass
class CartesianTopology:
    """``AMPI_Cart_create``.

    The canonical agent grid is (stage x shard): rows are pipeline stages
    (draft, translate, edit, verify) and columns are independent shards of
    the corpus.  A rank then talks *down* its column for the pipeline and
    *across* its row for consistency, which is exactly a 2-D stencil.
    """

    dims: tuple[int, ...]
    periods: tuple[bool, ...]
    size: int

    def __post_init__(self) -> None:
        if len(self.dims) != len(self.periods):
            raise ArgError("dims and periods must have the same length")
        if math.prod(self.dims) > self.size:
            raise TopologyError("Cartesian grid larger than the communicator",
                                dims=self.dims, size=self.size)

    def coords(self, rank: int) -> tuple[int, ...]:
        out: list[int] = []
        rem = rank
        for d in reversed(self.dims):
            out.append(rem % d)
            rem //= d
        return tuple(reversed(out))

    def rank_of(self, coords: Sequence[int]) -> int:
        if len(coords) != len(self.dims):
            raise ArgError("wrong number of coordinates")
        rank = 0
        for c, d in zip(coords, self.dims):
            if not 0 <= c < d:
                return PROC_NULL
            rank = rank * d + c
        return rank

    def shift(self, rank: int, direction: int, displacement: int) -> tuple[int, int]:
        """``AMPI_Cart_shift`` -> ``(source, destination)``."""
        coords = list(self.coords(rank))
        d = self.dims[direction]

        def _move(delta: int) -> int:
            c = list(coords)
            c[direction] += delta
            if not 0 <= c[direction] < d:
                if not self.periods[direction]:
                    return PROC_NULL
                c[direction] %= d
            return self.rank_of(c)

        return _move(-displacement), _move(displacement)

    def neighbors(self, rank: int) -> list[int]:
        out: list[int] = []
        for axis in range(len(self.dims)):
            src, dst = self.shift(rank, axis, 1)
            out.extend(r for r in (src, dst) if r != PROC_NULL)
        return out

    def sub(self, remain: Sequence[bool]) -> "CartesianTopology":
        """``AMPI_Cart_sub`` -- the sub-grid along the retained axes."""
        dims = tuple(d for d, keep in zip(self.dims, remain) if keep)
        periods = tuple(p for p, keep in zip(self.periods, remain) if keep)
        return CartesianTopology(dims, periods, math.prod(dims) or 1)


@dataclass
class GraphTopology:
    """``AMPI_Dist_graph_create`` -- an arbitrary directed communication graph."""

    size: int
    edges: dict[int, list[int]] = field(default_factory=dict)
    weights: dict[tuple[int, int], float] = field(default_factory=dict)
    labels: dict[int, str] = field(default_factory=dict)

    def out_neighbors(self, rank: int) -> list[int]:
        return list(self.edges.get(rank, ()))

    def in_neighbors(self, rank: int) -> list[int]:
        return [s for s, dsts in self.edges.items() if rank in dsts]

    def degree(self, rank: int) -> tuple[int, int]:
        return len(self.in_neighbors(rank)), len(self.out_neighbors(rank))

    # -- structural analysis ----------------------------------------------
    def cycles(self) -> list[list[int]]:
        """Directed cycles.

        A cycle in a *dependency* topology means two agents each wait for the
        other, which is a deadlock; detecting it statically converts a
        runtime hang into a startup error.  The classic MPI advice -- use
        ``MPI_Sendrecv`` to break exchange cycles -- is the same fix, and
        AgentMPI can now say precisely where it is needed.
        """
        found: list[list[int]] = []
        colour: dict[int, int] = {}
        stack: list[int] = []

        def visit(u: int) -> None:
            colour[u] = 1
            stack.append(u)
            for v in self.edges.get(u, ()):
                if colour.get(v, 0) == 0:
                    visit(v)
                elif colour.get(v) == 1:
                    found.append(stack[stack.index(v):] + [v])
            stack.pop()
            colour[u] = 2

        for node in range(self.size):
            if colour.get(node, 0) == 0:
                visit(node)
        return found

    def diameter(self) -> int:
        """Longest shortest path -- a lower bound on the critical path in turns."""
        best = 0
        for start in range(self.size):
            dist = {start: 0}
            frontier = [start]
            while frontier:
                nxt: list[int] = []
                for u in frontier:
                    for v in self.edges.get(u, ()):
                        if v not in dist:
                            dist[v] = dist[u] + 1
                            nxt.append(v)
                frontier = nxt
            best = max(best, max(dist.values(), default=0))
        return best

    def hubs(self, threshold: float = 2.0) -> list[int]:
        """Ranks whose in-degree is far above average: context bottlenecks."""
        degrees = [len(self.in_neighbors(r)) for r in range(self.size)]
        if not degrees:
            return []
        mean = sum(degrees) / len(degrees)
        return [r for r, d in enumerate(degrees) if mean > 0 and d >= threshold * mean]

    def topological_order(self) -> list[int] | None:
        indeg = {r: len(self.in_neighbors(r)) for r in range(self.size)}
        ready = sorted(r for r, d in indeg.items() if d == 0)
        order: list[int] = []
        while ready:
            u = ready.pop(0)
            order.append(u)
            for v in self.edges.get(u, ()):
                indeg[v] -= 1
                if indeg[v] == 0:
                    ready.append(v)
            ready.sort()
        return order if len(order) == self.size else None

    def critical_path_length(self, cost: dict[int, float] | None = None) -> float:
        """Longest weighted path: the harness's serial floor, in turns or seconds."""
        order = self.topological_order()
        if order is None:
            return float("inf")
        cost = cost or {r: 1.0 for r in range(self.size)}
        best = {r: cost.get(r, 1.0) for r in range(self.size)}
        for u in order:
            for v in self.edges.get(u, ()):
                best[v] = max(best[v], best[u] + cost.get(v, 1.0))
        return max(best.values(), default=0.0)


# --------------------------------------------------------------------------
# Communicator attachment
# --------------------------------------------------------------------------

def cart_create(comm, dims: Sequence[int], periods: Sequence[bool] | None = None):
    topo = CartesianTopology(tuple(dims), tuple(periods or [False] * len(dims)), comm.size)
    comm._topology = topo
    return topo


def dist_graph_create(
    comm, edges: dict[int, Iterable[int]], labels: dict[int, str] | None = None
):
    topo = GraphTopology(
        comm.size,
        {int(k): [int(x) for x in v] for k, v in edges.items()},
        labels=dict(labels or {}),
    )
    comm._topology = topo
    return topo


def pipeline_create(comm, stages: Sequence[str]):
    """Convenience: a linear pipeline, the most common agent topology.

    Every rank forwards to the next, so the critical path is exactly ``p``
    turns and no parallelism exists at all -- which is precisely why the
    harness should see the number before running it.
    """
    edges = {i: [i + 1] for i in range(comm.size - 1)}
    labels = {i: stages[i % len(stages)] for i in range(comm.size)}
    return dist_graph_create(comm, edges, labels)


def neighbors_of(comm) -> tuple[list[int], list[int]]:
    """``(sources, destinations)`` for the communicator's topology."""
    topo = getattr(comm, "_topology", None)
    if topo is None:
        raise TopologyError("communicator has no topology; call cart_create or "
                            "dist_graph_create first", comm=comm.name)
    rank = comm.rank
    if isinstance(topo, CartesianTopology):
        n = topo.neighbors(rank)
        return n, n
    return topo.in_neighbors(rank), topo.out_neighbors(rank)


def analyse(topo: GraphTopology) -> dict[str, Any]:
    """A static report a harness can print before spending anything."""
    cycles = topo.cycles()
    return {
        "nodes": topo.size,
        "edges": sum(len(v) for v in topo.edges.values()),
        "diameter": topo.diameter(),
        "cycles": cycles,
        "deadlock_risk": bool(cycles),
        "hubs": topo.hubs(),
        "serial_floor_turns": topo.critical_path_length(),
        "max_parallelism": (
            topo.size / topo.critical_path_length()
            if topo.critical_path_length() not in (0, float("inf")) else 0.0
        ),
    }
