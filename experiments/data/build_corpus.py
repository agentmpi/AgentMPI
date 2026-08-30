#!/usr/bin/env python3
"""Extract public-domain Aesop fables (Gutenberg 11339) into JSON shards."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TITLE = re.compile(r"^[A-ZÆ][A-ZÆ0-9 ,.'’\-]+$")


def extract(text: str) -> list[dict]:
    start = text.find("THE FOX AND THE GRAPES")
    # Second occurrence is the first story body (first is the contents list).
    start = text.find("THE FOX AND THE GRAPES", start + 1)
    if start < 0:
        start = text.find("THE FOX AND THE GRAPES")
    end = text.find("*** END OF THE PROJECT GUTENBERG")
    body = text[start:end if end > 0 else None]
    lines = body.splitlines()
    fables: list[dict] = []
    title = None
    buf: list[str] = []
    for line in lines:
        if TITLE.match(line.strip()) and len(line.strip()) < 80:
            if title and buf:
                prose = "\n".join(buf).strip()
                if len(prose) > 80:
                    fables.append({"title": title, "text": prose})
            title = line.strip()
            buf = []
        else:
            buf.append(line)
    if title and buf:
        prose = "\n".join(buf).strip()
        if len(prose) > 80:
            fables.append({"title": title, "text": prose})
    # Drop leftover front-matter duplicates that are title-only lists.
    cleaned = []
    for f in fables:
        if f["text"].count("\n") >= 2 and not TITLE.match(f["text"].splitlines()[0] if f["text"] else ""):
            cleaned.append(f)
    return cleaned


def shard(fables: list[dict], n: int) -> list[dict]:
    if n <= 0:
        raise ValueError("n must be positive")
    groups: list[list[dict]] = [[] for _ in range(n)]
    for i, f in enumerate(fables):
        groups[i % n].append(f)
    return [{"shard": i, "fables": groups[i]} for i in range(n)]


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/aesop.txt")
    dest = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).parent / "aesop_fables.json")
    fables = extract(src.read_text(encoding="utf-8", errors="replace"))
    dest.write_text(json.dumps(fables, indent=2, ensure_ascii=False))
    print(f"wrote {len(fables)} fables -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
