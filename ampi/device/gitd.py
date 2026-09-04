"""The git device behind a per-node daemon: intra-node aggregation for a slow link.

The git device (:mod:`ampi.device.gitlog`) makes one commit and one push per
mutation, and a push to a hosted remote is a three-second round trip.  Thirty-two
ranks on one machine, each making a hundred mutations over a run, would spend an
hour and a half of serialised pushes on protocol traffic alone --- and eight such
machines contending for one branch would multiply that by their rejection rate.
The E5 record at p=32, one rank per machine, was 1131 pushes against 13415
rejections.

HPC met this problem first.  Every production MPI does intra-node aggregation:
ranks on one node combine their contributions in shared memory and one of them
speaks to the network, so the number of messages on the wire is the number of
nodes, not the number of ranks.  This module is that, for git.

One daemon per node owns the working tree.  Rank processes on the node talk to it
over a Unix socket with the same six operations; reads are answered from the
daemon's copy of the state (refreshed from the remote at ``read_interval``), and
writes are **group-committed**: every mutation that arrives while a push is in
flight, or within ``batch_window`` of the first, is applied to the state in
arrival order and pushed as one commit.  The ordinary CAS loop still applies ---
a rejected push re-fetches and re-applies the whole batch, which is sound because
every mutation is a pure function of the state (see ``apply_op``) --- so the
guarantee each rank sees is unchanged: its call returns only once its write is
on the remote.  What changes is the cost: one round trip per batch, and the
number of pushes on the wire is bounded by the node's rate of *bursts*, not its
rate of operations.

The client, :class:`GitdDevice`, is registered as the ``gitd`` device and passes
the same conformance suite as every other transport.  It starts a daemon itself
if none is listening for its root, so ``open_device("gitd", root)`` is enough;
``ampirun`` starts one explicitly before its ranks and stops it after.  Nothing
above the waist knows the daemon exists.

Wire format: newline-delimited JSON, one request per line, one reply per line,
over a persistent connection.  Predicates travel as ``{"$in": [...]}`` and the
like; cells and leases as their dictionaries.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import queue
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .base import (
    Cell,
    Device,
    Ge,
    Gt,
    In,
    IsNull,
    Le,
    Lease,
    Lt,
    Ne,
    NotIn,
    NotNull,
    Predicate,
    register_device,
)
from .gitlog import ENV_BRANCH, ENV_REMOTE, GitDevice, apply_op

__all__ = ["GitdDevice", "GitDaemon", "socket_path", "main"]

ENV_IDLE = "AMPI_GITD_IDLE_S"
ENV_BATCH = "AMPI_GITD_BATCH_S"
ENV_READ_INTERVAL = "AMPI_GITD_READ_INTERVAL"
DEFAULT_IDLE_S = 300.0
DEFAULT_BATCH_S = 0.25
MAX_BATCH_S = 4.0
DEFAULT_READ_INTERVAL = 2.0
MAX_BATCH = 2000
#: Replies the daemon remembers so a client that resends a request after an
#: ambiguous reply (the connection closed after the batch landed) gets the
#: original outcome instead of a second application.
RECENT_REPLIES = 8192
MUTATIONS = frozenset({"append", "match", "update", "cas", "lease", "release", "put_object"})
_PLACEHOLDER = object()


def socket_path(root: str | os.PathLike[str]) -> str:
    """A short, stable socket path: Unix sockets are limited to ~100 characters."""
    digest = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"/tmp/ampi-gitd-{digest}.sock"


# --------------------------------------------------------------------------
# Wire encoding
# --------------------------------------------------------------------------


def encode_predicate(pred: Predicate) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pred.items():
        if isinstance(v, In):
            out[k] = {"$in": list(v.values)}
        elif isinstance(v, NotIn):
            out[k] = {"$nin": list(v.values)}
        elif isinstance(v, Lt):
            out[k] = {"$lt": v.value}
        elif isinstance(v, Le):
            out[k] = {"$le": v.value}
        elif isinstance(v, Gt):
            out[k] = {"$gt": v.value}
        elif isinstance(v, Ge):
            out[k] = {"$ge": v.value}
        elif isinstance(v, Ne):
            out[k] = {"$ne": v.value}
        elif isinstance(v, IsNull):
            out[k] = {"$null": True}
        elif isinstance(v, NotNull):
            out[k] = {"$notnull": True}
        else:
            out[k] = {"$eq": v}
    return out


def decode_predicate(raw: dict[str, Any]) -> Predicate:
    out: Predicate = {}
    for k, v in raw.items():
        if not isinstance(v, dict) or len(v) != 1:
            out[k] = v
            continue
        (tag, arg), = v.items()
        out[k] = {
            "$in": lambda a: In(a), "$nin": lambda a: NotIn(a), "$lt": Lt, "$le": Le,
            "$gt": Gt, "$ge": Ge, "$ne": Ne, "$null": lambda a: IsNull(),
            "$notnull": lambda a: NotNull(), "$eq": lambda a: a,
        }[tag](arg)
    return out


# --------------------------------------------------------------------------
# The daemon
# --------------------------------------------------------------------------


class GitDaemon:
    """Own one working tree; serve local ranks; group-commit their writes."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        remote: str | None = None,
        branch: str | None = None,
        read_interval: float | None = None,
        batch_window: float | None = None,
        idle_s: float | None = None,
        sock: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.dev = GitDevice(
            root, remote=remote, branch=branch,
            read_interval=(read_interval if read_interval is not None
                           else float(os.environ.get(ENV_READ_INTERVAL, DEFAULT_READ_INTERVAL))),
        )
        self.base_window = (batch_window if batch_window is not None
                            else float(os.environ.get(ENV_BATCH, DEFAULT_BATCH_S)))
        #: The current batch window.  It widens while pushes are being rejected
        #: --- other machines are writing the same branch, and the way to win
        #: more often is to push less often with more in each push --- and
        #: relaxes back to the base window when pushes land first time.
        self.batch_window = self.base_window
        self.idle_s = idle_s if idle_s is not None else float(os.environ.get(ENV_IDLE, DEFAULT_IDLE_S))
        self.sock = sock or socket_path(root)
        self._q: queue.Queue[tuple[str, dict[str, Any], threading.Event, list[Any]]] = queue.Queue()
        self._clients = 0
        self._clients_lock = threading.Lock()
        self._last_activity = time.time()
        self._stop = threading.Event()
        self.batches = 0
        self.batched_ops = 0
        self.largest_batch = 0
        # (client token, request id) -> outcome, for mutations.  A client whose
        # connection died between the commit and the reply resends the same
        # request; without this the daemon would apply it twice.  The window that
        # remains is a daemon restart, which forgets the table.
        self._recent: OrderedDict[tuple[str, int], tuple[str, Any]] = OrderedDict()
        self._recent_lock = threading.Lock()

    # -- writes ------------------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                first = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            batch = [first]
            deadline = time.time() + self.batch_window
            while len(batch) < MAX_BATCH:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    batch.append(self._q.get(timeout=remaining))
                except queue.Empty:
                    break
            rejected_before = self.dev.rejections
            results = self._commit(batch)
            if self.dev.rejections > rejected_before:
                self.batch_window = min(MAX_BATCH_S, self.batch_window * 2)
            else:
                self.batch_window = max(self.base_window, self.batch_window / 2)
            self.batches += 1
            self.batched_ops += len(batch)
            self.largest_batch = max(self.largest_batch, len(batch))
            for (_op, _args, ev, slot), res in zip(batch, results, strict=True):
                slot.append(res)
                ev.set()

    def _commit(self, batch: list[tuple[str, dict[str, Any], threading.Event, list[Any]]]
                ) -> list[tuple[str, Any]]:
        """One CAS round for a whole batch; re-run from scratch on every attempt."""
        results: list[tuple[str, Any]] = [("error", "not applied")] * len(batch)

        def fn(state: dict[str, Any]) -> None:
            for i, (op, args, _ev, _slot) in enumerate(batch):
                results[i] = ("ok", apply_op(state, op, args, self.dev.clock()))

        try:
            self.dev._mutate(fn, f"batch of {len(batch)}")  # noqa: SLF001 - the CAS loop
        except Exception as exc:  # noqa: BLE001 - every waiter must learn the outcome
            return [("error", f"{type(exc).__name__}: {exc}")] * len(batch)
        return results

    def recall(self, key: tuple[str, int] | None) -> tuple[str, Any] | None:
        if key is None:
            return None
        with self._recent_lock:
            return self._recent.get(key)

    def remember(self, key: tuple[str, int] | None, outcome: tuple[str, Any]) -> None:
        if key is None:
            return
        with self._recent_lock:
            self._recent[key] = outcome
            while len(self._recent) > RECENT_REPLIES:
                self._recent.popitem(last=False)

    def enqueue(self, op: str, args: dict[str, Any]) -> tuple[threading.Event, list[Any]]:
        ev = threading.Event()
        slot: list[Any] = []
        self._q.put((op, args, ev, slot))
        return ev, slot

    def mutate(self, op: str, args: dict[str, Any]) -> Any:
        ev, slot = self.enqueue(op, args)
        ev.wait()
        status, value = slot[0]
        if status == "error":
            raise RuntimeError(value)
        return value

    # -- request dispatch ---------------------------------------------------------
    def handle(self, op: str, args: dict[str, Any]) -> Any:
        self._last_activity = time.time()
        d = self.dev
        if op in MUTATIONS:
            if op == "match":
                args = {**args, "predicate": decode_predicate(args["predicate"])}
            return self.mutate(op, args)
        if op == "scan":
            return d.scan(args["stream"], decode_predicate(args["predicate"]),
                          order_by=args.get("order_by", "seq"),
                          descending=bool(args.get("descending")), limit=args.get("limit"))
        if op == "read":
            c = d.read(args["space"], args["key"], version=args.get("version"))
            return c.to_dict() if c else None
        if op == "keys":
            return [c.to_dict() for c in d.keys(args["space"], prefix=args.get("prefix", ""))]
        if op == "history":
            return [c.to_dict() for c in d.history(args["space"], args["key"],
                                                   limit=args.get("limit"))]
        if op == "leases":
            return [lk.to_dict() for lk in d.leases(args.get("space", ""),
                                                    include_expired=bool(args.get("include_expired")))]
        if op == "get_object":
            return d.get_object(args["digest"])
        if op == "clock":
            return d.clock()
        if op == "wipe":
            d.wipe()
            return True
        if op == "stats":
            return {**d.stats(), "daemon": {"pid": os.getpid(), "clients": self._clients,
                                            "batches": self.batches, "batched_ops": self.batched_ops,
                                            "largest_batch": self.largest_batch,
                                            "batch_window_s": self.batch_window,
                                            "base_window_s": self.base_window}}
        if op == "ping":
            return "pong"
        if op == "hello":
            return {"pid": os.getpid(), "root": str(self.root.resolve()),
                    "remote": d._remote, "branch": d.branch,  # noqa: SLF001
                    "root_exists": self.root.exists()}
        if op == "shutdown":
            self._stop.set()
            return True
        raise ValueError(f"unknown op {op!r}")

    # -- serving ------------------------------------------------------------------
    def serve(self) -> None:
        self.dev.initialize()
        daemon = self

        class Handler(socketserver.StreamRequestHandler):
            """One connection: requests are read as fast as they arrive, and
            replies are written in request order by a responder thread.

            A mutation is *enqueued* here and answered when its batch lands,
            so a client that pipelines sends the daemon a burst rather than a
            trickle --- the whole point of group commit.  Reads are answered at
            once but still go through the responder so order is preserved."""

            def handle(self) -> None:
                with daemon._clients_lock:
                    daemon._clients += 1
                pending: queue.Queue[tuple[threading.Event, list[Any], Any, Any] | None] = queue.Queue()

                def respond() -> None:
                    while True:
                        item = pending.get()
                        if item is None:
                            return
                        ev, slot, rid, key = item
                        ev.wait()
                        status, value = slot[0] if slot else ("error", "no result")
                        daemon.remember(key, (status, value))
                        out = ({"id": rid, "ok": True, "result": value} if status == "ok"
                               else {"id": rid, "ok": False, "error": value})
                        try:
                            self.wfile.write((json.dumps(out, default=str) + "\n").encode("utf-8"))
                            self.wfile.flush()
                        except OSError:
                            return

                responder = threading.Thread(target=respond, daemon=True)
                responder.start()
                try:
                    for line in self.rfile:
                        rid = None
                        key = None
                        try:
                            req = json.loads(line)
                            rid = req.get("id")
                            op, args = req["op"], req.get("args") or {}
                            daemon._last_activity = time.time()
                            if op in MUTATIONS:
                                if req.get("client") is not None and rid is not None:
                                    key = (str(req["client"]), int(rid))
                                seen = daemon.recall(key)
                                if seen is not None:
                                    ev, slot = threading.Event(), [seen]
                                    ev.set()
                                    key = None
                                else:
                                    if op == "match":
                                        args = {**args,
                                                "predicate": decode_predicate(args["predicate"])}
                                    ev, slot = daemon.enqueue(op, args)
                            else:
                                ev, slot = threading.Event(), [("ok", daemon.handle(op, args))]
                                ev.set()
                        except Exception as exc:  # noqa: BLE001 - reported to the client
                            ev, slot = threading.Event(), [("error", f"{type(exc).__name__}: {exc}")]
                            ev.set()
                        pending.put((ev, slot, rid, key))
                        if daemon._stop.is_set():
                            break
                finally:
                    pending.put(None)
                    responder.join(timeout=30)
                    with daemon._clients_lock:
                        daemon._clients -= 1
                    daemon._last_activity = time.time()

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
            allow_reuse_address = True

        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.sock)
        server = Server(self.sock, Handler)
        worker = threading.Thread(target=self._worker, daemon=True)
        worker.start()
        t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2},
                             daemon=True)
        t.start()
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
                idle = self._clients == 0 and time.time() - self._last_activity > self.idle_s
                if idle:
                    break
        finally:
            self._stop.set()
            server.shutdown()
            server.server_close()
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.sock)


# --------------------------------------------------------------------------
# The client device
# --------------------------------------------------------------------------


@register_device
class GitdDevice(Device):
    name = "gitd"
    durable = True
    touch_interval_s = 60.0

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        remote: str | None = None,
        branch: str | None = None,
        spawn: bool = True,
        connect_timeout: float = 120.0,
    ) -> None:
        self.root = Path(root)
        self._remote = remote or os.environ.get(ENV_REMOTE) or None
        self._branch = branch or os.environ.get(ENV_BRANCH) or None
        self.sock_path = socket_path(root)
        self.spawn = spawn
        self.connect_timeout = connect_timeout
        self._sock: socket.socket | None = None
        self._rfile: Any = None
        # Reentrant: a call that must first initialise the connection runs the
        # staleness check, which is itself a call.
        self._lock = threading.RLock()
        self._ids = 0
        # Identifies this client's request stream to the daemon, so a request
        # resent after an ambiguous reply is recognised rather than re-applied.
        self._client = uuid.uuid4().hex
        self.calls = 0
        self._pipelined: list[str] | None = None

    # -- connection ---------------------------------------------------------------
    def _connect(self) -> bool:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self.sock_path)
        except OSError:
            s.close()
            return False
        self._sock = s
        self._rfile = s.makefile("rb")
        return True

    def _spawn(self) -> None:
        cmd = [sys.executable, "-c",
               "import sys; from ampi.device.gitd import main; sys.exit(main(sys.argv[1:]))",
               "--root", str(self.root)]
        if self._remote:
            cmd += ["--remote", self._remote]
        if self._branch:
            cmd += ["--branch", self._branch]
        log = open(f"{self.sock_path}.log", "ab")  # noqa: SIM115 - inherited by the daemon
        subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True,
                         cwd=str(Path.cwd()))
        log.close()

    def _stale(self) -> str:
        """Why the daemon we reached is not the one this root needs, or ''."""
        try:
            h = self._call("hello")
        except Exception as exc:  # noqa: BLE001 - an unreachable daemon is stale
            return f"unreachable: {exc}"
        if not h.get("root_exists"):
            return "its working tree has been removed"
        if self._remote and h.get("remote") and h["remote"] != self._remote:
            return f"it serves remote {h['remote']!r}, not {self._remote!r}"
        if self._branch and h.get("branch") != self._branch:
            return f"it serves branch {h['branch']!r}, not {self._branch!r}"
        return ""

    def _replace_stale(self) -> None:
        """A daemon left over from an earlier job at the same root is shut down.

        Found the hard way: a test removed a root and recreated it, the old
        daemon kept answering from memory, and the new job's ranks did not exist
        as far as it was concerned.
        """
        with contextlib.suppress(Exception):
            self._call("shutdown")
        self.close()
        deadline = time.time() + 10
        while time.time() < deadline and os.path.exists(self.sock_path):
            time.sleep(0.1)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(self.sock_path)

    def initialize(self) -> None:
        if self._sock is not None:
            return
        if self._connect():
            why = self._stale()
            if not why:
                return
            self._replace_stale()
        if not self.spawn:
            raise RuntimeError(f"no gitd daemon listening at {self.sock_path}")
        # One spawner per socket: several rank processes starting at once must
        # not each start a daemon.
        import fcntl

        deadline = time.time() + self.connect_timeout
        with open(f"{self.sock_path}.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                if self._connect():
                    return
                self._spawn()
                while time.time() < deadline:
                    time.sleep(0.2)
                    if self._connect():
                        return
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        raise RuntimeError(f"gitd daemon for {self.root} did not come up within "
                           f"{self.connect_timeout:.0f}s; see {self.sock_path}.log")

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                with contextlib.suppress(OSError):
                    self._sock.close()
                self._sock = None
                self._rfile = None

    @contextmanager
    def pipeline(self) -> Iterator[None]:
        """Send mutations without waiting for each reply; collect them all at exit.

        Inside the block a mutation's return value is a placeholder, so this is
        only for callers that do not read one --- ``Ampi.create`` writing a rank
        cell per rank is the case it exists for.  The daemon still applies the
        requests in order and the connection delivers replies in order, so an
        error in any of them is raised at exit.  What it buys: the daemon sees
        the requests back to back and group-commits them, so creating a
        256-rank job is a handful of pushes rather than five hundred.
        """
        with self._lock:
            if self._sock is None:
                self.initialize()
            self._pipelined = []
        try:
            yield
        finally:
            with self._lock:
                pending, self._pipelined = self._pipelined or [], None
                errors = []
                for _ in pending:
                    raw = self._rfile.readline()
                    reply = json.loads(raw) if raw else {"ok": False, "error": "closed"}
                    if not reply.get("ok"):
                        errors.append(reply.get("error"))
            if errors:
                raise RuntimeError(f"gitd: pipelined request failed: {errors[0]}")

    def _call(self, op: str, **args: Any) -> Any:
        with self._lock:
            if self._sock is None:
                self.initialize()
            self._ids += 1
            line = json.dumps({"id": self._ids, "client": self._client, "op": op, "args": args},
                              default=list) + "\n"
            if self._pipelined is not None and op in MUTATIONS:
                assert self._sock is not None
                self._sock.sendall(line.encode("utf-8"))
                self._pipelined.append(op)
                self.calls += 1
                return _PLACEHOLDER
            for attempt in range(2):
                try:
                    assert self._sock is not None
                    self._sock.sendall(line.encode("utf-8"))
                    raw = self._rfile.readline()
                    if not raw:
                        raise ConnectionError("daemon closed the connection")
                    break
                except (OSError, ConnectionError):
                    if attempt == 1:
                        raise
                    self._sock = None
                    self.initialize()
            self.calls += 1
        reply = json.loads(raw)
        if not reply.get("ok"):
            raise RuntimeError(f"gitd: {reply.get('error')}")
        return reply.get("result")

    # -- lifecycle ---------------------------------------------------------------
    def wipe(self) -> None:
        self._call("wipe")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    # -- streams -----------------------------------------------------------------
    def append(self, stream: str, record: dict[str, Any]) -> int:
        got = self._call("append", stream=stream, record=record)
        return 0 if got is _PLACEHOLDER else int(got)

    def match(self, stream: str, predicate: Predicate, update: dict[str, Any], *,
              order_by: str = "seq") -> dict[str, Any] | None:
        return self._call("match", stream=stream, predicate=encode_predicate(predicate),
                          update=update, order_by=order_by)

    def scan(self, stream: str, predicate: Predicate, *, order_by: str = "seq",
             descending: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
        return self._call("scan", stream=stream, predicate=encode_predicate(predicate),
                          order_by=order_by, descending=descending, limit=limit)

    def update(self, stream: str, seq: int, fields: dict[str, Any]) -> bool:
        return bool(self._call("update", stream=stream, seq=seq, fields=fields))

    # -- cells -------------------------------------------------------------------
    def read(self, space: str, key: str, *, version: int | None = None) -> Cell | None:
        c = self._call("read", space=space, key=key, version=version)
        return Cell(**c) if c else None

    def cas(self, space: str, key: str, expect_version: int | None, value: Any, *,
            writer: int, epoch: int = 0, meta: dict[str, Any] | None = None) -> tuple[bool, Cell]:
        got = self._call("cas", space=space, key=key, expect_version=expect_version,
                         value=value, writer=writer, epoch=epoch, meta=meta)
        if got is _PLACEHOLDER:
            return True, Cell(space, key, 0, value, writer, epoch, time.time(), meta or {})
        ok, cell = got
        return bool(ok), Cell(**cell)

    def keys(self, space: str, *, prefix: str = "") -> list[Cell]:
        return [Cell(**c) for c in self._call("keys", space=space, prefix=prefix)]

    def history(self, space: str, key: str, *, limit: int | None = None) -> list[Cell]:
        return [Cell(**c) for c in self._call("history", space=space, key=key, limit=limit)]

    # -- leases ------------------------------------------------------------------
    def lease(self, space: str, key: str, *, holder: int, mode: str = "exclusive",
              ttl: float) -> Lease | None:
        got = self._call("lease", space=space, key=key, holder=holder, mode=mode, ttl=ttl)
        return Lease(**got) if got else None

    def release(self, lock_id: str, holder: int) -> bool:
        return bool(self._call("release", lock_id=lock_id, holder=holder))

    def leases(self, space: str = "", *, include_expired: bool = False) -> list[Lease]:
        return [Lease(**lk) for lk in self._call("leases", space=space,
                                                 include_expired=include_expired)]

    # -- clock and objects ---------------------------------------------------------
    def clock(self) -> float:
        return time.time()

    def put_object(self, digest: str, body: str) -> None:
        self._call("put_object", digest=digest, body=body)

    def get_object(self, digest: str) -> str | None:
        return self._call("get_object", digest=digest)

    def stats(self) -> dict[str, Any]:
        try:
            base = self._call("stats")
        except Exception as exc:  # noqa: BLE001 - stats must not fail a run
            base = {"error": str(exc)}
        return {**base, "device": self.name, "client_calls": self.calls, "socket": self.sock_path}

    def shutdown_daemon(self) -> None:
        with contextlib.suppress(Exception):
            self._call("shutdown")
        self.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ampi-gitd", description="per-node git device daemon")
    ap.add_argument("--root", required=True)
    ap.add_argument("--remote", default=None)
    ap.add_argument("--branch", default=None)
    ap.add_argument("--read-interval", type=float, default=None)
    ap.add_argument("--batch-window", type=float, default=None)
    ap.add_argument("--idle", type=float, default=None, help="exit after this long with no client")
    a = ap.parse_args(argv)
    GitDaemon(a.root, remote=a.remote, branch=a.branch, read_interval=a.read_interval,
              batch_window=a.batch_window, idle_s=a.idle).serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
