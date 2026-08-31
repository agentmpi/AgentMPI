"""The AgentMPI conformance suite.

An interface with one implementation is a library.  What makes MPI a standard is
that a program written against the document runs on MPICH, on Open MPI, and on a
vendor's stack, and that there is a way to tell whether a new implementation has
got it right.  This package is that way.

Two levels of suite live here:

``test_device.py``
    Validates a :class:`~ampi.device.base.Device` --- the six capabilities below
    the waist.  Parametrised over every device in the registry, so adding a
    transport means adding a registration, not a test file.

``test_protocol.py``
    Validates the portable layer above the waist against the specification, again
    parametrised over every device.  A device that passes this can carry any
    conforming harness; an implementation that passes it may claim the
    conformance level it reports.

Both suites are written against public interfaces only.  They import no private
name from the reference runtime, so they can be pointed at a different
implementation by changing one fixture.
"""

from __future__ import annotations

__all__ = ["device_ids", "make_device"]

from .fixtures import device_ids, make_device
