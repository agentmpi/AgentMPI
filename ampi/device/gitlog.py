"""The git device: a transport whose only requirement of the network is a git remote.

Every other bundled device assumes the ranks share a filesystem.  Ranks on
separate machines do not, and the machines this device was written for --- cloud
sandboxes behind NAT, with no inbound port and an egress policy that admits a
handful of hosts --- share nothing at all except a git hosting service.  That is
enough.  A git ref is a compare-and-swap cell: a push that is not a fast-forward is
rejected, so "fetch, apply, commit, push, retry on rejection" is a CAS loop, and
one CAS loop is all the waist needs.  The whole job state is one JSON document on
one branch, and every mutation is one commit.

What this costs.  A mutation is a network round trip, and contention between ``p``
writers serialises at the remote with retries, so latency is seconds rather than
milliseconds and a collective over thirty-two ranks takes on the order of a minute.
That is the point of measuring it: the protocol above the waist does not change,
and the cost model in the paper (α, β, and the operator cost that dwarfs both) is
what says whether a transport this slow is usable.  For an executor whose one step
costs thirty seconds, it is.

What it does not do.  It does not delete branches (the hosting proxy this was
written against refuses deletes); a finished job's branch stays until someone
removes it.  It does not compact: the state document grows with the event trace.
And its clock is the local wall clock, which is comparable across machines only
because every cloud VM is NTP-disciplined; a deployment where that is false should
not use leases shorter than the skew.

Layout.  ``root`` is a working tree on branch ``AMPI_GIT_BRANCH``; ``state.json`` is
the device state; ``job.json`` (the runtime's marker) travels with it so that a
machine joining the job needs nothing but the remote and the branch:

    AMPI_DEVICE=git AMPI_GIT_REMOTE=https://github.com/o/r AMPI_GIT_BRANCH=ampi-jobs/x \\
        AMPI_ROOT=/tmp/x AMPI_RANK=3 ampi init

With no remote configured the device creates a bare repository beside ``root`` and
uses that, which is how the conformance suite exercises the same push/reject path
without a network.
"""

from __future__ import annotations

import fcntl
import itertools
import json
import os
import random
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import STREAMS, Cell, Device, Lease, Predicate, matches, register_device

__all__ = ["GitDevice", "apply_op", "apply_append", "apply_match", "apply_update",
           "apply_cas", "apply_lease", "apply_release", "apply_put_object"]

ENV_REMOTE = "AMPI_GIT_REMOTE"
ENV_BRANCH = "AMPI_GIT_BRANCH"
ENV_READ_INTERVAL = "AMPI_GIT_READ_INTERVAL"
STATE_FILE = "state.json"
CONFIG_FILE = "git.json"
GIT_IDENTITY = ["-c", "user.name=ampi", "-c", "user.email=ampi@agentmpi.invalid",
                "-c", "commit.gpgsign=false"]


def _empty_state() -> dict[str, Any]:
    return {"streams": {s: [] for s in STREAMS}, "cells": {}, "locks": {}, "fence": {},
            "obj": {}, "counter": 0}


def _cell_key(space: str, key: str) -> str:
    return f"{space}\t{key}"


# --------------------------------------------------------------------------
# State transitions, as pure functions
# --------------------------------------------------------------------------
# Each mutating operation is a function of (state, arguments, clock) with no other
# input, so that a rejected push can re-run it against fresher state and --- the
# reason they are module-level rather than closures --- so that the per-node
# daemon (:mod:`ampi.device.gitd`) can apply a whole batch of them in one commit.


def apply_append(state: dict[str, Any], stream: str, record: dict[str, Any], now: float) -> int:
    state["counter"] += 1
    seq = state["counter"]
    rec = dict(record)
    rec["seq"] = seq
    rec.setdefault("ts", now)
    for f in STREAMS[stream]:
        rec.setdefault(f, None)
    state["streams"].setdefault(stream, []).append(rec)
    return seq


def apply_match(state: dict[str, Any], stream: str, predicate: Predicate,
                update: dict[str, Any], order_by: str = "seq") -> dict[str, Any] | None:
    for rec in sorted(state["streams"].get(stream, []), key=lambda r: r.get(order_by) or 0):
        if matches(rec, predicate):
            rec.update(update)
            return dict(rec)
    return None


def apply_update(state: dict[str, Any], stream: str, seq: int, fields: dict[str, Any]) -> bool:
    for rec in state["streams"].get(stream, []):
        if rec.get("seq") == seq:
            rec.update(fields)
            return True
    return False


def apply_cas(state: dict[str, Any], space: str, key: str, expect_version: int | None,
              value: Any, writer: int, epoch: int, meta: dict[str, Any] | None,
              now: float) -> tuple[bool, dict[str, Any]]:
    versions = state["cells"].setdefault(_cell_key(space, key), [])
    current = versions[-1]["version"] if versions else 0
    if expect_version is not None and expect_version != current:
        return False, (dict(versions[-1]) if versions
                       else Cell(space, key, 0, None, -1, 0, now, {}).to_dict())
    cell = Cell(space, key, current + 1, value, writer, epoch, now, meta or {})
    versions.append(cell.to_dict())
    return True, cell.to_dict()


def apply_lease(state: dict[str, Any], space: str, key: str, holder: int, mode: str,
                ttl: float, now: float) -> dict[str, Any] | None:
    locks = state["locks"]
    for lid in [lid for lid, lk in locks.items() if lk["expires_at"] <= now]:
        del locks[lid]
    held = [lk for lk in locks.values() if lk["space"] == space and lk["key"] == key]
    if held and (mode == "exclusive" or any(h["mode"] == "exclusive" for h in held)):
        if len(held) == 1 and held[0]["holder"] == holder and held[0]["mode"] == mode:
            held[0]["expires_at"] = now + ttl
            return dict(held[0])
        return None
    fk = _cell_key(space, key)
    token = state["fence"].get(fk, 0) + 1
    state["fence"][fk] = token
    lease = Lease(uuid.uuid4().hex[:16], space, key, holder, mode, token, now + ttl, now)
    locks[lease.lock_id] = lease.to_dict()
    return lease.to_dict()


def apply_release(state: dict[str, Any], lock_id: str, holder: int) -> bool:
    lk = state["locks"].get(lock_id)
    if lk is None or lk["holder"] != holder:
        return False
    del state["locks"][lock_id]
    return True


def apply_put_object(state: dict[str, Any], digest: str, body: str) -> None:
    state["obj"][digest] = body


def apply_op(state: dict[str, Any], op: str, args: dict[str, Any], now: float) -> Any:
    """Dispatch one serialised mutation; the daemon's batch is a list of these."""
    if op == "append":
        return apply_append(state, args["stream"], args["record"], now)
    if op == "match":
        return apply_match(state, args["stream"], args["predicate"], args["update"],
                           args.get("order_by", "seq"))
    if op == "update":
        return apply_update(state, args["stream"], args["seq"], args["fields"])
    if op == "cas":
        return apply_cas(state, args["space"], args["key"], args.get("expect_version"),
                         args.get("value"), args["writer"], args.get("epoch", 0),
                         args.get("meta"), now)
    if op == "lease":
        return apply_lease(state, args["space"], args["key"], args["holder"],
                           args.get("mode", "exclusive"), args["ttl"], now)
    if op == "release":
        return apply_release(state, args["lock_id"], args["holder"])
    if op == "put_object":
        return apply_put_object(state, args["digest"], args["body"])
    raise ValueError(f"unknown mutation {op!r}")


@register_device
class GitDevice(Device):
    name = "git"
    durable = True
    touch_interval_s = 60.0

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        remote: str | None = None,
        branch: str | None = None,
        max_retries: int = 500,
        read_interval: float | None = None,
    ) -> None:
        self.root = Path(root)
        self._remote = remote or os.environ.get(ENV_REMOTE) or None
        self._branch = branch or os.environ.get(ENV_BRANCH) or None
        self._max_retries = max_retries
        self.read_interval = (read_interval if read_interval is not None
                              else float(os.environ.get(ENV_READ_INTERVAL, "0.5")))
        self._last_fetch = 0.0
        # Readers' parsed copy of the state file, keyed by the file's identity, so
        # that a poll loop that finds the file unchanged does not parse it again.
        self._cached: tuple[tuple[int, int], dict[str, Any]] | None = None
        self._writing = False
        self._tlock = threading.RLock()
        self._depth = 0
        self._counter = itertools.count()
        self.pushes = 0
        self.rejections = 0
        self.fetches = 0

    # -- git plumbing --------------------------------------------------------
    def _git(self, *args: str, check: bool = True, cwd: Path | None = None) -> str:
        r = subprocess.run(["git", *GIT_IDENTITY, *args], cwd=str(cwd or self.root),
                           capture_output=True, text=True)
        if check and r.returncode:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout.strip()

    @property
    def branch(self) -> str:
        if self._branch is None:
            cfg = self.root / CONFIG_FILE
            if cfg.exists():
                self._branch = json.loads(cfg.read_text())["branch"]
            else:
                self._branch = f"ampi-jobs/{self.root.name}"
        return self._branch

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """One writer per root per machine; the remote serialises across machines."""
        with self._tlock:
            if self._depth:
                self._depth += 1
                try:
                    yield
                finally:
                    self._depth -= 1
                return
            self.root.parent.mkdir(parents=True, exist_ok=True)
            with open(self.root.parent / f".{self.root.name}.ampi-git.lock", "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                self._depth = 1
                try:
                    yield
                finally:
                    self._depth = 0
                    fcntl.flock(fh, fcntl.LOCK_UN)

    def _ensure_remote(self) -> str:
        if self._remote:
            return self._remote
        bare = self.root.parent / f"{self.root.name}.remote.git"
        if not (bare / "HEAD").exists():
            bare.mkdir(parents=True, exist_ok=True)
            self._git("init", "-q", "--bare", str(bare), cwd=bare)
        self._remote = str(bare)
        return self._remote

    # -- lifecycle -----------------------------------------------------------
    def initialize(self) -> None:
        with self._locked():
            if (self.root / ".git").exists():
                self._remote = self._remote or self._git("remote", "get-url", "origin")
                return
            remote = self._ensure_remote()
            self.root.mkdir(parents=True, exist_ok=True)
            self._git("init", "-q")
            self._git("remote", "add", "origin", remote)
            ok = subprocess.run(["git", *GIT_IDENTITY, "fetch", "-q", "origin", self.branch],
                                cwd=str(self.root), capture_output=True, text=True)
            if ok.returncode == 0:
                # The job exists on the remote: this machine is joining it.
                self._git("checkout", "-q", "-B", self.branch, f"origin/{self.branch}")
                return
            # A fresh job.  The first push claims the branch; losing the race means
            # another machine created it, so join that instead.
            self._git("checkout", "-q", "--orphan", self.branch)
            self._write_state(_empty_state())
            (self.root / CONFIG_FILE).write_text(json.dumps({"branch": self.branch}, indent=1))
            self._git("add", "-A")
            self._git("commit", "-q", "-m", "ampi: job created")
            r = subprocess.run(["git", *GIT_IDENTITY, "push", "-q", "-u", "origin", self.branch],
                               cwd=str(self.root), capture_output=True, text=True)
            if r.returncode:
                self._git("fetch", "-q", "origin", self.branch)
                self._git("reset", "-q", "--hard", f"origin/{self.branch}")

    def close(self) -> None:
        pass

    def wipe(self) -> None:
        with self._locked():
            if not (self.root / ".git").exists():
                self.initialize()
            self._sync()
            self._write_state(_empty_state())
            for p in self.root.iterdir():
                if p.name not in {".git", STATE_FILE, CONFIG_FILE}:
                    if p.is_dir():
                        subprocess.run(["rm", "-rf", str(p)], check=True)
                    else:
                        p.unlink()
            self._git("add", "-A")
            subprocess.run(["git", *GIT_IDENTITY, "commit", "-q", "-m", "ampi: wipe"],
                           cwd=str(self.root), capture_output=True)
            # A force push races with the other machines' ordinary pushes to the
            # same branch: the remote refuses to move a ref that changed while it
            # was being updated ("cannot lock ref").  The wipe wins by trying again.
            for _attempt in range(20):
                r = subprocess.run(["git", *GIT_IDENTITY, "push", "-q", "--force", "origin",
                                    self.branch], cwd=str(self.root), capture_output=True,
                                   text=True)
                if r.returncode == 0:
                    self.pushes += 1
                    return
                self.rejections += 1
                time.sleep(random.uniform(0.5, 2.0))
            raise RuntimeError(f"git push --force of {self.branch} rejected 20 times: "
                               f"{r.stderr.strip()[-300:]}")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        # Each operation is its own CAS round; the envelope only holds the local
        # lock so that a harness thread's sequence of operations is not
        # interleaved with another thread's on the same root.
        with self._locked():
            yield

    # -- the CAS loop --------------------------------------------------------
    def _sync(self, *, fresh: bool = True) -> dict[str, Any]:
        """Bring the working tree to the remote head and load the state.

        ``fresh=False`` is for readers: a poll loop that fetched within the last
        ``read_interval`` seconds reads the copy it has.  Writers always fetch,
        because a stale base is exactly what the remote will reject.
        """
        if not fresh and time.time() - self._last_fetch < self.read_interval:
            return self._read_state()
        self.fetches += 1
        self._last_fetch = time.time()
        r = subprocess.run(["git", *GIT_IDENTITY, "fetch", "-q", "origin", self.branch],
                           cwd=str(self.root), capture_output=True, text=True)
        if r.returncode == 0:
            local = self._git("rev-parse", "HEAD", check=False)
            remote = self._git("rev-parse", f"origin/{self.branch}")
            if local != remote:
                self._git("reset", "-q", "--hard", f"origin/{self.branch}")
        return self._read_state()

    def _read_state(self) -> dict[str, Any]:
        p = self.root / STATE_FILE
        if not p.exists():
            return _empty_state()
        return json.loads(p.read_text(encoding="utf-8"))

    def _snapshot(self) -> dict[str, Any]:
        """The state as a reader sees it.  Readers must not mutate the result.

        Found at 128 ranks over four machines: readers took the same lock as the
        batching writer, whose CAS loop holds it for the whole push contest with
        the other machines.  A lock is not a queue, so with thirty polling readers
        and a writer that reacquires the moment it releases, one reader could wait
        a quarter of an hour --- longer than its lease --- and be convicted for the
        crime of reading.  Readers now take the lock only to fetch, and a writer
        that fetched within ``read_interval`` has already done that for them: the
        working tree is the remote head, and the state file is replaced
        atomically, so the copy on disk is safe to read without the lock.
        """
        if time.time() - self._last_fetch < self.read_interval or self._writing:
            # While a mutation is in flight the writer fetches at every attempt;
            # a reader that fetched too would only lengthen the writer's cycle
            # and lose it more push contests.  Measured: node 0's daemon lost
            # 88% of its pushes while its readers were fetching between attempts.
            return self._read_cached()
        if not self._try_lock():
            # The writer is mid-contest and may hold the lock for its whole push
            # loop, tens of seconds under contention.  A poll is not worth that
            # wait: the copy on disk is at most one attempt old.  Measured before
            # this branch existed: single reads of 22 s at 128 ranks over four
            # machines, and a loop of a hundred reads that took half an hour.
            return self._read_cached()
        try:
            return self._sync(fresh=False)
        finally:
            self._unlock()

    def _try_lock(self) -> bool:
        """Take the writer's lock without waiting; ``False`` if someone holds it."""
        if not self._tlock.acquire(blocking=False):
            return False
        if self._depth:
            self._depth += 1
            return True
        self.root.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.root.parent / f".{self.root.name}.ampi-git.lock", "w")  # noqa: SIM115
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            self._tlock.release()
            return False
        self._depth = 1
        self._lock_fh = fh
        return True

    def _unlock(self) -> None:
        self._depth -= 1
        if self._depth == 0:
            fh = self.__dict__.pop("_lock_fh", None)
            if fh is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
        self._tlock.release()

    def _read_cached(self) -> dict[str, Any]:
        p = self.root / STATE_FILE
        for _ in range(50):
            try:
                st = p.stat()
                ident = (st.st_mtime_ns, st.st_size)
                cached = self._cached
                if cached is not None and cached[0] == ident:
                    return cached[1]
                state = json.loads(p.read_text(encoding="utf-8"))
                self._cached = (ident, state)
                return state
            except FileNotFoundError:
                if not (self.root / ".git").exists():
                    return _empty_state()
            except json.JSONDecodeError:
                pass
            # git is replacing the file underneath us; it is a small file
            time.sleep(0.02)
        with self._locked():
            return self._read_state()

    def _write_state(self, state: dict[str, Any]) -> None:
        tmp = self.root / (STATE_FILE + ".tmp")
        tmp.write_text(json.dumps(state, separators=(",", ":"), default=list),
                       encoding="utf-8")
        os.replace(tmp, self.root / STATE_FILE)

    def _mutate(self, fn: Any, label: str) -> Any:
        """Apply ``fn(state) -> result`` under the remote's CAS.

        ``fn`` must be a pure function of the state: on a rejected push it is
        re-run against the fresher state, and its first result is discarded.
        """
        with self._locked():
            self._writing = True
            try:
                return self._mutate_locked(fn, label)
            finally:
                self._writing = False

    def _mutate_locked(self, fn: Any, label: str) -> Any:
        for attempt in range(self._max_retries):
            state = self._sync()
            result = fn(state)
            self._write_state(state)
            self._git("add", "-A")
            c = subprocess.run(["git", *GIT_IDENTITY, "commit", "-q", "-m", f"ampi: {label}"],
                               cwd=str(self.root), capture_output=True, text=True)
            if c.returncode and "nothing to commit" in (c.stdout + c.stderr):
                return result
            r = subprocess.run(["git", *GIT_IDENTITY, "push", "-q", "origin",
                                f"HEAD:refs/heads/{self.branch}"],
                               cwd=str(self.root), capture_output=True, text=True)
            if r.returncode == 0:
                self.pushes += 1
                return result
            self.rejections += 1
            # Lost the race: somebody else's commit landed first.  The winner's
            # push takes about a round trip, so a loser that retries at once
            # loses again; spread the losers over a window that grows with the
            # number of consecutive defeats, which is a proxy for how many
            # writers are contending.
            time.sleep(random.uniform(0.1, 0.5 * min(attempt + 1, 10)))
        raise RuntimeError(f"git device: push of {label!r} rejected {self._max_retries} times")

# -- 1-3. streams --------------------------------------------------------
    def append(self, stream: str, record: dict[str, Any]) -> int:
        return self._mutate(lambda st: apply_append(st, stream, record, self.clock()),
                            f"append {stream}")

    def match(
        self,
        stream: str,
        predicate: Predicate,
        update: dict[str, Any],
        *,
        order_by: str = "seq",
    ) -> dict[str, Any] | None:
        return self._mutate(lambda st: apply_match(st, stream, predicate, update, order_by),
                            f"match {stream}")

    def scan(
        self,
        stream: str,
        predicate: Predicate,
        *,
        order_by: str = "seq",
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        state = self._snapshot()
        rows = [dict(r) for r in sorted(state["streams"].get(stream, []),
                                        key=lambda r: r.get(order_by) or 0, reverse=descending)
                if matches(r, predicate)]
        return rows[:limit] if limit is not None else rows

    def update(self, stream: str, seq: int, fields: dict[str, Any]) -> bool:
        return self._mutate(lambda st: apply_update(st, stream, seq, fields), f"update {stream}")

    # -- 4. cells ------------------------------------------------------------
    @staticmethod
    def _cell(d: dict[str, Any]) -> Cell:
        return Cell(**d)

    def read(self, space: str, key: str, *, version: int | None = None) -> Cell | None:
        state = self._snapshot()
        versions = state["cells"].get(_cell_key(space, key)) or []
        if not versions:
            return None
        if version is None:
            return self._cell(versions[-1])
        for c in versions:
            if c["version"] == version:
                return self._cell(c)
        return None

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
        ok, cell = self._mutate(
            lambda st: apply_cas(st, space, key, expect_version, value, writer, epoch, meta,
                                 self.clock()),
            f"cas {space}/{key}")
        return ok, self._cell(cell)

    def keys(self, space: str, *, prefix: str = "") -> list[Cell]:
        state = self._snapshot()
        out = []
        for ck, versions in state["cells"].items():
            sp, key = ck.split("\t", 1)
            if sp == space and key.startswith(prefix) and versions:
                c = versions[-1]
                out.append(Cell(sp, key, c["version"], None, c["writer"], c["epoch"], c["ts"],
                                c["meta"]))
        return sorted(out, key=lambda c: c.key)

    def history(self, space: str, key: str, *, limit: int | None = None) -> list[Cell]:
        state = self._snapshot()
        versions = [self._cell(c) for c in reversed(state["cells"].get(_cell_key(space, key), []))]
        return versions[:limit] if limit is not None else versions

    # -- 5. leases -----------------------------------------------------------
    def lease(
        self, space: str, key: str, *, holder: int, mode: str = "exclusive", ttl: float
    ) -> Lease | None:
        got = self._mutate(
            lambda st: apply_lease(st, space, key, holder, mode, ttl, self.clock()),
            f"lease {space}/{key}")
        return Lease(**got) if got else None

    def release(self, lock_id: str, holder: int) -> bool:
        return self._mutate(lambda st: apply_release(st, lock_id, holder), f"release {lock_id}")

    def leases(self, space: str = "", *, include_expired: bool = False) -> list[Lease]:
        state = self._snapshot()
        now = self.clock()
        return [Lease(**lk) for lk in state["locks"].values()
                if (not space or lk["space"] == space)
                and (include_expired or lk["expires_at"] > now)]

    # -- 6. clock ------------------------------------------------------------
    def clock(self) -> float:
        return time.time()

    # -- object store --------------------------------------------------------
    def put_object(self, digest: str, body: str) -> None:
        self._mutate(lambda st: apply_put_object(st, digest, body), f"put {digest[:12]}")

    def get_object(self, digest: str) -> str | None:
        state = self._snapshot()
        return state["obj"].get(digest)

    # -- introspection -------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        head = self._git("rev-parse", "--short", "HEAD", check=False) if (
            self.root / ".git").exists() else ""
        return {"device": self.name, "durable": True, "remote": self._remote,
                "branch": self.branch, "head": head, "pushes": self.pushes,
                "rejections": self.rejections, "fetches": self.fetches}
