"""The SQLite device: the reference transport.

A single file, WAL mode, one short transaction per operation.  It is the default
because it gives the two properties the protocol needs and a filesystem alone does
not: a total order over appends that survives concurrent writers, and genuine
compare-and-swap.  It is also inspectable --- when a twelve-rank job wedges, being
able to open the journal in ``sqlite3`` and ask which rank has not joined the
barrier is worth more than any amount of logging.

Concurrency model.  Every rank is a separate OS process (often a separate agent
turn) hitting the same file.  Writes take ``BEGIN IMMEDIATE`` so that two ranks
claiming the same message serialise at the database rather than racing in Python,
and ``busy_timeout`` is generous because an executor blocked for 200 ms is
invisible next to an executor thinking for 40 s.

Schema evolution is handled on open.  A long agent job outlives the runtime it
started under --- we added a column to a live 22-rank job --- so ``_migrate``
adds missing columns with ``ALTER TABLE`` rather than requiring a clean start.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import (
    STREAMS,
    Cell,
    Device,
    Ge,
    Gt,
    In,
    IsNull,
    Le,
    Lt,
    Ne,
    NotIn,
    NotNull,
    Predicate,
    matches,
    register_device,
)

__all__ = ["SqliteDevice"]

_RESERVED = {"seq", "ts", "body"}


def _table(stream: str) -> str:
    return f"s_{stream}"


@register_device
class SqliteDevice(Device):
    name = "sqlite"
    durable = True

    def __init__(self, root: str | os.PathLike[str], *, busy_timeout_ms: int = 60_000) -> None:
        self.root = Path(root)
        self.path = self.root / "journal.db"
        self._busy_timeout_ms = busy_timeout_ms
        # One connection per thread.  A ``sqlite3.Connection`` may not cross
        # threads, and while a rank is normally a whole OS process, harness-side
        # drivers run ranks as threads and the conformance suite exercises
        # concurrent claimants directly.  Per-thread connections make both work
        # without weakening isolation: two threads contending for the same write
        # serialise at the database exactly as two processes would.
        self._local = threading.local()
        self._appends = 0
        self._matches = 0
        self._cas = 0

    # -- connection --------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.root.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                self.path, timeout=self._busy_timeout_ms / 1000, isolation_level=None
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            self._local.conn = conn
            self._local.depth = 0
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """A serialised write transaction; re-entrant so callers may nest."""
        conn = self.conn
        if getattr(self._local, "depth", 0):
            yield conn
            return
        self._local.depth = 1
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            self._local.depth = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._write():
            yield

    # -- lifecycle ---------------------------------------------------------
    def initialize(self) -> None:
        with self._write() as conn:
            for stream, fields in STREAMS.items():
                cols = ", ".join(f"{f} " + ("INTEGER" if f in _INT_FIELDS else "TEXT") for f in fields)
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {_table(stream)} ("
                    " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " ts REAL NOT NULL,"
                    f" {cols},"
                    " body TEXT NOT NULL)"
                )
                for f in fields:
                    conn.execute(
                        f"CREATE INDEX IF NOT EXISTS ix_{stream}_{f} ON {_table(stream)}({f})"
                    )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cells ("
                " space TEXT NOT NULL, key TEXT NOT NULL, version INTEGER NOT NULL,"
                " value TEXT, writer INTEGER NOT NULL, epoch INTEGER NOT NULL DEFAULT 0,"
                " ts REAL NOT NULL, meta TEXT NOT NULL DEFAULT '{}',"
                " PRIMARY KEY (space, key, version))"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_cells_cur ON cells(space, key, version DESC)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS locks ("
                " lock_id TEXT PRIMARY KEY, space TEXT NOT NULL, key TEXT NOT NULL,"
                " holder INTEGER NOT NULL, mode TEXT NOT NULL, token INTEGER NOT NULL,"
                " acquired_at REAL NOT NULL, expires_at REAL NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_locks_cell ON locks(space, key)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS fence ("
                " space TEXT NOT NULL, key TEXT NOT NULL, token INTEGER NOT NULL,"
                " PRIMARY KEY (space, key))"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS obj (digest TEXT PRIMARY KEY, body TEXT NOT NULL)")
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns a newer runtime expects, without disturbing a live job.

        A twenty-two rank job that has been running for forty minutes cannot be
        restarted to pick up a schema change, and the alternative --- refusing to
        open --- loses the run.  ``ALTER TABLE ADD COLUMN`` is cheap in SQLite and
        leaves existing rows readable.
        """
        for stream, fields in STREAMS.items():
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({_table(stream)})")}
            for f in fields:
                if f not in have:
                    kind = "INTEGER" if f in _INT_FIELDS else "TEXT"
                    conn.execute(f"ALTER TABLE {_table(stream)} ADD COLUMN {f} {kind}")
                    conn.execute(
                        f"CREATE INDEX IF NOT EXISTS ix_{stream}_{f} ON {_table(stream)}({f})"
                    )

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def wipe(self) -> None:
        self.close()
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                p.unlink()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _split(stream: str, record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        indexed = STREAMS[stream]
        cols = {f: record.get(f) for f in indexed}
        body = {k: v for k, v in record.items() if k not in indexed and k not in _RESERVED}
        return cols, body

    @staticmethod
    def _row(stream: str, row: sqlite3.Row) -> dict[str, Any]:
        out: dict[str, Any] = json.loads(row["body"])
        for f in STREAMS[stream]:
            out[f] = row[f]
        out["seq"] = row["seq"]
        out["ts"] = row["ts"]
        return out

    @staticmethod
    def _compile(stream: str, predicate: Predicate) -> tuple[str, list[Any], Predicate]:
        """Compile the indexed part of a predicate to SQL; return the residue."""
        indexed = set(STREAMS[stream])
        clauses: list[str] = []
        args: list[Any] = []
        residue: Predicate = {}
        for key, want in predicate.items():
            if key not in indexed and key not in ("seq", "ts"):
                residue[key] = want
                continue
            if isinstance(want, In):
                if not want.values:
                    return "0", [], {}
                clauses.append(f"{key} IN ({','.join('?' * len(want.values))})")
                args.extend(want.values)
            elif isinstance(want, NotIn):
                if want.values:
                    clauses.append(f"({key} IS NULL OR {key} NOT IN ({','.join('?' * len(want.values))}))")
                    args.extend(want.values)
            elif isinstance(want, Ne):
                clauses.append(f"({key} IS NULL OR {key} <> ?)")
                args.append(want.value)
            elif isinstance(want, IsNull):
                clauses.append(f"{key} IS NULL")
            elif isinstance(want, NotNull):
                clauses.append(f"{key} IS NOT NULL")
            elif isinstance(want, Lt):
                clauses.append(f"{key} < ?")
                args.append(want.value)
            elif isinstance(want, Le):
                clauses.append(f"{key} <= ?")
                args.append(want.value)
            elif isinstance(want, Gt):
                clauses.append(f"{key} > ?")
                args.append(want.value)
            elif isinstance(want, Ge):
                clauses.append(f"{key} >= ?")
                args.append(want.value)
            else:
                clauses.append(f"{key} IS ?")
                args.append(want)
        return (" AND ".join(clauses) or "1"), args, residue

    # -- 1. append ---------------------------------------------------------
    def append(self, stream: str, record: dict[str, Any]) -> int:
        cols, body = self._split(stream, record)
        names = list(cols)
        with self._write() as conn:
            cur = conn.execute(
                f"INSERT INTO {_table(stream)} (ts, {', '.join(names)}, body) "
                f"VALUES (?, {', '.join('?' * len(names))}, ?)",
                [record.get("ts", self.clock()), *[cols[n] for n in names], json.dumps(body, ensure_ascii=False)],
            )
        self._appends += 1
        return int(cur.lastrowid)

    # -- 2. match ----------------------------------------------------------
    def match(
        self,
        stream: str,
        predicate: Predicate,
        update: dict[str, Any],
        *,
        order_by: str = "seq",
    ) -> dict[str, Any] | None:
        where, args, residue = self._compile(stream, predicate)
        with self._write() as conn:
            rows = conn.execute(
                f"SELECT * FROM {_table(stream)} WHERE {where} ORDER BY {order_by} ASC", args
            ).fetchall()
            for row in rows:
                rec = self._row(stream, row)
                if residue and not matches(rec, residue):
                    continue
                self._apply(conn, stream, row, update)
                rec.update(update)
                self._matches += 1
                return rec
        return None

    def _apply(
        self, conn: sqlite3.Connection, stream: str, row: sqlite3.Row, fields: dict[str, Any]
    ) -> None:
        indexed = set(STREAMS[stream])
        col_sets, col_args = [], []
        body = json.loads(row["body"])
        for k, v in fields.items():
            if k in indexed:
                col_sets.append(f"{k} = ?")
                col_args.append(v)
            else:
                body[k] = v
        col_sets.append("body = ?")
        col_args.append(json.dumps(body, ensure_ascii=False))
        conn.execute(
            f"UPDATE {_table(stream)} SET {', '.join(col_sets)} WHERE seq = ?",
            [*col_args, row["seq"]],
        )

    # -- 3. scan -----------------------------------------------------------
    def scan(
        self,
        stream: str,
        predicate: Predicate,
        *,
        order_by: str = "seq",
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        where, args, residue = self._compile(stream, predicate)
        direction = "DESC" if descending else "ASC"
        sql = f"SELECT * FROM {_table(stream)} WHERE {where} ORDER BY {order_by} {direction}"
        if limit is not None and not residue:
            sql += f" LIMIT {int(limit)}"
        out: list[dict[str, Any]] = []
        for row in self.conn.execute(sql, args):
            rec = self._row(stream, row)
            if residue and not matches(rec, residue):
                continue
            out.append(rec)
            if limit is not None and len(out) >= limit:
                break
        return out

    def update(self, stream: str, seq: int, fields: dict[str, Any]) -> bool:
        with self._write() as conn:
            row = conn.execute(f"SELECT * FROM {_table(stream)} WHERE seq = ?", [seq]).fetchone()
            if row is None:
                return False
            self._apply(conn, stream, row, fields)
            return True

    # -- 4. cas ------------------------------------------------------------
    def read(self, space: str, key: str, *, version: int | None = None) -> Cell | None:
        if version is None:
            row = self.conn.execute(
                "SELECT * FROM cells WHERE space=? AND key=? ORDER BY version DESC LIMIT 1",
                [space, key],
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM cells WHERE space=? AND key=? AND version=?",
                [space, key, version],
            ).fetchone()
        return self._cell(row) if row else None

    @staticmethod
    def _cell(row: sqlite3.Row) -> Cell:
        return Cell(
            space=row["space"],
            key=row["key"],
            version=row["version"],
            value=json.loads(row["value"]) if row["value"] is not None else None,
            writer=row["writer"],
            epoch=row["epoch"],
            ts=row["ts"],
            meta=json.loads(row["meta"]),
        )

    def cas(
        self,
        space: str,
        key: str,
        expect_version: int | None,
        value: Any,
        *,
        writer: int,
        epoch: int = 0,
        meta: dict[str, Any] | None = None,
    ) -> tuple[bool, Cell]:
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM cells WHERE space=? AND key=? ORDER BY version DESC LIMIT 1",
                [space, key],
            ).fetchone()
            current = row["version"] if row else 0
            if expect_version is not None and expect_version != current:
                self._cas += 1
                return False, (
                    self._cell(row)
                    if row
                    else Cell(space, key, 0, None, -1, 0, self.clock(), {})
                )
            nxt = current + 1
            ts = self.clock()
            conn.execute(
                "INSERT INTO cells (space, key, version, value, writer, epoch, ts, meta) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    space,
                    key,
                    nxt,
                    json.dumps(value, ensure_ascii=False) if value is not None else None,
                    writer,
                    epoch,
                    ts,
                    json.dumps(meta or {}, ensure_ascii=False),
                ],
            )
        self._cas += 1
        return True, Cell(space, key, nxt, value, writer, epoch, ts, meta or {})

    def keys(self, space: str, *, prefix: str = "") -> list[Cell]:
        rows = self.conn.execute(
            "SELECT space, key, MAX(version) AS version, writer, epoch, ts, meta "
            "FROM cells WHERE space=? AND key LIKE ? GROUP BY space, key ORDER BY key",
            [space, prefix + "%"],
        ).fetchall()
        out = []
        for row in rows:
            out.append(
                Cell(
                    space=row["space"],
                    key=row["key"],
                    version=row["version"],
                    value=None,
                    writer=row["writer"],
                    epoch=row["epoch"],
                    ts=row["ts"],
                    meta=json.loads(row["meta"]),
                )
            )
        return out

    def history(self, space: str, key: str, *, limit: int | None = None) -> list[Cell]:
        sql = "SELECT * FROM cells WHERE space=? AND key=? ORDER BY version DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [self._cell(r) for r in self.conn.execute(sql, [space, key])]

    # -- 5. lease ----------------------------------------------------------
    def lease(
        self,
        space: str,
        key: str,
        *,
        holder: int,
        mode: str = "exclusive",
        ttl: float,
    ) -> object | None:
        from .base import Lease

        now = self.clock()
        with self._write() as conn:
            conn.execute("DELETE FROM locks WHERE expires_at <= ?", [now])
            held = conn.execute(
                "SELECT * FROM locks WHERE space=? AND key=?", [space, key]
            ).fetchall()
            if held:
                if mode == "exclusive" or any(h["mode"] == "exclusive" for h in held):
                    if not (len(held) == 1 and held[0]["holder"] == holder and held[0]["mode"] == mode):
                        return None
                    # Re-acquiring one's own lock renews it rather than failing:
                    # an executor that retries a command must not lock itself out.
                    conn.execute(
                        "UPDATE locks SET expires_at=? WHERE lock_id=?",
                        [now + ttl, held[0]["lock_id"]],
                    )
                    return Lease(
                        held[0]["lock_id"], space, key, holder, mode,
                        held[0]["token"], now + ttl, held[0]["acquired_at"],
                    )
            row = conn.execute("SELECT token FROM fence WHERE space=? AND key=?", [space, key]).fetchone()
            token = (row["token"] if row else 0) + 1
            conn.execute(
                "INSERT INTO fence (space, key, token) VALUES (?,?,?) "
                "ON CONFLICT(space, key) DO UPDATE SET token=excluded.token",
                [space, key, token],
            )
            lock_id = uuid.uuid4().hex[:16]
            conn.execute(
                "INSERT INTO locks (lock_id, space, key, holder, mode, token, acquired_at, expires_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [lock_id, space, key, holder, mode, token, now, now + ttl],
            )
            return Lease(lock_id, space, key, holder, mode, token, now + ttl, now)

    def release(self, lock_id: str, holder: int) -> bool:
        with self._write() as conn:
            cur = conn.execute(
                "DELETE FROM locks WHERE lock_id=? AND holder=?", [lock_id, holder]
            )
            return cur.rowcount > 0

    def leases(self, space: str = "", *, include_expired: bool = False) -> list[object]:
        from .base import Lease

        sql = "SELECT * FROM locks WHERE 1"
        args: list[Any] = []
        if space:
            sql += " AND space=?"
            args.append(space)
        if not include_expired:
            sql += " AND expires_at > ?"
            args.append(self.clock())
        return [
            Lease(
                r["lock_id"], r["space"], r["key"], r["holder"], r["mode"],
                r["token"], r["expires_at"], r["acquired_at"],
            )
            for r in self.conn.execute(sql, args)
        ]

    # -- 6. clock ----------------------------------------------------------
    def clock(self) -> float:
        return time.time()

    # -- object store ------------------------------------------------------
    def put_object(self, digest: str, body: str) -> None:
        with self._write() as conn:
            conn.execute("INSERT OR IGNORE INTO obj (digest, body) VALUES (?,?)", [digest, body])

    def get_object(self, digest: str) -> str | None:
        row = self.conn.execute("SELECT body FROM obj WHERE digest=?", [digest]).fetchone()
        return row["body"] if row else None

    def stats(self) -> dict[str, Any]:
        out = super().stats()
        out.update(
            path=str(self.path),
            appends=self._appends,
            matches=self._matches,
            cas=self._cas,
            bytes=self.path.stat().st_size if self.path.exists() else 0,
        )
        return out


# Indexed fields that carry integers.  SQLite applies type affinity, so an integer
# written to a TEXT column comes back as a string -- which the memory and journal
# devices do not do.  Getting this list wrong produces a device-specific semantic
# divergence: a wildcard receive posted as src_want=-1 reads back as "-1" and
# stops matching.  The conformance suite caught exactly that, which is the whole
# reason it is parametrised over every device rather than run against one.
_INT_FIELDS = {
    "rank", "src", "dst", "tag", "epoch", "gen", "step", "assignee",
    "src_want", "tag_want", "provider", "verifier",
}
