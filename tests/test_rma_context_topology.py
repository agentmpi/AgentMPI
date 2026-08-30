"""Shared windows, context budgeting, and topology analysis."""

from __future__ import annotations

import pytest

import agentmpi as ampi
from agentmpi import sim
from agentmpi.context import (
    ContextBudget,
    feasible_allgather,
    head_tail_digest,
    peak_ingest_allgather,
    plan_reduction,
    safe_fanout,
    structural_digest,
)
from agentmpi.topology import analyse, dist_graph_create, pipeline_create
from agentmpi.win import win_create


# ---------------------------------------------------------------- windows

def test_window_put_get_reference_does_not_cost_context():
    """A Get must return a handle, not content, or shared state is unusable."""

    def body(comm):
        win = win_create(comm, "board")
        if comm.rank == 0:
            win.put("report", "x " * 5000)
        comm.barrier(timeout=30)
        before = comm.runtime.budget.ingested
        ref = win.get("report")
        after_ref = comm.runtime.budget.ingested
        content = win.materialize(ref)
        after_read = comm.runtime.budget.ingested
        return (after_ref - before, after_read - after_ref, len(content))

    r = sim.run(3, body, timeout=60)
    r.raise_errors()
    ref_cost, read_cost, length = r.results[2]
    assert ref_cost == 0, "obtaining a reference must not consume context"
    assert read_cost > 500, "materialising must consume context"
    assert length == len("x " * 5000)


def test_window_accumulate_loses_no_concurrent_updates():
    def body(comm):
        win = win_create(comm, "findings")
        for i in range(5):
            win.accumulate("all", {f"r{comm.rank}_{i}": [comm.rank]}, ampi.UNION)
        comm.barrier(timeout=60)
        ref = win.get("all")
        return win.materialize_raw(ref)

    r = sim.run(4, body, timeout=90)
    r.raise_errors()
    merged = r.results[0]
    assert len(merged) == 20, f"lost updates: got {len(merged)} of 20"


def test_window_query_respects_its_budget():
    def body(comm):
        win = win_create(comm, "notes")
        win.put(f"note/{comm.rank}",
                f"rank {comm.rank} observed that the scheduler deadlocks "
                + ("filler " * 200))
        comm.barrier(timeout=60)
        if comm.rank == 0:
            return win.query("scheduler deadlock", budget=400)
        return None

    r = sim.run(6, body, timeout=90, cvars={"ampi_context_capacity": 200000})
    r.raise_errors()
    result = r.results[0]
    assert result["entries_total"] == 6
    assert result["tokens"] <= 400
    assert result["entries_returned"] < 6, "a bounded read must omit something"
    assert len(result["omitted"]) > 0


def test_window_compare_and_swap():
    def body(comm):
        win = win_create(comm, "cas")
        ok_first, _ = win.compare_and_swap("owner", None, {"rank": comm.rank})
        return ok_first

    r = sim.run(5, body, timeout=60)
    r.raise_errors()
    assert sum(1 for v in r.ordered() if v) == 1, "exactly one rank may win"


def test_window_index_is_cheap_relative_to_content():
    def body(comm):
        win = win_create(comm, "big")
        win.put(f"doc/{comm.rank}", "content " * 2000)
        comm.barrier(timeout=60)
        if comm.rank == 0:
            index = win.index()
            index_tokens = sum(len(str(e)) for e in index) / 3.6
            return (len(index), index_tokens, win.total_tokens())
        return None

    r = sim.run(8, body, timeout=90, cvars={"ampi_context_capacity": 400000})
    r.raise_errors()
    entries, index_tokens, content_tokens = r.results[0]
    assert entries == 8
    assert index_tokens * 20 < content_tokens, (
        "the catalogue must be far cheaper than the content it describes")


# ---------------------------------------------------------------- context

def test_budget_reserves_headroom_for_reasoning():
    budget = ContextBudget(capacity=1000, reserve_fraction=0.4)
    assert budget.usable == 600
    budget.admit(500)
    assert budget.headroom == 100
    with pytest.raises(ampi.ContextOverflowError):
        budget.admit(200)


def test_budget_is_cumulative_not_peak():
    """The defining difference from a memory allocator."""
    budget = ContextBudget(capacity=1000, reserve_fraction=0.0)
    for _ in range(10):
        budget.admit(100)
    assert budget.live == 1000
    with pytest.raises(ampi.ContextOverflowError):
        budget.admit(1)


def test_compaction_reclaims_budget():
    budget = ContextBudget(capacity=1000, reserve_fraction=0.0)
    budget.admit(900)
    budget.compact(freed=800, cost=100)
    assert budget.live == 200
    budget.admit(700)


def test_lifetime_token_cap_raises_budget_error():
    budget = ContextBudget(capacity=1_000_000, lifetime_tokens=500)
    budget.admit(400)
    with pytest.raises(ampi.BudgetError):
        budget.admit(200)


def test_admission_control_digests_rather_than_failing():
    def body(comm):
        if comm.rank == 0:
            comm.send("sentence " * 4000, 1, 1)
            return None
        value, status = comm.recv(0, 1, timeout=30)
        return (status.reduced, status.tokens, comm.runtime.budget.pressure)

    r = sim.run(2, body, timeout=60, cvars={"ampi_context_capacity": 3000})
    r.raise_errors()
    reduced, tokens, pressure = r.results[1]
    assert reduced is True
    assert tokens < 2000
    assert pressure <= 1.0


def test_admission_control_refuses_when_loss_is_forbidden():
    strict = ampi.type_bounded(ampi.TEXT, max_tokens=100, lossy=False, name="Exact")

    def body(comm):
        if comm.rank == 0:
            with pytest.raises(ampi.ContextOverflowError):
                comm.send("word " * 500, 1, 1, strict)
            return "refused"
        return None

    r = sim.run(2, body, timeout=30)
    r.raise_errors()
    assert r.results[0] == "refused"


def test_safe_fanout_and_reduction_planning():
    assert safe_fanout(budget=10_000, item_tokens=1_000) == 10
    plan = plan_reduction(n=64, item_tokens=1000, budget=10_000, output_tokens=800)
    assert plan.feasible
    # A degree-10 tree would finish in 2 rounds but costs the root
    # 9 * 2 * 1000 = 18000 tokens cumulatively, over budget. The planner must
    # trade a round for capacity.
    assert plan.fanout == 4
    assert plan.rounds == 3
    assert plan.peak_ingest <= 10_000


def test_reduction_planning_accounts_for_cumulative_not_per_round_ingest():
    """The root of a depth-d tree pays d rounds of fan-in, not one."""
    per_round = plan_reduction(n=64, item_tokens=1000, budget=10_000,
                               output_tokens=1000)
    assert per_round.feasible
    assert per_round.peak_ingest == (per_round.fanout - 1) * per_round.rounds * 1000
    assert per_round.peak_ingest <= 10_000


def test_reduction_becomes_infeasible_when_even_a_binary_tree_will_not_fit():
    plan = plan_reduction(n=1024, item_tokens=4000, budget=10_000, output_tokens=4000)
    assert not plan.feasible
    assert "binary tree" in plan.reason


def test_non_contracting_operator_makes_a_deep_tree_infeasible():
    plan = plan_reduction(n=64, item_tokens=1000, budget=10_000, output_tokens=4000)
    assert not plan.feasible
    assert "not contracting" in plan.reason


def test_single_contribution_larger_than_budget_is_infeasible():
    plan = plan_reduction(n=8, item_tokens=50_000, budget=10_000)
    assert not plan.feasible
    assert "does not fit" in plan.reason


def test_allgather_feasibility_degrades_linearly():
    assert feasible_allgather(n=10, item_tokens=1000, budget=20_000)
    assert not feasible_allgather(n=100, item_tokens=1000, budget=20_000)
    assert peak_ingest_allgather(64, 1000) == 63_000


def test_digests_preserve_structure():
    text = "\n".join([f"line {i}" for i in range(500)])
    out = head_tail_digest(text, budget=100)
    assert "line 0" in out and "line 499" in out and "elided" in out

    code = "\n".join(["def alpha():", "    return 1", "class Beta:",
                      "    def gamma(self):", "        pass"] + ["    x = 1"] * 400)
    digest = structural_digest(code, budget=60)
    assert "def alpha():" in digest and "class Beta:" in digest


# --------------------------------------------------------------- topology

def test_pipeline_topology_reports_no_parallelism():
    def body(comm):
        topo = pipeline_create(comm, ["draft", "edit", "verify"])
        return analyse(topo)

    r = sim.run(6, body, timeout=30)
    r.raise_errors()
    report = r.results[0]
    assert report["diameter"] == 5
    assert report["serial_floor_turns"] == 6
    assert report["max_parallelism"] == pytest.approx(1.0)
    assert report["deadlock_risk"] is False


def test_cyclic_topology_is_flagged_as_a_deadlock_risk():
    def body(comm):
        topo = dist_graph_create(comm, {0: [1], 1: [2], 2: [0], 3: []})
        return analyse(topo)

    r = sim.run(4, body, timeout=30)
    r.raise_errors()
    report = r.results[0]
    assert report["deadlock_risk"] is True
    assert report["cycles"]


def test_star_topology_identifies_the_hub():
    def body(comm):
        topo = dist_graph_create(comm, {r: [0] for r in range(1, comm.size)})
        return analyse(topo)

    r = sim.run(8, body, timeout=30)
    r.raise_errors()
    assert r.results[0]["hubs"] == [0]


def test_neighbor_allgather_costs_degree_not_size():
    from agentmpi.collectives import neighbor_allgather

    def body(comm):
        # A ring: every rank talks to exactly two peers.
        dist_graph_create(comm, {r: [(r + 1) % comm.size] for r in range(comm.size)})
        got = neighbor_allgather(comm, f"v{comm.rank}", timeout=60)
        return got

    r = sim.run(8, body, timeout=90)
    r.raise_errors()
    for rank in range(8):
        assert r.results[rank] == [f"v{(rank - 1) % 8}"]


def test_cartesian_shift():
    def body(comm):
        from agentmpi.topology import cart_create

        topo = cart_create(comm, dims=(3, 4), periods=(False, True))
        coords = topo.coords(comm.rank)
        up, down = topo.shift(comm.rank, 0, 1)
        left, right = topo.shift(comm.rank, 1, 1)
        return {"coords": coords, "col": (up, down), "row": (left, right)}

    r = sim.run(12, body, timeout=30)
    r.raise_errors()
    assert r.results[0]["coords"] == (0, 0)
    assert r.results[0]["col"][0] == ampi.PROC_NULL     # no wrap on axis 0
    assert r.results[0]["row"][0] == 3                  # wraps on axis 1
    assert r.results[5]["coords"] == (1, 1)


def test_comm_split_type_by_model():
    specs = [ampi.RankSpec(rank=i, model="big" if i % 2 == 0 else "small")
             for i in range(6)]

    def body(comm):
        sub = comm.split_type("model")
        return (comm.spec().model, sub.size, list(sub.group.members))

    r = sim.run(6, body, specs=specs, timeout=60)
    r.raise_errors()
    for rank in range(6):
        model, size, members = r.results[rank]
        assert size == 3
        assert all(m % 2 == (0 if model == "big" else 1) for m in members)


def test_comm_split_by_color():
    def body(comm):
        sub = comm.split(color=comm.rank % 3, key=-comm.rank)
        return (sub.size, list(sub.group.members), sub.rank)

    r = sim.run(9, body, timeout=60)
    r.raise_errors()
    size, members, _ = r.results[0]
    assert size == 3
    assert members == [6, 3, 0]  # ordered by key = -rank


# ------------------------------------------- sub-communicator addressing

def test_collectives_work_on_a_split_communicator():
    """Regression: sub-communicator messages must reach the right inbox.

    Envelopes carry communicator-local ranks, because that is what matching
    and ordering are defined over, but the device owns one inbox per
    *physical* rank. On AMPI_COMM_WORLD the two numberings coincide, so
    conflating them passes every test that uses only the world communicator
    -- and silently misroutes every message the moment a job splits or
    shrinks. This test uses a split whose local ranks differ from the world
    ranks, so the two numberings cannot be confused.
    """

    def body(comm):
        sub = comm.split(color=comm.rank % 2, key=0)
        assert sub is not None
        # Local rank differs from world rank for the odd-coloured group.
        total = sub.allreduce(comm.rank, ampi.SUM, timeout=60)
        gathered = sub.allgather(comm.rank, timeout=60)
        sub.barrier(timeout=60)
        return (sub.size, sub.rank, comm.rank, total, gathered)

    r = sim.run(8, body, timeout=120)
    r.raise_errors()
    for world in range(8):
        size, local, rank, total, gathered = r.results[world]
        expected_members = [w for w in range(8) if w % 2 == world % 2]
        assert size == 4
        assert gathered == expected_members
        assert total == sum(expected_members)
        assert local == expected_members.index(world)


def test_point_to_point_on_a_split_communicator():
    def body(comm):
        sub = comm.split(color=comm.rank // 4, key=0)
        assert sub is not None
        if sub.rank == 0:
            for peer in range(1, sub.size):
                comm_msg, _ = sub.recv(peer, 11, timeout=60)
            return "root-ok"
        sub.send(f"hello from world {comm.rank}", 0, 11)
        return "sent"

    r = sim.run(8, body, timeout=120)
    r.raise_errors()
    assert r.results[0] == "root-ok" and r.results[4] == "root-ok"


def test_window_never_aliases_content_between_keys():
    """Concurrent puts to distinct keys must not cross-contaminate.

    A rank in our software build reported that the interface board had
    aliased two entries: the key for one module held another module's
    payload, with byte-identical previews. That would be a fatal window bug,
    so it is pinned here. It was not the window -- the harness had given
    every rank the same working directory and the same output filename, so
    the ranks were overwriting one another's file before publishing it. The
    protocol isolates the message plane; it cannot isolate a filesystem the
    agents also share.
    """

    def body(comm):
        win = win_create(comm, "interfaces")
        win.put(f"iface/m{comm.rank}",
                {"module": f"m{comm.rank}", "filler": "x" * (100 + comm.rank)})
        comm.barrier(timeout=60)
        if comm.rank == 0:
            return {r: win.materialize_raw(win.get(f"iface/m{r}"))["module"]
                    for r in range(comm.size)}
        return None

    r = sim.run(8, body, timeout=120, cvars={"ampi_context_capacity": 400_000})
    r.raise_errors()
    assert r.results[0] == {i: f"m{i}" for i in range(8)}


def test_window_keys_with_identical_content_stay_distinct():
    """Content addressing must not merge two keys that happen to agree."""

    def body(comm):
        win = win_create(comm, "same")
        win.put(f"k{comm.rank}", {"identical": "payload"})
        comm.barrier(timeout=60)
        if comm.rank == 0:
            return {r: win.get(f"k{r}").key for r in range(comm.size)}
        return None

    r = sim.run(6, body, timeout=120)
    r.raise_errors()
    assert r.results[0] == {i: f"k{i}" for i in range(6)}
