"""Build a redistributable systems summary without licensed book text."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE.parent / "results"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 3)


def summarize(run_dir: Path) -> dict[str, Any]:
    report = _read_json(run_dir / "report.json")
    provenance = _read_json(run_dir / "provenance.json")
    metrics = _read_json(run_dir / "analysis" / "metrics.json")
    assembled_path = run_dir / "assembled.jsonl"
    pages = [
        json.loads(line)
        for line in assembled_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    corpus = _read_json(Path(provenance["corpus"]))
    expected_hashes = {page["page"]: page["sha256"] for page in corpus["pages"]}
    observed_hashes = {page["page"]: page["source_sha256"] for page in pages}
    complete = (
        [page["page"] for page in pages] == list(range(1, 100))
        and observed_hashes == expected_hashes
    )
    required = {"id", "ru", "en", "zh", "ja"}
    segments = [segment for page in pages for segment in page["segments"]]
    schema_valid = all(required <= set(segment) for segment in segments)

    spans = metrics["broker"]["spans"]
    queues = [float(span["queue_s"]) for span in spans if span["queue_s"] is not None]
    service = [float(span["busy_s"]) for span in spans if span["busy_s"] is not None]
    public = {
        "schema": "ampi.durov-public-summary/v1",
        "run": run_dir.name,
        "source": {
            "repository": report["source"]["source_repo_url"],
            "commit": report["source"]["source_commit"],
            "corpus_sha256": provenance["corpus_sha256"],
            "licensed_payloads_committed": False,
        },
        "population": {
            "ranks": report["size"],
            "succeeded": report["succeeded"],
            "failed": report["failed"],
            "planned_executors": provenance["planned_executors"],
            "observed_executors": len(report["broker"]["executors"]),
            "executor_ids": report["broker"]["executors"],
        },
        "artifact": {
            "complete": complete,
            "schema_valid": schema_valid,
            "pages": len(pages),
            "segments": len(segments),
            "bytes": assembled_path.stat().st_size,
            "sha256": hashlib.sha256(assembled_path.read_bytes()).hexdigest(),
        },
        "systems": {
            "wall_s": report["wall_s"],
            "context_total": report["context_total"],
            "context_peak": report["context_peak"],
            "broker_tasks": report["broker"]["tasks"],
            "broker_requeues": report["broker"]["requeued"],
            "result_tokens": report["broker"]["result_tokens"],
            "trace_events": metrics["event_count"],
            "max_busy": metrics["concurrency"]["max_busy"],
            "achieved_parallelism": metrics["concurrency"]["achieved_parallelism"],
            "parallel_efficiency": metrics["concurrency"]["parallel_efficiency"],
            "queue_p50_s": _percentile(queues, 0.50),
            "queue_p95_s": _percentile(queues, 0.95),
            "service_p50_s": _percentile(service, 0.50),
            "service_p95_s": _percentile(service, 0.95),
        },
        "protocol": {
            "event_counts": report["events"],
            "collective_invocations": metrics["collectives"]["invocation_count"],
            "locks": metrics["rma"]["locks_acquired"],
            "lock_wait_s": metrics["rma"].get("lock_wait_s", 0.0),
            "stale_overwrites_observed": metrics["rma"]["stale_overwrites"],
            "fault_events": metrics["lifecycle"]["fault_count"],
            "recovery_events": metrics["lifecycle"]["recovery_count"],
        },
        "diagnosis": report["diagnosis"],
    }
    if not complete or not schema_valid:
        raise ValueError("assembled artifact failed source coverage or schema validation")
    return public


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    summary = summarize(args.run_dir)
    output = args.output or RESULTS / f"e3_{args.run_dir.name}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
