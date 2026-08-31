"""Tests for the collective catalogue, its costs, and selection.

The schedules are pure data, so their properties can be checked exactly rather
than measured.  Several assertions here are the paper's claims stated as code: if
one of them breaks, a sentence in the paper has become false.
"""

from __future__ import annotations

import math

import pytest

from ampi.core.algorithms import (
    CATALOGUE,
    build_schedule,
    cost_of,
    explain_selection,
    select_algorithm,
)
from ampi.core.ops import APPROX, NONE, Op, get_op
from ampi.errors import AmpiError


@pytest.mark.parametrize("collective", sorted(CATALOGUE))
@pytest.mark.parametrize("p", [1, 2, 3, 5, 8, 16])
def test_every_catalogued_schedule_builds_at_every_size(collective, p):
    """Non-power-of-two sizes are the ones that break schedule generators."""
    for algorithm in CATALOGUE[collective]:
        s = build_schedule(collective, algorithm, p, tokens=1000, inline=False)
        assert s.p == p
        assert all(0 <= t.src < p and 0 <= t.dst < p for r in s.rounds for t in r)
        assert all(t.src != t.dst for r in s.rounds for t in r)


@pytest.mark.parametrize("p", [2, 4, 8, 16, 32])
def test_broadcast_reaches_every_rank_on_every_algorithm(p):
    for algorithm in CATALOGUE["bcast"]:
        s = build_schedule("bcast", algorithm, p, tokens=100, inline=False)
        have = {0}
        for rnd in s.rounds:
            for t in rnd:
                if t.src in have:
                    have.add(t.dst)
        assert have == set(range(p)), f"{algorithm} left ranks uninformed at p={p}"


@pytest.mark.parametrize("p", [2, 4, 8, 16, 32])
def test_reduce_gathers_every_rank_into_the_root(p):
    for algorithm in CATALOGUE["reduce"]:
        s = build_schedule("reduce", algorithm, p, tokens=100, inline=False)
        contributed = {r: {r} for r in range(p)}
        for rnd in s.rounds:
            for t in rnd:
                contributed[t.dst] = contributed[t.dst] | contributed[t.src]
        assert contributed[0] == set(range(p)), f"{algorithm} lost contributions at p={p}"


@pytest.mark.parametrize("p", [4, 8, 16, 64, 128])
def test_binomial_reduce_has_logarithmic_critical_path(p):
    s = build_schedule("reduce", "binomial", p, tokens=100, inline=False)
    assert s.applications == p - 1, "a tree performs the same total work as a chain"
    assert s.critical_path_applications == math.ceil(math.log2(p))


@pytest.mark.parametrize("p", [4, 8, 16, 64, 128])
def test_flat_reduce_serialises_every_application_at_the_root(p):
    """The correction that makes the paper's comparison honest.

    A flat reduction looks like a one-round schedule, but the root applies p-1
    operators back to back, so its critical path is p-1.  With a runtime operator
    that costs nothing and flat is optimal; with an executor operator it is the
    worst choice in the catalogue.
    """
    s = build_schedule("reduce", "flat", p, tokens=100, inline=False)
    assert s.n_rounds == 1
    assert s.critical_path_applications == p - 1


@pytest.mark.parametrize("p", [8, 16, 32, 64, 128])
def test_recursive_doubling_allreduce_costs_p_log_p_applications(p):
    """MPI's short-message optimum, priced in a regime where operators are not free."""
    rd = build_schedule("allreduce", "recursive_doubling", p, tokens=100, inline=False)
    rb = build_schedule("allreduce", "reduce_bcast", p, tokens=100, inline=False)
    assert rd.applications == p * math.ceil(math.log2(p))
    assert rb.applications == p - 1
    assert rd.critical_path_applications == rb.critical_path_applications, (
        "they tie on latency, which is exactly why the price axis has to be reported"
    )
    assert rd.applications / rb.applications >= 3


def test_the_selection_rule_inverts_as_the_operator_gets_expensive():
    """The paper's central claim, as an executable check.

    At gamma = 0 --- MPI's regime, where the operator is a floating-point add ---
    the journal-folding flat schedule wins.  As gamma grows past the per-operation
    latency the tree schedules take over, and by one executor turn the flat
    schedule is an order of magnitude behind.
    """
    rows = {r["gamma_s"]: r for r in explain_selection("allreduce", 64, tokens=4000)}
    assert rows[0.0]["winner"] == "flat"
    assert rows[30.0]["winner"] != "flat"
    assert rows[30.0]["flat"] > 8 * rows[30.0]["reduce_bcast"]


def test_an_agent_operator_selects_a_tree_and_a_runtime_operator_selects_flat():
    agent = Op("agent:merge", None, associativity=APPROX, evaluator="agent", deterministic=False)
    assert select_algorithm("allreduce", 64, tokens=4000, op=agent).chosen == "reduce_bcast"
    assert select_algorithm("allreduce", 64, tokens=4000, op=get_op("union")).chosen == "flat"


def test_a_near_tie_on_latency_is_broken_on_price():
    """Recursive doubling ties reduce-then-broadcast on latency and costs 6x."""
    det = Op("agent:det", None, associativity=APPROX, evaluator="agent", deterministic=True)
    d = select_algorithm("allreduce", 64, tokens=4000, op=det)
    by_name = {c.algorithm: c for c in d.considered}
    assert by_name["recursive_doubling"].total_seconds <= by_name["reduce_bcast"].total_seconds
    assert by_name["recursive_doubling"].price_units > 5 * by_name["reduce_bcast"].price_units
    assert d.chosen == "reduce_bcast", "the cheaper of two schedules that finish together"


def test_selection_refuses_a_schedule_the_operators_algebra_forbids():
    op = Op("agent:sequential", None, associativity=NONE, evaluator="agent")
    d = select_algorithm("reduce", 16, tokens=1000, op=op)
    assert d.chosen == "chain"
    assert "binomial" in d.rejected
    assert "associativity=NONE" in d.rejected["binomial"]


def test_an_inline_allgather_is_rejected_as_infeasible_not_merely_slow():
    """The constraint MPI does not have.

    At p=128 with 4000-token contributions an inlining allgather charges one rank
    508,000 tokens.  No context window admits it, so the right answer is a
    rejection with a reason, not a slower schedule.
    """
    with pytest.raises(AmpiError) as e:
        select_algorithm("allgather", 128, tokens=4000, inline=True, ctx_limit=128_000)
    assert e.value.cls_name == "AMPI_ERR_CTX_EXCEEDED"
    assert all("peak residency" in v for v in e.value.detail["rejected"].values())


def test_the_same_allgather_is_admissible_over_handles():
    d = select_algorithm("allgather", 128, tokens=4000, inline=False, ctx_limit=128_000)
    peak = {c.algorithm: c.peak_resident_tokens for c in d.considered}
    assert max(peak.values()) < 128_000
    assert d.chosen in CATALOGUE["allgather"]


def test_handles_reduce_allgather_peak_residency_by_two_orders_of_magnitude():
    inline = build_schedule("allgather", "ring", 128, tokens=4000, inline=True)
    handle = build_schedule("allgather", "ring", 128, tokens=4000, inline=False)
    assert inline.peak_resident() / handle.peak_resident() == pytest.approx(100, rel=0.01)


def test_scan_trades_operator_work_for_rounds_in_the_direction_mpi_does_not():
    """Recursive-doubling scan buys ceil(log p) rounds with about p log p applications.

    That is the right trade only when per-invocation latency dominates, and it is
    the wrong one when the invocation *is* the cost.  Both numbers are reported so
    a harness can choose knowingly.
    """
    p = 32
    chain = build_schedule("scan", "chain", p, tokens=500, inline=False)
    rd = build_schedule("scan", "recursive_doubling", p, tokens=500, inline=False)
    assert chain.n_rounds == p - 1
    assert rd.n_rounds == math.ceil(math.log2(p))
    assert chain.applications == p - 1
    assert rd.applications > 3 * chain.applications


def test_a_shared_control_plane_collapses_the_barrier_hierarchy():
    """A place where the transplant changes the answer, and we say so.

    In MPI a counting barrier is avoided at scale because the root is a real
    bottleneck on a point-to-point network, so dissemination's logarithmic round
    count wins as p grows.  Here the control plane is a shared medium every rank
    can read, so counting costs 2(p-1) device operations against dissemination's
    p*log2(p) -- cheaper at every size -- and it can additionally name the ranks
    that have not arrived, which is the single most useful diagnostic a wedged
    agent job can produce.
    """
    for p in (8, 64, 256):
        d = select_algorithm("barrier", p)
        assert d.chosen == "central"
        assert "have not arrived" in d.rule
        by_name = {c.algorithm: c for c in d.considered}
        assert by_name["central"].messages < by_name["dissemination"].messages


def test_dissemination_barrier_is_correct_for_non_powers_of_two():
    for p in (3, 5, 6, 7, 9, 13):
        s = build_schedule("barrier", "dissemination", p)
        assert s.n_rounds == math.ceil(math.log2(p))
        knows = {r: {r} for r in range(p)}
        for rnd in s.rounds:
            nxt = {r: set(v) for r, v in knows.items()}
            for t in rnd:
                nxt[t.dst] |= knows[t.src]
            knows = nxt
        assert all(knows[r] == set(range(p)) for r in range(p))


def test_cost_reports_both_time_and_price():
    s = build_schedule("allreduce", "recursive_doubling", 32, tokens=2000, inline=False)
    c = cost_of(s, gamma_s=10.0)
    assert c.operator_seconds == pytest.approx(c.critical_path_applications * 10.0)
    assert c.price_units == s.applications
    assert c.total_seconds > c.operator_seconds


def test_an_override_is_honoured_but_still_checked_against_the_algebra():
    d = select_algorithm("reduce", 8, tokens=100, override="chain")
    assert d.chosen == "chain" and d.rule == "caller override"
    op = Op("nonassoc", lambda a, b: a, associativity=NONE)
    with pytest.raises(AmpiError):
        select_algorithm("reduce", 8, tokens=100, op=op, override="binomial")
