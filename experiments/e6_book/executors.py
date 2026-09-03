"""Executors for E6, and the page validator that every executor's output meets.

Three ways to turn a prompt into an artifact, interchangeable at the harness:

``stub``    a deterministic function.  Models an executor's protocol behaviour
            --- it disagrees with its peers about a third of the terms, it
            finds something to revise on some pages, it occasionally changes a
            seam --- and nothing about its quality.  Its output is marked so
            no analysis can mistake it for agent output.  Exists so a
            sixty-four rank harness can be debugged before sixty-four
            machines are paid for.
``claude``  one headless ``claude -p`` session per task, on this machine, with
            web search allowed for research.  Fresh context per task, which is
            the simplest possible context management: a task is a session.
``broker``  the runtime's own pull queue, served by whatever agent host is
            running the worker prompt.  In the cloud series that host is the
            Claude Code session on the rank's own machine.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ampi.core.payload import check_contract
from ampi.executor import FunctionExecutor, Task

__all__ = ["stub_executor", "ClaudeCliExecutor", "validate_page", "split_sentences",
           "edge_sentences"]

_SENT = re.compile(r"(?<=[.!?…»\"])\s+(?=[«\"A-ZА-ЯЁ0-9–—-])")


def split_sentences(text: str) -> list[str]:
    """A crude sentence splitter, for the stub and for size estimates only."""
    out: list[str] = []
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        out.extend(s.strip() for s in _SENT.split(para) if s.strip())
    return out


def validate_page(result: Any, page: dict[str, Any], languages: list[str]) -> list[str]:
    """Mechanical checks a page translation must pass before it is accepted.

    Checked harness-side because the contract language cannot express "every
    sentence carries every language".  The checks are deliberately cheap and
    literal: they catch the failures that make a page unusable by the PDF
    compiler (a missing language, a gap in the ids) and the one that makes it
    dishonest (a page summarised rather than translated), and nothing that
    requires taste.
    """
    v: list[str] = []
    if not isinstance(result, dict):
        return ["the result is not a JSON object"]
    if str(result.get("page")) != str(page["page"]):
        v.append(f"page is {result.get('page')!r}, expected {page['page']}")
    sents = result.get("sentences")
    if not isinstance(sents, list) or not sents:
        return v + ["sentences is missing or empty"]
    for i, s in enumerate(sents, 1):
        if not isinstance(s, dict):
            v.append(f"sentence {i} is not an object")
            continue
        if s.get("id") != i:
            v.append(f"sentence ids must be 1, 2, 3, …: entry {i} has id {s.get('id')!r}")
        for c in ["ru", *languages]:
            val = s.get(c)
            if not isinstance(val, str) or not val.strip():
                v.append(f"sentence {i} has no {c!r} text")
        if len(v) > 12:
            v.append("… further problems omitted")
            break
    if result.get("total_sentences") != len(sents):
        v.append(f"total_sentences is {result.get('total_sentences')!r} but there are {len(sents)} sentences")
    if page.get("page_type") != "front_matter":
        covered = sum(len(str(s.get("ru") or "")) for s in sents if isinstance(s, dict))
        if covered < 0.6 * page.get("chars", 0):
            v.append(
                f"the ru sentences cover {covered} characters of a {page.get('chars')}-character "
                "page: the page was summarised or sentences were dropped; include every sentence verbatim"
            )
    return v


def edge_sentences(draft: dict[str, Any], languages: list[str], *, head: bool, n: int = 2) -> list[dict[str, Any]]:
    sents = draft.get("sentences") or []
    picked = sents[:n] if head else sents[-n:]
    return [{"id": s.get("id"), "ru": s.get("ru"), **{c: s.get(c) for c in languages}}
            for s in picked if isinstance(s, dict)]


# ---------------------------------------------------------------------------
# The stub
# ---------------------------------------------------------------------------


def stub_executor(corpus: Any, languages: list[str], *, latency_s: float = 0.0) -> FunctionExecutor:
    """A deterministic stand-in.  Protocol behaviour, never quality."""

    def page_of(task: Task) -> dict[str, Any]:
        n = int(task.meta["page"])
        p = corpus.pages[n]
        return {"page": n, "chapter": p.chapter, "chapter_title": p.chapter_title,
                "page_type": p.page_type, "text": p.text, "chars": p.chars}

    def translate(task: Task, *, mark: str) -> dict[str, Any]:
        p = page_of(task)
        sents = split_sentences(p["text"]) or [p["text"]]
        return {
            "page": p["page"], "chapter": p["chapter"], "chapter_title": p["chapter_title"],
            "sentences": [
                {"id": i, "ru": s, **{c: f"[{c}:{mark}] {s[:60]}" for c in languages}}
                for i, s in enumerate(sents, 1)
            ],
            "translator_notes": [f"stub {mark} by rank {task.rank}"],
            "total_sentences": len(sents), "page_type": p["page_type"], "stub": True,
        }

    def fn(task: Task) -> Any:
        if latency_s:
            time.sleep(latency_s)
        rank, label = task.rank, task.label
        kind = label.split(":", 1)[0]
        if kind == "survey":
            base = ["Дуров", "ВКонтакте", "Петербург", "Дом Зингера", "ботаник", "нёрд"]
            terms = []
            for i, t in enumerate(base + [f"термин{rank}_{j}" for j in range(3)]):
                # A third of the population disagrees, on purpose: a stub that
                # agreed with itself would make the reduction look free.
                variant = rank % 3 if i % 3 == 0 else 0
                terms.append({"term": t, "kind": "person" if t == "Дуров" else "org",
                              "gloss": f"stub gloss for {t}", "why_hard": "stub",
                              "needs_research": i < 5,
                              "proposed": {c: f"[{c}:{t}:v{variant}]" for c in languages}})
            pages = task.meta.get("pages", [])
            return {"rank": rank, "stub": True, "terms": terms,
                    "chapter_titles": {str(pages[0]): f"Глава (stub {rank})"} if rank % 4 == 0 and pages else {},
                    "conventions": [f"stub convention from rank {rank}"] if rank % 5 == 0 else [],
                    "page_types": {}}
        if kind == "arbitrate":
            conflicts = task.meta.get("conflicts", {})
            return {"rulings": {k: (v[0] if v else {}) for k, v in conflicts.items()},
                    "reasons": {k: "stub: first candidate" for k in conflicts}, "stub": True}
        if kind == "research":
            term = task.meta.get("term", "?")
            return {"term": term, "stub": True, "finding": f"stub finding for {term}",
                    "sources": [], "register": "neutral",
                    "rendering": {c: f"[{c}:{term}:researched]" for c in languages},
                    "note_for_reader": "", "rationale": "stub", "confidence": "low"}
        if kind == "translate":
            return translate(task, mark="draft")
        if kind == "fix":
            return translate(task, mark="fixed")
        if kind == "review":
            n = int(task.meta["page"])
            revise = (n % 3 == 0)
            return {"page": n, "stub": True, "verdict": "revise" if revise else "accept",
                    "issues": ([{"id": 1, "lang": languages[0], "severity": "major",
                                 "problem": "stub", "suggestion": "stub"}] if revise else []),
                    "summary": "stub review"}
        if kind == "revise":
            return translate(task, mark="revised")
        if kind == "seam":
            head = task.meta.get("head_ids", [])
            changed = bool(head) and rank % 2 == 0
            return {"rank": rank, "stub": True, "changed": changed,
                    "revised": {"head": [{"id": head[0], **{c: f"[{c}:seam] stub" for c in languages}}]
                                if changed else [], "tail": []},
                    "reason": "stub"}
        return {"rank": rank, "stub": True}

    return FunctionExecutor(fn)


# ---------------------------------------------------------------------------
# Headless Claude Code, one session per task
# ---------------------------------------------------------------------------


class ClaudeCliExecutor:
    """Run each task as its own headless ``claude -p`` session.

    The session is given a two-line wrapper naming the prompt file and the
    result file, and the tools to read, write and search the web.  Everything
    else is in the prompt file.  A task's context is therefore exactly the task,
    which is the strongest form of context management there is, and the reason
    a run at this scale never has to compact anything.
    """

    kind = "claude"
    DEFAULT_TOOLS = "Read,Write,WebSearch,WebFetch"

    def __init__(
        self,
        work_dir: str | Path,
        *,
        model: str | None = None,
        tools: str = DEFAULT_TOOLS,
        max_turns: int = 60,
        timeout_s: float = 2400.0,
        effort: str | None = None,
        worker_id: str = "",
    ) -> None:
        self.dir = Path(work_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.tools = tools
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        self.effort = effort
        self.worker_id = worker_id or f"claude-cli:{os.getpid()}"
        self.calls: list[dict[str, Any]] = []

    def invoke(self, task: Task) -> Any:
        prompt_file = self.dir / f"{task.aid}.prompt.md"
        result_file = self.dir / f"{task.aid}.result"
        prompt_file.write_text(task.prompt, encoding="utf-8")
        wrapper = (
            f"Read the task in {prompt_file} and do exactly what it says. Write your answer "
            f"to {result_file} with the Write tool: only the JSON object the task asks for, "
            "no prose before it and no markdown fence around it. If the task asks you to "
            "research, use web search. When the file is written, reply with the single word DONE."
        )
        last_violations: list[str] = []
        for attempt in range(2):
            if attempt:
                wrapper += (
                    "\n\nYour previous file was rejected: " + "; ".join(last_violations[:4])
                    + ". Rewrite it completely, satisfying the task's rules."
                )
            session_id = str(uuid.uuid4())
            cmd = ["claude", "-p", wrapper, "--session-id", session_id,
                   "--output-format", "json", "--allowedTools", self.tools,
                   "--add-dir", str(self.dir), "--max-turns", str(self.max_turns)]
            if self.model:
                cmd += ["--model", self.model]
            if self.effort:
                cmd += ["--effort", self.effort]
            env = dict(os.environ)
            env.pop("AMPI_RANK", None)
            env.pop("AMPI_ROOT", None)
            started = time.time()
            try:
                proc = subprocess.run(cmd, cwd=str(self.dir), env=env, capture_output=True,
                                      text=True, timeout=self.timeout_s, stdin=subprocess.DEVNULL)
                rc, out, errtxt = proc.returncode, proc.stdout, proc.stderr
            except subprocess.TimeoutExpired:
                rc, out, errtxt = -9, "", "timeout"
            meta: dict[str, Any] = {"aid": task.aid, "rank": task.rank, "label": task.label,
                                    "attempt": attempt, "session_id": session_id, "rc": rc,
                                    "wall_s": round(time.time() - started, 1)}
            try:
                summary = json.loads(out) if out.strip() else {}
                meta.update(cost_usd=summary.get("total_cost_usd"),
                            num_turns=summary.get("num_turns"),
                            duration_ms=summary.get("duration_ms"))
            except json.JSONDecodeError:
                summary = {}
            (self.dir / f"{task.aid}.claude.{attempt}.json").write_text(
                json.dumps({"meta": meta, "stdout": out[-4000:], "stderr": errtxt[-2000:]},
                           indent=1), encoding="utf-8")
            self.calls.append(meta)
            if not result_file.exists():
                last_violations = [f"no result file was written (exit {rc})"]
                continue
            raw = result_file.read_text(encoding="utf-8").strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                last_violations = [f"the result file is not valid JSON ({exc.msg})"]
                result_file.unlink(missing_ok=True)
                continue
            violations = check_contract(value, task.contract, subs={"rank": task.rank})
            if violations:
                last_violations = violations
                result_file.unlink(missing_ok=True)
                continue
            return value
        raise RuntimeError(f"claude executor failed {task.label!r}: {'; '.join(last_violations)}")

    def stats(self) -> dict[str, Any]:
        return {"calls": len(self.calls),
                "cost_usd": round(sum(c.get("cost_usd") or 0 for c in self.calls), 4),
                "wall_s": round(sum(c.get("wall_s") or 0 for c in self.calls), 1)}
