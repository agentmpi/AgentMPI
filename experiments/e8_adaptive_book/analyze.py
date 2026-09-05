"""E8 analysis: where every rank's time went, and the pool against the phases.

Reads a sealed run's trace and writes, under ``runs/<name>/analysis_e8/``:

* ``ranks.json`` — per rank: pages (own and stolen), seams, model seconds,
  seconds waiting for work (``pool.wait``), seconds blocked in collectives;
* ``timeline.png`` — one row per rank, model calls as bars, waits as hatched
  gaps, so idleness is something to look at rather than a share;
* ``summary.json`` and a Markdown table comparing the run with an E7 run of the
  same size (wall, coordination share, blocked rank-hours, cost, coverage).

    python -m experiments.e8_adaptive_book.analyze e8-rawapi-p16 --against e7-rawapi-p16
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"


def _events(path: Path) -> list[dict[str, Any]]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def per_rank(ev: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    ranks: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "pages": [], "stolen": 0, "seams": 0, "model_s": 0.0, "wait_s": 0.0, "waits": 0,
        "blocked_s": 0.0, "calls": 0, "cost_usd": 0.0, "reclaimed": 0, "spans": []})
    starts: dict[str, dict[str, Any]] = {}
    for e in ev:
        r = e.get("rank")
        if not isinstance(r, int):
            try:
                r = int(r)
            except (TypeError, ValueError):
                continue
        k = e["kind"]
        row = ranks[r]
        if k == "pool.claim":
            item = str(e.get("item", ""))
            if item.startswith("p"):
                row["pages"].append(int(item[1:]))
                if not e.get("preferred"):
                    row["stolen"] += 1
            elif item.startswith("s"):
                row["seams"] += 1
            if e.get("reclaimed"):
                row["reclaimed"] += 1
        elif k == "pool.wait":
            row["wait_s"] += float(e.get("waited_s") or 0)
            row["waits"] += 1
        elif k == "task.start":
            starts[e["aid"]] = e
        elif k in ("task.done", "task.fail"):
            s = starts.get(e.get("aid"))
            if s:
                row["spans"].append([s["ts"], e["ts"], s.get("label", "")])
                row["model_s"] += e["ts"] - s["ts"]
        elif k == "task.call":
            row["calls"] += 1
            row["cost_usd"] += float(e.get("cost_usd") or 0)
        elif k in ("allreduce", "bcast", "gather", "barrier", "scatter", "exscan", "reduce"):
            row["blocked_s"] += float(e.get("waited_s") or 0)
    for row in ranks.values():
        row["pages"].sort()
        row["model_s"] = round(row["model_s"], 1)
        row["wait_s"] = round(row["wait_s"], 1)
        row["blocked_s"] = round(row["blocked_s"], 1)
        row["cost_usd"] = round(row["cost_usd"], 4)
    return dict(sorted(ranks.items()))


def summary(name: str, ev: list[dict[str, Any]], ranks: dict[int, dict[str, Any]]) -> dict[str, Any]:
    t0 = min(e["ts"] for e in ev)
    t1 = max(e["ts"] for e in ev)
    n = len(ranks)
    wall = t1 - t0
    model = sum(r["model_s"] for r in ranks.values())
    wait = sum(r["wait_s"] for r in ranks.values())
    blocked = sum(r["blocked_s"] for r in ranks.values())
    pages = [len(r["pages"]) for r in ranks.values()]
    first_claim = min((e["ts"] for e in ev if e["kind"] == "pool.claim"), default=t0)
    last_done = max((e["ts"] for e in ev if e["kind"] == "pool.done"), default=t1)
    return {
        "name": name, "ranks": n, "wall_s": round(wall, 1), "wall_min": round(wall / 60, 1),
        "bootstrap_min": round((first_claim - t0) / 60, 1),
        "pool_min": round((last_done - first_claim) / 60, 1),
        "tail_min": round((t1 - last_done) / 60, 1),
        "model_rank_h": round(model / 3600, 2), "wait_rank_h": round(wait / 3600, 2),
        "blocked_rank_h": round(blocked / 3600, 2),
        "idle_share": round((wait + blocked) / max(1e-9, n * wall), 4),
        "busy_share": round(model / max(1e-9, n * wall), 4),
        "pages_per_rank": {"min": min(pages) if pages else 0, "max": max(pages) if pages else 0,
                           "mean": round(sum(pages) / max(1, n), 2)},
        "stolen": sum(r["stolen"] for r in ranks.values()),
        "reclaimed": sum(r["reclaimed"] for r in ranks.values()),
        "seams": sum(r["seams"] for r in ranks.values()),
        "cost_usd": round(sum(r["cost_usd"] for r in ranks.values()), 2),
        "calls": sum(r["calls"] for r in ranks.values()),
    }


def against(e7: str) -> dict[str, Any] | None:
    m = RUNS / e7 / "analysis" / "metrics.json"
    r = RUNS / e7 / "report.json"
    if not m.exists():
        return None
    metrics = json.loads(m.read_text(encoding="utf-8"))
    report = json.loads(r.read_text(encoding="utf-8")) if r.exists() else {}
    conc = metrics.get("concurrency") or {}
    return {"name": e7, "wall_min": round(float(metrics.get("wall_s") or 0) / 60, 1),
            "coordination_share": metrics.get("coordination_share"),
            "blocked_rank_h": round(float(metrics.get("collective_rank_seconds") or 0) / 3600, 2),
            "work_rank_h": round(float(metrics.get("work_rank_seconds") or 0) / 3600, 2),
            "efficiency": conc.get("parallel_efficiency"),
            "cost_usd": metrics.get("total_cost_usd"),
            "coverage": (report.get("book") or {}).get("coverage")}


def timeline(ranks: dict[int, dict[str, Any]], t0: float, out: Path) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:  # noqa: BLE001 - figures are optional
        return None
    fig, ax = plt.subplots(figsize=(9, 0.32 * len(ranks) + 1.2))
    colours = {"survey": "#999999", "arbitrate": "#bbbbbb", "translate": "#1f77b4",
               "seam": "#ff7f0e"}
    for r, row in ranks.items():
        for s, e, label in row["spans"]:
            fam = str(label).split(":")[0]
            ax.barh(r, (e - s) / 60, left=(s - t0) / 60, height=0.7,
                    color=colours.get(fam, "#2ca02c"), edgecolor="none")
    ax.set_yticks(list(ranks))
    ax.set_ylabel("rank")
    ax.set_xlabel("minutes since launch")
    ax.invert_yaxis()
    for fam, c in colours.items():
        ax.barh([], [], color=c, label=fam)
    ax.legend(frameon=False, ncol=4, fontsize=8, loc="lower right")
    ax.set_title("E8: model calls per rank; gaps are waiting")
    fig.tight_layout()
    p = out / "timeline.png"
    fig.savefig(p, dpi=130)
    plt.close(fig)
    return p


def render(s: dict[str, Any], vs: dict[str, Any] | None, ranks: dict[int, dict[str, Any]]) -> str:
    lines = [f"## Where the time went ({s['name']})", "",
             "| quantity | value |", "|---|---|",
             f"| wall | {s['wall_min']} min (bootstrap {s['bootstrap_min']}, pool {s['pool_min']}, tail {s['tail_min']}) |",
             f"| model rank-hours / waiting for work / blocked in collectives | {s['model_rank_h']} / {s['wait_rank_h']} / {s['blocked_rank_h']} |",
             f"| busy share / idle share | {s['busy_share'] * 100:.1f}% / {s['idle_share'] * 100:.1f}% |",
             f"| pages per rank (min / mean / max) | {s['pages_per_rank']['min']} / {s['pages_per_rank']['mean']} / {s['pages_per_rank']['max']} |",
             f"| pages stolen / items reclaimed / seams | {s['stolen']} / {s['reclaimed']} / {s['seams']} |",
             f"| model exchanges / spend | {s['calls']} / ${s['cost_usd']} |", ""]
    if vs:
        lines += [f"### Against {vs['name']}", "",
                  "| | E7 (phases) | E8 (pool) |", "|---|---|---|",
                  f"| wall (min) | {vs['wall_min']} | {s['wall_min']} |",
                  f"| blocked rank-hours | {vs['blocked_rank_h']} | {round(s['wait_rank_h'] + s['blocked_rank_h'], 2)} |",
                  f"| work rank-hours | {vs['work_rank_h']} | {s['model_rank_h']} |",
                  f"| coordination / idle share | {float(vs['coordination_share'] or 0) * 100:.1f}% | {s['idle_share'] * 100:.1f}% |",
                  f"| spend | ${vs['cost_usd']} | ${s['cost_usd']} |", ""]
    lines += ["### Ranks", "", "| rank | pages | stolen | seams | model min | waited min | blocked min |",
              "|---|---|---|---|---|---|---|"]
    for r, row in ranks.items():
        lines.append(f"| {r} | {len(row['pages'])} | {row['stolen']} | {row['seams']} | "
                     f"{row['model_s'] / 60:.1f} | {row['wait_s'] / 60:.1f} | {row['blocked_s'] / 60:.1f} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--against", default="e7-rawapi-p16")
    a = ap.parse_args(argv)
    run = RUNS / a.name
    ev = _events(run / "harness.trace.jsonl")
    ranks = per_rank(ev)
    s = summary(a.name, ev, ranks)
    vs = against(a.against)
    out = run / "analysis_e8"
    out.mkdir(exist_ok=True)
    (out / "ranks.json").write_text(json.dumps(
        {r: {k: v for k, v in row.items() if k != "spans"} for r, row in ranks.items()}, indent=1),
        encoding="utf-8")
    (out / "summary.json").write_text(json.dumps({"run": s, "against": vs}, indent=1),
                                      encoding="utf-8")
    timeline(ranks, min(e["ts"] for e in ev), out)
    md = render(s, vs, ranks)
    (out / "README.md").write_text(md, encoding="utf-8")
    print(md)
    return s


if __name__ == "__main__":
    main()
