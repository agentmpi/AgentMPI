"""Prepare the public-domain literary translation AgentMPI workload."""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from agentmpi_sql import Runtime

SOURCE_URL = "https://www.gutenberg.org/files/11/11-0.txt"
SESSION = "alice-es"
TRANSLATORS = tuple(range(1, 11))
REVIEWERS = tuple(range(11, 16))
EDITOR = 16
GLOSSARY: dict[str, str] = {
    "Alice": "Alicia",
    "White Rabbit": "Conejo Blanco",
    "rabbit-hole": "madriguera",
    "waistcoat-pocket": "bolsillo del chaleco",
    "Dinah": "Dina",
    "Down, down, down.": "Abajo, abajo, abajo.",
    "DRINK ME": "BÉBEME",
    "EAT ME": "CÓMEME",
}


def fetch_chapter() -> str:
    request = urllib.request.Request(
        SOURCE_URL,
        headers={"User-Agent": "AgentMPI research artifact/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8-sig")
    start_marker = "CHAPTER I.\nDown the Rabbit-Hole"
    end_marker = "CHAPTER II.\nThe Pool of Tears"
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end].strip()


def chunk_chapter(chapter: str, count: int = 10) -> list[str]:
    body = chapter.split("\n", 2)[2]
    paragraphs = [
        re.sub(r"\s+", " ", paragraph).strip()
        for paragraph in re.split(r"\n\s*\n", body)
        if paragraph.strip() and not set(paragraph.strip()) <= {"*", " "}
    ]
    total = sum(len(paragraph) for paragraph in paragraphs)
    target = total / count
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        if current and current_size >= target and len(chunks) < count - 1:
            chunks.append("\n\n".join(current))
            current = []
            current_size = 0
        current.append(paragraph)
        current_size += len(paragraph)
    chunks.append("\n\n".join(current))
    if len(chunks) != count:
        raise RuntimeError(f"expected {count} chunks, generated {len(chunks)}")
    return chunks


def prepare(db: Path, manifest_path: Path) -> dict[str, Any]:
    chapter = fetch_chapter()
    chunks = chunk_chapter(chapter)
    if db.exists():
        db.unlink()
    for suffix in ("-shm", "-wal"):
        candidate = Path(f"{db}{suffix}")
        if candidate.exists():
            candidate.unlink()
    Runtime.initialize(
        db,
        size=17,
        session_id=SESSION,
        context_budget=24_000,
        mailbox_bytes=4 * 1024 * 1024,
        inline_token_limit=4_000,
        heartbeat_ttl=900,
    )
    coordinator = Runtime.attach(db, SESSION, 0, heartbeat_ttl=900)
    tasks: list[dict[str, Any]] = []
    for index, (rank, source) in enumerate(zip(TRANSLATORS, chunks, strict=True)):
        reviewer = REVIEWERS[index // 2]
        task = {
            "experiment": "translation",
            "task_id": f"alice-ch1-{index:02d}",
            "chunk_index": index,
            "source_language": "English",
            "target_language": "Spanish",
            "source": source,
            "reviewer_rank": reviewer,
            "editor_rank": EDITOR,
            "instructions": [
                "Translate all prose faithfully into literary, neutral Spanish.",
                "Preserve paragraph boundaries, dialogue, emphasis markers, and wordplay.",
                "Use every applicable term from the broadcast glossary.",
                "Return translation plus concise notes on ambiguous choices.",
            ],
        }
        coordinator.send(task, rank, tag="TASK")
        tasks.append(task)
    coordinator.close()
    manifest = {
        "session": SESSION,
        "db": str(db),
        "source": {
            "title": "Alice's Adventures in Wonderland, Chapter I",
            "author": "Lewis Carroll",
            "url": SOURCE_URL,
            "license": "Public domain in the USA (Project Gutenberg eBook #11)",
        },
        "glossary": GLOSSARY,
        "topology": {
            "coordinator": 0,
            "translators": list(TRANSLATORS),
            "reviewers": list(REVIEWERS),
            "editor": EDITOR,
            "edges": [
                {"from": rank, "to": REVIEWERS[index // 2], "tag": "DRAFT"}
                for index, rank in enumerate(TRANSLATORS)
            ]
            + [{"from": rank, "to": EDITOR, "tag": "REVIEW"} for rank in REVIEWERS]
            + [{"from": EDITOR, "to": 0, "tag": "FINAL"}],
        },
        "tasks": tasks,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("experiments/results/translation.db"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("experiments/results/translation_manifest.json"),
    )
    args = parser.parse_args()
    manifest = prepare(args.db, args.manifest)
    print(
        json.dumps(
            {
                "session": manifest["session"],
                "tasks": len(manifest["tasks"]),
                "db": str(args.db),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
