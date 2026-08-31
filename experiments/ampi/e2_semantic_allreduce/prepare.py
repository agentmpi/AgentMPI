"""E2 --- Does the operator's declared algebra actually buy anything?

The central claim of the collective layer is that declaring a reduction
operator associative lets the runtime substitute a tree for a linear fold, and
that the substitution is worth making: the tree does the same number of
operator evaluations but only lg p of them lie on the critical path, so a
semantic reduction over p agents finishes in lg p model turns rather than p.

Everything about that claim is checkable in simulation except the part that
matters --- whether the *answer* survives re-association when the operator is a
language model rather than integer addition.  So this experiment runs both
schedules with real agents over the same inputs and compares latency, cost and
agreement of the results.

Setup.  Eight agents have each reviewed a different chapter of the same
technical document and hold a partial style guide: opinions about terminology,
tone, and formatting conventions, deliberately seeded so that some of them
conflict.  They allreduce their style guides under ``AMPI_SYNTHESIZE``, a
semantic operator, once with ``--algo linear`` (canonical order, depth p-1) and
once with ``--algo binomial`` (tree, depth lg p).  The reduction is a genuine
LLM merge: at each step the runtime suspends the collective and hands two
partial guides to the agent whose turn it is to combine them.

What we measure, from the trace only: operator evaluations in total and on the
critical path, wall-clock, tokens moved, and the semantic agreement between the
two results.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

from ampi.launch import create_job, write_rank_cards  # noqa: E402

# Eight partial style guides.  Ranks 2 and 5 disagree about the same term as
# ranks 0 and 7, so a merge that silently takes the last writer is detectable;
# ranks 3 and 6 hold constraints that are compatible but stated differently, so
# a merge that simply concatenates is also detectable.
CHAPTER_NOTES: list[dict[str, str]] = [
    {
        "chapter": "1. Introduction",
        "notes": (
            "Terminology: render the German 'Rechner' as 'computer', never 'calculator'.\n"
            "Tone: the introduction is addressed to a general technical reader; expand every "
            "acronym on first use.\n"
            "Formatting: chapter headings use sentence case.\n"
            "Numbers: spell out numbers below ten in prose."
        ),
    },
    {
        "chapter": "2. Architecture",
        "notes": (
            "Terminology: 'Speicher' is 'memory' when it refers to RAM and 'storage' when it "
            "refers to disk; the distinction is load bearing in this chapter.\n"
            "Tone: architectural description stays in the present tense.\n"
            "Formatting: component names are set in monospace.\n"
            "Diagrams: figure captions are full sentences ending in a period."
        ),
    },
    {
        "chapter": "3. The scheduler",
        "notes": (
            "Terminology: render 'Rechner' as 'machine', because this chapter contrasts it with "
            "'Knoten' (node) and 'computer' would read oddly.\n"
            "Tone: algorithm descriptions use the imperative for steps.\n"
            "Formatting: pseudocode keywords in bold.\n"
            "Numbers: always use digits for step numbers."
        ),
    },
    {
        "chapter": "4. Memory management",
        "notes": (
            "Terminology: keep 'page fault' untranslated as a term of art; do not localise it.\n"
            "Tone: prefer the active voice; the original German passive constructions should be "
            "rewritten.\n"
            "Formatting: units are written with a non-breaking space before the unit symbol.\n"
            "Cross-references: refer to sections by number, not by name."
        ),
    },
    {
        "chapter": "5. The filesystem",
        "notes": (
            "Terminology: 'Verzeichnis' is 'directory', never 'folder', throughout.\n"
            "Tone: keep sentences short; this chapter is dense and the original is long-winded.\n"
            "Formatting: path names in monospace, with a leading slash.\n"
            "Lists: use bulleted lists where the original uses run-on enumerations."
        ),
    },
    {
        "chapter": "6. Networking",
        "notes": (
            "Terminology: render 'Rechner' as 'host' in networking contexts, following RFC usage.\n"
            "Tone: protocol descriptions use the present tense and name the actor explicitly.\n"
            "Formatting: protocol names in small caps.\n"
            "Numbers: byte counts always in digits with unit suffixes."
        ),
    },
    {
        "chapter": "7. Security",
        "notes": (
            "Terminology: keep 'capability' for 'Berechtigung' and reserve 'permission' for the "
            "POSIX mode bits; conflating them has caused real confusion.\n"
            "Tone: normative statements use 'must' and 'must not', never 'should'.\n"
            "Formatting: threat descriptions are set off in block quotes.\n"
            "Cross-references: refer to sections by number."
        ),
    },
    {
        "chapter": "8. Evaluation",
        "notes": (
            "Terminology: 'Rechner' is 'computer' here to match chapter 1, since the evaluation "
            "describes the same hardware the introduction promised.\n"
            "Tone: report results in the past tense and claims in the present.\n"
            "Formatting: table captions above the table, figure captions below.\n"
            "Numbers: three significant figures for all measurements."
        ),
    },
]

TASK = """
You hold one chapter's partial style guide for a book translation, and the job
is to reduce all eight partial guides into one guide that every translator can
follow.

**Step 1.** Run `ampi init --role "reviewer-{rank}"`.

**Step 2.** Your partial style guide is in the file `{notes_file}`. Read it.

**Step 3.** Take part in the reduction:

```
ampi reduce --root 0 --op AMPI_SYNTHESIZE --algo {algo} --datatype scalar --json-file {notes_file} --timeout 2400
```

Under this schedule most ranks are leaves: you send your contribution once and
the call returns immediately with `"status": "ok"` and a null payload. That is
success, not a failure --- only the ranks that sit above you in the reduction
tree are asked to evaluate the operator. If that is you, go to step 3a. If not,
go straight to step 4.

**Step 3a.** If your call printed `"status": "op_required"` instead:

The runtime has reached a step of the reduction that *you* have to evaluate:
it is handing you two partial style guides and asking for their merge.

* When this happens:
  - read the operands (they are in the JSON under `operands`, or in the file
    named by `operands_file` if they were too large to inline);
  - produce the merged style guide yourself. Merge means: keep every rule that
    both operands agree on; where they **conflict**, pick the better-justified
    rule and add a line starting with `RESOLVED:` that names the conflict and
    says why you chose as you did. Never silently drop a conflicting rule.
    Where two rules say the same thing in different words, state it once.
  - write the merged guide, as a JSON object of the same shape as the operands
    (an object with `chapter` and `notes` keys; set `chapter` to a
    comma-separated list of the chapters covered), to a file in your scratch
    directory;
  - run `ampi op-submit --op-token <the op_token you were given> --json-file <that file>`;
  - then run the **identical** `ampi reduce` command again to resume. Do not
    change any of its arguments.
  - Repeat until it prints `"status": "ok"`. You may be asked several times.

**Step 4.** Write your report to `{out_file}` as a JSON object with keys:
`{{"rank": {rank}, "algo": "{algo}", "upcalls": <how many times you were asked to
evaluate the operator, which may be 0>, "final": <the payload the reduce
returned, or null if it returned null because you are not the root>}}`.

**Step 5.** Run `ampi finalize --note "e2 {algo} done"`. Do **not** call
`ampi barrier` --- there is no barrier in this experiment, and calling one
would be a collective the other ranks are not making.

If you are asked to evaluate the operator, run `ampi hb --expect-idle 900`
before you start thinking about the merge. Over-estimating is free.

Do not edit any file outside your scratch directory and the one output file
named above. Do not run git.
"""


def prepare(root: str, algo: str, world_size: int = 8) -> dict:
    job_dir = os.path.join(os.path.abspath(root), f"job-{algo}")
    info = create_job(job_dir, world_size, ctx_limit=120_000,
                      meta={"experiment": "e2-semantic-allreduce", "algo": algo})
    tasks: dict[int, str] = {}
    for rank in range(world_size):
        scratch = os.path.join(job_dir, "ranks", str(rank))
        os.makedirs(scratch, exist_ok=True)
        notes_file = os.path.join(scratch, "notes.json")
        with open(notes_file, "w", encoding="utf-8") as fh:
            json.dump(CHAPTER_NOTES[rank % len(CHAPTER_NOTES)], fh, indent=2, ensure_ascii=False)
        out_file = os.path.join(scratch, "result.json")
        tasks[rank] = TASK.format(rank=rank, algo=algo, notes_file=notes_file,
                                  out_file=out_file)
    cards = write_rank_cards(job_dir, world_size, tasks,
                             {r: f"reviewer-{r}" for r in range(world_size)})
    return {**info, "algo": algo, "cards": cards}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.path.join(os.path.dirname(__file__), "runs"))
    parser.add_argument("--algos", default="linear,binomial")
    parser.add_argument("-n", type=int, default=8)
    args = parser.parse_args()
    out = [prepare(args.root, algo, args.n) for algo in args.algos.split(",")]
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
