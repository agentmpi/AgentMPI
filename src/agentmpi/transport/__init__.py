"""Transport devices (the AgentMPI Abstract Device Interface)."""

from .base import Device
from .inproc import InprocDevice
from .journal_device import FileLock, JournalDevice

__all__ = ["Device", "InprocDevice", "JournalDevice", "FileLock"]


def open_device(kind: str, root: str | None = None, owner: str = "?") -> Device:
    """Instantiate a device by name (the ``ampi_device`` control variable)."""
    if kind == "journal":
        if root is None:
            raise ValueError("the journal device requires a root directory")
        return JournalDevice(root, owner=owner)
    if kind == "inproc":
        return InprocDevice(owner=owner)
    raise ValueError(f"unknown device {kind!r}")
