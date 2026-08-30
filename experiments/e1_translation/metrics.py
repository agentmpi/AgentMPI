#!/usr/bin/env python3
"""Metrics for Experiment 1 (parallel book translation).

The evaluation asks one question: *does expressing the task's real dependencies
as MPI-style collectives produce a better artefact than ignoring them?* So the
metrics come in three groups.

**Artefact quality.** Terminology consistency is the headline. For each probe
term, we take the rendering each rank reports having actually used, and compute
the share held by the modal rendering among the ranks whose section contains
that term. A term rendered identically everywhere scores 1.0; a term rendered
three different ways by three ranks scores 0.33. This is automatable, it is the
thing a reader of the translation would notice first, and -- crucially -- it is
exactly the quantity the glossary Allreduce is supposed to fix. Boundary
continuity is measured by whether the halo exchange caused a rank to revise and
republish its opening (a window cell version above 1 is durable evidence, not
self-report).

**Protocol cost.** Payload tokens moved, tokens actually delivered into agent
context windows, per-rank context high-water marks, messages, and the number of
agent-evaluated merge steps on the critical path. The context comparison between
arms is the point: the naive arm's coordinator materialises every translation.

**Protocol robustness.** Retries per rank (from the traced error events),
failures detected, and phase completion derived from the durable memo table
rather than from what the agents claimed in prose.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ampi.journal import Journal  # noqa: E402
from ampi.trace import summarize  # noqa: E402

PHASE_ORDER = ["terms proposed", "glossary agreed", "draft published", "halo exchanged"]


def load_reports(root: Path, exp: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Collect per-rank reports, preferring the window (durable, attributable)."""
    out: Dict[int, Dict[str, Any]] = {}
    j = Journal(root)
    win = j.q1("SELECT id FROM win WHERE job=? AND name='book'", (j.job_id,))
    if win is not None:
        for row in j.q("SELECT key,obj FROM win_cell WHERE win=? AND key LIKE 'report/%'",
                       (str(win["id"]),)):
            try:
                rep = json.loads(j.object_text(str(row["obj"])))
                out[int(rep["rank"])] = rep
            except Exception:
                continue
    j.close()
    # Fall back to the working directory for ranks that never published.
    for m in exp["sections"]:
        r = int(m["rank"])
        if r in out:
            continue
        for cand in (root / "work" / f"rank{r:02d}").glob("*report*.json"):
            try:
                out[r] = json.loads(cand.read_text(encoding="utf-8"))
                break
            except Exception:
                continue
    return out


def load_drafts(root: Path, exp: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    j = Journal(root)
    drafts: Dict[int, Dict[str, Any]] = {}
    win = j.q1("SELECT id FROM win WHERE job=? AND name='book'", (j.job_id,))
    by_section = {int(m["section"]): int(m["rank"]) for m in exp["sections"]}
    if win is not None:
        for row in j.q(
            "SELECT key,obj,version,tokens,writer FROM win_cell WHERE win=? AND key LIKE 'draft/%'",
            (str(win["id"]),),
        ):
            sec = int(str(row["key"]).split("/")[-1])
            text = j.object_text(str(row["obj"]))
            drafts[by_section.get(sec, -1)] = {
                "section": sec,
                "version": int(row["version"]),
                "tokens": int(row["tokens"]),
                "chars": len(text),
                "cjk_chars": sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff"),
                "writer": int(row["writer"]) if row["writer"] is not None else None,
                "source": "window",
            }
    j.close()
    for m in exp["sections"]:
        r = int(m["rank"])
        if r in drafts:
            continue
        for cand in sorted((root / "work" / f"rank{r:02d}").glob("draft_*.md")):
            text = cand.read_text(encoding="utf-8", errors="replace")
            drafts[r] = {
                "section": int(m["section"]),
                "version": 1,
                "tokens": None,
                "chars": len(text),
                "cjk_chars": sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff"),
                "writer": r,
                "source": "file",
            }
            break
    return drafts


def terminology_consistency(
    reports: Dict[int, Dict[str, Any]], exp: Dict[str, Any]
) -> Dict[str, Any]:
    """Modal-share consistency per probe term, over ranks that reported it."""
    probes_by_rank = {int(m["rank"]): set(m["probes"]) for m in exp["sections"]}
    usage: Dict[str, Dict[int, str]] = defaultdict(dict)
    unreported: Dict[int, List[str]] = {}
    for r, rep in reports.items():
        used = rep.get("terms_used") or {}
        if not isinstance(used, dict):
            continue
        norm = {str(k).strip(): str(v).strip() for k, v in used.items() if v}
        for term in probes_by_rank.get(r, set()):
            if term in norm:
                usage[term][r] = norm[term]
        missing = sorted(probes_by_rank.get(r, set()) - set(norm))
        if missing:
            unreported[r] = missing

    per_term: List[Dict[str, Any]] = []
    for term in exp["probe_terms"]:
        renders = usage.get(term, {})
        if len(renders) < 2:
            continue
        counts = Counter(renders.values())
        modal, modal_n = counts.most_common(1)[0]
        per_term.append(
            {
                "term": term,
                "reporters": len(renders),
                "distinct_renderings": len(counts),
                "modal": modal,
                "modal_share": round(modal_n / len(renders), 4),
                "renderings": dict(counts),
            }
        )
    shares = [t["modal_share"] for t in per_term]
    return {
        "terms_scored": len(per_term),
        "mean_modal_share": round(statistics.fmean(shares), 4) if shares else None,
        "median_modal_share": round(statistics.median(shares), 4) if shares else None,
        "fully_consistent_terms": sum(1 for t in per_term if t["distinct_renderings"] == 1),
        "fully_consistent_fraction": (
            round(sum(1 for t in per_term if t["distinct_renderings"] == 1) / len(per_term), 4)
            if per_term else None
        ),
        "mean_distinct_renderings": (
            round(statistics.fmean([t["distinct_renderings"] for t in per_term]), 3)
            if per_term else None
        ),
        "worst_terms": sorted(per_term, key=lambda t: t["modal_share"])[:8],
        "per_term": per_term,
        "ranks_with_unreported_probes": {str(k): v for k, v in sorted(unreported.items())},
    }


def glossary_adherence(root: Path, reports: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Did ranks actually use the glossary the Allreduce agreed on?

    The agreed glossary is the result object of the ``glossary`` collective, so
    this is checked against the journal rather than against anything an agent
    said. A protocol that agrees a value nobody then uses has bought nothing,
    and this is the only honest way to find out.
    """
    j = Journal(root)
    row = j.q1(
        "SELECT result_obj FROM coll WHERE job=? AND op='allreduce'"
        " AND json_extract(params,'$.label')='glossary' AND result_obj IS NOT NULL",
        (j.job_id,),
    )
    agreed: Optional[Dict[str, str]] = None
    if row is not None:
        try:
            data = json.loads(j.object_text(str(row["result_obj"])))
            if isinstance(data, dict):
                agreed = {str(k).strip(): str(v).strip() for k, v in data.items()
                          if isinstance(v, str)}
        except Exception:
            agreed = None
    merges = int(j.scalar(
        "SELECT COUNT(*) FROM reduce_step WHERE state='committed'", (), 0))
    crit = int(j.scalar(
        "SELECT COALESCE(MAX(cnt),0) FROM (SELECT COUNT(*) AS cnt FROM reduce_step"
        " WHERE state='committed' GROUP BY crank)", (), 0))
    j.close()
    if agreed is None:
        return {"agreed_glossary": None, "merge_steps_total": merges,
                "merge_steps_critical_path": crit,
                "note": "no agreed glossary was produced by the collective"}
    hits = 0
    total = 0
    deviations: List[Dict[str, str]] = []
    for r, rep in sorted(reports.items()):
        for term, used in (rep.get("terms_used") or {}).items():
            term = str(term).strip()
            if term not in agreed:
                continue
            total += 1
            if str(used).strip() == agreed[term]:
                hits += 1
            else:
                deviations.append({"rank": str(r), "term": term,
                                   "agreed": agreed[term], "used": str(used).strip()})
    return {
        "agreed_glossary_terms": len(agreed),
        "agreed_glossary": agreed,
        "checked": total,
        "adhered": hits,
        "adherence": round(hits / total, 4) if total else None,
        "deviations": deviations[:20],
        "merge_steps_total": merges,
        "merge_steps_critical_path": crit,
    }


def protocol_metrics(root: Path) -> Dict[str, Any]:
    j = Journal(root)
    s = summarize(j)
    errs = Counter()
    per_rank_err: Dict[int, Counter] = defaultdict(Counter)
    for e in j.q("SELECT rank,status FROM event WHERE job=? AND kind='error'", (j.job_id,)):
        errs[str(e["status"])] += 1
        if e["rank"] is not None:
            per_rank_err[int(e["rank"])][str(e["status"])] += 1
    memos: Dict[int, str] = {}
    for m in j.q("SELECT rank,value FROM memo WHERE job=? AND key='phase'", (j.job_id,)):
        memos[int(m["rank"])] = str(m["value"])
    phase_hist = Counter(memos.values())
    ranks = j.q("SELECT rank,state,calls,ctx_hwm,ctx_budget FROM rank WHERE job=? ORDER BY rank",
                (j.job_id,))
    j.close()
    return {
        "wall_s": s["wall_s"],
        "messages": s["messages"],
        "context": {
            "total_delivered_tokens": s["context"]["total_delivered_tokens"],
            "hwm": s["context"]["hwm"],
            "budget": s["context"]["budget"],
            "per_rank_hwm": s["context"]["per_rank_hwm"],
        },
        "collectives": s["collectives"],
        "agent_merge_s": s["agent_merge_s"],
        "rma": s["rma"],
        "failures": s["failures"],
        "errors_by_class": dict(errs),
        "retries_per_rank": {str(k): dict(v) for k, v in sorted(per_rank_err.items())},
        "timeout_retries_total": errs.get("AMPI_ERR_TIMEOUT", 0),
        "last_phase_by_rank": {str(k): v for k, v in sorted(memos.items())},
        "phase_histogram": dict(phase_hist),
        "rank_states": Counter(str(r["state"]) for r in ranks),
        "calls_per_rank": {str(int(r["rank"])): int(r["calls"]) for r in ranks},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+", help="one or more experiment roots (arms)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    arms: Dict[str, Any] = {}
    for r in args.roots:
        root = Path(r).resolve()
        exp = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
        reports = load_reports(root, exp)
        drafts = load_drafts(root, exp)
        arm = exp["arm"]
        expected = len(exp["sections"])
        cjk = [d["cjk_chars"] for d in drafts.values()]
        arms[arm] = {
            "root": str(root),
            "np": exp["np"],
            "coverage": {
                "sections_expected": expected,
                "drafts_present": len(drafts),
                "drafts_nonempty": sum(1 for d in drafts.values() if d["cjk_chars"] > 200),
                "reports_present": len(reports),
                "completion": round(
                    sum(1 for d in drafts.values() if d["cjk_chars"] > 200) / expected, 4
                ),
                "cjk_chars_total": sum(cjk),
                "cjk_chars_per_section_p50": (
                    round(statistics.median(cjk), 1) if cjk else None
                ),
                "missing_ranks": sorted(
                    {int(m["rank"]) for m in exp["sections"]} - set(drafts)
                ),
            },
            "terminology": terminology_consistency(reports, exp),
            "glossary": glossary_adherence(root, reports),
            "boundary": {
                "drafts_revised_after_halo": sum(
                    1 for d in drafts.values() if (d.get("version") or 1) >= 2
                ),
                "revision_rate": (
                    round(sum(1 for d in drafts.values() if (d.get("version") or 1) >= 2)
                          / max(1, len(drafts)), 4)
                ),
                "self_reported_revisions": sum(
                    1 for rep in reports.values() if rep.get("revised_opening") is True
                ),
            },
            "protocol": protocol_metrics(root),
        }

    result: Dict[str, Any] = {"experiment": "e1_translation", "arms": arms}
    if len(arms) > 1 and "ampi" in arms and "naive" in arms:
        a, n = arms["ampi"], arms["naive"]
        tc_a = a["terminology"]["mean_modal_share"]
        tc_n = n["terminology"]["mean_modal_share"]
        hwm_a = a["protocol"]["context"]["hwm"]["max"]
        hwm_n = n["protocol"]["context"]["hwm"]["max"]
        result["comparison"] = {
            "terminology_mean_modal_share": {"ampi": tc_a, "naive": tc_n,
                                             "absolute_gain": (round(tc_a - tc_n, 4)
                                                               if tc_a and tc_n else None)},
            "fully_consistent_fraction": {
                "ampi": a["terminology"]["fully_consistent_fraction"],
                "naive": n["terminology"]["fully_consistent_fraction"],
            },
            "mean_distinct_renderings": {
                "ampi": a["terminology"]["mean_distinct_renderings"],
                "naive": n["terminology"]["mean_distinct_renderings"],
            },
            "peak_rank_context_tokens": {
                "ampi": hwm_a, "naive": hwm_n,
                "ratio_naive_over_ampi": (round(hwm_n / hwm_a, 2) if hwm_a and hwm_n else None),
            },
            "total_context_tokens": {
                "ampi": a["protocol"]["context"]["total_delivered_tokens"],
                "naive": n["protocol"]["context"]["total_delivered_tokens"],
            },
            "completion": {"ampi": a["coverage"]["completion"],
                           "naive": n["coverage"]["completion"]},
            "wall_s": {"ampi": a["protocol"]["wall_s"], "naive": n["protocol"]["wall_s"]},
        }
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
        if "comparison" in result:
            print(json.dumps(result["comparison"], indent=2, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
