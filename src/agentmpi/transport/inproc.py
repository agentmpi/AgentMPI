"""In-process device.

The analogue of MPICH's Nemesis shared-memory device: same semantics as the
filesystem device, no syscalls.  It exists for two reasons.  First, the test
suite must be able to exercise the full protocol without touching disk.
Second, the scaling microbenchmarks need to run hundreds of ranks with
simulated executors, where filesystem latency would dominate and obscure the
protocol's own costs.

Everything above the device interface is shared with the filesystem device,
so a result obtained here is a statement about the *protocol*, not about the
transport.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Any, Iterable, Iterator, Sequence

from ..envelope import Envelope, content_address
from ..errors import InternalError
from .base import Device


class InprocDevice(Device):
    name = "inproc"
    supports_late_join = True

    def __init__(self, owner: str = "?") -> None:
        self.owner = owner
        self._lock = threading.RLock()
        self._queues: dict[int, deque[Envelope]] = defaultdict(deque)
        self._blobs: dict[str, str] = {}
        self._kv: dict[str, str] = {}
        self._journal: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._acks: set[str] = set()
        self._delivered: set[str] = set()
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)

    # -- messages ----------------------------------------------------------
    def post(self, env: Envelope, payload: str) -> None:
        with self._lock:
            if env.blob is None and payload:
                env.inline = payload
            if env.idem in self._delivered:
                return
            dest = env.dst_world if env.dst_world >= 0 else env.dest
            self._queues[dest].append(env)

    def poll(self, rank: int) -> Iterator[tuple[Envelope, str | None]]:
        with self._lock:
            queue = self._queues.get(rank)
            batch = list(queue) if queue else []
            if queue:
                queue.clear()
        for env in batch:
            with self._lock:
                if env.idem in self._delivered:
                    continue
                self._delivered.add(env.idem)
            yield env, env.inline

    def consume(self, rank: int, env: Envelope) -> None:
        with self._lock:
            self._delivered.add(env.idem)

    def requeue(self, rank: int, env: Envelope) -> None:
        with self._lock:
            self._delivered.discard(env.idem)
            self._queues[rank].appendleft(env)

    def ack(self, rank: int, env: Envelope) -> None:
        with self._lock:
            self._acks.add(env.idem)

    def acked(self, env: Envelope) -> bool:
        with self._lock:
            return env.idem in self._acks

    # -- blobs -------------------------------------------------------------
    def put_blob(self, text: str) -> str:
        addr = content_address(text)
        with self._lock:
            self._blobs.setdefault(addr, text)
        return addr

    def get_blob(self, address: str) -> str:
        with self._lock:
            if address not in self._blobs:
                raise InternalError(f"blob {address} missing")
            return self._blobs[address]

    def has_blob(self, address: str) -> bool:
        with self._lock:
            return address in self._blobs

    # -- key/value ---------------------------------------------------------
    def kv_get(self, key: str) -> str | None:
        with self._lock:
            return self._kv.get(key)

    def kv_put(self, key: str, value: str) -> None:
        with self._lock:
            self._kv[key] = value

    def kv_cas(self, key: str, expected: str | None, value: str) -> bool:
        with self._lock:
            if self._kv.get(key) != expected:
                return False
            self._kv[key] = value
            return True

    def kv_list(self, prefix: str) -> Sequence[str]:
        with self._lock:
            return sorted(k for k in self._kv if k.startswith(prefix))

    def kv_delete(self, key: str) -> None:
        with self._lock:
            self._kv.pop(key, None)

    def lock(self, name: str, lease_s: float = 60.0, timeout_s: float = 300.0):
        device = self

        class _Ctx:
            stolen_from = None

            def acquire(self):
                deadline = time.time() + timeout_s
                lk = device._locks[name]
                while not lk.acquire(timeout=0.05):
                    if time.time() > deadline:
                        from ..errors import RmaConflictError

                        raise RmaConflictError("lock timeout", name=name)
                self._held = lk
                return self

            def release(self):
                held = getattr(self, "_held", None)
                if held is not None:
                    held.release()
                    self._held = None

            def __enter__(self):
                return self.acquire()

            def __exit__(self, *exc):
                self.release()

        return _Ctx()

    # -- journal -----------------------------------------------------------
    def append_journal(self, stream: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._journal[stream].append(record)

    def read_journal(self, stream: str) -> Iterable[dict[str, Any]]:
        with self._lock:
            return list(self._journal.get(stream, ()))
