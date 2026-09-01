"""Build the analysis package for one run: metrics, figures, and a LaTeX document.

Five hundred runs cannot be analysed by hand, and they should not be: the mechanical part ---
extracting metrics, checking each collective against its cost model, drawing the timeline,
tabulating per-rank occupancy --- is identical for every run and is exactly what a program does
better than a person. What a program cannot do is say what a run *means*: whether an idle rank
is a scheduling artefact or a design flaw, whether a 20-second barrier skew is the point of the
experiment or a symptom of something else.

So the split is deliberate. This script writes:

``metrics.json``
    Everything computed, so any claim in the document can be traced to a number and any
    number to the event log.

``figures/*.pdf``
    Timeline, concurrency, communication matrix, per-rank profile, and collective validation,
    drawn in the viewer's palette so they read as the same artefact as the dashboard.

``generated.tex``
    The facts as LaTeX: configuration, headline metrics, per-rank table, collective table with
    model comparison, and a findings list flagging anything mechanically detectable ---
    incomplete collectives, accounting disagreements, stray ranks, starved claims.

``analysis.tex``
    Written once and never overwritten, because this is where the interpretation lives. It
    inputs ``generated.tex`` and carries prose sections for an analyst to fill in. Re-running
    the script refreshes every derived artefact around it without touching a word of judgement.

    python3 scripts/analyze_run.py --run real-tr-p8-full
    python3 scripts/analyze_run.py --all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import plot_run  # noqa: E402
from agentmpi import analysis as an  # noqa: E402

EVENTS_DIR = REPO / "traces" / "events"
ANALYSIS_DIR = REPO / "analysis" / "runs"
MANIFEST = REPO / "traces" / "manifest.json"


def tex_escape(s: str) -> str:
    """Escape for LaTeX text mode. Run names contain underscores; every one of them would
    otherwise be a compile error or a silent subscript."""
    out = []
    for ch in str(s):
        if ch in "&%$#_{}":
            out.append("\\" + ch)
        elif ch == "~":
            out.append("\\textasciitilde{}")
        elif ch == "^":
            out.append("\\textasciicircum{}")
        elif ch == "\\":
            out.append("\\textbackslash{}")
        else:
            out.append(ch)
    return "".join(out)


def mono(s: str) -> str:
    return r"\texttt{" + tex_escape(s) + "}"


def num(x: Any, digits: int = 2) -> str:
    if x is None:
        return "--"
    if isinstance(x, bool):
        return "yes" if x else "no"
    if isinstance(x, int):
        return f"{x:,}"
    try:
        value = float(x)
    except (TypeError, ValueError):
        return tex_escape(str(x))
    if value != value:  # NaN
        return "--"
    return f"{value:,.{digits}f}"


# --------------------------------------------------------------------------------------
# Findings: everything a program can notice without knowing what the run was for
# --------------------------------------------------------------------------------------


def findings(a: an.Analysis) -> list[tuple[str, str]]:
    """Mechanically detectable observations, as ``(severity, prose)``.

    These are not conclusions. They are the things worth looking at, surfaced so an analyst
    starts from what is anomalous rather than from a blank page. Severity is only about how
    strongly the run departs from what its own configuration implies.
    """
    out: list[tuple[str, str]] = []

    if a.ok is False:
        out.append(("critical", f"The job recorded failure: {len(a.failed_ranks)} of {a.world_size} ranks were marked failed."))
    if a.max_claim_wait_s > 0:
        out.append((
            "critical",
            f"At least one rank waited {num(a.max_claim_wait_s, 0)}\\,s for an agent to claim its task "
            f"before the broker gave up. That is a pool-sizing failure outside the protocol, not a "
            f"slow run.",
        ))
    for c in a.incomplete_collectives:
        out.append((
            "warning",
            f"{mono(c.op + '/' + c.algorithm)}"
            + (f" (label {mono(c.label)})" if c.label else "")
            + f" was reached by {c.n_participants} of {c.size} ranks, so it never completed.",
        ))
    for c in a.misreported_collectives:
        out.append((
            "warning",
            f"{mono(c.op + '/' + c.algorithm)} reports {num(c.effective_messages)} messages but the "
            f"fabric logged {num(c.logged_messages)}: the collective's own accounting disagrees with "
            f"the traffic it produced.",
        ))
    orphans = [e for e in a.trouble if e["kind"] == "msg.orphaned"]
    if orphans:
        states = sorted({str(e["payload"].get("dst_state")) for e in orphans})
        out.append((
            "critical",
            f"{len(orphans)} message(s) were delivered to ranks that had already reached a terminal "
            f"state ({', '.join(states)}), so nothing will ever read them. This is the signature of a "
            f"contribution arriving after its group moved on --- usually a recovery path given a "
            f"deadline longer than the operation it was meant to rescue.",
        ))
    stray = a.stray_ranks
    if stray:
        out.append((
            "warning",
            f"Ranks {', '.join(str(r) for r in stray)} appear in the log without participating; the "
            f"declared world size is {a.world_size}. Shared worker pools let a worker register "
            f"against the wrong job, which inflates the apparent rank count.",
        ))

    conc = a.concurrency
    # Only claim serial execution where occupancy is actually observable. The in-process executors
    # emit no broker events, so their busy time is near zero however hard they worked, and asserting
    # "this ran serially" from that is a statement about the instrumentation: tr-smoke's four ranks
    # demonstrably interleave from 6 ms onward while their measured busy time is 1 ms.
    if a.work_spans and conc.max_busy == 1 and a.world_size > 1 and a.has_broker_executor:
        out.append(("warning", f"Concurrency never exceeded one rank despite a world size of {a.world_size}: this ran serially."))
    elif a.work_spans and conc.max_busy <= 1 and a.world_size > 1 and not a.has_broker_executor:
        out.append((
            "note",
            f"Occupancy is not measurable for this run: its executors "
            f"({', '.join(sorted(a.executors)) or 'none'}) emit no broker claim events, so busy time, "
            f"achieved parallelism, and the idle fraction are artefacts of the instrumentation rather "
            f"than properties of the run.",
        ))
    elif a.work_spans and a.world_size > 1 and conc.parallel_efficiency < 0.4 and a.has_broker_executor:
        out.append((
            "note",
            f"Parallel efficiency is {num(conc.parallel_efficiency * 100, 1)}\\%: "
            f"{num(conc.idle_fraction * 100, 1)}\\% of the rank-seconds paid for went unused.",
        ))
    if a.incomplete_collectives or a.ok is False:
        out.append((
            "warning",
            "The coordination figures count time inside \\emph{completed} collectives only, and this "
            "run has ranks that never completed one. A rank records a collective on completion, so "
            "a rank that blocked and then timed out contributes nothing --- the reported share is a "
            "floor, and on a badly degraded run a very loose one.",
        ))
    if a.undurated_collectives:
        kinds = sorted({c.op for c in a.undurated_collectives})
        out.append((
            "warning",
            f"{len(a.undurated_collectives)} collective(s) completed but recorded no duration "
            f"({', '.join(mono(k) for k in kinds)}), so they contribute nothing to the coordination "
            f"figures even though every participant blocked in them. The reported coordination share "
            f"is short by exactly their blocking; it can be recovered from the event timestamps.",
        ))
    if a.coordination_share > 0.5:
        out.append((
            "note",
            f"Collectives account for {num(a.coordination_share * 100, 1)}\\% of the run's rank-seconds, "
            f"so more of the pool's time went to coordination than to work.",
        ))
    fit = getattr(a.calibration, "fit_method", "default")
    if fit == "median_fallback":
        beta = getattr(a.calibration, "fit_rejected_beta", None)
        alpha = getattr(a.calibration, "fit_rejected_alpha", None)
        r2 = getattr(a.calibration, "fit_rejected_r2", None)
        # Name the coefficient that actually failed. Asserting a negative slope unconditionally was
        # wrong on every translation run: their slopes were positive and the intercept was negative.
        # The two mean opposite things, so conflating them misdiagnoses the run.
        if beta is not None and beta <= 0:
            cause = (
                f"the slope was negative ($\\beta={num(beta, 4)}$\\,s/token), meaning latency fell as "
                f"output grew --- so something outside the model dominated the measurement, typically "
                f"queueing, whose wait enters the latency but not the token count"
            )
        elif alpha is not None and alpha <= 0:
            cause = (
                f"the slope was positive but the intercept was negative "
                f"($\\alpha={num(alpha, 2)}$\\,s), so the fitted line passes below the origin. That is "
                f"physically impossible and statistically unremarkable on a narrow token range, and "
                f"the guard rejects it rather than publish a negative fixed cost"
            )
        else:
            cause = "the regression was rejected by the sign guard"
        out.append((
            "warning",
            f"The cost model's $\\alpha$ and $\\beta$ here are a median fallback, not a fit: {cause}"
            + (f", on a fit with $R^2={num(r2, 3)}$" if r2 is not None else "")
            + ". Any latency prediction from this run's calibration is a median, not a model.",
        ))
    elif fit == "median_only":
        out.append((
            "note",
            "Too few distinct output sizes to fit $\\beta$, so the reported latency parameters are "
            "medians rather than a regression.",
        ))
    if a.imbalance > 1.5:
        out.append(("note", f"Load imbalance is {num(a.imbalance)}$\\times$: the slowest rank was busy far longer than the mean."))

    lossy = [c for c in a.collectives if c.divergence_risk]
    if lossy:
        out.append((
            "note",
            f"{len(lossy)} collective(s) ran a lossy reduction operator, where the result depends on "
            f"the order of folding and repeated runs need not agree.",
        ))
    rejections = sum(p.context_rejections for p in a.ranks.values())
    if rejections:
        out.append(("warning", f"{num(rejections)} artefact(s) were refused admission to a rank's context: back-pressure was active."))
    violations = sum(p.n_contract_violations for p in a.ranks.values())
    if violations:
        out.append(("note", f"{num(violations)} contract violation(s): an agent returned something the declared schema rejected."))
    retries = sum(p.n_retries for p in a.ranks.values())
    if retries:
        out.append(("note", f"{num(retries)} agent call(s) needed more than one attempt."))
    if a.trouble and not a.degraded:
        out.append(("note", f"{len(a.trouble)} trouble event(s) occurred but the run still completed: the harness absorbed them."))
    if a.total_reattachments:
        peak = max(p.max_incarnation for p in a.ranks.values())
        out.append((
            "ok",
            f"{num(a.total_reattachments)} rank reattachment(s) occurred, up to incarnation {peak}: "
            f"agent processes were replaced mid-run while the rank roles, their mailboxes, and "
            f"their context accounts persisted.",
        ))

    agree, checked = a.model_checks
    if checked and agree == checked:
        out.append(("ok", f"All {checked} checkable collective(s) sent exactly the number of messages their closed-form cost expression predicts."))
    elif checked:
        out.append(("critical", f"{checked - agree} of {checked} collectives disagree with the cost model on message count."))
    return out


SEVERITY_LABEL = {
    "critical": r"\finding{critical}",
    "warning": r"\finding{warning}",
    "note": r"\finding{note}",
    "ok": r"\finding{ok}",
}


# --------------------------------------------------------------------------------------
# Generated LaTeX
# --------------------------------------------------------------------------------------


def _config_table(a: an.Analysis) -> str:
    rows = [
        ("run", mono(a.name)),
        ("experiment", mono(a.experiment or "--")),
        ("campaign label", mono(a.label or "--")),
        ("declared world size", num(a.world_size)),
        ("ranks appearing in the log", num(a.n_ranks_seen)),
        # Built piecewise because the multiplication sign is math and the executor name is
        # verbatim; wrapping the whole cell in \texttt and escaping it prints the markup.
        (
            "executors",
            ", ".join(rf"{mono(k)}~$\times$~{v}" for k, v in sorted(a.executors.items())) or "--",
        ),
        ("events", num(a.n_events)),
        ("wall time", num(a.wall_s) + r"\,s"),
        ("job outcome", "--" if a.ok is None else ("completed" if a.ok else r"\textbf{failed}")),
    ]
    body = "\n".join(rf"  {k} & {v} \\" for k, v in rows)
    return (
        "\\begin{center}\n\\begin{tabular}{@{}ll@{}}\n\\toprule\n"
        "  \\textbf{property} & \\textbf{value} \\\\\n\\midrule\n"
        f"{body}\n\\bottomrule\n\\end{{tabular}}\n\\end{{center}}\n"
    )


def _headline_table(a: an.Analysis) -> str:
    c = a.concurrency
    s = a.summary
    rows = [
        ("achieved parallelism", num(c.achieved_parallelism) + r"$\times$", "total busy time over wall time"),
        ("parallel efficiency", num(c.parallel_efficiency * 100, 1) + r"\%", "achieved parallelism per rank"),
        ("idle fraction", num(c.idle_fraction * 100, 1) + r"\%", "rank-seconds paid for and unused"),
        ("peak ranks busy", num(c.max_busy), f"of {a.world_size} in the world"),
        ("serial fraction of active time", num(c.serial_fraction_of_busy * 100, 1) + r"\%", "time with exactly one rank busy"),
        ("load imbalance", num(a.imbalance) + r"$\times$", "slowest rank's busy time over the mean"),
        (
            "coordination share",
            num(a.coordination_share * 100, 1) + r"\%",
            "rank-seconds blocked in collectives, of those available",
        ),
        (
            "coordination in flight",
            num(a.coordination_span_share * 100, 1) + r"\%",
            "wall clock with any rank inside a collective",
        ),
        (
            "rank reattachments",
            num(a.total_reattachments),
            "fresh agent processes that took over a rank role",
        ),
        ("agent calls", num(s.n_agent_calls), "invocations that reached a model"),
        ("agent latency p50 / p95", num(s.as_dict()["agent_latency_p50"]) + " / " + num(s.as_dict()["agent_latency_p95"]) + r"\,s", "per invocation"),
        ("messages", num(s.n_messages), f"{num(a.eager_messages)} eager, {num(a.rendezvous_messages)} rendezvous"),
        ("tokens sent", num(s.tokens_sent), f"{num(a.tokens_deferred)} deferred under rendezvous"),
        ("tokens in / out", num(s.tokens_in) + " / " + num(s.tokens_out), "prompt and completion"),
        ("cost", r"\$" + num(s.usd, 4), num(a.usd_per_ktoken_out, 4) + r"\,\$/kTok out"),
    ]
    body = "\n".join(rf"  {k} & {v} & {d} \\" for k, v, d in rows)
    return (
        "\\begin{center}\n\\begin{tabular}{@{}lll@{}}\n\\toprule\n"
        "  \\textbf{metric} & \\textbf{value} & \\textbf{meaning} \\\\\n\\midrule\n"
        f"{body}\n\\bottomrule\n\\end{{tabular}}\n\\end{{center}}\n"
    )


def _rank_table(a: an.Analysis, limit: int = 40) -> str:
    profiles = [a.ranks[r] for r in a.participating_ranks][:limit]
    if not profiles:
        return "No rank recorded any activity.\n"
    body = "\n".join(
        rf"  {p.rank} & {num(p.busy_s)} & {num(p.occupancy * 100, 1)} & {num(p.n_work)} & "
        rf"{num(p.n_agent_calls)} & {num(p.sent)} & {num(p.recv)} & {num(p.tokens_sent)} & "
        rf"{num(p.context_occupancy * 100, 1)} & {tex_escape(p.state)} \\"
        for p in profiles
    )
    caption = (
        "Per-rank behaviour. Occupancy is busy time over the rank's own lifetime, so a rank that "
        "joined late is not penalised for the time before it existed."
    )
    return (
        "\\begin{center}\n\\begin{tabular}{@{}rrrrrrrrrl@{}}\n\\toprule\n"
        "  rank & busy (s) & occ.\\ (\\%) & tasks & calls & sent & recv & tok.\\ sent & ctx (\\%) & state \\\\\n"
        "\\midrule\n" + body + "\n\\bottomrule\n\\end{tabular}\n\\end{center}\n"
        f"\\smallskip\n\\noindent\\footnotesize {caption}\\normalsize\n"
    )


def _collective_table(a: an.Analysis, limit: int = 60) -> str:
    if not a.collectives:
        return "This run invoked no collectives.\n"
    rows = []
    for c in a.collectives[:limit]:
        actual = c.logged_messages if c.logged_messages is not None else c.effective_messages
        if c.messages_agree is True:
            verdict = r"\modelok"
        elif c.messages_agree is False:
            verdict = r"\modelbad"
        else:
            verdict = r"\modelna"
        rows.append(
            rf"  {tex_escape(c.op)} & {tex_escape(c.algorithm)} & {tex_escape(c.label or '--')} & "
            rf"{num(c.size)} & {num(c.n_participants)} & {num(c.rounds)} & "
            rf"{num(c.predicted_rounds)} & {num(actual)} & {num(c.predicted_messages)} & "
            rf"{num(c.tokens)} & {num(c.wall_s)} & {num(c.skew_s)} & {verdict} \\"
        )
    note = (
        "Messages are the count the fabric logged, attributed to each invocation through its "
        "internal tag, rather than the count the collective reported --- the two are independently "
        "produced, and preferring the log is what makes this a check rather than a restatement. "
        "A dash in the model column means the comparison is not meaningful: either no closed form "
        "exists for that algorithm, or the invocation never completed."
    )
    return (
        "\\begin{center}\\footnotesize\n\\begin{tabular}{@{}lllrrrrrrrrrc@{}}\n\\toprule\n"
        "  op & algorithm & label & $p$ & seen & rounds & pred. & msgs & pred. & tokens & "
        "wall (s) & skew (s) & model \\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabular}\\normalsize\n\\end{center}\n"
        f"\\smallskip\n\\noindent\\footnotesize {note}\\normalsize\n"
    )


def _findings_block(a: an.Analysis) -> str:
    items = findings(a)
    if not items:
        return "Nothing anomalous was detected mechanically.\n"
    lines = [rf"  \item {SEVERITY_LABEL[sev]} {text}" for sev, text in items]
    return "\\begin{itemize}[leftmargin=1.4em]\n" + "\n".join(lines) + "\n\\end{itemize}\n"


FIGURE_CAPTIONS = {
    "timeline": (
        "Execution timeline, one lane per rank. Bars are intervals when a rank held a task; ticks "
        "are messages; diamonds are window operations, lifecycle transitions, failures, and "
        "recovery actions. Drawn in the trace viewer's palette so the same shapes read the same way "
        "in both."
    ),
    "concurrency": (
        "Ranks simultaneously busy over time. The dotted line is the declared world size and the "
        "dashed line the achieved parallelism; the gap between them is what the run paid for and "
        "did not use. Faint vertical lines mark collective invocations."
    ),
    "comm": (
        "Communication matrix: tokens sent from each rank to each rank. The algorithm's shape is "
        "visible here --- a tree concentrates traffic on a root, a ring forms a diagonal band, and an "
        "unintended all-to-all fills the plane."
    ),
    "ranks": (
        "Per-rank occupancy and context pressure. Left: busy time, with the remainder to wall time "
        "in grey. Right: context occupancy against budget, amber above half and red above 80\\%, "
        "where admission pressure starts to reject artefacts."
    ),
    "collectives": (
        "Logged message count against the closed-form prediction, per invocation. A red diamond "
        "marks a disagreement between the implementation and its own cost model."
    ),
}


def write_generated(a: an.Analysis, figures: dict[str, Path], outdir: Path) -> Path:
    parts = [
        "% Generated by scripts/analyze_run.py. Do not edit; edit analysis.tex instead.",
        "",
        r"\section{What this run is}",
        _config_table(a),
        r"\section{Headline measurements}",
        _headline_table(a),
        r"\section{What the analysis notices}",
        _findings_block(a),
    ]

    for name in ("timeline", "concurrency", "ranks", "comm", "collectives"):
        path = figures.get(name)
        if path is None:
            continue
        parts += [
            r"\begin{figure}[htbp]",
            r"  \centering",
            rf"  \includegraphics[width=\linewidth]{{{path.parent.name}/{path.name}}}",
            rf"  \caption{{{FIGURE_CAPTIONS[name]}}}",
            rf"  \label{{fig:{name}}}",
            r"\end{figure}",
            "",
        ]

    parts += [
        r"\section{Per-rank detail}",
        _rank_table(a),
        r"\section{Collectives, against the cost model}",
        _collective_table(a),
    ]
    out = outdir / "generated.tex"
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return out


SKELETON = r"""%% Analysis of run: {name}
%%
%% generated.tex holds every derived fact and is rewritten by
%%   python3 scripts/analyze_run.py --run {name}
%% This file is never overwritten: it is where interpretation lives.
\documentclass[11pt]{{article}}
\input{{../../preamble}}

\title{{\textbf{{{title}}}\\[2pt]\large An AgentMPI run analysis}}
\author{{}}
\date{{}}

\begin{{document}}
\maketitle

\begin{{abstract}}
\TODO{{One paragraph: what this run was for, what it shows, and the single most
important thing a reader should take away. Write this last.}}
\end{{abstract}}

\input{{generated}}

\section{{Reading the timeline}}
\TODO{{What shape does the timeline have, and why? Address the visual structure a
reader sees in the dashboard: where ranks idle, where work bunches, what the gaps
are waiting for. Refer to Figure~\ref{{fig:timeline}}.}}

\section{{Interpretation}}
\TODO{{What does this run establish? Distinguish what is by construction (the
harness asked for it) from what emerged. Where a finding above is flagged, say
whether it is expected for this configuration or a genuine defect.}}

\section{{Threats to this reading}}
\TODO{{What would make this interpretation wrong? Single-run noise, a surrogate
executor standing in for a real model, an artefact of how the run was driven
rather than of the protocol. Be specific and be honest.}}

\end{{document}}
"""


def write_skeleton(a: an.Analysis, outdir: Path, *, force: bool = False) -> Path:
    out = outdir / "analysis.tex"
    if out.exists() and not force:
        return out
    out.write_text(
        SKELETON.format(name=a.name, title=tex_escape(a.name)),
        encoding="utf-8",
    )
    return out


# --------------------------------------------------------------------------------------


def load_events(name: str) -> list[dict[str, Any]]:
    path = EVENTS_DIR / f"{name}.jsonl"
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def analyse_run(name: str, meta: dict[str, Any], *, force_skeleton: bool = False) -> dict[str, Any]:
    events = load_events(name)
    a = an.analyse(events, name, meta.get("experiment", ""), meta.get("label", ""))
    outdir = ANALYSIS_DIR / name
    outdir.mkdir(parents=True, exist_ok=True)

    figures = plot_run.render_all(a, outdir / "figures")
    (outdir / "metrics.json").write_text(json.dumps(a.as_dict(), indent=1), encoding="utf-8")
    write_generated(a, figures, outdir)
    write_skeleton(a, outdir, force=force_skeleton)
    return {
        "name": name,
        "figures": sorted(figures),
        "n_findings": len(findings(a)),
        "degraded": a.degraded,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", default=[], help="run name; repeatable")
    ap.add_argument("--all", action="store_true", help="every run in the manifest")
    ap.add_argument("--match", help="substring filter when used with --all")
    ap.add_argument("--force-skeleton", action="store_true", help="overwrite analysis.tex (destroys prose)")
    ap.add_argument("--quiet", action="store_true")
    cfg = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    by_name = {r["name"]: r for r in manifest["runs"]}

    if cfg.all:
        names = [n for n in by_name if not cfg.match or cfg.match in n]
    else:
        names = cfg.run
    if not names:
        print("nothing selected: pass --run NAME or --all")
        return 2

    done = 0
    for name in sorted(names):
        meta = by_name.get(name)
        if meta is None:
            print(f"  unknown run: {name}")
            continue
        try:
            result = analyse_run(name, meta, force_skeleton=cfg.force_skeleton)
        except Exception as exc:
            print(f"  FAILED {name}: {exc!r}")
            continue
        done += 1
        if not cfg.quiet:
            flag = " DEGRADED" if result["degraded"] else ""
            print(f"  {name}: {len(result['figures'])} figures, {result['n_findings']} findings{flag}")
    print(f"analysed {done}/{len(names)} runs into {ANALYSIS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
