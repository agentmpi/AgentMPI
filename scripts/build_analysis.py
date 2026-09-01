"""Build the per-run analysis documents and report which ones are actually written.

With five hundred documents, "is the analysis done" stops being a question anyone can answer
by looking. A document can exist, compile, and contain nothing but the generated tables and
four `\\TODO` placeholders --- which is worse than a missing document, because it looks finished
in a directory listing and in a PDF page count.

So this reports three states per run, and only the third counts:

*missing* --- no ``analysis.tex`` at all.

*unwritten* --- the file exists but still carries `\\TODO` markers, so the interpretation has not
been supplied. Counted and listed, never quietly passed over.

*written* --- every placeholder replaced. Reported with the prose word count, because a document
whose interpretation is thirty words has technically replaced the placeholders and has not
actually said anything.

    python3 scripts/build_analysis.py --status          # report only, no builds
    python3 scripts/build_analysis.py --build-written   # compile the ones that are done
    python3 scripts/build_analysis.py --build-all
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "analysis" / "runs"
MANIFEST = REPO / "traces" / "manifest.json"

TODO_RE = re.compile(r"\\TODO\{")
#: Strip LaTeX structure before counting prose, so a word count reflects what was written rather
#: than the section scaffolding that came with the skeleton.
STRIP_RE = re.compile(r"(\\[a-zA-Z@]+\s*(\[[^\]]*\])?(\{[^{}]*\})?|[{}$&%#_^~])")


def prose_words(tex: str) -> int:
    body = tex.split(r"\begin{document}", 1)[-1]
    body = re.sub(r"^\s*%.*$", "", body, flags=re.MULTILINE)
    body = body.replace(r"\input{generated}", "")
    return len(STRIP_RE.sub(" ", body).split())


def status_of(name: str) -> dict[str, Any]:
    directory = RUNS / name
    tex = directory / "analysis.tex"
    if not tex.exists():
        return {"name": name, "state": "missing", "words": 0, "todos": 0, "pdf": False}
    text = tex.read_text(encoding="utf-8")
    todos = len(TODO_RE.findall(text))
    return {
        "name": name,
        "state": "unwritten" if todos else "written",
        "words": prose_words(text),
        "todos": todos,
        "pdf": (directory / "analysis.pdf").exists(),
        "viewer": (directory / "viewer.png").exists(),
        "figures": len(list((directory / "figures").glob("*.pdf"))) if (directory / "figures").exists() else 0,
    }


def build(name: str, *, passes: int = 2) -> tuple[str, bool, str]:
    directory = RUNS / name
    if not (directory / "analysis.tex").exists():
        return name, False, "no analysis.tex"
    last = ""
    for _ in range(passes):
        proc = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "analysis.tex"],
            cwd=directory,
            capture_output=True,
            text=True,
            timeout=180,
        )
        last = proc.stdout
        if proc.returncode != 0:
            # The useful line is the one starting with `!`; the rest is font loading noise.
            errors = [ln for ln in last.splitlines() if ln.startswith("!")]
            return name, False, errors[0] if errors else f"pdflatex exit {proc.returncode}"
    return name, (directory / "analysis.pdf").exists(), ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="report only")
    ap.add_argument("--build-written", action="store_true", help="build documents with no TODOs left")
    ap.add_argument("--build-all", action="store_true")
    ap.add_argument("--match")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--list-unwritten", action="store_true")
    cfg = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    names = [r["name"] for r in manifest["runs"] if not cfg.match or cfg.match in r["name"]]
    rows = [status_of(n) for n in sorted(names)]

    by_state: dict[str, list[dict[str, Any]]] = {"written": [], "unwritten": [], "missing": []}
    for r in rows:
        by_state[r["state"]].append(r)

    written = by_state["written"]
    print(f"analysis documents over {len(rows)} runs")
    print(f"  written    {len(written):4d}" + (
        f"   median prose {sorted(r['words'] for r in written)[len(written) // 2]:,} words"
        if written else ""
    ))
    print(f"  unwritten  {len(by_state['unwritten']):4d}   (analysis.tex exists, \\TODO markers remain)")
    print(f"  missing    {len(by_state['missing']):4d}")
    print(f"  with a viewer screenshot: {sum(1 for r in rows if r.get('viewer')):4d}")
    print(f"  with figures:             {sum(1 for r in rows if r.get('figures')):4d}")
    print(f"  compiled to PDF:          {sum(1 for r in rows if r.get('pdf')):4d}")

    if cfg.list_unwritten:
        print("\nunwritten:")
        for r in by_state["unwritten"]:
            print(f"  {r['name']}")

    targets: list[str] = []
    if cfg.build_all:
        targets = [r["name"] for r in rows if r["state"] != "missing"]
    elif cfg.build_written:
        targets = [r["name"] for r in written]

    if targets and not cfg.status:
        print(f"\nbuilding {len(targets)} documents with {cfg.jobs} workers")
        ok = bad = 0
        with ThreadPoolExecutor(max_workers=cfg.jobs) as pool:
            for name, success, err in pool.map(build, targets):
                if success:
                    ok += 1
                else:
                    bad += 1
                    print(f"  FAIL {name}: {err}")
        print(f"built {ok}, failed {bad}")
        return 0 if bad == 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
