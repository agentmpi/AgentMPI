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
    done_by: dict[str, int] = {}
    for e in ev:
        r = e.get("rank")
        if not isinstance(r, int):
            try:
                r = int(r)
            except (TypeError, ValueError):
                continue
        if r < 0:
            continue
        k = e["kind"]
        row = ranks[r]
        if k == "pool.done":
            # Who finished an item is the fact; a claim may have been resumed
            # by a later process of the same rank, or reclaimed by another.
            item = str(e.get("item", ""))
            if item in done_by:
                continue
            done_by[item] = r
            if item.startswith("p"):
                row["pages"].append(int(item[1:]))
            elif item.startswith("s"):
                row["seams"] += 1
        elif k == "pool.claim":
            item = str(e.get("item", ""))
            # Stolen means "not from my home block".  The claim's ``preferred``
            # flag is a different thing: a rank whose block is finished prefers
            # the fullest block, and claiming from that is preferred but stolen.
            if item.startswith("p") and e.get("group") != f"b{r}":
                row["stolen"] += 1
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
            # A replayed completion reports the wait since the original join, which
            # is the life of the population, not time this process spent blocked.
            if not e.get("replayed"):
                row["blocked_s"] += float(e.get("waited_s") or 0)
    for row in ranks.values():
        row["pages"].sort()
        row["model_s"] = round(row["model_s"], 1)
        row["wait_s"] = round(row["wait_s"], 1)
        row["blocked_s"] = round(row["blocked_s"], 1)
        row["cost_usd"] = round(row["cost_usd"], 4)
    return dict(sorted(ranks.items()))


def device_latency(ev: list[dict[str, Any]], ranks_per_node: int) -> dict[str, Any]:
    """Seconds from a page's translation finishing to its pool.done, by node.

    Between the two a rank does a handful of synchronous device writes and no
    model call, so the gap is what the machine's transport costs per page.
    """
    ends: dict[tuple[int, str], float] = {}
    gaps: dict[int, list[float]] = defaultdict(list)
    for e in ev:
        if e["kind"] == "task.done" and str(e.get("label", "")).startswith("translate:p"):
            ends[(int(e["rank"]), e["label"].split(":")[1])] = e["ts"]
        elif e["kind"] == "pool.done" and str(e.get("item", "")).startswith("p"):
            key = (int(e["rank"]), e["item"])
            if key in ends:
                gaps[int(e["rank"]) // max(1, ranks_per_node)].append(e["ts"] - ends.pop(key))
    out: dict[str, Any] = {}
    total = 0.0
    for node, g in sorted(gaps.items()):
        g.sort()
        total += sum(g)
        out[f"node{node}"] = {"pages": len(g), "median_s": round(g[len(g) // 2], 1),
                              "p90_s": round(g[int(len(g) * 0.9)], 1)}
    out["total_rank_h"] = round(total / 3600, 2)
    return out


def tail_wait(ev: list[dict[str, Any]]) -> dict[str, Any]:
    """Split waiting into what the pool could remove and what it could not.

    A pool cannot finish before its last item, so once one item is left the rest
    of the population waits however long that item takes --- which in a
    heavy-tailed population is unbounded.  That is a different quantity from
    waiting while work exists, which is the idleness the pool was built to
    remove, and reporting one number for both hides the result in either
    direction.
    """
    # First completion per item: an item reclaimed from a lapsed holder is
    # completed twice, and counting the duplicate as the last item would put the
    # cut inside the stall rather than at its start.
    first: dict[str, float] = {}
    for e in ev:
        if e["kind"] == "pool.done":
            item = str(e.get("item", ""))
            first.setdefault(item, e["ts"])
    done = sorted((ts, item) for item, ts in first.items())
    waits = [e for e in ev if e["kind"] == "pool.wait"]
    total = sum(float(e.get("waited_s") or 0) for e in waits)
    if len(done) < 2:
        return {"total_h": round(total / 3600, 2), "tail_h": 0.0,
                "while_work_existed_h": round(total / 3600, 2), "tail_item": "", "tail_min": 0.0}
    # The last stretch in which only one item remained.
    cut = done[-2][0]
    tail = sum(float(e.get("waited_s") or 0) for e in waits if e["ts"] > cut)
    return {
        "total_h": round(total / 3600, 2),
        "tail_h": round(tail / 3600, 2),
        "while_work_existed_h": round((total - tail) / 3600, 2),
        "tail_item": done[-1][1],
        "tail_min": round((done[-1][0] - cut) / 60, 1),
        "tail_ranks": len({e["rank"] for e in waits if e["ts"] > cut}),
    }


def slowest_call(ev: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [e for e in ev if e["kind"] == "task.call" and e.get("api_seconds")]
    if not calls:
        return {}
    c = max(calls, key=lambda e: float(e["api_seconds"]))
    return {"label": c.get("label", ""), "model": str(c.get("model", "")).split("/")[-1],
            "seconds": round(float(c["api_seconds"]), 1),
            "finish_reason": c.get("finish_reason", "")}


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
        "device_latency": device_latency(ev, max(1, n // 2)),
        "wait": tail_wait(ev),
        "slowest_call": slowest_call(ev),
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
    # An empty bar carries no colour into the legend; a proxy patch does.
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=c, label=fam) for fam, c in colours.items()],
              frameon=False, ncol=4, fontsize=8, loc="lower right")
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
             f"| model exchanges / spend | {s['calls']} / ${s['cost_usd']} |",
             f"| waiting for work, while work existed / for the last item | "
             f"{s['wait']['while_work_existed_h']} / {s['wait']['tail_h']} rank-hours "
             f"({s['wait']['tail_item']}, {s['wait']['tail_min']} min) |",
             f"| slowest single model call | {s['slowest_call'].get('seconds', 0)} s "
             f"({s['slowest_call'].get('model', '?')}, {s['slowest_call'].get('label', '?')}) |",
             "| transport per page, by node (median / p90 s from translation done to pool done) | "
             + "; ".join(f"{k}: {v['median_s']} / {v['p90_s']} ({v['pages']} pages)"
                         for k, v in s["device_latency"].items()
                         if isinstance(v, dict))
             + f"; {s['device_latency'].get('total_rank_h', 0)} rank-hours in total |", ""]
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


def _tex(x: float, dp: int = 1) -> str:
    t = f"{x:,.{dp}f}"
    return t.replace(",", "{,}")


def macros(s: dict[str, Any], vs: dict[str, Any] | None, ranks: dict[int, dict[str, Any]],
           book: dict[str, Any], prefix: str = "eEight") -> str:
    """One macro per quantity, named ``\\eEight<Quantity>``; the paper types none of them."""
    pages = [len(r["pages"]) for r in ranks.values()] or [0]
    lat = s.get("device_latency") or {}
    vals = {
        "Ranks": str(s["ranks"]),
        "Wall": _tex(s["wall_min"], 1),
        "Bootstrap": _tex(s["bootstrap_min"], 1),
        "PoolMin": _tex(s["pool_min"], 1),
        "Tail": _tex(s["tail_min"], 1),
        "ModelH": _tex(s["model_rank_h"], 2),
        "WaitH": _tex(s["wait_rank_h"], 2),
        "WaitWorkH": _tex(float(s["wait"]["while_work_existed_h"]), 2),
        "WaitTailH": _tex(float(s["wait"]["tail_h"]), 2),
        "TailMin": _tex(float(s["wait"]["tail_min"]), 1),
        "SlowestCallS": _tex(float(s["slowest_call"].get("seconds") or 0), 0),
        "SlowestCallModel": str(s["slowest_call"].get("model") or "?"),
        "BlockedH": _tex(s["blocked_rank_h"], 2),
        "IdleShare": _tex(100 * s["idle_share"], 1),
        "BusyShare": _tex(100 * s["busy_share"], 1),
        "PagesMin": str(min(pages)),
        "PagesMax": str(max(pages)),
        "PagesMean": _tex(sum(pages) / max(1, len(pages)), 1),
        "Stolen": str(s["stolen"]),
        "Reclaimed": str(s["reclaimed"]),
        "Seams": str(s["seams"]),
        "Calls": str(s["calls"]),
        "Cost": _tex(s["cost_usd"], 2),
        "Coverage": _tex(100 * float(book.get("coverage") or 0), 1),
        "Paragraphs": str(book.get("paragraphs") or 0),
        "Missing": str(book.get("missing") or 0),
        "SeamRevised": str(book.get("seam_revised") or 0),
        "TransportMedian": _tex(max((v["median_s"] for v in lat.values()
                                     if isinstance(v, dict)), default=0), 1),
        "TransportH": _tex(float(lat.get("total_rank_h") or 0), 2),
    }
    if vs:
        vals.update({
            "SevenWall": _tex(float(vs["wall_min"]), 1),
            "SevenBlockedH": _tex(float(vs["blocked_rank_h"]), 2),
            "SevenWorkH": _tex(float(vs["work_rank_h"]), 2),
            "SevenCoordShare": _tex(100 * float(vs["coordination_share"] or 0), 1),
            "SevenCost": _tex(float(vs["cost_usd"] or 0), 2),
            "SevenCoverage": _tex(100 * float(vs["coverage"] or 0), 1),
        })
    lines = ["% generated by experiments/e8_adaptive_book/analyze.py; do not edit"]
    lines += [f"\\newcommand{{\\{prefix}{k}}}{{{v}}}" for k, v in vals.items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--against", default="e7-rawapi-p16")
    ap.add_argument("--tex", default=None, help="also write the macros here (paper/e8_results.tex)")
    ap.add_argument("--prefix", default="eEight", help="LaTeX macro prefix")
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
    report = run / "report.json"
    book = (json.loads(report.read_text(encoding="utf-8")).get("book") or {}) if report.exists() \
        else {}
    tex = macros(s, vs, ranks, book, a.prefix)
    (out / "generated.tex").write_text(tex, encoding="utf-8")
    if a.tex:
        Path(a.tex).write_text(tex, encoding="utf-8")
    print(md)
    return s


if __name__ == "__main__":
    main()
