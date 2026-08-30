"""Filesystem journal device.

This is AgentMPI's portable, cross-process, cross-tool device: the only
substrate it requires is a shared POSIX-ish directory.  That is deliberate.
The ranks of an AgentMPI job are frequently *not* processes we control --
they may be coding agents in separate sandboxes, each of which can run shell
commands but cannot be handed a socket.  A directory is the lowest common
denominator that all of them share, exactly as TCP was for early MPI
implementations.

Layout under ``root``::

    meta.json                     run manifest (immutable after init)
    blobs/<sha256>                payload plane, content addressed
    kv/<escaped-key>              control plane, CAS via atomic rename
    inbox/<ctx>/<rank>/<file>     per-destination message queues
    seen/<rank>/<idem>            per-destination delivery de-duplication
    ack/<idem>                    ingestion acknowledgements
    journal/<stream>.jsonl        append-only event log
    hb/<rank>.json                heartbeats

Concurrency argument.  Every mutation is either (a) a create-exclusive of a
uniquely named file, or (b) a write-to-temp followed by ``os.rename``, which
POSIX requires to be atomic within a filesystem.  Reads never observe a
partial file.  Message queues therefore need no locking at all: a send is
one atomic rename into the destination's directory, and a receive is a
directory listing.  Only compare-and-swap needs exclusion, and it gets it
from ``O_CREAT|O_EXCL`` lock files with an owner stamp and a lease, so a
crashed lock holder cannot wedge the run.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from ..envelope import Envelope, content_address
from ..errors import InternalError, RmaConflictError
from .base import Device

_SAFE = re.compile(r"[^A-Za-z0-9._@=+-]")


def _escape(key: str) -> str:
    """Reversibly escape a key into a single safe filename component."""
    return _SAFE.sub(lambda m: f"%{ord(m.group(0)):02x}", key)


def _unescape(name: str) -> str:
    return re.sub(r"%([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), name)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


class FileLock:
    """Exclusive lock with a lease, so a dead holder cannot wedge the run.

    A lock file records its owner and an expiry.  A contender that finds an
    expired lock steals it, recording the theft in the journal.  This is the
    smallest mechanism that gives us mutual exclusion in the common case and
    liveness in the presence of agent death -- the same trade-off that makes
    leases, rather than locks, the standard primitive in fault-tolerant
    distributed storage.
    """

    def __init__(
        self,
        path: Path,
        owner: str,
        lease_s: float = 60.0,
        poll_s: float = 0.01,
        timeout_s: float = 120.0,
    ) -> None:
        self.path = path
        self.owner = owner
        self.lease_s = lease_s
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.stolen_from: str | None = None

    def _try_acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"owner": self.owner, "expires": time.time() + self.lease_s, "pid": os.getpid()}
        )
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                raise
            return self._try_steal()
        with os.fdopen(fd, "w") as fh:
            fh.write(payload)
        return True

    def _try_steal(self) -> bool:
        try:
            info = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if info.get("expires", 0) > time.time():
            return False
        # Expired.  Steal by an atomic rename of a freshly created candidate;
        # only one contender's rename can win because we then verify.
        candidate = self.path.with_suffix(f".steal-{uuid.uuid4().hex[:8]}")
        _atomic_write(
            candidate,
            json.dumps(
                {"owner": self.owner, "expires": time.time() + self.lease_s, "pid": os.getpid()}
            ),
        )
        try:
            os.replace(candidate, self.path)
        except OSError:
            with contextlib.suppress(OSError):
                candidate.unlink()
            return False
        try:
            check = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return False
        if check.get("owner") == self.owner and check.get("pid") == os.getpid():
            self.stolen_from = info.get("owner")
            return True
        return False

    def acquire(self) -> "FileLock":
        deadline = time.time() + self.timeout_s
        backoff = self.poll_s
        while time.time() < deadline:
            if self._try_acquire():
                return self
            time.sleep(backoff)
            backoff = min(backoff * 1.6, 0.25)
        raise RmaConflictError("lock acquisition timed out", path=str(self.path))

    def release(self) -> None:
        with contextlib.suppress(OSError, json.JSONDecodeError):
            info = json.loads(self.path.read_text())
            if info.get("owner") != self.owner:
                return  # someone stole it; do not delete their lock
            self.path.unlink()

    def __enter__(self) -> "FileLock":
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


class JournalDevice(Device):
    """Shared-directory device."""

    name = "journal"
    supports_late_join = True

    def __init__(self, root: str | os.PathLike[str], owner: str = "?") -> None:
        self.root = Path(root)
        self.owner = owner
        for sub in ("blobs", "kv", "inbox", "seen", "ack", "journal", "hb", "locks"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._seen_cache: set[str] = set()

    # -- messages ----------------------------------------------------------
    def _inbox(self, context: str, rank: int) -> Path:
        return self.root / "inbox" / _escape(context) / str(rank)

    def post(self, env: Envelope, payload: str) -> None:
        if env.blob is None and payload:
            env.inline = payload
        target = self._inbox(env.context, env.dst_world if env.dst_world >= 0
                             else env.dest)
        target.mkdir(parents=True, exist_ok=True)
        # Sortable name: the sequence number first so that a directory
        # listing is already in per-source order, which makes the common
        # case of the matching engine cheap.
        fname = f"{env.seq:012d}-{env.source:05d}-{env.tag:08d}-{env.idem}.json"
        _atomic_write(target / fname, env.to_json())

    def poll(self, rank: int) -> Iterator[tuple[Envelope, str | None]]:
        """Yield messages this rank has not yet *consumed*.

        Polling deliberately does not consume.  A rank that polls a message
        and then exits before matching it -- which is the normal life of a
        rank implemented as a sequence of short-lived ``ampi`` invocations --
        must find that message again in its next process.  Consumption is a
        separate, explicit step (:meth:`consume`), taken only when the
        message is actually matched to a receive.  Getting this wrong is the
        classic at-most-once-versus-at-least-once mistake, and with a durable
        inbox there is no reason to accept message loss.
        """
        base = self.root / "inbox"
        if not base.exists():
            return
        for ctx_dir in sorted(base.iterdir()):
            rank_dir = ctx_dir / str(rank)
            if not rank_dir.is_dir():
                continue
            try:
                names = sorted(os.listdir(rank_dir))
            except FileNotFoundError:
                continue
            for name in names:
                if name.startswith(".tmp-") or not name.endswith(".json"):
                    continue
                idem = name.rsplit("-", 1)[-1].removesuffix(".json")
                if idem in self._seen_cache:
                    continue
                if (self.root / "seen" / str(rank) / idem).exists():
                    self._seen_cache.add(idem)
                    continue
                try:
                    raw = (rank_dir / name).read_text(encoding="utf-8")
                except FileNotFoundError:
                    continue
                if not raw:
                    continue
                try:
                    env = Envelope.from_json(raw)
                except json.JSONDecodeError:
                    continue
                self._seen_cache.add(idem)
                yield env, env.inline

    def consume(self, rank: int, env: Envelope) -> None:
        marker = self.root / "seen" / str(rank) / env.idem
        marker.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            marker.touch()
        self._seen_cache.add(env.idem)

    def requeue(self, rank: int, env: Envelope) -> None:
        marker = self.root / "seen" / str(rank) / env.idem
        with contextlib.suppress(OSError):
            marker.unlink()
        self._seen_cache.discard(env.idem)

    def ack(self, rank: int, env: Envelope) -> None:
        _atomic_write(
            self.root / "ack" / env.idem,
            json.dumps({"rank": rank, "ts": time.time(), "tokens": env.tokens}),
        )

    def acked(self, env: Envelope) -> bool:
        return (self.root / "ack" / env.idem).exists()

    # -- blobs -------------------------------------------------------------
    def put_blob(self, text: str) -> str:
        addr = content_address(text)
        path = self.root / "blobs" / addr
        if not path.exists():
            _atomic_write(path, text)
        return addr

    def get_blob(self, address: str) -> str:
        path = self.root / "blobs" / address
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise InternalError(f"blob {address} missing") from exc

    def has_blob(self, address: str) -> bool:
        return (self.root / "blobs" / address).exists()

    # -- key/value ---------------------------------------------------------
    def _kv_path(self, key: str) -> Path:
        return self.root / "kv" / _escape(key)

    def kv_get(self, key: str) -> str | None:
        try:
            return self._kv_path(key).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def kv_put(self, key: str, value: str) -> None:
        _atomic_write(self._kv_path(key), value)

    def kv_cas(self, key: str, expected: str | None, value: str) -> bool:
        lock = FileLock(self.root / "locks" / (_escape(key) + ".lock"), self.owner, lease_s=30.0)
        with lock:
            current = self.kv_get(key)
            if current != expected:
                return False
            self.kv_put(key, value)
            return True

    def kv_list(self, prefix: str) -> Sequence[str]:
        base = self.root / "kv"
        if not base.exists():
            return []
        out = []
        for entry in os.listdir(base):
            if entry.startswith(".tmp-"):
                continue
            key = _unescape(entry)
            if key.startswith(prefix):
                out.append(key)
        return sorted(out)

    def kv_delete(self, key: str) -> None:
        with contextlib.suppress(OSError):
            self._kv_path(key).unlink()

    def lock(self, name: str, lease_s: float = 60.0, timeout_s: float = 300.0) -> FileLock:
        return FileLock(
            self.root / "locks" / (_escape(name) + ".lock"),
            self.owner,
            lease_s=lease_s,
            timeout_s=timeout_s,
        )

    # -- journal -----------------------------------------------------------
    def append_journal(self, stream: str, record: dict[str, Any]) -> None:
        path = self.root / "journal" / f"{_escape(stream)}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        # A single write() of < PIPE_BUF to a file opened O_APPEND is
        # effectively atomic on Linux for our record sizes; for larger
        # records we take the lock.  Journal records are small by design.
        if len(data) < 4096:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
        else:
            with FileLock(self.root / "locks" / f"j-{_escape(stream)}.lock", self.owner):
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(line)

    def read_journal(self, stream: str) -> Iterable[dict[str, Any]]:
        path = self.root / "journal" / f"{_escape(stream)}.jsonl"
        if not path.exists():
            return []
        out = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # torn final record after a crash
        return out

    # -- housekeeping ------------------------------------------------------
    def destroy(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
