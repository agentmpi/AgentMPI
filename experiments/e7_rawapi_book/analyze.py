"""E7 across scales: one table, a few figures, and the macros the paper cites.

Reads what each run's own analysis wrote (``runs/<name>/analysis/metrics.json``,
from ``ampi analyze``) together with the driver's ``report.json`` and the
launcher's node identities, and produces the cross-scale view: wall time against
p, where the time went (executor work, coordination, serial arbitration), the
population's spend, coverage, and what the restarts cost.  It computes nothing a
single run's analysis does not already contain; it only lines the runs up.

    python -m experiments.e7_rawapi_book.analyze --runs e7-rawapi-p16,e7-rawapi-p32,...
        [--out runs/e7-series] [--tex-prefix eSeven]

The comparison is honest only if the workload is fixed, which it is: the same
book, the same partition rule, the same model, the same prompts, the same
research cap.  What changes with p is the segment size and the number of
segments --- strong scaling --- and the figures are read that way.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "runs"


def _windowed_metrics(d: Path, window: dict[str, Any]) -> dict[str, Any]:
    """Metrics of the trace up to ``window["until_ts"]``, computed once and kept.

    A run that was frozen and brought back measures two things: the production
    run up to the freeze, and the recovery after it.  ``window.json`` in the run
    directory names the boundary and why; the series reports the run's numbers
    from the trace before it and carries the full wall alongside, so neither
    period is hidden in the other.
    """
    out_dir = d / "analysis_window"
    metrics = out_dir / "metrics.json"
    if not metrics.exists():
        until = float(window["until_ts"])
        trace = d / "harness.trace.jsonl"
        cut = d / "harness.trace.window.jsonl"
        with open(trace, encoding="utf-8") as src, open(cut, "w", encoding="utf-8") as dst:
            for line in src:
                try:
                    if float(json.loads(line).get("ts", 0)) <= until:
                        dst.write(line)
                except (json.JSONDecodeError, ValueError):
                    continue
        subprocess.run([sys.executable, "-m", "ampi.cli", "analyze", "--trace", str(cut),
                        "--name", d.name + "-window", "--out", str(out_dir), "--format", "png",
                        "--json"], check=True, capture_output=True, text=True)
        cut.unlink(missing_ok=True)
    return json.loads(metrics.read_text(encoding="utf-8"))


def _load(run: str) -> dict[str, Any]:
    d = RUNS / run
    out: dict[str, Any] = {"name": run}
    for key, fn in (("metrics", "analysis/metrics.json"), ("report", "report.json"),
                    ("plan", "launch_plan.json"), ("config", "config.json"),
                    ("harness", "harness.json"), ("window", "window.json")):
        p = d / fn
        out[key] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if out["window"]:
        out["metrics_full"] = out["metrics"]
        out["metrics"] = _windowed_metrics(d, out["window"])
    nodes = []
    for p in sorted(d.glob("launch/launch-node*.json")):
        nodes.append(json.loads(p.read_text(encoding="utf-8")))
    # The trace is the authoritative record of which nodes took part: every
    # launcher announces itself there, and a node whose machine is gone still
    # left its identity in the job.
    trace = d / "harness.trace.jsonl"
    if trace.exists():
        seen = {n.get("node") for n in nodes}
        with open(trace, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("kind") == "launch.node" and e.get("node") not in seen:
                    nodes.append({"node": e.get("node"), "nodes": e.get("nodes"),
                                  "ranks": e.get("ranks"), "node_identity": e.get("identity") or {},
                                  "from_trace": True})
                    seen.add(e.get("node"))
    out["nodes"] = nodes
    return out


def _phase_seconds(metrics: dict[str, Any]) -> dict[str, float]:
    """Wall seconds per phase, from the memo-derived phase segmentation."""
    out: dict[str, float] = {}
    for ph in metrics.get("phases", []) or []:
        name = str(ph.get("name") or ph.get("label") or "?")
        span = float(ph.get("duration_s") or ph.get("span_s") or
                     (float(ph.get("t_end", 0)) - float(ph.get("t_start", 0))))
        out[name] = round(out.get(name, 0.0) + span, 1)
    return out


def _task_seconds(metrics: dict[str, Any]) -> dict[str, float]:
    """Executor seconds by task family, summed over ranks (busy rank-seconds)."""
    fam: dict[str, float] = {}
    for span in metrics.get("work_spans", []) or []:
        try:
            _rank, start, end, label = span
        except (TypeError, ValueError):
            continue
        key = str(label).split(":")[0]
        fam[key] = fam.get(key, 0.0) + (float(end) - float(start))
    return {k: round(v, 1) for k, v in sorted(fam.items())}


def row_of(run: dict[str, Any]) -> dict[str, Any]:
    m, r, plan = run["metrics"], run["report"], run["plan"]
    conc = m.get("concurrency") or {}
    book = r.get("book") or {}
    ev = r.get("evidence") or {}
    launch = r.get("launch") or {}
    nodes = run["nodes"]
    machines = {n.get("node_identity", {}).get("boot_id") for n in nodes} - {None}
    cost = float(m.get("total_cost_usd") or r.get("spend_total_usd") or 0.0)
    full = run.get("metrics_full") or {}
    return {
        "run": run["name"],
        "window": (run.get("window") or {}).get("reason", ""),
        "wall_total_s": round(float(full.get("wall_s") or 0.0), 1) if full else None,
        "p": int(m.get("world_size") or plan.get("size") or 0),
        "nodes": int(plan.get("nodes") or len(nodes) or 1),
        "machines_seen": len(machines),
        "device": plan.get("device", ""),
        "model": plan.get("model", ""),
        "wall_s": round(float(m.get("wall_s") or r.get("wall_s") or 0.0), 1),
        "ranks_seen": int(m.get("n_ranks_seen") or 0),
        "failed_ranks": len(m.get("failed_ranks") or []),
        "restarts": int(launch.get("restarts") or 0),
        "recovered_ranks": int(r.get("recovered_ranks") or 0),
        "tasks": int((m.get("tasks") or {}).get("submitted") or 0),
        "repairs": int(m.get("total_repairs") or 0),
        "work_rank_s": round(float(m.get("work_rank_seconds") or 0.0), 1),
        "blocked_rank_s": round(float(m.get("collective_rank_seconds") or 0.0), 1),
        "coordination_share": round(float(m.get("coordination_share") or 0.0), 4),
        "achieved_parallelism": round(float(conc.get("achieved_parallelism") or 0.0), 3),
        "parallel_efficiency": round(float(conc.get("parallel_efficiency") or 0.0), 4),
        "serial_fraction": round(float(conc.get("serial_fraction_of_busy") or 0.0), 4),
        "max_busy": int(conc.get("max_busy") or 0),
        "imbalance": round(float(m.get("imbalance") or 0.0), 3),
        "conflicts_lifted": int(m.get("conflicts_lifted") or 0),
        "collectives": len(m.get("collectives") or []),
        "incomplete_collectives": len(m.get("incomplete_collectives") or []),
        "prompt_tokens": int(m.get("total_prompt_tokens") or 0),
        "completion_tokens": int(m.get("total_completion_tokens") or 0),
        "tool_calls": int(m.get("total_tool_calls") or 0),
        "cost_usd": round(cost, 3),
        "paragraphs": int(book.get("paragraphs") or 0),
        "coverage": float(book.get("coverage_of_book") or book.get("coverage") or 0.0),
        "seam_revised": int(book.get("seam_revised") or 0),
        "glossary_terms": int(ev.get("glossary_terms") or 0),
        "findings": int(ev.get("findings") or 0),
        "sources_cited": int(ev.get("sources_cited") or 0),
        "amendments": int(ev.get("amendments") or 0),
        "amendment_clashes": int(ev.get("amendment_clashes") or 0),
        "phase_s": _phase_seconds(m),
        "task_rank_s": _task_seconds(m),
        "replays": sum(int(pr.get("n_replays") or 0) for pr in
                       (m.get("ranks") or {}).values()) if isinstance(m.get("ranks"), dict) else 0,
        "max_wait_s": max((float(c.get("max_wait_s") or 0) for c in m.get("collectives") or []),
                          default=0.0),
    }


def series(names: list[str]) -> list[dict[str, Any]]:
    rows = [row_of(_load(n)) for n in names]
    rows.sort(key=lambda r: (r["p"], r["run"]))
    base = next((r for r in rows if r["p"] == min(x["p"] for x in rows)), None)
    for r in rows:
        if base and base["wall_s"]:
            r["speedup_vs_smallest"] = round(base["wall_s"] / r["wall_s"], 3) if r["wall_s"] else 0.0
            r["p_ratio_vs_smallest"] = round(r["p"] / base["p"], 2) if base["p"] else 0.0
    return rows


def render_series(rows: list[dict[str, Any]], out: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    written: list[Path] = []
    ps = [r["p"] for r in rows]

    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.plot(ps, [r["wall_s"] / 60 for r in rows], "o-", label="measured wall")
    if rows and rows[0]["wall_s"]:
        ideal = [rows[0]["wall_s"] / 60 * rows[0]["p"] / p for p in ps]
        ax.plot(ps, ideal, "--", color="gray", label="ideal strong scaling")
    ax.set_xscale("log", base=2)
    ax.set_xticks(ps)
    ax.set_xticklabels([str(p) for p in ps])
    ax.set_xlabel("ranks p")
    ax.set_ylabel("wall time (min)")
    ax.legend(frameon=False)
    ax.set_title("E7: a fixed book, more ranks")
    fig.tight_layout()
    p1 = out / "wall_vs_p.pdf"
    fig.savefig(p1)
    fig.savefig(p1.with_suffix(".png"), dpi=160)
    plt.close(fig)
    written.append(p1)

    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    width = 0.6
    fams = sorted({k for r in rows for k in r["task_rank_s"]})
    bottoms = [0.0] * len(rows)
    for fam in fams:
        vals = [r["task_rank_s"].get(fam, 0.0) / 3600 for r in rows]
        ax.bar(range(len(rows)), vals, width, bottom=bottoms, label=fam)
        bottoms = [b + v for b, v in zip(bottoms, vals, strict=True)]
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([f"p={r['p']}" for r in rows])
    ax.set_ylabel("executor rank-hours")
    ax.set_title("where the executors' time went")
    ax.legend(frameon=False, fontsize=7, ncol=2)
    fig.tight_layout()
    p2 = out / "work_by_family.pdf"
    fig.savefig(p2)
    fig.savefig(p2.with_suffix(".png"), dpi=160)
    plt.close(fig)
    written.append(p2)

    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.plot(ps, [r["coordination_share"] for r in rows], "o-", label="coordination share")
    ax.plot(ps, [r["parallel_efficiency"] for r in rows], "s-", label="parallel efficiency")
    ax.plot(ps, [r["serial_fraction"] for r in rows], "^-", label="serial fraction (measured)")
    ax.set_xscale("log", base=2)
    ax.set_xticks(ps)
    ax.set_xticklabels([str(p) for p in ps])
    ax.set_ylim(0, 1)
    ax.set_xlabel("ranks p")
    ax.legend(frameon=False, fontsize=8)
    ax.set_title("coordination against parallelism")
    fig.tight_layout()
    p3 = out / "efficiency_vs_p.pdf"
    fig.savefig(p3)
    fig.savefig(p3.with_suffix(".png"), dpi=160)
    plt.close(fig)
    written.append(p3)

    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    ax.bar(range(len(rows)), [r["cost_usd"] for r in rows], 0.6)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels([f"p={r['p']}" for r in rows])
    ax.set_ylabel("spend (USD)")
    ax.set_title("what the population cost")
    fig.tight_layout()
    p4 = out / "cost_vs_p.pdf"
    fig.savefig(p4)
    fig.savefig(p4.with_suffix(".png"), dpi=160)
    plt.close(fig)
    written.append(p4)
    return written


def _tex_num(x: float, digits: int = 1) -> str:
    return f"{x:,.{digits}f}"


def macros(rows: list[dict[str, Any]], prefix: str) -> str:
    """One macro per (scale, quantity), named ``\\<prefix><Quantity><P>``.

    Scales are spelled out in words because LaTeX macro names cannot carry
    digits; ``\\eSevenWallSixtyfour`` reads badly but compiles.
    """
    words = {16: "Sixteen", 32: "Thirtytwo", 64: "Sixtyfour", 128: "Onetwentyeight",
             256: "Twofiftysix", 4: "Four", 8: "Eight"}
    lines = ["% generated by experiments/e7_rawapi_book/analyze.py; do not edit"]
    for r in rows:
        w = words.get(r["p"], f"P{r['p']}")
        vals = {
            "Wall": _tex_num(r["wall_s"] / 60, 1),
            "WallS": _tex_num(r["wall_s"], 0),
            "Nodes": str(r["nodes"]),
            "Machines": str(r["machines_seen"]),
            "Tasks": str(r["tasks"]),
            "Repairs": str(r["repairs"]),
            "Restarts": str(r["restarts"]),
            "Recovered": str(r["recovered_ranks"]),
            "Failed": str(r["failed_ranks"]),
            "WorkH": _tex_num(r["work_rank_s"] / 3600, 1),
            "BlockedH": _tex_num(r["blocked_rank_s"] / 3600, 1),
            "CoordShare": _tex_num(100 * r["coordination_share"], 1),
            "Parallelism": _tex_num(r["achieved_parallelism"], 1),
            "Efficiency": _tex_num(100 * r["parallel_efficiency"], 1),
            "SerialFraction": _tex_num(100 * r["serial_fraction"], 1),
            "MaxBusy": str(r["max_busy"]),
            "Imbalance": _tex_num(r["imbalance"], 2),
            "Conflicts": str(r["conflicts_lifted"]),
            "Collectives": str(r["collectives"]),
            "Incomplete": str(r["incomplete_collectives"]),
            "PromptTokensM": _tex_num(r["prompt_tokens"] / 1e6, 2),
            "CompletionTokensK": _tex_num(r["completion_tokens"] / 1e3, 0),
            "ToolCalls": str(r["tool_calls"]),
            "Cost": _tex_num(r["cost_usd"], 2),
            "Coverage": _tex_num(100 * r["coverage"], 1),
            "Seams": str(r["seam_revised"]),
            "Glossary": str(r["glossary_terms"]),
            "Findings": str(r["findings"]),
            "Sources": str(r["sources_cited"]),
            "Amendments": str(r["amendments"]),
            "Clashes": str(r["amendment_clashes"]),
            "Speedup": _tex_num(r.get("speedup_vs_smallest", 0.0), 2),
        }
        for k, v in vals.items():
            lines.append(f"\\newcommand{{\\{prefix}{k}{w}}}{{{v}}}")
    if rows:
        replays = sum(r.get("replays", 0) for r in rows)
        lines.append(f"\\newcommand{{\\{prefix}ReplaysNote}}{{{replays} collective re-entries traced as replays across the series}}")
        multi = [r for r in rows if r["nodes"] > 1]
        if multi:
            parts = []
            for r in multi:
                parts.append(
                    f"at $p={r['p']}$ over {r['nodes']} machines ({r['machines_seen']} distinct kernel boot "
                    f"ids in the trace) the run took {r['wall_s'] / 60:.1f} minutes, its slowest "
                    f"collective participant waited {r['max_wait_s']:.0f}~s, coverage was "
                    f"{100 * r['coverage']:.1f}\\%, and the population spent \\${r['cost_usd']:.2f}")
            note = "; ".join(parts) + "."
        else:
            note = "The multi-node runs had not completed when this build was made."
        lines.append(f"\\newcommand{{\\{prefix}MultiNodeNote}}{{{note}}}")
        body = table_rows(rows).strip().replace("\n", " ")
        lines.append(f"\\newcommand{{\\{prefix}TableRows}}{{{body}}}")
        lines.append(f"\\newcommand{{\\{prefix}Scales}}{{{len(rows)}}}")
        lines.append(f"\\newcommand{{\\{prefix}Largest}}{{{max(r['p'] for r in rows)}}}")
        lines.append(f"\\newcommand{{\\{prefix}TotalCost}}{{{_tex_num(sum(r['cost_usd'] for r in rows), 2)}}}")
    return "\n".join(lines) + "\n"


def model_stats(names: list[str]) -> list[dict[str, Any]]:
    """Per-model latency, tokens and cost for translation chunks, pooled across runs.

    The pool was a provider policy, not a design; but once every task carries the
    model that served it, the trace answers a question the experiment did not
    set out to ask --- which executor was the straggler, and what it cost.
    """
    import statistics

    by_model: dict[str, dict[str, list[float]]] = {}
    for name in names:
        trace = RUNS / name / "harness.trace.jsonl"
        if not trace.exists():
            continue
        with open(trace, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("kind") not in ("task.done", "task.fail", "task.retry"):
                    continue
                m = str(e.get("model") or "?")
                slot = by_model.setdefault(m, {"translate_s": [], "survey_s": [], "research_s": [],
                                               "seam_s": [], "cost": [], "prompt": [],
                                               "completion": [], "fails": [], "retries": [],
                                               "tasks": []})
                if e["kind"] == "task.fail":
                    slot["fails"].append(1)
                    continue
                if e["kind"] == "task.retry":
                    slot["retries"].append(1)
                    continue
                fam = str(e.get("label", "")).split(":")[0]
                if f"{fam}_s" in slot:
                    slot[f"{fam}_s"].append(float(e.get("seconds") or 0))
                slot["tasks"].append(1)
                slot["cost"].append(float(e.get("cost_usd") or 0))
                slot["prompt"].append(int(e.get("prompt_tokens") or 0))
                slot["completion"].append(int(e.get("completion_tokens") or 0))
    out = []
    for m, slot in sorted(by_model.items()):
        med = lambda xs: round(statistics.median(xs), 1) if xs else None  # noqa: E731
        out.append({
            "model": m, "tasks": len(slot["tasks"]), "fails": len(slot["fails"]),
            "retries": len(slot["retries"]),
            "translate_median_s": med(slot["translate_s"]),
            "translate_p90_s": (round(sorted(slot["translate_s"])[int(0.9 * (len(slot["translate_s"]) - 1))], 1)
                                if slot["translate_s"] else None),
            "survey_median_s": med(slot["survey_s"]), "research_median_s": med(slot["research_s"]),
            "seam_median_s": med(slot["seam_s"]),
            "cost_usd": round(sum(slot["cost"]), 2),
            "cost_per_task": round(sum(slot["cost"]) / max(1, len(slot["tasks"])), 4),
            "completion_tokens": sum(slot["completion"]), "prompt_tokens": sum(slot["prompt"]),
        })
    return out


def render_models(stats: list[dict[str, Any]], out: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    stats = [s for s in stats if s["translate_median_s"] is not None]
    stats.sort(key=lambda s: s["translate_median_s"])
    fig, ax = plt.subplots(figsize=(6.2, 3.4))
    names = [s["model"].split("/", 1)[-1] for s in stats]
    ax.barh(names, [s["translate_median_s"] for s in stats], color="#3b82f6", label="median")
    ax.barh(names, [s["translate_p90_s"] - s["translate_median_s"] for s in stats],
            left=[s["translate_median_s"] for s in stats], color="#93c5fd", label="to p90")
    ax.set_xlabel("seconds per translation chunk (~1100 source tokens)")
    ax.set_title("the executors are not interchangeable")
    ax.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    p = out / "latency_by_model.pdf"
    fig.savefig(p)
    fig.savefig(p.with_suffix(".png"), dpi=160)
    plt.close(fig)
    return p


def table_rows(rows: list[dict[str, Any]]) -> str:
    """The body of the paper's cross-scale table, one line per run."""
    out = []
    for r in rows:
        out.append(
            f"{r['p']} & {r['nodes']} & {r['wall_s'] / 60:.1f} & {r['tasks']} & {r['restarts']} & "
            f"{100 * r['coordination_share']:.1f}\\% & {100 * r['parallel_efficiency']:.1f}\\% & "
            f"{r['cost_usd']:.2f} & {100 * r['coverage']:.1f}\\% \\\\"
        )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser(description="E7 cross-scale analysis")
    ap.add_argument("--runs", required=True, help="comma-separated run names under runs/")
    ap.add_argument("--out", default=str(RUNS / "e7-series"))
    ap.add_argument("--tex-prefix", default="eSeven")
    ap.add_argument("--no-figures", action="store_true")
    a = ap.parse_args(argv)
    names = [n.strip() for n in a.runs.split(",") if n.strip()]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = series(names)
    (out / "series.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (out / "generated.tex").write_text(macros(rows, a.tex_prefix), encoding="utf-8")
    (out / "table_rows.tex").write_text(table_rows(rows), encoding="utf-8")
    written = [] if a.no_figures else render_series(rows, out)
    stats = model_stats(names)
    (out / "models.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    if stats and not a.no_figures:
        written.append(render_models(stats, out))
    mtable = ["| model | tasks | fails | repairs | translate chunk median / p90 (s) | spend | $/task |",
              "|---|---|---|---|---|---|---|"]
    for st in sorted(stats, key=lambda x: -(x["translate_median_s"] or 0)):
        mtable.append(f"| {st['model']} | {st['tasks']} | {st['fails']} | {st['retries']} | "
                      f"{st['translate_median_s']} / {st['translate_p90_s']} | ${st['cost_usd']:.2f} | "
                      f"${st['cost_per_task']:.3f} |")
    table = ["| p | nodes | wall (min) | tasks | repairs | restarts | coord. share | efficiency | "
             "cost ($) | coverage | conflicts |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        table.append(f"| {r['p']} | {r['nodes']} | {r['wall_s'] / 60:.1f} | {r['tasks']} | "
                     f"{r['repairs']} | {r['restarts']} | {100 * r['coordination_share']:.1f}% | "
                     f"{100 * r['parallel_efficiency']:.1f}% | {r['cost_usd']:.2f} | "
                     f"{100 * r['coverage']:.1f}% | {r['conflicts_lifted']} |")
    (out / "README.md").write_text(
        "# E7 across scales\n\nGenerated by `experiments/e7_rawapi_book/analyze.py` from "
        f"{', '.join(names)}.\n\n" + "\n".join(table) + "\n\n## By model\n\n" + "\n".join(mtable) + "\n",
        encoding="utf-8")
    print("\n".join(table))
    print("\n".join(mtable))
    return {"rows": rows, "figures": [str(p) for p in written]}


if __name__ == "__main__":
    main()
