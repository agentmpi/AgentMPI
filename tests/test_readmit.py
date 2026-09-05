"""A rank convicted for a lapsed lease re-admits itself; a killed rank does not.

The lease detector cannot tell a dead rank from one whose renewals keep losing
the race on a slow shared transport.  The runtime resolves the ambiguity in
the rank's favour when the rank itself turns up: its next touch re-admits it
at a new epoch.  An administrative kill is a decision the rank may not
overrule, and stays fenced.
"""

from __future__ import annotations

import time

from ampi.constants import STATE_FAILED, STATE_RUNNING
from ampi.runtime import Ampi


def _job(tmp_path):
    return Ampi.create(str(tmp_path / "j"), 2, device="sqlite", force=True)


def _convict_by_lease(root: str) -> tuple[Ampi, Ampi]:
    r0 = Ampi(root, rank=0)
    r1 = Ampi(root, rank=1)
    r0.init(lease_s=60)
    r1.init(lease_s=0.05)
    time.sleep(0.15)
    r0.detect_failures(confirm_s=0)  # suspect
    r0.detect_failures(confirm_s=0)  # confirm
    assert r1._rankview().state == STATE_FAILED
    assert r1._rankview().failure_kind == "lease_expired"
    return r0, r1


def test_lease_convicted_rank_readmits_on_touch(tmp_path):
    job = _job(tmp_path)
    r0, r1 = _convict_by_lease(job.root)
    r1._last_touch = 0.0
    r1.touch()
    view = r1._rankview()
    assert view.state == STATE_RUNNING
    assert view.epoch == 2, "re-admission is a new epoch, like a restart"
    kinds = [e["kind"] for e in job.events()]
    assert "failure.readmit" in kinds
    for a in (r0, r1, job):
        a.close()


def test_killed_rank_is_not_readmitted_by_touch(tmp_path):
    job = _job(tmp_path)
    r0 = Ampi(job.root, rank=0)
    r1 = Ampi(job.root, rank=1)
    r0.init(lease_s=60)
    r1.init(lease_s=60)
    r0.kill(1, reason="test")
    r1._last_touch = 0.0
    r1.touch()
    view = r1._rankview()
    assert view.state == STATE_FAILED and view.failure_kind == "killed"
    assert view.epoch == 1
    for a in (r0, r1, job):
        a.close()
