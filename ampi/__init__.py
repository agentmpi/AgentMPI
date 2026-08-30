"""AgentMPI: a message passing interface for multi-agent systems.

AgentMPI is a *protocol*, not a multi-agent system. It plays the role MPI plays
in parallel computing: it standardises how independent executors name each
other, exchange information, synchronise, share state, and survive each other's
failure, and it leaves what they actually do entirely to the harness author.

The reference runtime is organised in layers, bottom-up:

``ampi.journal``
    Durable job state: a per-job SQLite journal plus a content-addressed object
    store. Everything else is a library over this.
``ampi.tokens``
    Token accounting. Tokens, not bytes, are the unit of transfer cost.
``ampi.core``
    The universe: ranks, epochs, leases, communicators, the context ledger, and
    the eager/rendezvous packaging decision.
``ampi.p2p``
    Point-to-point send/recv with MPI matching semantics.
``ampi.ops``
    Reduction operators, both runtime-evaluated and agent-evaluated.
``ampi.views``
    Bounded projections of payloads: the derived-datatype analogue.
``ampi.collectives``
    Barrier, broadcast, reduce, gather, scatter, alltoall, scan, and their
    algorithm catalogue.
``ampi.rma``
    Windows: versioned shared state with atomics and leased locks.
``ampi.topology``
    Communicator construction, Cartesian and graph topologies.
``ampi.ft``
    Revoke, shrink, agree, respawn, recover.
``ampi.launcher``
    ``mpirun``: job creation, rank prompts, supervision.
``ampi.trace``
    Event trace export and metrics.
``ampi.cli``
    The command-line binding agents actually call.
"""

from __future__ import annotations

from .version import PROTOCOL_VERSION, SCHEMA_VERSION, __version__

__all__ = ["PROTOCOL_VERSION", "SCHEMA_VERSION", "__version__"]
