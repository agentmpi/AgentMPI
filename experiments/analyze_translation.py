"""Compute mechanical pilot metrics and a synthesis task from raw agent outputs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from agentmpi.runtime import estimate_tokens


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def analyze(
    source: dict[str, Any],
    drafts: dict[str, Any],
    reviews: dict[str, Any],
    baseline: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    passages = source["passages"]
    draft_values = drafts["rank_ordered_contributions"][1:]
    review_values = reviews["rank_ordered_contributions"][1:]
    by_passage = {item["passage_id"]: item for item in draft_values}

    glossary_checks = 0
    glossary_hits = 0
    for passage in passages:
        target = by_passage[passage["id"]]["translation"]
        for source_term, target_term in source["style"]["glossary"].items():
            if source_term.casefold() in passage["text"].casefold():
                glossary_checks += 1
                if target_term.casefold() in target.casefold():
                    glossary_hits += 1
    baseline_by_passage = {
        item["id"]: item["translation"] for item in baseline["passages"]
    }
    baseline_glossary_checks = 0
    baseline_glossary_hits = 0
    for passage in passages:
        target = baseline_by_passage[passage["id"]]
        for source_term, target_term in source["style"]["glossary"].items():
            if source_term.casefold() in passage["text"].casefold():
                baseline_glossary_checks += 1
                if target_term.casefold() in target.casefold():
                    baseline_glossary_hits += 1

    issues = [issue for review in review_values for issue in review["issues"]]
    issue_categories = Counter(
        str(issue.get("category", "unspecified")) for issue in issues
    )
    issue_severities = Counter(
        str(issue.get("severity", "unspecified")) for issue in issues
    )
    source_tokens = sum(estimate_tokens(passage["text"]) for passage in passages)
    style_tokens = estimate_tokens(source["style"])
    split_input_tokens = source_tokens + style_tokens * len(draft_values)
    naive_input_tokens = (source_tokens + style_tokens) * len(draft_values)

    all_events = drafts["trace"] + reviews["trace"]
    first_timestamp = min(event["timestamp"] for event in all_events)
    last_timestamp = max(event["timestamp"] for event in all_events)
    participants = sorted(
        {
            (event["data"].get("rank", event["rank"]), event["kind"])
            for event in all_events
            if event["kind"] == "agent.join"
        }
    )
    metrics = {
        "schema_version": 1,
        "experiment": "alice-french-pilot",
        "drafts_completed": len(draft_values),
        "reviews_completed": len(review_values),
        "baseline_passages_completed": len(baseline_by_passage),
        "revised_passages": sum(len(review["revisions"]) for review in review_values),
        "all_self_checks_true": all(
            all(item["self_check"].values()) for item in draft_values
        ),
        "all_review_contracts_followed": all(
            bool(review["contract_followed"]) for review in review_values
        ),
        "glossary_applications": glossary_checks,
        "glossary_exact_hits": glossary_hits,
        "glossary_exact_compliance": (
            glossary_hits / glossary_checks if glossary_checks else 1.0
        ),
        "baseline_glossary_exact_compliance": (
            baseline_glossary_hits / baseline_glossary_checks
            if baseline_glossary_checks
            else 1.0
        ),
        "review_issue_count": len(issues),
        "review_issue_categories": dict(sorted(issue_categories.items())),
        "review_issue_severities": dict(sorted(issue_severities.items())),
        "split_estimated_input_tokens": split_input_tokens,
        "naive_full_replication_estimated_input_tokens": naive_input_tokens,
        "estimated_input_token_reduction_fraction": (
            1 - split_input_tokens / naive_input_tokens
        ),
        "protocol_trace_events": len(all_events),
        "protocol_wall_seconds": last_timestamp - first_timestamp,
        "joined_rank_events": participants,
        "source_gap_detected": any(
            "source_gap" in json.dumps(review, ensure_ascii=False)
            for review in review_values
        ),
        "limitations": [
            "Mechanical glossary checks do not measure literary quality.",
            "Agent self-checks and reviews are not independent human judgments.",
            "This is one run with one model family and no statistical replication.",
            "The source selection accidentally omits intervening text between alice-03 and alice-04; a reviewer correctly reported the gap.",
        ],
    }
    synthesis_task = {
        "experiment": "alice-french-pilot-synthesis",
        "instruction": (
            "Produce a final rank-ordered French translation using each review's "
            "complete revisions. Preserve the source passage boundaries. Do not "
            "invent text for the detected source gap. Return JSON with title, "
            "target_language, passages [{id, translation, accepted_review_rank, "
            "change_summary}], unresolved_issues, and synthesis_notes."
        ),
        "source": source,
        "drafts": draft_values,
        "reviews": review_values,
    }
    return metrics, synthesis_task


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("experiments/tasks/translation_source.json"),
    )
    parser.add_argument(
        "--drafts",
        type=Path,
        default=Path("experiments/results/translation/drafts.json"),
    )
    parser.add_argument(
        "--reviews",
        type=Path,
        default=Path("experiments/results/translation/reviews.json"),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("experiments/results/translation/baseline.json"),
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        default=Path("experiments/results/translation/metrics.json"),
    )
    parser.add_argument(
        "--synthesis-task",
        type=Path,
        default=Path("experiments/results/translation/synthesis-task.json"),
    )
    arguments = parser.parse_args()
    metrics, synthesis_task = analyze(
        read_json(arguments.source),
        read_json(arguments.drafts),
        read_json(arguments.reviews),
        read_json(arguments.baseline),
    )
    write_json(arguments.metrics, metrics)
    write_json(arguments.synthesis_task, synthesis_task)
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

