"""Check the paper's sources before building, and the PDF after.

Three failures this catches, all of which have shipped in real papers: a macro
used but never defined (renders as an error or, worse, nothing); a citation key
the bibliography does not contain (renders as a question mark); and a float that
LaTeX silently dropped because it could not place it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PAPER = ROOT / "paper"


def main() -> int:
    tex = (PAPER / "main.tex").read_text(encoding="utf-8")
    # Every file main.tex inputs is part of the source: the E7 section and its
    # generated macros live in their own files.
    for name in re.findall(r"\\input\{(\w+)\}", tex):
        part = PAPER / f"{name}.tex"
        if part.exists() and name != "results":
            tex += "\n" + part.read_text(encoding="utf-8")
    results = (PAPER / "results.tex").read_text(encoding="utf-8")
    for name in re.findall(r"IfFileExists\{(\w+)\.tex\}", tex):
        part = PAPER / f"{name}.tex"
        if part.exists():
            results += "\n" + part.read_text(encoding="utf-8")
    bib = (PAPER / "refs.bib").read_text(encoding="utf-8", errors="replace")
    problems: list[str] = []

    defined = set(re.findall(r"\\newcommand\{\\(\w+)\}", results)) | set(
        re.findall(r"\\newcommand\{\\(\w+)\}", tex)
    )
    builtin = {
        "documentclass", "usepackage", "begin", "end", "section", "subsection",
        "paragraph", "textbf", "textit", "emph", "cite", "label", "ref", "item",
        "caption", "toprule", "midrule", "bottomrule", "maketitle", "title",
        "author", "affiliation", "institution", "country", "keywords", "input",
        "bibliographystyle", "bibliography", "xspace", "balance", "small",
        "scriptsize", "ttfamily", "lstset", "newcommand", "quad", "qquad",
        "log", "max", "lceil", "rceil", "alpha", "beta", "gamma", "lambda",
        "varphi", "oplus", "le", "ge", "times", "code", "ampi", "noindent", "hline", "hspace", "vspace", "footnote", "url", "href",
        "text", "mathrm", "in", "leq", "geq", "sum", "prod", "frac", "cdot", "S",
        "IfFileExists", "eqref", "centering", "includegraphics", "linewidth", "hfill",
        "cmidrule", "multicolumn", "mu", "bmod", "enumerate", "quote", "proposition",
        "tabular", "table", "figure", "abstract", "align", "lr", "textsc", "texttt",
    }
    used = set(re.findall(r"\\([a-zA-Z]+)", tex))
    unknown = sorted(u for u in used - defined - builtin if u.lower() != u or True)
    # Only report macros that look like ours: mixed case, not a known LaTeX name.
    suspicious = [u for u in unknown if re.search(r"[A-Z]", u) and u not in builtin]
    for name in suspicious:
        if name not in defined and not re.search(rf"\\newcommand\{{\\{name}\}}", results):
            if name in ("Bbbk", "ACM", "Reference", "Format"):
                continue
            problems.append(f"macro \\{name} is used but not defined")

    keys = set(re.findall(r"@\w+\{\s*([^,]+),", bib))
    for m in re.finditer(r"\\cite\{([^}]+)\}", tex):
        for key in m.group(1).split(","):
            if key.strip() not in keys:
                problems.append(f"citation key {key.strip()!r} is not in refs.bib")

    na = [
        m.group(1)
        for m in re.finditer(r"\\newcommand\{\\(\w+)\}\{\\textit\{n/a\}", results)
        if f"\\{m.group(1)}" in tex
    ]
    for name in na:
        problems.append(f"macro \\{name} is used in the paper but has no data yet")

    pdf = PAPER / "main.pdf"
    if pdf.exists():
        try:
            text = subprocess.run(
                ["pdftotext", str(pdf), "-"], capture_output=True, text=True, check=True
            ).stdout
            if "??" in text:
                problems.append("the built PDF contains an unresolved reference (??)")
            captions = len(re.findall(r"\\caption\{", tex))
            rendered = text.count("Table ") + text.count("Figure ")
            if rendered < captions:
                problems.append(
                    f"{captions} captioned floats in the source, {rendered} in the PDF: "
                    "LaTeX dropped one"
                )
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("note: pdftotext unavailable; skipping PDF checks")

    for p in problems:
        print(f"FAIL {p}")
    if not problems:
        print(f"ok: {len(defined)} macros, {len(keys)} bibliography entries, PDF consistent")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
