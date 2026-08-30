"""Task and directed acyclic graph primitives."""

from __future__ import annotations

import heapq
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class Task:
    """A named command and the names of the tasks it depends on."""

    name: str
    command: tuple[str, ...]
    deps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate the task's value types and basic invariants."""
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("task name must be a non-empty string")
        if not isinstance(self.command, tuple) or not all(
            isinstance(argument, str) for argument in self.command
        ):
            raise TypeError("task command must be a tuple of strings")
        if not self.command:
            raise ValueError(f"task {self.name!r} must have a non-empty command")
        if not isinstance(self.deps, tuple) or not all(
            isinstance(dependency, str) and dependency
            for dependency in self.deps
        ):
            raise TypeError("task dependencies must be a tuple of non-empty strings")
        if len(set(self.deps)) != len(self.deps):
            raise ValueError(f"task {self.name!r} has duplicate dependencies")


class Graph:
    """A collection of tasks linked by dependency names."""

    def __init__(self) -> None:
        """Create an empty graph."""
        self._tasks: dict[str, Task] = {}

    @property
    def tasks(self) -> Mapping[str, Task]:
        """Return a read-only mapping of task names to tasks."""
        return MappingProxyType(self._tasks)

    def __len__(self) -> int:
        """Return the number of tasks in the graph."""
        return len(self._tasks)

    def __iter__(self) -> Iterator[Task]:
        """Iterate over tasks in lexicographic name order."""
        return (self._tasks[name] for name in sorted(self._tasks))

    def __contains__(self, name: object) -> bool:
        """Return whether *name* identifies a task in the graph."""
        return name in self._tasks

    def __getitem__(self, name: str) -> Task:
        """Return the task named *name*."""
        return self._tasks[name]

    def add(self, task: Task) -> None:
        """Add *task*, rejecting an already registered task name."""
        if not isinstance(task, Task):
            raise TypeError("graph entries must be Task instances")
        if task.name in self._tasks:
            raise ValueError(f"duplicate task name: {task.name!r}")
        self._tasks[task.name] = task

    def validate(self) -> None:
        """Raise ``ValueError`` if a dependency is unknown or a cycle exists."""
        for name in sorted(self._tasks):
            for dependency in self._tasks[name].deps:
                if dependency not in self._tasks:
                    raise ValueError(
                        f"task {name!r} has unknown dependency {dependency!r}"
                    )

        state: dict[str, int] = {}
        path: list[str] = []
        path_indexes: dict[str, int] = {}

        for root in sorted(self._tasks):
            if state.get(root, 0) != 0:
                continue

            state[root] = 1
            path_indexes[root] = len(path)
            path.append(root)
            stack: list[tuple[str, Iterator[str]]] = [
                (root, iter(sorted(self._tasks[root].deps)))
            ]

            while stack:
                name, dependencies = stack[-1]
                try:
                    dependency = next(dependencies)
                except StopIteration:
                    stack.pop()
                    path.pop()
                    path_indexes.pop(name)
                    state[name] = 2
                    continue

                dependency_state = state.get(dependency, 0)
                if dependency_state == 0:
                    state[dependency] = 1
                    path_indexes[dependency] = len(path)
                    path.append(dependency)
                    stack.append(
                        (
                            dependency,
                            iter(sorted(self._tasks[dependency].deps)),
                        )
                    )
                elif dependency_state == 1:
                    cycle_start = path_indexes[dependency]
                    cycle = path[cycle_start:] + [dependency]
                    raise ValueError(f"dependency cycle detected: {' -> '.join(cycle)}")

    def topological_order(self) -> tuple[str, ...]:
        """Return task names in deterministic topological order."""
        self.validate()

        dependents: dict[str, list[str]] = {name: [] for name in self._tasks}
        indegree: dict[str, int] = {}
        for name, task in self._tasks.items():
            indegree[name] = len(task.deps)
            for dependency in task.deps:
                dependents[dependency].append(name)

        available = [name for name, degree in indegree.items() if degree == 0]
        heapq.heapify(available)
        order: list[str] = []
        while available:
            name = heapq.heappop(available)
            order.append(name)
            for dependent in sorted(dependents[name]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(available, dependent)
        return tuple(order)

    def ready(
        self,
        completed: Collection[str],
        running: Collection[str],
    ) -> list[Task]:
        """Return runnable tasks ordered lexicographically by name."""
        self.validate()
        completed_names = set(completed)
        unavailable_names = completed_names | set(running)
        return [
            task
            for name, task in sorted(self._tasks.items())
            if name not in unavailable_names
            and all(dependency in completed_names for dependency in task.deps)
        ]
