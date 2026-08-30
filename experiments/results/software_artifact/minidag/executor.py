"""Concurrent execution of validated dependency graphs."""

from __future__ import annotations

import subprocess
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass

from minidag.graph import Graph, Task
from minidag.scheduler import Scheduler


@dataclass(frozen=True, slots=True)
class RunResult:
    """Summarize the terminal states of an executed graph."""

    completed: tuple[str, ...]
    failed: tuple[str, ...]
    blocked: tuple[str, ...]

    @property
    def success(self) -> bool:
        """Return whether every task completed successfully."""
        return not self.failed and not self.blocked


def _run_task(task: Task) -> bool:
    """Run one task without a shell and report whether it succeeded."""
    try:
        process = subprocess.run(task.command, check=False, shell=False)
    except (OSError, ValueError):
        return False
    return process.returncode == 0


def execute(graph: Graph, jobs: int = 1) -> RunResult:
    """Execute *graph* with at most *jobs* concurrent subprocesses.

    The complete graph is validated before any command starts. Ready tasks are
    claimed lexicographically, failures block their transitive dependents, and
    each command tuple is passed directly to the operating system without shell
    interpretation.
    """
    if not isinstance(graph, Graph):
        raise TypeError("graph must be a Graph instance")
    if isinstance(jobs, bool) or not isinstance(jobs, int):
        raise TypeError("jobs must be an integer")
    if jobs < 1:
        raise ValueError("jobs must be at least 1")

    graph.validate()
    scheduler = Scheduler(graph)
    running: dict[Future[bool], Task] = {}

    with ThreadPoolExecutor(max_workers=jobs) as executor:
        while True:
            while len(running) < jobs:
                task = scheduler.claim()
                if task is None:
                    break
                running[executor.submit(_run_task, task)] = task

            if not running:
                break

            finished, _ = wait(running, return_when=FIRST_COMPLETED)
            for future in sorted(finished, key=lambda item: running[item].name):
                task = running.pop(future)
                scheduler.complete(task.name, future.result())

    snapshot = scheduler.snapshot()
    return RunResult(
        completed=snapshot["completed"],
        failed=snapshot["failed"],
        blocked=snapshot["blocked"],
    )
