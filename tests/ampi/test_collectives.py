"""Collective semantics, algorithm equivalence, and the decision function."""

from __future__ import annotations

import pytest

from ampi.constants import (
    ALGO_BINOMIAL,
    ALGO_DISSEMINATION,
    ALGO_LINEAR,
    ALGO_RABENSEIFNER,
    ALGO_RECURSIVE_DOUBLING,
    ALGO_RING,
    COLL_ALLREDUCE,
)
from ampi.core import collectives as coll
from ampi.core.ops import get_op, op_create
from ampi.errors import AmpiArgError, AmpiCollectiveMismatch


@pytest.mark.parametrize("p,algo", [(4, ALGO_LINEAR), (8, ALGO_DISSEMINATION),
                                    (7, ALGO_DISSEMINATION)])
def test_barrier(make_job, p, algo):
    job = make_job(p)
    out = job.run_ranks(lambda rt, r: coll.barrier(rt, "world", algo=algo, timeout=60))
    assert all(v["ok"] for v in out.values())
    assert {v["algo"] for v in out.values()} == {algo}


@pytest.mark.parametrize("p,algo", [(8, ALGO_BINOMIAL), (8, ALGO_LINEAR), (5, ALGO_BINOMIAL)])
def test_bcast(make_job, p, algo):
    job = make_job(p)
    payload = {"style": "formal", "glossary_version": 3}
    out = job.run_ranks(
        lambda rt, r: coll.bcast(rt, "world", 2, payload if r == 2 else None, algo=algo,
                                 timeout=60)["result"]
    )
    assert all(v == payload for v in out.values())


@pytest.mark.parametrize("p", [4, 8])
def test_reduce_tree_matches_linear_for_ordered_concat(make_job, p):
    """An associative, non-commutative operator must give the same answer
    under a binomial tree as under a canonical linear fold."""
    tree_job = make_job(p)
    tree = tree_job.run_ranks(
        lambda rt, r: coll.reduce_(rt, "world", 0, f"chapter-{r}", "AMPI_CONCAT",
                                   algo=ALGO_BINOMIAL, timeout=60)["result"]
    )[0]
    lin_job = make_job(p)
    linear = lin_job.run_ranks(
        lambda rt, r: coll.reduce_(rt, "world", 0, f"chapter-{r}", "AMPI_CONCAT",
                                   algo=ALGO_LINEAR, timeout=60)["result"]
    )[0]
    expected = "\n".join(f"chapter-{i}" for i in range(p))
    assert tree == expected
    assert linear == expected


@pytest.mark.parametrize("p,algo", [
    (8, ALGO_RECURSIVE_DOUBLING),
    (8, ALGO_RING),
    (8, ALGO_RABENSEIFNER),
    (6, ALGO_RECURSIVE_DOUBLING),
    (6, ALGO_RING),
])
def test_allreduce_algorithms_agree(make_job, p, algo):
    """Every admissible allreduce algorithm must compute the same glossary.

    This is the collective-substitutability property that makes the
    interface/algorithm split worth having: the harness says allreduce, the
    runtime picks, and the answer does not change.
    """
    job = make_job(p)

    def body(rt, r):
        contribution = {f"term{(r * 3 + i) % 12}": f"gloss-{(r * 3 + i) % 12}" for i in range(4)}
        contribution[f"own{r}"] = f"only-rank-{r}"
        return coll.allreduce(rt, "world", contribution, "AMPI_MERGE_JSON", algo=algo,
                              timeout=120)["result"]

    out = job.run_ranks(body, timeout=180)
    expected: dict[str, str] = {}
    for r in range(p):
        for i in range(4):
            k = (r * 3 + i) % 12
            expected[f"term{k}"] = f"gloss-{k}"
        expected[f"own{r}"] = f"only-rank-{r}"
    for rank, result in out.items():
        assert result == expected, f"rank {rank} disagreed under {algo}"


def test_allgather(make_job):
    p = 6
    job = make_job(p)
    out = job.run_ranks(
        lambda rt, r: coll.allgather(rt, "world", {"rank": r, "note": f"n{r}"}, timeout=90)["result"]
    )
    expected = {str(i): {"rank": i, "note": f"n{i}"} for i in range(p)}
    for rank, got in out.items():
        assert got == expected, f"rank {rank} saw {got}"


def test_alltoall(make_job):
    p = 4
    job = make_job(p)

    def body(rt, r):
        blocks = [f"from{r}to{j}" for j in range(p)]
        return coll.alltoall(rt, "world", blocks, timeout=90)["result"]

    out = job.run_ranks(body)
    for r in range(p):
        got = out[r]
        for src in range(p):
            assert got[str(src)] == [f"from{src}to{r}"], f"rank {r} block {src}: {got[str(src)]}"


def test_scan_is_an_ordered_prefix(make_job):
    p = 5
    job = make_job(p)
    out = job.run_ranks(
        lambda rt, r: coll.scan(rt, "world", f"s{r}", "AMPI_CONCAT", timeout=60)["result"]
    )
    for r in range(p):
        assert out[r] == "\n".join(f"s{i}" for i in range(r + 1))


def test_gather_and_scatter(make_job):
    p = 4
    gather_job = make_job(p)
    got = gather_job.run_ranks(
        lambda rt, r: coll.gather(rt, "world", 0, {"payload": r}, timeout=60)["result"]
    )
    assert got[0] == {str(i): {"payload": i} for i in range(p)}

    scatter_job = make_job(p)
    blocks = {str(i): f"chunk-{i}" for i in range(p)}
    out = scatter_job.run_ranks(
        lambda rt, r: coll.scatter(rt, "world", 0, blocks if r == 0 else None,
                                   timeout=60)["result"]
    )
    assert out == {i: f"chunk-{i}" for i in range(p)}


def test_vote_detects_disagreement(make_job):
    """AMPI_VOTE is the agent analogue of algorithm-based fault tolerance:
    a minority that disagrees is reported rather than averaged away."""
    p = 5
    job = make_job(p)

    def body(rt, r):
        answer = "42" if r != 3 else "41"
        return coll.allreduce(rt, "world", [answer], "AMPI_VOTE", timeout=60)["result"]

    out = job.run_ranks(body)
    for result in out.values():
        assert result["winner"] == "42"
        assert result["votes"] == 4
        assert result["total"] == 5
        assert result["unanimous"] is False


def test_collective_mismatch_is_diagnosed(make_job):
    """A rank that issues the wrong collective gets an error, not a hang.

    MPI declares this undefined behaviour.  With LLM ranks it is a routine
    event, so the runtime has to name it.
    """
    job = make_job(3)

    def body(rt, r):
        if r == 2:
            with pytest.raises(AmpiCollectiveMismatch):
                coll.bcast(rt, "world", 0, None, timeout=20)
            return "diagnosed"
        try:
            coll.barrier(rt, "world", timeout=20)
        except Exception as exc:  # the healthy ranks time out or are unblocked
            return type(exc).__name__
        return "barrier-ok"

    out = job.run_ranks(body, timeout=120)
    assert out[2] == "diagnosed"


# ---------------------------------------------------------------------------
# The decision function
# ---------------------------------------------------------------------------


def test_non_associative_operator_forces_linear_order():
    op = op_create("TEST_NONASSOC", lambda a, b: f"{a}|{b}", associative=False,
                   commutative=False, doc="test")
    algo, considered = coll.select_algorithm(COLL_ALLREDUCE, 16, 500, op, 100_000, vector=True)
    assert algo == ALGO_LINEAR
    rejected = {c["algo"]: c["reason"] for c in considered if not c["admissible"]}
    assert ALGO_RECURSIVE_DOUBLING in rejected
    assert "associative" in rejected[ALGO_RECURSIVE_DOUBLING]


def test_non_commutative_operator_rejects_reduce_scatter():
    op = get_op("AMPI_CONCAT")
    _, considered = coll.select_algorithm(COLL_ALLREDUCE, 8, 500, op, 100_000, vector=True)
    by_algo = {c["algo"]: c for c in considered}
    assert by_algo[ALGO_RABENSEIFNER]["admissible"] is False
    assert "commutative" in by_algo[ALGO_RABENSEIFNER]["reason"]
    assert by_algo[ALGO_RECURSIVE_DOUBLING]["admissible"] is True


def test_context_bound_rules_out_recursive_doubling():
    """The headline feasibility result.

    With a payload larger than half a rank's context window, every algorithm
    that materialises the whole vector at every rank becomes inadmissible, and
    only the reduce-scatter family survives.  In MPI this is a constant-factor
    bandwidth argument; here it decides whether the operation can run at all.
    """
    op = get_op("AMPI_MERGE_JSON")
    ctx = 60_000
    algo, considered = coll.select_algorithm(COLL_ALLREDUCE, 8, 120_000, op, ctx, vector=True)
    by_algo = {c["algo"]: c for c in considered}
    assert by_algo[ALGO_RECURSIVE_DOUBLING]["admissible"] is False
    assert "context limit" in by_algo[ALGO_RECURSIVE_DOUBLING]["reason"]
    assert algo in (ALGO_RABENSEIFNER, ALGO_RING)
    assert by_algo[algo]["peak_resident"] <= ctx


def test_decision_function_reports_no_admissible_algorithm():
    op = get_op("AMPI_MERGE_JSON")
    with pytest.raises(AmpiArgError):
        coll.select_algorithm(COLL_ALLREDUCE, 8, 10_000_000, op, 1_000, vector=True)


@pytest.mark.parametrize("p,algo", [(8, ALGO_RECURSIVE_DOUBLING), (8, ALGO_RING),
                                    (6, ALGO_RING)])
def test_reduce_scatter_partitions_the_key_space(make_job, p, algo):
    """Every key ends up owned by exactly one rank, fully reduced.

    Reduce-scatter is the operation the context-bound feasibility argument
    rests on, so its correctness matters more than its speed: the union of what
    the ranks hold afterwards must equal what an allreduce would have produced,
    and the blocks must be disjoint.
    """
    job = make_job(p)

    def body(rt, r):
        contribution = {f"term{(r * 3 + i) % 15}": f"g{(r * 3 + i) % 15}" for i in range(5)}
        contribution[f"own{r}"] = f"only{r}"
        return coll.reduce_scatter(rt, "world", contribution, "AMPI_MERGE_JSON", algo=algo,
                                   timeout=120, datatype="vector")["result"]

    out = job.run_ranks(body, timeout=180)
    expected: dict[str, str] = {}
    for r in range(p):
        for i in range(5):
            k = (r * 3 + i) % 15
            expected[f"term{k}"] = f"g{k}"
        expected[f"own{r}"] = f"only{r}"

    union: dict[str, str] = {}
    for rank, block in out.items():
        assert isinstance(block, dict), f"rank {rank} got {block!r}"
        for key in block:
            assert key not in union, f"key {key} is owned by two ranks"
        union.update(block)
    assert union == expected


def test_reduce_scatter_holds_less_than_allreduce(make_job):
    """The residency separation, asserted rather than only measured.

    An allreduce that materialises the vector everywhere must hold more than a
    reduce-scatter of the same data, and the gap must widen with p. This is the
    property that decides whether a reduction larger than one context window
    can run at all.
    """
    p = 8
    payload_keys = 60

    ar_job = make_job(p)
    rs_job = make_job(p)

    def contribution(r):
        return {f"k{r}-{i}": "v" * 60 for i in range(payload_keys)}

    def allreduce_body(rt, r):
        return coll.allreduce(rt, "world", contribution(r), "AMPI_MERGE_JSON",
                              algo=ALGO_RECURSIVE_DOUBLING, timeout=180,
                              datatype="vector")["peak_resident_tokens"]

    def scatter_body(rt, r):
        return coll.reduce_scatter(rt, "world", contribution(r), "AMPI_MERGE_JSON",
                                   algo=ALGO_RECURSIVE_DOUBLING, timeout=180,
                                   datatype="vector")["peak_resident_tokens"]

    allreduce_peak = max(ar_job.run_ranks(allreduce_body, timeout=300).values())
    scatter_peak = max(rs_job.run_ranks(scatter_body, timeout=300).values())
    assert scatter_peak * 2 < allreduce_peak, (
        f"reduce-scatter held {scatter_peak} tokens, allreduce held {allreduce_peak}; "
        "the reduce-scatter family must hold substantially less"
    )
