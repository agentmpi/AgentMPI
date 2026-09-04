"""Quantitative analysis of one AgentMPI run, computed from its event log alone.

The event log is the only input.  Not the device, not in-memory counters, not the
harness report: just the ordered events that ``Harness.save`` writes beside every
run and that survive a rank which died.  Anything derivable about a run should be
derivable from that, and where it is not, the gap is a defect in what the runtime
records rather than a licence to consult a second source.  Two such gaps were
found by writing this module and closed in ``ampi.core.collectives``: a broadcast
emitted no event at all, and no collective recorded how long its caller had been
blocked.

What this answers, roughly in the order a reader asks:

*Did it work, and how long did it take?*  Wall time, per-rank occupancy, and the
shape of the timeline.

*Was the parallelism real?*  Achieved concurrency over time, not rank count.  A
run with sixty-four ranks whose concurrency never exceeds three is a serial run
with expensive bookkeeping, and the distinction is invisible in a summary that
reports ``size=64``.

*What did coordination cost?*  Rank-seconds blocked inside collectives, separated
from rank-seconds spent working, because for agent ranks the two differ by orders
of magnitude and conflating them is how a harness comes to look efficient while
spending its budget on protocol.

*Who was everybody waiting for?*  Straggler attribution per collective.  A skew
figure says a barrier cost four minutes; it does not say which rank owed them.
Naming the rank is the difference between a number and an action, and it is the
one thing an operator of a long run actually wants at three in the morning.

*Did the implementation do what the cost model says?*  Every invocation is costed
against the closed form for its algorithm in ``ampi.core.algorithms``.  Since
AgentMPI's collectives fold in the shared journal rather than sending
point-to-point messages, the comparison that means something is not messages sent
but critical-path operator applications and rounds --- which is the quantity the
selection argument turns on.

The deliberate omission is judgement.  Nothing here decides whether a number is
good; that requires knowing what the run was trying to show.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ampi.core.algorithms import build_schedule, cost_of

from . import style as st

__all__ = [
    "Analysis",
    "CollectiveInvocation",
    "ConcurrencyProfile",
    "RankProfile",
    "analyse",
    "load_events",
]

Event = dict[str, Any]

#: Executor turn cost used when costing a schedule whose operator is an agent.
#: Only ratios matter for algorithm ordering, and thirty seconds is the order of
#: magnitude of one turn; the measured value for a run is reported beside it.
GAMMA_S = 30.0


def load_events(path: str | Path) -> list[Event]:
    """Read a ``.trace.jsonl`` file, tolerating a truncated final line.

    A long run's trace is often read while the run is still writing it, and the
    last line is then a partial object.  Discarding it is right; refusing to read
    the file is not, because the whole point of tracing a run that may not finish
    is being able to look at it before it does.
    """
    events: list[Event] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    events.sort(key=lambda e: (e.get("ts", 0.0), e.get("seq", 0)))
    return events


# ----------------------------------------------------------------------------
# Per-rank behaviour
# ----------------------------------------------------------------------------


@dataclass
class RankProfile:
    """What one rank did, and how much of the run it was actually busy for."""

    rank: int
    #: Seconds inside a broker claim-to-submission interval.  This is occupancy as
    #: the harness can observe it, which is the only definition available for an
    #: agent it does not host.
    busy_s: float = 0.0
    #: Seconds blocked inside collectives, summed over this rank's invocations.
    blocked_s: float = 0.0
    n_tasks: int = 0
    n_collectives: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    sent: int = 0
    recv: int = 0
    tokens_sent: int = 0
    n_trouble: int = 0
    n_recovery: int = 0
    n_rejects: int = 0
    n_requeues: int = 0
    state: str = "unknown"
    #: Highest epoch seen.  A rank is a durable *role*; the agent session filling
    #: it is ephemeral.  When one exits and another attaches the epoch increments
    #: while the rank keeps its identity, mailbox and context account, so this
    #: counts how many separate sessions stood in for one participant --- the
    #: clearest direct evidence that the abstraction survives what it abstracts.
    max_epoch: int = 1
    context_used: int = 0
    context_budget: int = 0
    context_high_water: int = 0
    n_degrade: int = 0
    n_stall: int = 0
    executors: set[str] = field(default_factory=set)
    task_latencies: list[float] = field(default_factory=list)
    kinds: Counter = field(default_factory=Counter)
    #: Model-executor accounting, from ``task.done`` events.  A raw API executor
    #: reports the exact size of every prompt it was sent, so these are measured
    #: rather than estimated: the prompt *is* the executor's whole context.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    n_repairs: int = 0
    #: Collectives this rank re-entered after a restart, found already closed.
    n_replays: int = 0

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
        """Sessions that took over this rank after the first."""
        return max(0, self.max_epoch - 1)

    @property
    def latency_p50(self) -> float:
        return statistics.median(self.task_latencies) if self.task_latencies else 0.0

    @property
    def latency_max(self) -> float:
        return max(self.task_latencies) if self.task_latencies else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "busy_s": round(self.busy_s, 3),
            "blocked_s": round(self.blocked_s, 3),
            "lifetime_s": round(self.lifetime_s, 3),
            "occupancy": round(self.occupancy, 4),
            "n_tasks": self.n_tasks,
            "n_collectives": self.n_collectives,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "tool_calls": self.tool_calls,
            "n_repairs": self.n_repairs,
            "n_replays": self.n_replays,
            "sent": self.sent,
            "recv": self.recv,
            "tokens_sent": self.tokens_sent,
            "n_trouble": self.n_trouble,
            "n_recovery": self.n_recovery,
            "n_rejects": self.n_rejects,
            "n_requeues": self.n_requeues,
            "state": self.state,
            "context_used": self.context_used,
            "context_budget": self.context_budget,
            "context_occupancy": round(self.context_occupancy, 4),
            "context_high_water": self.context_high_water,
            "n_degrade": self.n_degrade,
            "n_stall": self.n_stall,
            "max_epoch": self.max_epoch,
            "reattachments": self.reattachments,
            "executors": sorted(self.executors),
            "latency_p50": round(self.latency_p50, 2),
            "latency_max": round(self.latency_max, 2),
        }


# ----------------------------------------------------------------------------
# Collectives
# ----------------------------------------------------------------------------


@dataclass
class CollectiveInvocation:
    """One logical collective: the per-rank records belonging to a single call.

    Reconstructed rather than recorded, because each rank emits its own event and
    the runtime assigns no invocation id.  Two rules identify a group: a rank
    appears at most once in an invocation, and a rank's next event with the same
    kind and label belongs to the next one.  This is exact for a harness that does
    not run two same-labelled collectives concurrently on one communicator, which
    the protocol forbids anyway --- a label identifies a collective.
    """

    op: str
    label: str
    comm: str
    size: int
    root: int | None
    participants: list[int]
    t_first: float
    t_last: float
    #: Per-rank blocking, summed over participants.  The additive measure: two
    #: ranks' seconds are different resources even when they elapse concurrently,
    #: so this may be divided by rank-seconds available to give a true share.
    rank_wait_s: float
    #: The longest single rank's wait.  A critical-path figure; never summed
    #: across invocations, because concurrent waits would then be charged twice.
    max_wait_s: float
    #: ``(rank, end_time, wait)`` per participant, so overlapping waits can be
    #: unioned instead of summed.
    intervals: list[tuple[int, float, float]] = field(default_factory=list)
    algorithm: str = ""
    rule: str = ""
    op_name: str = ""
    dropped: list[int] = field(default_factory=list)
    conflicts: int = 0
    fold_depth: int = 0
    applications: int = 0
    tokens: int = 0
    charged: int = 0
    #: Rank whose arrival released the collective, and the seconds between the
    #: first arrival and that one.  Attribution, not just a spread.
    straggler: int | None = None
    arrival_skew_s: float = 0.0
    predicted_rounds: int | None = None
    predicted_messages: int | None = None
    predicted_applications: int | None = None
    predicted_critical_path: int | None = None
    predicted_protocol_s: float | None = None

    @property
    def n_participants(self) -> int:
        return len(self.participants)

    @property
    def complete(self) -> bool:
        """Did every member of the communicator record this call?

        An incomplete collective is not a cheaper collective.  When ranks die or
        never arrive the survivors do less work than the algorithm requires, and
        comparing that against a closed form derived for the full communicator
        manufactures a model disagreement out of a run that simply did not finish.
        """
        return self.size > 0 and self.n_participants >= self.size

    @property
    def absent(self) -> list[int]:
        return sorted(set(range(self.size)) - set(self.participants)) if self.size else []

    @property
    def completion_skew_s(self) -> float:
        """Spread between the first and last rank to record the call."""
        return self.t_last - self.t_first

    def to_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "label": self.label,
            "comm": self.comm,
            "algorithm": self.algorithm,
            "operator": self.op_name,
            "size": self.size,
            "root": self.root,
            "n_participants": self.n_participants,
            "complete": self.complete,
            "absent": self.absent,
            "t_first": round(self.t_first, 3),
            "completion_skew_s": round(self.completion_skew_s, 3),
            "arrival_skew_s": round(self.arrival_skew_s, 3),
            "straggler": self.straggler,
            "rank_wait_s": round(self.rank_wait_s, 3),
            "max_wait_s": round(self.max_wait_s, 3),
            "dropped": self.dropped,
            "conflicts": self.conflicts,
            "fold_depth": self.fold_depth,
            "applications": self.applications,
            "tokens": self.tokens,
            "charged": self.charged,
            "predicted_rounds": self.predicted_rounds,
            "predicted_messages": self.predicted_messages,
            "predicted_applications": self.predicted_applications,
            "predicted_critical_path": self.predicted_critical_path,
            "predicted_protocol_s": (
                round(self.predicted_protocol_s, 6)
                if self.predicted_protocol_s is not None
                else None
            ),
        }


# ----------------------------------------------------------------------------
# Concurrency
# ----------------------------------------------------------------------------


@dataclass
class ConcurrencyProfile:
    """How many ranks were simultaneously busy, sampled over the run.

    The point of a parallel harness is overlap, and rank count does not measure
    it.  Computed from the union of busy intervals, so it reports *achieved*
    parallelism: a run whose concurrency never exceeds one is serial however many
    ranks it registered.
    """

    times: list[float]
    busy: list[int]
    max_busy: int
    mean_busy_when_active: float
    busy_union_s: float
    total_busy_s: float
    wall_s: float
    world_size: int

    @property
    def achieved_parallelism(self) -> float:
        """Total work over wall time: the speedup against doing it on one rank."""
        return self.total_busy_s / self.wall_s if self.wall_s > 0 else 0.0

    @property
    def parallel_efficiency(self) -> float:
        return self.achieved_parallelism / self.world_size if self.world_size else 0.0

    @property
    def serial_fraction_of_busy(self) -> float:
        """Fraction of active time with exactly one rank busy: Amdahl, measured."""
        active = [b for b in self.busy if b > 0]
        return (sum(1 for b in active if b == 1) / len(active)) if active else 0.0

    @property
    def idle_fraction(self) -> float:
        """Rank-seconds idle over rank-seconds available: paid for and not used."""
        available = self.world_size * self.wall_s
        return 1.0 - (self.total_busy_s / available) if available > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
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


# ----------------------------------------------------------------------------
# Phases
# ----------------------------------------------------------------------------


@dataclass
class Phase:
    """A named stretch of the run, delimited by the harness's own memos.

    ``memo`` is the one event a harness author writes deliberately, so it is the
    only place the trace learns what the harness thought it was doing.  Segmenting
    by it means the breakdown reflects the program's structure rather than a
    guess made by the tool from event kinds.
    """

    name: str
    t_start: float
    t_end: float
    ranks: set[int] = field(default_factory=set)

    @property
    def duration_s(self) -> float:
        return max(0.0, self.t_end - self.t_start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "t_start": round(self.t_start, 3),
            "t_end": round(self.t_end, 3),
            "duration_s": round(self.duration_s, 3),
            "ranks": len(self.ranks),
        }


# ----------------------------------------------------------------------------
# The whole run
# ----------------------------------------------------------------------------


@dataclass
class Analysis:
    name: str
    job: str
    world_size: int
    n_events: int
    t0: float
    wall_s: float
    events: list[Event]
    ranks: dict[int, RankProfile]
    collectives: list[CollectiveInvocation]
    phases: list[Phase]
    concurrency: ConcurrencyProfile
    kind_counts: Counter
    role_counts: Counter
    work_spans: list[tuple[int, float, float, str]]
    comm: dict[tuple[int, int], tuple[int, int]]
    trouble: list[Event]
    recovery: list[Event]
    tasks: dict[str, int] = field(default_factory=dict)
    executors: Counter = field(default_factory=Counter)
    meta: dict[str, Any] = field(default_factory=dict)

    # -- population ---------------------------------------------------------
    @property
    def n_ranks_seen(self) -> int:
        return len(self.ranks)

    @property
    def participating_ranks(self) -> list[int]:
        return [r for r in sorted(self.ranks) if r >= 0]

    @property
    def inert_ranks(self) -> list[int]:
        """Ranks that registered and then did nothing observable.

        Worth reporting rather than smoothing over.  A run whose rank count meets
        its world size but whose ranks never worked is not a run with parallelism,
        it is a run with a bookkeeping leak, and the two are identical in any
        summary that reports only a count.
        """
        return [
            r
            for r, p in sorted(self.ranks.items())
            if r >= 0 and p.n_tasks == 0 and p.n_collectives == 0
        ]

    @property
    def total_reattachments(self) -> int:
        return sum(p.reattachments for p in self.ranks.values())

    # -- coordination -------------------------------------------------------
    @property
    def collective_rank_seconds(self) -> float:
        return sum(c.rank_wait_s for c in self.collectives)

    @property
    def coordination_share(self) -> float:
        """Rank-seconds blocked in *completed* collectives over rank-seconds available.

        Defined against ``world_size * wall_s`` rather than against wall time
        alone, so it is a proportion and cannot exceed one.  Read it beside
        :attr:`coordination_is_underreported`: a collective is recorded on
        completion, so a rank that blocked and then died contributes nothing here
        and the figure understates a degraded run --- correctly by its definition,
        and misleadingly for the question a reader is asking.
        """
        available = self.world_size * self.wall_s
        return self.collective_rank_seconds / available if available > 0 else 0.0

    @property
    def collective_span_s(self) -> float:
        """Wall seconds with coordination in flight anywhere, unioned not summed."""
        intervals = [
            (max(0.0, end - wait), end)
            for c in self.collectives
            for _rank, end, wait in c.intervals
            if wait > 0
        ]
        return _union_length(intervals)

    @property
    def coordination_span_share(self) -> float:
        return self.collective_span_s / self.wall_s if self.wall_s > 0 else 0.0

    @property
    def incomplete_collectives(self) -> list[CollectiveInvocation]:
        return [c for c in self.collectives if not c.complete]

    @property
    def coordination_is_underreported(self) -> bool:
        return bool(self.incomplete_collectives) or bool(self.failed_ranks)

    @property
    def work_rank_seconds(self) -> float:
        return sum(p.busy_s for p in self.ranks.values())

    @property
    def imbalance(self) -> float:
        """Slowest rank's busy time over the mean, across *all* participating ranks.

        Ranks that did no work are included, which is the point: filtering to
        ``busy_s > 0`` makes a flat reduction where one rank did everything and the
        rest idled report perfect balance, when it is the most imbalanced
        arrangement available.
        """
        ranks = self.participating_ranks
        if not ranks:
            return 0.0
        busy = [self.ranks[r].busy_s for r in ranks]
        mean = sum(busy) / len(busy)
        return max(busy) / mean if mean > 0 else 0.0

    # -- stragglers ---------------------------------------------------------
    @property
    def straggler_cost(self) -> dict[int, float]:
        """Rank-seconds its peers spent waiting, charged to the rank that arrived last.

        The quantity a skew figure cannot give: not how long a collective took,
        but who owed it.  A rank appearing here repeatedly is the one to
        investigate, and on an oversubscribed run it usually identifies an
        executor serving too many ranks rather than a slow model.
        """
        owed: dict[int, float] = defaultdict(float)
        for c in self.collectives:
            if c.straggler is not None and c.arrival_skew_s > 0:
                owed[c.straggler] += c.rank_wait_s
        return dict(sorted(owed.items(), key=lambda kv: -kv[1]))

    # -- model agreement -----------------------------------------------------
    @property
    def costed_collectives(self) -> list[CollectiveInvocation]:
        return [c for c in self.collectives if c.predicted_rounds is not None and c.complete]

    @property
    def failed_ranks(self) -> list[int]:
        return sorted({int(e["rank"]) for e in self.trouble if e["kind"] == "rank.error"})

    @property
    def degraded(self) -> bool:
        return bool(self.failed_ranks) or bool(self.incomplete_collectives)

    @property
    def conflicts_lifted(self) -> int:
        return sum(c.conflicts for c in self.collectives)

    @property
    def max_claim_wait_s(self) -> float:
        """Longest a task waited in the queue before an executor claimed it.

        Its own measure because it separates two failures that a wall-clock
        summary cannot: a run that was *slow*, and a run where nobody ever showed
        up.  The second is a pool-sizing problem outside the protocol, and reading
        it as the first blames the harness for the host's session limits.
        """
        published: dict[str, float] = {}
        waits: list[float] = []
        for e in self.events:
            aid = e.get("aid")
            if not aid:
                continue
            if e["kind"] == "broker.publish":
                published[str(aid)] = e["ts"]
            elif e["kind"] == "broker.claim":
                start = published.pop(str(aid), None)
                if start is not None:
                    waits.append(e["ts"] - start)
        return max(waits) if waits else 0.0

    @property
    def starved_tasks(self) -> list[dict[str, Any]]:
        """Tasks published that no executor ever claimed.

        The characteristic failure of running a long job against a host whose
        session lifetime is far shorter than the job's.  It is invisible in a task
        count --- a task nobody claimed and a task still being worked on are both
        simply "not done" --- and it is the difference between a harness that is
        wrong and a population that was never fully staffed.
        """
        claimed = {str(e.get("aid")) for e in self.events if e["kind"] == "broker.claim"}
        return [
            {"aid": str(e.get("aid")), "rank": int(e.get("rank", -1)),
             "label": str(e.get("label") or "")}
            for e in self.events
            if e["kind"] == "broker.publish" and str(e.get("aid")) not in claimed
        ]

    @property
    def wasted_submissions(self) -> list[dict[str, Any]]:
        """Results submitted after the rank that needed them had already failed.

        The signature of a deadline set without knowing the executor supply.  A
        harness that times out a task is declaring that waiting longer is worse
        than proceeding without the answer; when the answer then arrives anyway,
        the declaration was wrong and the work is thrown away.

        It is worth measuring separately from either failure or slowness, because
        it is the only one of the three that says the population was *capable* of
        finishing and the configuration prevented it.

        Read it as a *lower bound*.  The broker accepts a submission whether or not
        anybody is still waiting for it, so results keep arriving after the harness
        has exited and this count grows every time the log is re-read --- on the
        p=16 production run it went five, six, seven over the hours after the run
        was declared failed.  There is no instant at which a final figure can
        honestly be taken, which is why a trace should be sealed at a stated time
        and the count reported against that seal.
        """
        failed_at: dict[int, float] = {}
        for e in self.events:
            if e["kind"] == "rank.error":
                rank = int(e.get("rank", -1))
                failed_at.setdefault(rank, e["ts"])
        out = []
        for e in self.events:
            if e["kind"] not in ("broker.submit", "task.done"):
                continue
            rank = int(e.get("rank", -1))
            when = failed_at.get(rank)
            if when is not None and e["ts"] > when:
                out.append({
                    "rank": rank,
                    "label": str(e.get("label") or ""),
                    "late_by_s": round(e["ts"] - when, 1),
                })
        return out

    @property
    def has_broker(self) -> bool:
        """Was any rank driven through the broker, so occupancy is observable?

        Reported because a run driven by an in-process function executor emits no
        claim/submit pair, so its busy time is reconstructed differently and any
        concurrency statement derived from it describes the instrumentation rather
        than the run.
        """
        return self.kind_counts.get("broker.claim", 0) > 0

    @property
    def has_work_spans(self) -> bool:
        """Is occupancy observable at all: a broker claim/submit pair, or a model
        executor's ``task.start``/``task.done`` pair, both of which bound the
        interval a rank spent inside its executor."""
        return self.has_broker or self.kind_counts.get("task.start", 0) > 0

    @property
    def total_cost_usd(self) -> float:
        return sum(p.cost_usd for p in self.ranks.values())

    @property
    def total_prompt_tokens(self) -> int:
        return sum(p.prompt_tokens for p in self.ranks.values())

    @property
    def total_completion_tokens(self) -> int:
        return sum(p.completion_tokens for p in self.ranks.values())

    @property
    def total_reasoning_tokens(self) -> int:
        return sum(p.reasoning_tokens for p in self.ranks.values())

    @property
    def total_tool_calls(self) -> int:
        return sum(p.tool_calls for p in self.ranks.values())

    @property
    def total_repairs(self) -> int:
        return sum(p.n_repairs for p in self.ranks.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "has_work_spans": self.has_work_spans,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_reasoning_tokens": self.total_reasoning_tokens,
            "total_tool_calls": self.total_tool_calls,
            "total_repairs": self.total_repairs,
            "job": self.job,
            "world_size": self.world_size,
            "n_ranks_seen": self.n_ranks_seen,
            "inert_ranks": self.inert_ranks,
            "n_events": self.n_events,
            "wall_s": round(self.wall_s, 3),
            "degraded": self.degraded,
            "failed_ranks": self.failed_ranks,
            "has_broker": self.has_broker,
            "imbalance": round(self.imbalance, 3),
            "work_rank_seconds": round(self.work_rank_seconds, 3),
            "collective_rank_seconds": round(self.collective_rank_seconds, 3),
            "collective_span_s": round(self.collective_span_s, 3),
            "coordination_share": round(self.coordination_share, 4),
            "coordination_span_share": round(self.coordination_span_share, 4),
            "coordination_is_underreported": self.coordination_is_underreported,
            "n_collectives": len(self.collectives),
            "n_incomplete_collectives": len(self.incomplete_collectives),
            "conflicts_lifted": self.conflicts_lifted,
            "max_claim_wait_s": round(self.max_claim_wait_s, 1),
            "starved_tasks": self.starved_tasks,
            "wasted_submissions": self.wasted_submissions,
            "total_reattachments": self.total_reattachments,
            "n_trouble": len(self.trouble),
            "n_recovery": len(self.recovery),
            "straggler_cost": {str(k): round(v, 2) for k, v in self.straggler_cost.items()},
            "tasks": self.tasks,
            "executors": dict(self.executors),
            "meta": self.meta,
            "concurrency": self.concurrency.to_dict(),
            "phases": [p.to_dict() for p in self.phases],
            "ranks": [self.ranks[r].to_dict() for r in self.participating_ranks],
            "collectives": [c.to_dict() for c in self.collectives],
            "kind_counts": dict(sorted(self.kind_counts.items())),
            "role_counts": dict(sorted(self.role_counts.items())),
            "comm": {
                f"{s}->{d}": {"n": n, "tokens": t} for (s, d), (n, t) in sorted(self.comm.items())
            },
        }


# ----------------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------------


def _union_length(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    intervals = sorted(intervals)
    total = 0.0
    start, end = intervals[0]
    for s, e in intervals[1:]:
        if s > end:
            total += end - start
            start, end = s, e
        else:
            end = max(end, e)
    return total + (end - start)


def _concurrency(
    spans: list[tuple[int, float, float, str]],
    wall_s: float,
    world_size: int,
    samples: int = 800,
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
    return ConcurrencyProfile(
        times=times,
        busy=busy,
        max_busy=max(busy),
        mean_busy_when_active=(sum(active) / len(active)) if active else 0.0,
        busy_union_s=len(active) * step,
        total_busy_s=total_busy,
        wall_s=wall_s,
        world_size=world_size,
    )


#: Which schedule to cost each collective against when the trace does not name an
#: algorithm.  AgentMPI folds in the shared journal, so the honest default for a
#: runtime operator is the flat schedule; a reduction that chose a tree records
#: ``algorithm`` and is costed against what it chose.
DEFAULT_ALGORITHM: dict[str, str] = {
    "barrier": "central",
    "bcast": "flat",
    "scatter": "flat",
    "gather": "flat",
    "allgather": "flat",
    "alltoall": "flat",
    "reduce": "flat",
    "allreduce": "flat",
    "scan": "chain",
    "exscan": "chain",
    "neighbor_allgather": "flat",
}


def _cost_invocation(inv: CollectiveInvocation) -> None:
    """Attach the closed-form prediction for this invocation's schedule.

    A formula that cannot evaluate at this ``p`` is a gap in the model, not a
    reason to abandon the analysis, so failure leaves the prediction unset and the
    report counts the invocation as unchecked rather than as disagreeing.
    """
    algorithm = inv.algorithm or DEFAULT_ALGORITHM.get(inv.op, "")
    if not algorithm or inv.size < 1:
        return
    try:
        sched = build_schedule(
            inv.op, algorithm, inv.size, root=inv.root or 0, tokens=inv.tokens, inline=False
        )
        cost = cost_of(sched, gamma_s=0.0)
    except Exception:
        return
    inv.predicted_rounds = sched.n_rounds
    inv.predicted_messages = sched.n_messages
    inv.predicted_applications = sched.applications
    inv.predicted_critical_path = sched.critical_path_applications
    inv.predicted_protocol_s = cost.protocol_seconds


def _group_collectives(events: list[Event], t0: float, joins: dict[tuple[str, str, int], list[float]]) -> list[CollectiveInvocation]:
    # Two communicators may legally run the same kind under the same label; the
    # context keeps their completions apart.
    by_key: dict[tuple[str, str, str], list[Event]] = defaultdict(list)
    for e in events:
        if st.is_collective(e["kind"]) and not e.get("replayed"):
            by_key[(e["kind"], str(e.get("label") or ""), str(e.get("comm") or ""))].append(e)

    out: list[CollectiveInvocation] = []
    for (op, label, _comm), group in by_key.items():
        group.sort(key=lambda e: (e["ts"], e.get("seq", 0)))
        current: list[Event] = []
        seen: set[int] = set()
        for e in group:
            rank = int(e.get("rank", -1))
            if rank in seen:
                out.append(_build(op, label, current, t0, joins))
                current, seen = [], set()
            current.append(e)
            seen.add(rank)
        if current:
            out.append(_build(op, label, current, t0, joins))

    out.sort(key=lambda c: c.t_first)
    return out


def _build(
    op: str,
    label: str,
    group: list[Event],
    t0: float,
    joins: dict[tuple[str, str, int], list[float]],
) -> CollectiveInvocation:
    waits = [float(e.get("waited_s") or 0.0) for e in group]
    sizes = [int(e["size"]) for e in group if e.get("size")]
    roots = {int(e["root"]) for e in group if e.get("root") is not None}
    comms = {str(e.get("comm") or "world") for e in group}
    algorithms = Counter(str(e["algorithm"]) for e in group if e.get("algorithm"))
    ops = Counter(str(e["op"]) for e in group if e.get("op"))
    dropped: set[int] = set()
    for e in group:
        for r in e.get("dropped") or []:
            dropped.add(int(r))

    inv = CollectiveInvocation(
        op=op,
        label=label,
        comm=sorted(comms)[0] if comms else "world",
        size=max(sizes) if sizes else len(group),
        root=sorted(roots)[0] if roots else None,
        participants=sorted(int(e.get("rank", -1)) for e in group),
        t_first=group[0]["ts"] - t0,
        t_last=group[-1]["ts"] - t0,
        rank_wait_s=sum(waits),
        max_wait_s=max(waits) if waits else 0.0,
        intervals=[
            (int(e.get("rank", -1)), e["ts"] - t0, float(e.get("waited_s") or 0.0)) for e in group
        ],
        algorithm=algorithms.most_common(1)[0][0] if algorithms else "",
        rule=next((str(e["rule"]) for e in group if e.get("rule")), ""),
        op_name=ops.most_common(1)[0][0] if ops else "",
        dropped=sorted(dropped),
        conflicts=max((int(e.get("conflicts") or 0) for e in group), default=0),
        fold_depth=max((int(e.get("depth") or 0) for e in group), default=0),
        applications=max((int(e.get("applications") or 0) for e in group), default=0),
        tokens=max((int(e.get("tokens") or 0) for e in group), default=0),
        charged=sum(int(e.get("charged") or 0) for e in group),
    )

    # Straggler attribution.  A rank's arrival is its `coll.join`; the collective
    # releases when the last member has joined, so the last joiner is the rank the
    # others were waiting for, and the spread between first and last arrival is
    # what it cost them.  Falling back to completion order would name whichever
    # rank happened to poll last, which is an artefact of the poll schedule rather
    # than a fact about the population.
    arrivals = [
        (t, rank)
        for rank in inv.participants
        for t in joins.get((inv.comm, label, rank), [])
        if t - t0 <= inv.t_last + 1e-9
    ]
    if len(arrivals) >= 2:
        arrivals.sort()
        inv.arrival_skew_s = arrivals[-1][0] - arrivals[0][0]
        inv.straggler = arrivals[-1][1]

    _cost_invocation(inv)
    return inv


def analyse(events: list[Event], *, name: str = "", meta: dict[str, Any] | None = None) -> Analysis:
    """Compute everything derivable about a run from its event log."""
    if not events:
        raise ValueError("cannot analyse an empty event log")

    events = sorted(events, key=lambda e: (e.get("ts", 0.0), e.get("seq", 0)))
    t0 = events[0]["ts"]
    wall_s = max(0.0, events[-1]["ts"] - t0)

    ranks: dict[int, RankProfile] = {}
    comm: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])
    kind_counts: Counter = Counter()
    role_counts: Counter = Counter()
    work_spans: list[tuple[int, float, float, str]] = []
    trouble: list[Event] = []
    recovery: list[Event] = []
    open_claims: dict[str, tuple[int, float, str]] = {}
    joins: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    phase_marks: list[tuple[float, int, str]] = []
    tasks: Counter = Counter()
    executors: Counter = Counter()
    world_size = 0
    job = ""

    def prof(rank: int) -> RankProfile:
        p = ranks.get(rank)
        if p is None:
            p = ranks[rank] = RankProfile(rank=rank)
        return p

    for e in events:
        kind = e["kind"]
        ts = e["ts"]
        rank = int(e.get("rank", -1))
        kind_counts[kind] += 1
        role = st.role_of(kind)
        role_counts[role] += 1

        if kind == "job.create":
            world_size = int(e.get("size") or 0)
            job = str(e.get("job_id") or e.get("run") or "")

        if kind == "coll.join":
            joins[(str(e.get("comm") or "world"), str(e.get("label") or ""), rank)].append(ts)

        if rank < 0:
            continue
        p = prof(rank)
        p.kinds[kind] += 1
        p.first_ts = ts if p.first_ts is None else min(p.first_ts, ts)
        p.last_ts = ts if p.last_ts is None else max(p.last_ts, ts)

        if role == "trouble":
            p.n_trouble += 1
            trouble.append(e)
        elif role == "recovery":
            p.n_recovery += 1
            recovery.append(e)

        if kind == "init":
            p.max_epoch = max(p.max_epoch, int(e.get("epoch") or 1))
        elif kind == "finalize":
            p.state = str(e.get("state") or "finalized")
            p.context_used = int(e.get("used") or p.context_used)
            p.context_budget = int(e.get("budget") or p.context_budget)
            p.context_high_water = int(e.get("high_water") or p.context_high_water)
        elif kind == "memo":
            phase_marks.append((ts - t0, rank, str(e.get("note") or e.get("topic") or "")))
        elif kind == "send":
            p.sent += 1
            tokens = int(e.get("tokens") or 0)
            p.tokens_sent += tokens
            if e.get("dst") is not None:
                slot = comm[(rank, int(e["dst"]))]
                slot[0] += 1
                slot[1] += tokens
        elif kind == "recv":
            p.recv += 1
        elif kind == "ctx.degrade":
            p.n_degrade += 1
        elif kind == "ctx.stall":
            p.n_stall += 1
        elif kind == "broker.publish":
            tasks["published"] += 1
        elif kind == "broker.claim":
            tasks["claimed"] += 1
            aid = str(e.get("aid") or "")
            open_claims[aid] = (rank, ts - t0, str(e.get("label") or ""))
            if e.get("worker"):
                p.executors.add(str(e["worker"]))
                executors[str(e["worker"])] += 1
        elif kind == "broker.submit":
            tasks["submitted"] += 1
            claim = open_claims.pop(str(e.get("aid") or ""), None)
            if claim is not None:
                crank, start, clabel = claim
                end = ts - t0
                work_spans.append((crank, start, end, clabel))
                cp = prof(crank)
                cp.busy_s += end - start
                cp.n_tasks += 1
                cp.task_latencies.append(end - start)
        elif kind == "broker.reject":
            tasks["rejected"] += 1
            p.n_rejects += 1
        elif kind == "broker.requeue":
            tasks["requeued"] += 1
            p.n_requeues += 1
        elif kind == "broker.giveup":
            tasks["abandoned"] += 1
        # A model executor is claim and submit in one process: the start/done pair
        # bounds the same interval the broker's claim/submit pair does, and is
        # counted identically so a run staffed by raw API processes and a run
        # staffed by agent sessions are measured with one ruler.
        elif kind == "task.start":
            tasks["published"] += 1
            tasks["claimed"] += 1
            aid = str(e.get("aid") or "")
            open_claims[aid] = (rank, ts - t0, str(e.get("label") or ""))
            if e.get("worker"):
                p.executors.add(str(e["worker"]))
                executors[str(e["worker"])] += 1
        elif kind == "task.done":
            tasks["submitted"] += 1
            claim = open_claims.pop(str(e.get("aid") or ""), None)
            if claim is not None:
                crank, start, clabel = claim
                end = ts - t0
                work_spans.append((crank, start, end, clabel))
                cp = prof(crank)
                cp.busy_s += end - start
                cp.n_tasks += 1
                cp.task_latencies.append(end - start)
            p.prompt_tokens += int(e.get("prompt_tokens") or 0)
            p.completion_tokens += int(e.get("completion_tokens") or 0)
            p.reasoning_tokens += int(e.get("reasoning_tokens") or 0)
            p.cost_usd += float(e.get("cost_usd") or 0.0)
            p.tool_calls += int(e.get("tool_calls") or 0)
        elif kind == "task.retry":
            tasks["rejected"] += 1
            p.n_rejects += 1
            p.n_repairs += 1
        elif kind == "task.fail":
            tasks["abandoned"] += 1
            open_claims.pop(str(e.get("aid") or ""), None)
            p.cost_usd += float(e.get("cost_usd") or 0.0)
            p.prompt_tokens += int(e.get("prompt_tokens") or 0)
            p.completion_tokens += int(e.get("completion_tokens") or 0)

        if st.is_collective(kind):
            if e.get("replayed"):
                # A restarted rank re-entering a closed collective: not blocked,
                # not a participant a second time.
                p.n_replays += 1
                continue
            p.n_collectives += 1
            p.blocked_s += float(e.get("waited_s") or 0.0)

    if world_size == 0:
        world_size = len([r for r in ranks if r >= 0])

    collectives = _group_collectives(events, t0, joins)
    phases = _phases(phase_marks, wall_s)

    return Analysis(
        name=name,
        job=job,
        world_size=world_size,
        n_events=len(events),
        t0=t0,
        wall_s=wall_s,
        events=events,
        ranks=ranks,
        collectives=collectives,
        phases=phases,
        concurrency=_concurrency(work_spans, wall_s, world_size),
        kind_counts=kind_counts,
        role_counts=role_counts,
        work_spans=sorted(work_spans, key=lambda s: (s[0], s[1])),
        comm={k: (v[0], v[1]) for k, v in comm.items()},
        trouble=trouble,
        recovery=recovery,
        tasks=dict(tasks),
        executors=executors,
        meta=meta or {},
    )


def _phases(marks: list[tuple[float, int, str]], wall_s: float) -> list[Phase]:
    """Segment the run by the harness's memos, in the order they first appear.

    A phase begins when the first rank announces it and ends when the last rank
    announces the next one, so phases overlap at their boundaries exactly as much
    as the population does.  Pretending they are disjoint would hide the very
    thing the segmentation is for: a phase whose tail overlaps the next one's head
    is a population that has not synchronised, which is usually deliberate and
    always worth seeing.
    """
    if not marks:
        return []
    marks.sort()
    order: list[str] = []
    spans: dict[str, tuple[float, float, set[int]]] = {}
    for t, rank, note in marks:
        if not note:
            continue
        if note not in spans:
            order.append(note)
            spans[note] = (t, t, {rank})
        else:
            start, end, ranks = spans[note]
            spans[note] = (start, max(end, t), ranks | {rank})

    out: list[Phase] = []
    for i, note in enumerate(order):
        start, end, ranks = spans[note]
        nxt = spans[order[i + 1]][0] if i + 1 < len(order) else wall_s
        out.append(Phase(name=note, t_start=start, t_end=max(end, nxt), ranks=ranks))
    return out
