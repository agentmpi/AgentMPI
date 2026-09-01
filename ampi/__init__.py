"""AgentMPI: a message passing interface for multi-agent systems.

AgentMPI is a *protocol*, not a multi-agent system.  It stands to multi-agent
systems as MPI stands to parallel applications: a library that harness authors
call, not a harness.  It does not decide how many agents to run, what they should
do, how to prompt them, which model to use, or how to recover from failure.  It
provides the mechanisms with which those decisions are expressed.

It is also deliberately **semantics-thin**.  It does not interpret payloads.  It
has no ontology, no speech acts, no commitment semantics, no notion of belief or
intention.  A message is an opaque body plus an envelope of size and provenance
metadata.  This is a considered rejection of the KQML/FIPA-ACL lineage, whose
mentalistic semantics proved both unverifiable and unnecessary, in favour of MPI's
stance: standardise the mechanism, leave meaning to the application.

Quick start, harness-side::

    from ampi import Ampi

    job = Ampi.create("/tmp/job", size=8, device="sqlite")
    rank0 = Ampi("/tmp/job", rank=0)
    rank0.init()
    rank0.bcast("plan", payload={"task": "translate"}, root=0)

Quick start, agent-side: every operation above is a subcommand of ``ampi``, and
an executor that can run a shell command is a rank.
"""

from __future__ import annotations

from .constants import (
    ANY_SOURCE,
    ANY_TAG,
    PROC_NULL,
    PROTOCOL_VERSION,
    RUNTIME_VERSION,
    UNDEFINED,
)
from .core.context import Ledger, ResidencyModel
from .core.ops import OPS, Op, get_op, register_op
from .core.payload import Contract, Envelope, Payload, apply_view
from .core.safety import Coll, Local, Program, Recv, Send, analyse
from .device import Device, available_devices, register_device
from .errors import ERROR_CLASSES, AmpiError
from .runtime import Ampi, conformance

__version__ = RUNTIME_VERSION

__all__ = [
    "ANY_SOURCE",
    "ANY_TAG",
    "Ampi",
    "AmpiError",
    "Coll",
    "Contract",
    "Device",
    "ERROR_CLASSES",
    "Envelope",
    "Ledger",
    "Local",
    "OPS",
    "Op",
    "PROC_NULL",
    "PROTOCOL_VERSION",
    "Payload",
    "Program",
    "Recv",
    "ResidencyModel",
    "RUNTIME_VERSION",
    "Send",
    "UNDEFINED",
    "analyse",
    "apply_view",
    "available_devices",
    "conformance",
    "get_op",
    "register_device",
    "register_op",
    "__version__",
]
