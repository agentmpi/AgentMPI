from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from ampi import Ampi
from scripts import trace_server


def _write_run(
    runs: Path,
    name: str = "sample",
    *,
    report: dict | None = None,
) -> Path:
    run = runs / name
    run.mkdir(parents=True)
    events = [
        {"kind": "job.create", "rank": -1, "seq": 1, "ts": 10.0, "size": 2, "run": "job-1"},
        {
            "kind": "broker.claim",
            "rank": 0,
            "seq": 2,
            "ts": 11.0,
            "aid": "a1",
            "label": "draft",
        },
        {
            "kind": "broker.claim",
            "rank": 1,
            "seq": 3,
            "ts": 12.0,
            "aid": "a2",
            "label": "review",
        },
        {
            "kind": "broker.submit",
            "rank": 0,
            "seq": 4,
            "ts": 14.0,
            "aid": "a1",
            "tokens": 20,
        },
        {"kind": "broker.submit", "rank": 1, "seq": 5, "ts": 15.0, "aid": "a2"},
    ]
    (run / trace_server.TRACE_NAME).write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    if report is not None:
        (run / trace_server.REPORT_NAME).write_text(json.dumps(report), encoding="utf-8")
    return run


@pytest.fixture
def runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(trace_server, "RUNS", root)
    return root


def test_lists_only_valid_direct_harness_traces(runs: Path, tmp_path: Path) -> None:
    _write_run(runs, report={"size": 2, "experiment": "test"})
    (runs / "ordinary-directory").mkdir()
    outside = _write_run(tmp_path / "outside", "escaped")
    (runs / "linked-run").symlink_to(outside, target_is_directory=True)

    listed = trace_server.list_runs()

    assert [run["name"] for run in listed] == ["sample"]
    assert listed[0] == {
        "name": "sample",
        "n_events": 5,
        "n_ranks": 2,
        "world_size": 2,
        "job_id": "job-1",
        "duration_s": 5.0,
        "trace_bytes": (runs / "sample" / trace_server.TRACE_NAME).stat().st_size,
        "has_report": True,
        "live": False,
    }


@pytest.mark.parametrize("name", ["../outside", "..", "/tmp/run", "a/b", "a%2fb", ""])
def test_rejects_unsafe_run_names(runs: Path, name: str) -> None:
    with pytest.raises(trace_server.TraceError):
        trace_server.run_detail(name)


def test_returns_flat_current_events_optional_report_and_concurrency(runs: Path) -> None:
    _write_run(runs, report={"size": 2, "succeeded": 2})

    detail = trace_server.run_detail("sample")

    assert detail["live"] is False
    assert detail["schema"]["required"]["kind"] == "string"
    assert detail["events"][1]["kind"] == "broker.claim"
    assert "payload" not in detail["events"][1]
    assert detail["report"] == {"size": 2, "succeeded": 2}
    assert detail["duration_s"] == 5.0
    assert detail["work_spans"] == [
        {
            "aid": "a1",
            "rank": 0,
            "label": "draft",
            "start": 11.0,
            "end": 14.0,
            "outcome": "submit",
            "tokens": 20,
        },
        {
            "aid": "a2",
            "rank": 1,
            "label": "review",
            "start": 12.0,
            "end": 15.0,
            "outcome": "submit",
            "tokens": 0,
        },
    ]
    assert detail["concurrency"] == {
        "peak": 2,
        "average": 1.2,
        "busy_rank_seconds": 6.0,
        "utilization": 0.6,
    }

    (runs / "sample" / trace_server.REPORT_NAME).unlink()
    assert trace_server.run_detail("sample")["report"] is None


def test_reads_a_live_job_when_no_exported_trace_exists(runs: Path) -> None:
    run = runs / "live"
    amp = Ampi.create(str(run / "job"), 2, device="sqlite")
    amp.trace("broker.publish", rank=0, aid="live-task", label="research")
    amp.close()

    detail = trace_server.run_detail("live")

    assert detail["live"] is True
    assert any(event["kind"] == "broker.publish" for event in detail["events"])
    listed = {item["name"]: item for item in trace_server.list_runs()}
    assert listed["live"]["live"] is True
    assert listed["live"]["world_size"] == 2


def test_rejects_invalid_jsonl_event(runs: Path) -> None:
    run = runs / "broken"
    run.mkdir()
    (run / trace_server.TRACE_NAME).write_text('{"kind":"init","ts":1}\nnot-json\n')

    with pytest.raises(trace_server.TraceError, match=r"harness\.trace\.jsonl:2"):
        trace_server.run_detail("broken")
    assert trace_server.list_runs() == []


def test_http_api_is_get_only(runs: Path) -> None:
    _write_run(runs)
    server = trace_server.ThreadingHTTPServer(("127.0.0.1", 0), trace_server.Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address)
        connection.request("GET", "/api/run?name=sample")
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 200
        assert body["events"][0]["kind"] == "job.create"

        connection.request("GET", "/api/run?name=..%2Foutside")
        response = connection.getresponse()
        assert response.status == 400
        response.read()

        connection.request("POST", "/api/run?name=sample", body=b"{}")
        response = connection.getresponse()
        assert response.status == 405
        assert json.loads(response.read()) == {"error": "read-only server"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
