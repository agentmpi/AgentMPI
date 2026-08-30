"""Experiment 1: book translation, an embarrassingly parallel task that is not.

Translating a book looks like the ideal data-parallel workload: cut it into *p*
sections, hand one to each agent, concatenate.  Every multi-agent framework can
express that, and the result is bad in a specific and instructive way.  The
sections disagree about how to render proper nouns, they drift in register, and
the seams between them are visible.  The dependence is weak but real, and it has
exactly the shape of a stencil computation: mostly local work plus a small amount
of information that must be globally consistent, plus a little that must agree
with immediate neighbours.

HPC has a standard decomposition for that shape, and this harness is it:

============================  =====================================================
phase                          AgentMPI operation
============================  =====================================================
decompose                      ``scatter`` (rendezvous: sections are large)
extract local terminology      agent call, contract-checked
agree on global terminology    ``allreduce`` with ``UNION`` -- exact, idempotent,
                               so every rank ends with the identical glossary
translate                      agent call with the agreed glossary
reconcile section boundaries   ``halo_exchange`` on a 1-D Cartesian topology
collect                        ``gather`` (rendezvous: the root must not admit
                               *pn* tokens into its context)
============================  =====================================================

The glossary step is an ``allreduce`` and not a broadcast from a coordinator, and
that choice is the substance.  A coordinator would have to read all *p* sections
to build the glossary, which reintroduces the serial bottleneck the
parallelisation was for; the allreduce lets every rank contribute what it alone
saw and receive the union, in ``2⌈log₂ p⌉`` rounds and without any rank reading
another's text.  ``UNION`` is chosen over a semantic merge deliberately: it is
exact, associative, commutative *and* idempotent, so the resulting glossary is
independent of the reduction tree and identical at every rank.  A semantic merge
would be none of those, and the population would end up believing *p* slightly
different glossaries -- which is the failure the ``--glossary-op semantic``
ablation measures.

Ablations
---------
``--no-glossary``   skip the allreduce: measures the cost of not being able to
                    share information between executors.
``--no-halo``       skip the neighbour exchange: measures boundary drift.
``--glossary-op``   ``union`` (exact) vs ``semantic`` (LLM merge): measures
                    whether a lossy operator makes the population diverge.
``--ranks 1``       the sequential baseline, for speedup.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agentmpi as ampi
from agentmpi.constants import BarrierPolicy, Mode
from common import (  # noqa: E402
    SHERLOCK_ENTITIES,
    Unit,
    load_book,
    make_executor_factory,
    provenance,
    score_translation,
    split_units,
    write_result,
)

# ---------------------------------------------------------------- contracts

TERMSHEET = ampi.Contract(
    name="TermSheet",
    kind="json",
    required=("terms", "register"),
    # `terms` is deliberately not in `nonempty`: a section with no proper nouns
    # has an empty term sheet, and that is a correct answer.
    nonempty=("register",),
    max_tokens=3000,
    semantics=(
        "terms maps each proper noun or recurring domain term found in this section, "
        "written exactly as it appears in the source, to the single target-language "
        "rendering you propose. register is one short sentence describing the "
        "narrative register and tense of this section."
    ),
)

TRANSLATION = ampi.Contract(
    name="Translation",
    kind="json",
    required=("translation", "renderings"),
    nonempty=("translation",),
    min_tokens=20,
    semantics=(
        "translation is the full target-language text of the section, preserving the "
        "source paragraph structure exactly (one target paragraph per source paragraph, "
        "blank line separated). renderings maps each canonical entity that occurs in "
        "this section to the exact target string you used for it in the translation."
    ),
)

REVISION = ampi.Contract(
    name="Revision",
    kind="json",
    required=("translation", "renderings", "changes"),
    nonempty=("translation",),
    semantics=(
        "translation is the revised full target-language text. renderings is as before. "
        "changes is a short list of the boundary or terminology fixes you made."
    ),
)


# ------------------------------------------------------------------- prompts


def prompt_terms(unit: Unit, target_lang: str, entities: list[str]) -> str:
    return f"""You are rank-local terminology extraction for a book translation into {target_lang}.

Read the source section below and produce a term sheet.

Rules:
- Include every proper noun (people, places, organisations, titles) that appears.
- Include any recurring domain term whose translation should be fixed across the book.
- For each, propose exactly ONE target-language rendering.
- These canonical entities matter most; include any that appear: {", ".join(entities)}
- Do NOT translate the section yet.

Return ONLY a JSON object:
{{"terms": {{"<source term>": "<{target_lang} rendering>", ...}}, "register": "<one sentence>"}}

--- SOURCE SECTION {unit.index} ({unit.n_words} words, {unit.n_paragraphs} paragraphs) ---
{unit.text}
--- END SECTION ---"""


def prompt_translate(unit: Unit, target_lang: str, glossary: dict[str, str], register: str, entities: list[str]) -> str:
    gloss_lines = "\n".join(f"  {k} => {v}" for k, v in sorted(glossary.items())[:400]) or "  (none provided)"
    return f"""Translate the source section below into {target_lang}.

BINDING GLOSSARY. Every listed source term MUST be rendered exactly as given. This
glossary was agreed by all sections of the book; deviating from it breaks the book.
{gloss_lines}

Shared register across the book: {register}

Hard requirements:
- Produce exactly {unit.n_paragraphs} target paragraphs, one per source paragraph,
  separated by a blank line, in the same order. Do not merge or split paragraphs.
- Translate everything. Do not summarise, omit, or add commentary.
- Report the exact target string you used for each canonical entity that occurs here.
  Canonical entities present in this section: {", ".join(unit.entities) or "(none)"}

Return ONLY a JSON object:
{{"translation": "<full {target_lang} text>", "renderings": {{"<entity>": "<exact target string used>"}}}}

--- SOURCE SECTION {unit.index} ({unit.n_words} words, {unit.n_paragraphs} paragraphs) ---
{unit.text}
--- END SECTION ---"""


def prompt_revise(
    unit: Unit,
    target_lang: str,
    my_translation: str,
    left_tail: str | None,
    right_head: str | None,
    glossary: dict[str, str],
) -> str:
    ctx = []
    if left_tail:
        ctx.append(f"END OF THE PRECEDING SECTION (section {unit.index - 1}):\n{left_tail}")
    if right_head:
        ctx.append(f"START OF THE FOLLOWING SECTION (section {unit.index + 1}):\n{right_head}")
    ctx_text = "\n\n".join(ctx) or "(this section is at a boundary of the book)"
    gloss_lines = "\n".join(f"  {k} => {v}" for k, v in sorted(glossary.items())[:400]) or "  (none)"
    return f"""You translated section {unit.index} of a book into {target_lang}. Your neighbours have now
sent you the text abutting yours. Revise ONLY what is needed for the seams to read as
one continuous work.

{ctx_text}

BINDING GLOSSARY (unchanged):
{gloss_lines}

Fix, and change nothing else:
- Terminology that disagrees with the glossary or with the abutting text.
- Register, tense, or person that jars against the neighbouring text.
- A first or last paragraph that reads as if it began or ended a document.

Keep exactly {unit.n_paragraphs} paragraphs.

Return ONLY a JSON object:
{{"translation": "<revised full text>", "renderings": {{"<entity>": "<exact target string used>"}}, "changes": ["<short description>", ...]}}

--- YOUR CURRENT TRANSLATION ---
{my_translation}
--- END ---"""


# ------------------------------------------------------------------- harness


def safe_agent(
    comm: ampi.Communicator,
    prompt: str,
    *,
    fallback: Any,
    failures: list[dict[str, Any]],
    **kw: Any,
) -> Any:
    """Invoke an agent, degrading to ``fallback`` instead of abandoning the group.

    This wrapper encodes the single most important discipline for writing a
    correct AgentMPI harness, and its absence is a bug the first version of this
    experiment had.  A rank whose agent call raises will, if the exception
    propagates, never reach the collective its peers are already blocked inside —
    turning one recoverable local failure into a whole-population hang.  MPI
    programs are written the other way round on purpose: a rank that cannot
    compute its contribution still enters the collective, contributing an
    identity element.

    So the rule is: **local failure must not remove a rank from a collective.**
    Contribute a degraded value, record the failure so the run's quality can be
    discounted honestly, and stay in the group.  Fault *escalation* is then a
    deliberate decision made by the barrier policy and the supervisor, not an
    accident of exception propagation.
    """
    try:
        return comm.agent(prompt, **kw)
    except ampi.AmpiError as exc:
        failures.append(
            {
                "label": kw.get("label", ""),
                "error_class": getattr(exc, "cls_name", type(exc).__name__),
                "message": str(exc)[:400],
            }
        )
        comm.fabric.emit(
            "harness.degraded",
            rank=comm.rt.wrank,
            ctx=comm.ctx,
            label=str(kw.get("label", "")),
            error_class=getattr(exc, "cls_name", type(exc).__name__),
        )
        return fallback


def build_harness(cfg: argparse.Namespace, units: list[Unit]) -> Any:
    """Return the SPMD ``rank_main`` for this configuration.

    Everything below is host-side, deterministic code.  The only nondeterminism is
    inside ``comm.agent``.  That separation is what lets the same harness run
    against real agents, against a simulator, and against a recorded replay
    without changing a line.
    """
    target_lang = cfg.target_lang
    entities = list(SHERLOCK_ENTITIES)

    def rank_main(comm: ampi.Communicator) -> Any:
        t_start = time.time()
        failures: list[dict[str, Any]] = []
        stats: dict[str, Any] = {"rank": comm.rank, "phases": {}}

        # ---- phase 0: decompose -------------------------------------------
        t0 = time.time()
        n_local = max(1, len(units) // comm.size)
        assignment = [
            [u.to_json() for u in units[i * n_local : (i + 1) * n_local]] for i in range(comm.size)
        ]
        # Any remainder goes to the last rank, which is where scatterv's variable
        # counts earn their keep: an equal-count split would idle p-1 ranks.
        leftover = [u.to_json() for u in units[comm.size * n_local :]]
        if leftover:
            assignment[-1].extend(leftover)
        mine_raw = comm.scatterv(assignment if comm.rank == 0 else None, root=0, label="decompose")
        mine = [Unit(**{k: (tuple(v) if k == "entities" else v) for k, v in d.items()}) for d in mine_raw]
        stats["phases"]["decompose"] = round(time.time() - t0, 3)
        stats["n_units"] = len(mine)

        # ---- phase 1: local terminology -----------------------------------
        t0 = time.time()
        local_terms: dict[str, str] = {}
        registers: list[str] = []
        if cfg.glossary:
            for u in mine:
                sheet = safe_agent(
                    comm,
                    prompt_terms(u, target_lang, entities),
                    fallback={"terms": {}, "register": ""},
                    failures=failures,
                    label=f"terms:u{u.index}",
                    contract=TERMSHEET,
                    retries=2,
                    max_tokens=1500,
                )
                for k, v in (sheet.get("terms") or {}).items():
                    if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                        local_terms.setdefault(k.strip(), v.strip())
                registers.append(str(sheet.get("register", "")))
        stats["phases"]["extract"] = round(time.time() - t0, 3)
        stats["n_local_terms"] = len(local_terms)

        # ---- phase 2: agree on the glossary -------------------------------
        t0 = time.time()
        glossary: dict[str, str] = {}
        register = ""
        if cfg.glossary:
            if cfg.glossary_op == "union":
                merged = comm.allreduce(local_terms, ampi.UNION, algorithm=cfg.allreduce_alg, label="glossary")
                # UNION keeps conflicting proposals as a list; resolve them by a
                # deterministic rule so that every rank resolves identically.
                # Determinism is the point: an agent resolving conflicts locally
                # would give p different glossaries from the same union.
                glossary = {
                    k: (sorted(v)[0] if isinstance(v, list) else v) for k, v in merged.items() if k and v
                }
                stats["n_conflicting_terms"] = sum(1 for v in merged.values() if isinstance(v, list))
            else:
                semantic = ampi.semantic_op(
                    "GLOSSARY_MERGE",
                    "Merge these two translation glossaries into one. Where they disagree on a term, "
                    "choose the better rendering. Return ONLY a JSON object mapping source term to "
                    "target rendering, at most {budget} tokens.\n\nA:\n{left}\n\nB:\n{right}",
                    output_tokens=1500,
                )
                glossary = comm.allreduce(
                    local_terms, semantic, algorithm=cfg.allreduce_alg, label="glossary-semantic"
                )
                if not isinstance(glossary, dict):
                    glossary = local_terms
            regs = comm.allreduce(registers, ampi.UNION, label="register")
            register = "; ".join(sorted({r for r in regs if r})[:3])
        stats["phases"]["glossary"] = round(time.time() - t0, 3)
        stats["n_glossary_terms"] = len(glossary)
        stats["glossary_digest"] = comm.fabric.blobs.put(glossary).digest[:12]

        # ---- phase 3: translate -------------------------------------------
        t0 = time.time()
        results: dict[int, dict[str, Any]] = {}
        for u in mine:
            out = safe_agent(
                comm,
                prompt_translate(u, target_lang, glossary, register, entities),
                fallback={"translation": "", "renderings": {}},
                failures=failures,
                label=f"translate:u{u.index}",
                contract=TRANSLATION,
                retries=2,
                max_tokens=int(u.n_words * 2.2) + 400,
            )
            results[u.index] = {
                "translation": str(out.get("translation", "")),
                "renderings": out.get("renderings") or {},
            }
        stats["phases"]["translate"] = round(time.time() - t0, 3)

        # ---- phase 4: halo exchange ---------------------------------------
        t0 = time.time()
        stats["n_boundary_changes"] = 0
        if cfg.halo and comm.size > 1:
            topo = ampi.cart_create(comm, dims=[comm.size], periods=[False])
            first_idx, last_idx = mine[0].index, mine[-1].index
            head = _first_paragraphs(results[first_idx]["translation"], 1)
            tail = _last_paragraphs(results[last_idx]["translation"], 1)
            from_left, from_right = ampi.halo_exchange(
                topo, left_boundary=head, right_boundary=tail, label="seams"
            )
            for u in mine:
                left_tail = from_left if u.index == first_idx else None
                right_head = from_right if u.index == last_idx else None
                if left_tail is None and right_head is None:
                    continue
                rev = safe_agent(
                    comm,
                    prompt_revise(
                        u, target_lang, results[u.index]["translation"], left_tail, right_head, glossary
                    ),
                    fallback=dict(results[u.index], changes=[]),
                    failures=failures,
                    label=f"revise:u{u.index}",
                    contract=REVISION,
                    retries=2,
                    max_tokens=int(u.n_words * 2.2) + 400,
                )
                results[u.index] = {
                    "translation": str(rev.get("translation", results[u.index]["translation"])),
                    "renderings": rev.get("renderings") or results[u.index]["renderings"],
                }
                stats["n_boundary_changes"] += len(rev.get("changes") or [])
        stats["phases"]["halo"] = round(time.time() - t0, 3)

        # ---- phase 5: collect ---------------------------------------------
        t0 = time.time()
        # Barrier with PROCEED, not RAISE: a section that never came back should
        # degrade the book, not lose it.  The absentees are reported so the caller
        # can see exactly which sections are missing.
        bres = comm.barrier(timeout=cfg.barrier_timeout, policy=BarrierPolicy.PROCEED, label="pre-gather")
        stats["barrier_absent"] = list(bres.absent)
        gathered = comm.gather(
            {"units": results}, root=0, mode=Mode.RENDEZVOUS, admit=False, label="collect"
        )
        stats["phases"]["collect"] = round(time.time() - t0, 3)
        stats["wall_s"] = round(time.time() - t_start, 3)
        stats["context"] = comm.rt.context.snapshot()
        stats["cost"] = comm.rt.cost.snapshot()
        stats["degraded"] = failures

        if comm.rank != 0:
            return stats
        assembled: dict[int, dict[str, Any]] = {}
        for chunk in gathered or []:
            if not chunk:
                continue
            for k, v in (chunk.get("units") or {}).items():
                assembled[int(k)] = v
        stats["assembled"] = assembled
        stats["glossary"] = glossary
        return stats

    return rank_main


def _first_paragraphs(text: str, n: int) -> str:
    parts = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "\n\n".join(parts[:n])


def _last_paragraphs(text: str, n: int) -> str:
    parts = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "\n\n".join(parts[-n:] if n <= len(parts) else parts)


# ----------------------------------------------------------------- synthetic


def synthetic_agent(prompt: str, **meta: Any) -> Any:
    """A deterministic stand-in for a translator, for protocol-level testing.

    It is not a translation and makes no pretence of being one.  Its purpose is to
    exercise every code path -- contracts, paragraph counts, rendering reports,
    halo revision -- so that the harness can be regression-tested without an
    agent, and so the *protocol* measurements (message counts, phase structure,
    context high-water marks) can be taken deterministically.
    """
    label = str(meta.get("label", ""))
    body = prompt.split("--- SOURCE SECTION", 1)[-1]
    if label.startswith("terms"):
        found = {e: f"[{e}]" for e in SHERLOCK_ENTITIES if e.lower() in body.lower()}
        return {"terms": found, "register": "third-person past narrative"}
    if label.startswith("translate") or label.startswith("revise"):
        m = re.search(r"exactly (\d+) target paragraphs", prompt) or re.search(r"(\d+) paragraphs", prompt)
        n_paras = int(m.group(1)) if m else 1
        gloss = dict(re.findall(r"^  (.+?) => (.+)$", prompt, re.M))
        ents = {k: v for k, v in gloss.items() if k in SHERLOCK_ENTITIES}
        paras = []
        for i in range(n_paras):
            tokens = [f"译文段{i}"]
            tokens.extend(ents.values())
            paras.append("".join(tokens) + "。" * 30)
        out: dict[str, Any] = {"translation": "\n\n".join(paras), "renderings": ents}
        if label.startswith("revise"):
            out["changes"] = ["aligned register with the preceding section"]
        return out
    if "glossar" in prompt.lower():
        return {}
    return {}


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AgentMPI experiment 1: parallel book translation")
    ap.add_argument("--ranks", type=int, default=8)
    ap.add_argument("--units", type=int, default=None, help="number of sections (default: one per rank)")
    ap.add_argument("--words-per-unit", type=int, default=700)
    ap.add_argument("--target-lang", default="Simplified Chinese")
    ap.add_argument("--executor", choices=["broker", "simulated", "function", "replay"], default="function")
    ap.add_argument("--root", default=None)
    ap.add_argument("--label", default="translation")
    ap.add_argument("--no-glossary", dest="glossary", action="store_false")
    ap.add_argument("--no-halo", dest="halo", action="store_false")
    ap.add_argument("--glossary-op", choices=["union", "semantic"], default="union")
    ap.add_argument("--allreduce-alg", default="reduce_bcast", choices=["reduce_bcast", "recursive_doubling"])
    ap.add_argument("--barrier-timeout", type=float, default=1800.0)
    ap.add_argument("--job-timeout", type=float, default=14400.0)
    ap.add_argument("--context-budget", type=int, default=120_000)
    ap.add_argument("--seed", type=int, default=0)
    ap.set_defaults(glossary=True, halo=True)
    cfg = ap.parse_args(argv)

    book = load_book()
    n_units = cfg.units or cfg.ranks
    units = split_units(book, n_units, max_words_per_unit=cfg.words_per_unit)
    if not units:
        raise SystemExit("no units produced from the source text")

    root = Path(cfg.root) if cfg.root else Path("runs") / f"{cfg.label}-p{cfg.ranks}"
    root.parent.mkdir(parents=True, exist_ok=True)
    fabric = ampi.create_job(root, cfg.ranks, label=cfg.label)
    fabric.set_meta("experiment", "translation")
    fabric.set_meta("config", json.dumps(vars(cfg)))

    factory = make_executor_factory(
        cfg.executor,
        fabric_root=root,
        seed=cfg.seed,
        fn=synthetic_agent if cfg.executor == "function" else None,
    )
    rank_main = build_harness(cfg, units)

    t0 = time.time()
    job = ampi.launch(
        rank_main,
        size=cfg.ranks,
        root=root,
        fabric=fabric,
        executor_factory=factory,
        context_budget=cfg.context_budget,
        strict_context=False,
        label=cfg.label,
        timeout=cfg.job_timeout,
    )
    wall = time.time() - t0

    head = job.value(0) or {}
    assembled = head.get("assembled") or {}
    outputs: list[dict[str, Any] | None] = [assembled.get(u.index) for u in units]
    metrics = score_translation(units, outputs)
    params = ampi.cost.calibrate(fabric)
    summary = ampi.cost.summarise(fabric)

    payload = {
        "provenance": provenance(experiment="translation"),
        "config": vars(cfg),
        "source": {
            "n_units": len(units),
            "total_words": sum(u.n_words for u in units),
            "total_paragraphs": sum(u.n_paragraphs for u in units),
            "entities": list(SHERLOCK_ENTITIES),
        },
        "job": job.totals() | {"wall_s": round(wall, 2)},
        "failed_ranks": job.failed_ranks,
        "rank_errors": {o.rank: o.error for o in job.outcomes if not o.ok},
        "quality": metrics.as_dict(),
        "calibration": params.as_dict(),
        "runtime_summary": summary.as_dict(),
        "per_rank_stats": {
            o.rank: {k: v for k, v in (o.value or {}).items() if k not in ("assembled", "glossary")}
            for o in job.outcomes
            if o.value
        },
        "glossary": head.get("glossary") or {},
        "fabric_root": str(root),
    }
    # The variant key must mention every dimension the ablation ladder varies, or
    # two configurations collide and the later silently overwrites the earlier. An
    # earlier version omitted the allreduce algorithm and lost a result that way.
    variant = "-".join(
        (
            f"p{cfg.ranks}",
            "gloss" if cfg.glossary else "nogloss",
            "halo" if cfg.halo else "nohalo",
            cfg.glossary_op,
            cfg.allreduce_alg,
            f"w{cfg.words_per_unit}",
            f"u{len(units)}",
        )
    )
    path = write_result(f"{cfg.label}-{variant}", payload, subdir="translation")

    (root / "output").mkdir(parents=True, exist_ok=True)
    for u in units:
        out = assembled.get(u.index)
        if out:
            (root / "output" / f"section-{u.index:03d}.txt").write_text(out["translation"], encoding="utf-8")

    print(json.dumps({"result": str(path), "quality": metrics.as_dict() | {"per_unit": "..."}, "job": payload["job"]}, indent=2, ensure_ascii=False))
    return 0 if job.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
