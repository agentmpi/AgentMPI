"""The runtime: initialisation, the world communicator, and global state.

``AMPI_Init`` plays the same role as ``MPI_Init``: it establishes this
process's identity within the job, connects it to the transport, and creates
``AMPI_COMM_WORLD``.  Everything else in the protocol hangs off the object
this module returns.

Two departures from MPI are worth flagging up front.

**Late join is normal.**  ``MPI_Init`` assumes every rank starts at
approximately the same time, because ``mpiexec`` launched them together.
AgentMPI ranks are agents that may be spawned minutes apart, may be
restarted, and may join a run already in progress.  The runtime therefore
treats the rank table as *discovered* state in the key-value store rather
than as a launch-time argument, which is the same conclusion MPI reached
much later with Sessions.

**Init is idempotent and resumable.**  A rank that crashes and is respawned
calls ``AMPI_Init`` again with the same rank id and rejoins the same
communicator at the current epoch, replaying nothing it has already
acknowledged.  In MPI that is the exotic ULFM-plus-checkpoint path; here it
is the default, because in a fleet of agents something is always restarting.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from .comm import Communicator
from .constants import (
    CONTEXT_WORLD,
    TAG_UB,
    DEAD_STATES,
    Datatype,
    RankState,
    ThreadLevel,
)
from .context import DIGESTS, ContextBudget
from .datatypes import TypeDescriptor, TypeRegistry, lookup
from .envelope import Envelope
from .errors import (
    AmpiError,
    CollectiveMismatchError,
    ArgError,
    BudgetError,
    CommError,
    ContextOverflowError,
    ProcFailedError,
    StalledError,
)
from .group import Group, RankSpec, RankTable
from .matching import MatchingEngine
from .ops import SemanticOpRegistry
from .trace import JournalProfiler, NullProfiler, Profiler
from .transport import Device, open_device
from .tvars import CVARS, Pvars, default_cvars

_RUNTIME: "Runtime | None" = None

RUN_MANIFEST = "meta.json"


@dataclass
class RunManifest:
    """Immutable description of a run, written once by ``ampi init``."""

    run_id: str
    size: int
    created_at: float
    device: str = "journal"
    label: str = ""
    ranks: list[dict[str, Any]] = field(default_factory=list)
    cvars: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {
                "run_id": self.run_id,
                "size": self.size,
                "created_at": self.created_at,
                "device": self.device,
                "label": self.label,
                "ranks": self.ranks,
                "cvars": self.cvars,
            },
            indent=2,
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "RunManifest":
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return cls(
            run_id=d["run_id"], size=int(d["size"]), created_at=float(d["created_at"]),
            device=d.get("device", "journal"), label=d.get("label", ""),
            ranks=d.get("ranks", []), cvars=d.get("cvars", {}),
        )


class Runtime:
    """Per-rank runtime state."""

    def __init__(
        self,
        device: Device,
        world_rank: int,
        world_size: int,
        *,
        spec: RankSpec | None = None,
        root: str | None = None,
        cvars: dict[str, Any] | None = None,
        thread_level: ThreadLevel = ThreadLevel.SINGLE,
        profiler: Profiler | None = None,
    ) -> None:
        self.device = device
        self.world_rank = world_rank
        self.world_size = world_size
        self.root = root
        self.cvars = {**default_cvars(), **(cvars or {})}
        self.thread_level = thread_level
        self.spec = spec or RankSpec(rank=world_rank)
        self.types = TypeRegistry()
        self.ops = SemanticOpRegistry()
        self.pvars = Pvars()
        self.turn = 0
        self.started_at = time.time()
        self.finalized = False
        self.state = RankState.INIT

        self.budget = ContextBudget(
            capacity=self.spec.context_capacity or int(self.cvars["ampi_context_capacity"]),
            reserve_fraction=float(self.cvars["ampi_context_reserve"]),
            lifetime_tokens=(int(self.cvars["ampi_lifetime_tokens"]) or None),
            currency_budget=(float(self.cvars["ampi_currency_budget"]) or None),
        )
        self.profiler = profiler or (
            JournalProfiler(world_rank, device) if self.cvars["ampi_trace"] else NullProfiler()
        )
        self.matching = MatchingEngine(
            device, world_rank, gap_timeout_s=float(self.cvars["ampi_gap_timeout_s"])
        )
        self.rank_table = RankTable()
        self._comms: dict[str, Communicator] = {}
        self._last_heartbeat = 0.0
        self._failure_cache: dict[str, tuple[float, set[int]]] = {}

        # Durable protocol state.  An AgentMPI rank is often a *sequence of
        # short-lived processes* -- an agent that runs `ampi recv`, thinks,
        # then runs `ampi send` -- so the counters that implement
        # non-overtaking and collective tag separation cannot live in memory.
        self.send_seq: dict[str, int] = {}
        self.coll_counter: dict[str, int] = {}
        self.coll_log: dict[str, dict[int, str]] = {}
        self._mismatch_since: dict[str, float] = {}
        self.persist_state = device.name != "inproc"
        if self.persist_state:
            self._load_state()

        self.world = self._make_world()
        self._register_pvars()

    # -- durable protocol state -------------------------------------------
    def _state_key(self) -> str:
        return f"pstate/{self.world_rank}"

    def _load_state(self) -> None:
        raw = self.device.kv_get(self._state_key())
        if not raw:
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        self.send_seq = {k: int(v) for k, v in data.get("send_seq", {}).items()}
        self.coll_counter = {k: int(v) for k, v in data.get("coll_counter", {}).items()}
        self.coll_log = {ctx: {int(k): v for k, v in log.items()}
                         for ctx, log in data.get("coll_log", {}).items()}
        self.turn = int(data.get("turn", 0))
        for key, value in data.get("consumed", {}).items():
            ctx, src, dst = key.rsplit("|", 2)
            self.matching._consumed_wm[(ctx, int(src), int(dst))] = int(value["wm"])
            self.matching._consumed_extra[(ctx, int(src), int(dst))] = set(value["extra"])
        budget = data.get("budget") or {}
        self.budget.ingested = int(budget.get("ingested", 0))
        self.budget.emitted = int(budget.get("emitted", 0))
        self.budget.compacted_away = int(budget.get("compacted_away", 0))
        self.budget.currency_spent = float(budget.get("currency_spent", 0.0))

    def save_state(self) -> None:
        """Persist the counters a future process of this rank must inherit.

        Note what is *not* saved: the unexpected-message queue.  It does not
        need to be, because unmatched messages are still sitting in the
        device's inbox and the ``seen`` markers are per-message; a new
        process simply re-polls.  Durability of the transport is what lets
        the endpoint be stateless, which is the same argument that lets an
        HTTP server be stateless in front of a durable log.
        """
        if not self.persist_state:
            return
        consumed = {}
        for key in set(self.matching._consumed_wm) | set(self.matching._consumed_extra):
            ctx, src, dst = key
            wm = self.matching._consumed_wm[key]
            extra = sorted(self.matching._consumed_extra[key])
            if wm or extra:
                consumed[f"{ctx}|{src}|{dst}"] = {"wm": wm, "extra": extra}
        self.device.kv_put(self._state_key(), json.dumps({
            "send_seq": self.send_seq,
            "coll_counter": self.coll_counter,
            "coll_log": {ctx: {str(k): v for k, v in log.items()}
                         for ctx, log in self.coll_log.items()},
            "consumed": consumed,
            "turn": self.turn,
            "budget": self.budget.snapshot(),
        }))

    # -- construction ------------------------------------------------------
    def _make_world(self) -> Communicator:
        self._refresh_rank_table()
        group = self.rank_table.group(self.world_size)
        epoch = int(self.device.kv_get("epoch/world") or 0)
        comm = Communicator(self, CONTEXT_WORLD, group, name="AMPI_COMM_WORLD", epoch=epoch)
        self.matching.bump_epoch(CONTEXT_WORLD, epoch)
        return self._register_comm(comm)

    def _register_comm(self, comm: Communicator) -> Communicator:
        self._comms[comm.context] = comm
        return comm

    def _unregister_comm(self, comm: Communicator) -> None:
        self._comms.pop(comm.context, None)

    def _register_pvars(self) -> None:
        self.pvars.set_gauge("context_pressure", lambda: self.budget.pressure)
        self.pvars.set_gauge("tokens_recv", lambda: float(self.budget.ingested))
        self.pvars.set_gauge("tokens_sent", lambda: float(self.budget.emitted))
        self.pvars.set_gauge("currency_spent", lambda: self.budget.currency_spent)
        self.pvars.set_gauge("turns", lambda: float(self.turn))
        self.pvars.set_gauge("cache_hits", lambda: float(self.ops.stats["hits"]))

    def self_comm(self) -> Communicator:
        """``AMPI_COMM_SELF``."""
        ctx = f"self/{self.world_rank}"
        if ctx in self._comms:
            return self._comms[ctx]
        return self._register_comm(
            Communicator(self, ctx, Group((self.world_rank,), (self.spec,)),
                         name="AMPI_COMM_SELF")
        )

    # -- rank registry (the PMIx "business card" exchange) -----------------
    def publish_spec(self) -> None:
        from dataclasses import asdict

        self.device.kv_put(f"rank/{self.world_rank}", json.dumps(asdict(self.spec)))
        self.device.kv_put(
            f"state/{self.world_rank}",
            json.dumps({"state": self.state.value, "ts": time.time(), "turn": self.turn}),
        )

    def _refresh_rank_table(self) -> None:
        specs = []
        for key in self.device.kv_list("rank/"):
            raw = self.device.kv_get(key)
            if not raw:
                continue
            try:
                specs.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
        self.rank_table = RankTable.from_json(specs)

    # -- heartbeats and failure detection ----------------------------------
    def heartbeat(self, force: bool = False) -> None:
        """Publish liveness and progress.

        Two numbers, not one.  ``ts`` says the process exists; ``turn`` says
        it is getting somewhere.  A failure detector built on the first alone
        cannot see the most common agent pathology -- a process that is
        perfectly healthy and looping forever -- and one built on the second
        alone cannot distinguish a long turn from a dead process.
        """
        now = time.time()
        if not force and now - self._last_heartbeat < float(self.cvars["ampi_heartbeat_s"]):
            return
        self._last_heartbeat = now
        self.device.kv_put(
            f"hb/{self.world_rank}",
            json.dumps({
                "ts": now, "turn": self.turn, "state": self.state.value,
                "tokens": self.budget.ingested, "pressure": round(self.budget.pressure, 3),
                "spend": round(self.budget.currency_spent, 6),
            }),
        )

    def start_heartbeat(self, period_s: float | None = None) -> None:
        """Emit heartbeats from a background thread.

        Without this, a rank only heartbeats inside protocol calls, and a
        rank that is *working* -- which for an agent means a turn lasting
        minutes -- looks exactly like a rank that has died.  Separating
        liveness from progress is only useful if liveness can be observed
        while the rank is busy, which requires a thread.

        Ranks that cannot run one (an agent whose only interface is a shell,
        invoking ``ampi`` once per operation) fall back to inferring liveness
        from activity: their last ``ampi`` invocation is their last
        heartbeat, so their failure timeout must exceed their turn length.
        The runtime reports which mode a rank is in so that a harness can set
        the timeout accordingly rather than guessing.
        """
        import threading

        if getattr(self, "_hb_thread", None) is not None:
            return
        period = period_s or float(self.cvars["ampi_heartbeat_s"])
        self._hb_stop = threading.Event()

        def loop() -> None:
            while not self._hb_stop.wait(period):
                try:
                    self.heartbeat(force=True)
                except Exception:  # pragma: no cover - never kill the rank
                    return

        self._hb_thread = threading.Thread(target=loop, daemon=True,
                                           name=f"ampi-hb-{self.world_rank}")
        self._hb_thread.start()

    def stop_heartbeat(self) -> None:
        stop = getattr(self, "_hb_stop", None)
        if stop is not None:
            stop.set()
        self._hb_thread = None

    def peer_health(self, world_rank: int) -> dict[str, Any] | None:
        raw = self.device.kv_get(f"hb/{world_rank}")
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def detect_failures(self, comm: Communicator) -> set[int]:
        """Return the set of communicator-local ranks that appear dead or stuck."""
        now = time.time()
        cached = self._failure_cache.get(comm.context)
        if cached and now - cached[0] < 1.0:
            return cached[1]
        fail_after = float(self.cvars["ampi_failure_timeout_s"])
        stall_after = float(self.cvars["ampi_stall_timeout_s"])
        failed: set[int] = set()
        for local in range(comm.size):
            wr = comm.world(local)
            if wr == self.world_rank:
                continue
            hb = self.peer_health(wr)
            if hb is None:
                # Never heartbeated.  Only suspect it once the run is old
                # enough that a healthy rank would certainly have checked in;
                # otherwise every run would begin by declaring its own peers
                # dead before they had started.
                if now - self.started_at > fail_after:
                    failed.add(local)
                continue
            state = hb.get("state")
            if state in {s.value for s in DEAD_STATES}:
                failed.add(local)
                continue
            if now - float(hb.get("ts", 0)) > fail_after:
                failed.add(local)
                continue
            last_progress = self.device.kv_get(f"progress/{wr}")
            if last_progress:
                try:
                    p = json.loads(last_progress)
                    if now - float(p.get("ts", now)) > stall_after:
                        failed.add(local)
                except json.JSONDecodeError:
                    pass
        self._failure_cache[comm.context] = (now, failed)
        return failed

    def check_failures(self, comm: Communicator) -> None:
        """Called from inside blocking operations to drive failure detection.

        Also picks up a revocation issued by another rank.  This is the hook
        that makes ``AMPI_Comm_revoke`` able to interrupt a blocked receive:
        the revoker writes a durable record and every blocked peer notices it
        on its next poll, which is what turns an unbreakable wait into a
        recoverable ``AMPI_ERR_REVOKED``.
        """
        self.heartbeat()
        from .ft import is_revoked

        if is_revoked(comm):
            return
        self.detect_collective_mismatch(comm)
        newly = self.detect_failures(comm) - comm.failed
        if not newly:
            return
        comm.failed |= newly
        self.pvars.inc("failures_detected", len(newly))
        for local in newly:
            self.matching.forget_rank(comm.context, local)
            self.profiler.note("peer failure detected", peer=local,
                               world=comm.world(local), comm=comm.name)

    #: How many past collectives to remember per communicator.  Bounded
    #: because the log exists to diagnose a peer that is a few operations out
    #: of step, not to keep a full history.
    COLL_LOG_DEPTH = 64

    def record_collective(self, context: str, cid: int, name: str) -> None:
        log = self.coll_log.setdefault(context, {})
        log[cid] = name
        if len(log) > self.COLL_LOG_DEPTH:
            for stale in sorted(log)[: len(log) - self.COLL_LOG_DEPTH]:
                log.pop(stale, None)

    def detect_collective_mismatch(self, comm: Communicator) -> None:
        """Raise if a peer's collective sequence disagrees with ours.

        The check is cheap and exact.  Every internal envelope carries the
        name and sequence number of the collective that produced it.  If a
        peer sends us ``(#4, "exscan")`` and we executed ``#4`` as
        ``"scatterv"``, then that peer has skipped a collective, its counter
        is behind ours, and from here on it will label every message with a
        tag we are not listening for.  Waiting is futile, and MPI's usual
        remedy -- read the program and see that the calls line up -- does not
        apply when the caller is a language model that decided a step looked
        unnecessary.

        A peer that is *ahead* of us is not an error: its messages sit
        unmatched until we catch up, which is ordinary pipelining and which
        MPI explicitly permits. Only a disagreement about an operation we
        have already performed is diagnostic.
        """
        log = self.coll_log.get(comm.context)
        if not log:
            return
        disagreeing: dict[int, tuple[int, str]] = {}
        for env in self.matching.iter_unexpected(comm.context):
            if env.tag < TAG_UB:
                continue
            cid, name = env.meta.get("i"), env.meta.get("c")
            if cid is None or name is None:
                continue
            mine = log.get(int(cid))
            if mine is None or mine == name:
                continue
            disagreeing[env.source] = (int(cid), name)
        if not disagreeing:
            self._mismatch_since.pop(comm.context, None)
            return

        # Wait a moment before reporting. The first disagreeing message
        # arrives before the others, and raising on it would leave us with a
        # sample of one, which cannot distinguish "I skipped a step" from
        # "one peer skipped a step". A short grace period costs nothing --
        # the job is already wedged -- and buys a majority.
        first = self._mismatch_since.setdefault(comm.context, time.time())
        if time.time() - first < float(self.cvars["ampi_coll_mismatch_grace_s"]):
            return

        # Detection is symmetric: each side sees the other disagreeing, and
        # neither is locally distinguishable from the culprit. A majority
        # settles it. If two or more peers agree with each other and not with
        # us, we are the rank that skipped a step, and we are the one that has
        # to resynchronise -- so the error says so, rather than leaving every
        # participant to blame every other.
        peers = sorted(disagreeing)
        cid, peer_op = disagreeing[peers[0]]
        mine = log.get(cid, "?")
        agreeing_peers = [p for p, (c, n) in disagreeing.items()
                          if c == cid and n == peer_op]
        minority = len(agreeing_peers) >= 2
        who = ("this rank" if minority
               else f"rank {peers[0]} or this rank")
        raise CollectiveMismatchError(
            f"collective #{cid} was issued as {mine!r} here and as "
            f"{peer_op!r} by rank(s) {agreeing_peers}; {who} skipped or added "
            f"a collective, so the tags no longer line up and no further "
            f"collective on this communicator can complete",
            peer=peers[0], peers=agreeing_peers, cid=cid,
            peer_op=peer_op, local_op=mine, local_is_minority=minority,
            comm=comm.name,
        )

    def note_progress(self) -> None:
        """Record that this rank advanced.  Drives stall detection."""
        self.turn += 1
        self.device.kv_put(
            f"progress/{self.world_rank}",
            json.dumps({"ts": time.time(), "turn": self.turn}),
        )
        self.heartbeat(force=True)

    # -- admission control -------------------------------------------------
    def admit(self, tokens: int, datatype: TypeDescriptor, env: Envelope) -> None:
        """Charge an incoming payload against the context budget.

        The interesting branch is the recovery path.  A conventional runtime
        has two options when a message does not fit: fail, or overflow.
        AgentMPI has a third -- digest the payload and admit the smaller
        version -- and it is usually the right one, because the alternative
        is that the agent silently truncates it anyway, with no record.  Doing
        the reduction in the runtime makes the loss explicit, bounded, and
        visible in the trace.
        """
        if not self.cvars["ampi_admission_control"]:
            self.budget.ingested += tokens
            return
        try:
            self.budget.admit(tokens)
            return
        except ContextOverflowError:
            if not self.cvars["ampi_auto_digest"] or not datatype.lossy:
                self.pvars.inc("admission_rejections")
                raise
        headroom = max(self.budget.headroom - 64, 32)
        text = env.inline or (self.device.get_blob(env.blob) if env.blob else "")
        digest = DIGESTS["head_tail"](text, headroom)
        if datatype.base in (Datatype.JSON, Datatype.TOOLCALL):
            # A digested JSON document is no longer a JSON document.  Rather
            # than hand the receiver something that will not parse, wrap the
            # digest in a well-formed envelope that says plainly what was
            # done: the contract survives, and the loss is visible instead of
            # showing up later as a parse error nobody can explain.
            digest = json.dumps(
                {"_ampi_digest": digest, "_ampi_original_tokens": tokens,
                 "_ampi_reason": "receiver context budget"},
                ensure_ascii=False,
            )
        from .tokens import message_tokens

        new_tokens = message_tokens(digest)
        env.inline = digest
        env.blob = None
        env.reduced = True
        saved = tokens - new_tokens
        self.pvars.inc("tokens_digested", max(saved, 0))
        self.profiler.note("payload digested to fit context budget",
                           was=tokens, now=new_tokens, source=env.source)
        self.budget.admit(new_tokens)
        env.tokens = new_tokens

    def spend(self, amount: float) -> None:
        self.budget.spend(amount)

    def types_by_name(self, name: str) -> TypeDescriptor:
        try:
            return self.types.get(name)
        except Exception:
            base = name.split("<")[0].split("/")[0]
            try:
                return lookup(base)
            except Exception:
                return lookup("text")

    # -- teardown ----------------------------------------------------------
    def finalize(self) -> None:
        if self.finalized:
            return
        self.stop_heartbeat()
        self.state = RankState.FINALIZED
        self.save_state()
        self.publish_spec()
        self.heartbeat(force=True)
        self.device.append_journal(
            "lifecycle",
            {"event": "finalize", "rank": self.world_rank, "ts": time.time(),
             "pvars": self.pvars.snapshot(), "budget": self.budget.snapshot(),
             "matching": self.matching.stats},
        )
        self.finalized = True

    def status(self) -> dict[str, Any]:
        return {
            "rank": self.world_rank,
            "size": self.world_size,
            "state": self.state.value,
            "turn": self.turn,
            "uptime_s": round(time.time() - self.started_at, 2),
            "budget": self.budget.snapshot(),
            "matching": dict(self.matching.stats),
            "pvars": self.pvars.snapshot(),
            "device": self.device.name,
            "spec": {
                "role": self.spec.role, "model": self.spec.model,
                "provider": self.spec.provider,
            },
        }


# --------------------------------------------------------------------------
# Module-level API (AMPI_Init / AMPI_Finalize / AMPI_Abort)
# --------------------------------------------------------------------------

def init(
    root: str | None = None,
    rank: int | None = None,
    size: int | None = None,
    *,
    device: str | None = None,
    spec: RankSpec | None = None,
    cvars: dict[str, Any] | None = None,
    thread_level: ThreadLevel = ThreadLevel.SINGLE,
    profiler: Profiler | None = None,
) -> Runtime:
    """``AMPI_Init_thread``.

    Identity resolution order, mirroring how ``mpiexec`` and PMI cooperate:
    explicit argument, then environment (``AMPI_RANK``/``AMPI_SIZE``/
    ``AMPI_ROOT``), then the run manifest.  Environment discovery is what
    lets a *shell-capable agent with no library bindings* be a rank: the
    launcher exports three variables and the agent runs ``ampi`` commands.
    """
    global _RUNTIME
    if _RUNTIME is not None and not _RUNTIME.finalized:
        return _RUNTIME

    root = root or os.environ.get("AMPI_ROOT")
    kind = device or os.environ.get("AMPI_DEVICE") or ("journal" if root else "inproc")
    rank = rank if rank is not None else int(os.environ.get("AMPI_RANK", "0"))

    manifest: RunManifest | None = None
    if root and os.path.exists(os.path.join(root, RUN_MANIFEST)):
        manifest = RunManifest.load(os.path.join(root, RUN_MANIFEST))
    if size is None:
        env_size = os.environ.get("AMPI_SIZE")
        size = int(env_size) if env_size else (manifest.size if manifest else 1)

    dev = open_device(kind, root, owner=f"rank{rank}")

    if spec is None and manifest is not None:
        for entry in manifest.ranks:
            if int(entry.get("rank", -1)) == rank:
                known = RankSpec.__dataclass_fields__.keys()  # type: ignore[attr-defined]
                kwargs = {k: v for k, v in entry.items() if k in known}
                if "tools" in kwargs:
                    kwargs["tools"] = tuple(kwargs["tools"])
                spec = RankSpec(**kwargs)
                break
    merged_cvars = {**(manifest.cvars if manifest else {}), **(cvars or {})}

    rt = Runtime(dev, rank, size, spec=spec, root=root, cvars=merged_cvars,
                 thread_level=thread_level, profiler=profiler)
    rt.publish_spec()
    rt.heartbeat(force=True)
    rt.device.append_journal(
        "lifecycle",
        {"event": "init", "rank": rank, "size": size, "ts": time.time(),
         "host": socket.gethostname(), "pid": os.getpid(),
         "python": platform.python_version(), "device": kind},
    )
    rt._refresh_rank_table()
    rt.world.group = rt.rank_table.group(size)
    _RUNTIME = rt
    return rt


def initialized() -> bool:
    return _RUNTIME is not None and not _RUNTIME.finalized


def current() -> Runtime:
    if _RUNTIME is None:
        raise CommError("AMPI_Init has not been called")
    return _RUNTIME


def comm_world() -> Communicator:
    return current().world


def finalize() -> None:
    """``AMPI_Finalize``."""
    global _RUNTIME
    if _RUNTIME is None:
        return
    _RUNTIME.finalize()
    _RUNTIME = None


def abort(comm_name: str, code: int, reason: str = "") -> None:
    """``AMPI_Abort`` -- terminate the job."""
    rt = _RUNTIME
    if rt is not None:
        rt.device.append_journal(
            "lifecycle",
            {"event": "abort", "rank": rt.world_rank, "code": code,
             "reason": reason, "comm": comm_name, "ts": time.time()},
        )
        rt.device.kv_put("abort", json.dumps({"rank": rt.world_rank, "code": code,
                                              "reason": reason, "ts": time.time()}))
    raise SystemExit(code)


def wtime() -> float:
    """``AMPI_Wtime``."""
    return time.time()


def get_processor_name() -> str:
    return socket.gethostname()
