"""E3 --- Surviving agent death: the revoke/shrink/agree triad with real agents.

The question is not whether a runtime can notice that an agent stopped
answering; every framework has a timeout.  The question is what the *survivors*
are supposed to do next, and MPI's answer --- adopted by ULFM after a decade of
argument --- is that the library must not try to recover on the application's
behalf.  It should instead return control to the survivors in a state they can
reason about: revoke the damaged communicator so nobody is left blocked on a
peer that will never reply, shrink it to a communicator over the survivors so
collectives are well defined again, and agree so that everybody makes the same
decision about whether to continue.

This experiment runs that triad with agents that are actually killed mid-task.

Eight agents each own one section of a short technical report.  They publish
drafts into a shared window, then reach a barrier.  While they are working, the
orchestrator kills two of them.  The survivors must notice, revoke, shrink,
agree, adopt the orphaned sections --- which are still in the window, because
window writes are durable and outlive their author --- and finish the report on
the shrunken communicator.

Measured, from the trace only: how long detection took, how long recovery took,
how much of the dead agents' work was salvaged rather than redone, and whether
the job completed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from ampi.launch import create_job, write_rank_cards  # noqa: E402

SECTIONS = [
    ("abstract", "A 120-word abstract for a systems paper about a message-passing "
                 "protocol for multi-agent systems."),
    ("motivation", "Three concrete failure modes of ad-hoc multi-agent harnesses, one "
                   "paragraph each: duplicated work, lost updates to shared files, and "
                   "silent context exhaustion."),
    ("model", "A short description of a failure model in which a participant can crash, "
              "stall, or answer confidently but wrongly."),
    ("detector", "A paragraph explaining why a fixed heartbeat timeout misclassifies a "
                 "healthy but slow participant, and what to do instead."),
    ("recovery", "A paragraph explaining the difference between backward recovery "
                 "(restart from a checkpoint) and forward recovery (continue with fewer "
                 "participants)."),
    ("durability", "A paragraph on why writing shared state durably lets a survivor adopt "
                   "a dead participant's unfinished work."),
    ("evaluation", "A paragraph proposing how to measure recovery cost, naming three "
                   "specific metrics."),
    ("conclusion", "A 100-word conclusion tying the failure model to the recovery "
                   "mechanism."),
]

TASK = """
You own one section of a short technical report that the eight of you are
writing together. Some of you will be killed part way through. The report must
be finished anyway.

**Phase 1 --- publish your draft.**

```
ampi init --role "author-{rank}"
ampi win-create --name report
```

Write your section on this topic:

> {topic}

Aim for {words} words of real prose --- this is a genuine writing task, not a
placeholder. Save it to `{draft_file}`, then publish it and record that you are
done:

```
ampi win-put --win report --key section/{name} --file {draft_file}
ampi win-put --win report --key status/{name} --json '{{"author": {rank}, "state": "drafted"}}'
```

**Phase 2 --- synchronise, and cope with whatever has happened.**

Run `ampi barrier --timeout 900`.

That barrier may fail, and if it does it is not your fault. Handle it like
this:

1. If it reports `AMPI_ERR_PROC_FAILED`, `AMPI_ERR_REVOKED`, or times out, run
   `ampi failures` and `ampi doctor` to see what the runtime knows.
2. If any rank is listed as failed and the communicator is not yet revoked, run
   `ampi revoke`. It is safe and expected for several survivors to do this; the
   operation is idempotent.
3. Every survivor then runs `ampi shrink --name survivors`. This builds a new
   communicator over the ranks that are still alive, renumbered densely. Note
   what your new rank is --- `ampi --comm survivors status` and the shrink
   output will tell you.
4. Every survivor then runs `ampi --comm survivors agree --value true` to
   confirm that the survivors are all in the same place and intend to continue.

From this point on, **use `--comm survivors` on every collective**.

**Phase 3 --- adopt the orphaned work.**

Run `ampi win-list --win report --prefix section/` and
`ampi win-list --win report --prefix status/`. Any section that a dead author
already published is still there: window writes are durable and outlive their
author, so it must be **reused, not rewritten**.

For any section that is missing entirely, the survivors must divide the work.
Claim one atomically before you write it:

```
ampi win-claim --win report --key claim/<missing-section-name>
```

If that returns `"claimed": false` somebody else took it; try another. If it
returns `"claimed": true` it is yours: write it, then
`ampi win-put --win report --key section/<name> --file <your file>`.

**Phase 4 --- finish.**

```
ampi --comm survivors barrier --timeout 900
```

Then write a JSON summary to `{out_file}`:

```
{{"rank": {rank}, "survived": true, "new_rank": <your rank in survivors>,
  "sections_present": <how many section/* keys exist>,
  "adopted": [<names of sections you found already published by others>],
  "wrote_extra": [<names of sections you wrote to cover for a dead peer>]}}
```

Finally `ampi --comm survivors finalize --note "e3 done"`.

Run `ampi hb --expect-idle 300` before you start writing prose, and again
before any other long step.

Do not modify files outside your scratch directory. Do not run git. If you are
killed, you simply stop; that is the experiment working.
"""


def prepare(root: str, world_size: int = 8, words: int = 160) -> dict:
    job_dir = os.path.join(os.path.abspath(root), "job")
    info = create_job(job_dir, world_size, ctx_limit=120_000,
                      meta={"experiment": "e3-fault-tolerance"})
    tasks: dict[int, str] = {}
    for rank in range(world_size):
        name, topic = SECTIONS[rank % len(SECTIONS)]
        scratch = os.path.join(job_dir, "ranks", str(rank))
        os.makedirs(scratch, exist_ok=True)
        tasks[rank] = TASK.format(
            rank=rank, name=name, topic=topic, words=words,
            draft_file=os.path.join(scratch, f"{name}.md"),
            out_file=os.path.join(scratch, "result.json"))
    cards = write_rank_cards(job_dir, world_size, tasks,
                             {r: f"author-{SECTIONS[r % len(SECTIONS)][0]}"
                              for r in range(world_size)})
    return {**info, "cards": cards,
            "sections": [name for name, _ in SECTIONS[:world_size]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.join(os.path.dirname(__file__), "runs"))
    parser.add_argument("-n", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.n), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
