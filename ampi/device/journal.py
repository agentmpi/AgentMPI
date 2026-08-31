"""The filesystem journal device: a second, architecturally independent transport.

Two conforming implementations are what separate a standard from a library.  This
device shares no code with the SQLite one below the interface: records are
individual JSON files under a per-stream directory, sequence numbers come from a
counter file, atomicity comes from an advisory lock on a single lockfile, and
durability comes from write-then-rename.  If the portable layer above works over
both, its semantics are genuinely device independent --- and the conformance suite
runs the same assertions against both to keep it that way.

It also exists because the SQLite device does not work everywhere.  A shared job
directory on NFS is a common way to run agents across hosts, and SQLite's locking
over NFS is famously unsafe; a directory of immutable files with one lockfile is
not fast, but it is correct in places SQLite is not.

Layout::

    <root>/j/
      lock                  advisory lock (flock) serialising every operation
      seq                   the monotone counter
      s/<stream>/<seq>.json one record per file
      c/<space>/<key>/<version>.json   cell versions
      l/<lock_id>.json      leases
      f/<space>/<key>       fencing token
      o/<digest>            object bodies
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import STREAMS, Cell, Device, Lease, Predicate, matches, register_device

__all__ = ["JournalDevice"]


def _q(name: str) -> str:
    """Percent-encode a name so that any key is a safe single path component."""
    return urllib.parse.quote(name, safe="")


def _unq(name: str) -> str:
    return urllib.parse.unquote(name)


_MUTEX_REGISTRY: dict[str, threading.RLock] = {}
_MUTEX_REGISTRY_LOCK = threading.Lock()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@register_device
class JournalDevice(Device):
    name = "journal"
    durable = True

    def __init__(self, root: str | os.PathLike[str], **_: Any) -> None:
        self.root = Path(root)
        self.base = self.root / "j"
        self._lockfile: Any = None
        self._local = threading.local()
        # ``flock`` is held per open file description, so two threads in one
        # process sharing a descriptor do not exclude each other.  Cross-process
        # exclusion needs the flock; in-process exclusion needs this mutex; and
        # the mutex must be shared by every ``JournalDevice`` pointing at the same
        # directory, because a harness may construct several.
        with _MUTEX_REGISTRY_LOCK:
            self._mutex = _MUTEX_REGISTRY.setdefault(str(self.base.resolve()), threading.RLock())

    # -- lifecycle ---------------------------------------------------------
    def initialize(self) -> None:
        for sub in ("s", "c", "l", "f", "o"):
            (self.base / sub).mkdir(parents=True, exist_ok=True)
        for stream in STREAMS:
            (self.base / "s" / stream).mkdir(parents=True, exist_ok=True)
        seq = self.base / "seq"
        if not seq.exists():
            _atomic_write(seq, "0")
        (self.base / "lock").touch(exist_ok=True)

    def close(self) -> None:
        fh = getattr(self._local, "lockfile", None)
        if fh is not None:
            fh.close()
            self._local.lockfile = None

    def wipe(self) -> None:
        import shutil

        self.close()
        if self.base.exists():
            shutil.rmtree(self.base)

    @contextmanager
    def _guard(self) -> Iterator[None]:
        """Serialise operations, across threads with a mutex and across processes
        with an advisory lock.

        Coarse, and deliberately so.  The alternative --- per-record locking ---
        buys throughput we do not need (a rank's operation rate is bounded by an
        executor thinking for seconds) and costs correctness arguments we cannot
        afford to get wrong.
        """
        if getattr(self._local, "depth", 0):
            self._local.depth += 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        with self._mutex:
            fh = getattr(self._local, "lockfile", None)
            if fh is None:
                self.base.mkdir(parents=True, exist_ok=True)
                (self.base / "lock").touch(exist_ok=True)
                fh = open(self.base / "lock", "a+")  # noqa: SIM115
                self._local.lockfile = fh
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            self._local.depth = 1
            try:
                yield
            finally:
                self._local.depth = 0
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._guard():
            yield

    # -- helpers -----------------------------------------------------------
    def _next_seq(self) -> int:
        p = self.base / "seq"
        try:
            n = int(p.read_text()) + 1
        except (FileNotFoundError, ValueError):
            n = 1
        _atomic_write(p, str(n))
        return n

    def _stream_dir(self, stream: str) -> Path:
        d = self.base / "s" / stream
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_stream(self, stream: str) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self._stream_dir(stream).glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, FileNotFoundError):
                continue  # a concurrent writer's partial file; the rename makes this rare
        out.sort(key=lambda r: r.get("seq") or 0)
        return out

    # -- 1-3. streams ------------------------------------------------------
    def append(self, stream: str, record: dict[str, Any]) -> int:
        with self._guard():
            seq = self._next_seq()
            rec = dict(record)
            rec["seq"] = seq
            rec.setdefault("ts", self.clock())
            for f in STREAMS[stream]:
                rec.setdefault(f, None)
            _atomic_write(
                self._stream_dir(stream) / f"{seq:012d}.json",
                json.dumps(rec, ensure_ascii=False),
            )
            return seq

    def match(
        self,
        stream: str,
        predicate: Predicate,
        update: dict[str, Any],
        *,
        order_by: str = "seq",
    ) -> dict[str, Any] | None:
        with self._guard():
            records = self._load_stream(stream)
            records.sort(key=lambda r: r.get(order_by) or 0)
            for rec in records:
                if matches(rec, predicate):
                    rec.update(update)
                    _atomic_write(
                        self._stream_dir(stream) / f"{rec['seq']:012d}.json",
                        json.dumps(rec, ensure_ascii=False),
                    )
                    return rec
        return None

    def scan(
        self,
        stream: str,
        predicate: Predicate,
        *,
        order_by: str = "seq",
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._guard():
            rows = [r for r in self._load_stream(stream) if matches(r, predicate)]
        rows.sort(key=lambda r: r.get(order_by) or 0, reverse=descending)
        return rows[:limit] if limit is not None else rows

    def update(self, stream: str, seq: int, fields: dict[str, Any]) -> bool:
        with self._guard():
            p = self._stream_dir(stream) / f"{seq:012d}.json"
            if not p.exists():
                return False
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec.update(fields)
            _atomic_write(p, json.dumps(rec, ensure_ascii=False))
            return True

    # -- 4. cells ----------------------------------------------------------
    def _cell_dir(self, space: str, key: str) -> Path:
        return self.base / "c" / _q(space) / _q(key)

    def _versions(self, space: str, key: str) -> list[int]:
        d = self._cell_dir(space, key)
        if not d.exists():
            return []
        return sorted(int(p.stem) for p in d.glob("*.json"))

    def _read_version(self, space: str, key: str, version: int) -> Cell | None:
        p = self._cell_dir(space, key) / f"{version}.json"
        if not p.exists():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
        return Cell(
            space=space,
            key=key,
            version=version,
            value=raw.get("value"),
            writer=raw.get("writer", -1),
            epoch=raw.get("epoch", 0),
            ts=raw.get("ts", 0.0),
            meta=raw.get("meta", {}),
        )

    def read(self, space: str, key: str, *, version: int | None = None) -> Cell | None:
        with self._guard():
            versions = self._versions(space, key)
            if not versions:
                return None
            return self._read_version(space, key, version if version is not None else versions[-1])

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
        with self._guard():
            versions = self._versions(space, key)
            current = versions[-1] if versions else 0
            if expect_version is not None and expect_version != current:
                cur = self._read_version(space, key, current) if versions else None
                return False, (cur or Cell(space, key, 0, None, -1, 0, self.clock(), {}))
            nxt = current + 1
            d = self._cell_dir(space, key)
            d.mkdir(parents=True, exist_ok=True)
            ts = self.clock()
            _atomic_write(
                d / f"{nxt}.json",
                json.dumps(
                    {"value": value, "writer": writer, "epoch": epoch, "ts": ts, "meta": meta or {}},
                    ensure_ascii=False,
                ),
            )
            return True, Cell(space, key, nxt, value, writer, epoch, ts, meta or {})

    def keys(self, space: str, *, prefix: str = "") -> list[Cell]:
        with self._guard():
            base = self.base / "c" / _q(space)
            if not base.exists():
                return []
            out = []
            for d in sorted(base.iterdir()):
                key = _unq(d.name)
                if not key.startswith(prefix):
                    continue
                versions = self._versions(space, key)
                if not versions:
                    continue
                cell = self._read_version(space, key, versions[-1])
                if cell is not None:
                    out.append(
                        Cell(space, key, cell.version, None, cell.writer, cell.epoch, cell.ts, cell.meta)
                    )
            return sorted(out, key=lambda c: c.key)

    def history(self, space: str, key: str, *, limit: int | None = None) -> list[Cell]:
        with self._guard():
            versions = sorted(self._versions(space, key), reverse=True)
            cells = [c for v in versions if (c := self._read_version(space, key, v))]
        return cells[:limit] if limit is not None else cells

    # -- 5. leases ---------------------------------------------------------
    def _all_leases(self) -> list[Lease]:
        out = []
        for p in (self.base / "l").glob("*.json"):
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError):
                continue
            out.append(Lease(**raw))
        return out

    def lease(
        self, space: str, key: str, *, holder: int, mode: str = "exclusive", ttl: float
    ) -> Lease | None:
        now = self.clock()
        with self._guard():
            for lk in self._all_leases():
                if lk.expires_at <= now:
                    (self.base / "l" / f"{lk.lock_id}.json").unlink(missing_ok=True)
            held = [lk for lk in self._all_leases() if lk.space == space and lk.key == key]
            if held:
                if mode == "exclusive" or any(h.mode == "exclusive" for h in held):
                    if len(held) == 1 and held[0].holder == holder and held[0].mode == mode:
                        held[0].expires_at = now + ttl
                        _atomic_write(
                            self.base / "l" / f"{held[0].lock_id}.json",
                            json.dumps(held[0].to_dict()),
                        )
                        return held[0]
                    return None
            fpath = self.base / "f" / _q(space)
            fpath.mkdir(parents=True, exist_ok=True)
            tokfile = fpath / _q(key)
            try:
                token = int(tokfile.read_text()) + 1
            except (FileNotFoundError, ValueError):
                token = 1
            _atomic_write(tokfile, str(token))
            lease = Lease(uuid.uuid4().hex[:16], space, key, holder, mode, token, now + ttl, now)
            _atomic_write(self.base / "l" / f"{lease.lock_id}.json", json.dumps(lease.to_dict()))
            return lease

    def release(self, lock_id: str, holder: int) -> bool:
        with self._guard():
            p = self.base / "l" / f"{lock_id}.json"
            if not p.exists():
                return False
            raw = json.loads(p.read_text(encoding="utf-8"))
            if raw.get("holder") != holder:
                return False
            p.unlink(missing_ok=True)
            return True

    def leases(self, space: str = "", *, include_expired: bool = False) -> list[Lease]:
        now = self.clock()
        with self._guard():
            return [
                lk
                for lk in self._all_leases()
                if (not space or lk.space == space) and (include_expired or lk.expires_at > now)
            ]

    # -- 6. clock ----------------------------------------------------------
    def clock(self) -> float:
        return time.time()

    # -- object store ------------------------------------------------------
    def put_object(self, digest: str, body: str) -> None:
        with self._guard():
            p = self.base / "o" / digest
            if not p.exists():
                _atomic_write(p, body)

    def get_object(self, digest: str) -> str | None:
        p = self.base / "o" / digest
        return p.read_text(encoding="utf-8") if p.exists() else None

    def stats(self) -> dict[str, Any]:
        out = super().stats()
        out["path"] = str(self.base)
        return out
