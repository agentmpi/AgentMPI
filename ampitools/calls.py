"""Review what a rank said to its model and what came back --- from the trace.

The question this answers is the reviewer's: *what did rank 24 actually do?*  A
run's trace carries one ``task.start``/``task.done`` pair per task, one
``task.call`` per request to the provider and one ``task.tool`` per tool the model
used, each with sizes, digests, tokens, cost and outcome but none of the text
(the prompts quote a copyrighted book and the trace is committed).  This module
folds those events back into conversations and prints them, one task at a time::

    python -m ampitools.calls runs/e7-rawapi-p128/harness.trace.jsonl --rank 24
    python -m ampitools.calls runs/e7-rawapi-p128/harness.trace.jsonl --label research
    python -m ampitools.calls work/e7/e7-rawapi-p128/job --aid 049446b956 --calls work/e7/e7-rawapi-p128/calls

Given ``--calls DIR`` --- the executor's ``log_dir`` on the machine that ran the
rank --- the verbatim prompt, messages and result are located by task id and their
digests checked against the trace, so a reviewer holding both can be sure the file
is the exchange the event describes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Conversation", "conversations", "load_events", "render", "main"]


@dataclass
class Conversation:
    aid: str
    rank: int
    label: str
    model: str = ""
    worker: str = ""
    started: float = 0.0
    ended: float = 0.0
    prompt_tokens_est: int = 0
    prompt_sha: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    retries: list[dict[str, Any]] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    done: dict[str, Any] | None = None
    fail: dict[str, Any] | None = None

    @property
    def outcome(self) -> str:
        if self.done is not None:
            return "done"
        if self.fail is not None:
            return "fail"
        return "open"

    @property
    def cost_usd(self) -> float:
        end = self.done or self.fail or {}
        return float(end.get("cost_usd") or sum(float(c.get("cost_usd") or 0) for c in self.calls))

    @property
    def rounds(self) -> int:
        end = self.done or self.fail or {}
        return int(end.get("calls") or len(self.calls))

    @property
    def seconds(self) -> float:
        end = self.done or self.fail or {}
        if end.get("seconds") is not None:
            return float(end["seconds"])
        return max(0.0, self.ended - self.started)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aid": self.aid, "rank": self.rank, "label": self.label, "model": self.model,
            "worker": self.worker, "outcome": self.outcome, "rounds": self.rounds,
            "tool_calls": len(self.tools), "seconds": round(self.seconds, 3),
            "cost_usd": round(self.cost_usd, 6), "prompt_sha": self.prompt_sha,
            "calls": self.calls, "tools": self.tools, "retries": self.retries,
            "fallbacks": self.fallbacks, "done": self.done, "fail": self.fail,
        }


_TASK_KINDS = {"task.start", "task.call", "task.tool", "task.retry", "task.fallback",
               "task.done", "task.fail"}


def load_events(source: str | Path) -> list[dict[str, Any]]:
    """Events from a trace file (one JSON object per line) or a job root."""
    p = Path(source)
    if p.is_dir():
        from ampi import Ampi

        amp = Ampi(str(p), allow_volatile=True)
        try:
            return list(amp.events())
        finally:
            amp.close()
    out: list[dict[str, Any]] = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def conversations(events: list[dict[str, Any]]) -> list[Conversation]:
    """Fold the task events of a trace into one conversation per task id."""
    by: dict[str, Conversation] = {}
    for e in events:
        kind = e.get("kind")
        if kind not in _TASK_KINDS or not e.get("aid"):
            continue
        aid = str(e["aid"])
        c = by.get(aid)
        if c is None:
            c = by[aid] = Conversation(aid=aid, rank=int(e.get("rank", -1)),
                                       label=str(e.get("label") or ""))
        if not c.label and e.get("label"):
            c.label = str(e["label"])
        ts = float(e.get("ts") or 0.0)
        c.ended = max(c.ended, ts)
        if kind == "task.start":
            c.started = ts
            c.model = str(e.get("model") or c.model)
            c.worker = str(e.get("worker") or c.worker)
            c.prompt_tokens_est = int(e.get("prompt_tokens_est") or 0)
            c.prompt_sha = str(e.get("prompt_sha") or "")
        elif kind == "task.call":
            c.calls.append(_strip(e))
        elif kind == "task.tool":
            c.tools.append(_strip(e))
        elif kind == "task.retry":
            c.retries.append(_strip(e))
        elif kind == "task.fallback":
            c.fallbacks.append(str(e.get("model") or ""))
        elif kind == "task.done":
            c.done = _strip(e)
            c.model = str(e.get("model") or c.model)
        elif kind == "task.fail":
            c.fail = _strip(e)
    return sorted(by.values(), key=lambda c: (c.started, c.rank, c.aid))


def _strip(e: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in e.items() if k not in ("kind", "aid", "run", "comm", "seq", "rank")}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_secs(s: float) -> str:
    return f"{s:.0f}s" if s >= 10 else f"{s:.1f}s"


def render(c: Conversation, *, calls_dir: Path | None = None, verbose: bool = False) -> str:
    lines = []
    head = (f"{c.label}  rank {c.rank}  aid {c.aid}  {c.model or '?'}  {c.outcome}  "
            f"{c.rounds} round{'s' if c.rounds != 1 else ''}, {len(c.tools)} tool call"
            f"{'s' if len(c.tools) != 1 else ''}, {_fmt_secs(c.seconds)}, ${c.cost_usd:.4f}")
    lines.append(head)
    if c.prompt_tokens_est:
        lines.append(f"  prompt ~{c.prompt_tokens_est} tokens"
                     + (f"  sha {c.prompt_sha}" if c.prompt_sha else ""))
    if c.fallbacks:
        lines.append(f"  fell back to {', '.join(c.fallbacks)}")
    # interleave calls and tools in time order
    steps = sorted(
        [("call", x) for x in c.calls] + [("tool", x) for x in c.tools],
        key=lambda kv: float(kv[1].get("ts") or 0.0),
    )
    if steps:
        for kind, x in steps:
            if kind == "call":
                tools = x.get("tool_calls") or []
                fr = x.get("finish_reason") or ""
                lines.append(
                    f"  → call {x.get('step', '?')}: {x.get('messages', '?')} msgs, "
                    f"{x.get('prompt_chars', '?')} chars → {x.get('prompt_tokens', '?')}+"
                    f"{x.get('completion_tokens', '?')} tok"
                    + (f" (+{x['reasoning_tokens']} reasoning)" if x.get("reasoning_tokens") else "")
                    + f", {_fmt_secs(float(x.get('api_seconds') or 0))}, ${float(x.get('cost_usd') or 0):.4f}"
                    + (f"; asks {', '.join(tools)}" if tools else f"; answers {x.get('response_chars', '?')} chars")
                    + (f" [{fr}]" if fr and fr != "stop" and fr != "tool_calls" else "")
                )
            else:
                ok = x.get("ok")
                mark = "✗" if ok is False else "·"
                args = x.get("args") or ""
                lines.append(
                    f"    {mark} {x.get('tool')}({args}) → "
                    + (f"{x.get('error')}" if ok is False else f"{x.get('chars', '?')} chars")
                    + f" in {_fmt_secs(float(x.get('seconds') or 0))}"
                )
    for r in c.retries:
        v = r.get("violations") or []
        lines.append(f"  ↺ retry {r.get('attempt', '?')}: " + "; ".join(str(x) for x in v[:3]))
    end = c.done or c.fail
    if end:
        if c.fail is not None:
            lines.append(f"  ✗ failed after {end.get('attempts', '?')} attempt(s): {end.get('error')}")
        else:
            lines.append(
                f"  ✓ result {end.get('result_tokens', '?')} tokens"
                + (f"  sha {end['result_sha']}" if end.get("result_sha") else "")
                + (f"  finish {end['finish_reason']}" if end.get("finish_reason") else "")
                + f"  api {_fmt_secs(float(end.get('api_seconds') or 0))} of {_fmt_secs(c.seconds)} wall"
            )
    if calls_dir is not None:
        lines.extend("  " + ln for ln in _local_files(c, calls_dir, verbose))
    return "\n".join(lines)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _local_files(c: Conversation, calls_dir: Path, verbose: bool) -> list[str]:
    out: list[str] = []
    prompt = calls_dir / f"{c.aid}.prompt.md"
    msgs = calls_dir / f"{c.aid}.messages.jsonl"
    result = calls_dir / f"{c.aid}.result.json"
    if not prompt.exists() and not msgs.exists():
        out.append(f"(no local log under {calls_dir})")
        return out
    if prompt.exists():
        text = prompt.read_text(encoding="utf-8")
        check = ""
        if c.prompt_sha:
            check = "  digest matches" if _sha(text) == c.prompt_sha else "  DIGEST MISMATCH"
        out.append(f"prompt {prompt.name}: {len(text)} chars{check}")
        if verbose:
            out.extend("  | " + ln for ln in text.splitlines()[:40])
    if msgs.exists():
        rows = [json.loads(ln) for ln in msgs.read_text(encoding="utf-8").splitlines() if ln.strip()]
        out.append(f"messages {msgs.name}: {len(rows)} messages")
        for m in rows:
            role = m.get("role")
            content = m.get("content") or ""
            tc = m.get("tool_calls") or []
            summary = ""
            if tc:
                summary = "calls " + ", ".join(
                    f"{(t.get('function') or {}).get('name')}({str((t.get('function') or {}).get('arguments', ''))[:80]})"
                    for t in tc)
            elif isinstance(content, str):
                summary = " ".join(content.split())[: (400 if verbose else 100)]
            out.append(f"  {role:>9}: {summary}")
    if result.exists():
        text = result.read_text(encoding="utf-8")
        check = ""
        if c.done and c.done.get("result_sha"):
            from ampi.core.payload import canonical

            try:
                same = _sha(canonical(json.loads(text))) == c.done["result_sha"]
            except json.JSONDecodeError:
                same = False
            check = "  digest matches" if same else "  DIGEST MISMATCH"
        out.append(f"result {result.name}: {len(text)} chars{check}")
        if verbose:
            out.extend("  | " + ln for ln in text.splitlines()[:40])
    return out


def summary(convs: list[Conversation]) -> str:
    by_phase: dict[str, list[Conversation]] = defaultdict(list)
    for c in convs:
        by_phase[c.label.split(":")[0]].append(c)
    lines = [f"{len(convs)} tasks across {len({c.rank for c in convs})} ranks"]
    for phase, cs in sorted(by_phase.items(), key=lambda kv: min(c.started for c in kv[1])):
        done = sum(1 for c in cs if c.outcome == "done")
        failed = sum(1 for c in cs if c.outcome == "fail")
        cost = sum(c.cost_usd for c in cs)
        rounds = sum(c.rounds for c in cs)
        tools = sum(len(c.tools) for c in cs)
        bad = sum(1 for c in cs for t in c.tools if t.get("ok") is False)
        secs = [c.seconds for c in cs if c.outcome != "open"]
        med = sorted(secs)[len(secs) // 2] if secs else 0.0
        lines.append(
            f"  {phase:<10} {len(cs):>4} tasks  {done} done {failed} failed  {rounds} calls  "
            f"{tools} tool calls" + (f" ({bad} failed)" if bad else "") +
            f"  median {_fmt_secs(med)}  ${cost:.2f}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ampitools.calls", description=__doc__.split("\n\n")[0])
    ap.add_argument("source", help="a trace .jsonl or a job root")
    ap.add_argument("--rank", type=int, help="only this rank")
    ap.add_argument("--aid", help="only this task id")
    ap.add_argument("--label", help="only tasks whose label starts with this (e.g. research)")
    ap.add_argument("--calls", type=Path, help="the executor's log_dir with the verbatim exchanges")
    ap.add_argument("--json", action="store_true", help="emit one JSON object per task")
    ap.add_argument("--summary", action="store_true", help="only the per-phase summary")
    ap.add_argument("-v", "--verbose", action="store_true", help="show more of each local file")
    args = ap.parse_args(argv)

    convs = conversations(load_events(args.source))
    if args.rank is not None:
        convs = [c for c in convs if c.rank == args.rank]
    if args.aid:
        convs = [c for c in convs if c.aid.startswith(args.aid)]
    if args.label:
        convs = [c for c in convs if c.label.startswith(args.label)]
    if args.summary:
        print(summary(convs))
        return 0
    if args.json:
        for c in convs:
            print(json.dumps(c.to_dict(), ensure_ascii=False))
        return 0
    if not convs:
        print("no matching tasks", file=sys.stderr)
        return 1
    print(summary(convs))
    print()
    for c in convs:
        print(render(c, calls_dir=args.calls, verbose=args.verbose))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
