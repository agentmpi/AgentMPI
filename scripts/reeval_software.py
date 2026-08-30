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
            per_round.append(
                {
                    "round": int(rd.name.removeprefix("round")),
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
        }
        if not cfg.dry_run:
            result_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        n += 1
        traj = " / ".join(f"r{r['round']}:{r['n_passed']}" for r in per_round)
        print(f"  {result_path.name}: best {best['n_passed']}/{best['n_total']}  ({traj})")
    print(f"re-scored {n} runs against the current suite")
    return 0


def _suite_sha() -> str:
    import hashlib

    return hashlib.sha256(SUITE.read_bytes()).hexdigest()[:12]


if __name__ == "__main__":
    raise SystemExit(main())
