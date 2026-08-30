"""Campaign driver: run a sequence of experiment configurations against one
persistent pool of agent workers.

Why this exists
---------------
For real agent ranks the dominant cost is not the work but the *population*:
launching an agent session, giving it its standing instructions, and waiting for
it to reach the point where it can accept work.  A naive experimental design
launches a fresh population per configuration, so an ablation study over *k*
configurations pays that cost *k* times and most of the wall clock is spent
starting agents rather than measuring them.

The campaign directory is a one-file indirection that fixes this.  Workers poll
``<campaign>/active``, which names the fabric of the currently running job; the
driver runs each configuration in turn, flipping the pointer between them.  The
same pool therefore serves every ablation, and the population is launched once.
This is the same reason an HPC allocation runs many phases inside one job rather
than submitting one job per phase.

It also has a methodological benefit worth stating: because every configuration is
served by the *same* agents in the same session order, differences between
configurations are not confounded by differences in the population.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agentmpi as ampi
from common import REPO, provenance, write_result  # noqa: E402


@dataclass
class Step:
    """One configuration to run."""

    name: str
    module: str
    args: list[str]
    #: Ranks that must be served by workers for this step.
    ranks: int
    root: Path
    timeout_s: float = 10800.0
    result: dict[str, Any] = field(default_factory=dict)


class Campaign:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "campaign.log"
        #: Last value this campaign wrote to the pointer, so `deactivate` can tell
        #: whether it is still ours to clear.
        self._last_activated: str | None = None

    # ---- pointer management ----

    def activate(self, root: Path) -> None:
        resolved = str(Path(root).resolve())
        (self.dir / "active").write_text(resolved, encoding="utf-8")
        self._last_activated = resolved
        self.log(f"activate {root}")

    def deactivate(self) -> None:
        """Clear the pointer only if it still names *our* job.

        A compare-and-swap, not a blind write, and the reason is embarrassing enough
        to record. Two campaigns sharing a campaign directory ran concurrently: the
        second activated its job, and then the first finished and cleared the pointer,
        stranding the second's worker pool with nothing to poll. The population sat
        idle while a reduction waited on a rank that could no longer find its work.

        That is a lost update on a shared cell with two writers and no synchronisation
        -- precisely the bug that AgentMPI's own ``Window.compare_and_swap`` exists to
        prevent, committed in the harness that runs the experiments. It is a fair
        illustration of the paper's argument arriving as a self-inflicted wound: the
        discipline is easy to state and easy to skip, and skipping it fails silently
        rather than loudly.
        """
        active = self.dir / "active"
        current = active.read_text(encoding="utf-8").strip() if active.exists() else ""
        expected = getattr(self, "_last_activated", None)
        if expected is not None and current != expected:
            self.log(f"not clearing pointer: now held by {current or '(empty)'}, not ours ({expected})")
            return
        active.write_text("", encoding="utf-8")

    def stop(self) -> None:
        (self.dir / "stop").write_text("1", encoding="utf-8")
        self.log("stop requested")

    def clear_stop(self) -> None:
        (self.dir / "stop").unlink(missing_ok=True)

    def log(self, message: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {message}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # ---- status ----

    def status(self) -> dict[str, Any]:
        root = (self.dir / "active").read_text(encoding="utf-8").strip() if (self.dir / "active").exists() else ""
        out: dict[str, Any] = {"campaign": str(self.dir), "active": root, "stopped": (self.dir / "stop").exists()}
        if root and (Path(root) / "fabric.sqlite").exists():
            fabric = ampi.Fabric(root)
            out["broker"] = ampi.executor.pending_summary(fabric)
            out["ranks"] = [
                {
                    "rank": r.rank,
                    "state": r.state,
                    "alive": r.alive,
                    "calls": r.n_calls,
                    "suspected": r.suspected.value if r.suspected else None,
                }
                for r in ampi.ft.health(fabric)
            ]
            out["summary"] = ampi.cost.summarise(fabric).as_dict()
        return out

    # ---- execution ----

    def run_step(self, step: Step) -> dict[str, Any]:
        self.activate(step.root)
        self.log(f"START {step.name}: python3 -m {step.module} {' '.join(step.args)}")
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, str(REPO / "experiments" / f"{step.module}.py"), *step.args],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=step.timeout_s,
        )
        wall = time.time() - t0
        self.log(f"END   {step.name} rc={proc.returncode} in {wall:.0f}s")
        if proc.returncode != 0:
            self.log(f"  stderr tail: {proc.stderr.strip()[-800:]}")
        return {
            "name": step.name,
            "returncode": proc.returncode,
            "wall_s": round(wall, 1),
            "stdout_tail": proc.stdout.strip()[-4000:],
            "stderr_tail": proc.stderr.strip()[-2000:],
            "root": str(step.root),
        }


# ---------------------------------------------------------------- step recipes


def translation_steps(*, ranks: int, words: int, prefix: str) -> list[Step]:
    """The translation ablation ladder.

    Order matters.  The full-protocol configuration runs first so that a campaign
    truncated by a time limit still yields the headline result, and the ablations
    follow in decreasing order of expected effect size.  Configurations that
    differ only in an algorithm choice are adjacent so that any drift in the
    population over the campaign affects them equally.
    """
    base = ["--executor", "broker", "--words-per-unit", str(words), "--units", str(ranks)]
    out: list[Step] = []

    def add(name: str, extra: list[str], p: int) -> None:
        root = REPO / "runs" / f"{prefix}-{name}"
        out.append(
            Step(
                name=name,
                module="translation",
                args=[*base, "--ranks", str(p), "--root", str(root), "--label", prefix, *extra],
                ranks=p,
                root=root,
            )
        )

    add(f"p{ranks}-full", [], ranks)
    add(f"p{ranks}-noglossary", ["--no-glossary"], ranks)
    add(f"p{ranks}-nohalo", ["--no-halo"], ranks)
    # The strong-scaling ladder: identical total work, decreasing population.
    for p in (8, 4, 2, 1):
        if p < ranks:
            add(f"p{p}-full", [], p)
    add(f"p{ranks}-semanticgloss", ["--glossary-op", "semantic"], ranks)
    add(f"p{ranks}-rdallreduce", ["--allreduce-alg", "recursive_doubling"], ranks)
    return out


def software_steps(*, ranks: int, prefix: str, rounds: int) -> list[Step]:
    base = ["--executor", "broker", "--rounds", str(rounds)]
    out: list[Step] = []

    def add(name: str, extra: list[str], p: int) -> None:
        root = REPO / "runs" / f"{prefix}-{name}"
        out.append(
            Step(
                name=name,
                module="software",
                args=[*base, "--ranks", str(p), "--root", str(root), "--label", prefix, *extra],
                ranks=p,
                root=root,
            )
        )

    add(f"p{ranks}-full", [], ranks)
    add(f"p{ranks}-noshared", ["--no-shared-interfaces"], ranks)
    add(f"p{ranks}-nolocks", ["--no-locks"], ranks)
    add(f"p{ranks}-noreview", ["--no-review"], ranks)
    add("p1-full", [], 1)
    return out


def microbench_steps(*, ranks: int, prefix: str) -> list[Step]:
    out: list[Step] = []
    for name, extra, p in (
        ("pingpong", ["--bench", "pingpong"], 2),
        ("collectives", ["--bench", "collectives"], ranks),
        # A saturating configuration: enough items per rank, and a tight enough merge
        # budget, that the operator must discard. Without saturation every algorithm
        # retains everything and the depth effect is unobservable.
        ("fidelity", ["--bench", "fidelity", "--facts", "12", "--merge-budget", "450",
                      "--algorithms", "chain,flat,binomial,kary", "--fanin", "4"], ranks),
        ("faults", ["--bench", "faults"], ranks),
    ):
        root = REPO / "runs" / f"{prefix}-{name}"
        out.append(
            Step(
                name=name,
                module="microbench",
                args=["--executor", "broker", "--ranks", str(p), "--root", str(root), "--label", prefix, *extra],
                ranks=p,
                root=root,
            )
        )
    return out


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a sequence of AgentMPI experiments against one worker pool")
    ap.add_argument("--dir", default=str(REPO / "runs" / "campaign"))
    ap.add_argument("--suite", choices=["translation", "software", "microbench", "all"], default="translation")
    ap.add_argument("--ranks", type=int, default=16)
    ap.add_argument("--words-per-unit", type=int, default=700)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--prefix", default="run")
    ap.add_argument("--only", action="append", help="run only the named steps")
    ap.add_argument("--status", action="store_true", help="print campaign status and exit")
    ap.add_argument("--stop", action="store_true", help="ask workers to exit and exit")
    ap.add_argument("--step-timeout", type=float, default=10800.0)
    cfg = ap.parse_args(argv)

    camp = Campaign(Path(cfg.dir))
    if cfg.status:
        print(json.dumps(camp.status(), indent=2, default=str))
        return 0
    if cfg.stop:
        camp.stop()
        return 0

    camp.clear_stop()
    steps: list[Step] = []
    if cfg.suite in ("translation", "all"):
        steps += translation_steps(ranks=cfg.ranks, words=cfg.words_per_unit, prefix=f"{cfg.prefix}-tr")
    if cfg.suite in ("microbench", "all"):
        steps += microbench_steps(ranks=cfg.ranks, prefix=f"{cfg.prefix}-mb")
    if cfg.suite in ("software", "all"):
        steps += software_steps(ranks=cfg.ranks, prefix=f"{cfg.prefix}-sw", rounds=cfg.rounds)
    if cfg.only:
        wanted = set(cfg.only)
        steps = [s for s in steps if s.name in wanted]
    for s in steps:
        s.timeout_s = cfg.step_timeout

    camp.log(f"campaign {cfg.suite}: {len(steps)} steps -> {[s.name for s in steps]}")
    results = []
    for step in steps:
        try:
            results.append(camp.run_step(step))
        except subprocess.TimeoutExpired:
            camp.log(f"TIMEOUT {step.name}")
            results.append({"name": step.name, "returncode": -1, "error": "timeout", "root": str(step.root)})
        write_result(
            f"campaign-{cfg.prefix}-{cfg.suite}",
            {"provenance": provenance(suite=cfg.suite), "config": vars(cfg), "steps": results},
        )
    camp.deactivate()
    camp.log("campaign complete")
    print(json.dumps({"steps": results}, indent=2, default=str))
    return 0 if all(r.get("returncode") == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
