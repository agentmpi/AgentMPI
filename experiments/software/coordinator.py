#!/usr/bin/env python3
"""Rank 0 of the collaborative software-development experiment.

Where the translation experiment is nearly embarrassingly parallel, this one
deliberately is not.  Eight modules of a query engine are mutually
dependent: the parser must emit exactly the tree the predicate evaluator
consumes, the executor must call all six of the others, and no module can be
tested in isolation against anything that matters.  This is the workload
where multi-agent systems are usually reported to fail, and the failures the
literature names -- agents inventing incompatible interfaces, losing track of
what a peer decided, clobbering each other's files -- are precisely the ones
a message-passing layer is supposed to prevent.

The protocol:

    bcast   root=0    the architecture and the frozen interfaces
    scatter root=0    one module assignment per rank
    win.put           each rank publishes its module's realised signature
    barrier           nobody reads interfaces before everyone has written one
    (implement)
    barrier           nobody is judged before everyone has finished
    (rank 0 runs the suite)
    bcast   root=0    the test report
    (repair round)
    barrier
    (rank 0 runs the suite again)

The two barriers are the whole point of the exercise, and they are what an
unstructured shared directory cannot express: without the first, a rank
reads an interface that has not been published yet and invents one; without
the second, a rank is judged on a suite that half its peers have not
finished contributing to and spends its repair turn chasing failures that
were never its own.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

import agentmpi as ampi  # noqa: E402
from agentmpi.win import win_create  # noqa: E402

HERE = Path(__file__).resolve().parent

MODULES = [
    {"rank": 1, "module": "tinyq/schema.py",
     "also": ["tinyq/__init__.py"], "depends_on": []},
    {"rank": 2, "module": "tinyq/csvio.py", "also": [], "depends_on": ["schema"]},
    {"rank": 3, "module": "tinyq/lexer.py", "also": [], "depends_on": []},
    {"rank": 4, "module": "tinyq/parser.py", "also": [], "depends_on": ["lexer"]},
    {"rank": 5, "module": "tinyq/predicate.py", "also": [],
     "depends_on": ["parser", "schema"]},
    {"rank": 6, "module": "tinyq/aggregate.py", "also": [], "depends_on": ["schema"]},
    {"rank": 7, "module": "tinyq/executor.py", "also": [],
     "depends_on": ["schema", "parser", "predicate", "aggregate"]},
    {"rank": 8, "module": "tinyq/cli.py", "also": ["tinyq/__main__.py"],
     "depends_on": ["csvio", "executor"]},
]


def run_suite(package_root: Path) -> dict:
    """Run the frozen integration suite and attribute failures to modules."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "--no-header",
         "-p", "no:cacheprovider", "--tb=line"],
        capture_output=True, text=True, cwd=str(package_root), timeout=900,
    )
    stdout = proc.stdout + proc.stderr
    passed = failed = errors = 0
    for line in stdout.splitlines():
        if " passed" in line or " failed" in line or " error" in line:
            for token, name in (("passed", "passed"), ("failed", "failed"),
                                ("error", "errors")):
                if token in line:
                    parts = line.replace("=", " ").split()
                    for i, part in enumerate(parts):
                        if part.startswith(token) and i > 0 and parts[i - 1].isdigit():
                            value = int(parts[i - 1])
                            if name == "passed":
                                passed = value
                            elif name == "failed":
                                failed = value
                            else:
                                errors = value
    failing_tests = [ln.split("::")[1].split()[0]
                     for ln in stdout.splitlines()
                     if "::" in ln and ("FAILED" in ln or "ERROR" in ln)]
    by_module: dict[str, int] = {}
    for name in failing_tests:
        prefix = name.removeprefix("test_").split("_")[0]
        by_module[prefix] = by_module.get(prefix, 0) + 1
    total = passed + failed + errors
    return {
        "passed": passed, "failed": failed, "errors": errors, "total": total,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "failing_tests": sorted(set(failing_tests)),
        "failures_by_area": by_module,
        "tail": "\n".join(stdout.splitlines()[-40:]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--package", required=True, help="where tinyq/ is built")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", default="agentmpi", choices=["agentmpi", "adhoc"])
    ap.add_argument("--timeout", type=float, default=7200.0)
    args = ap.parse_args()

    package = Path(args.package)
    package.mkdir(parents=True, exist_ok=True)
    (package / "tinyq").mkdir(exist_ok=True)
    shutil.copytree(HERE / "tests", package / "tests", dirs_exist_ok=True)

    rt = ampi.init(root=args.root, rank=0, device="journal")
    rt.start_heartbeat()
    comm = rt.world
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    marks: dict[str, float] = {}

    def mark(name: str) -> None:
        marks[name] = round(time.time() - t0, 2)

    spec = (HERE / "spec.md").read_text(encoding="utf-8").replace(
        "PACKAGE_ROOT", str(package))

    comm.bcast(spec, root=0, datatype="text", timeout=args.timeout)
    mark("bcast_spec")

    assignment = [None] + [
        {**m, "package_root": str(package)} for m in MODULES[:comm.size - 1]
    ]
    while len(assignment) < comm.size:
        assignment.append(None)
    comm.scatterv(assignment, root=0, datatype="json", timeout=args.timeout)
    mark("scatter_assignments")

    win = win_create(comm, "interfaces")

    # Barrier 1: every rank has published its realised interface.
    comm.barrier(timeout=args.timeout)
    mark("barrier_interfaces")
    published = win.index()
    (out / "interfaces.json").write_text(
        json.dumps(published, indent=2), encoding="utf-8")

    # Barrier 2: every rank has written its module.
    comm.barrier(timeout=args.timeout)
    mark("barrier_implemented")
    first = run_suite(package)
    (out / "suite_round1.json").write_text(json.dumps(first, indent=2),
                                           encoding="utf-8")
    print(json.dumps({"round": 1, **{k: v for k, v in first.items()
                                     if k != "tail"}}, indent=2))

    report = {
        "round": 1,
        "pass_rate": first["pass_rate"],
        "passed": first["passed"], "failed": first["failed"],
        "errors": first["errors"], "total": first["total"],
        "failing_tests": first["failing_tests"],
        "tail": first["tail"][-4000:],
    }
    comm.bcast(report, root=0, datatype="json", timeout=args.timeout)
    mark("bcast_report")

    # Barrier 3: every rank has finished its repair turn.
    comm.barrier(timeout=args.timeout)
    mark("barrier_repaired")
    second = run_suite(package)
    (out / "suite_round2.json").write_text(json.dumps(second, indent=2),
                                           encoding="utf-8")

    summary = {
        "mode": args.mode,
        "ranks": comm.size,
        "modules": comm.size - 1,
        "round1": {k: v for k, v in first.items() if k != "tail"},
        "round2": {k: v for k, v in second.items() if k != "tail"},
        "interfaces_published": len(published),
        "interface_tokens": sum(int(e.get("tokens", 0)) for e in published),
        "marks": marks,
        "wall_s": round(time.time() - t0, 2),
        "pvars": rt.pvars.snapshot(),
        "window_stats": win.stats,
    }
    (out / "coordinator.json").write_text(json.dumps(summary, indent=2),
                                          encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "pvars"}, indent=2))
    ampi.finalize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
