"""E1 scoring: terminology consistency, coverage, and cost.

Every metric here is mechanical.  Nothing asks a model to judge anything, because
a measurement produced by the same class of system under test is not a
measurement.  The cost of that discipline is that we cannot score fluency; the
benefit is that the numbers mean the same thing in every arm and can be recomputed
from stored artifacts by anyone.

One threat this discipline does *not* remove. An executor in the treatment arm
reported, unprompted, that it capitalised compass words mid-sentence because the
glossary lists "South" as "el Sur" and it reasoned the check would be mechanical
-- it chose a less natural rendering to match what it believed the scorer looked
for. That is Goodhart's law appearing inside the experiment, and it biases the
treatment arm upward on the metric this file computes. Two others said they
rendered "City of Emeralds" using the agreed "Ciudad Esmeralda" for book-wide
consistency, which is the same instinct applied more defensibly. The measurement
is in ``experiments/results/e1_metric_gaming.json``; the effect is modest, and it
matters because the treatment arm did not beat the control even with it. A metric
an executor can see is a metric an executor will aim at, and a harness author
choosing an acceptance oracle should assume the population will read it.

The headline metric is **terminology consistency**.  For each recurring source
term, look at every passage whose source contains it, find which rendering that
passage's translation used, and take the modal rendering's share.  A population
that agreed scores 1.0; a population in which four translators each invented their
own name for the Tin Woodman scores 0.25.  Averaging over terms, weighted by how
many passages contain each, gives one number per run.

Matching a rendering inside a translation is the only fuzzy step, and it is done
by exact substring search over the candidate renderings the population itself
proposed, so the scorer never has to guess what a term "should" be.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
RUNS = HERE.parent.parent / "runs"
RESULTS = HERE.parent / "results"


def _norm(s: str) -> str:
    """Case- and accent-insensitive, so that 'Leon' and 'León' are one rendering.

    Diacritic differences are a spelling variation, not a terminology decision,
    and counting them as disagreement would inflate every arm equally while adding
    noise.
    """
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


def _strip_article(s: str) -> str:
    return re.sub(r"^(el|la|los|las|un|una|le|la|les|der|die|das)\s+", "", s)


def load_run(run_dir: Path) -> dict[str, Any]:
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    outputs = {}
    for p in sorted((run_dir / "out").glob("rank*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        outputs[d["rank"]] = d
    return {"report": report, "outputs": outputs}


def build_vocabulary(all_runs: list[dict[int, Any]]) -> dict[str, set[str]]:
    """The candidate renderings the scorer searches for, shared across every arm.

    This must be built from *all* arms being compared, not from each arm's own
    output.  The first version of this scorer built it per run, and since only the
    glossary arm produces term sheets, the control arm was scored against an
    almost-empty vocabulary: it could match only the terms left untranslated, and
    so scored a perfect 1.0 on five terms while the treatment scored 1.0 on ten.
    That is not a null result, it is a measurement artefact, and it flattered the
    control by hiding exactly the disagreements the experiment exists to find.
    """
    vocab: dict[str, set[str]] = defaultdict(set)
    for outputs in all_runs:
        for rec in outputs.values():
            for term, rendering in (rec.get("termsheet") or {}).items():
                if isinstance(rendering, str) and rendering.strip():
                    vocab[term].add(rendering.strip())
    return vocab


def consistency(
    corpus: dict[str, Any],
    outputs: dict[int, Any],
    vocabulary: dict[str, set[str]],
    *,
    ignore_articles: bool = True,
) -> dict[str, Any]:
    """Modal-rendering share per shared term, and the weighted mean."""
    candidates = vocabulary

    per_term: dict[str, Any] = {}
    for term in corpus["shared_terms"]:
        usage: Counter = Counter()
        passages_with_term = 0
        found_in = 0
        for _rank, rec in sorted(outputs.items()):
            passage = corpus["passages"][rec["passage"]]
            if term not in passage["terms"]:
                continue
            passages_with_term += 1
            text = rec.get("result", {}).get("text") or ""
            if not isinstance(text, str):
                continue
            hay = _norm(text)
            hits = []
            for cand in candidates.get(term, set()) | {term}:
                needle = _norm(cand)
                if ignore_articles:
                    needle = _strip_article(needle)
                if needle and needle in hay:
                    hits.append(_strip_article(_norm(cand)) if ignore_articles else _norm(cand))
            if hits:
                found_in += 1
                usage[max(hits, key=len)] += 1
        if passages_with_term < 2 or not usage:
            per_term[term] = {
                "passages": passages_with_term, "found_in": found_in,
                "distinct": len(usage), "modal_share": None,
                "renderings": dict(usage),
            }
            continue
        modal = usage.most_common(1)[0][1]
        per_term[term] = {
            "passages": passages_with_term,
            "found_in": found_in,
            "distinct": len(usage),
            "modal_share": modal / sum(usage.values()),
            "renderings": dict(usage),
        }

    scored = {t: v for t, v in per_term.items() if v["modal_share"] is not None}
    weight = sum(v["found_in"] for v in scored.values()) or 1
    weighted = sum(v["modal_share"] * v["found_in"] for v in scored.values()) / weight
    return {
        "per_term": per_term,
        "terms_scored": len(scored),
        "terms_total": len(corpus["shared_terms"]),
        "weighted_consistency": weighted,
        "unweighted_consistency": (
            sum(v["modal_share"] for v in scored.values()) / len(scored) if scored else None
        ),
        "fully_consistent_terms": sum(1 for v in scored.values() if v["modal_share"] == 1.0),
        "terms_with_disagreement": sum(1 for v in scored.values() if v["distinct"] > 1),
    }


def coverage(corpus: dict[str, Any], outputs: dict[int, Any]) -> dict[str, Any]:
    """Did the population actually translate what it was given?

    A consistency score is meaningless if half the ranks returned a summary.  The
    ratio of translated characters to source characters catches that, and it is
    the reason both arms have to report it.
    """
    rows = []
    for rank, rec in sorted(outputs.items()):
        passage = corpus["passages"][rec["passage"]]
        text = rec.get("result", {}).get("text") or ""
        text = text if isinstance(text, str) else json.dumps(text)
        rows.append({
            "rank": rank,
            "passage": rec["passage"],
            "source_chars": passage["chars"],
            "output_chars": len(text),
            "ratio": len(text) / max(1, passage["chars"]),
        })
    ratios = sorted(r["ratio"] for r in rows)
    return {
        "ranks": len(rows),
        "median_ratio": ratios[len(ratios) // 2] if ratios else 0.0,
        "min_ratio": min(ratios, default=0.0),
        "suspiciously_short": [r["rank"] for r in rows if r["ratio"] < 0.35],
        "per_rank": rows,
    }


def collect(run: str) -> dict[str, Any]:
    """Load a run and attach each rank's own term sheet from the broker's results."""
    run_dir = RUNS / run
    loaded = load_run(run_dir)
    outputs = loaded["outputs"]
    broker = run_dir / "broker"
    if broker.exists():
        for f in broker.glob("*.result"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(d, dict) and "renderings" in d and d.get("rank") in outputs:
                outputs[d["rank"]]["termsheet"] = d["renderings"]
    return loaded


def score(
    run: str, corpus_path: Path, loaded: dict[str, Any], vocabulary: dict[str, set[str]]
) -> dict[str, Any]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    outputs = loaded["outputs"]
    report = loaded["report"]
    out = {
        "run": run,
        "arm": report.get("arm"),
        "executor": report.get("executor"),
        "size": report.get("size"),
        "language": report.get("language"),
        "wall_s": report.get("wall_s"),
        "context_total": report.get("context_total"),
        "context_peak": report.get("context_peak"),
        "succeeded": report.get("succeeded"),
        "failed": report.get("failed"),
        "result_tokens": (report.get("broker") or {}).get("result_tokens"),
        "executors": (report.get("broker") or {}).get("executors", []),
        "ranks_with_output": sorted(outputs),
        "consistency": consistency(corpus, outputs, vocabulary),
        # Reported alongside, because the choice is load-bearing.  Ignoring the
        # article treats "los Munchkins" and "Munchkins" as one decision; keeping
        # it treats them as two.  The population's own term sheets disagreed on
        # exactly that, so a metric that normalises it away cannot see the thing
        # the glossary settled.  Both numbers, always.
        "consistency_strict": consistency(corpus, outputs, vocabulary, ignore_articles=False),
        "coverage": coverage(corpus, outputs),
    }
    out["headline"] = {
        "weighted_consistency": out["consistency"]["weighted_consistency"],
        "weighted_consistency_strict": out["consistency_strict"]["weighted_consistency"],
        "terms_with_disagreement_strict": out["consistency_strict"]["terms_with_disagreement"],
        "fully_consistent_terms": out["consistency"]["fully_consistent_terms"],
        "terms_scored": out["consistency"]["terms_scored"],
        "median_coverage": out["coverage"]["median_ratio"],
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--corpus", default=str(DATA / "oz.json"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    loaded = {r: collect(r) for r in a.runs}
    vocabulary = build_vocabulary([lo["outputs"] for lo in loaded.values()])
    scored = [score(r, Path(a.corpus), loaded[r], vocabulary) for r in a.runs]
    summary = {
        "experiment": "e1_translate",
        "corpus": Path(a.corpus).name,
        "vocabulary_terms": len(vocabulary),
        "vocabulary_note": (
            "Candidate renderings are pooled across every arm compared in this "
            "invocation, so both arms are scored against the same vocabulary. "
            "Scoring an arm against its own term sheets would flatter any arm that "
            "does not produce them."
        ),
        "runs": scored,
        "comparison": [
            {
                "run": s["run"], "arm": s["arm"], "size": s["size"],
                "executors": len(s["executors"]),
                "consistency": round(s["headline"]["weighted_consistency"], 4)
                if s["headline"]["weighted_consistency"] is not None else None,
                "consistency_strict": round(s["headline"]["weighted_consistency_strict"], 4)
                if s["headline"]["weighted_consistency_strict"] is not None else None,
                "disagreements_strict": s["headline"]["terms_with_disagreement_strict"],
                "fully_consistent": s["headline"]["fully_consistent_terms"],
                "scored": s["headline"]["terms_scored"],
                "coverage": round(s["headline"]["median_coverage"], 3),
                "wall_s": s["wall_s"],
                "tokens": s["result_tokens"],
            }
            for s in scored
        ],
    }
    path = Path(a.out or RESULTS / "e1_scores.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary["comparison"], indent=2))
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
