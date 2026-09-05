"""Seal an E7 run: analyse its trace, promote the launch evidence, write its README.

    python -m experiments.e7_rawapi_book.seal e7-rawapi-p16 [more names]

Run after the driver has finished (and, on a multi-node run, after every node's
launch record has been copied into ``runs/<name>/launch/``).  What it adds to
``runs/<name>/``: ``analysis/`` from ``ampi analyze``; ``launch/`` with each
node's launch record (machine identity, per-rank exits and restarts, device
statistics); ``ranks/`` with each rank's own report (phase counters, usage, the
model it ran on --- no text); and a README summarising the run from those files.
Nothing from ``work/`` that quotes the book is promoted.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "runs"
WORK = ROOT / "work" / "e7"


def seal(name: str, *, work_dir: Path | None = None) -> dict[str, Any]:
    run_dir = RUNS / name
    work = work_dir or (WORK / name)
    trace = run_dir / "harness.trace.jsonl"
    if not trace.exists():
        raise SystemExit(f"{run_dir} has no harness.trace.jsonl; did the driver export?")

    # 1. the launch records, one per node
    launch_dir = run_dir / "launch"
    launch_dir.mkdir(exist_ok=True)
    for p in sorted((work / "launch").glob("launch-node*.json")) if (work / "launch").exists() else []:
        shutil.copy2(p, launch_dir / p.name)

    # 2. per-rank reports: counters and usage, never text
    ranks_dir = run_dir / "ranks"
    ranks_dir.mkdir(exist_ok=True)
    for p in sorted((work / "out").glob("report*.json")) if (work / "out").exists() else []:
        rep = json.loads(p.read_text(encoding="utf-8"))
        rep.pop("manifest", None)
        (ranks_dir / p.name).write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")

    # 3. the analysis
    subprocess.run([sys.executable, "-m", "ampi.cli", "analyze", "--trace", str(trace),
                    "--name", name, "--out", str(run_dir / "analysis"), "--format", "png",
                    "--json"], check=True, capture_output=True, text=True)
    metrics = json.loads((run_dir / "analysis" / "metrics.json").read_text(encoding="utf-8"))

    # 4. the README
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8")) \
        if (run_dir / "report.json").exists() else {}
    plan = json.loads((run_dir / "launch_plan.json").read_text(encoding="utf-8"))
    # Coverage against the whole book, not against the segments that arrived: a
    # rank that failed and was dropped leaves its segment out of the window, and
    # the driver's count of assembled paragraphs does not see it.
    manifest_p = run_dir / "corpus_manifest.json"
    total_paragraphs = (json.loads(manifest_p.read_text(encoding="utf-8")).get("n_paragraphs")
                        if manifest_p.exists() else None)
    book = report.get("book") or {}
    if total_paragraphs and book:
        rendered = int(book.get("paragraphs", 0)) - int(book.get("missing", 0))
        book["paragraphs_in_book"] = total_paragraphs
        book["rendered"] = rendered
        book["coverage_of_book"] = round(rendered / total_paragraphs, 4)
        report["book"] = book
        (run_dir / "report.json").write_text(json.dumps(report, indent=2, default=str),
                                             encoding="utf-8")
    rank_reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(ranks_dir.glob("*.json"))]
    nodes = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(launch_dir.glob("*.json"))]
    machines = {n.get("node_identity", {}).get("boot_id") for n in nodes} - {None}
    # The launch records on disk are this machine's; the other nodes announced
    # themselves in the trace, which every node could reach.
    for e in _events(trace):
        if e.get("kind") == "launch.node":
            ident = e.get("identity") or {}
            machines.add(ident.get("boot_id") or ident.get("hostname") or f"node{e.get('node')}")
    machines -= {None}
    by_model: Counter = Counter()
    cost_by_model: dict[str, float] = {}
    for e in _events(trace):
        if e.get("kind") == "task.done":
            by_model[e.get("model", "?")] += 1
            cost_by_model[e.get("model", "?")] = cost_by_model.get(e.get("model", "?"), 0.0) + float(e.get("cost_usd") or 0)
    conc = metrics.get("concurrency") or {}
    waits = [c.get("max_wait_s", 0) for c in metrics.get("collectives") or []]
    lines = [
        f"# {name}",
        "",
        f"E7 production run: *{plan.get('size')}* ranks over *{plan.get('nodes', 1)}* node(s) "
        f"({len(machines)} distinct machine(s) recorded), device `{plan.get('device')}`, "
        f"executor `{plan.get('executor')}`, reasoning `{plan.get('reasoning')}`.",
        "",
        "| quantity | value |", "|---|---|",
        f"| wall | {metrics.get('wall_s', 0) / 60:.1f} min |",
        f"| ranks seen / failed | {metrics.get('n_ranks_seen')} / {len(metrics.get('failed_ranks') or [])} |",
        f"| restarts (recovered ranks) | {sum(s.get('restarts', 0) for n in nodes for s in n.get('rank_states', {}).values())} ({report.get('recovered_ranks', 0)}) |",
        f"| tasks done / repairs | {(metrics.get('tasks') or {}).get('submitted')} / {metrics.get('total_repairs')} |",
        f"| executor rank-hours | {metrics.get('work_rank_seconds', 0) / 3600:.2f} |",
        f"| blocked rank-hours | {metrics.get('collective_rank_seconds', 0) / 3600:.2f} |",
        f"| coordination share | {100 * metrics.get('coordination_share', 0):.1f}% |",
        f"| achieved parallelism / efficiency | {conc.get('achieved_parallelism', 0):.1f} / {100 * conc.get('parallel_efficiency', 0):.1f}% |",
        f"| collectives (median / max of slowest wait) | {len(waits)} ({statistics.median(waits) if waits else 0:.0f} s / {max(waits) if waits else 0:.0f} s) |",
        f"| conflicts lifted | {metrics.get('conflicts_lifted')} |",
        f"| prompt / completion tokens | {metrics.get('total_prompt_tokens', 0):,} / {metrics.get('total_completion_tokens', 0):,} |",
        f"| tool calls | {metrics.get('total_tool_calls')} |",
        f"| spend | ${metrics.get('total_cost_usd', 0):.2f} |",
        f"| coverage of the book | {100 * (report.get('book') or {}).get('coverage_of_book', (report.get('book') or {}).get('coverage', 0)):.1f}% "
        f"({(report.get('book') or {}).get('rendered', '?')} of {(report.get('book') or {}).get('paragraphs_in_book', '?')} paragraphs) |",
        f"| glossary / findings / sources | {(report.get('evidence') or {}).get('glossary_terms')} / {(report.get('evidence') or {}).get('findings')} / {(report.get('evidence') or {}).get('sources_cited')} |",
        f"| amendments / clashes | {(report.get('evidence') or {}).get('amendments')} / {(report.get('evidence') or {}).get('amendment_clashes')} |",
        "",
    ]
    if by_model:
        lines += ["## Executors by model", "", "| model | tasks | spend |", "|---|---|---|"]
        for m, n in by_model.most_common():
            lines.append(f"| {m} | {n} | ${cost_by_model.get(m, 0):.2f} |")
        lines.append("")
    if rank_reports:
        recovered = [r["rank"] for r in rank_reports if r.get("recovered")]
        lines += ["## Ranks", "",
                  f"{len(rank_reports)} rank reports; recovered after a restart: {recovered or 'none'}.", ""]
    lines += ["## Files", "",
              "`launch_plan.json` (every rank requested, before any ran), `config.json`, "
              "`corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` "
              "(diagnosis), `report.json` (the driver's summary), `glossary.json`, "
              "`findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node "
              "launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).", ""]
    (run_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return {"name": name, "wall_min": round(metrics.get("wall_s", 0) / 60, 1),
            "cost": round(metrics.get("total_cost_usd", 0), 2), "machines": len(machines)}


def _events(path: Path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="seal E7 runs")
    ap.add_argument("names", nargs="+")
    ap.add_argument("--work-dir", default=None)
    a = ap.parse_args(argv)
    for n in a.names:
        print(json.dumps(seal(n, work_dir=Path(a.work_dir) if a.work_dir else None)))


if __name__ == "__main__":
    main()
