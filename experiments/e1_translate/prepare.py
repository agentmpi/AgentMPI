"""E1 corpus preparation: split a book into passages and extract its shared terms.

The task is native translation of a book, chosen because it is *almost* but not
quite embarrassingly parallel.  Each passage can be translated independently; what
cannot be done independently is deciding how to render the names and coined terms
that recur across passages.  A harness that ignores the coupling produces fluent
output in which the Tin Woodman has four different names, and a harness that
serialises to avoid it pays p executor latencies.  That is exactly the shape MPI's
collectives address, and it is why this is the first experiment.

Everything here is deterministic and model-free, so the *measurement* is not
downstream of a language model's opinion.  Terminology consistency is computed by
counting renderings, and the term list is extracted by capitalisation frequency
rather than by asking anything to judge importance.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"

# Words that are capitalised for reasons other than being names.
_STOP = {
    "The", "A", "An", "And", "But", "For", "So", "Then", "When", "While", "If",
    "As", "At", "By", "In", "It", "On", "Of", "Or", "To", "We", "You", "He",
    "She", "They", "There", "This", "That", "These", "Those", "What", "Who",
    "Why", "How", "Now", "Yes", "No", "Oh", "Chapter", "Project", "Gutenberg",
    "I", "My", "His", "Her", "Their", "Our", "Its", "One", "Two", "Three",
    "All", "Some", "Not", "Do", "Did", "Was", "Were", "Is", "Are", "Be", "Been",
    "Have", "Has", "Had", "Will", "Would", "Could", "Should", "May", "Might",
    "Come", "Came", "Go", "Went", "Said", "Say", "Very", "Well", "Just", "Only",
    "After", "Before", "Because", "Every", "Never", "Always", "Again", "Here",
    "Let", "Look", "Take", "Make", "Give", "Know", "Think", "See", "Saw", "Get",
    "Perhaps", "Indeed", "Presently", "Nevertheless", "Meanwhile", "However",
}


def strip_gutenberg(text: str) -> str:
    start = re.search(r"\*\*\* START OF (?:THE|THIS) PROJECT GUTENBERG.*?\*\*\*", text, re.S)
    end = re.search(r"\*\*\* END OF (?:THE|THIS) PROJECT GUTENBERG", text)
    body = text[start.end() : end.start() if end else len(text)]
    return body.strip()


def split_passages(body: str, target: int) -> list[dict[str, Any]]:
    """Split into ``target`` passages on paragraph boundaries.

    Paragraph boundaries rather than a fixed character count, because a passage
    cut mid-sentence would make translation quality depend on where the split
    happened to fall, and the experiment is not about that.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    total = sum(len(p) for p in paragraphs)
    passages: list[dict[str, Any]] = []
    current: list[str] = []
    size = 0
    for i, para in enumerate(paragraphs):
        current.append(para)
        size += len(para)
        remaining_bins = target - len(passages)
        remaining_chars = total - sum(len(x) for pas in passages for x in [pas["text"]])
        # Rebalance the target after every emission so that a long paragraph early
        # on does not leave the tail with fewer passages than asked for.
        want = remaining_chars / max(1, remaining_bins)
        paras_left = len(paragraphs) - i - 1
        if (size >= want or paras_left < remaining_bins) and remaining_bins > 1:
            passages.append({"index": len(passages), "text": "\n\n".join(current)})
            current, size = [], 0
    if current:
        passages.append({"index": len(passages), "text": "\n\n".join(current)})
    for i, p in enumerate(passages):
        p["index"] = i
        p["chars"] = len(p["text"])
    return passages


def shared_terms(
    passages: list[dict[str, Any]], min_passages: int = 4
) -> list[str]:
    """Terms recurring across passages: what a glossary has to agree about.

    A term appearing in one passage needs no coordination; the whole point is the
    ones that appear in several, because those are where independent translators
    diverge and where a collective earns its cost.

    Two model-free filters.  A stop list removes words capitalised for grammatical
    reasons, and any candidate that also appears lower-cased somewhere in the
    corpus is dropped, because a genuine proper noun almost never does.  The
    second filter is what removes "Good", "Even" and "Finally" without anybody
    having to judge which words are names.
    """
    body = "\n".join(p["text"] for p in passages)
    per_passage = []
    for p in passages:
        words = re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?\b", p["text"])
        # A term split across a line break is the same term; without normalising,
        # "Emerald\nCity" and "Emerald City" are counted as two.
        per_passage.append(
            {" ".join(w.split()) for w in words if w.split()[0] not in _STOP}
        )
    counts: Counter = Counter()
    for s in per_passage:
        counts.update(s)
    out = []
    for term, n in counts.items():
        if n < min_passages:
            continue
        head = term.split()[0]
        lower = len(re.findall(rf"(?<![A-Za-z]){re.escape(head.lower())}(?![A-Za-z])", body))
        upper = len(re.findall(rf"(?<![A-Za-z]){re.escape(head)}(?![A-Za-z])", body))
        if lower > 0.15 * upper:
            continue
        out.append(term)
    # Prefer the longest form of an overlapping pair: "Emerald City" over "City".
    out.sort(key=len, reverse=True)
    kept: list[str] = []
    for term in out:
        if not any(term != k and term in k for k in kept):
            kept.append(term)
    return sorted(kept)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="a Project Gutenberg plain-text file")
    ap.add_argument("--passages", type=int, default=100)
    ap.add_argument("--title", default="The Wonderful Wizard of Oz")
    ap.add_argument("--out", default=str(DATA / "oz.json"))
    a = ap.parse_args()

    body = strip_gutenberg(Path(a.source).read_text(encoding="utf-8", errors="replace"))
    passages = split_passages(body, a.passages)
    terms = shared_terms(passages)
    for p in passages:
        flat = " ".join(p["text"].split())
        present = [t for t in terms if re.search(rf"\b{re.escape(t)}\b", flat)]
        p["terms"] = present

    out = {
        "title": a.title,
        "source": "Project Gutenberg, public domain",
        "passages": passages,
        "shared_terms": terms,
        "stats": {
            "passages": len(passages),
            "chars": sum(p["chars"] for p in passages),
            "median_chars": sorted(p["chars"] for p in passages)[len(passages) // 2],
            "shared_terms": len(terms),
            "median_terms_per_passage": sorted(len(p["terms"]) for p in passages)[len(passages) // 2],
            "passages_with_no_shared_term": sum(1 for p in passages if not p["terms"]),
        },
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out["stats"], indent=2))
    print(f"wrote {a.out}")
    print("terms:", ", ".join(terms[:24]), "...")


if __name__ == "__main__":
    main()
