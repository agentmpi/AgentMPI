"""E8: the production translation by an adaptive population.

E7 moved the book through phases, and at every phase boundary the population
waited for its slowest member.  E8 keeps E7's executor, prompts, glossary and
seams and replaces the phases after the glossary with a **work pool** (spec
S9.5): the book is cut into pages, every rank has a home block of pages, and a
rank that finishes a page claims the next --- from its own block first, then
from whichever block has the most left.  A seam between two finished pages is
work for whichever rank is free.  A rank that settles a term publishes it at
once with an atomic union and every later translation reads it.  The one
collective after the glossary is the reduction at the end.

What is protocol here and what is harness is written down in DESIGN.md.  In
short: the pool (claim, dependency, reclaim from a dead holder, termination) and
the nonblocking broadcast are the runtime's; pages, seams, prompts and the
order of preference are this file's.

Run it exactly as E7:

    python -m experiments.e8_adaptive_book.harness run --name e8-stub-p16 --size 16 --executor stub
    bash experiments/e8_adaptive_book/node.sh e8-rawapi-p16 16 2 0     # node 0 of 2
    bash experiments/e8_adaptive_book/node.sh e8-rawapi-p16 16 2 1     # node 1 of 2
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
from ampi.core.ops import CONFLICT_KEY, value_of
from ampi.core.ops import arbitrate as default_arbitrate
from ampi.core.payload import Contract
from ampi.errors import AmpiError
from ampitools.executor import Task, new_aid
from ampitools.harness import Harness
from ampitools.launcher import EXIT_EXECUTOR_DIED
from experiments.e7_rawapi_book import corpus as corpus_mod
from experiments.e7_rawapi_book.harness import (
    AMEND_WIN,
    ARBITRATION_BATCH,
    BOOK_WIN,
    MEMO_WIN,
    _align,
    _apply_seam,
    _edges_of,
    _relevant_glossary,
    _validate_translation,
    _win_values,
    assemble,
    model_executor,
    promote_evidence,
    stub_executor,
)
from experiments.e7_rawapi_book.prompts import (
    ARBITRATE_CONTRACT,
    SEAM_CONTRACT,
    SURVEY_CONTRACT,
    TRANSLATE_CONTRACT,
    arbitrate_prompt,
    seam_prompt,
    survey_prompt,
    translate_prompt,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "runs"
WORK = ROOT / "work" / "e8"
POOL = "book"
DEFAULT_LANGUAGES = ("en", "zh", "ja")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class Config:
    name: str
    size: int
    languages: list[str] = field(default_factory=lambda: list(DEFAULT_LANGUAGES))
    executor: str = "stub"
    model: str = "moonshotai/kimi-k3"
    arbiter_model: str = ""
    reasoning: str = "low"
    fallback_model: str = "deepseek/deepseek-v4-pro-0813"
    device: str = "sqlite"
    task_timeout: float = 1800.0
    phase_timeout: float = 7200.0
    quorum: float = 1.0
    algorithm: str | None = None
    die_fraction: float = 0.0
    respawn: int = 0
    ctx_budget: int = 200000
    lease_s: float = 1800.0
    max_attempts: int = 3
    first_page: int = corpus_mod.FIRST_PROSE_PAGE
    last_page: int | None = None
    steal: bool = True
    nodes: int = 1
    remote: str = ""
    branch: str = ""
    run_dir: str = ""
    work_dir: str = ""
    # E7's model_executor reads these; E8 has no research phase and no tools.
    tools: bool = False
    web: bool = False
    research_model: str = ""
    arm: str = "adaptive"

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# The book as pages and blocks
# ---------------------------------------------------------------------------


def build_plan(work_dir: Path, cfg: Config, *, source_dir: str | None = None) -> dict[str, Any]:
    """Cut the book into pages and the pages into ``size`` home blocks.

    A page is the unit of work: one model call renders it whole, with the last
    paragraph of the page before it as context when that page is done.  Blocks
    are contiguous and character-balanced, so every rank starts with about the
    same amount of its own book and steals only when it is ahead.
    """
    raw = corpus_mod.read_pages(work_dir, source_dir=source_dir)
    pages: list[dict[str, Any]] = []
    index = 0
    for n, text in enumerate(raw, start=1):
        if n < cfg.first_page or (cfg.last_page is not None and n > cfg.last_page):
            continue
        paras = corpus_mod.paragraphs_of(text, n, index)
        if not paras:
            continue
        index += len(paras)
        units = [{"i": p.index, "page": p.page, "chapter": p.chapter, "ru": p.text} for p in paras]
        body = "\n\n".join(p.text for p in paras)
        pages.append({"page": n, "chapter": corpus_mod.chapter_of(n), "units": units,
                      "chars": len(body),
                      "sha256_16": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]})
    if len(pages) < cfg.size:
        raise SystemExit(f"{len(pages)} pages cannot fill {cfg.size} blocks")
    total = sum(p["chars"] for p in pages)
    blocks: list[list[int]] = []
    bucket: list[int] = []
    acc = consumed = 0
    for k, p in enumerate(pages):
        bucket.append(p["page"])
        acc += p["chars"]
        consumed += p["chars"]
        left = len(pages) - k - 1
        slots_left = cfg.size - len(blocks) - 1
        target = (total - (consumed - acc)) / max(1, cfg.size - len(blocks))
        if slots_left > 0 and ((acc >= target and left > slots_left) or left == slots_left):
            blocks.append(bucket)
            bucket, acc = [], 0
    if bucket:
        blocks.append(bucket)
    owner = {n: b for b, block in enumerate(blocks) for n in block}
    for p in pages:
        p["block"] = owner[p["page"]]
    return {"pages": pages, "blocks": blocks,
            "meta": {"title": corpus_mod.TITLE, "author": corpus_mod.AUTHOR, "unit": "page",
                     "n_pages": len(pages), "n_paragraphs": index, "size": cfg.size,
                     "blocks": [{"rank": b, "pages": [blk[0], blk[-1]], "n_pages": len(blk),
                                 "chars": sum(p["chars"] for p in pages if p["block"] == b)}
                                for b, blk in enumerate(blocks)]}}


def page_id(n: int) -> str:
    return f"p{n:03d}"


def seam_id(a: int, b: int) -> str:
    return f"s{min(a, b):03d}-{max(a, b):03d}"


def seeds_of(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": page_id(p["page"]), "group": f"b{p['block']}", "priority": 0,
             "payload": {"page": p["page"]}} for p in plan["pages"]]


# ---------------------------------------------------------------------------
# The rank program
# ---------------------------------------------------------------------------


def rank_main(amp: Ampi, rank: int, cfg: Config, plan: dict[str, Any], executor: Any, *,
              out_dir: Path) -> dict[str, Any]:
    languages = cfg.languages
    size = amp.size
    pages = {p["page"]: p for p in plan["pages"]}
    order = [p["page"] for p in plan["pages"]]
    my_block = plan["blocks"][rank] if rank < len(plan["blocks"]) else []
    report: dict[str, Any] = {"rank": rank, "block": [my_block[0], my_block[-1]] if my_block else [],
                              "pages_done": [], "seams_done": [], "stolen": 0, "reclaimed": 0,
                              "waited_s": 0.0}
    info = amp.init(role="translator", lease_s=cfg.lease_s)
    epoch = int(info.get("epoch", 1))
    report["epoch"] = epoch
    if epoch > 1:
        report["recovered"] = True
    amp.ctx_release()
    bodies = out_dir / "bodies" / f"r{rank}"
    bodies.mkdir(parents=True, exist_ok=True)

    def take(res: dict[str, Any], label: str) -> Any:
        path = res.get("saved_to")
        if not path:
            return res.get("body", res.get("value"))
        text = Path(path).read_text(encoding="utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label}: the delivered body is not JSON ({exc})") from exc

    def read_cell(win: str, key: str) -> Any:
        """A window body, uncharged: the harness's bookkeeping, not a model's reading."""
        got = amp.get(win, key, out=str(bodies / f"{win}-{key.replace('/', '_')}.json"))
        if not got.get("present"):
            return None
        return take(got, f"{win}/{key}")

    def maybe_die(where: str) -> None:
        if cfg.die_fraction <= 0 or epoch != 1:
            return
        draw = (int(hashlib.sha1(f"{amp.manifest.job_id}:{rank}:{where}".encode()).hexdigest(), 16)
                % 10_000) / 10_000
        if draw < cfg.die_fraction:
            amp.trace("executor.die", rank=rank, phase=where, injected=True)
            if os.environ.get("AMPI_RANK") is None:
                raise RuntimeError(f"injected executor death in {where}")
            sys.stdout.flush()
            os._exit(EXIT_EXECUTOR_DIED)

    def invoke(label: str, prompt: str, contract: dict, meta: dict | None = None,
               validate: Any = None) -> Any:
        """One agent call: memoised, lease-extended, validated, checkpointed (as E7)."""
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

    # -- 0. commission and windows --------------------------------------------
    amp.memo("phase", "start")
    for w in (MEMO_WIN, BOOK_WIN, AMEND_WIN):
        amp.win_create(w)
    commission = {"book": corpus_mod.TITLE, "author": corpus_mod.AUTHOR, "languages": languages,
                  "register": "reported non-fiction, close to its subjects, frequently wry",
                  "rules": ["the binding glossary overrides local preference",
                            "translate every paragraph; never summarise",
                            "keep the source's paragraphing"], "model": cfg.model}
    if rank == 0:
        amp.bcast("commission", payload=commission, root=0, timeout=cfg.phase_timeout)
    else:
        take(amp.bcast("commission", root=0, timeout=cfg.phase_timeout,
                       out=str(bodies / "commission.json")), "commission")

    # -- 1. survey the home block -----------------------------------------------
    amp.memo("phase", "survey")
    block_units = [u for n in my_block for u in pages[n]["units"]]
    segment = {"index": rank, "pages": [my_block[0], my_block[-1]] if my_block else [],
               "chapters": sorted({pages[n]["chapter"] for n in my_block}), "units": block_units}
    survey = invoke(f"survey:r{rank}", survey_prompt(segment, languages, rank), SURVEY_CONTRACT,
                    meta={"segment": segment})
    terms = {t["term"]: t for t in survey.get("terms", [])
             if isinstance(t, dict) and t.get("term") and isinstance(t.get("proposed"), dict)}
    report["terms_surveyed"] = len(terms)

    # -- 2. census, arbitration, and a glossary nobody waits to hand out -----------
    amp.memo("phase", "census")
    amp.ctx_release()
    census = amp.allreduce("census", payload={t: terms[t]["proposed"] for t in terms}, op="union",
                           algorithm=cfg.algorithm, quorum=cfg.quorum, timeout=cfg.phase_timeout)
    conflicts = census.get("conflicts") or {}
    report["census_conflicts"] = len(conflicts)
    if conflicts:
        keys = sorted(conflicts)
        batches = [keys[i:i + ARBITRATION_BATCH] for i in range(0, len(keys), ARBITRATION_BATCH)]
        rulings: dict[str, Any] = {}
        for j, batch in enumerate(b for k, b in enumerate(batches) if k % size == rank):
            sub = {k: conflicts[k] for k in batch}
            got = invoke(f"arbitrate:census:r{rank}:{j}",
                         arbitrate_prompt(sub, languages, rank, "census"), ARBITRATE_CONTRACT,
                         meta={"conflicts": sub})
            for k, ruling in (got.get("rulings") or {}).items():
                if k in sub and isinstance(ruling, dict) and all(
                        isinstance(ruling.get(c), str) and ruling.get(c) for c in languages):
                    rulings[k] = {c: ruling[c] for c in languages}
        report["arbitrated"] = len(rulings)
        got = amp.gather("census-rulings", payload=rulings, root=0, quorum=cfg.quorum,
                         timeout=cfg.phase_timeout, materialize=(rank == 0))
        if rank == 0:
            merged: dict[str, Any] = {}
            for b in got.get("bodies", []):
                if isinstance(b.get("body"), dict):
                    merged.update(b["body"])
            _v, fallback = default_arbitrate(
                {CONFLICT_KEY: {k: v for k, v in conflicts.items() if k not in merged}})
            merged.update(fallback)
            report["rulings_fallback"] = len(fallback)
            glossary = amp.op_arbitrate("census", rulings=merged)["value"]
    else:
        glossary = census.get("value", {})
        if rank == 0 and (census.get("degraded_to") or not isinstance(glossary, dict)):
            glossary = amp.get_body(census["handle"])
    # The root publishes the settled glossary and goes straight to the pool: a
    # nonblocking broadcast (S7.4).  In E7 the root of this broadcast waited for
    # its slowest receiver while its own segment sat untranslated.
    if rank == 0:
        amp.ibcast("glossary", payload=glossary, root=0)
        amp.put(BOOK_WIN, "glossary", glossary)
    else:
        req = amp.ibcast("glossary", root=0)
        glossary = take(amp.wait(req["request"], timeout=cfg.phase_timeout,
                                 out=str(bodies / "glossary.json")), "glossary")
    if not isinstance(glossary, dict):
        glossary = {}
    report["glossary_terms"] = len(glossary)

    # -- 3. the pool ------------------------------------------------------------
    amp.memo("phase", "pool")
    amp.pool_create(POOL, seeds_of(plan))
    mine = f"b{rank}"

    def current_amendments(chapter: str) -> dict[str, Any]:
        raw = read_cell(AMEND_WIN, chapter)
        if not isinstance(raw, dict):
            return {}
        out: dict[str, Any] = {}
        for term, rendering in value_of(raw).items() if isinstance(value_of(raw), dict) else []:
            if isinstance(rendering, dict):
                out[term] = rendering
        return out

    def translate_page(n: int) -> None:
        page = pages[n]
        amp.ctx_release()
        maybe_die("translate")
        chapter = page["chapter"]
        extra = current_amendments(chapter)
        merged_glossary = {**extra, **glossary}   # the binding glossary wins over an amendment
        relevant = _relevant_glossary(merged_glossary, page["units"], set())
        previous = None
        if n - 1 in pages:
            prev = read_cell(BOOK_WIN, f"seg/{n - 1:03d}")
            if isinstance(prev, dict) and prev.get("units"):
                previous = prev["units"][-1]
        seg = {"index": n, "pages": [n, n], "chapters": [chapter], "units": page["units"]}
        expected = [u["i"] for u in page["units"]]
        translation = invoke(
            f"translate:p{n:03d}", translate_prompt(seg, languages, rank, relevant or None,
                                                    previous=previous, part=(1, 1)),
            TRANSLATE_CONTRACT, meta={"segment": seg},
            validate=lambda v: _validate_translation(v, expected, languages))
        units = _align(page["units"], translation.get("units", []) if isinstance(translation, dict)
                       else [], languages)
        new_terms = translation.get("new_terms") if isinstance(translation, dict) else None
        new_terms = {k: v for k, v in (new_terms or {}).items()
                     if isinstance(v, dict) and all(isinstance(v.get(c), str) for c in languages)
                     and k not in glossary}
        draft = {"rank": rank, "index": n, "pages": [n, n], "chapters": [chapter], "units": units,
                 "notes": translation.get("notes", []) if isinstance(translation, dict) else [],
                 "new_terms": new_terms, "epoch": epoch, "block": page["block"],
                 "stolen": page["block"] != rank}
        amp.put(BOOK_WIN, f"seg/{n:03d}", draft)
        if new_terms:
            # One atomic union, no lock: a term two translators settled differently
            # is lifted as a conflict, which the analysis counts as a clash and the
            # next reader resolves by taking the glossary's or the first value.
            amp.accumulate(AMEND_WIN, chapter,
                           {t: {**{c: r[c] for c in languages}, "by": rank} for t, r in
                            new_terms.items()}, op="union")
            report["amendments"] = report.get("amendments", 0) + len(new_terms)
        amp.pool_done(POOL, page_id(n), result={"units": len(units), "rank": rank})
        report["pages_done"].append(n)
        if page["block"] != rank:
            report["stolen"] += 1
        # A seam exists the moment both of its pages do; both neighbours may
        # propose it and the pool keeps one.
        for m in (n - 1, n + 1):
            if m in pages and amp.get(BOOK_WIN, f"seg/{m:03d}").get("present"):
                a, b = min(n, m), max(n, m)
                amp.pool_add(POOL, {"id": seam_id(a, b), "deps": [page_id(a), page_id(b)],
                                    "priority": 1, "group": f"b{pages[a]['block']}",
                                    "payload": {"pages": [a, b]}})

    def revise_seam(a: int, b: int) -> None:
        amp.ctx_release()
        maybe_die("seam")
        left = read_cell(BOOK_WIN, f"seg/{a:03d}")
        right = read_cell(BOOK_WIN, f"seg/{b:03d}")
        if not (isinstance(left, dict) and isinstance(right, dict)):
            amp.pool_done(POOL, seam_id(a, b), result={"skipped": "a page is missing"})
            return
        my_edges = _edges_of(left["units"], languages)
        seam = invoke(f"seam:{seam_id(a, b)}",
                      seam_prompt(rank, my_edges, [{"rank": right["rank"],
                                                    **_edges_of(right["units"], languages)}],
                                  languages), SEAM_CONTRACT)
        if isinstance(seam, dict) and seam.get("changed"):
            revised = dict(seam)
            revised["revised"] = {"tail": (seam.get("revised") or {}).get("tail") or {}}
            left["units"] = _apply_seam(left["units"], revised, languages)
            left["seam_revised_by"] = rank
            amp.put(BOOK_WIN, f"seg/{a:03d}", left)
            report["seams_changed"] = report.get("seams_changed", 0) + 1
        amp.pool_done(POOL, seam_id(a, b), result={"changed": bool(seam.get("changed"))
                                                   if isinstance(seam, dict) else False})
        report["seams_done"].append([a, b])

    while True:
        # Prefer the home block; when it is finished, prefer the block with the
        # most pages left, so stealing spreads rather than piling onto one victim.
        prefer = mine
        if cfg.steal:
            status = amp.pool_status(POOL)
            ready = status.get("ready_ids") or []
            if not any(pages.get(int(i[1:]), {}).get("block") == rank for i in ready
                       if i.startswith("p")):
                left_by_block: dict[int, int] = {}
                for i in ready:
                    if i.startswith("p"):
                        blk = pages[int(i[1:])]["block"]
                        left_by_block[blk] = left_by_block.get(blk, 0) + 1
                if left_by_block:
                    prefer = f"b{max(left_by_block, key=lambda k: (left_by_block[k], -k))}"
        nxt = amp.pool_next(POOL, prefer=prefer, wait=True, timeout=cfg.phase_timeout)
        report["waited_s"] = round(report["waited_s"] + float(nxt.get("waited_s") or 0), 3)
        item = nxt.get("item")
        if item is None:
            break
        if nxt.get("reclaimed"):
            report["reclaimed"] += 1
        if item["id"].startswith("p"):
            translate_page(int(item["payload"]["page"]))
        else:
            a, b = item["payload"]["pages"]
            revise_seam(int(a), int(b))
        amp.heartbeat()

    # -- 4. the spend, the manifest ------------------------------------------------
    amp.memo("phase", "done-pool")
    report["pages_in_order"] = [n for n in order if n in set(report["pages_done"])]
    usage = executor.stats().get("usage", {}) if hasattr(executor, "stats") else {}
    amp.ctx_release()
    spend = amp.allreduce("spend", payload=float(usage.get("cost_usd", 0.0)), op="sum",
                          quorum=cfg.quorum, timeout=cfg.phase_timeout)
    report["spend_total_usd"] = round(float(spend.get("value") or 0.0), 4)
    report["usage"] = usage
    got = amp.gather("assemble",
                     payload={"rank": rank, "pages": report["pages_done"],
                              "seams": len(report["seams_done"]), "stolen": report["stolen"],
                              "waited_s": report["waited_s"], "epoch": epoch,
                              **{k: usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens",
                                                              "cost_usd", "calls", "tool_calls")}},
                     root=0, quorum=cfg.quorum, timeout=cfg.phase_timeout, materialize=False)
    if rank == 0:
        manifest = []
        for b in got.get("bodies", []):
            body = b.get("body")
            if body is None and b.get("handle"):
                body = amp.get_body(b["handle"])
            if body is not None:
                manifest.append(body)
        report["manifest"] = manifest
    amp.memo("phase", "done")
    amp.finalize(note="e8 done")
    return report


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _paths(cfg: Config) -> tuple[Path, Path]:
    run_dir = Path(cfg.run_dir) if cfg.run_dir else RUNS / cfg.name
    work_dir = Path(cfg.work_dir) if cfg.work_dir else WORK / cfg.name
    return run_dir, work_dir


def cmd_rank(a: argparse.Namespace) -> int:
    cfg = Config.load(Path(a.run_dir or (RUNS / a.name)) / "config.json")
    if a.run_dir:
        cfg.run_dir = a.run_dir
    _run_dir, work_dir = _paths(cfg)
    plan = json.loads((work_dir / "plan.json").read_text(encoding="utf-8"))
    rank = int(os.environ["AMPI_RANK"])
    job_root = os.environ.get("AMPI_ROOT") or str(work_dir / "job")
    amp = Ampi(job_root, rank=rank, device=os.environ.get("AMPI_DEVICE") or cfg.device)
    try:
        ex = (stub_executor(cfg.languages) if cfg.executor == "stub"
              else model_executor(amp, cfg, work_dir / "calls"))
        report = rank_main(amp, rank, cfg, plan, ex, out_dir=work_dir / "out")
        (work_dir / "out" / f"report{rank}.json").write_text(
            json.dumps(report, indent=1, default=str), encoding="utf-8")
    except AmpiError as exc:
        amp.trace("rank.error", rank=rank, error=exc.cls_name, message=str(exc)[:300])
        print(f"[e8] rank {rank}: {exc.cls_name}: {exc}", file=sys.stderr)
        return 1
    finally:
        amp.close()
    return 0


def _rank_states(amp: Ampi) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in range(amp.size):
        try:
            out[str(r)] = amp._rankview(r).state  # noqa: SLF001 - the driver's view
        except Exception:  # noqa: BLE001
            out[str(r)] = "unknown"
    return out


def _wait_for_population(job_root: Path, timeout: float) -> bool:
    amp = Ampi(str(job_root), allow_volatile=True)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            if all(s in ("finalised", "failed", "fenced") for s in _rank_states(amp).values()):
                return True
            time.sleep(10)
        return False
    finally:
        amp.close()


def cmd_run(a: argparse.Namespace) -> dict[str, Any]:
    cfg = Config(
        name=a.name, size=a.size, languages=[c for c in a.languages.split(",") if c],
        executor=a.executor, model=a.model, arbiter_model=a.arbiter_model, reasoning=a.reasoning,
        fallback_model=a.fallback_model, device=a.device, task_timeout=a.task_timeout,
        phase_timeout=a.phase_timeout, quorum=a.quorum, algorithm=a.algorithm,
        die_fraction=a.die_fraction, respawn=a.respawn, ctx_budget=a.ctx_budget, lease_s=a.lease,
        first_page=a.first_page, last_page=a.last_page, steal=not a.no_steal, nodes=a.nodes,
        remote=a.remote or "", branch=a.branch or "", run_dir=a.run_dir or "",
        work_dir=a.work_dir or "",
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

    plan = build_plan(ROOT / "work" / "e7", cfg, source_dir=a.source_dir)
    (work_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    cfg.save(run_dir / "config.json")
    if a.node == 0:
        (run_dir / "corpus_manifest.json").write_text(json.dumps(plan["meta"], indent=2,
                                                                 ensure_ascii=False),
                                                      encoding="utf-8")
        (run_dir / "launch_plan.json").write_text(json.dumps({
            "experiment": "e8_adaptive_book", "name": cfg.name, "size": cfg.size,
            "nodes": cfg.nodes, "launch": a.launch, "device": cfg.device, "executor": cfg.executor,
            "model": cfg.model, "model_pool": [m.strip() for m in cfg.model.split(",") if m.strip()],
            "reasoning": cfg.reasoning, "fallback_model": cfg.fallback_model,
            "languages": cfg.languages, "quorum": cfg.quorum, "steal": cfg.steal,
            "die_fraction": cfg.die_fraction, "respawn": cfg.respawn, "job_root": str(job_root),
            "requested_ranks": list(range(cfg.size)), "created_at": time.time(),
            "ranks": plan["meta"]["blocks"]}, indent=2), encoding="utf-8")

    started = time.time()
    launch_record: dict[str, Any] = {}
    population_complete = True
    if a.launch == "threads":
        h = Harness(root=str(job_root), size=cfg.size, device=cfg.device, force=True,
                    ctx_budget=cfg.ctx_budget,
                    meta={"experiment": "e8_adaptive_book", "executor": cfg.executor,
                          "model": cfg.model})
        job = h.create()

        def main(amp: Ampi, rank: int) -> Any:
            ex = (stub_executor(cfg.languages) if cfg.executor == "stub"
                  else model_executor(amp, cfg, work_dir / "calls"))
            return rank_main(amp, rank, cfg, plan, ex, out_dir=work_dir / "out")

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

        cmd = [sys.executable, "-m", "experiments.e8_adaptive_book.harness", "rank",
               "--name", cfg.name]
        if cfg.run_dir:
            cmd += ["--run-dir", cfg.run_dir]
        launch_record = launch(
            cmd, size=cfg.size, root=job_root, device=cfg.device, nodes=cfg.nodes, node=a.node,
            ctx_budget=cfg.ctx_budget, join_deadline_s=cfg.phase_timeout,
            meta={"experiment": "e8_adaptive_book", "executor": cfg.executor, "model": cfg.model,
                  "name": cfg.name},
            log_dir=work_dir / "launch", env=env, respawn=cfg.respawn,
            timeout_s=cfg.phase_timeout * 4, worker_prefix="e8", quiet=a.quiet,
            create=False if getattr(a, "rejoin", False) else None,
        )
        if a.node == 0 and cfg.nodes > 1:
            population_complete = _wait_for_population(job_root, cfg.phase_timeout)
        if a.node == 0:
            export(job_root, run_dir, name=cfg.name)

    summary: dict[str, Any] = {"name": cfg.name, "size": cfg.size, "node": a.node,
                               "wall_s": round(time.time() - started, 2),
                               "population_complete": population_complete}
    if a.node == 0:
        amp = Ampi(str(job_root), allow_volatile=True)
        try:
            summary["book"] = assemble(amp, cfg.languages, work_dir / "out")
            summary["evidence"] = promote_evidence(amp, run_dir, cfg.languages)
            summary["rank_states"] = _rank_states(amp)
            segs = _win_values(amp, BOOK_WIN, prefix="seg/")
            summary["pages"] = {"total": len(plan["pages"]), "translated": len(segs),
                                "stolen": sum(1 for v in segs.values()
                                              if isinstance(v, dict) and v.get("stolen")),
                                "seam_revised": sum(1 for v in segs.values()
                                                    if isinstance(v, dict) and
                                                    v.get("seam_revised_by") is not None)}
            try:
                summary["pool"] = amp.pool_status(POOL)
            except Exception as exc:  # noqa: BLE001 - a run that never reached the pool
                summary["pool"] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
        finally:
            amp.close()
        summary["launch"] = {k: v for k, v in launch_record.items()
                             if k not in ("events", "command", "node_identity")}
        reports = []
        for p in sorted((work_dir / "out").glob("report*.json")):
            try:
                reports.append(json.loads(p.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
        summary["ranks_reported_here"] = len(reports)
        summary["ranks_finalised"] = sum(1 for s in summary["rank_states"].values()
                                         if s == "finalised")
        summary["spend_total_usd"] = max((r.get("spend_total_usd", 0) for r in reports), default=0)
        summary["units_missing"] = int((summary.get("book") or {}).get("missing", 0))
        (run_dir / "report.json").write_text(json.dumps(summary, indent=2, default=str),
                                             encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    return summary


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="e8", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the experiment (driver)")
    r.add_argument("--name", required=True)
    r.add_argument("--size", type=int, required=True)
    r.add_argument("--languages", default=",".join(DEFAULT_LANGUAGES))
    r.add_argument("--executor", choices=["stub", "model"], default="stub")
    r.add_argument("--model", default="moonshotai/kimi-k3",
                   help="one model, or a comma-separated pool assigned by rank")
    r.add_argument("--arbiter-model", default="")
    r.add_argument("--reasoning", default="low")
    r.add_argument("--fallback-model", default="deepseek/deepseek-v4-pro-0813")
    r.add_argument("--device", default="sqlite")
    r.add_argument("--launch", choices=["threads", "procs"], default="procs")
    r.add_argument("--nodes", type=int, default=1)
    r.add_argument("--node", type=int, default=0)
    r.add_argument("--rejoin", action="store_true", help="re-enter a job that exists on the branch")
    r.add_argument("--remote", default=None)
    r.add_argument("--branch", default=None)
    r.add_argument("--task-timeout", type=float, default=1800.0)
    r.add_argument("--phase-timeout", type=float, default=7200.0)
    r.add_argument("--lease", type=float, default=1800.0)
    r.add_argument("--quorum", type=float, default=1.0)
    r.add_argument("--algorithm", default=None)
    r.add_argument("--die-fraction", type=float, default=0.0)
    r.add_argument("--respawn", type=int, default=0)
    r.add_argument("--ctx-budget", type=int, default=200000)
    r.add_argument("--first-page", type=int, default=corpus_mod.FIRST_PROSE_PAGE)
    r.add_argument("--last-page", type=int, default=None)
    r.add_argument("--no-steal", action="store_true", help="ranks take only their home block")
    r.add_argument("--source-dir", default=None)
    r.add_argument("--run-dir", default=None)
    r.add_argument("--work-dir", default=None)
    r.add_argument("-q", "--quiet", action="store_true")
    k = sub.add_parser("rank", help="one rank process (started by the launcher)")
    k.add_argument("--name", required=True)
    k.add_argument("--run-dir", default=None)
    return ap


def main(argv: list[str] | None = None) -> Any:
    a = build_parser().parse_args(argv)
    if a.cmd == "run":
        return cmd_run(a)
    return cmd_rank(a)


if __name__ == "__main__":
    out = main()
    sys.exit(out if isinstance(out, int) else 0)
