"""E5 --- Collaborative software development without a simultaneity requirement.

E4 built the same package with a barrier between implementation and
integration, and it did not finish: the executor pool was smaller than the
communicator, so ranks blocked in the barrier occupied slots that the missing
ranks needed in order to be launched, and the barrier timed out after
thirty-three minutes with one rank still to arrive.

That is not an argument against barriers.  It is an argument that a harness
should know whether the pattern it has written requires *p* executors to exist
at the same instant, and should choose one that does not when the pool cannot
promise them.  This experiment is the same task rewritten in the idiom that
does not: every phase transition is carried by a durable window rather than by
a collective, so a rank that arrives twenty minutes late simply picks up
whatever is left and the others never waited for it.

Four protocol mechanisms carry the whole coordination, and each replaces
something a hand-written harness would have to invent:

``win-put`` on a contract cell
    replaces the broadcast.  A late rank reads the contract when it arrives
    instead of having to be present when it was sent.

``win-claim``
    replaces "the prompt tells each agent which module to write".  A module is
    taken by compare-and-swap, so two agents cannot pick the same one, and the
    assignment survives an agent dying with the item half done because the
    claim is visible and expires.

``win-fetch-add`` on a completion counter
    replaces the barrier.  The integrator waits for a count, not for a
    rendezvous, so implementation and integration overlap correctly without
    any rank blocking another.

``win-lock``
    replaces "hope two agents do not edit the same file".  The shared package
    ``__init__.py`` and the defect log are edited under an exclusive lease.

The deliverable is a real package with real tests, so ``pytest`` is the judge.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from ampi.launch import create_job, write_rank_cards  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "e4_software"))
from prepare import CONTRACT  # noqa: E402  (the same contract, so the tasks compare)

MODULES = ["estimate", "policy", "compact", "ledger", "planner", "cli"]

ARCHITECT_TASK = """
You are the architect. Publish the contract, create the package skeleton, then
help integrate. You never block waiting for anyone.

**Step 1.** Set up and publish:

```
ampi init --role architect
ampi win-create --name build
ampi win-put --win build --key contract --json-file {contract_file}
ampi win-put --win build --key modules --json '{module_list}'
```

**Step 2.** Create the package skeleton under `{pkg_dir}`:

* `{pkg_dir}/tokenbudget/__init__.py` re-exporting the public names the
  contract lists, with a real module docstring;
* `{pkg_dir}/tests/` (empty directory is fine);
* `{pkg_dir}/pyproject.toml` naming the package `tokenbudget`, requiring
  Python 3.11, with no third-party dependencies.

Because six implementers will each need to add their module's names to
`__init__.py`, that file is shared. Whenever you edit it, take the lease first:

```
ampi win-lock --win build --key initpy --ttl 180
# ... edit {pkg_dir}/tokenbudget/__init__.py ...
ampi win-unlock --lock-id <the lock_id you were given>
```

Then mark yourself ready: `ampi win-fetch-add --win build --key ready`.

**Step 3.** While the implementers work, poll for questions and answer them.
Do this in a loop, roughly every 30 seconds, for as long as
`ampi win-get --win build --key done` reports fewer than 6:

```
ampi probe --tag 10
ampi recv --tag 10 --timeout 20 --nonblocking     # returns immediately if nothing
ampi send --to <asker> --tag 11 --text "<your answer>"
```

If you must amend the contract, take `ampi win-lock --win build --key contract`
first, re-read the cell, apply your change, write it back, and unlock.

**Step 4.** When `done` reaches 6, run the full suite yourself:

```
cd {pkg_dir} && /workspace/.venv/bin/python -m pytest -q
```

Fix only skeleton-level problems (a missing re-export, an import cycle). Then
write `{out_file}`:

```
{{"rank": 0, "role": "architect", "questions_answered": <n>, "amendments": <n>,
  "pytest_exit": <code>, "summary": "<one honest sentence>"}}
```

and run `ampi finalize --note "e5 architect done"`.

Run `ampi hb --expect-idle 900` before long stretches of work. Do not run git.
"""

IMPLEMENTER_TASK = """
You claim one module of a shared Python package, write it, and prove it works.
You never wait for another rank.

**Step 1.** Join and read the contract:

```
ampi init --role implementer
ampi win-create --name build
ampi win-get --win build --key contract --out {scratch}/contract.json
```

If that reports `"found": false`, the architect has not published yet: wait 20
seconds and try again, up to ten times.

**Step 2.** Claim a module. Try these in order, starting with **{preferred}**:

{module_order}

```
ampi win-claim --win build --key module/<name>
```

`"claimed": true` means it is yours; stop trying. `"claimed": false` means
someone else has it, so move to the next name. **Never write a module you did
not claim.** If you win nothing, all six are taken: say so in your report,
write extra tests for an existing module instead, and skip to step 5.

**Step 3.** Implement your module at `{pkg_dir}/tokenbudget/<name>.py`,
following the contract exactly: the listed exports, the stated behaviour,
standard library only, type annotations, a module docstring. Real working
code, not stubs.

Then write at least six tests at `{pkg_dir}/tests/test_<name>.py`, covering the
edge cases the contract names explicitly. Run them until they pass:

```
cd {pkg_dir} && /workspace/.venv/bin/python -m pytest tests/test_<name>.py -q
```

If the contract is ambiguous, ask rather than guess:
`ampi send --to 0 --tag 10 --text "<question>"` then
`ampi recv --source 0 --tag 11 --timeout 120`. If no answer comes, make a
reasonable choice and record it in your report.

**Step 4.** Add your module's public names to the shared
`{pkg_dir}/tokenbudget/__init__.py`. That file is shared, so take the lease
first and hold it only while editing:

```
ampi win-lock --win build --key initpy --ttl 180
# re-read the file, add your exports, write it back
ampi win-unlock --lock-id <the lock_id you were given>
```

**Step 5.** Publish what you did and count yourself done:

```
ampi win-put --win build --key module/<name>/summary --json '{{"module": "<name>", "by": {rank}, "exports": [...], "tests": <n>, "passing": true}}'
ampi win-fetch-add --win build --key done
```

Then write `{out_file}`:

```
{{"rank": {rank}, "claimed": "<module or null>", "attempts": [<names you tried>],
  "tests_written": <n>, "tests_passing": <n>, "asked_architect": <n>}}
```

and run `ampi finalize --note "e5 implementer done"`.

Run `ampi hb --expect-idle 900` before long stretches of writing. Do not edit
another agent's module. Do not run git.
"""

INTEGRATOR_TASK = """
You make the package work as a whole and report honestly on whether it does.
You wait on a counter, never on a rendezvous.

**Step 1.** Join and read the contract:

```
ampi init --role integrator
ampi win-create --name build
ampi win-get --win build --key contract --out {scratch}/contract.json
```

If that reports `"found": false`, wait 20 seconds and retry, up to ten times.

**Step 2.** While the implementers work, write cross-module integration tests
at `{pkg_dir}/tests/test_integration.py`. These must exercise the modules
*together*, not one at a time: for example plan a fan-out budget, charge a
ledger against it, compact a message list to fit what remains, and assert that
the compacted list really does fit. Write at least five. They will fail until
the implementers finish; that is expected.

**Step 3.** Wait for implementation to finish by polling the counter, not by
calling a collective:

```
ampi win-get --win build --key done
```

Repeat every 30 seconds until it reports 6, or until 25 minutes have passed.
Between polls, run `ampi hb --expect-idle 120`. Also check
`ampi win-list --win build --prefix module/` to see which modules exist.

**Step 4.** Run the whole suite:

```
cd {pkg_dir} && /workspace/.venv/bin/python -m pytest -q
```

Fix **integration-level** problems only: a missing re-export in
`__init__.py` (take `ampi win-lock --win build --key initpy` first), an import
cycle, a mismatch between two modules' assumptions. If a single module is
simply wrong, do not quietly rewrite it: record the defect and tell its author.

```
ampi win-lock --win build --key defects --ttl 180
ampi win-get --win build --key defects --out {scratch}/defects.json
# append your finding, then write it back
ampi win-put --win build --key defects --json-file {scratch}/defects.json
ampi win-unlock --lock-id <lock_id>
ampi send --to <author rank> --tag 12 --text "defect: ..."
```

**Step 5.** Run the suite one final time and write `{out_file}`:

```
{{"rank": {rank}, "modules_present": [...], "pytest_exit": <code>,
  "tests_collected": <n>, "tests_passed": <n>, "tests_failed": <n>,
  "defects_filed": <n>, "summary": "<one honest sentence>"}}
```

Report what actually happened, including failures. A truthful failure is far
more useful here than a rosy summary. Then
`ampi finalize --note "e5 integrator done"`.

Do not run git.
"""


def prepare(root: str, world_size: int = 8) -> dict:
    job_dir = os.path.join(os.path.abspath(root), "job")
    pkg_dir = os.path.join(job_dir, "artifact")
    os.makedirs(pkg_dir, exist_ok=True)
    info = create_job(job_dir, world_size, ctx_limit=120_000,
                      meta={"experiment": "e5-collaborative-software-bag-of-tasks"})
    contract_file = os.path.join(job_dir, "contract.json")
    with open(contract_file, "w", encoding="utf-8") as fh:
        json.dump(CONTRACT, fh, indent=2)

    tasks: dict[int, str] = {}
    roles: dict[int, str] = {}
    n_impl = world_size - 2
    for rank in range(world_size):
        scratch = os.path.join(job_dir, "ranks", str(rank))
        os.makedirs(scratch, exist_ok=True)
        common = {"rank": rank, "scratch": scratch, "pkg_dir": pkg_dir,
                  "out_file": os.path.join(scratch, "result.json")}
        if rank == 0:
            roles[rank] = "architect"
            tasks[rank] = ARCHITECT_TASK.format(
                contract_file=contract_file,
                module_list=json.dumps(MODULES).replace("'", ""), **common)
        elif rank <= n_impl:
            roles[rank] = "implementer"
            offset = (rank - 1) % len(MODULES)
            rotated = MODULES[offset:] + MODULES[:offset]
            tasks[rank] = IMPLEMENTER_TASK.format(
                preferred=rotated[0],
                module_order="\n".join(f"{i + 1}. `{m}`" for i, m in enumerate(rotated)),
                **common)
        else:
            roles[rank] = "integrator"
            tasks[rank] = INTEGRATOR_TASK.format(**common)
    cards = write_rank_cards(job_dir, world_size, tasks, roles)
    return {**info, "cards": cards, "artifact": pkg_dir, "roles": roles,
            "modules": MODULES}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.join(os.path.dirname(__file__), "runs"))
    parser.add_argument("-n", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.n), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
