"""Black-box tests for deterministic scheduling and DAG execution."""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from collections.abc import Mapping
from pathlib import Path

from minidag.executor import execute
from minidag.graph import Graph, Task
from minidag.scheduler import Scheduler


_STATUS_ALIASES = {
    "pending": frozenset({"pending", "waiting"}),
    "running": frozenset({"running"}),
    "completed": frozenset({"completed", "succeeded", "successful", "success"}),
    "failed": frozenset({"failed", "failure"}),
    "blocked": frozenset({"blocked", "skipped"}),
}

_WRITE_ARGUMENT = textwrap.dedent(
    """\
    from pathlib import Path
    import sys

    Path(sys.argv[1]).write_text(sys.argv[2], encoding="utf-8")
    """
)

_FAIL_AFTER_MARKER = textwrap.dedent(
    """\
    from pathlib import Path
    import sys

    Path(sys.argv[1]).write_text("started", encoding="utf-8")
    raise SystemExit(7)
    """
)

_PARALLEL_BARRIER = textwrap.dedent(
    """\
    from pathlib import Path
    import sys
    import time

    directory = Path(sys.argv[1])
    name = sys.argv[2]
    (directory / f"{name}.ready").write_text("", encoding="utf-8")
    deadline = time.monotonic() + 5.0
    while len(tuple(directory.glob("*.ready"))) < 2:
        if time.monotonic() >= deadline:
            raise SystemExit(23)
        time.sleep(0.01)
    (directory / f"{name}.done").write_text("", encoding="utf-8")
    """
)


def _graph(*tasks: Task) -> Graph:
    """Build and validate a graph from the supplied tasks."""
    graph = Graph()
    for task in tasks:
        graph.add(task)
    graph.validate()
    return graph


def _claim_name(claim: Task | str | None) -> str | None:
    """Return a claimed task's name without constraining its representation."""
    if claim is None or isinstance(claim, str):
        return claim
    return claim.name


def _status_label(value: object) -> str | None:
    """Normalize a string or enum-like status value."""
    if isinstance(value, str):
        return value.lower()
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value.lower()
    return None


def _item_names(value: object) -> set[str]:
    """Normalize a snapshot state collection to task names."""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return {str(name) for name in value}

    names: set[str] = set()
    try:
        items = iter(value)  # type: ignore[arg-type]
    except TypeError as error:
        raise AssertionError(f"snapshot state is not a collection: {value!r}") from error
    for item in items:
        if isinstance(item, str):
            names.add(item)
        else:
            name = getattr(item, "name", None)
            if not isinstance(name, str):
                raise AssertionError(f"snapshot item has no task name: {item!r}")
            names.add(name)
    return names


def _state_names(snapshot: object, state: str) -> set[str]:
    """Extract task names in a semantic state from a public snapshot."""
    aliases = _STATUS_ALIASES[state]

    if isinstance(snapshot, Mapping):
        for key, value in snapshot.items():
            if _status_label(key) in aliases:
                return _item_names(value)

        matching = {
            str(name)
            for name, value in snapshot.items()
            if _status_label(value) in aliases
        }
        if matching or all(_status_label(value) is not None for value in snapshot.values()):
            return matching

    for alias in aliases:
        if hasattr(snapshot, alias):
            return _item_names(getattr(snapshot, alias))

    for attribute in ("states", "tasks", "status"):
        nested = getattr(snapshot, attribute, None)
        if nested is not None and nested is not snapshot:
            try:
                return _state_names(nested, state)
            except AssertionError:
                pass

    raise AssertionError(
        f"snapshot {snapshot!r} does not expose the {state!r} task state"
    )


class SchedulerTests(unittest.TestCase):
    """Verify scheduler claims, readiness, failures, and snapshots."""

    def test_claims_ready_tasks_in_lexicographic_order(self) -> None:
        """Insertion order must not affect deterministic claim order."""
        graph = _graph(
            Task("zulu", (sys.executable, "-c", "pass"), ()),
            Task("alpha", (sys.executable, "-c", "pass"), ()),
            Task("middle", (sys.executable, "-c", "pass"), ()),
        )
        scheduler = Scheduler(graph)

        claimed: list[str] = []
        for _ in range(3):
            name = _claim_name(scheduler.claim())
            if name is None:
                self.fail("scheduler exhausted claims before all ready tasks ran")
            claimed.append(name)
            scheduler.complete(name, True)

        self.assertEqual(claimed, ["alpha", "middle", "zulu"])
        self.assertIsNone(scheduler.claim())

    def test_dependency_waits_for_success_and_snapshots_are_detached(self) -> None:
        """A dependent becomes ready only after its prerequisite succeeds."""
        graph = _graph(
            Task("child", (sys.executable, "-c", "pass"), ("root",)),
            Task("root", (sys.executable, "-c", "pass"), ()),
        )
        scheduler = Scheduler(graph)

        initial = scheduler.snapshot()
        self.assertSetEqual(_state_names(initial, "pending"), {"root", "child"})
        self.assertSetEqual(_state_names(initial, "completed"), set())

        self.assertEqual(_claim_name(scheduler.claim()), "root")
        self.assertIsNone(scheduler.claim())
        running = scheduler.snapshot()
        self.assertSetEqual(_state_names(running, "running"), {"root"})
        self.assertSetEqual(_state_names(running, "pending"), {"child"})

        scheduler.complete("root", True)
        after_root = scheduler.snapshot()
        self.assertSetEqual(_state_names(after_root, "completed"), {"root"})
        self.assertSetEqual(_state_names(after_root, "pending"), {"child"})
        self.assertEqual(_claim_name(scheduler.claim()), "child")

        self.assertSetEqual(_state_names(initial, "pending"), {"root", "child"})
        self.assertSetEqual(_state_names(initial, "completed"), set())

    def test_failure_blocks_all_transitive_dependents(self) -> None:
        """A failed task must block direct and transitive dependents."""
        graph = _graph(
            Task("grandchild", (sys.executable, "-c", "pass"), ("child",)),
            Task("child", (sys.executable, "-c", "pass"), ("root",)),
            Task("root", (sys.executable, "-c", "pass"), ()),
        )
        scheduler = Scheduler(graph)

        self.assertEqual(_claim_name(scheduler.claim()), "root")
        scheduler.complete("root", False)
        self.assertIsNone(scheduler.claim())

        snapshot = scheduler.snapshot()
        self.assertSetEqual(_state_names(snapshot, "failed"), {"root"})
        self.assertSetEqual(
            _state_names(snapshot, "blocked"),
            {"child", "grandchild"},
        )
        self.assertSetEqual(_state_names(snapshot, "running"), set())
        self.assertSetEqual(_state_names(snapshot, "pending"), set())


class ExecutorTests(unittest.TestCase):
    """Verify observable executor behavior using real child processes."""

    def test_successful_dependency_chain_runs_in_order(self) -> None:
        """A successful prerequisite must enable its dependent command."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            parent_output = directory / "parent.txt"
            child_output = directory / "child.txt"
            graph = _graph(
                Task(
                    "child",
                    (
                        sys.executable,
                        "-c",
                        _WRITE_ARGUMENT,
                        str(child_output),
                        "child-ran",
                    ),
                    ("parent",),
                ),
                Task(
                    "parent",
                    (
                        sys.executable,
                        "-c",
                        _WRITE_ARGUMENT,
                        str(parent_output),
                        "parent-ran",
                    ),
                    (),
                ),
            )

            result = execute(graph, jobs=2)

            self.assertEqual(type(result).__name__, "RunResult")
            self.assertEqual(parent_output.read_text(encoding="utf-8"), "parent-ran")
            self.assertEqual(child_output.read_text(encoding="utf-8"), "child-ran")

    def test_jobs_two_runs_independent_tasks_concurrently(self) -> None:
        """Two ready tasks must overlap when the worker limit is two."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            graph = _graph(
                *(
                    Task(
                        name,
                        (
                            sys.executable,
                            "-c",
                            _PARALLEL_BARRIER,
                            temporary_directory,
                            name,
                        ),
                        (),
                    )
                    for name in ("alpha", "beta")
                )
            )

            result = execute(graph, jobs=2)

            self.assertEqual(type(result).__name__, "RunResult")
            directory = Path(temporary_directory)
            self.assertTrue((directory / "alpha.done").is_file())
            self.assertTrue((directory / "beta.done").is_file())

    def test_failed_dependency_is_never_executed(self) -> None:
        """A dependent command must not start after its prerequisite fails."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            failed_marker = directory / "failed-started.txt"
            blocked_marker = directory / "blocked-started.txt"
            graph = _graph(
                Task(
                    "blocked",
                    (
                        sys.executable,
                        "-c",
                        _WRITE_ARGUMENT,
                        str(blocked_marker),
                        "should-not-run",
                    ),
                    ("failure",),
                ),
                Task(
                    "failure",
                    (
                        sys.executable,
                        "-c",
                        _FAIL_AFTER_MARKER,
                        str(failed_marker),
                    ),
                    (),
                ),
            )

            result = execute(graph, jobs=2)

            self.assertEqual(type(result).__name__, "RunResult")
            self.assertEqual(failed_marker.read_text(encoding="utf-8"), "started")
            self.assertFalse(blocked_marker.exists())

    def test_command_arguments_are_not_interpreted_by_a_shell(self) -> None:
        """Metacharacters in an argument must reach the process literally."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            output = directory / "argument.txt"
            injected = directory / "injected.txt"
            payload = f"literal; touch {injected}"
            graph = _graph(
                Task(
                    "literal-argument",
                    (
                        sys.executable,
                        "-c",
                        _WRITE_ARGUMENT,
                        str(output),
                        payload,
                    ),
                    (),
                )
            )

            result = execute(graph)

            self.assertEqual(type(result).__name__, "RunResult")
            self.assertEqual(output.read_text(encoding="utf-8"), payload)
            self.assertFalse(injected.exists())


if __name__ == "__main__":
    unittest.main()
