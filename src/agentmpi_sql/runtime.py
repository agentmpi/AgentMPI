"""Durable SQLite reference runtime for the AgentMPI protocol.

The runtime intentionally favors inspectable semantics over raw throughput.  Each
operation maps to a short transaction, so independently launched agent processes
can coordinate through one database without a resident broker.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from .model import (
    ANY_SOURCE,
    ANY_TAG,
    AgentInfo,
    AgentState,
    CollectiveOp,
    Communicator,
    CommunicatorRevoked,
    DeliveryMode,
    LockUnavailable,
    ProcessFailed,
    ProtocolViolation,
    Received,
    ReduceOp,
    ResourceExhausted,
    Status,
    Timeout,
    WouldBlock,
)

SCHEMA_VERSION = 1


def estimate_tokens(value: Any) -> int:
    """Conservative tokenizer-independent estimate used for flow control."""
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return max(1, math.ceil(len(encoded.encode("utf-8")) / 4))


class Runtime:
    """One AgentMPI rank attached to a durable session."""

    def __init__(
        self,
        db_path: str | os.PathLike[str],
        session_id: str,
        rank: int,
        *,
        heartbeat_ttl: float = 30.0,
        poll_interval: float = 0.02,
    ) -> None:
        self.db_path = Path(db_path)
        self.session_id = session_id
        self.rank = rank
        self.heartbeat_ttl = heartbeat_ttl
        self.poll_interval = poll_interval
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.db_path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._ensure_schema()
        self._assert_member()

    @classmethod
    def initialize(
        cls,
        db_path: str | os.PathLike[str],
        *,
        size: int,
        session_id: str = "default",
        context_budget: int = 32_000,
        mailbox_bytes: int = 8 * 1024 * 1024,
        inline_token_limit: int = 2_048,
        heartbeat_ttl: float = 30.0,
    ) -> None:
        """Create a session and its initial ``WORLD`` communicator."""
        if size < 1:
            raise ValueError("size must be positive")
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        now = time.time()
        members = tuple(range(size))
        world_id = f"{session_id}:world:0"
        with conn:
            conn.execute(
                """INSERT INTO sessions(
                       id, size, created_at, state, mailbox_bytes,
                       inline_token_limit, schema_version
                   ) VALUES (?, ?, ?, 'active', ?, ?, ?)""",
                (
                    session_id,
                    size,
                    now,
                    mailbox_bytes,
                    inline_token_limit,
                    SCHEMA_VERSION,
                ),
            )
            conn.executemany(
                """INSERT INTO agents(
                       session_id, rank, state, heartbeat_at, lease_until,
                       context_budget, context_used, incarnation
                   ) VALUES (?, ?, 'joining', ?, ?, ?, 0, 0)""",
                [
                    (
                        session_id,
                        rank,
                        now,
                        now + heartbeat_ttl,
                        context_budget,
                    )
                    for rank in members
                ],
            )
            conn.execute(
                """INSERT INTO communicators(
                       id, session_id, name, generation, members_json,
                       revoked, created_at
                   ) VALUES (?, ?, 'WORLD', 0, ?, 0, ?)""",
                (world_id, session_id, json.dumps(members), now),
            )
        conn.close()

    @classmethod
    def attach(
        cls,
        db_path: str | os.PathLike[str],
        session_id: str,
        rank: int,
        *,
        context_budget: int | None = None,
        heartbeat_ttl: float = 30.0,
    ) -> Runtime:
        runtime = cls(
            db_path,
            session_id,
            rank,
            heartbeat_ttl=heartbeat_ttl,
        )
        now = time.time()
        with runtime._transaction():
            row = runtime._conn.execute(
                "SELECT state, incarnation FROM agents WHERE session_id=? AND rank=?",
                (session_id, rank),
            ).fetchone()
            if row is None:
                raise ProtocolViolation(f"rank {rank} does not exist")
            if row["state"] == AgentState.ACTIVE.value:
                raise ProtocolViolation(f"rank {rank} is already active")
            if row["state"] == AgentState.FINALIZED.value:
                raise ProtocolViolation(f"rank {rank} was finalized")
            budget_sql = ", context_budget=?" if context_budget is not None else ""
            params: list[Any] = [
                AgentState.ACTIVE.value,
                now,
                now + heartbeat_ttl,
                int(row["incarnation"]) + 1,
            ]
            if context_budget is not None:
                params.append(context_budget)
            params.extend([session_id, rank])
            runtime._conn.execute(
                f"""UPDATE agents SET state=?, heartbeat_at=?, lease_until=?,
                       incarnation=?{budget_sql}
                    WHERE session_id=? AND rank=?""",
                params,
            )
            runtime._event("agent.join", {"rank": rank})
        return runtime

    def close(self) -> None:
        self._conn.close()

    def finalize(self) -> None:
        with self._transaction():
            self._conn.execute(
                """UPDATE agents SET state=?, lease_until=?
                   WHERE session_id=? AND rank=?""",
                (AgentState.FINALIZED.value, time.time(), self.session_id, self.rank),
            )
            self._event("agent.finalize", {"rank": self.rank})

    def heartbeat(self) -> None:
        now = time.time()
        with self._transaction():
            self._conn.execute(
                """UPDATE agents SET heartbeat_at=?, lease_until=?
                   WHERE session_id=? AND rank=? AND state=?""",
                (
                    now,
                    now + self.heartbeat_ttl,
                    self.session_id,
                    self.rank,
                    AgentState.ACTIVE.value,
                ),
            )

    @property
    def world(self) -> Communicator:
        row = self._conn.execute(
            """SELECT * FROM communicators
               WHERE session_id=? AND name='WORLD'
               ORDER BY generation DESC LIMIT 1""",
            (self.session_id,),
        ).fetchone()
        if row is None:
            raise ProtocolViolation("WORLD communicator is missing")
        return self._communicator(row)

    def communicator(self, comm_id: str) -> Communicator:
        row = self._conn.execute(
            "SELECT * FROM communicators WHERE id=? AND session_id=?",
            (comm_id, self.session_id),
        ).fetchone()
        if row is None:
            raise ProtocolViolation(f"unknown communicator {comm_id}")
        return self._communicator(row)

    def create_communicator(
        self, members: Sequence[int], *, name: str | None = None
    ) -> Communicator:
        normalized = tuple(dict.fromkeys(int(rank) for rank in members))
        if not normalized:
            raise ValueError("communicator must have at least one member")
        known = {
            int(row["rank"])
            for row in self._conn.execute(
                "SELECT rank FROM agents WHERE session_id=?", (self.session_id,)
            )
        }
        if not set(normalized) <= known:
            raise ProtocolViolation("communicator contains an unknown rank")
        comm_id = f"{self.session_id}:comm:{uuid.uuid4().hex}"
        comm_name = name or comm_id.rsplit(":", 1)[-1]
        now = time.time()
        with self._transaction():
            self._conn.execute(
                """INSERT INTO communicators(
                       id, session_id, name, generation, members_json,
                       revoked, created_at
                   ) VALUES (?, ?, ?, 0, ?, 0, ?)""",
                (comm_id, self.session_id, comm_name, json.dumps(normalized), now),
            )
            self._event(
                "comm.create",
                {"comm_id": comm_id, "members": normalized, "name": comm_name},
            )
        return self.communicator(comm_id)

    def send(
        self,
        payload: Any,
        dest: int,
        *,
        tag: str = "default",
        comm: Communicator | None = None,
        mode: DeliveryMode = DeliveryMode.STANDARD,
        timeout: float | None = None,
    ) -> Status:
        """Send one ordered message.

        Standard and buffered sends complete after durable enqueue. Synchronous
        sends complete after the destination acknowledges receipt. Ready sends
        require a posted matching receive.
        """
        communicator = comm or self.world
        self._validate_comm(communicator)
        if self.rank not in communicator.members or dest not in communicator.members:
            raise ProtocolViolation("source and destination must belong to communicator")
        started = time.monotonic()
        token_count = estimate_tokens(payload)
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        payload_bytes = len(encoded.encode("utf-8"))
        artifact_ref: str | None = None
        session = self._session_row()
        if token_count > int(session["inline_token_limit"]):
            artifact_ref = self.put_artifact(
                encoded.encode("utf-8"), media_type="application/json"
            )
            encoded = json.dumps(
                {
                    "_agentmpi_artifact": artifact_ref,
                    "media_type": "application/json",
                    "tokens": token_count,
                },
                separators=(",", ":"),
            )
            payload_bytes = len(encoded)
        message_id = uuid.uuid4().hex
        while True:
            try:
                with self._transaction():
                    self._assert_communicator_live(communicator.id)
                    if mode is DeliveryMode.READY and not self._has_posted_receive(
                        communicator.id, self.rank, dest, tag
                    ):
                        raise ProtocolViolation("ready send has no matching posted receive")
                    queued = self._conn.execute(
                        """SELECT COALESCE(SUM(payload_bytes), 0) AS total
                           FROM messages
                           WHERE session_id=? AND dst=? AND state='pending'""",
                        (self.session_id, dest),
                    ).fetchone()["total"]
                    if int(queued) + payload_bytes > int(session["mailbox_bytes"]):
                        raise ResourceExhausted(
                            f"rank {dest} mailbox capacity would be exceeded"
                        )
                    sequence = int(
                        self._conn.execute(
                            """SELECT COALESCE(MAX(sequence), -1) + 1 AS next
                               FROM messages
                               WHERE comm_id=? AND src=? AND dst=? AND tag=?""",
                            (communicator.id, self.rank, dest, tag),
                        ).fetchone()["next"]
                    )
                    now = time.time()
                    self._conn.execute(
                        """INSERT INTO messages(
                               id, session_id, comm_id, generation, src, dst, tag,
                               sequence, mode, payload_json, payload_bytes,
                               payload_tokens, artifact_ref, state, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                     'pending', ?)""",
                        (
                            message_id,
                            self.session_id,
                            communicator.id,
                            communicator.generation,
                            self.rank,
                            dest,
                            tag,
                            sequence,
                            mode.value,
                            encoded,
                            payload_bytes,
                            token_count,
                            artifact_ref,
                            now,
                        ),
                    )
                    self._event(
                        "message.send",
                        {
                            "message_id": message_id,
                            "src": self.rank,
                            "dst": dest,
                            "tag": tag,
                            "sequence": sequence,
                            "tokens": token_count,
                            "artifact_ref": artifact_ref,
                        },
                    )
                status = Status(
                    source=self.rank,
                    tag=tag,
                    count=payload_bytes,
                    message_id=message_id,
                    sequence=sequence,
                    artifact_ref=artifact_ref,
                    payload_tokens=token_count,
                )
                if mode is DeliveryMode.SYNCHRONOUS:
                    self._wait_for_ack(message_id, timeout, started)
                return status
            except (sqlite3.OperationalError, ResourceExhausted):
                if timeout is None:
                    raise
                if time.monotonic() - started >= timeout:
                    raise Timeout("send timed out under backpressure") from None
                time.sleep(self.poll_interval)

    def recv(
        self,
        *,
        source: int = ANY_SOURCE,
        tag: str = ANY_TAG,
        comm: Communicator | None = None,
        timeout: float | None = None,
        charge_context: bool = True,
    ) -> Received:
        communicator = comm or self.world
        self._validate_comm(communicator)
        if self.rank not in communicator.members:
            raise ProtocolViolation("receiver does not belong to communicator")
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        with self._transaction():
            self._conn.execute(
                """INSERT INTO posted_receives(
                       id, session_id, comm_id, dst, source, tag, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    request_id,
                    self.session_id,
                    communicator.id,
                    self.rank,
                    source,
                    tag,
                    time.time(),
                ),
            )
        try:
            while True:
                with self._transaction():
                    self._assert_communicator_live(communicator.id)
                    row = self._match_message(communicator.id, source, tag)
                    if row is not None:
                        payload = json.loads(row["payload_json"])
                        if charge_context:
                            self._charge_context(estimate_tokens(payload))
                        now = time.time()
                        self._conn.execute(
                            """UPDATE messages
                               SET state='acked', matched_at=?, acked_at=?
                               WHERE id=?""",
                            (now, now, row["id"]),
                        )
                        self._conn.execute(
                            "DELETE FROM posted_receives WHERE id=?", (request_id,)
                        )
                        self._event(
                            "message.recv",
                            {
                                "message_id": row["id"],
                                "src": row["src"],
                                "dst": self.rank,
                                "tag": row["tag"],
                                "sequence": row["sequence"],
                            },
                        )
                        return Received(
                            payload=payload,
                            status=Status(
                                source=int(row["src"]),
                                tag=str(row["tag"]),
                                count=int(row["payload_bytes"]),
                                message_id=str(row["id"]),
                                sequence=int(row["sequence"]),
                                artifact_ref=row["artifact_ref"],
                                payload_tokens=int(row["payload_tokens"]),
                            ),
                        )
                if timeout is not None and time.monotonic() - started >= timeout:
                    raise Timeout("receive timed out")
                time.sleep(self.poll_interval)
        finally:
            with self._transaction():
                self._conn.execute("DELETE FROM posted_receives WHERE id=?", (request_id,))

    def probe(
        self,
        *,
        source: int = ANY_SOURCE,
        tag: str = ANY_TAG,
        comm: Communicator | None = None,
    ) -> Status:
        communicator = comm or self.world
        row = self._match_message(communicator.id, source, tag, mutate=False)
        if row is None:
            raise WouldBlock("no matching message")
        return Status(
            source=int(row["src"]),
            tag=str(row["tag"]),
            count=int(row["payload_bytes"]),
            message_id=str(row["id"]),
            sequence=int(row["sequence"]),
            artifact_ref=row["artifact_ref"],
            payload_tokens=int(row["payload_tokens"]),
        )

    def barrier(
        self, *, comm: Communicator | None = None, timeout: float | None = None
    ) -> None:
        self._collective(CollectiveOp.BARRIER, None, comm=comm, timeout=timeout)

    def bcast(
        self,
        value: Any = None,
        *,
        root: int = 0,
        comm: Communicator | None = None,
        timeout: float | None = None,
    ) -> Any:
        communicator = comm or self.world
        contribution = value if self.rank == root else None
        return self._collective(
            CollectiveOp.BCAST,
            contribution,
            comm=communicator,
            root=root,
            timeout=timeout,
        )

    def scatter(
        self,
        values: Sequence[Any] | None = None,
        *,
        root: int = 0,
        comm: Communicator | None = None,
        timeout: float | None = None,
    ) -> Any:
        communicator = comm or self.world
        if self.rank == root and (values is None or len(values) != communicator.size):
            raise ProtocolViolation("scatter root must provide one value per member")
        contribution = list(values) if self.rank == root and values is not None else None
        result = self._collective(
            CollectiveOp.SCATTER,
            contribution,
            comm=communicator,
            root=root,
            timeout=timeout,
        )
        return result[str(self.rank)]

    def gather(
        self,
        value: Any,
        *,
        root: int = 0,
        comm: Communicator | None = None,
        timeout: float | None = None,
    ) -> list[Any] | None:
        communicator = comm or self.world
        result = self._collective(
            CollectiveOp.GATHER,
            value,
            comm=communicator,
            root=root,
            timeout=timeout,
        )
        return result if self.rank == root else None

    def allgather(
        self,
        value: Any,
        *,
        comm: Communicator | None = None,
        timeout: float | None = None,
    ) -> list[Any]:
        result = self._collective(CollectiveOp.ALLGATHER, value, comm=comm, timeout=timeout)
        if not isinstance(result, list):
            raise ProtocolViolation("allgather produced a non-list result")
        return result

    def reduce(
        self,
        value: Any,
        *,
        op: ReduceOp = ReduceOp.SUM,
        root: int = 0,
        comm: Communicator | None = None,
        timeout: float | None = None,
    ) -> Any:
        communicator = comm or self.world
        result = self._collective(
            CollectiveOp.REDUCE,
            value,
            comm=communicator,
            root=root,
            reduce_op=op,
            timeout=timeout,
        )
        return result if self.rank == root else None

    def allreduce(
        self,
        value: Any,
        *,
        op: ReduceOp = ReduceOp.SUM,
        comm: Communicator | None = None,
        timeout: float | None = None,
    ) -> Any:
        return self._collective(
            CollectiveOp.ALLREDUCE,
            value,
            comm=comm,
            reduce_op=op,
            timeout=timeout,
        )

    def agree(
        self,
        flag: bool,
        *,
        comm: Communicator | None = None,
        timeout: float | None = None,
    ) -> bool:
        return bool(
            self._collective(
                CollectiveOp.AGREE,
                flag,
                comm=comm,
                reduce_op=ReduceOp.ALL,
                timeout=timeout,
                tolerate_failures=True,
            )
        )

    def revoke(self, comm: Communicator | None = None, reason: str = "user") -> None:
        communicator = comm or self.world
        with self._transaction():
            self._conn.execute(
                "UPDATE communicators SET revoked=1 WHERE id=?", (communicator.id,)
            )
            self._event("comm.revoke", {"comm_id": communicator.id, "reason": reason})

    def detect_failures(self, *, now: float | None = None) -> list[int]:
        timestamp = now or time.time()
        with self._transaction():
            rows = self._conn.execute(
                """SELECT rank FROM agents
                   WHERE session_id=? AND state='active' AND lease_until < ?""",
                (self.session_id, timestamp),
            ).fetchall()
            failed = [int(row["rank"]) for row in rows]
            for rank in failed:
                self._conn.execute(
                    "UPDATE agents SET state='failed' WHERE session_id=? AND rank=?",
                    (self.session_id, rank),
                )
                self._event("agent.failed", {"rank": rank, "cause": "lease_expired"})
            if failed:
                affected = self._conn.execute(
                    """SELECT id, members_json FROM communicators
                       WHERE session_id=? AND revoked=0""",
                    (self.session_id,),
                ).fetchall()
                for row in affected:
                    if set(failed) & set(json.loads(row["members_json"])):
                        self._conn.execute(
                            "UPDATE communicators SET revoked=1 WHERE id=?",
                            (row["id"],),
                        )
                        self._event(
                            "comm.revoke",
                            {"comm_id": row["id"], "reason": "member_failed"},
                        )
            return failed

    def fail_rank(self, rank: int, *, reason: str = "injected") -> None:
        """Mark a rank failed. This explicit hook supports fault-injection studies."""
        with self._transaction():
            self._conn.execute(
                "UPDATE agents SET state='failed', lease_until=? WHERE session_id=? AND rank=?",
                (time.time(), self.session_id, rank),
            )
            self._event("agent.failed", {"rank": rank, "cause": reason})
            rows = self._conn.execute(
                "SELECT id, members_json FROM communicators WHERE session_id=? AND revoked=0",
                (self.session_id,),
            ).fetchall()
            for row in rows:
                if rank in json.loads(row["members_json"]):
                    self._conn.execute(
                        "UPDATE communicators SET revoked=1 WHERE id=?", (row["id"],)
                    )

    def shrink(self, comm: Communicator | None = None) -> Communicator:
        communicator = comm or self.world
        states = {
            int(row["rank"]): str(row["state"])
            for row in self._conn.execute(
                "SELECT rank, state FROM agents WHERE session_id=?", (self.session_id,)
            )
        }
        survivors = tuple(
            rank
            for rank in communicator.members
            if states.get(rank) not in {AgentState.FAILED.value, AgentState.FINALIZED.value}
        )
        if self.rank not in survivors:
            raise ProcessFailed(f"rank {self.rank} is not a survivor")
        generation = communicator.generation + 1
        comm_id = f"{communicator.id.rsplit(':', 1)[0]}:{generation}"
        with self._transaction():
            self._conn.execute(
                """INSERT OR IGNORE INTO communicators(
                       id, session_id, name, generation, members_json,
                       revoked, created_at
                   ) VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (
                    comm_id,
                    self.session_id,
                    communicator.name,
                    generation,
                    json.dumps(survivors),
                    time.time(),
                ),
            )
            self._event(
                "comm.shrink",
                {
                    "old_comm": communicator.id,
                    "new_comm": comm_id,
                    "survivors": survivors,
                },
            )
        return self.communicator(comm_id)

    def acquire_lock(self, name: str, *, lease_seconds: float = 30.0) -> int:
        """Acquire a lease lock and return its monotonic fencing token."""
        now = time.time()
        with self._transaction():
            row = self._conn.execute(
                "SELECT * FROM locks WHERE session_id=? AND name=?",
                (self.session_id, name),
            ).fetchone()
            if (
                row is not None
                and float(row["lease_until"]) >= now
                and int(row["owner"]) != self.rank
            ):
                raise LockUnavailable(f"lock {name!r} is held by rank {row['owner']}")
            token = 1 if row is None else int(row["fencing_token"]) + 1
            self._conn.execute(
                """INSERT INTO locks(
                       session_id, name, owner, lease_until, fencing_token
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(session_id, name) DO UPDATE SET
                       owner=excluded.owner,
                       lease_until=excluded.lease_until,
                       fencing_token=excluded.fencing_token""",
                (self.session_id, name, self.rank, now + lease_seconds, token),
            )
            self._event(
                "lock.acquire",
                {"name": name, "owner": self.rank, "fencing_token": token},
            )
            return token

    def release_lock(self, name: str, fencing_token: int) -> None:
        with self._transaction():
            cursor = self._conn.execute(
                """DELETE FROM locks
                   WHERE session_id=? AND name=? AND owner=? AND fencing_token=?""",
                (self.session_id, name, self.rank, fencing_token),
            )
            if cursor.rowcount != 1:
                raise LockUnavailable("lock ownership or fencing token is stale")
            self._event(
                "lock.release",
                {"name": name, "owner": self.rank, "fencing_token": fencing_token},
            )

    def put_artifact(self, data: bytes, *, media_type: str) -> str:
        digest = hashlib.sha256(data).hexdigest()
        artifact_dir = self.db_path.with_suffix(self.db_path.suffix + ".artifacts")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / digest
        if not path.exists():
            temporary = artifact_dir / f".{digest}.{uuid.uuid4().hex}.tmp"
            temporary.write_bytes(data)
            os.replace(temporary, path)
        with self._transaction():
            self._conn.execute(
                """INSERT OR IGNORE INTO artifacts(
                       digest, session_id, media_type, size_bytes, path, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    digest,
                    self.session_id,
                    media_type,
                    len(data),
                    str(path),
                    time.time(),
                ),
            )
        return f"sha256:{digest}"

    def get_artifact(self, ref: str, *, charge_context: bool = True) -> bytes:
        digest = ref.removeprefix("sha256:")
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE digest=? AND session_id=?",
            (digest, self.session_id),
        ).fetchone()
        if row is None:
            raise ProtocolViolation(f"unknown artifact {ref}")
        data = Path(row["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ProtocolViolation(f"artifact checksum mismatch for {ref}")
        if charge_context:
            with self._transaction():
                self._charge_context(max(1, math.ceil(len(data) / 4)))
        return data

    def reset_context(self, *, used: int = 0) -> None:
        with self._transaction():
            self._conn.execute(
                "UPDATE agents SET context_used=? WHERE session_id=? AND rank=?",
                (used, self.session_id, self.rank),
            )
            self._event("context.compact", {"rank": self.rank, "new_used": used})

    def agent_info(self) -> list[AgentInfo]:
        rows = self._conn.execute(
            "SELECT * FROM agents WHERE session_id=? ORDER BY rank", (self.session_id,)
        ).fetchall()
        return [
            AgentInfo(
                rank=int(row["rank"]),
                state=AgentState(row["state"]),
                heartbeat_at=float(row["heartbeat_at"]),
                lease_until=float(row["lease_until"]),
                context_budget=int(row["context_budget"]),
                context_used=int(row["context_used"]),
                incarnation=int(row["incarnation"]),
            )
            for row in rows
        ]

    def trace(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE session_id=? ORDER BY sequence",
            (self.session_id,),
        ).fetchall()
        return [
            {
                "sequence": int(row["sequence"]),
                "timestamp": float(row["created_at"]),
                "rank": int(row["rank"]),
                "kind": str(row["kind"]),
                "data": json.loads(row["data_json"]),
            }
            for row in rows
        ]

    def _collective(
        self,
        op: CollectiveOp,
        value: Any,
        *,
        comm: Communicator | None,
        root: int = 0,
        reduce_op: ReduceOp = ReduceOp.SUM,
        timeout: float | None = None,
        tolerate_failures: bool = False,
    ) -> Any:
        communicator = comm or self.world
        self._validate_comm(communicator, allow_revoked=tolerate_failures)
        if root not in communicator.members:
            raise ProtocolViolation("collective root is not in communicator")
        ordinal = self._next_collective_ordinal(communicator.id)
        epoch = self._next_collective_epoch(communicator.id, op)
        started = time.monotonic()
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        signature_error: str | None = None
        with self._transaction():
            self._conn.execute(
                """INSERT OR IGNORE INTO collective_instances(
                       comm_id, generation, ordinal, operation, root,
                       reduce_op, error, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    communicator.id,
                    communicator.generation,
                    ordinal,
                    op.value,
                    root,
                    reduce_op.value,
                    time.time(),
                ),
            )
            instance = self._conn.execute(
                """SELECT operation, root, reduce_op, error
                   FROM collective_instances
                   WHERE comm_id=? AND generation=? AND ordinal=?""",
                (communicator.id, communicator.generation, ordinal),
            ).fetchone()
            if instance is None:
                raise ProtocolViolation("collective signature disappeared")
            expected = (op.value, root, reduce_op.value)
            actual = (
                str(instance["operation"]),
                int(instance["root"]),
                str(instance["reduce_op"]),
            )
            if actual != expected:
                message = (
                    f"collective ordinal {ordinal} mismatch: "
                    f"expected {actual}, rank {self.rank} entered {expected}"
                )
                self._conn.execute(
                    """UPDATE collective_instances SET error=?
                       WHERE comm_id=? AND generation=? AND ordinal=?""",
                    (
                        message,
                        communicator.id,
                        communicator.generation,
                        ordinal,
                    ),
                )
                self._event(
                    "collective.mismatch",
                    {
                        "comm_id": communicator.id,
                        "ordinal": ordinal,
                        "expected": actual,
                        "received": expected,
                    },
                )
                signature_error = message
            elif instance["error"] is not None:
                signature_error = str(instance["error"])
            else:
                self._conn.execute(
                    """INSERT INTO collective_contributions(
                           comm_id, generation, operation, epoch, rank,
                           value_json, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        communicator.id,
                        communicator.generation,
                        op.value,
                        epoch,
                        self.rank,
                        encoded,
                        time.time(),
                    ),
                )
                self._event(
                    "collective.enter",
                    {
                        "comm_id": communicator.id,
                        "op": op.value,
                        "epoch": epoch,
                        "ordinal": ordinal,
                    },
                )
        if signature_error is not None:
            raise ProtocolViolation(signature_error)
        while True:
            with self._transaction():
                if not tolerate_failures:
                    self._assert_communicator_live(communicator.id)
                instance = self._conn.execute(
                    """SELECT error FROM collective_instances
                       WHERE comm_id=? AND generation=? AND ordinal=?""",
                    (communicator.id, communicator.generation, ordinal),
                ).fetchone()
                if instance is not None and instance["error"] is not None:
                    raise ProtocolViolation(str(instance["error"]))
                members = list(communicator.members)
                if tolerate_failures:
                    failed = {
                        int(row["rank"])
                        for row in self._conn.execute(
                            """SELECT rank FROM agents
                               WHERE session_id=? AND state IN ('failed', 'finalized')""",
                            (self.session_id,),
                        )
                    }
                    members = [rank for rank in members if rank not in failed]
                rows = self._conn.execute(
                    """SELECT rank, value_json FROM collective_contributions
                       WHERE comm_id=? AND generation=? AND operation=? AND epoch=?
                       ORDER BY rank""",
                    (
                        communicator.id,
                        communicator.generation,
                        op.value,
                        epoch,
                    ),
                ).fetchall()
                submitted = {int(row["rank"]): json.loads(row["value_json"]) for row in rows}
                if set(members) <= set(submitted):
                    result_row = self._conn.execute(
                        """SELECT value_json FROM collective_results
                           WHERE comm_id=? AND generation=? AND operation=? AND epoch=?""",
                        (
                            communicator.id,
                            communicator.generation,
                            op.value,
                            epoch,
                        ),
                    ).fetchone()
                    if result_row is None:
                        result = self._compute_collective(
                            op, submitted, members, root, reduce_op
                        )
                        self._conn.execute(
                            """INSERT OR IGNORE INTO collective_results(
                                   comm_id, generation, operation, epoch,
                                   value_json, completed_at
                               ) VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                communicator.id,
                                communicator.generation,
                                op.value,
                                epoch,
                                json.dumps(
                                    result,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                time.time(),
                            ),
                        )
                    else:
                        result = json.loads(result_row["value_json"])
                    self._event(
                        "collective.exit",
                        {
                            "comm_id": communicator.id,
                            "op": op.value,
                            "epoch": epoch,
                            "ordinal": ordinal,
                        },
                    )
                    return result
            if timeout is not None and time.monotonic() - started >= timeout:
                raise Timeout(f"{op.value} collective epoch {epoch} timed out")
            time.sleep(self.poll_interval)

    def _compute_collective(
        self,
        op: CollectiveOp,
        submitted: dict[int, Any],
        members: list[int],
        root: int,
        reduce_op: ReduceOp,
    ) -> Any:
        ordered = [submitted[rank] for rank in members]
        if op is CollectiveOp.BARRIER:
            return None
        if op is CollectiveOp.BCAST:
            return submitted[root]
        if op is CollectiveOp.SCATTER:
            values = submitted[root]
            return {str(rank): values[index] for index, rank in enumerate(members)}
        if op in {CollectiveOp.GATHER, CollectiveOp.ALLGATHER}:
            return ordered
        if op in {CollectiveOp.REDUCE, CollectiveOp.ALLREDUCE, CollectiveOp.AGREE}:
            return _reduce_values(ordered, reduce_op)
        raise ProtocolViolation(f"unimplemented collective {op.value}")

    def _next_collective_epoch(self, comm_id: str, op: CollectiveOp) -> int:
        with self._transaction():
            row = self._conn.execute(
                """SELECT next_epoch FROM collective_counters
                   WHERE comm_id=? AND operation=? AND rank=?""",
                (comm_id, op.value, self.rank),
            ).fetchone()
            epoch = 0 if row is None else int(row["next_epoch"])
            self._conn.execute(
                """INSERT INTO collective_counters(
                       comm_id, operation, rank, next_epoch
                   ) VALUES (?, ?, ?, ?)
                   ON CONFLICT(comm_id, operation, rank) DO UPDATE SET
                       next_epoch=excluded.next_epoch""",
                (comm_id, op.value, self.rank, epoch + 1),
            )
            return epoch

    def _next_collective_ordinal(self, comm_id: str) -> int:
        """Return this rank's next communicator-global collective ordinal."""
        with self._transaction():
            row = self._conn.execute(
                """SELECT next_ordinal FROM collective_ordinals
                   WHERE comm_id=? AND rank=?""",
                (comm_id, self.rank),
            ).fetchone()
            if row is None:
                legacy = self._conn.execute(
                    """SELECT COALESCE(SUM(next_epoch), 0) AS next_ordinal
                       FROM collective_counters
                       WHERE comm_id=? AND rank=?""",
                    (comm_id, self.rank),
                ).fetchone()
                ordinal = int(legacy["next_ordinal"])
            else:
                ordinal = int(row["next_ordinal"])
            self._conn.execute(
                """INSERT INTO collective_ordinals(comm_id, rank, next_ordinal)
                   VALUES (?, ?, ?)
                   ON CONFLICT(comm_id, rank) DO UPDATE SET
                       next_ordinal=excluded.next_ordinal""",
                (comm_id, self.rank, ordinal + 1),
            )
            return ordinal

    def _match_message(
        self, comm_id: str, source: int, tag: str, *, mutate: bool = True
    ) -> sqlite3.Row | None:
        conditions = [
            "comm_id=?",
            "dst=?",
            "state='pending'",
        ]
        params: list[Any] = [comm_id, self.rank]
        if source != ANY_SOURCE:
            conditions.append("src=?")
            params.append(source)
        if tag != ANY_TAG:
            conditions.append("tag=?")
            params.append(tag)
        row = self._conn.execute(
            f"""SELECT * FROM messages WHERE {" AND ".join(conditions)}
                ORDER BY created_at, src, sequence LIMIT 1""",
            params,
        ).fetchone()
        if row is not None and mutate:
            self._conn.execute(
                "UPDATE messages SET state='matched', matched_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
        return cast("sqlite3.Row | None", row)

    def _has_posted_receive(self, comm_id: str, source: int, dest: int, tag: str) -> bool:
        row = self._conn.execute(
            """SELECT 1 FROM posted_receives
               WHERE comm_id=? AND dst=?
                 AND (source=? OR source=?)
                 AND (tag=? OR tag=?)
               LIMIT 1""",
            (comm_id, dest, source, ANY_SOURCE, tag, ANY_TAG),
        ).fetchone()
        return row is not None

    def _wait_for_ack(self, message_id: str, timeout: float | None, started: float) -> None:
        while True:
            row = self._conn.execute(
                "SELECT state FROM messages WHERE id=?", (message_id,)
            ).fetchone()
            if row is not None and row["state"] == "acked":
                return
            if timeout is not None and time.monotonic() - started >= timeout:
                raise Timeout("synchronous send timed out waiting for acknowledgement")
            time.sleep(self.poll_interval)

    def _charge_context(self, tokens: int) -> None:
        row = self._conn.execute(
            """SELECT context_budget, context_used FROM agents
               WHERE session_id=? AND rank=?""",
            (self.session_id, self.rank),
        ).fetchone()
        if row is None:
            raise ProcessFailed("agent membership disappeared")
        if int(row["context_used"]) + tokens > int(row["context_budget"]):
            raise ResourceExhausted(
                f"rank {self.rank} context budget exceeded; compact or fetch selectively"
            )
        self._conn.execute(
            """UPDATE agents SET context_used=context_used+?
               WHERE session_id=? AND rank=?""",
            (tokens, self.session_id, self.rank),
        )

    def _validate_comm(self, comm: Communicator, *, allow_revoked: bool = False) -> None:
        if comm.session_id != self.session_id:
            raise ProtocolViolation("communicator belongs to another session")
        current = self.communicator(comm.id)
        if current.generation != comm.generation:
            raise ProtocolViolation("communicator generation mismatch")
        if current.revoked and not allow_revoked:
            raise CommunicatorRevoked(comm.id)

    def _assert_communicator_live(self, comm_id: str) -> None:
        row = self._conn.execute(
            "SELECT revoked FROM communicators WHERE id=?", (comm_id,)
        ).fetchone()
        if row is None:
            raise ProtocolViolation(f"unknown communicator {comm_id}")
        if bool(row["revoked"]):
            raise CommunicatorRevoked(comm_id)

    def _assert_member(self) -> None:
        row = self._conn.execute(
            "SELECT 1 FROM agents WHERE session_id=? AND rank=?",
            (self.session_id, self.rank),
        ).fetchone()
        if row is None:
            raise ProtocolViolation(f"rank {self.rank} is not in session {self.session_id}")

    def _session_row(self) -> sqlite3.Row:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE id=?", (self.session_id,)
        ).fetchone()
        if row is None:
            raise ProtocolViolation(f"unknown session {self.session_id}")
        return cast("sqlite3.Row", row)

    def _event(self, kind: str, data: dict[str, Any]) -> None:
        self._conn.execute(
            """INSERT INTO events(session_id, rank, kind, data_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                self.session_id,
                self.rank,
                kind,
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                time.time(),
            ),
        )

    def _communicator(self, row: sqlite3.Row) -> Communicator:
        return Communicator(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            generation=int(row["generation"]),
            members=tuple(json.loads(row["members_json"])),
            name=str(row["name"]),
            revoked=bool(row["revoked"]),
        )

    def _ensure_schema(self) -> None:
        self._conn.executescript(_SCHEMA)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")


def _reduce_values(values: Sequence[Any], op: ReduceOp) -> Any:
    if not values:
        raise ProtocolViolation("cannot reduce an empty sequence")
    if op is ReduceOp.SUM:
        return sum(values)
    if op is ReduceOp.PRODUCT:
        result = 1
        for value in values:
            result *= value
        return result
    if op is ReduceOp.MIN:
        return min(values)
    if op is ReduceOp.MAX:
        return max(values)
    if op is ReduceOp.CONCAT:
        result_list: list[Any] = []
        for value in values:
            result_list.extend(value if isinstance(value, list) else [value])
        return result_list
    if op is ReduceOp.MERGE:
        merged: dict[str, Any] = {}
        for value in values:
            if not isinstance(value, dict):
                raise ProtocolViolation("merge reduction requires mappings")
            overlap = merged.keys() & value.keys()
            if overlap:
                raise ProtocolViolation(f"merge reduction key conflict: {sorted(overlap)}")
            merged.update(value)
        return merged
    if op is ReduceOp.SET_UNION:
        union: set[Any] = set()
        for value in values:
            union.update(value)
        return sorted(union)
    if op is ReduceOp.ALL:
        return all(values)
    if op is ReduceOp.ANY:
        return any(values)
    raise ProtocolViolation(f"unsupported reduction operation {op.value}")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    created_at REAL NOT NULL,
    state TEXT NOT NULL,
    mailbox_bytes INTEGER NOT NULL,
    inline_token_limit INTEGER NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    session_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    state TEXT NOT NULL,
    heartbeat_at REAL NOT NULL,
    lease_until REAL NOT NULL,
    context_budget INTEGER NOT NULL,
    context_used INTEGER NOT NULL,
    incarnation INTEGER NOT NULL,
    PRIMARY KEY (session_id, rank),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS communicators (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    generation INTEGER NOT NULL,
    members_json TEXT NOT NULL,
    revoked INTEGER NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    comm_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    src INTEGER NOT NULL,
    dst INTEGER NOT NULL,
    tag TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    mode TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_bytes INTEGER NOT NULL,
    payload_tokens INTEGER NOT NULL,
    artifact_ref TEXT,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    matched_at REAL,
    acked_at REAL,
    FOREIGN KEY (session_id) REFERENCES sessions(id),
    FOREIGN KEY (comm_id) REFERENCES communicators(id)
);
CREATE INDEX IF NOT EXISTS idx_messages_match
    ON messages(comm_id, dst, state, src, tag, created_at);
CREATE TABLE IF NOT EXISTS posted_receives (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    comm_id TEXT NOT NULL,
    dst INTEGER NOT NULL,
    source INTEGER NOT NULL,
    tag TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS collective_counters (
    comm_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    rank INTEGER NOT NULL,
    next_epoch INTEGER NOT NULL,
    PRIMARY KEY (comm_id, operation, rank)
);
CREATE TABLE IF NOT EXISTS collective_ordinals (
    comm_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    next_ordinal INTEGER NOT NULL,
    PRIMARY KEY (comm_id, rank)
);
CREATE TABLE IF NOT EXISTS collective_instances (
    comm_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    operation TEXT NOT NULL,
    root INTEGER NOT NULL,
    reduce_op TEXT NOT NULL,
    error TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (comm_id, generation, ordinal)
);
CREATE TABLE IF NOT EXISTS collective_contributions (
    comm_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    operation TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    value_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (comm_id, generation, operation, epoch, rank)
);
CREATE TABLE IF NOT EXISTS collective_results (
    comm_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    operation TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    value_json TEXT NOT NULL,
    completed_at REAL NOT NULL,
    PRIMARY KEY (comm_id, generation, operation, epoch)
);
CREATE TABLE IF NOT EXISTS locks (
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    owner INTEGER NOT NULL,
    lease_until REAL NOT NULL,
    fencing_token INTEGER NOT NULL,
    PRIMARY KEY (session_id, name)
);
CREATE TABLE IF NOT EXISTS artifacts (
    digest TEXT NOT NULL,
    session_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    path TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (digest, session_id)
);
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    kind TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""
