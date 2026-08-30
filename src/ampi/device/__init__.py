"""AgentMPI devices (transports).

Two devices ship with the reference implementation, chosen to bracket the
design space that real MPI implementations occupy:

``sqlite``
    A single durable file that every rank opens.  Every operation is
    serialised by one writer, which makes the semantics easy to reason about
    and gives crash durability for free.  Analogous to a shared-memory or
    collective-offload fabric where one agent observes all traffic.

``filesystem``
    POSIX mailbox directories with write-then-rename delivery and no
    coordinator on the data path.  Analogous to a distributed-memory fabric.

Both implement the six abstract device capabilities defined in ``base.py``;
``tests/test_device_conformance.py`` runs the identical suite against both,
including the concurrency properties the protocol depends on.
"""

from .base import Device
from .fs_device import FsDevice
from .sqlite_device import SqliteDevice

__all__ = ["Device", "SqliteDevice", "FsDevice", "open_device"]


def open_device(uri: str) -> Device:
    """Open a device from a URI.

    ``sqlite:///path/job.db``, ``fs:///path/jobdir``, or a bare path (SQLite).
    The indirection exists so that layers above never name a concrete
    transport, which is what makes the abstract device interface a claim that
    can be tested rather than a diagram.
    """
    if uri.startswith("sqlite://"):
        device: Device = SqliteDevice(uri[len("sqlite://"):])
    elif uri.startswith("fs://"):
        device = FsDevice(uri[len("fs://"):])
    else:
        device = SqliteDevice(uri)
    device.initialize()
    return device
