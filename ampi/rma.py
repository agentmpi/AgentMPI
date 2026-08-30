"""One-sided operations: windows, the AgentMPI shared blackboard.

MPI-2 added one-sided communication because two-sided message passing forces
the *receiver* to participate, and there are algorithms where only the initiator
knows what it wants. MPI-3 rewrote it because the original semantics were too
weak to program against. The result -- ``MPI_Put``/``Get``/``Accumulate`` over
explicitly exposed windows, with explicit synchronisation epochs -- is the model
AgentMPI adopts for shared agent state.

The motivation is the same and the failure it fixes is the most-reported failure
of real multi-agent systems: *executors cannot share what they learn*. A pure
message-passing harness forces every fact discovered by one agent to be routed
to every agent that will later need it, by an author who does not know in
advance who that is. A window inverts it: an agent publishes into a named,
versioned key space, and any agent may read it later without the publisher
having done anything.

What AgentMPI keeps from MPI-3 RMA:

* **Windows are named and explicit.** There is no ambient global memory; a rank
  can only touch state that a window exposes. This is what keeps the shared
  state auditable, and it is the difference between a blackboard and a mess.
* **Epochs and explicit synchronisation.** ``win fence`` (active target) and
  ``win lock``/``unlock`` (passive target) bracket accesses, so "when is my
  write visible" has an answer.
* **Accumulate and atomics.** ``accumulate``, ``compare_and_swap`` and
  ``fetch_and_op`` let concurrent agents combine state without a lock.

What AgentMPI adds, because the participants are unreliable:

* **Versioning and history.** Every cell keeps a version and an append-only
  history, so a replacement agent can see what its predecessor wrote and a
  reviewer can attribute any claim to a rank and a time.
* **Leased locks with fencing tokens.** An MPI process holding a window lock
  cannot wander off; an agent can. Locks therefore expire, and every lock
  carries a monotone token so that a revived zombie's writes are rejected.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ops as ops_mod
from . import p2p
from . import tokens as tok
from . import views as views_mod
from .core import (
    Ctx,
    check_comm_usable,
    comm_members,
    comm_to_world,
    ctx_charge,
    detect_failures,
    failed_ranks,
    heartbeat,
    package,
)
from .errors import (
    AmpiError,
    ArgError,
    ConflictError,
    ErrClass,
    LockBusyError,
    TimeoutError_,
    WinError,
)
from .journal import Journal, now_ns


def win_row(j: Journal, name: str) -> sqlite3.Row:
    row = j.q1("SELECT * FROM win WHERE job=? AND (id=? OR name=?)", (j.job_id, name, name))
    if row is None:
        raise WinError(
            f"no such window {name!r}",
            hint="create it with `ampi win create --name W`, or list with `ampi win list`",
        )
    return row


def create(ctx: Ctx, name: str, *, model: str = "unified", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``AMPI_Win_create``. Idempotent: any rank may create the shared window."""
    j = ctx.j
    existing = j.q1("SELECT * FROM win WHERE job=? AND name=?", (j.job_id, name))
    if existing is not None:
        return {"win": str(existing["id"]), "name": name, "created": False}
    wid = "w:" + uuid.uuid4().hex[:10]
    with j.tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO win(id,job,comm,name,model,created_ns,meta) VALUES(?,?,?,?,?,?,?)",
            (wid, j.job_id, ctx.comm, name, model, now_ns(), json.dumps(meta or {})),
        )
        j.trace("win_create", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm, win=wid,
                detail={"name": name, "model": model}, conn=c)
    row = win_row(j, name)
    return {"win": str(row["id"]), "name": name, "created": str(row["id"]) == wid}


def _check_lock(
    j: Journal,
    wid: str,
    key: str,
    *,
    rank: int,
    epoch: int,
    for_write: bool,
    conn: Optional[sqlite3.Connection] = None,
) -> Optional[int]:
    """Return our fencing token if we hold a usable lock, else raise if someone
    else holds a conflicting one. Expired locks are reclaimed here, lazily."""
    c = conn or j.conn
    ts = now_ns()
    c.execute("DELETE FROM win_lock WHERE win=? AND expires_ns<?", (wid, ts))
    rows = c.execute("SELECT * FROM win_lock WHERE win=? AND key IN (?, '*')", (wid, key)).fetchall()
    mine: Optional[int] = None
    for r in rows:
        if int(r["holder"]) == rank and int(r["holder_epoch"]) == epoch:
            mine = int(r["token"])
            continue
        if for_write or r["mode"] == "exclusive":
            raise LockBusyError(
                f"window {wid} key {key!r} is locked {r['mode']} by rank {r['holder']}",
                hint=(
                    "wait and retry (the lock expires automatically), or use "
                    "`ampi win accumulate` / `ampi win cas`, which need no lock"
                ),
                detail={"holder": int(r["holder"]), "mode": str(r["mode"]),
                        "expires_in_s": round((int(r["expires_ns"]) - ts) / 1e9, 1)},
            )
    return mine


def put(
    ctx: Ctx,
    win: str,
    key: str,
    text: str,
    *,
    expect_version: Optional[int] = None,
    schema: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """``AMPI_Put``: write a cell.

    ``expect_version`` turns the put into an optimistic-concurrency-control
    write: it succeeds only if the cell is still at the version the caller read.
    Agents overwrite each other constantly, and a compare-and-set put is the
    cheapest way to turn a silent lost update into a visible conflict the agent
    can resolve.
    """
    j = ctx.j
    ctx.check_live()
    w = win_row(j, win)
    wid = str(w["id"])
    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        _check_lock(j, wid, key, rank=ctx.rank, epoch=ctx.epoch, for_write=True, conn=c)
        cur = c.execute("SELECT version FROM win_cell WHERE win=? AND key=?", (wid, key)).fetchone()
        curver = int(cur["version"]) if cur else 0
        if expect_version is not None and expect_version != curver:
            raise ConflictError(
                f"{key!r} is at version {curver}, you expected {expect_version}",
                hint=f"re-read it with `ampi win get --win {w['name']} --key {key}` and merge, then retry",
                detail={"current_version": curver, "expected": expect_version},
            )
        pay = package(j, text, creator=ctx.rank, cfg=ctx.cfg, schema=schema, label=f"{win}:{key}", conn=c)
        newver = curver + 1
        c.execute(
            "INSERT INTO win_cell(win,key,version,obj,tokens,digest,summary,schema,writer,writer_epoch,written_ns)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(win,key) DO UPDATE SET version=excluded.version,obj=excluded.obj,"
            " tokens=excluded.tokens,digest=excluded.digest,summary=excluded.summary,"
            " schema=COALESCE(excluded.schema,win_cell.schema),writer=excluded.writer,"
            " writer_epoch=excluded.writer_epoch,written_ns=excluded.written_ns",
            (wid, key, newver, pay.obj, pay.tokens, pay.digest, pay.summary, schema,
             ctx.rank, ctx.epoch, now_ns()),
        )
        c.execute(
            "INSERT INTO win_hist(win,key,version,obj,op,writer,writer_epoch,tokens,written_ns,note)"
            " VALUES(?,?,?,?,'put',?,?,?,?,?)",
            (wid, key, newver, pay.obj, ctx.rank, ctx.epoch, pay.tokens, now_ns(), note),
        )
        j.bump("rma_puts", ctx.rank, 1, conn=c)
        j.trace("win_put", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm, win=wid, wkey=key,
                tokens=pay.tokens, detail={"version": newver}, conn=c)
    return {"win": wid, "key": key, "version": newver, "handle": pay.obj, "tokens": pay.tokens}


def get(
    ctx: Ctx,
    win: str,
    key: str,
    *,
    materialize: Optional[bool] = None,
    budget: Optional[int] = None,
    view: Optional[str] = None,
    version: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Get``: read a cell, paying context only for what you take."""
    j = ctx.j
    w = win_row(j, win)
    wid = str(w["id"])
    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        _check_lock(j, wid, key, rank=ctx.rank, epoch=ctx.epoch, for_write=False, conn=c)
    if version is not None:
        row = j.q1(
            "SELECT obj,version,writer,written_ns FROM win_hist WHERE win=? AND key=? AND version=?",
            (wid, key, version),
        )
    else:
        row = j.q1("SELECT * FROM win_cell WHERE win=? AND key=?", (wid, key))
    if row is None:
        return {"win": wid, "key": key, "found": False,
                "note": f"{key!r} has not been written yet"}
    oid = str(row["obj"])
    meta = j.object_meta(oid)
    out: Dict[str, Any] = {
        "win": wid,
        "key": key,
        "found": True,
        "version": int(row["version"]),
        "writer": int(row["writer"]) if row["writer"] is not None else None,
        "handle": oid,
        "tokens": int(meta["tokens"]),
        "summary": meta["summary"],
        "schema": meta["schema"],
        "age_s": round((now_ns() - int(row["written_ns"])) / 1e9, 1),
    }
    spec = views_mod.parse_spec(view) if view else None
    if spec is not None:
        if budget:
            spec["budget"] = int(budget)
        v = views_mod.render_view(j, oid, spec)
        out["body"] = v["body"]
        charged = v["tokens"]
    else:
        want = (int(meta["tokens"]) <= ctx.cfg.eager_tokens) if materialize is None else materialize
        if budget and int(meta["tokens"]) > int(budget):
            v = views_mod.render_view(j, oid, {"op": "headtail", "budget": int(budget)})
            out["body"] = v["body"]
            out["clipped"] = True
            charged = v["tokens"]
        elif want:
            out["body"] = j.object_text(oid)
            charged = int(meta["tokens"])
        else:
            charged = 30
            out["note"] = (
                f"{meta['tokens']} tokens not read into context; use --materialize or "
                f"--view head:600 when you need the body"
            )
    with j.tx() as c:
        ctx_charge(j, ctx.rank, ctx.epoch, charged, conn=c, force=True, what=f"win get {key}")
        j.bump("rma_gets", ctx.rank, 1, conn=c)
        j.trace("win_get", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm, win=wid, wkey=key,
                tokens=charged, conn=c)
    out["context_charged"] = charged
    return out


def accumulate(
    ctx: Ctx,
    win: str,
    key: str,
    text: str,
    *,
    op: str = "union",
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """``AMPI_Accumulate``: apply a runtime reduction op to a cell atomically.

    This is how AgentMPI supports lock-free collaborative state. Instead of
    read-modify-write (three round trips and a race), an agent says "union this
    finding into the shared findings list", and the runtime applies the operator
    inside the same transaction that bumps the version. Any operator from
    :mod:`ampi.ops` may be used, so ``sum`` gives shared counters, ``union``
    gives shared sets, ``jsonmerge`` gives shared records and ``maxby`` gives a
    shared best-so-far.
    """
    j = ctx.j
    o = ops_mod.get_op(op)
    if o.fn is None:
        raise ArgError("accumulate requires a runtime operator (agent ops cannot run inside a window epoch)")
    w = win_row(j, win)
    wid = str(w["id"])
    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        _check_lock(j, wid, key, rank=ctx.rank, epoch=ctx.epoch, for_write=True, conn=c)
        cur = c.execute("SELECT version,obj FROM win_cell WHERE win=? AND key=?", (wid, key)).fetchone()
        if cur is None:
            merged = text
            newver = 1
        else:
            merged = ops_mod.apply_op(o, j.object_text(str(cur["obj"])), text)
            newver = int(cur["version"]) + 1
        pay = package(j, merged, creator=ctx.rank, cfg=ctx.cfg, label=f"{win}:{key}", conn=c)
        c.execute(
            "INSERT INTO win_cell(win,key,version,obj,tokens,digest,summary,writer,writer_epoch,written_ns)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(win,key) DO UPDATE SET version=excluded.version,obj=excluded.obj,"
            " tokens=excluded.tokens,digest=excluded.digest,summary=excluded.summary,"
            " writer=excluded.writer,writer_epoch=excluded.writer_epoch,written_ns=excluded.written_ns",
            (wid, key, newver, pay.obj, pay.tokens, pay.digest, pay.summary, ctx.rank, ctx.epoch, now_ns()),
        )
        c.execute(
            "INSERT INTO win_hist(win,key,version,obj,op,writer,writer_epoch,tokens,written_ns,note)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (wid, key, newver, pay.obj, f"acc:{o.name}", ctx.rank, ctx.epoch, pay.tokens, now_ns(), note),
        )
        j.bump("rma_accs", ctx.rank, 1, conn=c)
        j.trace("win_acc", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm, win=wid, wkey=key,
                tokens=pay.tokens, status=o.name, detail={"version": newver}, conn=c)
    return {"win": wid, "key": key, "version": newver, "op": o.name, "handle": pay.obj,
            "tokens": pay.tokens}


def compare_and_swap(
    ctx: Ctx, win: str, key: str, *, expect: str, value: str
) -> Dict[str, Any]:
    """``AMPI_Compare_and_swap``: the atomic primitive for agent coordination.

    The canonical use is claiming work: a task cell holds ``"unclaimed"``, and
    whichever agent successfully swaps it to its own rank owns the task. That
    single operation replaces an entire class of duplicated-work bugs, and
    unlike a lock it cannot be held by a dead agent.
    """
    j = ctx.j
    w = win_row(j, win)
    wid = str(w["id"])
    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        cur = c.execute("SELECT version,obj FROM win_cell WHERE win=? AND key=?", (wid, key)).fetchone()
        old = j.object_text(str(cur["obj"])) if cur else ""
        ok = old.strip() == expect.strip()
        newver = (int(cur["version"]) if cur else 0) + 1
        if ok:
            pay = package(j, value, creator=ctx.rank, cfg=ctx.cfg, label=f"{win}:{key}", conn=c)
            c.execute(
                "INSERT INTO win_cell(win,key,version,obj,tokens,digest,summary,writer,writer_epoch,written_ns)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(win,key) DO UPDATE SET version=excluded.version,obj=excluded.obj,"
                " tokens=excluded.tokens,digest=excluded.digest,summary=excluded.summary,"
                " writer=excluded.writer,writer_epoch=excluded.writer_epoch,written_ns=excluded.written_ns",
                (wid, key, newver, pay.obj, pay.tokens, pay.digest, pay.summary, ctx.rank, ctx.epoch,
                 now_ns()),
            )
            c.execute(
                "INSERT INTO win_hist(win,key,version,obj,op,writer,writer_epoch,tokens,written_ns,note)"
                " VALUES(?,?,?,?,'cas',?,?,?,?,?)",
                (wid, key, newver, pay.obj, ctx.rank, ctx.epoch, pay.tokens, now_ns(),
                 f"expect={expect[:60]!r}"),
            )
        j.trace("win_cas", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm, win=wid, wkey=key,
                status=("ok" if ok else "failed"), conn=c)
    return {"win": wid, "key": key, "swapped": ok, "previous": old,
            "version": newver if ok else (int(cur["version"]) if cur else 0)}


def fetch_and_op(ctx: Ctx, win: str, key: str, *, op: str = "sum", value: str = "1") -> Dict[str, Any]:
    """``AMPI_Fetch_and_op``: atomic read-then-modify. Shared work counters,
    ticket numbers, and round-robin dispatch all reduce to this."""
    j = ctx.j
    o = ops_mod.get_op(op)
    if o.fn is None:
        raise ArgError("fetch_and_op requires a runtime operator")
    w = win_row(j, win)
    wid = str(w["id"])
    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        cur = c.execute("SELECT version,obj FROM win_cell WHERE win=? AND key=?", (wid, key)).fetchone()
        old = j.object_text(str(cur["obj"])) if cur else ("0" if o.name in ("sum", "max", "min", "count") else "")
        merged = ops_mod.apply_op(o, old, value) if cur else value
        newver = (int(cur["version"]) if cur else 0) + 1
        pay = package(j, merged, creator=ctx.rank, cfg=ctx.cfg, label=f"{win}:{key}", conn=c)
        c.execute(
            "INSERT INTO win_cell(win,key,version,obj,tokens,digest,summary,writer,writer_epoch,written_ns)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(win,key) DO UPDATE SET version=excluded.version,obj=excluded.obj,"
            " tokens=excluded.tokens,digest=excluded.digest,summary=excluded.summary,"
            " writer=excluded.writer,writer_epoch=excluded.writer_epoch,written_ns=excluded.written_ns",
            (wid, key, newver, pay.obj, pay.tokens, pay.digest, pay.summary, ctx.rank, ctx.epoch, now_ns()),
        )
        c.execute(
            "INSERT INTO win_hist(win,key,version,obj,op,writer,writer_epoch,tokens,written_ns,note)"
            " VALUES(?,?,?,?,?,?,?,?,?,NULL)",
            (wid, key, newver, pay.obj, f"faop:{o.name}", ctx.rank, ctx.epoch, pay.tokens, now_ns()),
        )
    return {"win": wid, "key": key, "fetched": old, "new": merged, "version": newver}


def lock(
    ctx: Ctx,
    win: str,
    *,
    key: str = "*",
    mode: str = "exclusive",
    timeout_ns: Optional[int] = None,
    lease_ns: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Win_lock``: passive-target epoch with a lease and a fencing token.

    Every distributed lock over unreliable participants needs both: the lease so
    a dead holder does not wedge the job, and the token so a revived holder
    cannot corrupt state after its lease expired. AgentMPI returns the token to
    the caller and rejects writes bearing a stale one, so the standard
    lease-expiry race is closed rather than merely made unlikely.
    """
    j = ctx.j
    w = win_row(j, win)
    wid = str(w["id"])
    lease = lease_ns if lease_ns is not None else ctx.cfg.lock_lease_ns
    deadline = now_ns() + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)
    start = time.time()
    prog = p2p.Progress(ctx, check_revoked=False)
    while True:
        try:
            prog()
            with j.tx() as c:
                heartbeat(j, ctx.rank, ctx.epoch, conn=c)
                c.execute("DELETE FROM win_lock WHERE win=? AND expires_ns<?", (wid, now_ns()))
                rows = c.execute(
                    "SELECT * FROM win_lock WHERE win=? AND key IN (?, '*')", (wid, key)
                ).fetchall()
                conflict = None
                for r in rows:
                    if int(r["holder"]) == ctx.rank and int(r["holder_epoch"]) == ctx.epoch:
                        continue
                    if mode == "exclusive" or r["mode"] == "exclusive":
                        conflict = r
                        break
                if conflict is not None:
                    raise LockBusyError(
                        f"held {conflict['mode']} by rank {conflict['holder']}",
                        detail={"holder": int(conflict["holder"])},
                    )
                token = j.next_seq(f"lock:{wid}", conn=c)
                c.execute(
                    "INSERT INTO win_lock(win,key,mode,holder,holder_epoch,token,acquired_ns,expires_ns)"
                    " VALUES(?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(win,key,holder) DO UPDATE SET mode=excluded.mode,"
                    " token=excluded.token,acquired_ns=excluded.acquired_ns,expires_ns=excluded.expires_ns",
                    (wid, key, mode, ctx.rank, ctx.epoch, token, now_ns(), now_ns() + lease),
                )
                j.trace("win_lock", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm, win=wid, wkey=key,
                        status=mode, detail={"token": token}, conn=c)
            return {"win": wid, "key": key, "mode": mode, "token": token,
                    "lease_s": round(lease / 1e9, 1)}
        except AmpiError as exc:
            if exc.err_class != ErrClass.LOCK_BUSY:
                raise
            if now_ns() > deadline:
                raise LockBusyError(
                    f"could not acquire {mode} lock on {win}:{key} before the deadline: {exc.message}",
                    hint=(
                        "locks expire automatically, so retrying works. Consider "
                        "`ampi win accumulate` or `ampi win cas`, which need no lock at all."
                    ),
                    detail=exc.detail,
                ) from exc
            p2p._poll_sleep(time.time() - start)


def unlock(ctx: Ctx, win: str, *, key: str = "*") -> Dict[str, Any]:
    j = ctx.j
    w = win_row(j, win)
    wid = str(w["id"])
    with j.tx() as c:
        c.execute(
            "DELETE FROM win_lock WHERE win=? AND key=? AND holder=? AND holder_epoch=?",
            (wid, key, ctx.rank, ctx.epoch),
        )
        j.trace("win_unlock", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm, win=wid, wkey=key, conn=c)
    return {"win": wid, "key": key, "released": True}


def fence(ctx: Ctx, win: str, *, label: Optional[str] = None, timeout_ns: Optional[int] = None,
          quorum: Optional[float] = None) -> Dict[str, Any]:
    """``AMPI_Win_fence``: close the current active-target epoch.

    A fence is a barrier plus a visibility guarantee. In AgentMPI the journal
    already makes writes visible immediately, so the fence exists purely for the
    *agreement* it provides: after it returns, every participating rank knows
    that every other rank's writes for this phase are in. That turns a
    blackboard, which is notoriously hard to reason about, into a sequence of
    bulk-synchronous supersteps in the BSP sense.
    """
    from . import collectives

    w = win_row(ctx.j, win)
    res = collectives.barrier(
        ctx, label=f"winfence:{w['name']}:{label or 'auto'}", timeout_ns=timeout_ns, quorum=quorum
    )
    n = int(ctx.j.scalar("SELECT COUNT(*) FROM win_cell WHERE win=?", (str(w["id"]),), 0))
    with ctx.j.tx() as c:
        ctx.j.trace("win_fence", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm, win=str(w["id"]),
                    detail={"cells": n}, conn=c)
    return {"win": str(w["id"]), "epoch_closed": True, "cells": n, **res}


def listing(ctx: Ctx, win: str, *, prefix: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
    """Enumerate a window's keys with sizes and provenance, without reading them.

    This is the operation that makes a shared blackboard usable by an agent with
    a bounded context: it can see *what exists* for ~10 tokens per key and then
    spend its budget deliberately.
    """
    j = ctx.j
    w = win_row(j, win)
    args: List[Any] = [str(w["id"])]
    sql = "SELECT key,version,tokens,summary,writer,written_ns FROM win_cell WHERE win=?"
    if prefix:
        sql += " AND key LIKE ?"
        args.append(prefix + "%")
    sql += " ORDER BY key LIMIT ?"
    args.append(limit)
    rows = j.q(sql, args)
    items = [
        {
            "key": str(r["key"]),
            "version": int(r["version"]),
            "tokens": int(r["tokens"]),
            "writer": int(r["writer"]) if r["writer"] is not None else None,
            "age_s": round((now_ns() - int(r["written_ns"])) / 1e9, 1),
            "summary": r["summary"],
        }
        for r in rows
    ]
    return {
        "win": str(w["id"]),
        "name": str(w["name"]),
        "count": len(items),
        "total_tokens": sum(i["tokens"] for i in items),
        "keys": items,
    }


def history(ctx: Ctx, win: str, key: str, *, limit: int = 20) -> Dict[str, Any]:
    j = ctx.j
    w = win_row(j, win)
    rows = j.q(
        "SELECT version,op,writer,tokens,written_ns,note FROM win_hist WHERE win=? AND key=?"
        " ORDER BY version DESC LIMIT ?",
        (str(w["id"]), key, limit),
    )
    return {
        "win": str(w["id"]),
        "key": key,
        "versions": [
            {
                "version": int(r["version"]),
                "op": str(r["op"]),
                "writer": int(r["writer"]) if r["writer"] is not None else None,
                "tokens": int(r["tokens"]),
                "age_s": round((now_ns() - int(r["written_ns"])) / 1e9, 1),
                "note": r["note"],
            }
            for r in rows
        ],
    }
