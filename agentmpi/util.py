from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex


def estimate_tokens(obj: Any) -> int:
    """Cheap, portable token estimator. 1 token ≈ 4 characters of JSON."""
    if obj is None:
        return 0
    if isinstance(obj, str):
        return max(1, len(obj) // 4)
    raw = json.dumps(obj, default=str, ensure_ascii=False)
    return max(1, len(raw) // 4)


def nbytes_of(obj: Any) -> int:
    if obj is None:
        return 0
    if isinstance(obj, (bytes, bytearray)):
        return len(obj)
    return len(json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8"))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_text(path, json.dumps(obj, indent=2, default=str, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def append_jsonl(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, default=str, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def mkdir_lock(path: Path, timeout_s: float | None = None) -> bool:
    """Acquire an exclusive lock by creating a directory (atomic on POSIX)."""
    deadline = None if timeout_s is None else time.time() + timeout_s
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            path.mkdir()
            return True
        except FileExistsError:
            if deadline is not None and time.time() >= deadline:
                return False
            time.sleep(0.01)


def release_dir_lock(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass
