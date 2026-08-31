"""Reference AgentMPI device: a single-file, crash-durable SQLite transport.

Why SQLite.  The reference device has to satisfy three requirements that are in
tension: (a) many independent OS processes --- each hosting one LLM agent ---
must share it concurrently; (b) it must survive the abrupt death of any subset
of those processes, because agent death is the common case we want to study
rather than an exceptional one; and (c) a human or a post-hoc analysis script
must be able to read the entire history of a run.  SQLite in WAL mode with
``BEGIN IMMEDIATE`` write transactions satisfies all three in ~700 lines and
zero external services, which matters for artifact reproducibility.

The durability property is not incidental.  Because every message, every
collective contribution, and every window write is committed before the
operation returns, a rank that is killed mid-run loses nothing that it had
already sent, and a respawned replacement can replay its inbox.  This is
message logging in the sense of the rollback-recovery literature, and it is
what lets AgentMPI offer forward recovery rather than whole-job restart.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from .. import util
from .base import Device

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS job (
    job_id       TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    world_size   INTEGER NOT NULL,
    spec_version TEXT NOT NULL,
    created_at   REAL NOT NULL,
    roll_call_timeout REAL NOT NULL,
    state        TEXT NOT NULL DEFAULT 'running',
    meta         TEXT NOT NULL DEFAULT '{}'
);

-- One row per rank per generation.  `generation` increments when a failed rank
-- is respawned, so a stale process from generation N cannot be mistaken for
-- the live generation N+1 -- the incarnation-number trick from the
-- crash-recovery literature.
CREATE TABLE IF NOT EXISTS rank (
    job_id         TEXT NOT NULL,
    rank           INTEGER NOT NULL,
    generation     INTEGER NOT NULL DEFAULT 0,
    state          TEXT NOT NULL,
    role           TEXT,
    started_at     REAL,
    join_deadline  REAL,
    finished_at    REAL,
    last_heartbeat REAL,
    -- Application-declared liveness deadline.  A rank about to spend four
    -- minutes inside a single model turn says so, and the failure detector
    -- believes it.  Fixed timeouts cannot work when turn latency is
    -- heavy-tailed; see core/ft.py.
    hb_deadline    REAL,
    ctx_limit      INTEGER NOT NULL,
    ctx_used       INTEGER NOT NULL DEFAULT 0,
    ctx_peak       INTEGER NOT NULL DEFAULT 0,
    -- How many times this rank has been wrongly condemned.  The failure
    -- detector widens its timeout for a rank in proportion, which is what
    -- stops a slow-but-healthy agent oscillating between alive and failed.
    suspicions     INTEGER NOT NULL DEFAULT 0,
    retractions    INTEGER NOT NULL DEFAULT 0,
    -- 0 when a timeout merely suspects this rank, 1 when its death is known
    -- (administratively killed, or finalized and then addressed).  Only a
    -- confirmed death may fail a peer's operation; a suspicion must not,
    -- because suspicions are routinely wrong.
    failure_confirmed INTEGER NOT NULL DEFAULT 0,
    -- Bumped by every AMPI_Init. A long-running operation captures the value
    -- it started under and aborts if it changes, so a process left over from
    -- an earlier attempt cannot go on acting as a rank that somebody else has
    -- since taken over. This is the fencing-token pattern.
    incarnation    INTEGER NOT NULL DEFAULT 0,
    tokens_sent    INTEGER NOT NULL DEFAULT 0,
    tokens_recvd   INTEGER NOT NULL DEFAULT 0,
    exit_note      TEXT,
    meta           TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, rank)
);

-- A communicator is a member list plus an isolated context id.  Two
-- communicators over the same members cannot intercept each other's messages,
-- which is what makes an AgentMPI *library* composable with user agent code.
CREATE TABLE IF NOT EXISTS comm (
    comm_id    TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    name       TEXT NOT NULL,
    context_id INTEGER NOT NULL,
    members    TEXT NOT NULL,
    parent     TEXT,
    topology   TEXT,
    revoked    INTEGER NOT NULL DEFAULT 0,
    revoked_by INTEGER,
    revoked_at REAL,
    created_at REAL NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS comm_name_uq ON comm(job_id, name);

CREATE TABLE IF NOT EXISTS message (
    msg_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    comm_id    TEXT NOT NULL,
    src        INTEGER NOT NULL,
    dst        INTEGER NOT NULL,
    tag        INTEGER NOT NULL,
    seq        INTEGER NOT NULL,
    mode       TEXT NOT NULL,
    body       TEXT,
    handle     TEXT,
    digest     TEXT,
    tokens     INTEGER NOT NULL DEFAULT 0,
    state      TEXT NOT NULL DEFAULT 'posted',
    sent_at    REAL NOT NULL,
    matched_at REAL,
    claimant   TEXT,
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS message_match_idx
    ON message(comm_id, dst, state, seq);
CREATE INDEX IF NOT EXISTS message_src_idx ON message(comm_id, src, dst, tag);

-- Posted receives.  Kept durable so that a blocked rank is visible to the
-- deadlock detector and to the failure detector, and so that `ampi wait` can
-- resume across a CLI process restart.
CREATE TABLE IF NOT EXISTS request (
    req_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    comm_id      TEXT NOT NULL,
    owner        INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    src          INTEGER NOT NULL,
    tag          INTEGER NOT NULL,
    state        TEXT NOT NULL DEFAULT 'posted',
    msg_id       INTEGER,
    posted_at    REAL NOT NULL,
    completed_at REAL,
    meta         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS request_owner_idx ON request(job_id, owner, state);

-- The artifact store.  Rendezvous payloads and window cells both point here.
-- Content is addressed by handle and hashed, so a rank can cheaply verify that
-- an artifact it saw earlier has not changed.
CREATE TABLE IF NOT EXISTS object (
    handle     TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    content    TEXT,
    path       TEXT,
    sha256     TEXT NOT NULL,
    tokens     INTEGER NOT NULL,
    digest     TEXT,
    created_by INTEGER,
    created_at REAL NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS win (
    win_id     TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    comm_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    model      TEXT NOT NULL,
    created_at REAL NOT NULL,
    meta       TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS win_name_uq ON win(job_id, name);

CREATE TABLE IF NOT EXISTS win_cell (
    win_id     TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT,
    handle     TEXT,
    version    INTEGER NOT NULL DEFAULT 0,
    owner      INTEGER,
    updated_by INTEGER,
    updated_at REAL,
    tokens     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (win_id, key)
);

CREATE TABLE IF NOT EXISTS win_lock (
    lock_id     TEXT PRIMARY KEY,
    win_id      TEXT NOT NULL,
    key         TEXT NOT NULL,
    holder      INTEGER NOT NULL,
    mode        TEXT NOT NULL,
    acquired_at REAL NOT NULL,
    expires_at  REAL NOT NULL,
    released_at REAL
);
CREATE INDEX IF NOT EXISTS win_lock_active_idx ON win_lock(win_id, key, released_at);

CREATE TABLE IF NOT EXISTS coll (
    coll_id       TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    comm_id       TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    op            TEXT NOT NULL,
    root          INTEGER,
    algo          TEXT,
    op_name       TEXT,
    state         TEXT NOT NULL DEFAULT 'open',
    expected      INTEGER NOT NULL,
    created_at    REAL NOT NULL,
    completed_at  REAL,
    result        TEXT,
    result_handle TEXT,
    meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS coll_seq_uq ON coll(comm_id, seq);

CREATE TABLE IF NOT EXISTS coll_contrib (
    coll_id   TEXT NOT NULL,
    rank      INTEGER NOT NULL,
    body      TEXT,
    handle    TEXT,
    tokens    INTEGER NOT NULL DEFAULT 0,
    arrived_at REAL NOT NULL,
    PRIMARY KEY (coll_id, rank)
);

-- Suspended semantic reduction steps awaiting an LLM upcall.
CREATE TABLE IF NOT EXISTS pending_op (
    op_token   TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    coll_id    TEXT,
    assignee   INTEGER NOT NULL,
    op_name    TEXT NOT NULL,
    step       INTEGER NOT NULL DEFAULT 0,
    operands   TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'pending',
    result     TEXT,
    created_at REAL NOT NULL,
    settled_at REAL,
    meta       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS failure (
    failure_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    generation  INTEGER NOT NULL DEFAULT 0,
    detected_at REAL NOT NULL,
    detected_by INTEGER,
    reason      TEXT NOT NULL,
    acked_by    TEXT NOT NULL DEFAULT '[]',
    meta        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS checkpoint (
    ckpt_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    rank       INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    label      TEXT,
    state      TEXT NOT NULL,
    tokens     INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS checkpoint_rank_idx ON checkpoint(job_id, rank, ckpt_id);

-- The PAMPI trace.  Every protocol call emits enter/exit records; the whole
-- evaluation section of the paper is computed from this one table.
CREATE TABLE IF NOT EXISTS event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id   TEXT NOT NULL,
    rank     INTEGER,
    ts       REAL NOT NULL,
    op       TEXT NOT NULL,
    phase    TEXT NOT NULL,
    comm_id  TEXT,
    peer     INTEGER,
    tag      INTEGER,
    tokens   INTEGER NOT NULL DEFAULT 0,
    dur      REAL,
    ok       INTEGER NOT NULL DEFAULT 1,
    err      TEXT,
    meta     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS event_job_idx ON event(job_id, event_id);
CREATE INDEX IF NOT EXISTS event_rank_idx ON event(job_id, rank, ts);

CREATE TABLE IF NOT EXISTS counter (
    job_id TEXT NOT NULL,
    name   TEXT NOT NULL,
    value  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (job_id, name)
);

CREATE TABLE IF NOT EXISTS kv (
    job_id TEXT NOT NULL,
    key    TEXT NOT NULL,
    value  TEXT,
    PRIMARY KEY (job_id, key)
);
"""


class SqliteDevice(Device):
    """SQLite-backed AgentMPI device."""

    name = "sqlite"

    def __init__(self, path: str, timeout: float = 60.0) -> None:
        self.path = os.path.abspath(path)
        self.timeout = timeout
        self._conn: sqlite3.Connection | None = None
        self._depth = 0

    # -- connection --------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._conn = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
        return self._conn

    def initialize(self) -> None:
        # executescript() implicitly commits, so it must not run inside a
        # transaction of ours.  Concurrent initialisation is safe because every
        # statement in SCHEMA is CREATE ... IF NOT EXISTS.
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a job database was first created.

        Jobs outlive releases here: a run that is halfway through must not be
        invalidated because the runtime grew a field.
        """
        job_columns = {r["name"] for r in self.query("PRAGMA table_info(job)")}
        for column, ddl in (
            ("run_id", "ALTER TABLE job ADD COLUMN run_id TEXT NOT NULL DEFAULT ''"),
            (
                "roll_call_timeout",
                "ALTER TABLE job ADD COLUMN roll_call_timeout REAL NOT NULL DEFAULT 3600",
            ),
        ):
            if column not in job_columns:
                try:
                    self.conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
        for row in self.query("SELECT job_id FROM job WHERE run_id=''"):
            self.conn.execute(
                "UPDATE job SET run_id=? WHERE job_id=?",
                (util.new_id("run"), row["job_id"]),
            )

        rank_columns = {r["name"] for r in self.query("PRAGMA table_info(rank)")}
        for column, ddl in (
            ("suspicions", "ALTER TABLE rank ADD COLUMN suspicions INTEGER NOT NULL DEFAULT 0"),
            ("retractions", "ALTER TABLE rank ADD COLUMN retractions INTEGER NOT NULL DEFAULT 0"),
            ("failure_confirmed",
             "ALTER TABLE rank ADD COLUMN failure_confirmed INTEGER NOT NULL DEFAULT 0"),
            ("incarnation",
             "ALTER TABLE rank ADD COLUMN incarnation INTEGER NOT NULL DEFAULT 0"),
            ("join_deadline", "ALTER TABLE rank ADD COLUMN join_deadline REAL"),
        ):
            if column not in rank_columns:
                try:
                    self.conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass

        message_columns = {r["name"] for r in self.query("PRAGMA table_info(message)")}
        if "run_id" not in message_columns:
            try:
                self.conn.execute(
                    "ALTER TABLE message ADD COLUMN run_id TEXT NOT NULL DEFAULT ''"
                )
            except sqlite3.OperationalError:
                pass
        self.conn.execute(
            "UPDATE message SET run_id=("
            "SELECT job.run_id FROM job WHERE job.job_id=message.job_id"
            ") WHERE run_id=''"
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def write_tx(self) -> Iterator[sqlite3.Connection]:
        """A serialized write transaction.

        ``BEGIN IMMEDIATE`` takes the write lock up front rather than on first
        write, which converts SQLITE_BUSY-on-upgrade deadlocks into a clean
        wait.  With up to a hundred agent processes contending, that matters.

        Re-entrant: an inner call joins the outer transaction rather than
        opening a nested one, so a protocol operation composed of several
        device operations still commits atomically.
        """
        if self._depth > 0:
            self._depth += 1
            try:
                yield self.conn
            finally:
                self._depth -= 1
            return
        deadline = time.time() + self.timeout
        while True:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(0.02)
        self._depth = 1
        try:
            yield self.conn
        except BaseException:
            self._depth = 0
            self.conn.execute("ROLLBACK")
            raise
        else:
            self._depth = 0
            self.conn.execute("COMMIT")

    # -- generic record access --------------------------------------------
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    # -- capability 1: append ---------------------------------------------
    def append(self, stream: str, record: dict[str, Any]) -> int | str:
        cols = ",".join(record.keys())
        marks = ",".join("?" for _ in record)
        cur = self.conn.execute(
            f"INSERT INTO {stream} ({cols}) VALUES ({marks})", tuple(record.values())
        )
        return record.get("handle") or record.get("coll_id") or cur.lastrowid

    # -- capability 2: match ----------------------------------------------
    def match(
        self,
        stream: str,
        predicate: dict[str, Any],
        claimant: str,
        order_by: str = "seq",
    ) -> dict[str, Any] | None:
        """First-fit atomic claim.

        The select and the claiming update must be one transaction, not two
        statements.  In autocommit mode two ranks can both read the same
        ``posted`` row before either writes, and both then believe they own it
        --- precisely the duplicated-work failure mode that ad-hoc agent
        harnesses hit.  ``write_tx`` is re-entrant, so this is still a single
        transaction when the caller has already opened one.
        """
        where, params = _where(predicate)
        with self.write_tx():
            row = self.query_one(
                f"SELECT * FROM {stream} WHERE {where} ORDER BY {order_by} ASC LIMIT 1", params
            )
            if row is None:
                return None
            self.conn.execute(
                f"UPDATE {stream} SET state='matched', matched_at=?, claimant=? WHERE msg_id=?",
                (util.now(), claimant, row["msg_id"]),
            )
            row["state"] = "matched"
            row["claimant"] = claimant
            return row

    # -- capability 3: compare-and-swap ------------------------------------
    def cas(
        self,
        cell: str,
        key: str,
        expected_version: int | None,
        value: Any,
        actor: int,
    ) -> tuple[bool, int, Any]:
        # Read-compare-write must be one transaction or the compare is
        # meaningless: two writers would both observe the same version.
        with self.write_tx():
            row = self.query_one("SELECT * FROM win_cell WHERE win_id=? AND key=?", (cell, key))
            current_version = row["version"] if row else 0
            current_value = util.loads(row["value"], row["value"]) if row else None
            if expected_version is not None and current_version != expected_version:
                return (False, current_version, current_value)
            new_version = current_version + 1
            encoded = util.dumps(value)
            if row:
                self.conn.execute(
                    "UPDATE win_cell SET value=?, version=?, updated_by=?, updated_at=?, "
                    "tokens=? WHERE win_id=? AND key=?",
                    (encoded, new_version, actor, util.now(), util.count_tokens(encoded),
                     cell, key),
                )
            else:
                self.conn.execute(
                    "INSERT INTO win_cell (win_id, key, value, version, owner, updated_by, "
                    "updated_at, tokens) VALUES (?,?,?,?,?,?,?,?)",
                    (cell, key, encoded, new_version, actor, actor, util.now(),
                     util.count_tokens(encoded)),
                )
            return (True, new_version, value)

    # -- capability 4: lease ----------------------------------------------
    def lease(
        self,
        cell: str,
        key: str,
        holder: int,
        mode: str,
        ttl: float,
    ) -> str | None:
        """Acquire a shared or exclusive lease, expiring stale holders.

        Leases rather than locks: an agent that dies holding an exclusive lock
        must not wedge the job forever.  TTL expiry is the failure detector of
        last resort, and it is why AgentMPI locks are always revocable.
        """
        with self.write_tx():
            now_ts = util.now()
            self.conn.execute(
                "UPDATE win_lock SET released_at=? WHERE win_id=? AND key=? "
                "AND released_at IS NULL AND expires_at < ?",
                (now_ts, cell, key, now_ts),
            )
            active = self.query(
                "SELECT * FROM win_lock WHERE win_id=? AND key=? AND released_at IS NULL",
                (cell, key),
            )
            if active:
                blocking = any(a["mode"] == "exclusive" for a in active) or mode == "exclusive"
                if blocking and not all(a["holder"] == holder for a in active):
                    return None
            lock_id = util.new_id("lk")
            self.conn.execute(
                "INSERT INTO win_lock (lock_id, win_id, key, holder, mode, acquired_at, "
                "expires_at) VALUES (?,?,?,?,?,?,?)",
                (lock_id, cell, key, holder, mode, now_ts, now_ts + ttl),
            )
            return lock_id

    def release(self, lock_id: str, holder: int) -> bool:
        cur = self.conn.execute(
            "UPDATE win_lock SET released_at=? WHERE lock_id=? AND holder=? AND released_at IS NULL",
            (util.now(), lock_id, holder),
        )
        return cur.rowcount > 0

    # -- capability 5: scan ------------------------------------------------
    def scan(self, stream: str, predicate: dict[str, Any]) -> list[dict[str, Any]]:
        where, params = _where(predicate)
        return self.query(f"SELECT * FROM {stream} WHERE {where}", params)

    # -- capability 6: clock ----------------------------------------------
    def clock(self) -> float:
        return util.now()

    # -- convenience -------------------------------------------------------
    def counter_next(self, job_id: str, name: str) -> int:
        with self.write_tx():
            self.conn.execute(
                "INSERT INTO counter (job_id, name, value) VALUES (?,?,0) "
                "ON CONFLICT(job_id, name) DO NOTHING",
                (job_id, name),
            )
            self.conn.execute(
                "UPDATE counter SET value = value + 1 WHERE job_id=? AND name=?", (job_id, name)
            )
            row = self.query_one(
                "SELECT value FROM counter WHERE job_id=? AND name=?", (job_id, name)
            )
            return int(row["value"]) if row else 0

    def kv_get(self, job_id: str, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM kv WHERE job_id=? AND key=?", (job_id, key))
        return util.loads(row["value"], default) if row else default

    def kv_set(self, job_id: str, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT INTO kv (job_id, key, value) VALUES (?,?,?) "
            "ON CONFLICT(job_id, key) DO UPDATE SET value=excluded.value",
            (job_id, key, util.dumps(value)),
        )


def _where(predicate: dict[str, Any]) -> tuple[str, list[Any]]:
    """Translate a predicate dict into SQL.

    Supported forms: ``k: v`` (equality), ``k: ("in", [...])``,
    ``k: ("!=", v)``, ``k: ("<", v)``, ``k: ("is", None)``.
    """
    clauses: list[str] = []
    params: list[Any] = []
    for key, val in predicate.items():
        if isinstance(val, tuple) and len(val) == 2:
            operator, operand = val
            if operator == "in":
                if not operand:
                    clauses.append("0=1")
                    continue
                marks = ",".join("?" for _ in operand)
                clauses.append(f"{key} IN ({marks})")
                params.extend(operand)
            elif operator == "is" and operand is None:
                clauses.append(f"{key} IS NULL")
            else:
                clauses.append(f"{key} {operator} ?")
                params.append(operand)
        elif val is None:
            clauses.append(f"{key} IS NULL")
        else:
            clauses.append(f"{key} = ?")
            params.append(val)
    return (" AND ".join(clauses) if clauses else "1=1", params)
