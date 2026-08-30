"""Initialisation, finalisation, and the launcher.

``init`` / ``finalize`` mirror ``MPI_Init`` / ``MPI_Finalize``, and ``launch``
mirrors ``mpiexec``: it is the *process manager*, deliberately separate from the
protocol, and it is the only place that knows how many ranks exist and how they
are embodied.

Session model
-------------
MPI-4 added *sessions* because the world model has a genuine defect: ``MPI_Init``
is global process state, so a library cannot initialise MPI without interfering
with its caller, and there is no way to have two independent MPI universes in one
process.  AgentMPI adopts the session model from the start — :func:`init` returns
a :class:`Session` handle and nothing is stored in module globals — because the
defect is worse here.  A harness is very often *itself* a component invoked by a
larger agent system, so a design in which "the runtime" is a process-wide
singleton cannot compose, and composability is the whole justification for
building a protocol rather than a framework.

The convenience global exists (:func:`world`) for scripts, and is explicitly a
convenience.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .comm import Communicator, make_world
from .constants import (
    DEFAULT_CONTEXT_BUDGET,
    DEFAULT_EAGER_LIMIT,
    DEFAULT_LEASE_SECONDS,
    RankState,
)
from .errors import AmpiError, AmpiUsageError
from .fabric import Fabric, open_fabric
from .rank import RankRuntime
from .executor import Executor, FunctionExecutor

#: Set by :func:`init` for the convenience accessors.  A *cache*, not state: the
#: session object returned by ``init`` remains the authoritative handle.
_CURRENT: threading.local = threading.local()


@dataclass
class Session:
    """A handle on one rank's participation in one job.

    The AgentMPI analogue of an MPI session.  Holds the fabric connection, the
    rank runtime and the world communicator; everything else is derived.
    """

    fabric: Fabric
    rt: RankRuntime
    world: Communicator
    started_at: float = field(default_factory=time.time)
    _finalized: bool = False

    @property
    def rank(self) -> int:
        return self.rt.wrank

    @property
    def size(self) -> int:
        return self.world.size

    def finalize(self, state: RankState = RankState.FINALIZED) -> None:
        if self._finalized:
            return
        self._finalized = True
        self.rt.finalize(state)
        self.fabric.close()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.finalize(RankState.FAILED if exc_type else RankState.FINALIZED)


def init(
    root: str | os.PathLike[str] | None = None,
    *,
    rank: int | None = None,
    size: int | None = None,
    executor: Executor | Callable[..., Any] | None = None,
    context_budget: int = DEFAULT_CONTEXT_BUDGET,
    eager_limit: int = DEFAULT_EAGER_LIMIT,
    unexpected_limit: int | None = None,
    lease_seconds: float = DEFAULT_LEASE_SECONDS,
    name: str = "",
    strict_context: bool = True,
    create: bool = False,
) -> Session:
    """Join a job as ``rank``.

    ``rank`` and ``root`` default to ``$AMPI_RANK`` and ``$AMPI_ROOT``, so a
    worker process launched by any process manager needs no arguments — the same
    contract MPI has with PMI.
    """
    if root is None:
        root = os.environ.get("AMPI_ROOT")
    if root is None:
        raise AmpiUsageError("no fabric root; pass root= or set $AMPI_ROOT")
    if rank is None:
        rank = int(os.environ.get("AMPI_RANK", "-1"))
        if rank < 0:
            raise AmpiUsageError("no rank; pass rank= or set $AMPI_RANK")
    if size is None and "AMPI_SIZE" in os.environ:
        size = int(os.environ["AMPI_SIZE"])

    fabric = Fabric(root, create=create)
    exec_fn = executor if executor is not None else None
    if exec_fn is not None and not isinstance(exec_fn, Executor) and callable(exec_fn):
        exec_fn = FunctionExecutor(fn=exec_fn)
    rt = RankRuntime(
        fabric,
        rank,
        executor=exec_fn,
        context_budget=context_budget,
        eager_limit=eager_limit,
        unexpected_limit=unexpected_limit,
        lease_seconds=lease_seconds,
        name=name,
        strict_context=strict_context,
    )
    rt.register(executor_name=getattr(exec_fn, "name", "none"))
    comm = make_world(fabric, rt, size)
    session = Session(fabric=fabric, rt=rt, world=comm)
    _CURRENT.session = session
    return session


def world() -> Communicator:
    """The world communicator of the current thread's session."""
    session = getattr(_CURRENT, "session", None)
    if session is None:
        raise AmpiUsageError("no active session in this thread; call init() first")
    return session.world


def finalize() -> None:
    session = getattr(_CURRENT, "session", None)
    if session is not None:
        session.finalize()
        _CURRENT.session = None


def create_job(
    root: str | os.PathLike[str],
    size: int,
    *,
    label: str = "",
    metadata: dict[str, Any] | None = None,
) -> Fabric:
    """Create a fresh fabric and declare the world communicator's size.

    The analogue of what ``mpiexec`` does before any rank starts: fix the size
    of ``COMM_WORLD``.  AgentMPI keeps MPI's decision that the world size is
    static, and the decision is defensible for the same reason: a static rank
    space makes every collective's cost and every work decomposition
    computable in advance.  Populations that must grow use ``spawn``
    (:func:`spawn`), which produces a *new* communicator rather than mutating
    the world — again following MPI-2.
    """
    fabric = Fabric(root, create=True)
    fabric.set_meta("world_size", str(size))
    fabric.set_meta("label", label)
    if metadata:
        for k, v in metadata.items():
            fabric.set_meta(f"meta:{k}", str(v))
    with fabric.write() as cur:
        cur.execute(
            "INSERT INTO comms(ctx, name, parent_ctx, kind, generation, revoked, created_at)"
            " VALUES(0,'world',NULL,'intra',0,0,?) ON CONFLICT(ctx) DO NOTHING",
            (time.time(),),
        )
        cur.executemany(
            "INSERT INTO comm_members(ctx, crank, wrank, state) VALUES(0,?,?,'active')"
            " ON CONFLICT(ctx, crank) DO NOTHING",
            [(i, i) for i in range(size)],
        )
        fabric.emit("job.create", cur=cur, size=size, label=label)
    return fabric


@dataclass
class RankOutcome:
    rank: int
    ok: bool
    value: Any = None
    error: str = ""
    traceback: str = ""
    wall_s: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobResult:
    root: Path
    size: int
    outcomes: list[RankOutcome]
    wall_s: float

    @property
    def ok(self) -> bool:
        return all(o.ok for o in self.outcomes)

    @property
    def failed_ranks(self) -> list[int]:
        return [o.rank for o in self.outcomes if not o.ok]

    def value(self, rank: int = 0) -> Any:
        for o in self.outcomes:
            if o.rank == rank:
                return o.value
        return None

    def totals(self) -> dict[str, Any]:
        return {
            "wall_s": round(self.wall_s, 3),
            "size": self.size,
            "ok": self.ok,
            "failed_ranks": self.failed_ranks,
            "tokens_in": sum(o.cost.get("tokens_in", 0) for o in self.outcomes),
            "tokens_out": sum(o.cost.get("tokens_out", 0) for o in self.outcomes),
            "agent_calls": sum(o.cost.get("agent_calls", 0) for o in self.outcomes),
            "messages": sum(o.cost.get("messages_sent", 0) for o in self.outcomes),
            "tokens_sent": sum(o.cost.get("tokens_sent", 0) for o in self.outcomes),
            "tokens_deferred": sum(o.cost.get("tokens_deferred", 0) for o in self.outcomes),
            "usd": round(sum(o.cost.get("usd", 0.0) for o in self.outcomes), 4),
            "context_high_water": max((o.context.get("high_water", 0) for o in self.outcomes), default=0),
            "context_rejections": sum(o.context.get("rejections", 0) for o in self.outcomes),
        }


def launch(
    rank_main: Callable[[Communicator], Any],
    size: int,
    *,
    root: str | os.PathLike[str] | None = None,
    executor_factory: Callable[[int], Executor | Callable[..., Any] | None] | None = None,
    context_budget: int | Callable[[int], int] = DEFAULT_CONTEXT_BUDGET,
    eager_limit: int = DEFAULT_EAGER_LIMIT,
    unexpected_limit: int | None = None,
    strict_context: bool = True,
    label: str = "",
    ranks: Sequence[int] | None = None,
    fabric: Fabric | None = None,
    timeout: float | None = 7200.0,
) -> JobResult:
    """Run ``rank_main`` once per rank, SPMD-style, in this process.

    One thread per rank.  Threads rather than processes because the fabric,
    not shared memory, is the medium of communication, so isolation buys
    nothing, while threads make a *whole job* debuggable in one stack trace and
    keep the launcher usable inside a test.  Ranks embodied by real agents block
    on the broker, which releases the GIL, so thread-per-rank scales to the
    hundreds without difficulty.

    This is the SPMD form, and it is the form the paper argues for: **the
    protocol lives in the harness, not in the prompt.**  Every AgentMPI call is
    made by trusted host-side code; the agent is invoked as a kernel that
    transforms artifacts.  The alternative — telling the model about the protocol
    and hoping it calls the right collective — is available through the CLI, and
    the paper measures how much worse it is.

    ``timeout`` bounds the whole job and defaults to two hours rather than to
    "forever".  A blocking collective in which one member never arrives is the
    normal case here, so a launcher that could wait indefinitely would be a
    liveness bug in the tool rather than in the program under test.  Ranks that
    exceed the deadline are reported as failed outcomes and the launcher returns.
    """
    root_path = Path(root) if root is not None else Path(os.environ.get("AMPI_ROOT", ".ampi-run"))
    fab = fabric or create_job(root_path, size, label=label)
    target_ranks = list(ranks) if ranks is not None else list(range(size))
    outcomes: list[RankOutcome] = []
    t0 = time.time()

    def _budget(r: int) -> int:
        return context_budget(r) if callable(context_budget) else context_budget

    def _one(r: int) -> RankOutcome:
        started = time.time()
        ex = executor_factory(r) if executor_factory else None
        session = init(
            root_path,
            rank=r,
            size=size,
            executor=ex,
            context_budget=_budget(r),
            eager_limit=eager_limit,
            unexpected_limit=unexpected_limit,
            strict_context=strict_context,
        )
        try:
            value = rank_main(session.world)
            session.finalize()
            return RankOutcome(
                rank=r,
                ok=True,
                value=value,
                wall_s=time.time() - started,
                context=session.rt.context.snapshot(),
                cost=session.rt.cost.snapshot(),
            )
        except BaseException as exc:  # a rank failure must not kill the launcher
            session.rt.finalize(RankState.FAILED)
            session.fabric.emit(
                "rank.error",
                rank=r,
                error=repr(exc),
                error_class=getattr(exc, "cls_name", type(exc).__name__),
            )
            session.fabric.close()
            return RankOutcome(
                rank=r,
                ok=False,
                error=repr(exc),
                traceback=traceback.format_exc(limit=12),
                wall_s=time.time() - started,
                context=session.rt.context.snapshot(),
                cost=session.rt.cost.snapshot(),
            )
        finally:
            _CURRENT.session = None

    # Daemon threads with a bounded join, not a thread pool.  A pool's shutdown
    # waits for its workers, so one rank stuck in a collective would hang the
    # launcher forever -- unacceptable for a runtime whose premise is that ranks
    # get stuck.  With daemon threads the launcher reports the stragglers and
    # returns; the stuck threads cannot outlive the process.
    results: dict[int, RankOutcome] = {}
    threads: dict[int, threading.Thread] = {}

    def _wrap(r: int) -> None:
        try:
            results[r] = _one(r)
        except BaseException as exc:  # pragma: no cover - _one already catches
            results[r] = RankOutcome(rank=r, ok=False, error=repr(exc))

    for r in target_ranks:
        th = threading.Thread(target=_wrap, args=(r,), name=f"ampi-rank-{r}", daemon=True)
        threads[r] = th
        th.start()

    deadline = time.time() + timeout if timeout is not None else None
    for r, th in threads.items():
        remaining = None if deadline is None else max(0.0, deadline - time.time())
        th.join(timeout=remaining)
    for r, th in threads.items():
        if r in results:
            continue
        if th.is_alive():
            fab.emit("rank.stuck", rank=r, waited_s=round(timeout or 0.0, 1))
            results[r] = RankOutcome(
                rank=r,
                ok=False,
                error=f"rank {r} did not complete within the launcher deadline (stuck in a blocking call)",
            )
        else:
            results[r] = RankOutcome(rank=r, ok=False, error=f"rank {r} produced no outcome")
    outcomes = sorted(results.values(), key=lambda o: o.rank)
    result = JobResult(root=root_path, size=size, outcomes=outcomes, wall_s=time.time() - t0)
    fab.emit("job.finish", **result.totals())
    return result


def spawn(
    comm: Communicator,
    count: int,
    *,
    name: str = "spawned",
) -> Communicator:
    """``MPI_Comm_spawn``: extend the population and return a communicator over it.

    Ranks are appended to the world rank space and a new communicator is created
    over the parent plus the children, which is MPI's intercommunicator idea
    flattened into an intracommunicator (AgentMPI does not model
    intercommunicators; the distinction buys little when there is no distinct
    address space per group, and the complexity is what made MPI-2's dynamic
    process management unpopular).

    Spawning is far more important for agents than for MPI programs, where it is
    famously little-used because batch schedulers allocate a fixed node count.
    An agent population has no such constraint, so "the reviewer discovered three
    more subsystems that need owners" is a normal event, and a protocol that
    fixed the population would force the harness to over-provision.
    """
    row = comm.fabric.query_one("SELECT COALESCE(MAX(rank), -1) AS m FROM ranks")
    base = int(row["m"]) + 1 if row else 0
    new_ranks = list(range(base, base + count))
    with comm.fabric.write() as cur:
        for w in new_ranks:
            cur.execute(
                "INSERT INTO ranks(rank, name, state, incarnation, context_budget) VALUES(?,?,?,0,?)"
                " ON CONFLICT(rank) DO NOTHING",
                (w, f"{name}{w}", RankState.PENDING.value, DEFAULT_CONTEXT_BUDGET),
            )
        n_world = int(cur.execute("SELECT COUNT(*) AS n FROM comm_members WHERE ctx=0").fetchone()["n"])
        for i, w in enumerate(new_ranks):
            cur.execute(
                "INSERT INTO comm_members(ctx, crank, wrank, state) VALUES(0,?,?,'active')"
                " ON CONFLICT(ctx, crank) DO NOTHING",
                (n_world + i, w),
            )
        comm.fabric.emit("proc.spawn", rank=comm.rt.wrank, ctx=comm.ctx, cur=cur, new_ranks=new_ranks)
    members = list(comm.members) + new_ranks
    ctx = comm._new_ctx(members, name=name)
    comm.refresh()
    return Communicator(comm.fabric, ctx, comm.rt, name=name)
