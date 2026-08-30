#!/usr/bin/env python3
"""Experiment 2: collaborative development of a software system.

Why this task
-------------
Translation is the easy case: the work partitions and the dependencies are a
global agreement plus a nearest-neighbour agreement. Software development is the
hard case, and it is hard in exactly the way tightly-coupled parallel codes are
hard: the pieces have *mutual* dependencies, the interfaces between them are not
given in advance, and a disagreement about a shared representation does not
degrade the result gracefully -- it produces an artefact that does not run at
all.

Eight agents implement **MiniScheme**, a Scheme interpreter, in Python. The public
behaviour is fixed by `spec.md` and graded by a held-out suite the agents never
see. The *internal* interfaces -- how a pair is represented, what an environment
lookup returns for an unbound name, which exception primitives raise, how the
evaluator signals a tail call -- are deliberately left unspecified. Agreeing them
is the coordination problem, and it is the one that the protocol either helps
with or does not.

Protocol mechanisms exercised
-----------------------------
* ``AMPI_Bcast`` of the specification and, later, of test failures;
* ``AMPI_Allreduce`` with an **agent-evaluated operator** to reduce ten
  independently-writeach rank's interface proposal into one contract;
* a **window** as the shared blackboard: the contract, an append-only decisions
  log written with lock-free ``accumulate``, and a fix log;
* ``AMPI_Win_lock`` around the one genuinely shared source file;
* ``AMPI_Win_fence`` and ``AMPI_Barrier`` to separate implementation from
  integration rounds;
* ``AMPI_Comm_agree`` to decide whether the team is done.

Arms
----
``naive``
    The same ten agents, the same specification, the same module assignment, and
    a shared filesystem they may read freely -- but no negotiated contract, no
    shared decisions log, and no synchronised integration rounds. This is a
    strong baseline, not a strawman: a shared repository with no coordination
    protocol is how most multi-agent coding harnesses actually work.
``ampi``
    The full protocol above.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from lib import PROTOCOL_DISCIPLINE, Rank, create_job, launch_plan, write_spec  # noqa: E402

MODULES: List[Dict[str, object]] = [
    {
        "rank": 0,
        "role": "integrator",
        "files": ["interp/__init__.py"],
        "owns": "the public API and the wiring that assembles everyone else's modules",
        "detail": """\
You own `interp/__init__.py`. It must export `run(source: str) -> str` and
`SchemeError`, and it must be the only place that knows how the modules fit
together: import the reader, the evaluator, the environment and every primitive
module, build the global environment from the primitive tables, read all
top-level data from the source, evaluate them in order, and return the `write`
form of the last value.

You are also the integrator: you run `python3 tests_visible.py`, you broadcast
the failures, and you assign the fixes. You do **not** fix other people's files.""",
    },
    {
        "rank": 1,
        "role": "reader",
        "files": ["interp/reader.py"],
        "owns": "the tokeniser and the S-expression reader",
        "detail": """\
You own `interp/reader.py`. Provide a function that turns a source string into a
list of top-level data, using the value representation that `interp/datum.py`
defines. Handle every literal form, both bracket styles, dotted pairs, vectors,
quote/quasiquote/unquote/unquote-splicing sugar, line comments and nestable
block comments. Unbalanced input and bad tokens must raise the shared error
type.""",
    },
    {
        "rank": 2,
        "role": "data representation",
        "files": ["interp/datum.py"],
        "owns": "the value representation, the printer, and the shared error type",
        "detail": """\
You own `interp/datum.py`, the most depended-upon module in the system: it
defines how every Scheme value is represented in Python (`Pair`, `Symbol`,
`Char`, `Vector`, the unspecified value, the empty list), the `SchemeError`
exception that everyone raises, and the `write`/`display` printers. Every other
rank imports from you.

Because everyone depends on you, publish your decisions early and do not change
them silently. If you must change a representation after the contract is agreed,
announce it in the decisions log and message the affected ranks.""",
    },
    {
        "rank": 3,
        "role": "environments",
        "files": ["interp/env.py"],
        "owns": "environments, frames, variable lookup, and closures",
        "detail": """\
You own `interp/env.py`: environment frames with parent links, define / lookup /
set!, and the closure object that `lambda` produces (parameter list including
rest arguments, body, captured environment, optional name). Binding arguments to
parameters -- including the `(a b . rest)` and bare-`args` forms -- and raising on
arity mismatch belongs to you.""",
    },
    {
        "rank": 4,
        "role": "special forms",
        "files": ["interp/special.py"],
        "owns": "every special form",
        "detail": """\
You own `interp/special.py`. Export a table mapping form names to handlers for
*all* the special forms in the specification: quote, if, define, set!, lambda,
begin, let, let*, letrec, named let, cond (with `else` and `=>`), case, and, or,
when, unless, do, quasiquote.

Tail position is the hard part and it is a shared interface: a handler for a form
whose last expression is in tail position must hand that expression back to rank
6's evaluator rather than recursing into it. Agree that mechanism with rank 6
explicitly -- it is the single most important internal interface in this
project.""",
    },
    {
        "rank": 5,
        "role": "numeric and list primitives",
        "files": ["interp/prim_num.py", "interp/prim_list.py"],
        "owns": "numeric primitives, pair/list primitives, equality and type predicates",
        "detail": """\
You own `interp/prim_num.py` and `interp/prim_list.py`: every numeric primitive,
every pair and list primitive, plus `eq?`, `eqv?`, `equal?`, `not` and the type
predicates. `map`, `for-each`, `apply`, `filter` and `reduce` must call back into
the evaluator to apply a procedure -- agree with rank 6 on how a primitive obtains
that capability. Agree the primitive-table shape and calling convention with rank
7, because rank 0 merges both tables into one global environment.""",
    },
    {
        "rank": 6,
        "role": "evaluator",
        "files": ["interp/evaluator.py"],
        "owns": "the evaluation driver and proper tail calls",
        "detail": """\
You own `interp/evaluator.py`: the driver that evaluates one datum in one
environment, dispatching to rank 4's special-form table and applying procedures.
**Tail calls must not consume Python stack** -- use an explicit loop (a
trampoline), not Python recursion, for tail position. A tail-recursive Scheme
loop of 100000 iterations must complete. You must define and publish the
protocol by which a special-form handler hands a tail expression back to you, and
the way a primitive obtains the ability to apply a procedure.""",
    },
    {
        "rank": 7,
        "role": "string/vector primitives",
        "files": ["interp/prim_str.py", "interp/prim_vec.py"],
        "owns": "string, char, symbol and vector primitives, plus error/display/write",
        "detail": """\
You own `interp/prim_str.py` and `interp/prim_vec.py`: every string, character,
symbol and vector primitive, plus `error`, `display`, `newline` and `write`.
`display`/`write` append to an output buffer rather than printing. Use the same
table shape and calling convention as rank 5.""",
    },
]

VISIBLE_TESTS = '''\
#!/usr/bin/env python3
"""Visible smoke tests for MiniScheme.

These are a small sample. The grading suite is larger and is not in this
repository. Run: python3 tests_visible.py
"""

import sys
import traceback

CASES = [
    ("42", "42"),
    ("#t", "#t"),
    ('"hi"', '"hi"'),
    ("'(1 2 3)", "(1 2 3)"),
    ("'(1 . 2)", "(1 . 2)"),
    ("'#(1 2)", "#(1 2)"),
    ("#\\\\a", "#\\\\a"),
    ("(+ 1 2 3)", "6"),
    ("(- 10 3)", "7"),
    ("(if (> 3 2) 'yes 'no)", "yes"),
    ("(define (f a b) (+ a b)) (f 2 3)", "5"),
    ("((lambda (x) (* x x)) 7)", "49"),
    ("((lambda (a . rest) rest) 1 2 3)", "(2 3)"),
    ("(let ((a 1) (b 2)) (+ a b))", "3"),
    ("(let* ((a 1) (b (+ a 1))) b)", "2"),
    ("(letrec ((f (lambda (n) (if (= n 0) 1 (* n (f (- n 1))))))) (f 5))", "120"),
    ("(cond ((= 1 2) 'a) (else 'c))", "c"),
    ("(case 3 ((3 4) 'mid) (else 'high))", "mid"),
    ("(and 1 2)", "2"),
    ("(or #f 5)", "5"),
    ("`(1 ,(+ 1 1) 3)", "(1 2 3)"),
    ("(car '(1 2))", "1"),
    ("(cdr '(1 2))", "(2)"),
    ("(cons 1 '(2))", "(1 2)"),
    ("(length '(1 2 3))", "3"),
    ("(append '(1) '(2 3))", "(1 2 3)"),
    ("(reverse '(1 2 3))", "(3 2 1)"),
    ("(map (lambda (x) (* x x)) '(1 2 3))", "(1 4 9)"),
    ("(apply + '(1 2 3))", "6"),
    ("(equal? '(1 (2)) '(1 (2)))", "#t"),
    ('(string-append "ab" "cd")', '"abcd"'),
    ('(string-length "hello")', "5"),
    ("(symbol->string 'abc)", '"abc"'),
    ("(char->integer #\\\\A)", "65"),
    ("(vector-ref (vector 1 2) 1)", "2"),
    ("(define v (vector 1 2)) (vector-set! v 0 9) v", "#(9 2)"),
    ("(let go ((i 0)) (if (= i 100000) i (go (+ i 1))))", "100000"),
    ("(define (fact n) (if (= n 0) 1 (* n (fact (- n 1))))) (fact 10)", "3628800"),
]

ERROR_CASES = ["(car '())", "(undefined-xyz)", "(1 2)", "(/ 1 0)", "(+ 1 2"]


def main() -> int:
    try:
        from interp import run, SchemeError
    except Exception:
        print("IMPORT FAILED:")
        traceback.print_exc()
        return 1
    npass = nfail = 0
    for src, want in CASES:
        try:
            got = run(src)
        except Exception as exc:
            print(f"FAIL  {src[:60]!r}\\n      raised {type(exc).__name__}: {exc}")
            nfail += 1
            continue
        if " ".join(str(got).split()) == " ".join(want.split()):
            npass += 1
        else:
            print(f"FAIL  {src[:60]!r}\\n      want {want!r}  got {got!r}")
            nfail += 1
    for src in ERROR_CASES:
        try:
            got = run(src)
            print(f"FAIL  {src[:60]!r} should have raised SchemeError, returned {got!r}")
            nfail += 1
        except SchemeError:
            npass += 1
        except Exception as exc:
            print(f"FAIL  {src[:60]!r} raised {type(exc).__name__} not SchemeError: {exc}")
            nfail += 1
    print(f"\\n{npass} passed, {nfail} failed, out of {npass + nfail}")
    return 0 if nfail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def ampi_task(m: Dict[str, object], np: int, project: Path, work: Path, spec: Path) -> str:
    r = int(m["rank"])
    files = ", ".join(f"`{f}`" for f in m["files"])  # type: ignore[arg-type]
    integrator = r == 0
    return f"""\
You are rank {r} of {np}, the **{m['role']}** on a team implementing MiniScheme.

- **Specification**: `{spec}` — read it fully before you write code.
- **Project root**: `{project}` (all source lives here; `interp/` is the package)
- **Your files**: {files} — you own these and nobody else may edit them.
- **Your working directory**: `{work}`
- **You own**: {m['owns']}

{m['detail']}

Do not edit files you do not own. If you need a change elsewhere, use
`ampi send --to <rank> --tag request --in "..."` and record it in the decisions log.

---

**PHASE 0 — join**
```
ampi init
ampi info
```

**PHASE 1 — receive the specification (AMPI_Bcast)**
```
{f'ampi bcast --root 0 --label spec --in @{spec} --timeout 120' if integrator else 'ampi bcast --root 0 --label spec --timeout 120 --budget 1200'}
```
{"You are the root: you broadcast the spec." if integrator else "You will get a handle plus a clipped view. Read the spec from the file path above rather than spending context on the broadcast payload — that is what handles are for."}

**PHASE 2 — propose the interfaces you provide and require**

Before writing any code, write `{work}/proposal.json` describing your module's
external surface and what you need from others. Use exactly this shape:

```json
{{
  "rank": {r},
  "module": "{m['files'][0]}",
  "provides": [
    {{"name": "read_all", "signature": "read_all(source: str) -> list[object]",
      "notes": "raises SchemeError on bad input"}}
  ],
  "requires": [
    {{"from_module": "interp/datum.py", "name": "Pair",
      "why": "I construct pairs while reading lists"}}
  ],
  "decisions_i_propose": [
    "Pairs are a Pair class with mutable car/cdr attributes, not Python lists"
  ]
}}
```

Be concrete. Name the functions and classes, give signatures, and state the
representation decisions you are assuming. Vagueness here is what breaks
integration later.

**PHASE 3 — reduce all proposals into ONE contract (AMPI_Allreduce, agent operator)**
```
ampi allreduce --op agent:merge_contract --label contract \\
    --in @{work}/proposal.json --algo reduce_bcast --timeout 150 --materialize --operand-budget 3000
```
When the output says `action_required=merge`, run `ampi hb --extend 900`, read the two operand files and
produce **one** merged JSON object of the same shape, with these keys instead of
the per-rank ones:

```json
{{"modules": {{"interp/datum.py": {{"provides": [...], "decisions": [...]}}}},
  "global_decisions": ["..."], "unresolved": ["..."]}}
```

Merge rules that matter: where two proposals conflict about a shared
representation, **pick one and record it in `global_decisions`** — do not keep
both. Where a `requires` has no matching `provides`, add it to `unresolved`.
Never drop a module. Then run the `ampi reduce-commit` command it gives you, and
keep going until you see `complete=true`.

The final payload is **the CONTRACT**. Save it to `{work}/contract.json`.
{"As rank 0, also publish it: `ampi win create --name build` then `ampi win put --win build --key contract --in @" + str(work) + "/contract.json`" if integrator else "Read the published copy any time with `ampi win get --win build --key contract --budget 2500`."}

```
ampi memo put phase "contract agreed"
```

**PHASE 4 — implement**

This is your longest step. First:
```
ampi hb --extend 1800
```
and run it again every few minutes while you work — if you go silent longer than
your lease, the job declares you dead and discards your work.

Write your files, honouring the contract. Two shared-state rules:

1. **Record every decision you make that others could trip over**, lock-free:
```
ampi win acc --win build --key decisions --op union \\
    --in '["rank {r}: <one-line decision>"]'
```
Read what others have decided before you guess:
```
ampi win get --win build --key decisions --budget 1500
```
2. There is exactly one genuinely shared file, `interp/contract.py`, holding the
agreed constants and tiny helpers everyone needs. To touch it, take the lock:
```
ampi win lock --win build --key contract-file --timeout 120
# ... edit interp/contract.py ...
ampi win unlock --win build --key contract-file
```
Never edit it without the lock; the run measures lock discipline.

Check your own work as you go (`python3 -c "import interp"` from `{project}`).

```
ampi memo put phase "implemented"
```

**PHASE 5 — integration round 1 (AMPI_Win_fence, then AMPI_Bcast of failures)**
```
ampi win fence --win build --label impl-done --quorum 0.9 --timeout 150
```
{f'''Then run the visible tests and broadcast the result:
```
cd {project} && python3 tests_visible.py > {work}/failures_r1.txt 2>&1; echo "exit=$?" >> {work}/failures_r1.txt
ampi bcast --root 0 --label failures-r1 --in @{work}/failures_r1.txt --timeout 150
```
Then assign the fixes. **Send to every rank, including the ones with nothing to
fix** --- an empty assignment is still an assignment, and a rank that is waiting for
a message you decided not to send waits forever:
```
for r in 1 2 3 4 5 6 7; do
  ampi send --to $r --tag fix --in "round 1: <what is broken, or: nothing assigned to you>"
done
```''' if integrator else '''Then receive the failures and check for a fix assignment:
```
ampi bcast --root 0 --label failures-r1 --timeout 150 --materialize
ampi recv --from 0 --tag fix --timeout 120 --materialize
```
If the `recv` times out 5 times, stop waiting: read the broadcast failures
yourself, fix anything clearly in *your* files, and continue. Never wait
indefinitely for a message that may not have been sent.'''}

Fix what is yours. Log what you changed:
```
ampi win acc --win build --key fixlog --op union --in '["rank {r} r1: <what you fixed>"]'
ampi barrier --label fix-r1 --quorum 0.9 --timeout 150
```

**PHASE 6 — integration round 2**

Repeat Phase 5 with the labels `failures-r2` and `fix-r2`.
{f'''```
cd {project} && python3 tests_visible.py > {work}/failures_r2.txt 2>&1; echo "exit=$?" >> {work}/failures_r2.txt
ampi bcast --root 0 --label failures-r2 --in @{work}/failures_r2.txt --timeout 150
for r in $(seq 1 {np - 1}); do
  ampi send --to $r --tag fix2 --in "round 2: <what is broken, or: nothing assigned to you>"
done
```
Send to every rank even when there is nothing to fix.''' if integrator else '''```
ampi bcast --root 0 --label failures-r2 --timeout 150 --materialize
ampi recv --from 0 --tag fix2 --timeout 120 --materialize
```
If that `recv` times out 5 times, stop waiting and continue to the barrier.'''}
```
ampi barrier --label fix-r2 --quorum 0.9 --timeout 150
```

**PHASE 7 — report and decide**
Write `{work}/report.json`:
```json
{{"rank": {r}, "files_written": ["..."], "contract_followed": true,
  "contract_deviations": ["..."], "requests_sent": 0, "requests_received": 0,
  "lock_acquisitions": 0, "notes": "one or two sentences"}}
```
```
ampi gather --root 0 --label reports --in @{work}/report.json --timeout 150
ampi agree --label ship --flag true --timeout 150 --quorum 0.9
ampi fini
```

Finally, reply with: which phases you completed, how many merge steps you
performed in Phase 3, how many timed-out calls you retried, whether the contract
actually prevented an integration bug (give a concrete example if so), and any
part of the `ampi` interface that was confusing or that you worked around.
"""


def naive_task(m: Dict[str, object], np: int, project: Path, work: Path, spec: Path) -> str:
    r = int(m["rank"])
    files = ", ".join(f"`{f}`" for f in m["files"])  # type: ignore[arg-type]
    integrator = r == 0
    return f"""\
You are agent {r} of {np}, the **{m['role']}** on a team implementing MiniScheme.

- **Specification**: `{spec}` — read it fully before you write code.
- **Project root**: `{project}` (all source lives here; `interp/` is the package)
- **Your files**: {files} — you own these and nobody else may edit them.
- **Your working directory**: `{work}`
- **You own**: {m['owns']}

{m['detail']}

The other {np - 1} agents are working at the same time in the same project
directory. You share a filesystem, so you may read their files. Do not edit
files you do not own.

---

**PHASE 0 — join**
```
ampi init
ampi info
```

**PHASE 1 — implement**

This is your longest step. First:
```
ampi hb --extend 1800
```
and run it again every few minutes while you work.

Read the specification and write your files. Decide any interface details you
need yourself, using your best judgement about what your teammates will expect.

Check your own work as you go (`python3 -c "import interp"` from `{project}`).

{f'''**PHASE 2 — integrate**

Run the visible tests and fix anything that is in *your* files:
```
cd {project} && python3 tests_visible.py
```
You are the integrator, so also make `interp/__init__.py` wire together whatever
your teammates actually produced.''' if integrator else '''**PHASE 2 — check**

Once your files are written, check whether the package imports and whether the
visible tests exercise your part:
```
cd ''' + str(project) + ''' && python3 tests_visible.py
```
Fix anything that is clearly in your own files.'''}

**PHASE 3 — report**
Write `{work}/report.json`:
```json
{{"rank": {r}, "files_written": ["..."], "contract_followed": false,
  "contract_deviations": [], "requests_sent": 0, "requests_received": 0,
  "lock_acquisitions": 0, "notes": "one or two sentences"}}
```
```
ampi gather --root 0 --label reports --in @{work}/report.json --timeout 150
ampi fini
```

Finally, reply with: whether you completed, and any interface assumption you had
to guess about because nobody told you.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["ampi", "naive"], required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--np", type=int, default=8)
    args = ap.parse_args()
    if args.np != len(MODULES):
        raise SystemExit(f"this experiment defines {len(MODULES)} module owners; use --np {len(MODULES)}")

    out = Path(args.out or f"runs/e2_{args.arm}").resolve()
    if out.exists():
        shutil.rmtree(out)
    project = out / "project"
    work = out / "work"
    (project / "interp").mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    spec_dst = project / "spec.md"
    shutil.copy(HERE / "spec.md", spec_dst)
    (project / "tests_visible.py").write_text(VISIBLE_TESTS, encoding="utf-8")
    (project / "interp" / "contract.py").write_text(
        '"""Agreed constants and helpers shared by every MiniScheme module.\n\n'
        "Take the AgentMPI window lock `contract-file` before editing this file.\n"
        '"""\n\n',
        encoding="utf-8",
    )

    ranks: List[Rank] = []
    for m in MODULES:
        r = int(m["rank"])
        rw = work / f"rank{r:02d}"
        rw.mkdir(parents=True, exist_ok=True)
        task = (ampi_task if args.arm == "ampi" else naive_task)(m, args.np, project, rw, spec_dst)
        ranks.append(
            Rank(rank=r, role=str(m["role"]), task=task,
                 env={"PROJECT": str(project), "WORKDIR": str(rw), "SPEC": str(spec_dst)})
        )

    preamble = f"""\
**Task**: {args.np} agents jointly implement **MiniScheme**, a Scheme interpreter in Python,
one module per rank. The public behaviour is fixed by the specification and is graded by a
held-out test suite you will not see. The *internal* interfaces between modules are not
specified: agreeing them is the coordination problem.

**Why this is hard**: the modules depend on each other mutually. A disagreement about how a
pair is represented, or about how a special form hands a tail expression back to the
evaluator, does not degrade the result — it produces something that does not run.

**Arm**: `{args.arm}`.
{"This arm uses the full protocol: an agent-evaluated Allreduce to reduce each rank's interface proposal into one contract, a window as the shared decisions log, a lock around the single shared file, and synchronised integration rounds." if args.arm == "ampi" else "This arm has the same agents, the same specification, the same module assignment and a shared filesystem, but no negotiated contract, no shared decisions log and no synchronised integration rounds."}

{PROTOCOL_DISCIPLINE}
"""
    spec_path = write_spec(
        out, label=f"e2-codev-{args.arm}", preamble=preamble, ranks=ranks,
        config={"eager_tokens": 700, "ctx_budget": 140_000, "lease_ns": 420 * 10 ** 9,
                "timeout_ns": 45 * 10 ** 9, "lock_lease_ns": 900 * 10 ** 9},
    )
    manifest = create_job(spec_path)
    plan = launch_plan(manifest, out / "launch_plan.json")
    (out / "experiment.json").write_text(
        json.dumps({"experiment": "e2_codev", "arm": args.arm, "np": args.np,
                    "project": str(project),
                    "modules": [{"rank": m["rank"], "role": m["role"], "files": m["files"]}
                                for m in MODULES]},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps({"arm": args.arm, "job": manifest["job"], "root": str(out),
                      "world_size": manifest["world_size"], "launch_plan": str(plan)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
