"""Devices: the transports below AgentMPI's narrow waist.

Importing this package registers every bundled device.  Third-party devices
register themselves by applying :func:`~ampi.device.base.register_device` to a
subclass of :class:`~ampi.device.base.Device`; the conformance suite discovers
them from the registry, so a new transport is validated by the same assertions
that validate ours.
"""

from __future__ import annotations

from .base import (
    Cell,
    Device,
    Ge,
    Gt,
    In,
    IsNull,
    Le,
    Lease,
    Lt,
    Ne,
    NotIn,
    NotNull,
    Predicate,
    available_devices,
    get_device,
    matches,
    open_device,
    register_device,
)
from .gitd import GitdDevice
from .gitlog import GitDevice
from .hub import HubDevice
from .journal import JournalDevice
from .memory import MemoryDevice
from .sqlite import SqliteDevice

__all__ = [
    "Cell",
    "Device",
    "Ge",
    "GitDevice",
    "GitdDevice",
    "Gt",
    "HubDevice",
    "In",
    "IsNull",
    "JournalDevice",
    "Le",
    "Lease",
    "Lt",
    "MemoryDevice",
    "Ne",
    "NotIn",
    "NotNull",
    "Predicate",
    "SqliteDevice",
    "available_devices",
    "get_device",
    "matches",
    "open_device",
    "register_device",
]
