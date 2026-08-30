"""Fault tolerance: failure detection, ULFM-style mitigation, and supervision.

The argument
------------
MPI's most-cited deficiency is that it has essentially no fault tolerance.  The
standard's position is that after an error the state of MPI is undefined, the
default error handler aborts the job, and a communicator containing a dead
process is unusable.  Twenty-five years of work — FT-MPI, checkpoint/restart,
and finally User Level Failure Mitigation — has produced a small, well-chosen
set of primitives that AgentMPI adopts wholesale, because they turn out to be
exactly what an agent harness needs:

``revoke``
    Make a communicator permanently unusable for *everyone*, so that ranks
    already blocked inside a collective are released with an error instead of
    hanging.  This is the primitive whose necessity is least obvious and most
    important: without it, one rank noticing a failure cannot rescue the others,
    because they are all blocked waiting for the dead peer.
``shrink``
    Build a new communicator from the survivors, renumbered contiguously.
``agree``
    Fault-tolerant agreement on a value over a communicator with failures — the
    only way to make a *consistent* decision when the set of participants is
    itself in doubt.

What MPI does not supply, and Erlang/OTP does, is a *recovery policy*.  OTP's
contribution is the supervision tree: a supervisor that owns child processes,
restarts them according to a declared strategy (``one_for_one``,
``one_for_all``, ``rest_for_one``), and escalates when restarts exceed an
intensity threshold.  AgentMPI's position is that the communication algebra
should come from MPI and the recovery discipline from OTP, because the two are
orthogonal and each field solved the problem the other ignored.

The failure model
-----------------
MPI's fault-tolerance work assumes fail-stop.  Agent ranks are not fail-stop,
and getting this wrong is why so much agent-reliability engineering consists of
retry loops that do not help.  AgentMPI's taxonomy is in
:class:`~agentmpi.constants.FailureClass`; the operational consequence is:

===============  ====================================  ==========================
class            detector                              mitigation
===============  ====================================  ==========================
FAIL_STOP        lease expiry                          re-incarnate, or shrink
FAIL_SLOW        deadline on the operation             hedge (duplicate the work)
FAIL_NOISY       contract check                        retry with the diagnosis
FAIL_PLAUSIBLE   *independent verification only*       replicate and compare,
                                                       or check with an oracle
FAIL_GREEDY      context accounting                    rendezvous + views
FAIL_ADVERSARIAL out of scope                          --
===============  ====================================  ==========================

The row that matters is FAIL_PLAUSIBLE.  A rank that returns a well-formed,
confident, wrong answer is undetectable by any amount of timeout tuning, retry
logic, or schema validation, because none of those look at whether the answer is
*right*.  It is the exact analogue of silent data corruption, and the HPC
literature's answer to silent data corruption is not checkpoint/restart — which
faithfully preserves the corruption — but *algorithm-based fault tolerance*:
carry redundant information that lets the computation check itself.  AgentMPI
therefore makes verification a first-class protocol concept
(:class:`~agentmpi.schema.Validator`, :func:`replicate_and_compare`) and treats
retry as the mitigation for the *easy* classes only.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .constants import FailureClass, RankState, RestartPolicy
from .errors import AmpiProcFailed, AmpiRevoked, AmpiTimeout
from .fabric import Fabric
from .schema import Validator

if TYPE_CHECKING:  # pragma: no cover - avoids a cycle with comm
    from .comm import Communicator


# ============================================================================
# Detection
# ============================================================================


@dataclass
class HealthReport:
    """Snapshot of one rank's health, as the detector sees it."""

    rank: int
    state: str
    alive: bool
    lease_age: float
    context_occupancy: float
    n_calls: int
    suspected: FailureClass | None = None
    detail: str = ""


def health(fabric: Fabric, *, now: float | None = None) -> list[HealthReport]:
    """Evaluate every rank against the lease-based failure detector.

    This is an *eventually perfect* detector in the Chandra–Toueg sense: it may
    suspect a live-but-slow rank, and it will eventually stop suspecting one
    that recovers.  It is not perfect and cannot be, since a rank that is merely
    slow is indistinguishable from one that is dead.  FLP tells us we cannot get
    consensus with an unreliable detector in an asynchronous system, and the
    escape hatch used here is the same one HPC uses: assume a partially
    synchronous system with a known lease bound, and accept that a rank whose
    lease expires is *declared* failed whether or not it is.  ``revoke`` then
    makes the declaration self-fulfilling, so the population's view stays
    consistent even when the detector was wrong.
    """
    now = now or time.time()
    out: list[HealthReport] = []
    for row in fabric.query("SELECT * FROM ranks ORDER BY rank"):
        lease_age = now - float(row["lease_expires"])
        alive = lease_age <= 0 and row["state"] in (RankState.RUNNING.value, RankState.IDLE.value)
        occupancy = (int(row["context_used"]) / int(row["context_budget"])) if int(row["context_budget"]) else 0.0
        suspected: FailureClass | None = None
        detail = ""
        if row["state"] == RankState.FINALIZED.value:
            suspected = None
        elif lease_age > 0:
            suspected = FailureClass.FAIL_STOP
            detail = f"lease expired {lease_age:.0f}s ago"
        elif occupancy > 0.95:
            suspected = FailureClass.FAIL_GREEDY
            detail = f"context occupancy {occupancy:.0%}"
        out.append(
            HealthReport(
                rank=int(row["rank"]),
                state=row["state"],
                alive=alive,
                lease_age=lease_age,
                context_occupancy=occupancy,
                n_calls=int(row["n_calls"]),
                suspected=suspected,
                detail=detail,
            )
        )
    return out


def straggler_threshold(latencies: Sequence[float], *, k: float = 2.0) -> float:
    """Deadline for declaring FAIL_SLOW, from observed latencies.

    Dean and Barroso's observation about tail latency applies with far more
    force to agent ranks than to servers: the spread between the median and the
    99th percentile of an agent invocation is routinely an order of magnitude,
    because it is driven by output length and retry behaviour rather than by
    queueing.  A fixed timeout is therefore either useless or harmful, and the
    threshold has to be derived from the run's own distribution.  ``k`` times the
    median-plus-IQR is a robust choice that does not assume normality.
    """
    if not latencies:
        return float("inf")
    med = statistics.median(latencies)
    if len(latencies) < 4:
        return med * (1 + k)
    q = statistics.quantiles(latencies, n=4)
    iqr = max(1e-9, q[2] - q[0])
    return med + k * iqr


def declare_failed(
    comm: Communicator,
    crank: int,
    *,
    kind: str | FailureClass = FailureClass.FAIL_STOP,
    detail: str = "",
) -> None:
    """Record that ``crank`` has failed, in this communicator's view."""
    kind_s = kind.value if isinstance(kind, FailureClass) else str(kind)
    wrank = comm.wrank(crank)
    with comm.fabric.write() as cur:
        cur.execute(
            "INSERT INTO failures(ctx, rank, kind, detail, detected_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(ctx, rank, kind) DO NOTHING",
            (comm.ctx, crank, kind_s, detail, time.time()),
        )
        cur.execute(
            "UPDATE ranks SET state=? WHERE rank=? AND state NOT IN (?, ?)",
            (RankState.FAILED.value, wrank, RankState.FINALIZED.value, RankState.EXCLUDED.value),
        )
        comm.fabric.emit(
            "ft.declare_failed", rank=comm.rt.wrank, ctx=comm.ctx, cur=cur, failed=crank, wrank=wrank, kind=kind_s, detail=detail
        )


def get_failed(comm: Communicator) -> tuple[int, ...]:
    rows = comm.fabric.query("SELECT DISTINCT rank FROM failures WHERE ctx=? ORDER BY rank", (comm.ctx,))
    return tuple(int(r["rank"]) for r in rows)


def failure_ack(comm: Communicator) -> tuple[int, ...]:
    """``MPIX_Comm_failure_ack``: acknowledge current failures.

    Acknowledgement is what makes ``ANY_SOURCE`` receives usable again after a
    failure.  Before acknowledging, a wildcard receive must fail, because the
    unacknowledged dead rank might have been about to send the very message the
    receive would match; after acknowledging, the harness has asserted that it
    understands the rank is gone and wildcard receives may proceed over the
    survivors.  The distinction looks pedantic until a harness silently loses a
    work item to it.
    """
    failed = get_failed(comm)
    with comm.fabric.write() as cur:
        cur.execute("UPDATE failures SET acked=1 WHERE ctx=?", (comm.ctx,))
        comm.fabric.emit("ft.failure_ack", rank=comm.rt.wrank, ctx=comm.ctx, cur=cur, acked=list(failed))
    return failed


# ============================================================================
# Mitigation: revoke / shrink / agree
# ============================================================================


def revoke(comm: Communicator) -> None:
    """``MPIX_Comm_revoke``: poison the communicator for every member.

    Any subsequent operation on it raises :class:`~agentmpi.errors.AmpiRevoked`,
    including operations already blocked inside a collective — which is the
    point.  One rank discovering a failure can therefore free the whole
    population from a collective that can never complete, without needing to
    reach agreement first (which it could not, since the group is broken).
    Revocation is deliberately irreversible: a communicator that could be
    un-revoked would let two ranks disagree about whether it is usable.
    """
    with comm.fabric.write() as cur:
        cur.execute("UPDATE comms SET revoked=1 WHERE ctx=?", (comm.ctx,))
        comm.fabric.emit("ft.revoke", rank=comm.rt.wrank, ctx=comm.ctx, cur=cur)
    comm._revoked = True


def is_revoked(comm: Communicator) -> bool:
    row = comm.fabric.query_one("SELECT revoked FROM comms WHERE ctx=?", (comm.ctx,))
    return bool(row and int(row["revoked"]))


def shrink(comm: Communicator, *, name: str | None = None, timeout: float = 300.0) -> Communicator:
    """``MPIX_Comm_shrink``: a new communicator over the survivors.

    Every caller must agree on the survivor set or they would build different
    communicators, so shrink is preceded by an agreement step on the failure
    list.  The result is renumbered contiguously, which is what makes
    subsequent collectives cheap again, and is also the trap: the harness's own
    mapping from rank to work must be re-derived, since rank 5's work is now
    rank 4's problem.  AgentMPI keeps world ranks stable inside the fabric so a
    harness can always recover the original identity through
    :attr:`~agentmpi.comm.Communicator.members`.
    """
    from .comm import Communicator as _Comm  # noqa: PLC0415 - genuine cycle: comm imports algorithms imports ft

    survivors_w = _agree_survivors(comm, timeout=timeout)
    cname = name or f"{comm.name}.shrunk{comm.generation + 1}"
    leader = survivors_w[0]
    key = f"shrink:{comm.ctx}:{comm.generation}:{','.join(map(str, survivors_w))}"
    if comm.rt.wrank == leader:
        row = comm.fabric.query_one("SELECT value FROM meta WHERE key=?", (key,))
        if row is None:
            ctx = comm._new_ctx(survivors_w, name=cname)
            with comm.fabric.write() as cur:
                cur.execute("UPDATE comms SET generation=? WHERE ctx=?", (comm.generation + 1, ctx))
            comm.fabric.set_meta(key, str(ctx))
        else:
            ctx = int(row["value"])
    else:
        deadline = time.time() + timeout
        ctx = -1
        while time.time() < deadline:
            row = comm.fabric.query_one("SELECT value FROM meta WHERE key=?", (key,))
            if row is not None:
                ctx = int(row["value"])
                break
            time.sleep(0.05)
        if ctx < 0:
            raise AmpiTimeout("shrink leader never published the survivor communicator", survivors=survivors_w)
    comm.fabric.emit("ft.shrink", rank=comm.rt.wrank, ctx=comm.ctx, new_ctx=ctx, survivors=list(survivors_w))
    return _Comm(comm.fabric, ctx, comm.rt, name=cname, generation=comm.generation + 1)


def shrink_in_place(comm: Communicator, absent: Sequence[int]) -> None:
    """Mark ranks inactive and renumber the *same* communicator.

    A departure from MPI, which never mutates a communicator.  It exists because
    an agent harness's rank-to-work mapping is usually expensive to recompute
    (rank 7 owns the parser module, and that fact is baked into prompts and
    artifacts), so renumbering is often worse than tolerating a sparse group.
    In-place shrink keeps ``members`` stable and only excludes the dead from
    future collectives, at the cost of leaving holes in the rank space — the
    ``BLANK`` mode of FT-MPI rather than its ``SHRINK`` mode.
    """
    wranks = [comm.wrank(a) for a in absent]
    with comm.fabric.write() as cur:
        for w in wranks:
            cur.execute("UPDATE comm_members SET state='failed' WHERE ctx=? AND wrank=?", (comm.ctx, w))
            cur.execute(
                "UPDATE ranks SET state=? WHERE rank=? AND state!=?",
                (RankState.EXCLUDED.value, w, RankState.FINALIZED.value),
            )
        cur.execute("UPDATE comms SET generation = generation + 1 WHERE ctx=?", (comm.ctx,))
        comm.fabric.emit("ft.shrink_in_place", rank=comm.rt.wrank, ctx=comm.ctx, cur=cur, excluded=list(absent))
    comm.refresh()


def _agree_survivors(comm: Communicator, *, timeout: float) -> list[int]:
    known = set(get_failed(comm))
    # Fold in anything the detector has noticed but nobody has declared yet, so
    # that shrink does not immediately produce a communicator with a dead member.
    for rep in health(comm.fabric):
        if rep.suspected is FailureClass.FAIL_STOP:
            cr = comm.crank(rep.rank)
            if cr >= 0:
                known.add(cr)
    survivors = [comm.wrank(c) for c in range(comm.size) if c not in known]
    if not survivors:
        raise AmpiProcFailed("no survivors", failed=tuple(sorted(known)), ctx=comm.ctx)
    return survivors


def agree(comm: Communicator, value: bool, *, timeout: float = 300.0) -> bool:
    """``MPIX_Comm_agree``: fault-tolerant agreement on a boolean.

    Returns the AND over the contributions of all *live* ranks, and does so
    consistently: either every surviving caller returns the same value, or all
    of them raise.  This is the primitive that makes "did the integration step
    succeed?" answerable when some builders died mid-step, and it is
    strictly stronger than an allreduce over LAND, which would hang.

    Implemented as a two-phase vote through the fabric.  The fabric is a shared
    fate-sharing point, so this is not a solution to consensus in an
    asynchronous system — it is the standard engineering answer of routing
    agreement through a reliable service, and the spec states that requirement
    explicitly rather than pretending to have circumvented FLP.
    """
    epoch = comm._next_epoch("agree")
    cid = comm._record_collective("agree", epoch, "fabric-vote", None, {"value": bool(value)})
    with comm.fabric.write() as cur:
        cur.execute(
            "INSERT INTO coll_parts(cid, crank, digest, tokens, arrived_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(cid, crank) DO UPDATE SET digest=excluded.digest",
            (cid, comm.rank, "1" if value else "0", 0, time.time()),
        )
    deadline = time.time() + timeout
    poll = 0.02
    while True:
        rows = comm.fabric.query("SELECT crank, digest FROM coll_parts WHERE cid=?", (cid,))
        voted = {int(r["crank"]): r["digest"] == "1" for r in rows}
        failed = set(get_failed(comm))
        expected = {c for c in range(comm.size) if c not in failed}
        if expected <= set(voted):
            result = all(v for c, v in voted.items() if c in expected)
            comm.fabric.emit(
                "ft.agree",
                rank=comm.rt.wrank,
                ctx=comm.ctx,
                result=result,
                voters=sorted(expected),
                excluded=sorted(failed),
            )
            return result
        if time.time() > deadline:
            missing = sorted(expected - set(voted))
            for m in missing:
                declare_failed(comm, m, kind=FailureClass.FAIL_STOP, detail="agree timeout")
            comm.fabric.emit("ft.agree_timeout", rank=comm.rt.wrank, ctx=comm.ctx, missing=missing)
            raise AmpiProcFailed("agree could not complete", failed=tuple(missing), ctx=comm.ctx)
        if is_revoked(comm):
            raise AmpiRevoked("communicator revoked during agree", ctx=comm.ctx)
        time.sleep(poll)
        poll = min(0.25, poll * 1.2)


# ============================================================================
# Redundancy: the only defence against FAIL_PLAUSIBLE
# ============================================================================


@dataclass
class ReplicaVerdict:
    """Result of running the same work on several ranks and comparing."""

    agreed: bool
    chosen: Any
    n_replicas: int
    n_distinct: int
    #: Fraction of replicas that produced the modal answer.
    consensus: float
    outputs: list[Any] = field(default_factory=list)
    detail: str = ""


def replicate_and_compare(
    outputs: Sequence[Any],
    *,
    key: Callable[[Any], str] | None = None,
    quorum: float = 0.5,
) -> ReplicaVerdict:
    """Compare redundant outputs and pick the modal one.

    This is *n*-modular redundancy, the classic defence against silent
    corruption, and the only one available when the failure mode is a
    confident-but-wrong answer.  The agent-specific wrinkle is that exact
    equality is useless — two correct answers phrased differently are not
    byte-identical — so the comparison happens on a ``key`` projection that the
    harness chooses to capture what must agree: a set of extracted decisions, a
    test-pass vector, a normalised term list.  Choosing that projection well is
    the whole game, and the protocol's job is only to make the pattern
    expressible and its outcome recorded.

    The literature's caution applies: replicating a language model does not give
    independent failures, because correlated errors are the norm.  Agreement is
    therefore evidence, not proof, and :attr:`ReplicaVerdict.consensus` is
    reported so a harness can escalate weak agreement rather than trust it.
    """
    if not outputs:
        return ReplicaVerdict(False, None, 0, 0, 0.0, [], "no replicas")
    keyed: dict[str, list[Any]] = {}
    kf = key or (lambda x: str(x))
    for o in outputs:
        keyed.setdefault(kf(o), []).append(o)
    best_key = max(keyed, key=lambda k: len(keyed[k]))
    consensus = len(keyed[best_key]) / len(outputs)
    return ReplicaVerdict(
        agreed=consensus >= quorum and len(keyed) == 1,
        chosen=keyed[best_key][0],
        n_replicas=len(outputs),
        n_distinct=len(keyed),
        consensus=consensus,
        outputs=list(outputs),
        detail=f"{len(keyed)} distinct answers, modal share {consensus:.0%}",
    )


def verify(payload: Any, validators: Sequence[Validator]) -> tuple[bool, FailureClass | None, str]:
    """Run validators in order, cheapest first, and classify the failure."""
    for v in sorted(validators, key=lambda x: x.cost_tokens):
        ok, detail = v(payload)
        if not ok:
            return False, FailureClass(v.classifies), f"{v.name}: {detail}"
    return True, None, ""


# ============================================================================
# Supervision (Erlang/OTP)
# ============================================================================


@dataclass
class ChildSpec:
    """Declaration of a supervised rank."""

    rank: int
    #: Called to (re)start the rank.  Receives the incarnation number.
    start: Callable[[int], Any]
    restart: str = "transient"  # permanent | transient | temporary
    #: Ranks that must be restarted together with this one, for REST_FOR_ONE.
    order: int = 0


@dataclass
class Supervisor:
    """An OTP-style supervisor over agent ranks.

    ``max_restarts`` within ``max_seconds`` bounds thrashing: a rank that keeps
    dying is not restarted forever, because in OTP's experience — and in every
    agent harness that retries on failure — an unbounded restart loop turns a
    local fault into a global cost overrun.  Exceeding the intensity escalates,
    which for a top-level supervisor means failing the job.
    """

    fabric: Fabric
    policy: RestartPolicy = RestartPolicy.ONE_FOR_ONE
    max_restarts: int = 3
    max_seconds: float = 600.0
    children: dict[int, ChildSpec] = field(default_factory=dict)
    _restarts: list[tuple[float, int]] = field(default_factory=list)

    def add(self, spec: ChildSpec) -> None:
        self.children[spec.rank] = spec

    def _within_intensity(self) -> bool:
        cutoff = time.time() - self.max_seconds
        self._restarts = [(t, r) for t, r in self._restarts if t >= cutoff]
        return len(self._restarts) < self.max_restarts

    def handle_failure(self, rank: int, *, kind: FailureClass, detail: str = "") -> list[int]:
        """Apply the restart strategy.  Returns the ranks restarted."""
        if rank not in self.children:
            return []
        if not self._within_intensity():
            self.fabric.emit(
                "sup.escalate",
                rank=rank,
                kind=kind.value,
                restarts=len(self._restarts),
                window_s=self.max_seconds,
            )
            raise AmpiProcFailed("restart intensity exceeded; escalating", failed=(rank,), detail=detail)
        spec = self.children[rank]
        if spec.restart == "temporary":
            return []
        if self.policy is RestartPolicy.NONE:
            targets: list[int] = []
        elif self.policy is RestartPolicy.ONE_FOR_ONE:
            targets = [rank]
        elif self.policy is RestartPolicy.ONE_FOR_ALL:
            targets = sorted(self.children)
        elif self.policy is RestartPolicy.REST_FOR_ONE:
            pivot = spec.order
            targets = sorted(r for r, s in self.children.items() if s.order >= pivot)
        else:
            targets = [rank]
        for t in targets:
            self._restarts.append((time.time(), t))
            row = self.fabric.query_one("SELECT incarnation FROM ranks WHERE rank=?", (t,))
            inc = int(row["incarnation"]) if row else 0
            self.fabric.emit("sup.restart", rank=t, policy=self.policy.value, incarnation=inc + 1, cause=kind.value)
            self.children[t].start(inc + 1)
        return targets
