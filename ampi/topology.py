"""Communicator construction and process topologies.

Communicators are, in the retrospective judgement of MPI's own designers, the
feature that made MPI composable: because every operation is scoped to a
communicator with its own private tag space, a library can communicate without
colliding with its caller. Nothing in the LLM-agent ecosystem currently has an
equivalent, which is why two agent "teams" in the same harness invariably share
one global message bus and interfere.

AgentMPI keeps the whole construction toolkit -- ``split``, ``dup``, ``create``
from an explicit group, plus Cartesian and graph topologies -- because the shapes
matter operationally:

* ``split`` by colour builds sub-teams (one communicator per feature, per
  chapter, per language) that can run collectives among themselves without the
  rest of the job hearing about it.
* Cartesian topologies give nearest-neighbour structure: a pipeline is a 1-D
  Cartesian grid, and ``AMPI_Neighbor_allgather`` on it is exactly the
  "coordinate with the stage before and after you" pattern.
* Graph topologies let a harness declare an *organisation chart* and then use
  neighbourhood collectives instead of hand-addressed messages, which is both
  shorter and analysable.

The payoff of declaring the topology rather than hard-coding peer ranks is the
same as in MPI: the runtime can see the communication structure, so it can
trace, validate and visualise it -- and a harness that says "my neighbours" keeps
working after a shrink renumbers everyone.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .core import (
    Ctx,
    _create_comm,
    comm_members,
    comm_row,
    comm_to_world,
    world_to_comm,
)
from .errors import ArgError, CommError, RankError
from .journal import Journal, now_ns


def split(
    ctx: Ctx,
    *,
    color: Optional[int],
    key: Optional[int] = None,
    name: Optional[str] = None,
    timeout_ns: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Comm_split``.

    Every rank in the parent communicator calls it; ranks sharing a colour end
    up in one new communicator, ordered by ``key`` (ties broken by parent rank).
    A ``color`` of ``None`` is ``MPI_UNDEFINED``: the caller participates in the
    split but joins no new communicator.

    Split is genuinely collective and the resulting communicators are immutable,
    so membership cannot be decided from a partial vote: each caller registers
    its colour durably, then waits until every *live* parent rank has registered
    before the groups are materialised. Failed ranks are excluded rather than
    waited for, which is the one place AgentMPI's split differs from MPI's -- and
    it is the difference between a split that survives a dead rank and one that
    hangs.
    """
    from .core import detect_failures, failed_ranks

    j = ctx.j
    parent = comm_row(j, ctx.comm)
    base = name or f"{parent['name']}.split"
    vote_key = f"__split__:{parent['id']}:{base}"
    with j.tx() as c:
        c.execute(
            "INSERT INTO memo(job,rank,key,value,epoch,ns) VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(job,rank,key) DO UPDATE SET value=excluded.value,ns=excluded.ns",
            (
                j.job_id,
                ctx.rank,
                vote_key,
                json.dumps({"color": color, "key": key if key is not None else ctx.crank}),
                ctx.epoch,
                now_ns(),
            ),
        )
    members = comm_members(j, str(parent["id"]))
    deadline = now_ns() + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)
    import time as _time

    from . import p2p

    start = _time.time()
    while True:
        with j.tx() as c:
            detect_failures(j, ctx.comm, by=ctx.rank, conn=c)
        dead = set(failed_ranks(j, ctx.comm))
        votes: Dict[int, Dict[str, Any]] = {}
        for w in members:
            row = j.q1(
                "SELECT value FROM memo WHERE job=? AND rank=? AND key=?", (j.job_id, w, vote_key)
            )
            if row is not None:
                votes[w] = json.loads(row["value"])
        missing = [w for w in members if w not in votes and w not in dead]
        already = j.q1("SELECT id FROM comm WHERE job=? AND name LIKE ? LIMIT 1",
                       (j.job_id, base + "%"))
        if not missing or already is not None:
            break
        if now_ns() > deadline:
            from .errors import TimeoutError_

            raise TimeoutError_(
                f"AMPI_Comm_split: waiting for rank(s) {missing[:10]} to register a colour",
                hint="your colour is recorded; re-run the identical command to resume",
                detail={"missing": missing},
            )
        p2p._poll_sleep(_time.time() - start)

    groups: Dict[int, List[Tuple[int, int]]] = {}
    for w, v in votes.items():
        if v.get("color") is None:
            continue
        groups.setdefault(int(v["color"]), []).append((int(v.get("key", 0)), w))
    created: List[Dict[str, Any]] = []
    with j.tx() as c:
        for col, entries in sorted(groups.items()):
            entries.sort()
            cname = f"{base}{col}"
            row = c.execute("SELECT * FROM comm WHERE job=? AND name=?", (j.job_id, cname)).fetchone()
            if row is None:
                _create_comm(
                    c,
                    j,
                    comm_id="c:" + uuid.uuid4().hex[:10],
                    name=cname,
                    members=[w for _, w in entries],
                    parent=str(parent["id"]),
                )
                row = c.execute("SELECT * FROM comm WHERE job=? AND name=?", (j.job_id, cname)).fetchone()
            created.append({"color": col, "comm": cname, "size": int(row["size"])})
    mycolor = votes.get(ctx.rank, {}).get("color")
    out: Dict[str, Any] = {
        "parent": str(parent["name"]),
        "your_color": mycolor,
        "groups": created,
        "excluded_failed": sorted(dead),
    }
    if mycolor is None:
        out["comm"] = None
        out["note"] = "you passed color=None (AMPI_UNDEFINED) and joined no new communicator"
    else:
        cname = f"{base}{mycolor}"
        out["comm"] = cname
        out["your_rank"] = world_to_comm(j, cname, ctx.rank)
        out["size"] = int(comm_row(j, cname)["size"])
        out["next"] = f"use `--comm {cname}` for collectives within your group"
    return out


def create_from_group(
    ctx: Ctx, *, members: Sequence[int], name: str
) -> Dict[str, Any]:
    """``AMPI_Comm_create`` from an explicit world-rank group.

    Unlike ``split``, this needs no participation from non-members, which makes
    it the right tool when a manager rank wants to form a task force: it names
    the members and tells them the communicator's name.
    """
    j = ctx.j
    parent = comm_row(j, ctx.comm)
    pm = set(comm_members(j, str(parent["id"])))
    bad = [m for m in members if m not in pm]
    if bad:
        raise RankError(f"ranks {bad} are not members of {parent['name']}")
    existing = j.q1("SELECT * FROM comm WHERE job=? AND name=?", (j.job_id, name))
    if existing is not None:
        mem = comm_members(j, name)
        return {"comm": name, "size": len(mem), "created": False,
                "your_rank": mem.index(ctx.rank) if ctx.rank in mem else None}
    with j.tx() as c:
        _create_comm(
            c, j, comm_id="c:" + uuid.uuid4().hex[:10], name=name,
            members=list(members), parent=str(parent["id"]),
        )
    mem = list(members)
    return {"comm": name, "size": len(mem), "created": True,
            "your_rank": mem.index(ctx.rank) if ctx.rank in mem else None,
            "members_world": mem}


def dup(ctx: Ctx, *, name: str) -> Dict[str, Any]:
    """``AMPI_Comm_dup``: same group, fresh context.

    This is the operation a *library* performs, and the reason it exists is worth
    restating for an agent audience: a reusable agent component (a reviewer
    sub-protocol, a consensus helper) must be able to send messages without any
    chance of matching its caller's messages. Duplicating gives it a private tag
    space over the same participants.
    """
    j = ctx.j
    parent = comm_row(j, ctx.comm)
    existing = j.q1("SELECT * FROM comm WHERE job=? AND name=?", (j.job_id, name))
    if existing is not None:
        return {"comm": name, "size": int(existing["size"]), "created": False}
    members = comm_members(j, str(parent["id"]))
    with j.tx() as c:
        _create_comm(
            c, j, comm_id="c:" + uuid.uuid4().hex[:10], name=name, members=members,
            parent=str(parent["id"]), kind=str(parent["kind"]),
            topo=json.loads(parent["topo"] or "{}"),
        )
    return {"comm": name, "size": len(members), "created": True,
            "your_rank": world_to_comm(j, name, ctx.rank)}


def cart_create(
    ctx: Ctx, *, dims: Sequence[int], periodic: Sequence[bool], name: Optional[str] = None
) -> Dict[str, Any]:
    """``AMPI_Cart_create``: impose a Cartesian grid on a communicator."""
    j = ctx.j
    parent = comm_row(j, ctx.comm)
    members = comm_members(j, str(parent["id"]))
    total = 1
    for d in dims:
        total *= int(d)
    if total > len(members):
        raise ArgError(f"grid {list(dims)} needs {total} ranks but the communicator has {len(members)}")
    cname = name or f"{parent['name']}.cart"
    topo = {"type": "cart", "dims": list(map(int, dims)), "periodic": [bool(p) for p in periodic]}
    existing = j.q1("SELECT * FROM comm WHERE job=? AND name=?", (j.job_id, cname))
    if existing is None:
        with j.tx() as c:
            _create_comm(
                c, j, comm_id="c:" + uuid.uuid4().hex[:10], name=cname,
                members=members[:total], kind="cart", parent=str(parent["id"]), topo=topo,
            )
    coords = cart_coords(topo, world_to_comm(j, cname, ctx.rank)) if ctx.rank in members[:total] else None
    return {
        "comm": cname,
        "dims": topo["dims"],
        "periodic": topo["periodic"],
        "size": total,
        "your_rank": world_to_comm(j, cname, ctx.rank) if coords is not None else None,
        "your_coords": coords,
    }


def cart_coords(topo: Dict[str, Any], crank: int) -> List[int]:
    dims = topo["dims"]
    coords: List[int] = []
    rem = crank
    for d in reversed(dims):
        coords.append(rem % d)
        rem //= d
    return list(reversed(coords))


def cart_rank(topo: Dict[str, Any], coords: Sequence[int]) -> Optional[int]:
    dims = topo["dims"]
    per = topo.get("periodic") or [False] * len(dims)
    r = 0
    for i, (cd, d) in enumerate(zip(coords, dims)):
        c = int(cd)
        if per[i]:
            c %= d
        elif c < 0 or c >= d:
            return None
        r = r * d + c
    return r


def cart_shift(ctx: Ctx, *, direction: int, disp: int) -> Dict[str, Any]:
    """``AMPI_Cart_shift``: source and destination for a shift along one axis.

    A pipeline stage asks for ``(direction=0, disp=1)`` and is told who is
    upstream and who is downstream, without knowing the grid size. That
    indirection is what lets the same harness code run at any P.
    """
    j = ctx.j
    row = comm_row(j, ctx.comm)
    topo = json.loads(row["topo"] or "{}")
    if topo.get("type") != "cart":
        raise CommError(f"{row['name']} is not a Cartesian communicator",
                        hint="create one with `ampi comm cart --dims ...`")
    coords = cart_coords(topo, ctx.crank)
    up = list(coords)
    up[direction] = coords[direction] - disp
    dn = list(coords)
    dn[direction] = coords[direction] + disp
    src = cart_rank(topo, up)
    dst = cart_rank(topo, dn)
    size = int(row["size"])
    return {
        "comm": str(row["name"]),
        "your_coords": coords,
        "source": src if (src is not None and src < size) else None,
        "dest": dst if (dst is not None and dst < size) else None,
    }


def graph_create(
    ctx: Ctx, *, edges: Dict[int, Sequence[int]], name: Optional[str] = None
) -> Dict[str, Any]:
    """``AMPI_Dist_graph_create``: declare an arbitrary organisation chart.

    ``edges`` maps a communicator rank to the ranks it may talk to. Declaring it
    is what enables neighbourhood collectives and lets the runtime flag a message
    that violates the declared structure -- an inexpensive way to catch the
    "agent invented a peer" failure mode.
    """
    j = ctx.j
    parent = comm_row(j, ctx.comm)
    members = comm_members(j, str(parent["id"]))
    cname = name or f"{parent['name']}.graph"
    topo = {"type": "graph", "edges": {str(k): list(map(int, v)) for k, v in edges.items()}}
    existing = j.q1("SELECT * FROM comm WHERE job=? AND name=?", (j.job_id, cname))
    if existing is None:
        with j.tx() as c:
            _create_comm(
                c, j, comm_id="c:" + uuid.uuid4().hex[:10], name=cname, members=members,
                kind="graph", parent=str(parent["id"]), topo=topo,
            )
    else:
        with j.tx() as c:
            c.execute("UPDATE comm SET topo=? WHERE id=?", (json.dumps(topo), str(existing["id"])))
    return {"comm": cname, "size": len(members), "your_rank": world_to_comm(j, cname, ctx.rank),
            "your_neighbors": topo["edges"].get(str(world_to_comm(j, cname, ctx.rank)), [])}


def neighbors(ctx: Ctx) -> Dict[str, Any]:
    j = ctx.j
    row = comm_row(j, ctx.comm)
    topo = json.loads(row["topo"] or "{}")
    rr = ctx.crank
    if topo.get("type") == "cart":
        out: List[int] = []
        dims = topo["dims"]
        for d in range(len(dims)):
            for disp in (-1, 1):
                coords = cart_coords(topo, rr)
                coords[d] += disp
                n = cart_rank(topo, coords)
                if n is not None and n < int(row["size"]):
                    out.append(n)
        return {"comm": str(row["name"]), "topology": "cart", "neighbors": sorted(set(out))}
    if topo.get("type") == "graph":
        return {"comm": str(row["name"]), "topology": "graph",
                "neighbors": topo.get("edges", {}).get(str(rr), [])}
    return {"comm": str(row["name"]), "topology": "none",
            "neighbors": [i for i in range(int(row["size"])) if i != rr],
            "note": "no topology declared; every rank is a neighbour"}


def neighbor_allgather(
    ctx: Ctx,
    *,
    text: str,
    label: Optional[str] = None,
    timeout_ns: Optional[int] = None,
    budget: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Neighbor_allgather``: exchange with declared neighbours only.

    The scaling argument is the whole point. A full ``allgather`` over P agents
    costs Theta(P) contributions of context for every rank; a neighbourhood
    allgather on a declared topology costs Theta(degree). For the collaborative
    software task, where each rank genuinely only needs the modules it links
    against, that is the difference between a harness that runs at P=12 and one
    that does not.
    """
    from . import collectives, p2p
    from .core import internal_tag

    j = ctx.j
    nb = neighbors(ctx)["neighbors"]
    tg = internal_tag("allgather", 300)
    for n in nb:
        p2p.send(ctx, n, tg, text, kind="coll", idem=f"nag:{label}:{ctx.crank}->{n}")
    got: List[Dict[str, Any]] = []
    deadline = now_ns() + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)
    for _ in nb:
        try:
            env = p2p.recv(ctx, -1, tg, timeout_ns=max(1, deadline - now_ns()), budget=budget)
        except Exception:
            break
        got.append({"from": env["source"], "tokens": env["tokens"],
                    "body": env.get("body"), "handle": env["handle"], "summary": env["summary"]})
    return {"neighbors": nb, "received": got, "complete": len(got) == len(nb)}
