"""The AgentMPI journal: durable state for a job.

Design rationale
----------------
An MPI implementation keeps its matching queues, window contents and request
handles in process memory, because MPI processes fail as a unit and there is
nothing to recover. AgentMPI ranks are LLM agents: they die individually and
routinely, they are replaced, and a replacement must be able to reconstruct
what its predecessor had committed. So *all* protocol state in AgentMPI is
durable and append-structured, and the runtime is a library over that store
rather than a daemon holding state in RAM.

Concretely this file provides:

* a single SQLite database per job, in WAL mode, acting as the message-matching
  queues, communicator table, window store, request table and event trace;
* a content-addressed object store on the filesystem for payload bodies, so the
  database stays small and payloads are deduplicated and immutable;
* short, retried transactions so that dozens of concurrent agent processes can
  hammer the same journal without livelock.

Choosing SQLite is deliberate: it gives us atomic multi-table transactions
(needed for message *matching*, which must atomically pop from one queue and
push to another) and crash-safe durability, without running a server that would
itself be a single point of failure the paper would have to defend.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import random
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from .errors import AmpiError, ErrClass, NoJobError
from .version import PROTOCOL_VERSION, SCHEMA_VERSION

#: Directory name used for AgentMPI state, relative to the job root.
STATE_DIR = ".ampi"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS meta(
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);

-- ------------------------------------------------------------------ job
CREATE TABLE IF NOT EXISTS job(
    id            TEXT PRIMARY KEY,
    world_size    INTEGER NOT NULL,
    created_ns    INTEGER NOT NULL,
    state         TEXT NOT NULL DEFAULT 'running',   -- running|done|aborted
    label         TEXT,
    config        TEXT NOT NULL DEFAULT '{}'
);

-- ----------------------------------------------------------------- ranks
-- One row per world rank. `epoch` is the fencing token: a respawned
-- replacement for rank r runs at epoch e+1, and any message or lock bearing
-- epoch <= e is stale and is discarded. This is what makes zombie agents
-- (alive but declared dead) harmless.
CREATE TABLE IF NOT EXISTS rank(
    job              TEXT NOT NULL,
    rank             INTEGER NOT NULL,
    epoch            INTEGER NOT NULL DEFAULT 0,
    state            TEXT NOT NULL DEFAULT 'unspawned',
        -- unspawned|spawned|init|running|finalized|failed|revoked|fenced
    role             TEXT,
    agent_id         TEXT,
    lease_ns         INTEGER NOT NULL DEFAULT 0,   -- lease duration
    -- When the failure detector first became suspicious. A rank is declared
    -- *failed* only after suspicion has persisted for a confirmation window,
    -- which keeps a slow executor from being killed merely for thinking.
    suspect_ns       INTEGER NOT NULL DEFAULT 0,
    lease_expires_ns INTEGER NOT NULL DEFAULT 0,
    last_hb_ns       INTEGER NOT NULL DEFAULT 0,
    init_ns          INTEGER,
    fini_ns          INTEGER,
    ctx_budget       INTEGER NOT NULL DEFAULT 0,   -- tokens
    ctx_used         INTEGER NOT NULL DEFAULT 0,   -- tokens delivered into context
    ctx_hwm          INTEGER NOT NULL DEFAULT 0,   -- high-water mark
    calls            INTEGER NOT NULL DEFAULT 0,
    meta             TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(job, rank)
);

-- --------------------------------------------------------- communicators
CREATE TABLE IF NOT EXISTS comm(
    id          TEXT PRIMARY KEY,
    job         TEXT NOT NULL,
    name        TEXT NOT NULL,
    parent      TEXT,
    kind        TEXT NOT NULL DEFAULT 'intra',   -- intra|inter|cart|graph
    size        INTEGER NOT NULL,
    generation  INTEGER NOT NULL DEFAULT 0,      -- bumped by shrink
    revoked     INTEGER NOT NULL DEFAULT 0,
    revoked_by  INTEGER,
    revoked_ns  INTEGER,
    topo        TEXT NOT NULL DEFAULT '{}',
    created_ns  INTEGER NOT NULL,
    freed_ns    INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS comm_name_uq ON comm(job, name);

CREATE TABLE IF NOT EXISTS comm_member(
    comm  TEXT NOT NULL,
    crank INTEGER NOT NULL,      -- rank within this communicator
    wrank INTEGER NOT NULL,      -- world rank
    PRIMARY KEY(comm, crank)
);
CREATE INDEX IF NOT EXISTS comm_member_w ON comm_member(comm, wrank);

-- -------------------------------------------------------------- messages
-- The unexpected-message queue and the posted-receive queue of an MPI
-- implementation, made durable. A message is 'posted' until matched, then
-- 'matched' until the receiver actually pulls the payload into its context
-- ('delivered'). Splitting matched from delivered is what lets AgentMPI
-- implement rendezvous: matching is cheap, delivery costs context.
CREATE TABLE IF NOT EXISTS msg(
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,
    comm        TEXT NOT NULL,
    src         INTEGER NOT NULL,        -- communicator-local rank
    dst         INTEGER NOT NULL,
    tag         INTEGER NOT NULL,
    src_epoch   INTEGER NOT NULL DEFAULT 0,
    kind        TEXT NOT NULL DEFAULT 'p2p',   -- p2p|coll|rma|ctrl
    mode        TEXT NOT NULL DEFAULT 'eager', -- eager|rendezvous|sync
    obj         TEXT,                    -- object id holding the full payload
    inline      TEXT,                    -- payload text when mode='eager'
    tokens      INTEGER NOT NULL DEFAULT 0,
    nbytes      INTEGER NOT NULL DEFAULT 0,
    digest      TEXT,
    summary     TEXT,
    schema      TEXT,
    status      TEXT NOT NULL DEFAULT 'posted',
        -- posted|matched|delivered|cancelled|dropped
    sent_ns     INTEGER NOT NULL,
    matched_ns  INTEGER,
    delivered_ns INTEGER,
    recv_id     INTEGER,
    coll        TEXT,
    coll_round  INTEGER,
    idem        TEXT,
    meta        TEXT NOT NULL DEFAULT '{}'
);
-- The matching index: this is the hot path (the "match list" of an MPI
-- implementation). Ordering by seq within (comm,src,dst) is what implements
-- MPI's non-overtaking rule.
CREATE INDEX IF NOT EXISTS msg_match ON msg(comm, dst, status, tag, src, seq);
CREATE INDEX IF NOT EXISTS msg_src ON msg(comm, src, status);
CREATE UNIQUE INDEX IF NOT EXISTS msg_idem ON msg(job, idem) WHERE idem IS NOT NULL;
CREATE INDEX IF NOT EXISTS msg_coll ON msg(coll);

CREATE TABLE IF NOT EXISTS recvq(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job        TEXT NOT NULL,
    comm       TEXT NOT NULL,
    dst        INTEGER NOT NULL,
    src        INTEGER NOT NULL,   -- -1 = AMPI_ANY_SOURCE
    tag        INTEGER NOT NULL,   -- -1 = AMPI_ANY_TAG
    dst_epoch  INTEGER NOT NULL DEFAULT 0,
    posted_ns  INTEGER NOT NULL,
    deadline_ns INTEGER,
    status     TEXT NOT NULL DEFAULT 'posted',  -- posted|matched|done|cancelled
    msg_seq    INTEGER,
    req        TEXT,
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS recvq_match ON recvq(comm, dst, status, posted_ns);

-- -------------------------------------------------------------- requests
CREATE TABLE IF NOT EXISTS request(
    id           TEXT PRIMARY KEY,
    job          TEXT NOT NULL,
    rank         INTEGER NOT NULL,
    epoch        INTEGER NOT NULL DEFAULT 0,
    op           TEXT NOT NULL,      -- isend|irecv|ibcast|iallreduce|ibarrier|...
    comm         TEXT,
    peer         INTEGER,
    tag          INTEGER,
    state        TEXT NOT NULL DEFAULT 'active',  -- active|complete|cancelled|failed
    msg_seq      INTEGER,
    recv_id      INTEGER,
    coll         TEXT,
    persistent   INTEGER NOT NULL DEFAULT 0,
    started      INTEGER NOT NULL DEFAULT 1,
    created_ns   INTEGER NOT NULL,
    completed_ns INTEGER,
    params       TEXT NOT NULL DEFAULT '{}',
    result       TEXT
);
CREATE INDEX IF NOT EXISTS request_rank ON request(job, rank, state);

-- ----------------------------------------------------------- collectives
CREATE TABLE IF NOT EXISTS coll(
    id           TEXT PRIMARY KEY,
    job          TEXT NOT NULL,
    comm         TEXT NOT NULL,
    seqno        INTEGER NOT NULL,     -- per-communicator collective sequence
    op           TEXT NOT NULL,        -- barrier|bcast|reduce|allreduce|...
    reduce_op    TEXT,
    root         INTEGER,
    algo         TEXT NOT NULL,
    quorum       REAL NOT NULL DEFAULT 1.0,
    deadline_ns  INTEGER,
    state        TEXT NOT NULL DEFAULT 'open',  -- open|closed|failed|revoked
    created_ns   INTEGER NOT NULL,
    closed_ns    INTEGER,
    nparts       INTEGER NOT NULL DEFAULT 0,
    result_obj   TEXT,
    params       TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS coll_seq ON coll(comm, op, seqno);

CREATE TABLE IF NOT EXISTS coll_part(
    coll       TEXT NOT NULL,
    crank      INTEGER NOT NULL,
    state      TEXT NOT NULL DEFAULT 'joined',  -- joined|reducing|done|absent|failed
    in_obj     TEXT,
    out_obj    TEXT,
    joined_ns  INTEGER NOT NULL,
    done_ns    INTEGER,
    rounds     INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(coll, crank)
);

-- Pending agent-evaluated reduction steps (AMPI_Op_create with an agent
-- callback). The runtime hands two operands to a rank and waits for it to
-- commit the merged result.
CREATE TABLE IF NOT EXISTS reduce_step(
    id         TEXT PRIMARY KEY,
    coll       TEXT NOT NULL,
    crank      INTEGER NOT NULL,
    round      INTEGER NOT NULL,
    left_obj   TEXT NOT NULL,
    right_obj  TEXT NOT NULL,
    left_from  INTEGER,
    right_from INTEGER,
    state      TEXT NOT NULL DEFAULT 'pending',  -- pending|committed|abandoned
    out_obj    TEXT,
    issued_ns  INTEGER NOT NULL,
    committed_ns INTEGER,
    deadline_ns INTEGER
);
CREATE INDEX IF NOT EXISTS reduce_step_open ON reduce_step(coll, crank, state);

-- --------------------------------------------------------------- windows
-- One-sided shared state: the "blackboard". Cells are versioned so that
-- accumulate/CAS have well-defined semantics and so that a replacement agent
-- can see the history its predecessor wrote.
CREATE TABLE IF NOT EXISTS win(
    id         TEXT PRIMARY KEY,
    job        TEXT NOT NULL,
    comm       TEXT NOT NULL,
    name       TEXT NOT NULL,
    model      TEXT NOT NULL DEFAULT 'unified',  -- unified|separate
    created_ns INTEGER NOT NULL,
    freed_ns   INTEGER,
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS win_name_uq ON win(job, name);

CREATE TABLE IF NOT EXISTS win_cell(
    win        TEXT NOT NULL,
    key        TEXT NOT NULL,
    version    INTEGER NOT NULL DEFAULT 1,
    obj        TEXT,
    tokens     INTEGER NOT NULL DEFAULT 0,
    digest     TEXT,
    summary    TEXT,
    schema     TEXT,
    owner      INTEGER,               -- home rank (for locality accounting)
    writer     INTEGER,
    writer_epoch INTEGER,
    written_ns INTEGER NOT NULL,
    PRIMARY KEY(win, key)
);

CREATE TABLE IF NOT EXISTS win_hist(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    win        TEXT NOT NULL,
    key        TEXT NOT NULL,
    version    INTEGER NOT NULL,
    obj        TEXT,
    op         TEXT NOT NULL,          -- put|acc|cas|faop|del
    writer     INTEGER,
    writer_epoch INTEGER,
    tokens     INTEGER NOT NULL DEFAULT 0,
    written_ns INTEGER NOT NULL,
    note       TEXT
);
CREATE INDEX IF NOT EXISTS win_hist_key ON win_hist(win, key, version);

CREATE TABLE IF NOT EXISTS win_lock(
    win          TEXT NOT NULL,
    key          TEXT NOT NULL,        -- '*' = whole-window lock (lock_all)
    mode         TEXT NOT NULL,        -- shared|exclusive
    holder       INTEGER NOT NULL,
    holder_epoch INTEGER NOT NULL,
    token        INTEGER NOT NULL,     -- monotone fencing token
    acquired_ns  INTEGER NOT NULL,
    expires_ns   INTEGER NOT NULL,
    PRIMARY KEY(win, key, holder)
);

-- --------------------------------------------------------------- objects
CREATE TABLE IF NOT EXISTS obj(
    id         TEXT PRIMARY KEY,       -- 'o:<sha256[:24]>'
    digest     TEXT NOT NULL,
    nbytes     INTEGER NOT NULL,
    tokens     INTEGER NOT NULL,
    mime       TEXT NOT NULL DEFAULT 'text/plain',
    summary    TEXT,
    schema     TEXT,
    label      TEXT,
    creator    INTEGER,
    created_ns INTEGER NOT NULL,
    path       TEXT NOT NULL,
    refcount   INTEGER NOT NULL DEFAULT 1
);

-- Cached derived views over objects (the AMPI_Type_* analogue). Deterministic
-- so that replays produce identical context costs.
CREATE TABLE IF NOT EXISTS objview(
    id         TEXT PRIMARY KEY,
    obj        TEXT NOT NULL,
    spec       TEXT NOT NULL,
    tokens     INTEGER NOT NULL,
    body       TEXT NOT NULL,
    created_ns INTEGER NOT NULL
);

-- -------------------------------------------------------------- failures
CREATE TABLE IF NOT EXISTS failure(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    epoch       INTEGER NOT NULL,
    kind        TEXT NOT NULL,   -- lease_expired|abort|killed|ctx_exhausted|...
    detected_ns INTEGER NOT NULL,
    detected_by INTEGER,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS failure_job ON failure(job, rank);

CREATE TABLE IF NOT EXISTS failure_ack(
    job    TEXT NOT NULL,
    comm   TEXT NOT NULL,
    acker  INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    ack_ns INTEGER NOT NULL,
    PRIMARY KEY(job, comm, acker, failed)
);

-- ------------------------------------------------------------- agreement
CREATE TABLE IF NOT EXISTS agree(
    id         TEXT PRIMARY KEY,
    comm       TEXT NOT NULL,
    seqno      INTEGER NOT NULL,
    state      TEXT NOT NULL DEFAULT 'open',
    created_ns INTEGER NOT NULL,
    closed_ns  INTEGER,
    result     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS agree_seq ON agree(comm, seqno);

CREATE TABLE IF NOT EXISTS agree_part(
    agree  TEXT NOT NULL,
    crank  INTEGER NOT NULL,
    flag   INTEGER NOT NULL,
    value  TEXT,
    ns     INTEGER NOT NULL,
    PRIMARY KEY(agree, crank)
);

-- ------------------------------------------------------------------ memo
-- Durable per-rank memo table used for idempotent side effects and for
-- reconstructing a replacement agent's state (durable-execution style).
CREATE TABLE IF NOT EXISTS memo(
    job     TEXT NOT NULL,
    rank    INTEGER NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    epoch   INTEGER NOT NULL DEFAULT 0,
    ns      INTEGER NOT NULL,
    PRIMARY KEY(job, rank, key)
);

-- ----------------------------------------------------------------- trace
-- Event trace, in the spirit of MPE/SLOG-2 and OTF2: enter/exit records with
-- matching arrows for point-to-point and intervals for collectives. Consumed
-- by the trace viewer and by the evaluation scripts.
CREATE TABLE IF NOT EXISTS event(
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job      TEXT NOT NULL,
    ts_ns    INTEGER NOT NULL,
    dur_ns   INTEGER NOT NULL DEFAULT 0,
    rank     INTEGER,
    epoch    INTEGER,
    kind     TEXT NOT NULL,
    phase    TEXT,                     -- enter|exit|instant
    comm     TEXT,
    peer     INTEGER,
    tag      INTEGER,
    coll     TEXT,
    win      TEXT,
    wkey     TEXT,
    msg_seq  INTEGER,
    tokens   INTEGER NOT NULL DEFAULT 0,
    nbytes   INTEGER NOT NULL DEFAULT 0,
    status   TEXT,
    detail   TEXT
);
CREATE INDEX IF NOT EXISTS event_ts ON event(job, ts_ns);
CREATE INDEX IF NOT EXISTS event_rank ON event(job, rank, ts_ns);

-- --------------------------------------------------------------- counters
CREATE TABLE IF NOT EXISTS counter(
    job   TEXT NOT NULL,
    name  TEXT NOT NULL,
    rank  INTEGER NOT NULL DEFAULT -1,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(job, name, rank)
);

-- Monotone sequence generator, used for collective sequence numbers and
-- fencing tokens.
CREATE TABLE IF NOT EXISTS seqgen(
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
"""


def now_ns() -> int:
    return time.time_ns()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Journal:
    """Durable job state. One instance per CLI invocation."""

    def __init__(self, root: Path, *, create: bool = False, job_id: Optional[str] = None):
        self.root = Path(root).resolve()
        self.dir = self.root / STATE_DIR
        self.db_path = self.dir / "journal.db"
        self.objects_dir = self.dir / "objects"
        if create:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.objects_dir.mkdir(parents=True, exist_ok=True)
        elif not self.db_path.exists():
            raise NoJobError(
                f"no AgentMPI job journal at {self.db_path}",
                hint="run `ampi run --np N ...` first, or set AMPI_ROOT to the job root",
            )
        self.conn = sqlite3.connect(str(self.db_path), timeout=60.0, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=60000")
        if create:
            self.conn.executescript(SCHEMA)
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(k,v) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO meta(k,v) VALUES('protocol',?)",
                (PROTOCOL_VERSION,),
            )
        else:
            self._migrate()
        self._job_id = job_id

    #: Columns added after the initial schema, with their definitions. Applied on
    #: open so that a job already in flight keeps working across a runtime
    #: upgrade. This is not hypothetical politeness: adding the two-phase
    #: detector's `suspect_ns` column mid-experiment broke a live 22-rank run
    #: whose agents were still executing, and the only honest fix is that a
    #: journal written by an older runtime must remain readable.
    _ADDED_COLUMNS = (
        ("rank", "suspect_ns", "INTEGER NOT NULL DEFAULT 0"),
    )

    def _migrate(self) -> None:
        for table, column, decl in self._ADDED_COLUMNS:
            try:
                cols = {
                    r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")
                }
            except sqlite3.Error:  # pragma: no cover - table absent
                continue
            if not cols or column in cols:
                continue
            with contextlib.suppress(sqlite3.Error):
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        with contextlib.suppress(sqlite3.Error):
            self.conn.execute(
                "INSERT INTO meta(k,v) VALUES('schema_version',?)"
                " ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (str(SCHEMA_VERSION),),
            )

    # ---------------------------------------------------------------- misc
    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.conn.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextlib.contextmanager
    def tx(self, *, immediate: bool = True, retries: int = 40) -> Iterator[sqlite3.Connection]:
        """Run a short transaction, retrying on SQLITE_BUSY.

        AgentMPI's correctness hinges on message matching being atomic, so every
        mutating operation goes through here with ``BEGIN IMMEDIATE``. With tens
        of agent processes on one journal, contention is real; we back off with
        jitter rather than relying on SQLite's internal busy handler alone,
        because the latter cannot retry a transaction that already began.
        """
        mode = "IMMEDIATE" if immediate else "DEFERRED"
        last: Optional[Exception] = None
        for attempt in range(retries):
            try:
                self.conn.execute(f"BEGIN {mode}")
            except sqlite3.OperationalError as exc:  # pragma: no cover - timing
                last = exc
                time.sleep(min(0.5, 0.005 * (2 ** min(attempt, 6))) * (0.5 + random.random()))
                continue
            try:
                yield self.conn
            except BaseException:
                with contextlib.suppress(Exception):
                    self.conn.execute("ROLLBACK")
                raise
            else:
                try:
                    self.conn.execute("COMMIT")
                    return
                except sqlite3.OperationalError as exc:  # pragma: no cover - timing
                    last = exc
                    with contextlib.suppress(Exception):
                        self.conn.execute("ROLLBACK")
                    time.sleep(min(0.5, 0.005 * (2 ** min(attempt, 6))) * (0.5 + random.random()))
                    continue
        raise AmpiError(
            f"journal transaction could not commit after {retries} attempts: {last}",
            err_class=ErrClass.INTERN,
            hint="the journal is under heavy contention; retry the command",
        )

    def q(self, sql: str, params: Sequence[Any] = ()) -> List[sqlite3.Row]:
        return list(self.conn.execute(sql, params))

    def q1(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(sql, params)
        return cur.fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.q1(sql, params)
        if row is None:
            return default
        return row[0]

    # ----------------------------------------------------------------- job
    @property
    def job_id(self) -> str:
        if self._job_id is None:
            jid = self.scalar("SELECT id FROM job ORDER BY created_ns DESC LIMIT 1")
            if jid is None:
                raise NoJobError("journal contains no job")
            self._job_id = str(jid)
        return self._job_id

    def job_row(self) -> sqlite3.Row:
        row = self.q1("SELECT * FROM job WHERE id=?", (self.job_id,))
        if row is None:
            raise NoJobError(f"unknown job {self.job_id}")
        return row

    def job_config(self) -> Dict[str, Any]:
        return json.loads(self.job_row()["config"])

    # ----------------------------------------------------------- sequences
    def next_seq(self, name: str, conn: Optional[sqlite3.Connection] = None) -> int:
        c = conn or self.conn
        c.execute("INSERT OR IGNORE INTO seqgen(name,value) VALUES(?,0)", (name,))
        c.execute("UPDATE seqgen SET value=value+1 WHERE name=?", (name,))
        return int(c.execute("SELECT value FROM seqgen WHERE name=?", (name,)).fetchone()[0])

    def bump(self, name: str, rank: int = -1, by: int = 1, conn: Optional[sqlite3.Connection] = None) -> None:
        c = conn or self.conn
        c.execute(
            "INSERT INTO counter(job,name,rank,value) VALUES(?,?,?,?) "
            "ON CONFLICT(job,name,rank) DO UPDATE SET value=value+excluded.value",
            (self.job_id, name, rank, by),
        )

    # ------------------------------------------------------- object store
    def put_object(
        self,
        text: str,
        *,
        creator: Optional[int] = None,
        mime: str = "text/plain",
        summary: Optional[str] = None,
        schema: Optional[str] = None,
        label: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> Dict[str, Any]:
        """Store ``text`` content-addressed and return its object record.

        Immutability + content addressing gives us three things the protocol
        needs: payload deduplication (a broadcast of the same body to 64 ranks
        stores one copy), a natural message digest for integrity, and stable
        identity across replays.
        """
        from . import tokens as tok

        data = text.encode("utf-8")
        digest = _sha256(data)
        oid = "o:" + digest[:24]
        rel = Path("objects") / digest[:2] / digest
        abs_path = self.dir / rel
        if not abs_path.exists():
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = abs_path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, abs_path)
        ntok = tok.count(text)
        c = conn or self.conn
        c.execute(
            "INSERT INTO obj(id,digest,nbytes,tokens,mime,summary,schema,label,creator,created_ns,path,refcount)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,1)"
            " ON CONFLICT(id) DO UPDATE SET refcount=refcount+1,"
            "   summary=COALESCE(excluded.summary,obj.summary),"
            "   schema=COALESCE(excluded.schema,obj.schema),"
            "   label=COALESCE(excluded.label,obj.label)",
            (oid, digest, len(data), ntok, mime, summary, schema, label, creator, now_ns(), str(rel)),
        )
        return {
            "id": oid,
            "digest": digest,
            "nbytes": len(data),
            "tokens": ntok,
            "mime": mime,
            "summary": summary,
            "schema": schema,
            "label": label,
        }

    def object_row(self, oid: str) -> sqlite3.Row:
        row = self.q1("SELECT * FROM obj WHERE id=?", (oid,))
        if row is None:
            raise AmpiError(
                f"unknown object handle {oid!r}",
                err_class=ErrClass.ARG,
                hint="handles look like o:<hex>; check the handle you were given",
            )
        return row

    def object_text(self, oid: str) -> str:
        row = self.object_row(oid)
        return (self.dir / row["path"]).read_text(encoding="utf-8")

    def object_meta(self, oid: str) -> Dict[str, Any]:
        row = self.object_row(oid)
        return {
            "id": row["id"],
            "tokens": row["tokens"],
            "nbytes": row["nbytes"],
            "digest": row["digest"],
            "summary": row["summary"],
            "schema": row["schema"],
            "label": row["label"],
            "mime": row["mime"],
        }

    # ----------------------------------------------------------- tracing
    def trace(
        self,
        kind: str,
        *,
        rank: Optional[int] = None,
        epoch: Optional[int] = None,
        phase: str = "instant",
        comm: Optional[str] = None,
        peer: Optional[int] = None,
        tag: Optional[int] = None,
        coll: Optional[str] = None,
        win: Optional[str] = None,
        wkey: Optional[str] = None,
        msg_seq: Optional[int] = None,
        tokens: int = 0,
        nbytes: int = 0,
        dur_ns: int = 0,
        status: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        ts_ns: Optional[int] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        c = conn or self.conn
        c.execute(
            "INSERT INTO event(job,ts_ns,dur_ns,rank,epoch,kind,phase,comm,peer,tag,coll,win,wkey,"
            "msg_seq,tokens,nbytes,status,detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.job_id,
                ts_ns if ts_ns is not None else now_ns(),
                dur_ns,
                rank,
                epoch,
                kind,
                phase,
                comm,
                peer,
                tag,
                coll,
                win,
                wkey,
                msg_seq,
                tokens,
                nbytes,
                status,
                json.dumps(detail, ensure_ascii=False) if detail else None,
            ),
        )


def find_root(start: Optional[str] = None) -> Path:
    """Locate the job root by walking up from ``start`` looking for ``.ampi``.

    Mirrors how ``git`` finds its repository: agents run commands from wherever
    they happen to be, and requiring them to track an absolute path is a
    reliability liability. ``AMPI_ROOT`` overrides the search.
    """
    env = os.environ.get("AMPI_ROOT")
    if env:
        return Path(env).resolve()
    cur = Path(start or os.getcwd()).resolve()
    for cand in [cur, *cur.parents]:
        if (cand / STATE_DIR / "journal.db").exists():
            return cand
    return cur


def open_journal(root: Optional[str] = None, *, create: bool = False) -> Journal:
    return Journal(find_root(root), create=create)


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]
