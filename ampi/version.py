"""Version and protocol-level constants for the AgentMPI reference runtime."""

from __future__ import annotations

#: Version of this reference implementation (``ampi`` runtime + CLI).
__version__ = "0.1.0"

#: Version of the AgentMPI *protocol* this runtime implements. Agents and
#: harnesses should check this, not ``__version__``: the protocol is the
#: contract, the runtime is one implementation of it.
PROTOCOL_VERSION = "AgentMPI/0.1"

#: On-disk journal schema version. Bumped whenever the SQLite schema changes in
#: a way that older journals cannot be read with.
SCHEMA_VERSION = 4
