"""Collective I/O over the artifact store.

MPI-IO exists because naive parallel I/O is catastrophic: a thousand ranks
each writing their own small, unaligned piece of a shared file produces a
thousand tiny, conflicting requests and the filesystem collapses.  ROMIO's
answer is *two-phase I/O* -- redistribute the data among a small set of
aggregators so that each aggregator issues one large contiguous write --
plus *data sieving* for reads, plus *file views* so each rank can describe
its slice declaratively instead of computing offsets.

An agent harness writing to a shared repository has the same problem with
the same shape and a worse failure mode.  Twenty agents each committing a
two-line edit to the same file do not merely thrash the filesystem, they
produce merge conflicts, and resolving a merge conflict costs an agent turn.
The fix is the same: elect an aggregator per file, have contributors send it
their patches, and let it write once.  In our software-development
experiment (Section 7.3) this is the difference between a run that spends a
third of its turns on conflict resolution and one that spends none.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .constants import InternalTag
from .errors import FileError
from .ops import lookup_op
from .trace import Event


@dataclass
class FileView:
    """``AMPI_File_set_view`` -- the slice of a shared artifact a rank owns.

    Declaring ownership up front is what makes non-overlapping parallel
    writes safe without any locking, and it turns an overlap -- two agents
    claiming the same region -- into a detectable error at view-setting time
    rather than a merge conflict twenty minutes later.
    """

    path: str
    start: int = 0
    length: int | None = None
    unit: str = "line"       # line | section | file
    tag: str = ""

    def overlaps(self, other: "FileView") -> bool:
        if self.path != other.path or self.unit != other.unit:
            return False
        if self.length is None or other.length is None:
            return True
        return not (self.start + self.length <= other.start
                    or other.start + other.length <= self.start)


@dataclass
class Artifact:
    """A durable, content-addressed output of the run."""

    path: str
    address: str
    tokens: int
    author: int
    version: int
    ts: float
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path, "address": self.address, "tokens": self.tokens,
            "author": self.author, "version": self.version, "ts": self.ts,
            "meta": self.meta,
        }


class CollectiveFile:
    """``AMPI_File`` -- a shared artifact written collectively."""

    def __init__(self, comm, path: str, *, aggregators: int = 1) -> None:
        self.comm = comm
        self.path = path
        self.aggregators = max(1, min(aggregators, comm.size))
        self.view: FileView | None = None
        self.stats = {"writes": 0, "reads": 0, "aggregated": 0, "conflicts": 0}

    # -- views -------------------------------------------------------------
    def set_view(self, view: FileView, *, check: bool = True) -> None:
        """Declare this rank's slice and, collectively, check for overlaps."""
        self.view = view
        if not check or self.comm.size == 1:
            return
        from .collectives import allgather

        views = allgather(self.comm, {"path": view.path, "start": view.start,
                                      "length": view.length, "unit": view.unit,
                                      "tag": view.tag})
        mine = self.comm.rank
        for other_rank, raw in enumerate(views):
            if other_rank == mine or not raw:
                continue
            other = FileView(**raw)
            if view.overlaps(other):
                self.stats["conflicts"] += 1
                self.comm.runtime.profiler.note(
                    "overlapping file views", path=view.path,
                    ranks=[mine, other_rank])

    # -- collective write --------------------------------------------------
    def write_at_all(
        self, content: str, *, op: str = "ampi_concat", timeout: float | None = None
    ) -> Artifact | None:
        """``AMPI_File_write_at_all`` -- two-phase collective write.

        Phase one redistributes: every rank ships its contribution to the
        aggregator responsible for the file.  Phase two writes: the
        aggregator assembles the contributions in rank order and performs a
        single atomic publish.  Only the aggregator ever touches the artifact,
        so there is exactly one writer and therefore no conflict to resolve.
        """
        t0 = time.time()
        aggregator = self._aggregator_for(self.path)
        comm = self.comm
        cid = comm._coll_counter = comm._coll_counter + 1
        tag = int(InternalTag.CTRL) + (cid & 0x3FF)

        if comm.rank != aggregator:
            comm.send({"rank": comm.rank, "content": content,
                       "view": self.view.__dict__ if self.view else None},
                      aggregator, tag, "json")
            self.stats["writes"] += 1
            return None

        pieces: dict[int, str] = {comm.rank: content}
        for _ in range(comm.size - 1):
            msg, st = comm.recv(-1, tag, "json", timeout=timeout)
            if msg:
                pieces[int(msg["rank"])] = msg.get("content") or ""
        ordered = [pieces[r] for r in sorted(pieces) if pieces[r]]
        operation = lookup_op(op)
        merged = operation.apply(ordered) if op != "ampi_concat" else "\n".join(ordered)
        text = merged if isinstance(merged, str) else json.dumps(merged, ensure_ascii=False,
                                                                 indent=2)
        artifact = self._publish(text)
        self.stats["writes"] += 1
        self.stats["aggregated"] += len(pieces)
        comm.runtime.profiler.emit(
            Event(kind="state", ts=time.time(), rank=comm.runtime.world_rank,
                  op="file_write_at_all", context=comm.context,
                  dur=time.time() - t0,
                  detail={"path": self.path, "contributors": len(pieces),
                          "aggregator": aggregator})
        )
        return artifact

    def write_shared(self, content: str) -> Artifact:
        """``AMPI_File_write_shared`` -- append under a shared file pointer.

        Serialised by an atomic counter, so concurrent appenders each get a
        distinct slot and none of them lose a write.  This is the primitive
        behind a shared run log that many agents append to.
        """
        device = self.comm.runtime.device
        key = f"file/{self.comm.context}/{self.path}/cursor"

        def _inc(current: str | None) -> str:
            return str(int(current or 0) + 1)

        slot = int(device.kv_update(key, _inc)) - 1
        device.kv_put(
            f"file/{self.comm.context}/{self.path}/part/{slot:08d}",
            json.dumps({"rank": self.comm.rank, "content": content, "ts": time.time()}),
        )
        self.stats["writes"] += 1
        return Artifact(path=self.path, address=hashlib.sha256(content.encode()).hexdigest(),
                        tokens=0, author=self.comm.rank, version=slot, ts=time.time())

    def read_all(self) -> str:
        """``AMPI_File_read_all`` -- read the assembled artifact."""
        device = self.comm.runtime.device
        raw = device.kv_get(f"file/{self.comm.context}/{self.path}/head")
        self.stats["reads"] += 1
        if raw is None:
            parts = sorted(device.kv_list(f"file/{self.comm.context}/{self.path}/part/"))
            chunks = []
            for key in parts:
                value = device.kv_get(key)
                if value:
                    chunks.append(json.loads(value).get("content", ""))
            return "\n".join(chunks)
        artifact = json.loads(raw)
        return device.get_blob(artifact["address"])

    def _publish(self, text: str) -> Artifact:
        device = self.comm.runtime.device
        address = device.put_blob(text)
        from .tokens import count_tokens

        version_key = f"file/{self.comm.context}/{self.path}/version"

        def _inc(current: str | None) -> str:
            return str(int(current or 0) + 1)

        version = int(device.kv_update(version_key, _inc))
        artifact = Artifact(path=self.path, address=address, tokens=count_tokens(text),
                            author=self.comm.rank, version=version, ts=time.time())
        device.kv_put(f"file/{self.comm.context}/{self.path}/head",
                      json.dumps(artifact.to_json()))
        device.append_journal("artifacts", artifact.to_json())
        return artifact

    def _aggregator_for(self, path: str) -> int:
        """Deterministic aggregator election.

        Hashing the path spreads distinct files across distinct aggregators
        without any communication, which is the same reason ROMIO assigns
        file domains deterministically rather than negotiating them.
        """
        h = int(hashlib.sha256(path.encode()).hexdigest()[:8], 16)
        return h % max(self.aggregators, 1)


def file_open(comm, path: str, *, aggregators: int = 1) -> CollectiveFile:
    """``AMPI_File_open``."""
    return CollectiveFile(comm, path, aggregators=aggregators)


def export_artifacts(comm, out_dir: str | os.PathLike[str]) -> list[str]:
    """Materialise every published artifact into a real directory."""
    device = comm.runtime.device
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for record in device.read_journal("artifacts"):
        try:
            text = device.get_blob(record["address"])
        except Exception:
            continue
        target = out / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(str(target))
    return written
