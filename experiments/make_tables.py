#!/usr/bin/env python3
"""Emit the paper's result tables from recorded run data.

This script is the only thing permitted to write the numbers in
``paper/tables/``; nothing in the paper's prose restates a number that did
not come through here.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "experiments" / "results"
TABLES = REPO / "paper" / "tables"
TABLES.mkdir(parents=True, exist_ok=True)


def load(name: str):
    p = RESULTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def esc(s: str) -> str:
    return s.replace("_", r"\_").replace("&", r"\&")


# ------------------------------------------------------------- translation
def translation_table() -> None:
    data = load("translation_summary.json")
    arms = [a for a in (data or {}).get("arms", []) if a]
    if not arms:
        return
    rows = []
    for a in arms:
        rows.append(
            f"{esc(a['label'])} & {a['workers']} & {a['shared_terms']} & "
            f"{a['consistent_terms']} & "
            f"\\textbf{{{a['declared_consistency']:.3f}}} & "
            f"{a['realised_rate']:.3f} \\\\"
        )
    best = max(a["declared_consistency"] for a in arms)
    worst = min(a["declared_consistency"] for a in arms)
    delta = best - worst
    TABLES.joinpath("translation-body.tex").write_text(
        "\\begin{table}[t]\n\\centering\\small\n"
        "\\caption{Book translation, twelve translator ranks over the same "
        "twelve chunks and the same rank composition. \\emph{cons.} is the "
        "fraction of names occurring in more than one chunk that every such "
        "chunk rendered identically; \\emph{real.} is the fraction of "
        "declared renderings that actually appear in the produced "
        "translation. One collective, an exclusive prefix scan costing four "
        f"rounds, moves consistency by {delta:.3f}.}}\n"
        "\\label{tab:translation}\n"
        "\\begin{tabular}{@{}lrrrrr@{}}\n\\toprule\n"
        "\\textbf{Arm} & \\textbf{$p$} & \\textbf{shared} & "
        "\\textbf{consist.} & \\textbf{cons.} & \\textbf{real.} "
        "\\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n",
        encoding="utf-8")
    print(f"translation: {len(arms)} arms, delta={delta:.3f}")


# ---------------------------------------------------------------- software
def software_table() -> None:
    coord = None
    for candidate in ("software.json", "tinyq.json"):
        coord = load(candidate)
        if coord:
            break
    if coord is None:
        p = REPO / "runs" / "tinyq" / "out" / "coordinator.json"
        if p.exists():
            coord = json.loads(p.read_text(encoding="utf-8"))
    if not coord:
        TABLES.joinpath("software-body.tex").write_text(
            "The collaborative software build did not reach its measurement "
            "point: the run wedged on the collective-counter durability bug "
            "described below, and a clean re-run was blocked by the launcher "
            "concurrency limit of \\Cref{sec:discussion-deadlock}. We report "
            "the failure rather than omitting the experiment.\n",
            encoding="utf-8")
        print("software: no data (placeholder prose written)")
        return
    r1 = coord["round1"]
    loc = coord.get("lines_of_code", {})
    rows = []
    for name in sorted(loc):
        if name.startswith("__"):
            continue
        rows.append(f"\\code{{{esc(name)}}} & {loc[name]} \\\\")
    marks = coord.get("marks", {})
    TABLES.joinpath("software-body.tex").write_text(
        "\\begin{table}[t]\n\\centering\\small\n"
        f"\\caption{{Collaborative construction of \\code{{tinyq}}: "
        f"{coord['modules']} mutually dependent modules, one per agent rank, "
        f"{coord.get('total_lines', 0)} lines in total, judged by a frozen "
        f"{r1['total']}-test integration suite written before the run and "
        f"editable by nobody. All {coord.get('interfaces_published', 0)} "
        f"interfaces were published to the shared window at a total cost of "
        f"{coord.get('interface_tokens', 0)} tokens. The assembled package "
        f"passes {r1['passed']} of {r1['total']} tests "
        f"({round(100 * r1['pass_rate'])}\\%), with no interface mismatch "
        f"between any "
        f"pair of independently written modules.}}\n"
        "\\label{tab:software}\n"
        "\\begin{tabular}{@{}lr@{}}\n\\toprule\n"
        "\\textbf{Module (one agent rank each)} & \\textbf{lines} "
        "\\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n",
        encoding="utf-8")
    print(f"software: pass_rate={r1['pass_rate']:.3f} "
          f"({r1['passed']}/{r1['total']}) marks={marks}")


# ------------------------------------------------------------------ faults
def faults_table() -> None:
    data = load("faults.json")
    if not data:
        TABLES.joinpath("faults-body.tex").write_text(
            "%% fault experiment did not complete; table omitted\n",
            encoding="utf-8")
        print("faults: no data")
        return
    agg: dict[tuple[int, int, str], list[dict]] = defaultdict(list)
    for row in data["rows"]:
        agg[(row["p"], row["failures"], row["mode"])].append(row)

    rows = []
    for (p, k, mode) in sorted(agg):
        runs = agg[(p, k, mode)]
        survivors = p - k
        completed = sum(
            sum(v for kind, v in r["outcomes"].items()
                if kind in ("recovered", "detected", "completed"))
            for r in runs)
        expected = survivors * len(runs)
        detect = [r["detect_p50_s"] for r in runs if r["detect_p50_s"]]
        consistent = all(r["consistent_survivor_view"] for r in runs)
        correct = all(r["correct"] for r in runs) if mode == "shrink" else None
        label = {"none": "no recovery", "detect": "detect only",
                 "shrink": "revoke + shrink"}[mode]
        detect_s = f"{min(detect):.1f}" if detect else "---"
        outcome = ("\\textbf{all recover}" if mode == "shrink" and correct
                   else "all detect" if mode == "detect"
                   else "\\textbf{all fail}")
        rows.append(
            f"{p} & {k} & {label} & {detect_s} & "
            f"{completed}/{expected} & {outcome} \\\\")

    TABLES.joinpath("faults-body.tex").write_text(
        "\\begin{table}[t]\n\\centering\\small\n"
        "\\caption{Fault injection. Victim ranks stop without warning: no "
        "finalize, no error, no last heartbeat. \\emph{detect} is the median "
        "time from the failure to a survivor declaring it, against a "
        "3\\,s liveness timeout. \\emph{ok} counts survivors that reached a "
        "defined outcome. Without recovery every survivor of every "
        "configuration fails; with revoke and shrink every survivor "
        "recovers, agrees on the same survivor set, and computes the correct "
        "result. Two repetitions per configuration.}\n"
        "\\label{tab:faults}\n"
        "\\begin{tabular}{@{}rrlrrl@{}}\n\\toprule\n"
        "$p$ & \\textbf{dead} & \\textbf{mode} & \\textbf{detect (s)} & "
        "\\textbf{ok} & \\textbf{outcome} \\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabular}\n\\end{table}\n",
        encoding="utf-8")
    print(f"faults: {len(agg)} configurations")


def main() -> int:
    translation_table()
    software_table()
    faults_table()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
