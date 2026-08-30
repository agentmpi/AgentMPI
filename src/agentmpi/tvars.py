"""AMPI_T: the tool information interface.

MPI-3 added ``MPI_T`` so that tools could read and write an implementation's
internal knobs (*control variables*) and counters (*performance variables*)
through a standard interface rather than through implementation-specific
environment variables.  It is a small part of the standard with an outsized
effect on operability: it is why an autotuner can drive MPICH and Open MPI
with the same code.

AgentMPI adopts it directly, and it matters more here than in MPI, because
the knobs are not just performance settings -- ``ampi_eager_chars`` decides
whether a payload enters an agent's context or is passed by reference, and
``ampi_strict_contracts`` decides whether a malformed reply is an error or a
warning.  Those are policy decisions a harness must be able to set, inspect,
and record in its experimental log.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Cvar:
    """A control variable."""

    name: str
    default: Any
    kind: type
    doc: str
    scope: str = "local"      # local | group | all_eq
    env: str | None = None

    def read_env(self) -> Any | None:
        key = self.env or f"AMPI_{self.name.removeprefix('ampi_').upper()}"
        raw = os.environ.get(key)
        if raw is None:
            return None
        if self.kind is bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        try:
            return self.kind(raw)
        except (TypeError, ValueError):
            return None


CVARS: dict[str, Cvar] = {
    c.name: c
    for c in [
        Cvar("ampi_device", "journal", str,
             "Transport device: 'journal' (shared directory) or 'inproc'."),
        Cvar("ampi_eager_chars", 8192, int,
             "Payloads longer than this travel by content-addressed reference "
             "instead of inline.  The AgentMPI eager limit: below it, ship the "
             "data; above it, ship a handle and let the receiver decide whether "
             "to spend context on it."),
        Cvar("ampi_strict_contracts", False, bool,
             "Raise AMPI_ERR_CONTRACT when a received payload violates its "
             "declared datatype instead of reporting the violation in the status."),
        Cvar("ampi_admission_control", True, bool,
             "Charge every receive against the rank's context budget and refuse "
             "receives that do not fit."),
        Cvar("ampi_auto_digest", True, bool,
             "When a payload does not fit the receiver's budget, digest it "
             "(if its datatype is lossy) rather than failing the receive."),
        Cvar("ampi_heartbeat_s", 5.0, float,
             "Heartbeat period.  Lower values detect death faster and cost more "
             "filesystem traffic."),
        Cvar("ampi_failure_timeout_s", 90.0, float,
             "A rank with no heartbeat for this long is declared failed."),
        Cvar("ampi_stall_timeout_s", 600.0, float,
             "A rank that heartbeats but does not advance its turn counter for "
             "this long is declared stalled.  Separating these two timeouts is "
             "essential: an agent stuck in a tool-call loop is alive by every "
             "liveness test and useless by every progress test."),
        Cvar("ampi_gap_timeout_s", 30.0, float,
             "How long the matching engine holds a sequence gap before skipping "
             "it, trading the non-overtaking guarantee for liveness."),
        Cvar("ampi_coll_mismatch_grace_s", 3.0, float,
             "How long to accumulate evidence before reporting that peers "
             "disagree about the collective sequence.  Detection is symmetric, "
             "so a single disagreeing message cannot tell you whether you or "
             "your peer skipped a step; a brief wait buys a majority."),
        Cvar("ampi_coll_algorithm", "auto", str,
             "Force a collective algorithm, overriding the decision function."),
        Cvar("ampi_max_retries", 2, int,
             "Retries for an operation that fails a contract check."),
        Cvar("ampi_trace", True, bool, "Emit trace events."),
        Cvar("ampi_context_capacity", 128000, int,
             "Default per-rank context capacity in tokens."),
        Cvar("ampi_context_reserve", 0.35, float,
             "Fraction of the context window reserved for the agent's own "
             "reasoning and output, and therefore unavailable for ingest."),
        Cvar("ampi_lifetime_tokens", 0, int,
             "Hard cap on cumulative ingest per rank (0 = unlimited)."),
        Cvar("ampi_currency_budget", 0.0, float,
             "Hard cap on spend per rank in currency units (0 = unlimited)."),
    ]
}


def default_cvars() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, cvar in CVARS.items():
        env = cvar.read_env()
        out[name] = cvar.default if env is None else env
    return out


class Pvars:
    """Performance variables: monotone counters and gauges."""

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, Callable[[], float]] = {}

    def inc(self, name: str, by: float = 1.0) -> None:
        self._counters[name] = self._counters.get(name, 0.0) + by

    def set_gauge(self, name: str, fn: Callable[[], float]) -> None:
        self._gauges[name] = fn

    def read(self, name: str) -> float:
        if name in self._gauges:
            return float(self._gauges[name]())
        return self._counters.get(name, 0.0)

    def snapshot(self) -> dict[str, float]:
        out = dict(self._counters)
        for k, fn in self._gauges.items():
            try:
                out[k] = float(fn())
            except Exception:  # pragma: no cover
                pass
        return {k: round(v, 6) for k, v in sorted(out.items())}


PVAR_DOCS: dict[str, str] = {
    "msgs_sent": "Messages sent by this rank.",
    "msgs_recv": "Messages received by this rank.",
    "tokens_sent": "Tokens emitted.",
    "tokens_recv": "Tokens ingested (charged against the context budget).",
    "tokens_digested": "Tokens removed by digesting oversized payloads.",
    "context_pressure": "Live context tokens divided by usable capacity.",
    "coll_calls": "Collective invocations.",
    "coll_steps": "Total communication rounds spent inside collectives.",
    "turns": "Agent turns executed by this rank.",
    "stalls_detected": "Peers declared stalled by this rank's failure detector.",
    "failures_detected": "Peers declared failed by this rank's failure detector.",
    "revokes": "Communicator revocations observed.",
    "shrinks": "Communicator shrink operations completed.",
    "contract_violations": "Payloads that failed their datatype contract.",
    "retries": "Operations retried after a contract or transport failure.",
    "cache_hits": "Semantic operator invocations served from the memo cache.",
    "currency_spent": "Cumulative spend attributed to this rank.",
}
