"""Render the worker prompt for each rank of a run, and record the launch plan.

The launcher writes prompts; it does not start agents.  Starting them is the
agent host's business, exactly as starting processes is ``mpiexec``'s business and
not MPI's, and keeping the separation is what lets the same experiment run against
a different vendor by changing who reads these files.

What the launcher *does* own is the record.  It writes one prompt per rank and a
launch plan naming every rank that was requested, before any of them starts, so
that the set the experiment intended is recorded independently of the set that
answered.  A scale claim is only as good as that record: an aggregate assembled
afterwards from whatever came back cannot distinguish a hundred executors from
one executor writing a hundred entries.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE.parent / "runs"


def worker_template() -> str:
    text = (HERE / "worker_prompt.md").read_text(encoding="utf-8")
    m = re.search(r"```text\n(.*?)\n```", text, re.S)
    if not m:  # pragma: no cover
        raise SystemExit("worker_prompt.md no longer contains a ```text block")
    return m.group(1)


def plan_run(
    *,
    name: str,
    size: int,
    campaign: str | None = None,
    max_tasks: int = 4,
    executors: int = 0,
    ampi: str = "/workspace/.venv/bin/ampi",
) -> dict:
    """Render one worker prompt per executor and write the launch plan.

    Returns the plan.  Kept separate from :func:`main` so that an agent host that
    *does* start sessions (``claude_ranks.py``) can render exactly the prompts the
    record describes rather than re-implementing the substitution.
    """
    run_dir = RUNS / name
    job_root = run_dir / "job"
    prompts = run_dir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    template = worker_template()
    campaign = campaign or name

    n_exec = executors or size
    # Round-robin rather than blocked, so that one executor's ranks are spread
    # through the corpus and a slow executor does not delay one contiguous region.
    assignment: dict[int, list[int]] = {e: [] for e in range(n_exec)}
    for rank in range(size):
        assignment[rank % n_exec].append(rank)

    written = []
    for executor, ranks in assignment.items():
        if not ranks:
            continue
        primary, extra = ranks[0], ranks[1:]
        body = (
            template.replace("{RANK}", str(primary))
            .replace("{SERVE}", f" --serve {','.join(map(str, extra))}" if extra else "")
            .replace("{CAMPAIGN}", campaign)
            .replace("{JOB_ROOT}", str(job_root))
            .replace("{AMPI}", ampi)
            .replace("{MAX_TASKS}", str(max_tasks * max(1, len(ranks))))
        )
        path = prompts / f"exec{executor}.md"
        path.write_text(body, encoding="utf-8")
        written.append({"executor": executor, "primary_rank": primary, "serves": ranks,
                        "prompt": str(path)})

    plan = {
        "name": name,
        "campaign": campaign,
        "job_root": str(job_root),
        "requested_ranks": list(range(size)),
        "executors": len(written),
        "oversubscription": round(size / max(1, len(written)), 2),
        "assignment": written,
        "prompts": str(prompts),
        "max_tasks": max_tasks,
    }
    (run_dir / "worker_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--max-tasks", type=int, default=4)
    ap.add_argument("--executors", type=int, default=0,
                    help="if set below --size, ranks are oversubscribed across this many "
                         "executors, which is what an agent host with a concurrency cap forces")
    ap.add_argument("--ampi", default="/workspace/.venv/bin/ampi")
    a = ap.parse_args()
    plan = plan_run(name=a.name, size=a.size, campaign=a.campaign, max_tasks=a.max_tasks,
                    executors=a.executors, ampi=a.ampi)
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
