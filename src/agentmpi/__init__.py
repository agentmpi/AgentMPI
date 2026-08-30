"""AgentMPI: a message passing interface for multi-agent harness development.

AgentMPI is not a multi-agent system.  It is the layer one writes multi-agent
systems *with*, in the sense that MPI is the layer one writes parallel programs
with: a small set of composable primitives — communicators, point-to-point
transfer with typed contracts, collectives with explicit algorithms, one-sided
shared state with epochs and locks, and failure mitigation — plus a runtime that
holds all protocol state durably outside the agents.

Minimal example (data-parallel, deterministic, runs anywhere)::

    import agentmpi as ampi

    def rank_main(comm):
        chunk = comm.scatter(WORK if comm.rank == 0 else None, root=0)
        result = comm.agent(f"Do the work: {chunk}")
        return comm.gather(result, root=0)

    job = ampi.launch(rank_main, size=8, executor_factory=lambda r: my_agent)

The names deliberately track MPI's so that anyone who knows MPI can read an
AgentMPI harness, and so that the places where the semantics *had* to change are
conspicuous.  Those places are documented in ``docs/spec/agentmpi-spec.md`` and
argued in the paper under ``paper/``.
"""

from __future__ import annotations

from . import algorithms, cost, ft, ops, rma, schema, sim, topology, tokens
from .comm import Communicator, Group, Message, Request, Status
from .constants import (
    ANY_SOURCE,
    ANY_TAG,
    DEFAULT_CONTEXT_BUDGET,
    DEFAULT_EAGER_LIMIT,
    UNDEFINED,
    Associativity,
    BarrierPolicy,
    FailureClass,
    LockType,
    Mode,
    RankState,
    RestartPolicy,
    WinMemoryModel,
)
from .errors import (
    AmpiContextOverflow,
    AmpiError,
    AmpiProcFailed,
    AmpiRevoked,
    AmpiTimeout,
    AmpiTruncateError,
    AmpiTypeError,
    AmpiUsageError,
    AmpiValidationError,
)
from .executor import (
    BrokerExecutor,
    Executor,
    FunctionExecutor,
    ReplayExecutor,
    SimulatedExecutor,
)
from .fabric import Fabric, open_fabric
from .ft import (
    ChildSpec,
    Supervisor,
    agree,
    declare_failed,
    get_failed,
    health,
    replicate_and_compare,
    revoke,
    shrink,
    verify,
)
from .ops import CONCAT, LAND, LOR, MAX, MIN, SUM, UNION, Op, ReduceContext, semantic_op
from .rank import AgentResult, RankRuntime
from .rma import Window, win_create
from .runtime import JobResult, RankOutcome, Session, create_job, finalize, init, launch, spawn, world
from .schema import ANY_CONTRACT, Contract, Validator, View, contract_validator
from .topology import (
    PROC_NULL,
    CartTopology,
    GraphTopology,
    cart_create,
    dist_graph_create,
    halo_exchange,
    neighbor_allgather,
    neighbor_alltoall,
    review_edges,
    ring_edges,
)

__version__ = "0.1.0"

__all__ = [
    # runtime
    "init",
    "finalize",
    "launch",
    "spawn",
    "create_job",
    "world",
    "Session",
    "JobResult",
    "RankOutcome",
    "Fabric",
    "open_fabric",
    # communication
    "Communicator",
    "Group",
    "Message",
    "Request",
    "Status",
    "ANY_SOURCE",
    "ANY_TAG",
    "UNDEFINED",
    "Mode",
    # types
    "Contract",
    "View",
    "Validator",
    "ANY_CONTRACT",
    "contract_validator",
    # reduction
    "Op",
    "ReduceContext",
    "semantic_op",
    "SUM",
    "MAX",
    "MIN",
    "LAND",
    "LOR",
    "CONCAT",
    "UNION",
    "Associativity",
    # one-sided
    "Window",
    "win_create",
    "WinMemoryModel",
    "LockType",
    # topology
    "CartTopology",
    "GraphTopology",
    "cart_create",
    "dist_graph_create",
    "neighbor_allgather",
    "neighbor_alltoall",
    "halo_exchange",
    "ring_edges",
    "review_edges",
    "PROC_NULL",
    # fault tolerance
    "FailureClass",
    "BarrierPolicy",
    "RestartPolicy",
    "RankState",
    "Supervisor",
    "ChildSpec",
    "revoke",
    "shrink",
    "agree",
    "health",
    "declare_failed",
    "get_failed",
    "verify",
    "replicate_and_compare",
    # executors
    "Executor",
    "FunctionExecutor",
    "SimulatedExecutor",
    "ReplayExecutor",
    "BrokerExecutor",
    "AgentResult",
    "RankRuntime",
    # errors
    "AmpiError",
    "AmpiUsageError",
    "AmpiTimeout",
    "AmpiTruncateError",
    "AmpiTypeError",
    "AmpiContextOverflow",
    "AmpiProcFailed",
    "AmpiRevoked",
    "AmpiValidationError",
    # submodules
    "algorithms",
    "cost",
    "ft",
    "ops",
    "rma",
    "schema",
    "sim",
    "tokens",
    "topology",
    "DEFAULT_CONTEXT_BUDGET",
    "DEFAULT_EAGER_LIMIT",
]
