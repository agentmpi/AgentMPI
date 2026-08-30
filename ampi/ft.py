"""Fault tolerance: revoke, shrink, agree, respawn, recover.

MPI's answer to process failure was, for twenty years, that there isn't one:
after an error the state of MPI is undefined and the default error handler
aborts the job. That is a defensible engineering choice when a node fails once a
week and a checkpoint costs minutes. It is indefensible when the executors are
LLM agents, which fail on the timescale of a single task, in more ways, and
usually without stopping.

AgentMPI adopts ULFM's design, because ULFM's principles are exactly right for
this setting:

1. **Failure notification is local.** There is no globally consistent view of
   who is alive; a rank learns about a failure when an operation it issued
   cannot complete. AgentMPI's detector is lazy and lease-based for this reason.
2. **The runtime does not choose the recovery strategy.** It provides revoke,
   shrink and agree; whether to redistribute work, respawn, or degrade is the
   harness author's decision, because only they know the application's
   semantics.
3. **Failure-free performance must not suffer.** Detection costs one timestamp
   update per runtime call.

To ULFM's three primitives AgentMPI adds two that the agent setting forces:

* ``respawn`` -- MPI has ``MPI_Comm_spawn``, but replacing a *failed* rank
  in-place, at a higher fencing epoch, with its predecessor's committed state
  intact, is the operation an agent harness actually needs.
* ``recover`` -- the briefing a replacement reads to learn what its predecessor
  owned, published, promised and left outstanding. This is the durable-execution
  idea (replay a recorded history rather than restore a memory image) adapted to
  an executor whose "memory image" was a context window that no longer exists.

The failure taxonomy the module works against, all of which the evaluation
injects: hard crash, silent stall, context exhaustion, cost exhaustion, protocol
violation, zombie (declared dead but still running), plausible-but-wrong output,
and deadlock inside a collective.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from . import p2p
from .core import (
    Ctx,
    comm_members,
    comm_row,
    comm_to_world,
    detect_failures,
    failed_ranks,
    heartbeat,
    live_ranks,
    rank_row,
    world_to_comm,
    _create_comm,
)
from .errors import (
    AmpiError,
    ArgError,
    CommError,
    ErrClass,
    ProcFailedError,
    RevokedError,
    TimeoutError_,
)
from .journal import Journal, now_ns

#: Failure kinds AgentMPI distinguishes. The distinction matters because the
#: right recovery differs: a stalled agent may be waited for, an
#: context-exhausted one must be replaced with a smaller assignment, and a
#: protocol violator must not simply be restarted with the same prompt.
FAILURE_KINDS = (
    "lease_expired",      # detector fired: no runtime call within the lease
    "crash",              # launcher observed the process exit non-zero
    "abort",              # the agent called AMPI_Abort
    "ctx_exhausted",      # context budget consumed without completing
    "budget_exhausted",   # monetary/token budget consumed
    "protocol_violation", # produced output that failed its declared schema
    "wrong_answer",       # a verifier rejected the result (soft/Byzantine)
    "killed",             # deliberately injected by the fault-injection harness
    "zombie",             # still running after being declared failed
)


# --------------------------------------------------------------------------
# Revoke
# --------------------------------------------------------------------------


def revoke(ctx: Ctx, *, reason: Optional[str] = None) -> Dict[str, Any]:
    """``AMPI_Comm_revoke``.

    Revocation is the primitive that makes fault tolerance possible at all, and
    the reason is worth stating because it is counter-intuitive: when a rank
    fails, the *surviving* ranks are the problem. They are blocked inside
    collectives that can never complete, and each of them will only discover the
    failure if it happens to be waiting on the dead rank directly. Revoking the
    communicator makes every pending and future non-local operation on it fail
    immediately, everywhere, which is what lets all survivors reach the recovery
    code path together.

    Any rank may revoke; revocation cannot be undone. The only way forward is
    ``shrink``.
    """
    j = ctx.j
    row = comm_row(j, ctx.comm)
    with j.tx() as c:
        c.execute(
            "UPDATE comm SET revoked=1, revoked_by=COALESCE(revoked_by,?), revoked_ns=COALESCE(revoked_ns,?)"
            " WHERE id=?",
            (ctx.rank, now_ns(), str(row["id"])),
        )
        # Cancel pending receives so that survivors' polls fail fast rather than
        # waiting out their deadlines.
        c.execute(
            "UPDATE recvq SET status='cancelled' WHERE comm=? AND status='posted'", (str(row["id"]),)
        )
        c.execute(
            "UPDATE coll SET state='revoked' WHERE comm=? AND state='open'", (str(row["id"]),)
        )
        j.trace("comm_revoke", rank=ctx.rank, epoch=ctx.epoch, comm=str(row["id"]),
                detail={"reason": reason}, conn=c)
    return {
        "comm": str(row["name"]),
        "revoked": True,
        "by": ctx.rank,
        "reason": reason,
        "next": f"every rank must now call `ampi comm shrink --comm {row['name']}`",
    }


# --------------------------------------------------------------------------
# Agree
# --------------------------------------------------------------------------


def agree(
    ctx: Ctx,
    *,
    label: str,
    flag: bool = True,
    value: Optional[str] = None,
    timeout_ns: Optional[int] = None,
    quorum: Optional[float] = None,
) -> Dict[str, Any]:
    """``AMPI_Comm_agree``: fault-tolerant agreement over a flag.

    ULFM's ``MPIX_Comm_agree`` is the one primitive in the whole failure-handling
    toolkit that requires consensus, and it is required because "did everyone
    successfully finish phase 3?" cannot be answered by any amount of local
    information. It works on a revoked communicator, which is precisely what
    makes it usable during recovery.

    AgentMPI's implementation exploits the shared journal (which is a sequentially
    consistent store, so consensus is not the hard part) and adds a quorum knob
    for the case where a straggler must not hold the survivors hostage.
    """
    j = ctx.j
    cid = ctx.comm
    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        row = c.execute(
            "SELECT * FROM agree WHERE comm=? AND json_extract(result,'$.label')=?", (cid, label)
        ).fetchone()
        if row is None:
            row = c.execute(
                "SELECT a.* FROM agree a WHERE a.comm=? AND a.state='open' AND a.id IN"
                " (SELECT id FROM agree WHERE comm=? AND state='open')"
                " AND EXISTS(SELECT 1 FROM agree WHERE id=a.id) AND a.id LIKE ? LIMIT 1",
                (cid, cid, f"a:{label}%"),
            ).fetchone()
        if row is None:
            aid = f"a:{label}:{uuid.uuid4().hex[:6]}"
            existing = c.execute(
                "SELECT * FROM agree WHERE comm=? AND id LIKE ? LIMIT 1", (cid, f"a:{label}:%")
            ).fetchone()
            if existing is not None:
                row = existing
            else:
                seqno = int(
                    c.execute("SELECT COALESCE(MAX(seqno),-1)+1 FROM agree WHERE comm=?", (cid,)).fetchone()[0]
                )
                c.execute(
                    "INSERT INTO agree(id,comm,seqno,state,created_ns,result) VALUES(?,?,?,'open',?,?)",
                    (aid, cid, seqno, now_ns(), json.dumps({"label": label})),
                )
                row = c.execute("SELECT * FROM agree WHERE id=?", (aid,)).fetchone()
        aid = str(row["id"])
        c.execute(
            "INSERT INTO agree_part(agree,crank,flag,value,ns) VALUES(?,?,?,?,?)"
            " ON CONFLICT(agree,crank) DO UPDATE SET flag=excluded.flag,value=excluded.value,ns=excluded.ns",
            (aid, ctx.crank, 1 if flag else 0, value, now_ns()),
        )
    deadline = now_ns() + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)
    q = quorum if quorum is not None else 1.0
    start = time.time()
    # Agreement is the one collective that must keep working on a *revoked*
    # communicator: it is how survivors coordinate their recovery. So the
    # progress engine here checks liveness but deliberately ignores revocation.
    prog = p2p.Progress(ctx, check_revoked=False)
    while True:
        prog()
        dead = set(failed_ranks(j, cid))
        live = [i for i, w in enumerate(comm_members(j, cid)) if w not in dead]
        parts = j.q("SELECT crank,flag,value FROM agree_part WHERE agree=?", (aid,))
        got = {int(r["crank"]): (bool(r["flag"]), r["value"]) for r in parts}
        got_live = {k: v for k, v in got.items() if k in live}
        need = max(1, math.ceil(q * len(live)))
        if len(got_live) >= need:
            allflag = all(v[0] for v in got_live.values())
            values = [v[1] for v in got_live.values() if v[1] is not None]
            res = {
                "label": label,
                "agreed": allflag,
                "participants": sorted(got_live),
                "failed": sorted(dead),
                "values": values,
            }
            with j.tx() as c:
                c.execute(
                    "UPDATE agree SET state='closed', closed_ns=COALESCE(closed_ns,?), result=? WHERE id=?",
                    (now_ns(), json.dumps(res, ensure_ascii=False), aid),
                )
                j.trace("comm_agree", rank=ctx.rank, epoch=ctx.epoch, comm=cid,
                        status=("agreed" if allflag else "disagreed"),
                        detail={"label": label, "n": len(got_live)}, conn=c)
            return {"agree": aid, **res}
        if now_ns() > deadline:
            raise TimeoutError_(
                f"AMPI_Comm_agree({label}): {len(got_live)}/{need} ranks have voted",
                hint="your vote is recorded; re-run to keep waiting",
                detail={"voted": sorted(got_live), "missing": sorted(set(live) - set(got_live))},
            )
        p2p._poll_sleep(time.time() - start)


# --------------------------------------------------------------------------
# Shrink
# --------------------------------------------------------------------------


def shrink(
    ctx: Ctx,
    *,
    name: Optional[str] = None,
    timeout_ns: Optional[int] = None,
    quorum: Optional[float] = None,
) -> Dict[str, Any]:
    """``AMPI_Comm_shrink``: derive a working communicator over the survivors.

    Two details make this correct rather than merely plausible. First, the set of
    survivors must be *agreed*, not locally computed, or two ranks end up with
    differently-sized communicators and every subsequent collective mismatches;
    we agree on it through the journal. Second, the new communicator gets a fresh
    generation number, so stale in-flight traffic addressed to the old one cannot
    be mistaken for traffic on the new one -- the communicator-level analogue of
    a rank's fencing epoch.

    Ranks are renumbered densely, preserving relative order, exactly as ULFM
    specifies. Harnesses must therefore treat rank identity as *communicator-
    relative* and never cache it across a shrink -- the same discipline
    fault-tolerant MPI codes need.
    """
    j = ctx.j
    old = comm_row(j, ctx.comm)
    gen = int(old["generation"]) + 1
    new_name = name or f"{old['name']}#g{gen}"
    existing = j.q1("SELECT * FROM comm WHERE job=? AND name=?", (j.job_id, new_name))
    if existing is not None:
        # Another survivor already agreed the membership and built it. Every
        # caller must report the same excluded set, or two survivors would
        # disagree about who died -- so derive it from the two memberships
        # rather than from this rank's local failure view.
        members = comm_members(j, str(existing["id"]))
        excluded = sorted(set(comm_members(j, str(old["id"]))) - set(members))
        return _shrink_result(ctx, existing, members, created=False, excluded=excluded)

    ag = agree(
        ctx,
        label=f"shrink:{old['name']}:g{gen}",
        flag=True,
        value=json.dumps({"alive": ctx.rank}),
        timeout_ns=timeout_ns,
        quorum=quorum if quorum is not None else 0.0,
    )
    with j.tx() as c:
        detect_failures(j, ctx.comm, by=ctx.rank, conn=c)
    dead = set(failed_ranks(j, ctx.comm))
    survivors = [w for w in comm_members(j, ctx.comm) if w not in dead]
    if not survivors:
        raise CommError("no survivors: the communicator cannot be shrunk")
    cid = "c:" + uuid.uuid4().hex[:10]
    with j.tx() as c:
        row = c.execute("SELECT * FROM comm WHERE job=? AND name=?", (j.job_id, new_name)).fetchone()
        if row is None:
            _create_comm(
                c, j, comm_id=cid, name=new_name, members=survivors, kind=str(old["kind"]),
                parent=str(old["id"]), topo=json.loads(old["topo"] or "{}"), generation=gen,
            )
            row = c.execute("SELECT * FROM comm WHERE id=?", (cid,)).fetchone()
        j.trace("comm_shrink", rank=ctx.rank, epoch=ctx.epoch, comm=str(old["id"]),
                detail={"new": str(row["id"]), "name": new_name, "survivors": survivors,
                        "excluded": sorted(dead)}, conn=c)
    return _shrink_result(ctx, row, survivors, created=True, excluded=sorted(dead), agreement=ag)


def _shrink_result(
    ctx: Ctx,
    row: sqlite3.Row,
    members: Sequence[int],
    *,
    created: bool,
    excluded: Optional[List[int]] = None,
    agreement: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    my_new = members.index(ctx.rank) if ctx.rank in members else None
    return {
        "comm": str(row["name"]),
        "comm_id": str(row["id"]),
        "generation": int(row["generation"]),
        "size": len(members),
        "your_new_rank": my_new,
        "members_world": list(members),
        "excluded": excluded or [],
        "created": created,
        "next": (
            f"use `--comm {row['name']}` from now on; your rank in it is {my_new}"
            if my_new is not None
            else "you are not a member of the shrunken communicator"
        ),
        **({"agreement": agreement} if agreement else {}),
    }


# --------------------------------------------------------------------------
# Failure acknowledgement / inspection
# --------------------------------------------------------------------------


def failure_ack(ctx: Ctx) -> Dict[str, Any]:
    """``AMPI_Comm_failure_ack``: accept the currently known failures.

    Acknowledging has a concrete effect, as in ULFM: it re-enables wildcard
    receives on the communicator, which would otherwise keep returning
    ``AMPI_ERR_PROC_FAILED_PENDING`` forever. The pattern is "learn who died,
    accept it, keep receiving from whoever is left".
    """
    j = ctx.j
    with j.tx() as c:
        detect_failures(j, ctx.comm, by=ctx.rank, conn=c)
        dead = failed_ranks(j, ctx.comm)
        for d in dead:
            c.execute(
                "INSERT OR IGNORE INTO failure_ack(job,comm,acker,failed,ack_ns) VALUES(?,?,?,?,?)",
                (j.job_id, ctx.comm, ctx.rank, d, now_ns()),
            )
        j.trace("failure_ack", rank=ctx.rank, epoch=ctx.epoch, comm=ctx.comm,
                detail={"acked": dead}, conn=c)
    return {"acknowledged": dead, "count": len(dead)}


def failure_get_acked(ctx: Ctx) -> Dict[str, Any]:
    rows = ctx.j.q(
        "SELECT failed FROM failure_ack WHERE job=? AND comm=? AND acker=? ORDER BY failed",
        (ctx.j.job_id, ctx.comm, ctx.rank),
    )
    return {"acked": [int(r["failed"]) for r in rows]}


def get_failed(ctx: Ctx) -> Dict[str, Any]:
    """``AMPI_Comm_get_failed`` plus the diagnostic detail an agent needs."""
    j = ctx.j
    with j.tx() as c:
        detect_failures(j, ctx.comm, by=ctx.rank, conn=c)
    dead = failed_ranks(j, ctx.comm)
    out = []
    for w in dead:
        r = rank_row(j, w)
        f = j.q1(
            "SELECT kind,detected_ns,detail FROM failure WHERE job=? AND rank=? ORDER BY id DESC LIMIT 1",
            (j.job_id, w),
        )
        out.append(
            {
                "world": w,
                "comm_rank": world_to_comm(j, ctx.comm, w),
                "epoch": int(r["epoch"]),
                "role": r["role"],
                "kind": str(f["kind"]) if f else "unknown",
                "detected_s_ago": round((now_ns() - int(f["detected_ns"])) / 1e9, 1) if f else None,
                "ctx_used": int(r["ctx_used"]),
            }
        )
    return {"failed": out, "count": len(out), "live": len(live_ranks(j, ctx.comm))}


def declare_failed(
    j: Journal,
    rank: int,
    *,
    kind: str = "killed",
    by: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Mark a rank failed without waiting for its lease. Used by the launcher
    when it observes a process exit, and by the fault-injection harness."""
    if kind not in FAILURE_KINDS:
        raise ArgError(f"unknown failure kind {kind!r}", hint="one of: " + ", ".join(FAILURE_KINDS))
    with j.tx() as c:
        row = c.execute("SELECT epoch FROM rank WHERE job=? AND rank=?", (j.job_id, rank)).fetchone()
        if row is None:
            raise ArgError(f"no such rank {rank}")
        epoch = int(row["epoch"])
        c.execute("UPDATE rank SET state='failed', lease_expires_ns=1 WHERE job=? AND rank=?",
                  (j.job_id, rank))
        c.execute(
            "INSERT INTO failure(job,rank,epoch,kind,detected_ns,detected_by,detail)"
            " VALUES(?,?,?,?,?,?,?)",
            (j.job_id, rank, epoch, kind, now_ns(), by, json.dumps(detail or {})),
        )
        # Break any locks the dead rank held so survivors are not wedged.
        c.execute("DELETE FROM win_lock WHERE holder=? AND holder_epoch=?", (rank, epoch))
        j.trace("failure", rank=rank, epoch=epoch, status=kind, detail={"declared_by": by}, conn=c)
    return {"rank": rank, "epoch": epoch, "kind": kind, "state": "failed"}


# --------------------------------------------------------------------------
# Respawn and recovery briefing
# --------------------------------------------------------------------------


def respawn(j: Journal, rank: int, *, role: Optional[str] = None) -> Dict[str, Any]:
    """Prepare rank ``rank`` for a replacement agent at epoch+1.

    The old epoch is fenced, not deleted: its messages remain in the journal (a
    survivor may still need to see what it sent), its locks are broken, its
    outstanding receives are cancelled, and any collective it was mid-way
    through records it as absent so the collective can still close.
    """
    with j.tx() as c:
        row = c.execute("SELECT * FROM rank WHERE job=? AND rank=?", (j.job_id, rank)).fetchone()
        if row is None:
            raise ArgError(f"no such rank {rank}")
        old_epoch = int(row["epoch"])
        new_epoch = old_epoch + 1
        c.execute(
            "UPDATE rank SET epoch=?, state='spawned', role=COALESCE(?,role), ctx_used=0,"
            " last_hb_ns=?, lease_expires_ns=?+lease_ns WHERE job=? AND rank=?",
            (new_epoch, role, now_ns(), now_ns(), j.job_id, rank),
        )
        c.execute("DELETE FROM win_lock WHERE holder=? AND holder_epoch=?", (rank, old_epoch))
        c.execute(
            "UPDATE recvq SET status='cancelled' WHERE dst IN"
            " (SELECT crank FROM comm_member WHERE wrank=?) AND status='posted' AND dst_epoch=?",
            (rank, old_epoch),
        )
        c.execute(
            "UPDATE request SET state='failed' WHERE job=? AND rank=? AND epoch=? AND state='active'",
            (j.job_id, rank, old_epoch),
        )
        j.trace("respawn", rank=rank, epoch=new_epoch,
                detail={"old_epoch": old_epoch}, conn=c)
    return {"rank": rank, "old_epoch": old_epoch, "new_epoch": new_epoch}


def recover(j: Journal, rank: int, comm: str = "world") -> Dict[str, Any]:
    """Build the recovery briefing a replacement agent reads on startup.

    This is the AgentMPI analogue of restoring a checkpoint, and it works the way
    durable-execution engines work rather than the way process checkpointing
    does: there is no memory image to restore, so instead we replay the *record
    of externally visible commitments* the predecessor made. Concretely the
    briefing answers five questions a replacement must know and cannot guess:

    1. What was I assigned? (scatter slices, role, task claims in windows)
    2. What did I already publish? (window cells I wrote, messages I sent)
    3. What did I already receive? (so I do not re-request it)
    4. What did I promise that is still outstanding? (unmatched sends, posted
       receives, open collectives, held locks)
    5. What did I record for myself? (the memo table -- explicit continuation
       state the predecessor chose to leave behind)
    """
    cid = comm_row(j, comm)["id"]
    row = rank_row(j, rank)
    crank = world_to_comm(j, cid, rank) if rank in comm_members(j, cid) else None

    sent = j.q(
        "SELECT seq,dst,tag,tokens,status,summary,sent_ns FROM msg WHERE comm=? AND src=?"
        " ORDER BY seq DESC LIMIT 40",
        (cid, crank),
    )
    recvd = j.q(
        "SELECT seq,src,tag,tokens,summary,delivered_ns FROM msg WHERE comm=? AND dst=?"
        " AND status='delivered' ORDER BY seq DESC LIMIT 40",
        (cid, crank),
    )
    inbox = j.q(
        "SELECT seq,src,tag,tokens,mode,obj,summary FROM msg WHERE comm=? AND dst=?"
        " AND status IN ('posted','matched') ORDER BY seq",
        (cid, crank),
    )
    wrote = j.q(
        "SELECT w.name AS win, h.key, MAX(h.version) AS version, SUM(h.tokens) AS tokens"
        " FROM win_hist h JOIN win w ON w.id=h.win WHERE w.job=? AND h.writer=?"
        " GROUP BY w.name, h.key ORDER BY w.name, h.key LIMIT 60",
        (j.job_id, rank),
    )
    open_colls = j.q(
        "SELECT k.id,k.op,k.reduce_op,k.algo,k.params,p.state,p.meta FROM coll k"
        " JOIN coll_part p ON p.coll=k.id WHERE k.comm=? AND p.crank=?"
        " AND p.state NOT IN ('done','absent','failed') LIMIT 20",
        (cid, crank),
    )
    steps = j.q(
        "SELECT id,coll,round,left_obj,right_obj FROM reduce_step WHERE crank=? AND state='pending'",
        (crank,),
    )
    memos = j.q("SELECT key,value,ns FROM memo WHERE job=? AND rank=? ORDER BY ns", (j.job_id, rank))
    assignments = j.q(
        "SELECT k.id, k.params, p.out_obj FROM coll k JOIN coll_part p ON p.coll=k.id"
        " WHERE k.comm=? AND k.op='scatter' AND p.crank=? AND p.out_obj IS NOT NULL",
        (cid, crank),
    )
    locks = j.q(
        "SELECT w.name AS win, l.key, l.mode FROM win_lock l JOIN win w ON w.id=l.win WHERE l.holder=?",
        (rank,),
    )
    failures = j.q(
        "SELECT kind,epoch,detected_ns,detail FROM failure WHERE job=? AND rank=? ORDER BY id",
        (j.job_id, rank),
    )
    return {
        "rank": rank,
        "epoch": int(row["epoch"]),
        "comm": comm,
        "comm_rank": crank,
        "role": row["role"],
        "predecessor_failures": [
            {"kind": str(f["kind"]), "epoch": int(f["epoch"]),
             "detail": json.loads(f["detail"] or "{}")}
            for f in failures
        ],
        "assignments": [
            {"coll": str(a["id"]), "handle": str(a["out_obj"]),
             "params": json.loads(a["params"] or "{}")}
            for a in assignments
        ],
        "published_window_cells": [
            {"win": str(w["win"]), "key": str(w["key"]), "version": int(w["version"]),
             "tokens": int(w["tokens"] or 0)}
            for w in wrote
        ],
        "messages_sent": [
            {"seq": int(s["seq"]), "to": int(s["dst"]), "tag": int(s["tag"]),
             "tokens": int(s["tokens"]), "status": str(s["status"]), "summary": s["summary"]}
            for s in sent
        ],
        "messages_received": [
            {"seq": int(r["seq"]), "from": int(r["src"]), "tag": int(r["tag"]),
             "tokens": int(r["tokens"]), "summary": r["summary"]}
            for r in recvd
        ],
        "unread_inbox": [
            {"seq": int(m["seq"]), "from": int(m["src"]), "tag": int(m["tag"]),
             "tokens": int(m["tokens"]), "mode": str(m["mode"]), "handle": str(m["obj"]),
             "summary": m["summary"]}
            for m in inbox
        ],
        "open_collectives": [
            {"coll": str(k["id"]), "op": str(k["op"]), "reduce_op": k["reduce_op"],
             "algo": str(k["algo"]), "state": str(k["state"]),
             "params": json.loads(k["params"] or "{}"),
             "progress": json.loads(k["meta"] or "{}")}
            for k in open_colls
        ],
        "pending_reduction_steps": [
            {"step": str(s["id"]), "coll": str(s["coll"]), "round": int(s["round"])} for s in steps
        ],
        "held_locks": [{"win": str(l["win"]), "key": str(l["key"]), "mode": str(l["mode"])} for l in locks],
        "memos": [{"key": str(m["key"]), "value": str(m["value"])} for m in memos],
    }


def memo_put(j: Journal, rank: int, epoch: int, key: str, value: str) -> Dict[str, Any]:
    with j.tx() as c:
        c.execute(
            "INSERT INTO memo(job,rank,key,value,epoch,ns) VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(job,rank,key) DO UPDATE SET value=excluded.value,epoch=excluded.epoch,ns=excluded.ns",
            (j.job_id, rank, key, value, epoch, now_ns()),
        )
    return {"key": key, "stored": True}


def memo_get(j: Journal, rank: int, key: Optional[str] = None) -> Dict[str, Any]:
    if key:
        row = j.q1("SELECT value FROM memo WHERE job=? AND rank=? AND key=?", (j.job_id, rank, key))
        return {"key": key, "found": row is not None, "value": row["value"] if row else None}
    rows = j.q("SELECT key,value FROM memo WHERE job=? AND rank=? ORDER BY ns", (j.job_id, rank))
    return {"memos": [{"key": str(r["key"]), "value": str(r["value"])} for r in rows]}
