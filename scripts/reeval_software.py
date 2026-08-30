"""Re-score every software-experiment run against the current acceptance suite.

Why this exists
---------------
The acceptance suite is the experiment's oracle, and partway through the campaign the
oracle turned out to have a defect: one case tested case-insensitive *keywords* using
a case-varied *identifier*, which the specification says is case-sensitive. The agent
population implemented the specification correctly and the suite marked it wrong.

Two consequences. First, an oracle is a program and can be the defective party, which
is worth remembering before quoting a pass rate — a verification-based fault-tolerance
scheme (\\S8 of the spec) inherits the reliability of its verifier. Second, once the
oracle changes, every configuration must be re-scored against the *same* oracle or the
ablation comparison is meaningless: a configuration measured under the old suite and
one measured under the new are not comparable.

Re-scoring is free, because the agents' code is on disk. This script walks every run
directory, re-runs the current suite out of process against each round's tree, and
writes the results back into the run's JSON under ``acceptance_reeval``, leaving the
original ``acceptance`` field untouched so the change is auditable.

    python3 scripts/reeval_software.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
SUITE = REPO / "experiments" / "minidb" / "acceptance.py"
RESULTS = REPO / "results" / "software"
RUNS = REPO / "runs"


#: Exception types an agent catches when it is guessing at another module's shape.
_ADAPTIVE_EXCEPTIONS = {"AttributeError", "KeyError", "TypeError", "IndexError"}


def defensiveness(tree: Path) -> dict[str, Any]:
    """Count the defensive-adaptation constructs in a generated tree.

    This metric exists because the interface-publication ablation came back nearly
    null on pass rate, and two agent ranks explained why in their own reports: with
    no published interfaces they did not fail, they *adapted*. One re-classified
    tokens by value when it could not know the token-kind vocabulary; another
    constructed AST nodes by matching values onto whatever field names the class
    actually declared; a third reached for ``getattr`` fallbacks throughout.

    So the cost of withholding interface information is not correctness. It is
    **defensive coupling**: code that introspects its collaborators at runtime
    instead of calling them. That is invisible to an acceptance suite and obvious in
    the source, and it is the thing worth measuring. Counted precisely with the AST
    rather than by regular expression, because a comment mentioning ``getattr`` is
    not defensive code.

    Three constructs are counted:

    ``getattr_default``
        Three-argument ``getattr``: reading an attribute the author is not certain
        exists.
    ``hasattr``
        Probing for shape before use.
    ``adaptive_except``
        Handlers for ``AttributeError``/``KeyError``/``TypeError``/``IndexError``,
        which are what a wrong guess about an interface raises. Handlers for the
        project's own ``QueryError`` are deliberately excluded: catching a declared
        error is correct design, not adaptation.
    """
    counts = {"getattr_default": 0, "hasattr": 0, "adaptive_except": 0, "n_files": 0, "n_lines": 0}
    per_file: dict[str, int] = {}
    import ast

    for path in sorted((tree / "minidb").glob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        counts["n_files"] += 1
        counts["n_lines"] += source.count("\n") + 1
        try:
            module = ast.parse(source)
        except SyntaxError:
            continue
        local = 0
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "getattr" and len(node.args) >= 3:
                    counts["getattr_default"] += 1
                    local += 1
                elif node.func.id == "hasattr":
                    counts["hasattr"] += 1
                    local += 1
            elif isinstance(node, ast.ExceptHandler) and node.type is not None:
                names = {
                    n.id
                    for n in ast.walk(node.type)
                    if isinstance(n, ast.Name)
                }
                if names & _ADAPTIVE_EXCEPTIONS:
                    counts["adaptive_except"] += 1
                    local += 1
        per_file[path.name] = local

    total = counts["getattr_default"] + counts["hasattr"] + counts["adaptive_except"]
    counts["total"] = total
    counts["per_kloc"] = round(1000.0 * total / max(1, counts["n_lines"]), 2)
    counts["per_file"] = per_file
    return counts


def score(tree: Path, timeout: float = 240.0) -> dict[str, Any]:
    """Run the current suite against one generated tree, out of process."""
    shutil.copy(SUITE, tree / "acceptance.py")
    # Remove any stale bytecode so a previous suite version cannot be imported.
    for pyc in tree.rglob("__pycache__"):
        shutil.rmtree(pyc, ignore_errors=True)
    try:
        proc = subprocess.run(
            [sys.executable, "acceptance.py"], cwd=tree, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {"importable": False, "import_error": "timed out", "n_total": 0, "n_passed": 0}
    return parse_report(proc.stdout, proc.stderr)


def parse_report(stdout: str, stderr: str = "") -> dict[str, Any]:
    """Parse the suite's JSON report from its stdout.

    The suite prints exactly one JSON object, so this is ``json.loads``. It is a
    named function with a test because an earlier version sliced from
    ``out.rfind("{")`` -- reaching for the *last* brace in the output, which lands
    inside a nested per-case object and never parses. That silently reported every
    run as unimportable with zero passes, which mattered far more than it sounds:
    the harness feeds this report back to the population as the definition of done,
    so the agents were told a build that passed 58 of 59 cases had failed to import,
    and spent a whole repair round on a phantom.
    """
    out = (stdout or "").strip()
    if not out:
        return {"importable": False, "import_error": (stderr or "")[-1200:], "n_total": 0, "n_passed": 0}
    for candidate in (out, out[out.find("{") :] if "{" in out else ""):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {"importable": False, "import_error": out[-1200:], "n_total": 0, "n_passed": 0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(RUNS))
    ap.add_argument("--dry-run", action="store_true")
    cfg = ap.parse_args()
    runs_dir = Path(cfg.runs)

    n = 0
    for result_path in sorted(RESULTS.glob("*.json")):
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        root = Path(payload.get("fabric_root") or "")
        if not root.is_absolute():
            root = REPO / root
        workspace = root / "workspace"
        if not workspace.exists():
            # Fall back to matching by name, since a run may have been relocated.
            candidates = [d for d in runs_dir.glob("*/workspace") if d.parent.name in str(result_path)]
            if not candidates:
                print(f"  skip {result_path.name}: no workspace on disk")
                continue
            workspace = candidates[0]

        rounds = sorted(workspace.glob("round*"), key=lambda p: int(p.name.removeprefix("round") or 0))
        per_round = []
        for rd in rounds:
            if not (rd / "minidb").exists():
                continue
            rep = score(rd)
            defence = defensiveness(rd)
            per_round.append(
                {
                    "round": int(rd.name.removeprefix("round")),
                    "defensiveness": defence,
                    "importable": rep.get("importable"),
                    "n_passed": rep.get("n_passed"),
                    "n_total": rep.get("n_total"),
                    "pass_rate": rep.get("pass_rate"),
                    "by_module": rep.get("by_module"),
                    "blame": rep.get("blame"),
                    "failures": [
                        {"name": c["name"], "reason": (c.get("reason") or "")[:200]}
                        for c in rep.get("cases", [])
                        if not c.get("passed")
                    ],
                }
            )
        if not per_round:
            print(f"  skip {result_path.name}: no generated trees")
            continue
        best = max(per_round, key=lambda r: (r["n_passed"] or 0))
        payload["acceptance_reeval"] = {
            "suite_sha": _suite_sha(),
            "per_round": per_round,
            "best_round": best["round"],
            "importable": best["importable"],
            "n_passed": best["n_passed"],
            "n_total": best["n_total"],
            "pass_rate": best["pass_rate"],
            "by_module": best["by_module"],
            "blame": best["blame"],
            "failures": best["failures"],
            "defensiveness": best.get("defensiveness"),
        }
        if not cfg.dry_run:
            result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        n += 1
        traj = " / ".join(f"r{r['round']}:{r['n_passed']}" for r in per_round)
        dfc = best.get("defensiveness") or {}
        print(
            f"  {result_path.name}: best {best['n_passed']}/{best['n_total']}  ({traj})"
            f"  defensive={dfc.get('total', 0)} ({dfc.get('per_kloc', 0)}/kloc, {dfc.get('n_lines', 0)} lines)"
        )
    print(f"re-scored {n} runs against the current suite")
    return 0


def _suite_sha() -> str:
    import hashlib

    return hashlib.sha256(SUITE.read_bytes()).hexdigest()[:12]


if __name__ == "__main__":
    raise SystemExit(main())
