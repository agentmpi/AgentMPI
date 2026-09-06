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

from dataclasses import dataclass, field
from typing import Any

from ..constants import (
    DEFAULT_CTX_BUDGET,
    DEFAULT_UNEXPECTED_BUDGET,
    DELIVERY_EAGER,
    DELIVERY_RENDEZVOUS,
    EAGER_THRESHOLD_TOKENS,
)
from ..errors import err

__all__ = ["Ledger", "Resident", "ResidentEntry", "choose_delivery", "degrade_spec",
           "ResidencyModel"]


@dataclass
class ResidentEntry:
    """One body the next model call would carry, and where to get it again.

    ``handle`` is an address, in one of the two forms the runtime has: a payload
    handle, which ``get_body`` resolves, or ``win:<window>/<key>@<version>``,
    which a window read at that version resolves.  Either way an evicted body is
    recoverable, which is the whole difference between this and compaction.
    """

    handle: str
    tokens: int
    what: str = ""
    pinned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"handle": self.handle, "tokens": self.tokens, "what": self.what,
                "pinned": self.pinned}


@dataclass
class Resident:
    """What is live in this rank's window now --- the reducible half of S6.1.

    The ledger says what a rank has *consumed*; this says what the next call will
    *carry*.  They are different questions and only the second has an answer that
    may go down without lying: a rank that drops a body from its window has still
    read it.

    Eviction here is not a chat agent's compaction.  Every body is content
    addressed and the handle outlives the eviction, so a rank that drops one can
    materialise it again or take a view of it (S5).  Nothing is summarised and
    nothing is lost --- which is why Appendix B's omission of a compaction policy
    is untouched by this.

    Order matters for a reason no token counter can see.  Providers cache the
    key-value state of a prompt prefix, so editing a body invalidates the cache
    from that point on: freeing fifty thousand tokens while forcing a full cache
    miss on every later call is usually the worse trade.  So entries are held in
    admission order, eviction takes the *tail* first, and a harness pins the
    immutable shared material it wants to stay at the front (E7's commission is
    byte-identical across every rank, which makes it a natural shared prefix).
    """

    budget: int = DEFAULT_CTX_BUDGET
    entries: list[ResidentEntry] = field(default_factory=list)
    evictions: int = 0
    evicted_tokens: int = 0

    @property
    def tokens(self) -> int:
        return sum(e.tokens for e in self.entries)

    @property
    def headroom(self) -> int:
        return max(0, self.budget - self.tokens)

    def admit(self, handle: str, tokens: int, *, what: str = "",
              pinned: bool = False) -> ResidentEntry:
        """Record a body as live.  An admission never fails: the ledger has
        already decided whether the delivery is allowed, and a resident set that
        refused would only hide what the rank is actually carrying."""
        entry = ResidentEntry(handle=handle, tokens=tokens, what=what, pinned=pinned)
        self.entries.append(entry)
        return entry

    def evict(self, *, down_to: int | None = None, keep: tuple[str, ...] = ()
              ) -> list[ResidentEntry]:
        """Drop bodies from the tail until at most ``down_to`` tokens are live.

        Pinned entries and those named in ``keep`` are never dropped.  Returns
        what was dropped, whose handles still resolve.
        """
        target = self.budget // 2 if down_to is None else max(0, down_to)
        dropped: list[ResidentEntry] = []
        for i in range(len(self.entries) - 1, -1, -1):
            if self.tokens <= target:
                break
            e = self.entries[i]
            if e.pinned or e.handle in keep:
                continue
            dropped.append(self.entries.pop(i))
        self.evictions += len(dropped)
        self.evicted_tokens += sum(e.tokens for e in dropped)
        return dropped

    def clear(self) -> int:
        """A fresh executor turn carries nothing, pinned material included."""
        freed = self.tokens
        self.entries = []
        return freed

    def by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.what or "?"] = out.get(e.what or "?", 0) + e.tokens
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Resident:
        raw = raw or {}
        return cls(
            budget=int(raw.get("budget", DEFAULT_CTX_BUDGET)),
            entries=[ResidentEntry(handle=str(e.get("handle", "")),
                                   tokens=int(e.get("tokens", 0)),
                                   what=str(e.get("what", "")),
                                   pinned=bool(e.get("pinned", False)))
                     for e in raw.get("entries", []) or []],
            evictions=int(raw.get("evictions", 0)),
            evicted_tokens=int(raw.get("evicted_tokens", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"budget": self.budget, "tokens": self.tokens, "headroom": self.headroom,
                "live": len(self.entries), "evictions": self.evictions,
                "evicted_tokens": self.evicted_tokens,
                "by_category": self.by_category(),
                "entries": [e.to_dict() for e in self.entries]}


@dataclass
class Ledger:
    """One rank's context accounting.

    ``used`` is cumulative, not a high-water mark of live data, because that is
    what an executor's window actually is: a transcript that only grows.  A rank
    that reads a 4000-token document, is told to forget it, and reads it again has
    spent 8000 tokens.  ``release`` exists for harnesses that genuinely start a
    fresh executor turn, and it is traced, because a ledger that can be silently
    zeroed measures nothing.

    ``resident`` is the other number, and it answers the other question (S6.1):
    what the *next* call will carry.  It is reducible by eviction where ``used``
    is not, because dropping a body does not unspend the tokens that read it.
    ``by_what`` keeps the provenance of every charge, which the runtime already
    computes for its own trace.
    """

    budget: int = DEFAULT_CTX_BUDGET
    used: int = 0
    unexpected_budget: int = DEFAULT_UNEXPECTED_BUDGET
    unexpected_used: int = 0
    releases: int = 0
    degradations: int = 0
    peak: int = 0
    by_what: dict[str, int] = field(default_factory=dict)
    resident: dict[str, Any] = field(default_factory=dict)

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
        self.note(tokens, what=what)

    def note(self, tokens: int, *, what: str = "") -> None:
        """Attribute a charge to the operation that caused it.

        The runtime computed this label already and threw it away on every
        delivery that succeeded, keeping it only in the degradation trace.  It
        costs a dictionary entry to keep, and it is the difference between
        knowing a rank spent its budget and knowing what it spent it on.
        """
        key = what or "?"
        self.by_what[key] = self.by_what.get(key, 0) + tokens

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
            "by_what": dict(self.by_what),
            "resident": self.resident,
        }

    def residency(self) -> Resident:
        """The live set, defaulting to this rank's budget."""
        r = Resident.from_dict(self.resident or None)
        if not self.resident:
            r.budget = self.budget
        return r


def choose_delivery(
    tokens: int,
    *,
    requested: str = "auto",
    eager_threshold: int = EAGER_THRESHOLD_TOKENS,
    remaining: int | None = None,
    headroom: int | None = None,
) -> str:
    """Decide eager versus rendezvous.

    The threshold is the primary rule, but a *receiver-driven* correction matters
    too: a payload comfortably under the eager limit should still travel by
    rendezvous when the receiver has almost no room left.  MPI has no analogue
    because an MPI receiver's buffer pressure is not visible to the sender; here
    it is, because the ledger is shared state.

    Two numbers can say the receiver is short (S6.1).  ``remaining`` is what is
    left of its budget and ``headroom`` what is left of its live window, and the
    second is the one MPI's buffer occupancy actually corresponds to: a receiver
    that has evicted its way back to room can take an eager body whatever its
    lifetime intake has been.  Where both are known the decision follows the
    tighter.
    """
    if requested in (DELIVERY_EAGER, DELIVERY_RENDEZVOUS):
        return requested
    if tokens > eager_threshold:
        return DELIVERY_RENDEZVOUS
    limits = [x for x in (remaining, headroom) if x is not None]
    if limits and tokens > min(limits) // 4:
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
