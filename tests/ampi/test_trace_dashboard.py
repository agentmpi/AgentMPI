"""Trace dashboard adapters over the canonical v0.2 runtime."""

from __future__ import annotations

from pathlib import Path

from scripts import trace_server


def test_trace_server_projects_v02_job_for_dashboard(make_job):
    job = make_job(2)
    sender = job.runtime(0)
    receiver = job.runtime(1)
    sender.init(0)
    receiver.init(1)
    sender.send("world", 1, 7, "hello")
    assert receiver.recv("world", 0, 7, timeout=5)["payload"] == "hello"
    sender.win_create("world", "board")
    sender.win_put("board", "result", {"ok": True})

    trace_server.RUNS = Path(job.job_dir).parent
    runs = trace_server.list_runs()
    selected = next(item for item in runs if item["job_id"] == job.job_id)
    detail = trace_server.run_detail(selected["name"])

    assert detail["run_id"] == sender.run_id
    assert detail["n_events"] > 0
    assert set(detail["lanes"]) == {"0", "1"}
    assert any(span["kind"] == "msg.send" for span in detail["lanes"]["0"])
    assert any(span["kind"] == "msg.recv" for span in detail["lanes"]["1"])
    assert detail["summary"]["messages"] == 1
    assert len(detail["health"]) == 2
