"""Analyse a family of collective runs across process counts, as one scaling study.

Four hundred and fifty of the five hundred runs are one collective algorithm measured at one
process count: ``coll-bcast-binomial-16`` sends seven messages in three rounds and finishes in
thirty-five milliseconds. Analysed alone, there is almost nothing to say about it, and five
hundred documents each saying almost nothing is not thoroughness.

Analysed as a *family* --- one algorithm across every measured $p$ --- the same runs answer the
questions the sweep was built to answer. Does the implementation's message count track its
closed form at every size, including the awkward ones? Where does the round count actually step,
and does it step where $\\lceil\\log_2 p\\rceil$ says it should? Do the non-power-of-two sizes
behave like the powers of two, or does the remainder path diverge?

That last question is not rhetorical. The sweep contains a real defect that only a family view
exposes: recursive-doubling allreduce under-reported its own traffic by exactly the remainder
at every non-power-of-two $p$, and the pattern --- exact agreement at 2, 4, 8, 16, 32 and a
constant shortfall everywhere else --- is invisible in any single run and unmistakable across
twenty-one.

    python3 scripts/analyze_family.py --family bcast/binomial
    python3 scripts/analyze_family.py --all
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import analyze_run  # noqa: E402
from agentmpi import analysis as an  # noqa: E402
from agentmpi import cost  # noqa: E402
from agentmpi import trace_style as ts  # noqa: E402

EVENTS = REPO / "traces" / "events"
OUT = REPO / "analysis" / "families"
MANIFEST = REPO / "traces" / "manifest.json"

NAME_RE = re.compile(r"coll-([a-z]+)-(.+)-(\d+)$")


def discover() -> dict[tuple[str, str], list[tuple[int, str]]]:
    """Group every collective sweep run by ``(op, algorithm)``, ordered by process count."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    families: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for row in manifest["runs"]:
        name = row["name"]
        m = NAME_RE.match(name.split("__", 1)[-1])
        if not m:
            continue
        key = (m.group(1), m.group(2))
        families.setdefault(key, []).append((int(m.group(3)), name))
    for key in families:
        families[key].sort()
    return families


def measure(op: str, names: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """One row per run: what it logged, what the model predicts, and whether they agree.

    Several campaigns measured the same process count, so rows are keyed by run rather than by
    $p$ and duplicates are kept. Two independent measurements agreeing at the same $p$ is
    evidence; silently collapsing them would discard it.

    The invocation is selected by matching the family's op, not by taking the first one. A
    composed algorithm produces nested invocations of *other* ops --- ``reduce_bcast`` logs a
    ``reduce`` and a ``bcast`` inside the ``allreduce`` --- so taking the first would compare the
    whole run's traffic against a constituent's closed form and report a disagreement at every
    single size.
    """
    rows = []
    for p, name in names:
        events = analyze_run.load_events(name)
        a = an.analyse(events, name)
        matching = [c for c in a.collectives if c.op == op]
        complete = [c for c in matching if c.complete]
        inv = (complete or matching or a.collectives or [None])[0]
        logged = sum(1 for e in events if e["kind"] == "msg.send")
        rows.append({
            "name": name,
            "campaign": name.split("__", 1)[0] if "__" in name else "",
            "p": p,
            "wall_s": a.wall_s,
            "logged_messages": logged,
            "reported_messages": inv.effective_messages if inv else 0,
            "predicted_messages": inv.predicted_messages if inv else None,
            "rounds": inv.rounds if inv else 0,
            "predicted_rounds": inv.predicted_rounds if inv else None,
            "tokens": inv.tokens if inv else 0,
            "skew_s": inv.skew_s if inv else 0.0,
            "collective_wall_s": inv.wall_s if inv else 0.0,
            "participants": inv.n_participants if inv else 0,
            "complete": bool(inv and inv.complete),
        })
    return rows


def _power_of_two(p: int) -> bool:
    return p > 0 and (p & (p - 1)) == 0


def figure(op: str, alg: str, rows: list[dict[str, Any]], out: Path) -> Path:
    """Messages and rounds against process count, measured versus closed form.

    Powers of two are marked separately because that is the distinction that matters: algorithms
    take a different code path at other sizes, and a plot that hides which points are which hides
    the only structure worth looking for.
    """
    fig, (msg_ax, round_ax) = plt.subplots(1, 2, figsize=(9.2, 3.2))
    fig.patch.set_facecolor(ts.BACKGROUND)
    for ax in (msg_ax, round_ax):
        ax.set_facecolor(ts.PANEL)
        for spine in ax.spines.values():
            spine.set_color(ts.LINE)
        ax.tick_params(colors=ts.MUTED, labelsize=8)
        ax.xaxis.label.set_color(ts.MUTED)
        ax.yaxis.label.set_color(ts.MUTED)
        ax.title.set_color(ts.FOREGROUND)
        ax.grid(True, color=ts.LINE, linewidth=0.5, alpha=0.55)
        ax.set_axisbelow(True)
        ax.set_xlabel("process count $p$")

    ps = [r["p"] for r in rows]
    grid = sorted(set(ps))
    predicted_msgs, predicted_rounds = [], []
    formula = cost.FORMULAS.get((op, alg))
    for p in grid:
        if formula is None:
            predicted_msgs.append(float("nan"))
            predicted_rounds.append(float("nan"))
            continue
        rounds, messages, _v, _d = formula(p, 1000)
        predicted_msgs.append(messages)
        predicted_rounds.append(rounds)

    if formula is not None:
        msg_ax.plot(grid, predicted_msgs, color=ts.ROLE_COLOR["recovery"], linewidth=1.2,
                    linestyle="--", label="closed form", zorder=1)
        round_ax.plot(grid, predicted_rounds, color=ts.ROLE_COLOR["recovery"], linewidth=1.2,
                      linestyle="--", label="closed form", zorder=1)

    for is_pow2, marker, label in ((True, "o", "$p=2^k$"), (False, "s", "other $p$")):
        sel = [r for r in rows if _power_of_two(r["p"]) is is_pow2]
        if not sel:
            continue
        msg_ax.scatter([r["p"] for r in sel], [r["logged_messages"] for r in sel],
                       s=26, marker=marker, color=ts.ROLE_COLOR["message"],
                       label=f"logged, {label}", zorder=3)
        round_ax.scatter([r["p"] for r in sel], [r["rounds"] for r in sel],
                         s=26, marker=marker, color=ts.ROLE_COLOR["message"],
                         label=f"measured, {label}", zorder=3)

    # A self-report that differs from the log is the defect this view exists to catch, so it is
    # drawn in the trouble colour rather than left to the table.
    off = [r for r in rows if r["reported_messages"] != r["logged_messages"]]
    if off:
        msg_ax.scatter([r["p"] for r in off], [r["reported_messages"] for r in off],
                       s=34, marker="x", color=ts.ROLE_COLOR["trouble"],
                       label="self-reported (disagrees)", zorder=4)

    msg_ax.set_ylabel("messages")
    msg_ax.set_title(f"{op}/{alg}: messages", fontsize=9, loc="left")
    round_ax.set_ylabel("rounds on the critical path")
    round_ax.set_title("rounds", fontsize=9, loc="left")
    for ax in (msg_ax, round_ax):
        legend = ax.legend(frameon=False, fontsize=7, loc="upper left")
        for text in legend.get_texts():
            text.set_color(ts.MUTED)

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=200, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return out


def table(rows: list[dict[str, Any]]) -> str:
    body = []
    for r in sorted(rows, key=lambda x: (x["p"], x["name"])):
        agree = r["predicted_messages"] is not None and r["logged_messages"] == r["predicted_messages"]
        self_agree = r["reported_messages"] == r["logged_messages"]
        body.append(
            rf"  {r['p']} & {analyze_run.mono(r['campaign'] or '--')} & {r['logged_messages']} & "
            rf"{analyze_run.num(r['predicted_messages'])} & "
            rf"{r['reported_messages']} & {r['rounds']} & {analyze_run.num(r['predicted_rounds'])} & "
            rf"{analyze_run.num(r['wall_s'], 3)} & "
            rf"{r'\modelok' if agree else r'\modelbad'} & "
            rf"{r'\modelok' if self_agree else r'\modelbad'} \\"
        )
    return (
        "\\begin{center}\\footnotesize\n\\begin{tabular}{@{}rlrrrrrrcc@{}}\n\\toprule\n"
        "  $p$ & campaign & logged & pred. & self-rep. & rounds & pred. & wall (s) & model & acct. \\\\\n"
        "\\midrule\n" + "\n".join(body) + "\n\\bottomrule\n\\end{tabular}\\normalsize\n\\end{center}\n"
    )


FAMILY_SKELETON = r"""%% Scaling analysis of one collective family: {op}/{alg}
%%
%% generated.tex holds the derived facts and is rewritten by
%%   python3 scripts/analyze_family.py --family {op}/{alg}
%% This file is never overwritten: it is where interpretation lives.
\documentclass[11pt]{{article}}
\input{{../../preamble}}

\title{{\textbf{{{op}/{alg}}}\\[2pt]\large An AgentMPI collective scaling analysis}}
\author{{}}
\date{{}}

\begin{{document}}
\maketitle

\begin{{abstract}}
\TODO{{One paragraph: what this algorithm is, what the sweep establishes about it, and
the single most important thing a reader should take away.}}
\end{{abstract}}

\input{{generated}}

\section{{How the algorithm scales}}
\TODO{{Derive the algorithm's cost from how it works, then compare with what was
measured. Where does the round count step, and why there? Explain the shape of
Figure~\ref{{fig:family}} rather than describing it.}}

\section{{Powers of two and everything else}}
\TODO{{Whether the non-power-of-two sizes behave like the powers of two. If the
algorithm has a remainder path, say what it does and whether the measurements show
it working. This is where defects in collective implementations live.}}

\section{{When to choose this algorithm}}
\TODO{{Against the alternatives in the same op family, on latency, message count,
and --- for reductions --- fold depth, which governs how much content survives a
lossy operator. Be concrete about the crossover.}}

\section{{Threats to this reading}}
\TODO{{What would make this wrong. These runs use no agents, so they measure the
protocol's own behaviour and say nothing about model latency; sub-second wall times
are dominated by fabric overhead. Be specific.}}

\end{{document}}
"""


def build_family(op: str, alg: str, names: list[tuple[int, str]]) -> dict[str, Any]:
    rows = measure(op, names)
    outdir = OUT / f"{op}-{alg}"
    outdir.mkdir(parents=True, exist_ok=True)

    fig_path = figure(op, alg, rows, outdir / "figures" / "family.pdf")
    (outdir / "metrics.json").write_text(
        json.dumps({"op": op, "algorithm": alg, "n_runs": len(rows), "runs": rows}, indent=1),
        encoding="utf-8",
    )

    model_bad = [r for r in rows if r["predicted_messages"] is not None and r["logged_messages"] != r["predicted_messages"]]
    acct_bad = [r for r in rows if r["reported_messages"] != r["logged_messages"]]
    findings = []
    if not model_bad:
        findings.append(("ok", f"All {len(rows)} measured sizes logged exactly the number of messages the closed form predicts."))
    else:
        findings.append((
            "critical",
            f"{len(model_bad)} of {len(rows)} sizes logged a message count the closed form does not predict: "
            + ", ".join(f"$p={r['p']}$ ({r['logged_messages']} vs {r['predicted_messages']})" for r in model_bad[:8]),
        ))
    if acct_bad:
        pows = [r["p"] for r in acct_bad if _power_of_two(r["p"])]
        findings.append((
            "warning",
            f"At {len(acct_bad)} size(s) the collective's self-reported count disagrees with the traffic "
            f"the fabric logged"
            + (
                ", and every one of them is a non-power-of-two size, which points at the remainder path"
                if acct_bad and not pows
                else ""
            )
            + ": "
            + ", ".join(f"$p={r['p']}$ (reported {r['reported_messages']}, logged {r['logged_messages']})" for r in acct_bad[:8]),
        ))
    walls = [r["wall_s"] for r in rows if r["wall_s"] > 0]
    if walls:
        findings.append((
            "note",
            f"Wall times span {min(walls):.3f}--{max(walls):.3f}\\,s with no agent in the loop, so these "
            f"measure the fabric and the protocol rather than model latency.",
        ))

    parts = [
        "% Generated by scripts/analyze_family.py. Do not edit; edit analysis.tex instead.",
        "",
        r"\section{What this family is}",
        # "N runs at M distinct process counts", not "N process counts": two campaigns cover
        # overlapping sizes, so runs outnumber sizes and conflating them overstates the sweep.
        f"The {analyze_run.mono(op)} collective under the {analyze_run.mono(alg)} algorithm, measured "
        f"across {len(rows)} runs at {len({r['p'] for r in rows})} distinct process counts from "
        f"{min(r['p'] for r in rows)} to {max(r['p'] for r in rows)}. "
        f"Every run is agent-free and deterministic, so the measurements are of the protocol itself.",
        "",
        r"\section{What the analysis notices}",
        "\\begin{itemize}[leftmargin=1.4em]\n"
        + "\n".join(rf"  \item {analyze_run.SEVERITY_LABEL[s]} {t}" for s, t in findings)
        + "\n\\end{itemize}\n",
        r"\begin{figure}[htbp]",
        r"  \centering",
        rf"  \includegraphics[width=\linewidth]{{figures/{fig_path.name}}}",
        r"  \caption{Measured message count and critical-path rounds against process count, with the "
        r"closed-form prediction. Powers of two are drawn as circles and other sizes as squares, "
        r"because algorithms take a different code path at sizes that are not powers of two. A red "
        r"cross marks a size where the collective's own reported count disagrees with the traffic the "
        r"fabric logged.}",
        r"  \label{fig:family}",
        r"\end{figure}",
        "",
        r"\section{Every measured size}",
        table(rows),
    ]
    (outdir / "generated.tex").write_text("\n".join(parts) + "\n", encoding="utf-8")

    skeleton = outdir / "analysis.tex"
    if not skeleton.exists():
        skeleton.write_text(
            FAMILY_SKELETON.format(op=analyze_run.tex_escape(op), alg=analyze_run.tex_escape(alg)),
            encoding="utf-8",
        )
    return {"family": f"{op}/{alg}", "n_runs": len(rows), "model_bad": len(model_bad), "acct_bad": len(acct_bad)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", action="append", default=[], help="op/algorithm; repeatable")
    ap.add_argument("--all", action="store_true")
    cfg = ap.parse_args()

    families = discover()
    if cfg.all:
        keys = sorted(families)
    else:
        keys = []
        for spec in cfg.family:
            op, _, alg = spec.partition("/")
            if (op, alg) in families:
                keys.append((op, alg))
            else:
                print(f"  unknown family: {spec}")
    if not keys:
        print("nothing selected: pass --family op/algorithm or --all")
        return 2

    for op, alg in keys:
        result = build_family(op, alg, families[(op, alg)])
        flags = []
        if result["model_bad"]:
            flags.append(f"{result['model_bad']} model disagreements")
        if result["acct_bad"]:
            flags.append(f"{result['acct_bad']} accounting gaps")
        print(f"  {result['family']:34s} {result['n_runs']:3d} runs" + (f"   {', '.join(flags)}" if flags else ""))
    print(f"built {len(keys)} family analyses into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
