#!/usr/bin/env python3
"""Combine the translation arms into a summary, a LaTeX table, and figure input.

No number in the paper's translation table is typed by hand; this script is
the only thing that writes it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "experiments" / "translation"))

from analyze import score  # noqa: E402

ARMS = [
    ("glossary", "runs/real-glossary/out", "AMPI_Exscan"),
    ("baseline", "runs/real-baseline/out", "no coordination"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "experiments" / "results" /
                                         "translation_summary.json"))
    ap.add_argument("--table", default=str(REPO / "paper" / "tables" /
                                           "translation.tex"))
    args = ap.parse_args()

    corpus = json.loads((REPO / "experiments" / "data" / "corpus.json")
                        .read_text(encoding="utf-8"))
    arms = []
    for mode, rel, label in ARMS:
        path = REPO / rel / "translations.json"
        if not path.exists():
            arms.append(None)
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        report = score(payload, corpus)
        report["label"] = label
        report["mode"] = mode
        arms.append(report)

    summary = {"arms": arms, "corpus": corpus["stats"]}
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    present = [a for a in arms if a]
    table = Path(args.table)
    table.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for a in present:
        rows.append(
            f"{a['label']} & {a['workers']} & {a['returned']} & "
            f"{a['shared_terms']} & {a['consistent_terms']} & "
            f"{a['declared_consistency']:.3f} & {a['realised_rate']:.3f} & "
            f"{a['translation_chars']//1000}k & {a['wall_s']/60:.0f} \\\\"
        )
    table.write_text(
        "\\begin{tabular}{@{}lrrrrrrrr@{}}\n\\toprule\n"
        "\\textbf{Arm} & \\textbf{$p$} & \\textbf{ret.} & \\textbf{terms} & "
        "\\textbf{consistent} & \\textbf{cons.} & \\textbf{real.} & "
        "\\textbf{chars} & \\textbf{min} \\\\\n\\midrule\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabular}\n",
        encoding="utf-8")

    for a in present:
        print(f"{a['label']:<18} consistency={a['declared_consistency']:.3f} "
              f"({a['consistent_terms']}/{a['shared_terms']}) "
              f"realised={a['realised_rate']:.3f} "
              f"chars={a['translation_chars']} wall={a['wall_s']:.0f}s")
        for item in a["inconsistent"]:
            print(f"    inconsistent: {item['term']} -> "
                  f"{sorted(set(item['renderings'].values()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
