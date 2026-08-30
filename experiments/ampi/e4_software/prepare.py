"""E4 --- A tightly coupled task: ten agents build one working Python package.

Translation parallelises because the chapters barely interact.  Software does
not.  Modules share interfaces, the interfaces change while the modules are
being written, two agents editing the same file lose each other's work, and two
agents that both decide to implement the parser have wasted a turn each.  These
are the coordination failures the multi-agent literature reports most often,
and they are exactly the failures that message passing has primitives for.

The harness uses four of them and nothing else:

``bcast``       the architect publishes the interface contract to everyone, so
                no agent starts from a different understanding of it;
``win-claim``   a module is claimed by compare-and-swap before it is written,
                so two agents cannot pick the same one;
``win-lock``    the shared interface file is edited under an exclusive lease,
                so a late clarification cannot clobber an earlier one;
``barrier``     implementation must finish before integration starts, so the
                integrators never test a half-written tree.

The deliverable is a real package with real tests, so the run either produces
working software or it does not, and ``pytest`` is the judge rather than a
rubric.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from ampi.launch import create_job, write_rank_cards  # noqa: E402

CONTRACT = {
    "package": "tokenbudget",
    "one_line": "A library for planning and enforcing token budgets across a set of "
                "cooperating LLM agents.",
    "python": ">=3.11, standard library only, no third-party dependencies",
    "modules": {
        "estimate": {
            "path": "tokenbudget/estimate.py",
            "exports": [
                "count_tokens(text: str) -> int",
                "estimate_messages(messages: list[dict]) -> int",
            ],
            "contract": "count_tokens is deterministic, returns 0 for empty or None input, "
                        "is monotone non-decreasing in len(text), and never returns a "
                        "negative number. estimate_messages sums count_tokens over the "
                        "'content' field of each message plus a fixed 4-token per-message "
                        "overhead.",
        },
        "policy": {
            "path": "tokenbudget/policy.py",
            "exports": [
                "class Budget(limit: int, reserved: int = 0)",
                "Budget.remaining(used: int) -> int",
                "Budget.admits(used: int, incoming: int) -> bool",
                "class BudgetExceeded(Exception)",
            ],
            "contract": "remaining() is max(0, limit - reserved - used). admits() is True "
                        "iff incoming <= remaining(used). Budget rejects a negative limit "
                        "or reserved with ValueError, and reserved > limit with ValueError.",
        },
        "compact": {
            "path": "tokenbudget/compact.py",
            "exports": [
                "head_tail(text: str, budget: int, head_frac: float = 0.6) -> str",
                "drop_oldest(messages: list[dict], budget: int) -> list[dict]",
            ],
            "contract": "head_tail returns text unchanged when it already fits, otherwise "
                        "keeps a head_frac prefix and the remaining suffix joined by the "
                        "marker '\\n...[elided]...\\n', and the result must fit the budget "
                        "as measured by estimate.count_tokens. drop_oldest removes messages "
                        "from the front until estimate_messages fits the budget, and always "
                        "keeps the last message even if it alone exceeds the budget.",
        },
        "ledger": {
            "path": "tokenbudget/ledger.py",
            "exports": [
                "class Ledger()",
                "Ledger.charge(agent: str, tokens: int) -> int",
                "Ledger.release(agent: str, tokens: int) -> int",
                "Ledger.usage() -> dict[str, int]",
                "Ledger.total() -> int",
            ],
            "contract": "charge and release return the agent's new balance. Balances never "
                        "go below zero. usage() returns a copy, not the internal dict. "
                        "charge with negative tokens raises ValueError.",
        },
        "planner": {
            "path": "tokenbudget/planner.py",
            "exports": [
                "plan_fanout(total_budget: int, n_agents: int, reserve_frac: float = 0.1) "
                "-> list[int]",
            ],
            "contract": "Returns exactly n_agents non-negative integers summing to at most "
                        "total_budget * (1 - reserve_frac), as evenly as possible, with any "
                        "remainder given to the earliest agents. Raises ValueError if "
                        "n_agents < 1 or total_budget < 0.",
        },
        "cli": {
            "path": "tokenbudget/cli.py",
            "exports": ["main(argv: list[str] | None = None) -> int"],
            "contract": "Subcommands 'count' (--text or --file, prints the token count), "
                        "'plan' (--total, --agents, prints a JSON list), and 'compact' "
                        "(--file, --budget, prints the compacted text). Always exits 0 on "
                        "success and 2 on a usage error, and prints JSON for 'count' and "
                        "'plan'.",
        },
    },
    "rules": [
        "Standard library only.",
        "Every module has a module docstring saying what it is for.",
        "Public functions have type annotations.",
        "No module imports another module's private names.",
        "estimate.py must not import any other package module (it is the leaf).",
    ],
}

ARCHITECT_TASK = """
You are the architect. Your job is to publish the contract, then hold the
interface stable while seven implementers and two integrators work against it.

**Phase 1 --- publish.** The contract is already written for you in
`{contract_file}`. Read it, then broadcast it so that every rank starts from
the same text:

```
ampi init --role architect
ampi win-create --name build
ampi bcast --root 0 --json-file {contract_file} --timeout 1200
ampi win-put --win build --key contract --json-file {contract_file}
```

Then create the package skeleton at `{pkg_dir}`: the directory
`{pkg_dir}/tokenbudget/` with an `__init__.py` that re-exports the public names
listed in the contract, a `{pkg_dir}/tests/` directory, and a
`{pkg_dir}/pyproject.toml` naming the package `tokenbudget`. Do not implement
any module other than `__init__.py`; that is the implementers' work.

Then `ampi barrier --timeout 1800` to release the implementers.

**Phase 2 --- answer questions and keep the contract honest.** Implementers
may send you clarification requests on tag 10. Poll for them and answer:

```
ampi probe --tag 10
ampi recv --tag 10 --timeout 60 --nonblocking
```

If you must amend the contract, take the lease first so amendments cannot
clobber each other:

```
ampi win-lock --win build --key contract --ttl 120
ampi win-get --win build --key contract --out {scratch}/contract-now.json
# edit, then
ampi win-put --win build --key contract --json-file {scratch}/contract-now.json
ampi win-unlock --lock-id <the lock_id you were given>
```

Reply to the asker with `ampi send --to <their rank> --tag 11 --text "..."`.

Keep polling until the next barrier. Then `ampi barrier --timeout 1800` a
second time, which marks the end of implementation.

**Phase 3 --- report.** After the second barrier, run
`ampi allgather --json-file {scratch}/mine.json --timeout 1200` where
`{scratch}/mine.json` is `{{"rank": 0, "role": "architect", "module": null}}`.
Write what you learn to `{out_file}` as
`{{"rank": 0, "modules_reported": [...], "amendments": <count>}}`, then
`ampi finalize --note "e4 architect done"`.

Heartbeat with `ampi hb --expect-idle 300` before long stretches of work.
Do not run git.
"""

IMPLEMENTER_TASK = """
You are an implementer. You will claim one module of a shared Python package,
write it, and make sure it satisfies the published contract.

**Phase 1 --- receive the contract and claim a module.**

```
ampi init --role implementer
ampi win-create --name build
ampi bcast --root 0 --timeout 1200 --out {scratch}/contract.json
ampi barrier --timeout 1800
```

The broadcast gives you the contract; read `{scratch}/contract.json`.

Now claim a module. The modules are named `estimate`, `policy`, `compact`,
`ledger`, `planner`, `cli`. Try them in this order --- start with
**{preferred}** --- and take the first one you win:

```
ampi win-claim --win build --key module/<name>
```

`"claimed": true` means it is yours. `"claimed": false` means somebody else got
there first: move on to the next name. Do not write a module you did not claim.
If you win nothing, you are a spare: help by writing extra tests instead (see
Phase 3) and say so in your report.

**Phase 2 --- implement.** Write your module at `{pkg_dir}/<path from the
contract>`. Follow the contract exactly: the listed exports, the stated
behaviour, standard library only, type annotations, a module docstring. Real
working code, not stubs.

Then write tests for your own module at
`{pkg_dir}/tests/test_<name>.py` --- at least six tests, covering the edge
cases the contract names explicitly (empty input, zero, negative, the "always
keeps the last message" rule, and so on).

Run them:

```
cd {pkg_dir} && /workspace/.venv/bin/python -m pytest tests/test_<name>.py -q
```

Fix until they pass. If the contract is ambiguous, ask the architect rather
than guessing:

```
ampi send --to 0 --tag 10 --text "your question"
ampi recv --source 0 --tag 11 --timeout 300
```

Publish a summary of what you built:

```
ampi win-put --win build --key module/<name>/summary --json '{{"module": "<name>", "by": {rank}, "exports": [...], "tests": <n>, "passing": true}}'
```

**Phase 3 --- integrate.**

```
ampi barrier --timeout 1800
ampi allgather --json-file {scratch}/mine.json --timeout 1200
```

where `{scratch}/mine.json` is
`{{"rank": {rank}, "module": "<what you claimed, or null>", "tests": <n>}}`.

Then write `{out_file}` as
`{{"rank": {rank}, "claimed": "<module or null>", "attempts": [<names you tried>],
  "tests_written": <n>, "tests_passing": <n>}}`
and run `ampi finalize --note "e4 implementer done"`.

Heartbeat with `ampi hb --expect-idle 300` before writing code.
Do not edit another agent's module. Do not run git.
"""

INTEGRATOR_TASK = """
You are an integrator. You do not write modules; you make the package work as a
whole and you report honestly on whether it does.

**Phase 1 --- wait for the design.**

```
ampi init --role integrator
ampi win-create --name build
ampi bcast --root 0 --timeout 1200 --out {scratch}/contract.json
ampi barrier --timeout 1800
```

While the implementers work, write the **cross-module integration tests** at
`{pkg_dir}/tests/test_integration_{rank}.py`. These must exercise the modules
*together* rather than one at a time --- for example: plan a fan-out budget,
charge a ledger against it, compact a message list to fit what remains, and
assert that the compacted list really does fit. Write at least five such tests
against the contract in `{scratch}/contract.json`. They will fail until the
implementers are done; that is expected.

**Phase 2 --- integrate after the barrier.**

```
ampi barrier --timeout 1800
```

Now the implementation is complete. Run the whole suite:

```
cd {pkg_dir} && /workspace/.venv/bin/python -m pytest -q
```

Fix **integration-level** problems only: a missing re-export in `__init__.py`,
an import cycle, a mismatch between two modules' assumptions. If a single
module is simply wrong, do not silently rewrite it --- take the interface lease,
record the defect, and message its author:

```
ampi win-lock --win build --key defects --ttl 120
ampi win-get --win build --key defects --out {scratch}/defects.json
# append your finding, then
ampi win-put --win build --key defects --json-file {scratch}/defects.json
ampi win-unlock --lock-id <lock_id>
ampi send --to <author rank> --tag 12 --text "defect: ..."
```

**Phase 3 --- report.**

```
ampi allgather --json-file {scratch}/mine.json --timeout 1200
```

where `{scratch}/mine.json` is
`{{"rank": {rank}, "role": "integrator"}}`.

Run the suite one last time and write `{out_file}`:

```
{{"rank": {rank}, "pytest_exit": <code>, "tests_collected": <n>, "tests_passed": <n>,
  "tests_failed": <n>, "defects_filed": <n>, "summary": "<one honest sentence>"}}
```

Report what actually happened, including failures. Then
`ampi finalize --note "e4 integrator done"`.

Heartbeat with `ampi hb --expect-idle 300` before long stretches.
Do not run git.
"""

MODULE_ORDER = ["estimate", "policy", "compact", "ledger", "planner", "cli"]


def prepare(root: str, world_size: int = 10) -> dict:
    job_dir = os.path.join(os.path.abspath(root), "job")
    pkg_dir = os.path.join(job_dir, "artifact")
    os.makedirs(pkg_dir, exist_ok=True)
    info = create_job(job_dir, world_size, ctx_limit=120_000,
                      meta={"experiment": "e4-collaborative-software"})
    contract_file = os.path.join(job_dir, "contract.json")
    with open(contract_file, "w", encoding="utf-8") as fh:
        json.dump(CONTRACT, fh, indent=2)

    tasks: dict[int, str] = {}
    roles: dict[int, str] = {}
    n_impl = world_size - 3
    for rank in range(world_size):
        scratch = os.path.join(job_dir, "ranks", str(rank))
        os.makedirs(scratch, exist_ok=True)
        out_file = os.path.join(scratch, "result.json")
        common = {"rank": rank, "scratch": scratch, "pkg_dir": pkg_dir, "out_file": out_file}
        if rank == 0:
            roles[rank] = "architect"
            tasks[rank] = ARCHITECT_TASK.format(contract_file=contract_file, **common)
        elif rank <= n_impl:
            roles[rank] = "implementer"
            # A staggered preference order so the common case needs no retry,
            # while a lost claim still has somewhere to go.
            preferred = MODULE_ORDER[(rank - 1) % len(MODULE_ORDER)]
            rotated = MODULE_ORDER[(rank - 1) % len(MODULE_ORDER):] + \
                MODULE_ORDER[:(rank - 1) % len(MODULE_ORDER)]
            tasks[rank] = IMPLEMENTER_TASK.format(
                preferred=preferred, **common).replace(
                "The modules are named `estimate`, `policy`, `compact`,\n`ledger`, "
                "`planner`, `cli`.",
                "The modules, in the order you should try them, are: "
                + ", ".join(f"`{m}`" for m in rotated) + ".")
        else:
            roles[rank] = "integrator"
            tasks[rank] = INTEGRATOR_TASK.format(**common)
    cards = write_rank_cards(job_dir, world_size, tasks, roles)
    return {**info, "cards": cards, "artifact": pkg_dir, "roles": roles}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.join(os.path.dirname(__file__), "runs"))
    parser.add_argument("-n", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.n), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
