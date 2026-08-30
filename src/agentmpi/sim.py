"""Discrete-event simulation of AgentMPI collectives, calibrated from real runs.

Why a simulator belongs in this repository
------------------------------------------
No agent-systems paper can afford to run a 1024-rank experiment, and none should
pretend otherwise.  HPC has an honest answer to the same problem: build a
discrete-event simulator of the communication pattern, calibrate its parameters
against a real run at a size you *can* afford, validate its predictions at
intermediate sizes, and then use it to extrapolate.  LogGOPSim does exactly this
for MPI.  This module is the AgentMPI equivalent, and every simulated number in
the paper is labelled as such and accompanied by its validation error against
measured runs at the sizes where both exist.

What is simulated
-----------------
The algorithms in :mod:`agentmpi.algorithms` are re-expressed as *dependency
graphs* over send/recv/operator events, and executed under a per-rank
availability model.  Each rank's agent invocation latency is drawn from a
log-normal distribution fitted to measurements — right-skewed with a heavy tail,
which is what agent latency actually looks like and which matters enormously,
because a collective's completion time is a maximum over ranks and maxima are
governed by the tail.

What is *not* simulated: output quality.  Fidelity is modelled only through fold
depth, and the per-application loss δ used there is measured, not assumed.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .cost import CostParams, _log2c


@dataclass
class SimParams:
    """Simulation parameters, normally produced by :func:`from_cost_params`."""

    #: Median of the log-normal agent latency, seconds.
    alpha_median_s: float = 12.0
    #: Shape parameter σ of the log-normal.  Fitted values of 0.5-0.9 are typical.
    alpha_sigma: float = 0.7
    #: Marginal seconds per output token.
    beta_s_per_token: float = 1.0 / 45.0
    #: Fabric round-trip, seconds.
    fabric_s: float = 0.003
    #: Probability that a given invocation stalls, and by how much.
    stall_rate: float = 0.0
    stall_multiplier: float = 8.0
    #: Probability that a rank dies permanently during the run.
    death_rate: float = 0.0
    #: Per-application fidelity loss for a lossy reduction operator.
    delta: float = 0.05
    seed: int = 0

    def draw_latency(self, rng: random.Random, n_tokens: int) -> float:
        lat = rng.lognormvariate(0.0, self.alpha_sigma) * self.alpha_median_s + n_tokens * self.beta_s_per_token
        if rng.random() < self.stall_rate:
            lat *= self.stall_multiplier
        return lat


def from_cost_params(params: CostParams, *, sigma: float | None = None, **kw: Any) -> SimParams:
    """Build simulation parameters from a calibration fit."""
    est_sigma = sigma
    if est_sigma is None and params.alpha_p50 > 0 and params.alpha_p99 > params.alpha_p50:
        # For a log-normal, p99/p50 = exp(z_0.99 * sigma) with z_0.99 = 2.326,
        # so the observed quantile ratio identifies sigma directly.
        est_sigma = max(0.05, min(2.0, math.log(params.alpha_p99 / params.alpha_p50) / 2.326))
    return SimParams(
        alpha_median_s=params.alpha_p50 or params.alpha_s,
        alpha_sigma=est_sigma if est_sigma is not None else 0.7,
        beta_s_per_token=params.beta_s_per_token,
        fabric_s=params.fabric_s,
        delta=params.delta,
        **kw,
    )


@dataclass
class SimResult:
    op: str
    algorithm: str
    p: int
    n_tokens: int
    makespan_s: float
    critical_path_rounds: int
    messages: int
    volume_tokens: int
    agent_calls: int
    fold_depth: int
    fidelity: float
    #: Fraction of the makespan during which the median rank was idle: the
    #: quantity a harness author actually wants, because idle agents are paid for
    #: in wall time and in nothing else.
    idle_fraction: float
    per_rank_finish: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "algorithm": self.algorithm,
            "p": self.p,
            "n_tokens": self.n_tokens,
            "makespan_s": round(self.makespan_s, 3),
            "rounds": self.critical_path_rounds,
            "messages": self.messages,
            "volume_tokens": self.volume_tokens,
            "agent_calls": self.agent_calls,
            "fold_depth": self.fold_depth,
            "fidelity": round(self.fidelity, 4),
            "idle_fraction": round(self.idle_fraction, 4),
        }


# ---------------------------------------------------------------------- engine


@dataclass(order=True)
class _Event:
    t: float
    seq: int
    rank: int = field(compare=False)
    kind: str = field(compare=False)
    data: dict[str, Any] = field(compare=False, default_factory=dict)


class _Sim:
    """Minimal event-driven executor for a rank-dependency schedule."""

    def __init__(self, p: int, params: SimParams) -> None:
        self.p = p
        self.params = params
        self.rng = random.Random(params.seed)
        self.ready = [0.0] * p          # earliest time rank r can act
        self.arrive: dict[tuple[int, str], float] = {}
        self.busy = [0.0] * p           # total time spent working
        self.messages = 0
        self.volume = 0
        self.agent_calls = 0
        self.dead: set[int] = set()
        for r in range(p):
            if self.rng.random() < params.death_rate:
                self.dead.add(r)

    def transfer(self, src: int, dst: int, n_tokens: int, key: str) -> None:
        """Model one message: available at the destination after a fabric hop."""
        if src in self.dead:
            return
        t = max(self.ready[src], 0.0) + self.params.fabric_s
        self.ready[src] = t
        self.messages += 1
        self.volume += n_tokens
        prev = self.arrive.get((dst, key), 0.0)
        self.arrive[(dst, key)] = max(prev, t)

    def wait(self, rank: int, key: str) -> None:
        self.ready[rank] = max(self.ready[rank], self.arrive.get((rank, key), self.ready[rank]))

    def work(self, rank: int, n_tokens: int) -> None:
        """Model one agent invocation on ``rank``."""
        if rank in self.dead:
            self.ready[rank] = float("inf")
            return
        lat = self.params.draw_latency(self.rng, n_tokens)
        self.ready[rank] += lat
        self.busy[rank] += lat
        self.agent_calls += 1

    def finish(self) -> tuple[float, float, list[float]]:
        finite = [t for t in self.ready if t != float("inf")]
        makespan = max(finite) if finite else 0.0
        idle = [max(0.0, makespan - b) / makespan if makespan else 0.0 for b in self.busy]
        return makespan, (statistics.median(idle) if idle else 0.0), list(self.ready)


# ------------------------------------------------------------------ schedules


def simulate(
    op: str,
    algorithm: str,
    p: int,
    n_tokens: int,
    params: SimParams | None = None,
    *,
    op_cost_tokens: int = 0,
    trials: int = 1,
) -> SimResult:
    """Simulate one collective.  Averages over ``trials`` random draws.

    Averaging matters: with a heavy-tailed latency distribution, a single draw of
    a 512-rank barrier is dominated by one unlucky rank, so a single-trial
    makespan is not a useful statistic.  Every simulated figure in the paper uses
    at least 32 trials and reports the spread.
    """
    params = params or SimParams()
    makespans: list[float] = []
    idles: list[float] = []
    last: _Sim | None = None
    last_depth = 0
    last_ranks: list[float] = []
    for t in range(max(1, trials)):
        sp = SimParams(**{**params.__dict__, "seed": params.seed + t})
        sim = _Sim(p, sp)
        depth = _run_schedule(sim, op, algorithm, p, n_tokens, op_cost_tokens)
        makespan, idle, ranks = sim.finish()
        makespans.append(makespan)
        idles.append(idle)
        last = sim
        last_depth = depth
        last_ranks = ranks
    assert last is not None
    return SimResult(
        op=op,
        algorithm=algorithm,
        p=p,
        n_tokens=n_tokens,
        makespan_s=statistics.median(makespans),
        critical_path_rounds=_rounds(op, algorithm, p),
        messages=last.messages,
        volume_tokens=last.volume,
        agent_calls=last.agent_calls,
        fold_depth=last_depth,
        fidelity=(1.0 - params.delta) ** last_depth,
        idle_fraction=statistics.median(idles),
        per_rank_finish=last_ranks,
    )


def _rounds(op: str, algorithm: str, p: int) -> int:
    if algorithm in ("binomial", "dissemination", "recursive_doubling", "bruck"):
        return _log2c(p)
    if algorithm in ("reduce_bcast",):
        return 2 * _log2c(p)
    if algorithm in ("flat", "linear", "central"):
        return 1 if op != "barrier" else 2
    return max(1, p - 1)


def _run_schedule(sim: _Sim, op: str, algorithm: str, p: int, n: int, op_tokens: int) -> int:
    """Execute the dependency schedule for one collective; returns fold depth."""
    if op == "barrier":
        return _sched_barrier(sim, algorithm, p)
    if op == "bcast":
        return _sched_bcast(sim, algorithm, p, n)
    if op in ("gather", "scatter"):
        return _sched_gather(sim, algorithm, p, n, gather=(op == "gather"))
    if op == "allgather":
        return _sched_allgather(sim, algorithm, p, n)
    if op == "reduce":
        return _sched_reduce(sim, algorithm, p, n, op_tokens or n)
    if op == "allreduce":
        d = _sched_reduce(sim, "binomial" if algorithm == "reduce_bcast" else algorithm, p, n, op_tokens or n)
        _sched_bcast(sim, "binomial", p, n)
        return d
    if op == "scan":
        return _sched_scan(sim, algorithm, p, n, op_tokens or n)
    if op == "alltoall":
        return _sched_alltoall(sim, algorithm, p, n)
    raise KeyError(f"no simulation schedule for {op}/{algorithm}")


def _sched_barrier(sim: _Sim, algorithm: str, p: int) -> int:
    if algorithm == "dissemination":
        for k in range(_log2c(p)):
            key = f"b{k}"
            for r in range(p):
                sim.transfer(r, (r + (1 << k)) % p, 1, key)
            for r in range(p):
                sim.wait(r, key)
    elif algorithm == "linear":
        for r in range(1, p):
            sim.transfer(r, 0, 1, "up")
        sim.wait(0, "up")
        for r in range(1, p):
            sim.transfer(0, r, 1, "dn")
        for r in range(1, p):
            sim.wait(r, "dn")
    else:  # central
        t = max(sim.ready) + 2 * sim.params.fabric_s
        for r in range(p):
            sim.ready[r] = t
    return 0


def _sched_bcast(sim: _Sim, algorithm: str, p: int, n: int) -> int:
    if algorithm == "flat":
        for r in range(1, p):
            sim.transfer(0, r, n, "bc")
            sim.wait(r, "bc")
    elif algorithm == "chain":
        for r in range(1, p):
            sim.transfer(r - 1, r, n, f"bc{r}")
            sim.wait(r, f"bc{r}")
    else:  # binomial
        for k in range(_log2c(p)):
            mask = 1 << k
            for r in range(p):
                if r < mask and r + mask < p:
                    sim.transfer(r, r + mask, n, f"bc{k}")
            for r in range(p):
                if mask <= r < 2 * mask and r < p:
                    sim.wait(r, f"bc{k}")
    return 0


def _sched_gather(sim: _Sim, algorithm: str, p: int, n: int, *, gather: bool) -> int:
    if algorithm == "linear":
        for r in range(1, p):
            if gather:
                sim.transfer(r, 0, n, "g")
            else:
                sim.transfer(0, r, n, f"s{r}")
                sim.wait(r, f"s{r}")
        if gather:
            sim.wait(0, "g")
    else:  # binomial
        levels = _log2c(p)
        for k in range(levels):
            mask = 1 << (levels - 1 - k) if gather else 1 << k
            for r in range(p):
                partner = r + mask
                if r % (2 * mask) == 0 and partner < p:
                    if gather:
                        sim.transfer(partner, r, n * mask, f"g{k}")
                        sim.wait(r, f"g{k}")
                    else:
                        sim.transfer(r, partner, n * mask, f"s{k}")
                        sim.wait(partner, f"s{k}")
    return 0


def _sched_allgather(sim: _Sim, algorithm: str, p: int, n: int) -> int:
    if algorithm == "ring":
        for k in range(p - 1):
            for r in range(p):
                sim.transfer(r, (r + 1) % p, n, f"ag{k}")
            for r in range(p):
                sim.wait(r, f"ag{k}")
    elif algorithm == "gather_bcast":
        _sched_gather(sim, "binomial", p, n, gather=True)
        _sched_bcast(sim, "binomial", p, n * p)
    else:  # bruck / recursive_doubling
        for k in range(_log2c(p)):
            vol = n * min(1 << k, p - (1 << k))
            for r in range(p):
                sim.transfer(r, (r - (1 << k)) % p, vol, f"ag{k}")
            for r in range(p):
                sim.wait(r, f"ag{k}")
    return 0


def _sched_reduce(sim: _Sim, algorithm: str, p: int, n: int, op_tokens: int) -> int:
    if algorithm == "chain":
        for r in range(1, p):
            sim.transfer(r - 1, r, n, f"rd{r}")
            sim.wait(r, f"rd{r}")
            sim.work(r, op_tokens)
        return p - 1
    if algorithm == "flat":
        for r in range(1, p):
            sim.transfer(r, 0, n, "rd")
        sim.wait(0, "rd")
        for _ in range(p - 1):
            sim.work(0, op_tokens)
        return p - 1
    # binomial
    levels = _log2c(p)
    for k in range(levels):
        mask = 1 << k
        for r in range(0, p, 2 * mask):
            partner = r + mask
            if partner < p:
                sim.transfer(partner, r, n, f"rd{k}")
                sim.wait(r, f"rd{k}")
                sim.work(r, op_tokens)
    return levels


def _sched_scan(sim: _Sim, algorithm: str, p: int, n: int, op_tokens: int) -> int:
    if algorithm == "chain":
        for r in range(1, p):
            sim.transfer(r - 1, r, n, f"sc{r}")
            sim.wait(r, f"sc{r}")
            sim.work(r, op_tokens)
        return p - 1
    levels = _log2c(p)
    for k in range(levels):
        dist = 1 << k
        for r in range(p):
            if r + dist < p:
                sim.transfer(r, r + dist, n, f"sc{k}")
        for r in range(dist, p):
            sim.wait(r, f"sc{k}")
            sim.work(r, op_tokens)
    return levels


def _sched_alltoall(sim: _Sim, algorithm: str, p: int, n: int) -> int:
    if algorithm == "bruck":
        for k in range(_log2c(p)):
            for r in range(p):
                sim.transfer(r, (r + (1 << k)) % p, n * p // 2, f"aa{k}")
            for r in range(p):
                sim.wait(r, f"aa{k}")
    elif algorithm == "linear":
        for r in range(p):
            for d in range(p):
                if d != r:
                    sim.transfer(r, d, n, "aa")
        for r in range(p):
            sim.wait(r, "aa")
    else:  # pairwise
        for k in range(1, p):
            for r in range(p):
                sim.transfer(r, (r + k) % p, n, f"aa{k}")
            for r in range(p):
                sim.wait(r, f"aa{k}")
    return 0


# ------------------------------------------------------------------- studies


def scaling_study(
    op: str,
    algorithms: Sequence[str],
    sizes: Sequence[int],
    n_tokens: int,
    params: SimParams | None = None,
    *,
    trials: int = 32,
    op_cost_tokens: int = 0,
) -> list[dict[str, Any]]:
    """Sweep ``algorithms`` x ``sizes`` and return tidy rows for plotting."""
    rows: list[dict[str, Any]] = []
    for alg in algorithms:
        for p in sizes:
            try:
                res = simulate(op, alg, p, n_tokens, params, trials=trials, op_cost_tokens=op_cost_tokens)
            except KeyError:
                continue
            rows.append(res.as_dict())
    return rows


def validate(
    measured: Sequence[tuple[int, float]],
    op: str,
    algorithm: str,
    n_tokens: int,
    params: SimParams,
    *,
    trials: int = 64,
) -> dict[str, Any]:
    """Compare simulated makespans against measured ones.

    Reports mean absolute percentage error, which is the number a reader needs
    in order to decide how much to believe the extrapolated curves.  A simulator
    presented without this number is not evidence.
    """
    errs: list[float] = []
    rows: list[dict[str, Any]] = []
    for p, meas in measured:
        sim = simulate(op, algorithm, p, n_tokens, params, trials=trials)
        err = abs(sim.makespan_s - meas) / meas if meas else 0.0
        errs.append(err)
        rows.append({"p": p, "measured_s": round(meas, 3), "simulated_s": round(sim.makespan_s, 3), "rel_err": round(err, 4)})
    return {
        "op": op,
        "algorithm": algorithm,
        "n_tokens": n_tokens,
        "mape": round(100.0 * sum(errs) / len(errs), 2) if errs else None,
        "max_rel_err": round(100.0 * max(errs), 2) if errs else None,
        "points": rows,
    }
