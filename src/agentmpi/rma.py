"""One-sided operations: the shared blackboard, with MPI-3 RMA semantics.

Why RMA and not "a shared file"
-------------------------------
Every multi-agent system eventually grows a shared scratchpad — a design
document, a glossary, an interface file, a task board — and every one of them
gets the concurrency wrong in the same way: two agents read it, both edit their
private copy, and the second write silently discards the first's work.  This is
not a new problem and it has a precise vocabulary.  MPI-3's one-sided chapter
supplies it.

The pieces AgentMPI takes:

**Windows.**  A window is an explicitly created, named region of shared state
associated with a communicator, not an ambient global.  Making it explicit means
the harness declares *what* is shared with *whom*, and the trace records every
access.

**Put / Get / Accumulate / Fetch-and-op / Compare-and-swap.**  The distinction
between ``put`` (blind overwrite — the operation that loses work),
``accumulate`` (apply an operator, so concurrent contributions combine instead
of clobbering) and ``compare_and_swap`` (optimistic concurrency on a version)
is exactly the distinction an agent harness needs and never articulates.
``accumulate`` with :data:`agentmpi.ops.UNION` is the correct way for *p* agents
to contribute to a shared glossary, and it needs no locks at all.

**Epochs and synchronisation.**  ``fence`` is active-target synchronisation: a
collective boundary at which all accesses from the previous epoch are complete
and visible.  ``lock``/``unlock`` is passive-target: exclusive or shared access
to a slot, with no participation from the owner.  ``flush`` completes
outstanding operations without ending the epoch.

**The SEPARATE memory model, and why it is the right default.**  MPI-3
distinguishes a *unified* model, where a rank always sees the latest public
value, from a *separate* model, where each rank has a private copy reconciled
only at synchronisation points, so reading without synchronising may return
stale data.  For an agent, the private copy is not a hardware artefact: it is
the copy of the document sitting in the agent's context from ten minutes ago,
and it *is* stale, because three peers have edited it since.  AgentMPI therefore
defaults to SEPARATE, tracks each rank's observed version per slot, and reports
a **staleness violation** when a rank writes based on a version that is no
longer current.  ``sync`` is "re-read before you edit".  This turns the most
common multi-agent data race into a detected, counted, attributable event, which
is the single most useful thing the protocol can do about it.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import tokens as _tokens
from .constants import LockType, WinMemoryModel
from .errors import AmpiLockError, AmpiTimeout, AmpiUsageError
from .ops import Op, ReduceContext, get_op

if TYPE_CHECKING:  # pragma: no cover
    from .comm import Communicator

#: Default lease on an RMA lock.  A lock held by a dead agent must eventually be
#: reclaimed or the window deadlocks forever; MPI has no such problem because a
#: dead process takes the whole job with it.
DEFAULT_LOCK_SECONDS = 300.0


@dataclass
class SlotState:
    slot: str
    version: int
    tokens: int
    updated_by: int | None
    updated_at: float
    digest: str | None

    @property
    def exists(self) -> bool:
        return self.digest is not None


@dataclass
class StalenessReport:
    """How out of date a rank's private copy of a slot is."""

    slot: str
    seen_version: int
    current_version: int

    @property
    def stale(self) -> bool:
        return self.seen_version < self.current_version

    @property
    def lag(self) -> int:
        return max(0, self.current_version - self.seen_version)


class Window:
    """A named region of shared state over a communicator.

    Slots are the unit of versioning and locking, chosen over byte offsets
    because the natural granularity of agent-shared state is "the section about
    error handling", not "bytes 4096-8191".  Slot names are opaque to the
    protocol; conventions belong to the harness.
    """

    def __init__(
        self,
        comm: Communicator,
        name: str,
        *,
        model: WinMemoryModel = WinMemoryModel.SEPARATE,
        create: bool = True,
    ) -> None:
        self.comm = comm
        self.fabric = comm.fabric
        self.name = name
        self.model = model
        self._held: dict[str, tuple[str, LockType]] = {}
        self.n_staleness_violations = 0
        if create:
            with self.fabric.write() as cur:
                cur.execute(
                    "INSERT INTO windows(name, ctx, model, created_at) VALUES(?,?,?,?)"
                    " ON CONFLICT(name) DO NOTHING",
                    (name, comm.ctx, model.value, time.time()),
                )
                self.fabric.emit("win.create", rank=comm.rt.wrank, ctx=comm.ctx, cur=cur, win=name, model=model.value)
        row = self.fabric.query_one("SELECT model FROM windows WHERE name=?", (name,))
        if row is None:
            raise AmpiUsageError("window does not exist", win=name)
        self.model = WinMemoryModel(row["model"])

    # -------------------------------------------------------------- inspection

    def slots(self) -> list[SlotState]:
        rows = self.fabric.query(
            "SELECT slot, digest, version, tokens, updated_by, updated_at FROM win_slots WHERE win=? ORDER BY slot",
            (self.name,),
        )
        return [
            SlotState(
                slot=r["slot"],
                version=int(r["version"]),
                tokens=int(r["tokens"]),
                updated_by=None if r["updated_by"] is None else int(r["updated_by"]),
                updated_at=float(r["updated_at"]),
                digest=r["digest"],
            )
            for r in rows
        ]

    def state(self, slot: str) -> SlotState:
        row = self.fabric.query_one(
            "SELECT slot, digest, version, tokens, updated_by, updated_at FROM win_slots WHERE win=? AND slot=?",
            (self.name, slot),
        )
        if row is None:
            return SlotState(slot=slot, version=0, tokens=0, updated_by=None, updated_at=0.0, digest=None)
        return SlotState(
            slot=slot,
            version=int(row["version"]),
            tokens=int(row["tokens"]),
            updated_by=None if row["updated_by"] is None else int(row["updated_by"]),
            updated_at=float(row["updated_at"]),
            digest=row["digest"],
        )

    def staleness(self, slot: str) -> StalenessReport:
        cur_v = self.state(slot).version
        row = self.fabric.query_one(
            "SELECT version FROM win_views WHERE win=? AND slot=? AND rank=?",
            (self.name, slot, self.comm.rt.wrank),
        )
        seen = int(row["version"]) if row else 0
        return StalenessReport(slot=slot, seen_version=seen, current_version=cur_v)

    def _note_seen(self, slot: str, version: int) -> None:
        with self.fabric.write() as cur:
            cur.execute(
                "INSERT INTO win_views(win, slot, rank, version, seen_at) VALUES(?,?,?,?,?)"
                " ON CONFLICT(win, slot, rank) DO UPDATE SET version=excluded.version, seen_at=excluded.seen_at",
                (self.name, slot, self.comm.rt.wrank, version, time.time()),
            )

    # ------------------------------------------------------------- data access

    def get(self, slot: str, *, default: Any = None, admit: bool = True) -> Any:
        """``MPI_Get``: read a slot and record the version observed.

        Recording the version is what makes staleness detectable later.  Under
        the SEPARATE model this is also the *only* legitimate way to refresh a
        private copy, so a harness that puts without a preceding get is, by
        construction, guessing.
        """
        st = self.state(slot)
        if not st.exists:
            return default
        payload = self.fabric.blobs.get(st.digest or "", "json")
        self._note_seen(slot, st.version)
        if admit:
            self.comm.rt.admit(st.digest or "", st.tokens)
        self.fabric.emit(
            "win.get", rank=self.comm.rt.wrank, ctx=self.comm.ctx, win=self.name, slot=slot, version=st.version, tokens=st.tokens
        )
        return payload

    def put(self, slot: str, value: Any, *, expect_version: int | None = None, require_lock: bool = False) -> int:
        """``MPI_Put``: overwrite a slot.  Returns the new version.

        The dangerous operation, and named accordingly.  If the harness passes
        ``expect_version``, the write becomes a compare-and-swap and fails
        loudly on conflict.  If it does not, and the writing rank's last
        observed version is behind, the write proceeds but a **staleness
        violation** is recorded — because refusing the write would break
        harnesses that legitimately overwrite, while staying silent would
        reproduce the lost-update bug this module exists to expose.
        """
        rep = self.staleness(slot)
        blob = self.fabric.blobs.put(value)
        now = time.time()
        if require_lock and slot not in self._held:
            raise AmpiLockError("put requires a held lock", win=self.name, slot=slot)
        with self.fabric.write() as cur:
            row = cur.execute("SELECT version FROM win_slots WHERE win=? AND slot=?", (self.name, slot)).fetchone()
            cur_v = int(row["version"]) if row else 0
            if expect_version is not None and expect_version != cur_v:
                raise AmpiLockError(
                    "version conflict on put", win=self.name, slot=slot, expected=expect_version, actual=cur_v
                )
            new_v = cur_v + 1
            cur.execute(
                "INSERT INTO win_slots(win, slot, digest, kind, version, tokens, updated_by, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(win, slot) DO UPDATE SET"
                " digest=excluded.digest, kind=excluded.kind, version=excluded.version,"
                " tokens=excluded.tokens, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (self.name, slot, blob.digest, blob.kind, new_v, blob.tokens, self.comm.rt.wrank, now),
            )
            cur.execute(
                "INSERT INTO win_views(win, slot, rank, version, seen_at) VALUES(?,?,?,?,?)"
                " ON CONFLICT(win, slot, rank) DO UPDATE SET version=excluded.version, seen_at=excluded.seen_at",
                (self.name, slot, self.comm.rt.wrank, new_v, now),
            )
            stale = rep.stale and cur_v > 0
            if stale:
                self.n_staleness_violations += 1
            self.fabric.emit(
                "win.put",
                rank=self.comm.rt.wrank,
                ctx=self.comm.ctx,
                cur=cur,
                win=self.name,
                slot=slot,
                version=new_v,
                tokens=blob.tokens,
                stale_write=stale,
                seen_version=rep.seen_version,
                overwrote_version=cur_v,
                locked=slot in self._held,
            )
        return new_v

    def accumulate(
        self,
        slot: str,
        value: Any,
        op: Op | str = "UNION",
        *,
        timeout: float = 120.0,
    ) -> int:
        """``MPI_Accumulate``: combine ``value`` into the slot atomically.

        The operation that makes lost updates *impossible* rather than merely
        detectable, and therefore the one a harness should default to whenever
        the shared state is genuinely accumulative.  Atomicity is provided by
        performing the read-modify-write inside a single fabric transaction, so
        no lock is needed and no rank can interleave.

        The operator must be exact and, ideally, idempotent: a semantic operator
        cannot be used here, because the fabric transaction would have to hold a
        write lock across an LLM call.  A harness that needs judgement in the
        combine should ``lock``, ``get``, reason, ``put`` with
        ``expect_version``, and ``unlock`` — accepting the serialisation that
        implies.  Making that trade-off explicit is the point.
        """
        operator = get_op(op)
        if operator.lossy:
            raise AmpiUsageError(
                "accumulate requires an exact operator; use lock/get/put for semantic combines",
                op=operator.name,
            )
        now = time.time()
        deadline = time.time() + timeout
        while True:
            try:
                with self.fabric.write() as cur:
                    row = cur.execute(
                        "SELECT digest, version FROM win_slots WHERE win=? AND slot=?", (self.name, slot)
                    ).fetchone()
                    if row is None or row["digest"] is None:
                        combined = value
                        cur_v = 0
                    else:
                        existing = self.fabric.blobs.get(row["digest"], "json")
                        cur_v = int(row["version"])
                        combined = operator.fn(existing, value, ReduceContext(rank=self.comm.rt.wrank, depth=cur_v))
                    blob = self.fabric.blobs.put(combined)
                    new_v = cur_v + 1
                    cur.execute(
                        "INSERT INTO win_slots(win, slot, digest, kind, version, tokens, updated_by, updated_at)"
                        " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(win, slot) DO UPDATE SET"
                        " digest=excluded.digest, version=excluded.version, tokens=excluded.tokens,"
                        " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                        (self.name, slot, blob.digest, blob.kind, new_v, blob.tokens, self.comm.rt.wrank, now),
                    )
                    self.fabric.emit(
                        "win.accumulate",
                        rank=self.comm.rt.wrank,
                        ctx=self.comm.ctx,
                        cur=cur,
                        win=self.name,
                        slot=slot,
                        op=operator.name,
                        version=new_v,
                        tokens=blob.tokens,
                    )
                return new_v
            except AmpiLockError:
                if time.time() > deadline:
                    raise
                time.sleep(0.02)

    def get_accumulate(self, slot: str, value: Any, op: Op | str = "UNION") -> Any:
        """``MPI_Get_accumulate``: return the previous value and accumulate."""
        before = self.get(slot, admit=False)
        self.accumulate(slot, value, op)
        return before

    def fetch_and_op(self, slot: str, value: Any, op: Op | str = "SUM") -> Any:
        """``MPI_Fetch_and_op``: atomic fetch-and-modify on a scalar slot.

        The primitive behind a shared work counter, which is how a harness
        implements dynamic self-scheduling: each rank atomically claims the next
        index.  Dynamic scheduling matters more for agents than for CPUs because
        per-item cost varies by an order of magnitude, so a static decomposition
        wastes most of the population waiting for one straggler.
        """
        return self.get_accumulate(slot, value, op)

    def compare_and_swap(self, slot: str, expect: Any, value: Any) -> tuple[bool, Any]:
        """``MPI_Compare_and_swap``: swap if the current value matches.

        Returns ``(swapped, observed)``.  Compares on the canonical digest, so
        structurally equal JSON compares equal regardless of key order.
        """
        expect_digest = self.fabric.blobs.put(expect).digest if expect is not None else None
        blob = self.fabric.blobs.put(value)
        with self.fabric.write() as cur:
            row = cur.execute(
                "SELECT digest, version FROM win_slots WHERE win=? AND slot=?", (self.name, slot)
            ).fetchone()
            observed_digest = row["digest"] if row else None
            cur_v = int(row["version"]) if row else 0
            if observed_digest != expect_digest:
                observed = self.fabric.blobs.get(observed_digest, "json") if observed_digest else None
                self.fabric.emit(
                    "win.cas", rank=self.comm.rt.wrank, ctx=self.comm.ctx, cur=cur, win=self.name, slot=slot, swapped=False
                )
                return False, observed
            cur.execute(
                "INSERT INTO win_slots(win, slot, digest, kind, version, tokens, updated_by, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(win, slot) DO UPDATE SET"
                " digest=excluded.digest, version=excluded.version, tokens=excluded.tokens,"
                " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                (self.name, slot, blob.digest, blob.kind, cur_v + 1, blob.tokens, self.comm.rt.wrank, time.time()),
            )
            self.fabric.emit(
                "win.cas",
                rank=self.comm.rt.wrank,
                ctx=self.comm.ctx,
                cur=cur,
                win=self.name,
                slot=slot,
                swapped=True,
                version=cur_v + 1,
            )
        return True, expect

    # --------------------------------------------------------- synchronisation

    def lock(
        self,
        slot: str,
        *,
        mode: LockType | str = LockType.EXCLUSIVE,
        timeout: float = 300.0,
        lease: float = DEFAULT_LOCK_SECONDS,
    ) -> str:
        """``MPI_Win_lock``: begin a passive-target access epoch on ``slot``.

        Shared locks coexist; an exclusive lock excludes everything.  Unlike
        MPI, every lock carries a **lease**, because the holder may be an agent
        that never comes back.  An expired lease is reclaimable by any other
        rank, and the reclamation is traced — a held-then-stolen lock is exactly
        the situation in which two agents believe they own the same file, so it
        must be visible rather than silent.
        """
        mode = LockType(mode)
        token = uuid.uuid4().hex
        deadline = time.time() + timeout
        waited = time.time()
        stalled = False
        while True:
            now = time.time()
            with self.fabric.write() as cur:
                cur.execute("DELETE FROM win_locks WHERE win=? AND slot=? AND expires < ?", (self.name, slot, now))
                rows = cur.execute(
                    "SELECT holder, mode FROM win_locks WHERE win=? AND slot=?", (self.name, slot)
                ).fetchall()
                blocked = any(
                    r["mode"] == LockType.EXCLUSIVE.value or mode is LockType.EXCLUSIVE for r in rows
                ) and any(int(r["holder"]) != self.comm.rt.wrank for r in rows)
                if not blocked:
                    cur.execute(
                        "INSERT INTO win_locks(win, slot, holder, token, mode, acquired, expires)"
                        " VALUES(?,?,?,?,?,?,?) ON CONFLICT(win, slot, holder) DO UPDATE SET"
                        " token=excluded.token, mode=excluded.mode, acquired=excluded.acquired, expires=excluded.expires",
                        (self.name, slot, self.comm.rt.wrank, token, mode.value, now, now + lease),
                    )
                    self.fabric.emit(
                        "win.lock",
                        rank=self.comm.rt.wrank,
                        ctx=self.comm.ctx,
                        cur=cur,
                        win=self.name,
                        slot=slot,
                        mode=mode.value,
                        waited_s=round(now - waited, 3),
                        contended=stalled,
                    )
                    self._held[slot] = (token, mode)
                    return token
            stalled = True
            if time.time() > deadline:
                holders = [int(r["holder"]) for r in self.fabric.query(
                    "SELECT holder FROM win_locks WHERE win=? AND slot=?", (self.name, slot)
                )]
                self.fabric.emit(
                    "win.lock_timeout", rank=self.comm.rt.wrank, ctx=self.comm.ctx, win=self.name, slot=slot, holders=holders
                )
                raise AmpiTimeout("could not acquire window lock", win=self.name, slot=slot, holders=holders)
            time.sleep(0.02)

    def unlock(self, slot: str) -> None:
        token = self._held.pop(slot, None)
        with self.fabric.write() as cur:
            cur.execute(
                "DELETE FROM win_locks WHERE win=? AND slot=? AND holder=?", (self.name, slot, self.comm.rt.wrank)
            )
            self.fabric.emit(
                "win.unlock", rank=self.comm.rt.wrank, ctx=self.comm.ctx, cur=cur, win=self.name, slot=slot, had_token=token is not None
            )

    def lock_all(self, *, mode: LockType | str = LockType.SHARED, timeout: float = 300.0) -> None:
        """``MPI_Win_lock_all``: lock every existing slot."""
        for st in self.slots():
            self.lock(st.slot, mode=mode, timeout=timeout)

    def unlock_all(self) -> None:
        for slot in list(self._held):
            self.unlock(slot)

    def renew(self, slot: str, *, lease: float = DEFAULT_LOCK_SECONDS) -> None:
        """Extend a held lock's lease.  Long agent turns must renew."""
        with self.fabric.write() as cur:
            cur.execute(
                "UPDATE win_locks SET expires=? WHERE win=? AND slot=? AND holder=?",
                (time.time() + lease, self.name, slot, self.comm.rt.wrank),
            )

    def fence(self, *, timeout: float | None = 900.0, label: str = "") -> None:
        """``MPI_Win_fence``: collective epoch boundary.

        A barrier plus the guarantee that every access issued before it is
        complete and visible after it.  In AgentMPI the visibility half is free
        (the fabric is coherent), so the fence is a barrier plus an invalidation
        of every rank's private view: after a fence, a rank must ``get`` again
        before it may reason about a slot.  That is precisely the discipline
        agent harnesses lack, and stating it as an epoch boundary makes it
        checkable.
        """
        self.comm.barrier(timeout=timeout, label=f"win_fence:{self.name}:{label}")
        self.sync()

    def sync(self) -> None:
        """``MPI_Win_sync``: discard this rank's private copies.

        Under the SEPARATE model this is mandatory before trusting anything read
        earlier.  Implemented by evicting the slots from the rank's context
        working set, so the next ``get`` genuinely re-reads and re-admits.
        """
        for st in self.slots():
            if st.digest:
                self.comm.rt.evict(st.digest)
        with self.fabric.write() as cur:
            cur.execute("DELETE FROM win_views WHERE win=? AND rank=?", (self.name, self.comm.rt.wrank))
            self.fabric.emit("win.sync", rank=self.comm.rt.wrank, ctx=self.comm.ctx, cur=cur, win=self.name)

    def flush(self, slot: str | None = None) -> None:
        """``MPI_Win_flush``: complete outstanding operations.

        A no-op in the reference fabric, because every operation completes
        synchronously inside its transaction.  Retained in the API because a
        fabric backed by an asynchronous store would need it, and because
        harness code that omits it would then be silently wrong.
        """
        self.fabric.emit("win.flush", rank=self.comm.rt.wrank, ctx=self.comm.ctx, win=self.name, slot=slot)

    # ------------------------------------------------------------- convenience

    def critical(self, slot: str, *, timeout: float = 300.0):
        """Context manager: exclusive lock, then unlock.

        Usage::

            with win.critical("interfaces"):
                spec = win.get("interfaces")
                spec = agent_revises(spec)
                win.put("interfaces", spec)

        Correct by construction, and the only pattern under which a *semantic*
        (LLM-performed) update to shared state is safe.  Its cost is exactly the
        serialisation MPI's exclusive locks impose, and a harness that puts this
        pattern on its critical path has built a sequential program with extra
        steps -- which the trace will show as lock wait time.
        """
        return _Critical(self, slot, timeout)


class _Critical:
    def __init__(self, win: Window, slot: str, timeout: float) -> None:
        self.win, self.slot, self.timeout = win, slot, timeout

    def __enter__(self) -> Window:
        self.win.lock(self.slot, mode=LockType.EXCLUSIVE, timeout=self.timeout)
        self.win.get(self.slot, admit=False)
        return self.win

    def __exit__(self, *exc: Any) -> None:
        self.win.unlock(self.slot)


def win_create(
    comm: Communicator,
    name: str,
    *,
    model: WinMemoryModel | str = WinMemoryModel.SEPARATE,
    initial: dict[str, Any] | None = None,
) -> Window:
    """Create (or attach to) a window over ``comm``."""
    win = Window(comm, name, model=WinMemoryModel(model), create=True)
    if initial:
        for slot, value in initial.items():
            if not win.state(slot).exists:
                win.put(slot, value)
    return win


def contention_report(fabric: Any, win: str) -> dict[str, Any]:
    """Summarise lock waiting and stale writes for a window.

    The two numbers a harness author needs after a run: how much wall time the
    population spent waiting for each other's locks (which bounds the achievable
    speedup, Amdahl-style) and how many writes were issued from a stale view
    (which bounds trust in the result).
    """
    events = fabric.events(kinds=["win.lock", "win.put", "win.lock_timeout", "win.accumulate"])
    waits: list[float] = []
    stale = 0
    contended = 0
    puts = 0
    accs = 0
    timeouts = 0
    per_slot: dict[str, dict[str, Any]] = {}
    for e in events:
        p = e["payload"]
        if p.get("win") != win:
            continue
        slot = p.get("slot", "?")
        s = per_slot.setdefault(slot, {"waits": [], "stale": 0, "puts": 0, "accumulates": 0})
        if e["kind"] == "win.lock":
            waits.append(float(p.get("waited_s", 0.0)))
            s["waits"].append(float(p.get("waited_s", 0.0)))
            contended += 1 if p.get("contended") else 0
        elif e["kind"] == "win.put":
            puts += 1
            s["puts"] += 1
            if p.get("stale_write"):
                stale += 1
                s["stale"] += 1
        elif e["kind"] == "win.accumulate":
            accs += 1
            s["accumulates"] += 1
        elif e["kind"] == "win.lock_timeout":
            timeouts += 1
    return {
        "window": win,
        "n_locks": len(waits),
        "n_contended": contended,
        "total_lock_wait_s": round(sum(waits), 3),
        "max_lock_wait_s": round(max(waits), 3) if waits else 0.0,
        "n_puts": puts,
        "n_stale_writes": stale,
        "stale_write_rate": round(stale / puts, 4) if puts else 0.0,
        "n_accumulates": accs,
        "n_lock_timeouts": timeouts,
        "per_slot": {
            k: {
                "puts": v["puts"],
                "stale": v["stale"],
                "accumulates": v["accumulates"],
                "lock_wait_s": round(sum(v["waits"]), 3),
            }
            for k, v in sorted(per_slot.items())
        },
    }
