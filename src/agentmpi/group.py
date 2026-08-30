"""Groups: ordered sets of ranks.

MPI separates *groups* (an ordered set of process identities, a purely local
object) from *communicators* (a group plus a communication context).  The
separation is easy to overlook and turns out to be essential: it lets a
program compute a membership -- by set algebra, by predicate, by
translation between groups -- entirely locally, and only then pay the
collective cost of creating a communicator over it.

For agents the same separation buys something extra.  A group is cheap to
manipulate and cheap to *store*, so a harness can keep the pre-failure
membership around and compute exactly which ranks were lost, which is what
recovery needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

from .constants import UNDEFINED
from .errors import ArgError


@dataclass(frozen=True)
class RankSpec:
    """The identity of a rank: who it is and what it is made of.

    MPI's notion of a process identity is just an integer, because every
    process runs the same binary.  Agent ranks are heterogeneous by
    construction -- different models, different tool permissions, different
    prices -- so the identity must carry that, and collectives must be able
    to reason about it (``AMPI_Comm_split_type`` by model, cost-aware
    algorithm selection, and capability-aware task placement all depend on
    it).  This is the AgentMPI analogue of an MPI *process image* plus the
    hardware locality information that ``MPI_Comm_split_type`` and MPI-4's
    hardware-resource info exposes.
    """

    rank: int
    role: str = "worker"
    model: str = "unknown"
    provider: str = "unknown"
    host: str = "local"
    store: str = "default"
    context_capacity: int = 0
    """Context window in tokens.  Zero means "inherit the run's default"
    (the ``ampi_context_capacity`` control variable), so that a harness can
    set one capacity for the whole job and override it only for the ranks
    that differ."""
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    tools: tuple[str, ...] = ()
    label: str = ""

    def attribute(self, kind: str) -> str:
        return {
            "model": self.model,
            "provider": self.provider,
            "host": self.host,
            "store": self.store,
            "role": self.role,
        }.get(kind, "")


@dataclass(frozen=True)
class Group:
    """An ordered set of ranks, addressed by position within the group."""

    members: tuple[int, ...]
    specs: tuple[RankSpec, ...] = ()

    # -- queries -----------------------------------------------------------
    @property
    def size(self) -> int:
        return len(self.members)

    def rank_of(self, world_rank: int) -> int:
        """``MPI_Group_rank``-style lookup: position of a world rank."""
        try:
            return self.members.index(world_rank)
        except ValueError:
            return UNDEFINED

    def world_rank(self, index: int) -> int:
        if not 0 <= index < len(self.members):
            raise ArgError("group index out of range", index=index, size=self.size)
        return self.members[index]

    def spec(self, index: int) -> RankSpec:
        if self.specs and 0 <= index < len(self.specs):
            return self.specs[index]
        return RankSpec(rank=self.world_rank(index))

    def __contains__(self, world_rank: object) -> bool:
        return world_rank in self.members

    def __iter__(self):
        return iter(self.members)

    def __len__(self) -> int:
        return len(self.members)

    # -- constructors ------------------------------------------------------
    @classmethod
    def of(cls, n: int, specs: Sequence[RankSpec] | None = None) -> "Group":
        return cls(tuple(range(n)), tuple(specs or ()))

    def _reindex(self, members: Sequence[int]) -> "Group":
        if not self.specs:
            return Group(tuple(members))
        by_world = {s.rank: s for s in self.specs}
        return Group(
            tuple(members),
            tuple(by_world.get(m, RankSpec(rank=m)) for m in members),
        )

    # -- set algebra (MPI_Group_incl / excl / union / ...) -----------------
    def incl(self, indices: Iterable[int]) -> "Group":
        idx = list(indices)
        for i in idx:
            if not 0 <= i < self.size:
                raise ArgError("index out of range in Group.incl", index=i, size=self.size)
        return self._reindex([self.members[i] for i in idx])

    def excl(self, indices: Iterable[int]) -> "Group":
        drop = set(indices)
        return self._reindex([m for i, m in enumerate(self.members) if i not in drop])

    def union(self, other: "Group") -> "Group":
        seen = list(self.members)
        seen += [m for m in other.members if m not in self.members]
        merged = {s.rank: s for s in other.specs}
        merged.update({s.rank: s for s in self.specs})
        return Group(tuple(seen), tuple(merged.get(m, RankSpec(rank=m)) for m in seen))

    def intersection(self, other: "Group") -> "Group":
        return self._reindex([m for m in self.members if m in other.members])

    def difference(self, other: "Group") -> "Group":
        return self._reindex([m for m in self.members if m not in other.members])

    def range_incl(self, ranges: Sequence[tuple[int, int, int]]) -> "Group":
        out: list[int] = []
        for first, last, stride in ranges:
            out.extend(self.members[i] for i in range(first, last + 1, stride))
        return self._reindex(out)

    def translate_ranks(self, indices: Iterable[int], other: "Group") -> list[int]:
        """``MPI_Group_translate_ranks``."""
        return [other.rank_of(self.members[i]) for i in indices]

    # -- agent-specific selectors -----------------------------------------
    def filter(self, predicate: Callable[[RankSpec], bool]) -> "Group":
        """Select by capability.  No MPI analogue; ranks there are fungible."""
        keep = [i for i in range(self.size) if predicate(self.spec(i))]
        return self.incl(keep)

    def partition_by(self, attribute: str) -> dict[str, "Group"]:
        """Group ranks sharing an attribute -- the basis of ``split_type``."""
        buckets: dict[str, list[int]] = {}
        for i in range(self.size):
            buckets.setdefault(self.spec(i).attribute(attribute), []).append(i)
        return {k: self.incl(v) for k, v in sorted(buckets.items())}

    def total_capacity(self) -> int:
        return sum(self.spec(i).context_capacity for i in range(self.size))

    def describe(self) -> str:
        return f"Group(size={self.size}, members={list(self.members)})"


EMPTY_GROUP = Group(())


@dataclass
class RankTable:
    """The run's rank registry, replicated through the device key-value store."""

    specs: dict[int, RankSpec] = field(default_factory=dict)

    def add(self, spec: RankSpec) -> None:
        self.specs[spec.rank] = spec

    def group(self, n: int) -> Group:
        return Group(
            tuple(range(n)),
            tuple(self.specs.get(i, RankSpec(rank=i)) for i in range(n)),
        )

    def to_json(self) -> list[dict]:
        from dataclasses import asdict

        return [asdict(s) for s in sorted(self.specs.values(), key=lambda s: s.rank)]

    @classmethod
    def from_json(cls, data: list[dict]) -> "RankTable":
        table = cls()
        known = RankSpec.__dataclass_fields__.keys()  # type: ignore[attr-defined]
        for d in data:
            kwargs = {k: v for k, v in d.items() if k in known}
            if "tools" in kwargs:
                kwargs["tools"] = tuple(kwargs["tools"])
            table.add(RankSpec(**kwargs))
        return table
