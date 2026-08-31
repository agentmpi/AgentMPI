"""Tests for the operator algebra, and for the shape-independence of conflict lifting.

The central claim of :mod:`ampi.core.ops` is a small theorem: because the conflict
component merges by set union --- associative, commutative, idempotent --- the set
of conflicts reaching the root of a reduction does not depend on the shape of the
tree, even though the *value* component's result does.  That is what buys back the
"one ruling per key" invariant that a locally-deciding merge cannot maintain.

A theorem in a paper is worth what its counterexample search is worth, so the
shape independence is checked exhaustively over every binary tree shape at small
p, and over random shapes at larger p.
"""

from __future__ import annotations

import hashlib
import json
import math
import random

import pytest

from ampi.core.ops import (
    APPROX,
    CONFLICT_KEY,
    EXACT,
    LIFT,
    NONE,
    Op,
    arbitrate,
    check_invariant,
    conflicts_of,
    fold,
    get_op,
    items_conserved,
    keys_conserved,
    serial_fold,
    value_of,
)
from ampi.errors import AmpiError


# --------------------------------------------------------------------------
# The algebra gates the schedule
# --------------------------------------------------------------------------


def test_a_non_associative_operator_refuses_a_tree():
    op = Op("merge", lambda a, b: a, associativity=NONE)
    with pytest.raises(AmpiError) as e:
        op.check_schedule("binomial")
    assert e.value.cls_name == "AMPI_ERR_OP_UNSOUND"
    assert "chain" in e.value.hint


def test_a_non_commutative_operator_refuses_a_tree_not_rooted_at_zero():
    op = Op("concat", lambda a, b: a + b, associativity=EXACT, commutative=False)
    op.check_schedule("binomial", root=0)
    with pytest.raises(AmpiError) as e:
        op.check_schedule("binomial", root=3)
    assert "rank order" in e.value.message


def test_a_non_deterministic_operator_refuses_recursive_doubling():
    """The divergence hazard: p ranks would obtain p different 'agreed' values."""
    op = Op("agent:merge", None, associativity=APPROX, evaluator="agent", deterministic=False)
    with pytest.raises(AmpiError) as e:
        op.check_schedule("recursive_doubling")
    assert "disagree about the agreed value" in e.value.message
    assert "reduce_bcast" in e.value.hint


def test_an_associative_commutative_operator_licenses_everything():
    op = get_op("union")
    for algorithm in ("chain", "binomial", "recursive_doubling"):
        op.check_schedule(algorithm, root=2)


# --------------------------------------------------------------------------
# Exactness: every schedule must reproduce the serial fold
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["sum", "max", "min", "and", "or", "bag", "union"])
@pytest.mark.parametrize("algorithm", ["chain", "binomial"])
def test_exact_operators_agree_with_the_serial_fold(name, algorithm):
    op = get_op(name)
    if name in ("bag", "union"):
        values = [{"k%d" % i: i} for i in range(8)] if name == "union" else [[i] for i in range(8)]
    else:
        values = [i + 1 for i in range(8)] if name in ("sum", "max", "min") else [True] * 8
    got = fold(op, list(values), algorithm=algorithm)["value"]
    assert got == serial_fold(op, list(values))


def test_fold_depth_distinguishes_chain_from_tree():
    op = get_op("sum")
    values = list(range(1, 65))
    chain = fold(op, values, algorithm="chain")
    tree = fold(op, values, algorithm="binomial")
    assert chain["value"] == tree["value"] == sum(values)
    assert chain["applications"] == tree["applications"] == 63
    assert chain["depth"] == 63
    assert tree["depth"] == 6, "the same work, one tenth the depth"


def test_recursive_doubling_costs_p_log_p_applications():
    """MPI's short-message optimum, and the reason it is wrong for agents.

    Redundant arithmetic is free in MPI.  When one application is an executor
    spending a minute, ``p log p`` against ``p-1`` is the difference between a
    reduction that finishes and one that does not.
    """
    op = get_op("sum")
    for p in (8, 16, 32, 64, 128):
        values = list(range(p))
        rd = fold(op, values, algorithm="recursive_doubling")
        tree = fold(op, values, algorithm="binomial")
        assert rd["value"] == tree["value"] == sum(values)
        assert rd["applications"] == p * rd["depth"] == p * int(math.log2(p))
        assert tree["applications"] == p - 1
        # The ratio is (p log p)/(p-1), which grows without bound: 3.4x at p=8,
        # 7.1x at p=128.  MPI accepts it because the applications are additions.
        assert rd["applications"] / tree["applications"] > 3.0


# --------------------------------------------------------------------------
# Union retains disagreement rather than deciding it
# --------------------------------------------------------------------------


def test_union_retains_a_disagreement_instead_of_letting_the_tree_decide():
    op = get_op("union")
    a = {"exception_type": "errors.py", "parser": "parse.py"}
    b = {"exception_type": "core.py", "lexer": "lex.py"}
    merged = op.fn(a, b)
    assert conflicts_of(merged) == {"exception_type": ["errors.py", "core.py"]}
    assert "exception_type" not in value_of(merged), "a contested key must not be decided locally"
    assert value_of(merged)["parser"] == "parse.py"


def test_union_agreement_is_not_a_conflict():
    op = get_op("union")
    merged = op.fn({"k": 1}, {"k": 1})
    assert conflicts_of(merged) == {}
    assert value_of(merged) == {"k": 1}


# --------------------------------------------------------------------------
# The theorem: the conflict set at the root is tree-shape independent
# --------------------------------------------------------------------------


def _all_shapes(values):
    """Every distinct binary fold order over ``values``, preserving order."""
    if len(values) == 1:
        yield values[0]
        return
    for split in range(1, len(values)):
        for left in _all_shapes(values[:split]):
            for right in _all_shapes(values[split:]):
                yield ("node", left, right)


def _evaluate(shape, op):
    if not (isinstance(shape, tuple) and shape and shape[0] == "node"):
        return shape
    return op.fn(_evaluate(shape[1], op), _evaluate(shape[2], op))


@pytest.mark.parametrize("p", [2, 3, 4, 5, 6])
def test_conflict_set_is_identical_over_every_tree_shape(p):
    """Exhaustive over all Catalan(p-1) fold orders.

    This is the property that makes lifting worth doing.  The value component's
    result legitimately differs between shapes for a lossy operator; the conflict
    component's does not, because a semilattice fold is shape-independent.
    """
    op = get_op("union")
    # Leaves disagree pairwise about `shared`, and each contributes a private key,
    # so a locally-deciding merge would produce a shape-dependent ruling.
    leaves = [{"shared": f"v{i % 3}", f"own{i}": i} for i in range(p)]

    conflict_sets = set()
    value_keys = set()
    for shape in _all_shapes(leaves):
        merged = _evaluate(shape, op)
        conflict_sets.add(json.dumps(conflicts_of(merged), sort_keys=True))
        value_keys.add(tuple(sorted(value_of(merged))))

    assert len(conflict_sets) == 1, "the conflict set must not depend on the fold order"
    assert len(value_keys) == 1, "and with lifting, neither does the set of decided keys"


@pytest.mark.parametrize("p", [8, 16, 32])
def test_conflict_set_is_shape_independent_at_scale(p):
    op = get_op("union")
    rng = random.Random(20260831)
    leaves = [{"shared": f"v{i % 4}", "also": f"w{i % 2}", f"own{i}": i} for i in range(p)]

    def random_shape(items):
        items = list(items)
        while len(items) > 1:
            i = rng.randrange(len(items) - 1)
            items[i : i + 2] = [op.fn(items[i], items[i + 1])]
        return items[0]

    seen = {json.dumps(conflicts_of(random_shape(leaves)), sort_keys=True) for _ in range(40)}
    chain = json.dumps(conflicts_of(serial_fold(op, list(leaves))), sort_keys=True)
    assert seen == {chain}


def test_arbitration_decides_each_conflict_exactly_once():
    op = get_op("union")
    leaves = [{"owner": "a"}, {"owner": "b"}, {"owner": "a"}, {"other": 1}]
    merged = serial_fold(op, leaves)
    assert set(conflicts_of(merged)) == {"owner"}
    resolved, rulings = arbitrate(merged)
    assert set(rulings) == {"owner"}
    assert resolved["owner"] == "a", "the modal candidate wins by default"
    assert CONFLICT_KEY not in resolved


def test_arbitration_accepts_a_harness_supplied_arbiter():
    op = get_op("union")
    merged = serial_fold(op, [{"k": "x"}, {"k": "y"}])
    resolved, rulings = arbitrate(merged, decide=lambda key, cands: f"decided:{sorted(cands)}")
    assert resolved["k"] == "decided:['x', 'y']"
    assert rulings == {"k": "decided:['x', 'y']"}


def _simulated_agent_merge(a, b):
    """A faithful model of an agent operator resolving a collision locally.

    The decision is a well-defined function of the two operands the merge can see
    --- which is exactly the constraint a real executor is under --- but it is not
    a function of anything else, so it is not associative.  Two branches of a tree
    that meet the same conflict decide it independently, and may decide it
    differently.  That is the observed failure, reproduced deterministically.
    """
    out = dict(a)
    for k, v in b.items():
        if k in out and out[k] != v:
            fingerprint = hashlib.sha256(
                (json.dumps(a, sort_keys=True) + json.dumps(b, sort_keys=True) + k).encode()
            ).digest()[0]
            out[k] = v if fingerprint % 2 else out[k]
        else:
            out[k] = v
    return out


def test_a_locally_deciding_merge_is_shape_dependent_which_is_the_point():
    """The control arm for the lifting claim.

    An operator that decides collisions from its two operands alone gives
    different answers for different fold orders, and no node of the tree is in a
    position to notice: each merge saw a locally consistent pair.
    """
    op = Op("agentish", _simulated_agent_merge, associativity=APPROX, deterministic=False)
    leaves = [{"owner": f"m{i}", f"k{i}": i} for i in range(5)]
    results = {
        json.dumps(_evaluate(s, op), sort_keys=True) for s in _all_shapes(leaves)
    }
    owners = {json.loads(r)["owner"] for r in results}
    assert len(owners) > 1, "a locally-deciding merge resolves the same conflict differently"


def test_lifting_removes_the_shape_dependence_the_control_exhibits():
    """The treatment, on the identical leaves."""
    leaves = [{"owner": f"m{i}", f"k{i}": i} for i in range(5)]
    op = get_op("union")
    conflict_sets = {
        json.dumps(conflicts_of(_evaluate(s, op)), sort_keys=True) for s in _all_shapes(leaves)
    }
    assert len(conflict_sets) == 1
    assert set(json.loads(next(iter(conflict_sets)))) == {"owner"}


# --------------------------------------------------------------------------
# Invariant verification: the failure lifting cannot see
# --------------------------------------------------------------------------


def test_keys_conserved_catches_a_reduction_that_dropped_contributions():
    """Every local merge was faithful; the global result was not.

    This is the shape of "it dropped four of eight modules despite an explicit
    instruction never to drop a module".  No pair of operands exhibits a conflict,
    so lifting is blind to it; only a check against the leaf multiset sees it.
    """
    op = Op("lossy", lambda a, b: {**a, **b}, associativity=APPROX, invariant=keys_conserved())
    leaves = [{f"module{i}": f"spec{i}"} for i in range(8)]
    faithful = {k: v for leaf in leaves for k, v in leaf.items()}
    assert check_invariant(op, leaves, faithful) == []

    lossy = {k: v for k, v in list(faithful.items())[:4]}
    violations = check_invariant(op, leaves, lossy)
    assert len(violations) == 1
    assert "module7" in violations[0]


def test_items_conserved_checks_a_named_list_field():
    op = Op("summarise", lambda a, b: a, associativity=APPROX, invariant=items_conserved("findings"))
    leaves = [{"findings": [f"f{i}"]} for i in range(5)]
    assert check_invariant(op, leaves, {"findings": [f"f{i}" for i in range(5)]}) == []
    assert check_invariant(op, leaves, {"findings": ["f0"]}) != []


def test_lifted_conflicts_count_as_present_for_conservation():
    """A key the root has yet to arbitrate has not been dropped.

    Without this the two mechanisms would fight: lifting removes a contested key
    from the value component, and a naive conservation check would then report it
    as lost.
    """
    op = Op("u", get_op("union").fn, associativity=EXACT, invariant=keys_conserved())
    leaves = [{"k": "a"}, {"k": "b"}]
    merged = serial_fold(get_op("union"), leaves)
    assert check_invariant(op, leaves, merged) == []


# --------------------------------------------------------------------------
# Vote reports a fraction, not a boolean
# --------------------------------------------------------------------------


def test_vote_reports_the_consensus_fraction_not_just_a_winner():
    from ampi.core.ops import finalise_vote

    op = get_op("vote")
    result = finalise_vote(serial_fold(op, ["yes", "yes", "no", "yes"]))
    assert result["winner"] == "yes"
    assert result["votes"] == 4
    assert result["distinct"] == 2
    assert result["consensus"] == pytest.approx(0.75)
