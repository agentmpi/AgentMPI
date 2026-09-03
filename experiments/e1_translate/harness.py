"""E1: native translation of a book, written as an AgentMPI harness.

The task is chosen because it is *almost* embarrassingly parallel.  Each passage
translates independently; what does not is the rendering of the names and coined
terms that recur across passages.  Ignore the coupling and the Tin Woodman
acquires four different names; serialise to avoid it and you pay ``p`` executor
latencies for a task that is otherwise fully parallel.  That is the shape MPI's
collectives exist for.

The harness is written the recommended way: **the protocol is in the harness, not
in the prompt**.  Every AgentMPI call below is made by this file.  An executor's
entire obligation is to read a prompt file, write a result file, and submit, which
is why the same experiment runs against a deterministic function, a recorded
replay, or a hundred live agents without changing a line.

Five phases, and each maps onto one collective:

1. ``scatter``   the root hands each rank its passage.
2. *agent*        each rank proposes renderings for the recurring terms it sees.
3. ``allreduce``  those proposals are merged with ``union``, which lifts
                  disagreements rather than letting whichever branch merged last
                  decide them, and the root arbitrates each lifted conflict once.
4. ``bcast``      the arbitrated glossary goes to everyone, by handle.
5. *agent*        each rank translates its passage under the binding glossary.
6. ``gather``     the root collects a manifest and assembles the book.

The control arm (``--arm nogloss``) removes phases 2 to 4 and nothing else, so the
comparison isolates the collective rather than the prompt.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from ampi import Ampi
from ampi.core.payload import Contract
from ampitools.executor import BrokerExecutor, FunctionExecutor, Task, new_aid
from ampitools.harness import Harness

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RESULTS = HERE.parent / "results"
RUNS = HERE.parent.parent / "runs"

TERMSHEET_CONTRACT = {
    "kind": "json",
    "name": "termsheet",
    "required": ["rank", "renderings"],
    "expect": {"rank": "{rank}"},
    "max_tokens": 700,
    "semantics": "One proposed target-language rendering for each listed source term.",
}

TRANSLATION_CONTRACT = {
    "kind": "json",
    "name": "translation",
    "required": ["rank", "text"],
    "nonempty": ["text"],
    "expect": {"rank": "{rank}"},
    "semantics": "A faithful literary translation of the passage, obeying the glossary.",
}


def termsheet_prompt(passage: dict, language: str, rank: int) -> str:
    terms = passage["terms"]
    return f"""\
# AgentMPI rank {rank}: propose renderings

You are translating one passage of *The Wonderful Wizard of Oz* into **{language}**.
Before translating, the population agrees on how to render the names and coined
terms that recur across the whole book, so that every passage uses the same ones.

## Your passage (passage {passage["index"]} of the book)

{passage["text"]}

## The recurring terms that appear in your passage

{json.dumps(terms, ensure_ascii=False)}

## What to write

Write ONLY a JSON object, with no prose around it and no markdown fence:

{{"rank": {rank},
 "renderings": {{"<source term>": "<your proposed {language} rendering>", ...}}}}

Include every term in the list above and nothing else.  Propose the rendering you
would actually use in a published literary translation.  Do not explain.

Your peers are doing the same for their passages.  Where two of you propose
different renderings for one term, the runtime will surface the disagreement and
one rank will settle it; you do not need to guess what anyone else will say.
"""


def translate_prompt(passage: dict, language: str, rank: int, glossary: dict | None) -> str:
    gloss = ""
    if glossary:
        gloss = f"""\
## The binding glossary

These renderings were agreed by the whole population.  Use them exactly, every
time the source term appears.  Do not substitute your own preference.

{json.dumps(glossary, ensure_ascii=False, indent=1)}
"""
    return f"""\
# AgentMPI rank {rank}: translate passage {passage["index"]}

Translate the passage below into **{language}**.  Produce a faithful literary
translation: natural target-language prose, not a gloss, preserving paragraph
breaks and dialogue.

{gloss}
## The passage

{passage["text"]}

## What to write

Write ONLY a JSON object, with no prose around it and no markdown fence:

{{"rank": {rank}, "text": "<your {language} translation>"}}

Translate the whole passage.  Do not summarise, do not omit paragraphs, and do
not add a translator's note.
"""


def stub_executor(corpus: dict, language: str) -> FunctionExecutor:
    """A deterministic stand-in, for validating the harness without paying for agents.

    It is not a model of translation quality and is never reported as one.  It
    exists so that the protocol's behaviour can be regression-tested, and so that a
    hundred-rank harness can be debugged before a hundred agents are launched.  Its
    output is marked, so that no analysis can mistake it for agent output.
    """

    def fn(task: Task) -> Any:
        rank = task.rank
        passage = corpus["passages"][rank]
        if task.label.startswith("termsheet"):
            return {
                "rank": rank,
                "stub": True,
                # Deliberately rank-dependent for a third of the terms, so that the
                # glossary collective has real disagreements to lift.
                "renderings": {
                    t: f"[{language}:{t}]" if hash((t, rank % 3)) % 3 else f"[{language}:{t}:v{rank % 3}]"
                    for t in passage["terms"]
                },
            }
        return {"rank": rank, "stub": True, "text": f"[{language}] " + passage["text"][:400]}

    return FunctionExecutor(fn)


def build(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="E1: book translation as an AgentMPI harness")
    ap.add_argument("--name", required=True, help="run name; artifacts go to runs/<name>")
    ap.add_argument("--corpus", default=str(DATA / "oz.json"))
    ap.add_argument("--size", type=int, default=8, help="number of ranks (= passages)")
    ap.add_argument("--language", default="Spanish")
    ap.add_argument("--arm", default="glossary", choices=["glossary", "nogloss"])
    ap.add_argument("--executor", default="stub", choices=["stub", "broker"])
    ap.add_argument("--device", default="sqlite")
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--task-timeout", type=float, default=2400.0)
    ap.add_argument("--phase-timeout", type=float, default=3600.0)
    ap.add_argument("--quorum", type=float, default=1.0)
    ap.add_argument("--algorithm", default=None, help="override the glossary reduction schedule")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    a = build(argv)
    corpus = json.loads(Path(a.corpus).read_text(encoding="utf-8"))
    size = min(a.size, len(corpus["passages"]))
    run_dir = RUNS / a.name
    run_dir.mkdir(parents=True, exist_ok=True)
    campaign = a.campaign or a.name

    h = Harness(
        root=str(run_dir / "job"),
        size=size,
        device=a.device,
        force=True,
        meta={"experiment": "e1_translate", "arm": a.arm, "language": a.language,
              "corpus": corpus["title"], "executor": a.executor},
    )
    job = h.create()
    broker = BrokerExecutor(
        job, campaign=campaign, work_dir=run_dir / "broker", timeout_s=a.task_timeout
    )
    broker.open()
    executor = broker if a.executor == "broker" else stub_executor(corpus, a.language)

    # The launch plan is written before anything runs, so that the set of ranks the
    # experiment *intended* is recorded independently of the set that answered.
    plan = {
        "campaign": campaign,
        "job_root": str(run_dir / "job"),
        "size": size,
        "arm": a.arm,
        "language": a.language,
        "ranks": [
            {"rank": r, "passage": r, "terms": corpus["passages"][r]["terms"],
             "chars": corpus["passages"][r]["chars"]}
            for r in range(size)
        ],
    }
    (run_dir / "launch_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

    started = time.time()
    phase_times: dict[str, float] = {}

    def rank_main(amp: Ampi, rank: int) -> dict[str, Any]:
        t0 = time.time()
        # -- 1. scatter -----------------------------------------------------
        slices = (
            [{"rank": i, **corpus["passages"][i]} for i in range(size)] if rank == 0 else None
        )
        mine = amp.scatter(
            "assign", payload=slices, root=0, timeout=a.phase_timeout,
            contract={"kind": "json", "expect": {"rank": "{rank}"}},
        )["body"]
        amp.memo("phase", "received my passage")

        glossary: dict[str, Any] | None = None
        if a.arm == "glossary":
            # -- 2. agent: propose renderings --------------------------------
            amp.heartbeat(extend=a.task_timeout)
            proposal = executor.invoke(
                Task(
                    aid=new_aid(), rank=rank, label=f"termsheet-{rank}",
                    prompt=termsheet_prompt(mine, a.language, rank),
                    contract=Contract.parse(TERMSHEET_CONTRACT),
                )
            )
            amp.memo("phase", "proposed renderings")

            # -- 3. allreduce with conflict lifting ---------------------------
            merged = amp.allreduce(
                "glossary",
                payload=proposal.get("renderings", {}),
                op="union",
                algorithm=a.algorithm,
                quorum=a.quorum,
                timeout=a.phase_timeout,
            )
            # -- 4. the root arbitrates, once, and broadcasts -----------------
            if rank == 0:
                if merged.get("conflicts"):
                    settled = amp.op_arbitrate("glossary")["value"]
                else:
                    settled = merged["value"]
                amp.bcast("binding-glossary", payload=settled, root=0, timeout=a.phase_timeout)
                glossary = settled
            else:
                glossary = amp.bcast(
                    "binding-glossary", root=0, timeout=a.phase_timeout, materialize=True
                )["body"]
            amp.memo("phase", "glossary agreed")
            phase_times.setdefault("glossary_s", time.time() - t0)

        # -- 5. agent: translate ------------------------------------------
        amp.heartbeat(extend=a.task_timeout)
        out = executor.invoke(
            Task(
                aid=new_aid(), rank=rank, label=f"translate-{rank}",
                prompt=translate_prompt(mine, a.language, rank, glossary),
                contract=Contract.parse(TRANSLATION_CONTRACT),
            )
        )
        amp.memo("phase", "translated")

        # Each rank writes its own artifact.  The evidence for a scale claim has to
        # be per rank, not an aggregate somebody assembled afterwards.
        (run_dir / "out").mkdir(exist_ok=True)
        (run_dir / "out" / f"rank{rank}.json").write_text(
            json.dumps({"rank": rank, "passage": mine["index"], "arm": a.arm,
                        "glossary_used": bool(glossary), "result": out}, indent=2,
                       ensure_ascii=False),
            encoding="utf-8",
        )

        # -- 6. gather ------------------------------------------------------
        got = amp.gather(
            "assemble", payload={"rank": rank, "index": mine["index"]},
            root=0, quorum=a.quorum, timeout=a.phase_timeout,
        )
        return {"passage": mine["index"], "contributors": got.get("contributors")}

    results = h.run(rank_main, timeout=a.phase_timeout * 2)
    broker.close()
    report = h.report(results)
    report.update(
        experiment="e1_translate",
        arm=a.arm,
        language=a.language,
        executor=a.executor,
        corpus=corpus["title"],
        shared_terms=corpus["shared_terms"],
        wall_s=round(time.time() - started, 2),
        phase_times=phase_times,
        broker=broker.stats(),
        run_dir=str(run_dir),
    )
    out_path = run_dir / "report.json"
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    h.save(results, run_dir / "harness.json")
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"e1_{a.name}.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(
        {k: report[k] for k in ("succeeded", "failed", "wall_s", "context_total", "arm")},
        indent=2,
    ))
    return report


if __name__ == "__main__":
    main()
