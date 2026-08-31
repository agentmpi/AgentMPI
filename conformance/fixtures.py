"""Fixtures shared by both conformance suites."""

from __future__ import annotations

import os
from typing import Any

import ampi.device  # noqa: F401  (registers the bundled devices)
from ampi.device import Device, available_devices, get_device

__all__ = ["device_ids", "make_device", "SKIP_VOLATILE"]

#: Devices excluded by ``AMPI_CONFORMANCE_DEVICES`` are skipped, so a third party
#: can run the suite against only their transport.
_SELECTED = os.environ.get("AMPI_CONFORMANCE_DEVICES", "")


def device_ids() -> list[str]:
    names = available_devices()
    if _SELECTED:
        wanted = {n.strip() for n in _SELECTED.split(",") if n.strip()}
        names = [n for n in names if n in wanted]
    return names


def make_device(name: str, root: str, **kw: Any) -> Device:
    dev = get_device(name)(root, **kw)  # type: ignore[call-arg]
    dev.initialize()
    return dev


#: Tests that require state to survive a process boundary skip volatile devices.
SKIP_VOLATILE = "device is not durable; the protocol refuses it for multi-process jobs"
