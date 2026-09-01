"""Production, vendor-neutral AgentMPI harness for the Durov translation corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import ampi
from ampi import Ampi
from ampi.core.payload import Contract, check_contract
from ampi.executor import BrokerExecutor, FunctionExecutor, Task, new_aid
from ampi.harness import Harness

HERE = Path(__file__).resolve().parent
SUPPORTED_RANKS = (16, 32, 64)
LANGUAGES = ("en", "zh", "ja")

RESEARCH_CONTRACT = {
    "kind": "json",
    "name": "durov-research",
    "required": ["rank", "terminology", "proposals"],
    "expect": {"rank": "{rank}"},
    "max_tokens": 2400,
    "semantics": "Cultural and terminology proposals, each supported by a URL and evidence.",
}
ARBITRATION_CONTRACT = {
    "kind": "json",
    "name": "durov-terminology-arbitration",
    "required": ["rank", "rulings", "reasons"],
    "expect": {"rank": "{rank}"},
    "max_tokens": 5000,
    "semantics": "One evidence-grounded ruling for every lifted terminology conflict.",
}
TRANSLATION_CONTRACT = {
    "kind": "json",
    "name": "durov-translation",
    "required": ["rank", "pages"],
    "nonempty": ["pages"],
    "expect": {"rank": "{rank}"},
    "max_tokens": 12000,
    "semantics": "One complete literary English, Simplified Chinese, and Japanese page.",
}
REVIEW_CONTRACT = {
    "kind": "json",
    "name": "durov-review",
    "required": ["rank", "target_rank", "critique", "revised_translation"],
    "nonempty": ["critique", "revised_translation"],
    "expect": {"rank": "{rank}"},
    "max_tokens": 14000,
    "semantics": "A substantive peer review and corrected translation of one page.",
}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _bounded_prompt(prompt: str, limit: int) -> str:
    if len(prompt) > limit:
        raise ValueError(
            f"self-contained prompt has {len(prompt)} characters, above --max-prompt-chars={limit}"
        )
    return prompt


def research_prompt(
    assignment: dict[str, Any],
    brief: dict[str, Any],
    rank: int,
    max_chars: int,
) -> str:
    prompt = f"""# Durov cultural and terminology research — rank {rank}

You are preparing a multilingual literary edition of Nikolai Kononov's Russian
book *Код Дурова*. Your assigned source pages are included below. Develop
terminology and cultural-context proposals that will improve their translation
into American English, Simplified Chinese, and standard Japanese.

Existing source-checkout research (reference material, not unquestionable fact):
{_dump(brief)}

Assigned Russian pages:
{_dump(assignment["pages"])}

Return only JSON:
{{"rank":{rank},"terminology":{{"<Russian term>":{{"en":"...","zh":"...","ja":"..."}}}},
"proposals":[{{"topic":"...","recommendation":"...","url":"https://...",
"evidence":"A specific fact or short quotation supported by that URL"}}]}}

Include at least one proposal with a working http(s) URL and specific evidence.
Prefer primary or reputable sources. Flag uncertainty; do not invent citations.
Do not translate the pages in this phase.
"""
    return _bounded_prompt(prompt, max_chars)


def translation_prompt(
    page: dict[str, Any],
    glossary: dict[str, Any],
    rank: int,
    max_chars: int,
) -> str:
    prompt = f"""# Literary translation — rank {rank}

Translate every assigned Russian page from Nikolai Kononov's *Код Дурова* into
American English, Simplified Chinese, and standard Japanese. Preserve paragraph
order, dialogue, ambiguity, rhythm, and the author's journalistic/literary voice.
Do not summarize or omit text. Use the binding terminology when applicable.

Binding population glossary:
{_dump(glossary)}

Assigned page:
{_dump(page)}

Return only JSON:
{{"rank":{rank},"pages":[{{"page":1,"source_sha256":"...","segments":[
{{"id":1,"ru":"exact source segment","en":"...","zh":"...","ja":"..."}}]}}]}}

The assigned page must occur exactly once and source_sha256 must match. Represent
every source segment in order and make all four language fields nonempty. For a
blank front-matter page, return an empty segments list and explain it in
translator_notes. Translator notes, if indispensable, may be added at page level.
"""
    return _bounded_prompt(prompt, max_chars)


def arbitration_prompt(
    conflicts: dict[str, list[Any]],
    proposals: list[dict[str, Any]],
    rank: int,
    max_chars: int,
) -> str:
    prompt = f"""# Binding terminology arbitration — rank {rank}

The population researching Nikolai Kononov's *Код Дурова* proposed conflicting
English, Simplified Chinese, and Japanese renderings. Resolve every lifted
conflict exactly once. Select one of the supplied candidate values for each key;
do not invent a third rendering. Use the URL-backed research evidence where it
is relevant, and prefer literary naturalness over word-for-word equivalence.

Lifted conflicts:
{_dump(conflicts)}

Population research proposals:
{_dump(proposals)}

Return only JSON:
{{"rank":{rank},"rulings":{{"<conflict key>":<one supplied candidate>}},
"reasons":{{"<conflict key>":"brief evidence-grounded reason"}}}}

The rulings object must contain exactly the conflict keys.
"""
    return _bounded_prompt(prompt, max_chars)


def review_prompt(
    target: dict[str, Any],
    reviewer_rank: int,
    target_rank: int,
    glossary: dict[str, Any],
    max_chars: int,
) -> str:
    prompt = f"""# Peer review and revision — rank {reviewer_rank}

Review rank {target_rank}'s complete multilingual translation of assigned pages
from Nikolai Kononov's *Код Дурова*. Check fidelity against every Russian segment,
literary voice, omissions, terminology, names, and natural American English,
Simplified Chinese, and Japanese. Then revise the artifact itself; do not merely
list suggestions. Preserve page and source hashes.

Binding glossary:
{_dump(glossary)}

Artifact to review:
{_dump(target)}

Return only JSON:
{{"rank":{reviewer_rank},"target_rank":{target_rank},
"critique":[{{"page":1,"issue":"...","revision":"..."}}],
"revised_translation":<the complete corrected artifact with its original rank>}}
"""
    return _bounded_prompt(prompt, max_chars)


def stub_executor(corpus: dict[str, Any]) -> FunctionExecutor:
    """Deterministic protocol fixture; intentionally not a translation system."""
    by_page = {page["page"]: page for page in corpus["pages"]}

    def invoke(task: Task) -> Any:
        page_ids = task.meta.get("pages", [])
        if task.label == "research":
            variant = task.rank % 2
            return {
                "rank": task.rank,
                "stub": True,
                "terminology": {
                    "Код Дурова": {
                        "en": f"TEST-Durov-Code-v{variant}",
                        "zh": f"测试-杜罗夫-v{variant}",
                        "ja": f"テスト-ドゥーロフ-v{variant}",
                    }
                },
                "proposals": [
                    {
                        "topic": "TEST terminology evidence",
                        "recommendation": f"fixture-{task.rank}",
                        "url": "https://example.com/ampi-test-fixture",
                        "evidence": "Deterministic fixture evidence; not research.",
                    }
                ],
            }
        if task.label == "arbitrate":
            conflicts = task.meta["conflicts"]
            return {
                "rank": task.rank,
                "stub": True,
                "rulings": {key: candidates[0] for key, candidates in conflicts.items()},
                "reasons": {key: "Deterministic first-candidate fixture." for key in conflicts},
            }
        if task.label.startswith("translate-page-"):
            translated_pages = []
            for page in page_ids:
                source = by_page[page]
                segments = []
                if source["text"]:
                    segments.append(
                        {
                            "id": 1,
                            "ru": source["text"],
                            "en": f"[TEST en page {page}]",
                            "zh": f"[测试 zh page {page}]",
                            "ja": f"[テスト ja page {page}]",
                        }
                    )
                translated_pages.append(
                    {
                        "page": page,
                        "source_sha256": source["sha256"],
                        "segments": segments,
                        "translator_notes": ["Blank front matter."] if not source["text"] else [],
                    }
                )
            return {
                "rank": task.rank,
                "stub": True,
                "pages": translated_pages,
            }
        target = task.meta["target"]
        return {
            "rank": task.rank,
            "target_rank": target["rank"],
            "stub": True,
            "critique": [
                {
                    "page": target["pages"][0]["page"],
                    "issue": "TEST fixture review",
                    "revision": "No semantic revision in stub mode.",
                }
            ],
            "revised_translation": target,
        }

    return FunctionExecutor(invoke)


def _invoke_with_policy(
    amp: Ampi,
    executor: Any,
    task: Task,
    *,
    policy: str,
    max_restarts: int,
    validate: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    attempts = max_restarts + 1 if policy == "retry-then-fail" else 1
    for attempt in range(attempts):
        try:
            amp.heartbeat(note=f"{task.label} attempt {attempt + 1}")
            value = executor.invoke(task)
            violations = check_contract(value, task.contract, subs={"rank": task.rank})
            if violations:
                raise ValueError(f"{task.label} output violates its contract: {violations}")
            if not isinstance(value, dict):
                raise ValueError(f"{task.label} output is not an object")
            validate(value)
            return value
        except Exception as exc:  # noqa: BLE001 - policy boundary for external executors
            amp.memo(
                f"{task.label}-failure-{attempt + 1}",
                {"error_class": type(exc).__name__, "error": str(exc)},
            )
            if attempt + 1 < attempts:
                task.aid = new_aid()
                continue
            amp.kill(task.rank, reason=f"{task.label} exhausted policy {policy}: {exc}")
            raise
    raise AssertionError("executor policy loop completed without a result")


def _validate_research(value: dict[str, Any]) -> None:
    proposals = value.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise ValueError("research output must contain at least one proposal")
    for proposal in proposals:
        if not all(proposal.get(key) for key in ("topic", "recommendation", "url", "evidence")):
            raise ValueError("every research proposal needs topic, recommendation, URL, and evidence")
        if not str(proposal["url"]).startswith(("http://", "https://")):
            raise ValueError("research proposal URL must use http(s)")


def _validate_translation(
    value: dict[str, Any],
    assignment: dict[str, Any],
    *,
    expected_rank: int,
) -> None:
    expected = {page["page"]: page["sha256"] for page in assignment["pages"]}
    pages = value.get("pages")
    if not isinstance(pages, list) or {page.get("page") for page in pages} != set(expected):
        raise ValueError(f"rank {expected_rank} translation does not cover exactly its assigned pages")
    for page in pages:
        if page.get("source_sha256") != expected[page["page"]]:
            raise ValueError(f"page {page['page']} has the wrong source hash")
        segments = page.get("segments")
        source_page = next(item for item in assignment["pages"] if item["page"] == page["page"])
        if not isinstance(segments, list) or (source_page["text"] and not segments):
            raise ValueError(f"page {page['page']} has no translated segments")
        for index, segment in enumerate(segments, 1):
            if segment.get("id") != index or any(not segment.get(lang) for lang in ("ru", *LANGUAGES)):
                raise ValueError(f"page {page['page']} has an invalid segment at position {index}")


def _validate_review(
    value: dict[str, Any],
    *,
    target_rank: int,
    reviewer_rank: int,
    assignment: dict[str, Any],
) -> None:
    if value.get("target_rank") != target_rank:
        raise ValueError(f"rank {reviewer_rank} reviewed the wrong target")
    revised = value.get("revised_translation")
    if not isinstance(revised, dict):
        raise ValueError("review must contain a revised translation object")
    _validate_translation(revised, assignment, expected_rank=target_rank)


def _assignments(corpus: dict[str, Any], size: int) -> list[dict[str, Any]]:
    buckets: list[list[dict[str, Any]]] = [[] for _ in range(size)]
    for index, page in enumerate(corpus["pages"]):
        buckets[index % size].append(page)
    return [{"rank": rank, "pages": pages} for rank, pages in enumerate(buckets)]


def _research_brief(corpus: dict[str, Any], max_chars: int) -> dict[str, Any]:
    documents = []
    remaining = max_chars
    for item in corpus["research"]:
        text = item["text"][:remaining]
        documents.append({"name": item["name"], "sha256": item["sha256"], "text": text})
        remaining -= len(text)
        if remaining <= 0:
            break
    return {"documents": documents, "truncated_to_chars": max_chars}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _assemble(run_dir: Path, size: int) -> tuple[int, str]:
    rows: list[dict[str, Any]] = []
    for rank in range(size):
        path = run_dir / "out" / f"rank_{rank:03d}.json"
        if not path.is_file():
            continue
        artifact = json.loads(path.read_text(encoding="utf-8"))
        revised = artifact["reviewed_translation"]
        for page in revised["pages"]:
            rows.append(
                {
                    "page": page["page"],
                    "author_rank": revised["rank"],
                    "reviewer_rank": artifact["reviewed_by"],
                    "source_sha256": page["source_sha256"],
                    "segments": page["segments"],
                }
            )
    rows.sort(key=lambda row: row["page"])
    assembled = run_dir / "assembled.jsonl"
    assembled.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return len(rows), str(assembled)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--source-dir", type=Path, default=HERE / "data")
    parser.add_argument("--corpus", default="durov_corpus.json")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--size", type=int, choices=SUPPORTED_RANKS, default=16)
    parser.add_argument("--executor", choices=("broker", "stub"), default="broker")
    parser.add_argument(
        "--test-stub",
        action="store_true",
        help="required safety flag for the deterministic test-only stub",
    )
    parser.add_argument("--device", default="sqlite")
    parser.add_argument("--campaign")
    parser.add_argument("--ctx-budget", type=int, default=100_000)
    parser.add_argument("--research-chars", type=int, default=12_000)
    parser.add_argument("--max-prompt-chars", type=int, default=180_000)
    parser.add_argument("--task-timeout", type=float, default=3600.0)
    parser.add_argument("--phase-timeout", type=float, default=5400.0)
    parser.add_argument("--claim-ttl", type=float, default=1200.0)
    parser.add_argument("--quorum", type=float, default=1.0)
    parser.add_argument(
        "--barrier-policy",
        choices=("wait", "raise", "proceed", "shrink", "revoke"),
        default="wait",
    )
    parser.add_argument(
        "--failure-policy",
        choices=("retry-then-fail", "fail-rank"),
        default="retry-then-fail",
    )
    parser.add_argument("--max-restarts", type=int, default=2)
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.executor == "stub" and not args.test_stub:
        raise ValueError("--executor stub is test-only and requires --test-stub")
    corpus_path = args.source_dir / args.corpus
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if corpus.get("schema") != "ampi.durov-corpus/v1" or len(corpus.get("pages", [])) != 99:
        raise ValueError("corpus must use ampi.durov-corpus/v1 and contain exactly 99 pages")

    run_dir = args.run_dir or HERE / "runs" / args.name
    run_dir.mkdir(parents=True, exist_ok=True)
    campaign = args.campaign or args.name
    assignments = _assignments(corpus, args.size)
    brief = _research_brief(corpus, args.research_chars)
    contracts = {
        "research": {**RESEARCH_CONTRACT},
        "arbitration": {**ARBITRATION_CONTRACT},
        "translation": {**TRANSLATION_CONTRACT},
        "review": {**REVIEW_CONTRACT},
    }
    provenance = {
        "schema": "ampi.durov-run/v1",
        "corpus": str(corpus_path.resolve()),
        "corpus_sha256": hashlib.sha256(corpus_path.read_bytes()).hexdigest(),
        "source": corpus["provenance"],
        "ampi_version": ampi.__version__,
        "executor": args.executor,
        "stub": args.executor == "stub",
        "created_unix": time.time(),
    }
    _write_json(run_dir / "provenance.json", provenance)

    harness = Harness(
        root=str(run_dir / "job"),
        size=args.size,
        device=args.device,
        ctx_budget=args.ctx_budget,
        force=True,
        roles={rank: "researcher-translator-reviewer" for rank in range(args.size)},
        meta={
            "experiment": "e3_durov",
            "campaign": campaign,
            "failure_policy": args.failure_policy,
            "max_restarts": args.max_restarts,
        },
    )
    job = harness.create()
    broker = BrokerExecutor(
        job,
        campaign=campaign,
        work_dir=run_dir / "broker",
        timeout_s=args.task_timeout,
        claim_ttl_s=args.claim_ttl,
    )
    executor = broker if args.executor == "broker" else stub_executor(corpus)
    plan = {
        "campaign": campaign,
        "job_root": str(run_dir / "job"),
        "size": args.size,
        "supported_sizes": list(SUPPORTED_RANKS),
        "executor": args.executor,
        "worker_command": (
            f"ampi worker --campaign {campaign} --rank RANK "
            f"--job-root {run_dir / 'job'} next --timeout 240"
        ),
        "lifecycle": {
            "task_timeout_s": args.task_timeout,
            "claim_ttl_s": args.claim_ttl,
            "barrier_policy": args.barrier_policy,
            "failure_policy": args.failure_policy,
            "max_restarts": args.max_restarts,
        },
        "bounds": {
            "context_tokens_per_rank": args.ctx_budget,
            "research_chars": args.research_chars,
            "max_prompt_chars": args.max_prompt_chars,
            "contracts": contracts,
        },
        "ranks": [
            {
                "rank": item["rank"],
                "pages": [page["page"] for page in item["pages"]],
                "source_chars": sum(len(page["text"]) for page in item["pages"]),
            }
            for item in assignments
        ],
    }
    _write_json(run_dir / "launch_plan.json", plan)

    started = time.time()
    broker.open()

    def rank_main(amp: Ampi, rank: int) -> dict[str, Any]:
        project = amp.bcast(
            "project-brief",
            payload={"provenance": provenance, "research": brief} if rank == 0 else None,
            root=0,
            timeout=args.phase_timeout,
            materialize=True,
        )["body"]
        slices = assignments if rank == 0 else None
        assignment = amp.scatter(
            "page-assignments",
            payload=slices,
            root=0,
            timeout=args.phase_timeout,
            contract={"kind": "json", "required": ["rank", "pages"], "expect": {"rank": "{rank}"}},
        )["body"]
        amp.memo("assignment", {"pages": [page["page"] for page in assignment["pages"]]})

        research_task = Task(
            aid=new_aid(),
            rank=rank,
            label="research",
            prompt=research_prompt(assignment, project["research"], rank, args.max_prompt_chars),
            contract=Contract.parse(contracts["research"]),
            meta={"pages": [page["page"] for page in assignment["pages"]]},
        )
        research = _invoke_with_policy(
            amp,
            executor,
            research_task,
            policy=args.failure_policy,
            max_restarts=args.max_restarts,
            validate=_validate_research,
        )
        amp.memo("research", {"digest": _digest(research)})

        reduced = amp.allreduce(
            "terminology",
            payload=research["terminology"],
            op="union",
            quorum=args.quorum,
            timeout=args.phase_timeout,
        )
        evidence = amp.gather(
            "research-evidence",
            payload=research["proposals"],
            root=0,
            quorum=args.quorum,
            timeout=args.phase_timeout,
            materialize=rank == 0,
        )
        if rank == 0:
            conflicts = reduced.get("conflicts", {})
            if conflicts:
                proposals = [
                    proposal
                    for contribution in evidence.get("bodies", [])
                    for proposal in contribution["body"]
                ]
                arbitration_task = Task(
                    aid=new_aid(),
                    rank=rank,
                    label="arbitrate",
                    prompt=arbitration_prompt(
                        conflicts,
                        proposals,
                        rank,
                        args.max_prompt_chars,
                    ),
                    contract=Contract.parse(contracts["arbitration"]),
                    meta={"conflicts": conflicts},
                )

                def validate_arbitration(value: dict[str, Any]) -> None:
                    rulings = value.get("rulings")
                    if not isinstance(rulings, dict) or set(rulings) != set(conflicts):
                        raise ValueError("arbitration must rule on exactly every lifted conflict")
                    for key, ruling in rulings.items():
                        if ruling not in conflicts[key]:
                            raise ValueError(f"ruling for {key!r} is not a supplied candidate")

                arbitration = _invoke_with_policy(
                    amp,
                    executor,
                    arbitration_task,
                    policy=args.failure_policy,
                    max_restarts=args.max_restarts,
                    validate=validate_arbitration,
                )
                glossary = amp.op_arbitrate(
                    "terminology",
                    rulings=arbitration["rulings"],
                )["value"]
            else:
                glossary = reduced["value"]
            amp.bcast(
                "binding-glossary",
                payload=glossary,
                root=0,
                timeout=args.phase_timeout,
                materialize=True,
            )
        else:
            glossary = amp.bcast(
                "binding-glossary",
                root=0,
                timeout=args.phase_timeout,
                materialize=True,
            )["body"]

        amp.win_create("editorial")
        amp.accumulate(
            "editorial",
            "research",
            {f"rank-{rank:03d}": research["proposals"]},
            op="union",
        )
        if rank == 0:
            for target_rank in range(args.size):
                amp.put("editorial", f"review-claim/{target_rank:03d}", "unclaimed")
        amp.win_fence(
            "editorial",
            "research-and-claims",
            timeout=args.phase_timeout,
            quorum=args.quorum,
        )

        translated_pages: list[dict[str, Any]] = []
        for page in assignment["pages"]:
            page_assignment = {"rank": rank, "pages": [page]}
            page_number = page["page"]
            translation_task = Task(
                aid=new_aid(),
                rank=rank,
                label=f"translate-page-{page_number:03d}",
                prompt=translation_prompt(page, glossary, rank, args.max_prompt_chars),
                contract=Contract.parse(contracts["translation"]),
                meta={"pages": [page_number]},
            )
            translated = _invoke_with_policy(
                amp,
                executor,
                translation_task,
                policy=args.failure_policy,
                max_restarts=args.max_restarts,
                validate=lambda value, expected=page_assignment: _validate_translation(
                    value,
                    expected,
                    expected_rank=rank,
                ),
            )
            translated_pages.extend(translated["pages"])
            amp.put("editorial", f"draft-page/{page_number:03d}", translated["pages"][0])
        draft = {"rank": rank, "pages": translated_pages}
        amp.memo("translation", {"digest": _digest(draft)})
        amp.win_fence(
            "editorial",
            "drafts",
            timeout=args.phase_timeout,
            quorum=args.quorum,
        )

        live_ranks = sorted(amp.live_ranks())
        if len(live_ranks) < 2:
            raise ValueError("peer review requires at least two live ranks")
        target_rank = live_ranks[(live_ranks.index(rank) - 1) % len(live_ranks)]
        claim = amp.compare_and_swap(
            "editorial",
            f"review-claim/{target_rank:03d}",
            "unclaimed",
            {"reviewer_rank": rank, "target_rank": target_rank},
        )
        if not claim["swapped"]:
            raise ValueError(f"rank {rank} could not claim review target {target_rank}")
        critiques: list[dict[str, Any]] = []
        revised_pages: list[dict[str, Any]] = []
        for target_page in assignments[target_rank]["pages"]:
            page_number = target_page["page"]
            target = {
                "rank": target_rank,
                "pages": [
                    amp.get("editorial", f"draft-page/{page_number:03d}")["value"]
                ],
            }
            review_task = Task(
                aid=new_aid(),
                rank=rank,
                label=f"review-page-{page_number:03d}",
                prompt=review_prompt(
                    target,
                    rank,
                    target_rank,
                    glossary,
                    args.max_prompt_chars,
                ),
                contract=Contract.parse(contracts["review"]),
                meta={"target": target},
            )

            page_review = _invoke_with_policy(
                amp,
                executor,
                review_task,
                policy=args.failure_policy,
                max_restarts=args.max_restarts,
                validate=partial(
                    _validate_review,
                    target_rank=target_rank,
                    reviewer_rank=rank,
                    assignment={"rank": target_rank, "pages": [target_page]},
                ),
            )
            critiques.extend(page_review["critique"])
            revised_pages.extend(page_review["revised_translation"]["pages"])

            # Editors touching pages in the same ten-page section serialize this
            # short index update. The leased lock protects actual shared state,
            # so contention and stale fencing tokens are observable in the trace.
            chapter_key = f"chapter-index/{(page_number - 1) // 10:02d}"
            lock = amp.win_lock(
                "editorial",
                chapter_key,
                mode="exclusive",
                ttl=args.claim_ttl,
                timeout=args.phase_timeout,
            )
            try:
                current = amp.get("editorial", chapter_key)
                entries = current.get("value", []) if current["present"] else []
                amp.put(
                    "editorial",
                    chapter_key,
                    [*entries, {"page": page_number, "reviewer": rank}],
                    lock_token=lock["token"],
                )
            finally:
                amp.win_unlock(lock["lock_id"])

        review = {
            "rank": rank,
            "target_rank": target_rank,
            "critique": critiques,
            "revised_translation": {"rank": target_rank, "pages": revised_pages},
        }
        amp.put("editorial", f"final/{target_rank:03d}", review)
        amp.memo("review", {"target_rank": target_rank, "digest": _digest(review)})
        amp.win_fence(
            "editorial",
            "reviewed",
            timeout=args.phase_timeout,
            quorum=args.quorum,
        )

        reviewed = amp.get("editorial", f"final/{rank:03d}")["value"]
        rank_artifact = {
            "rank": rank,
            "assigned_pages": [page["page"] for page in assignment["pages"]],
            "draft": draft,
            "review_of_rank": target_rank,
            "review": review,
            "reviewed_by": reviewed["rank"],
            "reviewed_translation": reviewed["revised_translation"],
            "research": research,
            "provenance": provenance,
        }
        rank_path = run_dir / "out" / f"rank_{rank:03d}.json"
        _write_json(rank_path, rank_artifact)
        amp.barrier(
            "rank-artifacts-written",
            quorum=args.quorum,
            timeout=args.phase_timeout,
            policy=args.barrier_policy,
        )
        gathered = amp.gather(
            "output-manifest",
            payload={
                "rank": rank,
                "path": str(rank_path),
                "sha256": hashlib.sha256(rank_path.read_bytes()).hexdigest(),
                "pages": rank_artifact["assigned_pages"],
            },
            root=0,
            quorum=args.quorum,
            timeout=args.phase_timeout,
        )
        return {
            "pages": rank_artifact["assigned_pages"],
            "reviewed_rank": target_rank,
            "gathered": gathered.get("contributors"),
        }

    try:
        results = harness.run(rank_main, timeout=args.phase_timeout * 8)
    finally:
        broker.close()

    assembled_pages, assembled_path = _assemble(run_dir, args.size)
    report = harness.report(results)
    report.update(
        {
            "experiment": "e3_durov",
            "campaign": campaign,
            "executor": args.executor,
            "stub": args.executor == "stub",
            "source": corpus["provenance"],
            "wall_s": round(time.time() - started, 3),
            "broker": broker.stats(),
            "lifecycle": plan["lifecycle"],
            "bounds": plan["bounds"],
            "assembled_pages": assembled_pages,
            "assembled_jsonl": assembled_path,
            "run_dir": str(run_dir),
        }
    )
    _write_json(run_dir / "report.json", report)
    harness.save(results, run_dir / "harness.json")
    return report


def main(argv: list[str] | None = None) -> dict[str, Any]:
    report = run(build_parser().parse_args(argv))
    print(
        json.dumps(
            {
                "succeeded": report["succeeded"],
                "failed": report["failed"],
                "assembled_pages": report["assembled_pages"],
                "run_dir": report["run_dir"],
            },
            indent=2,
        )
    )
    return report


if __name__ == "__main__":
    main()
