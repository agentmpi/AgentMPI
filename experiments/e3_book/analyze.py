"""Cross-scale analysis of the E3 runs: what changes as ``p`` goes 16, 32, 64.

The per-run analysis in ``ampi.analysis`` answers "what happened in this run".
This answers the question the experiment was actually run to ask, which no single
run can: **what does scaling the population do to the coordination?**

Five things are worth measuring across the series, and each is a claim the paper
makes that a single run cannot support.

*Does coordination cost grow with p, and how?*  The protocol's own overhead is
modelled as ``alpha`` per round; a journal-folding collective is one round
regardless of p, so the prediction is that coordination *share* is roughly flat
and coordination *seconds* grow only through the straggler term.  If share grows
steeply, the protocol is not paying for itself.

*Does disagreement grow with p?*  Every rank surveys its own segment, so a larger
population sees more of the book and meets more of the same terms.  The
prediction is that lifted conflicts grow with p --- and that is the argument for
lifting rather than for last-writer-wins merging, because the disagreements a
larger population finds are real and were previously being silently resolved.

*Does the straggler term dominate?*  A collective waits for its slowest member,
and executor latency is heavy tailed, so the expected wait grows like the maximum
of p heavy-tailed samples.  This is the term that decides whether a quorum is
worth having, and it is measured here rather than assumed.

*Is the work actually parallel?*  Achieved parallelism against p.  A run that
registers sixty-four ranks and achieves three is not a scaling result.

*Does research sharing pay?*  The window exists so a term is researched once
rather than p times.  The saving is measurable: terms researched against terms
that needed research, times the population that would otherwise have duplicated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ampi.analysis import analyse, load_events
from ampi.analysis import style as st

ROOT = Path(__file__).resolve().parent.parent.parent
RUNS = ROOT / "runs"

__all__ = ["load_series", "series_table", "render_series"]


def load_series(names: list[str]) -> list[Any]:
    out = []
    for name in names:
        trace = RUNS / name / "harness.trace.jsonl"
        if not trace.exists():
            continue
        meta_path = RUNS / name / "report.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        a = analyse(load_events(trace), name=name, meta=meta)
        out.append(a)
    return sorted(out, key=lambda a: a.world_size)


def _phase_seconds(a: Any) -> dict[str, float]:
    return {p.name: p.duration_s for p in a.phases}


def series_table(series: list[Any]) -> list[dict[str, Any]]:
    """One row per run: the quantities that only mean something across the series."""
    rows = []
    for a in series:
        by_op: dict[str, float] = {}
        for c in a.collectives:
            by_op[c.op] = by_op.get(c.op, 0.0) + c.rank_wait_s
        waits = sorted((c.max_wait_s for c in a.collectives), reverse=True)
        rows.append({
            "run": a.name,
            "p": a.world_size,
            "arm": (a.meta.get("arm") if a.meta else "") or "",
            "executor": (a.meta.get("executor") if a.meta else "") or "",
            "wall_s": round(a.wall_s, 1),
            "tasks": a.tasks.get("submitted", 0),
            "executors": len(a.executors),
            "collectives": len(a.collectives),
            "incomplete": len(a.incomplete_collectives),
            "work_rank_s": round(a.work_rank_seconds, 1),
            "blocked_rank_s": round(a.collective_rank_seconds, 1),
            "coordination_share": round(a.coordination_share, 4),
            "coordination_span_share": round(a.coordination_span_share, 4),
            "achieved_parallelism": round(a.concurrency.achieved_parallelism, 2),
            "parallel_efficiency": round(a.concurrency.parallel_efficiency, 4),
            "imbalance": round(a.imbalance, 2),
            "conflicts": a.conflicts_lifted,
            "conflicts_per_rank": round(a.conflicts_lifted / max(1, a.world_size), 3),
            "max_single_wait_s": round(waits[0], 1) if waits else 0.0,
            "reattachments": a.total_reattachments,
            "rejected": a.tasks.get("rejected", 0),
            "requeued": a.tasks.get("requeued", 0),
            "abandoned": a.tasks.get("abandoned", 0),
            "blocked_by_op": {k: round(v, 1) for k, v in sorted(by_op.items())},
            "phases_s": {k: round(v, 1) for k, v in _phase_seconds(a).items()},
        })
    return rows


def render_series(series: list[Any], out: Path) -> Path | None:
    """Four panels: cost, disagreement, parallelism, and where the time went."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if len(series) < 2:
        return None
    ps = [a.world_size for a in series]

    fig, axes = plt.subplots(2, 2, figsize=(9.6, 6.2))
    fig.patch.set_facecolor(st.BACKGROUND)
    for ax in axes.flat:
        ax.set_facecolor(st.PANEL)
        for spine in ax.spines.values():
            spine.set_color(st.LINE)
        ax.tick_params(colors=st.MUTED, labelsize=8)
        ax.xaxis.label.set_color(st.MUTED)
        ax.yaxis.label.set_color(st.MUTED)
        ax.title.set_color(st.FOREGROUND)
        ax.grid(True, color=st.LINE, linewidth=0.5, alpha=0.55)
        ax.set_axisbelow(True)

    ax = axes[0][0]
    ax.plot(ps, [a.coordination_share * 100 for a in series], "o-",
            color=st.ROLE_COLOR["collective"], label="rank-seconds blocked")
    ax.plot(ps, [a.coordination_span_share * 100 for a in series], "s--",
            color=st.ROLE_COLOR["consensus"], label="wall clock with coordination in flight")
    ax.set_xscale("log", base=2)
    ax.set_xticks(ps)
    ax.set_xticklabels([str(p) for p in ps])
    ax.set_xlabel("ranks")
    ax.set_ylabel("% ")
    ax.set_title("coordination cost", fontsize=9, loc="left")
    leg = ax.legend(frameon=False, fontsize=7)
    for t in leg.get_texts():
        t.set_color(st.MUTED)

    ax = axes[0][1]
    ax.plot(ps, [a.conflicts_lifted for a in series], "o-", color=st.ROLE_COLOR["consensus"])
    ax.set_xscale("log", base=2)
    ax.set_xticks(ps)
    ax.set_xticklabels([str(p) for p in ps])
    ax.set_xlabel("ranks")
    ax.set_ylabel("conflicts lifted")
    ax.set_title("disagreement found, not silently resolved", fontsize=9, loc="left")

    ax = axes[1][0]
    ax.plot(ps, [a.concurrency.achieved_parallelism for a in series], "o-",
            color=st.ROLE_COLOR["work"], label="achieved")
    ax.plot(ps, ps, ":", color=st.MUTED, label="ideal")
    ax.set_xscale("log", base=2)
    ax.set_yscale("log", base=2)
    ax.set_xticks(ps)
    ax.set_xticklabels([str(p) for p in ps])
    ax.set_xlabel("ranks")
    ax.set_ylabel("achieved parallelism")
    ax.set_title("speedup against one rank", fontsize=9, loc="left")
    leg = ax.legend(frameon=False, fontsize=7)
    for t in leg.get_texts():
        t.set_color(st.MUTED)

    ax = axes[1][1]
    ops = sorted({c.op for a in series for c in a.collectives})
    bottom = [0.0] * len(series)
    palette = [st.ROLE_COLOR["collective"], st.ROLE_COLOR["consensus"], st.ROLE_COLOR["work"],
               st.ROLE_COLOR["rma"], st.ROLE_COLOR["recovery"], st.ROLE_COLOR["message"],
               st.ROLE_COLOR["trouble"]]
    xs = list(range(len(series)))
    for i, op in enumerate(ops):
        vals = [
            sum(c.rank_wait_s for c in a.collectives if c.op == op) for a in series
        ]
        ax.bar(xs, vals, bottom=bottom, color=palette[i % len(palette)], label=op, width=0.6)
        bottom = [b + v for b, v in zip(bottom, vals, strict=True)]
    ax.set_xticks(xs)
    ax.set_xticklabels([f"p={p}" for p in ps])
    ax.set_ylabel("rank-seconds blocked")
    ax.set_title("which collective the waiting was in", fontsize=9, loc="left")
    leg = ax.legend(frameon=False, fontsize=6.5, ncol=2)
    for t in leg.get_texts():
        t.set_color(st.MUTED)

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="cross-scale analysis of the E3 series")
    ap.add_argument("--runs", required=True, help="comma-separated run names, any order")
    ap.add_argument("--out", default="runs/e3-series", help="directory for the series artifacts")
    ap.add_argument("--tex-prefix", default="EThree")
    a = ap.parse_args(argv)

    names = [n.strip() for n in a.runs.split(",") if n.strip()]
    series = load_series(names)
    if not series:
        raise SystemExit(f"none of {names} has a trace under runs/")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = series_table(series)
    (out / "series.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    render_series(series, out / "figures" / "scaling.pdf")
    render_series(series, out / "figures" / "scaling.png")

    # Per-run packages go beside their own run, not into the series directory.
    # Writing them here as well would duplicate every figure and metrics file, and
    # a reader who then found the two copies disagreeing --- because one was
    # regenerated and the other was not --- would have no way to tell which was
    # current. One artifact, one home.
    from ampi.analysis.report import write_all

    for an in series:
        write_all(
            an,
            RUNS / an.name / "analysis",
            tex_prefix=f"{a.tex_prefix}{an.world_size}",
            fmt="pdf",
        )

    lines = [f"% E3 series, generated from {', '.join(n.name for n in series)}"]
    for row in rows:
        p = row["p"]
        for key, value in (
            ("Wall", row["wall_s"]), ("Tasks", row["tasks"]), ("Execs", row["executors"]),
            ("Coord", round(row["coordination_share"] * 100, 1)),
            ("Par", row["achieved_parallelism"]), ("Conf", row["conflicts"]),
            ("Imbal", row["imbalance"]), ("MaxWait", row["max_single_wait_s"]),
        ):
            lines.append(f"\\newcommand{{\\{a.tex_prefix}{key}{p}}}{{{value}}}")
    (out / "generated.tex").write_text("\n".join(lines) + "\n", encoding="utf-8")

    header = ["run", "p", "wall_s", "tasks", "executors", "coordination_share",
              "achieved_parallelism", "imbalance", "conflicts", "max_single_wait_s"]
    print(" | ".join(header))
    for row in rows:
        print(" | ".join(str(row[h]) for h in header))


if __name__ == "__main__":
    main()
