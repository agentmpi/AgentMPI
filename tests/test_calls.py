"""ampitools.calls: conversations folded back out of the trace."""

from __future__ import annotations

import json

from ampitools import calls


def _trace(tmp_path):
    ev = [
        {"kind": "task.start", "aid": "a1", "rank": 3, "label": "research:t", "model": "m",
         "worker": "w", "prompt_tokens_est": 500, "prompt_sha": "abc123abc123", "ts": 10.0},
        {"kind": "task.call", "aid": "a1", "rank": 3, "label": "research:t", "model": "m",
         "step": 0, "messages": 2, "prompt_chars": 2000, "prompt_sha": "abc123abc123",
         "tools_offered": 3, "json_mode": False, "prompt_tokens": 600, "completion_tokens": 40,
         "reasoning_tokens": 0, "cached_tokens": 0, "cost_usd": 0.001, "api_seconds": 2.0,
         "finish_reason": "tool_calls", "response_chars": 0, "response_sha": "e3b0c44298fc",
         "tool_calls": ["wiki_search", "fetch_url"], "ts": 12.0},
        {"kind": "task.tool", "aid": "a1", "rank": 3, "tool": "wiki_search",
         "args": '{"query": "Гриз"}', "seconds": 0.4, "chars": 300, "ok": True, "error": None,
         "ts": 12.5},
        {"kind": "task.tool", "aid": "a1", "rank": 3, "tool": "fetch_url",
         "args": '{"url": "https://x/Дуров"}', "seconds": 0.1, "chars": 60, "ok": False,
         "error": "error: could not fetch", "ts": 12.6},
        {"kind": "task.call", "aid": "a1", "rank": 3, "label": "research:t", "model": "m",
         "step": 1, "messages": 5, "prompt_chars": 2600, "prompt_sha": "def456def456",
         "tools_offered": 3, "json_mode": False, "prompt_tokens": 800, "completion_tokens": 200,
         "reasoning_tokens": 0, "cached_tokens": 600, "cost_usd": 0.002, "api_seconds": 3.0,
         "finish_reason": "stop", "response_chars": 700, "response_sha": "0123456789ab",
         "tool_calls": [], "ts": 16.0},
        {"kind": "task.done", "aid": "a1", "rank": 3, "label": "research:t", "model": "m",
         "worker": "w", "attempts": 1, "result_tokens": 150, "result_sha": "0123456789ab",
         "finish_reason": "stop", "messages": 6, "seconds": 6.5, "prompt_tokens": 1400,
         "completion_tokens": 240, "reasoning_tokens": 0, "cached_tokens": 600,
         "cost_usd": 0.003, "calls": 2, "tool_calls": 2, "api_seconds": 5.0, "ts": 16.5},
        # an older trace: start/done only, no task.call events
        {"kind": "task.start", "aid": "b2", "rank": 4, "label": "translate:r4:c0", "model": "m",
         "worker": "w", "prompt_tokens_est": 3000, "ts": 20.0},
        {"kind": "task.fail", "aid": "b2", "rank": 4, "label": "translate:r4:c0", "worker": "w",
         "error": "boom", "attempts": 3, "seconds": 30.0, "cost_usd": 0.05, "calls": 3,
         "tool_calls": 0, "api_seconds": 25.0, "ts": 50.0},
        {"kind": "barrier", "rank": 3, "label": "x", "ts": 51.0},
    ]
    p = tmp_path / "trace.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in ev) + "\n", encoding="utf-8")
    return p


def test_conversations_fold_the_task_events(tmp_path):
    convs = calls.conversations(calls.load_events(_trace(tmp_path)))
    assert [c.aid for c in convs] == ["a1", "b2"]
    a, b = convs
    assert a.rank == 3 and a.label == "research:t" and a.outcome == "done"
    assert a.rounds == 2 and len(a.tools) == 2 and a.cost_usd == 0.003 and a.seconds == 6.5
    assert [c["step"] for c in a.calls] == [0, 1]
    assert a.tools[1]["ok"] is False
    assert b.outcome == "fail" and b.rounds == 3 and b.seconds == 30.0
    d = a.to_dict()
    assert d["outcome"] == "done" and d["tool_calls"] == 2 and d["prompt_sha"] == "abc123abc123"


def test_render_shows_rounds_tools_and_outcome_without_text(tmp_path):
    convs = calls.conversations(calls.load_events(_trace(tmp_path)))
    text = calls.render(convs[0])
    assert "research:t  rank 3  aid a1" in text
    assert "2 rounds, 2 tool calls" in text
    assert "asks wiki_search, fetch_url" in text and "answers 700 chars" in text
    assert "wiki_search(" in text and "Гриз" in text          # the arguments are the record
    assert "✗ fetch_url(" in text and "could not fetch" in text
    assert "result 150 tokens  sha 0123456789ab" in text
    failed = calls.render(convs[1])
    assert "failed after 3 attempt(s): boom" in failed
    s = calls.summary(convs)
    assert "2 tasks across 2 ranks" in s and "research" in s and "1 failed" in s


def test_local_logs_are_matched_by_digest(tmp_path):
    p = _trace(tmp_path)
    convs = calls.conversations(calls.load_events(p))
    d = tmp_path / "calls"
    d.mkdir()
    prompt = "the prompt text"
    (d / "a1.prompt.md").write_text(prompt, encoding="utf-8")
    (d / "a1.messages.jsonl").write_text(
        json.dumps({"role": "user", "content": prompt}) + "\n"
        + json.dumps({"role": "assistant", "content": '{"finding": 1}'}) + "\n", encoding="utf-8")
    (d / "a1.result.json").write_text('{"finding": 1}', encoding="utf-8")
    text = calls.render(convs[0], calls_dir=d)
    assert "prompt a1.prompt.md: 15 chars  DIGEST MISMATCH" in text   # sha in trace was made up
    assert "messages a1.messages.jsonl: 2 messages" in text
    assert "result a1.result.json" in text and "DIGEST MISMATCH" in text
    # a matching digest is reported as such
    convs[0].prompt_sha = calls._sha(prompt)
    assert "digest matches" in calls.render(convs[0], calls_dir=d)
    assert "(no local log" in calls.render(convs[1], calls_dir=d)


def test_cli_filters_and_json(tmp_path, capsys):
    p = _trace(tmp_path)
    assert calls.main([str(p), "--rank", "3", "--json"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and json.loads(out[0])["aid"] == "a1"
    assert calls.main([str(p), "--label", "translate"]) == 0
    assert "translate:r4:c0" in capsys.readouterr().out
    assert calls.main([str(p), "--aid", "zzz"]) == 1
    assert calls.main([str(p), "--summary"]) == 0
    assert "2 tasks" in capsys.readouterr().out
