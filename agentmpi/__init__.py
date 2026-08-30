"""Agent Message Passing Interface (AgentMPI).

A portable message-passing protocol for writing multi-agent harnesses,
modeled on MPI. Import the public surface and write SPMD harnesses:

    from agentmpi import Init, Finalize, COMM_WORLD, Op

    Init()
    if COMM_WORLD.rank == 0:
        COMM_WORLD.bcast({"task": "translate"}, root=0)
    else:
        COMM_WORLD.bcast(None, root=0)
    Finalize()
"""

from agentmpi.constants import (
    ANY_SOURCE,
    ANY_TAG,
    COMM_WORLD_NAME,
    LOCK_EXCLUSIVE,
    LOCK_SHARED,
    PROTOCOL_VERSION,
)
from agentmpi.errors import (
    AgentMPIError,
    ContextBudgetExceeded,
    DeadRankError,
    RevokedCommunicatorError,
    TimeoutError,
)
from agentmpi.runtime import COMM_WORLD, Finalize, Init, attach
from agentmpi.types import Envelope, Lifecycle, Message, Op, RankStatus

__all__ = [
    "ANY_SOURCE",
    "ANY_TAG",
    "COMM_WORLD",
    "COMM_WORLD_NAME",
    "PROTOCOL_VERSION",
    "LOCK_EXCLUSIVE",
    "LOCK_SHARED",
    "Init",
    "Finalize",
    "attach",
    "Op",
    "Lifecycle",
    "Envelope",
    "Message",
    "RankStatus",
    "AgentMPIError",
    "DeadRankError",
    "RevokedCommunicatorError",
    "TimeoutError",
    "ContextBudgetExceeded",
]

__version__ = "0.1.0"
