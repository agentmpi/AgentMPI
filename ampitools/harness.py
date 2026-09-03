"""The SPMD harness driver: protocol in the harness, not in the prompt.

A harness author writes one function, ``rank_main(comm, rank)``, and the driver
runs it once per rank.  Every AgentMPI call is made by that function --- trusted
host-side code --- and the agent is invoked as a kernel that transforms artifacts.

The alternative is agent-side: the executor issues protocol operations itself.
Both are supported, because agent-side is sometimes the only option and because
the difference between them is measurable.  But the recommendation is not
balanced, and the reason is worth stating plainly.

    In the agent-side form, protocol conformance is a property of model
    behaviour.  A rank that forgets to enter a barrier does not merely produce a
    worse answer; it prevents the population from making progress.  Confining the
    protocol to host-side code makes conformance a property of the runtime.

That claim is testable, and ``experiments/e4_placement`` tests it: the same task,
the same executors, the same collectives, differing only in who issues them.

The driver runs ranks as threads by default because that is what makes a harness
debuggable and a microbenchmark meaningful.  It runs them as processes when asked,
which is what a real deployment looks like, and the conformance suite checks that
the two agree.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ampi.constants import DEFAULT_CTX_BUDGET
from ampi.errors import AmpiError
from ampi.runtime import Ampi

__all__ = ["Harness", "RankResult", "run"]

RankMain = Callable[["Ampi", int], Any]


@dataclass
class RankResult:
    rank: int
    ok: bool
    value: Any = None
    error: str = ""
    error_class: str = ""
    seconds: float = 0.0
    context_used: int = 0
    traceback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in ("", None)}


@dataclass
class Harness:
    """Run ``rank_main`` once per rank against one job."""

    root: str
    size: int
    device: str = "sqlite"
    ctx_budget: int = DEFAULT_CTX_BUDGET
    unexpected_budget: int | None = None
    eager_threshold: int | None = None
    roles: dict[int, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    force: bool = False
    _job: Ampi | None = None

    def create(self) -> Ampi:
        kw: dict[str, Any] = {
            "device": self.device,
            "ctx_budget": self.ctx_budget,
            "roles": self.roles,
            "meta": self.meta,
            "force": self.force,
            "allow_volatile": True,
        }
        if self.unexpected_budget is not None:
            kw["unexpected_budget"] = self.unexpected_budget
        if self.eager_threshold is not None:
            kw["eager_threshold"] = self.eager_threshold
        self._job = Ampi.create(self.root, self.size, **kw)  # type: ignore[assignment]
        return self._job  # type: ignore[return-value]

    def attach(self, rank: int) -> Ampi:
        return Ampi(self.root, rank=rank, allow_volatile=True)

    def run(
        self,
        rank_main: RankMain,
        *,
        ranks: list[int] | None = None,
        timeout: float = 3600.0,
        finalize: bool = True,
    ) -> list[RankResult]:
        """Execute ``rank_main`` on every rank concurrently.

        A rank's exception is caught and recorded rather than propagated, because
        the rule a harness author most easily violates is the expensive one: *a
        local failure must not remove a rank from a collective*.  If an exception
        escaped a rank's main function, that rank would never reach the collective
        its peers are already blocked inside, and one recoverable local failure
        would become a whole-population hang.  Escalation must be a deliberate
        decision by a barrier policy or a supervisor, never an accident of
        exception propagation.
        """
        if self._job is None:
            self.create()
        targets = ranks if ranks is not None else list(range(self.size))

        def one(rank: int) -> RankResult:
            started = time.time()
            amp = self.attach(rank)
            try:
                amp.init(role=self.roles.get(rank, ""))
                value = rank_main(amp, rank)
                if finalize:
                    amp.finalize()
                return RankResult(
                    rank=rank, ok=True, value=value,
                    seconds=time.time() - started, context_used=amp.ledger().used,
                )
            except AmpiError as exc:
                amp.trace("rank.error", rank=rank, error=exc.cls_name, message=exc.message)
                return RankResult(
                    rank=rank, ok=False, error=exc.message, error_class=exc.cls_name,
                    seconds=time.time() - started, context_used=amp.ledger().used,
                    traceback=traceback.format_exc(limit=6),
                )
            except Exception as exc:  # noqa: BLE001 - see the docstring
                amp.trace("rank.error", rank=rank, error=type(exc).__name__, message=str(exc))
                return RankResult(
                    rank=rank, ok=False, error=str(exc), error_class=type(exc).__name__,
                    seconds=time.time() - started, context_used=amp.ledger().used,
                    traceback=traceback.format_exc(limit=6),
                )
            finally:
                amp.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(targets))) as pool:
            futures = {pool.submit(one, r): r for r in targets}
            out: list[RankResult] = []
            for fut in concurrent.futures.as_completed(futures, timeout=timeout):
                out.append(fut.result())
        return sorted(out, key=lambda r: r.rank)

    # -- reporting -------------------------------------------------------------
    def report(self, results: list[RankResult]) -> dict[str, Any]:
        job = self._job or self.attach(0)
        from .doctor import diagnose

        events = job.events()
        by_kind: dict[str, int] = {}
        for e in events:
            by_kind[e["kind"]] = by_kind.get(e["kind"], 0) + 1
        return {
            "job": job.manifest.job_id,
            "size": self.size,
            "device": self.device,
            "ranks": [r.to_dict() for r in results],
            "succeeded": sum(1 for r in results if r.ok),
            "failed": sum(1 for r in results if not r.ok),
            "wall_s": round(max((r.seconds for r in results), default=0.0), 3),
            "context_total": sum(r.context_used for r in results),
            "context_peak": max((r.context_used for r in results), default=0),
            "events": by_kind,
            "diagnosis": diagnose(job),
        }

    def save(self, results: list[RankResult], path: str | Path) -> Path:
        """Write the report, and export the trace beside it.

        The trace goes with the report unconditionally.  A run that was not traced
        is a run whose claims cannot be checked, and these runs are expensive
        enough that nobody will repeat one to settle an argument.
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        report = self.report(results)
        p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        job = self._job or self.attach(0)
        trace_path = p.with_suffix(".trace.jsonl")
        with open(trace_path, "w", encoding="utf-8") as fh:
            for e in job.events():
                fh.write(json.dumps(e, default=str) + "\n")
        report["trace"] = str(trace_path)
        p.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        return p


def run(
    root: str,
    size: int,
    rank_main: RankMain,
    **kw: Any,
) -> tuple[Harness, list[RankResult]]:
    """Convenience: create a job, run every rank, return the harness and results."""
    h = Harness(root=root, size=size, **kw)
    h.create()
    return h, h.run(rank_main)
