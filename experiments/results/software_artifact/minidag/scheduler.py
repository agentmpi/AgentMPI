"""Deterministic task scheduling for validated dependency graphs."""

from __future__ import annotations

from threading import RLock

from minidag.graph import Graph, Task


class Scheduler:
    """Track task states and claim runnable work in deterministic order."""

    def __init__(self, graph: Graph) -> None:
        """Create a scheduler for *graph* after validating its dependencies."""
        if not isinstance(graph, Graph):
            raise TypeError("graph must be a Graph instance")
        graph.validate()
        self._graph = graph
        self._completed: set[str] = set()
        self._running: set[str] = set()
        self._failed: set[str] = set()
        self._blocked: set[str] = set()
        self._lock = RLock()

    def claim(self) -> Task | None:
        """Claim and return the next runnable task, or ``None`` if none exists."""
        with self._lock:
            self._refresh_blocked()
            for task in self._graph.ready(self._completed, self._running):
                if task.name in self._failed or task.name in self._blocked:
                    continue
                self._running.add(task.name)
                return task
            return None

    def complete(self, name: str, success: bool) -> None:
        """Finish a claimed task and record whether it succeeded."""
        if not isinstance(name, str):
            raise TypeError("task name must be a string")
        if not isinstance(success, bool):
            raise TypeError("success must be a bool")

        with self._lock:
            if name not in self._graph:
                raise ValueError(f"unknown task: {name!r}")
            if name not in self._running:
                raise ValueError(f"task {name!r} is not running")

            self._running.remove(name)
            if success:
                self._completed.add(name)
            else:
                self._failed.add(name)
            self._refresh_blocked()

    def snapshot(self) -> dict[str, tuple[str, ...]]:
        """Return an immutable-by-value, deterministic view of task states."""
        with self._lock:
            self._refresh_blocked()
            terminal = self._completed | self._failed | self._blocked
            pending = set(self._graph.tasks) - terminal - self._running
            return {
                "pending": tuple(sorted(pending)),
                "running": tuple(sorted(self._running)),
                "completed": tuple(sorted(self._completed)),
                "failed": tuple(sorted(self._failed)),
                "blocked": tuple(sorted(self._blocked)),
            }

    def _refresh_blocked(self) -> None:
        terminal = self._completed | self._failed | self._blocked
        for name in self._graph.topological_order():
            if name in terminal or name in self._running:
                continue
            task = self._graph[name]
            if any(
                dependency in self._failed or dependency in self._blocked
                for dependency in task.deps
            ):
                self._blocked.add(name)
                terminal.add(name)
