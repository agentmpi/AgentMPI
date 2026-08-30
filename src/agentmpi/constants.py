"""Named constants of the AgentMPI protocol.

The naming convention deliberately mirrors MPI: predefined handles are
upper-case module-level constants, and the protocol-level spelling of a
constant such as ``AMPI_ANY_SOURCE`` is the Python name ``ANY_SOURCE``
re-exported from :mod:`agentmpi` as ``AMPI_ANY_SOURCE``.
"""

from __future__ import annotations

import enum
from typing import Final

PROTOCOL_VERSION: Final[str] = "0.1"

# --- Wildcards and sentinels (MPI: MPI_ANY_SOURCE, MPI_ANY_TAG, MPI_PROC_NULL)
ANY_SOURCE: Final[int] = -1
ANY_TAG: Final[int] = -1
PROC_NULL: Final[int] = -2
UNDEFINED: Final[int] = -32766
ROOT_UNDEFINED: Final[int] = -32765

# Reserved tag space. Application tags must be >= 0 and < TAG_UB.
TAG_UB: Final[int] = 1 << 20
_INTERNAL_TAG_BASE: Final[int] = TAG_UB


class InternalTag(enum.IntEnum):
    """Tags reserved for the runtime's own collective and control traffic.

    Keeping these in a disjoint range is the AgentMPI analogue of MPI's
    rule that a communicator's context id separates library traffic from
    application traffic; the tag split additionally separates the
    *runtime's* traffic from the traffic of the library that uses it.
    """

    COLL = _INTERNAL_TAG_BASE + 1
    BARRIER = _INTERNAL_TAG_BASE + 2
    BCAST = _INTERNAL_TAG_BASE + 3
    SCATTER = _INTERNAL_TAG_BASE + 4
    GATHER = _INTERNAL_TAG_BASE + 5
    REDUCE = _INTERNAL_TAG_BASE + 6
    ALLGATHER = _INTERNAL_TAG_BASE + 7
    ALLTOALL = _INTERNAL_TAG_BASE + 8
    SCAN = _INTERNAL_TAG_BASE + 9
    AGREE = _INTERNAL_TAG_BASE + 10
    REVOKE = _INTERNAL_TAG_BASE + 11
    SHRINK = _INTERNAL_TAG_BASE + 12
    CTRL = _INTERNAL_TAG_BASE + 13


# --- Predefined context ids (MPI: MPI_COMM_WORLD, MPI_COMM_SELF)
CONTEXT_WORLD: Final[str] = "world"
CONTEXT_SELF_PREFIX: Final[str] = "self"


class Datatype(str, enum.Enum):
    """Payload datatypes.

    MPI datatypes describe *layout* so that a receiver can interpret raw
    bytes.  AgentMPI datatypes describe *contract*: how the payload is to
    be rendered into an agent's context and what shape the receiver may
    assume.  ``BOUNDED`` types additionally carry a token bound; see
    :mod:`agentmpi.datatypes`.
    """

    TEXT = "text"
    JSON = "json"
    PATCH = "patch"
    ARTIFACT = "artifact"
    TOOLCALL = "toolcall"
    DIGEST = "digest"
    NULL = "null"


class SendMode(str, enum.Enum):
    """Point-to-point completion modes (MPI: standard/buffered/synchronous/ready).

    The agent reading of each mode:

    ``STANDARD``
        Completes when the runtime has taken ownership of the payload.
    ``BUFFERED``
        Completes immediately; the payload is spilled to the blob store.
    ``SYNCHRONOUS``
        Completes only when the destination rank has *ingested* the message
        into its context.  This is strictly stronger than delivery and has
        no cheap analogue in conventional agent frameworks.
    ``READY``
        The sender asserts a matching receive is already posted; if it is
        not, the send is erroneous.
    """

    STANDARD = "standard"
    BUFFERED = "buffered"
    SYNCHRONOUS = "synchronous"
    READY = "ready"


class ThreadLevel(enum.IntEnum):
    """Concurrency levels (MPI: MPI_THREAD_SINGLE .. MPI_THREAD_MULTIPLE)."""

    SINGLE = 0
    FUNNELED = 1
    SERIALIZED = 2
    MULTIPLE = 3


class RankState(str, enum.Enum):
    """Lifecycle states of a rank.

    MPI ranks are either alive or (in ULFM) failed.  Agent ranks need a
    richer lattice because they fail in ways that are not fail-stop.
    """

    UNBORN = "unborn"          # allocated a rank id, not yet initialised
    INIT = "init"              # called AMPI_Init, joined the run
    ACTIVE = "active"          # running a turn
    BLOCKED = "blocked"        # inside a blocking AgentMPI call
    FINALIZED = "finalized"    # called AMPI_Finalize cleanly
    FAILED = "failed"          # detected dead (crash / no heartbeat)
    STALLED = "stalled"        # alive but making no progress
    DRIFTED = "drifted"        # producing contract-violating output
    BANKRUPT = "bankrupt"      # exhausted its token or currency budget
    EVICTED = "evicted"        # removed from the communicator by shrink


#: States a failure detector treats as "not usable as a peer".
DEAD_STATES: Final[frozenset[RankState]] = frozenset(
    {RankState.FAILED, RankState.STALLED, RankState.BANKRUPT, RankState.EVICTED}
)


class CommSplitType(str, enum.Enum):
    """Arguments to ``AMPI_Comm_split_type``.

    MPI-3 added ``MPI_COMM_TYPE_SHARED`` so that a program can discover the
    set of ranks that share physical memory.  The agent analogue is the set
    of ranks that share a *resource with a coherent cost or capability*:
    the same model, the same provider quota, or the same context store.
    """

    MODEL = "model"          # same underlying model identifier
    PROVIDER = "provider"    # same inference provider / quota pool
    HOST = "host"            # same physical machine (shared filesystem)
    STORE = "store"          # same context/memory store
    ROLE = "role"            # same declared role


class OpKind(str, enum.Enum):
    BUILTIN = "builtin"
    USER = "user"


class ErrorClass(enum.IntEnum):
    """Error classes.  Values below 100 mirror MPI; 100+ are AgentMPI-specific."""

    SUCCESS = 0
    ERR_BUFFER = 1
    ERR_COUNT = 2
    ERR_TYPE = 3
    ERR_TAG = 4
    ERR_COMM = 5
    ERR_RANK = 6
    ERR_REQUEST = 7
    ERR_ROOT = 8
    ERR_GROUP = 9
    ERR_OP = 10
    ERR_TOPOLOGY = 11
    ERR_ARG = 12
    ERR_UNKNOWN = 14
    ERR_TRUNCATE = 15
    ERR_OTHER = 16
    ERR_INTERN = 17
    ERR_PENDING = 18
    ERR_WIN = 20
    ERR_RMA_SYNC = 21
    ERR_RMA_CONFLICT = 22
    ERR_FILE = 30
    ERR_ACCESS = 31
    ERR_NO_SPACE = 39

    # ULFM-derived
    ERR_PROC_FAILED = 75
    ERR_PROC_FAILED_PENDING = 76
    ERR_REVOKED = 77

    # AgentMPI-specific: failure modes with no MPI analogue.
    ERR_CONTRACT = 100        # payload violated the declared datatype contract
    ERR_BUDGET = 101          # token / currency budget exhausted
    ERR_CONTEXT_OVERFLOW = 102  # message does not fit the receiver's context
    ERR_STALLED = 103         # peer alive but not progressing
    ERR_DRIFT = 104           # peer repeatedly violating its contract
    ERR_NONDETERMINISM = 105  # replay diverged beyond the declared tolerance
    ERR_TIMEOUT = 106
    ERR_UNSUPPORTED = 107
    ERR_COLL_MISMATCH = 108


ERROR_STRINGS: Final[dict[int, str]] = {
    ErrorClass.SUCCESS: "no error",
    ErrorClass.ERR_COMM: "invalid communicator",
    ErrorClass.ERR_RANK: "invalid rank",
    ErrorClass.ERR_TAG: "invalid tag",
    ErrorClass.ERR_TYPE: "invalid datatype",
    ErrorClass.ERR_OP: "invalid reduction operation",
    ErrorClass.ERR_ROOT: "invalid root",
    ErrorClass.ERR_ARG: "invalid argument",
    ErrorClass.ERR_TRUNCATE: "message truncated",
    ErrorClass.ERR_PENDING: "pending request",
    ErrorClass.ERR_WIN: "invalid window",
    ErrorClass.ERR_RMA_SYNC: "wrong RMA synchronisation epoch",
    ErrorClass.ERR_RMA_CONFLICT: "conflicting concurrent RMA accesses",
    ErrorClass.ERR_FILE: "file error",
    ErrorClass.ERR_PROC_FAILED: "process failure detected",
    ErrorClass.ERR_PROC_FAILED_PENDING: "pending operation on a failed process",
    ErrorClass.ERR_REVOKED: "communicator has been revoked",
    ErrorClass.ERR_CONTRACT: "payload violated the datatype contract",
    ErrorClass.ERR_BUDGET: "budget exhausted",
    ErrorClass.ERR_CONTEXT_OVERFLOW: "message exceeds the receiver's context capacity",
    ErrorClass.ERR_STALLED: "peer stalled",
    ErrorClass.ERR_DRIFT: "peer drifted from its contract",
    ErrorClass.ERR_NONDETERMINISM: "replay diverged",
    ErrorClass.ERR_TIMEOUT: "operation timed out",
    ErrorClass.ERR_UNSUPPORTED: "operation not supported by this device",
    ErrorClass.ERR_COLL_MISMATCH: "peers disagree about the collective sequence",
}


class CollAlgorithm(str, enum.Enum):
    """Selectable collective algorithms (the AMPI_T ``coll_algorithm`` cvar)."""

    AUTO = "auto"
    FLAT = "flat"                    # linear / star
    BINOMIAL = "binomial"
    KNOMIAL = "knomial"
    CHAIN = "chain"                  # pipeline
    RING = "ring"
    RECURSIVE_DOUBLING = "recursive_doubling"
    RECURSIVE_HALVING = "recursive_halving"
    RABENSEIFNER = "rabenseifner"    # reduce_scatter + allgather
    SCATTER_ALLGATHER = "scatter_allgather"  # van de Geijn broadcast
    BRUCK = "bruck"
    DISSEMINATION = "dissemination"  # barrier
    PAIRWISE = "pairwise"


DEFAULT_HEARTBEAT_PERIOD_S: Final[float] = 5.0
DEFAULT_FAILURE_TIMEOUT_S: Final[float] = 90.0
DEFAULT_STALL_TIMEOUT_S: Final[float] = 600.0
DEFAULT_POLL_INTERVAL_S: Final[float] = 0.05
DEFAULT_MAX_POLL_INTERVAL_S: Final[float] = 2.0
