"""The memory device: a non-durable transport for tests and simulation.

It exists for three reasons.  It makes the conformance suite fast enough to run on
every commit; it lets the microbenchmarks measure protocol cost without measuring
the filesystem; and --- most importantly --- it is the device that proves the
waist is real.  A layer that can run unchanged over an in-process dictionary and
over a SQLite file is a layer that does not secretly depend on either.

It is not durable, so it is not conforming for jobs whose ranks are separate
processes.  ``AMPI_Init`` refuses it unless the caller says ``--allow-volatile``.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .base import STREAMS, Cell, Device, Lease, Predicate, matches, register_device

__all__ = ["MemoryDevice"]

_SHARED: dict[str, dict[str, Any]] = {}


def _copy(lease: Lease) -> Lease:
    """Hand out a snapshot, never the live object.

    Returning the stored object let a caller observe a later renewal through an
    earlier handle, which is a difference in behaviour from every durable device
    and therefore a conformance bug.  The suite caught it.
    """
    return Lease(**lease.to_dict())


@register_device
class MemoryDevice(Device):
    name = "memory"
    durable = False

    def __init__(self, root: str = "", **_: Any) -> None:
        # Keying shared state by root lets two Device objects in one process --- a
        # harness and the CLI it shells out to in-process --- see the same job.
        self.root = str(root) or uuid.uuid4().hex
        self._state = _SHARED.setdefault(
            self.root,
            {
                "streams": {s: {} for s in STREAMS},
                "cells": {},
                "locks": {},
                "fence": {},
                "obj": {},
                "counter": itertools.count(1),
                "lock": threading.RLock(),
            },
        )

    # -- lifecycle ---------------------------------------------------------
    def initialize(self) -> None:
        for s in STREAMS:
            self._state["streams"].setdefault(s, {})

    def close(self) -> None:
        pass

    def wipe(self) -> None:
        _SHARED.pop(self.root, None)
        self.__init__(self.root)  # type: ignore[misc]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._state["lock"]:
            yield

    # -- 1-3. streams ------------------------------------------------------
    def append(self, stream: str, record: dict[str, Any]) -> int:
        with self._state["lock"]:
            seq = next(self._state["counter"])
            rec = dict(record)
            rec["seq"] = seq
            rec.setdefault("ts", self.clock())
            for f in STREAMS[stream]:
                rec.setdefault(f, None)
            self._state["streams"][stream][seq] = rec
            return seq

    def match(
        self,
        stream: str,
        predicate: Predicate,
        update: dict[str, Any],
        *,
        order_by: str = "seq",
    ) -> dict[str, Any] | None:
        with self._state["lock"]:
            for rec in sorted(
                self._state["streams"][stream].values(), key=lambda r: r.get(order_by) or 0
            ):
                if matches(rec, predicate):
                    rec.update(update)
                    return dict(rec)
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
        with self._state["lock"]:
            rows = [
                dict(r)
                for r in sorted(
                    self._state["streams"][stream].values(),
                    key=lambda r: r.get(order_by) or 0,
                    reverse=descending,
                )
                if matches(r, predicate)
            ]
        return rows[:limit] if limit is not None else rows

    def update(self, stream: str, seq: int, fields: dict[str, Any]) -> bool:
        with self._state["lock"]:
            rec = self._state["streams"][stream].get(seq)
            if rec is None:
                return False
            rec.update(fields)
            return True

    # -- 4. cells ----------------------------------------------------------
    def read(self, space: str, key: str, *, version: int | None = None) -> Cell | None:
        with self._state["lock"]:
            versions = self._state["cells"].get((space, key))
            if not versions:
                return None
            if version is None:
                return versions[-1]
            for c in versions:
                if c.version == version:
                    return c
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
        with self._state["lock"]:
            versions = self._state["cells"].setdefault((space, key), [])
            current = versions[-1].version if versions else 0
            if expect_version is not None and expect_version != current:
                return False, (
                    versions[-1] if versions else Cell(space, key, 0, None, -1, 0, self.clock(), {})
                )
            cell = Cell(space, key, current + 1, value, writer, epoch, self.clock(), meta or {})
            versions.append(cell)
            return True, cell

    def keys(self, space: str, *, prefix: str = "") -> list[Cell]:
        with self._state["lock"]:
            out = []
            for (sp, key), versions in self._state["cells"].items():
                if sp == space and key.startswith(prefix) and versions:
                    c = versions[-1]
                    out.append(Cell(sp, key, c.version, None, c.writer, c.epoch, c.ts, c.meta))
            return sorted(out, key=lambda c: c.key)

    def history(self, space: str, key: str, *, limit: int | None = None) -> list[Cell]:
        with self._state["lock"]:
            versions = list(reversed(self._state["cells"].get((space, key), [])))
        return versions[:limit] if limit is not None else versions

    # -- 5. leases ---------------------------------------------------------
    def lease(
        self, space: str, key: str, *, holder: int, mode: str = "exclusive", ttl: float
    ) -> Lease | None:
        now = self.clock()
        with self._state["lock"]:
            locks = self._state["locks"]
            for lid, lk in list(locks.items()):
                if lk.expires_at <= now:
                    del locks[lid]
            held = [lk for lk in locks.values() if lk.space == space and lk.key == key]
            if held:
                if mode == "exclusive" or any(h.mode == "exclusive" for h in held):
                    if len(held) == 1 and held[0].holder == holder and held[0].mode == mode:
                        held[0].expires_at = now + ttl
                        return _copy(held[0])
                    return None
            token = self._state["fence"].get((space, key), 0) + 1
            self._state["fence"][(space, key)] = token
            lock_id = uuid.uuid4().hex[:16]
            lease = Lease(lock_id, space, key, holder, mode, token, now + ttl, now)
            locks[lock_id] = lease
            return _copy(lease)

    def release(self, lock_id: str, holder: int) -> bool:
        with self._state["lock"]:
            lk = self._state["locks"].get(lock_id)
            if lk is None or lk.holder != holder:
                return False
            del self._state["locks"][lock_id]
            return True

    def leases(self, space: str = "", *, include_expired: bool = False) -> list[Lease]:
        now = self.clock()
        with self._state["lock"]:
            return [
                _copy(lk)
                for lk in self._state["locks"].values()
                if (not space or lk.space == space) and (include_expired or lk.expires_at > now)
            ]

    # -- 6. clock ----------------------------------------------------------
    def clock(self) -> float:
        return time.time()

    # -- object store ------------------------------------------------------
    def put_object(self, digest: str, body: str) -> None:
        self._state["obj"][digest] = body

    def get_object(self, digest: str) -> str | None:
        return self._state["obj"].get(digest)
