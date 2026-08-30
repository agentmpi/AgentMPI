#!/usr/bin/env python3
"""Score a translation run.

The headline metric is **terminology consistency**, and it is deliberately
model-free.  Asking a judge model whether a translation is "good" would make
the result depend on a second stochastic system whose failure modes we are
not studying; counting whether chapter 9 and chapter 10 call the Mock Turtle
the same thing does not.

Two versions are reported, because the difference between them is itself
informative:

``declared consistency``
    Do the chunks that contain a term agree on the rendering they *say* they
    used?  This measures whether the coordination protocol delivered.
``realised consistency``
    Does the declared rendering actually occur in the produced translation?
    This measures whether the agent did what it said.  An agent harness that
    only checks the first number is measuring its own bookkeeping.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def normalise(text: str) -> str:
    """Case- and accent-insensitive comparison key.

    Translators legitimately differ on capitalisation and on whether to write
    an accent, and treating "la Simili-Tortue" and "la simili-tortue" as
    disagreements would overstate inconsistency.  Genuinely different lexical
    choices survive this normalisation.
    """
    stripped = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    stripped = stripped.lower().replace("’", "'")
    stripped = re.sub(r"^(le |la |les |l'|un |une |des )", "", stripped.strip())
    return re.sub(r"[\s\-]+", " ", stripped).strip(" .,;:!?")


def score(payload: dict, corpus: dict) -> dict:
    results = payload.get("results", [])
    by_term: dict[str, dict[int, str]] = defaultdict(dict)
    realised: dict[str, dict[int, bool]] = defaultdict(dict)
    lengths: list[int] = []
    empty: list[int] = []

    for entry in results:
        rank = entry.get("rank")
        glossary = entry.get("glossary") or {}
        translation = entry.get("translation") or ""
        lengths.append(len(translation))
        if len(translation) < 200:
            empty.append(rank)
        norm_translation = normalise(translation)
        for term, rendering in glossary.items():
            if not isinstance(rendering, str):
                rendering = (rendering or [""])[0] if isinstance(rendering, list) else ""
            if not rendering:
                continue
            by_term[term][rank] = rendering
            realised[term][rank] = normalise(rendering) in norm_translation

    shared = {t: v for t, v in by_term.items() if len(v) > 1}
    consistent, inconsistent = [], []
    for term, renderings in sorted(shared.items()):
        variants = {normalise(r) for r in renderings.values()}
        record = {
            "term": term,
            "chunks": len(renderings),
            "variants": len(variants),
            "renderings": {str(k): v for k, v in sorted(renderings.items())},
        }
        (consistent if len(variants) == 1 else inconsistent).append(record)

    total_declared = sum(len(v) for v in realised.values())
    total_realised = sum(sum(1 for ok in v.values() if ok) for v in realised.values())

    n_shared = len(shared)
    return {
        "mode": payload.get("mode"),
        "workers": payload.get("workers"),
        "returned": len(results),
        "wall_s": payload.get("wall_s"),
        "marks": payload.get("marks"),
        "shared_terms": n_shared,
        "consistent_terms": len(consistent),
        "inconsistent_terms": len(inconsistent),
        "declared_consistency": round(len(consistent) / n_shared, 4) if n_shared else None,
        "variant_rate": round(
            sum(t["variants"] for t in consistent + inconsistent) / n_shared, 3
        ) if n_shared else None,
        "realised_rate": round(total_realised / total_declared, 4) if total_declared else None,
        "glossary_entries": total_declared,
        "translation_chars": sum(lengths),
        "min_translation_chars": min(lengths) if lengths else 0,
        "suspiciously_short": empty,
        "inconsistent": inconsistent,
        "consistent": [t["term"] for t in consistent],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="directory holding translations.json")
    ap.add_argument("--corpus", default=str(REPO / "experiments" / "data" / "corpus.json"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    run_dir = Path(args.run)
    payload = json.loads((run_dir / "translations.json").read_text(encoding="utf-8"))
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    report = score(payload, corpus)
    out = Path(args.out or (run_dir / "metrics.json"))
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("inconsistent", "consistent")}, indent=2))
    if report["inconsistent"]:
        print("\ninconsistent terms:")
        for item in report["inconsistent"]:
            print(f"  {item['term']:24s} {item['variants']} variants across "
                  f"{item['chunks']} chunks: "
                  f"{sorted(set(item['renderings'].values()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
