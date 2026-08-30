"""Collect and score a completed translation experiment without model judging."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from prepare import GLOSSARY, SESSION

from agentmpi import Runtime


def evaluate(final: dict[str, Any], trace: list[dict[str, Any]]) -> dict[str, Any]:
    chunks = sorted(final["chunks"], key=lambda item: item["chunk_index"])
    glossary_checks = 0
    glossary_hits = 0
    ratios: list[float] = []
    quote_balance = 0
    for chunk in chunks:
        source = chunk["source"]
        target = chunk["translation"]
        ratios.append(len(target) / max(1, len(source)))
        if target.count("“") == target.count("”"):
            quote_balance += 1
        for english, spanish in GLOSSARY.items():
            if english.casefold() in source.casefold():
                glossary_checks += 1
                if spanish.casefold() in target.casefold():
                    glossary_hits += 1
    sends = [event for event in trace if event["kind"] == "message.send"]
    receives = [event for event in trace if event["kind"] == "message.recv"]
    collectives = [event for event in trace if event["kind"].startswith("collective.")]
    participants = sorted(
        {
            event["rank"]
            for event in trace
            if event["kind"] in {"agent.join", "message.send", "message.recv"}
        }
    )
    return {
        "completed_chunks": len(chunks),
        "expected_chunks": 10,
        "participating_ranks": participants,
        "participating_rank_count": len(participants),
        "glossary_applications": glossary_checks,
        "glossary_hits": glossary_hits,
        "glossary_consistency": (glossary_hits / glossary_checks if glossary_checks else 1.0),
        "balanced_curly_quotes_fraction": quote_balance / max(1, len(chunks)),
        "target_source_character_ratio_mean": statistics.mean(ratios),
        "target_source_character_ratio_stdev": (
            statistics.stdev(ratios) if len(ratios) > 1 else 0.0
        ),
        "message_sends": len(sends),
        "message_receives": len(receives),
        "collective_events": len(collectives),
        "trace_event_count": len(trace),
        "note": (
            "Metrics test protocol completion and mechanical consistency, not "
            "human-equivalent translation quality."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("experiments/results/translation.db"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/translation_output.json"),
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        default=Path("experiments/results/translation_metrics.json"),
    )
    parser.add_argument(
        "--trace",
        type=Path,
        default=Path("experiments/results/translation_trace.json"),
    )
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    runtime = Runtime(args.db, SESSION, 0)
    received = runtime.recv(source=16, tag="FINAL", timeout=args.timeout)
    final = received.payload
    trace = runtime.trace()
    metrics = evaluate(final, trace)
    runtime.finalize()
    runtime.close()

    for path, value in (
        (args.output, final),
        (args.metrics, metrics),
        (args.trace, {"generated_at": time.time(), "events": trace}),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
