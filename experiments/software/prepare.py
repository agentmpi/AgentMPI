"""Prepare the dependency-coupled collaborative software workload."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from agentmpi import Runtime


SESSION = "dag-dev"
SIZE = 13
INTEGRATOR = 12
INTERFACE = {
    "project": "minidag",
    "goal": "A dependency-free Python 3.11 library and CLI for deterministic DAG execution.",
    "public_api": {
        "minidag.graph.Task": "frozen dataclass(name: str, command: tuple[str, ...], deps: tuple[str, ...])",
        "minidag.graph.Graph": "add(task), validate(), topological_order(), ready(completed, running)",
        "minidag.parser.load": "load(path: Path) -> Graph from strict JSON",
        "minidag.scheduler.Scheduler": "claim(), complete(name, success), snapshot()",
        "minidag.executor.execute": "execute(graph, jobs=1) -> RunResult",
        "minidag.cli.main": "CLI taking graph JSON and --jobs",
    },
    "constraints": [
        "Use only the Python standard library.",
        "Reject duplicate tasks, unknown dependencies, and cycles with useful errors.",
        "Deterministic tie-breaking is lexicographic by task name.",
        "A failed task prevents dependent tasks from running.",
        "Do not execute shell strings; commands are argument arrays.",
        "All public functions and classes require type annotations and docstrings.",
    ],
}

TASKS: dict[int, dict[str, Any]] = {
    1: {
        "role": "graph implementer",
        "files": ["minidag/graph.py"],
        "send_done_to": [9, 12],
    },
    2: {
        "role": "parser implementer",
        "files": ["minidag/parser.py"],
        "send_done_to": [9, 12],
    },
    3: {
        "role": "scheduler implementer",
        "files": ["minidag/scheduler.py"],
        "send_done_to": [9, 12],
    },
    4: {
        "role": "executor implementer",
        "files": ["minidag/executor.py"],
        "send_done_to": [9, 12],
    },
    5: {
        "role": "CLI implementer",
        "files": ["minidag/cli.py", "minidag/__main__.py"],
        "send_done_to": [10, 12],
    },
    6: {
        "role": "graph and parser test author",
        "files": ["tests/test_graph_parser.py"],
        "send_done_to": [10, 12],
    },
    7: {
        "role": "scheduler and executor test author",
        "files": ["tests/test_scheduler_executor.py"],
        "send_done_to": [10, 12],
    },
    8: {
        "role": "documentation and example author",
        "files": ["README.md", "examples/build.json"],
        "send_done_to": [11, 12],
    },
    9: {
        "role": "implementation reviewer",
        "wait_for_ranks": [1, 2, 3, 4],
        "review_files": [
            "minidag/graph.py",
            "minidag/parser.py",
            "minidag/scheduler.py",
            "minidag/executor.py",
        ],
        "send_done_to": [11, 12],
    },
    10: {
        "role": "test and CLI reviewer",
        "wait_for_ranks": [5, 6, 7],
        "review_files": [
            "minidag/cli.py",
            "tests/test_graph_parser.py",
            "tests/test_scheduler_executor.py",
        ],
        "send_done_to": [11, 12],
    },
    11: {
        "role": "reliability reviewer",
        "wait_for_ranks": [8, 9, 10],
        "review_files": ["README.md", "minidag"],
        "send_done_to": [12],
    },
    12: {
        "role": "integrator",
        "wait_for_ranks": list(range(1, 12)),
        "duties": [
            "run all tests",
            "repair only integration defects",
            "run the example",
            "send a machine-readable final report to rank 0",
        ],
    },
}


def prepare(db: Path, workspace: Path, manifest_path: Path) -> dict[str, Any]:
    if db.exists():
        db.unlink()
    for suffix in ("-shm", "-wal"):
        candidate = Path(f"{db}{suffix}")
        if candidate.exists():
            candidate.unlink()
    if workspace.exists():
        shutil.rmtree(workspace)
    (workspace / "minidag").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "examples").mkdir()
    (workspace / "minidag" / "__init__.py").write_text(
        '"""Deterministic dependency graph execution."""\n',
        encoding="utf-8",
    )
    (workspace / "pyproject.toml").write_text(
        """[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "minidag"
version = "0.1.0"
requires-python = ">=3.11"

[project.scripts]
minidag = "minidag.cli:main"
""",
        encoding="utf-8",
    )
    Runtime.initialize(
        db,
        size=SIZE,
        session_id=SESSION,
        context_budget=24_000,
        mailbox_bytes=4 * 1024 * 1024,
        inline_token_limit=4_000,
        heartbeat_ttl=1_800,
    )
    coordinator = Runtime.attach(db, SESSION, 0, heartbeat_ttl=1_800)
    for rank, task in TASKS.items():
        coordinator.send(
            {
                "experiment": "software",
                "rank": rank,
                "workspace": str(workspace.resolve()),
                "interface": INTERFACE,
                **task,
            },
            rank,
            tag="TASK",
        )
    coordinator.close()
    manifest = {
        "session": SESSION,
        "db": str(db),
        "workspace": str(workspace),
        "interface": INTERFACE,
        "tasks": TASKS,
        "coordination": {
            "task_tag": "TASK",
            "dependency_tag": "DONE",
            "review_tag": "REVIEW",
            "final_tag": "FINAL",
            "locking": "Every file mutation uses an AgentMPI lease lock named file:<relative-path>.",
        },
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("experiments/results/software.db"))
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("experiments/results/software_artifact"),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("experiments/results/software_manifest.json"),
    )
    args = parser.parse_args()
    manifest = prepare(args.db, args.workspace, args.manifest)
    print(json.dumps({"session": manifest["session"], "tasks": 12}, indent=2))


if __name__ == "__main__":
    main()
