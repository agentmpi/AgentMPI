"""Executors: how a rank is embodied.

MPI separates the *interface* from the *process manager*.  ``mpiexec`` and the
PMI interface launch processes, wire up their addresses, and get out of the way;
the MPI library itself has no opinion about how a rank came to exist.  This
separation is why the same MPI program runs under Slurm, under Hydra, and inside
a test harness, and it is worth preserving exactly.

AgentMPI's equivalent is the executor.  A rank's protocol behaviour — which
collectives it enters, in what order, with what payloads — is written once by the
harness author; *what actually produces the artifact* is chosen at launch:

:class:`FunctionExecutor`
    A plain Python callable.  Used by the test suite and by the microbenchmarks,
    because it makes runs deterministic and free, which is the only way to test
    a protocol.
:class:`ReplayExecutor`
    Reads outputs from a previous run's artifact store, keyed by prompt digest.
    Turns a recorded agent run into a deterministic one, so protocol bugs can be
    debugged without paying for inference and without the nondeterminism that
    makes agent bugs irreproducible.
:class:`SimulatedExecutor`
    Synthesises outputs with calibrated latency and token volumes drawn from a
    measured distribution.  Used for scaling studies beyond the population size
    that can be run for real — the same role LogGOPSim plays for MPI.
:class:`BrokerExecutor`
    Publishes the invocation to the fabric and waits for an external worker to
    complete it.  This is the executor that makes *real* agents ranks: the
    process manager is whatever launches the workers, and the worker's only
    obligation is to run ``ampi worker`` (see :mod:`agentmpi.cli`).  Cursor
    subagents, a headless CLI agent, or a human at a terminal are all valid
    workers, and the harness cannot tell them apart.

The broker deserves a note on why it is designed as a *pull* queue rather than a
push.  A pushed invocation requires the harness to know how to start an agent,
which couples it to a vendor.  A pulled invocation lets the population be
launched, scaled, and re-incarnated entirely outside the harness — which is
required, because an agent session's lifetime is controlled by the environment
that hosts it, not by the program that wants its output.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import tokens as _tokens
from .errors import AmpiTimeout, AmpiUsageError
from .fabric import Fabric
from .rank import AgentResult
from .schema import Contract


class Executor:
    """Base class.  Callable with ``(prompt, **meta) -> AgentResult``."""

    name = "base"

    def __call__(self, prompt: str, **meta: Any) -> AgentResult:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass
class FunctionExecutor(Executor):
    """Wraps a deterministic Python function as a rank's kernel."""

    fn: Callable[..., Any]
    name: str = "function"
    #: Artificial latency, so that tests of straggler handling and hedging have
    #: something to observe.
    latency_s: float = 0.0

    def __call__(self, prompt: str, **meta: Any) -> AgentResult:
        t0 = time.time()
        if self.latency_s:
            time.sleep(self.latency_s)
        out = self.fn(prompt, **meta)
        return AgentResult(
            output=out,
            prompt_tokens=_tokens.count(prompt),
            output_tokens=_tokens.count(out),
            latency_s=time.time() - t0,
            label=str(meta.get("label", "")),
        )


@dataclass
class SimulatedExecutor(Executor):
    """Synthesises output with calibrated latency and volume.

    Latency is drawn log-normally, which fits measured agent-invocation latency
    far better than a normal or exponential: the distribution is right-skewed
    with a heavy tail, and it is that tail — not the mean — that determines how
    long a barrier waits.  ``fail_rate`` and ``stall_rate`` inject the failure
    classes so that fault-tolerance paths are exercised.
    """

    median_latency_s: float = 30.0
    sigma: float = 0.7
    tokens_per_s: float = 40.0
    fail_rate: float = 0.0
    stall_rate: float = 0.0
    stall_multiplier: float = 8.0
    output_tokens: int = 800
    seed: int | None = None
    name: str = "simulated"
    template: Callable[[str, dict[str, Any]], Any] | None = None
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def __call__(self, prompt: str, **meta: Any) -> AgentResult:
        t0 = time.time()
        n_out = max(1, int(self._rng.gauss(self.output_tokens, self.output_tokens * 0.25)))
        lat = self._rng.lognormvariate(0.0, self.sigma) * self.median_latency_s + n_out / self.tokens_per_s
        if self._rng.random() < self.stall_rate:
            lat *= self.stall_multiplier
        if self._rng.random() < self.fail_rate:
            raise AmpiTimeout("simulated agent failure", rank=meta.get("rank"), label=meta.get("label"))
        # Wall time is scaled down by default so that a scaling study completes;
        # the *modelled* latency is what the cost model consumes.
        time.sleep(min(0.05, lat / 1000.0))
        out = self.template(prompt, meta) if self.template else _synthetic_text(n_out, self._rng)
        return AgentResult(
            output=out,
            prompt_tokens=_tokens.count(prompt),
            output_tokens=n_out,
            latency_s=lat,
            label=str(meta.get("label", "")),
        )


def _synthetic_text(n_tokens: int, rng: random.Random) -> str:
    words = ["module", "interface", "invariant", "contract", "boundary", "glossary", "chapter", "term", "review"]
    return " ".join(rng.choice(words) for _ in range(max(1, int(n_tokens / 0.82))))


@dataclass
class ReplayExecutor(Executor):
    """Serves recorded outputs from a previous run.

    Keyed by ``sha256(prompt)``, so replay is exact when the harness is
    deterministic and *detectably divergent* when it is not: a cache miss means
    the harness asked a different question than it did last time, which is
    precisely the signal a harness author wants when debugging a change.
    """

    fabric: Fabric
    strict: bool = True
    fallback: Executor | None = None
    name: str = "replay"
    _index: dict[str, str] = field(default_factory=dict)
    n_hits: int = 0
    n_misses: int = 0

    def __post_init__(self) -> None:
        rows = self.fabric.query(
            "SELECT prompt_digest, result_digest FROM agent_calls WHERE state='done' AND result_digest IS NOT NULL"
        )
        for r in rows:
            self._index.setdefault(r["prompt_digest"], r["result_digest"])

    def __call__(self, prompt: str, **meta: Any) -> AgentResult:
        digest = self.fabric.blobs.put(prompt).digest
        target = self._index.get(digest)
        if target is None:
            self.n_misses += 1
            if self.fallback is not None:
                return self.fallback(prompt, **meta)
            if self.strict:
                raise AmpiUsageError(
                    "replay miss: this prompt was not recorded",
                    prompt_digest=digest[:12],
                    label=meta.get("label"),
                    hint="the harness diverged from the recorded run",
                )
            return AgentResult(output="", prompt_tokens=_tokens.count(prompt), output_tokens=0, latency_s=0.0)
        self.n_hits += 1
        payload = self.fabric.blobs.get(target, "json")
        return AgentResult(
            output=payload,
            prompt_tokens=_tokens.count(prompt),
            output_tokens=_tokens.count(payload),
            latency_s=0.0,
            label=str(meta.get("label", "")),
        )


@dataclass
class BrokerExecutor(Executor):
    """Publishes invocations to the fabric for external workers to serve.

    The lifecycle of one invocation:

    1. The harness calls ``comm.agent(prompt)`` on rank *r*.
    2. This executor writes the prompt to the artifact store and inserts an
       ``agent_calls`` row in state ``pending``, then blocks.
    3. A worker for rank *r* — launched by whatever process manager the operator
       chose — claims the row (``ampi worker next``), producing state
       ``claimed``.
    4. The worker does the work and submits the result (``ampi worker done``),
       producing state ``done``.
    5. This executor observes ``done``, reads the result, and returns.

    Two properties make this robust to the realities of agent workers.  Claims
    carry a **deadline**: an invocation claimed by a worker that then disappears
    returns to ``pending`` and can be re-claimed, so a lost agent session costs
    one re-execution rather than a hung job.  And the queue is **per rank**, so
    a worker cannot steal work belonging to another rank's role — the rank's
    identity, and the accumulated context that goes with it, is preserved across
    the whole run.
    """

    fabric: Fabric
    poll_s: float = 0.5
    default_timeout: float = 3600.0
    claim_timeout_s: float = 1800.0
    name: str = "broker"
    #: Directory in which prompt/result files are exposed for worker convenience.
    spool: Path | None = None

    def __post_init__(self) -> None:
        if self.spool is None:
            self.spool = self.fabric.root / "spool"
        self.spool.mkdir(parents=True, exist_ok=True)

    def __call__(self, prompt: str, **meta: Any) -> AgentResult:
        rank = int(meta.get("rank", -1))
        label = str(meta.get("label", "agent"))
        contract: Contract | None = meta.get("contract")
        timeout = float(meta.get("timeout") or self.default_timeout)
        blob = self.fabric.blobs.put(prompt)
        now = time.time()
        with self.fabric.write() as cur:
            cur.execute(
                "INSERT INTO agent_calls(rank, ctx, kind, label, state, prompt_digest, contract, prompt_tokens,"
                " created_at, incarnation, attempt, meta) VALUES(?,?,?,?,'pending',?,?,?,?,?,?,?)",
                (
                    rank,
                    int(meta.get("ctx", 0)),
                    "task",
                    label,
                    blob.digest,
                    json.dumps(contract.to_json()) if contract else None,
                    blob.tokens,
                    now,
                    int(meta.get("incarnation", 0)),
                    int(meta.get("attempt", 1)),
                    json.dumps({k: v for k, v in meta.items() if k in ("max_tokens", "phase", "unit")}, default=str),
                ),
            )
            aid = int(cur.lastrowid or 0)
            self.fabric.emit(
                "broker.enqueue",
                rank=rank,
                ctx=int(meta.get("ctx", 0)),
                cur=cur,
                aid=aid,
                label=label,
                prompt_tokens=blob.tokens,
            )
        (self.spool / f"call-{aid}.prompt.md").write_text(prompt, encoding="utf-8")

        deadline = time.time() + timeout
        while True:
            row = self.fabric.query_one("SELECT * FROM agent_calls WHERE aid=?", (aid,))
            if row is None:
                raise AmpiUsageError("agent call vanished", aid=aid)
            if row["state"] == "done":
                payload = self.fabric.blobs.get(row["result_digest"], "json")
                return AgentResult(
                    output=payload,
                    prompt_tokens=int(row["prompt_tokens"]),
                    output_tokens=int(row["result_tokens"]),
                    latency_s=float(row["finished_at"] or time.time()) - float(row["created_at"]),
                    call_id=aid,
                    label=label,
                )
            if row["state"] == "failed":
                raise AmpiTimeout("worker reported failure", aid=aid, error=row["error"], rank=rank)
            if row["state"] == "claimed":
                claimed = float(row["claimed_at"] or now)
                if time.time() - claimed > self.claim_timeout_s:
                    # The claiming worker is presumed gone; return the call to
                    # the queue so a re-incarnated worker can take it.
                    with self.fabric.write() as cur:
                        cur.execute(
                            "UPDATE agent_calls SET state='pending', claimed_at=NULL, attempt=attempt+1 WHERE aid=? AND state='claimed'",
                            (aid,),
                        )
                        self.fabric.emit("broker.reclaim", rank=rank, cur=cur, aid=aid, held_s=round(time.time() - claimed, 1))
            if time.time() > deadline:
                with self.fabric.write() as cur:
                    cur.execute("UPDATE agent_calls SET state='expired' WHERE aid=?", (aid,))
                    self.fabric.emit("broker.expire", rank=rank, cur=cur, aid=aid, waited_s=round(timeout, 1))
                raise AmpiTimeout("no worker served the invocation", aid=aid, rank=rank, waited_s=timeout)
            time.sleep(self.poll_s)


# --------------------------------------------------------------- worker side


def claim_next(fabric: Fabric, rank: int, *, lease_s: float = 1800.0) -> dict[str, Any] | None:
    """Atomically claim the oldest pending invocation for ``rank``."""
    with fabric.write() as cur:
        row = cur.execute(
            "SELECT * FROM agent_calls WHERE rank=? AND state='pending' ORDER BY aid ASC LIMIT 1", (rank,)
        ).fetchone()
        if row is None:
            return None
        cur.execute("UPDATE agent_calls SET state='claimed', claimed_at=? WHERE aid=?", (time.time(), row["aid"]))
        fabric.emit("broker.claim", rank=rank, cur=cur, aid=int(row["aid"]), label=row["label"])
        return dict(row)


def complete(fabric: Fabric, aid: int, payload: Any) -> None:
    blob = fabric.blobs.put(payload)
    with fabric.write() as cur:
        cur.execute(
            "UPDATE agent_calls SET state='done', result_digest=?, result_tokens=?, finished_at=? WHERE aid=?",
            (blob.digest, blob.tokens, time.time(), aid),
        )
        row = cur.execute("SELECT rank, label FROM agent_calls WHERE aid=?", (aid,)).fetchone()
        fabric.emit(
            "broker.complete",
            rank=int(row["rank"]) if row else None,
            cur=cur,
            aid=aid,
            label=row["label"] if row else "",
            result_tokens=blob.tokens,
        )


def fail(fabric: Fabric, aid: int, error: str) -> None:
    with fabric.write() as cur:
        cur.execute("UPDATE agent_calls SET state='failed', error=?, finished_at=? WHERE aid=?", (error, time.time(), aid))
        row = cur.execute("SELECT rank FROM agent_calls WHERE aid=?", (aid,)).fetchone()
        fabric.emit("broker.fail", rank=int(row["rank"]) if row else None, cur=cur, aid=aid, error=error[:500])


def pending_summary(fabric: Fabric) -> dict[str, Any]:
    rows = fabric.query(
        "SELECT state, COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS toks FROM agent_calls GROUP BY state"
    )
    by_state = {r["state"]: {"n": int(r["n"]), "prompt_tokens": int(r["toks"])} for r in rows}
    waiting = fabric.query(
        "SELECT rank, COUNT(*) AS n FROM agent_calls WHERE state='pending' GROUP BY rank ORDER BY rank"
    )
    return {
        "by_state": by_state,
        "pending_by_rank": {int(r["rank"]): int(r["n"]) for r in waiting},
        "n_pending": by_state.get("pending", {}).get("n", 0),
        "n_claimed": by_state.get("claimed", {}).get("n", 0),
        "n_done": by_state.get("done", {}).get("n", 0),
    }
