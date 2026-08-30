"""Context-window management: the AgentMPI memory hierarchy.

This module has no MPI counterpart, and it is the single largest semantic
departure of the protocol.

In MPI the receive buffer is supplied by the application, its size is known
statically, and overflow is a truncation error the programmer is expected never
to hit.  In an agent system the receive buffer is the model's context window:
it is shared by every message the agent has ever received, it fills
monotonically over the agent's life, and overflowing it does not raise an error
--- it silently degrades the agent's reasoning and then terminates it.  Context
exhaustion is, empirically, the dominant failure mode of long-running
multi-agent harnesses, so the protocol has to make context a first-class,
accounted, flow-controlled resource rather than an implementation detail.

Three mechanisms do that work.

*Receiver-driven rendezvous.*  MPI implementations switch from eager to
rendezvous at a fixed byte threshold chosen by the implementer, because the
constraint being managed is the size of a pre-posted network buffer.  AgentMPI
switches based on the *receiver's remaining context at the moment of the
transfer*, because that is the resource actually at risk.  The same message may
be delivered eagerly to a fresh agent and by reference to a nearly-full one.

*Projections.*  A projection is to an artifact what an MPI derived datatype is
to a buffer: a description of which part participates in a transfer, so the
sender need not materialise a smaller copy.  ``full`` moves everything,
``digest`` moves a bounded structural summary, ``schema`` moves shape without
content, ``ref`` moves only a handle.

*Explicit release.*  ``AMPI_Ctx_release`` is ``free()``.  An agent that
compacts its own history tells the runtime, and its accounted occupancy drops.
Without this the accounting is monotone and every long run eventually reports
exhaustion whether or not the agent actually compacted.
"""

from __future__ import annotations

from typing import Any

from .. import util
from ..constants import (
    DEFAULT_EAGER_LIMIT,
    MODE_EAGER,
    MODE_RENDEZVOUS,
    PROJ_DIGEST,
    PROJ_FULL,
    PROJ_REF,
    PROJ_SCHEMA,
)
from ..errors import AmpiArgError, AmpiContextExhausted

# Fraction of a receiver's *remaining* context that a single eager message may
# consume.  Above this the transfer degrades to rendezvous.  The value is a
# policy knob, exposed as a control variable, not a protocol constant.
EAGER_FRACTION = 0.10

# Occupancy above which the runtime warns, and above which collectives prefer
# context-frugal algorithms.
HIGH_WATER = 0.75


def remaining(rank_row: dict[str, Any]) -> int:
    return max(0, int(rank_row["ctx_limit"]) - int(rank_row["ctx_used"]))


def occupancy(rank_row: dict[str, Any]) -> float:
    limit = int(rank_row["ctx_limit"]) or 1
    return int(rank_row["ctx_used"]) / limit


def choose_mode(tokens: int, receiver: dict[str, Any], eager_limit: int | None = None) -> str:
    """Receiver-driven eager/rendezvous decision.

    Returns MODE_EAGER when the payload is small in absolute terms *and* small
    relative to what the receiver has left.  Both conditions are needed: the
    absolute cap keeps a single message from dominating a fresh agent's
    context, and the relative cap keeps a nearly-full agent from being finished
    off by a medium-sized one.
    """
    cap = DEFAULT_EAGER_LIMIT if eager_limit is None else eager_limit
    room = remaining(receiver)
    return MODE_EAGER if (tokens <= cap and tokens <= room * EAGER_FRACTION) else MODE_RENDEZVOUS


def project(content: str, projection: str, budget: int = 400) -> str:
    """Apply a projection to artifact content."""
    if projection == PROJ_FULL:
        return content
    if projection == PROJ_DIGEST:
        return util.structural_digest(content, budget_tokens=budget)
    if projection == PROJ_REF:
        return ""
    if projection == PROJ_SCHEMA:
        return _schema_of(content)
    raise AmpiArgError(f"unknown projection {projection!r}")


def _schema_of(content: str) -> str:
    """Shape without content: JSON key structure, or a heading outline."""
    parsed = util.loads(content, None)
    if parsed is not None and isinstance(parsed, (dict, list)):
        return util.pretty(_shape(parsed))
    headings = [
        ln.strip()
        for ln in content.splitlines()
        if ln.strip().startswith("#") or (ln.strip().endswith(":") and len(ln.strip()) < 80)
    ]
    return "\n".join(headings[:60]) or util.structural_digest(content, 60)


def _shape(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in sorted(value.items())[:40]}
    if isinstance(value, list):
        return [_shape(value[0], depth + 1), f"...x{len(value)}"] if value else []
    return type(value).__name__


class ContextAccount:
    """Per-rank context accounting, backed by the device."""

    def __init__(self, device: Any, job_id: str) -> None:
        self.device = device
        self.job_id = job_id

    def get(self, rank: int) -> dict[str, Any]:
        row = self.device.query_one(
            "SELECT * FROM rank WHERE job_id=? AND rank=?", (self.job_id, rank)
        )
        if row is None:
            raise AmpiArgError(f"rank {rank} is not registered in job {self.job_id}")
        return row

    def admit(self, rank: int, tokens: int, *, what: str = "payload") -> None:
        """Refuse a transfer that would overflow the receiver.

        Raising here rather than truncating is deliberate.  Truncation is what
        an unprotected harness does implicitly, and it is undetectable from
        inside the agent; an explicit AMPI_ERR_CONTEXT_EXHAUSTED gives the
        caller the chance to retry with a projection, to offload to a window,
        or to spawn a fresh rank.
        """
        row = self.get(rank)
        room = remaining(row)
        if tokens > room:
            raise AmpiContextExhausted(
                f"delivering {tokens} tokens of {what} to rank {rank} would exceed its "
                f"context budget ({row['ctx_used']}/{row['ctx_limit']} used, {room} free); "
                f"retry with --projection digest or store the artifact in a window",
                rank=rank,
                tokens=tokens,
                room=room,
                ctx_used=row["ctx_used"],
                ctx_limit=row["ctx_limit"],
            )

    def charge(self, rank: int, tokens: int) -> dict[str, Any]:
        self.device.execute(
            "UPDATE rank SET ctx_used = ctx_used + ?, "
            "ctx_peak = MAX(ctx_peak, ctx_used + ?), tokens_recvd = tokens_recvd + ? "
            "WHERE job_id=? AND rank=?",
            (tokens, tokens, tokens, self.job_id, rank),
        )
        return self.get(rank)

    def credit_sent(self, rank: int, tokens: int) -> None:
        self.device.execute(
            "UPDATE rank SET tokens_sent = tokens_sent + ? WHERE job_id=? AND rank=?",
            (tokens, self.job_id, rank),
        )

    def release(self, rank: int, tokens: int) -> dict[str, Any]:
        """AMPI_Ctx_release: the agent compacted; give the budget back."""
        self.device.execute(
            "UPDATE rank SET ctx_used = MAX(0, ctx_used - ?) WHERE job_id=? AND rank=?",
            (max(0, tokens), self.job_id, rank),
        )
        return self.get(rank)

    def reset(self, rank: int) -> dict[str, Any]:
        self.device.execute(
            "UPDATE rank SET ctx_used = 0 WHERE job_id=? AND rank=?", (self.job_id, rank)
        )
        return self.get(rank)
