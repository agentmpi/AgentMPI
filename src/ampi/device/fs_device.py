"""Decentralised POSIX-filesystem device.

The SQLite device routes every operation through one file that all ranks open.
That is convenient and durable, but it puts a serialisation point on the data
path, and it makes the portability claim of the abstract device interface
untestable: a narrow waist with one implementation behind it is just an
indirection.

This device is the counter-example.  It has no central process and no shared
transaction: a message is a file written to a temporary name and then
``rename(2)``-d into the destination rank's mailbox directory, which POSIX
guarantees to be atomic within a filesystem.  Matching scans a mailbox and
claims a message by renaming it again, so two ranks racing on the same wildcard
receive resolve by whichever rename wins --- exactly the atomic claim the ADI
requires, implemented with no coordinator at all.  Counters are files updated
under an ``O_EXCL`` lock directory, which is the classic portable mutex.

The two devices bracket the design space that real MPI implementations occupy:
SQLite behaves like a shared-memory or offloaded fabric where a single agent
sees all traffic, and this one behaves like a distributed-memory fabric where
nobody does.  Running the identical conformance suite and the identical
collective schedules over both is what makes the layering claim empirical.
"""

from __future__ import annotations

import errno
import json
import os
import time
from typing import Any

from .. import util
from .base import Device


class FsDevice(Device):
    """Filesystem-backed AgentMPI device."""

    name = "filesystem"

    def __init__(self, root: str, timeout: float = 60.0) -> None:
        self.root = os.path.abspath(root)
        self.timeout = timeout
        self._depth = 0
        self._held: list[str] = []

    # -- layout ------------------------------------------------------------
    def _stream_dir(self, stream: str) -> str:
        path = os.path.join(self.root, "streams", stream)
        os.makedirs(path, exist_ok=True)
        return path

    def _lock_dir(self, name: str) -> str:
        return os.path.join(self.root, "locks", f"{_safe(name)}.lock")

    def initialize(self) -> None:
        for sub in ("streams", "locks", "counters", "cells", "leases"):
            os.makedirs(os.path.join(self.root, sub), exist_ok=True)

    def close(self) -> None:
        for lock in list(self._held):
            _unlock(lock)
        self._held.clear()

    # -- coarse mutual exclusion for multi-record operations ---------------
    def write_tx(self):  # noqa: ANN201 - mirrors SqliteDevice.write_tx
        return _FsTransaction(self)

    # -- capability 1: append ---------------------------------------------
    def append(self, stream: str, record: dict[str, Any]) -> int | str:
        """Durable append via write-then-rename.

        The sequence number comes from a monotone counter rather than from the
        filesystem, because directory order is not defined and the protocol's
        non-overtaking guarantee needs a total order per stream.
        """
        seq = self.counter_next("_device", f"append:{stream}")
        record = {**record, "_seq": seq}
        directory = self._stream_dir(stream)
        name = f"{seq:012d}-{util.new_id('r')}.json"
        _atomic_write_json(os.path.join(directory, name), record)
        return record.get("handle") or record.get("coll_id") or seq

    # -- capability 2: match ----------------------------------------------
    def match(
        self,
        stream: str,
        predicate: dict[str, Any],
        claimant: str,
        order_by: str = "_seq",
    ) -> dict[str, Any] | None:
        """Claim the oldest matching record by renaming it out of the pool.

        ``rename`` is the atomic primitive: only one racing claimant can
        succeed, and the loser sees ENOENT and moves on to the next candidate.
        No lock is required on the fast path, which is why this device scales
        with rank count where a single-writer store does not.
        """
        directory = self._stream_dir(stream)
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json") or name.startswith("claimed-"):
                continue
            path = os.path.join(directory, name)
            record = _read_json(path)
            if record is None or not _matches(record, predicate):
                continue
            claimed_path = os.path.join(directory, f"claimed-{name}")
            try:
                os.rename(path, claimed_path)
            except OSError as exc:
                if exc.errno in (errno.ENOENT, errno.EEXIST):
                    continue  # lost the race; try the next message
                raise
            record["state"] = "matched"
            record["claimant"] = claimant
            record["matched_at"] = util.now()
            _atomic_write_json(claimed_path, record)
            return record
        return None

    # -- capability 3: compare-and-swap ------------------------------------
    def cas(
        self,
        cell: str,
        key: str,
        expected_version: int | None,
        value: Any,
        actor: int,
    ) -> tuple[bool, int, Any]:
        path = self._cell_path(cell, key)
        with _FsLock(self._lock_dir(f"cell:{cell}:{key}"), self.timeout):
            current = _read_json(path) or {"version": 0, "value": None}
            if expected_version is not None and int(current["version"]) != expected_version:
                return (False, int(current["version"]), current["value"])
            new_version = int(current["version"]) + 1
            _atomic_write_json(path, {
                "version": new_version, "value": value, "updated_by": actor,
                "updated_at": util.now(), "tokens": util.count_tokens(util.dumps(value)),
            })
            return (True, new_version, value)

    def cell_get(self, cell: str, key: str) -> dict[str, Any] | None:
        return _read_json(self._cell_path(cell, key))

    def cell_keys(self, cell: str, prefix: str = "") -> list[str]:
        directory = os.path.join(self.root, "cells", _safe(cell))
        if not os.path.isdir(directory):
            return []
        keys = [_unsafe(n[:-5]) for n in os.listdir(directory) if n.endswith(".json")]
        return sorted(k for k in keys if k.startswith(prefix))

    def _cell_path(self, cell: str, key: str) -> str:
        directory = os.path.join(self.root, "cells", _safe(cell))
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, f"{_safe(key)}.json")

    # -- capability 4: lease ----------------------------------------------
    def lease(
        self,
        cell: str,
        key: str,
        holder: int,
        mode: str,
        ttl: float,
    ) -> str | None:
        path = os.path.join(self.root, "leases", f"{_safe(cell)}--{_safe(key)}.json")
        with _FsLock(self._lock_dir(f"lease:{cell}:{key}"), self.timeout):
            now_ts = util.now()
            active = [
                entry for entry in (_read_json(path) or {"holders": []})["holders"]
                if entry["expires_at"] > now_ts and entry.get("released_at") is None
            ]
            if active:
                blocking = any(a["mode"] == "exclusive" for a in active) or mode == "exclusive"
                if blocking and not all(a["holder"] == holder for a in active):
                    return None
            lock_id = util.new_id("lk")
            active.append({"lock_id": lock_id, "holder": holder, "mode": mode,
                           "acquired_at": now_ts, "expires_at": now_ts + ttl,
                           "released_at": None})
            _atomic_write_json(path, {"holders": active})
            return lock_id

    def release(self, lock_id: str, holder: int) -> bool:
        directory = os.path.join(self.root, "leases")
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            data = _read_json(path)
            if not data:
                continue
            hit = [h for h in data["holders"] if h["lock_id"] == lock_id and h["holder"] == holder]
            if not hit:
                continue
            with _FsLock(self._lock_dir(f"release:{name}"), self.timeout):
                data = _read_json(path) or {"holders": []}
                data["holders"] = [h for h in data["holders"] if h["lock_id"] != lock_id]
                _atomic_write_json(path, data)
            return True
        return False

    # -- capability 5: scan ------------------------------------------------
    def scan(self, stream: str, predicate: dict[str, Any]) -> list[dict[str, Any]]:
        directory = self._stream_dir(stream)
        out: list[dict[str, Any]] = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            record = _read_json(os.path.join(directory, name))
            if record is not None and _matches(record, predicate):
                out.append(record)
        return out

    # -- capability 6: clock ----------------------------------------------
    def clock(self) -> float:
        return util.now()

    # -- counters ----------------------------------------------------------
    def counter_next(self, job_id: str, name: str) -> int:
        path = os.path.join(self.root, "counters", f"{_safe(job_id)}--{_safe(name)}")
        with _FsLock(self._lock_dir(f"ctr:{job_id}:{name}"), self.timeout):
            value = 0
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as fh:
                        value = int(fh.read().strip() or 0)
                except (ValueError, OSError):
                    value = 0
            value += 1
            _atomic_write_text(path, str(value))
            return value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FsTransaction:
    """Coarse job-wide mutual exclusion, used only where atomicity spans records.

    The fast paths --- append and match --- never take it, which is the point:
    a decentralised device must not funnel the data path through one lock.
    """

    def __init__(self, device: FsDevice) -> None:
        self.device = device
        self.lock: _FsLock | None = None

    def __enter__(self) -> FsDevice:
        if self.device._depth == 0:
            self.lock = _FsLock(self.device._lock_dir("job"), self.device.timeout)
            self.lock.__enter__()
        self.device._depth += 1
        return self.device

    def __exit__(self, *exc: Any) -> None:
        self.device._depth -= 1
        if self.device._depth == 0 and self.lock is not None:
            self.lock.__exit__(*exc)
            self.lock = None


class _FsLock:
    """A portable mutex built from ``mkdir``, which is atomic on POSIX.

    Stale locks expire: a holder that dies must not wedge the job, the same
    reasoning that makes AgentMPI window locks leases rather than locks.
    """

    STALE_AFTER = 60.0

    def __init__(self, path: str, timeout: float) -> None:
        self.path = path
        self.timeout = timeout

    def __enter__(self) -> _FsLock:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                os.mkdir(self.path)
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                    if age > self.STALE_AFTER:
                        os.rmdir(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError(
                        f"could not acquire filesystem lock {self.path}"
                    ) from None
                time.sleep(0.005)

    def __exit__(self, *exc: Any) -> None:
        _unlock(self.path)


def _unlock(path: str) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else f"~{ord(c):02x}" for c in str(name))


def _unsafe(name: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(name):
        if name[i] == "~" and i + 2 < len(name):
            out.append(chr(int(name[i + 1:i + 3], 16)))
            i += 3
        else:
            out.append(name[i])
            i += 1
    return "".join(out)


def _atomic_write_json(path: str, payload: Any) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _atomic_write_text(path: str, text: str) -> None:
    tmp = f"{path}.tmp.{os.getpid()}.{util.new_id('t')}"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_json(path: str) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _matches(record: dict[str, Any], predicate: dict[str, Any]) -> bool:
    for key, expected in predicate.items():
        actual = record.get(key)
        if isinstance(expected, tuple) and len(expected) == 2:
            operator, operand = expected
            if operator == "in" and actual not in operand:
                return False
            if operator == "!=" and actual == operand:
                return False
            if operator == "<" and not (actual is not None and actual < operand):
                return False
            if operator == "is" and operand is None and actual is not None:
                return False
        elif actual != expected:
            return False
    return True
