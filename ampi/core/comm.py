"""Communicators, groups, and virtual topologies.

The communicator is MPI's central abstraction and the one worth transplanting
unchanged.  Pre-MPI libraries addressed messages by ``(destination, tag)``, so a
library's messages could be intercepted by its caller whenever they collided on an
integer, and MPI's designers retrospectively identify the private context as the
reason MPI could support libraries at all.

The agent-world version of that collision is not hypothetical.  A reviewer's
critique intended for the author of module A being consumed by the author of
module B is the same bug, and the "one shared group chat" architecture that every
agent framework ships makes it certain rather than merely possible.  A
communicator also bounds cost: a broadcast to a four-member team costs four
messages, not ``p``.

``create_group`` exists for the reason MPI-3 added it: ``split`` requires every
member of the parent to participate, which is impossible once some of them are
dead.

Topologies buy two things in MPI --- expressiveness, and the chance for the
implementation to remap ranks onto hardware.  The second does not transfer, since
there is no interconnect to be near.  The first becomes far more valuable, because
the communication pattern *is* the harness's design and it determines the cost: a
full group conversation over ``p`` agents costs ``O(p^2 n)`` tokens, and the same
information over a review graph costs ``O(pn)``.  Restricting communication to a
topology is how a harness buys scalability.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ..constants import DEFAULT_TIMEOUT_S, PROC_NULL
from ..errors import err

__all__ = ["CommMixin"]


class CommMixin:
    # -- construction ---------------------------------------------------------
    def comm_dup(self, comm: str = "world", *, name: str | None = None) -> dict[str, Any]:
        """Same group, fresh context.  The operation a library performs."""
        info = self.comm_info(comm)
        derived = name or f"{comm}.dup{int(time.time() * 1000) % 100000}"
        self.device.cas(
            "comm", derived, 0,
            {"name": derived, "members": list(info["members"]), "gen": info["gen"],
             "state": "live", "parent": comm},
            writer=self.rank,
        )
        self.trace("comm.dup", comm=comm, derived=derived, rank=self.rank)
        return self.comm_info(derived)

    def comm_create(self, name: str, members: list[int], *, parent: str = "world") -> dict[str, Any]:
        """Non-collective creation from an explicit member set.

        Requires no participation from non-members, which is what makes it usable
        after a failure: ``split`` cannot complete when some member of the parent
        is dead, and that is exactly when a harness most needs a new group.
        """
        self.assert_identity()
        pinfo = self.comm_info(parent)
        outside = sorted(set(members) - set(pinfo["members"]))
        if outside:
            raise err(
                "AMPI_ERR_RANK",
                f"rank(s) {outside} are not members of the parent communicator {parent!r}",
                members=pinfo["members"],
            )
        ok, cell = self.device.cas(
            "comm", name, 0,
            {"name": name, "members": sorted(members), "gen": pinfo["gen"], "state": "live",
             "parent": parent},
            writer=self.rank,
        )
        if not ok and cell.value.get("members") != sorted(members):
            raise err(
                "AMPI_ERR_COMM",
                f"communicator {name!r} already exists with different members",
                existing=cell.value.get("members"),
            )
        self.trace("comm.create", name=name, members=sorted(members), rank=self.rank)
        return self.comm_info(name)

    def comm_split(
        self,
        colour: int | None,
        *,
        key: int | None = None,
        comm: str = "world",
        label: str = "split",
        timeout: float = DEFAULT_TIMEOUT_S,
        quorum: float = 1.0,
    ) -> dict[str, Any]:
        """Collective split by colour, ordered by key.

        Membership must not be decided from a partial vote, or two ranks obtain
        differently-sized communicators and every subsequent collective
        mismatches.  The operation therefore completes only when every *live*
        parent rank has registered a colour; failed ranks are excluded rather than
        waited for, which is the only way the operation can terminate at all in a
        population where failure is normal.
        """
        self.assert_identity()
        key = self.comm_rank(comm) if key is None else key
        self._join_collective(comm, f"split:{label}", "split", payload={"colour": colour, "key": key})
        arrived, dropped = self._await_participation(
            comm, f"split:{label}", kind="split", quorum=quorum, timeout=timeout
        )
        votes = {p["rank"]: self.get_body(p["handle"]) for p in arrived if p.get("handle")}
        mine = votes.get(self.rank, {}).get("colour")
        if mine is None:
            return {"colour": None, "comm": None, "note": "participated but joined nothing"}
        group = sorted(
            (v["key"], r) for r, v in votes.items() if v.get("colour") == mine
        )
        members = [r for _, r in group]
        derived = f"{comm}/c{mine}"
        self.device.cas(
            "comm", derived, None,
            {"name": derived, "members": members, "gen": self.comm_info(comm)["gen"],
             "state": "live", "parent": comm, "colour": mine},
            writer=self.rank,
        )
        self.trace("comm.split", comm=comm, derived=derived, colour=mine, members=members,
                   rank=self.rank, dropped=dropped)
        return {"colour": mine, "comm": derived, "members": members, "rank_in_comm": members.index(self.rank),
                "dropped": dropped}

    def comm_free(self, name: str) -> dict[str, Any]:
        if name == "world":
            raise err("AMPI_ERR_COMM", "the world communicator may not be freed")
        info = self.comm_info(name)
        self.device.cas("comm", name, None, {**info, "state": "freed"}, writer=self.rank)
        return {"comm": name, "state": "freed"}

    def comm_list(self) -> list[dict[str, Any]]:
        return [self.device.read("comm", c.key).value for c in self.device.keys("comm")]

    # -- groups ----------------------------------------------------------------
    def group_of(self, comm: str = "world") -> list[int]:
        return self.comm_members(comm)

    @staticmethod
    def group_union(a: list[int], b: list[int]) -> list[int]:
        return sorted(set(a) | set(b))

    @staticmethod
    def group_intersection(a: list[int], b: list[int]) -> list[int]:
        return sorted(set(a) & set(b))

    @staticmethod
    def group_difference(a: list[int], b: list[int]) -> list[int]:
        return sorted(set(a) - set(b))

    # -- Cartesian topology -----------------------------------------------------
    def cart_create(
        self,
        dims: list[int],
        *,
        periodic: list[bool] | None = None,
        comm: str = "world",
        name: str | None = None,
    ) -> dict[str, Any]:
        members = self.comm_members(comm)
        total = math.prod(dims)
        if total > len(members):
            raise err(
                "AMPI_ERR_ARG",
                f"a {dims} grid needs {total} ranks but {comm!r} has {len(members)}",
                dims=dims, available=len(members),
            )
        derived = name or f"{comm}/cart{'x'.join(map(str, dims))}"
        self.device.cas(
            "comm", derived, None,
            {"name": derived, "members": members[:total], "gen": self.comm_info(comm)["gen"],
             "state": "live", "parent": comm,
             "topology": {"kind": "cart", "dims": dims,
                          "periodic": periodic or [False] * len(dims)}},
            writer=self.rank,
        )
        self.trace("cart.create", comm=derived, dims=dims, rank=self.rank)
        return self.comm_info(derived)

    def cart_coords(self, comm: str, rank: int | None = None) -> list[int]:
        info = self.comm_info(comm)
        topo = info.get("topology") or {}
        if topo.get("kind") != "cart":
            raise err("AMPI_ERR_COMM", f"{comm!r} has no Cartesian topology")
        idx = info["members"].index(self.rank if rank is None else rank)
        coords = []
        for d in reversed(topo["dims"]):
            coords.append(idx % d)
            idx //= d
        return list(reversed(coords))

    def cart_shift(self, comm: str, direction: int, disp: int = 1) -> dict[str, Any]:
        """Return ``(source, dest)``, either of which may be ``PROC_NULL``.

        Declaring the structure rather than hard-coding peer ranks buys three
        things: the runtime can trace and validate it, a harness that says "my
        neighbours" keeps working after a shrink renumbers everyone, and a
        neighbourhood collective costs ``O(degree)`` context per rank where a full
        allgather costs ``O(p)``.
        """
        info = self.comm_info(comm)
        topo = info.get("topology") or {}
        if topo.get("kind") != "cart":
            raise err("AMPI_ERR_COMM", f"{comm!r} has no Cartesian topology")
        dims, periodic = topo["dims"], topo["periodic"]
        coords = self.cart_coords(comm)

        def rank_of(c: list[int]) -> int:
            idx = 0
            for value, d in zip(c, dims, strict=True):
                idx = idx * d + value
            return idx

        out = {}
        for name, delta in (("source", -disp), ("dest", +disp)):
            c = list(coords)
            c[direction] += delta
            if 0 <= c[direction] < dims[direction]:
                out[name] = rank_of(c)
            elif periodic[direction]:
                c[direction] %= dims[direction]
                out[name] = rank_of(c)
            else:
                out[name] = PROC_NULL
        return {"comm": comm, "direction": direction, "disp": disp, **out}

    # -- graph topology ---------------------------------------------------------
    def graph_create(
        self,
        edges: dict[int, list[int]],
        *,
        comm: str = "world",
        name: str | None = None,
        symmetric: bool = False,
    ) -> dict[str, Any]:
        """Declare an arbitrary adjacency: an organisation chart, or a review graph.

        A *directed* collective needs the graph and its transpose.  Getting that
        wrong misroutes reviews: a rank asked "who should I review" is answered by
        the out-edges, and a rank asked "who is reviewing me" by the in-edges, and
        a harness that stores only one of them will deliver every critique to the
        wrong author.
        """
        members = self.comm_members(comm)
        out_edges = {int(k): sorted(v) for k, v in edges.items()}
        if symmetric:
            for a, peers in list(out_edges.items()):
                for b in peers:
                    out_edges.setdefault(b, [])
                    if a not in out_edges[b]:
                        out_edges[b] = sorted([*out_edges[b], a])
        in_edges: dict[int, list[int]] = {r: [] for r in members}
        for a, peers in out_edges.items():
            for b in peers:
                in_edges.setdefault(b, []).append(a)
        derived = name or f"{comm}/graph"
        self.device.cas(
            "comm", derived, None,
            {"name": derived, "members": members, "gen": self.comm_info(comm)["gen"],
             "state": "live", "parent": comm,
             "topology": {"kind": "graph",
                          "out": {str(k): v for k, v in out_edges.items()},
                          "in": {str(k): sorted(v) for k, v in in_edges.items()}}},
            writer=self.rank,
        )
        self.trace("graph.create", comm=derived, edges=len(out_edges), rank=self.rank)
        return self.comm_info(derived)

    def neighbours(self, comm: str, *, rank: int | None = None) -> dict[str, list[int]]:
        info = self.comm_info(comm)
        topo = info.get("topology") or {}
        me = self.comm_rank(comm) if rank is None else rank
        if topo.get("kind") == "graph":
            return {
                "out": list(topo["out"].get(str(me), [])),
                "in": list(topo["in"].get(str(me), [])),
            }
        if topo.get("kind") == "cart":
            out = []
            for d in range(len(topo["dims"])):
                s = self.cart_shift(comm, d)
                out.extend(x for x in (s["source"], s["dest"]) if x != PROC_NULL)
            return {"out": sorted(set(out)), "in": sorted(set(out))}
        raise err("AMPI_ERR_COMM", f"{comm!r} has no topology")

    def neighbor_allgather(
        self,
        label: str,
        *,
        payload: Any,
        comm: str,
        timeout: float = DEFAULT_TIMEOUT_S,
        materialize: bool = False,
        view: str = "",
    ) -> dict[str, Any]:
        """Exchange with declared neighbours only, at ``O(degree)`` cost.

        The most valuable non-obvious transfer from HPC.  A task whose parts each
        depend on their neighbours *looks* sequential; expressed as a stencil it is
        one barrier-separated parallel step plus a bounded-degree boundary
        exchange.
        """
        self.assert_identity()
        joined = self._join_collective(comm, label, "neighbor_allgather", payload=payload)
        nbrs = self.neighbours(comm)
        want = set(nbrs["in"]) | {self.comm_rank(comm)}
        members = self.comm_members(comm)
        world_want = {members[i] for i in want if i < len(members)}
        self._await(
            lambda: world_want.issubset(
                {p["rank"] for p in self._participants(comm, label) if p.get("handle")}
                | {r for r in world_want if self._rankview(r).state in ("failed", "fenced")}
            ),
            timeout=timeout,
            what=f"neighbours {sorted(world_want)} to contribute to {label!r}",
        )
        got = []
        for p in self._participants(comm, label):
            if p["rank"] not in world_want or not p.get("handle"):
                continue
            entry = {"rank": p["rank"], "handle": p["handle"], "tokens": p.get("tokens", 0)}
            if materialize or view:
                from .payload import apply_view

                body = self.get_body(p["handle"])
                entry["body"] = apply_view(body, view) if view else body
            got.append(entry)
        from ..tokens import count_tokens
        from .payload import canonical

        charged, _ = self.charge(count_tokens(canonical(got)), what="neighbor_allgather")
        self._coll_done(
            "neighbor_allgather", joined, comm=comm, label=label,
            degree=len(got), charged=charged,
        )
        return {"label": label, "degree": len(got), "neighbours": got, "charged": charged}
