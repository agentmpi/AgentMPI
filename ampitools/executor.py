"""Executors: the process manager, kept out of the protocol.

MPI separates the interface from the process manager, and that separation is why
the same program runs under Slurm, under Hydra, and inside a unit test.  The same
separation lets an AgentMPI harness run against real agents, against a
deterministic function, and against a recorded replay without changing a line,
which is the only way protocol behaviour can be regression-tested at all --- real
agent runs are neither free nor reproducible.

Four executor kinds ship here.  The interesting one is :class:`BrokerExecutor`.

**Why the broker is a pull queue rather than a push.**  A pushed invocation would
require the harness to know how to start an agent, coupling it to a vendor and to
a particular host's concurrency limits.  Pulling lets the population be launched,
scaled and replaced entirely outside the harness --- which is required, because an
agent session's lifetime is controlled by its host, not by the program that wants
its output.

**Why the queue is per rank.**  A rank is a durable role with accumulated state
and an identity that appears in prompts and artifacts.  A worker stealing another
rank's work would destroy the thing the whole design rests on.

**Why `next` blocks server-side.**  A worker in a collective-heavy phase spends
most of its time waiting for peers.  Blocking in the runtime for four minutes
costs one shell call; polling costs one call per poll, and an agent that is
polling is an agent that is burning context on nothing.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ampi.core.payload import Contract, canonical, check_contract
from ampi.errors import err
from ampi.tokens import count_tokens

__all__ = [
    "Task",
    "Executor",
    "FunctionExecutor",
    "ReplayExecutor",
    "BrokerExecutor",
    "RecordingExecutor",
]


@dataclass
class Task:
    """One unit of work handed to an executor."""

    aid: str
    rank: int
    label: str
    prompt: str
    contract: Contract | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aid": self.aid,
            "rank": self.rank,
            "label": self.label,
            "prompt_tokens": count_tokens(self.prompt),
            "contract": self.contract.to_dict() if self.contract else None,
            "meta": self.meta,
        }


class Executor(Protocol):
    """Turn a prompt into an artifact.  The protocol must not depend on how."""

    kind: str

    def invoke(self, task: Task) -> Any: ...


@dataclass
class FunctionExecutor:
    """A deterministic function standing in for an agent.

    Not a simulation of an agent's *quality* --- nothing here pretends to model
    that --- but a faithful stand-in for its *protocol behaviour*, which is what
    the microbenchmarks and the conformance suite need to measure.
    """

    fn: Callable[[Task], Any]
    kind: str = "function"
    latency_s: float = 0.0

    def invoke(self, task: Task) -> Any:
        if self.latency_s:
            time.sleep(self.latency_s)
        return self.fn(task)


@dataclass
class ReplayExecutor:
    """Replay a recorded run.

    An agent run is expensive and not reproducible, so the only way to regression
    test a harness against real agent output is to record it once and replay it.
    A missing key is an error rather than a fallback: silently substituting a
    generated answer for a recorded one would make a replay meaningless.
    """

    recording: dict[str, Any]
    kind: str = "replay"

    @classmethod
    def from_file(cls, path: str | Path) -> ReplayExecutor:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def invoke(self, task: Task) -> Any:
        key = f"{task.rank}/{task.label}"
        if key not in self.recording:
            raise err(
                "AMPI_ERR_ARG",
                f"the recording has no entry for {key}",
                hint="Replay only reproduces a run that was recorded; it cannot invent one.",
            )
        return self.recording[key]


@dataclass
class RecordingExecutor:
    """Wrap an executor and record everything it produces, for later replay."""

    inner: Executor
    path: Path
    kind: str = "recording"
    _log: dict[str, Any] = field(default_factory=dict)

    def invoke(self, task: Task) -> Any:
        out = self.inner.invoke(task)
        self._log[f"{task.rank}/{task.label}"] = out
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._log, indent=2, default=str), encoding="utf-8")
        return out


class BrokerExecutor:
    """Publish invocations for external workers --- LLM agents --- to claim.

    The harness makes every protocol call; the worker reads a prompt file, writes
    a result file, and submits.  That is the whole of the worker's obligation, and
    it is deliberately the whole of it: protocol conformance becomes a property of
    the runtime rather than of a model's memory.
    """

    kind = "broker"

    def __init__(
        self,
        amp: Any,
        *,
        campaign: str,
        work_dir: str | Path,
        timeout_s: float = 3600.0,
        claim_ttl_s: float = 900.0,
    ) -> None:
        self.amp = amp
        self.campaign = campaign
        self.dir = Path(work_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.claim_ttl_s = claim_ttl_s

    # -- harness side --------------------------------------------------------
    def invoke(self, task: Task) -> Any:
        """Publish a task and block until a worker submits a conforming result."""
        aid = task.aid
        prompt_file = self.dir / f"{aid}.prompt.md"
        result_file = self.dir / f"{aid}.result"
        prompt_file.write_text(task.prompt, encoding="utf-8")

        self.amp.device.append(
            "task",
            {
                "rank": task.rank,
                "state": "queued",
                "campaign": self.campaign,
                "run": self.amp.manifest.job_id,
                "aid": aid,
                "label": task.label,
                "prompt_file": str(prompt_file),
                "result_file": str(result_file),
                "contract": task.contract.to_dict() if task.contract else None,
                "meta": task.meta,
                "queued_at": self.amp.device.clock(),
            },
        )
        self.amp.trace("broker.publish", rank=task.rank, label=task.label, aid=aid,
                       prompt_tokens=count_tokens(task.prompt))

        deadline = time.time() + self.timeout_s
        wait = 0.2
        while True:
            rows = self.amp.device.scan("task", {"aid": aid}, limit=1)
            rec = rows[0] if rows else {}
            if rec.get("state") == "done":
                raw = Path(rec["result_file"]).read_text(encoding="utf-8")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
            if rec.get("state") == "abandoned":
                raise err(
                    "AMPI_ERR_OP_FAILED",
                    f"worker for rank {task.rank} gave up on {task.label!r}: "
                    f"{rec.get('reason', 'no reason given')}",
                    aid=aid,
                )
            # Reclaim a claim whose holder disappeared.  An executor's session can
            # end at any moment, and a task stuck in "claimed" forever is the
            # commonest way a harness silently stops making progress.
            if (
                rec.get("state") == "claimed"
                and self.amp.device.clock() - rec.get("claimed_at", 0) > self.claim_ttl_s
            ):
                self.amp.device.update("task", rec["seq"], {"state": "queued", "requeued": True})
                self.amp.trace("broker.requeue", rank=task.rank, aid=aid)
            if time.time() >= deadline:
                raise err(
                    "AMPI_ERR_TIMEOUT",
                    f"no worker completed {task.label!r} for rank {task.rank} "
                    f"within {self.timeout_s:.0f}s",
                    hint="Check that a worker is running for that rank: 'ampi doctor'.",
                    aid=aid,
                    state=rec.get("state", "missing"),
                )
            time.sleep(wait)
            wait = min(2.0, wait * 1.4)

    # -- worker side ----------------------------------------------------------
    @staticmethod
    def next_task(
        amp: Any,
        campaign: str,
        rank: int,
        *,
        timeout: float = 240.0,
        serve: list[int] | None = None,
    ) -> dict[str, Any]:
        """Claim the next task for any rank this executor serves.

        Returns one of three shapes, and the worker prompt branches on exactly
        these three: ``task`` (do it), ``idle`` (nothing within the window; ask
        again), ``exit`` (the job is over).

        ``serve`` is **oversubscription**, and it has an exact MPI analogue:
        ``mpirun -np 100`` on an eight-core machine runs a hundred ranks on eight
        cores.  It works here for the same reason it works there --- a rank is a
        durable role whose state lives outside its executor --- and it is the
        answer when the agent host caps concurrent sessions below the rank count,
        which every host we have used does.

        It works *only* when the protocol is in the harness.  An executor serving
        ten ranks agent-side would have to be inside ten barriers at once and would
        deadlock immediately.  Here the harness thread blocks in the collective and
        the executor blocks on nothing, so one session can serve ten roles in
        sequence within a single phase.
        """
        from ampi.device import In

        serving = sorted({rank, *(serve or [])})
        deadline = time.time() + timeout
        wait = 0.2
        while True:
            rec = amp.device.match(
                "task",
                {"rank": In(serving), "campaign": campaign, "state": "queued",
                 "run": amp.manifest.job_id},
                {"state": "claimed", "claimed_at": amp.device.clock()},
            )
            if rec is not None:
                rank = rec["rank"]
                # Record which executor took the task.  Provenance is not
                # bookkeeping: a scale claim without per-executor evidence is
                # indistinguishable from one process generating everything, and
                # that distinction is the whole difference between a measurement
                # and an assertion.
                worker_id = os.environ.get("AMPI_WORKER_ID", "")
                if worker_id:
                    amp.device.update("task", rec["seq"], {"worker_id": worker_id})
                amp.trace("broker.claim", rank=rank, aid=rec["aid"], label=rec["label"],
                          worker=worker_id or None)
                root = amp.root
                return {
                    "status": "task",
                    "aid": rec["aid"],
                    "rank": rank,
                    "label": rec["label"],
                    "prompt_file": rec["prompt_file"],
                    "result_file": rec["result_file"],
                    "contract": rec.get("contract"),
                    # --expect-rank is in the printed command, not left to the
                    # agent to remember.  Every executor that noticed its absence
                    # added it by hand; the ones that did not, did not have it.  A
                    # guard an agent must think of is a guard that is sometimes
                    # absent, and the whole argument for handing over the exact
                    # command is that it should require recognition, not recall.
                    "submit": (
                        f"ampi worker --campaign {campaign} --rank {rank} "
                        f"--expect-rank {rank} --job-root {root} submit --aid {rec['aid']}"
                    ),
                    "give_up": (
                        f"ampi worker --campaign {campaign} --rank {rank} "
                        f"--expect-rank {rank} --job-root {root} "
                        f"give-up --aid {rec['aid']} --reason 'WHY'"
                    ),
                    "check_size": (
                        f"ampi tokens --file {rec['result_file']} --limit "
                        f"{(rec.get('contract') or {}).get('max_tokens', 0)}"
                        if (rec.get("contract") or {}).get("max_tokens")
                        else ""
                    ),
                }
            if amp.device.read("campaign", campaign) is not None:
                state = amp.device.read("campaign", campaign).value
                if state.get("state") == "closed":
                    return {"status": "exit", "reason": "the campaign is closed"}
            if time.time() >= deadline:
                return {"status": "idle", "waited_s": round(timeout, 1)}
            time.sleep(wait)
            wait = min(2.0, wait * 1.4)

    @staticmethod
    def submit(
        amp: Any, campaign: str, rank: int, aid: str, *, serve: list[int] | None = None
    ) -> dict[str, Any]:
        rows = amp.device.scan("task", {"aid": aid}, limit=1)
        if not rows:
            raise err("AMPI_ERR_ARG", f"no task {aid!r}")
        rec = rows[0]
        if rec["rank"] not in {rank, *(serve or [])}:
            raise err(
                "AMPI_ERR_IDENTITY",
                f"task {aid} belongs to rank {rec['rank']}, not rank {rank}",
                hint="You are working on another rank's task. Re-check AMPI_RANK.",
            )
        path = Path(rec["result_file"])
        if not path.exists():
            raise err(
                "AMPI_ERR_ARG",
                f"the result file {path} does not exist",
                hint="Write your answer to the result_file the task named, then submit.",
            )
        raw = path.read_text(encoding="utf-8")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        contract = Contract.parse(rec.get("contract"))
        violations = check_contract(value, contract, subs={"rank": rec["rank"]})
        if violations:
            amp.trace("broker.reject", rank=rank, aid=aid, violations=violations)
            raise err(
                "AMPI_ERR_TYPE",
                f"your result does not satisfy the task's contract: {violations[0]}",
                hint="Fix the result file and submit again. "
                + (
                    f"Run: ampi tokens --file {path} --limit {contract.max_tokens}"
                    if contract and contract.max_tokens
                    else "The contract is in the task JSON."
                ),
                violations=violations,
            )
        amp.device.update("task", rec["seq"], {"state": "done", "done_at": amp.device.clock(),
                                               "result_tokens": count_tokens(canonical(value))})
        amp.trace("broker.submit", rank=rank, aid=aid, label=rec["label"],
                  tokens=count_tokens(canonical(value)))
        return {"status": "done", "aid": aid, "label": rec["label"]}

    @staticmethod
    def give_up(amp: Any, campaign: str, rank: int, aid: str, reason: str) -> dict[str, Any]:
        rows = amp.device.scan("task", {"aid": aid}, limit=1)
        if not rows:
            raise err("AMPI_ERR_ARG", f"no task {aid!r}")
        amp.device.update("task", rows[0]["seq"], {"state": "abandoned", "reason": reason})
        amp.trace("broker.giveup", rank=rank, aid=aid, reason=reason)
        return {"status": "abandoned", "aid": aid}

    # -- campaign lifecycle -----------------------------------------------------
    def open(self) -> None:
        self.amp.device.cas(
            "campaign", self.campaign, None,
            {"state": "open", "opened_at": self.amp.device.clock()}, writer=-1,
        )

    def close(self) -> dict[str, Any]:
        """Close the campaign so idle workers wind down.

        A compare-and-swap rather than a blind write: two harness threads closing
        concurrently, or a close racing a re-open, must not silently lose one of
        the decisions.
        """
        cell = self.amp.device.read("campaign", self.campaign)
        ok, current = self.amp.device.cas(
            "campaign", self.campaign, cell.version if cell else 0,
            {"state": "closed", "closed_at": self.amp.device.clock()}, writer=-1,
        )
        if not ok:
            raise err(
                "AMPI_ERR_CONFLICT",
                f"campaign {self.campaign!r} changed state concurrently",
                current=current.value,
            )
        return {"campaign": self.campaign, "state": "closed"}

    def stats(self) -> dict[str, Any]:
        rows = self.amp.device.scan("task", {"campaign": self.campaign})
        by_state: dict[str, int] = {}
        for r in rows:
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        done = [r for r in rows if r["state"] == "done"]
        return {
            "campaign": self.campaign,
            "tasks": len(rows),
            "by_state": by_state,
            "requeued": sum(1 for r in rows if r.get("requeued")),
            "result_tokens": sum(r.get("result_tokens", 0) for r in done),
            "ranks": sorted({r["rank"] for r in rows}),
            "executors": sorted({r["worker_id"] for r in rows if r.get("worker_id")}),
            "tasks_with_executor_id": sum(1 for r in rows if r.get("worker_id")),
        }


def new_aid() -> str:
    return uuid.uuid4().hex[:10]
