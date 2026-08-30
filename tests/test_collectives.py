"""Collective algorithms: correctness across algorithms, roots and sizes.

The central invariant tested here is the one that makes the design defensible:
for an *exact* operator, every algorithm must produce the result of the serial
left fold (:meth:`agentmpi.ops.Op.fold`).  If a tree algorithm and the reference
fold disagree, either the algorithm is wrong or the operator was mis-declared,
and both are bugs worth failing loudly on.  The tests also check that the
measured message counts agree with the closed-form cost formulas in
:mod:`agentmpi.cost`, which is how an implementation/model mismatch gets caught.
"""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi import algorithms
from agentmpi.constants import Associativity

SIZES = [1, 2, 3, 4, 5, 7, 8, 11, 16]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["dissemination", "linear", "central"])
def test_barrier_completes(tmp_path, size, algorithm):
    def rank_main(comm):
        res = comm.barrier(algorithm=algorithm, timeout=60.0, policy="wait" if algorithm != "central" else "proceed")
        assert res.complete, res
        return res.algorithm

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"b{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["flat", "binomial", "chain"])
@pytest.mark.parametrize("root", [0, 1])
def test_bcast_delivers_identical_content(tmp_path, size, algorithm, root):
    """Broadcast must be byte-identical at every rank, for any root or tree."""
    if root >= size:
        pytest.skip("root outside communicator")
    payload = {"spec": "translate faithfully", "terms": ["Hoefler", "Gropp"], "n": 7}

    def rank_main(comm):
        got = comm.bcast(payload if comm.rank == root else None, root=root, algorithm=algorithm)
        assert got == payload, (comm.rank, got)
        return got

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"bc{size}{algorithm}{root}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert all(o.value == payload for o in job.outcomes)


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["linear", "binomial"])
@pytest.mark.parametrize("root", [0, 2])
def test_scatter_gather_roundtrip(tmp_path, size, algorithm, root):
    if root >= size:
        pytest.skip("root outside communicator")
    items = [{"unit": i, "text": f"chunk {i}"} for i in range(size)]

    def rank_main(comm):
        mine = comm.scatter(items if comm.rank == root else None, root=root, algorithm=algorithm)
        assert mine == items[comm.rank], (comm.rank, mine)
        out = comm.gather({"unit": mine["unit"], "done": True}, root=root, algorithm=algorithm)
        if comm.rank == root:
            assert out is not None and len(out) == size
            assert [o["unit"] for o in out] == list(range(size)), out
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"sg{size}{algorithm}{root}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["ring", "bruck", "recursive_doubling", "gather_bcast"])
def test_allgather_rank_ordered(tmp_path, size, algorithm):
    def rank_main(comm):
        out = comm.allgather({"r": comm.rank}, algorithm=algorithm)
        assert out == [{"r": i} for i in range(comm.size)], (comm.rank, out)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"ag{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["chain", "flat", "binomial"])
def test_reduce_matches_serial_fold(tmp_path, size, algorithm):
    """Every reduce algorithm must equal the reference fold for an exact op."""
    expected = ampi.SUM.fold(range(size))

    def rank_main(comm):
        out = comm.reduce(comm.rank, ampi.SUM, root=0, algorithm=algorithm)
        if comm.rank == 0:
            assert out == expected, (algorithm, out, expected)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"rd{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["chain", "flat", "binomial"])
def test_reduce_preserves_order_for_noncommutative_op(tmp_path, size, algorithm):
    """CONCAT is associative but not commutative: order must be rank order.

    This is the property MPI guarantees for user operators declared
    non-commutative, and it is the property a tree implementation is most likely
    to break, because a naive tree combines whichever child arrives first.
    """
    expected = "".join(f"[{i}]" for i in range(size))

    def rank_main(comm):
        out = comm.reduce(f"[{comm.rank}]", ampi.CONCAT, root=0, algorithm=algorithm)
        if comm.rank == 0:
            assert out == expected, (algorithm, out, expected)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"cc{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["reduce_bcast", "recursive_doubling"])
def test_allreduce_agrees_everywhere(tmp_path, size, algorithm):
    expected = sum(range(size))

    def rank_main(comm):
        out = comm.allreduce(comm.rank, ampi.SUM, algorithm=algorithm)
        assert out == expected, (comm.rank, algorithm, out, expected)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"ar{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", SIZES)
def test_allreduce_union_merges_glossaries(tmp_path, size):
    """UNION is exact, commutative and idempotent: the well-behaved merge."""

    def rank_main(comm):
        mine = {f"term{comm.rank}": f"rendering{comm.rank}", "shared": "agreed"}
        out = comm.allreduce(mine, ampi.UNION)
        assert out["shared"] == "agreed"
        assert len(out) == comm.size + 1, out
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"un{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    values = [o.value for o in job.outcomes]
    assert all(v == values[0] for v in values), "UNION allreduce must not diverge across ranks"


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("algorithm", ["chain", "recursive_doubling"])
def test_scan_inclusive_and_exclusive(tmp_path, size, algorithm):
    def rank_main(comm):
        inc = comm.scan(comm.rank + 1, ampi.SUM, algorithm=algorithm)
        assert inc == sum(range(1, comm.rank + 2)), (comm.rank, algorithm, inc)
        exc = comm.scan(comm.rank + 1, ampi.SUM, exclusive=True, algorithm=algorithm)
        assert exc == sum(range(1, comm.rank + 1)), (comm.rank, algorithm, exc)
        return inc

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"sc{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [2, 3, 4, 5, 8])
@pytest.mark.parametrize("algorithm", ["pairwise", "linear", "bruck"])
def test_alltoall(tmp_path, size, algorithm):
    def rank_main(comm):
        send = [f"{comm.rank}->{j}" for j in range(comm.size)]
        got = comm.alltoall(send, algorithm=algorithm)
        assert got == [f"{i}->{comm.rank}" for i in range(comm.size)], (comm.rank, algorithm, got)
        return got

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"a2a{size}{algorithm}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [2, 3, 4, 6])
def test_reduce_scatter_partitions_the_fan_in(tmp_path, size):
    def rank_main(comm):
        send = [comm.rank * 10 + j for j in range(comm.size)]
        got = comm.reduce_scatter(send, ampi.SUM)
        expected = sum(i * 10 + comm.rank for i in range(comm.size))
        assert got == expected, (comm.rank, got, expected)
        return got

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"rs{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


def test_nonassociative_op_rejects_tree_algorithms(tmp_path):
    """A declared-non-associative operator may only use the serial chain."""
    first_wins = ampi.Op(
        "FIRST", lambda a, b, ctx: a, commutative=False, associativity=Associativity.NONE
    )

    def rank_main(comm):
        with pytest.raises(ampi.AmpiUsageError):
            comm.reduce(comm.rank, first_wins, root=0, algorithm="binomial")
        out = comm.reduce(comm.rank, first_wins, root=0)  # defaults to chain
        return out

    job = ampi.launch(rank_main, size=4, root=tmp_path / "na")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [4, 8, 16])
def test_fold_depth_is_logarithmic_for_trees(tmp_path, size):
    """The fidelity-relevant statistic: tree depth vs chain depth."""
    depths: dict[str, int] = {}

    def make(algorithm):
        def rank_main(comm):
            comm.reduce(comm.rank, ampi.SUM, root=0, algorithm=algorithm)
            st = algorithms.LAST_STATS.get(comm.rt.wrank)
            return st.fold_depth if st else None

        return rank_main

    for algorithm, expected in (("chain", size - 1), ("binomial", max(1, (size - 1).bit_length()))):
        job = ampi.launch(make(algorithm), size=size, root=tmp_path / f"fd{size}{algorithm}")
        assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
        depths[algorithm] = job.value(0)
        assert job.value(0) == expected, (algorithm, size, job.value(0), expected)
    assert depths["binomial"] < depths["chain"] or size <= 2


@pytest.mark.parametrize("size", [4, 8])
def test_message_counts_match_cost_formulas(tmp_path, size):
    """Implementation and cost model must agree on message counts."""
    from agentmpi.cost import FORMULAS

    cases = [
        ("bcast", "flat"),
        ("bcast", "binomial"),
        ("bcast", "chain"),
        ("reduce", "flat"),
        ("reduce", "binomial"),
        ("reduce", "chain"),
        ("alltoall", "pairwise"),
        ("barrier", "dissemination"),
    ]
    for op, alg in cases:
        totals: list[int] = []

        def rank_main(comm, op=op, alg=alg):
            if op == "bcast":
                comm.bcast("x" if comm.rank == 0 else None, root=0, algorithm=alg)
            elif op == "reduce":
                comm.reduce(1, ampi.SUM, root=0, algorithm=alg)
            elif op == "alltoall":
                comm.alltoall([f"{comm.rank}->{j}" for j in range(comm.size)], algorithm=alg)
            else:
                comm.barrier(algorithm=alg, policy="wait")
            st = algorithms.LAST_STATS.get(comm.rt.wrank)
            return st.messages_sent if st else 0

        job = ampi.launch(rank_main, size=size, root=tmp_path / f"mc{size}{op}{alg}")
        assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
        measured = sum(o.value or 0 for o in job.outcomes)
        _, predicted, _, _ = FORMULAS[(op, alg)](size, 1)
        assert measured == int(predicted), f"{op}/{alg} p={size}: measured {measured} != model {predicted}"


def test_collectives_are_isolated_from_user_traffic(tmp_path):
    """A pending user message with a wildcard tag must not be eaten by a collective."""

    def rank_main(comm):
        if comm.rank == 1:
            comm.send("user-payload", 0, "user")
        comm.barrier(policy="wait")
        comm.bcast("spec" if comm.rank == 0 else None, root=0)
        total = comm.allreduce(1, ampi.SUM)
        assert total == comm.size
        if comm.rank == 0:
            msg = comm.recv(source=1, tag="user", timeout=30)
            assert msg.payload == "user-payload"
        return True

    job = ampi.launch(rank_main, size=4, root=tmp_path / "iso")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


# ---------------------------------------------------------------------------
# k-ary reduction: the shape the agent setting wants and MPI does not.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("fanin", [2, 3, 4, 8])
def test_kary_reduce_matches_serial_fold(tmp_path, size, fanin):
    """Every fan-in must still produce the reference fold for an exact operator."""
    expected = ampi.SUM.fold(range(size))

    def rank_main(comm):
        out = comm.reduce(comm.rank, ampi.SUM, root=0, algorithm="kary", fanin=fanin)
        if comm.rank == 0:
            assert out == expected, (fanin, out, expected)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"k{size}x{fanin}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [4, 8, 16])
@pytest.mark.parametrize("fanin", [2, 4, 8])
def test_kary_reduce_preserves_rank_order(tmp_path, size, fanin):
    """Rank order must survive a wide tree, not just a binary one."""
    expected = "".join(f"[{i}]" for i in range(size))

    def rank_main(comm):
        out = comm.reduce(f"[{comm.rank}]", ampi.CONCAT, root=0, algorithm="kary", fanin=fanin)
        if comm.rank == 0:
            assert out == expected, (fanin, out, expected)
        return out

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"ko{size}x{fanin}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]


@pytest.mark.parametrize("size", [8, 16, 27, 32])
def test_wider_fanin_reduces_fold_depth_at_equal_message_count(tmp_path, size):
    """The whole point: widening the tree costs no extra messages.

    Every non-root rank sends exactly once regardless of fan-in, so the entire
    effect of widening is on rounds and fold depth. For a lossy operator that is a
    pure gain, which is why the agent setting should prefer the widest feasible
    tree where MPI prefers the narrowest.
    """
    from agentmpi.cost import _logkc

    observed: dict[int, tuple[int, int]] = {}
    for fanin in (2, 4, 8):

        def rank_main(comm, fanin=fanin):
            comm.reduce(1, ampi.SUM, root=0, algorithm="kary", fanin=fanin)
            st = algorithms.LAST_STATS.get(comm.rt.wrank)
            return (comm.rt.cost.n_messages_sent, st.fold_depth if st else 0)

        job = ampi.launch(rank_main, size=size, root=tmp_path / f"kd{size}x{fanin}")
        assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
        msgs = sum(o.value[0] for o in job.outcomes if o.value)
        depth = max(o.value[1] for o in job.outcomes if o.value)
        observed[fanin] = (msgs, depth)
        assert msgs == size - 1, f"fanin={fanin}: {msgs} messages, expected {size - 1}"
        assert depth == _logkc(size, fanin), f"fanin={fanin}: depth {depth} != ceil(log_{fanin} {size})"

    assert observed[8][1] <= observed[4][1] <= observed[2][1]
    assert observed[8][1] < observed[2][1], observed
    assert observed[2][0] == observed[8][0], "message count must not depend on fan-in"


def test_variadic_operator_folds_in_one_application(tmp_path):
    """A variadic kernel combines k inputs at depth 1; a binary one costs k-1."""
    calls: dict[str, int] = {"binary": 0, "variadic": 0}

    def binary_fn(a, b, _ctx):
        calls["binary"] += 1
        return f"({a}+{b})"

    def variadic_fn(values, _ctx):
        calls["variadic"] += 1
        return "(" + "+".join(str(v) for v in values) + ")"

    wide = ampi.Op(
        "WIDE",
        binary_fn,
        commutative=False,
        associativity=Associativity.APPROX,
        variadic=variadic_fn,
    )
    narrow = ampi.Op("NARROW", binary_fn, commutative=False, associativity=Associativity.APPROX)

    ctx = ampi.ReduceContext(rank=0)
    assert wide.combine(["a", "b", "c", "d"], ctx) == "(a+b+c+d)"
    assert calls["variadic"] == 1 and calls["binary"] == 0

    calls["binary"] = 0
    ctx2 = ampi.ReduceContext(rank=0)
    narrow.combine(["a", "b", "c", "d"], ctx2)
    assert calls["binary"] == 3, "a binary kernel needs k-1 applications"
    assert ctx2.depth == 3, "and it spends the depth the wide tree would have saved"


def test_optimal_fanin_tracks_the_context_budget():
    from agentmpi.ops import optimal_fanin

    # A large budget with small payloads admits a wide tree.
    assert optimal_fanin(128_000, 1_000) == 32
    # A tight budget with large payloads forces a narrow one.
    assert optimal_fanin(32_000, 4_000) == 6
    # It never returns something unusable.
    assert optimal_fanin(1_000, 100_000) == 2
    assert optimal_fanin(10**9, 1) == 32


# ---------------------------------------------------------------------------
# The allreduce divergence hazard.
# ---------------------------------------------------------------------------


def _order_sensitive_op() -> ampi.Op:
    """A deterministic stand-in for a lossy, order-sensitive semantic merge.

    Keeps only the first two contributions it ever sees, in the order it saw them,
    which is a caricature of a summariser with a fixed output budget: it drops
    input, and *which* input it drops depends on the fold order. Deterministic, so
    the divergence it exposes is a property of the algorithm rather than of a model.
    """

    def fn(a, b, _ctx):
        merged = (a if isinstance(a, list) else [a]) + (b if isinstance(b, list) else [b])
        return merged[:2]

    return ampi.Op(
        "LOSSY_MERGE", fn, commutative=False, associativity=Associativity.APPROX
    )


@pytest.mark.parametrize("size", [4, 8])
def test_reduce_bcast_allreduce_never_diverges(tmp_path, size):
    """One rank computes the result and broadcasts it, so agreement is agreement."""

    def rank_main(comm):
        return comm.allreduce([comm.rank], _order_sensitive_op(), algorithm="reduce_bcast")

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"rb{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    values = [o.value for o in job.outcomes]
    assert all(v == values[0] for v in values), f"reduce_bcast must not diverge, got {values}"


@pytest.mark.parametrize("size", [4, 8])
def test_canonical_operand_order_removes_fold_order_divergence(tmp_path, size):
    """Recursive doubling agrees even for a lossy operator -- by construction.

    The textbook worry about an independent-fold allreduce is that each rank folds
    in its own arrival order, so with a non-associative operator the p ranks end up
    with p different answers to the question they just agreed on. That worry is
    real, and it is *eliminated* by a one-line discipline: at every pairwise
    exchange, order the operands by rank rather than by arrival. Both partners then
    evaluate the identical expression tree, so a deterministic operator -- however
    lossy or order-sensitive -- gives every rank the same result.

    This costs nothing and it is the same reasoning MPI uses to permit trees for
    operators declared non-commutative. It is worth a test because the property is
    invisible: an implementation that folded by arrival order would pass every
    correctness test that uses an exact operator.
    """

    def rank_main(comm):
        return comm.allreduce([comm.rank], _order_sensitive_op(), algorithm="recursive_doubling")

    job = ampi.launch(rank_main, size=size, root=tmp_path / f"rd{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    values = [o.value for o in job.outcomes]
    assert len({tuple(v) for v in values}) == 1, f"canonical ordering should prevent divergence, got {values}"


@pytest.mark.parametrize("size", [4, 8])
def test_nondeterministic_operator_diverges_under_independent_folds(tmp_path, size):
    """The hazard that ordering cannot fix, and the reason for the default.

    Canonical operand ordering makes every rank evaluate the same *expression*. It
    cannot make them get the same *answer*, because a semantic operator implemented
    by a model returns something different every time it is called. Under
    ``recursive_doubling`` each rank evaluates that expression itself, so the
    population diverges; under ``reduce_bcast`` exactly one rank evaluates it and
    the others receive the result by handle, so they are byte-identical.

    This is why a lossy operator defaults to ``reduce_bcast``, at a cost of a factor
    of two in rounds, and why the runtime flags the other combination rather than
    forbidding it -- a harness whose operator happens to be deterministic should
    still be allowed the faster algorithm.
    """
    counter = {"n": 0}

    def fn(a, b, _ctx):
        # Nondeterministic in the same way a model is: same inputs, different output.
        counter["n"] += 1
        merged = (a if isinstance(a, list) else [a]) + (b if isinstance(b, list) else [b])
        return [*merged[:2], f"call{counter['n']}"]

    nondet = ampi.Op("NONDET_MERGE", fn, commutative=False, associativity=Associativity.APPROX)

    def rd_main(comm):
        return comm.allreduce([comm.rank], nondet, algorithm="recursive_doubling")

    job = ampi.launch(rd_main, size=size, root=tmp_path / f"nd{size}")
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    values = [tuple(o.value) for o in job.outcomes]
    assert len(set(values)) > 1, f"independent folds of a nondeterministic operator must diverge, got {values}"

    fabric = ampi.Fabric(tmp_path / f"nd{size}")
    flags = [
        e["payload"].get("divergence_risk")
        for e in fabric.events(kinds=["coll.allreduce"])
        if "divergence_risk" in e["payload"]
    ]
    assert flags and all(flags), "the combination must be flagged in the trace"

    def rb_main(comm):
        return comm.allreduce([comm.rank], nondet, algorithm="reduce_bcast")

    job2 = ampi.launch(rb_main, size=size, root=tmp_path / f"nd2{size}")
    assert job2.ok, [o.traceback for o in job2.outcomes if not o.ok]
    values2 = [tuple(o.value) for o in job2.outcomes]
    assert len(set(values2)) == 1, f"reduce_bcast must deliver one value to everyone, got {values2}"


@pytest.mark.parametrize("size", [4, 8])
def test_exact_operator_is_safe_under_either_algorithm(tmp_path, size):
    """With an exact operator the choice is a pure latency decision."""
    results: dict[str, list] = {}
    for alg in ("reduce_bcast", "recursive_doubling"):

        def rank_main(comm, alg=alg):
            return comm.allreduce({f"k{comm.rank}": comm.rank}, ampi.UNION, algorithm=alg)

        job = ampi.launch(rank_main, size=size, root=tmp_path / f"ex{size}{alg}")
        assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
        values = [o.value for o in job.outcomes]
        assert all(v == values[0] for v in values), f"{alg} diverged on an exact operator"
        results[alg] = values[0]
    assert results["reduce_bcast"] == results["recursive_doubling"]


def test_contract_max_tokens_is_what_makes_a_budget_real(tmp_path):
    """A budget in a prompt is advice; a budget in a contract is enforced.

    Our reduction-fidelity experiment set a 450-token output budget in the prompt and
    passed it to the executor as a hint. Eight of ten merges overran it, by up to 55%,
    because a hint is not a constraint -- and an operator free to both compress and
    overflow is never forced to discard, which is why retention never fell.

    The runtime had the enforcement all along: a `max_tokens` bound on a Contract is
    checked at the boundary and a violation is rejected and retried with the diagnosis.
    This test pins that difference, because the failure is silent from the outside: an
    unenforced budget produces plausible output that is simply too big.
    """
    budget = 60
    oversized = {"summary": "word " * 400}

    advisory = ampi.Contract(name="Summary", kind="json", required=("summary",))
    enforced = ampi.Contract(name="Summary", kind="json", required=("summary",), max_tokens=budget)

    assert advisory.check(oversized) == [], "an advisory contract cannot see the overrun"
    problems = enforced.check(oversized)
    assert problems and "max_tokens" in problems[0], problems

    # And end to end: the runtime must reject and retry, then raise if the executor
    # never complies, rather than silently accepting an over-budget artifact.
    attempts: list[int] = []

    def stubborn(prompt: str, **_kw):
        attempts.append(1)
        return oversized

    def rank_main(comm):
        with pytest.raises(ampi.AmpiError):
            comm.agent("summarise", contract=enforced, retries=3)
        return len(attempts)

    job = ampi.launch(
        rank_main, size=1, root=tmp_path / "budget", executor_factory=lambda r: stubborn, timeout=60
    )
    assert job.ok, [o.traceback for o in job.outcomes if not o.ok]
    assert job.value(0) == 3, f"expected 3 attempts, got {job.value(0)}"

    # A compliant executor succeeds on the first attempt.
    attempts.clear()

    def compliant(prompt: str, **_kw):
        attempts.append(1)
        return {"summary": "short enough"}

    job2 = ampi.launch(
        lambda comm: comm.agent("summarise", contract=enforced, retries=3),
        size=1,
        root=tmp_path / "budget2",
        executor_factory=lambda r: compliant,
        timeout=60,
    )
    assert job2.ok and len(attempts) == 1
