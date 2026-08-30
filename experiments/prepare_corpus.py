#!/usr/bin/env python3
"""Prepare the translation corpus.

We use *Alice's Adventures in Wonderland* (Project Gutenberg #11, public
domain).  It is a deliberate choice, not a convenient one.  The book's twelve
chapters are close to independent as *prose*, which makes it an honest
embarrassingly-parallel workload, but they share a dense set of recurring
proper nouns and coined terms -- the Mock Turtle, the Cheshire Cat, the
Knave of Hearts, the Caucus-race, the Duchess -- whose translations must
agree across chapters or the result is visibly the work of twelve different
translators.

That is exactly the structure we want to study: a task that *looks*
embarrassingly parallel and in fact carries a sequential dependency through
shared terminology.  It gives us an objective, model-free quality metric
(does chapter 9 call the Mock Turtle what chapter 10 calls it?) that does
not require a judge model and cannot be gamed by fluency.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

CHAPTER_RE = re.compile(r"^CHAPTER [IVXL]+\.?\s*$", re.M)

#: Terms whose translation must be consistent across chapters.  Chosen
#: because each is a *coined* or *specific* noun phrase with no single
#: obvious rendering, so independent translators genuinely diverge on them;
#: generic vocabulary would make the metric trivially saturate.
PIVOT_TERMS = [
    "the Mock Turtle",
    "the Cheshire Cat",
    "the Knave of Hearts",
    "the March Hare",
    "the Mad Hatter",
    "the Duchess",
    "the Caterpillar",
    "the White Rabbit",
    "the Queen of Hearts",
    "the Gryphon",
    "the Dormouse",
    "the Caucus-race",
    "the Lobster Quadrille",
    "the Rabbit-Hole",
    "the Pool of Tears",
    "Wonderland",
]


def strip_gutenberg(text: str) -> str:
    start = text.find("*** START OF THE PROJECT GUTENBERG EBOOK")
    if start != -1:
        start = text.find("\n", start) + 1
    else:
        start = 0
    end = text.find("*** END OF THE PROJECT GUTENBERG EBOOK")
    if end == -1:
        end = len(text)
    return text[start:end]


def split_chapters(text: str) -> list[dict]:
    positions = [m.start() for m in CHAPTER_RE.finditer(text)]
    # The first occurrences are the table of contents; real chapters are the
    # later, longer spans.
    spans = []
    for i, pos in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        spans.append((pos, end))
    chapters = [text[a:b].strip() for a, b in spans if b - a > 3000]
    out = []
    for i, body in enumerate(chapters):
        first_line, _, rest = body.partition("\n")
        title_line = rest.strip().split("\n")[0].strip() if rest.strip() else ""
        out.append({
            "index": i,
            "id": f"ch{i:02d}",
            "heading": f"{first_line.strip()} {title_line}".strip(),
            "text": body,
            "chars": len(body),
            "words": len(body.split()),
        })
    return out


def rebalance(chapters: list[dict], target: int) -> list[dict]:
    """Merge or split chapters so there are exactly ``target`` chunks.

    Load imbalance is the dominant scalability limit of an embarrassingly
    parallel agent workload, so the partitioner is part of the experiment,
    not a detail.  We balance by character count with a greedy pass, and we
    report the resulting imbalance so that the measured efficiency can be
    compared against the bound it implies.
    """
    if target >= len(chapters):
        return chapters[:target]
    total = sum(c["chars"] for c in chapters)
    quota = total / target
    chunks: list[dict] = []
    current: list[dict] = []
    running = 0
    for chapter in chapters:
        current.append(chapter)
        running += chapter["chars"]
        if running >= quota and len(chunks) < target - 1:
            chunks.append(_merge(current, len(chunks)))
            current, running = [], 0
    if current:
        chunks.append(_merge(current, len(chunks)))
    return chunks


def _merge(group: list[dict], index: int) -> dict:
    body = "\n\n".join(c["text"] for c in group)
    return {
        "index": index,
        "id": f"chunk{index:02d}",
        "heading": " + ".join(c["heading"] for c in group),
        "text": body,
        "chars": len(body),
        "words": len(body.split()),
        "source_chapters": [c["id"] for c in group],
    }


def term_occurrences(chunks: list[dict]) -> dict[str, list[int]]:
    """Which chunks contain each pivot term."""
    out: dict[str, list[int]] = {}
    for term in PIVOT_TERMS:
        needle = term.lower().removeprefix("the ")
        hits = [c["index"] for c in chunks if needle in c["text"].lower()]
        if hits:
            out[term] = hits
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", default=str(DATA / "alice_raw.txt"))
    ap.add_argument("--chunks", type=int, default=12)
    ap.add_argument("--out", default=str(DATA / "corpus.json"))
    ap.add_argument("--max-chars", type=int, default=0,
                    help="truncate each chunk, to bound per-agent cost")
    args = ap.parse_args()

    raw = Path(args.raw).read_text(encoding="utf-8")
    body = strip_gutenberg(raw)
    chapters = split_chapters(body)
    chunks = rebalance(chapters, args.chunks)
    if args.max_chars:
        for c in chunks:
            if len(c["text"]) > args.max_chars:
                cut = c["text"][:args.max_chars]
                cut = cut[:cut.rfind(".") + 1] or cut
                c["text"] = cut
                c["chars"] = len(cut)
                c["words"] = len(cut.split())
                c["truncated"] = True

    occurrences = term_occurrences(chunks)
    shared = {t: v for t, v in occurrences.items() if len(v) > 1}
    payload = {
        "source": "Alice's Adventures in Wonderland, Lewis Carroll "
                  "(Project Gutenberg #11, public domain)",
        "target_language": "French",
        "chunks": chunks,
        "pivot_terms": PIVOT_TERMS,
        "term_occurrences": occurrences,
        "shared_terms": shared,
        "stats": {
            "chunks": len(chunks),
            "total_chars": sum(c["chars"] for c in chunks),
            "total_words": sum(c["words"] for c in chunks),
            "min_chars": min(c["chars"] for c in chunks),
            "max_chars": max(c["chars"] for c in chunks),
            "imbalance": round(
                max(c["chars"] for c in chunks)
                / (sum(c["chars"] for c in chunks) / len(chunks)), 3),
            "terms_in_multiple_chunks": len(shared),
        },
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps(payload["stats"], indent=2))
    print(f"shared pivot terms ({len(shared)}):")
    for term, chunk_ids in sorted(shared.items(), key=lambda kv: -len(kv[1])):
        print(f"  {term:28s} appears in {len(chunk_ids):2d} chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
