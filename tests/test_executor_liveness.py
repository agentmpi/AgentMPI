"""A busy executor is not a dead one.

Several ranks on one machine share one queue and one executor, which claims
their tasks in turn.  A task that sits unclaimed for the whole claim window
because three siblings' tasks were ahead of it is waiting on supply, not on a
corpse; NO_WORKER must be reserved for a queue in which nothing moved at all.
"""

from __future__ import annotations

import threading
import time

import pytest

from ampi.core.payload import Contract
from ampi.errors import AmpiError
from ampi.executor import BrokerExecutor, Task, new_aid
from ampi.runtime import Ampi


def _queue(tmp_path):
    return Ampi.create(str(tmp_path / "q"), 2, device="sqlite", force=True)


def _task(rank: int) -> Task:
    return Task(aid=new_aid(), rank=rank, label="translate:1", prompt="p",
                contract=Contract.parse({"kind": "json"}), meta={})


def test_dead_executor_raises_no_worker_after_one_window(tmp_path):
    q = _queue(tmp_path)
    ex = BrokerExecutor(q, campaign="c", work_dir=tmp_path / "w", timeout_s=30,
                        claim_wait_s=1.0)
    ex.open()
    t0 = time.time()
    with pytest.raises(AmpiError) as ei:
        ex.invoke(_task(1))
    assert ei.value.cls_name == "AMPI_ERR_NO_WORKER"
    assert time.time() - t0 < 2.5, "nothing moved: give up after one window"
    assert not any(e["kind"] == "broker.busy" for e in q.events())
    q.close()


def test_busy_executor_defers_no_worker(tmp_path):
    q = _queue(tmp_path)
    ex = BrokerExecutor(q, campaign="c", work_dir=tmp_path / "w", timeout_s=30,
                        claim_wait_s=1.0)
    ex.open()

    def sibling_claim() -> None:
        # Another rank's task on the same queue gets claimed mid-window: the
        # executor is alive and busy.
        time.sleep(0.4)
        q.device.append("task", {"rank": 0, "state": "claimed", "campaign": "c",
                                 "run": q.manifest.job_id, "aid": new_aid(),
                                 "label": "translate:0", "prompt_file": "", "result_file": "",
                                 "contract": None, "meta": {},
                                 "queued_at": q.device.clock(), "claimed_at": q.device.clock()})

    threading.Thread(target=sibling_claim, daemon=True).start()
    t0 = time.time()
    with pytest.raises(AmpiError) as ei:
        ex.invoke(_task(1))
    waited = time.time() - t0
    assert ei.value.cls_name == "AMPI_ERR_NO_WORKER"
    assert waited >= 1.8, f"a live executor bought at least one more window, waited {waited:.1f}s"
    kinds = [e["kind"] for e in q.events()]
    assert "broker.busy" in kinds, "the deferral is traced"
    q.close()
