"""Parse strict JSON task graphs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeAlias

from minidag.graph import Graph, Task


JsonObject: TypeAlias = dict[str, object]


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _require_object(value: object, location: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    return value


def _require_string(value: object, location: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{location} must be a string")
    if not value:
        raise ValueError(f"{location} must not be empty")
    return value


def _require_string_array(
    value: object,
    location: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{location} must be an array of strings")
    if not allow_empty and not value:
        raise ValueError(f"{location} must not be empty")

    items: list[str] = []
    for index, item in enumerate(value):
        items.append(_require_string(item, f"{location}[{index}]"))
    return tuple(items)


def _parse_task(value: object, index: int) -> Task:
    location = f"tasks[{index}]"
    task = _require_object(value, location)
    required = {"name", "command"}
    allowed = required | {"deps"}
    missing = required - task.keys()
    unknown = task.keys() - allowed
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"{location} is missing required field(s): {names}")
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"{location} has unknown field(s): {names}")

    name = _require_string(task["name"], f"{location}.name")
    command = _require_string_array(
        task["command"],
        f"{location}.command",
        allow_empty=False,
    )
    deps = _require_string_array(
        task.get("deps", []),
        f"{location}.deps",
        allow_empty=True,
    )
    if len(deps) != len(set(deps)):
        raise ValueError(f"{location}.deps contains duplicate task names")
    return Task(name=name, command=command, deps=deps)


def load(path: Path) -> Graph:
    """Load and validate a task graph from a strict JSON document."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read graph file {path}: {exc}") from exc

    try:
        document = json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid graph JSON in {path}: {exc}") from exc

    root = _require_object(document, "document")
    if set(root) != {"tasks"}:
        missing = {"tasks"} - root.keys()
        unknown = root.keys() - {"tasks"}
        details: list[str] = []
        if missing:
            details.append("missing required field: tasks")
        if unknown:
            details.append(f"unknown field(s): {', '.join(sorted(unknown))}")
        raise ValueError(f"invalid document object: {'; '.join(details)}")

    task_values = root["tasks"]
    if not isinstance(task_values, list):
        raise ValueError("document.tasks must be an array")

    graph = Graph()
    names: set[str] = set()
    for index, value in enumerate(task_values):
        task = _parse_task(value, index)
        if task.name in names:
            raise ValueError(f"duplicate task name: {task.name!r}")
        names.add(task.name)
        graph.add(task)
    graph.validate()
    return graph
