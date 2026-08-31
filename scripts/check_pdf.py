#!/usr/bin/env python3
"""Validation of the *built* paper, complementing check_tex.py.

check_tex.py reads the source, so it cannot see the failure that actually cost
us content: a float too tall to place is deferred forever, every float queued
behind it is deferred with it, and LaTeX drops all of them at \\end{document}
while still exiting 0. The source was structurally perfect -- the labels were
declared -- but three tables were missing from the PDF and four \\Cref's printed
as "??". Nothing in the build reported an error.

So this script compares what the source asked for against what the PDF contains.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path
from typing import List

# Only overfull boxes wide enough to visibly run into the gutter or margin.
OVERFULL_FAIL_PT = 20.0


def pdf_text(pdf: Path) -> str:
    if not shutil.which("pdftotext"):
        return ""
    out = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"],
        capture_output=True, text=True, check=False,
    )
    return out.stdout


def page_count(pdf: Path) -> int:
    if not shutil.which("pdfinfo"):
        return 0
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=False)
    m = re.search(r"^Pages:\s+(\d+)", out.stdout, re.M)
    return int(m.group(1)) if m else 0


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        i, cut = 0, len(line)
        while i < len(line):
            if line[i] == "\\":
                i += 2
                continue
            if line[i] == "%":
                cut = i
                break
            i += 1
        out.append(line[:cut])
    return "\n".join(out)


def count_floats(tex: str, kind: str) -> int:
    """Count captioned floats of one kind, both single- and double-column."""
    n = 0
    for env in (kind, kind + r"\*"):
        for m in re.finditer(r"\\begin\{" + env + r"\}(.*?)\\end\{" + env + r"\}", tex, re.S):
            n += len(re.findall(r"\\caption\b", m.group(1)))
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", default="paper/main.pdf")
    ap.add_argument("--log", default="paper/main.log")
    ap.add_argument("--tex", default="paper/main.tex")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    problems: List[str] = []
    notes: List[str] = []

    if not pdf.exists():
        print(f"{pdf} does not exist; build the paper first")
        return 1

    pages = page_count(pdf)
    notes.append(f"{pages} pages, {pdf.stat().st_size // 1024} KiB")

    text = pdf_text(pdf)
    if not text:
        notes.append("pdftotext unavailable; skipping content checks")
    else:
        # ---- unresolved references ----
        bad = text.count("??")
        if bad:
            lines = [ln.strip() for ln in text.splitlines() if "??" in ln]
            problems.append(
                f"{bad} unresolved reference(s) printed as '??': "
                + " | ".join(lines[:4])
            )

        # ---- every float the source declares must reach the page ----
        tex = strip_comments(Path(args.tex).read_text(encoding="utf-8"))
        for kind, word in (("table", "Table"), ("figure", "Figure")):
            want = count_floats(tex, kind)
            # Two-column layout interleaves both columns on a physical line, so
            # a caption is not necessarily at the start of one.
            got = len(set(re.findall(rf"\b{word} (\d+):", text)))
            if got < want:
                problems.append(
                    f"{want - got} {kind}(s) dropped from the PDF: source declares "
                    f"{want} captioned {kind}s, the PDF shows {got}. A float that "
                    f"cannot be placed also blocks the floats queued behind it."
                )
            else:
                notes.append(f"{got}/{want} {kind}s placed")

    # ---- engine warnings that mean lost or damaged output ----
    log = Path(args.log)
    if not log.exists():
        notes.append(f"{log} not found; skipping log checks")
    else:
        raw = log.read_text(encoding="utf-8", errors="replace")

        for pattern, desc in (
            (r"float\(s\) lost", "LaTeX reported lost float(s)"),
            (r"Float too large for page", "a float is taller than the page and cannot be placed"),
            (r"Reference `([^']*)' on page \d+ undefined", "undefined reference"),
            (r"Citation `([^']*)' on page \d+ undefined", "undefined citation"),
        ):
            hits = re.findall(pattern, raw)
            if hits:
                detail = ", ".join(sorted(set(h for h in hits if h))[:6])
                problems.append(f"{desc} ({len(hits)}x)" + (f": {detail}" if detail else ""))

        overfull = [float(x) for x in re.findall(r"Overfull \\hbox \(([0-9.]+)pt", raw)]
        wide = sorted((x for x in overfull if x > OVERFULL_FAIL_PT), reverse=True)
        if wide:
            problems.append(
                f"{len(wide)} overfull hbox(es) wider than {OVERFULL_FAIL_PT:g}pt "
                f"(worst {wide[0]:.1f}pt) -- content runs into the margin"
            )
        elif overfull:
            notes.append(f"{len(overfull)} overfull hbox(es), worst {max(overfull):.1f}pt")

    print(f"checking {pdf}")
    for n in notes:
        print(f"  note: {n}")
    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nbuilt PDF matches the source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
