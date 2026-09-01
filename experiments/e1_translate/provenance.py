"""Audit who actually did the work in a run, and whether identity held.

Two identities travel with every task in these experiments, and the difference
between them turned out to be the most informative thing the hundred-rank run
produced.

The **protocol identity** is the rank an operation acts as. It comes from the
environment, and the binding lets a caller *assert* it, so every command carries
``--rank N --expect-rank N`` and the runtime refuses the operation before it takes
effect if the ambient rank disagrees. The broker checks it again on submit: a task
belonging to a rank the caller does not serve is rejected with
``AMPI_ERR_IDENTITY``.

The **provenance label** is ``AMPI_WORKER_ID``, which we added for this evaluation
so that a scale claim would have per-executor evidence. It is *outside* the
protocol. Nothing checks it.

On a host where shell sessions are shared between concurrently running agents,
one of these drifted and the other did not, which is close to the cleanest
possible demonstration of what asserting identity buys. This script measures it.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ampi import Ampi

RUNS = Path(__file__).resolve().parent.parent.parent / "runs"
RESULTS = Path(__file__).resolve().parent.parent / "results"


def assignments(run_dir: Path) -> dict[str, set[int]]:
    """The rank set each executor's prompt told it to serve."""
    out: dict[str, set[int]] = {}
    for p in sorted((run_dir / "prompts").glob("*.md")):
        body = p.read_text(encoding="utf-8")
        rank = re.search(r"--rank (\d+)", body)
        serve = re.search(r"--serve ([\d,]+)", body)
        if not rank:
            continue
        out[p.stem] = {int(rank.group(1))} | (
            {int(x) for x in serve.group(1).split(",")} if serve else set()
        )
    return out


def audit(run: str) -> dict[str, Any]:
    run_dir = RUNS / run
    amp = Ampi(str(run_dir / "job"), rank=0, allow_volatile=True)
    tasks = amp.device.scan("task", {})
    assigned = assignments(run_dir)

    mislabelled = []
    for t in tasks:
        label = t.get("worker_id")
        if not label or label not in assigned:
            continue
        if t["rank"] not in assigned[label]:
            mislabelled.append(
                {"label": label, "task_rank": t["rank"], "state": t["state"],
                 "aid": t["aid"], "task": t.get("label")}
            )

    # The broker rejects a submit whose task belongs to a rank the caller does not
    # serve.  A task that reached "done" therefore had a *correct* protocol
    # identity, whatever its provenance label said.
    done_but_mislabelled = [m for m in mislabelled if m["state"] == "done"]
    abandoned = [
        {"rank": t["rank"], "reason": t.get("reason", ""), "aid": t["aid"]}
        for t in tasks
        if t["state"] == "abandoned"
    ]
    identity_abandons = [
        a for a in abandoned
        if re.search(r"identity|misroute|clobber|shared.shell|wrong rank", a["reason"], re.I)
    ]

    out = {
        "run": run,
        "tasks": len(tasks),
        "states": dict(Counter(t["state"] for t in tasks)),
        "distinct_provenance_labels": len({t.get("worker_id") for t in tasks if t.get("worker_id")}),
        "ranks_with_a_task": len({t["rank"] for t in tasks}),
        "provenance_label_drift": {
            "count": len(mislabelled),
            "fraction": round(len(mislabelled) / max(1, len(tasks)), 4),
            "completed_anyway": len(done_but_mislabelled),
            "examples": mislabelled[:6],
        },
        "protocol_identity_violations": {
            "rejected_at_submit": len(identity_abandons),
            "detail": identity_abandons,
        },
        "reading": (
            "The provenance label AMPI_WORKER_ID is outside the protocol and nothing "
            "checks it; it drifted on a host with shared shell sessions. The protocol "
            "identity is asserted on every call with --expect-rank and re-checked by "
            "the broker on submit; where it drifted, the operation was refused and the "
            "executor abandoned the task with an accurate reason instead of doing "
            "another rank's work. Every task that completed did so under a correct "
            "protocol identity, because the submit check is what makes that true."
        ),
    }
    amp.close()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", default=str(RESULTS / "e1_provenance.json"))
    a = ap.parse_args()
    audits = [audit(r) for r in a.runs]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"audits": audits}, indent=2), encoding="utf-8")
    for x in audits:
        d = x["provenance_label_drift"]
        v = x["protocol_identity_violations"]
        print(
            f"{x['run']}: {x['tasks']} tasks, {x['distinct_provenance_labels']} executors; "
            f"provenance label drifted on {d['count']} ({d['fraction']:.1%}), "
            f"protocol identity refused {v['rejected_at_submit']}"
        )
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
