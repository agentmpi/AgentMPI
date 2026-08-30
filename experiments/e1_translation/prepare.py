#!/usr/bin/env python3
"""Experiment 1: parallel book translation.

Why this task
-------------
Translation is the agent equivalent of an embarrassingly parallel numerical
kernel: the sections can be worked on independently, so a naive harness scales
trivially. What makes it a *real* parallel programming problem is the same thing
that makes stencil codes real -- **the pieces are not actually independent**. Two
translators who never talk will render "Regularity" and "Chief Circle"
differently, and section 13 will open in a way that does not follow from how
section 12 ended. The dependencies are a global agreement (terminology) and a
nearest-neighbour agreement (boundaries), which are precisely the dependency
structures MPI exists to express: a reduction and a halo exchange.

So this experiment measures whether MPI's abstractions buy anything real when
the executors are agents:

* ``AMPI_Allreduce`` with an **agent-evaluated operator** to reconcile a glossary
  across all ranks (``ceil(log2 P)`` agent merges on the critical path);
* a **halo exchange** over a 1-D Cartesian topology for boundary continuity;
* handle-based ``AMPI_Gather`` so the coordinator can collect 22 translations
  without reading 22 translations.

Arms
----
``naive``
    Same 22 agents, same sections, no shared glossary, no boundary exchange, and
    a final gather that materialises every full translation into rank 0's
    context. This is what a harness written without the protocol's discipline
    does, and it is instrumented identically so the comparison is meaningful.
``ampi``
    The full protocol as described above.

Corpus
------
Edwin A. Abbott, *Flatland* (1884), Project Gutenberg #97: public domain, 22
numbered sections, and unusually dense in invented terminology that *must* be
translated consistently (Flatland, Lineland, Spaceland, Regularity, Chief
Circle, Colour Bill, ...). Sections are capped at ``--max-words`` so that every
rank has comparable work; the cap is recorded in the manifest.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import (  # noqa: E402
    PROTOCOL_DISCIPLINE,
    Rank,
    Section,
    create_job,
    launch_plan,
    split_sections,
    strip_gutenberg,
    write_spec,
)

#: The consistency probe set. These are terms whose rendering must agree across
#: sections for the translation to be usable, and which occur in more than one
#: section so that disagreement is possible. Chosen from Abbott's invented
#: vocabulary and social taxonomy, not from ordinary English, because ordinary
#: words have many acceptable renderings and would measure nothing.
PROBE_TERMS: List[str] = [
    "Flatland",
    "Lineland",
    "Spaceland",
    "Pointland",
    "Sphere",
    "Circle",
    "Square",
    "Equilateral",
    "Isosceles",
    "Polygon",
    "Regularity",
    "Irregular",
    "Configuration",
    "Dimension",
    "Priest",
    "Chief Circle",
    "Colour Bill",
    "Chromatic Sedition",
    "Recognition by Sight",
    "Feeling",
    "Monarch",
    "Straight Line",
    "Gospel of Three Dimensions",
    "Upward, not Northward",
]

LANG = "Simplified Chinese"

TARGET_NOTE = (
    "Render every probe term the same way everywhere. Keep paragraph breaks. Do not "
    "translate the section number. Do not add translator's notes or commentary. Do not "
    "summarise: translate the whole section."
)


def probes_in(text: str) -> List[str]:
    """Probe terms that actually occur in a section, case-insensitively."""
    low = text.lower()
    out = []
    for t in PROBE_TERMS:
        if t.lower() in low:
            out.append(t)
        elif t == "Straight Line" and "straight line" in low:
            out.append(t)
    return out


def cap_words(text: str, max_words: int) -> str:
    """Trim to a paragraph boundary at or below ``max_words``."""
    paras = text.split("\n\n")
    kept: List[str] = []
    total = 0
    for p in paras:
        n = len(p.split())
        if total and total + n > max_words:
            break
        kept.append(p)
        total += n
    return "\n\n".join(kept) if kept else " ".join(text.split()[:max_words])


# --------------------------------------------------------------------------
# Rank prompts
# --------------------------------------------------------------------------


def ampi_task(
    r: int, np: int, sec: Section, words: int, work: Path, secfile: Path,
    parts_json: Path, probes: List[str],
) -> str:
    dest = r + 1 if r + 1 < np else None
    src = r - 1 if r > 0 else None
    scatter_line = (
        f"ampi scatter --root 0 --label assign --parts @{parts_json} --timeout 30 --materialize"
        if r == 0
        else "ampi scatter --root 0 --label assign --timeout 30 --materialize"
    )
    probe_json = json.dumps(probes, ensure_ascii=False, indent=None)
    return f"""\
You translate **section {sec.number}** of Abbott's *Flatland* ("{sec.title}") into {LANG},
in cooperation with {np - 1} other ranks who are translating the other sections.

Your source text: `{secfile}`  (~{words} words)
Your working directory: `{work}`  (create it if needed)

The probe terms that appear in YOUR section are:
{probe_json}

{TARGET_NOTE}

Work through these phases **in order**. Do not skip a phase: other ranks are blocked
waiting for you in every collective.

---

**PHASE 0 — join the job**

```
ampi init
ampi info
```

**PHASE 1 — confirm your assignment (AMPI_Scatter)**

```
{scatter_line}
```
The payload restates your section number. If it disagrees with section {sec.number}, trust the
scatter and say so in your final report.

**PHASE 2 — propose terminology**

Read `{secfile}`. For each probe term listed above, decide the {LANG} rendering you think
everyone should use. Write exactly this JSON (English term -> single rendering, no nesting) to
`{work}/rank{r}_terms.json`:

```json
{{"Flatland": "...", "Sphere": "..."}}
```

Include **only** the probe terms listed above for your section. Then:
```
ampi memo put phase "terms proposed"
```

**PHASE 3 — agree ONE shared glossary with all {np} ranks (AMPI_Allreduce, agent operator)**

```
ampi allreduce --op agent:reconcile_glossary --label glossary \\
    --in @{work}/rank{r}_terms.json --algo reduce_bcast --timeout 30 --materialize
```

This is a reduction whose operator is *you*. The runtime will sometimes answer with
`action_required=merge` and name two operand files. When it does:

1. Read both operand files (they are JSON objects of the same shape).
2. Produce **one** merged JSON object of the same shape. Where the two disagree about a term,
   pick the single best rendering and use it — do not keep both, do not invent a nested
   structure, do not drop terms.
3. Write the merged object to the `suggested_out` path it gave you.
4. Run: `ampi reduce-commit --step <STEP> --in @<suggested_out> --timeout 30 --materialize`
5. You may immediately be handed another merge step. Keep going.

Stop when the output says `complete=true`. Its payload is the **AGREED GLOSSARY**. Save it to
`{work}/glossary.json` and use it in Phase 4. If a timeout interrupts you, re-run the same
`ampi allreduce` command — your accumulator is checkpointed and the reduction resumes.

```
ampi memo put phase "glossary agreed"
```

**PHASE 4 — translate**

Translate the whole of `{secfile}` into {LANG}. **Use the agreed glossary from Phase 3 for every
probe term** — that agreement is the entire purpose of Phase 3, and the experiment measures
whether you honoured it. Write your translation to `{work}/draft_{sec.number:02d}.md`, then publish:

```
ampi win create --name book
ampi win put --win book --key draft/{sec.number:02d} --in @{work}/draft_{sec.number:02d}.md --schema markdown
ampi memo put phase "draft published"
```

**PHASE 5 — boundary exchange with your neighbours (halo exchange)**

Discover your neighbours the way an MPI code does, then exchange:

```
ampi comm cart --dims {np}
ampi comm shift --comm world.cart --direction 0 --disp 1
```
It will report `source` (upstream) and `dest` (downstream). For you these should be
source={src if src is not None else "null"}, dest={dest if dest is not None else "null"}.

{"" if dest is None else f'''Send your ending downstream:
```
ampi send --to {dest} --tag halo --in "<the last two sentences of your translation>"
```
'''}{"You are the first section, so you have no upstream neighbour and nothing to receive." if src is None else f'''Receive your upstream neighbour's ending:
```
ampi recv --from {src} --tag halo --timeout 25 --materialize
```
Then **revise the opening** of your translation so that it reads continuously after that
ending: fix pronoun antecedents, connectives, and any terminology mismatch. Rewrite
`{work}/draft_{sec.number:02d}.md` and republish it:
```
ampi win put --win book --key draft/{sec.number:02d} --in @{work}/draft_{sec.number:02d}.md
```
(The republish is how the experiment detects that the exchange changed something, so do it
only if you actually revised the text.)'''}

```
ampi memo put phase "halo exchanged"
```

**PHASE 6 — report (AMPI_Gather)**

Write `{work}/rank{r}_report.json` with exactly these keys:

```json
{{"rank": {r}, "section": {sec.number}, "chinese_chars": 0,
  "terms_used": {{"Flatland": "..."}},
  "used_agreed_glossary": true, "revised_opening": true,
  "merge_steps": 0, "notes": "one sentence"}}
```

`terms_used` must record the rendering you **actually used in your final translation** for each
probe term in your section — report what you did, not what you intended. `merge_steps` is how
many `reduce-commit` calls you made in Phase 3. Then:

```
ampi win put --win book --key report/{sec.number:02d} --in @{work}/rank{r}_report.json
ampi gather --root 0 --label results --in @{work}/rank{r}_report.json --timeout 30
```

**PHASE 7 — finish**

```
ampi allreduce --op sum --label total-chars --in "<your chinese_chars as a bare number>" --timeout 30 --materialize
ampi barrier --label done --quorum 0.85 --timeout 25
ampi fini
```

Finally, reply with a short report: which phases completed, how many merge steps you performed,
how many times you had to retry a timed-out call, and anything about the protocol that was
confusing or that you had to work around.
"""


def naive_task(
    r: int, np: int, sec: Section, words: int, work: Path, secfile: Path, probes: List[str]
) -> str:
    probe_json = json.dumps(probes, ensure_ascii=False)
    return f"""\
You translate **section {sec.number}** of Abbott's *Flatland* ("{sec.title}") into {LANG}.
{np - 1} other agents are translating the other sections at the same time.

Your source text: `{secfile}`  (~{words} words)
Your working directory: `{work}`  (create it if needed)

The probe terms that appear in YOUR section are:
{probe_json}

{TARGET_NOTE}

---

**PHASE 0 — join**

```
ampi init
ampi info
```

**PHASE 1 — translate**

Translate the whole of `{secfile}` into {LANG}. Choose the {LANG} rendering for each probe term
yourself, using your own judgement. Write your translation to `{work}/draft_{sec.number:02d}.md`.

**PHASE 2 — report**

Write `{work}/rank{r}_report.json` with exactly these keys:

```json
{{"rank": {r}, "section": {sec.number}, "chinese_chars": 0,
  "terms_used": {{"Flatland": "..."}},
  "used_agreed_glossary": false, "revised_opening": false,
  "merge_steps": 0, "notes": "one sentence"}}
```

`terms_used` must record the rendering you actually used for each probe term in your section.

**PHASE 3 — submit your work to the coordinator**

```
ampi gather --root 0 --label collect --in @{work}/draft_{sec.number:02d}.md --timeout 40 --materialize
ampi gather --root 0 --label reports --in @{work}/rank{r}_report.json --timeout 40
ampi fini
```

Note the `--materialize` on the first gather: rank 0 collects the full text of every translation.
That is deliberate for this arm of the experiment.

Finally, reply with a short report: whether you completed, and how many retries you needed.
"""


COORDINATOR_NOTE = """\
You are also **rank 0**, the coordinator. In addition to your own section you own the
`--parts` argument to the Phase 1 scatter (already written into your commands below) and you
are the root of the gathers. When a gather returns a manifest rather than bodies, that is
correct and intended: do not try to read every contribution.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["ampi", "naive"], required=True)
    ap.add_argument("--source", default="/tmp/gb97.txt")
    ap.add_argument("--out", default=None)
    ap.add_argument("--np", type=int, default=22)
    ap.add_argument("--max-words", type=int, default=1100)
    args = ap.parse_args()

    raw = Path(args.source).read_text(encoding="utf-8", errors="replace")
    body = strip_gutenberg(raw)
    sections = split_sections(body)
    if len(sections) < args.np:
        raise SystemExit(f"found only {len(sections)} sections, need {args.np}")
    sections = sections[: args.np]

    out = Path(args.out or f"runs/e1_{args.arm}").resolve()
    if out.exists():
        shutil.rmtree(out)
    corpus = out / "corpus"
    work = out / "work"
    corpus.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    ranks: List[Rank] = []
    manifest_sections = []
    for r, sec in enumerate(sections):
        capped = cap_words(sec.body, args.max_words)
        secfile = corpus / f"section_{sec.number:02d}.txt"
        secfile.write_text(f"§ {sec.number} {sec.title}\n\n{capped}\n", encoding="utf-8")
        probes = probes_in(capped)
        manifest_sections.append(
            {"rank": r, "section": sec.number, "title": sec.title,
             "words": len(capped.split()), "probes": probes, "file": str(secfile)}
        )

    parts_json = corpus / "assignments.json"
    parts_json.write_text(
        json.dumps(
            [json.dumps({"rank": m["rank"], "section": m["section"], "title": m["title"],
                         "file": m["file"]}, ensure_ascii=False) for m in manifest_sections],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    for r, sec in enumerate(sections):
        m = manifest_sections[r]
        secfile = Path(m["file"])
        rank_work = work / f"rank{r:02d}"
        rank_work.mkdir(parents=True, exist_ok=True)
        if args.arm == "ampi":
            task = ampi_task(r, args.np, sec, m["words"], rank_work, secfile, parts_json,
                             m["probes"])
        else:
            task = naive_task(r, args.np, sec, m["words"], rank_work, secfile, m["probes"])
        if r == 0:
            task = COORDINATOR_NOTE + "\n" + task
        ranks.append(
            Rank(
                rank=r,
                role=("coordinator+translator" if r == 0 else "translator"),
                task=task,
                env={"SECTION": str(sec.number), "SOURCE_FILE": str(secfile),
                     "WORKDIR": str(rank_work)},
            )
        )

    preamble = f"""\
**Task**: translate Edwin A. Abbott's *Flatland* (1884) into {LANG}, section by section, using
{args.np} agent ranks in parallel. Section {{r+1}} belongs to rank r.

**Why this is not embarrassingly parallel**: the sections share invented terminology
(Flatland, Lineland, Regularity, Chief Circle, the Colour Bill, ...) and they must read
continuously across boundaries. A translation in which section 7 calls a Circle one thing and
section 8 calls it another is not usable, however fast it was produced.

**Arm**: `{args.arm}`.
{"This arm uses the full protocol: an agent-evaluated Allreduce to agree one glossary, a halo exchange for boundary continuity, and handle-based collection." if args.arm == "ampi" else "This arm deliberately omits the protocol's coordination: no shared glossary, no boundary exchange, and a final gather that reads every full translation into rank 0's context."}

{PROTOCOL_DISCIPLINE}
"""

    spec = write_spec(
        out,
        label=f"e1-translation-{args.arm}",
        preamble=preamble,
        ranks=ranks,
        config={
            "eager_tokens": 700,
            "ctx_budget": 120_000,
            "lease_ns": 2400 * 10 ** 9,
            "timeout_ns": 40 * 10 ** 9,
            "summary_tokens": 60,
        },
    )
    manifest = create_job(spec)
    plan = launch_plan(manifest, out / "launch_plan.json")
    (out / "experiment.json").write_text(
        json.dumps(
            {
                "experiment": "e1_translation",
                "arm": args.arm,
                "np": args.np,
                "target_language": LANG,
                "max_words_per_section": args.max_words,
                "corpus": {"title": "Flatland", "author": "Edwin A. Abbott",
                           "source": "Project Gutenberg #97", "license": "public domain"},
                "probe_terms": PROBE_TERMS,
                "sections": manifest_sections,
            },
            indent=2, ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"arm": args.arm, "job": manifest["job"], "root": str(out),
                      "world_size": manifest["world_size"], "launch_plan": str(plan)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
