"""Tracing and the PAMPI profiling interface.

MPI standardised ``PMPI_`` name shifting in 1994, and it is arguably the
single highest-leverage decision in the standard's tooling story: because
every MPI call has a weak-symbol wrapper, an entire ecosystem of profilers
(mpiP, TAU, Score-P, Vampir, Jumpshot) could be built by third parties
without a single line of change in any MPI implementation.  Multi-agent
frameworks today have nothing comparable; observability is bolted on per
framework, in incompatible formats.

AgentMPI therefore specifies its profiling interface as part of the
protocol rather than as an implementation feature.  Every protocol call is
routed through :class:`Profiler`, which emits a stream of events in a
documented schema.  A harness can install its own profiler exactly as an
MPI tool interposes on ``PMPI_``.

The event model borrows from OTF2 / SLOG-2: *states* (enter/leave pairs with
a duration) and *arrows* (matched send/receive pairs), plus counters.  That
model is what makes Gantt-style timeline visualisation and communication
matrices possible, and it transfers unchanged to agents.  The one addition
is that every event carries token and currency counters, because in
AgentMPI those are the resources being profiled.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

TRACE_SCHEMA = "ampi-trace/1"


@dataclass
class Event:
    """One trace record."""

    kind: str            # enter | leave | send | recv | ack | coll | state | counter | note
    ts: float
    rank: int
    op: str = ""
    context: str = ""
    peer: int | None = None
    tag: int | None = None
    tokens: int = 0
    bytes_: int = 0
    dur: float | None = None
    seq: int | None = None
    idem: str | None = None
    algorithm: str | None = None
    turn: int = 0
    state: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bytes"] = d.pop("bytes_")
        return {k: v for k, v in d.items() if v not in (None, {}, "")}


class Profiler:
    """Collects events.  Subclass and install to build a tool."""

    def __init__(self, rank: int = 0, sink=None, enabled: bool = True) -> None:
        self.rank = rank
        self.enabled = enabled
        self.events: list[Event] = []
        self._sink = sink
        self._lock = threading.Lock()
        self._t0 = time.time()
        self.counters: dict[str, float] = {}

    # -- emission ----------------------------------------------------------
    def emit(self, event: Event) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.events.append(event)
            if self._sink is not None:
                self._sink(event)

    def bump(self, name: str, amount: float = 1.0) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0.0) + amount

    @contextmanager
    def region(self, op: str, **kw: Any) -> Iterator[Event]:
        """Enter/leave pair -- the AgentMPI analogue of an OTF2 state."""
        start = time.time()
        enter = Event(kind="enter", ts=start, rank=self.rank, op=op, **_norm(kw))
        self.emit(enter)
        leave = Event(kind="leave", ts=start, rank=self.rank, op=op, **_norm(kw))
        try:
            yield leave
        finally:
            leave.ts = time.time()
            leave.dur = leave.ts - start
            self.emit(leave)
            self.bump(f"time.{op}", leave.dur)
            self.bump(f"calls.{op}")

    def note(self, message: str, **detail: Any) -> None:
        self.emit(
            Event(kind="note", ts=time.time(), rank=self.rank, op="note",
                  detail={"message": message, **detail})
        )

    # -- export ------------------------------------------------------------
    def to_jsonl(self) -> str:
        with self._lock:
            return "\n".join(json.dumps(e.to_dict(), ensure_ascii=False) for e in self.events)

    def dump(self, path: str | os.PathLike[str]) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_jsonl())
            fh.write("\n")


class NullProfiler(Profiler):
    def __init__(self) -> None:
        super().__init__(enabled=False)

    def emit(self, event: Event) -> None:  # noqa: D102
        return


class JournalProfiler(Profiler):
    """Profiler that streams events into the device journal.

    Streaming rather than buffering matters for the same reason it matters
    in HPC tracing: if a rank dies, a buffered trace dies with it, and the
    failure you most wanted to see is the one you lose.
    """

    def __init__(self, rank: int, device, stream: str = "trace") -> None:
        super().__init__(rank=rank)
        self._device = device
        self._stream = stream

    def emit(self, event: Event) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.events.append(event)
        try:
            self._device.append_journal(self._stream, event.to_dict())
        except Exception:  # pragma: no cover - tracing must never break the run
            pass


def _norm(kw: dict[str, Any]) -> dict[str, Any]:
    if "bytes" in kw:
        kw["bytes_"] = kw.pop("bytes")
    allowed = set(Event.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    detail = {k: v for k, v in kw.items() if k not in allowed}
    out = {k: v for k, v in kw.items() if k in allowed}
    if detail:
        out.setdefault("detail", {}).update(detail)
    return out


# --------------------------------------------------------------------------
# Trace post-processing: the analysis a tool would do
# --------------------------------------------------------------------------

def load_trace(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def match_arrows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair send events with their receive events (SLOG-2 "arrows")."""
    sends = {e.get("idem"): e for e in events if e.get("kind") == "send" and e.get("idem")}
    arrows = []
    for e in events:
        if e.get("kind") != "recv" or not e.get("idem"):
            continue
        s = sends.get(e["idem"])
        if s is None:
            continue
        arrows.append(
            {
                "src": s.get("rank"),
                "dst": e.get("rank"),
                "t_send": s.get("ts"),
                "t_recv": e.get("ts"),
                "latency": (e.get("ts", 0) - s.get("ts", 0)),
                "tokens": e.get("tokens", 0),
                "tag": e.get("tag"),
                "op": s.get("op"),
                "context": e.get("context"),
            }
        )
    return arrows


def communication_matrix(events: list[dict[str, Any]], n: int) -> list[list[int]]:
    """Token volume from rank i to rank j -- the classic MPI tool view."""
    matrix = [[0] * n for _ in range(n)]
    for arrow in match_arrows(events):
        src, dst = arrow["src"], arrow["dst"]
        if isinstance(src, int) and isinstance(dst, int) and 0 <= src < n and 0 <= dst < n:
            matrix[src][dst] += int(arrow.get("tokens") or 0)
    return matrix


def critical_path(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Longest chain of causally dependent agent turns.

    In an MPI trace the critical path is measured in microseconds of
    computation and communication.  Here the unit that matters is the *turn*:
    an agent's think-act step, which costs seconds to minutes and dominates
    everything else.  We therefore report both the wall-clock critical path
    and its length in turns; the ratio of total turns to critical-path turns
    is the achievable parallelism of the harness, independent of how fast the
    model happens to be on the day of the run.
    """
    arrows = match_arrows(events)
    turn_events = [
        e for e in events
        if e.get("kind") == "leave" and e.get("op") in ("turn", "agent_turn", "compute")
    ]
    by_rank: dict[int, list[dict[str, Any]]] = {}
    for e in turn_events:
        by_rank.setdefault(int(e.get("rank", 0)), []).append(e)
    total_turns = len(turn_events)
    total_turn_time = sum(float(e.get("dur") or 0.0) for e in turn_events)

    # Dynamic program over turns ordered by completion time, where a turn's
    # predecessors are the last turn on the same rank and any turn that
    # produced a message this rank consumed before starting.
    nodes = sorted(turn_events, key=lambda e: float(e.get("ts", 0)))
    best: dict[int, float] = {}
    best_turns: dict[int, int] = {}
    prev_on_rank: dict[int, int] = {}
    incoming: dict[int, list[int]] = {}
    for idx, node in enumerate(nodes):
        rank = int(node.get("rank", 0))
        start = float(node.get("ts", 0)) - float(node.get("dur") or 0.0)
        preds: list[int] = []
        if rank in prev_on_rank:
            preds.append(prev_on_rank[rank])
        for arrow in arrows:
            if arrow["dst"] == rank and arrow["t_recv"] <= start:
                for j, cand in enumerate(nodes):
                    if (
                        int(cand.get("rank", 0)) == arrow["src"]
                        and float(cand.get("ts", 0)) <= arrow["t_send"]
                        and j < idx
                    ):
                        preds.append(j)
        incoming[idx] = preds
        base = max((best.get(p, 0.0) for p in preds), default=0.0)
        base_turns = max((best_turns.get(p, 0) for p in preds), default=0)
        best[idx] = base + float(node.get("dur") or 0.0)
        best_turns[idx] = base_turns + 1
        prev_on_rank[rank] = idx

    cp_time = max(best.values(), default=0.0)
    cp_turns = max(best_turns.values(), default=0)
    return {
        "total_turns": total_turns,
        "total_turn_time_s": round(total_turn_time, 3),
        "critical_path_time_s": round(cp_time, 3),
        "critical_path_turns": cp_turns,
        "turn_parallelism": round(total_turns / cp_turns, 3) if cp_turns else 0.0,
        "time_parallelism": round(total_turn_time / cp_time, 3) if cp_time else 0.0,
    }


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = sorted({int(e["rank"]) for e in events if "rank" in e})
    ops: dict[str, dict[str, float]] = {}
    for e in events:
        if e.get("kind") != "leave":
            continue
        op = e.get("op", "?")
        rec = ops.setdefault(op, {"calls": 0, "time_s": 0.0, "tokens": 0})
        rec["calls"] += 1
        rec["time_s"] += float(e.get("dur") or 0.0)
        rec["tokens"] += int(e.get("tokens") or 0)
    tokens_sent = sum(int(e.get("tokens") or 0) for e in events if e.get("kind") == "send")
    tokens_recv = sum(int(e.get("tokens") or 0) for e in events if e.get("kind") == "recv")
    t0 = min((float(e["ts"]) for e in events if "ts" in e), default=0.0)
    t1 = max((float(e["ts"]) for e in events if "ts" in e), default=0.0)
    return {
        "schema": TRACE_SCHEMA,
        "ranks": len(ranks),
        "events": len(events),
        "wall_s": round(t1 - t0, 3),
        "tokens_sent": tokens_sent,
        "tokens_received": tokens_recv,
        "messages": sum(1 for e in events if e.get("kind") == "send"),
        "by_op": {
            k: {"calls": int(v["calls"]), "time_s": round(v["time_s"], 3),
                "tokens": int(v["tokens"])}
            for k, v in sorted(ops.items(), key=lambda kv: -kv[1]["time_s"])
        },
        **critical_path(events),
    }
