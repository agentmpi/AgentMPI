"""E3: a production book-translation harness, written against AgentMPI.

This is the experiment the protocol was designed for, and it replaces a legacy
project whose ad-hoc harness failed in the way multi-agent harnesses usually do:
of sixteen agents launched, one ignored the coordination scheme and translated
the whole book alone, and the run was reported as a success because a book came
out of it.  That failure is not incidental --- it is what a coordination scheme
expressed as *instructions to agents* degrades into, because an instruction an
agent may decline is not a mechanism.  Here every coordination decision is made
by this file, and an agent's entire obligation is to turn a prompt into an
artifact.

The task is chosen because it is genuinely, unevenly parallel.  Rendering a
paragraph is independent work.  Three things are not:

*Terminology.*  A name, an institution, a period slang term must be rendered the
same way in every segment or the book reads as if four people wrote it --- which
is exactly what happens when parallel translators do not talk.  This is a
reduction: every rank contributes what it saw, the union is taken, and
disagreements are *lifted* rather than settled by whichever branch merged last.

*Research.*  Deciding what a 2013 Russian allusion carried is expensive external
work, and the same term appears in many segments.  Doing it once and sharing it
is a mutual-exclusion problem over shared state: a window, and a compare-and-swap
to claim an item.  Without it, sixty-four ranks research the same forty terms.

*Seams.*  Adjacent segments must join.  This looks sequential and is not: it is a
halo exchange, one parallel step plus a bounded-degree boundary exchange, which
is the most valuable non-obvious thing HPC has to offer this workload.

Nine phases, and each is a collective:

    0  bcast              the commission: brief, languages, conventions
    1  scatter            each rank receives its segment
    2  agent              survey: what must be rendered consistently
    3  allreduce(union)   the term census, with conflicts lifted
       op_arbitrate       the root settles each lifted conflict exactly once
       bcast              the research agenda
    4  win/claim + agent  research, each term claimed by exactly one rank
    5  win_fence          close the research epoch
       allreduce(union)   the binding glossary, conflicts lifted and arbitrated
       bcast              the glossary, by handle
    6  exscan             assembly offsets, without serialising the assembly
    7  agent              translate under the binding glossary
    8  neighbor_allgather seam exchange on a ring; agent revises its own edges
    9  gather             the manifest; the root assembles

Every phase is barrier-separated with a declared policy, so a missing executor
degrades the run rather than hanging it.

**On the corpus.**  The source is in copyright.  It is fetched at run time and
never written to a tracked path; what this harness commits is the protocol
evidence --- traces, metrics, the glossary the population researched --- and not
the book or a translation of it.  See ``DATA_POLICY.md``.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from ampi import Ampi
from ampi.core.payload import Contract
from ampi.executor import BrokerExecutor, FunctionExecutor, Task, new_aid
from ampi.harness import Harness

from . import corpus as corpus_mod
from .prompts import (
    RESEARCH_CONTRACT,
    SEAM_CONTRACT,
    SURVEY_CONTRACT,
    TRANSLATE_CONTRACT,
    research_prompt,
    seam_prompt,
    survey_prompt,
    translate_prompt,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "runs"
#: Untracked.  Fetched corpus, prompts, results and the assembled translation all
#: live here; only the evidence is promoted into ``runs/``.
WORK = ROOT / "work" / "e3"

RESEARCH_WIN = "research"
DEFAULT_LANGUAGES = ("en", "zh", "ja")


# ---------------------------------------------------------------------------
# The surrogate executor
# ---------------------------------------------------------------------------


def stub_executor(corpus: Any, languages: list[str]) -> FunctionExecutor:
    """A deterministic stand-in, for validating the harness without paying for agents.

    It models an executor's *protocol behaviour*, never its quality, and its
    output is marked so that no analysis can mistake it for agent output.  It
    exists so a sixty-four rank harness can be debugged before sixty-four agents
    are launched, which is the difference between one wasted afternoon and one
    wasted population.

    Its one deliberate piece of realism: ranks disagree about a third of their
    proposed renderings, so the reduction has real conflicts to lift.  A stub that
    agreed with itself would make the collective look free.
    """

    def fn(task: Task) -> Any:
        rank = task.rank
        label = task.label
        if label.startswith("survey"):
            seg = corpus.segments[rank]
            base = ["Дуров", "ВКонтакте", "Петербург", "хакер", "Сингер"]
            terms = [f"{t}" for t in base] + [f"term{rank}_{i}" for i in range(3)]
            return {
                "rank": rank,
                "stub": True,
                "terms": [
                    {
                        "term": t,
                        "kind": "person" if t == "Дуров" else "org",
                        "gloss": f"stub gloss for {t}",
                        "why_hard": "stub",
                        "needs_research": i < 4,
                        # A third of the population disagrees, on purpose.
                        "proposed": {c: f"[{c}:{t}:v{rank % 3 if i % 3 == 0 else 0}]"
                                     for c in languages},
                    }
                    for i, t in enumerate(terms)
                ],
                "pages": seg.first_page,
            }
        if label.startswith("research"):
            term = task.meta.get("term", "?")
            return {
                "term": term, "stub": True,
                "finding": f"stub finding for {term}",
                "sources": [], "register": "neutral",
                "rendering": {c: f"[{c}:{term}:agreed]" for c in languages},
                "rationale": "stub", "confidence": "low",
            }
        if label.startswith("translate"):
            seg = corpus.segments[rank]
            paras = [p for p in seg.text.split("\n\n") if p.strip()][:6]
            return {
                "rank": rank, "stub": True,
                "units": [
                    {"i": i, "ru": p[:120], **{c: f"[{c}] {p[:80]}" for c in languages}}
                    for i, p in enumerate(paras)
                ],
                "notes": [],
            }
        return {
            "rank": rank, "stub": True, "changed": False,
            "revised": {"head": {c: "[stub head]" for c in languages},
                        "tail": {c: "[stub tail]" for c in languages}},
            "reason": "stub",
        }

    return FunctionExecutor(fn)


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


def build_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="E3: book translation as an AgentMPI harness")
    ap.add_argument("--name", required=True, help="run name; evidence goes to runs/<name>")
    ap.add_argument("--size", type=int, default=16, help="ranks, one segment each")
    ap.add_argument("--languages", default=",".join(DEFAULT_LANGUAGES))
    ap.add_argument("--executor", default="stub", choices=["stub", "broker"])
    ap.add_argument("--device", default="sqlite")
    ap.add_argument("--campaign", default=None)
    ap.add_argument("--arm", default="full",
                    choices=["full", "noglossary", "noresearch", "noseams"],
                    help="ablate one mechanism, changing nothing else")
    ap.add_argument("--task-timeout", type=float, default=3600.0)
    ap.add_argument("--phase-timeout", type=float, default=7200.0)
    ap.add_argument("--quorum", type=float, default=1.0,
                    help="fraction of live ranks a collective waits for")
    ap.add_argument("--barrier-policy", default="proceed",
                    choices=["wait", "proceed", "shrink", "revoke"])
    ap.add_argument("--research-budget", type=int, default=3,
                    help="terms one rank will research before moving on")
    ap.add_argument("--research-cap", type=int, default=48,
                    help="size of the research agenda. A population that surveyed the "
                         "whole book nominates far more terms than a run can afford; the "
                         "cap keeps the agenda bounded and independent of p, which is what "
                         "makes shared research a saving rather than a cost")
    ap.add_argument("--algorithm", default=None, help="override the reduction schedule")
    ap.add_argument("--work-dir", default=str(WORK))
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> dict[str, Any]:
    a = build_args(argv)
    languages = [c.strip() for c in a.languages.split(",") if c.strip()]
    run_dir = RUNS / a.name
    work_dir = Path(a.work_dir) / a.name
    for d in (run_dir, work_dir):
        d.mkdir(parents=True, exist_ok=True)
    campaign = a.campaign or a.name

    corpus = corpus_mod.build(a.work_dir, a.size)
    corpus_mod.write_manifest(corpus, run_dir / "corpus_manifest.json")

    h = Harness(
        root=str(work_dir / "job"),
        size=a.size,
        device=a.device,
        force=True,
        meta={
            "experiment": "e3_book",
            "arm": a.arm,
            "languages": languages,
            "corpus": corpus.title,
            "executor": a.executor,
        },
    )
    job = h.create()
    broker = BrokerExecutor(
        job, campaign=campaign, work_dir=work_dir / "broker", timeout_s=a.task_timeout
    )
    broker.open()
    executor = broker if a.executor == "broker" else stub_executor(corpus, languages)

    # Written before anything runs, so the set of ranks the experiment *intended*
    # is recorded independently of the set that answered.  An aggregate assembled
    # afterwards from whatever came back cannot distinguish sixty-four executors
    # from one executor writing sixty-four entries.
    (run_dir / "launch_plan.json").write_text(
        json.dumps(
            {
                "campaign": campaign,
                "job_root": str(work_dir / "job"),
                "size": a.size,
                "arm": a.arm,
                "languages": languages,
                "executor": a.executor,
                "quorum": a.quorum,
                "barrier_policy": a.barrier_policy,
                "ranks": [s.metadata() for s in corpus.segments],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    started = time.time()
    out_dir = work_dir / "out"
    out_dir.mkdir(exist_ok=True)

    commission = {
        "book": corpus.title,
        "author": corpus.author,
        "languages": languages,
        "register": "reported non-fiction, close to its subjects, frequently wry",
        "rules": [
            "the binding glossary overrides local preference",
            "translate every paragraph; never summarise",
            "keep the source's paragraphing",
        ],
        "arm": a.arm,
    }

    def invoke(amp: Ampi, rank: int, label: str, prompt: str, contract: dict,
               meta: dict | None = None) -> Any:
        """One agent call, with the lease extended across it.

        The heartbeat matters more than it looks.  A research or translation turn
        can take many minutes, and a lease-based failure detector cannot tell a
        thinking executor from a dead one.  Extending the lease *before* the call
        is what stops the population convicting a rank that is working.
        """
        amp.heartbeat(extend=a.task_timeout)
        return executor.invoke(
            Task(aid=new_aid(), rank=rank, label=label, prompt=prompt,
                 contract=Contract.parse(contract), meta=meta or {})
        )

    def rank_main(amp: Ampi, rank: int) -> dict[str, Any]:
        report: dict[str, Any] = {"rank": rank, "arm": a.arm}

        # -- 0. bcast the commission ------------------------------------
        if rank == 0:
            amp.bcast("commission", payload=commission, root=0, timeout=a.phase_timeout)
        else:
            amp.bcast("commission", root=0, timeout=a.phase_timeout, materialize=True)

        # -- 1. scatter the segments ------------------------------------
        slices = (
            [{"rank": i, "text": s.text, **s.metadata()} for i, s in enumerate(corpus.segments)]
            if rank == 0
            else None
        )
        segment = amp.scatter(
            "segments", payload=slices, root=0, timeout=a.phase_timeout,
            contract={"kind": "json", "expect": {"rank": "{rank}"}},
        )["body"]
        report["pages"] = segment["pages"]

        # -- 2. agent: survey -------------------------------------------
        amp.memo("phase", "survey")
        survey = invoke(amp, rank, f"survey:r{rank}",
                        survey_prompt(segment, languages, rank), SURVEY_CONTRACT)
        terms = {t["term"]: t for t in survey.get("terms", []) if t.get("term")}
        report["terms_surveyed"] = len(terms)

        glossary: dict[str, Any] = {}
        agenda: list[dict[str, Any]] = []

        if a.arm != "noglossary":
            # -- 3. allreduce: the term census ---------------------------
            # `union` lifts a disagreement into a conflict set rather than letting
            # whichever branch merged last decide it.  That is the whole point: two
            # branches can meet the same disagreement and resolve it oppositely,
            # and no node is placed to notice, because each merge saw a locally
            # consistent pair.
            amp.memo("phase", "census")
            census = amp.allreduce(
                "census",
                payload={t: terms[t].get("proposed", {}) for t in terms},
                op="union", algorithm=a.algorithm,
                quorum=a.quorum, timeout=a.phase_timeout,
            )
            report["census_conflicts"] = len(census.get("conflicts") or [])

            if rank == 0:
                settled = (
                    amp.op_arbitrate("census")["value"]
                    if census.get("conflicts")
                    else census.get("value", {})
                )
                # The conflict set is taken *before* arbitration: once the root has
                # settled a disagreement the value looks unanimous, and the fact
                # that the population disagreed about it -- which is precisely what
                # makes it worth researching -- is no longer visible.
                need = _agenda_of(
                    terms, settled, census.get("conflicts") or {}, cap=a.research_cap
                )
                amp.bcast("agenda", payload=need, root=0, timeout=a.phase_timeout)
                agenda = need
            else:
                agenda = amp.bcast(
                    "agenda", root=0, timeout=a.phase_timeout, materialize=True
                )["body"]

            amp.barrier("agenda-ready", quorum=a.quorum, timeout=a.phase_timeout,
                        policy=a.barrier_policy)

            # -- 4. research under mutual exclusion ----------------------
            if a.arm != "noresearch":
                amp.memo("phase", "research")
                amp.win_create(RESEARCH_WIN)
                if rank == 0:
                    for item in agenda:
                        amp.put(RESEARCH_WIN, f"claim/{item['key']}", "unclaimed")
                amp.barrier("agenda-posted", quorum=a.quorum, timeout=a.phase_timeout,
                            policy=a.barrier_policy)

                done = 0
                for item in _my_order(agenda, rank, amp.size):
                    if done >= a.research_budget:
                        break
                    # Compare-and-swap, not a lock: a claim taken by an executor
                    # whose session then ends must not wedge the term forever, and
                    # unlike a lock a swapped cell cannot be held by a dead rank.
                    if not amp.claim(RESEARCH_WIN, f"claim/{item['key']}")["claimed"]:
                        continue
                    finding = invoke(
                        amp, rank, f"research:{item['key']}",
                        research_prompt(item, languages, rank), RESEARCH_CONTRACT,
                        meta={"term": item["term"]},
                    )
                    amp.put(RESEARCH_WIN, f"finding/{item['key']}", finding)
                    done += 1
                report["researched"] = done

                # -- 5. close the epoch --------------------------------
                # A window fence rather than a bare barrier: it is a barrier *plus*
                # the guarantee that the epoch's writes are visible, which is what
                # turns a blackboard into a sequence of supersteps.
                amp.win_fence(RESEARCH_WIN, "research-done", timeout=a.phase_timeout,
                              quorum=a.quorum)
                mine = _read_findings(amp, rank)
            else:
                mine = {t: terms[t].get("proposed", {}) for t in terms}

            # -- 5b. allreduce: the binding glossary ---------------------
            amp.memo("phase", "glossary")
            merged = amp.allreduce(
                "glossary", payload=mine, op="union", algorithm=a.algorithm,
                quorum=a.quorum, timeout=a.phase_timeout,
            )
            report["glossary_conflicts"] = len(merged.get("conflicts") or [])
            if rank == 0:
                settled = (
                    amp.op_arbitrate("glossary")["value"]
                    if merged.get("conflicts")
                    else merged.get("value", {})
                )
                amp.bcast("binding-glossary", payload=settled, root=0, timeout=a.phase_timeout)
                glossary = settled
            else:
                glossary = amp.bcast(
                    "binding-glossary", root=0, timeout=a.phase_timeout, materialize=True
                )["body"]

        # -- 6. exscan: assembly offsets --------------------------------
        # The assembly needs each segment's running offset.  Computing it by
        # walking the segments in order would serialise the one part of the job
        # that has no reason to be serial; a prefix reduction gives every rank its
        # offset in log p rounds.
        offset = amp.exscan("offsets", payload=segment["tokens"], op="sum",
                            quorum=a.quorum, timeout=a.phase_timeout)
        report["token_offset"] = offset.get("value", 0)

        amp.barrier("ready-to-translate", quorum=a.quorum, timeout=a.phase_timeout,
                    policy=a.barrier_policy)

        # -- 7. agent: translate ----------------------------------------
        amp.memo("phase", "translate")
        translation = invoke(
            amp, rank, f"translate:r{rank}",
            translate_prompt(segment, languages, rank, glossary or None,
                             max(1, segment["chars"] // 700)),
            TRANSLATE_CONTRACT,
        )
        units = translation.get("units", [])
        report["units"] = len(units)

        # -- 8. halo exchange: reconcile the seams ----------------------
        if a.arm != "noseams" and amp.size > 1:
            amp.memo("phase", "seams")
            ring = amp.cart_create([amp.size], periodic=[True], name="ring")["name"]
            edges = _edges_of(units, languages)
            halo = amp.neighbor_allgather(
                "seams", payload={"rank": rank, **edges}, comm=ring,
                timeout=a.phase_timeout, materialize=True,
            )
            neighbours = [
                n["body"] for n in halo.get("neighbours", [])
                if n.get("body") and n["body"].get("rank") != rank
            ]
            if neighbours:
                seam = invoke(amp, rank, f"seam:r{rank}",
                              seam_prompt(rank, edges, neighbours, languages), SEAM_CONTRACT)
                report["seam_changed"] = bool(seam.get("changed"))
                units = _apply_seam(units, seam, languages)

        # Each rank writes its own artifact.  Evidence for a scale claim has to be
        # per rank, not an aggregate somebody assembled afterwards.
        (out_dir / f"rank{rank}.json").write_text(
            json.dumps({"rank": rank, "segment": segment["index"], "pages": segment["pages"],
                        "arm": a.arm, "glossary_terms": len(glossary), "units": units,
                        "notes": translation.get("notes", [])},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # -- 9. gather the manifest -------------------------------------
        # A manifest, not a concatenation.  At p=64 with segment-sized
        # contributions an inlining gather would charge the root a six-figure
        # token bill for something it only needs to index.
        got = amp.gather(
            "assemble",
            payload={"rank": rank, "segment": segment["index"], "pages": segment["pages"],
                     "units": len(units), "offset": report["token_offset"]},
            root=0, quorum=a.quorum, timeout=a.phase_timeout,
        )
        amp.memo("phase", "done")
        report["contributors"] = got.get("contributors")
        return report

    results = h.run(rank_main, timeout=a.phase_timeout * 2)
    broker.close()

    report = h.report(results)
    report.update(
        experiment="e3_book",
        arm=a.arm,
        size=a.size,
        languages=languages,
        executor=a.executor,
        corpus={k: v for k, v in corpus.metadata().items() if k != "segments"},
        wall_s=round(time.time() - started, 2),
        broker=broker.stats(),
        run_dir=str(run_dir),
        work_dir=str(work_dir),
    )
    (run_dir / "report.json").write_text(
        json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
    )
    h.save(results, run_dir / "harness.json")
    _promote_glossary(work_dir, run_dir, h)

    print(json.dumps(
        {k: report[k] for k in ("succeeded", "failed", "wall_s", "context_total", "arm", "size")},
        indent=2,
    ))
    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agenda_of(
    terms: dict,
    settled: Any,
    conflicts: dict[str, Any] | None = None,
    *,
    cap: int = 0,
) -> list[dict[str, Any]]:
    """Turn the arbitrated census into a research agenda, contested terms first.

    Keyed by a slug rather than by the term itself, because the term is Cyrillic
    and the key becomes a window cell name that appears in traces, filenames and
    error messages, all of which are read by people and some by shells.

    The ordering is the interesting part.  A population that surveyed the whole
    book nominates far more terms than a run can afford to research, so the
    agenda has to be prioritised, and the reduction has already computed the right
    priority for free: a term in the *conflict set* is one that two ranks, reading
    different parts of the book, proposed to render differently.  That is exactly
    the term where inconsistency would be visible to a reader, and exactly the
    term a single translator would never have noticed was contentious.  Terms
    nobody disagreed about are cheaper to leave to the first-pass gloss.

    So the conflict set is not merely reported here, it is *used*: it aims the
    expensive external research at the places the population has demonstrated it
    is not already in agreement.
    """
    contested = set(conflicts or {})
    agenda: list[dict[str, Any]] = []
    seen: set[str] = set()
    proposals = settled if isinstance(settled, dict) else {}
    ordered = sorted(
        (t for t, m in terms.items() if m.get("needs_research")),
        key=lambda t: (t not in contested, t),
    )
    for term in ordered:
        meta = terms[term]
        key = _slug(term)
        if key in seen:
            continue
        seen.add(key)
        agenda.append({
            "key": key,
            "term": term,
            "kind": meta.get("kind", ""),
            "gloss": meta.get("gloss", ""),
            "why_hard": meta.get("why_hard", ""),
            "contested": term in contested,
            "proposed": proposals.get(term, meta.get("proposed", {})),
        })
        if cap and len(agenda) >= cap:
            break
    return agenda


def _slug(term: str) -> str:
    import hashlib
    import re

    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", term).strip("-").lower()[:24]
    return f"{ascii_part or 'term'}-{hashlib.sha1(term.encode('utf-8')).hexdigest()[:8]}"


def _my_order(agenda: list[dict[str, Any]], rank: int, size: int) -> list[dict[str, Any]]:
    """Rotate the agenda per rank so ranks do not all contend on the same first item.

    Every rank scanning in the same order makes the claim loop a thundering herd:
    p ranks compare-and-swap the same cell, one wins, and the rest have burned a
    device round trip each to learn they lost.  Rotating by rank makes the common
    case an uncontended claim.
    """
    if not agenda:
        return []
    start = (rank * max(1, len(agenda) // max(1, size))) % len(agenda)
    return agenda[start:] + agenda[:start]


def _read_findings(amp: Ampi, rank: int) -> dict[str, Any]:
    """Read the whole research window, as a glossary contribution.

    Every rank reads every finding, which is the point: research done once is
    visible to all, and a rank contributes the union to the reduction so a finding
    survives even if the rank that produced it dies before the glossary is agreed.
    """
    out: dict[str, Any] = {}
    for item in amp.win_ls(RESEARCH_WIN, prefix="finding/")["items"]:
        cell = amp.get(RESEARCH_WIN, item["key"])
        if not cell.get("present"):
            continue
        body = cell.get("value") or cell.get("body") or {}
        if isinstance(body, dict) and body.get("term"):
            out[body["term"]] = body.get("rendering", {})
    return out


def _edges_of(units: list[dict], languages: list[str]) -> dict[str, Any]:
    def edge(u: dict | None) -> dict[str, str]:
        return {c: str((u or {}).get(c, "")) for c in languages}

    return {"head": edge(units[0] if units else None),
            "tail": edge(units[-1] if units else None)}


def _apply_seam(units: list[dict], seam: dict, languages: list[str]) -> list[dict]:
    revised = seam.get("revised") or {}
    if not units or not seam.get("changed"):
        return units
    for slot, index in (("head", 0), ("tail", len(units) - 1)):
        block = revised.get(slot) or {}
        for c in languages:
            if block.get(c):
                units[index][c] = block[c]
    return units


def _promote_glossary(work_dir: Path, run_dir: Path, h: Harness) -> None:
    """Promote the researched glossary into the committed evidence.

    This is the one piece of *content* the run commits, and it is committed
    deliberately: a term list with findings and rationales is the population's own
    scholarly output --- a reference work about the book --- not a substitute for
    the book or a derivative of its prose.  The translation itself stays in the
    untracked working directory.
    """
    try:
        amp = h.attach(0)
    except Exception:  # pragma: no cover - a job whose root was removed
        return
    try:
        findings = {}
        for item in amp.win_ls(RESEARCH_WIN, prefix="finding/")["items"]:
            cell = amp.get(RESEARCH_WIN, item["key"])
            body = cell.get("value") or cell.get("body")
            if isinstance(body, dict):
                findings[body.get("term", item["key"])] = body
        if findings:
            (run_dir / "glossary.json").write_text(
                json.dumps(findings, indent=2, ensure_ascii=False), encoding="utf-8"
            )
    except Exception:  # pragma: no cover - the window may not exist in an ablation
        pass
    finally:
        amp.close()


if __name__ == "__main__":
    main()
