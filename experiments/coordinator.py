"""Coordinator utilities for the real Cursor-agent macrobenchmarks."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from agentmpi_sql import Runtime


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare(
    db: Path,
    *,
    session: str,
    size: int,
    force: bool,
    context_budget: int = 64_000,
    inline_token_limit: int = 8_000,
) -> None:
    """Create a fresh durable session and activate coordinator rank zero."""

    artifact_dir = db.with_suffix(db.suffix + ".artifacts")
    if db.exists() and not force:
        raise FileExistsError(f"{db} already exists; pass --force to replace it")
    if db.exists():
        db.unlink()
    for suffix in ("-shm", "-wal"):
        sidecar = Path(f"{db}{suffix}")
        if sidecar.exists():
            sidecar.unlink()
    if artifact_dir.exists():
        shutil.rmtree(artifact_dir)
    Runtime.initialize(
        db,
        size=size,
        session_id=session,
        context_budget=context_budget,
        inline_token_limit=inline_token_limit,
        heartbeat_ttl=900,
    )
    coordinator = Runtime.attach(db, session, 0, heartbeat_ttl=900)
    coordinator.close()


def translation_root(
    db: Path,
    *,
    session: str,
    source_path: Path,
    output_path: Path,
    timeout: float,
) -> None:
    """Drive broadcast/scatter/gather for eight independent translators."""

    source = _read(source_path)
    passages = source["passages"]
    runtime = Runtime(db, session, 0, heartbeat_ttl=900)
    style = runtime.bcast(source["style"], root=0, timeout=timeout)
    assignments = [
        {
            "role": "coordinator",
            "instruction": "Do not translate; contribute coordinator metadata.",
        },
        *[
            {
                "role": "translator",
                "target_language": source["target_language"],
                "passage": passage,
            }
            for passage in passages
        ],
    ]
    runtime.scatter(assignments, root=0, timeout=timeout)
    gathered = runtime.gather(
        {
            "rank": 0,
            "role": "coordinator",
            "status": "collective-complete",
        },
        root=0,
        timeout=timeout,
    )
    result = {
        "experiment": "translation",
        "condition": "agentmpi-scatter-gather",
        "session": session,
        "source": source["work"],
        "target_language": source["target_language"],
        "style": style,
        "rank_ordered_contributions": gathered,
        "trace": runtime.trace(),
    }
    _write(output_path, result)
    runtime.finalize()
    runtime.close()


def review_root(
    db: Path,
    *,
    session: str,
    source_path: Path,
    drafts_path: Path,
    output_path: Path,
    timeout: float,
) -> None:
    """Drive broadcast/scatter/gather for four translation reviewers."""

    source = _read(source_path)
    drafts = _read(drafts_path)["rank_ordered_contributions"][1:]
    if len(drafts) != 8:
        raise ValueError("review phase requires exactly eight translator contributions")
    runtime = Runtime(db, session, 0, heartbeat_ttl=900)
    shared = runtime.bcast(
        {
            "style": source["style"],
            "target_language": source["target_language"],
            "review_schema": {
                "required": ["reviewer_rank", "passage_ids", "issues", "revisions"]
            },
        },
        root=0,
        timeout=timeout,
    )
    assignments: list[dict[str, Any]] = [
        {
            "role": "coordinator",
            "instruction": "Do not review; contribute coordinator metadata.",
        }
    ]
    for index in range(0, 8, 2):
        assignments.append(
            {
                "role": "reviewer",
                "drafts": drafts[index : index + 2],
                "left_neighbor": drafts[index - 1] if index > 0 else None,
                "right_neighbor": drafts[index + 2] if index + 2 < len(drafts) else None,
            }
        )
    runtime.scatter(assignments, root=0, timeout=timeout)
    gathered = runtime.gather(
        {"rank": 0, "role": "coordinator", "status": "collective-complete"},
        root=0,
        timeout=timeout,
    )
    result = {
        "experiment": "translation-review",
        "condition": "agentmpi-neighborhood-review",
        "session": session,
        "shared_contract": shared,
        "rank_ordered_contributions": gathered,
        "trace": runtime.trace(),
    }
    _write(output_path, result)
    runtime.finalize()
    runtime.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--db", type=Path, required=True)
    prepare_parser.add_argument("--session", required=True)
    prepare_parser.add_argument("--size", type=int, required=True)
    prepare_parser.add_argument("--force", action="store_true")
    prepare_parser.add_argument("--context-budget", type=int, default=64_000)
    prepare_parser.add_argument("--inline-token-limit", type=int, default=8_000)

    for command in ("translation-root", "review-root"):
        root_parser = subparsers.add_parser(command)
        root_parser.add_argument("--db", type=Path, required=True)
        root_parser.add_argument("--session", required=True)
        root_parser.add_argument("--source", type=Path, required=True)
        root_parser.add_argument("--output", type=Path, required=True)
        root_parser.add_argument("--timeout", type=float, default=900)
    subparsers.choices["review-root"].add_argument(
        "--drafts", type=Path, required=True
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.command == "prepare":
        prepare(
            arguments.db,
            session=arguments.session,
            size=arguments.size,
            force=arguments.force,
            context_budget=arguments.context_budget,
            inline_token_limit=arguments.inline_token_limit,
        )
    elif arguments.command == "translation-root":
        translation_root(
            arguments.db,
            session=arguments.session,
            source_path=arguments.source,
            output_path=arguments.output,
            timeout=arguments.timeout,
        )
    elif arguments.command == "review-root":
        review_root(
            arguments.db,
            session=arguments.session,
            source_path=arguments.source,
            drafts_path=arguments.drafts,
            output_path=arguments.output,
            timeout=arguments.timeout,
        )
    else:
        raise AssertionError(f"unhandled command: {arguments.command}")


if __name__ == "__main__":
    main()

