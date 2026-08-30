"""The AgentMPI fabric: durable protocol state outside the agents.

Design rationale
----------------
In MPI the library is linked into the application process, so protocol state
(posted receives, unexpected messages, window contents, communicator contexts)
lives in the process's own memory and is exactly as reliable as the process.
That assumption fails for agent ranks twice over: an agent's working memory is
a lossy context window that is compacted and truncated, and an agent instance
routinely disappears mid-computation.  A protocol whose state lived in the
agent would lose the state whenever the agent forgot or died.

AgentMPI therefore takes MPI's *opaque object* principle to its conclusion: an
agent holds only **handles** — a rank number, a communicator id, a window name,
a blob digest — and every byte of protocol state lives in an external fabric
with ACID semantics.  A rank can be killed between any two calls and rebuilt
from the fabric alone.

The reference fabric is SQLite in WAL mode plus the content-addressed blob
store.  SQLite is not a performance choice; it is a *correctness* choice.  It
gives multi-process atomic transactions, crash consistency, and blocking
locks, which is precisely the set of guarantees the protocol needs, with zero
operational surface.  The same interface is implementable over Postgres or
etcd; ``docs/spec`` specifies the required guarantees rather than the backend.

Concurrency discipline
----------------------
* All writes go through :meth:`Fabric.write`, which opens ``BEGIN IMMEDIATE``
  so writers serialise without deadlocking.
* ``busy_timeout`` is set high, and :meth:`Fabric.write` retries on
  ``SQLITE_BUSY`` with jittered backoff, because dozens of agent ranks poll
  concurrently.
* Every state transition appends to ``events``, giving a total order over the
  run that the trace viewer and the replayer both consume.
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .constants import WORLD_CTX
from .errors import AmpiFabricError
from .store import BlobStore

SCHEMA_VERSION = 3

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Ranks are durable roles.  `incarnation` counts how many agent instances have
-- served the role; `lease_expires` is the failure detector's input.
CREATE TABLE IF NOT EXISTS ranks (
    rank            INTEGER PRIMARY KEY,
    name            TEXT,
    state           TEXT NOT NULL,
    incarnation     INTEGER NOT NULL DEFAULT 0,
    executor        TEXT,
    lease_expires   REAL NOT NULL DEFAULT 0,
    last_seen       REAL NOT NULL DEFAULT 0,
    context_budget  INTEGER NOT NULL,
    eager_limit     INTEGER NOT NULL DEFAULT 2048,
    unexpected_limit INTEGER NOT NULL DEFAULT 16384,
    context_used    INTEGER NOT NULL DEFAULT 0,
    context_high    INTEGER NOT NULL DEFAULT 0,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    n_calls         INTEGER NOT NULL DEFAULT 0,
    n_compactions   INTEGER NOT NULL DEFAULT 0,
    meta            TEXT NOT NULL DEFAULT '{}'
);

-- A communicator is (group, context).  `ctx` is the context id; it is what
-- keeps a library's collectives from matching a harness's point-to-point
-- traffic, which is the problem MPI's communicators were invented to solve.
CREATE TABLE IF NOT EXISTS comms (
    ctx         INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_ctx  INTEGER,
    kind        TEXT NOT NULL DEFAULT 'intra',
    generation  INTEGER NOT NULL DEFAULT 0,   -- bumped by shrink
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS comm_members (
    ctx        INTEGER NOT NULL,
    crank      INTEGER NOT NULL,   -- rank *within* the communicator
    wrank      INTEGER NOT NULL,   -- rank in the world communicator
    state      TEXT NOT NULL DEFAULT 'active',
    PRIMARY KEY (ctx, crank)
);
CREATE INDEX IF NOT EXISTS idx_members_w ON comm_members(ctx, wrank);

-- Point-to-point messages.  `mid` is a global monotone id; `seq` is per
-- (ctx, src, dst) and enforces the non-overtaking guarantee.
CREATE TABLE IF NOT EXISTS messages (
    mid        INTEGER PRIMARY KEY AUTOINCREMENT,
    ctx        INTEGER NOT NULL,
    src        INTEGER NOT NULL,
    dst        INTEGER NOT NULL,
    tag        TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    mode       TEXT NOT NULL,
    contract   TEXT,
    digest     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    tokens     INTEGER NOT NULL,
    nbytes     INTEGER NOT NULL,
    synopsis   TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL,
    sent_at    REAL NOT NULL,
    recv_at    REAL,
    req        TEXT,
    ack        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_msg_match ON messages(ctx, dst, state, tag, src, seq);
CREATE INDEX IF NOT EXISTS idx_msg_req ON messages(req);

CREATE TABLE IF NOT EXISTS seqs (
    ctx  INTEGER NOT NULL,
    src  INTEGER NOT NULL,
    dst  INTEGER NOT NULL,
    next INTEGER NOT NULL,
    PRIMARY KEY (ctx, src, dst)
);

-- Collective operations.  Each rank inserts one contribution row; the
-- algorithm itself runs over point-to-point messages, so this table is used
-- for the rendezvous/agreement collectives (barrier, agree) and for tracing.
CREATE TABLE IF NOT EXISTS collectives (
    cid        INTEGER PRIMARY KEY AUTOINCREMENT,
    ctx        INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    epoch      INTEGER NOT NULL,   -- per-communicator collective sequence number
    op         TEXT NOT NULL,
    algorithm  TEXT,
    root       INTEGER,
    state      TEXT NOT NULL,
    opened_at  REAL NOT NULL,
    closed_at  REAL,
    params     TEXT NOT NULL DEFAULT '{}',
    UNIQUE (ctx, generation, epoch, op)
);

CREATE TABLE IF NOT EXISTS coll_parts (
    cid        INTEGER NOT NULL,
    crank      INTEGER NOT NULL,
    digest     TEXT,
    tokens     INTEGER NOT NULL DEFAULT 0,
    arrived_at REAL NOT NULL,
    PRIMARY KEY (cid, crank)
);

-- RMA windows: the shared blackboard.  A window is a keyed map; a slot is the
-- unit of locking and versioning.
CREATE TABLE IF NOT EXISTS windows (
    name       TEXT PRIMARY KEY,
    ctx        INTEGER NOT NULL,
    model      TEXT NOT NULL,
    created_at REAL NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS win_slots (
    win        TEXT NOT NULL,
    slot       TEXT NOT NULL,
    digest     TEXT,
    kind       TEXT NOT NULL DEFAULT 'json',
    version    INTEGER NOT NULL DEFAULT 0,
    tokens     INTEGER NOT NULL DEFAULT 0,
    updated_by INTEGER,
    updated_at REAL NOT NULL,
    PRIMARY KEY (win, slot)
);

CREATE TABLE IF NOT EXISTS win_locks (
    win        TEXT NOT NULL,
    slot       TEXT NOT NULL,
    holder     INTEGER NOT NULL,
    token      TEXT NOT NULL,
    mode       TEXT NOT NULL,
    acquired   REAL NOT NULL,
    expires    REAL NOT NULL,
    PRIMARY KEY (win, slot, holder)
);

-- What each rank last observed for each slot: the SEPARATE memory model's
-- private copy, made explicit so staleness is measurable.
CREATE TABLE IF NOT EXISTS win_views (
    win     TEXT NOT NULL,
    slot    TEXT NOT NULL,
    rank    INTEGER NOT NULL,
    version INTEGER NOT NULL,
    seen_at REAL NOT NULL,
    PRIMARY KEY (win, slot, rank)
);

CREATE TABLE IF NOT EXISTS failures (
    ctx        INTEGER NOT NULL,
    rank       INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    detected_at REAL NOT NULL,
    acked      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ctx, rank, kind)
);

-- Agent invocations.  The fabric brokers them so that an external process
-- manager (mpiexec's analogue) can serve them with whatever executor it likes.
CREATE TABLE IF NOT EXISTS agent_calls (
    aid         INTEGER PRIMARY KEY AUTOINCREMENT,
    rank        INTEGER NOT NULL,
    ctx         INTEGER NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'task',
    label       TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL,
    prompt_digest TEXT NOT NULL,
    result_digest TEXT,
    contract    TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    result_tokens INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    claimed_at  REAL,
    finished_at REAL,
    incarnation INTEGER NOT NULL DEFAULT 0,
    attempt     INTEGER NOT NULL DEFAULT 1,
    error       TEXT,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_calls_state ON agent_calls(state, rank);

CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);

-- The total order over the run.  Everything else can be rebuilt from this.
CREATE TABLE IF NOT EXISTS events (
    eid     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    rank    INTEGER,
    ctx     INTEGER,
    kind    TEXT NOT NULL,
    phase   TEXT,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_rank ON events(rank, ts);
"""


class Fabric:
    """Handle on one AgentMPI job's durable state.

    A :class:`Fabric` is cheap to construct and safe to construct many times in
    many processes over the same directory.  Connections are thread-local
    because SQLite connections are not thread-safe by default and AgentMPI runs
    one driver thread per rank in the in-process executor.
    """

    def __init__(self, root: str | os.PathLike[str], *, create: bool = False, timeout: float = 60.0) -> None:
        self.root = Path(root).resolve()
        self.db_path = self.root / "fabric.sqlite"
        self.blobs = BlobStore(self.root / "blobs")
        self.timeout = timeout
        self._local = threading.local()
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
            self._init_schema()
        elif not self.db_path.exists():
            raise AmpiFabricError("no fabric at path (did you run `ampi init`?)", root=str(self.root))

    # ---------------------------------------------------------------- plumbing

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=self.timeout, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _init_schema(self) -> None:
        conn = self.conn
        conn.executescript(_SCHEMA)
        with self.write() as cur:
            cur.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )
            cur.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
                ("job_id", uuid.uuid4().hex),
            )
            cur.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
                ("created_at", repr(time.time())),
            )
            cur.execute("INSERT OR IGNORE INTO counters(name, value) VALUES('ctx', ?)", (WORLD_CTX,))

    @contextmanager
    def write(self, *, retries: int = 40) -> Iterator[sqlite3.Cursor]:
        """Serialised write transaction with jittered retry on contention.

        ``BEGIN IMMEDIATE`` takes the write lock up front rather than upgrading
        mid-transaction, which is what prevents the deadlock-prone
        read-then-upgrade pattern under many concurrent ranks.
        """
        conn = self.conn
        last: Exception | None = None
        for attempt in range(retries):
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:  # database is locked
                last = exc
                time.sleep(min(0.5, 0.01 * (2**min(attempt, 6))) * (0.5 + random.random()))
                continue
            cur = conn.cursor()
            try:
                yield cur
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return
        raise AmpiFabricError("could not acquire fabric write lock", cause=repr(last))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)))

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        cur = self.conn.execute(sql, tuple(params))
        return cur.fetchone()

    # -------------------------------------------------------------------- meta

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.query_one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.write() as cur:
            cur.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    @property
    def job_id(self) -> str:
        return self.get_meta("job_id") or "unknown"

    # ---------------------------------------------------------------- counters

    def next_counter(self, name: str, *, cur: sqlite3.Cursor | None = None) -> int:
        """Atomically increment and return a named counter.

        Used for context-id allocation.  In MPI, agreeing on a fresh context id
        during ``MPI_Comm_dup`` requires a distributed allreduce over a bit
        vector of free ids; here the fabric is a shared serialisation point, so
        the same guarantee costs one row update.  The spec records the weaker
        requirement (a monotone unique-id service) rather than the mechanism.
        """
        if cur is None:
            with self.write() as c:
                return self.next_counter(name, cur=c)
        cur.execute("INSERT INTO counters(name,value) VALUES(?,0) ON CONFLICT(name) DO NOTHING", (name,))
        cur.execute("UPDATE counters SET value = value + 1 WHERE name=?", (name,))
        row = cur.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()
        return int(row["value"])

    # ------------------------------------------------------------------ events

    def emit(
        self,
        kind: str,
        *,
        rank: int | None = None,
        ctx: int | None = None,
        phase: str | None = None,
        cur: sqlite3.Cursor | None = None,
        **payload: Any,
    ) -> None:
        """Append a trace event.

        Tracing is unconditional and in the same transaction as the state
        change it describes.  MPI's tooling interface (MPI_T, PMPI) is opt-in
        and out-of-band; AgentMPI makes the trace part of the protocol because
        the trace *is* the artifact a harness author debugs against, and
        because an agent run cannot be cheaply re-executed to reproduce a bug.
        """
        row = (time.time(), rank, ctx, kind, phase, json.dumps(payload, default=str, ensure_ascii=False))
        sql = "INSERT INTO events(ts, rank, ctx, kind, phase, payload) VALUES(?,?,?,?,?,?)"
        if cur is not None:
            cur.execute(sql, row)
        else:
            with self.write() as c:
                c.execute(sql, row)

    def events(self, *, since: int = 0, kinds: Sequence[str] | None = None, limit: int = 100_000) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events WHERE eid > ?"
        params: list[Any] = [since]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            params.extend(kinds)
        sql += " ORDER BY eid LIMIT ?"
        params.append(limit)
        out = []
        for row in self.query(sql, params):
            d = dict(row)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out


def open_fabric(root: str | os.PathLike[str] | None = None, *, create: bool = False) -> Fabric:
    """Open the fabric named by ``root`` or by ``$AMPI_ROOT``."""
    if root is None:
        root = os.environ.get("AMPI_ROOT")
    if root is None:
        raise AmpiFabricError("no fabric root given and $AMPI_ROOT is unset")
    return Fabric(root, create=create)
