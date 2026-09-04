"""E6: the book-translation harness, written against AgentMPI.

One function, ``BookHarness.rank_main``, is executed once per rank.  Every
AgentMPI call is made by that function; the agent is invoked as a kernel that
turns a prompt into an artifact.  The phases, and the mechanism each one is a
test of:

    launch     barrier                  everyone arrived; policy names the rest
    0          bcast                    the commission: book, languages, digests, segments
    1          scatter                  each rank its contiguous pages, self-identifying
    2          agent: survey            the terms a translator must decide once
               win_lock + put           a registry of chapter titles and conventions
                                        (read-modify-write under an exclusive lock)
    3          allreduce(union)         the term census; disagreements lifted, not merged
               gather                   term metadata to the root, by handle
               agent: arbitrate         the root settles every lifted conflict, once
               op_arbitrate + bcast     the settled census and the research agenda
    4          put                      the agenda's claim cells, ordered by the bcast
               compare_and_swap         each term researched by exactly one rank
               agent: research          web-grounded, one term per task
               win_fence                the research epoch closes: writes visible
    5          allreduce(union) + bcast the binding glossary (an invariant: no conflicts)
    6          exscan(sum)              each rank's offset in the book
    7          agent: translate         one page per task, under the glossary
               put                      the draft, into the shared window
               failure detection +      pages of a convicted rank are claimed by
               compare_and_swap         survivors and translated in their place
    8          win_fence                drafts visible (a superstep boundary)
               cart_shift + get         a review ring: each rank reviews its right neighbour
               agent: review            issues, a verdict
               win_fence                reviews visible
               agent: revise            the author revises its own pages
    9          neighbor_allgather       boundary sentences exchanged on the ring
               agent: seam              each rank revises only its own edges
    10         win_fence + gather       the manifest; the root assembles
               barrier                  done

An executor's death is a rank's death: a task nobody claims within the claim
window makes the harness kill its own rank, so its peers drop it at their next
collective instead of waiting a phase timeout for it, and its pages are stolen.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ampi import Ampi
from ampi.constants import PROC_NULL
from ampi.core.context import Ledger
from ampi.core.payload import Contract
from ampi.errors import AmpiError
from ampi.executor import Task, new_aid

from . import corpus as corpus_mod
from .executors import edge_sentences, validate_page
from .prompts import (
    ARBITRATE_CONTRACT,
    RESEARCH_CONTRACT,
    REVIEW_CONTRACT,
    SEAM_CONTRACT,
    SURVEY_CONTRACT,
    TRANSLATE_CONTRACT,
    arbitrate_prompt,
    fix_prompt,
    research_prompt,
    review_prompt,
    revise_prompt,
    seam_prompt,
    survey_prompt,
    translate_prompt,
)

__all__ = ["Config", "BookHarness", "TaskFailed", "run_one", "assemble_cells", "PAGES_WIN", "MEMO_WIN",
           "RESEARCH_WIN", "REGISTRY_WIN"]

PAGES_WIN = "pages"
RESEARCH_WIN = "research"
REGISTRY_WIN = "registry"
RING = "ring"
#: Where a rank records each task's result so that a restart replays instead
#: of redoing it.  Keyed ``{rank}/{label}``; pages are not memoised here
#: because the page window already holds them.
MEMO_WIN = "memo"


@dataclass
class Config:
    """Everything a rank needs to know, carried in the job manifest.

    A rank on a fresh machine reads this from the job rather than from its
    command line, so that sixty-four machines cannot be launched with sixty-four
    slightly different ideas of the experiment.
    """

    name: str
    size: int
    corpus: str = "chairs"
    languages: list[str] = field(default_factory=lambda: ["en", "zh", "ja"])
    arm: str = "full"
    phase_timeout_s: float = 10800.0
    task_timeout_s: float = 2400.0
    claim_ttl_s: float = 1800.0
    #: Seconds a published task may sit unclaimed before the rank concludes its
    #: executor is gone and fails itself.
    claim_wait_s: float = 1200.0
    lease_s: float = 1800.0
    quorum: float = 1.0
    barrier_policy: str = "proceed"
    research_cap: int = 48
    research_budget: int = 0
    arbitrate_cap: int = 80
    review_cap: int = 3
    retries: int = 2
    review: bool = True
    seam: bool = True
    ctx_budget: int = 300_000
    context_chars: int = 500
    #: A page subset such as "13-16", for smoke tests; empty means the whole book.
    pages: str = ""
    #: Record every task result in a window and replay it on restart.  A machine
    #: that is paused and resumed re-runs its rank program from the top; with
    #: the memo it re-runs it in seconds, through collectives that are already
    #: complete, and pays for no task twice.
    memo: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class TaskFailed(Exception):
    """An agent task that could not be brought to a conforming result."""

    def __init__(self, label: str, violations: list[str]) -> None:
        super().__init__(f"{label}: {'; '.join(violations[:3])}")
        self.label = label
        self.violations = violations


def slug(term: str) -> str:
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "-", term).strip("-").lower()[:24]
    return f"{ascii_part or 'term'}-{hashlib.sha1(term.encode('utf-8')).hexdigest()[:8]}"


def rotate(items: list[Any], rank: int, size: int) -> list[Any]:
    """Rotate the agenda per rank so ranks do not all contend on the same item.

    Every rank scanning in the same order makes the claim loop a thundering
    herd: p ranks swap the same cell, one wins, and the rest have each burned a
    device round trip to learn they lost.  Rotating makes the common case an
    uncontended claim.
    """
    if not items:
        return []
    start = (rank * max(1, len(items) // max(1, size))) % len(items)
    return items[start:] + items[:start]


class BookHarness:
    def __init__(self, cfg: Config, corpus: corpus_mod.Corpus, executor: Any,
                 work_dir: str | Path) -> None:
        self.cfg = cfg
        self.corpus = corpus
        self.executor = executor
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._last_heartbeat: dict[int, float] = {}

    # -- corpus views ------------------------------------------------------
    def page_payload(self, n: int, registry: dict[str, Any] | None = None) -> dict[str, Any]:
        p = self.corpus.pages[n]
        title = p.chapter_title
        if registry:
            found = (registry.get("chapter_titles") or {}).get(str(p.chapter_first_page))
            if found and found not in title:
                title = f"{found} ({p.chapter_title.split('(')[-1].rstrip(')')})" \
                    if "(" in p.chapter_title else found
        return {"page": n, "chapter": p.chapter, "chapter_title": title,
                "page_type": p.page_type, "text": p.text, "chars": p.chars}

    def context(self, n: int, *, before: bool) -> str:
        limit = self.cfg.context_chars
        keys = sorted(self.corpus.pages)
        i = keys.index(n)
        j = i - 1 if before else i + 1
        if j < 0 or j >= len(keys):
            return ""
        text = self.corpus.pages[keys[j]].text
        return text[-limit:] if before else text[:limit]

    def commission(self) -> dict[str, Any]:
        return {
            "corpus": self.corpus.source,
            "book": self.corpus.title, "author": self.corpus.author,
            "languages": self.cfg.languages, "arm": self.cfg.arm,
            "segments": [list(s) for s in self.corpus.segments],
            "digests": {str(n): p.digest for n, p in self.corpus.pages.items()},
            "source_commit": self.corpus.origin_commit,
            "rules": ["the binding glossary overrides local preference",
                      "translate every sentence; never summarise",
                      "one JSON file per page in the legacy schema"],
        }

    # -- the one agent call -------------------------------------------------
    def invoke(
        self,
        amp: Ampi,
        rank: int,
        label: str,
        prompt: str,
        contract: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
        validate: Callable[[Any], list[str]] | None = None,
        retries: int | None = None,
    ) -> Any:
        """One agent task, retried on a non-conforming result, fatal on a lost executor.

        The lease is not extended here for the whole step: the executor keeps it
        alive from inside its wait, once a minute, so a machine that dies
        mid-step is convicted within minutes rather than after the step's worth
        of lease.
        """
        retries = self.cfg.retries if retries is None else retries
        memo_key = f"{rank}/{label}"
        if self.cfg.memo:
            cell = self._cell(amp, MEMO_WIN, memo_key)
            if cell is not None:
                amp.trace("task.replayed", rank=rank, label=label)
                return cell
        parsed = Contract.parse(contract)
        base_meta = {"claim_ttl_s": self.cfg.claim_ttl_s, "claim_wait_s": self.cfg.claim_wait_s,
                     **(meta or {})}
        attempt = 0
        current = prompt
        t0 = time.time()
        while True:
            aid = new_aid()
            task = Task(aid=aid, rank=rank, label=label if attempt == 0 else f"fix:{label}",
                        prompt=current, contract=parsed, meta=base_meta)
            self._keep_alive(amp, rank)
            try:
                result = self.executor.invoke(task)
            except AmpiError as exc:
                if exc.cls_name == "AMPI_ERR_NO_WORKER":
                    # The executor is gone.  Say so, fail the rank so that its
                    # peers stop waiting for it, and let the exception end the
                    # rank's thread.  Its pages are now the survivors' to steal.
                    amp.trace("executor.lost", rank=rank, label=label, aid=aid)
                    amp.kill(rank, reason=f"executor lost: nobody claimed {label}")
                    raise
                if exc.cls_name in ("AMPI_ERR_OP_FAILED", "AMPI_ERR_TIMEOUT") and attempt < retries:
                    attempt += 1
                    amp.trace("task.retry", rank=rank, label=label, aid=aid, why=exc.cls_name)
                    continue
                raise
            except RuntimeError as exc:
                if attempt < retries:
                    attempt += 1
                    amp.trace("task.retry", rank=rank, label=label, aid=aid, why=str(exc)[:120])
                    continue
                raise TaskFailed(label, [str(exc)]) from exc
            violations = validate(result) if validate else []
            if not violations:
                amp.trace("task.done", rank=rank, label=label, aid=aid, attempts=attempt + 1,
                          seconds=round(time.time() - t0, 1))
                if self.cfg.memo and not memo_key.split("/", 1)[1].startswith(("translate:", "revise:")):
                    amp.put(MEMO_WIN, memo_key, result)
                return result
            amp.trace("task.invalid", rank=rank, label=label, aid=aid, violations=violations[:6])
            if attempt >= retries:
                raise TaskFailed(label, violations)
            attempt += 1
            current = fix_prompt(prompt, violations)

    def _keep_alive(self, amp: Ampi, rank: int) -> None:
        """Renew the lease before a step, but only when it is worth a write.

        On a transport where a write is a network round trip, a heartbeat before
        every one of a rank's dozens of tasks is dozens of round trips that buy
        nothing the executor's once-a-minute keepalive does not already buy.
        """
        now = time.time()
        last = self._last_heartbeat.get(rank, 0.0)
        if now - last >= self.cfg.lease_s / 3:
            amp.heartbeat(extend=self.cfg.lease_s)
            self._last_heartbeat[rank] = now

    # -- bulk window reads, without charging the ledger -----------------------
    @staticmethod
    def _cell(amp: Ampi, win: str, key: str) -> Any:
        """One cell's value straight from the device, or None; not charged."""
        cell = amp.device.read(amp._space(win), key)
        return None if cell is None else cell.value

    @staticmethod
    def _cells(amp: Ampi, win: str, prefix: str) -> dict[str, Any]:
        """Read every cell under a prefix straight from the device.

        Harness bookkeeping, not an agent's context: the root assembling a
        hundred pages is not a model reading them, so charging the ledger for it
        would make the context measurement report something that never entered a
        context.  Agent-facing reads (a reviewer's draft) go through ``get`` and
        are charged.
        """
        space = amp._space(win)
        out: dict[str, Any] = {}
        for c in amp.device.keys(space, prefix=prefix):
            cell = amp.device.read(space, c.key)
            if cell is not None:
                out[c.key] = cell.value
        return out

    # -- phase helpers ----------------------------------------------------------
    def register(self, amp: Ampi, rank: int, survey: dict[str, Any]) -> None:
        """Merge this rank's conventions and chapter titles into a shared registry.

        A read-modify-write on one cell, which is what a lock is for.  A
        compare-and-swap loop would do the same job without a lease to expire,
        and on a contended cell would do it with p^2 device round trips; the
        lock serialises the p writers at p round trips plus the waits, and the
        waits are what the trace measures.
        """
        conventions = [str(c) for c in (survey.get("conventions") or [])][:3]
        titles = {str(k): str(v) for k, v in (survey.get("chapter_titles") or {}).items()}
        if not conventions and not titles:
            return
        have = self._cell(amp, REGISTRY_WIN, "registry")
        if isinstance(have, dict) and rank in (have.get("writers") or []):
            amp.trace("registry.replayed", rank=rank)
            return
        # The lease outlives a slow transport: sixteen writers replaying through
        # this step at once on the git device put a read-modify-write well past
        # the two minutes the first run allowed, and a lease that lapses after
        # the write has landed is not a failure of the write.
        lock = amp.win_lock(REGISTRY_WIN, "registry", ttl=600.0, timeout=self.cfg.phase_timeout_s)
        try:
            cur = amp.get(REGISTRY_WIN, "registry")
            value = (cur.get("value") if cur.get("present") else None) or \
                {"conventions": [], "chapter_titles": {}, "writers": []}
            value["conventions"].extend(f"{c} [rank {rank}]" for c in conventions)
            for page, title in titles.items():
                value["chapter_titles"].setdefault(page, title)
            value["writers"].append(rank)
            # Conditional on the version just read: under the lock nobody else can
            # have written, so a mismatch would mean the lease was broken, and the
            # write is then rejected rather than recorded as a stale overwrite.
            amp.put(REGISTRY_WIN, "registry", value, lock_token=lock["token"],
                    expect_version=cur.get("version") if cur.get("present") else None)
        finally:
            try:
                amp.win_unlock(lock["lock_id"])
            except AmpiError as exc:
                if exc.cls_name != "AMPI_ERR_STALE_LEASE":
                    raise
                # The write either landed under a live lease or was rejected
                # by its token; a lease that lapsed afterwards leaves nothing
                # to release.
                amp.trace("lock.lapsed", rank=rank, win=REGISTRY_WIN, lock=lock["lock_id"])

    def registry(self, amp: Ampi) -> dict[str, Any]:
        cells = self._cells(amp, REGISTRY_WIN, "registry")
        return cells.get("registry") or {"conventions": [], "chapter_titles": {}}

    def agenda(self, meta_all: dict[str, dict[str, Any]], settled: dict[str, Any],
               conflicts: dict[str, list[Any]]) -> list[dict[str, Any]]:
        """Turn the arbitrated census into a research agenda, contested terms first.

        The conflict set is not merely reported, it is *used*: a term two ranks
        rendered differently is exactly the one where inconsistency would be
        visible to a reader, and exactly the one a single translator would never
        have noticed was contentious.  Research is aimed there first.
        """
        contested = set(conflicts)
        ordered = sorted(
            (t for t, m in meta_all.items() if m.get("needs_research") or t in contested),
            key=lambda t: (t not in contested, t),
        )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for term in ordered:
            key = slug(term)
            if key in seen:
                continue
            seen.add(key)
            m = meta_all[term]
            out.append({"key": key, "term": term, "kind": m.get("kind", ""),
                        "gloss": m.get("gloss", ""), "why_hard": m.get("why_hard", ""),
                        "contested": term in contested,
                        "proposed": settled.get(term, m.get("proposed", {}))})
            if self.cfg.research_cap and len(out) >= self.cfg.research_cap:
                break
        return out

    def translate_page(self, amp: Ampi, rank: int, n: int, glossary: dict[str, Any],
                       conventions: list[str], registry: dict[str, Any],
                       *, stolen_from: int | None = None) -> dict[str, Any] | None:
        page = self.page_payload(n, registry)
        L = self.cfg.languages
        contract = {**TRANSLATE_CONTRACT, "expect": {"page": str(n)}}
        have = self._cell(amp, PAGES_WIN, f"page/{n}")
        if isinstance(have, dict) and isinstance(have.get("data"), dict):
            # Already translated, by this rank before it was restarted or by a
            # survivor that stole it while this rank was away.  Either way the
            # page is done and the book needs one translation of it, not two.
            amp.trace("page.replayed", rank=rank, page=n, by=have.get("by"),
                      revision=have.get("revision"))
            return have["data"]
        if self._cell(amp, PAGES_WIN, f"failed/{n}") is not None:
            return None
        try:
            result = self.invoke(
                amp, rank, f"translate:{n}",
                translate_prompt(rank, page, L, glossary, conventions,
                                 self.context(n, before=True), self.context(n, before=False),
                                 self.corpus.brief),
                contract, meta={"page": n},
                validate=lambda r: validate_page(r, page, L),
            )
        except TaskFailed as exc:
            amp.put(PAGES_WIN, f"failed/{n}", {"page": n, "by": rank, "violations": exc.violations[:6]})
            amp.trace("page.failed", rank=rank, page=n, violations=exc.violations[:3])
            return None
        amp.put(PAGES_WIN, f"page/{n}", {"page": n, "by": rank, "revision": 1,
                                         "stolen_from": stolen_from, "data": result})
        amp.trace("page.done", rank=rank, page=n, sentences=len(result.get("sentences", [])),
                  stolen_from=stolen_from)
        return result

    def steal(self, amp: Ampi, rank: int, my_pages: list[int], segments: list[list[int]],
              glossary: dict[str, Any], conventions: list[str], registry: dict[str, Any]) -> dict[int, dict[str, Any]]:
        """Translate the untranslated pages of ranks the population has convicted.

        The claim is a compare-and-swap on a cell that does not yet exist, so two
        survivors that notice the same death cannot both take the same page, and
        a survivor that dies mid-steal leaves a claim its own conviction makes
        stealable in turn.
        """
        amp.detect_failures()
        out: dict[int, dict[str, Any]] = {}
        done = self._cells(amp, PAGES_WIN, "page/")
        failed = self._cells(amp, PAGES_WIN, "failed/")
        for view in sorted(amp.failed_ranks(), key=lambda v: v.rank):
            victim = view.rank
            if victim >= len(segments):
                continue
            for n in segments[victim]:
                if n in my_pages or f"page/{n}" in done or f"failed/{n}" in failed:
                    continue
                got = amp.compare_and_swap(PAGES_WIN, f"steal/{n}", None,
                                           {"by": rank, "victim": victim, "at": amp.device.clock()})
                if not got["swapped"]:
                    continue
                amp.trace("page.steal", rank=rank, page=n, victim=victim)
                result = self.translate_page(amp, rank, n, glossary, conventions, registry,
                                             stolen_from=victim)
                if result is not None:
                    out[n] = result
        return out

    def assemble(self, amp: Ampi) -> dict[str, Any]:
        cells = self._cells(amp, PAGES_WIN, "")
        return assemble_cells(cells, self.work_dir / "out", self.corpus)

    # -- the rank program -------------------------------------------------------
    def rank_main(self, amp: Ampi, rank: int) -> dict[str, Any]:  # noqa: C901 - one program, in order
        cfg = self.cfg
        T = cfg.phase_timeout_s
        q = cfg.quorum
        L = cfg.languages
        pol = cfg.barrier_policy
        report: dict[str, Any] = {"rank": rank, "arm": cfg.arm}

        # -- launch ---------------------------------------------------------
        amp.memo("phase", "launch")
        if rank == 0:
            for w in (PAGES_WIN, RESEARCH_WIN, REGISTRY_WIN, MEMO_WIN):
                amp.win_create(w)
        amp.barrier("launch", quorum=q, timeout=T, policy=pol)

        # -- 0. the commission ------------------------------------------------
        if rank == 0:
            commission = self.commission()
            amp.bcast("commission", payload=commission, root=0, timeout=T)
        else:
            commission = amp.bcast("commission", root=0, timeout=T, materialize=True)["body"]
        segments: list[list[int]] = [list(s) for s in commission["segments"]]
        if commission.get("source_commit") and self.corpus.origin_commit and \
                commission["source_commit"] != self.corpus.origin_commit:
            amp.trace("corpus.mismatch", rank=rank, mine=self.corpus.origin_commit,
                      root=commission["source_commit"])

        # -- 1. scatter the segments ------------------------------------------
        slices = [{"rank": r, "pages": seg} for r, seg in enumerate(segments)] if rank == 0 else None
        mine = amp.scatter("segments", payload=slices, root=0, timeout=T,
                           contract={"kind": "json", "expect": {"rank": "{rank}"}})["body"]
        my_pages = [int(n) for n in mine["pages"]]
        report["pages"] = my_pages
        for n in my_pages:
            if self.corpus.pages[n].digest != commission["digests"].get(str(n)):
                amp.trace("corpus.mismatch", rank=rank, page=n)

        # -- 2. survey ----------------------------------------------------------
        amp.memo("phase", "survey")
        try:
            survey = self.invoke(
                amp, rank, "survey",
                survey_prompt(rank, [self.page_payload(n) for n in my_pages], L,
                              self.corpus.seed_glossary, self.corpus.brief),
                SURVEY_CONTRACT, meta={"pages": my_pages},
            )
        except TaskFailed as exc:
            amp.trace("survey.failed", rank=rank, violations=exc.violations[:3])
            survey = {"terms": [], "chapter_titles": {}, "conventions": []}
        terms = {t["term"]: t for t in survey.get("terms", [])
                 if isinstance(t, dict) and t.get("term")}
        report["terms"] = len(terms)
        self.register(amp, rank, survey)

        glossary: dict[str, Any] = {}
        conventions: list[str] = []
        agenda: list[dict[str, Any]] = []
        settled: dict[str, Any] = {}
        if cfg.arm != "noglossary":
            # -- 3. the census -------------------------------------------------
            # `union` lifts a disagreement into the conflict set rather than
            # letting whichever branch merged last decide it, and the conflict
            # set arriving at the root is the same for every tree shape.
            amp.memo("phase", "census")
            census = amp.allreduce(
                "census", payload={t: m.get("proposed", {}) for t, m in terms.items()},
                op="union", quorum=q, timeout=T,
            )
            conflicts: dict[str, list[Any]] = census.get("conflicts") or {}
            report["census_conflicts"] = len(conflicts)
            meta_mine = {t: {k: m.get(k) for k in ("kind", "gloss", "why_hard", "needs_research")}
                         for t, m in terms.items()}
            got = amp.gather("term-meta", payload=meta_mine, root=0, quorum=q, timeout=T,
                             materialize=True)
            if rank == 0:
                meta_all: dict[str, dict[str, Any]] = {}
                for b in got.get("bodies", []):
                    body = b.get("body") if isinstance(b, dict) else None
                    if isinstance(body, dict):
                        for t, m in body.items():
                            meta_all.setdefault(t, {}).update({k: v for k, v in m.items() if v})
                if conflicts:
                    capped = dict(list(conflicts.items())[:cfg.arbitrate_cap])
                    rulings: dict[str, Any] = {}
                    try:
                        arb = self.invoke(amp, 0, "arbitrate:census",
                                          arbitrate_prompt(capped, meta_all, L, self.corpus.brief),
                                          ARBITRATE_CONTRACT, meta={"conflicts": capped})
                        rulings = {k: v for k, v in (arb.get("rulings") or {}).items()
                                   if k in conflicts and isinstance(v, dict)}
                    except TaskFailed as exc:
                        amp.trace("arbitrate.failed", rank=0, violations=exc.violations[:3])
                    # Every lifted conflict gets exactly one ruling; the ones the
                    # arbiter did not reach fall back to the runtime's modal choice.
                    for k, cands in conflicts.items():
                        rulings.setdefault(k, cands[0] if cands else {})
                    report["arbitrated"] = len(rulings)
                    settled = amp.op_arbitrate("census", rulings=rulings)["value"]
                else:
                    settled = census.get("value", {}) or {}
                settled = {k: v for k, v in settled.items() if not k.startswith("__")}
                agenda = self.agenda(meta_all, settled, conflicts)
                # The claim cells are posted before the agenda is broadcast, so
                # the broadcast itself orders them: no rank can learn of an item
                # before its cell exists, and no barrier is needed to say so.
                if cfg.arm != "noresearch":
                    for item in agenda:
                        amp.put(RESEARCH_WIN, f"claim/{item['key']}", "unclaimed")
                amp.bcast("agenda", payload={"agenda": agenda, "settled": settled},
                          root=0, timeout=T)
            else:
                body = amp.bcast("agenda", root=0, timeout=T, materialize=True)["body"]
                agenda, settled = body["agenda"], body["settled"]
            report["agenda"] = len(agenda)

            # -- 4. research under mutual exclusion ------------------------------
            findings: dict[str, Any] = {}
            if cfg.arm != "noresearch" and agenda:
                amp.memo("phase", "research")
                live = max(1, len(amp.live_ranks()))
                budget = cfg.research_budget or (math.ceil(len(agenda) / live) + 1)
                done = 0
                attempts = 0
                for item in rotate(agenda, rank, amp.size):
                    if done >= budget:
                        break
                    attempts += 1
                    # Compare-and-swap, not a lock: a claim taken by a rank whose
                    # machine then dies must not wedge the term behind a lease.
                    got_claim = amp.claim(RESEARCH_WIN, f"claim/{item['key']}")
                    holder = got_claim.get("holder")
                    mine = isinstance(holder, dict) and holder.get("claimed_by") == rank
                    if not got_claim["claimed"] and not mine:
                        continue
                    try:
                        finding = self.invoke(amp, rank, f"research:{item['key']}",
                                              research_prompt(rank, item, L, self.corpus.brief),
                                              RESEARCH_CONTRACT,
                                              meta={"term": item["term"]})
                    except TaskFailed as exc:
                        amp.put(RESEARCH_WIN, f"finding/{item['key']}",
                                {"term": item["term"], "failed": True,
                                 "violations": exc.violations[:3]})
                        continue
                    finding["term"] = item["term"]
                    finding["by"] = rank
                    amp.put(RESEARCH_WIN, f"finding/{item['key']}", finding)
                    done += 1
                report.update(researched=done, claim_attempts=attempts)
                # A window fence, not a bare barrier: the epoch's writes are
                # guaranteed visible, which turns a blackboard into supersteps.
                amp.win_fence(RESEARCH_WIN, "research-done", quorum=q, timeout=T)
                for body in self._cells(amp, RESEARCH_WIN, "finding/").values():
                    if isinstance(body, dict) and body.get("term") and not body.get("failed") \
                            and isinstance(body.get("rendering"), dict):
                        findings[body["term"]] = body["rendering"]

            # -- 5. the binding glossary ---------------------------------------
            # Every rank contributes the settled census overlaid with the
            # findings it can see.  The contributions are identical, so the
            # reduction must lift nothing: a conflict here is a bug, and the
            # trace records the count so the invariant is checkable.
            amp.memo("phase", "glossary")
            contribution = {**settled, **findings}
            merged = amp.allreduce("glossary", payload=contribution, op="union",
                                   quorum=q, timeout=T)
            report["glossary_conflicts"] = len(merged.get("conflicts") or {})
            if rank == 0:
                bound = (amp.op_arbitrate("glossary")["value"] if merged.get("conflicts")
                         else merged.get("value", {}))
                bound = {k: v for k, v in bound.items() if not k.startswith("__")}
                amp.bcast("binding-glossary", payload=bound, root=0, timeout=T)
                glossary = bound
            else:
                glossary = amp.bcast("binding-glossary", root=0, timeout=T,
                                     materialize=True)["body"]
            report["glossary_terms"] = len(glossary)

        registry = self.registry(amp)
        conventions = list(registry.get("conventions") or [])[:12]

        # -- 6. offsets ----------------------------------------------------------
        # Walking the segments in order to compute each rank's offset would
        # serialise the one part of the job with no reason to be serial.
        chars = sum(self.corpus.pages[n].chars for n in my_pages)
        offset = amp.exscan("offsets", payload=chars, op="sum", quorum=q, timeout=T)
        report["offset"] = offset.get("value", 0)

        # -- 7. translate -----------------------------------------------------------
        amp.memo("phase", "translate")
        drafts: dict[int, dict[str, Any]] = {}
        for n in my_pages:
            r = self.translate_page(amp, rank, n, glossary, conventions, registry)
            if r is not None:
                drafts[n] = r
        stolen = self.steal(amp, rank, my_pages, segments, glossary, conventions, registry)
        drafts.update(stolen)
        report["stolen"] = sorted(stolen)
        provenance: dict[int, int] = {n: self.corpus.owner_of(n) for n in stolen}
        report["translated"] = sorted(drafts)

        # -- 8. review ring -------------------------------------------------------
        ring = None
        if amp.size > 1:
            ring = amp.cart_create([amp.size], periodic=[True], name=RING)["name"]
        if cfg.review and cfg.arm != "noreview" and ring is not None:
            amp.memo("phase", "review")
            amp.win_fence(PAGES_WIN, "drafts", quorum=q, timeout=T)
            shift = amp.cart_shift(ring, 0, 1)
            reviewee = shift.get("dest")
            reviewed: list[int] = []
            if reviewee not in (rank, PROC_NULL, None):
                for n in segments[reviewee][:cfg.review_cap]:
                    cell = amp.get(PAGES_WIN, f"page/{n}")
                    if not cell.get("present"):
                        continue
                    draft = (cell.get("value") or {}).get("data") or {}
                    try:
                        review = self.invoke(
                            amp, rank, f"review:{n}",
                            review_prompt(rank, self.page_payload(n, registry), draft, L, glossary,
                                          self.corpus.brief),
                            {**REVIEW_CONTRACT, "expect": {"page": str(n)}}, meta={"page": n},
                        )
                    except TaskFailed as exc:
                        amp.trace("review.failed", rank=rank, page=n, violations=exc.violations[:3])
                        continue
                    review["by"] = rank
                    amp.put(PAGES_WIN, f"review/{n}", review)
                    reviewed.append(n)
            report["reviewed"] = reviewed
            amp.win_fence(PAGES_WIN, "reviews", quorum=q, timeout=T)
            revised: list[int] = []
            for n in sorted(drafts):
                rv = amp.get(PAGES_WIN, f"review/{n}")
                if not rv.get("present") or (rv.get("value") or {}).get("verdict") != "revise":
                    continue
                have = self._cell(amp, PAGES_WIN, f"page/{n}")
                if isinstance(have, dict) and (have.get("revision") or 0) >= 2:
                    drafts[n] = have.get("data") or drafts[n]
                    revised.append(n)
                    amp.trace("page.replayed", rank=rank, page=n, revision=have.get("revision"))
                    continue
                page = self.page_payload(n, registry)
                try:
                    new = self.invoke(
                        amp, rank, f"revise:{n}",
                        revise_prompt(rank, page, drafts[n], rv["value"], L, glossary),
                        {**TRANSLATE_CONTRACT, "expect": {"page": str(n)}}, meta={"page": n},
                        validate=lambda r, page=page: validate_page(r, page, L),
                    )
                except TaskFailed as exc:
                    amp.trace("revise.failed", rank=rank, page=n, violations=exc.violations[:3])
                    continue
                drafts[n] = new
                amp.put(PAGES_WIN, f"page/{n}", {"page": n, "by": rank, "revision": 2,
                                                 "reviewed_by": rv["value"].get("by"),
                                                 "stolen_from": provenance.get(n), "data": new})
                revised.append(n)
            report["revised"] = revised
            late = self.steal(amp, rank, my_pages, segments, glossary, conventions, registry)
            if late:
                drafts.update(late)
                provenance.update({n: self.corpus.owner_of(n) for n in late})
                report["stolen"] = sorted(set(report["stolen"]) | set(late))

        # -- 9. seams -------------------------------------------------------------
        if cfg.seam and cfg.arm != "noseams" and ring is not None:
            amp.memo("phase", "seams")
            first, last = my_pages[0], my_pages[-1]
            edges = {
                "first_page": first,
                "head": edge_sentences(drafts[first], L, head=True) if first in drafts else [],
                "last_page": last,
                "tail": edge_sentences(drafts[last], L, head=False) if last in drafts else [],
            }
            halo = amp.neighbor_allgather("seams", payload={"rank": rank, **edges}, comm=ring,
                                          timeout=T, materialize=True)
            shift = amp.cart_shift(ring, 0, 1)
            left = right = None
            for nb in halo.get("neighbours", []):
                body = nb.get("body") if isinstance(nb, dict) else None
                if not isinstance(body, dict) or body.get("rank") == rank:
                    continue
                if body.get("rank") == shift.get("source"):
                    left = {"page": body.get("last_page"), "tail": body.get("tail")}
                if body.get("rank") == shift.get("dest"):
                    right = {"page": body.get("first_page"), "head": body.get("head")}
            if (left or right) and (edges["head"] or edges["tail"]):
                try:
                    seam = self.invoke(amp, rank, "seam",
                                       seam_prompt(rank, edges, left, right, L), SEAM_CONTRACT,
                                       meta={"head_ids": [s.get("id") for s in edges["head"]]})
                except TaskFailed as exc:
                    amp.trace("seam.failed", rank=rank, violations=exc.violations[:3])
                    seam = {"changed": False}
                changed = bool(seam.get("changed"))
                report["seam_changed"] = changed
                if changed:
                    for slot, n in (("head", first), ("tail", last)):
                        if n not in drafts:
                            continue
                        touched = self._apply_seam(drafts[n], (seam.get("revised") or {}).get(slot) or [], L)
                        if touched:
                            amp.put(PAGES_WIN, f"page/{n}",
                                    {"page": n, "by": rank, "revision": 3, "seam": True,
                                     "stolen_from": provenance.get(n), "data": drafts[n]})
                    amp.trace("seam.applied", rank=rank, pages=[first, last],
                              reason=str(seam.get("reason", ""))[:160])

        # -- 10. manifest and assembly ------------------------------------------------
        amp.memo("phase", "assemble")
        amp.win_fence(PAGES_WIN, "final", quorum=q, timeout=T)
        manifest = {"rank": rank, "pages": sorted(drafts),
                    "sentences": sum(len(d.get("sentences") or []) for d in drafts.values()),
                    "offset": report["offset"], "stolen": report.get("stolen", []),
                    "reviewed": report.get("reviewed", []), "revised": report.get("revised", [])}
        got = amp.gather("manifest", payload=manifest, root=0, quorum=q, timeout=T,
                         materialize=True)
        if rank == 0:
            book = self.assemble(amp)
            report["assembled"] = book["n_pages"]
            report["missing"] = book["missing"]
            report["contributors"] = got.get("contributors")
            amp.put(PAGES_WIN, "book/summary", {k: v for k, v in book.items() if k != "pages"})
        amp.memo("phase", "done")
        amp.barrier("done", quorum=q, timeout=T, policy=pol)
        return report

    @staticmethod
    def _apply_seam(draft: dict[str, Any], revised: list[dict[str, Any]], languages: list[str]) -> bool:
        by_id = {s.get("id"): s for s in draft.get("sentences") or [] if isinstance(s, dict)}
        touched = False
        for entry in revised:
            if not isinstance(entry, dict):
                continue
            target = by_id.get(entry.get("id"))
            if target is None:
                continue
            for c in languages:
                if isinstance(entry.get(c), str) and entry[c].strip() and entry[c] != target.get(c):
                    target[c] = entry[c]
                    touched = True
        return touched


# ---------------------------------------------------------------------------
# Assembly, from cells
# ---------------------------------------------------------------------------


def assemble_cells(cells: dict[str, Any], out_dir: str | Path, corpus: corpus_mod.Corpus | None = None,
                   *, expected: list[int] | None = None) -> dict[str, Any]:
    """Write one legacy-schema JSON file per page and a book-order JSONL.

    Takes the pages window's cells (key -> latest value) so it works both from a
    live job and from the sealed state of a job whose machines are gone.
    """
    out = Path(out_dir)
    (out / "pages").mkdir(parents=True, exist_ok=True)
    pages: dict[int, dict[str, Any]] = {}
    failed: dict[int, Any] = {}
    stolen: dict[int, int] = {}
    revisions: dict[int, int] = {}
    for key, value in cells.items():
        if key.startswith("page/") and isinstance(value, dict) and isinstance(value.get("data"), dict):
            n = int(key.split("/", 1)[1])
            pages[n] = value["data"]
            revisions[n] = int(value.get("revision") or 1)
            if value.get("stolen_from") is not None:
                stolen[n] = int(value["stolen_from"])
        elif key.startswith("failed/"):
            failed[int(key.split("/", 1)[1])] = value
    want = expected if expected is not None else (sorted(corpus.pages) if corpus else sorted(pages))
    with open(out / "book.jsonl", "w", encoding="utf-8") as fh:
        for n in sorted(pages):
            (out / "pages" / f"page_{n:03d}.json").write_text(
                json.dumps(pages[n], ensure_ascii=False, indent=2), encoding="utf-8")
            fh.write(json.dumps(pages[n], ensure_ascii=False) + "\n")
    missing = [n for n in want if n not in pages]
    summary = {
        "n_pages": len(pages), "expected": len(want), "missing": missing,
        "failed": sorted(failed), "stolen": stolen,
        "revised": sorted(n for n, r in revisions.items() if r >= 2),
        "sentences": sum(len(p.get("sentences") or []) for p in pages.values()),
        "out_dir": str(out),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


# ---------------------------------------------------------------------------
# Running one rank outside the threaded driver
# ---------------------------------------------------------------------------


def run_one(amp: Ampi, rank: int, harness: BookHarness, *, lease_s: float,
            identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute ``rank_main`` for one rank on this machine, recording the outcome.

    The same discipline as ``Harness.run``: an exception is a rank's result, not
    a crash, because a local failure must never silently remove a rank from a
    collective its peers are already blocked inside.  Here the rank *does* leave
    the population when it fails, but it leaves loudly: its lease lapses, its
    peers convict it, and the trace says why.
    """
    started = time.time()
    out: dict[str, Any] = {"rank": rank, "ok": False}
    try:
        joined = amp.init(lease_s=lease_s)
        if joined.get("already_running") or joined.get("recovery") is not None:
            # A restart.  The ledger models an executor's transcript, and this
            # process has a new one: the replay that follows re-delivers every
            # collective body the rank ever received, and charging those again
            # would degrade a rank that spent its budget honestly in its first
            # life into string views its program cannot use.  The release is
            # traced with what the previous life had used, so the measurement
            # loses nothing.
            view = amp._rankview()
            ledger = Ledger.from_dict(view.ctx)
            spent = ledger.used
            ledger.release(spent)
            view.ctx = ledger.to_dict()
            amp._write_rank(view)
            amp.trace("ctx.release", rank=rank, tokens=spent, why="restart",
                      epoch=joined.get("epoch"))
        if identity:
            amp.trace("rank.identity", rank=rank, **identity)
        value = harness.rank_main(amp, rank)
        amp.finalize(note="e6 done")
        out.update(ok=True, value=value)
    except AmpiError as exc:
        amp.trace("rank.error", rank=rank, error=exc.cls_name, message=exc.message)
        out.update(error=exc.message, error_class=exc.cls_name)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        with contextlib.suppress(Exception):  # the device itself may be gone
            amp.trace("rank.error", rank=rank, error=type(exc).__name__, message=str(exc)[:300])
        out.update(error=str(exc), error_class=type(exc).__name__)
    out["seconds"] = round(time.time() - started, 1)
    with contextlib.suppress(Exception):
        out["context_used"] = amp.ledger().used
    return out
