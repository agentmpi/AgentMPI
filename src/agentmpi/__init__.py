"""AgentMPI -- a message-passing interface for multi-agent harnesses.

AgentMPI is a *protocol*, not a framework.  It specifies how independently
written agent processes name each other, exchange messages, synchronise,
share state, and survive each other's failures -- and then gets out of the
way.  What the agents do, which models they run, and how they are prompted
are none of the protocol's business, exactly as MPI has no opinion about
what your ranks compute.

The public surface follows MPI's naming so that anyone who has written a
parallel program can read a harness without a manual::

    import agentmpi as ampi

    comm = ampi.init().world
    rank, size = comm.rank, comm.size

    chunk = comm.scatter(chapters if rank == 0 else None, root=0)
    glossary = comm.exscan(local_terms(chunk), ampi.UNION)   # log(p) rounds
    translated = translate(chunk, glossary)
    book = comm.gather(translated, root=0)

    ampi.finalize()

Every name below is the Python spelling of a protocol operation; the
protocol spellings (``AMPI_Comm_scatter`` and so on) are given in
``spec/agentmpi-spec.md`` and are what the ``ampi`` command-line tool
exposes to agents that have a shell but no library bindings.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .constants import (
    ANY_SOURCE,
    ANY_TAG,
    PROC_NULL,
    PROTOCOL_VERSION,
    TAG_UB,
    UNDEFINED,
    CollAlgorithm,
    CommSplitType,
    Datatype,
    ErrorClass,
    RankState,
    SendMode,
    ThreadLevel,
)
from .context import (
    ContextBudget,
    ReductionPlan,
    feasible_allgather,
    peak_ingest_allgather,
    plan_reduction,
    safe_fanout,
)
from .datatypes import (
    ARTIFACT,
    DIGEST,
    JSON_,
    NULL,
    PATCH,
    TEXT,
    TOOLCALL,
    TypeDescriptor,
    type_bounded,
    type_contiguous,
    type_contract,
    type_struct,
)
from .envelope import Envelope, Status
from .errors import (
    AmpiError,
    BudgetError,
    ContextOverflowError,
    ContractError,
    DriftError,
    ERRORS_ABORT,
    ERRORS_ARE_FATAL,
    ERRORS_RETURN,
    ProcFailedError,
    RevokedError,
    StalledError,
    TimeoutError_,
)
from .ft import (
    Checkpoint,
    RestartPolicy,
    Supervisor,
    checkpoint,
    comm_agree,
    comm_replace,
    comm_revoke,
    comm_shrink,
    restore,
    with_retry,
)
from .group import Group, RankSpec, RankTable
from .ops import (
    CONCAT,
    FIRST,
    LAND,
    LAST,
    LOR,
    MAX,
    MAX_BY_SCORE,
    MERGE_JSON,
    MIN,
    PATCH_MERGE,
    SUM,
    UNION,
    VOTE,
    Op,
    SemanticOpRegistry,
    op_create,
    semantic_op,
    summarize_op,
)
from .runtime import (
    Runtime,
    RunManifest,
    abort,
    comm_world,
    current,
    finalize,
    get_processor_name,
    init,
    initialized,
    wtime,
)
from .topology import (
    CartesianTopology,
    GraphTopology,
    analyse,
    cart_create,
    dist_graph_create,
    pipeline_create,
)
from .trace import Event, Profiler, communication_matrix, critical_path, summarize
from .win import Reference, Window, win_allocate_shared, win_create

# Protocol-style aliases, so that harness code can read like MPI code.
AMPI_ANY_SOURCE = ANY_SOURCE
AMPI_ANY_TAG = ANY_TAG
AMPI_PROC_NULL = PROC_NULL
AMPI_UNDEFINED = UNDEFINED
AMPI_TAG_UB = TAG_UB
AMPI_TEXT = TEXT
AMPI_JSON = JSON_
AMPI_PATCH = PATCH
AMPI_ARTIFACT = ARTIFACT
AMPI_DIGEST = DIGEST
AMPI_NULL = NULL
AMPI_CONCAT = CONCAT
AMPI_UNION = UNION
AMPI_FIRST = FIRST
AMPI_LAST = LAST
AMPI_VOTE = VOTE
AMPI_SUM = SUM
AMPI_MAX = MAX
AMPI_MIN = MIN
AMPI_LAND = LAND
AMPI_LOR = LOR
AMPI_MAXLOC = MAX_BY_SCORE
AMPI_PATCH_MERGE = PATCH_MERGE
AMPI_MERGE_JSON = MERGE_JSON

__all__ = [
    "__version__",
    "PROTOCOL_VERSION",
    # lifecycle
    "init", "finalize", "current", "initialized", "comm_world", "abort", "wtime",
    "get_processor_name", "Runtime", "RunManifest",
    # constants
    "ANY_SOURCE", "ANY_TAG", "PROC_NULL", "UNDEFINED", "TAG_UB",
    "AMPI_ANY_SOURCE", "AMPI_ANY_TAG", "AMPI_PROC_NULL", "AMPI_UNDEFINED", "AMPI_TAG_UB",
    "SendMode", "ThreadLevel", "RankState", "CommSplitType", "CollAlgorithm",
    "Datatype", "ErrorClass",
    # datatypes
    "TypeDescriptor", "TEXT", "JSON_", "PATCH", "ARTIFACT", "TOOLCALL", "DIGEST", "NULL",
    "AMPI_TEXT", "AMPI_JSON", "AMPI_PATCH", "AMPI_ARTIFACT", "AMPI_DIGEST", "AMPI_NULL",
    "type_bounded", "type_contract", "type_struct", "type_contiguous",
    # groups
    "Group", "RankSpec", "RankTable",
    # messages
    "Envelope", "Status",
    # ops
    "Op", "op_create", "semantic_op", "summarize_op", "SemanticOpRegistry",
    "CONCAT", "UNION", "FIRST", "LAST", "VOTE", "SUM", "MAX", "MIN", "LAND", "LOR", "MERGE_JSON",
    "MAX_BY_SCORE", "PATCH_MERGE",
    "AMPI_CONCAT", "AMPI_UNION", "AMPI_FIRST", "AMPI_LAST", "AMPI_VOTE", "AMPI_SUM", "AMPI_MAX", "AMPI_MIN",
    "AMPI_LAND", "AMPI_LOR", "AMPI_MAXLOC", "AMPI_PATCH_MERGE", "AMPI_MERGE_JSON",
    # context
    "ContextBudget", "plan_reduction", "safe_fanout", "ReductionPlan",
    "peak_ingest_allgather", "feasible_allgather",
    # rma
    "Window", "Reference", "win_create", "win_allocate_shared",
    # topology
    "CartesianTopology", "GraphTopology", "cart_create", "dist_graph_create",
    "pipeline_create", "analyse",
    # fault tolerance
    "comm_revoke", "comm_shrink", "comm_agree", "comm_replace", "checkpoint",
    "restore", "with_retry", "Checkpoint", "RestartPolicy", "Supervisor",
    # errors
    "AmpiError", "ProcFailedError", "RevokedError", "StalledError", "ContractError",
    "BudgetError", "ContextOverflowError", "DriftError", "TimeoutError_",
    "ERRORS_ARE_FATAL", "ERRORS_RETURN", "ERRORS_ABORT",
    # tracing
    "Profiler", "Event", "summarize", "critical_path", "communication_matrix",
]
