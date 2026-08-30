"""Test configuration.

The one non-obvious thing here is the watchdog.  Every bug in a message-passing
runtime that is worth catching manifests as a hang, and a hang in a test suite
produces no diagnostic at all unless something dumps the stacks of every thread.
``faulthandler.dump_traceback_later`` does exactly that, per test, so a
regression that deadlocks a collective reports *where* every rank is blocked
instead of timing out silently in CI.
"""

from __future__ import annotations

import faulthandler
import sys

import pytest

#: Generous enough for the largest parametrised collective, short enough that a
#: genuine deadlock reports quickly.
WATCHDOG_SECONDS = 120.0


@pytest.fixture(autouse=True)
def _watchdog():
    faulthandler.dump_traceback_later(WATCHDOG_SECONDS, exit=True, file=sys.stderr)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def _fast_leases(monkeypatch):
    """Keep default launcher deadlines short so a stuck rank fails the test fast."""
    import agentmpi.runtime as runtime

    original = runtime.launch

    def patched(*args, **kwargs):
        kwargs.setdefault("timeout", 90.0)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "launch", patched)
    monkeypatch.setattr("agentmpi.launch", patched)
    yield
