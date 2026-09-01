"""The context ledger: flow control denominated in tokens.

Context exhaustion is the dominant scaling failure of agent harnesses, and the
reason it is so damaging is that it is *silent*.  A process that runs out of
memory dies with a diagnosable error; an agent whose window is full does not stop,
it degrades --- it forgets the earlier half of its instructions and produces
confident, plausible, wrong output.  There is no signal at the point of failure.

The move this module makes is to treat the receiving executor's context window as
MPI treats the unexpected-message buffer: a finite resource, charged explicitly,
protected by an eager limit, and subject to a credit discipline that converts an
invisible quality collapse into a reported, attributable stall.

Three mechanisms, each a direct transplant:

*The eager limit* (S5.2).  Below a threshold, pushing the body into the receiver
unsolicited is cheaper than a handshake.  Above it, the receiver's attention is
too precious to spend without permission, so it gets an envelope and a handle and
decides for itself.  In MPI the units are bytes and the precious thing is buffer
space; here the units are tokens and the precious thing is attention.  The
mechanism transfers exactly.

*The unexpected-message budget* (S5.6).  Every rank publishes a bound on the total
volume of unmatched eager messages it will accept.  A sender that would exceed it
blocks.  This is what makes "context-safe program" a checkable property rather
than a hope --- see :mod:`ampi.core.safety`.

*The ledger* (S2.3).  Delivering a body charges the receiver.  An operation whose
delivery would exceed the budget must not silently succeed: it either fails with
``AMPI_ERR_CTX_EXCEEDED`` or *degrades* to a bounded view.  Degrading is preferred
because an agent that receives a truncated message can continue, whereas one that
receives an error usually cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..constants import (
    DEFAULT_CTX_BUDGET,
    DEFAULT_UNEXPECTED_BUDGET,
    DELIVERY_EAGER,
    DELIVERY_RENDEZVOUS,
    EAGER_THRESHOLD_TOKENS,
)
from ..errors import err

__all__ = ["Ledger", "choose_delivery", "degrade_spec", "ResidencyModel"]


@dataclass
class Ledger:
    """One rank's context accounting.

    ``used`` is cumulative, not a high-water mark of live data, because that is
    what an executor's window actually is: a transcript that only grows.  A rank
    that reads a 4000-token document, is told to forget it, and reads it again has
    spent 8000 tokens.  ``release`` exists for harnesses that genuinely start a
    fresh executor turn, and it is traced, because a ledger that can be silently
    zeroed measures nothing.
    """

    budget: int = DEFAULT_CTX_BUDGET
    used: int = 0
    unexpected_budget: int = DEFAULT_UNEXPECTED_BUDGET
    unexpected_used: int = 0
    releases: int = 0
    degradations: int = 0
    peak: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    def would_exceed(self, tokens: int) -> bool:
        return self.used + tokens > self.budget

    def charge(self, tokens: int, *, what: str = "") -> None:
        if self.would_exceed(tokens):
            raise err(
                "AMPI_ERR_CTX_EXCEEDED",
                f"delivering {tokens} tokens would take this rank to "
                f"{self.used + tokens} against a budget of {self.budget}",
                hint="Re-issue with --view head:400 to take a bounded projection, "
                "or --out FILE to save the body to disk without charging context.",
                tokens=tokens,
                used=self.used,
                budget=self.budget,
                what=what,
            )
        self.used += tokens
        self.peak = max(self.peak, self.used)

    def release(self, tokens: int) -> int:
        """Record that an executor turn ended and its transcript was dropped."""
        freed = min(tokens, self.used)
        self.used -= freed
        self.releases += 1
        return freed

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Ledger:
        """Rebuild from a serialised ledger, ignoring derived fields.

        ``to_dict`` reports ``remaining`` because that is the number an executor
        needs; it is not state, so it must not be fed back in.
        """
        fields = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (raw or {}).items() if k in fields})

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "used": self.used,
            "remaining": self.remaining,
            "peak": self.peak,
            "unexpected_budget": self.unexpected_budget,
            "unexpected_used": self.unexpected_used,
            "releases": self.releases,
            "degradations": self.degradations,
        }


def choose_delivery(
    tokens: int,
    *,
    requested: str = "auto",
    eager_threshold: int = EAGER_THRESHOLD_TOKENS,
    remaining: int | None = None,
) -> str:
    """Decide eager versus rendezvous.

    The threshold is the primary rule, but a *receiver-driven* correction matters
    too: a payload comfortably under the eager limit should still travel by
    rendezvous when the receiver has almost no budget left.  MPI has no analogue
    because an MPI receiver's buffer pressure is not visible to the sender; here
    it is, because the ledger is shared state.
    """
    if requested in (DELIVERY_EAGER, DELIVERY_RENDEZVOUS):
        return requested
    if tokens > eager_threshold:
        return DELIVERY_RENDEZVOUS
    if remaining is not None and tokens > remaining // 4:
        return DELIVERY_RENDEZVOUS
    return DELIVERY_EAGER


#: Below this many tokens a projection cannot say anything useful, so an
#: implementation must fail rather than pretend to have delivered something.
MIN_DEGRADE_TOKENS = 64


def degrade_allowance(remaining: int) -> int:
    """How many tokens an over-budget delivery may still be charged.

    Half the remaining budget rather than all of it, because a rank that spends
    its last token on one message can do nothing with what it read --- but never
    more than what is actually left.  An earlier version took
    ``max(64, remaining // 2)``, and that floor is a bug: with two tokens
    remaining it charges sixty-four, so the mechanism that exists to keep a rank
    inside its budget takes it outside.  A randomised invariant test found it at
    4035/4000.
    """
    return min(remaining, max(MIN_DEGRADE_TOKENS, remaining // 2))


def degrade_spec(tokens: int, remaining: int) -> str:
    """The view an over-budget delivery degrades to.

    ``headtail`` rather than ``head`` because the end of an agent artifact is
    where the conclusions are, and losing them silently is worse than losing the
    middle visibly.
    """
    return f"headtail:{min(degrade_allowance(remaining), tokens)}"


@dataclass
class ResidencyModel:
    """Closed-form peak context residency for the collective catalogue.

    This is the analysis that has no MPI counterpart.  In MPI an algorithm choice
    trades latency against bandwidth and every choice *runs*; here an algorithm
    can be **infeasible**, because the peak data resident in one rank exceeds a
    context window that cannot be enlarged.  Selection must therefore be an
    admissibility test before it is an optimisation, and the numbers below are
    what the test consults.

    ``n`` is the per-contribution token count, ``p`` the participant count, and
    ``h`` the envelope-plus-handle cost of referring to a payload without
    delivering it (measured at 40 tokens for the reference envelope).
    """

    p: int
    n: int
    handle_tokens: int = 40

    # -- broadcast ---------------------------------------------------------
    def bcast_inline(self) -> tuple[int, int]:
        """(total across ranks, peak at one rank)"""
        return (self.p - 1) * self.n, self.n

    def bcast_handle(self) -> tuple[int, int]:
        return (self.p - 1) * self.handle_tokens, self.handle_tokens

    # -- gather ------------------------------------------------------------
    def gather_inline(self) -> tuple[int, int]:
        return (self.p - 1) * self.n, (self.p - 1) * self.n

    def gather_manifest(self) -> tuple[int, int]:
        return (self.p - 1) * self.handle_tokens, (self.p - 1) * self.handle_tokens

    # -- allgather ---------------------------------------------------------
    def allgather_inline(self) -> tuple[int, int]:
        """The one that kills naive harnesses.

        At p=128 with 4000-token contributions this charges a single rank half a
        million tokens.  Concatenation is not a reasonable default at any
        interesting p, which is why ``gather`` returns a manifest.
        """
        return self.p * (self.p - 1) * self.n, (self.p - 1) * self.n

    def allgather_handle(self) -> tuple[int, int]:
        h = self.handle_tokens
        return self.p * (self.p - 1) * h, (self.p - 1) * h

    def allgather_view(self, view_tokens: int) -> tuple[int, int]:
        return self.p * (self.p - 1) * view_tokens, (self.p - 1) * view_tokens

    def as_table(self, view_tokens: int = 400) -> list[dict[str, Any]]:
        rows = []
        for name, (total, peak) in [
            ("bcast/inline", self.bcast_inline()),
            ("bcast/handle", self.bcast_handle()),
            ("gather/inline", self.gather_inline()),
            ("gather/manifest", self.gather_manifest()),
            ("allgather/inline", self.allgather_inline()),
            ("allgather/handle", self.allgather_handle()),
            (f"allgather/view({view_tokens})", self.allgather_view(view_tokens)),
        ]:
            rows.append({"case": name, "p": self.p, "n": self.n, "total": total, "peak": peak})
        return rows
