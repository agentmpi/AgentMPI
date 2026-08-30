"""Fault tolerance: revoke, shrink, agree, replace, and checkpoints.

MPI's position on failure, from 1994 until today, is that after a process
dies the state of MPI is undefined.  That was a defensible engineering
choice: on a machine where the mean time between failures is measured in
days and jobs are checkpointed anyway, making every call fault-aware would
have cost performance everyone would pay and few would use.  The ULFM
proposal (Bland, Bosilca, Bouteiller, Herault and Dongarra) added the
minimum viable set of primitives on top -- *revoke* a communicator, *shrink*
it to the survivors, *agree* on a value despite failures -- under a design
principle we adopt verbatim: **the library should not recover for you, it
should make recovery writable**.

For agents the calculus is reversed.  Failure is not rare, it is the
steady state: an agent hits a rate limit, loops on a broken tool, returns
JSON with a trailing comma, or simply stops. So AgentMPI makes the ULFM
primitives non-optional, adds the failure modes MPI does not have, and adds
one primitive ULFM deliberately omits.

**The failure lattice.**  MPI ranks are alive or dead.  Agent ranks are
alive, dead, *stalled* (heartbeating, not progressing), *drifted* (producing
contract-violating output), or *bankrupt* (out of tokens or money).  Each
needs a different response, and conflating them produces the two classic
failures of agent harnesses: killing a slow-but-working agent, and waiting
forever for a fast-but-broken one.

**Rank replacement.**  ULFM shrinks a communicator, changing every rank's
index -- correct, and painful, because the application's data distribution
was written against the old indices.  FT-MPI's earlier ``REBUILD`` mode kept
the indices and respawned the dead.  With agents, respawning is cheap: an
agent is a prompt and a checkpoint, not a process image with 40 GB of state.
So :func:`comm_replace` is the recommended recovery path and shrink is the
fallback, which is the opposite of the usual HPC preference and follows
directly from the cost of a restart being near zero.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .constants import DEAD_STATES, InternalTag, RankState
from .errors import (
    AmpiError,
    ProcFailedError,
    RevokedError,
    TimeoutError_,
)
from .group import Group, RankSpec
from .trace import Event


# --------------------------------------------------------------------------
# ULFM core
# --------------------------------------------------------------------------

def comm_revoke(comm) -> None:
    """``AMPI_Comm_revoke``.

    Invalidate the communicator everywhere.  Every operation on it, present
    or future, at every rank, terminates with ``AMPI_ERR_REVOKED`` rather
    than blocking.  This is the primitive that converts a deadlock into an
    error, and it is the one thing an application genuinely cannot build for
    itself: a rank blocked in a receive from a peer that will never send has
    no way to notice on its own, so *some* out-of-band mechanism has to
    interrupt it.

    Propagation must be reliable even though the propagator may itself die
    mid-broadcast; ULFM implementations use a reliable broadcast for this.
    Our devices give us a simpler route: the revocation is a durable record
    in the shared key-value store, so it survives the revoker.
    """
    rt = comm.runtime
    rt.device.kv_put(
        f"revoked/{comm.context}",
        json.dumps({"by": rt.world_rank, "ts": time.time(), "epoch": comm.epoch}),
    )
    comm.revoked = True
    rt.matching.revoke(comm.context)
    rt.pvars.inc("revokes")
    rt.profiler.emit(
        Event(kind="state", ts=time.time(), rank=rt.world_rank, op="revoke",
              context=comm.context, state="revoked")
    )


def is_revoked(comm) -> bool:
    if comm.revoked:
        return True
    raw = comm.runtime.device.kv_get(f"revoked/{comm.context}")
    if raw:
        comm.revoked = True
        comm.runtime.matching.revoke(comm.context)
        return True
    return False


def comm_agree(
    comm, value: Any, *, op: str = "ampi_land", timeout: float | None = 120.0
) -> Any:
    """``AMPI_Comm_agree`` -- fault-tolerant agreement.

    Every surviving rank contributes a value; all of them return the same
    reduced result, *even though ranks are failing during the operation*.
    This is a consensus problem, and the FLP result says it is unsolvable in
    a purely asynchronous system with even one crash failure -- which is why
    agreement here, as in ULFM, relies on a failure detector, and why the
    detector's timeouts are the parameter that decides whether the operation
    is fast or merely eventually correct.

    Agreement is what makes collective recovery possible at all: without it,
    two survivors can disagree about which ranks are dead, shrink to
    different groups, and produce two divergent worlds.  We use an
    early-returning two-phase protocol over the durable key-value store: each
    rank writes its vote, the lowest-numbered survivor decides, and the
    decision is durable so that the decider's own death after writing it does
    not lose it.
    """
    from .collectives import _next_coll_id

    rt = comm.runtime
    t0 = time.time()
    # The round identifier must be *agreed without agreeing*, which sounds
    # circular but is not: agreement is a collective, and collectives are
    # required to be issued in the same order on every rank, so the
    # communicator's collective counter is already a replicated value.
    # Deriving the round from a shared counter in the key-value store instead
    # would be a read-then-write race, and two ranks landing on different
    # rounds would each wait forever for votes the other posted elsewhere.
    round_id = _next_coll_id(comm)
    base = f"agree/{comm.context}/{round_id}"
    rt.device.kv_put(f"{base}/vote/{comm.rank}", json.dumps(
        {"value": value, "ts": time.time()}))

    from .ops import lookup_op

    operation = lookup_op(op)
    deadline = None if timeout is None else t0 + timeout
    decision_key = f"{base}/decision"

    while True:
        raw = rt.device.kv_get(decision_key)
        if raw is not None:
            decided = json.loads(raw)
            rt.profiler.emit(
                Event(kind="coll", ts=time.time(), rank=rt.world_rank, op="agree",
                      context=comm.context, dur=time.time() - t0,
                      detail={"round": round_id, "decider": decided.get("by"),
                              "survivors": decided.get("survivors")})
            )
            comm.failed |= set(decided.get("failed", []))
            return decided["value"]

        rt.check_failures(comm)
        survivors = [r for r in range(comm.size) if r not in comm.failed]
        decider = min(survivors) if survivors else comm.rank
        votes: dict[int, Any] = {}
        for r in survivors:
            raw_vote = rt.device.kv_get(f"{base}/vote/{r}")
            if raw_vote:
                votes[r] = json.loads(raw_vote)["value"]

        if comm.rank == decider and len(votes) == len(survivors):
            reduced = operation.apply([votes[r] for r in sorted(votes)])
            # Compare-and-swap, not put: two ranks can briefly disagree about
            # who is dead and therefore about who decides, and a second
            # decision overwriting the first would let ranks that already read
            # the first one diverge.  First writer wins, permanently.
            rt.device.kv_cas(decision_key, None, json.dumps({
                "value": reduced, "by": comm.rank, "ts": time.time(),
                "survivors": survivors, "failed": sorted(comm.failed),
                "round": round_id,
            }))
            continue

        if deadline is not None and time.time() > deadline:
            raise TimeoutError_("agreement did not converge", round=round_id,
                                votes=len(votes), survivors=len(survivors))
        time.sleep(0.1)


def comm_shrink(comm, *, timeout: float | None = 180.0):
    """``AMPI_Comm_shrink`` -- a new communicator over the survivors.

    Necessarily collective and necessarily an agreement: if two ranks
    disagreed about the survivor set they would build different
    communicators and every subsequent collective would deadlock.  We
    therefore agree on the survivor set first and derive the group from the
    agreed value, exactly as ULFM specifies.

    The new communicator gets a fresh epoch.  That is what lets the matching
    engine discard in-flight messages from the pre-shrink world, which would
    otherwise be delivered to a rank that now has a different index and mean
    something completely different.
    """
    rt = comm.runtime
    rt.check_failures(comm)
    alive = sorted(r for r in range(comm.size) if r not in comm.failed)
    agreed = _agree_on_survivors(comm, alive, timeout)
    members = tuple(comm.world(r) for r in agreed)
    specs = tuple(s for s in comm.group.specs if s.rank in members) if comm.group.specs else ()
    new_epoch = comm.epoch + 1
    rt.device.kv_put(f"epoch/{comm.context}", str(new_epoch))
    rt.matching.bump_epoch(comm.context, new_epoch)
    from .comm import Communicator

    shrunk = Communicator(
        rt, f"{comm.context}/shrink{new_epoch}", Group(members, specs),
        name=f"{comm.name}.shrunk", parent=comm, epoch=new_epoch,
    )
    rt.pvars.inc("shrinks")
    rt.profiler.emit(
        Event(kind="state", ts=time.time(), rank=rt.world_rank, op="shrink",
              context=comm.context, state="shrunk",
              detail={"from": comm.size, "to": shrunk.size, "epoch": new_epoch})
    )
    return rt._register_comm(shrunk)


def _agree_on_survivors(comm, alive: Sequence[int], timeout: float | None) -> list[int]:
    """Agree on the survivor set.

    We deliberately intersect rather than union the proposals.  A rank that
    one survivor believes is alive and another believes is dead must be
    treated as dead, because including a rank that some participants will not
    talk to reproduces the split-brain the shrink exists to resolve.
    """
    from .ops import op_create

    intersect = op_create(
        lambda a, b: sorted(set(a or []) & set(b or [])),
        name="ampi_intersect", commute=True, idempotent=True, associative=True,
        output_tokens=256,
    )
    result = comm_agree(comm, list(alive), op=intersect, timeout=timeout)  # type: ignore[arg-type]
    return sorted(result or [comm.rank])


def comm_failure_ack(comm) -> None:
    """``AMPI_Comm_failure_ack``.

    Acknowledge the currently known failures, which re-enables wildcard
    receives on the communicator.  The reason wildcards must be disabled
    after an unacknowledged failure is subtle and worth stating: a receive
    from ``ANY_SOURCE`` could have been satisfied by the dead rank, so the
    runtime cannot know whether it should keep waiting.  Acknowledging is the
    application saying "I know, and I am no longer expecting anything from
    them".
    """
    comm.acknowledged = set(comm.failed)


def comm_failure_get_acked(comm) -> Group:
    """``AMPI_Comm_failure_get_acked`` -- the group of acknowledged failures."""
    members = tuple(comm.world(r) for r in sorted(comm.acknowledged))
    return Group(members)


# --------------------------------------------------------------------------
# Rank replacement (FT-MPI REBUILD, adapted)
# --------------------------------------------------------------------------

def comm_replace(
    comm,
    *,
    spawn: Callable[[int, RankSpec], None] | None = None,
    timeout: float | None = 600.0,
    restore: bool = True,
):
    """``AMPI_Comm_replace`` -- respawn the failed ranks, keeping their indices.

    This has no ULFM counterpart, and it is the primitive we reach for most.
    An agent rank's entire state is its role, its checkpoint, and the
    messages it has acknowledged; all three are durable, so a replacement can
    be indistinguishable from the original to every peer.  Because the
    indices are preserved, the harness's data distribution -- "rank *i* owns
    chapter *i*" -- survives the failure untouched, which is precisely the
    thing a shrink destroys.

    ``spawn`` is supplied by the executor backend; the protocol does not know
    how to create an agent, only when one is needed.  That separation is the
    same one MPI draws between the standard and the process manager.
    """
    rt = comm.runtime
    rt.check_failures(comm)
    dead = sorted(comm.failed)
    if not dead:
        return comm
    new_epoch = comm.epoch + 1
    rt.device.kv_put(f"epoch/{comm.context}", str(new_epoch))
    for local in dead:
        world = comm.world(local)
        spec = comm.group.spec(local)
        rt.device.kv_put(f"state/{world}", json.dumps(
            {"state": RankState.UNBORN.value, "ts": time.time(),
             "replaced_from_epoch": comm.epoch}))
        rt.device.kv_delete(f"hb/{world}")
        rt.device.append_journal("lifecycle", {
            "event": "replace_requested", "rank": world, "local": local,
            "epoch": new_epoch, "restore": restore, "ts": time.time(),
        })
        if spawn is not None:
            spawn(world, spec)

    deadline = None if timeout is None else time.time() + timeout
    pending = {comm.world(r) for r in dead}
    while pending:
        for world in sorted(pending):
            hb = rt.peer_health(world)
            if hb and hb.get("state") in (RankState.INIT.value, RankState.ACTIVE.value):
                pending.discard(world)
        if not pending:
            break
        if deadline is not None and time.time() > deadline:
            raise TimeoutError_("replacement ranks did not rejoin",
                                pending=sorted(pending))
        time.sleep(1.0)

    comm.failed.clear()
    comm.acknowledged.clear()
    comm.epoch = new_epoch
    rt.matching.bump_epoch(comm.context, new_epoch)
    rt.profiler.emit(
        Event(kind="state", ts=time.time(), rank=rt.world_rank, op="replace",
              context=comm.context, state="replaced",
              detail={"ranks": dead, "epoch": new_epoch})
    )
    return comm


# --------------------------------------------------------------------------
# Checkpoints
# --------------------------------------------------------------------------

@dataclass
class Checkpoint:
    """A rank's durable state.

    The contents are chosen so that a replacement is *behaviourally*
    equivalent, not bitwise identical -- bitwise is unattainable with a
    stochastic executor.  What must be preserved is everything a peer could
    already have observed: the messages this rank acknowledged (so it does
    not consume them twice), the messages it sent (so it does not re-send
    them with different content), and the artifacts it published.  Its
    internal chain of thought is explicitly *not* preserved, because it is
    both enormous and unnecessary: the replacement re-derives it from the
    same inputs.
    """

    rank: int
    turn: int
    epoch: int
    ts: float
    role: str = ""
    acknowledged: list[str] = field(default_factory=list)
    sent: list[dict[str, Any]] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Checkpoint":
        return cls(**json.loads(text))


def checkpoint(comm, state: dict[str, Any] | None = None,
               artifacts: dict[str, str] | None = None) -> Checkpoint:
    """``AMPI_Checkpoint`` -- persist this rank's recoverable state.

    Checkpoints are *local and uncoordinated*.  Coordinated checkpointing
    (Chandy-Lamport) exists because uncoordinated checkpointing plus message
    replay can cascade into the domino effect, rolling a whole job back to
    its start.  That argument depends on replay being the only way to
    reconstruct a lost message.  Here it is not: every message ever sent is
    durable in the journal and content-addressed, so a restarted rank reads
    what it missed instead of forcing its peers to re-send it.  Removing the
    need for coordination removes the domino effect with it, and is the
    single biggest simplification the durable-journal design buys.
    """
    rt = comm.runtime
    cp = Checkpoint(
        rank=rt.world_rank,
        turn=rt.turn,
        epoch=comm.epoch,
        ts=time.time(),
        role=rt.spec.role,
        acknowledged=sorted(rt.matching._seen_idem),
        artifacts=dict(artifacts or {}),
        state=dict(state or {}),
        budget=rt.budget.snapshot(),
    )
    rt.device.kv_put(f"ckpt/{rt.world_rank}", cp.to_json())
    rt.device.append_journal("lifecycle", {
        "event": "checkpoint", "rank": rt.world_rank, "turn": rt.turn, "ts": cp.ts,
    })
    return cp


def restore(comm) -> Checkpoint | None:
    """``AMPI_Restore`` -- reload this rank's checkpoint after a restart."""
    rt = comm.runtime
    raw = rt.device.kv_get(f"ckpt/{rt.world_rank}")
    if raw is None:
        return None
    cp = Checkpoint.from_json(raw)
    rt.matching._seen_idem |= set(cp.acknowledged)
    rt.turn = cp.turn
    rt.device.append_journal("lifecycle", {
        "event": "restored", "rank": rt.world_rank, "turn": cp.turn, "ts": time.time(),
    })
    return cp


# --------------------------------------------------------------------------
# Supervision (Erlang/OTP, adapted)
# --------------------------------------------------------------------------

@dataclass
class RestartPolicy:
    """An OTP-style supervision policy.

    Erlang's "let it crash" is the correct default for agents, and for the
    same reason Armstrong gave: defensive code for every failure mode is
    larger, buggier, and less complete than a clean restart from a known
    state.  The strategies below are Erlang's, with the one addition that
    matters here -- a *budget* -- because unlike an Erlang process, an agent
    that crashes and restarts has spent real money, so an unbounded restart
    loop is not merely useless but expensive.
    """

    strategy: str = "one_for_one"      # one_for_one | one_for_all | rest_for_one
    max_restarts: int = 3
    within_s: float = 3600.0
    backoff_s: float = 5.0
    backoff_factor: float = 2.0
    escalate: str = "shrink"           # shrink | abort | continue
    max_currency: float | None = None

    def next_delay(self, attempt: int) -> float:
        return self.backoff_s * (self.backoff_factor ** max(attempt - 1, 0))


@dataclass
class Supervisor:
    """Tracks restarts and decides whether to keep trying."""

    policy: RestartPolicy = field(default_factory=RestartPolicy)
    history: dict[int, list[float]] = field(default_factory=dict)
    spent: float = 0.0

    def record(self, rank: int) -> None:
        self.history.setdefault(rank, []).append(time.time())

    def attempts(self, rank: int) -> int:
        now = time.time()
        events = [t for t in self.history.get(rank, []) if now - t <= self.policy.within_s]
        self.history[rank] = events
        return len(events)

    def should_restart(self, rank: int) -> bool:
        if self.policy.max_currency is not None and self.spent >= self.policy.max_currency:
            return False
        return self.attempts(rank) < self.policy.max_restarts

    def affected(self, rank: int, all_ranks: Sequence[int]) -> list[int]:
        if self.policy.strategy == "one_for_all":
            return list(all_ranks)
        if self.policy.strategy == "rest_for_one":
            return [r for r in all_ranks if r >= rank]
        return [rank]


# --------------------------------------------------------------------------
# Retry with contract validation
# --------------------------------------------------------------------------

def with_retry(
    fn: Callable[[], Any],
    *,
    validate: Callable[[Any], Sequence[str]] | None = None,
    retries: int = 2,
    backoff_s: float = 1.0,
    on_retry: Callable[[int, Sequence[str] | AmpiError], None] | None = None,
) -> Any:
    """Run ``fn``, retrying on failure *or* on contract violation.

    The second trigger is the agent-specific one.  A conventional retry loop
    handles transport failures; here the far more common failure is a
    perfectly delivered reply that does not satisfy its contract, and a
    protocol that cannot retry on *content* leaves the harness to reimplement
    validation-and-retry at every call site.  Because retries are expensive,
    the violation list is passed back so the caller can include it in the
    next prompt -- a repair loop rather than a blind retry.
    """
    last: Sequence[str] | AmpiError = ()
    for attempt in range(retries + 1):
        try:
            result = fn()
            problems = tuple(validate(result)) if validate else ()
            if not problems:
                return result
            last = problems
        except AmpiError as exc:
            last = exc
        if attempt < retries:
            if on_retry:
                on_retry(attempt + 1, last)
            time.sleep(backoff_s * (2 ** attempt))
    if isinstance(last, AmpiError):
        raise last
    from .errors import ContractError

    raise ContractError("operation failed contract validation after retries",
                        violations=tuple(last), retries=retries)
