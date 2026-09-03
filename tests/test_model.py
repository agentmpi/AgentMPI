"""The model executor: tool loop, contract repair, retry, and its trace events."""

from __future__ import annotations

import json
from typing import Any

import pytest

from ampi.core.payload import Contract
from ampi.errors import AmpiError
from ampitools.executor import Task
from ampitools.harness import Harness
from ampitools.launcher import ranks_of_node
from ampitools.model import ChatModel, ModelError, ModelExecutor, Tool, Usage, extract_json


def _reply(content: str, *, tool_calls: list[dict] | None = None, usage: dict | None = None,
           model: str = "fake/model") -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {
        "model": model,
        "choices": [{"message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001,
                           "completion_tokens_details": {"reasoning_tokens": 5}},
    }


class Script:
    """A transport that plays back scripted replies and records the requests."""

    def __init__(self, replies: list[Any]):
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(json.loads(json.dumps(body)))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(body)
        return item


@pytest.fixture
def job(tmp_path):
    h = Harness(root=str(tmp_path / "job"), size=2, device="sqlite", force=True)
    amp = h.create()
    yield h, amp
    amp.close()


def _amp(job):
    h, _ = job
    amp = h.attach(0)
    amp.init()
    return amp


def test_extract_json_tolerates_prose_and_fences():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Sure!\n```json\n{"a": [1, 2]}\n```\nDone.') == {"a": [1, 2]}
    assert extract_json('Here it is: {"a": {"b": "}"}} trailing') == {"a": {"b": "}"}}
    with pytest.raises(ValueError):
        extract_json("no object here")


def test_usage_is_parsed_and_summed():
    u = Usage.from_response(_reply("x"), 1.5)
    assert (u.prompt_tokens, u.completion_tokens, u.reasoning_tokens, u.calls) == (100, 20, 5, 1)
    assert u.cost_usd == pytest.approx(0.001)
    total = Usage().add(u).add(u)
    assert total.calls == 2 and total.prompt_tokens == 200 and total.seconds == 3.0


def test_plain_completion_and_json_result(job):
    amp = _amp(job)
    script = Script([_reply('{"rank": 0, "answer": 42}')])
    ex = ModelExecutor(amp, ChatModel("fake/model", transport=script, api_key="k"))
    task = Task(aid="a1", rank=0, label="t", prompt="hi",
                contract=Contract.parse({"kind": "json", "required": ["answer"],
                                         "expect": {"rank": "{rank}"}}))
    assert ex.invoke(task) == {"rank": 0, "answer": 42}
    kinds = [e["kind"] for e in amp.events()]
    assert "task.start" in kinds and "task.done" in kinds
    done = next(e for e in amp.events() if e["kind"] == "task.done")
    assert done["prompt_tokens"] == 100 and done["calls"] == 1 and done["attempts"] == 1
    assert done["worker"] == ex.worker_id and done["model"] == "fake/model"
    # json mode was requested because the contract is json and no tools were offered
    assert script.requests[0]["response_format"] == {"type": "json_object"}


def test_tool_loop_executes_tools_and_feeds_results_back(job):
    amp = _amp(job)
    calls: list[dict] = []

    def lookup(term: str, lang: str = "ru") -> str:
        calls.append({"term": term, "lang": lang})
        return f"{term} means something in {lang}"

    tool = Tool("lookup", "look a term up", {"type": "object", "properties": {
        "term": {"type": "string"}, "lang": {"type": "string"}}, "required": ["term"]}, lookup)
    script = Script([
        _reply("", tool_calls=[{"id": "c1", "type": "function",
                                "function": {"name": "lookup",
                                             "arguments": json.dumps({"term": "гопник"})}}]),
        _reply("", tool_calls=[{"id": "c2", "type": "function",
                                "function": {"name": "missing", "arguments": "{}"}}]),
        _reply('{"term": "гопник", "finding": "street tough"}'),
    ])
    ex = ModelExecutor(amp, ChatModel("fake/model", transport=script, api_key="k"), tools=[tool])
    out = ex.invoke(Task(aid="a2", rank=0, label="research:x", prompt="research",
                         contract=Contract.parse({"kind": "json", "required": ["finding"]})))
    assert out["finding"] == "street tough"
    assert calls == [{"term": "гопник", "lang": "ru"}]
    # the tool result went back as a tool message, and the unknown tool got an error string
    msgs = script.requests[2]["messages"]
    roles = [m["role"] for m in msgs]
    assert roles == ["user", "assistant", "tool", "assistant", "tool"]
    assert "means something" in msgs[2]["content"]
    assert msgs[4]["content"].startswith("error: no tool named")
    # tools were offered on every non-final round and json mode was not forced with tools
    assert "tools" in script.requests[0] and "response_format" not in script.requests[0]
    done = next(e for e in amp.events() if e["kind"] == "task.done")
    assert done["tool_calls"] == 2 and done["calls"] == 3
    assert sum(1 for e in amp.events() if e["kind"] == "task.tool") == 2


def test_contract_violation_is_repaired_in_conversation(job):
    amp = _amp(job)
    script = Script([
        _reply('{"rank": 1, "answer": 1}'),          # wrong self-identification
        _reply("not json at all"),                   # unparsable
        _reply('{"rank": 0, "answer": 1}'),          # fixed
    ])
    ex = ModelExecutor(amp, ChatModel("fake/model", transport=script, api_key="k"),
                       max_attempts=3)
    task = Task(aid="a3", rank=0, label="t", prompt="p",
                contract=Contract.parse({"kind": "json", "required": ["answer"],
                                         "expect": {"rank": "{rank}"}}))
    assert ex.invoke(task) == {"rank": 0, "answer": 1}
    retries = [e for e in amp.events() if e["kind"] == "task.retry"]
    assert len(retries) == 2
    assert any("self-identifying" in v for v in retries[0]["violations"])
    # the repair was a continuation of the same conversation, not a fresh start
    assert len(script.requests[2]["messages"]) == 5
    assert "does not satisfy its contract" in script.requests[1]["messages"][-1]["content"]
    done = next(e for e in amp.events() if e["kind"] == "task.done")
    assert done["attempts"] == 3 and done["calls"] == 3


def test_exhausted_attempts_fail_loudly_and_are_traced(job):
    amp = _amp(job)
    script = Script([_reply('{"rank": 9}')] * 2)
    ex = ModelExecutor(amp, ChatModel("fake/model", transport=script, api_key="k"),
                       max_attempts=2)
    task = Task(aid="a4", rank=0, label="t", prompt="p",
                contract=Contract.parse({"kind": "json", "expect": {"rank": "{rank}"}}))
    with pytest.raises(AmpiError) as ei:
        ex.invoke(task)
    assert ei.value.cls_name == "AMPI_ERR_OP_FAILED"
    fail = next(e for e in amp.events() if e["kind"] == "task.fail")
    assert fail["attempts"] == 2 and "self-identifying" in fail["error"]
    assert ex.failures == 1 and ex.tasks == 0


def test_transport_errors_are_retried_with_backoff(job, monkeypatch):
    amp = _amp(job)
    monkeypatch.setattr("ampitools.model.time.sleep", lambda s: None)
    script = Script([
        ModelError("HTTP 429: slow down", status=429, retryable=True),
        {"error": {"code": 502, "message": "upstream"}},
        _reply("ok"),
    ])
    m = ChatModel("fake/model", transport=script, api_key="k", max_retries=3)
    ex = ModelExecutor(amp, m)
    assert ex.invoke(Task(aid="a5", rank=0, label="t", prompt="p")) == "ok"
    assert m.retries == 2
    # a non-retryable error is raised at once
    script2 = Script([ModelError("HTTP 400: bad", status=400, retryable=False)])
    m2 = ChatModel("fake/model", transport=script2, api_key="k")
    with pytest.raises(AmpiError):
        ModelExecutor(amp, m2).invoke(Task(aid="a6", rank=0, label="t", prompt="p"))


def test_per_label_model_override_and_logging(job, tmp_path):
    amp = _amp(job)
    cheap = Script([_reply("cheap said", model="cheap/model")])
    dear = Script([_reply("dear said", model="dear/model")])
    ex = ModelExecutor(
        amp, ChatModel("dear/model", transport=dear, api_key="k"),
        models={"survey": ChatModel("cheap/model", transport=cheap, api_key="k")},
        log_dir=tmp_path / "log",
    )
    assert ex.invoke(Task(aid="b1", rank=0, label="survey:r0", prompt="s")) == "cheap said"
    assert ex.invoke(Task(aid="b2", rank=0, label="translate:r0", prompt="t")) == "dear said"
    assert (tmp_path / "log" / "b1.prompt.md").read_text() == "s"
    assert (tmp_path / "log" / "b2.messages.jsonl").exists()
    dones = [e for e in amp.events() if e["kind"] == "task.done"]
    assert [d["model"] for d in dones] == ["cheap/model", "dear/model"]
    assert ex.stats()["usage"]["calls"] == 2


def test_analysis_reads_task_events_as_work_spans(job):
    from ampitools.analysis import analyse

    amp = _amp(job)
    script = Script([_reply("x"), _reply("y")])
    ex = ModelExecutor(amp, ChatModel("fake/model", transport=script, api_key="k"))
    ex.invoke(Task(aid="c1", rank=0, label="t1", prompt="p"))
    ex.invoke(Task(aid="c2", rank=0, label="t2", prompt="p"))
    amp.finalize()
    a = analyse(amp.events(), name="t")
    assert a.has_work_spans and not a.has_broker
    assert a.ranks[0].n_tasks == 2 and len(a.work_spans) == 2
    assert a.ranks[0].prompt_tokens == 200 and a.total_cost_usd == pytest.approx(0.002)
    assert a.executors and list(a.executors)[0] == ex.worker_id
    assert a.to_dict()["total_prompt_tokens"] == 200


def test_ranks_of_node_is_a_block_distribution():
    assert ranks_of_node(8, 2, 0) == [0, 1, 2, 3]
    assert ranks_of_node(8, 2, 1) == [4, 5, 6, 7]
    assert ranks_of_node(10, 3, 0) == [0, 1, 2, 3]
    assert ranks_of_node(10, 3, 2) == [7, 8, 9]
    assert sorted(sum((ranks_of_node(257, 8, k) for k in range(8)), [])) == list(range(257))
    with pytest.raises(ValueError):
        ranks_of_node(4, 2, 2)


def test_a_failing_model_falls_back_to_another(job):
    amp = _amp(job)
    bad = Script([_reply("{ broken", model="bad/model")] * 2)
    good = Script([_reply('{"rank": 0, "ok": true}', model="good/model")])
    ex = ModelExecutor(amp, ChatModel("bad/model", transport=bad, api_key="k"), max_attempts=2,
                       fallback=ChatModel("good/model", transport=good, api_key="k"))
    out = ex.invoke(Task(aid="f1", rank=0, label="t", prompt="p",
                         contract=Contract.parse({"kind": "json", "expect": {"rank": "{rank}"}})))
    assert out == {"rank": 0, "ok": True} and ex.fallbacks == 1
    kinds = [e["kind"] for e in amp.events()]
    assert "task.fail" in kinds and "task.fallback" in kinds and kinds.count("task.done") == 1
