"""Named constants of the AgentMPI specification, v0.1.

Naming follows the MPI standard closely enough that an HPC reader can transfer
intuition directly, and diverges only where the underlying resource being
managed is different (tokens and context windows rather than bytes and memory).
"""

from __future__ import annotations

SPEC_VERSION = "0.1"

# --- Wildcards -------------------------------------------------------------
# MPI_ANY_SOURCE / MPI_ANY_TAG.  Negative sentinels so they can never collide
# with a real rank or a user tag, which the spec constrains to be >= 0.
AMPI_ANY_SOURCE = -1
AMPI_ANY_TAG = -1
AMPI_PROC_NULL = -2
AMPI_UNDEFINED = -32766

# Reserved tag space.  MPI reserves nothing above MPI_TAG_UB; AgentMPI reserves
# a high band for library-internal traffic so that collectives implemented over
# point-to-point cannot be intercepted by a careless (or adversarial) agent.
AMPI_TAG_UB = 1 << 20
AMPI_TAG_INTERNAL_BASE = AMPI_TAG_UB + 1

# --- Communicator identities ----------------------------------------------
AMPI_COMM_WORLD = "world"
AMPI_COMM_SELF = "self"
AMPI_COMM_NULL = None

# --- Rank lifecycle states -------------------------------------------------
# MPI has no notion of a rank lifecycle: a process is alive until the job dies.
# Agents are cheap to create and destroy, so lifecycle is first class here.
RANK_INIT = "init"
RANK_ALIVE = "alive"
RANK_FINALIZED = "finalized"
RANK_FAILED = "failed"
RANK_REVOKED = "revoked"
RANK_STATES = (RANK_INIT, RANK_ALIVE, RANK_FINALIZED, RANK_FAILED, RANK_REVOKED)

LIVE_RANK_STATES = (RANK_INIT, RANK_ALIVE)

# --- Message transfer protocols -------------------------------------------
# The MPI eager/rendezvous split, reinterpreted.  In MPI the crossover is a
# static byte threshold chosen by the implementer.  In AgentMPI the scarce
# resource is the *receiver's* context window, so the crossover is receiver
# driven and dynamic (see core/context.py).
MODE_EAGER = "eager"
MODE_RENDEZVOUS = "rendezvous"

# --- Payload projections ---------------------------------------------------
# The AgentMPI analogue of MPI derived datatypes.  An MPI datatype describes
# which bytes of a buffer participate in a transfer; a projection describes
# which *information* of an artifact participates.  Both exist so that a
# transfer can move less than the whole object without the sender rewriting it.
PROJ_FULL = "full"
PROJ_DIGEST = "digest"
PROJ_SCHEMA = "schema"
PROJ_REF = "ref"
PROJECTIONS = (PROJ_FULL, PROJ_DIGEST, PROJ_SCHEMA, PROJ_REF)

# --- Window (RMA) memory models -------------------------------------------
WIN_UNIFIED = "unified"
WIN_SEPARATE = "separate"

LOCK_SHARED = "shared"
LOCK_EXCLUSIVE = "exclusive"

# --- Collective operations -------------------------------------------------
COLL_BARRIER = "barrier"
COLL_BCAST = "bcast"
COLL_GATHER = "gather"
COLL_SCATTER = "scatter"
COLL_ALLGATHER = "allgather"
COLL_ALLTOALL = "alltoall"
COLL_REDUCE = "reduce"
COLL_ALLREDUCE = "allreduce"
COLL_REDUCE_SCATTER = "reduce_scatter"
COLL_SCAN = "scan"
COLL_EXSCAN = "exscan"

# --- Collective algorithms -------------------------------------------------
# Exposed so that the decision function is observable and overridable, in the
# spirit of MPICH cvars and Open MPI's `coll tuned` decision files.
ALGO_AUTO = "auto"
ALGO_LINEAR = "linear"
ALGO_FLAT = "flat"
ALGO_BINOMIAL = "binomial"
ALGO_CHAIN = "chain"
ALGO_RECURSIVE_DOUBLING = "recursive_doubling"
ALGO_RING = "ring"
ALGO_RABENSEIFNER = "rabenseifner"
ALGO_BRUCK = "bruck"
ALGO_DISSEMINATION = "dissemination"
ALGO_OFFLOAD = "offload"

# --- Reduction operator classes -------------------------------------------
# STRUCTURAL ops execute inside the library, cost no tokens, and are exactly
# reproducible.  SEMANTIC ops require an LLM turn: the library performs an
# upcall into the calling agent, which is the AgentMPI analogue of the user
# function registered through MPI_Op_create.
OP_STRUCTURAL = "structural"
OP_SEMANTIC = "semantic"

# --- Error classes ---------------------------------------------------------
# MPI error classes plus five that have no MPI counterpart, each corresponding
# to a failure mode that MPI leaves as undefined behaviour but that an agent
# runtime must diagnose because its participants are unreliable interpreters of
# the protocol.
AMPI_SUCCESS = 0
AMPI_ERR_COMM = 5
AMPI_ERR_RANK = 6
AMPI_ERR_TAG = 7
AMPI_ERR_ARG = 12
AMPI_ERR_TIMEOUT = 24
AMPI_ERR_REVOKED = 74
AMPI_ERR_PROC_FAILED = 75
AMPI_ERR_PROC_FAILED_PENDING = 76
# --- no MPI counterpart ---
AMPI_ERR_CONTEXT_EXHAUSTED = 128
AMPI_ERR_COLLECTIVE_MISMATCH = 129
AMPI_ERR_DEADLOCK = 130
AMPI_ERR_PROTOCOL_VIOLATION = 131
AMPI_ERR_BUDGET_EXCEEDED = 132

ERROR_NAMES = {
    AMPI_SUCCESS: "AMPI_SUCCESS",
    AMPI_ERR_COMM: "AMPI_ERR_COMM",
    AMPI_ERR_RANK: "AMPI_ERR_RANK",
    AMPI_ERR_TAG: "AMPI_ERR_TAG",
    AMPI_ERR_ARG: "AMPI_ERR_ARG",
    AMPI_ERR_TIMEOUT: "AMPI_ERR_TIMEOUT",
    AMPI_ERR_REVOKED: "AMPI_ERR_REVOKED",
    AMPI_ERR_PROC_FAILED: "AMPI_ERR_PROC_FAILED",
    AMPI_ERR_PROC_FAILED_PENDING: "AMPI_ERR_PROC_FAILED_PENDING",
    AMPI_ERR_CONTEXT_EXHAUSTED: "AMPI_ERR_CONTEXT_EXHAUSTED",
    AMPI_ERR_COLLECTIVE_MISMATCH: "AMPI_ERR_COLLECTIVE_MISMATCH",
    AMPI_ERR_DEADLOCK: "AMPI_ERR_DEADLOCK",
    AMPI_ERR_PROTOCOL_VIOLATION: "AMPI_ERR_PROTOCOL_VIOLATION",
    AMPI_ERR_BUDGET_EXCEEDED: "AMPI_ERR_BUDGET_EXCEEDED",
}

# --- Defaults --------------------------------------------------------------
DEFAULT_CTX_LIMIT = 120_000
DEFAULT_EAGER_LIMIT = 2_000
DEFAULT_HEARTBEAT_PERIOD = 20.0
DEFAULT_FAILURE_TIMEOUT = 180.0
DEFAULT_POLL_INTERVAL = 0.35
