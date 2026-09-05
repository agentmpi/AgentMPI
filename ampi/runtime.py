"""The AgentMPI runtime: one class composing the protocol's chapters.

Two ways to write a harness are supported, and the difference between them is
measurable and worth measuring.

*Harness-side (SPMD).*  The harness author writes a ``rank_main`` executed once per
rank by trusted host-side code.  Every AgentMPI call is made by that code, and the
agent is invoked as a kernel that transforms artifacts.  Protocol conformance is a
property of the runtime.

*Agent-side.*  The executor itself issues AgentMPI operations through the command
binding.  Protocol conformance becomes a property of model behaviour: a rank that
forgets to enter a barrier does not merely produce a worse answer, it prevents the
population from making progress.

Both must be supported, because agent-side is sometimes the only option and
because it is where the interesting failures live.  The reference documentation
recommends harness-side as the default.
"""

from __future__ import annotations

from typing import Any

from .constants import CONFORMANCE_LEVELS, PROTOCOL_VERSION, RUNTIME_VERSION
from .core.base import RuntimeBase
from .core.collectives import CollectiveMixin
from .core.comm import CommMixin
from .core.ft import FaultMixin
from .core.iface import IfaceMixin
from .core.p2p import P2PMixin
from .core.pool import PoolMixin
from .core.rma import RmaMixin

__all__ = ["Ampi", "conformance"]


class Ampi(
    P2PMixin,
    CollectiveMixin,
    CommMixin,
    RmaMixin,
    PoolMixin,
    FaultMixin,
    IfaceMixin,
    RuntimeBase,
):
    """A rank's handle on an AgentMPI job.

    Construct one per rank.  There is no process-global state: two independent
    sessions coexist in one process, which MPI-4 had to add sessions to achieve and
    which matters more here, because an AgentMPI harness is very often itself a
    component invoked by a larger agent system.  A protocol whose runtime is a
    process-wide singleton cannot compose.
    """

    def info(self) -> dict[str, Any]:
        m = self.manifest
        return {
            "protocol": PROTOCOL_VERSION,
            "runtime": RUNTIME_VERSION,
            "job": m.job_id,
            "size": m.size,
            "device": self.device.name,
            "durable": self.device.durable,
            "token_counter": m.token_counter,
            "eager_threshold": m.eager_threshold,
            "ctx_budget": m.ctx_budget,
            "conformance": conformance(),
            "root": self.root,
        }


def conformance() -> dict[str, Any]:
    """What this implementation claims to provide.

    A harness should be able to ask what it may rely on rather than discovering an
    absence at run time.  The levels are cumulative and the omissions are named,
    following MPI's practice of standardising only what is understood: a standard
    that answers a research question prematurely is worse than one that leaves a
    hook.
    """
    return {
        "protocol": PROTOCOL_VERSION,
        "level": "L5",
        "levels": CONFORMANCE_LEVELS,
        "provides": sorted(
            [
                "init", "finalize", "heartbeat", "abort", "recover", "memo",
                "send", "ssend", "rsend", "isend", "recv", "irecv", "wait", "test",
                "cancel", "sendrecv", "probe", "inbox",
                "barrier", "bcast", "scatter", "gather", "allgather", "alltoall",
                "reduce", "allreduce", "scan", "exscan", "neighbor_allgather",
                "op_commit", "op_arbitrate",
                "comm_create", "comm_dup", "comm_split", "comm_free",
                "cart_create", "cart_shift", "graph_create", "neighbours",
                "win_create", "put", "get", "accumulate", "compare_and_swap",
                "fetch_and_op", "claim", "win_ls", "win_history", "win_fence",
                "win_lock", "win_unlock",
                "comm_revoke", "comm_shrink", "comm_agree", "failure_ack",
                "kill", "respawn", "supervise",
                "iface_publish", "iface_list", "iface_get", "iface_wait",
                "iface_verify", "iface_report",
                "view", "contract", "trace", "doctor",
            ]
        ),
        "omits": {
            "reduce_scatter": "specified, not provided for agent-evaluated operators",
            "agent_scan": "an agent-evaluated prefix reduction is an open question",
            "intercommunicators": "little value without distinct address spaces",
            "persistent_collectives": "no measured benefit in this cost regime",
            "partitioned_communication": "no analogue of a partitioned buffer yet",
            "byzantine_tolerance": "acknowledged and out of scope",
            "automatic_context_compaction": "a research question; the hook is Type_view",
        },
        "devices": ["sqlite", "journal", "memory", "git", "gitd"],
    }
