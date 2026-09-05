"""E7: the production book translation, on raw-API ranks, from one node to many.

E3 wrote the protocol for this task and then could not staff it: its executors
were agent-host sessions, the host capped them at ten, and every production run
above p=16 spent its wall time waiting for an executor to exist.  E7 keeps E3's
design --- the same nine collectives, for the same reasons --- and changes what a
rank *is*: an operating-system process whose executor is a chat-completions call
with a small tool loop (:mod:`ampitools.model`), launched by ``ampirun``
(:mod:`ampitools.launcher`) on one machine or across several.  Executor supply stops
being the experiment's limiting resource, so for the first time the population can
be scaled --- 16, 32, 64 on one node; 128 and 256 over several --- and the
protocol's own cost measured against a fixed workload.

What is different from E3, and why:

* **The unit is the paragraph**, not the page, so p can exceed the page count.
* **Arbitration is agent-evaluated and parallel.**  After an ``allreduce`` every
  rank holds the same lifted conflict set; rank ``r`` arbitrates batch ``r``, the
  rulings are gathered to the root, and the root commits them with
  ``op_arbitrate`` exactly once.  The serial step that would otherwise grow with
  the population is spread over it.
* **Agent results are checkpointed** in a window before the rank moves on, and
  every phase re-entered by a restarted process finds its collective closed and
  its result stored.  With ``--die-fraction`` a fraction of ranks exit mid-phase;
  ``ampirun --respawn`` restarts them; the runtime hands the successor a new epoch
  and the harness resumes.  That is the lifecycle failure every multi-agent
  postmortem reports, made a measured quantity.
* **A window lock guards the amendment ledger.**  A translator who settles a term
  the glossary did not cover records it under an exclusive, leased, fenced lock
  per chapter, first writer wins; contention and stale-token rejections are in
  the trace.
* **The population's spend is an allreduce.**  Every rank learns the running cost
  of the whole job; the number is in the trace and in the report.

Phases (each a collective, each memoised for restart):

    0  bcast              the commission
    1  scatter            each rank's segment, self-identifying
    2  agent              survey
    3  allreduce(union)   term census, conflicts lifted
       agent (parallel)   arbitrate one batch of conflicts per rank
       gather             rulings to the root; op_arbitrate once; bcast agenda
    4  win/claim + agent  research, each term claimed by exactly one rank
    5  win_fence          close the research epoch
       allreduce(union)   binding glossary; parallel arbitration again; bcast
    6  exscan             paragraph offsets; barrier
    7  agent              translate; win_lock amendments; put the draft
    8  cart + neighbor_allgather + agent   seams
    9  allreduce(sum)     spend; gather manifest; root assembles; finalize

The source is in copyright.  It is read from a local checkout or fetched at run
time into an untracked working directory; the repository carries the protocol
evidence, the population's glossary, and a manifest of what each rank was given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ampi import Ampi
from ampi.core.ops import arbitrate as default_arbitrate
from ampi.core.payload import Contract
from ampi.errors import AmpiError
from ampitools.executor import FunctionExecutor, Task, new_aid
from ampitools.harness import Harness
from ampitools.launcher import EXIT_EXECUTOR_DIED

from . import corpus as corpus_mod
from .prompts import (
    ARBITRATE_CONTRACT,
    RESEARCH_CONTRACT,
    SEAM_CONTRACT,
    SURVEY_CONTRACT,
    SYSTEM,
    TRANSLATE_CONTRACT,
    arbitrate_prompt,
    research_prompt,
    seam_prompt,
    survey_prompt,
    translate_prompt,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "runs"
WORK = ROOT / "work" / "e7"

RESEARCH_WIN = "research"
BOOK_WIN = "book"
MEMO_WIN = "results"
AMEND_WIN = "amendments"
DEFAULT_LANGUAGES = ("en", "zh", "ja")
ARBITRATION_BATCH = 24
GLOSSARY_CAP = 160
#: Source tokens per translation call.  A rank's segment at p=16 is ~9,000 tokens,
#: whose rendering into three languages is ~27,000 output tokens: past what many
#: providers allow in one completion and, at ~30 tokens a second, a quarter of an
#: hour in which a lost connection loses everything.  Chunking bounds both, and
#: each chunk is checkpointed, so a restarted rank resumes at the chunk it lost.
#: Within a rank the chunks are a prefix computation --- each sees the previous
#: chunk's last rendering --- which is the sequential dependence translation
#: really has, kept inside the rank where it costs nothing to honour.
TRANSLATE_CHUNK_TOKENS = 1100


# ---------------------------------------------------------------------------
# Configuration: written once by the driver, read by every rank process
# ---------------------------------------------------------------------------


@dataclass
class Config:
    name: str
    size: int
    languages: list[str] = field(default_factory=lambda: list(DEFAULT_LANGUAGES))
    executor: str = "stub"
    model: str = "moonshotai/kimi-k3"
    research_model: str = ""
    arbiter_model: str = ""
    reasoning: str = "low"
    fallback_model: str = "deepseek/deepseek-v4-pro-0813"
    tools: bool = True
    web: bool = False
    device: str = "sqlite"
    arm: str = "full"
    task_timeout: float = 1800.0
    phase_timeout: float = 7200.0
    quorum: float = 1.0
    barrier_policy: str = "proceed"
    research_budget: int = 3
    research_cap: int = 48
    algorithm: str | None = None
    die_fraction: float = 0.0
    die_phase: str = "translate"
    respawn: int = 0
    ctx_budget: int = 200000
    lease_s: float = 900.0
    max_attempts: int = 3
    first_page: int = corpus_mod.FIRST_PROSE_PAGE
    last_page: int | None = None
    nodes: int = 1
    remote: str = ""
    branch: str = ""
    run_dir: str = ""
    work_dir: str = ""

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


def stub_executor(languages: list[str]) -> FunctionExecutor:
    """A deterministic stand-in that models protocol behaviour, never quality.

    Ranks disagree about a third of their proposals on purpose, so the reductions
    have real conflicts to lift and the arbitration step has real work.
    """

    def fn(task: Task) -> Any:
        rank, label = task.rank, task.label
        seg = task.meta.get("segment") or {}
        if label.startswith("survey"):
            base = ["Дуров", "ВКонтакте", "Петербург", "гопник", "Дом Зингера"]
            terms = base + [f"term{rank}_{i}" for i in range(2)]
            return {"rank": rank, "stub": True, "terms": [
                {"term": t, "kind": "org", "gloss": f"stub gloss for {t}", "why_hard": "stub",
                 "needs_research": i < 4,
                 "proposed": {c: f"[{c}:{t}:v{rank % 3 if i % 3 == 0 else 0}]" for c in languages}}
                for i, t in enumerate(terms)]}
        if label.startswith("research"):
            term = task.meta.get("term", "?")
            return {"term": term, "stub": True, "finding": f"stub finding for {term}",
                    "sources": [], "register": "neutral",
                    "rendering": {c: f"[{c}:{term}:researched]" for c in languages},
                    "rationale": "stub", "confidence": "low"}
        if label.startswith("arbitrate"):
            conflicts = task.meta.get("conflicts") or {}
            return {"rulings": {k: (v[0] if isinstance(v[0], dict) else
                                    {c: str(v[0]) for c in languages})
                                for k, v in conflicts.items()}, "notes": ["stub"]}
        if label.startswith("translate"):
            units = seg.get("units", [])
            return {"rank": rank, "stub": True,
                    "units": [{"i": u["i"], **{c: f"[{c}] {u['ru'][:60]}" for c in languages}}
                              for u in units],
                    "new_terms": {f"new{rank % 2}": {c: f"[{c}:new]" for c in languages}},
                    "notes": []}
        return {"rank": rank, "stub": True, "changed": rank % 2 == 0,
                "revised": {"head": {c: "[stub head]" for c in languages},
                            "tail": {c: "[stub tail]" for c in languages}},
                "reason": "stub"}

    return FunctionExecutor(fn)


def model_for_rank(spec: str, rank: int) -> str:
    """The model a rank uses: one name, or one of a comma-separated pool by rank.

    The pool exists because of a provider policy, not a research design: on the
    account these runs used, every capable model was limited to twenty requests a
    minute *per model*, so a population of sixty-four ranks on one model spends
    its time queueing on the limit rather than translating.  Spreading the ranks
    over a pool multiplies the aggregate limit by the pool's size.  It also makes
    the population heterogeneous --- and the protocol, which never asks what an
    executor is, does not notice, which is a claim this experiment now tests.
    Rank ``r`` uses ``pool[r % len(pool)]`` at every scale, so the mix is the same
    across the series and a segment's model is a function of its rank alone.
    """
    pool = [m.strip() for m in spec.split(",") if m.strip()]
    if not pool:
        raise ValueError("no model given")
    return pool[rank % len(pool)]


def model_executor(amp: Ampi, cfg: Config, log_dir: Path) -> Any:
    from ampitools.model import ChatModel, ModelExecutor
    from ampitools.tools import research_tools

    reasoning = ({"enabled": False} if cfg.reasoning in ("none", "off")
                 else {"effort": cfg.reasoning})

    def make(name: str, *, plugins: list[dict] | None = None) -> ChatModel:
        return ChatModel(name, reasoning=reasoning, plugins=plugins, timeout_s=cfg.task_timeout,
                         rate_limit_patience_s=cfg.task_timeout)

    rank = amp.rank
    mine = model_for_rank(cfg.model, rank)
    main = make(mine)
    research = make(model_for_rank(cfg.research_model, rank) if cfg.research_model else mine,
                    plugins=[{"id": "web", "max_results": 5}] if cfg.web else None)
    arbiter = make(model_for_rank(cfg.arbiter_model, rank) if cfg.arbiter_model else mine)
    fallback = make(cfg.fallback_model) if cfg.fallback_model else None
    tools = research_tools() if cfg.tools else []
    return ModelExecutor(
        amp, main, system=SYSTEM, max_attempts=cfg.max_attempts, log_dir=log_dir,
        models={"research": research, "arbitrate": arbiter}, fallback=fallback,
        tools_for=lambda task: tools if task.label.startswith("research") else [],
        max_steps=5, max_prompt_tokens=40_000,
    )


# ---------------------------------------------------------------------------
# The rank program
# ---------------------------------------------------------------------------


def rank_main(amp: Ampi, rank: int, cfg: Config, segments: list[dict[str, Any]],
              executor: Any, *, out_dir: Path) -> dict[str, Any]:
    languages = cfg.languages
    size = amp.size
    report: dict[str, Any] = {"rank": rank, "arm": cfg.arm}
    info = amp.init(role="translator", lease_s=cfg.lease_s)
    epoch = int(info.get("epoch", 1))
    report["epoch"] = epoch
    if epoch > 1:
        report["recovered"] = True
    # A process rank composes every prompt from files: its executor's context is
    # empty at the start of every call.  The ledger is released accordingly ---
    # here, so a successor does not inherit its predecessor's spend, and after
    # every task.  Without this a respawned rank that replayed its broadcasts
    # exhausted the ledger it had inherited, its glossary broadcast came back
    # degraded to a summary string, and the rank crashed on it.
    amp.ctx_release()
    my_segment = segments[rank]
    # Per rank: two ranks receiving the same broadcast at the same instant must
    # not share a file, or one of them reads the other's half-written copy.  A
    # rank crashed on exactly that before the directory was per rank.
    bodies = out_dir / "bodies" / f"r{rank}"
    bodies.mkdir(parents=True, exist_ok=True)

    def take(res: dict[str, Any], label: str) -> Any:
        """A broadcast or window body, read from the file the runtime wrote.

        ``out=`` charges nothing: what enters a model's context is decided when
        the prompt is composed, not when the harness reads shared state.
        """
        path = res.get("saved_to")
        if not path:
            return res.get("body", res.get("value"))
        text = Path(path).read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{label}: the delivered body is not JSON ({exc}); a body with an elision "
                "marker was degraded by a ledger somewhere upstream") from exc

    def bcast_in(label: str) -> Any:
        return take(amp.bcast(label, root=0, timeout=cfg.phase_timeout,
                              out=str(bodies / f"{label}.json")), label)

    def phase(name: str) -> None:
        amp.memo("phase", name)

    def maybe_die(where: str) -> None:
        """Fault injection: a fraction of ranks lose their executor once.

        Only on the first epoch, so a respawned rank does not die again and the
        supervisor's restart budget is not consumed proving nothing.  Only in
        processes: a thread cannot exit alone.
        """
        if cfg.die_fraction <= 0 or where != cfg.die_phase or epoch != 1:
            return
        draw = (int(hashlib.sha1(f"{amp.manifest.job_id}:{rank}".encode()).hexdigest(), 16)
                % 10_000) / 10_000
        if draw < cfg.die_fraction:
            amp.trace("executor.die", rank=rank, phase=where, injected=True)
            if os.environ.get("AMPI_RANK") is None:
                raise RuntimeError(f"injected executor death in {where}")
            sys.stdout.flush()
            os._exit(EXIT_EXECUTOR_DIED)

    def invoke(label: str, prompt: str, contract: dict, meta: dict | None = None,
               validate: Any = None) -> Any:
        """One agent call: memoised, lease-extended, validated, checkpointed.

        Memoised first: a restarted rank re-enters this function with the same
        label and must not pay for the work its predecessor finished.  Then the
        lease is extended across the call, because a lease-based detector cannot
        tell a thinking executor from a dead one.  Then a harness-side validation
        the contract cannot express (paragraph count, languages present) gets one
        corrective re-invocation, and the result --- or the best partial --- is
        stored before the rank moves on.
        """
        key = f"r{rank}/{label}"
        saved = amp.get(MEMO_WIN, key, out=str(bodies / f"memo-{label.replace(':', '_')}.json"))
        if saved.get("present"):
            amp.trace("task.replay", rank=rank, label=label)
            return take(saved, label)
        amp.heartbeat(extend=cfg.task_timeout)
        value: Any = None
        problems: list[str] = []
        for attempt in range(2):
            text = prompt if not problems else (
                prompt + "\n\n## Your previous attempt was rejected\n\n" +
                "\n".join(f"- {p}" for p in problems) + "\n\nProduce the complete artifact.")
            value = executor.invoke(Task(aid=new_aid(), rank=rank, label=label, prompt=text,
                                         contract=Contract.parse(contract), meta=meta or {}))
            problems = validate(value) if validate else []
            if not problems:
                break
            amp.trace("task.invalid", rank=rank, label=label, attempt=attempt + 1,
                      problems=problems[:5])
        amp.put(MEMO_WIN, key, value)
        amp.ctx_release()
        return value

    # -- 0. commission ------------------------------------------------------
    phase("start")
    amp.win_create(MEMO_WIN)
    amp.win_create(BOOK_WIN)
    amp.win_create(AMEND_WIN)
    commission = {
        "book": corpus_mod.TITLE, "author": corpus_mod.AUTHOR, "languages": languages,
        "register": "reported non-fiction, close to its subjects, frequently wry",
        "rules": ["the binding glossary overrides local preference",
                  "translate every paragraph; never summarise",
                  "keep the source's paragraphing"],
        "arm": cfg.arm, "model": cfg.model,
    }
    if rank == 0:
        amp.bcast("commission", payload=commission, root=0, timeout=cfg.phase_timeout)
    else:
        commission = bcast_in("commission")

    # -- 1. scatter -----------------------------------------------------------
    slices = [{"rank": i, **s} for i, s in enumerate(segments)] if rank == 0 else None
    segment = amp.scatter(
        "segments", payload=slices, root=0, timeout=cfg.phase_timeout,
        contract={"kind": "json", "expect": {"rank": "{rank}"}, "required": ["units"]},
    )["body"]
    assert segment["sha256_16"] == my_segment["sha256_16"], "scatter delivered the wrong slice"
    units_in = segment["units"]
    report["pages"] = segment["pages"]
    report["paragraphs"] = len(units_in)

    # -- 2. survey ------------------------------------------------------------
    phase("survey")
    survey = invoke(f"survey:r{rank}", survey_prompt(segment, languages, rank), SURVEY_CONTRACT,
                    meta={"segment": segment})
    terms = {t["term"]: t for t in survey.get("terms", [])
             if isinstance(t, dict) and t.get("term") and isinstance(t.get("proposed"), dict)}
    report["terms_surveyed"] = len(terms)

    glossary: dict[str, Any] = {}
    agenda: list[dict[str, Any]] = []
    settled: dict[str, Any] = {}

    def parallel_arbitration(label: str, lifted: dict[str, Any], stage: str) -> dict[str, Any]:
        """Every rank holds the conflict set; each arbitrates its share; the root commits.

        Returns the settled value, at every rank (the root broadcasts it).
        """
        conflicts = lifted.get("conflicts") or {}
        if not conflicts:
            value = lifted.get("value", {})
            if rank == 0 and (lifted.get("degraded_to") or not isinstance(value, dict)):
                # The returned value is what the ledger let through; the stored
                # result is the whole thing.  A degraded view is a string with
                # elisions, and broadcasting it once poisoned every rank's copy
                # of the binding glossary at p = 128.
                value = amp.get_body(lifted["handle"])
            if rank == 0:
                amp.bcast(f"{label}-settled", payload=value, root=0, timeout=cfg.phase_timeout)
                return value
            return bcast_in(f"{label}-settled")
        keys = sorted(conflicts)
        batches = [keys[i:i + ARBITRATION_BATCH] for i in range(0, len(keys), ARBITRATION_BATCH)]
        mine = [b for j, b in enumerate(batches) if j % size == rank]
        rulings: dict[str, Any] = {}
        for j, batch in enumerate(mine):
            sub = {k: conflicts[k] for k in batch}
            got = invoke(f"arbitrate:{stage}:r{rank}:{j}",
                         arbitrate_prompt(sub, languages, rank, stage), ARBITRATE_CONTRACT,
                         meta={"conflicts": sub})
            for k, ruling in (got.get("rulings") or {}).items():
                if k in sub and isinstance(ruling, dict) and all(
                        isinstance(ruling.get(c), str) and ruling.get(c) for c in languages):
                    rulings[k] = {c: ruling[c] for c in languages}
        report[f"arbitrated_{stage}"] = len(rulings)
        got = amp.gather(f"{label}-rulings", payload=rulings, root=0, quorum=cfg.quorum,
                         timeout=cfg.phase_timeout, materialize=(rank == 0))
        if rank == 0:
            merged: dict[str, Any] = {}
            for b in got.get("bodies", []):
                if isinstance(b.get("body"), dict):
                    merged.update(b["body"])
            # Anything no rank ruled on (a lost rank, a malformed reply) falls to the
            # runtime's default arbiter, so the reduction always closes.
            _value, fallback = default_arbitrate(
                {"_ampi_conflicts": {k: v for k, v in conflicts.items() if k not in merged}})
            merged.update(fallback)
            report[f"rulings_{stage}"] = len(merged)
            report[f"rulings_{stage}_fallback"] = len(fallback)
            value = amp.op_arbitrate(label, rulings=merged)["value"]
            amp.bcast(f"{label}-settled", payload=value, root=0, timeout=cfg.phase_timeout)
            return value
        return bcast_in(f"{label}-settled")

    if cfg.arm != "noglossary":
        # -- 3. the census, with conflicts lifted -----------------------------
        phase("census")
        amp.ctx_release()   # the reductions' bodies must not compete with the ledger's leftovers
        census = amp.allreduce(
            "census", payload={t: terms[t]["proposed"] for t in terms}, op="union",
            algorithm=cfg.algorithm, quorum=cfg.quorum, timeout=cfg.phase_timeout,
        )
        report["census_conflicts"] = len(census.get("conflicts") or {})
        settled = parallel_arbitration("census", census, "census")

        if rank == 0:
            agenda = _agenda_of(terms_all(amp, settled), settled, census.get("conflicts") or {},
                                cap=cfg.research_cap)
            amp.bcast("agenda", payload=agenda, root=0, timeout=cfg.phase_timeout)
        else:
            agenda = bcast_in("agenda")
        amp.barrier("agenda-ready", quorum=cfg.quorum, timeout=cfg.phase_timeout,
                    policy=cfg.barrier_policy)

        # -- 4. research under mutual exclusion -------------------------------
        findings: dict[str, Any] = {}
        if cfg.arm != "noresearch":
            phase("research")
            amp.win_create(RESEARCH_WIN)
            # A claim is a compare-and-swap from absence, so no cell needs posting
            # first.  The root used to post one "unclaimed" cell per agenda item,
            # one push at a time, while 127 ranks waited at the barrier below: on a
            # four-machine git transport that was forty-eight serial pushes.
            amp.barrier("agenda-posted", quorum=cfg.quorum, timeout=cfg.phase_timeout,
                        policy=cfg.barrier_policy)
            done = 0
            for item in _my_order(agenda, rank, size):
                if done >= cfg.research_budget:
                    break
                if amp.get(RESEARCH_WIN, f"finding/{item['key']}").get("present"):
                    continue
                if not amp.claim(RESEARCH_WIN, f"claim/{item['key']}")["claimed"]:
                    continue
                maybe_die("research")
                finding = invoke(f"research:{item['key']}",
                                 research_prompt(item, languages, rank, tools=cfg.tools),
                                 RESEARCH_CONTRACT, meta={"term": item["term"]})
                amp.put(RESEARCH_WIN, f"finding/{item['key']}", finding)
                done += 1
            report["researched"] = done
            # -- 5. close the epoch --------------------------------------------
            amp.win_fence(RESEARCH_WIN, "research-done", timeout=cfg.phase_timeout,
                          quorum=cfg.quorum)
            findings = _read_findings(amp)
        # -- 5b. the binding glossary -----------------------------------------
        phase("glossary")
        mine = {t: settled.get(t, terms[t]["proposed"]) for t in terms}
        for term, f in findings.items():
            if isinstance(f.get("rendering"), dict):
                mine[term] = f["rendering"]
        amp.ctx_release()
        merged = amp.allreduce("glossary", payload=mine, op="union", algorithm=cfg.algorithm,
                               quorum=cfg.quorum, timeout=cfg.phase_timeout)
        report["glossary_conflicts"] = len(merged.get("conflicts") or {})
        glossary = parallel_arbitration("glossary", merged, "glossary")
        if rank == 0:
            amp.put(BOOK_WIN, "glossary", glossary)
            amp.put(BOOK_WIN, "findings", findings)

    # -- 6. offsets --------------------------------------------------------------
    offset = amp.exscan("offsets", payload=len(units_in), op="sum", quorum=cfg.quorum,
                        timeout=cfg.phase_timeout)
    report["paragraph_offset"] = offset.get("value", 0)
    amp.barrier("ready-to-translate", quorum=cfg.quorum, timeout=cfg.phase_timeout,
                policy=cfg.barrier_policy)

    # -- 7. translate --------------------------------------------------------------
    phase("translate")
    maybe_die("translate")
    relevant = _relevant_glossary(glossary, segment["units"], set(findings) if
                                  cfg.arm != "noglossary" else set())
    report["glossary_terms_given"] = len(relevant)

    chunks = _chunks(units_in, TRANSLATE_CHUNK_TOKENS)
    report["translate_chunks"] = len(chunks)
    rendered: list[dict[str, Any]] = []
    notes: list[str] = []
    new_terms: dict[str, Any] = {}
    previous: dict[str, Any] | None = None
    for j, chunk in enumerate(chunks):
        sub = {**segment, "units": chunk}
        expected = [u["i"] for u in chunk]
        translation = invoke(
            f"translate:r{rank}:c{j}",
            translate_prompt(sub, languages, rank, relevant or None, previous=previous,
                             part=(j + 1, len(chunks))),
            TRANSLATE_CONTRACT, meta={"segment": sub},
            validate=lambda v, expected=expected: _validate_translation(v, expected, languages),
        )
        part = _align(chunk, translation.get("units", []) if isinstance(translation, dict) else [],
                      languages)
        rendered.extend(part)
        if isinstance(translation, dict):
            notes.extend(n for n in translation.get("notes", []) if isinstance(n, str))
            nt = translation.get("new_terms")
            if isinstance(nt, dict):
                for k, v in nt.items():
                    new_terms.setdefault(k, v)
        previous = part[-1] if part else None
    units = rendered
    translation = {"units": units, "notes": notes, "new_terms": new_terms}
    report["units"] = len(units)
    report["units_missing"] = sum(1 for u in units if u.get("missing"))

    # -- 7b. amendments under a lock ---------------------------------------------
    if new_terms:
        adopted, clashed = _record_amendments(amp, rank, segment["chapters"], new_terms,
                                              languages, ttl=cfg.lease_s)
        report["amendments"] = adopted
        report["amendment_clashes"] = clashed

    # -- 8. seams ------------------------------------------------------------------
    if cfg.arm != "noseams" and size > 1:
        phase("seams")
        maybe_die("seams")
        ring = amp.cart_create([size], periodic=[True], name="ring")["name"]
        edges = _edges_of(units, languages)
        halo = amp.neighbor_allgather("seams", payload={"rank": rank, **edges}, comm=ring,
                                      timeout=cfg.phase_timeout, materialize=True)
        neighbours = [n["body"] for n in halo.get("neighbours", [])
                      if n.get("body") and n["body"].get("rank") != rank]
        if neighbours:
            seam = invoke(f"seam:r{rank}", seam_prompt(rank, edges, neighbours, languages),
                          SEAM_CONTRACT)
            report["seam_changed"] = bool(seam.get("changed"))
            units = _apply_seam(units, seam, languages)

    # -- 9. the draft, the spend, the manifest ------------------------------------
    draft = {"rank": rank, "index": segment["index"], "pages": segment["pages"],
             "chapters": segment["chapters"], "units": units,
             "notes": translation.get("notes", []) if isinstance(translation, dict) else [],
             "new_terms": new_terms or {}, "epoch": epoch}
    amp.put(BOOK_WIN, f"seg/{segment['index']:03d}", draft)
    (out_dir / f"rank{rank}.json").write_text(
        json.dumps(draft, indent=1, ensure_ascii=False), encoding="utf-8")

    usage = executor.stats().get("usage", {}) if hasattr(executor, "stats") else {}
    spend = amp.allreduce("spend", payload=float(usage.get("cost_usd", 0.0)), op="sum",
                          quorum=cfg.quorum, timeout=cfg.phase_timeout)
    report["spend_total_usd"] = round(float(spend.get("value") or 0.0), 4)
    report["usage"] = usage

    got = amp.gather("assemble",
                     payload={"rank": rank, "segment": segment["index"], "pages": segment["pages"],
                              "units": len(units), "offset": report["paragraph_offset"],
                              "epoch": epoch, **{k: usage.get(k, 0) for k in
                                                 ("prompt_tokens", "completion_tokens",
                                                  "cost_usd", "calls", "tool_calls")}},
                     root=0, quorum=cfg.quorum, timeout=cfg.phase_timeout,
                     materialize=(rank == 0))
    if rank == 0:
        report["contributors"] = got.get("contributors")
        report["manifest"] = [b["body"] for b in got.get("bodies", [])]
    phase("done")
    amp.finalize(note="e7 done")
    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _win_values(amp: Ampi, win: str, prefix: str = "") -> dict[str, Any]:
    """Read a window at the device level, uncharged.

    The context ledger accounts for what enters an executor's context.  These
    reads never do: they are host-side bookkeeping --- assembling the book, building
    the agenda, promoting evidence --- so charging them would misstate the one
    quantity the ledger exists to measure, and at p=64 a root reading every
    survey would exhaust its budget on data no prompt ever sees.
    """
    space = amp._space(win)  # noqa: SLF001 - the window's device namespace
    out: dict[str, Any] = {}
    for cell in amp.device.keys(space, prefix=prefix):
        full = amp.device.read(space, cell.key)
        if full is not None:
            out[cell.key] = full.value
    return out


def terms_all(amp: Ampi, settled: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The root's view of every surveyed term, for building the agenda.

    The census value carries renderings only; the survey metadata (kind, gloss,
    needs_research) lives in each rank's memoised survey.  Read them back from the
    window: it is the population's shared state, which is what a window is for.
    """
    out: dict[str, dict[str, Any]] = {}
    for key, value in _win_values(amp, MEMO_WIN).items():
        if "/survey:" not in key or not isinstance(value, dict):
            continue
        for t in value.get("terms", []):
            if isinstance(t, dict) and t.get("term"):
                prev = out.get(t["term"])
                if prev is None or (t.get("needs_research") and not prev.get("needs_research")):
                    out[t["term"]] = t
    for t in settled:
        out.setdefault(t, {"term": t, "proposed": settled[t]})
    return out


def _agenda_of(terms: dict, settled: Any, conflicts: dict[str, Any] | None = None, *,
               cap: int = 0) -> list[dict[str, Any]]:
    """Contested terms first: the reduction already computed the priority."""
    contested = set(conflicts or {})
    agenda: list[dict[str, Any]] = []
    seen: set[str] = set()
    proposals = settled if isinstance(settled, dict) else {}
    ordered = sorted((t for t, m in terms.items() if m.get("needs_research")),
                     key=lambda t: (t not in contested, t))
    for term in ordered:
        meta = terms[term]
        key = _slug(term)
        if key in seen:
            continue
        seen.add(key)
        agenda.append({"key": key, "term": term, "kind": meta.get("kind", ""),
                       "gloss": meta.get("gloss", ""), "why_hard": meta.get("why_hard", ""),
                       "contested": term in contested,
                       "proposed": proposals.get(term, meta.get("proposed", {}))})
        if cap and len(agenda) >= cap:
            break
    return agenda


def _slug(term: str) -> str:
    import re

    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", term).strip("-").lower()[:24]
    return f"{ascii_part or 'term'}-{hashlib.sha1(term.encode('utf-8')).hexdigest()[:8]}"


def _my_order(agenda: list[dict[str, Any]], rank: int, size: int) -> list[dict[str, Any]]:
    if not agenda:
        return []
    start = (rank * max(1, len(agenda) // max(1, size))) % len(agenda)
    return agenda[start:] + agenda[:start]


def _read_findings(amp: Ampi) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in amp.win_ls(RESEARCH_WIN, prefix="finding/")["items"]:
        cell = amp.get(RESEARCH_WIN, item["key"])
        if not cell.get("present"):
            continue
        body = cell.get("value") or {}
        if isinstance(body, dict) and body.get("term"):
            out[body["term"]] = body
    return out


def _relevant_glossary(glossary: dict[str, Any], units: list[dict[str, Any]],
                       researched: set[str]) -> dict[str, Any]:
    """The glossary entries this segment can use, bounded.

    A p=64 census has a thousand terms and a segment uses forty of them; sending
    the whole glossary to every rank would make the prompt --- the executor's
    entire context --- mostly irrelevant.  Researched terms are always sent: they
    are the ones the population paid to settle.
    """
    if not glossary:
        return {}
    if len(glossary) <= GLOSSARY_CAP:
        return dict(sorted(glossary.items()))
    text = "\n".join(u["ru"] for u in units).lower()
    out: dict[str, Any] = {}
    for term, rendering in glossary.items():
        if term in researched:
            out[term] = rendering
            continue
        low = term.lower()
        words = [w for w in low.replace("«", " ").replace("»", " ").split() if len(w) >= 4]
        stem_hit = any(w[:5] in text for w in words)
        if low in text or stem_hit:
            out[term] = rendering
    if len(out) > GLOSSARY_CAP:
        keep = [t for t in out if t in researched] + [t for t in sorted(out) if t not in researched]
        out = {t: out[t] for t in keep[:GLOSSARY_CAP]}
    return dict(sorted(out.items()))


def _chunks(units: list[dict[str, Any]], budget_tokens: int) -> list[list[dict[str, Any]]]:
    """Cut a segment into runs of paragraphs of about ``budget_tokens`` source tokens."""
    from ampi.tokens import count_tokens

    out: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    acc = 0
    for u in units:
        n = count_tokens(u["ru"])
        if cur and acc + n > budget_tokens:
            out.append(cur)
            cur, acc = [], 0
        cur.append(u)
        acc += n
    if cur:
        out.append(cur)
    return out


def _validate_translation(value: Any, expected_i: list[int], languages: list[str]) -> list[str]:
    problems: list[str] = []
    units = value.get("units") if isinstance(value, dict) else None
    if not isinstance(units, list):
        return ["`units` must be a list"]
    got = {u.get("i"): u for u in units if isinstance(u, dict)}
    missing = [i for i in expected_i if i not in got]
    if missing:
        problems.append(f"paragraphs {missing[:12]} are missing ({len(missing)} of "
                        f"{len(expected_i)})")
    for i in expected_i:
        u = got.get(i)
        if u is None:
            continue
        for c in languages:
            if not isinstance(u.get(c), str) or not u[c].strip():
                problems.append(f"paragraph {i} has no {c} rendering")
                break
    return problems[:8]


def _align(units_in: list[dict[str, Any]], units_out: Any, languages: list[str]) -> list[dict]:
    """Pair the model's units with the source paragraphs by index; mark what is missing."""
    got = {u.get("i"): u for u in (units_out or []) if isinstance(u, dict)}
    out = []
    for u in units_in:
        m = got.get(u["i"]) or {}
        entry = {"i": u["i"], "page": u["page"], "chapter": u["chapter"], "ru": u["ru"]}
        missing = False
        for c in languages:
            v = m.get(c)
            if not isinstance(v, str) or not v.strip():
                missing = True
                v = ""
            entry[c] = v
        if missing:
            entry["missing"] = True
        out.append(entry)
    return out


def _record_amendments(amp: Ampi, rank: int, chapters: list[str], new_terms: dict[str, Any],
                       languages: list[str], *, ttl: float) -> tuple[int, int]:
    """Record the terms this translator settled, under a lock, first writer wins.

    A lock rather than ``accumulate`` because the merge needs a judgement the
    runtime operator cannot make: a term already present with a *different*
    rendering is a clash to record, not a value to overwrite or to lift.  The lock
    is leased and fenced, so a translator that dies holding it does not wedge the
    chapter and one that wakes up late cannot corrupt it.
    """
    adopted = clashed = 0
    clean = {t: r for t, r in new_terms.items()
             if isinstance(r, dict) and all(isinstance(r.get(c), str) for c in languages)}
    if not clean:
        return 0, 0
    for chapter in chapters:
        try:
            lock = amp.win_lock(AMEND_WIN, chapter, ttl=ttl, timeout=min(300.0, ttl))
        except AmpiError as exc:
            amp.trace("amend.skipped", rank=rank, chapter=chapter, error=exc.cls_name)
            continue
        try:
            cell = amp.get(AMEND_WIN, chapter)
            ledger = dict(cell.get("value") or {}) if cell.get("present") else {}
            for term, rendering in clean.items():
                prev = ledger.get(term)
                if prev is None:
                    ledger[term] = {**{c: rendering[c] for c in languages}, "by": rank}
                    adopted += 1
                elif any(prev.get(c) != rendering.get(c) for c in languages):
                    clashed += 1
                    ledger[term].setdefault("clashes", []).append(
                        {"by": rank, **{c: rendering[c] for c in languages}})
            amp.put(AMEND_WIN, chapter, ledger, lock_token=lock["token"])
        finally:
            try:
                amp.win_unlock(lock["lock_id"])
            except AmpiError as exc:
                amp.trace("amend.unlock-failed", rank=rank, chapter=chapter, error=exc.cls_name)
    return adopted, clashed


def _edges_of(units: list[dict], languages: list[str]) -> dict[str, Any]:
    def edge(u: dict | None) -> dict[str, str]:
        return {"ru": str((u or {}).get("ru", ""))[:600],
                **{c: str((u or {}).get(c, "")) for c in languages}}

    return {"head": edge(units[0] if units else None),
            "tail": edge(units[-1] if units else None)}


def _apply_seam(units: list[dict], seam: dict, languages: list[str]) -> list[dict]:
    revised = seam.get("revised") or {}
    if not units or not seam.get("changed"):
        return units
    for slot, index in (("head", 0), ("tail", len(units) - 1)):
        block = revised.get(slot) or {}
        for c in languages:
            if isinstance(block.get(c), str) and block[c].strip():
                units[index][c] = block[c]
                units[index]["seam_revised"] = True
    return units


# ---------------------------------------------------------------------------
# Assembly and evidence
# ---------------------------------------------------------------------------


def assemble(amp: Ampi, languages: list[str], out_dir: Path) -> dict[str, Any]:
    """Read every segment from the window and write the book, in the working directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    segs = [v for v in _win_values(amp, BOOK_WIN, prefix="seg/").values() if isinstance(v, dict)]
    segs.sort(key=lambda s: s["index"])
    paragraphs = [u for s in segs for u in s["units"]]
    with open(out_dir / "book.jsonl", "w", encoding="utf-8") as fh:
        for s in segs:
            for u in s["units"]:
                fh.write(json.dumps({"segment": s["index"], "rank": s["rank"], **u},
                                    ensure_ascii=False) + "\n")
    names = {"ru": "Русский", "en": "English", "zh": "中文", "ja": "日本語"}
    with open(out_dir / "book.parallel.md", "w", encoding="utf-8") as fh:
        fh.write(f"# {corpus_mod.TITLE}\n\n{corpus_mod.AUTHOR}\n\n")
        page = None
        for u in paragraphs:
            if u["page"] != page:
                page = u["page"]
                fh.write(f"\n---\n\n*page {page}*\n\n")
            fh.write(f"**RU** {u['ru']}\n\n")
            for c in languages:
                fh.write(f"**{c.upper()}** {u.get(c, '')}\n\n")
    for c in languages:
        with open(out_dir / f"book.{c}.md", "w", encoding="utf-8") as fh:
            fh.write(f"# {corpus_mod.TITLE} ({names.get(c, c)})\n\n")
            page = None
            for u in paragraphs:
                if u["page"] != page:
                    page = u["page"]
                    fh.write(f"\n<!-- page {page} -->\n\n")
                fh.write(f"{u.get(c, '')}\n\n")
    missing = sum(1 for u in paragraphs if u.get("missing"))
    return {"segments": len(segs), "paragraphs": len(paragraphs), "missing": missing,
            "coverage": round(1 - missing / max(1, len(paragraphs)), 4),
            "seam_revised": sum(1 for u in paragraphs if u.get("seam_revised"))}


def promote_evidence(amp: Ampi, run_dir: Path, languages: list[str]) -> dict[str, Any]:
    """The population's glossary and amendments: its scholarly output, committed."""
    out: dict[str, Any] = {}
    book = _win_values(amp, BOOK_WIN)
    if "glossary" in book:
        (run_dir / "glossary.json").write_text(
            json.dumps(book["glossary"], indent=1, ensure_ascii=False), encoding="utf-8")
        out["glossary_terms"] = len(book["glossary"] or {})
    if "findings" in book:
        (run_dir / "findings.json").write_text(
            json.dumps(book["findings"], indent=1, ensure_ascii=False), encoding="utf-8")
        out["findings"] = len(book["findings"] or {})
        out["sources_cited"] = sum(len(v.get("sources") or [])
                                   for v in (book["findings"] or {}).values()
                                   if isinstance(v, dict))
    try:
        amend = _win_values(amp, AMEND_WIN)
    except AmpiError:
        amend = {}
    if amend:
        (run_dir / "amendments.json").write_text(
            json.dumps(amend, indent=1, ensure_ascii=False), encoding="utf-8")
        out["amendments"] = sum(len(v) for v in amend.values())
        out["amendment_clashes"] = sum(
            len(e.get("clashes", [])) for v in amend.values() for e in v.values()
            if isinstance(e, dict))
    return out


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _paths(cfg: Config) -> tuple[Path, Path]:
    run_dir = Path(cfg.run_dir) if cfg.run_dir else RUNS / cfg.name
    work_dir = Path(cfg.work_dir) if cfg.work_dir else WORK / cfg.name
    return run_dir, work_dir


def cmd_rank(a: argparse.Namespace) -> int:
    """Be one rank.  Everything about the run comes from the config the driver wrote."""
    run_dir = RUNS / a.name if not a.run_dir else Path(a.run_dir)
    cfg = Config.load(run_dir / "config.json")
    run_dir, work_dir = _paths(cfg)
    segments = json.loads((work_dir / "segments.json").read_text(encoding="utf-8"))["segments"]
    root = os.environ["AMPI_ROOT"]
    rank = int(os.environ["AMPI_RANK"])
    out_dir = work_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    amp = Ampi(root, rank=rank, expect_rank=rank)
    executor = (stub_executor(cfg.languages) if cfg.executor == "stub"
                else model_executor(amp, cfg, work_dir / "calls"))
    started = time.time()
    try:
        report = rank_main(amp, rank, cfg, segments, executor, out_dir=out_dir)
        report["seconds"] = round(time.time() - started, 2)
        (out_dir / f"report{rank}.json").write_text(json.dumps(report, indent=1, default=str),
                                                    encoding="utf-8")
        print(json.dumps({k: report.get(k) for k in ("rank", "epoch", "units", "seconds",
                                                     "spend_total_usd")}))
        return 0
    except AmpiError as exc:
        amp.trace("rank.error", rank=rank, error=exc.cls_name, message=exc.message)
        print(f"rank {rank}: {exc.cls_name}: {exc.message}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        amp.trace("rank.error", rank=rank, error=type(exc).__name__, message=str(exc)[:300])
        import traceback

        traceback.print_exc()
        return 1
    finally:
        amp.close()


def cmd_run(a: argparse.Namespace) -> dict[str, Any]:
    cfg = Config(
        name=a.name, size=a.size, languages=[c for c in a.languages.split(",") if c],
        executor=a.executor, model=a.model, research_model=a.research_model,
        arbiter_model=a.arbiter_model, reasoning=a.reasoning, fallback_model=a.fallback_model,
        tools=not a.no_tools, web=a.web,
        device=a.device, arm=a.arm, task_timeout=a.task_timeout, phase_timeout=a.phase_timeout,
        quorum=a.quorum, barrier_policy=a.barrier_policy, research_budget=a.research_budget,
        research_cap=a.research_cap, algorithm=a.algorithm, die_fraction=a.die_fraction,
        die_phase=a.die_phase, respawn=a.respawn, ctx_budget=a.ctx_budget, lease_s=a.lease,
        max_attempts=a.max_attempts, first_page=a.first_page, last_page=a.last_page,
        nodes=a.nodes, remote=a.remote or "", branch=a.branch or "",
        run_dir=a.run_dir or "", work_dir=a.work_dir or "",
    )
    run_dir, work_dir = _paths(cfg)
    for d in (run_dir, work_dir, work_dir / "out"):
        d.mkdir(parents=True, exist_ok=True)
    job_root = work_dir / "job"
    env = {"AMPI_TOOL_CACHE": str(WORK / "tool-cache")}
    if cfg.device in ("git", "gitd"):
        env["AMPI_GIT_BRANCH"] = cfg.branch or f"ampi-jobs/{cfg.name}"
        if cfg.remote:
            env["AMPI_GIT_REMOTE"] = cfg.remote
        os.environ.update(env)

    corpus = corpus_mod.build(WORK, cfg.size, source_dir=a.source_dir,
                              first_page=cfg.first_page, last_page=cfg.last_page)
    corpus_mod.dump_segments(corpus, work_dir / "segments.json")
    if a.node == 0:
        cfg.save(run_dir / "config.json")
        corpus_mod.write_manifest(corpus, run_dir / "corpus_manifest.json")
        (run_dir / "launch_plan.json").write_text(json.dumps({
            "experiment": "e7_rawapi_book", "name": cfg.name, "size": cfg.size, "nodes": cfg.nodes,
            "launch": a.launch, "device": cfg.device, "executor": cfg.executor,
            "model": cfg.model, "model_pool": [m.strip() for m in cfg.model.split(",") if m.strip()],
            "research_model": cfg.research_model or cfg.model,
            "arbiter_model": cfg.arbiter_model or cfg.model, "reasoning": cfg.reasoning,
            "fallback_model": cfg.fallback_model,
            "tools": cfg.tools, "web": cfg.web, "arm": cfg.arm, "languages": cfg.languages,
            "quorum": cfg.quorum, "barrier_policy": cfg.barrier_policy,
            "die_fraction": cfg.die_fraction, "die_phase": cfg.die_phase, "respawn": cfg.respawn,
            "job_root": str(job_root), "requested_ranks": list(range(cfg.size)),
            "created_at": time.time(),
            "ranks": [s.metadata() for s in corpus.segments],
        }, indent=2), encoding="utf-8")
    else:
        # A joining node is on another machine and sees only its own disk: it
        # writes the same config from the same flags, so its rank processes read
        # what node 0's do.  The flags are the operator's responsibility; the
        # job manifest on the device is what the runtime actually enforces.
        cfg.save(run_dir / "config.json")

    started = time.time()
    segments = [s.payload() for s in corpus.segments]
    launch_record: dict[str, Any] = {}
    population_complete = True
    if a.launch == "threads":
        h = Harness(root=str(job_root), size=cfg.size, device=cfg.device, force=True,
                    ctx_budget=cfg.ctx_budget,
                    meta={"experiment": "e7_rawapi_book", "arm": cfg.arm, "executor": cfg.executor,
                          "model": cfg.model})
        job = h.create()
        execs: dict[int, Any] = {}

        def main(amp: Ampi, rank: int) -> Any:
            ex = (stub_executor(cfg.languages) if cfg.executor == "stub"
                  else model_executor(amp, cfg, work_dir / "calls"))
            execs[rank] = ex
            return rank_main(amp, rank, cfg, segments, ex, out_dir=work_dir / "out")

        results = h.run(main, timeout=cfg.phase_timeout * 4, finalize=False)
        for r in results:
            if r.ok:
                (work_dir / "out" / f"report{r.rank}.json").write_text(
                    json.dumps(r.value, indent=1, default=str), encoding="utf-8")
        h.save(results, run_dir / "harness.json")
        launch_record = {"launch": "threads", "succeeded": sum(1 for r in results if r.ok),
                         "failed": sum(1 for r in results if not r.ok),
                         "errors": [{"rank": r.rank, "error": r.error} for r in results if not r.ok]}
        job.close()
    else:
        from ampitools.launcher import export, launch

        cmd = [sys.executable, "-m", "experiments.e7_rawapi_book.harness", "rank", "--name", cfg.name]
        if cfg.run_dir:
            cmd += ["--run-dir", cfg.run_dir]
        launch_record = launch(
            cmd, size=cfg.size, root=job_root, device=cfg.device, nodes=cfg.nodes, node=a.node,
            ctx_budget=cfg.ctx_budget, join_deadline_s=cfg.phase_timeout,
            meta={"experiment": "e7_rawapi_book", "arm": cfg.arm, "executor": cfg.executor,
                  "model": cfg.model, "name": cfg.name},
            log_dir=work_dir / "launch", env=env, respawn=cfg.respawn,
            timeout_s=cfg.phase_timeout * 4, worker_prefix="e7", quiet=a.quiet,
            create=False if getattr(a, "rejoin", False) else None,
        )
        if a.node == 0 and cfg.nodes > 1:
            population_complete = _wait_for_population(job_root, cfg.size, cfg.phase_timeout)
        if a.node == 0:
            export(job_root, run_dir, name=cfg.name)

    summary: dict[str, Any] = {"name": cfg.name, "size": cfg.size, "node": a.node,
                               "wall_s": round(time.time() - started, 2),
                               "population_complete": population_complete}
    if not population_complete:
        # The other nodes' ranks are still running (or unreachable) after the
        # phase timeout.  What follows is a snapshot, and it must say so: the
        # report carries the flag, the process exits non-zero, and nothing here
        # is to be sealed as a completed run.
        summary["population_wait_expired_s"] = cfg.phase_timeout
        print(f"[e7] population wait expired after {cfg.phase_timeout:.0f}s; exporting a "
              "SNAPSHOT of an unfinished population", file=sys.stderr)
    if a.node == 0:
        amp = Ampi(str(job_root), allow_volatile=True)
        try:
            summary["book"] = assemble(amp, cfg.languages, work_dir / "out")
            summary["evidence"] = promote_evidence(amp, run_dir, cfg.languages)
            summary["rank_states"] = _rank_states(amp)
            summary["_recovered_ranks"] = sum(1 for r in range(amp.size) if _epoch(amp, r) > 1)
        finally:
            amp.close()
        summary["launch"] = {k: v for k, v in launch_record.items()
                             if k not in ("events", "command", "node_identity")}
        # Rank reports on disk are this machine's only; the population's numbers
        # come from the device, which every node wrote.
        reports = _collect_reports(work_dir / "out")
        summary["ranks_reported_here"] = len(reports)
        summary["ranks_finalised"] = sum(
            1 for s in summary.get("rank_states", {}).values() if s == "finalised")
        summary["spend_total_usd"] = max((r.get("spend_total_usd", 0) for r in reports), default=0)
        summary["recovered_ranks"] = summary.pop("_recovered_ranks", 0)
        summary["units_missing"] = int((summary.get("book") or {}).get("missing", 0))
        (run_dir / "report.json").write_text(json.dumps(summary, indent=2, default=str),
                                             encoding="utf-8")
        _sample(work_dir / "out", run_dir, cfg.languages)
    print(json.dumps(summary, indent=2, default=str))
    return summary


def _wait_for_population(job_root: Path, size: int, timeout: float) -> bool:
    """Wait for every rank to reach a terminal state; ``False`` if the wait expired."""
    amp = Ampi(str(job_root), allow_volatile=True)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            states = _rank_states(amp)
            if all(s in ("finalised", "failed", "fenced") for s in states.values()):
                return True
            time.sleep(10)
        return False
    finally:
        amp.close()


def _epoch(amp: Ampi, rank: int) -> int:
    try:
        return int(amp._rankview(rank).epoch)  # noqa: SLF001 - the driver's view
    except Exception:  # noqa: BLE001
        return 0


def _rank_states(amp: Ampi) -> dict[str, str]:
    out = {}
    for r in range(amp.size):
        try:
            out[str(r)] = amp._rankview(r).state  # noqa: SLF001
        except Exception:  # noqa: BLE001
            out[str(r)] = "unknown"
    return out


def _collect_reports(out_dir: Path) -> list[dict[str, Any]]:
    reports = []
    for p in sorted(out_dir.glob("report*.json")):
        try:
            reports.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return reports


def _sample(out_dir: Path, run_dir: Path, languages: list[str], *, page: int = 13,
            limit: int = 6) -> None:
    """A few paragraphs of one page, for inspection.

    Page 13 is the page the legacy project itself published as its worked example,
    so a reader can compare the two without any further text leaving the working
    directory.
    """
    book = out_dir / "book.jsonl"
    if not book.exists():
        return
    rows = []
    for line in book.read_text(encoding="utf-8").splitlines():
        u = json.loads(line)
        if u.get("page") == page and not u.get("missing"):
            rows.append({k: u[k] for k in ("i", "page", "ru", *languages) if k in u})
        if len(rows) >= limit:
            break
    if rows:
        (run_dir / "sample_page13.json").write_text(
            json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="E7: book translation on raw-API ranks")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rank", help="be one rank (run by ampirun)")
    r.add_argument("--name", required=True)
    r.add_argument("--run-dir", default="")
    p = sub.add_parser("run", help="drive a whole run on this node")
    p.add_argument("--name", required=True)
    p.add_argument("--size", type=int, default=16)
    p.add_argument("--languages", default=",".join(DEFAULT_LANGUAGES))
    p.add_argument("--executor", default="stub", choices=["stub", "model"])
    p.add_argument("--model", default="moonshotai/kimi-k3")
    p.add_argument("--research-model", default="")
    p.add_argument("--arbiter-model", default="")
    p.add_argument("--reasoning", default="low", help="none|low|medium|high")
    p.add_argument("--fallback-model", default="deepseek/deepseek-v4-pro-0813",
                   help="a second model tried from a fresh conversation when the first fails "
                        "its attempts; empty to disable")
    p.add_argument("--no-tools", action="store_true", help="research without the tool loop")
    p.add_argument("--web", action="store_true", help="research with the provider's web plugin")
    p.add_argument("--launch", default="processes", choices=["processes", "threads"])
    p.add_argument("--device", default="sqlite")
    p.add_argument("--nodes", type=int, default=1)
    p.add_argument("--node", type=int, default=0)
    p.add_argument("--rejoin", action="store_true",
                   help="node 0 re-enters an existing job instead of creating it (after a machine restart)")
    p.add_argument("--remote", default=None, help="git remote for the git device")
    p.add_argument("--branch", default=None, help="git branch for the git device")
    p.add_argument("--arm", default="full", choices=["full", "noglossary", "noresearch", "noseams"])
    p.add_argument("--task-timeout", type=float, default=1800.0)
    p.add_argument("--phase-timeout", type=float, default=7200.0)
    p.add_argument("--quorum", type=float, default=1.0)
    p.add_argument("--barrier-policy", default="proceed",
                   choices=["wait", "proceed", "shrink", "revoke"])
    p.add_argument("--research-budget", type=int, default=3)
    p.add_argument("--research-cap", type=int, default=48)
    p.add_argument("--algorithm", default=None)
    p.add_argument("--die-fraction", type=float, default=0.0,
                   help="fraction of ranks whose executor dies once, in --die-phase")
    p.add_argument("--die-phase", default="translate", choices=["research", "translate", "seams"])
    p.add_argument("--respawn", type=int, default=0, help="restarts ampirun allows per rank")
    p.add_argument("--ctx-budget", type=int, default=200000)
    p.add_argument("--lease", type=float, default=900.0)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--first-page", type=int, default=corpus_mod.FIRST_PROSE_PAGE)
    p.add_argument("--last-page", type=int, default=None)
    p.add_argument("--source-dir", default=None, help="local checkout of the page extraction")
    p.add_argument("--run-dir", default="")
    p.add_argument("--work-dir", default="")
    p.add_argument("-q", "--quiet", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> Any:
    a = build_parser().parse_args(argv)
    if a.cmd == "rank":
        return cmd_rank(a)
    out = cmd_run(a)
    if isinstance(out, dict) and out.get("population_complete") is False:
        return 3
    return out


if __name__ == "__main__":
    out = main()
    if isinstance(out, int):
        sys.exit(out)
