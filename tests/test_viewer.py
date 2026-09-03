"""The live trace viewer.

The viewer's contract is narrow and worth pinning: it must serve the *same*
measurements as the figures and the terminal digest, it must degrade to a message
rather than a stack trace when a job has not started, and it must not become a
second place where the event taxonomy lives.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from ampitools.analysis import style as st
from ampitools.analysis.server import PAGE, TraceSource, _handler
from ampitools.harness import Harness


def _trace(tmp_path):
    h = Harness(root=str(tmp_path / "job"), size=4, device="sqlite", force=True)
    h.create()

    def rank_main(amp, rank):
        amp.barrier("start", timeout=30)
        amp.memo("phase", "work")
        amp.allreduce("g", payload={"shared": f"v{rank % 2}"}, op="union", timeout=30)
        return rank

    results = h.run(rank_main, timeout=60)
    path = tmp_path / "run.trace.jsonl"
    h.save(results, tmp_path / "report.json")
    job = h.attach(0)
    with open(path, "w", encoding="utf-8") as fh:
        for e in job.events():
            fh.write(json.dumps(e, default=str) + "\n")
    job.close()
    return path


def _serve(source):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(source, 5.0))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as fh:  # noqa: S310 - a local test server
        return fh.read().decode()


def test_source_requires_somewhere_to_read_from():
    with pytest.raises(SystemExit):
        TraceSource()


def test_serves_the_page_and_the_state(tmp_path):
    source = TraceSource(trace=_trace(tmp_path), name="unit")
    server, base = _serve(source)
    try:
        page = _get(base + "/")
        assert "AgentMPI trace viewer" in page
        assert "__REFRESH__" not in page, "the refresh interval was not substituted"

        state = json.loads(_get(base + "/api/state"))
        assert state["ready"] is True
        assert state["world_size"] == 4
        assert state["metrics"]["n_collectives"] >= 2
        assert state["findings"]
        assert state["phases"] and state["phases"][0]["name"] == "work"
    finally:
        server.shutdown()


def test_state_agrees_with_the_analysis_it_claims_to_show(tmp_path):
    """A viewer that disagreed with the figures would be worse than no viewer."""
    from ampitools.analysis import analyse, load_events

    path = _trace(tmp_path)
    a = analyse(load_events(path), name="unit")
    source = TraceSource(trace=path, name="unit")
    state = source.snapshot()

    assert state["metrics"]["coordination_share"] == round(a.coordination_share, 4)
    assert state["metrics"]["conflicts_lifted"] == a.conflicts_lifted
    assert len(state["collectives"]) == len(a.collectives)
    assert state["world_size"] == a.world_size


def test_roles_come_from_the_shared_taxonomy(tmp_path):
    """Colour must not be redefined in the browser, or it will drift from the figures."""
    source = TraceSource(trace=_trace(tmp_path), name="unit")
    state = source.snapshot()
    assert set(state["roles"]) == set(st.ROLE_ORDER)
    for role, spec in state["roles"].items():
        assert spec["color"] == st.ROLE_COLOR[role]
    for instant in state["instants"]:
        assert instant["color"] == st.style_for(instant["kind"]).color
        assert instant["glyph"] in ("tick", "diamond", "bar")


def test_missing_job_reports_rather_than_raising(tmp_path):
    """A viewer is consulted when things are going wrong; it must not add to them."""
    source = TraceSource(job_root=str(tmp_path / "nonexistent"), name="absent")
    state = source.snapshot()
    assert state["ready"] is False
    assert state["error"]

    server, base = _serve(source)
    try:
        assert json.loads(_get(base + "/api/state"))["ready"] is False
        assert _get(base + "/healthz") == "ok"
    finally:
        server.shutdown()


def test_snapshot_is_cached(tmp_path):
    """Polling every few seconds must not rescan a growing log every few seconds."""
    source = TraceSource(trace=_trace(tmp_path), name="unit", min_interval=60.0)
    first = source.snapshot()
    assert source.snapshot() is first


def test_page_carries_no_build_step():
    """A viewer that needs `npm install` before it can show you a wedged job is
    a viewer you will not use at the moment you need it."""
    assert "<script src=" not in PAGE
    assert "import " not in PAGE.split("<script>")[-1].split("</script>")[0][:400]
