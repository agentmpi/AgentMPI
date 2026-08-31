"""Shared experiment helpers: SPMD thread/process launch and result I/O."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from agentmpi.comm import Communicator
from agentmpi.types import Lifecycle
from agentmpi.util import atomic_write_json


def run_spmd(
    home: Path,
    n: int,
    fn: Callable[[Communicator], Any],
    *,
    failure_timeout_s: float = 8.0,
    context_budget: int = 200_000,
) -> tuple[list[Any], dict[str, Any]]:
    home = Path(home)
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)
    Communicator(home, rank=0, size=n, bootstrap=True)
    t0 = time.perf_counter()

    def worker(rank: int) -> Any:
        comm = Communicator(
            home,
            rank=rank,
            size=n,
            failure_timeout_s=failure_timeout_s,
            context_budget=context_budget,
            poll_s=0.005,
        )
        comm.heartbeat(Lifecycle.ACTIVE)
        try:
            return fn(comm)
        finally:
            comm.finalize()

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(worker, range(n)))
    elapsed = time.perf_counter() - t0
    events_path = home / "comms" / "world" / "logs" / "events.jsonl"
    events = []
    if events_path.exists():
        events = [json.loads(line) for line in events_path.read_text().splitlines() if line]
    summary = {
        "n": n,
        "home": str(home),
        "elapsed_s": elapsed,
        "events": len(events),
        "sends": sum(1 for e in events if e.get("event") == "send"),
        "recvs": sum(1 for e in events if e.get("event") == "recv"),
        "eager": sum(1 for e in events if e.get("event") == "send" and e.get("eager")),
        "rendezvous": sum(1 for e in events if e.get("event") == "send" and e.get("eager") is False),
        "failures": [e for e in events if e.get("event") == "failure"],
        "bytes": sum(int(e.get("bytes") or 0) for e in events if e.get("event") == "send"),
    }
    return results, summary


def write_result(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, obj)
