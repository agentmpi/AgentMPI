"""Quantitative analysis of a single AgentMPI run, computed from its event log.

The event log is the only input. Not the fabric, not in-memory counters, not a results file:
just the ordered list of events, which is what ``traces/events/<name>.jsonl`` contains and
what survives a crashed rank. Anything derivable about a run should be derivable from that,
and where it is not, the gap is a defect in what the runtime records rather than a reason to
consult a second source.

What this module answers, roughly in the order a reader asks:

*Did it work, and how long did it take?* Wall time, per-rank occupancy, and the shape of the
timeline.

*Was the parallelism real?* Achieved concurrency over time, not just rank count. A run with
eight ranks where concurrency never exceeds two is a serial run with extra bookkeeping, and
the distinction is invisible in a summary that reports ``size=8``.

*What did coordination cost?* Messages, token volume, and time inside collectives --- separated
from time spent working, because for agent ranks the two differ by orders of magnitude and
conflating them is how a harness comes to look efficient while spending most of its budget on
protocol traffic.

*Did the implementation do what the model says?* Every collective invocation is checked against
the closed-form cost expression for its algorithm. A disagreement is a bug in one of the two,
and this is where it surfaces.

*What went wrong, and what did the harness do about it?* Failures by class, and the recovery
events that followed, with the latency between them.

The deliberate omission is judgement. Nothing here decides whether a number is good; that
requires knowing what the run was trying to show, which lives in the analysis documents.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import cost, trace_style

Event = dict[str, Any]


# --------------------------------------------------------------------------------------
# Per-rank behaviour
# --------------------------------------------------------------------------------------


@dataclass
class RankProfile:
    """What one rank did, and how much of the run it was actually busy for."""

    rank: int
    #: Seconds inside a broker claim-to-completion interval. This is occupancy as the harness
    #: can observe it, which is the only definition available for an agent it does not host.
    busy_s: float = 0.0
    n_work: int = 0
    n_agent_calls: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    sent: int = 0
    recv: int = 0
    tokens_sent: int = 0
    tokens_recv: int = 0
    tokens_admitted: int = 0
    n_trouble: int = 0
    n_recovery: int = 0
    n_retries: int = 0
    n_contract_violations: int = 0
    state: str = "unknown"
    #: Highest incarnation number seen for this rank. A rank is a durable *role*, and the agent
    #: process filling it is ephemeral: when one exits and another attaches, the incarnation
    #: increments while the rank keeps its identity, its mailbox, and its context account. So this
    #: counts how many separate agent processes stood in for one participant, which is the
    #: clearest direct evidence that the abstraction survives the thing it abstracts over.
    max_incarnation: int = 0
    context_used: int = 0
    context_budget: int = 0
    context_high_water: int = 0
    context_rejections: int = 0
    context_evictions: int = 0
    latencies: list[float] = field(default_factory=list)
    kinds: Counter = field(default_factory=Counter)

    @property
    def lifetime_s(self) -> float:
        if self.first_ts is None or self.last_ts is None:
            return 0.0
        return self.last_ts - self.first_ts

    @property
    def occupancy(self) -> float:
        """Busy fraction of the rank's own lifetime, in [0, 1]."""
        return self.busy_s / self.lifetime_s if self.lifetime_s > 0 else 0.0

    @property
    def context_occupancy(self) -> float:
        return self.context_used / self.context_budget if self.context_budget else 0.0

    @property
    def reattachments(self) -> int:
        """Times a fresh agent process took over this rank after the first."""
        return max(0, self.max_incarnation - 1)

    @property
    def latency_p50(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    @property
    def latency_max(self) -> float:
        return max(self.latencies) if self.latencies else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "busy_s": round(self.busy_s, 3),
            "lifetime_s": round(self.lifetime_s, 3),
            "occupancy": round(self.occupancy, 4),
            "n_work": self.n_work,
            "n_agent_calls": self.n_agent_calls,
            "sent": self.sent,
            "recv": self.recv,
            "tokens_sent": self.tokens_sent,
            "tokens_recv": self.tokens_recv,
            "tokens_admitted": self.tokens_admitted,
            "n_trouble": self.n_trouble,
            "n_recovery": self.n_recovery,
            "n_retries": self.n_retries,
            "n_contract_violations": self.n_contract_violations,
            "state": self.state,
            "context_used": self.context_used,
            "context_budget": self.context_budget,
            "context_occupancy": round(self.context_occupancy, 4),
            "context_high_water": self.context_high_water,
            "context_rejections": self.context_rejections,
            "context_evictions": self.context_evictions,
            "max_incarnation": self.max_incarnation,
            "reattachments": self.reattachments,
            "latency_p50": round(self.latency_p50, 3),
            "latency_max": round(self.latency_max, 3),
        }


# --------------------------------------------------------------------------------------
# Collectives
# --------------------------------------------------------------------------------------


@dataclass
class CollectiveInvocation:
    """One logical collective: the p per-rank records that belong to a single call.

    Reconstructed rather than recorded, because each rank emits its own event and the runtime
    assigns no invocation id. Two rules identify a group: participants are unique within an
    invocation, and a rank's next event of the same kind and label belongs to the next one.
    """

    op: str
    label: str
    algorithm: str
    size: int
    root: int | None
    participants: list[int]
    t_first: float
    t_last: float
    #: Critical-path rounds is the maximum over ranks, not the sum: rounds are concurrent.
    rounds: int
    messages: int
    tokens: int
    fold_depth: int
    #: Longest per-rank wall time inside the call. For a barrier this is the straggler's wait, and
    #: it is a *critical path* figure: it must never be summed across invocations, because two
    #: invocations whose stragglers waited concurrently would then be charged twice for the same
    #: wall clock.
    wall_s: float
    #: Rank-seconds spent blocked inside this call, summed over participants. This is the additive
    #: quantity: it can be divided by the rank-seconds the run had available to give a share that
    #: is bounded by one, which ``wall_s`` cannot.
    rank_wall_s: float = 0.0
    #: ``(end_time, blocking_duration)`` per participant, relative to the run's first event. Kept so
    #: overlapping waits can be unioned rather than summed; a collective is recorded on completion,
    #: so a rank's blocking interval ends at its event and began ``blocking_duration`` earlier.
    rank_intervals: list[tuple[float, float]] = field(default_factory=list)
    absent: list[int] = field(default_factory=list)
    divergence_risk: bool = False
    predicted_rounds: int | None = None
    predicted_messages: int | None = None
    #: Ops this invocation is built from, when it delegates its traffic. ``allreduce`` under
    #: ``reduce_bcast`` sends nothing itself: the nested reduce and bcast record the messages,
    #: and the outer event reports zero to avoid counting them twice. Validating the outer
    #: event against its own closed form without this would report a phantom 14-message
    #: shortfall on a run where nothing is wrong.
    composed_of: list[str] = field(default_factory=list)
    nested_messages: int = 0
    #: Messages the fabric actually logged for this invocation, recovered from the internal tag
    #: rather than from what the collective said it sent. Kept separate from ``messages`` on
    #: purpose: when the two disagree the implementation's accounting is wrong, and that is a
    #: defect worth surfacing rather than a number to average away.
    logged_messages: int | None = None
    logged_tokens: int = 0

    @property
    def accounting_agrees(self) -> bool | None:
        """Does the collective's self-report match the traffic the fabric recorded?"""
        if self.logged_messages is None:
            return None
        return self.effective_messages == self.logged_messages

    @property
    def skew_s(self) -> float:
        """Spread between first and last rank to record the call: the synchronisation cost."""
        return self.t_last - self.t_first

    @property
    def n_participants(self) -> int:
        return len(self.participants)

    @property
    def is_composed(self) -> bool:
        return bool(self.composed_of)

    @property
    def complete(self) -> bool:
        """Did every rank in the communicator record this call?

        An incomplete collective is not a cheaper collective. When ranks die or never arrive,
        the survivors log fewer messages than the algorithm requires, and comparing that to a
        closed form derived for the full communicator manufactures a model disagreement out of a
        run that simply did not finish. The p=16 translation run is exactly this: four ranks never
        reached the reduce, twelve messages were logged where fifteen were predicted, and nothing
        about the model was wrong.
        """
        return self.size > 0 and self.n_participants >= self.size

    @property
    def effective_messages(self) -> int:
        """Messages attributable to this call, including those its constituents sent."""
        return self.messages + self.nested_messages

    @property
    def messages_agree(self) -> bool | None:
        """Does the model match reality? Checked against logged traffic where it is available.

        Preferring the log over the self-report is what makes this an independent check. Both
        numbers come from the same implementation, but the log is produced by the transport as
        messages are sent, while the count is maintained by hand in the algorithm --- so a model
        validated against the self-report can agree with a collective that is miscounting, which
        is precisely what happened for recursive doubling at non-power-of-two sizes.
        """
        if self.predicted_messages is None or not self.complete:
            return None
        actual = self.logged_messages if self.logged_messages is not None else self.effective_messages
        return actual == self.predicted_messages

    @property
    def rounds_agree(self) -> bool | None:
        if self.predicted_rounds is None:
            return None
        return self.rounds == self.predicted_rounds

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "label": self.label,
            "algorithm": self.algorithm,
            "size": self.size,
            "root": self.root,
            "n_participants": self.n_participants,
            "complete": self.complete,
            "t_first": round(self.t_first, 3),
            "skew_s": round(self.skew_s, 3),
            "rounds": self.rounds,
            "messages": self.messages,
            "nested_messages": self.nested_messages,
            "effective_messages": self.effective_messages,
            "logged_messages": self.logged_messages,
            "logged_tokens": self.logged_tokens,
            "accounting_agrees": self.accounting_agrees,
            "composed_of": self.composed_of,
            "tokens": self.tokens,
            "fold_depth": self.fold_depth,
            "wall_s": round(self.wall_s, 3),
            "rank_wall_s": round(self.rank_wall_s, 3),
            "absent": self.absent,
            "divergence_risk": self.divergence_risk,
            "predicted_rounds": self.predicted_rounds,
            "predicted_messages": self.predicted_messages,
            "messages_agree": self.messages_agree,
            "rounds_agree": self.rounds_agree,
        }


# --------------------------------------------------------------------------------------
# Concurrency
# --------------------------------------------------------------------------------------


@dataclass
class ConcurrencyProfile:
    """How many ranks were simultaneously busy, sampled over the run.

    The point of a parallel harness is overlap, and rank count does not measure it. This is
    computed from the union of busy intervals so it reports achieved parallelism: a run whose
    concurrency never exceeds one is serial regardless of how many ranks it registered.
    """

    times: list[float]
    busy: list[int]
    max_busy: int
    #: Averaged over the interval where at least one rank was busy, so idle tails at the start
    #: and end of a run do not flatter or penalise it.
    mean_busy_when_active: float
    busy_union_s: float
    total_busy_s: float
    wall_s: float
    world_size: int

    @property
    def achieved_parallelism(self) -> float:
        """Total work divided by wall time: the speedup over doing it on one rank."""
        return self.total_busy_s / self.wall_s if self.wall_s > 0 else 0.0

    @property
    def parallel_efficiency(self) -> float:
        """Achieved parallelism per rank, in [0, 1]."""
        p = self.world_size
        return self.achieved_parallelism / p if p else 0.0

    @property
    def serial_fraction_of_busy(self) -> float:
        """Fraction of active time with exactly one rank busy --- Amdahl's serial part, measured."""
        if not self.busy:
            return 0.0
        active = [b for b in self.busy if b > 0]
        if not active:
            return 0.0
        return sum(1 for b in active if b == 1) / len(active)

    @property
    def idle_fraction(self) -> float:
        """Rank-seconds idle over rank-seconds available. What the run paid for and did not use."""
        available = self.world_size * self.wall_s
        return 1.0 - (self.total_busy_s / available) if available > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_busy": self.max_busy,
            "mean_busy_when_active": round(self.mean_busy_when_active, 3),
            "busy_union_s": round(self.busy_union_s, 3),
            "total_busy_s": round(self.total_busy_s, 3),
            "achieved_parallelism": round(self.achieved_parallelism, 3),
            "parallel_efficiency": round(self.parallel_efficiency, 4),
            "serial_fraction_of_busy": round(self.serial_fraction_of_busy, 4),
            "idle_fraction": round(self.idle_fraction, 4),
        }


# --------------------------------------------------------------------------------------
# The whole run
# --------------------------------------------------------------------------------------


@dataclass
class Analysis:
    name: str
    experiment: str
    label: str
    world_size: int
    n_events: int
    t0: float
    wall_s: float
    ok: bool | None
    #: The log this analysis is a view over. Kept so consumers that need per-event detail --- the
    #: timeline figure needs every instant, not just the aggregates --- do not have to re-read and
    #: re-parse the file, and cannot accidentally pair one run's events with another's metrics.
    events: list[Event]
    ranks: dict[int, RankProfile]
    collectives: list[CollectiveInvocation]
    #: ``(src, dst) -> (n_messages, tokens)`` over world ranks.
    comm: dict[tuple[int, int], tuple[int, int]]
    kind_counts: Counter
    role_counts: Counter
    work_spans: list[tuple[int, float, float, str]]
    trouble: list[Event]
    recovery: list[Event]
    concurrency: ConcurrencyProfile
    summary: cost.RunSummary
    calibration: Any
    eager_messages: int = 0
    rendezvous_messages: int = 0
    tokens_deferred: int = 0
    failed_ranks: list[int] = field(default_factory=list)
    executors: Counter = field(default_factory=Counter)

    # -- derived quantities a document will want to cite -------------------------------

    @property
    def n_ranks_seen(self) -> int:
        return len(self.ranks)

    @property
    def stray_ranks(self) -> list[int]:
        """Ranks that registered but did nothing, or that sit outside the declared world.

        Real, and worth reporting rather than smoothing over: when experiments share a
        persistent worker pool, a worker can register against the wrong job and appear in its
        log as a rank with a single ``rank.init`` and no participation. A run whose rank count
        exceeds its world size is not a run with extra parallelism, it is a run with a
        bookkeeping leak, and the two look identical in any summary that reports only a count.
        """
        out = []
        for rank, p in sorted(self.ranks.items()):
            inert = p.sent == 0 and p.recv == 0 and p.n_work == 0 and p.n_agent_calls == 0
            if rank >= self.world_size or (inert and p.kinds.get("rank.init", 0) > 0 and len(p.kinds) <= 1):
                out.append(rank)
        return out

    @property
    def participating_ranks(self) -> list[int]:
        stray = set(self.stray_ranks)
        return [r for r in sorted(self.ranks) if r not in stray]

    @property
    def primitive_collectives(self) -> list[CollectiveInvocation]:
        """Collectives excluding those that merely delegate to others.

        A composed invocation spans its constituents in time, so counting both charges the same
        waiting twice. ``reduce_bcast`` is the whole of its nested reduce and bcast.
        """
        return [c for c in self.collectives if not c.is_composed]

    @property
    def collective_rank_seconds(self) -> float:
        """Rank-seconds spent blocked inside collectives.

        The additive measure of coordination cost. Per-rank blocking times are summed over
        participants and over primitive invocations, which is legitimate because rank-seconds of
        two different ranks are genuinely different resources even when they elapse concurrently.
        """
        return sum(c.rank_wall_s for c in self.primitive_collectives)

    @property
    def collective_span_s(self) -> float:
        """Wall-clock seconds during which at least one rank was inside a collective.

        Computed as a union of intervals rather than a sum, so it answers "how much of the run had
        coordination in flight" without double counting concurrent waits. Each rank's interval ends
        when it records the collective and began ``wall_s`` earlier.
        """
        intervals: list[tuple[float, float]] = []
        for c in self.primitive_collectives:
            for rank_end, rank_wall in c.rank_intervals:
                if rank_wall > 0:
                    intervals.append((max(0.0, rank_end - rank_wall), rank_end))
        if not intervals:
            return 0.0
        intervals.sort()
        total = 0.0
        cur_start, cur_end = intervals[0]
        for start, end in intervals[1:]:
            if start > cur_end:
                total += cur_end - cur_start
                cur_start, cur_end = start, end
            else:
                cur_end = max(cur_end, end)
        return total + (cur_end - cur_start)

    @property
    def coordination_is_underreported(self) -> bool:
        """Does the blocking figure omit ranks that never finished a collective?

        A rank records a ``coll.*`` event on *completion*, so a rank that blocks and then dies or
        times out contributes nothing to the coordination measures. On the p=16 translation run
        that understates coordination catastrophically: sixteen ranks each blocked 1800\u2009s inside
        the glossary reduce, and ``coordination_share`` reads 0.0% because none of them ever got to
        record it. The measures are still correct as defined --- time spent inside *completed*
        collectives --- but on a degraded run that definition is not the quantity a reader wants,
        and silence about the difference would be misleading.
        """
        return bool(self.incomplete_collectives) or self.ok is False

    @property
    def coordination_share(self) -> float:
        """Fraction of the run's rank-seconds spent blocked in *completed* collectives, in [0, 1].

        Read with ``coordination_is_underreported``: on a run where ranks died inside a collective
        this omits their blocking entirely, because a collective is recorded on completion.

        Defined against rank-seconds available --- ``world_size`` times wall time --- rather than
        against wall time alone. The earlier definition summed each invocation's *maximum* per-rank
        wait and divided by wall time, which is not a share of anything: it charged one rank's wait
        inside a reduce and another rank's concurrent wait inside the following broadcast as two
        separate costs, and on the translation ablations it produced 137%. A quantity that can
        exceed 1 cannot be read as a proportion, and it made a harness that was coordinating less
        look like one that was coordinating more.
        """
        available = self.world_size * self.wall_s
        return self.collective_rank_seconds / available if available > 0 else 0.0

    @property
    def coordination_span_share(self) -> float:
        """Fraction of wall clock with coordination in flight anywhere, in [0, 1]."""
        return self.collective_span_s / self.wall_s if self.wall_s > 0 else 0.0

    @property
    def imbalance(self) -> float:
        """Slowest rank's busy time over the mean. 1.0 is perfect; 2.0 means half the pool idles."""
        busy = [r.busy_s for r in self.ranks.values() if r.busy_s > 0]
        if not busy:
            return 0.0
        mean = sum(busy) / len(busy)
        return max(busy) / mean if mean > 0 else 0.0

    @property
    def model_checks(self) -> tuple[int, int]:
        """``(agreeing, checked)`` over collectives whose prediction is meaningful to check."""
        checked = [c for c in self.collectives if c.messages_agree is not None]
        return sum(1 for c in checked if c.messages_agree), len(checked)

    @property
    def incomplete_collectives(self) -> list[CollectiveInvocation]:
        """Collectives some rank never reached. The clearest signature of a run that broke."""
        return [c for c in self.collectives if not c.complete]

    @property
    def misreported_collectives(self) -> list[CollectiveInvocation]:
        """Collectives whose self-reported message count disagrees with the logged traffic."""
        return [c for c in self.collectives if c.accounting_agrees is False]

    @property
    def degraded(self) -> bool:
        """Did the run finish everything it started, by its own accounting?"""
        return bool(self.failed_ranks) or self.ok is False or bool(self.incomplete_collectives)

    @property
    def total_reattachments(self) -> int:
        """Agent processes that took over a rank role mid-run, summed over ranks.

        Reported at the run level because it is the measurement behind the claim that ranks are
        durable roles rather than processes. A translation run with eight ranks and thirty-two
        reattachments is one where every participant was replaced four times and the collectives
        neither noticed nor cared.
        """
        return sum(p.reattachments for p in self.ranks.values())

    @property
    def max_claim_wait_s(self) -> float:
        """Longest a rank waited for an agent to claim its task before the broker gave up.

        Its own metric because it separates two failures that look identical in a summary: a run
        that was slow, and a run where no agent ever showed up. The second is a pool-sizing
        problem outside the protocol, and reading it as the first would blame the harness.
        """
        waits = [
            float(e["payload"].get("waited_s") or 0.0)
            for e in self.trouble
            if e["kind"] == "broker.expire"
        ]
        return max(waits) if waits else 0.0

    @property
    def usd_per_ktoken_out(self) -> float:
        out = self.summary.tokens_out
        return (self.summary.usd / out * 1000) if out else 0.0

    def as_dict(self) -> dict[str, Any]:
        agree, checked = self.model_checks
        return {
            "name": self.name,
            "experiment": self.experiment,
            "label": self.label,
            "world_size": self.world_size,
            "n_ranks_seen": self.n_ranks_seen,
            "stray_ranks": self.stray_ranks,
            "n_events": self.n_events,
            "wall_s": round(self.wall_s, 3),
            "ok": self.ok,
            "imbalance": round(self.imbalance, 3),
            "collective_rank_seconds": round(self.collective_rank_seconds, 3),
            "collective_span_s": round(self.collective_span_s, 3),
            "coordination_share": round(self.coordination_share, 4),
            "coordination_span_share": round(self.coordination_span_share, 4),
            "coordination_is_underreported": self.coordination_is_underreported,
            "n_primitive_collectives": len(self.primitive_collectives),
            "n_collectives": len(self.collectives),
            "model_checks_agree": agree,
            "model_checks_total": checked,
            "degraded": self.degraded,
            "n_incomplete_collectives": len(self.incomplete_collectives),
            "n_misreported_collectives": len(self.misreported_collectives),
            "max_claim_wait_s": round(self.max_claim_wait_s, 1),
            "total_reattachments": self.total_reattachments,
            "eager_messages": self.eager_messages,
            "rendezvous_messages": self.rendezvous_messages,
            "tokens_deferred": self.tokens_deferred,
            "failed_ranks": self.failed_ranks,
            "n_trouble": len(self.trouble),
            "n_recovery": len(self.recovery),
            "executors": dict(self.executors),
            "usd_per_ktoken_out": round(self.usd_per_ktoken_out, 5),
            "concurrency": self.concurrency.as_dict(),
            "summary": self.summary.as_dict(),
            "calibration": self.calibration.as_dict(),
            "ranks": [r.as_dict() for r in sorted(self.ranks.values(), key=lambda r: r.rank)],
            "collectives": [c.as_dict() for c in self.collectives],
            "kind_counts": dict(sorted(self.kind_counts.items())),
            "role_counts": dict(sorted(self.role_counts.items())),
            "comm": {f"{s}->{d}": {"n": n, "tokens": t} for (s, d), (n, t) in sorted(self.comm.items())},
        }


# --------------------------------------------------------------------------------------


def _concurrency(
    spans: list[tuple[int, float, float, str]], wall_s: float, world_size: int, samples: int = 600
) -> ConcurrencyProfile:
    total_busy = sum(e - s for _r, s, e, _l in spans)
    if not spans or wall_s <= 0:
        return ConcurrencyProfile([], [], 0, 0.0, 0.0, total_busy, wall_s, world_size)

    step = wall_s / samples
    times = [i * step for i in range(samples + 1)]
    busy = [0] * len(times)
    for _rank, start, end, _label in spans:
        lo = max(0, int(start / step))
        hi = min(len(times) - 1, math.ceil(end / step))
        for i in range(lo, hi + 1):
            if start <= times[i] <= end:
                busy[i] += 1

    active = [b for b in busy if b > 0]
    union_s = len(active) * step
    return ConcurrencyProfile(
        times=times,
        busy=busy,
        max_busy=max(busy),
        mean_busy_when_active=(sum(active) / len(active)) if active else 0.0,
        busy_union_s=union_s,
        total_busy_s=total_busy,
        wall_s=wall_s,
        world_size=world_size,
    )


def _group_collectives(events: list[Event], t0: float) -> list[CollectiveInvocation]:
    """Reconstruct logical collective invocations from per-rank records."""
    by_key: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for e in events:
        if e["kind"].startswith("coll."):
            op = e["kind"].split(".", 1)[1]
            by_key[(op, str(e["payload"].get("label") or ""))].append(e)

    out: list[CollectiveInvocation] = []
    for (op, label), group in by_key.items():
        group.sort(key=lambda e: e["ts"])
        current: list[Event] = []
        seen: set[int] = set()
        for e in group:
            rank = e["rank"]
            if rank in seen:
                out.append(_build_invocation(op, label, current, t0))
                current, seen = [], set()
            current.append(e)
            seen.add(rank)
        if current:
            out.append(_build_invocation(op, label, current, t0))

    out.sort(key=lambda c: c.t_first)
    _attribute_nested_traffic(out)
    _attribute_logged_traffic(out, events, t0)
    return out


#: Internal tags are ``_ampi:<op>:<generation>:<epoch>[:<extra>]``. The op name in a tag is not
#: always the collective's name, because the topology module abbreviates.
TAG_PREFIX = "_ampi:"
TAG_OP_TO_COLL: dict[str, str] = {
    "halo": "halo_exchange",
    "nbr_ag": "neighbor_allgather",
    "nbr_a2a": "neighbor_alltoall",
}


def _attribute_logged_traffic(
    invocations: list[CollectiveInvocation], events: list[Event], t0: float
) -> None:
    """Attach the traffic the fabric actually logged to each collective invocation.

    Every message a collective sends is tagged ``_ampi:<op>:<generation>:<epoch>``, and the epoch
    is a per-communicator counter that increments once per call --- so it identifies an invocation
    exactly, without needing to guess from timing. Groups are then matched to invocations of the
    same op in time order.

    Composed algorithms take no epoch of their own; their nested calls do. So they correctly end
    up with no logged traffic attributed directly, matching their zero self-report.
    """
    groups: dict[tuple[str, str, str], list[float]] = {}
    for e in events:
        if e["kind"] != "msg.send":
            continue
        tag = str(e["payload"].get("tag") or "")
        if not tag.startswith(TAG_PREFIX):
            continue
        parts = tag[len(TAG_PREFIX) :].split(":")
        if len(parts) < 3:
            continue
        tag_op, generation, epoch = parts[0], parts[1], parts[2]
        op = TAG_OP_TO_COLL.get(tag_op, tag_op)
        slot = groups.setdefault((op, generation, epoch), [0, 0, e["ts"] - t0])
        slot[0] += 1
        slot[1] += int(e["payload"].get("tokens") or 0)
        slot[2] = min(slot[2], e["ts"] - t0)

    by_op: dict[str, list[tuple[float, int, int]]] = defaultdict(list)
    for (op, _generation, _epoch), (n, tokens, t_first) in groups.items():
        by_op[op].append((t_first, n, tokens))
    for op in by_op:
        by_op[op].sort()

    # Composed invocations take no epoch of their own --- their constituents do --- so they must be
    # excluded before the counts are compared. Otherwise a run with two `allreduce/reduce_bcast`
    # calls sees one tagged group against two invocations, the mismatch guard fires, and
    # `logged_messages` is left unset for the very collective under study. The log-based check then
    # silently degrades into a restatement of the self-report, which is the one thing it exists not
    # to be, and the invocation is also exempted from the misreporting check.
    per_op_invocations: dict[str, list[CollectiveInvocation]] = defaultdict(list)
    for inv in invocations:
        if inv.is_composed:
            continue
        per_op_invocations[inv.op].append(inv)

    for op, invs in per_op_invocations.items():
        observed = by_op.get(op)
        if observed is None:
            # No tagged traffic for this op at all: a barrier that sent nothing, or an op whose
            # messages are tagged under its constituents. Leaving `logged_messages` as None says
            # "not observed" rather than falsely claiming zero.
            continue
        if len(observed) != len(invs):
            # Counts disagree, so positional matching would silently pair the wrong records.
            # Fall back to attributing the total to the whole op, which is still checkable in
            # aggregate, and leave per-invocation attribution unset.
            continue
        for inv, (_t, n, tokens) in zip(sorted(invs, key=lambda c: c.t_first), observed, strict=True):
            inv.logged_messages = n
            inv.logged_tokens = tokens


#: Algorithms implemented over other collectives, and what they delegate to. Taken from the
#: implementations in ``algorithms.py`` rather than inferred: the outer event is recorded on
#: completion, *after* its constituents, so time containment does not identify the nesting and a
#: timing heuristic would silently mis-attribute traffic.
COMPOSED_ALGORITHMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("allreduce", "reduce_bcast"): ("reduce", "bcast"),
    ("allgather", "gather_bcast"): ("gather", "bcast"),
    ("bcast", "scatter_allgather"): ("scatter", "allgather"),
}


def _attribute_nested_traffic(invocations: list[CollectiveInvocation]) -> None:
    """Credit a delegating collective with the messages its constituents sent.

    A composed collective reports zero messages of its own --- the nested calls record them, and
    double counting would be worse --- so validating the outer event against its own closed form
    in isolation always fails. On the translation runs that showed up as two allreduces each
    apparently 14 messages short on a run where nothing was wrong.

    Constituents are matched by position within a label: the i-th ``allreduce`` labelled
    ``glossary`` pairs with the i-th ``reduce`` and the i-th ``bcast`` under the same label,
    which is exactly the order a harness issues them in.
    """
    by_label_op: dict[tuple[str, str], list[CollectiveInvocation]] = defaultdict(list)
    for inv in invocations:
        by_label_op[(inv.label, inv.op)].append(inv)

    for key in by_label_op:
        by_label_op[key].sort(key=lambda c: c.t_first)

    for (label, op), group in by_label_op.items():
        for index, outer in enumerate(group):
            parts = COMPOSED_ALGORITHMS.get((op, outer.algorithm))
            if parts is None:
                continue
            nested: list[CollectiveInvocation] = []
            for part in parts:
                candidates = by_label_op.get((label, part), [])
                if index < len(candidates):
                    nested.append(candidates[index])
            if not nested:
                continue
            outer.composed_of = list(parts)
            outer.nested_messages = sum(inv.messages for inv in nested)


def _build_invocation(op: str, label: str, group: list[Event], t0: float) -> CollectiveInvocation:
    payloads = [e["payload"] for e in group]
    algorithms = Counter(str(p.get("algorithm") or "?") for p in payloads)
    algorithm = algorithms.most_common(1)[0][0]
    # Not every collective records its size (halo_exchange does not), so fall back to the number
    # of ranks that participated. Reporting p=0 would make the cost check silently unavailable.
    size = max(
        max((int(p.get("size") or 0) for p in payloads), default=0),
        len(group),
    )
    roots = {p.get("root") for p in payloads if p.get("root") is not None}
    absent: set[int] = set()
    for p in payloads:
        for r in p.get("absent") or []:
            absent.add(int(r))

    inv = CollectiveInvocation(
        op=op,
        label=label,
        algorithm=algorithm,
        size=size,
        root=(sorted(roots)[0] if roots else None),
        participants=sorted(e["rank"] for e in group),
        t_first=group[0]["ts"] - t0,
        t_last=group[-1]["ts"] - t0,
        rounds=max((int(p.get("rounds") or 0) for p in payloads), default=0),
        messages=sum(int(p.get("messages_sent") or 0) for p in payloads),
        tokens=sum(int(p.get("tokens_sent") or 0) for p in payloads),
        fold_depth=max((int(p.get("fold_depth") or 0) for p in payloads), default=0),
        wall_s=max((float(p.get("wall_s") or 0.0) for p in payloads), default=0.0),
        rank_wall_s=sum(float(p.get("wall_s") or 0.0) for p in payloads),
        rank_intervals=[
            (e["ts"] - t0, float(e["payload"].get("wall_s") or 0.0)) for e in group
        ],
        absent=sorted(absent),
        divergence_risk=any(bool(p.get("divergence_risk")) for p in payloads),
    )

    formula = cost.FORMULAS.get((op, algorithm))
    if formula is not None and size > 0:
        try:
            rounds, messages, _volume, _depth = formula(size, 1000)
            inv.predicted_rounds = int(rounds)
            inv.predicted_messages = int(messages)
        except Exception:
            # A formula that cannot evaluate for this p is a gap in the model, not a reason to
            # abandon the rest of the analysis; the document reports it as unchecked.
            pass
    return inv


def analyse(events: list[Event], name: str = "", experiment: str = "", label: str = "") -> Analysis:
    """Compute everything derivable about a run from its event log."""
    if not events:
        raise ValueError("cannot analyse an empty event log")

    t0 = events[0]["ts"]
    wall_s = events[-1]["ts"] - t0

    ranks: dict[int, RankProfile] = {}
    comm: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    kind_counts: Counter = Counter()
    role_counts: Counter = Counter()
    work_spans: list[tuple[int, float, float, str]] = []
    trouble: list[Event] = []
    recovery: list[Event] = []
    open_claims: dict[int, tuple[int, float, str]] = {}
    world_size = 0
    ok: bool | None = None
    eager = rendezvous = deferred = 0
    failed_ranks: list[int] = []
    executors: Counter = Counter()

    def prof(rank: int) -> RankProfile:
        p = ranks.get(rank)
        if p is None:
            p = ranks[rank] = RankProfile(rank=rank)
        return p

    for e in events:
        kind, payload, rank, ts = e["kind"], e["payload"], e["rank"], e["ts"]
        kind_counts[kind] += 1
        role_counts[trace_style.role_of(kind)] += 1

        if kind == "job.create":
            world_size = int(payload.get("size") or 0)
        elif kind == "job.finish":
            ok = bool(payload.get("ok"))
            failed_ranks = [int(r) for r in (payload.get("failed_ranks") or [])]

        if rank is None:
            continue
        p = prof(int(rank))
        p.kinds[kind] += 1
        p.first_ts = ts if p.first_ts is None else min(p.first_ts, ts)
        p.last_ts = ts if p.last_ts is None else max(p.last_ts, ts)

        role = trace_style.role_of(kind)
        if role == "trouble":
            p.n_trouble += 1
            trouble.append(e)
        elif role == "recovery":
            p.n_recovery += 1
            recovery.append(e)

        if kind == "rank.init":
            executors[str(payload.get("executor") or "?")] += 1
            p.context_budget = int(payload.get("budget") or 0)
            p.max_incarnation = max(p.max_incarnation, int(payload.get("incarnation") or 1))
        elif kind == "rank.finalize":
            p.state = str(payload.get("state") or "unknown")
            ctx = payload.get("context") or {}
            p.context_used = int(ctx.get("used") or 0)
            p.context_budget = int(ctx.get("budget") or p.context_budget)
            p.context_high_water = int(ctx.get("high_water") or 0)
            p.context_rejections = int(ctx.get("rejections") or 0)
            p.context_evictions = int(ctx.get("evictions") or 0)
        elif kind == "msg.send":
            p.sent += 1
            tokens = int(payload.get("tokens") or 0)
            p.tokens_sent += tokens
            dst = payload.get("wdst")
            if dst is None:
                dst = payload.get("dst")
            if dst is not None:
                slot = comm[(int(rank), int(dst))]
                slot[0] += 1
                slot[1] += tokens
            if payload.get("mode") == "rendezvous":
                rendezvous += 1
                deferred += tokens
            else:
                eager += 1
        elif kind == "msg.recv":
            p.recv += 1
            p.tokens_recv += int(payload.get("tokens") or 0)
            p.tokens_admitted += int(payload.get("admitted_tokens") or 0)
        elif kind == "agent.call":
            p.n_agent_calls += 1
            if payload.get("latency_s"):
                p.latencies.append(float(payload["latency_s"]))
            if int(payload.get("attempt") or 1) > 1:
                p.n_retries += 1
        elif kind == "agent.contract_violation":
            p.n_contract_violations += 1
        elif kind == "broker.claim":
            open_claims[int(payload.get("aid", -1))] = (
                int(rank),
                ts - t0,
                str(payload.get("label") or ""),
            )
        elif kind == "broker.complete":
            claim = open_claims.pop(int(payload.get("aid", -1)), None)
            if claim is not None:
                claim_rank, start, claim_label = claim
                work_spans.append((claim_rank, start, ts - t0, claim_label))
                cp = prof(claim_rank)
                cp.busy_s += (ts - t0) - start
                cp.n_work += 1

    # A run driven through a function executor has no broker, so occupancy has to come from the
    # agent calls themselves. Without this, every non-broker run reports zero busy time and an
    # idle fraction of 1.0, which is an artefact of how the run was driven, not a property of it.
    if not work_spans:
        for e in events:
            if e["kind"] == "agent.call" and e["rank"] is not None:
                latency = float(e["payload"].get("latency_s") or 0.0)
                if latency <= 0:
                    continue
                end = e["ts"] - t0
                work_spans.append(
                    (int(e["rank"]), max(0.0, end - latency), end, str(e["payload"].get("kind_label") or ""))
                )
                p = prof(int(e["rank"]))
                p.busy_s += latency
                p.n_work += 1

    if world_size == 0:
        world_size = len(ranks)

    return Analysis(
        name=name,
        experiment=experiment,
        label=label,
        world_size=world_size,
        n_events=len(events),
        t0=t0,
        wall_s=wall_s,
        ok=ok,
        events=events,
        ranks=ranks,
        collectives=_group_collectives(events, t0),
        comm={k: (v[0], v[1]) for k, v in comm.items()},
        kind_counts=kind_counts,
        role_counts=role_counts,
        work_spans=sorted(work_spans, key=lambda s: (s[0], s[1])),
        trouble=trouble,
        recovery=recovery,
        concurrency=_concurrency(work_spans, wall_s, world_size),
        summary=cost.summarise(events),
        calibration=cost.calibrate(events),
        eager_messages=eager,
        rendezvous_messages=rendezvous,
        tokens_deferred=deferred,
        failed_ranks=failed_ranks,
        executors=executors,
    )
