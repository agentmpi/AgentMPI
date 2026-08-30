"""Rank runtime: lifecycle, leases, context budget, and agent invocation.

A *rank* in AgentMPI is a durable role, not a process.  The distinction is
forced on us: in MPI, rank 7 is a process that exists for the lifetime of the
job, and if it dies the job dies.  An agent rank's physical embodiment is an
LLM session that may be killed by a timeout, may exhaust its context, or may
simply decide it is finished; a harness that identified the rank with the
session would have to renumber its communicator on every such event.

So a rank is a row in the fabric, and an *incarnation* is one agent session
bound to it.  The mapping is one-to-many over time and exactly-one at any
instant, enforced by a lease.  A rank whose lease expires is a candidate for
FAIL_STOP; re-incarnating it means starting a fresh agent session, bumping the
incarnation counter, and letting it drain the same durable mailbox.  This is
the virtual-actor discipline from Orleans, and it is what makes MPI's static
rank space viable on top of ephemeral agents.

Context budget
--------------
The second job of this module is to make the context window a first-class,
accounted resource.  MPI does not need this: a receive buffer is allocated by
the application and its size is known.  An agent's context is a shared,
overcommitted, silently-lossy buffer, and every framework that ignores it
eventually hits the same wall — the agent's history grows until quality
collapses or the provider refuses the request.

The rank therefore tracks a **working set**: the artifacts currently admitted
into the agent's context, keyed by digest so that admitting the same artifact
twice is free.  Admission is explicit, eviction is explicit, and the budget is
checked on admission.  Failing loudly at admission time (``ERR_TRUNCATE``) is
strictly better than the alternative, because the harness can then choose a
narrower :class:`~agentmpi.schema.View` and retry — the same recovery an MPI
program performs when a probe reports a message larger than its buffer.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import tokens as _tokens
from .constants import (
    DEFAULT_CONTEXT_BUDGET,
    DEFAULT_EAGER_LIMIT,
    DEFAULT_LEASE_SECONDS,
    RankState,
)
from .errors import AmpiContextOverflow, AmpiTimeout, AmpiTruncateError, AmpiUsageError
from .fabric import SCHEMA_VERSION, Fabric
from .schema import Contract

#: Version string recorded in the fabric at job creation and checked by every
#: incarnation. Includes the schema version so that a runtime whose fabric layout
#: changed is distinguishable from one whose behaviour merely changed.
def _package_version() -> str:
    try:
        return version("agentmpi")
    except PackageNotFoundError:
        return "unknown"


RUNTIME_VERSION = f"{_package_version()}+schema{SCHEMA_VERSION}"

#: Signature of an executor: it receives a prompt and metadata and returns the
#: agent's output.  See :mod:`agentmpi.executor`.
ExecutorFn = Callable[..., Any]


@dataclass
class AgentResult:
    """Outcome of one agent invocation."""

    output: Any
    prompt_tokens: int
    output_tokens: int
    latency_s: float
    attempts: int = 1
    call_id: int | None = None
    label: str = ""
    validated: bool = True
    validation_detail: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


@dataclass
class ContextAccount:
    """Explicit model of one rank's context window occupancy."""

    budget: int = DEFAULT_CONTEXT_BUDGET
    #: Tokens consumed by the rank's standing instructions and scaffolding.
    overhead: int = 0
    #: digest -> tokens for artifacts currently admitted.
    working_set: dict[str, int] = field(default_factory=dict)
    #: Peak occupancy observed, for reporting.
    high_water: int = 0
    n_admissions: int = 0
    n_evictions: int = 0
    n_rejections: int = 0

    @property
    def used(self) -> int:
        return self.overhead + sum(self.working_set.values())

    @property
    def free(self) -> int:
        return max(0, self.budget - self.used)

    @property
    def occupancy(self) -> float:
        return self.used / self.budget if self.budget else 0.0

    def would_fit(self, n_tokens: int) -> bool:
        return self.used + n_tokens <= self.budget

    def admit(self, digest: str, n_tokens: int, *, strict: bool = True) -> bool:
        if digest in self.working_set:
            return True
        if not self.would_fit(n_tokens):
            self.n_rejections += 1
            if strict:
                raise AmpiTruncateError(
                    "artifact does not fit in receiver context budget; "
                    "receive it as a view or with mode=rendezvous",
                    needed=n_tokens,
                    free=self.free,
                    budget=self.budget,
                )
            return False
        self.working_set[digest] = n_tokens
        self.n_admissions += 1
        self.high_water = max(self.high_water, self.used)
        return True

    def evict(self, digest: str) -> int:
        n = self.working_set.pop(digest, 0)
        if n:
            self.n_evictions += 1
        return n

    def clear(self) -> None:
        self.n_evictions += len(self.working_set)
        self.working_set.clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "used": self.used,
            "occupancy": round(self.occupancy, 4),
            "high_water": self.high_water,
            "n_items": len(self.working_set),
            "admissions": self.n_admissions,
            "evictions": self.n_evictions,
            "rejections": self.n_rejections,
        }


@dataclass
class CostAccount:
    """Token and money accounting for one rank."""

    tokens_in: int = 0
    tokens_out: int = 0
    n_agent_calls: int = 0
    n_messages_sent: int = 0
    n_messages_recv: int = 0
    tokens_sent: int = 0
    tokens_recv: int = 0
    #: Tokens that a rendezvous transfer avoided moving into context.  This is
    #: the headline number for the transport-mode experiment.
    tokens_deferred: int = 0
    usd_in_per_mtok: float = 3.0
    usd_out_per_mtok: float = 15.0

    @property
    def usd(self) -> float:
        return (self.tokens_in / 1e6) * self.usd_in_per_mtok + (self.tokens_out / 1e6) * self.usd_out_per_mtok

    def snapshot(self) -> dict[str, Any]:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "agent_calls": self.n_agent_calls,
            "messages_sent": self.n_messages_sent,
            "messages_recv": self.n_messages_recv,
            "tokens_sent": self.tokens_sent,
            "tokens_recv": self.tokens_recv,
            "tokens_deferred": self.tokens_deferred,
            "usd": round(self.usd, 4),
        }


class RankRuntime:
    """Per-rank state shared by every communicator the rank belongs to.

    One of these exists per agent rank per process.  It owns the lease, the
    context account, the cost account, and the binding to an executor.
    """

    def __init__(
        self,
        fabric: Fabric,
        wrank: int,
        *,
        executor: ExecutorFn | None = None,
        context_budget: int = DEFAULT_CONTEXT_BUDGET,
        eager_limit: int = DEFAULT_EAGER_LIMIT,
        unexpected_limit: int | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
        name: str = "",
        strict_context: bool = True,
    ) -> None:
        self.fabric = fabric
        self.wrank = wrank
        self.name = name or f"rank{wrank}"
        self.executor = executor
        self.eager_limit = eager_limit
        #: Bound on the total token volume of *unmatched eager* messages a rank
        #: will accept.  The analogue of an MPI implementation's eager buffer
        #: pool: exceeding it applies back-pressure to senders, which is what
        #: turns an unsafe all-eager program into a detectable deadlock rather
        #: than an out-of-memory crash.
        self.unexpected_limit = unexpected_limit if unexpected_limit is not None else 8 * eager_limit
        self.context = ContextAccount(budget=context_budget)
        self.cost = CostAccount()
        self.strict_context = strict_context
        self.lease_seconds = lease_seconds
        self.incarnation = 0
        self.token = uuid.uuid4().hex
        self._finalized = False
        #: Set when a supervisor asks this rank to wind down cleanly.
        self.stop_requested = False

    # ------------------------------------------------------------- lifecycle

    def register(self, *, executor_name: str = "local") -> None:
        """Claim the rank, bumping the incarnation counter.

        Also records the runtime version this incarnation is running, and warns when
        it differs from the version that created the job.  Protocol state lives
        outside the agents and is durable; the runtime *code* is shared mutable state
        that the protocol says nothing about, and a population half of which is
        running a different build is a class of failure no amount of durable state
        prevents.  We hit exactly this by editing an editable install while a live
        population executed against it.
        """
        now = time.time()
        self._check_runtime_version()
        with self.fabric.write() as cur:
            row = cur.execute("SELECT incarnation FROM ranks WHERE rank=?", (self.wrank,)).fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO ranks(rank, name, state, incarnation, executor, lease_expires, last_seen,"
                    " context_budget, eager_limit, unexpected_limit, meta) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        self.wrank,
                        self.name,
                        RankState.RUNNING.value,
                        1,
                        executor_name,
                        now + self.lease_seconds,
                        now,
                        self.context.budget,
                        self.eager_limit,
                        self.unexpected_limit,
                        json.dumps({"token": self.token}),
                    ),
                )
                self.incarnation = 1
            else:
                self.incarnation = int(row["incarnation"]) + 1
                cur.execute(
                    "UPDATE ranks SET state=?, incarnation=?, executor=?, lease_expires=?, last_seen=?,"
                    " context_budget=?, eager_limit=?, unexpected_limit=?, meta=? WHERE rank=?",
                    (
                        RankState.RUNNING.value,
                        self.incarnation,
                        executor_name,
                        now + self.lease_seconds,
                        now,
                        self.context.budget,
                        self.eager_limit,
                        self.unexpected_limit,
                        json.dumps({"token": self.token}),
                        self.wrank,
                    ),
                )
            self.fabric.emit(
                "rank.init",
                rank=self.wrank,
                cur=cur,
                incarnation=self.incarnation,
                executor=executor_name,
                budget=self.context.budget,
                eager_limit=self.eager_limit,
            )

    def _check_runtime_version(self) -> None:
        """Compare this incarnation's runtime version against the job's.

        A mismatch is emitted as a traced event rather than raised, because refusing
        to start would strand a population mid-run for what is usually a benign
        upgrade -- but it must be *visible*, since the alternative is debugging a
        heisenbug caused by half the ranks running different code.
        """
        recorded = self.fabric.get_meta("runtime_version")
        if recorded is None:
            self.fabric.set_meta("runtime_version", RUNTIME_VERSION)
            return
        if recorded != RUNTIME_VERSION:
            self.fabric.emit(
                "rank.version_mismatch",
                rank=self.wrank,
                job_version=recorded,
                worker_version=RUNTIME_VERSION,
            )

    def heartbeat(self) -> None:
        """Renew the lease.  The only input to the failure detector."""
        now = time.time()
        with self.fabric.write() as cur:
            cur.execute(
                "UPDATE ranks SET lease_expires=?, last_seen=?, context_used=?, context_high=?,"
                " tokens_in=?, tokens_out=?, n_calls=? WHERE rank=?",
                (
                    now + self.lease_seconds,
                    now,
                    self.context.used,
                    self.context.high_water,
                    self.cost.tokens_in,
                    self.cost.tokens_out,
                    self.cost.n_agent_calls,
                    self.wrank,
                ),
            )

    def finalize(self, state: RankState = RankState.FINALIZED) -> None:
        if self._finalized:
            return
        self._finalized = True
        with self.fabric.write() as cur:
            cur.execute(
                "UPDATE ranks SET state=?, last_seen=?, context_used=?, context_high=?, tokens_in=?,"
                " tokens_out=?, n_calls=?, cost_usd=? WHERE rank=?",
                (
                    state.value,
                    time.time(),
                    self.context.used,
                    self.context.high_water,
                    self.cost.tokens_in,
                    self.cost.tokens_out,
                    self.cost.n_agent_calls,
                    self.cost.usd,
                    self.wrank,
                ),
            )
            self.fabric.emit(
                "rank.finalize",
                rank=self.wrank,
                cur=cur,
                state=state.value,
                context=self.context.snapshot(),
                cost=self.cost.snapshot(),
            )

    # ------------------------------------------------------------ context ops

    def admit(self, digest: str, n_tokens: int) -> bool:
        return self.context.admit(digest, n_tokens, strict=self.strict_context)

    def evict(self, digest: str) -> int:
        return self.context.evict(digest)

    def compact(self, *, keep: int = 0) -> int:
        """Drop all but the ``keep`` largest admitted artifacts.

        A blunt instrument, offered because it is the operation every agent
        framework performs implicitly and silently.  Making it an explicit,
        traced protocol action means a harness's context pressure shows up in
        the run record instead of as an unexplained quality drop.
        """
        items = sorted(self.context.working_set.items(), key=lambda kv: -kv[1])
        dropped = 0
        for digest, n in items[keep:]:
            self.context.evict(digest)
            dropped += n
        if dropped:
            with self.fabric.write() as cur:
                cur.execute("UPDATE ranks SET n_compactions = n_compactions + 1 WHERE rank=?", (self.wrank,))
                self.fabric.emit("rank.compact", rank=self.wrank, cur=cur, dropped_tokens=dropped, kept=keep)
        return dropped

    # -------------------------------------------------------------- executor

    def agent(
        self,
        prompt: str,
        *,
        label: str = "",
        contract: Contract | None = None,
        max_tokens: int | None = None,
        retries: int = 1,
        ctx: int = 0,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke this rank's executor: the "compute kernel" of a rank.

        This is the one call in AgentMPI whose cost is dominated by a model
        rather than by the fabric, and it is deliberately the *only* place a
        language model appears in the protocol.  Everything else — matching,
        collectives, locking, failure detection — is host-side and
        deterministic.  Keeping the nondeterminism confined to this call is
        what makes a harness debuggable: a run's protocol behaviour can be
        replayed exactly from the event log while the model outputs are read
        back from the artifact store.
        """
        if self.executor is None:
            raise AmpiUsageError("rank has no executor bound", rank=self.wrank)
        prompt_tokens = _tokens.count(prompt)
        if not self.context.would_fit(prompt_tokens) and self.strict_context:
            raise AmpiContextOverflow(
                "prompt does not fit in remaining context budget",
                rank=self.wrank,
                prompt_tokens=prompt_tokens,
                free=self.context.free,
                budget=self.context.budget,
            )
        last_detail = ""
        started = time.time()
        for attempt in range(1, max(1, retries) + 1):
            result = self.executor(
                prompt,
                rank=self.wrank,
                label=label or "agent",
                contract=contract,
                max_tokens=max_tokens,
                ctx=ctx,
                incarnation=self.incarnation,
                timeout=timeout,
                attempt=attempt,
                **kwargs,
            )
            output = result.output if isinstance(result, AgentResult) else result
            out_tokens = result.output_tokens if isinstance(result, AgentResult) else _tokens.count(output)
            self.cost.tokens_in += prompt_tokens
            self.cost.tokens_out += out_tokens
            self.cost.n_agent_calls += 1
            problems = contract.check(output) if contract is not None else []
            if not problems:
                self.fabric.emit(
                    "agent.call",
                    rank=self.wrank,
                    ctx=ctx,
                    kind_label=label,
                    prompt_tokens=prompt_tokens,
                    output_tokens=out_tokens,
                    attempt=attempt,
                    latency_s=round(time.time() - started, 3),
                    contract=contract.name if contract else None,
                )
                return output
            last_detail = "; ".join(problems)
            self.fabric.emit(
                "agent.contract_violation",
                rank=self.wrank,
                ctx=ctx,
                kind_label=label,
                attempt=attempt,
                problems=problems,
                contract=contract.name if contract else None,
            )
            prompt = (
                f"{prompt}\n\n---\nYour previous response violated its output contract:\n"
                f"{last_detail}\nReturn a corrected response that satisfies the contract."
            )
            prompt_tokens = _tokens.count(prompt)
        raise AmpiTimeout("agent failed to satisfy contract", rank=self.wrank, detail=last_detail, retries=retries)

    def snapshot(self) -> dict[str, Any]:
        return {
            "rank": self.wrank,
            "name": self.name,
            "incarnation": self.incarnation,
            "context": self.context.snapshot(),
            "cost": self.cost.snapshot(),
        }


def rank_from_env(fabric: Fabric | None = None) -> int:
    """Read the local rank from the environment, as ``mpiexec`` provides it."""
    for var in ("AMPI_RANK", "OMPI_COMM_WORLD_RANK", "PMI_RANK"):
        if var in os.environ:
            return int(os.environ[var])
    raise AmpiUsageError("no rank in environment; set $AMPI_RANK")
