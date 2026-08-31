"""Test harness: run p AgentMPI ranks concurrently in one process.

Each rank gets its own device handle, because a SQLite connection may not be
shared across threads and because we want the tests to exercise the same
multi-process contention path that real agent ranks do.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

import pytest

from ampi.core.runtime import Runtime
from ampi.device import open_device


class Job:
    def __init__(self, job_dir: str, world_size: int, ctx_limit: int = 200_000) -> None:
        self.job_dir = job_dir
        self.job_id = os.path.basename(job_dir)
        self.world_size = world_size
        os.makedirs(job_dir, exist_ok=True)
        device = open_device(os.path.join(job_dir, "job.db"))
        Runtime.create_job(device, self.job_id, world_size, ctx_limit=ctx_limit)
        device.close()

    def runtime(self, rank: int) -> Runtime:
        device = open_device(os.path.join(self.job_dir, "job.db"))
        rt = Runtime(device, self.job_id, rank, poll_interval=0.01, failure_timeout=1e9)
        return rt

    def run_ranks(
        self,
        body: Callable[[Runtime, int], Any],
        ranks: list[int] | None = None,
        timeout: float = 90.0,
    ) -> dict[int, Any]:
        """Run ``body(runtime, rank)`` on every rank concurrently."""
        ranks = list(range(self.world_size)) if ranks is None else ranks
        results: dict[int, Any] = {}
        errors: dict[int, BaseException] = {}

        def worker(rank: int) -> None:
            rt = self.runtime(rank)
            try:
                rt.init(rank)
                results[rank] = body(rt, rank)
            except BaseException as exc:  # noqa: BLE001 - reported to the test
                errors[rank] = exc
            finally:
                rt.device.close()

        threads = [threading.Thread(target=worker, args=(r,), daemon=True) for r in ranks]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout)
        alive = [t for t in threads if t.is_alive()]
        if errors:
            rank, exc = next(iter(errors.items()))
            raise AssertionError(f"rank {rank} failed: {type(exc).__name__}: {exc}") from exc
        if alive:
            raise AssertionError(f"{len(alive)} rank(s) did not finish within {timeout}s")
        return results


@pytest.fixture
def make_job(tmp_path):
    def _make(world_size: int, ctx_limit: int = 200_000) -> Job:
        return Job(str(tmp_path / f"job{world_size}"), world_size, ctx_limit)

    return _make
