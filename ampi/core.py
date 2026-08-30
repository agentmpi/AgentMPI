"""Core AgentMPI objects: the universe, ranks, communicators, leases, context.

This module holds everything that the point-to-point, collective, one-sided and
fault-tolerance layers build on:

* :class:`Ctx` -- a bound (journal, communicator, rank) triple, the analogue of
  the implicit state an MPI process carries after ``MPI_Init``;
* rank lifecycle and lease management (``AMPI_Init``/``AMPI_Finalize`` and the
  heartbeat that keeps a rank alive);
* communicator creation, splitting, duplication, revocation and shrinking;
* the *local, lazy* failure detector: a rank is declared failed the first time
  some other rank looks at it and finds its lease expired. This follows ULFM's
  principle that failure notification is local rather than globally consistent,
  and it means AgentMPI needs no monitoring daemon;
* the context ledger, which turns "avoid OOM" from a prompt-engineering hope
  into an accounted resource with an enforced limit.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import tokens as tok
from .errors import (
    ArgError,
    CommError,
    CtxExceededError,
    FencedError,
    NotInitError,
    RankError,
    RevokedError,
)
from .journal import Journal, now_ns

#: Wildcards, spelled as in MPI.
ANY_SOURCE = -1
ANY_TAG = -1

#: Reserved tag range. Like MPI's ``MPI_TAG_UB``, user tags are bounded so the
#: runtime can carve out a private space for collective and control traffic.
TAG_UB = 1_000_000
TAG_INTERNAL_BASE = 2_000_000


def internal_tag(purpose: str, round_: int = 0) -> int:
    """Deterministic internal tag for runtime-generated traffic."""
    base = {
        "bcast": 0,
        "reduce": 1,
        "gather": 2,
        "scatter": 3,
        "allgather": 4,
        "alltoall": 5,
        "barrier": 6,
        "scan": 7,
        "shrink": 8,
        "revoke": 9,
        "agree": 10,
        "rma": 11,
    }.get(purpose, 31)
    return TAG_INTERNAL_BASE + base * 1024 + (round_ % 1024)


# --------------------------------------------------------------------------
# Defaults. Every one of these is a protocol-visible knob; the spec documents
# them and the CLI exposes them, because the right value is workload-dependent
# in exactly the way MPI's eager threshold is.
# --------------------------------------------------------------------------


@dataclass
class Config:
    #: Payloads at or below this many tokens are delivered *inline* into the
    #: receiver's context (eager protocol). Larger payloads are delivered as a
    #: handle plus metadata, and the receiver pays for the body only if it
    #: materialises it (rendezvous protocol).
    eager_tokens: int = 700
    #: Per-rank context budget, in tokens of *delivered payload*. This is not
    #: the model's context window; it is the share of it that the protocol is
    #: permitted to fill with message bodies.
    ctx_budget: int = 60_000
    #: Lease duration. A rank that has not called into the runtime for this long
    #: becomes a failure-detector *suspect*.
    lease_ns: int = 900 * 1_000_000_000
    #: How long suspicion must persist before a rank is declared failed. The
    #: two-phase detector exists because a thinking executor and a dead one look
    #: identical to a timeout, and the two errors have very different costs: in one
    #: real run a translator was declared dead after 580s of legitimate work
    #: because it did not volunteer a heartbeat it had been asked for, and being
    #: declared dead is terminal. Suspicion is free; conviction needs
    #: corroboration -- either persistence, or an explicit declaration from the
    #: launcher, which unlike the protocol knows whether the process still exists.
    confirm_ns: int = 900 * 1_000_000_000
    #: Default deadline for blocking calls.
    timeout_ns: int = 120 * 1_000_000_000
    #: Default fraction of live ranks required to close a quorum collective.
    quorum: float = 1.0
    #: Token budget for automatically generated payload summaries.
    summary_tokens: int = 60
    #: Lock lease; an exclusive window lock is released automatically after
    #: this long so a dead lock holder cannot wedge the job forever.
    lock_lease_ns: int = 600 * 1_000_000_000

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Config":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


# --------------------------------------------------------------------------
# Bound context
# --------------------------------------------------------------------------


@dataclass
class Ctx:
    """A journal bound to a (rank, epoch, communicator) identity."""

    j: Journal
    rank: int
    epoch: int
    comm: str
    cfg: Config = field(default_factory=Config)

    # -- communicator views ------------------------------------------------
    @property
    def comm_row(self) -> sqlite3.Row:
        return comm_row(self.j, self.comm)

    @property
    def size(self) -> int:
        return int(self.comm_row["size"])

    @property
    def crank(self) -> int:
        """This rank's index inside ``self.comm``."""
        return world_to_comm(self.j, self.comm, self.rank)

    def members(self) -> List[int]:
        return comm_members(self.j, self.comm)

    def check_live(self) -> None:
        """Fail fast if this rank has been fenced out by a replacement."""
        row = rank_row(self.j, self.rank)
        if int(row["epoch"]) != self.epoch:
            raise FencedError(
                f"rank {self.rank} is now at epoch {row['epoch']}, you are epoch {self.epoch}",
                hint=(
                    "a replacement agent has taken over this rank. Stop working, "
                    "do not write further output, and report that you were fenced."
                ),
                detail={"rank": self.rank, "your_epoch": self.epoch, "current_epoch": int(row["epoch"])},
            )
        if row["state"] in ("failed", "fenced"):
            raise FencedError(
                f"rank {self.rank} is marked {row['state']}",
                hint="you were declared failed by a peer; stop and report.",
            )


# --------------------------------------------------------------------------
# Job / rank lifecycle
# --------------------------------------------------------------------------


def create_job(
    j: Journal,
    *,
    world_size: int,
    label: Optional[str] = None,
    cfg: Optional[Config] = None,
    roles: Optional[Sequence[Optional[str]]] = None,
) -> str:
    """Create a job and its ``AMPI_COMM_WORLD``."""
    if world_size < 1:
        raise ArgError("world size must be >= 1")
    cfg = cfg or Config()
    job_id = "j-" + uuid.uuid4().hex[:12]
    ts = now_ns()
    with j.tx() as c:
        c.execute(
            "INSERT INTO job(id,world_size,created_ns,state,label,config) VALUES(?,?,?,?,?,?)",
            (job_id, world_size, ts, "running", label, json.dumps(cfg.to_dict())),
        )
        j._job_id = job_id
        for r in range(world_size):
            c.execute(
                "INSERT INTO rank(job,rank,epoch,state,role,lease_ns,ctx_budget) VALUES(?,?,?,?,?,?,?)",
                (
                    job_id,
                    r,
                    0,
                    "unspawned",
                    (roles[r] if roles and r < len(roles) else None),
                    cfg.lease_ns,
                    cfg.ctx_budget,
                ),
            )
        _create_comm(
            c,
            j,
            comm_id="c:world",
            name="world",
            members=list(range(world_size)),
            kind="intra",
            parent=None,
        )
        j.trace("job_start", phase="instant", detail={"world_size": world_size, "label": label}, conn=c)
    return job_id


def _create_comm(
    c: sqlite3.Connection,
    j: Journal,
    *,
    comm_id: str,
    name: str,
    members: Sequence[int],
    kind: str = "intra",
    parent: Optional[str] = None,
    topo: Optional[Dict[str, Any]] = None,
    generation: int = 0,
) -> str:
    c.execute(
        "INSERT INTO comm(id,job,name,parent,kind,size,generation,topo,created_ns)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (
            comm_id,
            j.job_id,
            name,
            parent,
            kind,
            len(members),
            generation,
            json.dumps(topo or {}),
            now_ns(),
        ),
    )
    for i, w in enumerate(members):
        c.execute("INSERT INTO comm_member(comm,crank,wrank) VALUES(?,?,?)", (comm_id, i, w))
    return comm_id


def rank_row(j: Journal, rank: int) -> sqlite3.Row:
    row = j.q1("SELECT * FROM rank WHERE job=? AND rank=?", (j.job_id, rank))
    if row is None:
        raise RankError(f"no such world rank {rank} in job {j.job_id}")
    return row


def comm_row(j: Journal, comm: str) -> sqlite3.Row:
    row = j.q1(
        "SELECT * FROM comm WHERE job=? AND (id=? OR name=?)",
        (j.job_id, comm, comm),
    )
    if row is None:
        raise CommError(
            f"no such communicator {comm!r}",
            hint="list communicators with `ampi comm list`",
        )
    return row


def comm_members(j: Journal, comm: str) -> List[int]:
    cid = comm_row(j, comm)["id"]
    return [int(r["wrank"]) for r in j.q("SELECT wrank FROM comm_member WHERE comm=? ORDER BY crank", (cid,))]


def world_to_comm(j: Journal, comm: str, wrank: int) -> int:
    cid = comm_row(j, comm)["id"]
    v = j.scalar("SELECT crank FROM comm_member WHERE comm=? AND wrank=?", (cid, wrank))
    if v is None:
        raise RankError(
            f"world rank {wrank} is not a member of communicator {comm!r}",
            hint="you cannot use a communicator you do not belong to",
        )
    return int(v)


def comm_to_world(j: Journal, comm: str, crank: int) -> int:
    cid = comm_row(j, comm)["id"]
    v = j.scalar("SELECT wrank FROM comm_member WHERE comm=? AND crank=?", (cid, crank))
    if v is None:
        raise RankError(f"communicator {comm!r} has no rank {crank}")
    return int(v)


def load_config(j: Journal) -> Config:
    return Config.from_dict(j.job_config())


def init_rank(
    j: Journal,
    rank: int,
    *,
    agent_id: Optional[str] = None,
    role: Optional[str] = None,
    ctx_budget: Optional[int] = None,
    reinit: bool = False,
) -> Dict[str, Any]:
    """``AMPI_Init``: join the universe as ``rank``.

    Unlike ``MPI_Init``, this is idempotent-ish by design: a replacement agent
    for a failed rank calls it with ``reinit=True``, which bumps the fencing
    epoch and returns a recovery briefing rather than a clean slate.
    """
    cfg = load_config(j)
    ts = now_ns()
    with j.tx() as c:
        row = c.execute("SELECT * FROM rank WHERE job=? AND rank=?", (j.job_id, rank)).fetchone()
        if row is None:
            raise RankError(f"no such rank {rank}")
        epoch = int(row["epoch"])
        if row["state"] == "spawned":
            # The launcher (or `ampi respawn`) already allocated this epoch for
            # us, so adopt it. Bumping again here would fence the replacement
            # against its own newly-created epoch.
            pass
        elif row["state"] in ("init", "running") and not reinit:
            # Re-running init from the same agent (e.g. after a shell retry) is
            # benign; treat it as a heartbeat rather than an error, because
            # agents do retry commands.
            pass
        elif reinit or row["state"] in ("failed", "fenced"):
            epoch += 1
        c.execute(
            "UPDATE rank SET epoch=?, state='running', agent_id=COALESCE(?,agent_id),"
            " role=COALESCE(?,role), ctx_budget=?, last_hb_ns=?,"
            " lease_expires_ns=?, init_ns=COALESCE(init_ns,?), calls=calls+1"
            " WHERE job=? AND rank=?",
            (
                epoch,
                agent_id,
                role,
                int(ctx_budget or row["ctx_budget"] or cfg.ctx_budget),
                ts,
                ts + int(row["lease_ns"] or cfg.lease_ns),
                ts,
                j.job_id,
                rank,
            ),
        )
        j.trace("init", rank=rank, epoch=epoch, phase="instant", detail={"reinit": reinit}, conn=c)
    row = rank_row(j, rank)
    return {
        "rank": rank,
        "epoch": int(row["epoch"]),
        "world_size": int(j.job_row()["world_size"]),
        "role": row["role"],
        "ctx_budget": int(row["ctx_budget"]),
        "ctx_used": int(row["ctx_used"]),
    }


def finalize_rank(j: Journal, rank: int, epoch: int, *, status: str = "ok") -> None:
    with j.tx() as c:
        c.execute(
            "UPDATE rank SET state='finalized', fini_ns=? WHERE job=? AND rank=? AND epoch=?",
            (now_ns(), j.job_id, rank, epoch),
        )
        j.trace("finalize", rank=rank, epoch=epoch, status=status, conn=c)


def extend_lease(
    j: Journal, rank: int, epoch: int, seconds: float, conn: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """Guarantee this rank's lease survives for at least ``seconds`` more.

    Leases exist to detect dead agents, but an agent that is *thinking* makes no
    runtime calls, and a long think is indistinguishable from death to a
    timeout-based detector. The classical answer -- make the lease longer than
    the longest legitimate pause -- is bad here, because the lease also bounds how
    long a peer blocked in a collective waits before it can make progress.

    So AgentMPI gives the agent a way to say what a timeout cannot infer: "I am
    about to spend ten minutes on one step." This is the same move a
    long-running task makes when it extends a distributed lock rather than
    holding a lock long enough for its worst case, and it lets the default lease
    stay short enough to make failure detection useful.
    """
    c = conn or j.conn
    ts = now_ns()
    want = ts + int(max(0.0, seconds) * 1_000_000_000)
    c.execute(
        "UPDATE rank SET last_hb_ns=?, lease_expires_ns=MAX(?, ?+lease_ns), calls=calls+1"
        " WHERE job=? AND rank=? AND epoch=?",
        (ts, want, ts, j.job_id, rank, epoch),
    )
    row = c.execute(
        "SELECT lease_expires_ns FROM rank WHERE job=? AND rank=?", (j.job_id, rank)
    ).fetchone()
    return {
        "rank": rank,
        "lease_expires_in_s": round((int(row["lease_expires_ns"]) - ts) / 1e9, 1),
        "extended_by_s": round(max(0.0, seconds), 1),
    }


def heartbeat(j: Journal, rank: int, epoch: int, conn: Optional[sqlite3.Connection] = None) -> None:
    """Renew this rank's lease. Called implicitly by every runtime entry point.

    Piggy-backing the heartbeat on real protocol calls, rather than requiring a
    separate keep-alive, matters for agents: an agent that is *thinking* is not
    calling the runtime, so a lease long enough to cover a think step is the
    right granularity, and every call is evidence of liveness.
    """
    c = conn or j.conn
    ts = now_ns()
    c.execute(
        "UPDATE rank SET last_hb_ns=?, lease_expires_ns=?+lease_ns, calls=calls+1"
        " WHERE job=? AND rank=? AND epoch=?",
        (ts, ts, j.job_id, rank, epoch),
    )


# --------------------------------------------------------------------------
# Failure detection: local, lazy, lease-based
# --------------------------------------------------------------------------


def detect_failures(
    j: Journal,
    comm: Optional[str] = None,
    *,
    by: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[int]:
    """Run the two-phase failure detector; return ranks newly declared failed.

    Phase 1 (**suspect**): the lease deadline passed. Suspicion is recorded and is
    visible to every rank, but it changes nothing -- collectives keep waiting and
    no work is redistributed.

    Phase 2 (**failed**): suspicion has persisted for a confirmation window. Only
    now is the rank written off, its subtrees dropped from reduction trees and its
    locks broken.

    The split matters because a timeout cannot distinguish a thinking executor
    from a dead one, and the two errors are not symmetric. Declaring a working
    agent dead discards minutes of real work and then fences it, so it cannot even
    finish what it had. Suspicion is cheap; conviction requires corroboration --
    persistence, or an explicit declaration from the launcher, which unlike the
    protocol actually knows whether the executor process still exists.

    Detection remains lazy and local: it runs only when some rank blocks on an
    operation a failure would prevent, so its failure-free cost is a timestamp
    comparison, and two ranks may legitimately hold different views. That is
    ULFM's "notification is local" principle.
    """
    ts = now_ns()
    scope: Optional[List[int]] = comm_members(j, comm) if comm else None
    c = conn or j.conn
    confirm_ns = int(load_config(j).confirm_ns)
    rows = c.execute(
        "SELECT rank, epoch, state, lease_expires_ns, suspect_ns FROM rank WHERE job=?",
        (j.job_id,),
    ).fetchall()
    newly: List[int] = []
    for r in rows:
        wr = int(r["rank"])
        if scope is not None and wr not in scope:
            continue
        state = str(r["state"])
        if state not in ("running", "init", "spawned", "suspect"):
            continue
        exp = int(r["lease_expires_ns"] or 0)
        if not exp or ts <= exp:
            # Evidence of activity clears suspicion: a rank that has called in is
            # alive, whatever a pessimistic deadline implied a moment ago.
            if state == "suspect":
                c.execute(
                    "UPDATE rank SET state='running', suspect_ns=0 WHERE job=? AND rank=?",
                    (j.job_id, wr),
                )
                j.trace("suspicion_cleared", rank=wr, epoch=int(r["epoch"]), conn=c)
            continue
        suspect_since = int(r["suspect_ns"] or 0)
        if suspect_since == 0:
            c.execute(
                "UPDATE rank SET state='suspect', suspect_ns=? WHERE job=? AND rank=? AND epoch=?",
                (ts, j.job_id, wr, int(r["epoch"])),
            )
            j.trace(
                "suspect",
                rank=wr,
                epoch=int(r["epoch"]),
                status="lease_expired",
                detail={"detected_by": by, "silent_s": round((ts - exp) / 1e9, 1)},
                conn=c,
            )
            continue
        if ts - suspect_since < confirm_ns:
            continue
        c.execute(
            "UPDATE rank SET state='failed' WHERE job=? AND rank=? AND epoch=?",
            (j.job_id, wr, int(r["epoch"])),
        )
        c.execute(
            "INSERT INTO failure(job,rank,epoch,kind,detected_ns,detected_by,detail)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                j.job_id,
                wr,
                int(r["epoch"]),
                "lease_expired",
                ts,
                by,
                json.dumps({
                    "silent_s": round((ts - exp) / 1e9, 1),
                    "suspected_s": round((ts - suspect_since) / 1e9, 1),
                }),
            ),
        )
        j.trace(
            "failure",
            rank=wr,
            epoch=int(r["epoch"]),
            status="lease_expired",
            detail={"detected_by": by, "suspected_s": round((ts - suspect_since) / 1e9, 1)},
            conn=c,
        )
        newly.append(wr)
    return newly


def suspect_ranks(j: Journal, comm: Optional[str] = None) -> List[int]:
    """Ranks the detector currently suspects but has not convicted."""
    scope = set(comm_members(j, comm)) if comm else None
    out = []
    for r in j.q(
        "SELECT rank FROM rank WHERE job=? AND state='suspect' ORDER BY rank", (j.job_id,)
    ):
        wr = int(r["rank"])
        if scope is None or wr in scope:
            out.append(wr)
    return out


def failed_ranks(j: Journal, comm: Optional[str] = None) -> List[int]:
    scope = set(comm_members(j, comm)) if comm else None
    out = []
    for r in j.q(
        "SELECT rank FROM rank WHERE job=? AND state IN ('failed','fenced') ORDER BY rank",
        (j.job_id,),
    ):
        wr = int(r["rank"])
        if scope is None or wr in scope:
            out.append(wr)
    return out


def live_ranks(j: Journal, comm: Optional[str] = None) -> List[int]:
    """Ranks not declared failed. Suspects count as live: suspicion alone must not
    cause work to be redistributed, or a merely slow executor loses its
    assignment to a duplicate."""
    scope = comm_members(j, comm) if comm else [int(r["rank"]) for r in j.q("SELECT rank FROM rank WHERE job=?", (j.job_id,))]
    dead = set(failed_ranks(j))
    return [r for r in scope if r not in dead]


def check_comm_usable(j: Journal, comm: str) -> sqlite3.Row:
    row = comm_row(j, comm)
    if int(row["revoked"]):
        raise RevokedError(
            f"communicator {row['name']!r} has been revoked",
            hint=(
                "call `ampi comm shrink --comm "
                f"{row['name']}` to build a working communicator over the survivors"
            ),
            detail={"comm": row["name"], "revoked_by": row["revoked_by"]},
        )
    return row


# --------------------------------------------------------------------------
# Context ledger: the OOM guard
# --------------------------------------------------------------------------


def ctx_state(j: Journal, rank: int) -> Dict[str, int]:
    row = rank_row(j, rank)
    budget = int(row["ctx_budget"] or 0)
    used = int(row["ctx_used"] or 0)
    return {
        "budget": budget,
        "used": used,
        "remaining": max(0, budget - used),
        "hwm": int(row["ctx_hwm"] or 0),
    }


def ctx_charge(
    j: Journal,
    rank: int,
    epoch: int,
    ntokens: int,
    *,
    conn: Optional[sqlite3.Connection] = None,
    what: str = "payload",
    force: bool = False,
) -> Dict[str, int]:
    """Charge ``ntokens`` of delivered payload against ``rank``'s budget.

    Raises :class:`CtxExceededError` when the charge would overrun the budget,
    unless ``force``. Callers are expected to catch that and fall back to
    delivering a *view* (a bounded projection) instead of the body -- the
    protocol's answer to context exhaustion is graceful degradation, not death.
    """
    c = conn or j.conn
    st = ctx_state(j, rank)
    if not force and ntokens > st["remaining"]:
        raise CtxExceededError(
            f"delivering {ntokens} tokens of {what} would exceed the context budget "
            f"({st['used']}/{st['budget']} used, {st['remaining']} left)",
            hint=(
                "read a bounded projection instead: `ampi view <handle> --budget "
                f"{max(200, st['remaining'] // 2)}`, or raise your budget with "
                "`ampi ctx grant --tokens N` if the harness allows it"
            ),
            detail={"needed": ntokens, **st},
        )
    used = st["used"] + ntokens
    c.execute(
        "UPDATE rank SET ctx_used=?, ctx_hwm=MAX(ctx_hwm,?) WHERE job=? AND rank=? AND epoch=?",
        (used, used, j.job_id, rank, epoch),
    )
    j.bump("ctx_tokens", rank, ntokens, conn=c)
    return {"budget": st["budget"], "used": used, "remaining": max(0, st["budget"] - used)}


def ctx_release(j: Journal, rank: int, epoch: int, ntokens: int, *, conn: Optional[sqlite3.Connection] = None) -> None:
    """Give context back, e.g. after the agent compacts its own transcript."""
    c = conn or j.conn
    c.execute(
        "UPDATE rank SET ctx_used=MAX(0,ctx_used-?) WHERE job=? AND rank=? AND epoch=?",
        (max(0, ntokens), j.job_id, rank, epoch),
    )


# --------------------------------------------------------------------------
# Payload packaging: the eager / rendezvous decision
# --------------------------------------------------------------------------


def summarize(text: str, budget: int) -> str:
    """Produce a deterministic, cheap structural summary of a payload.

    This is *not* an LLM summary: it must be free, deterministic and replayable,
    because it is part of the message envelope. It gives the receiver enough to
    decide whether materialising the body is worth the tokens -- head, tail,
    shape, and, for JSON, the key structure. Semantic summarisation is available
    separately as an agent-evaluated view (``ampi view --op agent``).
    """
    text = text.strip()
    if not text:
        return "(empty)"
    stripped = text.lstrip()
    if stripped[:1] in "{[":
        try:
            data = json.loads(text)
            return _json_shape(data, budget)
        except Exception:
            pass
    lines = text.splitlines()
    head = "\n".join(lines[:6])
    out = f"{len(lines)} lines, {tok.count(text)} tokens; head:\n{head}"
    if len(lines) > 12:
        out += "\n...\ntail:\n" + "\n".join(lines[-3:])
    return tok.truncate_to_tokens(out, budget, marker=" …")


def _json_shape(data: Any, budget: int, depth: int = 0) -> str:
    if isinstance(data, dict):
        keys = list(data.keys())
        shown = keys[:12]
        inner = ", ".join(
            f"{k}:{_json_shape(data[k], 8, depth + 1)}" for k in shown
        ) if depth < 2 else ", ".join(shown)
        more = f", +{len(keys) - len(shown)} more" if len(keys) > len(shown) else ""
        return tok.truncate_to_tokens("{" + inner + more + "}", budget, marker="…}")
    if isinstance(data, list):
        if not data:
            return "[]"
        return tok.truncate_to_tokens(
            f"[{len(data)} x {_json_shape(data[0], 8, depth + 1)}]", budget, marker="…]"
        )
    if isinstance(data, str):
        return f"str({len(data)}c)"
    return type(data).__name__


@dataclass
class Payload:
    """A packaged message body, ready to be enqueued."""

    obj: str
    tokens: int
    nbytes: int
    digest: str
    summary: str
    schema: Optional[str]
    inline: Optional[str]
    mode: str  # eager | rendezvous


def package(
    j: Journal,
    text: str,
    *,
    creator: Optional[int] = None,
    cfg: Optional[Config] = None,
    schema: Optional[str] = None,
    label: Optional[str] = None,
    force_mode: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Payload:
    """Store a payload and decide eager vs rendezvous.

    The threshold is the direct analogue of an MPI implementation's eager limit,
    and it exists for the same reason: below it, pushing the bytes to the
    receiver unsolicited is cheaper than a handshake; above it, the receiver's
    buffer -- here, its context window -- is too precious to fill without
    permission. In MPI the scarce resource is unexpected-message buffer memory;
    in AgentMPI it is the receiver's attention.
    """
    cfg = cfg or Config()
    summary = summarize(text, cfg.summary_tokens)
    rec = j.put_object(
        text, creator=creator, summary=summary, schema=schema, label=label, conn=conn
    )
    ntok = int(rec["tokens"])
    mode = force_mode or ("eager" if ntok <= cfg.eager_tokens else "rendezvous")
    return Payload(
        obj=rec["id"],
        tokens=ntok,
        nbytes=int(rec["nbytes"]),
        digest=rec["digest"],
        summary=summary,
        schema=schema,
        inline=text if mode == "eager" else None,
        mode=mode,
    )


# --------------------------------------------------------------------------
# Environment binding for the CLI
# --------------------------------------------------------------------------


def bind(
    j: Journal,
    *,
    rank: Optional[int] = None,
    comm: str = "world",
    require_init: bool = True,
    beat: bool = True,
) -> Ctx:
    """Resolve the calling rank's identity from arguments or the environment.

    ``AMPI_RANK`` is set in every rank's launch environment, so agents normally
    never pass ``--rank``. Getting this right matters more than it sounds: the
    single most common agent error in early testing was passing the wrong rank,
    and making the identity ambient eliminated it.
    """
    if rank is None:
        env = os.environ.get("AMPI_RANK")
        if env is None or env == "":
            raise NotInitError(
                "cannot tell which rank you are",
                hint="pass --rank R, or set AMPI_RANK in the environment",
            )
        try:
            rank = int(env)
        except ValueError as exc:
            raise ArgError(f"AMPI_RANK={env!r} is not an integer") from exc
    row = rank_row(j, rank)
    if require_init and row["state"] in ("unspawned", "spawned"):
        raise NotInitError(
            f"rank {rank} has not called AMPI_Init",
            hint=f"run `ampi init --rank {rank}` first",
        )
    cfg = load_config(j)
    ctx = Ctx(j=j, rank=rank, epoch=int(row["epoch"]), comm=comm_row(j, comm)["id"], cfg=cfg)
    if beat:
        with j.tx() as c:
            heartbeat(j, rank, ctx.epoch, conn=c)
    return ctx


def resolve_peer(ctx: Ctx, peer: str) -> int:
    """Accept a communicator rank, ``any``, or ``w<N>`` for a world rank."""
    peer = str(peer).strip().lower()
    if peer in ("any", "*", "any_source"):
        return ANY_SOURCE
    if peer.startswith("w"):
        return world_to_comm(ctx.j, ctx.comm, int(peer[1:]))
    try:
        v = int(peer)
    except ValueError as exc:
        raise ArgError(f"cannot parse rank {peer!r}") from exc
    if v < 0:
        return ANY_SOURCE
    if v >= ctx.size:
        raise RankError(
            f"rank {v} is out of range for communicator of size {ctx.size}",
            hint=f"valid ranks are 0..{ctx.size - 1}",
        )
    return v


def resolve_tag(tag: Optional[str]) -> int:
    if tag is None:
        return 0
    t = str(tag).strip().lower()
    if t in ("any", "*", "any_tag"):
        return ANY_TAG
    try:
        v = int(t)
    except ValueError:
        # Symbolic tags are a genuine ergonomic win for agents, which reason
        # about names far more reliably than about integers. We hash them into
        # the user tag space deterministically.
        import zlib

        return zlib.crc32(t.encode()) % TAG_UB
    if v > TAG_UB:
        raise ArgError(f"tag {v} exceeds AMPI_TAG_UB={TAG_UB}")
    return v


def tag_name(tag: int) -> str:
    if tag == ANY_TAG:
        return "ANY"
    if tag >= TAG_INTERNAL_BASE:
        return f"internal:{tag - TAG_INTERNAL_BASE}"
    return str(tag)


def assert_not_internal(tag: int) -> None:
    if tag >= TAG_INTERNAL_BASE:
        raise ArgError(
            f"tag {tag} is in the reserved internal range (>= {TAG_INTERNAL_BASE})",
            hint=f"use tags in 0..{TAG_UB}",
        )
