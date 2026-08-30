"""The matching engine.

MPI's matching rules are deceptively small and carry most of its semantic
weight:

1. A receive matches the first message with the same ``(context, source,
   tag)``, where ``source`` and ``tag`` may be wildcards.
2. **Non-overtaking**: two messages sent by the same rank to the same rank on
   the same communicator are matched in send order, even if the network
   reorders them.
3. Unmatched arrivals are buffered (the *unexpected queue*); unmatched
   receives are buffered (the *posted receive queue*).

AgentMPI keeps all three, because they are exactly the properties that make
message-passing code composable, and adds three more that a non-deterministic
executor forces:

4. **Deduplication.** Delivery is at-least-once (a retried send, a resurrected
   rank replaying its log), so a message is matched at most once per
   idempotency key.
5. **Epoch filtering.** After a communicator shrinks, messages sent in a
   previous epoch by ranks that no longer exist must not match, or a
   recovered run would consume stale work.
6. **Admission control.** A match is only completed if the payload fits the
   receiver's context budget; otherwise the message is digested (if its
   datatype permits) or the match fails with a recoverable error.

The engine is intentionally free of any transport knowledge: it consumes an
iterator of envelopes from a :class:`~agentmpi.transport.base.Device` and
knows nothing else about it.  That separation is the lesson of MPICH's
ADI/CH3/CH4 refactors, and it is what allows the same matching semantics --
and the same test suite -- to hold over a shared directory, an in-process
queue, or a future networked device.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .constants import (
    ANY_SOURCE,
    ANY_TAG,
    DEFAULT_MAX_POLL_INTERVAL_S,
    DEFAULT_POLL_INTERVAL_S,
)
from .envelope import Envelope
from .transport.base import Device


@dataclass
class PostedRecv:
    """An entry of the posted-receive queue."""

    source: int
    tag: int
    context: str
    posted_at: float = field(default_factory=time.time)
    matched: Envelope | None = None
    cancelled: bool = False


class MatchingEngine:
    """Per-rank message matching over a device."""

    def __init__(
        self,
        device: Device,
        rank: int,
        *,
        gap_timeout_s: float = 30.0,
        on_arrival: Callable[[Envelope], None] | None = None,
    ) -> None:
        self.device = device
        self.rank = rank
        self.gap_timeout_s = gap_timeout_s
        self.on_arrival = on_arrival

        #: Messages that have arrived but not yet been matched.  In-process
        #: only: a message sitting here is *not* consumed, so if the process
        #: exits it will be re-polled by the rank's next process.
        self.unexpected: deque[Envelope] = deque()
        #: Out-of-order arrivals held back to preserve non-overtaking.
        self._reorder: dict[tuple[str, int, int], dict[int, Envelope]] = defaultdict(dict)
        #: Next sequence number to release into the unexpected queue.
        #: In-process only; rebuilt from the consumption watermark on restart.
        self._expected_seq: dict[tuple[str, int, int], int] = defaultdict(int)
        self._gap_since: dict[tuple[str, int, int], float] = {}
        self._seen_idem: set[str] = set()

        # Durable consumption state, kept as a watermark plus a small set of
        # out-of-order exceptions above it.  The watermark is what makes this
        # bounded: a rank that has consumed a million in-order messages
        # stores one integer, not a million identifiers.  MPI never needs
        # this because an MPI rank is one process; an AgentMPI rank is a
        # succession of processes sharing one identity.
        self._consumed_wm: dict[tuple[str, int, int], int] = defaultdict(int)
        self._consumed_extra: dict[tuple[str, int, int], set[int]] = defaultdict(set)
        #: Communicator epochs; messages from an older epoch are dropped.
        self.epochs: dict[str, int] = defaultdict(int)
        #: Contexts that have been revoked.
        self.revoked: set[str] = set()

        self.stats = {
            "arrivals": 0,
            "duplicates": 0,
            "reordered": 0,
            "gaps_skipped": 0,
            "stale_epoch": 0,
            "matched": 0,
            "polls": 0,
        }

    # -- progress ----------------------------------------------------------
    def progress(self) -> int:
        """Pull newly available messages into the unexpected queue.

        Returns the number of messages made matchable.  This is AgentMPI's
        *progress engine*: like MPI, the protocol only guarantees progress
        inside protocol calls, so every blocking operation drives it.
        """
        self.stats["polls"] += 1
        made_available = 0
        for env, payload in self.device.poll(self.rank):
            self.stats["arrivals"] += 1
            if payload is not None and env.inline is None:
                env.inline = payload
            if env.idem in self._seen_idem:
                self.stats["duplicates"] += 1
                continue
            self._seen_idem.add(env.idem)
            if env.epoch < self.epochs.get(env.context, 0):
                self.stats["stale_epoch"] += 1
                continue
            key = (env.context, env.source, env.dest)
            if self.is_consumed(key, env.seq):
                # Already delivered to an earlier process of this rank.
                self.stats["duplicates"] += 1
                continue
            self._reorder[key][env.seq] = env
            if env.seq != self._expected_seq[key]:
                self.stats["reordered"] += 1
                self._gap_since.setdefault(key, time.time())
            made_available += self._drain(key)
        made_available += self._expire_gaps()
        return made_available

    def is_consumed(self, key: tuple[str, int, int], seq: int) -> bool:
        return seq < self._consumed_wm[key] or seq in self._consumed_extra[key]

    def consume(self, env: Envelope) -> None:
        """Record that ``env`` was matched to a receive, durably.

        The watermark is compacted after every consumption, so the exception
        set stays empty in the common in-order case and small otherwise.
        """
        key = (env.context, env.source, env.dest)
        if env.seq >= self._consumed_wm[key]:
            self._consumed_extra[key].add(env.seq)
        extras = self._consumed_extra[key]
        while self._consumed_wm[key] in extras:
            extras.discard(self._consumed_wm[key])
            self._consumed_wm[key] += 1
        self.device.consume(self.rank, env)

    def _drain(self, key: tuple[str, int, int]) -> int:
        """Release the in-order prefix of the reorder buffer.

        Sequence numbers already consumed by an earlier process of this rank
        are skipped rather than waited for; without that, a restarted rank
        would block forever on a message it had already handled.
        """
        released = 0
        pending = self._reorder[key]
        while True:
            nxt = self._expected_seq[key]
            if nxt in pending:
                env = pending.pop(nxt)
                self._expected_seq[key] = nxt + 1
                self.unexpected.append(env)
                if self.on_arrival is not None:
                    self.on_arrival(env)
                released += 1
                continue
            if self.is_consumed(key, nxt) and (
                pending or nxt < self._consumed_wm[key]
            ):
                self._expected_seq[key] = nxt + 1
                continue
            break
        if not pending:
            self._gap_since.pop(key, None)
        return released

    def _expire_gaps(self) -> int:
        """Skip a sequence gap whose filler never arrived.

        MPI can assume the network eventually delivers everything.  AgentMPI
        cannot: the rank that owed us sequence *k* may be dead, and blocking
        forever on a message that no longer has a sender is precisely the
        failure mode we are trying to eliminate.  After ``gap_timeout_s`` we
        advance past the hole and record it, so a harness can see in the
        trace exactly which ordering guarantee was sacrificed to make
        progress.
        """
        released = 0
        now = time.time()
        for key, since in list(self._gap_since.items()):
            if now - since < self.gap_timeout_s:
                continue
            pending = self._reorder[key]
            if not pending:
                self._gap_since.pop(key, None)
                continue
            lowest = min(pending)
            self.stats["gaps_skipped"] += lowest - self._expected_seq[key]
            self._expected_seq[key] = lowest
            self._gap_since.pop(key, None)
            released += self._drain(key)
        return released

    # -- matching ----------------------------------------------------------
    def try_match(self, source: int, tag: int, context: str) -> Envelope | None:
        """Match against the unexpected queue without blocking."""
        for i, env in enumerate(self.unexpected):
            if env.matches(source, tag, context):
                del self.unexpected[i]
                self.stats["matched"] += 1
                self.consume(env)
                return env
        return None

    def peek(self, source: int, tag: int, context: str) -> Envelope | None:
        """``AMPI_Iprobe`` -- inspect without consuming."""
        for env in self.unexpected:
            if env.matches(source, tag, context):
                return env
        return None

    def iter_unexpected(self, context: str | None = None) -> Iterable[Envelope]:
        for env in list(self.unexpected):
            if context is None or env.context == context:
                yield env

    def wait_match(
        self,
        source: int,
        tag: int,
        context: str,
        *,
        timeout: float | None = None,
        on_poll: Callable[[float], None] | None = None,
    ) -> Envelope | None:
        """Block until a matching message arrives, or the deadline passes.

        The poll interval backs off geometrically.  Aggressive polling is the
        right choice in MPI, where a message may land within a microsecond;
        here the expected wait is a peer's whole turn, tens of seconds, so
        backing off costs nothing and avoids hammering the filesystem when
        hundreds of ranks are blocked at a barrier.
        """
        deadline = None if timeout is None else time.time() + timeout
        interval = DEFAULT_POLL_INTERVAL_S
        while True:
            env = self.try_match(source, tag, context)
            if env is not None:
                return env
            if context in self.revoked:
                from .errors import RevokedError

                raise RevokedError("communicator revoked while receiving", context=context)
            self.progress()
            env = self.try_match(source, tag, context)
            if env is not None:
                return env
            if deadline is not None and time.time() >= deadline:
                return None
            if on_poll is not None:
                on_poll(interval)
            sleep_for = interval
            if deadline is not None:
                sleep_for = min(sleep_for, max(deadline - time.time(), 0.0))
            time.sleep(sleep_for)
            interval = min(interval * 1.5, DEFAULT_MAX_POLL_INTERVAL_S)

    # -- fault handling ----------------------------------------------------
    def revoke(self, context: str) -> None:
        self.revoked.add(context)

    def bump_epoch(self, context: str, epoch: int) -> None:
        self.epochs[context] = epoch
        # Drop buffered traffic from the previous epoch: after a shrink the
        # surviving ranks agree on a new membership, and any message still in
        # flight from before that agreement refers to a world that no longer
        # exists.
        self.unexpected = deque(e for e in self.unexpected if e.epoch >= epoch)
        for key in list(self._reorder):
            if key[0] == context:
                self._reorder[key] = {
                    s: e for s, e in self._reorder[key].items() if e.epoch >= epoch
                }

    def forget_rank(self, context: str, rank: int) -> None:
        """Discard ordering state for a rank that will never send again."""
        for key in list(self._reorder):
            if key[0] == context and key[1] == rank:
                self._reorder.pop(key, None)
                self._expected_seq.pop(key, None)
                self._gap_since.pop(key, None)
        self.unexpected = deque(
            e for e in self.unexpected if not (e.context == context and e.source == rank)
        )
