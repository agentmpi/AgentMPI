from __future__ import annotations

import json
from pathlib import Path

import pytest

from ampi.analysis import analyse, analyse_path, load_events


@pytest.fixture
def trace_events() -> list[dict[str, object]]:
    rows: list[tuple[float, int, str, dict[str, object]]] = [
        (0.0, -1, "job.create", {"size": 2, "device": "memory"}),
        (0.1, 0, "init", {"epoch": 1}),
        (0.2, 1, "init", {"epoch": 1}),
        (0.3, 0, "coll.join", {"label": "phase", "arg_kind": "barrier", "comm": "world"}),
        (0.4, 1, "coll.join", {"label": "phase", "arg_kind": "barrier", "comm": "world"}),
        (0.5, 0, "barrier", {"label": "phase", "arrived": 2}),
        (0.6, 1, "barrier", {"label": "phase", "arrived": 2}),
        (1.0, 0, "broker.claim", {"aid": "a", "label": "draft", "worker": "worker-a"}),
        (1.5, 1, "broker.publish", {"aid": "b", "label": "review", "prompt_tokens": 20}),
        (2.0, 1, "broker.claim", {"aid": "b", "label": "review", "worker": "worker-b"}),
        (3.0, 0, "broker.submit", {"aid": "a", "label": "draft", "tokens": 11}),
        (4.0, 1, "broker.submit", {"aid": "b", "label": "review", "tokens": 13}),
        (4.1, 0, "send", {"dst": 1, "tokens": 8}),
        (4.2, 1, "recv", {"src": 0, "charged": 7}),
        (4.3, 1, "ctx.stall", {"dst": 0, "tokens": 30}),
        (4.4, 1, "ctx.stall.end", {"dst": 0, "waited": 0.1}),
        (4.5, 1, "ctx.degrade", {"what": "body"}),
        (4.6, 1, "ctx.release", {"freed": 4}),
        (4.7, 0, "win.create", {"win": "work", "created": True}),
        (4.8, 0, "win.put", {"win": "work", "key": "x", "tokens": 5}),
        (4.9, 1, "win.stale", {"win": "work", "key": "x"}),
        (5.0, 1, "win.cas", {"win": "work", "key": "x", "swapped": True}),
        (5.1, 0, "win.lock", {"win": "work", "key": "x", "token": 1}),
        (5.2, 0, "win.unlock", {"released": True}),
        (5.3, 1, "failure.suspect", {"silent_for": 20}),
        (5.4, 1, "failure.retract", {}),
        (5.5, 0, "coll.join", {"label": "phase", "arg_kind": "barrier", "comm": "world"}),
        (5.6, 1, "coll.join", {"label": "phase", "arg_kind": "barrier", "comm": "world"}),
        (5.7, 0, "finalize", {}),
        (5.8, 1, "finalize", {}),
    ]
    return [
        {"ts": 1000.0 + offset, "seq": index, "rank": rank, "kind": kind, **fields}
        for index, (offset, rank, kind, fields) in enumerate(rows, 1)
    ]


@pytest.fixture
def trace_path(tmp_path: Path, trace_events: list[dict[str, object]]) -> Path:
    path = tmp_path / "harness.trace.jsonl"
    path.write_text(
        "\n".join(json.dumps(event) for event in reversed(trace_events)) + "\n",
        encoding="utf-8",
    )
    return path


def test_reconstructs_spans_concurrency_and_rank_occupancy(
    trace_events: list[dict[str, object]],
) -> None:
    analysis = analyse(trace_events)
    report = analysis.as_dict()

    assert report["broker"]["task_count"] == 2
    assert report["broker"]["complete_spans"] == 2
    first = report["broker"]["spans"][0]
    assert first["published_at_s"] is None
    assert first["busy_s"] == 2.0
    assert report["concurrency"]["max_busy"] == 2
    assert report["concurrency"]["total_busy_s"] == 4.0
    assert report["ranks"][0]["busy_s"] == 2.0
    assert report["ranks"][1]["busy_s"] == 2.0


def test_reports_collectives_context_faults_rma_and_diversity(
    trace_events: list[dict[str, object]],
) -> None:
    report = analyse(trace_events).as_dict()

    assert report["collectives"]["invocation_count"] == 2
    assert report["collectives"]["by_label_kind"] == [
        {
            "label": "phase",
            "kind": "barrier",
            "invocations": 2,
            "complete": 2,
            "participant_events": 4,
            "input_tokens": 0,
        }
    ]
    assert report["context"]["charged_tokens"] == 7
    assert report["context"]["released_tokens"] == 4
    assert report["context"]["stalls"] == 1
    assert report["lifecycle"]["fault_counts"] == {"failure.suspect": 1, "win.stale": 1}
    assert report["lifecycle"]["recovery_counts"] == {"failure.retract": 1}
    assert report["rma"]["stale_overwrites"] == 1
    assert report["rma"]["successful_compare_and_swaps"] == 1
    assert report["rma"]["lock_wait_observable"] is False
    assert report["diversity"]["rank_diversity_evidenced"] is True
    assert report["diversity"]["executor_diversity_evidenced"] is True
    json.dumps(report)


def test_loads_out_of_order_jsonl_and_analyzes_path(trace_path: Path) -> None:
    events = load_events(trace_path)

    assert events[0]["kind"] == "job.create"
    assert analyse_path(trace_path).source == str(trace_path)


@pytest.mark.parametrize(
    "content, message",
    [
        ("not json\n", "invalid JSON"),
        ("[]\n", "JSON object"),
        ('{"kind": "init"}\n', "numeric ts"),
        ('{"ts": 1}\n', "string kind"),
    ],
)
def test_rejects_malformed_trace(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_events(path)


def test_rejects_empty_analysis() -> None:
    with pytest.raises(ValueError, match="empty"):
        analyse([])
