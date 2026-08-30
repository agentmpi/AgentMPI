"""Point-to-point communication: the AgentMPI matching engine.

Everything else in AgentMPI -- every collective, every one-sided epoch, every
recovery primitive -- is built on the four operations in this file, exactly as
in MPI. The two properties that make that possible are:

1. **Deterministic matching.** A message is delivered to the *first posted
   receive that matches it*, where a receive matches on the triple
   ``(communicator, source, tag)`` with wildcards allowed on source and tag.
   Among messages that match the same receive, they are matched in send order.
   That is MPI's non-overtaking rule, and it is what lets a harness author
   reason about a multi-round schedule at all.

2. **Separation of matching from delivery.** In MPI, a matched message's bytes
   land in the receiver's buffer. In AgentMPI, matching is cheap but *delivery
   costs context*, so the two are distinct states in the journal. Small
   messages are delivered inline (eager); large ones are delivered as a handle
   with metadata, and the receiver decides whether to spend context on the body
   (rendezvous). This is the single most important adaptation in the protocol:
   it makes context exhaustion a flow-control problem with a threshold, rather
   than an emergent failure.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core import (
    ANY_SOURCE,
    ANY_TAG,
    Ctx,
    check_comm_usable,
    comm_members,
    comm_to_world,
    ctx_charge,
    detect_failures,
    failed_ranks,
    heartbeat,
    package,
    world_to_comm,
)
from .errors import (
    AmpiError,
    ArgError,
    ErrClass,
    ProcFailedError,
    ProcFailedPendingError,
    RequestError,
    RevokedError,
    TimeoutError_,
)
from .journal import Journal, now_ns

#: Polling cadence for blocking calls. AgentMPI deliberately polls rather than
#: blocking on a condition variable: agent-scale waits are seconds to minutes,
#: so a coarse poll is free, and polling keeps the runtime a library with no
#: background threads of its own to fail.
#:
#: The schedule is three-phase, for the same reason MPI implementations spin
#: before they block: a brief fast spin keeps the *runtime's* own latency out of
#: the measurement (and makes the multi-round tree collectives cheap), while the
#: long tail backs off so that a rank waiting ten minutes for a peer costs
#: nothing.
POLL_SPIN_S = 0.002
POLL_MID_S = 0.02
POLL_MAX_S = 2.0
POLL_SPIN_UNTIL_S = 0.1
POLL_MID_UNTIL_S = 2.0


def _poll_sleep(elapsed_s: float) -> None:
    if elapsed_s < POLL_SPIN_UNTIL_S:
        time.sleep(POLL_SPIN_S)
    elif elapsed_s < POLL_MID_UNTIL_S:
        time.sleep(POLL_MID_S)
    else:
        time.sleep(min(POLL_MAX_S, elapsed_s / 20.0))


class Progress:
    """The progress engine: what a rank must do while it is blocked.

    MPI has an analogous rule -- an implementation only guarantees progress while
    the application is inside the MPI library -- but the consequences differ. A
    blocked AgentMPI rank has three obligations, and getting any of them wrong
    produces a spectacular failure:

    1. **Renew its lease.** A rank waiting inside a barrier is not calling the
       runtime, so without an explicit renewal the failure detector will declare
       it dead for the crime of waiting. In an early version of this runtime that
       is exactly what happened: every rank that reached a barrier first was
       declared failed, and the job cascaded. Blocking is not evidence of death.
    2. **Run the failure detector.** Detection is lazy and local, so the only
       time it runs is when somebody is waiting -- which is precisely when
       somebody cares.
    3. **Notice revocation.** Revocation exists to unblock survivors, so a
       blocked rank that does not check for it defeats the whole mechanism.

    Both writes are throttled, because the poll loop spins every 2ms early on and
    a write transaction per poll would turn the journal into a bottleneck.
    """

    HB_EVERY_S = 5.0
    DETECT_EVERY_S = 1.0

    def __init__(self, ctx: Ctx, *, check_revoked: bool = True, check_fenced: bool = True) -> None:
        self.ctx = ctx
        self.check_revoked = check_revoked
        self.check_fenced = check_fenced
        self._last_hb = 0.0
        self._last_detect = 0.0

    def __call__(self) -> None:
        ctx = self.ctx
        j = ctx.j
        now = time.monotonic()
        need_hb = now - self._last_hb >= self.HB_EVERY_S
        need_detect = now - self._last_detect >= self.DETECT_EVERY_S
        if need_hb or need_detect:
            with j.tx() as c:
                if need_hb:
                    heartbeat(j, ctx.rank, ctx.epoch, conn=c)
                    self._last_hb = now
                if need_detect:
                    detect_failures(j, ctx.comm, by=ctx.rank, conn=c)
                    self._last_detect = now
        if self.check_fenced:
            ctx.check_live()
        if self.check_revoked:
            row = j.q1("SELECT revoked, revoked_by, name FROM comm WHERE id=?", (ctx.comm,))
            if row is not None and int(row["revoked"]):
                raise RevokedError(
                    f"communicator {row['name']!r} was revoked (by rank {row['revoked_by']}) "
                    "while you were blocked",
                    hint=(
                        f"this is the intended way to unblock you. Run "
                        f"`ampi comm shrink --comm {row['name']}` and continue on the new communicator."
                    ),
                    detail={"comm": str(row["name"]), "revoked_by": row["revoked_by"]},
                )

    def wait(self, started_s: float) -> None:
        """One poll iteration: make progress, then sleep proportionally."""
        self()
        _poll_sleep(time.time() - started_s)


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------


def send(
    ctx: Ctx,
    dest: int,
    tag: int,
    text: str,
    *,
    mode: str = "standard",
    kind: str = "p2p",
    schema: Optional[str] = None,
    label: Optional[str] = None,
    idem: Optional[str] = None,
    coll: Optional[str] = None,
    coll_round: Optional[int] = None,
    force_payload_mode: Optional[str] = None,
    timeout_ns: Optional[int] = None,
) -> Dict[str, Any]:
    """``AMPI_Send``: enqueue a message for ``dest`` on ``ctx.comm``.

    ``mode`` mirrors MPI's communication modes:

    * ``standard`` -- returns as soon as the message is durably enqueued. Local
      completion; the receiver need not have posted anything.
    * ``sync`` (``AMPI_Ssend``) -- returns only once the message has been
      *matched* by a receive. Non-local completion; used to get a handshake.
    * ``ready`` (``AMPI_Rsend``) -- errors unless a matching receive is already
      posted. Useful in tightly scheduled harnesses to catch schedule bugs
      early rather than deadlocking later.

    ``idem`` is an idempotency key. Agents retry shell commands; without a key,
    a retried send duplicates the message. With one, the second send is a no-op
    that returns the original result. This is not an optimisation, it is a
    correctness requirement for agent executors.
    """
    ctx.check_live()
    check_comm_usable(ctx.j, ctx.comm)
    j = ctx.j
    if dest == ANY_SOURCE:
        raise ArgError("cannot send to AMPI_ANY_SOURCE")
    src = ctx.crank
    ts = now_ns()

    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        if idem:
            prev = c.execute(
                "SELECT seq,status,tokens,mode,obj FROM msg WHERE job=? AND idem=?",
                (j.job_id, idem),
            ).fetchone()
            if prev is not None:
                return {
                    "seq": int(prev["seq"]),
                    "status": prev["status"],
                    "tokens": int(prev["tokens"]),
                    "mode": prev["mode"],
                    "handle": prev["obj"],
                    "duplicate": True,
                }
        pay = package(
            j,
            text,
            creator=ctx.rank,
            cfg=ctx.cfg,
            schema=schema,
            label=label,
            force_mode=force_payload_mode,
            conn=c,
        )
        if mode == "ready":
            if not _has_posted_recv(c, ctx, src, tag):
                raise AmpiError(
                    f"AMPI_Rsend to rank {dest} with tag {tag}: no matching receive is posted",
                    err_class=ErrClass.ARG,
                    hint="the receiver is not ready; use standard send or fix the schedule",
                )
        cur = c.execute(
            "INSERT INTO msg(job,comm,src,dst,tag,src_epoch,kind,mode,obj,inline,tokens,nbytes,"
            "digest,summary,schema,status,sent_ns,coll,coll_round,idem)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'posted',?,?,?,?)",
            (
                j.job_id,
                ctx.comm,
                src,
                dest,
                tag,
                ctx.epoch,
                kind,
                pay.mode,
                pay.obj,
                pay.inline,
                pay.tokens,
                pay.nbytes,
                pay.digest,
                pay.summary,
                pay.schema,
                ts,
                coll,
                coll_round,
                idem,
            ),
        )
        seq = int(cur.lastrowid)
        j.bump("msgs_sent", ctx.rank, 1, conn=c)
        j.bump("tokens_sent", ctx.rank, pay.tokens, conn=c)
        j.trace(
            "send",
            rank=ctx.rank,
            epoch=ctx.epoch,
            comm=ctx.comm,
            peer=comm_to_world(j, ctx.comm, dest),
            tag=tag,
            msg_seq=seq,
            tokens=pay.tokens,
            nbytes=pay.nbytes,
            status=pay.mode,
            coll=coll,
            detail={"mode": mode, "round": coll_round},
            conn=c,
        )
        # Opportunistically satisfy an already-posted receive so that a waiting
        # peer's next poll finds the message matched.
        _match_posted_receives(c, ctx.j, ctx.comm, dest)

    result = {
        "seq": seq,
        "status": "posted",
        "tokens": pay.tokens,
        "nbytes": pay.nbytes,
        "mode": pay.mode,
        "handle": pay.obj,
        "summary": pay.summary,
        "duplicate": False,
    }
    if mode == "sync":
        deadline = ts + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)
        result["status"] = _await_match(ctx, seq, deadline)
    return result


def _has_posted_recv(c: sqlite3.Connection, ctx: Ctx, src: int, tag: int) -> bool:
    row = c.execute(
        "SELECT id FROM recvq WHERE comm=? AND dst=? AND status='posted'"
        " AND (src=? OR src=?) AND (tag=? OR tag=?) LIMIT 1",
        (ctx.comm, src, src, ANY_SOURCE, tag, ANY_TAG),
    ).fetchone()
    return row is not None


def _await_match(ctx: Ctx, seq: int, deadline_ns: int) -> str:
    start = time.time()
    prog = Progress(ctx)
    while True:
        row = ctx.j.q1("SELECT status FROM msg WHERE seq=?", (seq,))
        st = str(row["status"]) if row else "dropped"
        if st in ("matched", "delivered", "dropped", "cancelled"):
            return st
        if now_ns() > deadline_ns:
            raise TimeoutError_(
                f"AMPI_Ssend: message {seq} was not matched before the deadline",
                hint="the receiver may be slow or dead; re-run the same command to keep waiting",
                detail={"seq": seq},
            )
        prog()
        _poll_sleep(time.time() - start)


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


def _tag_matches(want: int, have: int) -> bool:
    return want == ANY_TAG or want == have


def _src_matches(want: int, have: int) -> bool:
    return want == ANY_SOURCE or want == have


def _candidate_messages(
    c: sqlite3.Connection, comm: str, dst: int, src: int, tag: int, limit: int = 256
) -> List[sqlite3.Row]:
    sql = (
        "SELECT * FROM msg WHERE comm=? AND dst=? AND status='posted'"
        " AND (? = -1 OR src=?) AND (? = -1 OR tag=?) ORDER BY seq LIMIT ?"
    )
    return list(c.execute(sql, (comm, dst, src, src, tag, tag, limit)))


def _older_recv_matches(
    c: sqlite3.Connection, comm: str, dst: int, msg: sqlite3.Row, than_id: int
) -> bool:
    """True if a receive posted before ``than_id`` also matches ``msg``.

    This is what enforces receive-queue ordering: a message belongs to the
    oldest posted receive that matches it, not to whichever rank polls first.
    """
    rows = c.execute(
        "SELECT id,src,tag FROM recvq WHERE comm=? AND dst=? AND status='posted' AND id<? ORDER BY id",
        (comm, dst, than_id),
    ).fetchall()
    for r in rows:
        if _src_matches(int(r["src"]), int(msg["src"])) and _tag_matches(int(r["tag"]), int(msg["tag"])):
            return True
    return False


def _match_for_recv(c: sqlite3.Connection, j: Journal, recv: sqlite3.Row) -> Optional[sqlite3.Row]:
    comm = str(recv["comm"])
    dst = int(recv["dst"])
    for msg in _candidate_messages(c, comm, dst, int(recv["src"]), int(recv["tag"])):
        if _older_recv_matches(c, comm, dst, msg, int(recv["id"])):
            continue
        ts = now_ns()
        c.execute(
            "UPDATE msg SET status='matched', matched_ns=?, recv_id=? WHERE seq=? AND status='posted'",
            (ts, int(recv["id"]), int(msg["seq"])),
        )
        if c.total_changes == 0:  # pragma: no cover - lost race
            continue
        c.execute(
            "UPDATE recvq SET status='matched', msg_seq=? WHERE id=? AND status='posted'",
            (int(msg["seq"]), int(recv["id"])),
        )
        return c.execute("SELECT * FROM msg WHERE seq=?", (int(msg["seq"]),)).fetchone()
    return None


def _match_posted_receives(c: sqlite3.Connection, j: Journal, comm: str, dst: int) -> int:
    """Try to satisfy posted receives for ``dst``; returns number matched."""
    n = 0
    for recv in c.execute(
        "SELECT * FROM recvq WHERE comm=? AND dst=? AND status='posted' ORDER BY id",
        (comm, dst),
    ).fetchall():
        if _match_for_recv(c, j, recv) is not None:
            n += 1
    return n


def _post_recv(
    c: sqlite3.Connection,
    ctx: Ctx,
    src: int,
    tag: int,
    *,
    deadline_ns: Optional[int],
    req: Optional[str] = None,
) -> int:
    cur = c.execute(
        "INSERT INTO recvq(job,comm,dst,src,tag,dst_epoch,posted_ns,deadline_ns,status,req)"
        " VALUES(?,?,?,?,?,?,?,?,'posted',?)",
        (
            ctx.j.job_id,
            ctx.comm,
            ctx.crank,
            src,
            tag,
            ctx.epoch,
            now_ns(),
            deadline_ns,
            req,
        ),
    )
    return int(cur.lastrowid)


# --------------------------------------------------------------------------
# Delivery
# --------------------------------------------------------------------------


def _deliver(
    ctx: Ctx,
    msg: sqlite3.Row,
    *,
    materialize: Optional[bool] = None,
    budget: Optional[int] = None,
) -> Dict[str, Any]:
    """Move a matched message into the receiver's context, charging tokens.

    Returns an envelope dict. ``body`` is present iff the payload was actually
    delivered into context; otherwise the caller gets ``handle``, ``summary``,
    ``schema`` and ``tokens`` and must decide.
    """
    j = ctx.j
    seq = int(msg["seq"])
    mode = str(msg["mode"])
    ntok = int(msg["tokens"])
    want_body = mode == "eager" if materialize is None else materialize
    envelope_tokens = 0
    body: Optional[str] = None
    delivered_tokens = 0
    note: Optional[str] = None

    if want_body:
        try:
            with j.tx() as c:
                ctx_charge(j, ctx.rank, ctx.epoch, ntok, conn=c, what=f"message {seq}")
            body = msg["inline"] if msg["inline"] is not None else j.object_text(str(msg["obj"]))
            delivered_tokens = ntok
        except AmpiError as exc:
            if exc.err_class != ErrClass.CTX_EXCEEDED:
                raise
            # Graceful degradation: hand back a bounded projection instead of
            # failing the receive. The message is still delivered; only the
            # body is withheld.
            from .views import render_view

            avail = max(120, min(budget or 400, ctx.cfg.eager_tokens))
            body = render_view(j, str(msg["obj"]), spec={"op": "head", "budget": avail})["body"]
            with j.tx() as c:
                ctx_charge(j, ctx.rank, ctx.epoch, avail, conn=c, force=True, what="degraded view")
            delivered_tokens = avail
            note = (
                "context budget exhausted: delivered a truncated view instead of the body. "
                f"Full payload remains at handle {msg['obj']} ({ntok} tokens)."
            )
    else:
        envelope_tokens = min(80, max(20, len(str(msg["summary"] or "")) // 4))
        with j.tx() as c:
            ctx_charge(j, ctx.rank, ctx.epoch, envelope_tokens, conn=c, force=True, what="envelope")
        delivered_tokens = envelope_tokens

    ts = now_ns()
    with j.tx() as c:
        c.execute(
            "UPDATE msg SET status='delivered', delivered_ns=? WHERE seq=?",
            (ts, seq),
        )
        j.bump("msgs_recvd", ctx.rank, 1, conn=c)
        j.bump("tokens_recvd", ctx.rank, delivered_tokens, conn=c)
        j.trace(
            "recv",
            rank=ctx.rank,
            epoch=ctx.epoch,
            comm=str(msg["comm"]),
            peer=comm_to_world(j, str(msg["comm"]), int(msg["src"])),
            tag=int(msg["tag"]),
            msg_seq=seq,
            tokens=delivered_tokens,
            nbytes=int(msg["nbytes"]),
            status=mode,
            dur_ns=ts - int(msg["sent_ns"]),
            coll=msg["coll"],
            detail={"materialized": body is not None, "payload_tokens": ntok},
            conn=c,
        )

    env: Dict[str, Any] = {
        "seq": seq,
        "source": int(msg["src"]),
        "source_world": comm_to_world(j, str(msg["comm"]), int(msg["src"])),
        "tag": int(msg["tag"]),
        "comm": str(msg["comm"]),
        "mode": mode,
        "tokens": ntok,
        "nbytes": int(msg["nbytes"]),
        "handle": str(msg["obj"]),
        "digest": msg["digest"],
        "summary": msg["summary"],
        "schema": msg["schema"],
        "latency_ns": ts - int(msg["sent_ns"]),
        "context_charged": delivered_tokens,
    }
    if body is not None:
        env["body"] = body
    if note:
        env["note"] = note
    return env


# --------------------------------------------------------------------------
# Receiving
# --------------------------------------------------------------------------


def recv(
    ctx: Ctx,
    source: int = ANY_SOURCE,
    tag: int = ANY_TAG,
    *,
    timeout_ns: Optional[int] = None,
    materialize: Optional[bool] = None,
    budget: Optional[int] = None,
    on_wait: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """``AMPI_Recv``: block until a matching message is delivered.

    Deadline semantics are a deliberate departure from MPI. ``MPI_Recv`` blocks
    forever, which is tolerable when a peer's failure kills the whole job. An
    agent peer may be merely slow (a long think step), permanently wedged, or
    dead, and no timeout can distinguish them. AgentMPI therefore makes every
    blocking call deadline-bounded and idempotently retryable: on
    ``AMPI_ERR_TIMEOUT`` the posted receive *remains posted*, so re-issuing the
    identical command resumes the same wait rather than starting a new one.
    """
    ctx.check_live()
    j = ctx.j
    start_ns = now_ns()
    deadline = start_ns + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)
    start_s = time.time()
    prog = Progress(ctx, check_revoked=False, check_fenced=False)

    with j.tx() as c:
        heartbeat(j, ctx.rank, ctx.epoch, conn=c)
        # Reuse a receive left over by a previous timed-out attempt, so that
        # retrying neither loses queue position nor abandons a message that was
        # matched to the earlier attempt after it gave up. This is what makes
        # `AMPI_ERR_TIMEOUT` genuinely retryable rather than merely harmless.
        existing = c.execute(
            "SELECT * FROM recvq WHERE comm=? AND dst=? AND src=? AND tag=?"
            " AND status IN ('posted','matched') AND req IS NULL ORDER BY id LIMIT 1",
            (ctx.comm, ctx.crank, source, tag),
        ).fetchone()
        recv_id = int(existing["id"]) if existing else _post_recv(
            c, ctx, source, tag, deadline_ns=deadline
        )
        c.execute("UPDATE recvq SET deadline_ns=? WHERE id=?", (deadline, recv_id))
        j.trace(
            "recv_post",
            rank=ctx.rank,
            epoch=ctx.epoch,
            comm=ctx.comm,
            peer=(comm_to_world(j, ctx.comm, source) if source >= 0 else None),
            tag=tag,
            conn=c,
        )

    while True:
        with j.tx() as c:
            row = c.execute("SELECT * FROM recvq WHERE id=?", (recv_id,)).fetchone()
            if row is not None and row["status"] == "posted":
                _match_for_recv(c, j, row)
                row = c.execute("SELECT * FROM recvq WHERE id=?", (recv_id,)).fetchone()
        if row is not None and row["status"] == "matched" and row["msg_seq"] is not None:
            msg = j.q1("SELECT * FROM msg WHERE seq=?", (int(row["msg_seq"]),))
            with j.tx() as c:
                c.execute("UPDATE recvq SET status='done' WHERE id=?", (recv_id,))
            assert msg is not None
            env = _deliver(ctx, msg, materialize=materialize, budget=budget)
            env["wait_ns"] = now_ns() - start_ns
            return env
        if row is not None and row["status"] == "cancelled":
            raise RequestError("this receive was cancelled")

        # No match yet: renew our lease, then check the reasons we might never
        # get one. Renewing first matters: a rank blocked in a long receive must
        # not be declared dead for waiting.
        prog()
        _check_recv_failure_modes(ctx, source, recv_id)
        if now_ns() > deadline:
            waited = (now_ns() - start_ns) / 1e9
            raise TimeoutError_(
                f"AMPI_Recv(src={_src_str(source)}, tag={_tag_str(tag)}) timed out after {waited:.1f}s",
                hint=(
                    "the receive is still posted, so re-running the identical command resumes "
                    "the same wait. Check `ampi status` to see whether the sender is alive."
                ),
                detail={"recv_id": recv_id, "waited_s": round(waited, 2)},
            )
        if on_wait:
            on_wait(time.time() - start_s)
        _poll_sleep(time.time() - start_s)


def _src_str(source: int) -> str:
    return "ANY" if source == ANY_SOURCE else str(source)


def _tag_str(tag: int) -> str:
    return "ANY" if tag == ANY_TAG else str(tag)


def _check_recv_failure_modes(ctx: Ctx, source: int, recv_id: int) -> None:
    j = ctx.j
    ctx.check_live()
    row = j.q1("SELECT revoked FROM comm WHERE id=?", (ctx.comm,))
    if row is not None and int(row["revoked"]):
        with j.tx() as c:
            c.execute("UPDATE recvq SET status='cancelled' WHERE id=?", (recv_id,))
        raise RevokedError(
            "the communicator was revoked while this receive was pending",
            hint="call `ampi comm shrink` to rebuild over the survivors, then retry",
        )
    dead = set(failed_ranks(j, ctx.comm))
    if not dead:
        return
    if source != ANY_SOURCE:
        src_world = comm_to_world(j, ctx.comm, source)
        if src_world in dead:
            with j.tx() as c:
                c.execute("UPDATE recvq SET status='cancelled' WHERE id=?", (recv_id,))
            raise ProcFailedError(
                f"rank {source} (world {src_world}) has failed; this receive can never complete",
                hint=(
                    "this is a real failure, not a delay. Either take over its work, or "
                    "revoke and shrink the communicator: `ampi comm revoke && ampi comm shrink`"
                ),
                detail={"failed": [src_world]},
            )
    else:
        # A wildcard receive might still be satisfied by a live peer, so this is
        # advisory, exactly like MPIX_ERR_PROC_FAILED_PENDING.
        raise ProcFailedPendingError(
            f"ranks {sorted(dead)} have failed; a wildcard receive may still complete from a live peer",
            hint="retry the same command to keep waiting, or narrow the source",
            detail={"failed": sorted(dead)},
        )


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------


def probe(
    ctx: Ctx,
    source: int = ANY_SOURCE,
    tag: int = ANY_TAG,
    *,
    blocking: bool = False,
    timeout_ns: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """``AMPI_Iprobe`` / ``AMPI_Probe``: inspect without receiving.

    Probing is disproportionately useful for agents. In MPI it is mostly used to
    size a buffer; here it lets an agent see *what is waiting and what it would
    cost* before committing context to it, which is the basis of every
    context-aware scheduling decision a harness can make.
    """
    ctx.check_live()
    j = ctx.j
    start_s = time.time()
    prog = Progress(ctx, check_revoked=False, check_fenced=False)
    deadline = now_ns() + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)
    while True:
        with j.tx(immediate=False) as c:
            rows = _candidate_messages(c, ctx.comm, ctx.crank, source, tag, limit=1)
        if rows:
            m = rows[0]
            return {
                "seq": int(m["seq"]),
                "source": int(m["src"]),
                "tag": int(m["tag"]),
                "tokens": int(m["tokens"]),
                "nbytes": int(m["nbytes"]),
                "mode": str(m["mode"]),
                "handle": str(m["obj"]),
                "summary": m["summary"],
                "schema": m["schema"],
                "sent_ns": int(m["sent_ns"]),
            }
        if not blocking:
            return None
        if now_ns() > deadline:
            raise TimeoutError_("AMPI_Probe timed out", hint="retry to keep waiting")
        prog()
        _check_recv_failure_modes(ctx, source, -1)
        _poll_sleep(time.time() - start_s)


def pending(ctx: Ctx, *, limit: int = 50) -> List[Dict[str, Any]]:
    """All messages waiting for this rank -- the unexpected-message queue."""
    rows = ctx.j.q(
        "SELECT seq,src,tag,tokens,mode,obj,summary,schema,sent_ns FROM msg"
        " WHERE comm=? AND dst=? AND status IN ('posted','matched') ORDER BY seq LIMIT ?",
        (ctx.comm, ctx.crank, limit),
    )
    return [
        {
            "seq": int(r["seq"]),
            "source": int(r["src"]),
            "tag": int(r["tag"]),
            "tokens": int(r["tokens"]),
            "mode": str(r["mode"]),
            "handle": str(r["obj"]),
            "summary": r["summary"],
            "schema": r["schema"],
            "age_s": round((now_ns() - int(r["sent_ns"])) / 1e9, 1),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------
# Nonblocking requests
# --------------------------------------------------------------------------


def new_request_id() -> str:
    return "r:" + uuid.uuid4().hex[:12]


def isend(
    ctx: Ctx,
    dest: int,
    tag: int,
    text: str,
    *,
    schema: Optional[str] = None,
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    """``AMPI_Isend``. Because sends are durable enqueues, an isend completes
    immediately; the request exists so harnesses can uniformly wait on mixed
    request sets, and so ``AMPI_Ssend``-like completion can be awaited later."""
    res = send(ctx, dest, tag, text, schema=schema, idem=idem)
    rid = new_request_id()
    with ctx.j.tx() as c:
        c.execute(
            "INSERT INTO request(id,job,rank,epoch,op,comm,peer,tag,state,msg_seq,created_ns,params)"
            " VALUES(?,?,?,?,'isend',?,?,?,'active',?,?,?)",
            (
                rid,
                ctx.j.job_id,
                ctx.rank,
                ctx.epoch,
                ctx.comm,
                dest,
                tag,
                res["seq"],
                now_ns(),
                json.dumps({"tokens": res["tokens"]}),
            ),
        )
    res["request"] = rid
    return res


def irecv(
    ctx: Ctx,
    source: int = ANY_SOURCE,
    tag: int = ANY_TAG,
    *,
    materialize: Optional[bool] = None,
) -> Dict[str, Any]:
    """``AMPI_Irecv``: post a receive and return immediately.

    This is how an agent overlaps "communication" with "computation": post the
    receives you will eventually need, go think, then ``ampi wait``. Because the
    posted receive is durable, the overlap survives the agent being restarted.
    """
    ctx.check_live()
    rid = new_request_id()
    with ctx.j.tx() as c:
        heartbeat(ctx.j, ctx.rank, ctx.epoch, conn=c)
        recv_id = _post_recv(c, ctx, source, tag, deadline_ns=None, req=rid)
        c.execute(
            "INSERT INTO request(id,job,rank,epoch,op,comm,peer,tag,state,recv_id,created_ns,params)"
            " VALUES(?,?,?,?,'irecv',?,?,?,'active',?,?,?)",
            (
                rid,
                ctx.j.job_id,
                ctx.rank,
                ctx.epoch,
                ctx.comm,
                source,
                tag,
                recv_id,
                now_ns(),
                json.dumps({"materialize": materialize}),
            ),
        )
        row = c.execute("SELECT * FROM recvq WHERE id=?", (recv_id,)).fetchone()
        _match_for_recv(c, ctx.j, row)
    return {"request": rid, "recv_id": recv_id, "source": source, "tag": tag}


def _request_row(ctx: Ctx, rid: str) -> sqlite3.Row:
    row = ctx.j.q1("SELECT * FROM request WHERE id=?", (rid,))
    if row is None:
        raise RequestError(f"unknown request {rid!r}", hint="list yours with `ampi req list`")
    return row


def test(ctx: Ctx, rid: str, *, materialize: Optional[bool] = None) -> Dict[str, Any]:
    """``AMPI_Test``: has this request completed?"""
    row = _request_row(ctx, rid)
    op = str(row["op"])
    if row["state"] == "complete":
        return {"request": rid, "complete": True, "cached": True, "result": json.loads(row["result"] or "{}")}
    if op == "isend":
        st = str(ctx.j.scalar("SELECT status FROM msg WHERE seq=?", (int(row["msg_seq"]),), "dropped"))
        done = st in ("matched", "delivered")
        if done:
            _complete_request(ctx, rid, {"status": st})
        return {"request": rid, "complete": done, "status": st}
    if op == "irecv":
        with ctx.j.tx() as c:
            r = c.execute("SELECT * FROM recvq WHERE id=?", (int(row["recv_id"]),)).fetchone()
            if r is not None and r["status"] == "posted":
                _match_for_recv(c, ctx.j, r)
                r = c.execute("SELECT * FROM recvq WHERE id=?", (int(row["recv_id"]),)).fetchone()
        if r is not None and r["status"] == "matched" and r["msg_seq"] is not None:
            msg = ctx.j.q1("SELECT * FROM msg WHERE seq=?", (int(r["msg_seq"]),))
            assert msg is not None
            params = json.loads(row["params"] or "{}")
            mat = materialize if materialize is not None else params.get("materialize")
            env = _deliver(ctx, msg, materialize=mat)
            with ctx.j.tx() as c:
                c.execute("UPDATE recvq SET status='done' WHERE id=?", (int(row["recv_id"]),))
            _complete_request(ctx, rid, env)
            return {"request": rid, "complete": True, "envelope": env}
        return {"request": rid, "complete": False}
    if op.startswith("i"):
        from . import collectives

        return collectives.test_collective(ctx, rid)
    raise RequestError(f"cannot test request of op {op!r}")


def _complete_request(ctx: Ctx, rid: str, result: Dict[str, Any]) -> None:
    with ctx.j.tx() as c:
        c.execute(
            "UPDATE request SET state='complete', completed_ns=?, result=? WHERE id=?",
            (now_ns(), json.dumps(result, ensure_ascii=False), rid),
        )


def wait(
    ctx: Ctx,
    rids: List[str],
    *,
    mode: str = "all",
    timeout_ns: Optional[int] = None,
    materialize: Optional[bool] = None,
) -> Dict[str, Any]:
    """``AMPI_Wait`` / ``Waitall`` / ``Waitany`` / ``Waitsome``."""
    start_s = time.time()
    prog = Progress(ctx, check_revoked=False, check_fenced=False)
    deadline = now_ns() + (timeout_ns if timeout_ns is not None else ctx.cfg.timeout_ns)
    done: Dict[str, Any] = {}
    while True:
        for rid in rids:
            if rid in done:
                continue
            try:
                res = test(ctx, rid, materialize=materialize)
            except AmpiError as exc:
                if exc.err_class == ErrClass.PROC_FAILED_PENDING:
                    continue
                raise
            if res.get("complete"):
                done[rid] = res
        if mode == "any" and done:
            return {"completed": done, "pending": [r for r in rids if r not in done]}
        if mode == "some" and done:
            return {"completed": done, "pending": [r for r in rids if r not in done]}
        if mode == "all" and len(done) == len(rids):
            return {"completed": done, "pending": []}
        if now_ns() > deadline:
            raise TimeoutError_(
                f"AMPI_Wait({mode}) timed out with {len(done)}/{len(rids)} complete",
                hint="requests remain valid; re-run to keep waiting",
                detail={"completed": list(done), "pending": [r for r in rids if r not in done]},
            )
        prog()
        _poll_sleep(time.time() - start_s)


def cancel(ctx: Ctx, rid: str) -> Dict[str, Any]:
    row = _request_row(ctx, rid)
    with ctx.j.tx() as c:
        if row["recv_id"] is not None:
            c.execute(
                "UPDATE recvq SET status='cancelled' WHERE id=? AND status='posted'",
                (int(row["recv_id"]),),
            )
        c.execute("UPDATE request SET state='cancelled', completed_ns=? WHERE id=?", (now_ns(), rid))
    return {"request": rid, "state": "cancelled"}


def sendrecv(
    ctx: Ctx,
    *,
    dest: int,
    sendtag: int,
    text: str,
    source: int,
    recvtag: int,
    timeout_ns: Optional[int] = None,
    materialize: Optional[bool] = None,
    idem: Optional[str] = None,
) -> Dict[str, Any]:
    """``AMPI_Sendrecv``: the primitive every recursive-doubling collective needs.

    Doing this as one call rather than send-then-recv is not merely convenient:
    it removes the deadlock that a naive pairwise exchange creates, which is the
    same reason MPI provides it.
    """
    s = send(ctx, dest, sendtag, text, idem=idem)
    r = recv(ctx, source, recvtag, timeout_ns=timeout_ns, materialize=materialize)
    return {"sent": s, "recvd": r}
