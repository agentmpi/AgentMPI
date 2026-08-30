"""Shared plumbing for the AgentMPI experiments.

The experiments are *real* multi-agent runs: each AgentMPI rank is a separate
LLM agent process with its own context window, and the only thing connecting
them is the protocol. That makes the launch artefacts -- the per-rank prompts --
the most important code in this directory, because they are the program the
agents execute.

Two conventions apply throughout and are worth stating once:

**Arms, not baselines.** Every experiment runs the same task twice or more, with
the protocol's mechanisms ablated rather than absent. The ``naive`` arm still
uses AgentMPI for identity, launch and metering -- it simply uses it the way a
harness written without the protocol's discipline would: gather full texts into
one rank's context, no shared glossary, no boundary exchange. Comparing against
a strawman that also lacks instrumentation would produce numbers we could not
interpret.

**Blocking is bounded and retried.** An agent's shell tool is not a place to
block for ten minutes. So every blocking AgentMPI call in a rank prompt uses a
short deadline (20-40s) and the prompt instructs the agent to retry, which the
protocol makes safe: a timed-out call leaves its state posted and re-running the
identical command resumes it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# --------------------------------------------------------------------------
# Corpus handling
# --------------------------------------------------------------------------


def strip_gutenberg(text: str) -> str:
    """Remove Project Gutenberg header/footer boilerplate."""
    start = re.search(r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text)
    end = re.search(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text)
    body = text[(start.end() if start else 0) : (end.start() if end else len(text))]
    return body.strip("\n")


@dataclass
class Section:
    index: int
    number: int
    title: str
    body: str

    @property
    def words(self) -> int:
        return len(self.body.split())


def split_sections(text: str, pattern: str = r"^§\s*(\d+)\s+(.*)$") -> List[Section]:
    """Split a text on section headings, keeping heading text as the title."""
    lines = text.splitlines()
    marks: List[tuple[int, int, str]] = []
    rx = re.compile(pattern)
    for i, line in enumerate(lines):
        m = rx.match(line.strip())
        if m:
            marks.append((i, int(m.group(1)), m.group(2).strip()))
    out: List[Section] = []
    for k, (i, num, title) in enumerate(marks):
        j = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        # Some headings wrap onto a second line; fold continuation lines into
        # the title so the body starts at real prose.
        body_start = i + 1
        while body_start < j and lines[body_start].strip() and not lines[body_start].startswith(" "):
            if len(title) < 60 and len(lines[body_start].strip()) < 90:
                title = (title + " " + lines[body_start].strip()).strip()
                body_start += 1
            else:
                break
        body = "\n".join(lines[body_start:j]).strip("\n")
        out.append(Section(index=k, number=num, title=title, body=body))
    return out


# --------------------------------------------------------------------------
# Job specs
# --------------------------------------------------------------------------


@dataclass
class Rank:
    rank: int
    role: str
    task: str
    env: Dict[str, str] = field(default_factory=dict)


def write_spec(
    out_dir: Path,
    *,
    label: str,
    preamble: str,
    ranks: Sequence[Rank],
    config: Optional[Dict[str, Any]] = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = {
        "label": label,
        "root": str(out_dir),
        "preamble": preamble,
        "config": config or {},
        "ranks": [
            {"rank": r.rank, "role": r.role, "task": r.task, "env": r.env} for r in ranks
        ],
    }
    p = out_dir / "job_spec.json"
    p.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def create_job(spec_path: Path) -> Dict[str, Any]:
    """Run `ampi run --spec` to materialise the journal and rank prompts."""
    proc = subprocess.run(
        [sys.executable, "-m", "ampi.cli", "run", "--spec", str(spec_path), "--json"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    if proc.returncode != 0:
        raise SystemExit(f"ampi run failed:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(proc.stdout)


def launch_plan(manifest: Dict[str, Any], out: Path) -> Path:
    """Write the list of subagent launch instructions for the parent to execute.

    The parent agent (playing ``mpirun``) reads this and spawns one subagent per
    rank. Keeping it a file rather than an API call is what lets the same
    experiment run under a different agent host.
    """
    plan = {
        "job": manifest["job"],
        "label": manifest["label"],
        "root": manifest["root"],
        "world_size": manifest["world_size"],
        "ranks": [
            {
                "rank": r["rank"],
                "role": r["role"],
                "prompt_file": r["prompt"],
                "env": r["env"],
            }
            for r in manifest["ranks"]
        ],
    }
    out.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# --------------------------------------------------------------------------
# Prompt fragments shared by every rank in every experiment
# --------------------------------------------------------------------------

PROTOCOL_DISCIPLINE = """### How to use `ampi` reliably (read this carefully; it is short)

**1. Pin your identity on every command. This is the most important rule.** The
shell session may be shared with other agents, and `AMPI_RANK` has been observed
changing between one call and the next. Set it inline every time, and assert it:

```bash
AMPI_ROOT=<your job root> AMPI_RANK=<your rank> ampi <cmd> --expect-rank <your rank>
```

Every command echoes `[acting as rank N of job J]` — **read it**. A command that
fails with `AMPI_ERR_IDENTITY` means your environment drifted; the hint names the
rank you actually hold credentials for. `ampi whoami --expect-rank N` is a cheap
one-command check.

**2. Blocking calls.** Always pass `--timeout 120`. The command retries
internally (you will see `AMPI_RETRY` on stderr), so one invocation may block for
several minutes; set your shell tool's `block_until_ms` to 400000. If it still
ends in `AMPI_ERR_TIMEOUT`, **re-run the identical command** — your place in the
queue is durable, so retrying resumes the same wait rather than restarting it. Do
this at least 5 more times before concluding anything is wrong. Other ranks are
blocked waiting for you.

**3. Before any step taking over a minute**, run `ampi hb --extend 900`, and
again every few minutes while you work.

**4. Every collective needs `--label`**, and every rank must use the same label
for the same collective. The labels are given to you. Do not invent labels.

**5. If output contains `action_required`, do that before anything else.**

**6. Do not wait forever for a message that may not have been sent.** If a `recv`
fails five times, check `ampi inbox` and `ampi status`, then continue without it
and record that in your report.

**7. Read `dropped=[...]`** in collective output: it names peers whose
contribution the collective completed without. Report it.

**8. Errors prescribe.** Follow the `hint:` line. `AMPI_ERR_FENCED` means you
were declared dead and possibly replaced: run `ampi recover` to see whether a
successor exists, and continue only if none does.

**9. Watch your context.** `ampi ctx`. Above 70%, stop materialising payloads:
use `ampi view <handle> --budget 400`, or `ampi view <handle> --op full --out
<path>` to save a payload to disk for free.

**10. Use `--in @file`, not `--in "..."`, for anything long** — your shell runs
backticks inside double quotes.

**11. Record progress** with `ampi memo put <key> <value>` after each phase.
"""


def rank_header(exp: str, phase_labels: Dict[str, str]) -> str:
    lines = [f"You are one rank in AgentMPI experiment **{exp}**.", "", "Collective labels you will use:"]
    for k, v in phase_labels.items():
        lines.append(f"- `{k}`: label `{v}`")
    return "\n".join(lines)


def read_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default
