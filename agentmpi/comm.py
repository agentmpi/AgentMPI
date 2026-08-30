"""Communicator: the AgentMPI analog of MPI_Comm.

A communicator is a group of ranks plus a communication context. Two
harnesses can share the same group of executors without mixing messages
because matching includes the communicator name (MPI's hidden context id).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentmpi.algorithms.doubling import doubling_partners, is_pow2
from agentmpi.algorithms.trees import binomial_children, binomial_parent
from agentmpi.constants import (
    ANY_SOURCE,
    ANY_TAG,
    COMM_WORLD_NAME,
    DEFAULT_CONTEXT_BUDGET,
    DEFAULT_EAGER_BYTES,
    DEFAULT_FAILURE_TIMEOUT_S,
    DEFAULT_HEARTBEAT_S,
    DEFAULT_POLL_S,
    LOCK_EXCLUSIVE,
    TAG_COLLECTIVE,
)
from agentmpi.errors import (
    ContextBudgetExceeded,
    DeadRankError,
    RevokedCommunicatorError,
    TimeoutError,
)
from agentmpi.transport.filesystem import FilesystemTransport
from agentmpi.types import Envelope, Lifecycle, Op, RankStatus
from agentmpi.util import (
    atomic_write_json,
    estimate_tokens,
    mkdir_lock,
    now,
    read_json,
    release_dir_lock,
)

ReduceFn = Callable[[Any, Any], Any]


def _apply_op(op: Op, acc: Any, val: Any) -> Any:
    if acc is None:
        return val
    if val is None:
        return acc
    if op is Op.SUM:
        return acc + val
    if op is Op.PROD:
        return acc * val
    if op is Op.MIN:
        return acc if acc <= val else val
    if op is Op.MAX:
        return acc if acc >= val else val
    if op is Op.LAND:
        return bool(acc) and bool(val)
    if op is Op.LOR:
        return bool(acc) or bool(val)
    if op is Op.BAND:
        return acc & val
    if op is Op.BOR:
        return acc | val
    if op is Op.CONCAT:
        if isinstance(acc, list) and isinstance(val, list):
            return acc + val
        return [acc, val]
    if op is Op.MERGE:
        if isinstance(acc, dict) and isinstance(val, dict):
            merged = dict(acc)
            merged.update(val)
            return merged
        return {"left": acc, "right": val}
    if op is Op.SYNTHESIZE:
        # Hierarchical map-reduce of text. The default is a structured stitch;
        # a harness may replace this by passing op as a callable.
        left = acc if isinstance(acc, str) else str(acc)
        right = val if isinstance(val, str) else str(val)
        return f"{left.rstrip()}\n\n{right.lstrip()}"
    raise ValueError(f"unknown op {op}")


class Communicator:
    def __init__(
        self,
        home: Path | str,
        rank: int,
        size: int,
        name: str = COMM_WORLD_NAME,
        *,
        role: str = "worker",
        eager_bytes: int = DEFAULT_EAGER_BYTES,
        context_budget: int = DEFAULT_CONTEXT_BUDGET,
        heartbeat_s: float = DEFAULT_HEARTBEAT_S,
        failure_timeout_s: float = DEFAULT_FAILURE_TIMEOUT_S,
        poll_s: float = DEFAULT_POLL_S,
        bootstrap: bool = False,
    ):
        self.home = Path(home)
        self.rank = rank
        self.size = size
        self.name = name
        self.role = role
        self.context_budget = context_budget
        self.heartbeat_s = heartbeat_s
        self.failure_timeout_s = failure_timeout_s
        self.poll_s = poll_s
        self.transport = FilesystemTransport(self.home, comm=name, eager_bytes=eager_bytes)
        if bootstrap:
            self.transport.bootstrap(size)
        self._cid = 0
        self._state = Lifecycle.INIT
        self._context_tokens = 0
        self._hb_seq = 0
        self._revoked = False
        self._dead: set[int] = set()
        self.heartbeat(state=Lifecycle.INIT)

    # --- lifecycle --------------------------------------------------------

    def heartbeat(self, state: Lifecycle | None = None, note: str = "") -> RankStatus:
        if state is not None:
            self._state = state
        self._hb_seq += 1
        status = RankStatus(
            rank=self.rank,
            comm=self.name,
            state=self._state.value,
            last_heartbeat=now(),
            context_tokens=self._context_tokens,
            context_budget=self.context_budget,
            pid=os.getpid(),
            role=self.role,
            note=note,
            seq=self._hb_seq,
        )
        self.transport.write_status(status)
        return status

    def finalize(self) -> None:
        self.heartbeat(Lifecycle.FINALIZED)
        self._state = Lifecycle.FINALIZED

    # --- matching / progress ---------------------------------------------

    def _next_cid(self) -> int:
        self._cid += 1
        return self._cid

    def _check_revoked(self) -> None:
        if self._revoked:
            raise RevokedCommunicatorError(f"communicator {self.name} revoked")
        try:
            meta = self.transport.meta()
        except OSError:
            return
        if meta.get("revoked"):
            self._revoked = True
            raise RevokedCommunicatorError(f"communicator {self.name} revoked")
        for dead in meta.get("dead", []):
            self._dead.add(int(dead))

    def live_ranks(self) -> list[int]:
        self.probe_failures()
        return [r for r in range(self.size) if r not in self._dead]

    def probe_failures(self, timeout_s: float | None = None) -> list[int]:
        timeout = self.failure_timeout_s if timeout_s is None else timeout_s
        now_ts = now()
        newly: list[int] = []
        for r in range(self.size):
            if r == self.rank or r in self._dead:
                continue
            st = self.transport.read_status(r)
            if st is None:
                continue
            if st.state == Lifecycle.FAILED.value:
                newly.append(r)
                continue
            if st.state == Lifecycle.FINALIZED.value:
                continue
            if now_ts - st.last_heartbeat > timeout:
                newly.append(r)
        for r in newly:
            self._dead.add(r)
        if newly:
            meta = self.transport.meta()
            dead = sorted(set(meta.get("dead", [])) | self._dead)
            self.transport.update_meta(dead=dead)
            self.transport.emit({"event": "failure", "src": self.rank, "dead": newly})
        return newly

    def _wait_for(
        self,
        pred: Callable[[], Any],
        timeout_s: float | None,
        *,
        fail_on_dead: list[int] | None = None,
    ) -> Any:
        deadline = None if timeout_s is None else time.time() + timeout_s
        last_hb = 0.0
        while True:
            self._check_revoked()
            hit = pred()
            if hit is not None and hit is not False:
                return hit
            t = time.time()
            if t - last_hb >= self.heartbeat_s:
                self.heartbeat()
                last_hb = t
            if fail_on_dead:
                self.probe_failures()
                dead = [r for r in fail_on_dead if r in self._dead]
                if dead:
                    raise DeadRankError(dead)
            if deadline is not None and t >= deadline:
                raise TimeoutError("blocking AgentMPI call timed out")
            time.sleep(self.poll_s)

    # --- point-to-point ---------------------------------------------------

    def send(self, obj: Any, dest: int, tag: int = 0) -> None:
        """Blocking send. Eager if small, rendezvous artifact if large."""
        self._check_revoked()
        self.transport.post(self.rank, dest, tag, obj, cid=0)
        self.heartbeat()

    def recv(self, source: int = ANY_SOURCE, tag: int = ANY_TAG, timeout_s: float | None = None) -> Any:
        """Blocking receive with MPI envelope matching."""
        self._check_revoked()
        watch = None if source == ANY_SOURCE else [source]

        def _try() -> Any:
            msg = self.transport.consume(self.rank, source, tag)
            return msg.payload if msg is not None else None

        payload = self._wait_for(_try, timeout_s, fail_on_dead=watch)
        self._charge(payload)
        self.heartbeat()
        return payload

    def isend(self, obj: Any, dest: int, tag: int = 0) -> None:
        """Non-blocking send. Filesystem post is already asynchronous to the receiver."""
        self.send(obj, dest, tag)

    def irecv_probe(self, source: int = ANY_SOURCE, tag: int = ANY_TAG) -> Envelope | None:
        """Non-blocking probe (MPI_Iprobe)."""
        self._check_revoked()
        return self.transport.probe(self.rank, source, tag)

    def probe(self, source: int = ANY_SOURCE, tag: int = ANY_TAG, timeout_s: float | None = None) -> Envelope:
        def _try() -> Envelope | None:
            return self.transport.probe(self.rank, source, tag)

        return self._wait_for(_try, timeout_s)

    # --- collectives ------------------------------------------------------

    def barrier(self, timeout_s: float | None = None, *, resilient: bool = False) -> None:
        """Recursive-doubling / Bruck barrier. resilient=True waits only on live ranks."""
        cid = self._next_cid()
        if resilient:
            self.probe_failures()
            live = self.live_ranks()
            self._flat_ack(cid, live, timeout_s)
            return
        token = {"cid": cid, "rank": self.rank, "op": "barrier"}
        tag = TAG_COLLECTIVE + (cid % 100)
        dist = 1
        while dist < self.size:
            dest = (self.rank + dist) % self.size
            src = (self.rank - dist) % self.size
            self.send(token, dest=dest, tag=tag)
            self.recv(source=src, tag=tag, timeout_s=timeout_s)
            dist <<= 1

    def _flat_ack(self, cid: int, live: list[int], timeout_s: float | None) -> None:
        """Linear barrier over a (possibly shrunk) live set. Used after failures."""
        tag = TAG_COLLECTIVE + (cid % 100)
        root = min(live)
        if self.rank != root:
            if self.rank in live:
                self.send({"ack": self.rank, "cid": cid}, dest=root, tag=tag)
                self.recv(source=root, tag=tag, timeout_s=timeout_s)
            return
        need = [r for r in live if r != root]
        seen: set[int] = set()
        deadline = None if timeout_s is None else time.time() + timeout_s
        while len(seen) < len(need):
            remaining = deadline - time.time() if deadline else None
            if remaining is not None and remaining <= 0:
                raise TimeoutError("resilient barrier timed out")
            msg = self.recv(source=ANY_SOURCE, tag=tag, timeout_s=remaining)
            seen.add(int(msg["ack"]))
        for r in need:
            self.send({"go": True, "cid": cid}, dest=r, tag=tag)

    def bcast(self, obj: Any, root: int = 0, timeout_s: float | None = None) -> Any:
        """Binomial-tree broadcast (short-message MPICH algorithm)."""
        cid = self._next_cid()
        tag = TAG_COLLECTIVE + (cid % 100)
        parent = binomial_parent(self.rank, root, self.size)
        if parent is None:
            payload = obj
        else:
            payload = self.recv(source=parent, tag=tag, timeout_s=timeout_s)
        for child in binomial_children(self.rank, root, self.size):
            self.send(payload, dest=child, tag=tag)
        return payload

    def scatter(self, sendbuf: list[Any] | None, root: int = 0, timeout_s: float | None = None) -> Any:
        """Binomial scatter: parent sends the contiguous slice for each subtree."""
        cid = self._next_cid()
        tag = TAG_COLLECTIVE + (cid % 100)
        parent = binomial_parent(self.rank, root, self.size)
        if parent is None:
            if sendbuf is None or len(sendbuf) != self.size:
                raise ValueError("root must supply sendbuf of length comm.size")
            chunks = list(sendbuf)
        else:
            chunks = self.recv(source=parent, tag=tag, timeout_s=timeout_s)
        rel = (self.rank - root) % self.size
        mine = chunks[0] if chunks else None
        for child in binomial_children(self.rank, root, self.size):
            child_rel = (child - root) % self.size
            # Subtree of child is the ranks whose bits sit under child's span.
            span = child_rel & -child_rel
            if span == 0:
                span = 1
            # chunk index 0 is this process's own slot in the relative slice
            start = child_rel - rel
            sl = chunks[start : start + span]
            self.send(sl, dest=child, tag=tag)
        return mine

    def gather(self, obj: Any, root: int = 0, timeout_s: float | None = None) -> list[Any] | None:
        """Binomial gather: each parent concatenates child subtree slices."""
        cid = self._next_cid()
        tag = TAG_COLLECTIVE + (cid % 100)
        assembled = [obj]
        for child in binomial_children(self.rank, root, self.size):
            part = self.recv(source=child, tag=tag, timeout_s=timeout_s)
            assembled.extend(part)
        parent = binomial_parent(self.rank, root, self.size)
        if parent is None:
            # assembled is in relative-preorder; restore rank order
            out: list[Any] = [None] * self.size
            order = self._binomial_preorder(root)
            for r, val in zip(order, assembled, strict=True):
                out[r] = val
            return out
        self.send(assembled, dest=parent, tag=tag)
        return None

    def _binomial_preorder(self, root: int) -> list[int]:
        order: list[int] = []

        def walk(r: int) -> None:
            order.append(r)
            for c in binomial_children(r, root, self.size):
                walk(c)

        walk(root)
        return order

    def reduce(self, obj: Any, op: Op | ReduceFn = Op.SUM, root: int = 0, timeout_s: float | None = None) -> Any:
        """Binomial reduce (short-message). Combine on the way to root."""
        cid = self._next_cid()
        tag = TAG_COLLECTIVE + (cid % 100)
        acc = obj
        for child in binomial_children(self.rank, root, self.size):
            part = self.recv(source=child, tag=tag, timeout_s=timeout_s)
            acc = op(acc, part) if callable(op) and not isinstance(op, Op) else _apply_op(op, acc, part)  # type: ignore[arg-type]
        parent = binomial_parent(self.rank, root, self.size)
        if parent is None:
            return acc
        self.send(acc, dest=parent, tag=tag)
        return None

    def allreduce(self, obj: Any, op: Op | ReduceFn = Op.SUM, timeout_s: float | None = None) -> Any:
        """Recursive doubling when p is a power of two; else reduce+broadcast."""
        if is_pow2(self.size) and not callable(op):
            return self._allreduce_doubling(obj, op, timeout_s)
        reduced = self.reduce(obj, op=op, root=0, timeout_s=timeout_s)
        return self.bcast(reduced, root=0, timeout_s=timeout_s)

    def _allreduce_doubling(self, obj: Any, op: Op, timeout_s: float | None) -> Any:
        cid = self._next_cid()
        tag = TAG_COLLECTIVE + (cid % 100)
        acc = obj
        for peer in doubling_partners(self.rank, self.size):
            if peer is None:
                continue
            self.send(acc, dest=peer, tag=tag)
            other = self.recv(source=peer, tag=tag, timeout_s=timeout_s)
            # Deterministic combine: lower rank is the left operand.
            if self.rank < peer:
                acc = _apply_op(op, acc, other)
            else:
                acc = _apply_op(op, other, acc)
        return acc

    def allgather(self, obj: Any, timeout_s: float | None = None) -> list[Any]:
        """Bruck allgather: each rank accumulates a map of origin→value.

        At step k the rank sends everything it holds to (rank-2^k) and receives
        from (rank+2^k). After ⌈log2 p⌉ steps every rank holds all p values.
        Sending the growing map is a constant-factor more bandwidth than the
        textbook block form; agent payloads already take the rendezvous path
        when large, so the extra metadata is negligible.
        """
        cid = self._next_cid()
        tag = TAG_COLLECTIVE + (cid % 100)
        have: dict[int, Any] = {self.rank: obj}
        dist = 1
        while dist < self.size:
            dest = (self.rank - dist) % self.size
            src = (self.rank + dist) % self.size
            self.send(have, dest=dest, tag=tag)
            incoming = self.recv(source=src, tag=tag, timeout_s=timeout_s)
            have.update({int(k): v for k, v in incoming.items()})
            dist <<= 1
        return [have[i] for i in range(self.size)]

    def alltoall(self, sendbuf: list[Any], timeout_s: float | None = None) -> list[Any]:
        """Pairwise-exchange alltoall (p-1 steps). sendbuf[i] is for rank i."""
        if len(sendbuf) != self.size:
            raise ValueError("alltoall sendbuf must have length comm.size")
        cid = self._next_cid()
        tag = TAG_COLLECTIVE + (cid % 100)
        recvbuf: list[Any] = [None] * self.size
        recvbuf[self.rank] = sendbuf[self.rank]
        for step in range(1, self.size):
            dest = (self.rank + step) % self.size
            src = (self.rank - step) % self.size
            self.send(sendbuf[dest], dest=dest, tag=tag)
            recvbuf[src] = self.recv(source=src, tag=tag, timeout_s=timeout_s)
        return recvbuf

    def scan(self, obj: Any, op: Op = Op.SUM, timeout_s: float | None = None) -> Any:
        """Inclusive prefix reduction via binomial up-sweep."""
        gathered = self.gather(obj, root=0, timeout_s=timeout_s)
        prefixes: list[Any] | None = None
        if self.rank == 0 and gathered is not None:
            prefixes = []
            acc = None
            for val in gathered:
                acc = val if acc is None else _apply_op(op, acc, val)
                prefixes.append(acc)
        return self.scatter(prefixes, root=0, timeout_s=timeout_s)

    # --- RMA / locks / shared windows ------------------------------------

    def win_ensure(self, name: str, initial: Any = None) -> str:
        """Non-collective window create. Use when only some ranks touch a file."""
        path = self.transport.windows / f"{name}.json"
        if not path.exists():
            atomic_write_json(path, {"name": name, "value": initial, "epoch": 0, "cid": 0})
        return name

    def win_create(self, name: str, initial: Any = None) -> str:
        """Collective window create. Analog of MPI_Win_create. Every rank must call it."""
        cid = self._next_cid()
        path = self.transport.windows / f"{name}.json"
        if self.rank == 0 and not path.exists():
            atomic_write_json(path, {"name": name, "value": initial, "epoch": 0, "cid": cid})
        self.barrier()
        return name

    def win_lock(self, name: str, lock_type: str = LOCK_EXCLUSIVE, timeout_s: float | None = None) -> bool:
        lock_path = self.transport.locks / f"{name}.{lock_type}"
        if lock_type == LOCK_EXCLUSIVE:
            ok = mkdir_lock(lock_path, timeout_s=timeout_s if timeout_s is not None else 30.0)
            if ok:
                self.transport.emit({"event": "lock", "src": self.rank, "window": name, "kind": lock_type})
            return ok
        # Shared: count-file under a directory.
        lock_path.mkdir(parents=True, exist_ok=True)
        atomic_write_json(lock_path / f"{self.rank}.hold", {"rank": self.rank, "ts": now()})
        return True

    def win_unlock(self, name: str, lock_type: str = LOCK_EXCLUSIVE) -> None:
        if lock_type == LOCK_EXCLUSIVE:
            release_dir_lock(self.transport.locks / f"{name}.{lock_type}")
        else:
            hold = self.transport.locks / f"{name}.{lock_type}" / f"{self.rank}.hold"
            try:
                hold.unlink()
            except OSError:
                pass
        self.transport.emit({"event": "unlock", "src": self.rank, "window": name})

    def put(self, name: str, value: Any) -> None:
        """One-sided put. Caller must hold the window lock (passive-target analog)."""
        path = self.transport.windows / f"{name}.json"
        data = read_json(path) if path.exists() else {"name": name, "value": None, "epoch": 0}
        data["value"] = value
        data["epoch"] = int(data.get("epoch", 0)) + 1
        data["origin"] = self.rank
        atomic_write_json(path, data)
        self.transport.emit({"event": "put", "src": self.rank, "window": name, "epoch": data["epoch"]})

    def get(self, name: str) -> Any:
        path = self.transport.windows / f"{name}.json"
        if not path.exists():
            return None
        return read_json(path).get("value")

    # --- context / OOM ----------------------------------------------------

    def _charge(self, obj: Any) -> None:
        cost = estimate_tokens(obj)
        if self._context_tokens + cost > self.context_budget:
            raise ContextBudgetExceeded(
                f"rank {self.rank} budget {self.context_budget} exceeded "
                f"({self._context_tokens}+{cost})"
            )
        self._context_tokens += cost

    def context_reset(self, tokens: int | None = None) -> None:
        self._context_tokens = 0 if tokens is None else tokens
        self.heartbeat()

    def context_compact(self, summary: Any) -> Any:
        """Replace accumulated context with a summary (agent analog of paging)."""
        self._context_tokens = estimate_tokens(summary)
        if self._context_tokens > self.context_budget:
            raise ContextBudgetExceeded("summary itself exceeds budget")
        self.heartbeat(Lifecycle.SUSPENDED, note="compact")
        self.heartbeat(Lifecycle.ACTIVE)
        self.transport.emit({"event": "compact", "src": self.rank, "tokens": self._context_tokens})
        return summary

    def context_put(self, summary: Any) -> None:
        """Publish a compact summary into the shared context window."""
        self.win_ensure("context", [])
        self.win_lock("context", LOCK_EXCLUSIVE)
        try:
            current = self.get("context") or []
            if not isinstance(current, list):
                current = [current]
            current.append({"rank": self.rank, "summary": summary, "ts": now()})
            self.put("context", current)
        finally:
            self.win_unlock("context")

    def context_get(self) -> Any:
        return self.get("context")

    # --- fault (ULFM analogs) --------------------------------------------

    def revoke(self) -> None:
        self._revoked = True
        self.transport.update_meta(revoked=True)
        self.transport.emit({"event": "revoke", "src": self.rank})

    def agree(self, value: Any, timeout_s: float | None = None) -> Any:
        """Fault-aware agreement: allreduce of a value over currently live ranks.

        Analog of MPI_COMM_AGREE (ULFM). If ranks have died, they are excluded
        rather than deadlocking the collective.
        """
        self.probe_failures()
        live = self.live_ranks()
        votes = self.allgather(value, timeout_s=timeout_s)
        live_votes = [votes[r] for r in live]
        # Strict agreement: if any live vote differs, raise.
        first = live_votes[0] if live_votes else value
        if any(v != first for v in live_votes):
            return {"agreed": False, "votes": votes, "live": live}
        return {"agreed": True, "value": first, "live": live}

    def shrink(self) -> Communicator:
        """Create a new communicator containing only live ranks (MPI_COMM_SHRINK).

        Membership is the intersection of per-rank liveness views so two
        survivors cannot disagree on the new rank map (ULFM's agree-then-shrink).
        """
        self.heartbeat()
        view = self.live_ranks()
        view_dir = self.transport.root / "shrink"
        view_dir.mkdir(parents=True, exist_ok=True)
        generation = int(self.transport.meta().get("generation", 0)) + 1
        atomic_write_json(view_dir / f"g{generation}-r{self.rank}.json", {"rank": self.rank, "live": view})
        deadline = time.time() + 30
        views = {self.rank: set(view)}
        while time.time() < deadline:
            self.heartbeat()
            for r in list(view):
                if r in views:
                    continue
                path = view_dir / f"g{generation}-r{r}.json"
                if path.exists():
                    try:
                        views[r] = set(read_json(path)["live"])
                    except (OSError, KeyError, TypeError):
                        continue
            if all(r in views for r in view):
                break
            time.sleep(self.poll_s)
        agreed = set(view)
        for v in views.values():
            agreed &= v
        live = sorted(agreed)
        if self.rank not in live:
            raise DeadRankError([self.rank], "this rank is not live and cannot join shrink")
        new_name = f"{self.name}.g{generation}"
        new_rank = live.index(self.rank)
        new_size = len(live)
        if new_rank == 0:
            child = FilesystemTransport(self.home, comm=new_name)
            child.bootstrap(new_size, extra={"parent": self.name, "map": live})
        child_meta = self.home / "comms" / new_name / "meta.json"
        wait_deadline = time.time() + 30
        while not child_meta.exists():
            if time.time() > wait_deadline:
                raise TimeoutError("shrink did not materialize")
            time.sleep(self.poll_s)
        self.transport.update_meta(generation=generation)
        self.transport.emit({"event": "shrink", "src": self.rank, "live": live, "new": new_name})
        return Communicator(
            self.home,
            rank=new_rank,
            size=new_size,
            name=new_name,
            role=self.role,
            eager_bytes=self.transport.eager_bytes,
            context_budget=self.context_budget,
            heartbeat_s=self.heartbeat_s,
            failure_timeout_s=self.failure_timeout_s,
            poll_s=self.poll_s,
        )

    def spawn(self, n: int, role: str = "worker") -> dict[str, Any]:
        """Advertise n new ranks (MPI_Comm_spawn analog). A launcher fills them."""
        ticket = {
            "parent": self.name,
            "n": n,
            "role": role,
            "from_rank": self.rank,
            "ts": now(),
        }
        path = self.home / "spawns" / f"{self.name}-{self.rank}-{int(now())}.json"
        atomic_write_json(path, ticket)
        self.transport.emit({"event": "spawn", "src": self.rank, "n": n, "role": role})
        return ticket

    def comm_split(self, color: int, key: int | None = None) -> Communicator:
        """MPI_Comm_split: group ranks by color, order by key then rank."""
        cid = self._next_cid()
        key = self.rank if key is None else key
        roster = self.allgather({"color": color, "key": key, "rank": self.rank})
        members = [e for e in roster if e["color"] == color]
        members.sort(key=lambda e: (e["key"], e["rank"]))
        new_rank = next(i for i, e in enumerate(members) if e["rank"] == self.rank)
        new_size = len(members)
        new_name = f"{self.name}.c{color}.s{cid}"
        if new_rank == 0:
            FilesystemTransport(self.home, comm=new_name).bootstrap(
                new_size, extra={"parent": self.name, "color": color, "map": [e["rank"] for e in members]}
            )
        deadline = time.time() + 30
        meta = self.home / "comms" / new_name / "meta.json"
        while not meta.exists():
            if time.time() > deadline:
                raise TimeoutError("comm_split did not materialize")
            time.sleep(self.poll_s)
        return Communicator(
            self.home,
            rank=new_rank,
            size=new_size,
            name=new_name,
            role=self.role,
            eager_bytes=self.transport.eager_bytes,
            context_budget=self.context_budget,
            heartbeat_s=self.heartbeat_s,
            failure_timeout_s=self.failure_timeout_s,
            poll_s=self.poll_s,
        )
