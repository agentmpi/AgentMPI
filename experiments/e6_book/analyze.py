"""Cross-scale analysis of the E6 series: what changes as p goes 16, 32, 64.

Per run, ``ampi analyze`` answers the single-run questions from the trace alone.
This script answers the series questions, which is where the scaling claims
live, from the artifacts ``collect`` wrote under ``runs/<name>/``:

* how wall time divides between agent work and coordination, per phase;
* what the git transport cost --- commits, rejections, and the seconds a rank
  spent inside collectives waiting for pushes rather than for peers;
* how many disagreements the census lifted and how long research took;
* who the stragglers were and what they cost;
* how faults were handled: convictions, stolen pages, tasks nobody claimed;
* what the book looks like: coverage, revisions, sentences.

Nothing here is typed by hand; every number traces back to a file under
``runs/``.  The output is one JSON table (``runs/e6-series/series.json``), a
markdown report, and three figures.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
RUNS = ROOT / "runs"

#: A silence longer than this in the event stream is a population-wide pause
#: (an account usage-limit freeze), not a slow step: no collective or agent
#: task in this harness runs anywhere near twenty minutes.
_FREEZE_GAP_S = 1200.0

PHASES = ("launch", "survey", "census", "research", "glossary", "translate", "review",
          "seams", "assemble", "done")
AGENT_LABELS = ("survey", "arbitrate", "research", "translate", "fix", "review", "revise", "seam")


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _events(path: Path) -> list[dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    out.sort(key=lambda e: (e.get("ts", 0.0), e.get("seq", 0)))
    return out


def task_kind(label: str) -> str:
    return label.split(":", 1)[0]


def summarise_run(name: str) -> dict[str, Any] | None:
    run = RUNS / name
    report = _load(run / "report.json")
    metrics = _load(run / "analysis" / "metrics.json")
    plan = _load(run / "launch_plan.json")
    events = _events(run / "harness.trace.jsonl")
    if not report or not events:
        return None
    t0 = events[0]["ts"]
    wall = events[-1]["ts"] - t0
    p = report.get("size") or (plan or {}).get("size")

    # Freeze accounting.  When the account hits a usage limit every session is
    # paused at once, and the harness processes do not survive it: the whole
    # population stops writing for hours, then the resume machinery brings it
    # back.  A gap in the event stream longer than any single collective or
    # agent step could explain is such a pause; subtracting the gaps gives the
    # active wall time, which is what the per-task costs actually accrued in.
    # The span is kept too, because the fact that a production run took a day of
    # wall clock to do five hours of work is itself a finding.
    ts = sorted(e["ts"] for e in events)
    freezes = [{"start": round(a, 1), "end": round(b, 1), "hours": round((b - a) / 3600, 2)}
               for a, b in zip(ts, ts[1:]) if b - a > _FREEZE_GAP_S]
    frozen_s = sum(f["end"] - f["start"] for f in freezes)
    active_wall = wall - frozen_s

    # Agent work, from the mirrored broker spans.
    claims: dict[str, dict[str, Any]] = {}
    spans: list[tuple[str, int, float, float]] = []
    for e in events:
        if e["kind"] == "broker.claim":
            claims[str(e.get("aid"))] = e
        elif e["kind"] == "broker.submit":
            c = claims.pop(str(e.get("aid")), None)
            if c is not None:
                spans.append((task_kind(str(e.get("label") or c.get("label") or "")),
                              int(e.get("rank", -1)), c["ts"] - t0, e["ts"] - t0))
    by_kind: dict[str, list[float]] = defaultdict(list)
    for kind, _r, s, t in spans:
        by_kind[kind].append(t - s)
    work_s = sum(t - s for _k, _r, s, t in spans)
    task_stats = {k: {"n": len(v), "median_s": round(statistics.median(v), 1),
                      "max_s": round(max(v), 1), "total_s": round(sum(v), 1)}
                  for k, v in sorted(by_kind.items())}

    # Collectives, from the completion events: rank-seconds blocked and stragglers.
    colls = [e for e in events if e.get("waited_s") is not None and e["kind"] in
             ("barrier", "bcast", "scatter", "gather", "allgather", "allreduce", "reduce",
              "exscan", "scan", "neighbor_allgather", "alltoall")]
    blocked = sum(float(e.get("waited_s") or 0) for e in colls)
    by_label: dict[str, list[float]] = defaultdict(list)
    for e in colls:
        by_label[f"{e['kind']}:{e.get('label')}"].append(float(e.get("waited_s") or 0))
    worst = sorted(((sum(v), k, max(v)) for k, v in by_label.items()), reverse=True)[:6]

    # Phases, from memos: first and last announcement per phase.
    phase_t: dict[str, list[float]] = defaultdict(list)
    for e in events:
        if e["kind"] == "memo" and e.get("key") == "phase":
            phase_t[str(e.get("note"))].append(e["ts"] - t0)
    phases = {}
    for i, ph in enumerate(PHASES):
        if ph not in phase_t:
            continue
        nxt = next((phase_t[q] for q in PHASES[i + 1:] if q in phase_t), None)
        start = min(phase_t[ph])
        end = max(nxt) if nxt else wall
        phases[ph] = {"start_s": round(start, 1), "end_s": round(end, 1),
                      "spread_s": round(max(phase_t[ph]) - start, 1)}

    # Faults and recovery.
    kinds = Counter(e["kind"] for e in events)
    convicted = sorted({e["rank"] for e in events if e["kind"] in ("failure.convict", "failure.kill")})
    stolen = [e for e in events if e["kind"] == "page.steal"]
    lost = [e for e in events if e["kind"] == "executor.lost"]

    # The transport.
    gitlog = run / "evidence" / "git-log.txt"
    commits = 0
    commit_kinds: Counter = Counter()
    if gitlog.exists():
        for line in gitlog.read_text().splitlines():
            parts = line.split()
            if len(parts) > 3:
                commits += 1
                commit_kinds[parts[3]] += 1
    identities = [e for e in events if e["kind"] == "rank.identity"]
    machines = len({e.get("boot_id") for e in identities if e.get("boot_id")})
    lock_waits = [float(e.get("waited_s") or 0) for e in events if e["kind"] == "win.lock"]
    claim_attempts = [e for e in events if e["kind"] == "win.cas"]

    book = report.get("book") or {}
    row = {
        "name": name, "p": p, "wall_s": round(wall, 1), "wall_h": round(wall / 3600, 2),
        "active_wall_s": round(active_wall, 1), "active_wall_h": round(active_wall / 3600, 2),
        "freezes": freezes, "n_freezes": len(freezes), "frozen_h": round(frozen_s / 3600, 2),
        "machines": machines,
        "ranks_finalised": (report.get("rank_states") or {}).get("finalised", 0),
        "ranks_failed": (report.get("rank_states") or {}).get("failed", 0),
        "convicted": convicted, "executors_lost": len(lost), "pages_stolen": len(stolen),
        "tasks": len(spans), "task_stats": task_stats,
        "work_rank_s": round(work_s, 1), "blocked_rank_s": round(blocked, 1),
        "coordination_share": round(blocked / (p * wall), 4) if p and wall else None,
        "achieved_parallelism": round(work_s / wall, 2) if wall else None,
        "parallel_efficiency": round(work_s / (p * wall), 4) if p and wall else None,
        # On active wall the blocked total still spans the freezes, so it is not
        # recomputed; achieved parallelism on active wall is the honest figure
        # for how much of the work overlapped while the machines were awake.
        "active_parallelism": round(work_s / active_wall, 2) if active_wall > 0 else None,
        "active_efficiency": round(work_s / (p * active_wall), 4) if p and active_wall > 0 else None,
        "worst_collectives": [{"label": k, "rank_s": round(s, 1), "max_wait_s": round(m, 1)}
                              for s, k, m in worst],
        "phases": phases,
        "census_conflicts": max((int(e.get("conflicts") or 0) for e in events
                                 if e["kind"] == "allreduce" and e.get("label") == "census"), default=0),
        "glossary_conflicts": max((int(e.get("conflicts") or 0) for e in events
                                   if e["kind"] == "allreduce" and e.get("label") == "glossary"), default=0),
        "researched": report.get("findings"), "binding_terms": report.get("binding_terms"),
        "claim_attempts": len(claim_attempts),
        "claims_won": sum(1 for e in claim_attempts if e.get("swapped")),
        "lock_wait_max_s": round(max(lock_waits), 1) if lock_waits else 0.0,
        "lock_wait_total_s": round(sum(lock_waits), 1),
        "commits": commits, "commit_kinds": dict(commit_kinds.most_common(6)),
        "commits_per_rank": round(commits / p, 1) if p and commits else None,
        "events": len(events), "event_kinds": dict(kinds),
        "book": {k: book.get(k) for k in ("n_pages", "expected", "missing", "failed", "sentences",
                                          "revised")},
        "stolen_pages": sorted({int(e["page"]) for e in stolen}),
        "straggler_cost": (metrics or {}).get("straggler_cost"),
        "imbalance": (metrics or {}).get("imbalance"),
        "reattachments": (metrics or {}).get("total_reattachments"),
    }
    return row


def markdown(rows: list[dict[str, Any]]) -> str:
    out = ["# E6 series", "",
           "| p | machines | wall (h) | tasks | work rank-s | blocked rank-s | coord. share | "
           "parallelism | efficiency | census conflicts | commits | convicted | stolen | pages |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        b = r["book"]
        out.append(
            f"| {r['p']} | {r['machines']} | {r['wall_h']} | {r['tasks']} | {r['work_rank_s']} | "
            f"{r['blocked_rank_s']} | {r['coordination_share']} | {r['achieved_parallelism']} | "
            f"{r['parallel_efficiency']} | {r['census_conflicts']} | {r['commits']} | "
            f"{len(r['convicted'])} | {r['pages_stolen']} | {b.get('n_pages')}/{b.get('expected')} |")
    out.append("")
    for r in rows:
        out.append(f"## {r['name']}")
        out.append("")
        out.append("| phase | start (s) | end (s) | spread of arrival (s) |")
        out.append("|---|---|---|---|")
        for ph, v in r["phases"].items():
            out.append(f"| {ph} | {v['start_s']} | {v['end_s']} | {v['spread_s']} |")
        out.append("")
        out.append("| task | n | median (s) | max (s) | total rank-s |")
        out.append("|---|---|---|---|---|")
        for k, v in r["task_stats"].items():
            out.append(f"| {k} | {v['n']} | {v['median_s']} | {v['max_s']} | {v['total_s']} |")
        out.append("")
        out.append("Most expensive collectives (rank-seconds blocked, worst single wait):")
        out.append("")
        for w in r["worst_collectives"]:
            out.append(f"- `{w['label']}`: {w['rank_s']} rank-s, worst {w['max_wait_s']} s")
        out.append("")
        out.append(f"Transport: {r['commits']} commits ({r['commits_per_rank']} per rank), "
                   f"{r['commit_kinds']}; lock waits total {r['lock_wait_total_s']} s, "
                   f"max {r['lock_wait_max_s']} s; research claims {r['claims_won']}/"
                   f"{r['claim_attempts']} won.")
        out.append("")
        out.append(f"Faults: convicted {r['convicted']}, executors lost {r['executors_lost']}, "
                   f"pages stolen {r['stolen_pages']}; book {r['book']}.")
        out.append("")
    return "\n".join(out)


def figures(rows: list[dict[str, Any]], out: Path) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # pragma: no cover
        return []
    written = []
    ps = [r["p"] for r in rows]

    # 1. Phase timeline per scale (a Gantt of the population's phases).
    fig, axes = plt.subplots(len(rows), 1, figsize=(8, 1.4 * len(rows) + 1), sharex=True,
                             squeeze=False)
    for ax, r in zip(axes[:, 0], rows, strict=True):
        for i, (ph, v) in enumerate(r["phases"].items()):
            ax.barh(0, (v["end_s"] - v["start_s"]) / 60, left=v["start_s"] / 60, height=0.6,
                    color=plt.cm.tab10(i % 10), edgecolor="white")
            ax.text((v["start_s"] + v["end_s"]) / 120, 0, ph, ha="center", va="center",
                    fontsize=7, color="white")
        ax.set_yticks([])
        ax.set_ylabel(f"p={r['p']}", rotation=0, ha="right", va="center")
        ax.spines[["top", "right", "left"]].set_visible(False)
    axes[-1, 0].set_xlabel("minutes since first event")
    fig.tight_layout()
    fp = out / "phases.pdf"
    fig.savefig(fp)
    plt.close(fig)
    written.append(fp)

    # 2. Work versus coordination, rank-seconds and share.
    fig, ax = plt.subplots(figsize=(5, 3.2))
    work = [r["work_rank_s"] / 3600 for r in rows]
    blocked = [r["blocked_rank_s"] / 3600 for r in rows]
    x = range(len(rows))
    ax.bar(x, work, color="#4c72b0", label="agent work")
    ax.bar(x, blocked, bottom=work, color="#dd8452", label="blocked in collectives")
    ax.set_xticks(list(x), [f"p={p}" for p in ps])
    ax.set_ylabel("rank-hours")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fp = out / "work_vs_coordination.pdf"
    fig.savefig(fp)
    plt.close(fig)
    written.append(fp)

    # 3. Wall time, parallelism and transport commits against p.
    fig, axs = plt.subplots(1, 3, figsize=(9, 2.8))
    axs[0].plot(ps, [r["wall_h"] for r in rows], "o-")
    axs[0].set_ylabel("wall time (h)")
    axs[1].plot(ps, [r["achieved_parallelism"] or 0 for r in rows], "o-", label="achieved")
    axs[1].plot(ps, ps, "--", color="grey", label="ideal")
    axs[1].set_ylabel("parallelism")
    axs[1].legend(frameon=False, fontsize=8)
    axs[2].plot(ps, [r["commits"] for r in rows], "o-")
    axs[2].set_ylabel("commits on the job branch")
    for ax in axs:
        ax.set_xlabel("ranks p")
        ax.set_xticks(ps)
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fp = out / "scaling.pdf"
    fig.savefig(fp)
    plt.close(fig)
    written.append(fp)
    return written


def main(argv: list[str] | None = None) -> list[dict[str, Any]]:
    ap = argparse.ArgumentParser(description="E6 series analysis")
    ap.add_argument("--runs", default="e6-book-p16,e6-book-p32,e6-book-p64")
    ap.add_argument("--out", default=str(RUNS / "e6-series"))
    a = ap.parse_args(argv)
    rows = [r for r in (summarise_run(n.strip()) for n in a.runs.split(",") if n.strip()) if r]
    rows.sort(key=lambda r: r["p"] or 0)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "series.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    (out / "series.md").write_text(markdown(rows), encoding="utf-8")
    figs = figures(rows, out)
    print(json.dumps({"runs": [r["name"] for r in rows], "out": str(out),
                      "figures": [str(f) for f in figs]}, indent=2))
    return rows


if __name__ == "__main__":
    main()
