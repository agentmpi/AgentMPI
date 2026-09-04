"""Render an :class:`~ampi.analysis.model.Analysis` as text a person will read.

Three renderers over one analysis, because the same numbers are consumed in three
places and regenerating them independently is how a paper comes to disagree with
its own repository.

``summary``  a terminal digest, for the operator watching a long run.
``markdown`` the run's committed report, beside its trace.
``latex``    macros and tables the paper inputs, so no figure is hand-copied.

The findings list is the part worth arguing about.  It is deliberately a list of
*flags with thresholds*, not a verdict: the tool says "this collective was
incomplete" or "achieved parallelism was 2.1x on 32 ranks", and a reader decides
whether that is expected for the configuration.  A tool that graded runs would be
wrong about the interesting ones, which are exactly the runs where something
unexpected happened and the harness author knows why.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model import Analysis

__all__ = ["findings", "summary", "markdown", "latex", "write_all"]

#: Achieved parallelism below this fraction of world size is flagged.  Not a
#: failure --- a barrier-heavy phase legitimately serialises --- but on a run
#: launched at p=64 it is the first thing to explain.
LOW_PARALLELISM = 0.35
#: Coordination above this share of rank-seconds is flagged.
HIGH_COORDINATION = 0.5
#: Context occupancy above this is flagged per rank.
HIGH_CONTEXT = 0.8


def findings(a: Analysis) -> list[dict[str, str]]:
    """Mechanical observations about a run, each with the number behind it."""
    out: list[dict[str, str]] = []

    def add(level: str, text: str) -> None:
        out.append({"level": level, "text": text})

    if a.incomplete_collectives:
        names = ", ".join(
            f"{c.op}:{c.label} ({c.n_participants}/{c.size}, absent {c.absent})"
            for c in a.incomplete_collectives[:6]
        )
        add("error", f"{len(a.incomplete_collectives)} collective(s) some rank never reached: {names}")
    if a.failed_ranks:
        add("error", f"ranks {a.failed_ranks} raised an error and did not complete their phase")
    if a.inert_ranks:
        add(
            "warn",
            f"ranks {a.inert_ranks} registered but did no observable work: "
            "a launched population is not a participating one, and the two are "
            "identical in any summary that reports only a count",
        )

    par = a.concurrency
    if a.has_work_spans and a.world_size and par.achieved_parallelism < LOW_PARALLELISM * a.world_size:
        add(
            "warn",
            f"achieved parallelism was {par.achieved_parallelism:.2f}x on {a.world_size} ranks "
            f"({par.parallel_efficiency:.0%} efficiency); "
            f"{par.serial_fraction_of_busy:.0%} of active time had exactly one rank busy",
        )
    if a.coordination_share > HIGH_COORDINATION:
        add(
            "warn",
            f"{a.coordination_share:.0%} of available rank-seconds were spent blocked in "
            f"collectives ({a.collective_rank_seconds:.0f} of "
            f"{a.world_size * a.wall_s:.0f} rank-seconds)",
        )
    if a.coordination_is_underreported:
        add(
            "note",
            "coordination cost is understated: a collective is recorded on completion, "
            "so ranks that blocked and then failed contribute nothing to the total",
        )

    hot = [p for p in a.ranks.values() if p.context_occupancy > HIGH_CONTEXT]
    if hot:
        add(
            "warn",
            f"{len(hot)} rank(s) exceeded {HIGH_CONTEXT:.0%} of their context budget "
            f"(worst: rank {max(hot, key=lambda p: p.context_occupancy).rank} at "
            f"{max(p.context_occupancy for p in hot):.0%})",
        )
    degraded = sum(p.n_degrade for p in a.ranks.values())
    if degraded:
        add("note", f"{degraded} payload(s) were degraded to a view to fit a context budget")

    if a.imbalance > 1.5 and a.has_work_spans:
        add(
            "warn",
            f"load imbalance {a.imbalance:.2f}: the busiest rank worked "
            f"{a.imbalance:.1f}x the population mean",
        )

    owed = a.straggler_cost
    if owed:
        worst, cost = next(iter(owed.items()))
        if cost > 0.05 * max(a.wall_s, 1e-9) * max(1, a.world_size):
            add(
                "note",
                f"rank {worst} was last into the collectives it joined, costing its peers "
                f"{cost:.0f} rank-seconds of waiting",
            )

    if a.tasks.get("rejected"):
        add(
            "note",
            f"{a.tasks['rejected']} task result(s) were rejected for violating their contract "
            "and had to be resubmitted",
        )
    if a.tasks.get("requeued"):
        add(
            "note",
            f"{a.tasks['requeued']} task(s) were requeued after a claim expired: "
            "an executor session ended holding work",
        )
    if a.tasks.get("abandoned"):
        add("warn", f"{a.tasks['abandoned']} task(s) were abandoned by their executor")
    starved = a.starved_tasks
    if starved:
        ranks = sorted({t["rank"] for t in starved})
        add(
            "error",
            f"{len(starved)} task(s) were published and never claimed by any executor "
            f"(ranks {ranks}): the population was not fully staffed, which is a "
            "pool-sizing problem outside the protocol and must not be read as a slow harness",
        )
    wasted = a.wasted_submissions
    if wasted:
        add(
            "error",
            f"{len(wasted)} result(s) were submitted after the rank that needed them had "
            f"already failed, and were discarded (ranks {sorted({w['rank'] for w in wasted})}): "
            "the task deadline was shorter than the executor supply could satisfy, so the "
            "run failed while its workers were still productively working",
        )
    if a.max_claim_wait_s > 60:
        add(
            "note",
            f"the longest a task waited in the queue before an executor claimed it was "
            f"{a.max_claim_wait_s:.0f}s; queue waits enter measured latency but not token counts",
        )
    if a.total_reattachments:
        add(
            "note",
            f"{a.total_reattachments} rank role(s) were taken over by a fresh session mid-run; "
            "the collectives did not notice",
        )
    if a.conflicts_lifted:
        add(
            "note",
            f"{a.conflicts_lifted} conflict(s) were lifted by reductions rather than "
            "silently resolved by whichever branch merged last",
        )
    if not a.has_work_spans:
        add(
            "note",
            "no executor spans: occupancy is reconstructed from task spans that do not exist, "
            "so concurrency figures describe the instrumentation, not agent behaviour",
        )
    if not out:
        add("ok", "nothing flagged: every collective completed and no rank failed")
    return out


def _headline(a: Analysis) -> list[tuple[str, str]]:
    par = a.concurrency
    return [
        ("world size", str(a.world_size)),
        ("ranks seen", str(a.n_ranks_seen)),
        ("wall clock", f"{a.wall_s:.1f} s"),
        ("events", str(a.n_events)),
        ("agent tasks", str(a.tasks.get("submitted", 0))),
        ("distinct executors", str(len(a.executors))),
        ("collectives", str(len(a.collectives))),
        ("work rank-seconds", f"{a.work_rank_seconds:.0f}"),
        ("blocked rank-seconds", f"{a.collective_rank_seconds:.0f}"),
        ("coordination share", f"{a.coordination_share:.1%}"),
        ("achieved parallelism", f"{par.achieved_parallelism:.2f}x"),
        ("parallel efficiency", f"{par.parallel_efficiency:.1%}"),
        ("load imbalance", f"{a.imbalance:.2f}"),
        ("conflicts lifted", str(a.conflicts_lifted)),
        ("rank reattachments", str(a.total_reattachments)),
        ("degraded", "yes" if a.degraded else "no"),
    ]


def summary(a: Analysis) -> str:
    """A terminal digest for the operator watching a run."""
    lines = [f"AgentMPI run {a.name or a.job}", "=" * 60]
    for key, value in _headline(a):
        lines.append(f"  {key:<24} {value}")
    lines.append("")
    lines.append("  findings")
    for f in findings(a):
        lines.append(f"    [{f['level']}] {f['text']}")
    if a.phases:
        lines.append("")
        lines.append("  phases")
        for ph in a.phases:
            lines.append(f"    {ph.duration_s:8.1f}s  {len(ph.ranks):>4} ranks  {ph.name}")
    costly = sorted(a.collectives, key=lambda c: -c.rank_wait_s)[:8]
    if costly and costly[0].rank_wait_s > 0:
        lines.append("")
        lines.append("  most expensive collectives (rank-seconds blocked)")
        for c in costly:
            if c.rank_wait_s <= 0:
                continue
            who = f"last in r{c.straggler}" if c.straggler is not None else ""
            lines.append(
                f"    {c.rank_wait_s:9.1f}  {c.op}:{c.label}"
                f"  ({c.n_participants}/{c.size})  {who}"
            )
    return "\n".join(lines)


def _table(rows: list[list[str]], header: list[str]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def markdown(a: Analysis, *, figures: dict[str, Path] | None = None) -> str:
    """The run's committed report, written beside its trace."""
    par = a.concurrency
    parts = [
        f"# Run `{a.name or a.job}`",
        "",
        "Generated by `ampi analyze` from the event log alone. Every number here is "
        "derived from `*.trace.jsonl`; nothing is copied from a harness report.",
        "",
        "## Headline",
        "",
        _table([[k, v] for k, v in _headline(a)], ["quantity", "value"]),
        "",
        "## Findings",
        "",
    ]
    for f in findings(a):
        parts.append(f"- **{f['level']}** — {f['text']}")
    parts.append("")

    if figures:
        parts += ["## Figures", ""]
        for name, path in figures.items():
            parts.append(f"![{name}]({Path(path).name})")
            parts.append("")

    if a.phases:
        parts += [
            "## Phases",
            "",
            "Segmented by the harness's own `memo` events, so the breakdown reflects the "
            "program's structure rather than a guess made from event kinds.",
            "",
            _table(
                [
                    [p.name, f"{p.t_start:.1f}", f"{p.duration_s:.1f}", str(len(p.ranks))]
                    for p in a.phases
                ],
                ["phase", "starts (s)", "duration (s)", "ranks"],
            ),
            "",
        ]

    costed = a.collectives
    if costed:
        parts += [
            "## Collectives",
            "",
            "`blocked` is rank-seconds summed over participants; `slowest` is the single "
            "longest wait, which is the critical-path figure and must never be summed. "
            "`last in` names the rank whose arrival released the call.",
            "",
            _table(
                [
                    [
                        f"`{c.op}:{c.label}`",
                        c.algorithm or "journal",
                        f"{c.n_participants}/{c.size}",
                        f"{c.rank_wait_s:.1f}",
                        f"{c.max_wait_s:.1f}",
                        f"{c.arrival_skew_s:.1f}",
                        "" if c.straggler is None else f"r{c.straggler}",
                        str(c.conflicts or ""),
                    ]
                    for c in sorted(costed, key=lambda c: -c.rank_wait_s)[:40]
                ],
                ["collective", "algorithm", "arrived", "blocked (rank-s)",
                 "slowest (s)", "arrival skew (s)", "last in", "conflicts"],
            ),
            "",
        ]

    ranks = a.participating_ranks
    if ranks:
        parts += [
            "## Ranks",
            "",
            _table(
                [
                    [
                        f"r{p.rank}",
                        str(p.n_tasks),
                        f"{p.busy_s:.1f}",
                        f"{p.blocked_s:.1f}",
                        f"{p.occupancy:.0%}",
                        f"{p.context_used}/{p.context_budget}" if p.context_budget else "-",
                        str(p.reattachments),
                        str(p.n_trouble),
                        ",".join(sorted(p.executors)[:2]) or "-",
                    ]
                    for p in (a.ranks[r] for r in ranks)
                ],
                ["rank", "tasks", "busy (s)", "blocked (s)", "occupancy",
                 "context", "reattach", "trouble", "executor"],
            ),
            "",
        ]

    parts += [
        "## Concurrency",
        "",
        f"Peak {par.max_busy} ranks busy simultaneously; mean {par.mean_busy_when_active:.2f} "
        f"while any rank was active. Achieved parallelism {par.achieved_parallelism:.2f}x against "
        f"a world size of {a.world_size} ({par.parallel_efficiency:.1%} efficiency). "
        f"{par.idle_fraction:.1%} of the rank-seconds the run reserved went unused.",
        "",
        "## Event vocabulary",
        "",
        _table(
            [[f"`{k}`", str(v)] for k, v in sorted(a.kind_counts.items(), key=lambda kv: -kv[1])],
            ["kind", "count"],
        ),
        "",
    ]
    return "\n".join(parts)


def _tex_escape(s: str) -> str:
    for a_, b in (("\\", r"\textbackslash{}"), ("_", r"\_"), ("%", r"\%"),
                  ("&", r"\&"), ("#", r"\#"), ("$", r"\$")):
        s = s.replace(a_, b)
    return s


def latex(a: Analysis, *, prefix: str) -> str:
    """Macros the paper inputs, so no number in it is typed by hand."""
    par = a.concurrency
    values: dict[str, str] = {
        "Size": str(a.world_size),
        "RanksSeen": str(a.n_ranks_seen),
        "WallS": f"{a.wall_s:.0f}",
        "WallH": f"{a.wall_s / 3600:.1f}",
        "Events": str(a.n_events),
        "Tasks": str(a.tasks.get("submitted", 0)),
        "Executors": str(len(a.executors)),
        "Collectives": str(len(a.collectives)),
        "WorkRankSeconds": f"{a.work_rank_seconds:.0f}",
        "BlockedRankSeconds": f"{a.collective_rank_seconds:.0f}",
        "CoordShare": f"{a.coordination_share * 100:.1f}",
        "SpanShare": f"{a.coordination_span_share * 100:.1f}",
        "Parallelism": f"{par.achieved_parallelism:.2f}",
        "Efficiency": f"{par.parallel_efficiency * 100:.1f}",
        "SerialFraction": f"{par.serial_fraction_of_busy * 100:.1f}",
        "MaxBusy": str(par.max_busy),
        "Imbalance": f"{a.imbalance:.2f}",
        "Conflicts": str(a.conflicts_lifted),
        "Reattachments": str(a.total_reattachments),
        "Incomplete": str(len(a.incomplete_collectives)),
        "Rejected": str(a.tasks.get("rejected", 0)),
        "Requeued": str(a.tasks.get("requeued", 0)),
        "Abandoned": str(a.tasks.get("abandoned", 0)),
        "Trouble": str(len(a.trouble)),
        "Recovery": str(len(a.recovery)),
    }
    lines = [f"% generated by ampi.analysis.report from run {a.name or a.job}"]
    for key, value in values.items():
        lines.append(f"\\newcommand{{\\{prefix}{key}}}{{{_tex_escape(value)}}}")
    return "\n".join(lines) + "\n"


def write_all(
    a: Analysis,
    outdir: str | Path,
    *,
    tex_prefix: str = "",
    fmt: str = "pdf",
) -> dict[str, Path]:
    """Write metrics, figures, report and macros for one run into ``outdir``."""
    try:
        from .figures import render_all
    except ImportError:  # matplotlib is an extra; the report is still worth writing
        render_all = None

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    metrics = out / "metrics.json"
    metrics.write_text(json.dumps(a.to_dict(), indent=2, default=str), encoding="utf-8")
    written["metrics"] = metrics

    figs = render_all(a, out / "figures", fmt=fmt) if render_all is not None else {}
    written.update({f"figure:{k}": v for k, v in figs.items()})

    report = out / "report.md"
    report.write_text(
        markdown(a, figures={k: Path("figures") / v.name for k, v in figs.items()}),
        encoding="utf-8",
    )
    written["report"] = report

    if tex_prefix:
        tex = out / "generated.tex"
        tex.write_text(latex(a, prefix=tex_prefix), encoding="utf-8")
        written["latex"] = tex

    findings_path = out / "findings.json"
    findings_path.write_text(json.dumps(findings(a), indent=2), encoding="utf-8")
    written["findings"] = findings_path
    return written


def to_dict(a: Analysis) -> dict[str, Any]:
    return a.to_dict()
