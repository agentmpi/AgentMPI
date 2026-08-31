"""Communicators, groups, and virtual topologies.

The communicator is the idea from MPI that transfers most cleanly and most
usefully to agent systems, and it is the one that every existing agent
framework is missing.

An MPI communicator bundles two things: a *group* (an ordered set of
participants, giving each a small dense rank) and a *context id* (an opaque
tag-space separator).  The context id exists for one reason --- so that a
library can communicate internally without any possibility that its messages
are intercepted by, or intercept, the messages of the application that called
it.  Before communicators, a parallel library and its caller had to agree on a
tag convention out of band, and every combination of two libraries was a new
integration risk.  MPI made libraries composable by making message spaces
first-class objects rather than an informal agreement about integers.

Multi-agent harnesses are at exactly the pre-communicator stage.  A "group
chat" is a broadcast domain with no isolation: any agent added for any reason
sees every message, and two independently written agent modules dropped into
the same harness will read each other's traffic.  Communicators fix this, and
they simultaneously provide the sub-team abstraction (``AMPI_Comm_split``)
that agent frameworks currently hand-roll as bespoke "crews" or "sub-graphs".

A topology is an additional, purely advisory annotation on a communicator that
names each rank's neighbours.  In MPI it lets the runtime map ranks onto a
network; here it lets a harness express "reviewer *i* reads writer *i-1*'s
output" as structure rather than as prompt text, and it makes the resulting
communication pattern statically inspectable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import util
from ..constants import AMPI_COMM_WORLD, AMPI_UNDEFINED
from ..errors import AmpiArgError, AmpiCommError


@dataclass
class Communicator:
    comm_id: str
    job_id: str
    name: str
    context_id: int
    members: list[int]
    parent: str | None
    topology: dict[str, Any] | None
    revoked: bool
    meta: dict[str, Any]

    @property
    def size(self) -> int:
        return len(self.members)

    def rank_of(self, world_rank: int) -> int:
        try:
            return self.members.index(world_rank)
        except ValueError:
            return AMPI_UNDEFINED

    def world_of(self, comm_rank: int) -> int:
        if not 0 <= comm_rank < len(self.members):
            raise AmpiArgError(
                f"rank {comm_rank} out of range for communicator {self.name!r} "
                f"of size {len(self.members)}"
            )
        return self.members[comm_rank]

    def to_dict(self) -> dict[str, Any]:
        return {
            "comm_id": self.comm_id,
            "name": self.name,
            "size": self.size,
            "context_id": self.context_id,
            "members": self.members,
            "parent": self.parent,
            "topology": self.topology,
            "revoked": self.revoked,
        }


def _row_to_comm(row: dict[str, Any]) -> Communicator:
    return Communicator(
        comm_id=row["comm_id"],
        job_id=row["job_id"],
        name=row["name"],
        context_id=int(row["context_id"]),
        members=util.loads(row["members"], []),
        parent=row["parent"],
        topology=util.loads(row["topology"], None),
        revoked=bool(row["revoked"]),
        meta=util.loads(row["meta"], {}),
    )


class CommRegistry:
    def __init__(self, device: Any, job_id: str) -> None:
        self.device = device
        self.job_id = job_id

    # -- lookup ------------------------------------------------------------
    def get(self, name_or_id: str) -> Communicator:
        row = self.device.query_one(
            "SELECT * FROM comm WHERE job_id=? AND (name=? OR comm_id=?)",
            (self.job_id, name_or_id, name_or_id),
        )
        if row is None:
            raise AmpiCommError(
                f"no communicator named {name_or_id!r} in job {self.job_id}",
                known=[c["name"] for c in self.list()],
            )
        return _row_to_comm(row)

    def try_get(self, name_or_id: str) -> Communicator | None:
        try:
            return self.get(name_or_id)
        except AmpiCommError:
            return None

    def list(self) -> list[dict[str, Any]]:
        return self.device.query(
            "SELECT * FROM comm WHERE job_id=? ORDER BY created_at", (self.job_id,)
        )

    # -- construction ------------------------------------------------------
    def create(
        self,
        name: str,
        members: list[int],
        *,
        parent: str | None = None,
        topology: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Communicator:
        """Create a communicator with a fresh context id.

        Creation is idempotent by name.  In MPI, communicator construction is
        collective and every participant must call it; here the members are
        named explicitly and the first caller wins, because an agent that fails
        to make its call must not be able to wedge communicator creation for
        everyone else.  This is a deliberate weakening of MPI's collectivity
        requirement in exchange for robustness against unreliable participants.
        """
        existing = self.try_get(name)
        if existing is not None:
            if existing.members != list(members):
                raise AmpiCommError(
                    f"communicator {name!r} already exists with different members",
                    existing=existing.members,
                    requested=list(members),
                )
            return existing
        context_id = self.device.counter_next(self.job_id, "context_id")
        comm_id = util.new_id("comm")
        self.device.execute(
            "INSERT INTO comm (comm_id, job_id, name, context_id, members, parent, topology, "
            "created_at, meta) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                comm_id,
                self.job_id,
                name,
                context_id,
                util.dumps(list(members)),
                parent,
                util.dumps(topology) if topology else None,
                util.now(),
                util.dumps(meta or {}),
            ),
        )
        return self.get(name)

    def dup(self, source: str, name: str) -> Communicator:
        """AMPI_Comm_dup: same group, fresh context id.

        The workhorse of library safety: a component that wants to talk to its
        own instances duplicates the communicator it was handed and uses the
        duplicate, guaranteeing that its traffic cannot be confused with its
        caller's even though the participants are identical.
        """
        src = self.get(source)
        return self.create(
            name, src.members, parent=src.comm_id, topology=src.topology, meta=dict(src.meta)
        )

    def split(
        self, source: str, colors: dict[int, int | None], keys: dict[int, int] | None = None
    ) -> dict[int, Communicator]:
        """AMPI_Comm_split with MPI's colour/key semantics.

        Ranks sharing a colour land in one new communicator; ordering within it
        follows ``key`` and breaks ties by the source rank, exactly as MPI
        specifies.  A colour of ``None`` (MPI_UNDEFINED) excludes the rank.

        Unlike MPI, split is evaluated centrally from a supplied colour map
        rather than by a collective exchange of colours.  The resulting groups
        are identical; the difference is that a silent rank cannot stall it.
        """
        src = self.get(source)
        keys = keys or {}
        buckets: dict[int, list[tuple[int, int]]] = {}
        for comm_rank, colour in colors.items():
            if colour is None or colour == AMPI_UNDEFINED:
                continue
            buckets.setdefault(int(colour), []).append((keys.get(comm_rank, 0), comm_rank))
        out: dict[int, Communicator] = {}
        for colour in sorted(buckets):
            ordered = [cr for _, cr in sorted(buckets[colour])]
            members = [src.world_of(cr) for cr in ordered]
            out[colour] = self.create(
                f"{src.name}.split{colour}", members, parent=src.comm_id, meta={"color": colour}
            )
        return out

    # -- topologies --------------------------------------------------------
    def cart_create(
        self, source: str, dims: list[int], periods: list[bool], name: str | None = None
    ) -> Communicator:
        src = self.get(source)
        total = 1
        for d in dims:
            total *= d
        if total > src.size:
            raise AmpiArgError(f"cartesian topology {dims} needs {total} ranks, have {src.size}")
        return self.create(
            name or f"{src.name}.cart",
            src.members[:total],
            parent=src.comm_id,
            topology={"kind": "cart", "dims": list(dims), "periods": [bool(p) for p in periods]},
        )

    def dist_graph_create(
        self, source: str, adjacency: dict[int, list[int]], name: str | None = None
    ) -> Communicator:
        src = self.get(source)
        return self.create(
            name or f"{src.name}.graph",
            src.members,
            parent=src.comm_id,
            topology={"kind": "graph", "adj": {str(k): list(v) for k, v in adjacency.items()}},
        )

    # -- revocation --------------------------------------------------------
    def revoke(self, name: str, by_rank: int) -> Communicator:
        self.device.execute(
            "UPDATE comm SET revoked=1, revoked_by=?, revoked_at=? WHERE job_id=? AND name=?",
            (by_rank, util.now(), self.job_id, name),
        )
        return self.get(name)


# ---------------------------------------------------------------------------
# Topology helpers
# ---------------------------------------------------------------------------


def cart_coords(dims: list[int], rank: int) -> list[int]:
    coords: list[int] = []
    rest = rank
    for stride in _strides(dims):
        coords.append(rest // stride)
        rest %= stride
    return coords


def cart_rank(dims: list[int], coords: list[int], periods: list[bool]) -> int | None:
    total = 1
    for d in dims:
        total *= d
    normalized: list[int] = []
    for i, (coord, dim) in enumerate(zip(coords, dims, strict=True)):
        if 0 <= coord < dim:
            normalized.append(coord)
        elif periods[i]:
            normalized.append(coord % dim)
        else:
            return None
    rank = 0
    for coord, stride in zip(normalized, _strides(dims), strict=True):
        rank += coord * stride
    return rank if rank < total else None


def cart_shift(
    dims: list[int], periods: list[bool], rank: int, direction: int, disp: int
) -> tuple[int | None, int | None]:
    """AMPI_Cart_shift: the (source, dest) pair for a halo exchange."""
    coords = cart_coords(dims, rank)
    up = list(coords)
    up[direction] += disp
    down = list(coords)
    down[direction] -= disp
    return (cart_rank(dims, down, periods), cart_rank(dims, up, periods))


def _strides(dims: list[int]) -> list[int]:
    strides = [1] * len(dims)
    for i in range(len(dims) - 2, -1, -1):
        strides[i] = strides[i + 1] * dims[i + 1]
    return strides


def neighbours(comm: Communicator, rank: int) -> list[int]:
    """Neighbour set implied by the communicator's topology."""
    topo = comm.topology
    if not topo:
        return [r for r in range(comm.size) if r != rank]
    if topo["kind"] == "cart":
        dims, periods = topo["dims"], topo["periods"]
        out: list[int] = []
        for direction in range(len(dims)):
            src, dst = cart_shift(dims, periods, rank, direction, 1)
            out.extend(r for r in (src, dst) if r is not None)
        return sorted(set(out))
    if topo["kind"] == "graph":
        return list(topo["adj"].get(str(rank), []))
    return []


def default_world_members(world_size: int) -> list[int]:
    return list(range(world_size))


WORLD = AMPI_COMM_WORLD
