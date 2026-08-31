#!/usr/bin/env python3
"""Coupled collaborative-development harness.

Unlike translation, this job has data dependences: a design document in a
shared RMA window, exclusive locks on source files, a barrier at integration,
and a gather of artifacts. It is the agent analog of an MPI halo-exchange
plus critical-section code — the pattern that shows up when agents jointly
build a software system.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.common import run_spmd, write_result

ROLES = {
    0: "architect",
    1: "store",
    2: "cli",
    3: "tests",
    4: "docs",
    5: "reviewer",
}

STORE_PY = '''\
"""In-memory key-value store with compare-and-swap."""

from __future__ import annotations


class KVStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._rev: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def put(self, key: str, value: str) -> int:
        self._data[key] = value
        self._rev[key] = self._rev.get(key, 0) + 1
        return self._rev[key]

    def cas(self, key: str, expected_rev: int, value: str) -> bool:
        if self._rev.get(key, 0) != expected_rev:
            return False
        self.put(key, value)
        return True

    def delete(self, key: str) -> bool:
        if key not in self._data:
            return False
        del self._data[key]
        self._rev.pop(key, None)
        return True

    def keys(self) -> list[str]:
        return sorted(self._data)
'''

CLI_PY = '''\
from __future__ import annotations

import argparse
from kvstore.store import KVStore

_STORE = KVStore()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kv")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get"); g.add_argument("key")
    u = sub.add_parser("put"); u.add_argument("key"); u.add_argument("value")
    d = sub.add_parser("delete"); d.add_argument("key")
    sub.add_parser("keys")
    args = p.parse_args(argv)
    if args.cmd == "get":
        print(_STORE.get(args.key) or "")
    elif args.cmd == "put":
        print(_STORE.put(args.key, args.value))
    elif args.cmd == "delete":
        print("ok" if _STORE.delete(args.key) else "missing")
    elif args.cmd == "keys":
        print("\\n".join(_STORE.keys()))
    return 0
'''

TEST_PY = '''\
from kvstore.store import KVStore


def test_put_get():
    s = KVStore()
    rev = s.put("a", "1")
    assert s.get("a") == "1"
    assert rev == 1


def test_cas():
    s = KVStore()
    s.put("a", "1")
    assert s.cas("a", 1, "2")
    assert not s.cas("a", 1, "3")
    assert s.get("a") == "2"


def test_delete():
    s = KVStore()
    s.put("a", "1")
    assert s.delete("a")
    assert s.get("a") is None
'''

DOCS = """# kvstore\n\nA tiny compare-and-swap key-value library produced by an AgentMPI collab job.\n"""

INIT_PY = "from kvstore.store import KVStore\n"

FILES = {
    "store": ("kvstore/store.py", STORE_PY),
    "cli": ("kvstore/cli.py", CLI_PY),
    "tests": ("tests/test_kvstore.py", TEST_PY),
    "docs": ("README.md", DOCS),
    "architect": ("kvstore/__init__.py", INIT_PY),
}


def fn(comm):
    role = ROLES.get(comm.rank, "observer")
    comm.win_create("design", {})
    comm.barrier(timeout_s=30)

    if role == "architect":
        comm.win_lock("design")
        comm.put(
            "design",
            {
                "package": "kvstore",
                "modules": ["store", "cli", "tests", "docs"],
                "invariants": ["cas is atomic", "keys() is sorted", "delete is idempotent-false"],
            },
        )
        comm.win_unlock("design")
        comm.context_put({"role": role, "design": "published"})
    comm.barrier(timeout_s=30)
    design = comm.get("design")

    produced = {}
    if role in FILES:
        rel, content = FILES[role]
        comm.win_ensure(rel, None)
        comm.win_lock(rel)
        try:
            comm.put(rel, {"path": rel, "content": content, "author": comm.rank})
            produced[rel] = len(content)
        finally:
            comm.win_unlock(rel)

    comm.barrier(timeout_s=30)
    catalog = comm.allgather({"rank": comm.rank, "role": role, "files": produced})

    review = None
    if role == "reviewer":
        notes = []
        for rel, _ in FILES.values():
            art = comm.get(rel)
            if not art:
                notes.append(f"missing {rel}")
                continue
            if rel.endswith(".py"):
                try:
                    ast.parse(art["content"])
                    notes.append(f"syntax-ok {rel}")
                except SyntaxError as exc:
                    notes.append(f"syntax-error {rel}: {exc}")
            else:
                notes.append(f"present {rel}")
        review = {"notes": notes, "design": design}
        comm.context_put({"role": role, "review": notes})

    reviews = comm.gather(review, root=0, timeout_s=30)
    if comm.rank == 0:
        artifacts = {}
        for rel, _ in FILES.values():
            art = comm.get(rel)
            if art:
                artifacts[rel] = art
        return {"design": design, "catalog": catalog, "reviews": reviews, "artifacts": artifacts}
    return {"role": role, "produced": produced}


def materialize(artifacts: dict, dest: Path) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for art in artifacts.values():
        path = dest / art["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(art["content"])
        written.append(str(path))
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=6)
    parser.add_argument("--out", default="experiments/results/collab.json")
    parser.add_argument("--workdir", default="experiments/results/collab_pkg")
    args = parser.parse_args()
    home = ROOT / "experiments/results/.ampi" / f"collab-{args.n}"
    results, summary = run_spmd(home, args.n, fn)
    artifacts = results[0]["artifacts"]
    written = materialize(artifacts, Path(args.workdir))
    # Execute the produced tests in-process.
    testdir = Path(args.workdir)
    sys.path.insert(0, str(testdir))
    produced_tests = importlib.import_module("tests.test_kvstore")

    produced_tests.test_put_get()
    produced_tests.test_cas()
    produced_tests.test_delete()
    payload = {
        "experiment": "collab",
        "n": args.n,
        "roles": ROLES,
        "files": written,
        "review": [r for r in results[0]["reviews"] if r],
        "tests_passed": True,
        **summary,
    }
    write_result(Path(args.out), payload)
    print(f"collab n={args.n} tests_passed=True files={len(written)} sends={summary['sends']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
