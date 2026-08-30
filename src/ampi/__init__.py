"""AgentMPI: a message-passing interface protocol for multi-agent harnesses.

AgentMPI is a *protocol*, not a multi-agent system.  It is to agent harnesses
what MPI is to parallel programs: a fixed vocabulary of communication,
synchronisation, sharing and failure-mitigation operations with defined
semantics, which many different harnesses can be written against and many
different runtimes can implement.

The package layers exactly as MPICH does:

    ampi.api / ampi.cli      language bindings
    ampi.core                    portable protocol semantics
    ampi.device                  abstract device interface + transports
    ampi.launch                  process manager (the ``mpirun`` analogue)
"""

from .constants import SPEC_VERSION
from .errors import (
    AmpiCollectiveMismatch,
    AmpiContextExhausted,
    AmpiDeadlock,
    AmpiError,
    AmpiProcFailed,
    AmpiRevoked,
    AmpiTimeout,
)

__version__ = "0.1.0"
__all__ = [
    "SPEC_VERSION",
    "AmpiError",
    "AmpiTimeout",
    "AmpiRevoked",
    "AmpiProcFailed",
    "AmpiContextExhausted",
    "AmpiCollectiveMismatch",
    "AmpiDeadlock",
    "__version__",
]


def Ampi(*args, **kwargs):  # noqa: N802 - matches the class it constructs
    """Lazily construct the Python binding, so importing the package is cheap."""
    from .api import Ampi as _Ampi

    return _Ampi(*args, **kwargs)
