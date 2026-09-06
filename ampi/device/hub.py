"""The hub device: one authoritative server, reached over TCP from every node.

The git device exists because a cloud sandbox has no shared filesystem and no
inbound port: each session is its own VM behind NAT, and the one host every
sandbox can reach is a git service.  A git ref is a compare-and-swap cell, so
:mod:`ampi.device.gitlog` makes the six waist operations out of "fetch, apply,
commit, push, retry on rejection", and :mod:`ampi.device.gitd` puts a daemon in
front of it so a node's ranks push in bursts rather than one at a time.

That is a transport built around a constraint, and the constraint has a ceiling.
Every node's daemon contends for the same ref, and the contest is not fair: the
E7 record is eight daemons landing about one push in ten
(``runs/e7-rawapi-p256-attempt1``), four machines sitting just inside the
ceiling, and a loser whose backoff grows while the winner never pauses.  Nothing
above the waist is wrong; the transport is a single CAS cell three seconds away,
and thirty-two writers cannot share one.

**A real cloud does not impose that constraint.**  Thirty-two EC2 instances in one
VPC are not behind NAT from each other: every instance has a routable private
address, a security group is a firewall the operator writes, and the round trip
between two instances in one availability zone is a few tenths of a millisecond
rather than three seconds.  The reason to encode state in a git ref is gone, and
with it the reason to pay for a distributed CAS: if the machines can reach one
authoritative process, there is no push contest to lose, because there is only
one writer and it is not on the network.

So this device is the obvious one, which the sandbox could not have:

* :class:`HubServer` owns a backing device --- ``sqlite`` by default, on the hub's
  own disk --- and answers the six operations over TCP.  Mutations are applied in
  arrival order under one lock.  There is no batching, no retry loop and no
  rejection, because there is no contest: the serialisation point is a mutex in
  one process rather than a ref on a remote.
* :class:`HubDevice` is the client every rank on every node opens.  It speaks the
  wire format :mod:`ampi.device.gitd` established --- newline-delimited JSON, one
  request per line, predicates as ``{"$in": [...]}`` --- so the two clients are
  the same shape and the daemon's hard-won properties come with it: an
  idempotency key per request so a resend after an ambiguous reply is recognised
  rather than re-applied, ``nowait`` trace appends acknowledged when queued so a
  rank never waits on its own evidence, and a pipelining block so creating a
  256-rank job is a burst rather than five hundred round trips.

It passes the same conformance suite as the other five transports, and nothing
above the waist changed.

Two things the hub gives that no previous transport could, both consequences of
there being one authoritative process rather than a replicated document:

*One clock.*  A lease is a time comparison, and until now the time came from the
rank's own machine: the git device's clock is the local wall clock, which is
comparable across NTP-disciplined VMs and nowhere else.  A conviction is a
judgement about someone else's lease, so a fleet whose clocks differ by a second
convicts the living.  :meth:`HubDevice.clock` returns the *hub's* clock, sampled
periodically and carried locally as an offset (the client measures the round trip
and takes the midpoint, which is NTP's estimator), so every rank in the job reads
one clock without a round trip per read.

*One state, of a known size.*  The git device parses the whole job state on every
commit --- 38 MB by the end of a 256-rank run --- because a document is the only
thing a ref can hold.  Here the state is a SQLite file with indices on the fields
the runtime queries, so a read is a lookup rather than a parse, and the cost of a
poll does not grow with the length of the run.

What it costs, and it should be said plainly: the hub is a single point of
failure, where the git remote was a hosted service someone else keeps up.  If the
hub instance dies the job stops.  What survives is its disk, so a hub restarted
on the same volume serves the same job and the population comes back with
``rejoin`` --- the same recovery the frozen 128-rank run used --- but that is
recovery, not availability.  Run the hub on its own instance, do not give it
ranks, and keep its volume.

Wire format: newline-delimited JSON, one request per line, one reply per line,
over a persistent connection.  The first line of a connection is a ``hello``
carrying the shared token when one is configured; the server answers nothing else
until it has been authenticated.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import queue
import secrets
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
    Lease,
    Predicate,
    get_device,
    register_device,
)
from .gitd import MUTATIONS, decode_predicate, encode_predicate

__all__ = ["HubDevice", "HubServer", "address_file", "main"]

ENV_ADDR = "AMPI_HUB_ADDR"
ENV_TOKEN = "AMPI_HUB_TOKEN"
ENV_BACKEND = "AMPI_HUB_BACKEND"
ENV_IDLE = "AMPI_HUB_IDLE_S"
ENV_CLOCK_INTERVAL = "AMPI_HUB_CLOCK_INTERVAL_S"

DEFAULT_PORT = 7411
DEFAULT_BACKEND = "sqlite"
DEFAULT_IDLE_S = 0.0  # a hub started by an operator does not exit on its own
DEFAULT_CLOCK_INTERVAL_S = 30.0
#: Replies the server remembers so a client that resends a request after an
#: ambiguous reply (the connection dropped after the mutation landed) gets the
#: original outcome instead of a second application.  A network between the
#: client and the server makes this load-bearing rather than defensive.
RECENT_REPLIES = 16384
_PLACEHOLDER = object()


def address_file(root: str | os.PathLike[str]) -> str:
    """Where a hub started for ``root`` on this machine writes its address.

    Only used when no ``AMPI_HUB_ADDR`` is configured, which is the single-machine
    case: the conformance suite and the tests.  In production the operator knows
    the hub's address and every node is told it.
    """
    digest = hashlib.sha1(str(Path(root).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"/tmp/ampi-hub-{digest}.addr"


def parse_addr(text: str) -> tuple[str, int]:
    """``host:port``, ``host`` or ``:port`` to a pair.  IPv6 in brackets."""
    text = text.strip()
    if text.startswith("["):  # [::1]:7411
        host, _, rest = text[1:].partition("]")
        port = int(rest.lstrip(":")) if rest.lstrip(":") else DEFAULT_PORT
        return host, port
    host, sep, port = text.rpartition(":")
    if not sep:
        return text, DEFAULT_PORT
    return (host or "127.0.0.1"), int(port or DEFAULT_PORT)


# --------------------------------------------------------------------------
# The server
# --------------------------------------------------------------------------


class HubServer:
    """Own the job's state; answer every node's ranks over TCP.

    The backing device does the work: this class is the network in front of it,
    a mutex around its mutations, and the two properties a network makes
    necessary --- idempotent resend and asynchronous trace appends.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        backend: str | None = None,
        token: str | None = None,
        idle_s: float | None = None,
        advertise: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.host = host
        self.port = port
        self.backend = backend or os.environ.get(ENV_BACKEND, DEFAULT_BACKEND)
        self.token = token if token is not None else os.environ.get(ENV_TOKEN, "")
        self.idle_s = idle_s if idle_s is not None else float(os.environ.get(ENV_IDLE, DEFAULT_IDLE_S))
        self.advertise = advertise
        self.dev: Device = get_device(self.backend)(self.root)  # type: ignore[call-arg]

        # One writer thread, fed by one ordered queue.  Every mutation goes
        # through it --- the ones a client waits for and the trace appends it
        # does not --- because that ordering is what ``append_nowait`` promises:
        # a queued record lands before any later synchronous write from the same
        # client, and ``flush`` (a queued no-op that *is* waited for) makes it
        # readable.  Serialising here is also what makes the reply cache exact:
        # a remembered reply is the reply of the application that happened.
        self._q: queue.Queue[tuple[str, dict[str, Any], threading.Event | None,
                                   list[Any]] | None] = queue.Queue()
        self._recent: OrderedDict[tuple[str, int], tuple[str, Any]] = OrderedDict()
        self._recent_lock = threading.Lock()
        self._stop = threading.Event()
        self._clients = 0
        self._clients_lock = threading.Lock()
        self._last_activity = time.time()
        self._addr_file: str | None = None

        self._writer: threading.Thread | None = None

        self.requests = 0
        self.mutations = 0
        self.async_ops = 0
        self.resends = 0
        self.peak_clients = 0
        self.started = time.time()

    # -- reply memory -------------------------------------------------------
    def recall(self, key: tuple[str, int] | None) -> tuple[str, Any] | None:
        if key is None:
            return None
        with self._recent_lock:
            return self._recent.get(key)

    def remember(self, key: tuple[str, int] | None, reply: tuple[str, Any]) -> None:
        if key is None:
            return
        with self._recent_lock:
            self._recent[key] = reply
            while len(self._recent) > RECENT_REPLIES:
                self._recent.popitem(last=False)

    # -- the writer ---------------------------------------------------------
    def enqueue(self, op: str, args: dict[str, Any], *,
                wait: bool = True) -> tuple[threading.Event | None, list[Any]]:
        ev = threading.Event() if wait else None
        slot: list[Any] = []
        self._q.put((op, args, ev, slot))
        return ev, slot

    def mutate(self, op: str, args: dict[str, Any]) -> Any:
        ev, slot = self.enqueue(op, args)
        assert ev is not None
        ev.wait()
        status, value = slot[0]
        if status == "error":
            raise RuntimeError(value)
        return value

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            op, args, ev, slot = item
            try:
                slot.append(("ok", self._apply(op, args)))
            except Exception as exc:  # noqa: BLE001 - returned to the waiter, or dropped
                # A trace event nobody waits for never fails a run: the evidence
                # is best-effort by construction (spec S13), and a rank whose
                # program stopped because its own trace could not be written
                # would be the defect, not the record of one.
                slot.append(("error", f"{type(exc).__name__}: {exc}"))
            if ev is not None:
                ev.set()

    def _apply(self, op: str, args: dict[str, Any]) -> Any:
        """The only place the backing device is written.  Runs on one thread."""
        d = self.dev
        if op == "noop":
            return 0
        if op == "append":
            return d.append(args["stream"], args["record"])
        if op == "match":
            return d.match(args["stream"], args["predicate"], args["update"],
                           order_by=args.get("order_by", "seq"))
        if op == "update":
            return bool(d.update(args["stream"], int(args["seq"]), args["fields"]))
        if op == "cas":
            ok, cell = d.cas(args["space"], args["key"], args.get("expect_version"),
                             args["value"], writer=int(args["writer"]),
                             epoch=int(args.get("epoch", 0)), meta=args.get("meta"))
            return [bool(ok), cell.to_dict()]
        if op == "lease":
            lk = d.lease(args["space"], args["key"], holder=int(args["holder"]),
                         mode=args.get("mode", "exclusive"), ttl=float(args["ttl"]))
            return lk.to_dict() if lk else None
        if op == "release":
            return bool(d.release(args["lock_id"], int(args["holder"])))
        if op == "put_object":
            put = getattr(d, "put_object", None)
            if put is None:
                raise ValueError(f"backend {self.backend!r} has no object store")
            put(args["digest"], args["body"])
            return True
        if op == "wipe":
            # The hub outlives any one client, so it re-creates the backing state
            # at once: a client's own ``initialize`` is a no-op while its
            # connection is up, and the next request must not find a device that
            # has been emptied and not remade.
            d.wipe()
            d.initialize()
            return True
        raise ValueError(f"unknown mutation {op!r}")

    # -- request dispatch ---------------------------------------------------
    def handle(self, op: str, args: dict[str, Any]) -> Any:
        self._last_activity = time.time()
        d = self.dev
        if op in MUTATIONS:
            self.mutations += 1
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
            return [lk.to_dict() for lk in
                    d.leases(args.get("space", ""),
                             include_expired=bool(args.get("include_expired")))]
        if op == "get_object":
            get = getattr(d, "get_object", None)
            return get(args["digest"]) if get is not None else None
        if op == "clock":
            return time.time()
        if op == "wipe":
            return self.mutate("wipe", {})
        if op == "stats":
            return {**d.stats(), "hub": self.report()}
        if op == "ping":
            return "pong"
        if op == "hello":
            return {"pid": os.getpid(), "root": str(self.root.resolve()),
                    "backend": self.backend, "root_exists": self.root.exists(),
                    "clock": time.time(), "protocol": 1}
        if op == "shutdown":
            self._stop.set()
            return True
        raise ValueError(f"unknown op {op!r}")

    def report(self) -> dict[str, Any]:
        return {"pid": os.getpid(), "clients": self._clients, "peak_clients": self.peak_clients,
                "requests": self.requests, "mutations": self.mutations,
                "async_ops": self.async_ops, "resends": self.resends,
                "backend": self.backend, "uptime_s": round(time.time() - self.started, 1),
                "listen": f"{self.host}:{self.port}"}

    # -- serving ------------------------------------------------------------
    def serve(self) -> None:
        self.dev.initialize()
        hub = self
        needs_token = bool(self.token)

        class Handler(socketserver.StreamRequestHandler):
            """One connection, one client, requests answered in order.

            Unlike the git daemon there is nothing to wait for: a mutation is
            applied under the write lock and answered.  The reply cache is what
            makes that safe across a dropped connection.
            """

            def setup(self) -> None:
                super().setup()
                with contextlib.suppress(OSError):
                    self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            def handle(self) -> None:
                with hub._clients_lock:
                    hub._clients += 1
                    hub.peak_clients = max(hub.peak_clients, hub._clients)
                authed = not needs_token
                try:
                    for line in self.rfile:
                        rid: Any = None
                        try:
                            req = json.loads(line)
                            rid = req.get("id")
                            op, args = req["op"], req.get("args") or {}
                            hub.requests += 1
                            hub._last_activity = time.time()
                            if not authed:
                                if op != "hello" or not secrets.compare_digest(
                                        str(req.get("token", "")), hub.token):
                                    self._reply({"id": rid, "ok": False,
                                                 "error": "unauthenticated"})
                                    return
                                authed = True
                            key = None
                            if op in MUTATIONS and args.pop("nowait", False):
                                # Acknowledged now, applied in queue order.  The
                                # rank's program does not depend on a trace
                                # record's sequence number, and a rank blocked on
                                # its own evidence was the shape of a ten minute
                                # stall on the git transports.
                                hub.enqueue(op, args, wait=False)
                                hub.async_ops += 1
                                status, value = "ok", 0
                            elif op in MUTATIONS:
                                if req.get("client") is not None and rid is not None:
                                    key = (str(req["client"]), int(rid))
                                seen = hub.recall(key)
                                if seen is not None:
                                    hub.resends += 1
                                    status, value = seen
                                    key = None
                                else:
                                    status, value = "ok", hub.handle(op, args)
                            else:
                                status, value = "ok", hub.handle(op, args)
                        except Exception as exc:  # noqa: BLE001 - reported to the client
                            status, value, key = "error", f"{type(exc).__name__}: {exc}", None
                        hub.remember(key, (status, value))
                        out = ({"id": rid, "ok": True, "result": value} if status == "ok"
                               else {"id": rid, "ok": False, "error": value})
                        if not self._reply(out):
                            return
                        if hub._stop.is_set():
                            break
                finally:
                    with hub._clients_lock:
                        hub._clients -= 1
                    hub._last_activity = time.time()

            def _reply(self, out: dict[str, Any]) -> bool:
                try:
                    self.wfile.write((json.dumps(out, default=str) + "\n").encode("utf-8"))
                    self.wfile.flush()
                    return True
                except OSError:
                    return False

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True
            address_family = socket.AF_INET6 if ":" in hub.host else socket.AF_INET
            request_queue_size = 256

        server = Server((self.host, self.port), Handler)
        self.port = server.server_address[1]  # an ephemeral port resolves here
        self._publish()
        self._writer = threading.Thread(target=self._worker, daemon=True)
        self._writer.start()
        t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2},
                             daemon=True)
        t.start()
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
                if self.idle_s and self._clients == 0 and \
                        time.time() - self._last_activity > self.idle_s:
                    break
        finally:
            self._stop.set()
            self._q.put(None)
            if self._writer is not None:
                self._writer.join(timeout=30)
            server.shutdown()
            server.server_close()
            self._unpublish()
            with contextlib.suppress(Exception):
                self.dev.close()

    def _publish(self) -> None:
        """Write the address where a same-machine client will look for it."""
        if self.advertise is not None:
            return
        self._addr_file = address_file(self.root)
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        with contextlib.suppress(OSError):
            Path(self._addr_file).write_text(f"{host}:{self.port}\n", encoding="utf-8")

    def _unpublish(self) -> None:
        if self._addr_file:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self._addr_file)


# --------------------------------------------------------------------------
# The client device
# --------------------------------------------------------------------------


@register_device
class HubDevice(Device):
    """Every rank's end of the hub: a socket, a request id, and a clock offset."""

    name = "hub"
    durable = True
    #: A renewal is one round trip on a VPC link, not a push to a hosted remote,
    #: so a blocked rank can afford the shared-filesystem rate.
    touch_interval_s = 5.0

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        addr: str | None = None,
        token: str | None = None,
        spawn: bool = True,
        connect_timeout: float = 120.0,
    ) -> None:
        self.root = Path(root)
        self._addr = addr or os.environ.get(ENV_ADDR) or None
        self._token = token if token is not None else os.environ.get(ENV_TOKEN, "")
        self.spawn = spawn
        self.connect_timeout = connect_timeout
        self._sock: socket.socket | None = None
        self._rfile: Any = None
        # Reentrant: a call that must first initialise the connection makes a
        # call itself (the hello that authenticates and samples the clock).
        self._lock = threading.RLock()
        self._ids = 0
        self._client = uuid.uuid4().hex
        self._pipelined: list[str] | None = None
        self._unflushed = False
        # The hub's clock, carried as an offset from ours.
        self._skew = 0.0
        self._skew_at = 0.0
        self._clock_interval = float(os.environ.get(ENV_CLOCK_INTERVAL, DEFAULT_CLOCK_INTERVAL_S))
        self.calls = 0

    # -- connection ---------------------------------------------------------
    def _resolve(self) -> tuple[str, int] | None:
        if self._addr:
            return parse_addr(self._addr)
        path = address_file(self.root)
        try:
            return parse_addr(Path(path).read_text(encoding="utf-8"))
        except OSError:
            return None

    def _connect(self) -> bool:
        where = self._resolve()
        if where is None:
            return False
        host, port = where
        try:
            s = socket.create_connection((host, port), timeout=30)
        except OSError:
            return False
        with contextlib.suppress(OSError):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        s.settimeout(None)
        self._sock = s
        self._rfile = s.makefile("rb")
        try:
            self._greet()
        except Exception:  # noqa: BLE001 - a hub that will not greet us is not ours
            self.close()
            return False
        return True

    def _greet(self) -> None:
        """Authenticate and take the first clock sample, in one round trip."""
        sent = time.time()
        hello = self._raw("hello", token=self._token)
        got = time.time()
        if "clock" in hello:
            # NTP's estimator: the reply was written at about the midpoint of
            # the round trip, so the difference is the offset.
            self._skew = float(hello["clock"]) - (sent + got) / 2.0
            self._skew_at = got

    def _spawn(self) -> None:
        """Start a hub for this root on this machine.

        Only reachable when no address is configured, which is the
        single-machine case the conformance suite and the tests exercise.  A
        production node is always told where the hub is.
        """
        cmd = [sys.executable, "-c",
               "import sys; from ampi.device.hub import main; sys.exit(main(sys.argv[1:]))",
               "--root", str(self.root), "--host", "127.0.0.1", "--port", "0"]
        if self._token:
            cmd += ["--token", self._token]
        log = open(f"{address_file(self.root)}.log", "ab")  # noqa: SIM115 - the hub inherits it
        subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True,
                         cwd=str(Path.cwd()))
        log.close()

    def _stale(self) -> str:
        """Why the hub we reached is not the one this root needs, or ''."""
        try:
            h = self._raw("hello", token=self._token)
        except Exception as exc:  # noqa: BLE001 - an unreachable hub is stale
            return f"unreachable: {exc}"
        if not h.get("root_exists"):
            return "its backing state has been removed"
        return ""

    def initialize(self) -> None:
        if self._sock is not None:
            return
        if self._connect():
            why = self._stale()
            if not why:
                return
            self._replace_stale()
        if self._addr:
            # An address was configured and nothing answered there.  Starting a
            # second hub would be a second authority; say so instead.
            raise RuntimeError(f"no hub answering at {self._addr} for {self.root}")
        if not self.spawn:
            raise RuntimeError(f"no hub listening for {self.root}")
        import fcntl

        deadline = time.time() + self.connect_timeout
        with open(f"{address_file(self.root)}.lock", "w") as fh:
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
        raise RuntimeError(f"hub for {self.root} did not come up within "
                           f"{self.connect_timeout:.0f}s; see {address_file(self.root)}.log")

    def _replace_stale(self) -> None:
        with contextlib.suppress(Exception):
            self._raw("shutdown")
        self.close()
        deadline = time.time() + 10
        while time.time() < deadline and os.path.exists(address_file(self.root)):
            time.sleep(0.1)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(address_file(self.root))

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                with contextlib.suppress(OSError):
                    self._sock.close()
                self._sock = None
                self._rfile = None

    # -- the wire -----------------------------------------------------------
    def _raw(self, op: str, **args: Any) -> Any:
        """One request on the open socket, with no reconnect and no bookkeeping.

        The greeting uses this, because reconnecting inside the greeting would
        be a loop.
        """
        self._ids += 1
        line = json.dumps({"id": self._ids, "client": self._client, "op": op,
                           **({"token": args.pop("token")} if "token" in args else {}),
                           "args": args}, default=list) + "\n"
        assert self._sock is not None
        self._sock.sendall(line.encode("utf-8"))
        raw = self._rfile.readline()
        if not raw:
            raise ConnectionError("hub closed the connection")
        reply = json.loads(raw)
        if not reply.get("ok"):
            raise RuntimeError(f"hub: {reply.get('error')}")
        return reply.get("result")

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
                        raise ConnectionError("hub closed the connection")
                    break
                except (OSError, ConnectionError):
                    if attempt == 1:
                        raise
                    # The same request id and client id go out again, so a
                    # mutation that landed before the connection dropped is
                    # answered from the hub's memory rather than applied twice.
                    self._sock = None
                    self.initialize()
            self.calls += 1
        reply = json.loads(raw)
        if not reply.get("ok"):
            raise RuntimeError(f"hub: {reply.get('error')}")
        return reply.get("result")

    @contextmanager
    def pipeline(self) -> Iterator[None]:
        """Send mutations without waiting for each reply; collect them at exit.

        Inside the block a mutation's return value is a placeholder, so this is
        only for callers that do not read one.  What it buys over a VPC link is
        the same thing it buys over git: creating a 256-rank job is one burst
        rather than five hundred round trips.
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
                raise RuntimeError(f"hub: pipelined request failed: {errors[0]}")

    # -- lifecycle ----------------------------------------------------------
    def wipe(self) -> None:
        self._call("wipe")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    # -- streams ------------------------------------------------------------
    def append(self, stream: str, record: dict[str, Any]) -> int:
        got = self._call("append", stream=stream, record=record)
        return 0 if got is _PLACEHOLDER else int(got)

    def append_nowait(self, stream: str, record: dict[str, Any]) -> None:
        self._call("append", stream=stream, record=record, nowait=True)
        self._unflushed = True

    def flush(self) -> None:
        """Land the appends this client did not wait for.

        The hub's writer thread takes them in order, so an operation that waits
        lands behind them.
        """
        if self._unflushed:
            self._unflushed = False
            self._call("noop")

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

    # -- cells --------------------------------------------------------------
    def read(self, space: str, key: str, *, version: int | None = None) -> Cell | None:
        c = self._call("read", space=space, key=key, version=version)
        return Cell(**c) if c else None

    def cas(self, space: str, key: str, expect_version: int | None, value: Any, *,
            writer: int, epoch: int = 0, meta: dict[str, Any] | None = None) -> tuple[bool, Cell]:
        got = self._call("cas", space=space, key=key, expect_version=expect_version,
                         value=value, writer=writer, epoch=epoch, meta=meta)
        if got is _PLACEHOLDER:
            # The local reading, never the sampling one: this path runs inside a
            # pipeline block, where a synchronous request would read back the
            # reply of a mutation that was sent and not yet collected.
            return True, Cell(space, key, 0, value, writer, epoch, self._clock_local(),
                              meta or {})
        ok, cell = got
        return bool(ok), Cell(**cell)

    def keys(self, space: str, *, prefix: str = "") -> list[Cell]:
        return [Cell(**c) for c in self._call("keys", space=space, prefix=prefix)]

    def history(self, space: str, key: str, *, limit: int | None = None) -> list[Cell]:
        return [Cell(**c) for c in self._call("history", space=space, key=key, limit=limit)]

    # -- leases -------------------------------------------------------------
    def lease(self, space: str, key: str, *, holder: int, mode: str = "exclusive",
              ttl: float) -> Lease | None:
        got = self._call("lease", space=space, key=key, holder=holder, mode=mode, ttl=ttl)
        return Lease(**got) if got else None

    def release(self, lock_id: str, holder: int) -> bool:
        return bool(self._call("release", lock_id=lock_id, holder=holder))

    def leases(self, space: str = "", *, include_expired: bool = False) -> list[Lease]:
        return [Lease(**lk) for lk in self._call("leases", space=space,
                                                 include_expired=include_expired)]

    # -- clock and objects --------------------------------------------------
    def clock(self) -> float:
        """The hub's clock, not this machine's.

        A lease is a time comparison and a conviction is a judgement about
        someone else's lease, so a fleet whose clocks differ convicts the
        living.  The offset is re-sampled every ``AMPI_HUB_CLOCK_INTERVAL_S``
        seconds; between samples this costs what ``time.time()`` costs.
        """
        now = time.time()
        if now - self._skew_at > self._clock_interval and self._pipelined is None:
            # Not while pipelining: the reply to a sample would be read back as
            # the reply to one of the mutations already in flight.
            with contextlib.suppress(Exception):  # a clock sample never fails a call
                sent = time.time()
                hub = float(self._call("clock"))
                got = time.time()
                self._skew = hub - (sent + got) / 2.0
                self._skew_at = got
                now = got
        return now + self._skew

    def _clock_local(self) -> float:
        """The hub's clock from the offset already measured: no round trip, and
        safe to call from anywhere, including inside a pipeline block."""
        return time.time() + self._skew

    def put_object(self, digest: str, body: str) -> None:
        self._call("put_object", digest=digest, body=body)

    def get_object(self, digest: str) -> str | None:
        return self._call("get_object", digest=digest)

    def stats(self) -> dict[str, Any]:
        try:
            base = self._call("stats")
        except Exception as exc:  # noqa: BLE001 - stats must not fail a run
            base = {"error": str(exc)}
        where = self._resolve()
        return {**base, "device": self.name, "client_calls": self.calls,
                "hub_addr": f"{where[0]}:{where[1]}" if where else None,
                "clock_skew_s": round(self._skew, 6)}

    def shutdown_hub(self) -> None:
        with contextlib.suppress(Exception):
            self._call("shutdown")
        self.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ampi-hub", description="the AgentMPI hub server")
    ap.add_argument("--root", required=True, help="where the backing state lives")
    ap.add_argument("--host", default="0.0.0.0", help="address to bind")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to bind; 0 for ephemeral")
    ap.add_argument("--backend", default=None, help="the device that holds the state")
    ap.add_argument("--token", default=None, help="shared secret every client must present")
    ap.add_argument("--idle", type=float, default=None,
                    help="exit after this long with no client; 0 (default) never exits")
    a = ap.parse_args(argv)
    hub = HubServer(a.root, host=a.host, port=a.port, backend=a.backend, token=a.token,
                    idle_s=a.idle)
    hub.serve()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
