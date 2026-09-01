r"""The AgentMPI cost model, and calibration from measured runs.

MPI's algorithm selection rests on the Hockney model: a message of *n* bytes
between two processes costs :math:`\alpha + n\beta`.  Everything in a
collective-tuning paper is an application of that formula plus a topology term.
The model works because α and β are stable properties of an interconnect.

For agent ranks the same decomposition applies, with three changes that
materially alter which algorithms win.

**1. Reinterpretation.**  Volume is measured in tokens.  α is the fixed cost of
an agent invocation — queueing, session setup, prompt processing, time to first
token.  β is the marginal cost per output token.  Both are far larger than their
HPC counterparts, and their *ratio* is what matters:

.. math::
   T_\text{msg}(n) = \alpha + n\beta

with α on the order of 10⁰–10¹ s and β⁻¹ on the order of 10¹–10² tokens/s.  The
resulting α/β product — the message size at which latency and bandwidth costs
balance — is a few hundred to a few thousand tokens, which is *exactly* the size
of a typical agent artifact.  Neither term can be neglected, which is why the
protocol exposes both a message count and a token volume in every collective's
statistics.

**2. A price axis.**  Wall time and money are not proportional, because money is
paid per token regardless of how much parallelism hid the latency.  Running 32
ranks instead of 1 divides time by up to 32 and divides cost by nothing; if
anything it *multiplies* cost, because coordination messages are pure overhead.
So an agent harness has two independent objectives and the cost model must report
both:

.. math::
   C = \gamma_\text{in} \sum_r n^\text{in}_r + \gamma_\text{out} \sum_r n^\text{out}_r

**3. A fidelity axis.**  A reduction implemented by a model loses information,
and the loss compounds with the *depth* of the reduction, not its width.  Model
the surviving fidelity of a *d*-deep fold as

.. math::
   F(d) = (1 - \delta)^d

for a per-application loss δ.  A linear chain over *p* ranks has *d = p−1*; a
binomial tree has *d = ⌈log₂ p⌉*.  So the tree is better on *all three* axes for
a simply-lossy operator, and the interesting failure case is an operator whose
loss is *order-dependent* rather than depth-dependent.  This module provides the
estimator; ``benchmarks/bench_reduce.py`` measures δ empirically.

Scaling laws worth stating explicitly, because agent harnesses violate their
assumptions constantly:

*Amdahl*: :math:`S(p) = 1/(f + (1-f)/p)` where *f* is the serial fraction.  In an
agent harness *f* is dominated by the phases that must be done by one rank —
plan, integrate, arbitrate — and those phases do not shrink.

*Universal Scalability Law* (Gunther): :math:`S(p) = p / (1 + \sigma(p-1) + \kappa p(p-1))`
where σ is contention and κ is *coherency* cost.  The κ term is the one that
matters here: an all-to-all agent conversation has κ > 0 by construction, so its
throughput has a maximum and then *declines* with population size.  Fitting κ
from a measured scaling curve tells a harness author whether their coordination
pattern has a ceiling, and every result in this repository reports the fit.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .fabric import Fabric


@dataclass
class CostParams:
    """Calibrated parameters of the AgentMPI cost model."""

    #: Per-invocation latency, seconds.
    alpha_s: float = 12.0
    #: Marginal seconds per output token.
    beta_s_per_token: float = 1.0 / 45.0
    #: Marginal seconds per input token (prompt processing).
    beta_in_s_per_token: float = 1.0 / 8000.0
    #: Price per input token, USD.
    gamma_in: float = 3.0 / 1e6
    #: Price per output token, USD.
    gamma_out: float = 15.0 / 1e6
    #: Fabric round-trip latency, seconds.  Two orders of magnitude below α,
    #: which is why the protocol can afford durable state on every operation.
    fabric_s: float = 0.003
    #: Per-application fidelity loss of a lossy reduction operator.
    delta: float = 0.05
    n_samples: int = 0
    alpha_p50: float = 0.0
    alpha_p99: float = 0.0
    #: How ``alpha_s`` and ``beta_s_per_token`` were obtained.  ``least_squares`` is a real fit;
    #: ``median_fallback`` means the regression produced a negative slope or intercept and robust
    #: medians were substituted; ``median_only`` means there were too few distinct output sizes to
    #: fit at all; ``default`` means no invocations were observed.  Reported because a fit and a
    #: fallback are not interchangeable, and a run with broker contention silently produces the
    #: latter --- the queue wait enters the latency but not the token count, so the relationship the
    #: regression is looking for is not in the data.
    fit_method: str = "default"
    #: The slope the regression produced when it was rejected, for diagnosis.
    fit_rejected_beta: float | None = None
    #: The intercept the regression produced when it was rejected.  Recorded separately because the
    #: guard tests both and they fail for different reasons: a negative *slope* means latency fell
    #: as output grew, which is nonsense and usually means something outside the model dominates;
    #: a negative *intercept* often means the opposite --- a good fit whose line simply passes below
    #: the origin, which is physically impossible but statistically unremarkable on a narrow token
    #: range. Reporting only one of them mislabelled every translation run: their slopes were
    #: positive and it was the intercept that failed.
    fit_rejected_alpha: float | None = None
    #: Coefficient of determination of the rejected fit, so a reader can see whether a rejected
    #: regression was actually a poor description of the data or merely an inconvenient one.
    fit_rejected_r2: float | None = None

    def message_time(self, n_tokens: int) -> float:
        return self.alpha_s + n_tokens * self.beta_s_per_token

    def message_price(self, n_in: int, n_out: int) -> float:
        return n_in * self.gamma_in + n_out * self.gamma_out

    def fidelity(self, depth: int) -> float:
        return (1.0 - self.delta) ** max(0, depth)

    def as_dict(self) -> dict[str, Any]:
        return {
            "alpha_s": round(self.alpha_s, 4),
            "beta_s_per_token": round(self.beta_s_per_token, 6),
            "tokens_per_s": round(1.0 / self.beta_s_per_token, 2) if self.beta_s_per_token else None,
            "alpha_beta_crossover_tokens": round(self.alpha_s / self.beta_s_per_token, 1) if self.beta_s_per_token else None,
            "gamma_in_per_mtok": round(self.gamma_in * 1e6, 3),
            "gamma_out_per_mtok": round(self.gamma_out * 1e6, 3),
            "fabric_s": self.fabric_s,
            "delta": self.delta,
            "n_samples": self.n_samples,
            "alpha_p50": round(self.alpha_p50, 3),
            "alpha_p99": round(self.alpha_p99, 3),
            "fit_method": self.fit_method,
            "fit_rejected_beta": (
                round(self.fit_rejected_beta, 6) if self.fit_rejected_beta is not None else None
            ),
            "fit_rejected_alpha": (
                round(self.fit_rejected_alpha, 4) if self.fit_rejected_alpha is not None else None
            ),
            "fit_rejected_r2": (
                round(self.fit_rejected_r2, 4) if self.fit_rejected_r2 is not None else None
            ),
        }


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linearly interpolated percentile of an already-sorted list.

    Written out because the index arithmetic it replaces was wrong for small samples. Taking
    ``lat[int(q * (n - 1))]`` for p95 and ``lat[n // 2]`` for p50 gives, at ``n = 2``, the minimum
    for p95 and the maximum for p50 --- so the dashboard reported a p95 *below* its p50 for every
    two-call run, and ``alpha_p99`` had the same defect. Interpolation also stops a small sample
    reporting a percentile that is simply one of its own extremes.
    """
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def calibrate(
    source: "Fabric | list[dict[str, Any]]",
    *,
    price_in_per_mtok: float = 3.0,
    price_out_per_mtok: float = 15.0,
) -> CostParams:
    """Fit α and β from an executed run's ``agent.call`` events.

    Least squares on ``latency = α + n_out·β`` over observed invocations.  The
    fit is reported alongside the median and p99 of the intercept, because the
    *spread* of α — not its mean — governs how long a barrier waits, and a model
    that reported only the mean would predict collective latencies that are
    systematically too optimistic.

    Accepts a ``Fabric`` or an already-materialised event list, so an exported
    ``traces/events/*.jsonl`` log calibrates through this same code path.
    """
    if isinstance(source, Fabric):
        events = source.events(kinds=["agent.call"])
    else:
        events = [e for e in source if e.get("kind") == "agent.call"]
    xs: list[float] = []
    ys: list[float] = []
    for e in events:
        p = e["payload"]
        lat = float(p.get("latency_s", 0.0) or 0.0)
        n_out = float(p.get("output_tokens", 0) or 0)
        if lat > 0:
            xs.append(n_out)
            ys.append(lat)
    params = CostParams(gamma_in=price_in_per_mtok / 1e6, gamma_out=price_out_per_mtok / 1e6, n_samples=len(xs))
    if len(xs) >= 3 and len(set(xs)) > 1:
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
        beta = sxy / sxx if sxx else 0.0
        alpha = my - beta * mx
        # A negative slope or intercept is a fit artefact of a narrow token
        # range; fall back to the robust decomposition rather than emit a
        # nonsensical model.
        #
        # Which branch was taken is recorded, because the two are not
        # interchangeable and nothing previously distinguished them.  On the
        # semantic-glossary run the regression returned β = −0.108 s/token --- the
        # longest broker queue waits landed on the smallest-output calls --- so the
        # published α and β were the median fallback rather than a fit at all, and
        # a reader had no way to tell.  The guard prevented a nonsensical model; it
        # did not make the reported numbers a regression.
        if beta > 0 and alpha > 0:
            params.beta_s_per_token = beta
            params.alpha_s = alpha
            params.fit_method = "least_squares"
        else:
            params.alpha_s = statistics.median(ys)
            params.beta_s_per_token = max(1e-6, (statistics.median(ys) / max(1.0, statistics.median(xs))) / 2)
            params.fit_method = "median_fallback"
            params.fit_rejected_beta = beta
            params.fit_rejected_alpha = alpha
            sst = sum((y - my) ** 2 for y in ys)
            ssr = sum((y - (alpha + beta * x)) ** 2 for x, y in zip(xs, ys, strict=True))
            params.fit_rejected_r2 = (1.0 - ssr / sst) if sst > 0 else None
    elif ys:
        params.alpha_s = statistics.median(ys)
        params.fit_method = "median_only"
    if ys:
        ordered = sorted(ys)
        params.alpha_p50 = _percentile(ordered, 0.50)
        params.alpha_p99 = _percentile(ordered, 0.99)
    return params


# ---------------------------------------------------------------- predictions


@dataclass
class Prediction:
    op: str
    algorithm: str
    p: int
    n_tokens: int
    rounds: int
    messages: int
    volume_tokens: int
    time_s: float
    price_usd: float
    fold_depth: int = 0
    fidelity: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "algorithm": self.algorithm,
            "p": self.p,
            "n_tokens": self.n_tokens,
            "rounds": self.rounds,
            "messages": self.messages,
            "volume_tokens": self.volume_tokens,
            "time_s": round(self.time_s, 3),
            "price_usd": round(self.price_usd, 6),
            "fold_depth": self.fold_depth,
            "fidelity": round(self.fidelity, 4),
        }


def _log2c(p: int) -> int:
    return 0 if p <= 1 else (p - 1).bit_length()


#: Fan-in assumed by the tabulated ``kary`` cost entry. Chosen as a value a
#: 128k-token context comfortably admits for ~1k-token payloads.
DEFAULT_FANIN = 8


def _logkc(p: int, k: int) -> int:
    """ceil(log_k p), computed integrally."""
    if p <= 1:
        return 0
    levels, reach = 0, 1
    while reach < p:
        reach *= max(2, k)
        levels += 1
    return levels


def predict_kary(p: int, n_tokens: int, k: int, params: "CostParams | None" = None, *, op_cost_tokens: int = 0):
    """Cost of a k-ary reduction for an explicit fan-in.

    Exposed separately because the tabulated entry fixes one k, whereas the
    interesting question is how cost and fidelity move *with* k. Message count is
    invariant in k -- every non-root rank sends exactly once -- so the whole effect
    of widening the tree is on rounds and depth, which is why it is close to free.
    """
    params = params or CostParams()
    depth = _logkc(p, k)
    time_s = depth * (params.fabric_s + n_tokens * params.beta_in_s_per_token)
    time_s += depth * params.message_time(op_cost_tokens or n_tokens * k)
    # A k-ary fold performs ceil((p-1)/(k-1)) operator applications, not p-1: each application
    # consumes k inputs and yields one, so it retires k-1 of them. Pricing p-1 charged a wide tree
    # as if it folded pairwise, which erased the whole advantage from the price axis --- the
    # advantage a variadic operator exists to provide. Measured on the archived fidelity runs the
    # count is 3 at p=8 and 5 at p=16 with k=4, against the 7 and 15 the old term charged.
    n_applications = max(1, -(-(p - 1) // max(1, k - 1)))
    price = params.message_price((p - 1) * n_tokens, n_applications * (op_cost_tokens or n_tokens))
    return Prediction(
        op="reduce",
        algorithm=f"kary(k={k})",
        p=p,
        n_tokens=n_tokens,
        rounds=depth,
        messages=p - 1,
        volume_tokens=(p - 1) * n_tokens,
        time_s=time_s,
        price_usd=price,
        fold_depth=depth,
        fidelity=params.fidelity(depth),
    )


#: Closed-form cost expressions for every implemented algorithm.
#:
#: Each entry maps ``(op, algorithm)`` to a function of ``(p, n)`` returning
#: ``(critical_path_rounds, total_messages, total_token_volume, fold_depth)``.
#: These are the formulas the paper tabulates, and the test suite checks the
#: implementations against them: an implementation whose measured message count
#: disagrees with its own cost model is a bug in one of the two, and having both
#: is how the discrepancy gets caught.
FORMULAS: dict[tuple[str, str], Any] = {
    ("bcast", "flat"): lambda p, n: (p - 1, p - 1, (p - 1) * n, 0),
    ("bcast", "binomial"): lambda p, n: (_log2c(p), p - 1, (p - 1) * n, 0),
    ("bcast", "chain"): lambda p, n: (p - 1, p - 1, (p - 1) * n, 0),
    ("bcast", "scatter_allgather"): lambda p, n: (_log2c(p) + p - 1, 2 * (p - 1), 2 * n * (p - 1) / p, 0),
    ("scatter", "linear"): lambda p, n: (p - 1, p - 1, (p - 1) * n, 0),
    ("scatter", "binomial"): lambda p, n: (_log2c(p), p - 1, n * p * _log2c(p) / 2, 0),
    ("gather", "linear"): lambda p, n: (p - 1, p - 1, (p - 1) * n, 0),
    ("gather", "binomial"): lambda p, n: (_log2c(p), p - 1, n * p * _log2c(p) / 2, 0),
    ("allgather", "ring"): lambda p, n: (p - 1, p * (p - 1), p * (p - 1) * n, 0),
    ("allgather", "recursive_doubling"): lambda p, n: (_log2c(p), p * _log2c(p), p * (p - 1) * n, 0),
    ("allgather", "bruck"): lambda p, n: (_log2c(p), p * _log2c(p), p * (p - 1) * n, 0),
    ("allgather", "gather_bcast"): lambda p, n: (2 * _log2c(p), 2 * (p - 1), 2 * (p - 1) * n * (p / 2), 0),
    ("barrier", "dissemination"): lambda p, n: (_log2c(p), p * _log2c(p), p * _log2c(p), 0),
    ("barrier", "linear"): lambda p, n: (2 * (p - 1), 2 * (p - 1), 2 * (p - 1), 0),
    ("barrier", "central"): lambda p, n: (2, 0, 0, 0),
    ("reduce", "chain"): lambda p, n: (p - 1, p - 1, (p - 1) * n, p - 1),
    ("reduce", "flat"): lambda p, n: (1, p - 1, (p - 1) * n, p - 1),
    ("reduce", "binomial"): lambda p, n: (_log2c(p), p - 1, (p - 1) * n, _log2c(p)),
    # A k-ary tree with a variadic operator: same message count as the binary tree,
    # but the fold depth is log_k p rather than log2 p, because an interior rank
    # combines all its children in one application. DEFAULT_FANIN is the value
    # tabulated here; use predict_kary() for a specific k.
    ("reduce", "kary"): lambda p, n: (
        _logkc(p, DEFAULT_FANIN),
        p - 1,
        (p - 1) * n,
        _logkc(p, DEFAULT_FANIN),
    ),
    ("allreduce", "reduce_bcast"): lambda p, n: (2 * _log2c(p), 2 * (p - 1), 2 * (p - 1) * n, _log2c(p)),
    # Recursive-doubling allreduce with the standard non-power-of-two correction.
    # The lowest 2*rem ranks pair up first so that a power-of-two set remains; each
    # pair costs two sends, the reduced set costs pof2*log2(pof2), and the ranks
    # that sat out are sent the answer at the end.  The naive p*log2(p) figure is
    # wrong by up to 25% at non-power-of-two sizes, which are the common case for
    # agent populations.
    ("allreduce", "recursive_doubling"): lambda p, n: (
        _log2c(p),
        3 * (p - (1 << (p.bit_length() - 1))) + (1 << (p.bit_length() - 1)) * (p.bit_length() - 1),
        (3 * (p - (1 << (p.bit_length() - 1))) + (1 << (p.bit_length() - 1)) * (p.bit_length() - 1)) * n,
        _log2c(p),
    ),
    ("scan", "chain"): lambda p, n: (p - 1, p - 1, (p - 1) * n, p - 1),
    # Hillis-Steele prefix: in round k the ranks with r + 2^k >= p have no
    # destination, so the message count is p*ceil(log2 p) minus the sum of the
    # skipped sends, sum_k 2^k = 2^ceil(log2 p) - 1.  The naive p*log(p) figure
    # over-counts by nearly p, which matters at the sizes where scan is used.
    ("scan", "recursive_doubling"): lambda p, n: (
        _log2c(p),
        p * _log2c(p) - ((1 << _log2c(p)) - 1),
        (p * _log2c(p) - ((1 << _log2c(p)) - 1)) * n,
        _log2c(p),
    ),
    ("alltoall", "pairwise"): lambda p, n: (p - 1, p * (p - 1), p * (p - 1) * n, 0),
    # `linear` posts all its sends without waiting, which is not the same as completing them in one
    # round: each rank still transfers p-1 messages, so its critical path is p-1, exactly as for
    # `pairwise`. Recording 1 made the model rank `linear` fastest at every size and so recommend
    # it --- the schedule this module's own docstring calls the canonical way to deadlock an AgentMPI
    # program, and the one the sweep measured at 250.6 aggregate receive-blocking rank-seconds at
    # p=32 against 30.6 for `pairwise` and 4.6 for `bruck`.
    #
    # The two are now indistinguishable to this model, which is the honest outcome: they move the
    # same messages over the same critical path and differ in *concurrency pressure*, which an
    # alpha-beta-gamma model does not represent. `linear` requires a rank to hold p-1 unexpected
    # messages at once where `pairwise` requires one, and the sweep only survived it because it ran
    # with a 10^9-token eager limit. Choose between them on that basis, not on predicted time.
    ("alltoall", "linear"): lambda p, n: (p - 1, p * (p - 1), p * (p - 1) * n, 0),
    ("alltoall", "bruck"): lambda p, n: (_log2c(p), p * _log2c(p), p * _log2c(p) * n * p / 2, 0),
    ("reduce_scatter", "linear"): lambda p, n: (1, p * (p - 1), p * (p - 1) * n, p - 1),
}


def predict(
    op: str,
    algorithm: str,
    p: int,
    n_tokens: int,
    params: CostParams | None = None,
    *,
    op_cost_tokens: int = 0,
) -> Prediction:
    """Closed-form prediction for one collective.

    ``op_cost_tokens`` is the output volume of one application of a semantic
    reduction operator; it is what makes a reduction's price scale with the
    number of *operator applications* rather than with the message volume, and
    it is the term that makes ``flat`` reduce so expensive at the root.
    """
    params = params or CostParams()
    key = (op, algorithm)
    if key not in FORMULAS:
        raise KeyError(f"no cost formula for {op}/{algorithm}")
    rounds, messages, volume, depth = FORMULAS[key](p, n_tokens)
    rounds, messages, volume, depth = int(rounds), int(messages), int(volume), int(depth)
    # Critical-path time: each round on the critical path pays one α plus the
    # transfer of the payload; a reduction additionally pays one operator
    # application per level of the tree.
    op_rounds = depth if op in ("reduce", "allreduce", "scan", "reduce_scatter") else 0
    time_s = rounds * (params.fabric_s + n_tokens * params.beta_in_s_per_token)
    if op_rounds:
        # Charged for the fold depth, not clamped to the round count. Clamping made flat reduce pay
        # for one operator application when its root performs p-1 of them back to back --- flat's
        # sends land in a single communication round but the fold that follows is left-deep, which
        # is why its measured `fold_depth` is p-1 and not 1. The clamp also put time and price in
        # contradiction, since the price term below charges (p-1) applications, and it made
        # `best_algorithm(objective="time")` recommend flat: at p=8 the agent-executed runs measured
        # root blocking of 51.8 s for k-ary and 251.6 s for flat.
        time_s += op_rounds * params.message_time(op_cost_tokens or n_tokens)
    price = params.message_price(volume, 0)
    if op_cost_tokens:
        price += params.message_price(volume, (p - 1) * op_cost_tokens)
    return Prediction(
        op=op,
        algorithm=algorithm,
        p=p,
        n_tokens=n_tokens,
        rounds=rounds,
        messages=messages,
        volume_tokens=volume,
        time_s=time_s,
        price_usd=price,
        fold_depth=depth,
        fidelity=params.fidelity(depth),
    )


def best_algorithm(op: str, p: int, n_tokens: int, params: CostParams | None = None, *, objective: str = "time") -> str:
    """Pick the cheapest implemented algorithm for ``op`` under ``objective``.

    Exposed so a harness can ask the model rather than guess, and so the paper
    can tabulate the *crossover* points — the (p, n) boundaries at which the
    recommendation changes — which is the classic form of an MPI
    collective-tuning result.
    """
    cands = [alg for (o, alg) in FORMULAS if o == op]
    if not cands:
        raise KeyError(f"unknown collective {op}")
    scored = []
    for alg in cands:
        pr = predict(op, alg, p, n_tokens, params)
        if objective == "time":
            score = pr.time_s
        elif objective == "price":
            score = pr.price_usd
        elif objective == "volume":
            score = float(pr.volume_tokens)
        elif objective == "fidelity":
            score = -pr.fidelity
        else:
            raise ValueError(f"unknown objective {objective}")
        scored.append((score, alg))
    scored.sort()
    return scored[0][1]


# --------------------------------------------------------------- scaling laws


def amdahl(p: int, serial_fraction: float) -> float:
    f = serial_fraction
    return 1.0 / (f + (1.0 - f) / max(1, p))


def gustafson(p: int, serial_fraction: float) -> float:
    return serial_fraction + (1.0 - serial_fraction) * p


def usl(p: int, sigma: float, kappa: float) -> float:
    """Gunther's Universal Scalability Law."""
    return p / (1.0 + sigma * (p - 1) + kappa * p * (p - 1))


def fit_usl(points: Sequence[tuple[int, float]]) -> tuple[float, float, float]:
    """Fit (σ, κ, R²) to measured (p, speedup) points.

    Grid search rather than a nonlinear solver: the parameter space is tiny, the
    objective is well behaved, and a grid has no convergence failures to explain
    in a paper.
    """
    if len(points) < 3:
        return 0.0, 0.0, 0.0
    best = (float("inf"), 0.0, 0.0)
    for si in range(0, 201):
        sigma = si / 200.0
        for ki in range(0, 201):
            kappa = ki / 2000.0
            err = sum((usl(p, sigma, kappa) - s) ** 2 for p, s in points)
            if err < best[0]:
                best = (err, sigma, kappa)
    _, sigma, kappa = best
    mean = sum(s for _, s in points) / len(points)
    sst = sum((s - mean) ** 2 for _, s in points)
    sse = sum((usl(p, sigma, kappa) - s) ** 2 for p, s in points)
    r2 = 1.0 - sse / sst if sst > 0 else 0.0
    return sigma, kappa, r2


def karp_flatt(p: int, speedup: float) -> float:
    """Karp-Flatt metric: the experimentally determined serial fraction.

    Useful because it *increases* with *p* when the overhead is not a fixed
    serial section but a growing coordination cost — which is the signature of
    an agent harness whose collective pattern does not scale, and is exactly what
    a plain speedup plot hides.
    """
    if p <= 1 or speedup <= 0:
        return 0.0
    return (1.0 / speedup - 1.0 / p) / (1.0 - 1.0 / p)


def little(arrival_rate: float, mean_residence_s: float) -> float:
    """Little's Law: the mean number of in-flight agent invocations.

    The sizing rule for an agent pool: to sustain λ tasks/s with a mean agent
    turn of W seconds, λW invocations must be in flight, so the population must
    be at least ⌈λW⌉ or the queue grows without bound.
    """
    return arrival_rate * mean_residence_s


def daly_interval(mttf_s: float, checkpoint_cost_s: float) -> float:
    """Daly's first-order optimal checkpoint interval, √(2·δ·M).

    Included because it is the standard HPC answer to "how often should I
    checkpoint", and because it is the *wrong* answer for agent harnesses: it
    optimises against fail-stop failures, and the dominant agent failure is a
    plausible-but-wrong result that a checkpoint faithfully preserves.  The
    function is provided, and the paper argues that verification budget rather
    than checkpoint interval is the quantity to optimise.
    """
    return math.sqrt(2.0 * checkpoint_cost_s * mttf_s)


@dataclass
class RunSummary:
    """Aggregate measurements extracted from a fabric's event log."""

    wall_s: float
    n_agent_calls: int
    tokens_in: int
    tokens_out: int
    usd: float
    n_messages: int
    tokens_sent: int
    #: Send-side, rendezvous only. A subset of ``tokens_sent`` by construction.
    tokens_deferred: int
    #: Receive-side: arrived but never admitted into a rank's context, any transport mode.
    tokens_unadmitted: int = 0
    collectives: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_rank: dict[int, dict[str, Any]] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)
    context_high_water: int = 0
    n_context_rejections: int = 0
    n_contract_violations: int = 0
    n_failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        lat = sorted(self.latencies)
        return {
            "agent_latency_p50": round(_percentile(lat, 0.50), 2),
            "agent_latency_p95": round(_percentile(lat, 0.95), 2),
            "agent_latency_max": round(lat[-1], 2) if lat else 0.0,
            "wall_s": round(self.wall_s, 2),
            "agent_calls": self.n_agent_calls,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "usd": round(self.usd, 4),
            "messages": self.n_messages,
            "tokens_sent": self.tokens_sent,
            "tokens_deferred": self.tokens_deferred,
            "tokens_unadmitted": self.tokens_unadmitted,
            "context_high_water": self.context_high_water,
            "context_rejections": self.n_context_rejections,
            "contract_violations": self.n_contract_violations,
            "failures": self.n_failures,
            "collectives": self.collectives,
        }


def summarise(source: "Fabric | list[dict[str, Any]]") -> RunSummary:
    """Extract a run summary from the event log alone.

    The event log is authoritative: a summary computed from in-memory counters
    would not survive a crashed rank, and crashed ranks are the normal case.

    Accepts either a live ``Fabric`` or an already-materialised event list, so an
    exported ``traces/events/*.jsonl`` log summarises through exactly this code
    path rather than a reimplementation of it.  That is what lets the archive
    verifier check a fabric and its export for drift: any divergence is real,
    not an artefact of two different summarisers.
    """
    events = source.events() if isinstance(source, Fabric) else list(source)
    if not events:
        return RunSummary(0.0, 0, 0, 0, 0.0, 0, 0, 0)
    t0, t1 = events[0]["ts"], events[-1]["ts"]
    s = RunSummary(wall_s=t1 - t0, n_agent_calls=0, tokens_in=0, tokens_out=0, usd=0.0, n_messages=0, tokens_sent=0, tokens_deferred=0)
    for e in events:
        kind, p = e["kind"], e["payload"]
        if kind == "agent.call":
            s.n_agent_calls += 1
            s.tokens_in += int(p.get("prompt_tokens", 0) or 0)
            s.tokens_out += int(p.get("output_tokens", 0) or 0)
            if p.get("latency_s"):
                s.latencies.append(float(p["latency_s"]))
        elif kind == "msg.send":
            s.n_messages += 1
            s.tokens_sent += int(p.get("tokens", 0) or 0)
            if p.get("mode") == "rendezvous":
                s.tokens_deferred += int(p.get("tokens", 0) or 0)
        elif kind == "msg.recv":
            if not p.get("admitted"):
                s.tokens_unadmitted += int(p.get("tokens", 0) or 0)
        elif kind == "agent.contract_violation":
            s.n_contract_violations += 1
        elif kind in ("ft.declare_failed", "rank.error"):
            s.n_failures += 1
        elif kind == "rank.finalize":
            ctxinfo = p.get("context") or {}
            s.context_high_water = max(s.context_high_water, int(ctxinfo.get("high_water", 0) or 0))
            s.n_context_rejections += int(ctxinfo.get("rejections", 0) or 0)
            s.per_rank[int(e["rank"] or -1)] = {"context": ctxinfo, "cost": p.get("cost") or {}}
        elif kind.startswith("coll."):
            name = kind.split(".", 1)[1]
            bucket = s.collectives.setdefault(
                name, {"n": 0, "algorithms": {}, "rounds": 0, "messages": 0, "tokens": 0, "max_fold_depth": 0, "wall_s": 0.0}
            )
            bucket["n"] += 1
            alg = p.get("algorithm", "?")
            bucket["algorithms"][alg] = bucket["algorithms"].get(alg, 0) + 1
            bucket["rounds"] += int(p.get("rounds", 0) or 0)
            bucket["messages"] += int(p.get("messages_sent", 0) or 0)
            bucket["tokens"] += int(p.get("tokens_sent", 0) or 0)
            bucket["max_fold_depth"] = max(bucket["max_fold_depth"], int(p.get("fold_depth", 0) or 0))
            bucket["wall_s"] += float(p.get("wall_s", 0.0) or 0.0)
    s.usd = s.tokens_in * 3.0 / 1e6 + s.tokens_out * 15.0 / 1e6
    return s
