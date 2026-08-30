"""Contract tests for graph construction and strict JSON parsing."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from minidag.graph import Graph, Task
from minidag.parser import load


def _task(
    name: str,
    deps: tuple[str, ...] = (),
    command: tuple[str, ...] = ("python", "-V"),
) -> Task:
    """Create a small task suitable for graph-only tests."""
    return Task(name=name, command=command, deps=deps)


class GraphTests(unittest.TestCase):
    """Verify graph validation, ordering, and ready-state behavior."""

    def test_normal_graph_exposes_immutable_tasks(self) -> None:
        """A valid graph should retain frozen tasks and support lookup."""
        graph = Graph()
        compile_task = _task("compile")
        test_task = _task("test", ("compile",))

        graph.add(test_task)
        graph.add(compile_task)
        self.assertIsNone(graph.validate())

        self.assertEqual(len(graph), 2)
        self.assertIn("compile", graph)
        self.assertIs(graph["test"], test_task)
        self.assertEqual([task.name for task in graph], ["compile", "test"])
        with self.assertRaises(TypeError):
            graph.tasks["deploy"] = _task("deploy")  # type: ignore[index]
        with self.assertRaises(FrozenInstanceError):
            compile_task.name = "renamed"  # type: ignore[misc]

    def test_duplicate_task_names_are_rejected(self) -> None:
        """Adding a second task with an existing name must fail usefully."""
        graph = Graph()
        graph.add(_task("build"))

        with self.assertRaisesRegex(ValueError, r"(?i)duplicate.*build"):
            graph.add(_task("build", command=("other-program",)))

    def test_duplicate_dependencies_are_rejected(self) -> None:
        """A task cannot name the same dependency more than once."""
        with self.assertRaisesRegex(ValueError, r"(?i)duplicate"):
            _task("package", ("build", "build"))

    def test_unknown_dependency_is_rejected(self) -> None:
        """Validation must identify both the task and its missing dependency."""
        graph = Graph()
        graph.add(_task("deploy", ("package",)))

        with self.assertRaisesRegex(
            ValueError,
            r"(?is)(deploy.*package|package.*deploy)",
        ):
            graph.validate()

    def test_multi_task_cycle_is_rejected(self) -> None:
        """Validation must reject cycles and report their participants."""
        graph = Graph()
        graph.add(_task("alpha", ("charlie",)))
        graph.add(_task("bravo", ("alpha",)))
        graph.add(_task("charlie", ("bravo",)))

        with self.assertRaisesRegex(
            ValueError,
            r"(?is)(cycle.*alpha.*(bravo|charlie)|alpha.*(bravo|charlie).*cycle)",
        ):
            graph.validate()

    def test_self_dependency_is_a_cycle(self) -> None:
        """A one-node self dependency is not a valid DAG."""
        graph = Graph()
        graph.add(_task("recursive", ("recursive",)))

        with self.assertRaisesRegex(ValueError, r"(?i)cycle.*recursive"):
            graph.validate()

    def test_topological_order_uses_lexicographic_ready_ties(self) -> None:
        """Insertion order must not affect deterministic ready-node selection."""
        graph = Graph()
        for task in (
            _task("aardvark", ("zeta",)),
            _task("zeta"),
            _task("bravo", ("alpha",)),
            _task("alpha"),
        ):
            graph.add(task)

        self.assertEqual(
            list(graph.topological_order()),
            ["alpha", "bravo", "zeta", "aardvark"],
        )
        self.assertEqual(
            list(graph.topological_order()),
            ["alpha", "bravo", "zeta", "aardvark"],
        )

    def test_ready_respects_completed_running_and_dependencies(self) -> None:
        """Ready tasks must be sorted and exclude unavailable tasks."""
        graph = Graph()
        for task in (
            _task("zulu"),
            _task("final", ("child", "alpha")),
            _task("child", ("root",)),
            _task("root"),
            _task("alpha"),
        ):
            graph.add(task)

        self.assertEqual(
            [task.name for task in graph.ready(completed=(), running=())],
            ["alpha", "root", "zulu"],
        )
        self.assertEqual(
            [
                task.name
                for task in graph.ready(
                    completed={"root"},
                    running={"alpha"},
                )
            ],
            ["child", "zulu"],
        )
        self.assertEqual(
            [
                task.name
                for task in graph.ready(
                    completed={"alpha", "child", "root"},
                    running=(),
                )
            ],
            ["final", "zulu"],
        )


class ParserTests(unittest.TestCase):
    """Verify valid loading and strict rejection of malformed documents."""

    def test_loads_normal_graph_and_defaults_dependencies(self) -> None:
        """The parser should preserve argument arrays and default deps to empty."""
        document = {
            "tasks": [
                {
                    "name": "test",
                    "command": ["python", "-m", "unittest"],
                    "deps": ["compile"],
                },
                {
                    "name": "compile",
                    "command": ["python", "-c", "print('ok')"],
                },
            ]
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "graph.json"
            path.write_text(json.dumps(document), encoding="utf-8")

            graph = load(path)

        self.assertEqual(list(graph.topological_order()), ["compile", "test"])
        self.assertEqual(
            graph["compile"],
            Task("compile", ("python", "-c", "print('ok')"), ()),
        )
        self.assertEqual(graph["test"].deps, ("compile",))

    def test_rejects_duplicate_task_names(self) -> None:
        """Duplicate task names in JSON must produce a useful error."""
        document = {
            "tasks": [
                {"name": "build", "command": ["first"]},
                {"name": "build", "command": ["second"]},
            ]
        }

        self._assert_invalid_document(
            json.dumps(document),
            r"(?i)duplicate.*build",
        )

    def test_rejects_duplicate_dependencies(self) -> None:
        """Repeated dependency names must be rejected by the parser."""
        document = {
            "tasks": [
                {"name": "build", "command": ["build"]},
                {
                    "name": "test",
                    "command": ["test"],
                    "deps": ["build", "build"],
                },
            ]
        }

        self._assert_invalid_document(json.dumps(document), r"(?i)duplicate")

    def test_rejects_unknown_dependency(self) -> None:
        """Loading validates references after parsing all task declarations."""
        document = {
            "tasks": [
                {
                    "name": "deploy",
                    "command": ["deploy"],
                    "deps": ["package"],
                }
            ]
        }

        self._assert_invalid_document(
            json.dumps(document),
            r"(?is)(deploy.*package|package.*deploy)",
        )

    def test_rejects_cycle(self) -> None:
        """Loading must validate and reject cyclic dependency data."""
        document = {
            "tasks": [
                {"name": "alpha", "command": ["a"], "deps": ["beta"]},
                {"name": "beta", "command": ["b"], "deps": ["alpha"]},
            ]
        }

        self._assert_invalid_document(json.dumps(document), r"(?i)cycle")

    def test_rejects_duplicate_json_object_keys(self) -> None:
        """Strict parsing must detect duplicate keys at every object level."""
        documents = {
            "root": '{"tasks": [], "tasks": []}',
            "task": (
                '{"tasks": [{"name": "one", "name": "two", '
                '"command": ["run"]}]}'
            ),
        }
        for location, document in documents.items():
            with self.subTest(location=location):
                self._assert_invalid_document(
                    document,
                    r"(?i)(duplicate JSON key|duplicate.*key)",
                )

    def test_rejects_invalid_json_syntax(self) -> None:
        """Comments, trailing commas, and trailing data are not valid JSON."""
        documents = {
            "comment": '{"tasks": [] /* comment */}',
            "trailing comma": '{"tasks": [],}',
            "trailing data": '{"tasks": []} garbage',
        }
        for problem, document in documents.items():
            with self.subTest(problem=problem):
                self._assert_invalid_document(document, r"(?i)invalid.*JSON")

    def test_rejects_invalid_document_shapes_and_fields(self) -> None:
        """The document schema must reject missing, extra, and wrong fields."""
        cases = (
            ("non-object root", "[]", r"(?i)document.*object"),
            ("missing tasks", "{}", r"(?i)missing.*tasks"),
            (
                "unknown root field",
                '{"tasks": [], "extra": true}',
                r"(?i)unknown.*extra",
            ),
            (
                "tasks not array",
                '{"tasks": {}}',
                r"(?i)tasks.*array",
            ),
            (
                "task not object",
                '{"tasks": [null]}',
                r"(?i)tasks\[0\].*object",
            ),
            (
                "missing name",
                '{"tasks": [{"command": ["run"]}]}',
                r"(?i)missing.*name",
            ),
            (
                "missing command",
                '{"tasks": [{"name": "run"}]}',
                r"(?i)missing.*command",
            ),
            (
                "unknown task field",
                '{"tasks": [{"name": "run", "command": ["run"], "x": 1}]}',
                r"(?i)unknown.*x",
            ),
        )
        for problem, document, message_pattern in cases:
            with self.subTest(problem=problem):
                self._assert_invalid_document(document, message_pattern)

    def test_rejects_invalid_task_value_types_and_empty_strings(self) -> None:
        """Task values must be non-empty strings in correctly typed arrays."""
        invalid_tasks = (
            ('{"name": "", "command": ["run"]}', r"(?i)name.*empty"),
            ('{"name": 3, "command": ["run"]}', r"(?i)name.*string"),
            ('{"name": "task", "command": "run"}', r"(?i)command.*array"),
            ('{"name": "task", "command": []}', r"(?i)command.*empty"),
            ('{"name": "task", "command": [""]}', r"(?i)command\[0\].*empty"),
            ('{"name": "task", "command": [1]}', r"(?i)command\[0\].*string"),
            (
                '{"name": "task", "command": ["run"], "deps": "build"}',
                r"(?i)deps.*array",
            ),
            (
                '{"name": "task", "command": ["run"], "deps": [""]}',
                r"(?i)deps\[0\].*empty",
            ),
            (
                '{"name": "task", "command": ["run"], "deps": [1]}',
                r"(?i)deps\[0\].*string",
            ),
        )
        for task, message_pattern in invalid_tasks:
            with self.subTest(task=task):
                self._assert_invalid_document(
                    f'{{"tasks": [{task}]}}',
                    message_pattern,
                )

    def test_rejects_non_utf8_input(self) -> None:
        """Graph files must contain valid UTF-8 encoded JSON."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "graph.json"
            path.write_bytes(b'{"tasks": []}\xff')

            with self.assertRaisesRegex(ValueError, r"(?i)(read|UTF)"):
                load(path)

    def _assert_invalid_document(self, document: str, pattern: str) -> None:
        """Assert that loading document fails with the expected useful message."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "graph.json"
            path.write_text(document, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, pattern):
                load(path)


if __name__ == "__main__":
    unittest.main()
