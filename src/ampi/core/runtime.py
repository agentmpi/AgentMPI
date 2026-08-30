"""The AgentMPI runtime: lifecycle, point-to-point, RMA, and failure mitigation.

This is the layer that an MPI reader will find most familiar, and the places
where it is *not* familiar are the interesting ones.  Three decisions differ
from MPI, each forced by a property of LLM agents that HPC processes do not
have:

1. **Buffering is specified, not left to the implementation.**  MPI
   deliberately leaves it unspecified whether a standard-mode send buffers, so
   that portable programs cannot rely on it; the consequence is the classic
   deadlock where two ranks each send before receiving and the program works on
   one machine and hangs on another.  AgentMPI specifies unbounded, durable
   buffering.  The store has to be durable anyway for crash recovery, so
   buffering is free, and the eager-send deadlock is a hazard that an LLM agent
   has no way to diagnose.  ``ssend`` remains available when the application
   genuinely wants synchronisation.

2. **Every blocking wait is registered.**  A rank about to block writes a
   request record first.  MPI cannot afford this, and does not need it: a
   hanging MPI job is debugged offline with a stack trace.  An agent job is
   debugged by the runtime itself, so the wait-for graph must be materialised.

3. **Liveness is declared, not inferred.**  A rank that is about to spend three
   minutes inside one model turn extends its own deadline.  A fixed heartbeat
   timeout cannot separate "thinking" from "dead" when turn latency is
   heavy-tailed, and getting that wrong means killing a healthy agent.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from typing import Any

from .. import util
from ..constants import (
    AMPI_ANY_SOURCE,
    AMPI_ANY_TAG,
    AMPI_COMM_WORLD,
    DEFAULT_CTX_LIMIT,
    DEFAULT_FAILURE_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    LIVE_RANK_STATES,
    LOCK_EXCLUSIVE,
    LOCK_SHARED,
    MODE_EAGER,
    MODE_RENDEZVOUS,
    PROJ_DIGEST,
    PROJ_FULL,
    RANK_ALIVE,
    RANK_FAILED,
    RANK_FINALIZED,
    RANK_INIT,
    SPEC_VERSION,
    WIN_UNIFIED,
)
from ..errors import (
    AmpiArgError,
    AmpiStaleIncarnation,
    AmpiCommError,
    AmpiDeadlock,
    AmpiProcFailed,
    AmpiRevoked,
    AmpiTimeout,
)
from .collectives import allreduce_structural, barrier
from .comm import CommRegistry, Communicator
from .context import ContextAccount, choose_mode, project
from .ops import get_op
from .trace import Tracer


class Runtime:
    """One rank's view of an AgentMPI job."""

    def __init__(
        self,
        device: Any,
        job_id: str,
        rank: int | None = None,
        *,
        failure_timeout: float = DEFAULT_FAILURE_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self.device = device
        self.job_id = job_id
        self.rank = rank
        self.failure_timeout = failure_timeout
        self.max_failure_timeout = failure_timeout * 16
        self.poll_interval = poll_interval
        # A rank is not "stuck" until it has been waiting longer than any
        # plausible schedule skew between peers.
        self.deadlock_grace = 8.0
        self.health_check_period = 2.0
        self._blocked_since = 0.0
        self._last_health_check = 0.0
        # The incarnation this handle is operating under, captured at Init.
        # None means we have not claimed the rank in this process.
        self.incarnation: int | None = None
        self.comms = CommRegistry(device, job_id)
        self.ctx = ContextAccount(device, job_id)
        self.tracer = Tracer(device, job_id, rank)

    # =====================================================================
    # Job and rank lifecycle
    # =====================================================================

    @classmethod
    def create_job(
        cls,
        device: Any,
        job_id: str,
        world_size: int,
        *,
        ctx_limit: int = DEFAULT_CTX_LIMIT,
        meta: dict[str, Any] | None = None,
    ) -> Runtime:
        with device.write_tx():
            existing = device.query_one("SELECT * FROM job WHERE job_id=?", (job_id,))
            if existing is None:
                device.execute(
                    "INSERT INTO job (job_id, world_size, spec_version, created_at, meta) "
                    "VALUES (?,?,?,?,?)",
                    (job_id, world_size, SPEC_VERSION, util.now(), util.dumps(meta or {})),
                )
                for r in range(world_size):
                    device.execute(
                        "INSERT INTO rank (job_id, rank, generation, state, ctx_limit) "
                        "VALUES (?,?,?,?,?)",
                        (job_id, r, 0, RANK_INIT, ctx_limit),
                    )
            rt = cls(device, job_id, None)
            rt.comms.create(AMPI_COMM_WORLD, list(range(world_size)))
            rt.tracer.emit("AMPI_Job_create", "exit", world_size=world_size)
        return cls(device, job_id, None)

    def job(self) -> dict[str, Any]:
        row = self.device.query_one("SELECT * FROM job WHERE job_id=?", (self.job_id,))
        if row is None:
            raise AmpiArgError(f"no such job {self.job_id!r}")
        return row

    def init(
        self,
        rank: int,
        *,
        role: str | None = None,
        ctx_limit: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """AMPI_Init.  Idempotent, so a restarted agent process can re-enter."""
        self.rank = rank
        self.tracer.rank = rank
        with self.device.write_tx():
            row = self.device.query_one(
                "SELECT * FROM rank WHERE job_id=? AND rank=?", (self.job_id, rank)
            )
            if row is None:
                raise AmpiArgError(f"rank {rank} is not part of job {self.job_id}")
            fields: list[str] = ["state=?", "last_heartbeat=?", "incarnation=incarnation+1"]
            params: list[Any] = [RANK_ALIVE, util.now()]
            if row["started_at"] is None:
                fields.append("started_at=?")
                params.append(util.now())
            if role:
                fields.append("role=?")
                params.append(role)
            if ctx_limit:
                fields.append("ctx_limit=?")
                params.append(int(ctx_limit))
            if meta:
                fields.append("meta=?")
                params.append(util.dumps({**util.loads(row["meta"], {}), **meta}))
            params += [self.job_id, rank]
            self.device.execute(
                f"UPDATE rank SET {', '.join(fields)} WHERE job_id=? AND rank=?", params
            )
            self.comms.create(f"self.{rank}", [rank])
            fresh = self.rank_row(rank)
            self.incarnation = int(fresh["incarnation"])
            self.tracer.emit("AMPI_Init", "exit", role=role, generation=row["generation"],
                             incarnation=self.incarnation)
            return fresh

    def finalize(self, note: str | None = None) -> dict[str, Any]:
        """AMPI_Finalize: an orderly exit, distinguishable from a crash."""
        with self.device.write_tx():
            self.device.execute(
                "UPDATE rank SET state=?, finished_at=?, exit_note=? WHERE job_id=? AND rank=?",
                (RANK_FINALIZED, util.now(), note, self.job_id, self.rank),
            )
            self.tracer.emit("AMPI_Finalize", "exit", note=note)
        return self.rank_row(self.rank)

    def heartbeat(self, expect_idle: float | None = None) -> dict[str, Any]:
        """AMPI_Heartbeat, optionally declaring an expected quiet period."""
        now_ts = util.now()
        deadline = now_ts + float(expect_idle) if expect_idle else None
        with self.device.write_tx():
            self.device.execute(
                "UPDATE rank SET last_heartbeat=?, hb_deadline=? WHERE job_id=? AND rank=?",
                (now_ts, deadline, self.job_id, self.rank),
            )
        return self.rank_row(self.rank)

    def _touch(self) -> None:
        """Record liveness, and retract a false suspicion if one was recorded.

        An eventually-perfect failure detector is allowed to make mistakes and
        obliged to correct them.  Here the correction is cheap and exact: a
        rank that is executing a library call is, by direct evidence, alive, so
        if some peer condemned it on a timeout the condemnation was wrong and
        is withdrawn.  With LLM ranks this is not a corner case --- turn
        latency is heavy tailed enough that false suspicion is routine --- and
        the retraction rate is itself a number worth reporting.
        """
        row = self.device.query_one(
            "SELECT state, generation, failure_confirmed FROM rank WHERE job_id=? AND rank=?",
            (self.job_id, self.rank),
        )
        if row is not None and row["state"] == RANK_FAILED:
            if row["failure_confirmed"]:
                # A confirmed death is a decision, not a guess, and the rank
                # does not get to overrule it by speaking again. Only
                # AMPI_Respawn clears it. Without this an administratively
                # killed rank resurrects itself on its very next call, which
                # makes the kill unobservable and fault injection impossible.
                raise AmpiProcFailed(
                    f"rank {self.rank} has been terminated and may not continue; "
                    "a replacement must be started with AMPI_Respawn",
                    rank=self.rank,
                )
            self.device.execute(
                "UPDATE rank SET state=?, last_heartbeat=?, finished_at=NULL, "
                "suspicions=suspicions+1, retractions=retractions+1 "
                "WHERE job_id=? AND rank=?",
                (RANK_ALIVE, util.now(), self.job_id, self.rank),
            )
            self.device.execute(
                "INSERT INTO event (job_id, rank, ts, op, phase, meta) VALUES (?,?,?,?,?,?)",
                (self.job_id, self.rank, util.now(), "AMPI_Failure_retracted", "exit",
                 util.dumps({"reason": "the condemned rank made a library call"})),
            )
            return
        self.device.execute(
            "UPDATE rank SET last_heartbeat=?, hb_deadline=CASE WHEN hb_deadline < ? "
            "THEN NULL ELSE hb_deadline END WHERE job_id=? AND rank=?",
            (util.now(), util.now(), self.job_id, self.rank),
        )

    def actor(self) -> int:
        """This handle's rank, for attributing an action in the log.

        Written out rather than spelled ``self.rank or -1`` because rank 0 is
        falsy: that idiom silently attributed every window write, lock and
        revocation made by rank 0 to the sentinel -1, which is indistinguishable
        from an action taken with no rank identity at all. We found it while
        reading a trace that said a communicator had been revoked by nobody.
        """
        return -1 if self.rank is None else self.rank

    def rank_row(self, rank: int | None = None) -> dict[str, Any]:
        rank = self.rank if rank is None else rank
        row = self.device.query_one(
            "SELECT * FROM rank WHERE job_id=? AND rank=?", (self.job_id, rank)
        )
        if row is None:
            raise AmpiArgError(f"no such rank {rank}")
        return row

    def all_ranks(self) -> list[dict[str, Any]]:
        return self.device.query(
            "SELECT * FROM rank WHERE job_id=? ORDER BY rank", (self.job_id,)
        )

    # =====================================================================
    # Failure detection  (Chandra-Toueg eventually-perfect, with declared
    # deadlines so that a slow model turn is not mistaken for a crash)
    # =====================================================================

    def suspected(self) -> list[int]:
        """World ranks whose liveness lease has lapsed, under an adaptive timeout.

        A plain fixed-timeout detector is eventually perfect only in theory.
        In practice, against ranks whose turn latency is heavy tailed, it
        oscillates: it condemns a rank that is merely thinking, the rank makes
        a call and is reinstated, it thinks again, and it is condemned again.
        We measured 1091 such condemnations in a single twenty-minute
        eight-rank run before adding the rule below.

        The rule is the standard adaptive one, in the spirit of Chen-Toueg and
        phi-accrual detectors: each time a rank is wrongly condemned its
        timeout doubles, so the detector converges on that rank's actual turn
        latency instead of arguing with it.  The cap keeps a genuinely dead
        rank from becoming undetectable.
        """
        now_ts = util.now()
        out: list[int] = []
        for row in self.all_ranks():
            if row["state"] not in LIVE_RANK_STATES:
                continue
            if row["last_heartbeat"] is None:
                continue
            widened = self.failure_timeout * (2 ** min(6, int(row["suspicions"] or 0)))
            deadline = row["last_heartbeat"] + min(widened, self.max_failure_timeout)
            # A declared idle period may only *extend* the lease.  Honouring it
            # verbatim was a bug with a nasty shape: a rank that announced a
            # five-minute quiet period and then blocked for forty minutes was
            # condemned at minute five and stayed condemned however often it
            # called the library, because the declaration overrode its own
            # heartbeats.  Declaring a short idle period was strictly worse
            # than declaring none, which is the opposite of what the call is
            # for.
            if row["hb_deadline"]:
                deadline = max(deadline, row["hb_deadline"])
            if now_ts > deadline:
                out.append(int(row["rank"]))
        return out

    def failed_ranks(self) -> set[int]:
        rows = self.device.query(
            "SELECT rank FROM rank WHERE job_id=? AND state=?", (self.job_id, RANK_FAILED)
        )
        return {int(r["rank"]) for r in rows}

    def declare_failed(self, rank: int, reason: str, detected_by: int | None = None,
                       confirmed: bool = False) -> None:
        """Move a rank to FAILED and record the detection event.

        ``confirmed`` separates the two things a fixed-timeout detector
        conflates.  A timeout is a *suspicion*: the rank may simply be inside a
        long model turn, and suspicions here are wrong far more often than they
        are right.  An administrative kill, or a rank that finalized, is
        *confirmed*.  Only a confirmed death is allowed to fail a peer's
        operation; letting a suspicion do it is what turned one slow agent into
        a revoked communicator and a lost job in our first run.
        """
        with self.device.write_tx():
            row = self.device.query_one(
                "SELECT * FROM rank WHERE job_id=? AND rank=?", (self.job_id, rank)
            )
            if row is None:
                return
            if row["state"] == RANK_FAILED:
                # A suspicion may later be upgraded to a confirmation; the
                # reverse never happens, because evidence of death does not
                # decay. Only a retraction (the rank speaking) clears it.
                if confirmed and not row["failure_confirmed"]:
                    self.device.execute(
                        "UPDATE rank SET failure_confirmed=1 WHERE job_id=? AND rank=?",
                        (self.job_id, rank),
                    )
                    self.tracer.emit("AMPI_Failure_confirmed", "exit", peer=rank, reason=reason)
                return
            self.device.execute(
                "UPDATE rank SET state=?, finished_at=?, failure_confirmed=? "
                "WHERE job_id=? AND rank=?",
                (RANK_FAILED, util.now(), 1 if confirmed else 0, self.job_id, rank),
            )
            self.device.execute(
                "INSERT INTO failure (job_id, rank, generation, detected_at, detected_by, reason) "
                "VALUES (?,?,?,?,?,?)",
                (
                    self.job_id,
                    rank,
                    int(row["generation"]),
                    util.now(),
                    detected_by if detected_by is not None else self.rank,
                    reason,
                ),
            )
            self.tracer.emit("AMPI_Failure_detected", "exit", peer=rank, reason=reason,
                             confirmed=confirmed)

    # =====================================================================
    # Object store  (the substrate for rendezvous payloads and window cells)
    # =====================================================================

    def put_object(
        self,
        content: str,
        *,
        kind: str = "text",
        path: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handle = util.new_id("obj")
        tokens = util.count_tokens(content)
        self.device.execute(
            "INSERT INTO object (handle, job_id, kind, content, path, sha256, tokens, digest, "
            "created_by, created_at, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                handle,
                self.job_id,
                kind,
                content,
                path,
                util.sha256_text(content),
                tokens,
                util.structural_digest(content),
                self.rank,
                util.now(),
                util.dumps(meta or {}),
            ),
        )
        return {"handle": handle, "tokens": tokens, "sha256": util.sha256_text(content)}

    def get_object(self, handle: str) -> dict[str, Any]:
        row = self.device.query_one("SELECT * FROM object WHERE handle=?", (handle,))
        if row is None:
            raise AmpiArgError(f"no such object handle {handle!r}")
        return row

    # =====================================================================
    # Point-to-point
    # =====================================================================

    def _check_comm(self, comm: Communicator) -> None:
        if comm.revoked:
            raise AmpiRevoked(
                f"communicator {comm.name!r} has been revoked; rebuild it with AMPI_Comm_shrink",
                comm=comm.name,
            )

    def send(
        self,
        comm_name: str,
        dst: int,
        tag: int,
        payload: Any,
        *,
        projection: str = PROJ_FULL,
        digest_budget: int = 400,
        force_mode: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """AMPI_Send: buffered, durable, non-overtaking.

        The transfer mode is chosen from the *receiver's* remaining context, so
        an identical call may deliver inline to one peer and by reference to
        another.  The digest that accompanies a rendezvous message is
        structural and therefore free: the receiver learns what arrived and
        decides whether to pay for it.
        """
        comm = self.comms.get(comm_name)
        self._check_comm(comm)
        src_rank = comm.rank_of(self.rank)
        if src_rank < 0:
            raise AmpiCommError(f"rank {self.rank} is not a member of {comm_name!r}")
        if not 0 <= dst < comm.size:
            raise AmpiArgError(f"destination {dst} out of range for {comm_name!r} (size {comm.size})")

        text = payload if isinstance(payload, str) else util.dumps(payload)
        with self.tracer.span("AMPI_Send", comm_id=comm.comm_id, peer=dst, tag=tag) as span:
            with self.device.write_tx():
                self._touch()
                dst_world = comm.world_of(dst)
                receiver = self.ctx.get(dst_world)
                if receiver["state"] == RANK_FAILED and receiver["failure_confirmed"]:
                    raise AmpiProcFailed(
                        f"destination rank {dst} (world {dst_world}) is confirmed dead; "
                        "respawn it or shrink the communicator", peer=dst
                    )
                # A merely suspected peer is still sent to.  The message is
                # durable, so it survives until the peer returns or a
                # replacement replays the inbox; refusing to send would discard
                # work on the strength of a guess.

                projected = project(text, projection, digest_budget)
                obj = self.put_object(text) if projection != PROJ_DIGEST else None
                tokens = util.count_tokens(projected)
                mode = force_mode or choose_mode(tokens, receiver)

                if mode == MODE_EAGER:
                    body, handle, digest = projected, (obj or {}).get("handle"), None
                else:
                    if obj is None:
                        obj = self.put_object(text)
                    body = None
                    handle = obj["handle"]
                    digest = util.structural_digest(projected, budget_tokens=digest_budget)
                    tokens = util.count_tokens(digest)

                seq = self.device.counter_next(self.job_id, f"seq:{comm.comm_id}:{src_rank}:{dst}")
                cur = self.device.execute(
                    "INSERT INTO message (job_id, comm_id, src, dst, tag, seq, mode, body, "
                    "handle, digest, tokens, sent_at, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        self.job_id,
                        comm.comm_id,
                        src_rank,
                        dst,
                        tag,
                        seq,
                        mode,
                        body,
                        handle,
                        digest,
                        tokens,
                        util.now(),
                        util.dumps({**(meta or {}), "projection": projection}),
                    ),
                )
                self.ctx.credit_sent(self.rank, tokens)
                span["tokens"] = tokens
                span["meta"] = {"mode": mode, "projection": projection}
                return {
                    "msg_id": cur.lastrowid,
                    "mode": mode,
                    "tokens": tokens,
                    "handle": handle,
                    "seq": seq,
                }

    def ssend(self, *args: Any, timeout: float = 300.0, **kw: Any) -> dict[str, Any]:
        """AMPI_Ssend: return only once the message has been matched."""
        result = self.send(*args, **kw)
        deadline = time.time() + timeout
        while time.time() < deadline:
            row = self.device.query_one(
                "SELECT state FROM message WHERE msg_id=?", (result["msg_id"],)
            )
            if row and row["state"] != "posted":
                result["synchronised"] = True
                return result
            self._sleep()
        raise AmpiTimeout(
            f"ssend of message {result['msg_id']} was not matched within {timeout}s",
            msg_id=result["msg_id"],
        )

    def probe(
        self, comm_name: str, src: int = AMPI_ANY_SOURCE, tag: int = AMPI_ANY_TAG
    ) -> dict[str, Any] | None:
        """AMPI_Iprobe: inspect the envelope without consuming the message."""
        comm = self.comms.get(comm_name)
        me = comm.rank_of(self.rank)
        predicate: dict[str, Any] = {"comm_id": comm.comm_id, "dst": me, "state": "posted"}
        if src != AMPI_ANY_SOURCE:
            predicate["src"] = src
        if tag != AMPI_ANY_TAG:
            predicate["tag"] = tag
        rows = self.device.scan("message", predicate)
        if not rows:
            return None
        best = min(rows, key=lambda r: r["msg_id"])
        return {
            "msg_id": best["msg_id"],
            "src": best["src"],
            "tag": best["tag"],
            "tokens": best["tokens"],
            "mode": best["mode"],
            "digest": best["digest"],
            "pending": len(rows),
        }

    def recv(
        self,
        comm_name: str,
        src: int = AMPI_ANY_SOURCE,
        tag: int = AMPI_ANY_TAG,
        *,
        timeout: float = 600.0,
        blocking: bool = True,
        deref: bool = False,
        max_tokens: int | None = None,
        charge_context: bool = True,
    ) -> dict[str, Any] | None:
        """AMPI_Recv.

        ``deref`` controls whether a rendezvous payload is materialised.  The
        default is *not* to: the receiver gets the envelope and the free
        structural digest, and pulls the body only if it decides the body is
        worth its context.  Making that decision explicit at the receive site
        is the main lever AgentMPI gives a harness author against context
        exhaustion.
        """
        comm = self.comms.get(comm_name)
        self._check_comm(comm)
        me = comm.rank_of(self.rank)
        if me < 0:
            raise AmpiCommError(f"rank {self.rank} is not a member of {comm_name!r}")

        req_id = self._post_request(comm, me, src, tag)
        deadline = time.time() + timeout
        self._blocked_since = time.time()
        self._last_health_check = 0.0
        try:
            with self.tracer.span("AMPI_Recv", comm_id=comm.comm_id, peer=src, tag=tag) as span:
                while True:
                    hit = self._try_match(comm, me, src, tag, req_id, deref, max_tokens,
                                          charge_context)
                    if hit is not None:
                        span["tokens"] = hit.get("tokens", 0)
                        span["meta"] = {"mode": hit.get("mode"), "matched_src": hit.get("src")}
                        return hit
                    if not blocking:
                        return None
                    self._check_blocked_health(comm, src)
                    if time.time() >= deadline:
                        raise AmpiTimeout(
                            f"recv(src={src}, tag={tag}) on {comm_name!r} timed out after "
                            f"{timeout}s",
                            comm=comm_name,
                            src=src,
                            tag=tag,
                        )
                    self._sleep()
        finally:
            self._close_request(req_id)

    def _try_match(
        self,
        comm: Communicator,
        me: int,
        src: int,
        tag: int,
        req_id: int,
        deref: bool,
        max_tokens: int | None,
        charge_context: bool,
    ) -> dict[str, Any] | None:
        predicate: dict[str, Any] = {"comm_id": comm.comm_id, "dst": me, "state": "posted"}
        if src != AMPI_ANY_SOURCE:
            predicate["src"] = src
        if tag != AMPI_ANY_TAG:
            predicate["tag"] = tag
        with self.device.write_tx():
            self._touch()
            row = self.device.match(
                "message", predicate, claimant=f"req:{req_id}", order_by="msg_id"
            )
            if row is None:
                return None
            payload = row["body"]
            tokens = int(row["tokens"])
            materialised = False
            if row["mode"] == MODE_RENDEZVOUS and deref and row["handle"]:
                obj = self.get_object(row["handle"])
                body = obj["content"]
                if max_tokens is not None and obj["tokens"] > max_tokens:
                    body = util.clamp_text(body, max_tokens)
                payload = body
                tokens = util.count_tokens(body)
                materialised = True
            if charge_context:
                self.ctx.admit(self.rank, tokens, what=f"message {row['msg_id']}")
                self.ctx.charge(self.rank, tokens)
            self.device.execute(
                "UPDATE message SET state='delivered' WHERE msg_id=?", (row["msg_id"],)
            )
            self.device.execute(
                "UPDATE request SET state='complete', msg_id=?, completed_at=? WHERE req_id=?",
                (row["msg_id"], util.now(), req_id),
            )
        return {
            "msg_id": row["msg_id"],
            "src": row["src"],
            "tag": row["tag"],
            "mode": row["mode"],
            "handle": row["handle"],
            "digest": row["digest"],
            "payload": payload,
            "tokens": tokens,
            "materialised": materialised,
            "meta": util.loads(row["meta"], {}),
        }

    def deref(self, handle: str, *, max_tokens: int | None = None,
              charge_context: bool = True) -> dict[str, Any]:
        """AMPI_Deref: pay for a rendezvous body, now that you have decided to."""
        obj = self.get_object(handle)
        body = obj["content"]
        if max_tokens is not None and obj["tokens"] > max_tokens:
            body = util.clamp_text(body, max_tokens)
        tokens = util.count_tokens(body)
        with self.device.write_tx():
            if charge_context:
                self.ctx.admit(self.rank, tokens, what=f"object {handle}")
                self.ctx.charge(self.rank, tokens)
            self.tracer.emit("AMPI_Deref", "exit", tokens=tokens, handle=handle)
        return {"handle": handle, "payload": body, "tokens": tokens, "sha256": obj["sha256"]}

    def sendrecv(
        self,
        comm_name: str,
        dst: int,
        send_tag: int,
        payload: Any,
        src: int,
        recv_tag: int,
        *,
        timeout: float = 600.0,
        **kw: Any,
    ) -> dict[str, Any]:
        """AMPI_Sendrecv: the deadlock-free paired exchange used by halo swaps."""
        sent = self.send(comm_name, dst, send_tag, payload, **kw)
        got = self.recv(comm_name, src, recv_tag, timeout=timeout)
        return {"sent": sent, "received": got}

    # -- request bookkeeping ------------------------------------------------
    def _post_request(self, comm: Communicator, me: int, src: int, tag: int,
                      kind: str = "recv", meta: dict[str, Any] | None = None) -> int:
        with self.device.write_tx():
            cur = self.device.execute(
                "INSERT INTO request (job_id, comm_id, owner, kind, src, tag, posted_at, meta) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (self.job_id, comm.comm_id, me, kind, src, tag, util.now(),
                 util.dumps(meta or {})),
            )
            return int(cur.lastrowid)

    def _close_request(self, req_id: int) -> None:
        with self.device.write_tx():
            self.device.execute(
                "UPDATE request SET state=CASE WHEN state='posted' THEN 'cancelled' ELSE state END,"
                " completed_at=COALESCE(completed_at, ?) WHERE req_id=?",
                (util.now(), req_id),
            )

    def _sleep(self) -> None:
        self._check_incarnation()
        time.sleep(self.poll_interval)

    def _check_incarnation(self) -> None:
        """Abort if another process has claimed this rank since we started.

        Checked on every blocking poll rather than once at entry, because the
        takeover we care about happens *while* a call is blocked: an abandoned
        attempt's receive sits in the store and matches the next attempt's
        messages, silently mixing two generations of ranks into one result.
        """
        if self.incarnation is None or self.rank is None:
            return
        row = self.device.query_one(
            "SELECT incarnation FROM rank WHERE job_id=? AND rank=?", (self.job_id, self.rank))
        if row is not None and int(row["incarnation"]) != self.incarnation:
            raise AmpiStaleIncarnation(
                f"rank {self.rank} has been re-initialised by another process "
                f"(incarnation {row['incarnation']}, this call started under "
                f"{self.incarnation}); abandoning so the newer process owns the rank",
                rank=self.rank, started_under=self.incarnation,
                current=int(row["incarnation"]),
            )

    def _check_blocked_health(self, comm: Communicator, src: int) -> None:
        """While blocked: promote suspicions to failures and look for cycles.

        Throttled deliberately.  Building the wait-for graph is a global query,
        and --- more importantly --- a rank that has been blocked for a few
        hundred milliseconds is not stuck, it is simply earlier than its peer.
        Checking too eagerly turns ordinary schedule skew into a false deadlock
        report, which is worse than reporting nothing.
        """
        now_ts = time.time()
        if now_ts - self._blocked_since < self.deadlock_grace:
            return
        if now_ts - self._last_health_check < self.health_check_period:
            return
        self._last_health_check = now_ts
        for suspect in self.suspected():
            self.declare_failed(suspect, "heartbeat lease expired")
        if self.comms.get(comm.name).revoked:
            raise AmpiRevoked(f"communicator {comm.name!r} was revoked while blocked",
                              comm=comm.name)
        if src != AMPI_ANY_SOURCE:
            src_world = comm.world_of(src)
            src_row = self.rank_row(src_world)
            if src_row["state"] == RANK_FAILED and src_row["failure_confirmed"]:
                # A dead sender only matters if it died still owing us data.
                # If the message is already in the store the transfer can
                # complete, and failing here would discard work that survived
                # the sender -- the whole point of a durable message log.
                pending = self.device.scan("message", {
                    "comm_id": comm.comm_id, "dst": comm.rank_of(self.rank),
                    "src": src, "state": "posted",
                })
                if not pending:
                    raise AmpiProcFailed(
                        f"rank {src} (world {src_world}) failed while we waited for it "
                        f"on {comm.name!r}",
                        peer=src,
                    )
        cycle = self.detect_deadlock()
        if cycle:
            raise AmpiDeadlock(
                "wait-for cycle detected among ranks " + " -> ".join(str(r) for r in cycle),
                cycle=cycle,
            )

    # =====================================================================
    # Deadlock detection
    # =====================================================================

    def detect_deadlock(self) -> list[int] | None:
        """Materialise the wait-for graph and look for a cycle.

        MPI cannot do this online: no component of an MPI job knows the global
        set of outstanding receives.  AgentMPI's device *does*, because every
        blocking wait is registered before it blocks.  A cycle here is a real
        deadlock only if no posted message can satisfy any edge on it, so we
        check matchability before reporting.
        """
        waiting = self.device.query(
            "SELECT * FROM request WHERE job_id=? AND state='posted'", (self.job_id,)
        )
        if not waiting:
            return None
        live = {int(r["rank"]) for r in self.all_ranks() if r["state"] in LIVE_RANK_STATES}
        edges: dict[int, set[int]] = {}
        comm_cache: dict[str, Communicator] = {}
        for req in waiting:
            comm = comm_cache.setdefault(
                req["comm_id"], self.comms.get(req["comm_id"])
            )
            owner_world = comm.world_of(int(req["owner"]))
            if owner_world not in live:
                continue
            if self._satisfiable(req, comm):
                return None
            if int(req["src"]) == AMPI_ANY_SOURCE:
                targets = {w for w in comm.members if w != owner_world and w in live}
            else:
                targets = {comm.world_of(int(req["src"]))}
            edges.setdefault(owner_world, set()).update(targets & live)
        return _find_cycle(edges)

    def _satisfiable(self, req: dict[str, Any], comm: Communicator) -> bool:
        predicate: dict[str, Any] = {
            "comm_id": req["comm_id"],
            "dst": int(req["owner"]),
            "state": "posted",
        }
        if int(req["src"]) != AMPI_ANY_SOURCE:
            predicate["src"] = int(req["src"])
        if int(req["tag"]) != AMPI_ANY_TAG:
            predicate["tag"] = int(req["tag"])
        return bool(self.device.scan("message", predicate))

    # =====================================================================
    # One-sided operations (RMA)
    # =====================================================================

    def win_create(
        self, comm_name: str, name: str, *, model: str = WIN_UNIFIED
    ) -> dict[str, Any]:
        """AMPI_Win_create: a shared, versioned, key-addressed artifact space.

        The MPI-3 window is a byte range other ranks may read and write without
        the owner participating.  The agent analogue is the shared blackboard
        every multi-agent framework reinvents --- but with the parts those
        frameworks leave out: versions, atomics, leases, and epochs.  Without
        those you get exactly the lost-update and duplicated-work failures that
        the multi-agent literature reports.
        """
        comm = self.comms.get(comm_name)
        with self.device.write_tx():
            existing = self.device.query_one(
                "SELECT * FROM win WHERE job_id=? AND name=?", (self.job_id, name)
            )
            if existing:
                return dict(existing)
            win_id = util.new_id("win")
            self.device.execute(
                "INSERT INTO win (win_id, job_id, comm_id, name, model, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (win_id, self.job_id, comm.comm_id, name, model, util.now()),
            )
            self.tracer.emit("AMPI_Win_create", "exit", comm_id=comm.comm_id, window=name)
        return dict(
            self.device.query_one("SELECT * FROM win WHERE job_id=? AND name=?", (self.job_id, name))
        )

    def _win(self, name: str) -> dict[str, Any]:
        row = self.device.query_one(
            "SELECT * FROM win WHERE job_id=? AND (name=? OR win_id=?)", (self.job_id, name, name)
        )
        if row is None:
            raise AmpiArgError(f"no such window {name!r}")
        return row

    def win_put(
        self, win_name: str, key: str, value: Any, *, expected_version: int | None = None
    ) -> dict[str, Any]:
        """AMPI_Put / AMPI_Compare_and_swap when ``expected_version`` is given."""
        win = self._win(win_name)
        with self.device.write_tx():
            self._touch()
            ok, version, current = self.device.cas(
                win["win_id"], key, expected_version, value, self.actor()
            )
            self.tracer.emit(
                "AMPI_Put", "exit", window=win_name, key=key, ok=ok,
                tokens=util.count_tokens(util.dumps(value)),
            )
        return {"ok": ok, "version": version, "current": current if not ok else value}

    def win_get(self, win_name: str, key: str, *, charge_context: bool = False) -> dict[str, Any]:
        win = self._win(win_name)
        row = self.device.query_one(
            "SELECT * FROM win_cell WHERE win_id=? AND key=?", (win["win_id"], key)
        )
        if row is None:
            return {"key": key, "found": False, "value": None, "version": 0}
        value = util.loads(row["value"], row["value"])
        if charge_context:
            with self.device.write_tx():
                self.ctx.admit(self.rank, int(row["tokens"]), what=f"window cell {key}")
                self.ctx.charge(self.rank, int(row["tokens"]))
        return {
            "key": key,
            "found": True,
            "value": value,
            "version": int(row["version"]),
            "updated_by": row["updated_by"],
            "tokens": int(row["tokens"]),
        }

    def win_list(self, win_name: str, prefix: str = "") -> list[dict[str, Any]]:
        win = self._win(win_name)
        rows = self.device.query(
            "SELECT key, version, updated_by, updated_at, tokens FROM win_cell "
            "WHERE win_id=? AND key LIKE ? ORDER BY key",
            (win["win_id"], f"{prefix}%"),
        )
        return rows

    def win_accumulate(self, win_name: str, key: str, value: Any, op_name: str) -> dict[str, Any]:
        """AMPI_Accumulate: atomic read-modify-write with a reduction operator."""
        op = get_op(op_name)
        if op.is_semantic:
            raise AmpiArgError(
                f"{op.name} is semantic; accumulate requires a structural operator so that the "
                "read-modify-write can be atomic"
            )
        win = self._win(win_name)
        with self.device.write_tx():
            self._touch()
            row = self.device.query_one(
                "SELECT * FROM win_cell WHERE win_id=? AND key=?", (win["win_id"], key)
            )
            current = util.loads(row["value"], None) if row else op.identity
            merged = op.fn(current, value) if row else (
                op.fn(op.identity, value) if op.identity is not None else value
            )
            ok, version, _ = self.device.cas(
                win["win_id"], key, None, merged, self.actor()
            )
            self.tracer.emit("AMPI_Accumulate", "exit", window=win_name, key=key,
                             operator=op.name)
        return {"ok": ok, "version": version, "value": merged}

    def win_fetch_and_op(self, win_name: str, key: str, delta: float = 1.0) -> dict[str, Any]:
        """AMPI_Fetch_and_op on a counter: the atomic work-queue primitive.

        This one call removes the most commonly reported multi-agent
        coordination bug --- two agents picking up the same task --- and it is
        the same mechanism HPC runtimes use to build shared work queues.
        """
        win = self._win(win_name)
        with self.device.write_tx():
            self._touch()
            row = self.device.query_one(
                "SELECT * FROM win_cell WHERE win_id=? AND key=?", (win["win_id"], key)
            )
            old = float(util.loads(row["value"], 0) or 0) if row else 0.0
            self.device.cas(win["win_id"], key, None, old + delta, self.actor())
            self.tracer.emit("AMPI_Fetch_and_op", "exit", window=win_name, key=key, old=old)
        return {"old": old, "new": old + delta}

    def win_claim(self, win_name: str, key: str, *, note: str = "") -> dict[str, Any]:
        """Compare-and-swap a work item from unclaimed to claimed-by-me."""
        win = self._win(win_name)
        with self.device.write_tx():
            self._touch()
            row = self.device.query_one(
                "SELECT * FROM win_cell WHERE win_id=? AND key=?", (win["win_id"], key)
            )
            current = util.loads(row["value"], None) if row else None
            if current is not None and isinstance(current, dict) and current.get("owner") is not None:
                return {"claimed": False, "owner": current.get("owner"), "value": current}
            value = {"owner": self.rank, "claimed_at": util.now(), "note": note}
            ok, version, _ = self.device.cas(
                win["win_id"], key, int(row["version"]) if row else 0, value, self.actor()
            )
            self.tracer.emit("AMPI_Claim", "exit", window=win_name, key=key, ok=ok)
        return {"claimed": ok, "owner": self.rank if ok else None, "version": version}

    def win_lock(
        self, win_name: str, key: str, *, mode: str = LOCK_EXCLUSIVE, ttl: float = 300.0,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """AMPI_Win_lock, as a *lease*.

        MPI locks are held until the holder unlocks; a holder that dies wedges
        the window.  Agents die routinely, so AgentMPI locks always expire.
        The TTL is the maximum time the protocol is willing to be blocked by a
        holder that has stopped making progress.
        """
        if mode not in (LOCK_SHARED, LOCK_EXCLUSIVE):
            raise AmpiArgError(f"lock mode must be shared or exclusive, got {mode!r}")
        win = self._win(win_name)
        deadline = time.time() + timeout
        while True:
            with self.device.write_tx():
                self._touch()
                lock_id = self.device.lease(win["win_id"], key, self.actor(), mode, ttl)
                if lock_id:
                    self.tracer.emit("AMPI_Win_lock", "exit", window=win_name, key=key, mode=mode)
                    return {"lock_id": lock_id, "key": key, "mode": mode, "ttl": ttl}
            if time.time() >= deadline:
                holders = self.device.query(
                    "SELECT holder, mode, expires_at FROM win_lock WHERE win_id=? AND key=? "
                    "AND released_at IS NULL",
                    (win["win_id"], key),
                )
                raise AmpiTimeout(
                    f"could not acquire {mode} lock on {win_name}:{key} within {timeout}s",
                    holders=holders,
                )
            self._sleep()

    def win_unlock(self, lock_id: str) -> dict[str, Any]:
        with self.device.write_tx():
            ok = self.device.release(lock_id, self.actor())
            self.tracer.emit("AMPI_Win_unlock", "exit", lock_id=lock_id, ok=ok)
        return {"released": ok}

    def win_fence(self, win_name: str, comm_name: str, *, timeout: float = 600.0) -> dict[str, Any]:
        """AMPI_Win_fence: a barrier that also closes the current epoch.

        Every write issued before the fence is visible to every reader after
        it.  With a durable store that visibility is automatic, so the fence
        reduces to a barrier plus a recorded epoch boundary --- which is
        exactly what makes it useful for reasoning: a harness author can say
        "phase 2 reads only what phase 1 published" and have it be true.
        """
        result = barrier(self, comm_name, timeout=timeout)
        with self.device.write_tx():
            epoch = self.device.counter_next(self.job_id, f"epoch:{win_name}")
            self.tracer.emit("AMPI_Win_fence", "exit", window=win_name, epoch=epoch)
        return {"epoch": epoch, "barrier": result}

    # =====================================================================
    # Failure mitigation  (the ULFM triad, plus respawn)
    # =====================================================================

    def _assert_not_terminated(self) -> None:
        """Refuse a state-changing call from a rank that has been killed.

        ``_touch`` already enforces this for anything that sends, receives or
        touches a window, but communicator surgery does not touch, and a
        terminated rank was still able to revoke and shrink. A dead rank must
        not be able to reshape the communicator the survivors are repairing.
        """
        if self.rank is None:
            return
        row = self.device.query_one(
            "SELECT state, failure_confirmed FROM rank WHERE job_id=? AND rank=?",
            (self.job_id, self.rank))
        if row is not None and row["state"] == RANK_FAILED and row["failure_confirmed"]:
            raise AmpiProcFailed(
                f"rank {self.rank} has been terminated and may not alter the job; "
                "a replacement must be started with AMPI_Respawn", rank=self.rank)

    def comm_revoke(self, comm_name: str) -> dict[str, Any]:
        """AMPI_Comm_revoke.

        Revocation exists because knowledge of a failure is not uniform: some
        ranks have noticed, others are still blocked waiting on the dead peer
        and will wait forever.  Revoking the communicator makes every
        outstanding and future operation on it fail immediately at every rank,
        which is the only way to get all survivors to the same place at the
        same time so that recovery can be collective.
        """
        self._assert_not_terminated()
        comm = self.comms.revoke(comm_name, self.actor())
        with self.device.write_tx():
            self.tracer.emit("AMPI_Comm_revoke", "exit", comm_id=comm.comm_id)
        return comm.to_dict()

    def comm_resync(self, comm_name: str) -> dict[str, Any]:
        """Discard the in-flight collectives on a communicator and realign the
        per-rank collective sequence counters.

        Detecting a collective mismatch is necessary but not sufficient.  A
        collective slot is keyed by (communicator, sequence number) and its
        operation is fixed by whichever rank arrives first, so one rank issuing
        the wrong collective poisons that slot for everybody: the conforming
        ranks get ``AMPI_ERR_COLLECTIVE_MISMATCH`` forever, and the only
        recoveries MPI would offer are to abandon the job or to shrink the
        communicator, which discards live participants and their work for what
        is not a failure at all.

        This operation is the missing third option.  It is deliberately
        administrative and deliberately not collective: the ranks that need it
        most are the ones already stuck.  Callers SHOULD agree first with
        ``AMPI_Comm_agree`` on a healthy communicator, but the protocol cannot
        require it, because requiring agreement to escape a state that blocks
        agreement is not a recovery path.
        """
        comm = self.comms.get(comm_name)
        with self.device.write_tx():
            open_colls = self.device.query(
                "SELECT coll_id, seq, op FROM coll WHERE comm_id=? AND state='open'",
                (comm.comm_id,))
            for row in open_colls:
                self.device.execute(
                    "UPDATE coll SET state='abandoned', completed_at=? WHERE coll_id=?",
                    (util.now(), row["coll_id"]))
                self.device.execute(
                    "DELETE FROM coll_contrib WHERE coll_id=?", (row["coll_id"],))
            highest = self.device.query_one(
                "SELECT MAX(seq) AS s FROM coll WHERE comm_id=?", (comm.comm_id,))
            base = int(highest["s"] or 0) + 1
            # Every rank restarts numbering above every slot ever used, so a
            # rank that had already advanced past the poisoned one cannot
            # collide with a rank that had not.
            for world_rank in comm.members:
                local = comm.rank_of(world_rank)
                self.device.execute(
                    "INSERT INTO counter (job_id, name, value) VALUES (?,?,?) "
                    "ON CONFLICT(job_id, name) DO UPDATE SET value=excluded.value",
                    (self.job_id, f"collseq:{comm.comm_id}:{local}", base))
                self.device.kv_set(self.job_id, f"inflight:{comm.comm_id}:{local}", None)
            self.tracer.emit("AMPI_Comm_resync", "exit", comm_id=comm.comm_id,
                             abandoned=len(open_colls), base=base)
        return {
            "comm": comm.name,
            "abandoned": [{"seq": r["seq"], "op": r["op"]} for r in open_colls],
            "next_sequence": base,
            "note": "every rank must now re-issue the collective it intended; "
                    "sequence numbers restart above every slot ever used",
        }

    def comm_shrink(self, comm_name: str, new_name: str | None = None) -> dict[str, Any]:
        """AMPI_Comm_shrink: derive a communicator over the survivors.

        Ranks are renumbered densely, preserving relative order, so that
        collectives over the new communicator are well defined again.  The
        renumbering is the price of shrinking, and it is why applications that
        want to survive failures must not hard-code rank identities into
        durable state --- the same discipline ULFM demands.

        The default name is derived from the survivor set, not from a counter.
        An earlier version numbered them ``#s0``, ``#s1``, ``#s2``, which meant
        that survivors calling shrink concurrently --- the only way it is ever
        called --- each invented a different name and the group fragmented into
        singleton communicators instead of regrouping.  Three separate agents
        diagnosed that independently in one run.  Deriving the name from the
        membership makes shrink idempotent and convergent: ranks that agree on
        who survived land on the same communicator without having to
        coordinate, and ranks that disagree get a name collision they can see
        rather than a silent split.
        """
        self._assert_not_terminated()
        comm = self.comms.get(comm_name)
        failed = self.failed_ranks()
        survivors = [w for w in comm.members if w not in failed]
        target = new_name or _shrunk_name(comm.name, survivors)
        with self.device.write_tx():
            new_comm = self.comms.create(
                target,
                survivors,
                parent=comm.comm_id,
                meta={"shrunk_from": comm.name, "excluded": sorted(failed & set(comm.members))},
            )
            self.tracer.emit(
                "AMPI_Comm_shrink", "exit", comm_id=new_comm.comm_id,
                survivors=len(survivors), excluded=sorted(failed & set(comm.members)),
            )
        return new_comm.to_dict()

    def comm_agree(
        self, comm_name: str, value: bool, *, timeout: float = 300.0
    ) -> dict[str, Any]:
        """AMPI_Comm_agree: fault-tolerant agreement on a boolean.

        The survivors agree on the conjunction of their inputs *and* on the
        set of failed ranks.  This is deliberately weaker than consensus in the
        Paxos sense: it needs no leader, no persistent quorum, and no log,
        because the question being decided is small, the participant set is
        known, and the store is already durable.  ULFM makes the same call, and
        it is the right one --- agreement here is used to answer "shall we all
        continue?", which must be cheap enough to ask after every phase.
        """
        result = allreduce_structural(
            self, comm_name, bool(value), "AMPI_LAND", timeout=timeout, tolerate_failures=True
        )
        return {
            "agreed": bool(result["result"]),
            "participants": result["participants"],
            "failed": result.get("failed", []),
        }

    def respawn(self, rank: int, *, ctx_limit: int | None = None) -> dict[str, Any]:
        """Reset a failed rank so a fresh agent can take its place.

        MPI's dynamic process management is vestigial because spawning a
        process on an HPC batch system is slow and entangled with the resource
        manager.  Spawning an agent is an API call, so AgentMPI promotes
        respawn to a first-class recovery action: the generation counter
        increments, the context budget is reset, and the replacement recovers
        its predecessor's state from the durable log rather than from nothing.
        """
        with self.device.write_tx():
            row = self.device.query_one(
                "SELECT * FROM rank WHERE job_id=? AND rank=?", (self.job_id, rank)
            )
            if row is None:
                raise AmpiArgError(f"no such rank {rank}")
            generation = int(row["generation"]) + 1
            self.device.execute(
                "UPDATE rank SET generation=?, state=?, ctx_used=0, last_heartbeat=?, "
                "hb_deadline=NULL, finished_at=NULL, exit_note=NULL, ctx_limit=? "
                "WHERE job_id=? AND rank=?",
                (
                    generation,
                    RANK_INIT,
                    util.now(),
                    int(ctx_limit or row["ctx_limit"]),
                    self.job_id,
                    rank,
                ),
            )
            self.tracer.emit("AMPI_Respawn", "exit", peer=rank, generation=generation)
        return {"rank": rank, "generation": generation, "state": RANK_INIT}

    def replay_inbox(self, rank: int, comm_name: str = AMPI_COMM_WORLD) -> list[dict[str, Any]]:
        """Everything ever addressed to a rank, for a replacement to catch up on.

        This is message logging in the rollback-recovery sense.  Because the
        device is durable and messages are never destroyed by delivery, a
        respawned rank can reconstruct its predecessor's inbound history
        without any cooperation from the senders.
        """
        comm = self.comms.get(comm_name)
        me = comm.rank_of(rank)
        rows = self.device.query(
            "SELECT msg_id, src, tag, mode, body, handle, digest, tokens, state, sent_at "
            "FROM message WHERE comm_id=? AND dst=? ORDER BY msg_id",
            (comm.comm_id, me),
        )
        return rows

    # -- checkpoints --------------------------------------------------------
    def checkpoint(self, state: Any, label: str | None = None) -> dict[str, Any]:
        text = state if isinstance(state, str) else util.dumps(state)
        with self.device.write_tx():
            row = self.rank_row()
            cur = self.device.execute(
                "INSERT INTO checkpoint (job_id, rank, generation, label, state, tokens, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    self.job_id,
                    self.rank,
                    int(row["generation"]),
                    label,
                    text,
                    util.count_tokens(text),
                    util.now(),
                ),
            )
            self.tracer.emit("AMPI_Checkpoint", "exit", label=label,
                             tokens=util.count_tokens(text))
        return {"ckpt_id": cur.lastrowid, "label": label, "tokens": util.count_tokens(text)}

    def restore(self, rank: int | None = None, label: str | None = None) -> dict[str, Any] | None:
        rank = self.rank if rank is None else rank
        sql = "SELECT * FROM checkpoint WHERE job_id=? AND rank=?"
        params: list[Any] = [self.job_id, rank]
        if label:
            sql += " AND label=?"
            params.append(label)
        sql += " ORDER BY ckpt_id DESC LIMIT 1"
        row = self.device.query_one(sql, params)
        return dict(row) if row else None


def _shrunk_name(base: str, survivors: list[int]) -> str:
    """A name that every rank agreeing on the survivor set will compute."""
    digest = hashlib.sha256(",".join(str(r) for r in sorted(survivors)).encode()).hexdigest()
    return f"{base}#s{len(survivors)}-{digest[:8]}"


def _find_cycle(edges: dict[int, set[int]]) -> list[int] | None:
    """Iterative DFS cycle detection over the wait-for graph."""
    colour: dict[int, int] = {}
    parent: dict[int, int] = {}
    for start in sorted(edges):
        if colour.get(start, 0) != 0:
            continue
        stack: list[tuple[int, Iterable[int]]] = [(start, iter(sorted(edges.get(start, ()))))]
        colour[start] = 1
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if colour.get(nxt, 0) == 1:
                    cycle = [nxt]
                    cur = node
                    while cur != nxt and cur in parent:
                        cycle.append(cur)
                        cur = parent[cur]
                    cycle.append(nxt)
                    return list(reversed(cycle))
                if colour.get(nxt, 0) == 0:
                    colour[nxt] = 1
                    parent[nxt] = node
                    stack.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                    break
            if not advanced:
                colour[node] = 2
                stack.pop()
    return None
