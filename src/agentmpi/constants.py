"""Named constants and enumerations for AgentMPI.

Wildcards, transfer modes, failure classes and the rank lifecycle live here so
that the CLI, the runtime and the harness library all agree on spellings.
"""

from __future__ import annotations

import enum
from typing import Final

#: Wildcard source for :meth:`agentmpi.comm.Communicator.recv`.  Mirrors
#: ``MPI_ANY_SOURCE``.  A receive with ANY_SOURCE is the only place AgentMPI
#: gives up its per-pair ordering guarantee across *different* senders.
ANY_SOURCE: Final[int] = -1

#: Wildcard tag.  Mirrors ``MPI_ANY_TAG``.
ANY_TAG: Final[str] = "*"

#: Sentinel rank used by :meth:`agentmpi.comm.Communicator.split` to mean "I am
#: not a member of any resulting communicator".  Mirrors ``MPI_UNDEFINED``.
UNDEFINED: Final[int] = -32766

#: The context id of the world communicator.  Context ids are the AgentMPI
#: analogue of MPI's communicator contexts: they partition the message
#: namespace so that a harness and a library it calls cannot collide on tags.
WORLD_CTX: Final[int] = 0

#: Reserved tag namespace prefix.  Tags beginning with ``"_ampi:"`` are used by
#: collective algorithms and the fault-tolerance layer.  User tags may not use
#: this prefix, which is how AgentMPI achieves the isolation that MPI achieves
#: by hiding collectives inside a duplicated communicator.
INTERNAL_TAG_PREFIX: Final[str] = "_ampi:"


class Mode(str, enum.Enum):
    """Transfer mode for a point-to-point send.

    The distinction between EAGER and RENDEZVOUS is the load-bearing idea of
    AgentMPI's transport layer.  In MPI it is an *implementation* detail chosen
    by the library from a byte threshold; in AgentMPI it is *semantically
    visible*, because it decides whether a payload is injected into the
    receiving agent's context window or merely announced to it.

    EAGER
        The payload is delivered into the receiver's mailbox and will be
        materialised into the receiving rank's context on ``recv``.  Cheap for
        small payloads, and the only mode that behaves like ordinary MPI
        buffered communication.
    RENDEZVOUS
        Only an *envelope* (digest, token count, schema, one-line synopsis) is
        delivered.  The receiver must call ``fetch`` — possibly through a
        :class:`~agentmpi.schema.View` — to materialise any part of the
        payload.  This is what makes large-payload programs context-safe.
    SYNCHRONOUS
        Like MPI_Ssend: the send does not complete until a matching receive has
        been posted.  Used to bound in-flight work.
    AUTO
        The runtime chooses EAGER below the eager limit and RENDEZVOUS above
        it, exactly as an MPI implementation chooses from its eager threshold.
    """

    EAGER = "eager"
    RENDEZVOUS = "rendezvous"
    SYNCHRONOUS = "synchronous"
    AUTO = "auto"


class MessageState(str, enum.Enum):
    QUEUED = "queued"
    MATCHED = "matched"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class RankState(str, enum.Enum):
    """Lifecycle of a *rank*, which is a durable role rather than a process.

    A rank outlives the agent instances (incarnations) that embody it.  This is
    the Orleans "virtual actor" idea grafted onto MPI's static rank space: the
    rank exists from ``init`` to ``finalize`` regardless of how many agent
    processes are spawned to serve it.
    """

    #: Declared in the rank table but no incarnation has claimed it yet.
    PENDING = "pending"
    #: An incarnation holds a live lease and is executing.
    RUNNING = "running"
    #: The incarnation completed its assigned work and released the lease
    #: voluntarily; the rank may be re-incarnated.
    IDLE = "idle"
    #: Called ``finalize`` successfully.
    FINALIZED = "finalized"
    #: Lease expired or the supervisor declared it dead.
    FAILED = "failed"
    #: Removed from a shrunken communicator.
    EXCLUDED = "excluded"


#: States from which a rank will never read its mailbox again.  A message queued for a rank in
#: one of these is an orphan: the send succeeded and nothing will ever receive it.
#:
#: ``IDLE`` is deliberately absent.  An idle rank has released its lease but may be
#: re-incarnated, and its mailbox survives the gap --- that persistence is the point of a rank
#: being a durable role rather than a process, and treating a message to an idle rank as lost
#: would contradict it.
TERMINAL_RANK_STATES: frozenset[str] = frozenset(
    {RankState.FINALIZED.value, RankState.FAILED.value, RankState.EXCLUDED.value}
)


class FailureClass(str, enum.Enum):
    """AgentMPI's failure taxonomy (spec section 8).

    MPI's fault-tolerance work assumes fail-stop processes.  Agent ranks
    exhibit a wider spectrum, and the distinction matters because different
    classes require different mitigations: F1/F2 are handled by detection and
    redundancy, F3 by contract checking, F4 only by *verification*, and F5 by
    transport mode selection.
    """

    #: Rank stopped reporting: crashed, killed, or exceeded a hard deadline.
    FAIL_STOP = "fail_stop"
    #: Rank is alive but violates its latency budget (straggler).
    FAIL_SLOW = "fail_slow"
    #: Output violates its structural contract; cheap to detect.
    FAIL_NOISY = "fail_noisy"
    #: Output is well-formed, confident and wrong.  The analogue of silent data
    #: corruption, and the dominant failure mode of agent systems.
    FAIL_PLAUSIBLE = "fail_plausible"
    #: Rank exhausted its context budget.
    FAIL_GREEDY = "fail_greedy"
    #: Rank actively deviates from the protocol (injection, tool misuse).
    FAIL_ADVERSARIAL = "fail_adversarial"


class Associativity(str, enum.Enum):
    """Declared algebraic strength of a reduction operator.

    MPI requires user reduction operators to be associative and lets the
    implementation pick any tree.  Semantic operators implemented by an LLM are
    at best *approximately* associative, so AgentMPI makes the property part of
    the operator's declaration and uses it to constrain algorithm selection.
    """

    #: f(f(a,b),c) == f(a,f(b,c)) exactly.  Any tree is legal.
    EXACT = "exact"
    #: Associative up to a tolerance in the quality metric.  Trees are legal
    #: but the runtime records the reduction *depth* so the harness can bound
    #: accumulated drift.
    APPROX = "approx"
    #: Not associative.  Only the left-to-right serial chain is legal.
    NONE = "none"


class WinMemoryModel(str, enum.Enum):
    """Memory model of an RMA window, following MPI-3.

    SEPARATE
        Each rank has a private copy of the window that may be stale; it is
        reconciled with the public copy only at synchronisation points.  This
        is the *correct* default for agents, because an agent's belief about
        shared state lives in its context window and goes stale the moment a
        peer writes.
    UNIFIED
        Public and private copies coincide; a rank always observes the latest
        committed value.  Available in AgentMPI because the fabric is
        genuinely coherent, but selecting it means the harness accepts that
        agents must re-read before every use.
    """

    SEPARATE = "separate"
    UNIFIED = "unified"


class LockType(str, enum.Enum):
    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class CollState(str, enum.Enum):
    OPEN = "open"
    COMPLETE = "complete"
    FAILED = "failed"


class BarrierPolicy(str, enum.Enum):
    """What a synchronising collective does when a peer does not arrive.

    MPI has no answer here: a barrier with a dead peer hangs forever.  Every
    AgentMPI synchronising call therefore takes a deadline and a policy.
    """

    #: Block indefinitely (MPI semantics; useful only for debugging).
    WAIT = "wait"
    #: Raise :class:`~agentmpi.errors.AmpiTimeout` at the deadline.
    RAISE = "raise"
    #: Mark non-arrivers failed, revoke, and return; the harness is expected to
    #: call ``shrink``.
    REVOKE = "revoke"
    #: Mark non-arrivers failed and continue with the survivors, renumbering
    #: the communicator in place.
    SHRINK = "shrink"
    #: Continue with whatever contributions arrived, leaving the communicator
    #: intact and reporting the absentees.
    PROCEED = "proceed"


class RestartPolicy(str, enum.Enum):
    """Supervision restart strategies, borrowed from Erlang/OTP.

    MPI contributes the communication algebra; OTP contributes the recovery
    discipline.  AgentMPI's supervisor implements the three OTP strategies
    plus a no-op for harnesses that prefer to handle failure inline.
    """

    NONE = "none"
    ONE_FOR_ONE = "one_for_one"
    ONE_FOR_ALL = "one_for_all"
    REST_FOR_ONE = "rest_for_one"


#: Default eager limit in tokens.  Payloads at or below this size travel EAGER.
#: Chosen to be roughly one screenful of text: large enough that control
#: messages are never delayed by a handshake, small enough that a rank can
#: absorb a full fan-in of eager messages without exhausting a 128k context.
DEFAULT_EAGER_LIMIT: Final[int] = 2048

#: Default per-rank context budget in tokens.
DEFAULT_CONTEXT_BUDGET: Final[int] = 128_000

#: Default lease duration for a rank incarnation, in seconds.  A rank whose
#: lease expires without renewal is a candidate for FAIL_STOP.
DEFAULT_LEASE_SECONDS: Final[float] = 900.0

#: How often an incarnation should renew its lease.
DEFAULT_HEARTBEAT_SECONDS: Final[float] = 30.0
