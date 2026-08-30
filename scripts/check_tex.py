#!/usr/bin/env python3
"""Static validation of the paper source, for use when no TeX engine is present.

This is not a substitute for compiling, but it catches the failure modes that
actually bite: a \\cite key with no BibTeX entry, a \\Cref to a label that does
not exist, a macro from results.tex that the paper uses but the data did not
define (so a claim has no measurement behind it), unbalanced braces, and
environments that are opened and not closed.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set, Tuple


def strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        i, esc = 0, False
        cut = len(line)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tex", default="paper/main.tex")
    ap.add_argument("--bib", default="paper/refs.bib")
    ap.add_argument("--macros", default="paper/results.tex")
    ap.add_argument("--figdir", default="paper/figures")
    args = ap.parse_args()

    tex_path = Path(args.tex)
    raw = tex_path.read_text(encoding="utf-8")
    tex = strip_comments(raw)
    problems: List[str] = []
    notes: List[str] = []

    # ---- citations ----
    bib = Path(args.bib).read_text(encoding="utf-8")
    bib_keys = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bib))
    cited: Set[str] = set()
    for m in re.finditer(r"\\cite[a-zA-Z]*\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", tex):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                cited.add(k)
    missing_cites = sorted(cited - bib_keys)
    if missing_cites:
        problems.append(f"{len(missing_cites)} \\cite keys with no BibTeX entry: "
                        + ", ".join(missing_cites))
    notes.append(f"{len(cited)} distinct citations, {len(bib_keys)} bib entries")

    # ---- labels and refs ----
    labels = set(re.findall(r"\\label\{([^}]*)\}", tex))
    refs: Set[str] = set()
    for m in re.finditer(r"\\(?:C?ref|autoref|pageref|eqref)\s*\{([^}]*)\}", tex):
        for k in m.group(1).split(","):
            k = k.strip()
            if k:
                refs.add(k)
    missing_refs = sorted(refs - labels)
    if missing_refs:
        problems.append(f"{len(missing_refs)} references to undefined labels: "
                        + ", ".join(missing_refs))
    unused = sorted(labels - refs)
    if unused:
        notes.append(f"{len(unused)} labels never referenced: {', '.join(unused)}")

    # ---- result macros ----
    mac_path = Path(args.macros)
    defined = set()
    if mac_path.exists():
        defined = set(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}", mac_path.read_text()))
    local = set(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}", tex))
    known_latex = {
        "textwidth", "columnwidth", "textbf", "textit", "emph", "small", "footnotesize",
        "scriptsize", "ttfamily", "bf", "bfseries", "it", "rm", "sf", "tt", "cite",
        "citet", "citep", "label", "ref", "Cref", "cref", "input", "include",
        "bibliography", "bibliographystyle", "maketitle", "title", "author", "date",
        "begin", "end", "section", "subsection", "subsubsection", "paragraph",
        "item", "centering", "includegraphics", "caption", "toprule", "midrule",
        "bottomrule", "multirow", "xspace", "balance", "documentclass", "usepackage",
        "definecolor", "lstset", "lstdefinestyle", "captionsetup", "setlist",
        "newcommand", "providecommand", "color", "log", "lceil", "rceil", "cdot",
        "approx", "le", "ge", "times", "alpha", "beta", "gamma", "lambda", "text",
        "emptyset", "rightarrow", "quad", "qquad", "hfill", "vspace", "hspace",
        "url", "href", "footnote", "geometry", "leftmargin", "varphi", "verb",
        "texttt", ",", "\\", "%", "&", "_", "#", "$", "{", "}", "sim", "linewidth",
        "textsc",
    }
    used = set(re.findall(r"\\([A-Za-z]+)", tex))
    # Macros that look like result macros (camelCase, defined nowhere)
    suspicious = sorted(
        u for u in used
        if u not in known_latex and u not in local and u not in defined
        and re.match(r"^[a-z]+[A-Z]", u)
    )
    if suspicious:
        problems.append(
            f"{len(suspicious)} result-style macros used but not defined in "
            f"{mac_path.name} (a claim with no measurement behind it): "
            + ", ".join(suspicious)
        )
    used_defined = sorted(defined & used)
    unused_macros = sorted(defined - used)
    notes.append(f"{len(used_defined)}/{len(defined)} result macros used")
    if unused_macros:
        notes.append(f"unused result macros: {', '.join(unused_macros[:20])}"
                     + (" ..." if len(unused_macros) > 20 else ""))

    # ---- figures ----
    figs = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", tex)
    figdir = Path(args.figdir).parent
    missing_figs = []
    for f in figs:
        base = figdir / f
        if not any(base.with_suffix(ext).exists() for ext in (".pdf", ".png", ".jpg", "")):
            missing_figs.append(f)
    if missing_figs:
        problems.append(f"{len(missing_figs)} figures not found: " + ", ".join(missing_figs))
    notes.append(f"{len(figs)} figure inclusions, {len(figs) - len(missing_figs)} present")

    # ---- braces and environments ----
    depth = 0
    i = 0
    minimum = 0
    while i < len(tex):
        ch = tex[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            minimum = min(minimum, depth)
        i += 1
    if depth != 0:
        problems.append(f"unbalanced braces: net {depth:+d}")
    if minimum < 0:
        problems.append(f"brace closed before opened (min depth {minimum})")

    envs = Counter()
    for m in re.finditer(r"\\(begin|end)\{([^}]*)\}", tex):
        envs[m.group(2)] += 1 if m.group(1) == "begin" else -1
    bad_envs = {k: v for k, v in envs.items() if v != 0}
    if bad_envs:
        problems.append(f"unbalanced environments: {bad_envs}")

    # ---- placeholders ----
    placeholders = re.findall(r"\[RESULTS PENDING[^\]]*\]|\bTODO\b|\bXXX\b|\bTBD\b", tex)
    if placeholders:
        notes.append(f"{len(placeholders)} explicit placeholder(s) remaining "
                     "(expected while runs are in flight)")

    print(f"checking {tex_path} ({len(raw.splitlines())} lines)")
    for n in notes:
        print(f"  note: {n}")
    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nno structural problems found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
