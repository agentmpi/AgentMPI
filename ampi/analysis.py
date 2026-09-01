"""Trace-only analysis for the flat event schema emitted by :mod:`ampi`.

The module intentionally uses only the standard library.  Plotting is kept in
``scripts/plot_run.py`` so traces remain analysable without matplotlib.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .trace_style import role_of

Event = dict[str, Any]
COLLECTIVE_KINDS = {
    "allgather",
    "allreduce",
    "alltoall",
    "barrier",
    "bcast",
    "exscan",
    "gather",
    "neighbor_allgather",
    "neighbor_alltoall",
    "reduce",
    "scan",
    "scatter",
}
TERMINAL_KINDS = {"finalize", "failure.convict", "failure.kill", "rank.error"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_events(path: str | Path) -> list[Event]:
    """Load and validate a JSON-lines trace, sorted by trace sequence."""
    trace_path = Path(path)
    events: list[Event] = []
    with trace_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{trace_path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"{trace_path}:{line_number}: event must be a JSON object")
            if not isinstance(event.get("kind"), str) or not event["kind"]:
                raise ValueError(f"{trace_path}:{line_number}: event has no string kind")
            if not isinstance(event.get("ts"), (int, float)):
                raise ValueError(f"{trace_path}:{line_number}: event has no numeric ts")
            events.append(event)
    events.sort(key=lambda event: (_integer(event.get("seq"), 2**63 - 1), event["ts"]))
    return events


@dataclass
class BrokerSpan:
    """One broker task reconstructed by ``aid`` from any available phase events."""

    aid: str
    rank: int | None = None
    label: str = ""
    worker: str = ""
    published_at: float | None = None
    claimed_at: float | None = None
    submitted_at: float | None = None
    outcome: str = "open"
    output_tokens: int = 0

    @property
    def queue_s(self) -> float | None:
        if self.published_at is None or self.claimed_at is None:
            return None
        return max(0.0, self.claimed_at - self.published_at)

    @property
    def busy_s(self) -> float | None:
        if self.claimed_at is None or self.submitted_at is None:
            return None
        return max(0.0, self.submitted_at - self.claimed_at)

    @property
    def wall_s(self) -> float | None:
        if self.published_at is None or self.submitted_at is None:
            return None
        return max(0.0, self.submitted_at - self.published_at)

    def as_dict(self, t0: float) -> dict[str, Any]:
        def relative(value: float | None) -> float | None:
            return round(value - t0, 6) if value is not None else None

        return {
            "aid": self.aid,
            "rank": self.rank,
            "label": self.label,
            "worker": self.worker,
            "published_at_s": relative(self.published_at),
            "claimed_at_s": relative(self.claimed_at),
            "submitted_at_s": relative(self.submitted_at),
            "queue_s": _rounded(self.queue_s),
            "busy_s": _rounded(self.busy_s),
            "wall_s": _rounded(self.wall_s),
            "outcome": self.outcome,
            "output_tokens": self.output_tokens,
        }


@dataclass
class RankProfile:
    rank: int
    first_at: float | None = None
    last_at: float | None = None
    busy_s: float = 0.0
    tasks: int = 0
    published: int = 0
    claims: int = 0
    submits: int = 0
    sent: int = 0
    received: int = 0
    tokens_sent: int = 0
    tokens_received: int = 0
    context_charged: int = 0
    context_released: int = 0
    context_degrades: int = 0
    context_stalls: int = 0
    context_stall_s: float = 0.0
    epochs: set[int] = field(default_factory=set)
    executors: set[str] = field(default_factory=set)
    state: str = "unknown"
    faults: int = 0
    recoveries: int = 0
    kind_counts: Counter[str] = field(default_factory=Counter)

    @property
    def lifetime_s(self) -> float:
        if self.first_at is None or self.last_at is None:
            return 0.0
        return max(0.0, self.last_at - self.first_at)

    @property
    def occupancy(self) -> float:
        if self.lifetime_s <= 0:
            return 0.0
        return min(1.0, self.busy_s / self.lifetime_s)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "state": self.state,
            "lifetime_s": round(self.lifetime_s, 6),
            "busy_s": round(self.busy_s, 6),
            "occupancy": round(self.occupancy, 6),
            "tasks": self.tasks,
            "published": self.published,
            "claims": self.claims,
            "submits": self.submits,
            "sent": self.sent,
            "received": self.received,
            "tokens_sent": self.tokens_sent,
            "tokens_received": self.tokens_received,
            "context_charged": self.context_charged,
            "context_released": self.context_released,
            "context_degrades": self.context_degrades,
            "context_stalls": self.context_stalls,
            "context_stall_s": round(self.context_stall_s, 6),
            "epochs": sorted(self.epochs),
            "reattachments": max(0, len(self.epochs) - 1),
            "executors": sorted(self.executors),
            "faults": self.faults,
            "recoveries": self.recoveries,
            "kind_counts": dict(sorted(self.kind_counts.items())),
        }


@dataclass
class CollectiveInvocation:
    label: str
    kind: str
    comm: str
    index: int
    first_at: float
    last_at: float
    participants: set[int] = field(default_factory=set)
    completion_ranks: set[int] = field(default_factory=set)
    input_tokens: int = 0
    algorithms: set[str] = field(default_factory=set)
    dropped: set[int] = field(default_factory=set)

    def as_dict(self, t0: float, world_size: int) -> dict[str, Any]:
        expected = world_size if self.comm == "world" else None
        return {
            "label": self.label,
            "kind": self.kind,
            "comm": self.comm,
            "index": self.index,
            "first_at_s": round(self.first_at - t0, 6),
            "last_at_s": round(self.last_at - t0, 6),
            "join_span_s": round(max(0.0, self.last_at - self.first_at), 6),
            "participants": sorted(self.participants),
            "participant_count": len(self.participants),
            "expected_participants": expected,
            "complete": expected is None or len(self.participants | self.dropped) >= expected,
            "completion_ranks": sorted(self.completion_ranks),
            "input_tokens": self.input_tokens,
            "algorithms": sorted(self.algorithms),
            "dropped": sorted(self.dropped),
        }


@dataclass
class ConcurrencyProfile:
    times: list[float]
    busy: list[int]
    max_busy: int
    total_busy_s: float
    active_s: float
    wall_s: float
    world_size: int

    @property
    def mean_busy_when_active(self) -> float:
        return self.total_busy_s / self.active_s if self.active_s else 0.0

    @property
    def achieved_parallelism(self) -> float:
        return self.total_busy_s / self.wall_s if self.wall_s else 0.0

    @property
    def parallel_efficiency(self) -> float:
        return self.achieved_parallelism / self.world_size if self.world_size else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "times_s": [round(value, 6) for value in self.times],
            "busy": self.busy,
            "max_busy": self.max_busy,
            "total_busy_s": round(self.total_busy_s, 6),
            "active_s": round(self.active_s, 6),
            "mean_busy_when_active": round(self.mean_busy_when_active, 6),
            "achieved_parallelism": round(self.achieved_parallelism, 6),
            "parallel_efficiency": round(self.parallel_efficiency, 6),
        }


@dataclass
class Analysis:
    events: list[Event]
    source: str
    run: str
    world_size: int
    t0: float
    wall_s: float
    ranks: dict[int, RankProfile]
    broker_spans: list[BrokerSpan]
    work_spans: list[tuple[int, float, float, str]]
    collectives: list[CollectiveInvocation]
    concurrency: ConcurrencyProfile
    kind_counts: Counter[str]
    role_counts: Counter[str]
    lifecycle: dict[str, Any]
    context: dict[str, Any]
    rma: dict[str, Any]
    diversity: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        collective_rows = [
            invocation.as_dict(self.t0, self.world_size) for invocation in self.collectives
        ]
        return {
            "source": self.source,
            "run": self.run,
            "world_size": self.world_size,
            "ranks_seen": len(self.ranks),
            "event_count": len(self.events),
            "t0": self.t0,
            "wall_s": round(self.wall_s, 6),
            "kind_counts": dict(sorted(self.kind_counts.items())),
            "role_counts": dict(sorted(self.role_counts.items())),
            "ranks": [
                profile.as_dict()
                for profile in sorted(self.ranks.values(), key=lambda profile: profile.rank)
            ],
            "broker": {
                "task_count": len(self.broker_spans),
                "complete_spans": sum(span.busy_s is not None for span in self.broker_spans),
                "unresolved_claims": sum(
                    span.claimed_at is not None and span.submitted_at is None
                    for span in self.broker_spans
                ),
                "orphan_submits": sum(
                    span.submitted_at is not None and span.claimed_at is None
                    for span in self.broker_spans
                ),
                "spans": [span.as_dict(self.t0) for span in self.broker_spans],
            },
            "concurrency": self.concurrency.as_dict(),
            "collectives": {
                "invocation_count": len(collective_rows),
                "by_label_kind": _collective_summary(collective_rows),
                "invocations": collective_rows,
            },
            "lifecycle": self.lifecycle,
            "context": self.context,
            "rma": self.rma,
            "diversity": self.diversity,
        }


def _rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _rank_of(event: Event) -> int | None:
    rank = _integer(event.get("rank"), -1)
    return rank if rank >= 0 else None


def _broker_spans(events: Iterable[Event]) -> list[BrokerSpan]:
    spans: dict[str, BrokerSpan] = {}
    order: list[str] = []
    for event in events:
        if event["kind"] not in {
            "broker.publish",
            "broker.claim",
            "broker.submit",
            "broker.giveup",
            "broker.reject",
            "broker.requeue",
        }:
            continue
        aid = str(event.get("aid") or "")
        if not aid:
            continue
        if aid not in spans:
            spans[aid] = BrokerSpan(aid=aid)
            order.append(aid)
        span = spans[aid]
        rank = _rank_of(event)
        span.rank = rank if rank is not None else span.rank
        span.label = str(event.get("label") or span.label)
        kind = event["kind"]
        if kind == "broker.publish":
            span.published_at = _number(event["ts"])
            span.outcome = "published"
        elif kind == "broker.claim":
            span.claimed_at = _number(event["ts"])
            span.worker = str(event.get("worker") or span.worker)
            span.outcome = "claimed"
        elif kind == "broker.submit":
            span.submitted_at = _number(event["ts"])
            span.output_tokens = _integer(event.get("tokens"))
            span.outcome = "submitted"
        elif kind == "broker.giveup":
            span.outcome = "gave_up"
        elif kind == "broker.reject":
            span.outcome = "rejected"
        elif kind == "broker.requeue":
            span.outcome = "requeued"
    return [spans[aid] for aid in order]


def _collective_invocations(events: list[Event]) -> list[CollectiveInvocation]:
    active: dict[tuple[str, str, str], CollectiveInvocation] = {}
    invocations: list[CollectiveInvocation] = []
    counts: Counter[tuple[str, str, str]] = Counter()
    for event in events:
        if event["kind"] != "coll.join":
            continue
        rank = _rank_of(event)
        if rank is None:
            continue
        label = str(event.get("label") or "")
        kind = str(event.get("arg_kind") or event.get("collective_kind") or "unknown")
        comm = str(event.get("comm") or "world")
        key = (label, kind, comm)
        invocation = active.get(key)
        if invocation is None or rank in invocation.participants:
            counts[key] += 1
            invocation = CollectiveInvocation(
                label=label,
                kind=kind,
                comm=comm,
                index=counts[key],
                first_at=_number(event["ts"]),
                last_at=_number(event["ts"]),
            )
            active[key] = invocation
            invocations.append(invocation)
        invocation.participants.add(rank)
        invocation.input_tokens += _integer(event.get("tokens"))
        invocation.last_at = max(invocation.last_at, _number(event["ts"]))

    by_key: dict[tuple[str, str], list[CollectiveInvocation]] = defaultdict(list)
    for invocation in invocations:
        by_key[(invocation.label, invocation.kind)].append(invocation)
    for event in events:
        kind = event["kind"]
        completion_kind = kind if kind in COLLECTIVE_KINDS else ""
        if kind in {"coll.dropped", "barrier.proceed"}:
            completion_kind = str(event.get("arg_kind") or "barrier")
        if not completion_kind:
            continue
        label = str(event.get("label") or "")
        candidates = by_key.get((label, completion_kind), [])
        timestamp = _number(event["ts"])
        selected = next(
            (
                candidate
                for candidate in reversed(candidates)
                if candidate.first_at <= timestamp
            ),
            None,
        )
        if selected is None:
            continue
        rank = _rank_of(event)
        if rank is not None and kind in COLLECTIVE_KINDS:
            selected.completion_ranks.add(rank)
        algorithm = event.get("algorithm")
        if algorithm:
            selected.algorithms.add(str(algorithm))
        selected.dropped.update(_integer(value) for value in event.get("dropped", []) or [])
        selected.dropped.update(_integer(value) for value in event.get("absent", []) or [])
    return invocations


def _concurrency(
    spans: list[tuple[int, float, float, str]],
    t0: float,
    wall_s: float,
    world_size: int,
) -> ConcurrencyProfile:
    changes: dict[float, int] = defaultdict(int)
    total_busy = 0.0
    for _rank, start, end, _label in spans:
        if end <= start:
            continue
        relative_start = max(0.0, start - t0)
        relative_end = min(wall_s, end - t0)
        if relative_end <= relative_start:
            continue
        changes[relative_start] += 1
        changes[relative_end] -= 1
        total_busy += relative_end - relative_start
    if not changes:
        return ConcurrencyProfile([], [], 0, 0.0, 0.0, wall_s, world_size)
    times = sorted(changes)
    busy: list[int] = []
    current = 0
    active_s = 0.0
    previous = times[0]
    for timestamp in times:
        if current > 0:
            active_s += timestamp - previous
        current += changes[timestamp]
        busy.append(current)
        previous = timestamp
    return ConcurrencyProfile(
        times=times,
        busy=busy,
        max_busy=max(busy, default=0),
        total_busy_s=total_busy,
        active_s=active_s,
        wall_s=wall_s,
        world_size=world_size,
    )


def _collective_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["label"], row["kind"])].append(row)
    return [
        {
            "label": label,
            "kind": kind,
            "invocations": len(group),
            "complete": sum(bool(row["complete"]) for row in group),
            "participant_events": sum(_integer(row["participant_count"]) for row in group),
            "input_tokens": sum(_integer(row["input_tokens"]) for row in group),
        }
        for (label, kind), group in sorted(grouped.items())
    ]


def _rma_metrics(events: list[Event]) -> dict[str, Any]:
    counts = Counter(event["kind"] for event in events if event["kind"].startswith("win."))
    return {
        "operation_counts": dict(sorted(counts.items())),
        "windows_created": counts["win.create"],
        "puts": counts["win.put"],
        "accumulates": counts["win.accumulate"],
        "compare_and_swaps": counts["win.cas"],
        "successful_compare_and_swaps": sum(
            bool(event.get("swapped")) for event in events if event["kind"] == "win.cas"
        ),
        "stale_overwrites": counts["win.stale"],
        "fences": counts["win.fence"],
        "locks_acquired": counts["win.lock"],
        "unlock_attempts": counts["win.unlock"],
        "unlocks_released": sum(
            bool(event.get("released")) for event in events if event["kind"] == "win.unlock"
        ),
        "lock_wait_observable": False,
    }


def analyse(events: list[Event], *, source: str = "") -> Analysis:
    """Compute a JSON-serialisable account from current-runtime trace events."""
    if not events:
        raise ValueError("cannot analyse an empty trace")
    ordered = sorted(
        events, key=lambda event: (_integer(event.get("seq"), 2**63 - 1), _number(event["ts"]))
    )
    t0 = min(_number(event["ts"]) for event in ordered)
    t_end = max(_number(event["ts"]) for event in ordered)
    wall_s = max(0.0, t_end - t0)
    world_size = max(
        (_integer(event.get("size")) for event in ordered if event["kind"] == "job.create"),
        default=0,
    )
    run = next((str(event.get("run") or "") for event in ordered if event.get("run")), "")
    kind_counts: Counter[str] = Counter(event["kind"] for event in ordered)
    role_counts: Counter[str] = Counter(role_of(event["kind"]) for event in ordered)
    ranks: dict[int, RankProfile] = {}

    for event in ordered:
        rank = _rank_of(event)
        if rank is None:
            continue
        profile = ranks.setdefault(rank, RankProfile(rank))
        timestamp = _number(event["ts"])
        profile.first_at = timestamp if profile.first_at is None else min(profile.first_at, timestamp)
        profile.last_at = timestamp if profile.last_at is None else max(profile.last_at, timestamp)
        kind = event["kind"]
        profile.kind_counts[kind] += 1
        role = role_of(kind)
        if role == "fault":
            profile.faults += 1
        elif role == "recovery":
            profile.recoveries += 1
        if kind == "init":
            profile.epochs.add(_integer(event.get("epoch"), 1))
            profile.state = "running"
        elif kind == "finalize":
            profile.state = "finalized"
        elif kind in {"failure.convict", "failure.kill"}:
            profile.state = "failed"
        elif kind == "rank.error":
            profile.state = "error"
        elif kind == "broker.publish":
            profile.published += 1
        elif kind == "broker.claim":
            profile.claims += 1
            worker = str(event.get("worker") or "")
            if worker:
                profile.executors.add(worker)
        elif kind == "broker.submit":
            profile.submits += 1
        elif kind == "send":
            profile.sent += 1
            profile.tokens_sent += _integer(event.get("tokens"))
        elif kind == "recv":
            profile.received += 1
            charged = _integer(event.get("charged"))
            profile.tokens_received += charged
            profile.context_charged += charged
        elif kind == "ctx.release":
            profile.context_released += _integer(event.get("freed"))
        elif kind == "ctx.degrade":
            profile.context_degrades += 1
        elif kind == "ctx.stall":
            profile.context_stalls += 1
        elif kind == "ctx.stall.end":
            profile.context_stall_s += _number(event.get("waited"))

    broker_spans = _broker_spans(ordered)
    work_spans: list[tuple[int, float, float, str]] = []
    for span in broker_spans:
        if span.rank is None or span.claimed_at is None or span.submitted_at is None:
            continue
        work_spans.append((span.rank, span.claimed_at, span.submitted_at, span.label))
        profile = ranks.setdefault(span.rank, RankProfile(span.rank))
        profile.busy_s += span.busy_s or 0.0
        profile.tasks += 1

    if world_size <= 0:
        world_size = max(ranks, default=-1) + 1
    fault_events = [event for event in ordered if role_of(event["kind"]) == "fault"]
    recovery_events = [event for event in ordered if role_of(event["kind"]) == "recovery"]
    lifecycle = {
        "initializations": kind_counts["init"],
        "heartbeats": kind_counts["init.heartbeat"],
        "finalizations": kind_counts["finalize"],
        "terminal_ranks": sorted(
            rank
            for rank, profile in ranks.items()
            if profile.state in {"finalized", "failed", "error"}
        ),
        "fault_count": len(fault_events),
        "fault_counts": dict(sorted(Counter(event["kind"] for event in fault_events).items())),
        "recovery_count": len(recovery_events),
        "recovery_counts": dict(
            sorted(Counter(event["kind"] for event in recovery_events).items())
        ),
        "rank_errors": kind_counts["rank.error"],
    }
    context = {
        "charged_tokens": sum(profile.context_charged for profile in ranks.values()),
        "released_tokens": sum(profile.context_released for profile in ranks.values()),
        "degradations": kind_counts["ctx.degrade"],
        "stalls": kind_counts["ctx.stall"],
        "stall_s": round(
            sum(profile.context_stall_s for profile in ranks.values()),
            6,
        ),
        "budget_snapshots_available": False,
    }
    workers = {
        span.worker
        for span in broker_spans
        if span.worker
    }
    claimed_ranks = {
        span.rank
        for span in broker_spans
        if span.claimed_at is not None and span.rank is not None
    }
    worker_ranks: dict[str, set[int]] = defaultdict(set)
    for span in broker_spans:
        if span.worker and span.rank is not None:
            worker_ranks[span.worker].add(span.rank)
    diversity = {
        "ranks_seen": sorted(ranks),
        "claimed_ranks": sorted(claimed_ranks),
        "distinct_rank_count": len(ranks),
        "distinct_claimed_rank_count": len(claimed_ranks),
        "workers": sorted(workers),
        "distinct_worker_count": len(workers),
        "worker_ranks": {
            worker: sorted(worker_ranks[worker]) for worker in sorted(worker_ranks)
        },
        "workers_serving_multiple_ranks": sorted(
            worker for worker, served in worker_ranks.items() if len(served) > 1
        ),
        "rank_diversity_evidenced": len(claimed_ranks) > 1,
        "executor_diversity_evidenced": len(workers) > 1,
    }
    return Analysis(
        events=ordered,
        source=source,
        run=run,
        world_size=world_size,
        t0=t0,
        wall_s=wall_s,
        ranks=ranks,
        broker_spans=broker_spans,
        work_spans=sorted(work_spans),
        collectives=_collective_invocations(ordered),
        concurrency=_concurrency(work_spans, t0, wall_s, world_size),
        kind_counts=kind_counts,
        role_counts=role_counts,
        lifecycle=lifecycle,
        context=context,
        rma=_rma_metrics(ordered),
        diversity=diversity,
    )


def analyze(events: list[Event], *, source: str = "") -> Analysis:
    """US spelling alias for :func:`analyse`."""
    return analyse(events, source=source)


def analyse_path(path: str | Path) -> Analysis:
    trace_path = Path(path)
    return analyse(load_events(trace_path), source=str(trace_path))


def analyze_path(path: str | Path) -> Analysis:
    """US spelling alias for :func:`analyse_path`."""
    return analyse_path(path)
