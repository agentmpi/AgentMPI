"""Decentralized filesystem transport.

Each communicator lives in a directory. Point-to-point messages are files
dropped into the destination mailbox via write-then-rename (POSIX atomic).
This is the AgentMPI analog of a reliable network fabric: the harness never
talks to a central broker for data-path operations.

Matching follows the MPI envelope rule: a posted receive with (source, tag)
matches the oldest unexpected message whose source and tag are compatible,
including ANY_SOURCE / ANY_TAG wildcards.

Large payloads use the rendezvous path: the mailbox file holds only the
envelope and an artifact path, so receivers do not ingest unbounded context.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from agentmpi.constants import (
    ANY_SOURCE,
    ANY_TAG,
    COMM_WORLD_NAME,
    DEFAULT_EAGER_BYTES,
    PROTOCOL_VERSION,
)
from agentmpi.errors import ProtocolError
from agentmpi.types import Envelope, Message, RankStatus
from agentmpi.util import (
    append_jsonl,
    atomic_write_json,
    estimate_tokens,
    nbytes_of,
    new_id,
    now,
    read_json,
)


class FilesystemTransport:
    def __init__(self, home: Path, comm: str = COMM_WORLD_NAME, eager_bytes: int = DEFAULT_EAGER_BYTES):
        self.home = Path(home)
        self.comm = comm
        self.eager_bytes = eager_bytes
        self.root = self.home / "comms" / comm
        self.mailboxes = self.root / "mailboxes"
        self.ranks_dir = self.root / "ranks"
        self.artifacts = self.root / "artifacts"
        self.windows = self.root / "windows"
        self.locks = self.root / "locks"
        self.events = self.root / "logs" / "events.jsonl"
        self.meta_path = self.root / "meta.json"

    def bootstrap(self, size: int, extra: dict[str, Any] | None = None) -> None:
        for path in (self.mailboxes, self.ranks_dir, self.artifacts, self.windows, self.locks, self.events.parent):
            path.mkdir(parents=True, exist_ok=True)
        for rank in range(size):
            (self.mailboxes / str(rank)).mkdir(parents=True, exist_ok=True)
        meta = {
            "protocol": PROTOCOL_VERSION,
            "comm": self.comm,
            "size": size,
            "created": now(),
            "eager_bytes": self.eager_bytes,
            "revoked": False,
            "dead": [],
            "generation": 0,
        }
        if extra:
            meta.update(extra)
        if not self.meta_path.exists():
            atomic_write_json(self.meta_path, meta)

    def meta(self) -> dict[str, Any]:
        return read_json(self.meta_path)

    def update_meta(self, **kwargs: Any) -> dict[str, Any]:
        data = self.meta()
        data.update(kwargs)
        atomic_write_json(self.meta_path, data)
        return data

    def mailbox(self, rank: int) -> Path:
        path = self.mailboxes / str(rank)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def emit(self, event: dict[str, Any]) -> None:
        event = {"ts": now(), **event}
        append_jsonl(self.events, event)

    def write_status(self, status: RankStatus) -> None:
        atomic_write_json(self.ranks_dir / f"{status.rank}.json", status.to_dict())

    def read_status(self, rank: int) -> RankStatus | None:
        path = self.ranks_dir / f"{rank}.json"
        if not path.exists():
            return None
        try:
            return RankStatus.from_dict(read_json(path))
        except (OSError, json.JSONDecodeError, TypeError, KeyError):
            return None

    def all_statuses(self, size: int) -> list[RankStatus | None]:
        return [self.read_status(r) for r in range(size)]

    def post(self, src: int, dst: int, tag: int, payload: Any, cid: int = 0) -> Message:
        raw_bytes = nbytes_of(payload)
        eager = raw_bytes <= self.eager_bytes
        msg_id = new_id()
        artifact = None
        on_wire: Any = payload
        if not eager:
            artifact = str(self.artifacts / f"{msg_id}.json")
            atomic_write_json(Path(artifact), payload)
            on_wire = {"_rendezvous": True, "artifact": artifact, "nbytes": raw_bytes}
        env = Envelope(
            protocol=PROTOCOL_VERSION,
            kind="p2p",
            src=src,
            dst=dst,
            tag=tag,
            comm=self.comm,
            cid=cid,
            msg_id=msg_id,
            ts=now(),
            nbytes=raw_bytes,
            eager=eager,
            artifact=artifact,
            tokens=estimate_tokens(payload),
        )
        message = Message(envelope=env, payload=on_wire)
        dest = self.mailbox(dst) / f"{env.ts:.6f}_{src}_{tag}_{msg_id}.msg"
        atomic_write_json(dest, message.to_dict())
        self.emit(
            {
                "event": "send",
                "src": src,
                "dst": dst,
                "tag": tag,
                "bytes": raw_bytes,
                "eager": eager,
                "msg_id": msg_id,
                "cid": cid,
            }
        )
        return Message(envelope=env, payload=payload)

    def _iter_mailbox(self, rank: int) -> Iterable[Path]:
        box = self.mailbox(rank)
        try:
            files = list(box.glob("*.msg"))
        except OSError:
            return []
        return sorted(files, key=lambda p: p.name)

    def _load_file(self, path: Path) -> Message | None:
        try:
            return Message.from_dict(read_json(path))
        except (OSError, json.JSONDecodeError, TypeError, KeyError, ProtocolError):
            return None

    def _materialize(self, message: Message) -> Message:
        payload = message.payload
        if isinstance(payload, dict) and payload.get("_rendezvous") and payload.get("artifact"):
            art = Path(payload["artifact"])
            if art.exists():
                payload = read_json(art)
        return Message(envelope=message.envelope, payload=payload)

    def match(self, rank: int, source: int, tag: int) -> tuple[Path, Message] | None:
        for path in self._iter_mailbox(rank):
            message = self._load_file(path)
            if message is None:
                continue
            src_ok = source == ANY_SOURCE or message.envelope.src == source
            tag_ok = tag == ANY_TAG or message.envelope.tag == tag
            if src_ok and tag_ok:
                return path, self._materialize(message)
        return None

    def consume(self, rank: int, source: int, tag: int) -> Message | None:
        hit = self.match(rank, source, tag)
        if hit is None:
            return None
        path, message = hit
        try:
            path.unlink()
        except OSError:
            return None
        self.emit(
            {
                "event": "recv",
                "src": message.envelope.src,
                "dst": rank,
                "tag": message.envelope.tag,
                "bytes": message.envelope.nbytes,
                "eager": message.envelope.eager,
                "msg_id": message.envelope.msg_id,
            }
        )
        return message

    def probe(self, rank: int, source: int, tag: int) -> Envelope | None:
        hit = self.match(rank, source, tag)
        if hit is None:
            return None
        return hit[1].envelope
