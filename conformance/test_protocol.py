"""Conformance suite for the AgentMPI protocol.

Every test here is a normative obligation from the specification, and each names
the section it enforces.  The suite is parametrised over every registered device,
because a claim that the semantics are transport independent is worth exactly the
test that checks it.

It uses only the public interface.  Pointing it at a different implementation
means changing :func:`job` and nothing else, which is the property that
distinguishes a standard from a library with tests.
"""

from __future__ import annotations

import concurrent.futures
import json

import pytest

from ampi import Ampi
from ampi.constants import ANY_SOURCE, ANY_TAG, PROC_NULL, TAG_UB
from ampi.core.ops import CONFLICT_KEY, value_of
from ampi.errors import AmpiError

from .fixtures import device_ids

pytestmark = pytest.mark.device


@pytest.fixture(params=device_ids())
def device_name(request):
    return request.param


@pytest.fixture
def job(tmp_path, device_name):
    """A four-rank job with one runtime handle per rank."""

    def make(size: int = 4, **kw):
        root = str(tmp_path / f"job{size}")
        Ampi.create(root, size, device=device_name, allow_volatile=True, **kw)
        ranks = [Ampi(root, rank=r, allow_volatile=True) for r in range(size)]
        for r in ranks:
            r.init()
        return ranks

    return make


def parallel(ranks, fn):
    """Run ``fn`` on every rank concurrently and return results in rank order.

    Collectives cannot be tested serially: the whole point is that they block
    until their peers arrive.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(ranks)) as pool:
        futures = [pool.submit(fn, r) for r in ranks]
        return [f.result(timeout=60) for f in futures]


# ==========================================================================
# S1 -- execution model, identity, epochs
# ==========================================================================


def test_s1_init_is_idempotent_at_the_same_epoch(job):
    """S1: agents retry commands, so a second init must not allocate a new epoch."""
    (r0,) = job(1)
    first = r0.init()
    second = r0.init()
    assert second["already_running"] is True
    assert r0._rankview().epoch == first["epoch"]


def test_s1_reinit_after_failure_increments_the_epoch_and_briefs(job):
    ranks = job(2)
    ranks[0].kill(1)
    out = ranks[1].init(reinit=True)
    assert out["epoch"] >= 2
    assert "recovery" in out, "a replacement must be briefed, not given a clean slate"


def test_s1_a_fenced_executor_may_not_act(job):
    """S1: the epoch is a fencing token; a zombie must be harmless, not corrupting."""
    ranks = job(2)
    ranks[0].fence_rank(1)
    with pytest.raises(AmpiError) as e:
        ranks[1].send(0, "I am still here")
    assert e.value.cls_name == "AMPI_ERR_FENCED"
    assert e.value.terminal, "being replaced is terminal for the executor that was replaced"


def test_s1_asserted_identity_is_checked_before_the_operation_runs(job):
    ranks = job(2)
    liar = Ampi(ranks[0].root, rank=0, expect_rank=1, allow_volatile=True)
    with pytest.raises(AmpiError) as e:
        liar.send(1, "hello")
    assert e.value.cls_name == "AMPI_ERR_IDENTITY"
    assert liar.device.scan("msg", {"src": 0}) == [], "the operation must not have run"


def test_s1_a_launch_token_that_belongs_elsewhere_names_its_owner(job):
    """S1: the executor cannot otherwise discover that its environment drifted."""
    ranks = job(3)
    someone_elses = ranks[0].device.read("token", "2").value
    confused = Ampi(ranks[0].root, rank=0, token=someone_elses, allow_volatile=True)
    with pytest.raises(AmpiError) as e:
        confused.heartbeat()
    assert e.value.cls_name == "AMPI_ERR_IDENTITY"
    assert e.value.detail["token_owner"] == "2"
    assert "rank 2" in e.value.hint


def test_s1_an_asserted_job_id_is_checked(job):
    ranks = job(2)
    wrong = Ampi(ranks[0].root, rank=0, expect_job="deadbeef", allow_volatile=True)
    with pytest.raises(AmpiError) as e:
        wrong.heartbeat()
    assert e.value.cls_name == "AMPI_ERR_IDENTITY"


# ==========================================================================
# S2/S5 -- the context ledger and flow control
# ==========================================================================


def test_s2_delivering_a_body_charges_the_receiver(job):
    ranks = job(2)
    before = ranks[1].ledger().used
    ranks[0].send(1, "word " * 200, delivery="eager")
    got = ranks[1].recv(0, timeout=10, materialize=True)
    assert got["charged"] > 100
    assert ranks[1].ledger().used == before + got["charged"]


def test_s5_a_large_payload_travels_by_rendezvous_and_charges_only_an_envelope(job):
    """S5.2: MPI's eager limit, with the unit changed from bytes to tokens."""
    ranks = job(2)
    ranks[0].send(1, {"chapter": "word " * 5000})
    got = ranks[1].recv(0, timeout=10)
    assert got["delivery"] == "rendezvous"
    assert "body" not in got
    assert got["charged"] <= 40
    assert got["envelope"]["tokens"] > 700


def test_s5_the_caller_may_override_the_delivery_decision(job):
    ranks = job(2)
    ranks[0].send(1, "tiny", delivery="rendezvous")
    assert ranks[1].recv(0, timeout=10)["delivery"] == "rendezvous"


def test_s2_an_over_budget_delivery_degrades_rather_than_failing(job):
    """S2.3: an agent with a truncated message can continue; one with an error cannot."""
    ranks = job(2, ctx_budget=900)
    ranks[0].send(1, "word " * 4000, delivery="rendezvous")
    got = ranks[1].recv(0, timeout=10, materialize=True)
    assert "degraded_to" in got
    assert got["charged"] <= 900
    assert ranks[1].ledger().degradations == 1


def test_s5_out_saves_a_body_to_disk_and_charges_nothing(tmp_path, job):
    """S12: every operation that hands back a payload must offer a free path to disk."""
    ranks = job(2)
    ranks[0].send(1, {"big": "word " * 3000})
    dest = tmp_path / "payload.json"
    got = ranks[1].recv(0, timeout=10, out=str(dest))
    assert got["charged"] == 0
    assert json.loads(dest.read_text())["big"].startswith("word")
    assert ranks[1].ledger().used == 0


def test_s5_a_view_bounds_what_a_delivery_costs(job):
    ranks = job(2)
    ranks[0].send(1, "word " * 4000, delivery="rendezvous")
    got = ranks[1].recv(0, timeout=10, view="head:100")
    assert got["charged"] <= 120
    assert len(got["body"]) < 1000


def test_s5_unexpected_message_budget_stalls_a_sender_rather_than_overrunning(job):
    """S5.6: the invisible quality failure becomes a reported, attributable stall."""
    ranks = job(2, unexpected_budget=150)
    ranks[0].send(1, "alpha " * 100, delivery="eager")
    with pytest.raises(AmpiError) as e:
        ranks[0].send(1, "gamma " * 100, delivery="eager", timeout=1)
    assert e.value.cls_name == "AMPI_ERR_CTX_CREDIT"
    assert "rendezvous" in e.value.hint
    assert ranks[0].events(kind="ctx.stall"), "the stall must be traced"


def test_s5_consuming_a_message_returns_its_eager_credit(job):
    ranks = job(2, unexpected_budget=150)
    ranks[0].send(1, "alpha " * 100, delivery="eager")
    ranks[1].recv(0, timeout=10, materialize=True)
    ranks[0].send(1, "gamma " * 100, delivery="eager", timeout=5)


# ==========================================================================
# S6.1 -- matching
# ==========================================================================


def test_s6_a_message_goes_to_the_first_matching_posted_receive(job):
    ranks = job(3)
    ranks[0].send(1, "for one", tag=5)
    ranks[0].send(1, "also for one", tag=6)
    assert ranks[1].recv(0, tag=6, timeout=10, materialize=True)["body"] == "also for one"
    assert ranks[1].recv(0, tag=5, timeout=10, materialize=True)["body"] == "for one"


def test_s6_non_overtaking_holds_between_a_pair(job):
    """S6.1: among messages matching the same receive, send order is preserved."""
    ranks = job(2)
    for i in range(12):
        ranks[0].send(1, {"i": i}, tag=1)
    got = [ranks[1].recv(0, tag=1, timeout=10, materialize=True)["body"]["i"] for _ in range(12)]
    assert got == list(range(12))


def test_s6_wildcards_match_any_source_and_any_tag(job):
    ranks = job(3)
    ranks[1].send(0, "from one", tag=11)
    ranks[2].send(0, "from two", tag=12)
    seen = {ranks[0].recv(ANY_SOURCE, tag=ANY_TAG, timeout=10, materialize=True)["source"]
            for _ in range(2)}
    assert seen == {1, 2}


def test_s6_a_message_is_never_delivered_twice_under_concurrent_wildcard_receives(job):
    """The duplicated-work failure that ad-hoc harnesses hit."""
    ranks = job(4)
    for i in range(12):
        ranks[0].send(1, {"i": i}, tag=3)

    workers = [Ampi(ranks[0].root, rank=1, allow_volatile=True) for _ in range(4)]

    def drain(w):
        out = []
        while True:
            try:
                out.append(w.recv(0, tag=3, timeout=1, materialize=True)["body"]["i"])
            except AmpiError:
                return out

    got = [i for part in parallel(workers, drain) for i in part]
    assert sorted(got) == list(range(12))


def test_s6_reserved_tags_are_refused(job):
    ranks = job(2)
    with pytest.raises(AmpiError) as e:
        ranks[0].send(1, "x", tag=TAG_UB + 1)
    assert e.value.cls_name == "AMPI_ERR_TAG"


def test_s6_symbolic_tags_are_accepted_and_deterministic(job):
    """S6.1: agents use names far more reliably than integers."""
    ranks = job(2)
    ranks[0].send(1, "review please", tag="review")
    assert ranks[1].recv(0, tag="review", timeout=10, materialize=True)["body"] == "review please"


def test_s6_proc_null_is_a_no_op(job):
    ranks = job(2)
    assert ranks[0].send(PROC_NULL, "into the void")["proc_null"] is True
    assert ranks[0].recv(PROC_NULL)["proc_null"] is True


def test_s6_a_retried_send_does_not_duplicate(job):
    """Agents retry commands; a duplicated contribution doubles a reduction's input."""
    ranks = job(2)
    ranks[0].send(1, {"contribution": 1}, tag=2)
    again = ranks[0].send(1, {"contribution": 1}, tag=2)
    assert again.get("duplicate") is True
    assert len(ranks[0].device.scan("msg", {"src": 0, "dst": 1, "tag": 2})) == 1


def test_s6_a_timed_out_receive_resumes_rather_than_reposting(job):
    """S6.3: re-issuing the identical operation resumes the same wait."""
    ranks = job(2)
    with pytest.raises(AmpiError):
        ranks[1].recv(0, tag=9, timeout=0.4)
    with pytest.raises(AmpiError):
        ranks[1].recv(0, tag=9, timeout=0.4)
    assert len(ranks[1].device.scan("recvq", {"dst": 1, "state": "open"})) == 1


def test_s6_ready_mode_catches_a_schedule_bug_early(job):
    ranks = job(2)
    with pytest.raises(AmpiError) as e:
        ranks[0].send(1, "x", mode="ready")
    assert "no matching receive is posted" in e.value.message
    ranks[1].irecv(0)
    ranks[0].send(1, "x", mode="ready")


def test_s6_probe_reports_cost_without_charging_for_the_body(job):
    """S6.5: the basis of every context-aware scheduling decision a harness makes."""
    ranks = job(2)
    ranks[0].send(1, "word " * 2000)
    before = ranks[1].ledger().used
    seen = ranks[1].probe(0)
    assert seen["available"] and seen["envelope"]["tokens"] > 1000
    assert ranks[1].ledger().used == before


def test_s6_inbox_reports_what_is_waiting_and_what_it_would_cost(job):
    ranks = job(3)
    ranks[1].send(0, "a " * 100)
    ranks[2].send(0, "b " * 100)
    box = ranks[0].inbox()
    assert box["pending"] == 2
    assert box["total_tokens"] > 100
    assert box["context_remaining"] > 0


def test_s6_a_posted_receive_survives_its_posters_replacement(job):
    """S6.4: a successor inherits obligations rather than rediscovering them."""
    ranks = job(2)
    req = ranks[1].irecv(0, tag=4)["request"]
    ranks[0].respawn(1)
    successor = Ampi(ranks[0].root, rank=1, allow_volatile=True)
    successor.init()
    ranks[0].send(1, "for whoever holds rank 1", tag=4)
    assert successor.wait(req, timeout=10, materialize=True)["body"].startswith("for whoever")


def test_s6_sendrecv_does_not_deadlock_in_a_symmetric_ring(job):
    """The primitive that makes a ring exchange safe without odd/even reasoning."""
    ranks = job(4)
    n = len(ranks)
    out = parallel(
        ranks,
        lambda r: r.sendrecv(
            (r.rank + 1) % n, {"from": r.rank}, (r.rank - 1) % n, timeout=30, materialize=True
        ),
    )
    assert [o["received"]["body"]["from"] for o in out] == [(r - 1) % n for r in range(n)]


# ==========================================================================
# S6.6 -- collectives
# ==========================================================================


def test_s6_barrier_releases_only_when_every_member_arrives(job):
    ranks = job(4)
    out = parallel(ranks, lambda r: r.barrier("phase-1", timeout=30))
    assert all(o["released"] for o in out)
    assert all(sorted(o["arrived"]) == [0, 1, 2, 3] for o in out)


def test_s6_a_reordered_collective_is_named_rather_than_silently_paired(job):
    """S6.6: an executor's program order is not a reliable identifier.

    Positional identification would pair rank 2's *second* call with everyone
    else's *first* and return a confidently wrong result.  Labels cannot make a
    reordered schedule complete -- nothing can, since each barrier still needs all
    its members -- but they turn an invisible mispairing into a timeout that names
    the label and the rank that never arrived.
    """
    ranks = job(3)

    def go(r):
        order = ["b", "a"] if r.rank == 2 else ["a", "b"]
        return [r.barrier(name, timeout=3) for name in order]

    with pytest.raises(AmpiError) as e:
        parallel(ranks, go)
    assert e.value.cls_name == "AMPI_ERR_TIMEOUT"
    assert e.value.detail["label"] in ("a", "b")
    assert e.value.detail["missing"], "the error must name who has not arrived"

    from ampitools.doctor import diagnose

    open_labels = {
        f["what"] for f in diagnose(ranks[0])["findings"] if "collective" in f["what"]
    }
    assert len(open_labels) == 2, "both mismatched collectives are visible, by label"


def test_s6_a_retried_collective_rejoins_rather_than_starting_a_new_one(job):
    ranks = job(2)
    ranks[0]._join_collective("world", "again", "barrier")
    ranks[0]._join_collective("world", "again", "barrier")
    assert len(ranks[0]._participants("world", "again")) == 1


def test_s6_calling_one_label_as_two_kinds_is_a_named_error(job):
    ranks = job(2)
    ranks[0]._join_collective("world", "x", "barrier")
    with pytest.raises(AmpiError) as e:
        ranks[0]._join_collective("world", "x", "allreduce")
    assert e.value.cls_name == "AMPI_ERR_COLL_MISMATCH"


def test_s6_broadcast_forwards_a_handle_so_depth_cannot_degrade_content(job):
    """S6: a tree broadcast must forward digests, never regenerated content."""
    ranks = job(4)
    body = {"plan": "word " * 400}
    out = parallel(
        ranks, lambda r: r.bcast("plan", payload=body if r.rank == 0 else None, timeout=30)
    )
    handles = {o["handle"] for o in out}
    assert len(handles) == 1, "every rank must see the identical content address"


def test_s6_gather_returns_a_manifest_not_a_concatenation(job):
    """S6.6: where naive harnesses die."""
    ranks = job(4)
    out = parallel(
        ranks, lambda r: r.gather("drafts", payload="word " * 500, root=0, timeout=30)
    )
    root = out[0]
    assert root["contributors"] == 4
    assert len(root["manifest"]) == 4
    assert "bodies" not in root
    assert root["charged"] < root["total_tokens"] / 4, "a manifest must cost far less than bodies"


def test_s6_gather_can_be_asked_for_bounded_bodies(job):
    ranks = job(4)
    out = parallel(
        ranks,
        lambda r: r.gather("drafts2", payload="word " * 500, root=0, timeout=30, view="head:50"),
    )
    assert len(out[0]["bodies"]) == 4
    assert out[0]["charged"] < 500


def test_s6_scatter_gives_each_rank_its_own_slice(job):
    ranks = job(4)
    slices = [{"rank": i, "text": f"chapter {i}"} for i in range(4)]
    out = parallel(
        ranks,
        lambda r: r.scatter("chapters", payload=slices if r.rank == 0 else None, timeout=30),
    )
    assert [o["body"]["rank"] for o in out] == [0, 1, 2, 3]


def test_s6_a_self_identifying_slice_that_is_misrouted_is_a_loud_error(job):
    """The contract turns a plausible wrong answer into an immediate failure."""
    ranks = job(3)
    wrong = [{"rank": (i + 1) % 3, "text": "x"} for i in range(3)]
    contract = {"kind": "json", "expect": {"rank": "{rank}"}}

    def go(r):
        return r.scatter(
            "misrouted", payload=wrong if r.rank == 0 else None, timeout=30, contract=contract
        )

    with pytest.raises(AmpiError) as e:
        parallel(ranks, go)
    assert e.value.cls_name == "AMPI_ERR_TYPE"


def test_s6_alltoall_delivers_the_right_item_to_each_peer(job):
    ranks = job(4)
    out = parallel(
        ranks,
        lambda r: r.alltoall(
            "review", payload=[f"{r.rank}->{j}" for j in range(4)], timeout=30
        ),
    )
    for me, o in enumerate(out):
        got = {item["from"]: item["item"] for item in o["received"]}
        assert all(got[src] == f"{src}->{me}" for src in range(4))


def test_s6_scan_gives_each_rank_the_prefix_over_lower_ranks(job):
    ranks = job(4)
    out = parallel(ranks, lambda r: r.scan("prefix", payload=[r.rank], op="concat", timeout=30))
    assert [o["value"] for o in out] == [[0], [0, 1], [0, 1, 2], [0, 1, 2, 3]]


def test_s6_exscan_excludes_the_caller(job):
    ranks = job(4)
    out = parallel(ranks, lambda r: r.exscan("ex", payload=[r.rank], op="concat", timeout=30))
    assert [o["value"] for o in out] == [[], [0], [0, 1], [0, 1, 2]]


def test_s6_quorum_releases_a_barrier_without_closing_it(job):
    """S6.8: a straggler must still pass through, or quorum guarantees the slow fail."""
    ranks = job(4)
    early = parallel(ranks[:3], lambda r: r.barrier("q", quorum=0.75, timeout=30))
    assert all(o["released"] for o in early)
    late = ranks[3].barrier("q", quorum=0.75, timeout=30)
    assert late["released"], "the collective must not have closed"


# ==========================================================================
# S7 -- operators
# ==========================================================================


def test_s7_a_runtime_reduction_folds_in_the_journal_in_one_round(job):
    ranks = job(4)
    out = parallel(
        ranks, lambda r: r.allreduce("sum", payload=r.rank, op="sum", timeout=30)
    )
    assert all(o["value"] == 6 for o in out)
    assert out[0]["algorithm"] == "flat"
    assert "one round" in out[0]["rule"]


def test_s7_union_lifts_a_disagreement_instead_of_deciding_it(job):
    """S7.3: a canonical tree makes a reduction reproducible, not consistent."""
    ranks = job(4)
    out = parallel(
        ranks,
        lambda r: r.allreduce(
            "gloss",
            payload={"owner": f"m{r.rank % 2}", f"own{r.rank}": r.rank},
            op="union",
            timeout=30,
        ),
    )
    assert out[0]["conflicts"] == {"owner": ["m0", "m1"]}
    assert "owner" not in value_of(out[0]["value"])
    assert all(o["conflicts"] == out[0]["conflicts"] for o in out)


def test_s7_arbitration_decides_every_lifted_conflict_once(job):
    ranks = job(4)
    parallel(
        ranks,
        lambda r: r.allreduce(
            "gloss2", payload={"owner": f"m{r.rank % 2}"}, op="union", timeout=30
        ),
    )
    decided = ranks[0].op_arbitrate("gloss2", rulings={"owner": "m0"})
    assert decided["value"]["owner"] == "m0"
    assert CONFLICT_KEY not in decided["value"]


def test_s7_arbitration_refuses_to_leave_a_conflict_undecided(job):
    ranks = job(4)
    parallel(
        ranks,
        lambda r: r.allreduce(
            "gloss3", payload={"a": r.rank % 2, "b": r.rank % 3}, op="union", timeout=30
        ),
    )
    with pytest.raises(AmpiError) as e:
        ranks[0].op_arbitrate("gloss3", rulings={"a": 0})
    assert "b" in e.value.detail["missing"]


def test_s7_a_reduction_reports_its_fold_depth(job):
    ranks = job(4)
    out = parallel(ranks, lambda r: r.allreduce("d", payload=[r.rank], op="bag", timeout=30))
    assert out[0]["fold_depth"] >= 1
    assert sorted(x for x in out[0]["value"]) == [0, 1, 2, 3]


def test_s7_vote_reports_the_consensus_fraction(job):
    ranks = job(4)
    out = parallel(
        ranks,
        lambda r: r.allreduce("v", payload="yes" if r.rank else "no", op="vote", timeout=30),
    )
    assert out[0]["value"]["winner"] == "yes"
    assert out[0]["value"]["consensus"] == pytest.approx(0.75)
    assert out[0]["value"]["distinct"] == 2


# ==========================================================================
# S8 -- windows
# ==========================================================================


def test_s8_compare_and_swap_admits_exactly_one_claimant(job):
    """S8.2: how work is claimed, and unlike a lock it cannot be held by a dead rank."""
    ranks = job(4)
    ranks[0].win_create("board")
    ranks[0].put("board", "task/0", "unclaimed")
    got = parallel(ranks, lambda r: r.claim("board", "task/0"))
    assert sum(1 for g in got if g["claimed"]) == 1


def test_s8_accumulate_needs_no_lock_and_loses_nothing(job):
    ranks = job(4)
    ranks[0].win_create("findings")
    parallel(
        ranks, lambda r: r.accumulate("findings", "all", {f"f{r.rank}": r.rank}, op="union")
    )
    final = ranks[0].get("findings", "all")["value"]
    assert set(value_of(final)) == {"f0", "f1", "f2", "f3"}


def test_s8_accumulate_refuses_a_lossy_operator(job):
    """S8.2: an atomic section cannot be held across a model call."""
    ranks = job(2)
    ranks[0].win_create("w")
    with pytest.raises(AmpiError) as e:
        ranks[0].accumulate("w", "k", "x", op="agent:merge")
    assert e.value.cls_name == "AMPI_ERR_OP"
    assert "serialisation" in e.value.hint


def test_s8_a_versioned_write_that_loses_a_race_is_told_who_won(job):
    ranks = job(2)
    ranks[0].win_create("w")
    ranks[0].put("w", "k", "first")
    with pytest.raises(AmpiError) as e:
        ranks[1].put("w", "k", "second", expect_version=0)
    assert e.value.cls_name == "AMPI_ERR_CONFLICT"
    assert e.value.detail["current_writer"] == 0
    assert e.value.detail["current_version"] == 1


def test_s8_enumeration_reports_sizes_and_writers_without_bodies(job):
    """S8.2: what makes a blackboard usable by an executor with a bounded context."""
    ranks = job(4)
    ranks[0].win_create("board")
    parallel(ranks, lambda r: r.put("board", f"note/{r.rank}", "word " * 300))
    listing = ranks[0].win_ls("board", prefix="note/")
    assert listing["keys"] == 4
    assert all(i["tokens"] > 100 for i in listing["items"])
    assert listing["charged"] < 400, "enumeration must cost far less than the bodies"


def test_s8_history_attributes_every_version_to_a_writer_and_epoch(job):
    ranks = job(3)
    ranks[0].win_create("w")
    for r in ranks:
        r.put("w", "k", f"by {r.rank}")
    hist = ranks[0].win_history("w", "k")["versions"]
    assert [h["writer"] for h in hist] == [2, 1, 0]
    assert all(h["epoch"] >= 1 for h in hist)


def test_s8_an_overwrite_of_another_ranks_value_is_recorded_as_staleness(job):
    """S8: counted and attributed rather than prevented; a harness may legitimately overwrite."""
    ranks = job(2)
    ranks[0].win_create("w")
    ranks[0].put("w", "k", "mine")
    ranks[1].put("w", "k", "actually mine")
    assert any(e["kind"] == "win.stale" for e in ranks[0].events(kind="win.stale"))


def test_s8_a_lock_excludes_and_carries_a_monotone_fencing_token(job):
    ranks = job(3)
    ranks[0].win_create("w")
    a = ranks[0].win_lock("w", "k", ttl=60)
    with pytest.raises(AmpiError) as e:
        ranks[1].win_lock("w", "k", ttl=60)
    assert e.value.cls_name == "AMPI_ERR_LOCK_BUSY"
    ranks[0].win_unlock(a["lock_id"])
    b = ranks[1].win_lock("w", "k", ttl=60)
    assert b["token"] > a["token"]


def test_s8_a_write_with_a_stale_fencing_token_is_rejected(job):
    """S8.3: the lease stops a dead holder wedging; the token stops a revived one corrupting."""
    ranks = job(2)
    ranks[0].win_create("w")
    old = ranks[0].win_lock("w", "k", ttl=60)
    ranks[0].win_unlock(old["lock_id"])
    ranks[1].win_lock("w", "k", ttl=60)
    with pytest.raises(AmpiError) as e:
        ranks[0].put("w", "k", "from the past", lock_token=old["token"])
    assert e.value.cls_name == "AMPI_ERR_STALE_LEASE"


def test_s8_a_fence_is_a_superstep_boundary(job):
    ranks = job(4)
    ranks[0].win_create("w")
    parallel(ranks, lambda r: r.put("w", f"k{r.rank}", r.rank))
    out = parallel(ranks, lambda r: r.win_fence("w", "phase-1", timeout=30))
    assert all(o["released"] for o in out)
    assert ranks[0].win_ls("w")["keys"] == 4


def test_s8_windows_on_different_communicators_do_not_alias(job):
    ranks = job(4)
    ranks[0].comm_create("team", [0, 1])
    ranks[0].win_create("shared")
    ranks[0].win_create("shared", comm="team")
    ranks[0].put("shared", "k", "world")
    ranks[0].put("shared", "k", "team", comm="team")
    assert ranks[0].get("shared", "k")["value"] == "world"
    assert ranks[0].get("shared", "k", comm="team")["value"] == "team"


# ==========================================================================
# S4 -- communicators and topologies
# ==========================================================================


def test_s4_a_sub_communicators_traffic_is_invisible_on_the_parent(job):
    """S4.1: the property MPI's designers identify as why libraries were possible."""
    ranks = job(4)
    ranks[0].comm_create("team", [0, 1])
    ranks[0].send(1, "team business", comm="team", tag=1)
    assert ranks[1].probe(ANY_SOURCE, tag=ANY_TAG, comm="world")["available"] is False
    assert ranks[1].recv(0, tag=1, comm="team", timeout=10, materialize=True)["body"] == "team business"


def test_s4_a_collective_on_a_sub_communicator_needs_only_its_members(job):
    ranks = job(4)
    ranks[0].comm_create("team", [0, 1])
    out = parallel(ranks[:2], lambda r: r.barrier("team-sync", comm="team", timeout=30))
    assert all(o["released"] for o in out)


def test_s4_split_partitions_by_colour_and_orders_by_key(job):
    ranks = job(4)
    out = parallel(ranks, lambda r: r.comm_split(r.rank % 2, key=-r.rank, timeout=30))
    assert out[0]["members"] == [2, 0]
    assert out[1]["members"] == [3, 1]


def test_s4_dup_gives_a_fresh_context_over_the_same_group(job):
    ranks = job(2)
    dup = ranks[0].comm_dup("world", name="library")["name"]
    ranks[0].send(1, "library traffic", comm=dup, tag=1)
    assert ranks[1].probe(0, tag=1, comm="world")["available"] is False
    assert ranks[1].recv(0, tag=1, comm=dup, timeout=10, materialize=True)["body"] == "library traffic"


def test_s4_cartesian_shift_reports_proc_null_at_a_non_periodic_boundary(job):
    ranks = job(4)
    ranks[0].cart_create([4], periodic=[False], name="line")
    assert ranks[0].cart_shift("line", 0)["source"] == PROC_NULL
    assert ranks[0].cart_shift("line", 0)["dest"] == 1
    assert ranks[3].cart_shift("line", 0)["dest"] == PROC_NULL


def test_s4_a_periodic_shift_wraps(job):
    ranks = job(4)
    ranks[0].cart_create([4], periodic=[True], name="ring")
    assert ranks[0].cart_shift("ring", 0)["source"] == 3


def test_s4_a_directed_graph_records_both_the_graph_and_its_transpose(job):
    """A directed collective needs both, or every critique reaches the wrong author."""
    ranks = job(4)
    ranks[0].graph_create({0: [1, 2], 1: [3], 2: [3], 3: []}, name="reviews")
    assert ranks[0].neighbours("reviews") == {"out": [1, 2], "in": []}
    assert ranks[3].neighbours("reviews") == {"out": [], "in": [1, 2]}


def test_s4_a_neighbourhood_collective_costs_degree_not_size(job):
    ranks = job(4)
    ranks[0].graph_create({0: [1], 1: [2], 2: [3], 3: [0]}, name="ring", symmetric=True)
    out = parallel(
        ranks,
        lambda r: r.neighbor_allgather("halo", payload={"from": r.rank}, comm="ring", timeout=30),
    )
    assert all(o["degree"] == 3 for o in out), "two neighbours plus self, not four ranks"


# ==========================================================================
# S9 -- fault tolerance
# ==========================================================================


def test_s9_a_kill_is_not_retractable_by_its_victim(job):
    """Otherwise fault injection is unobservable and an FT experiment measures nothing."""
    ranks = job(2)
    ranks[0].kill(1)
    with pytest.raises(AmpiError):
        ranks[1].heartbeat()
    assert ranks[0]._rankview(1).state == "failed"


def test_s9_revocation_makes_every_survivor_fail_fast(job):
    """S9.4: ULFM's least obvious and most necessary primitive."""
    ranks = job(3)
    ranks[0].comm_revoke("world", reason="rank 2 is gone")
    with pytest.raises(AmpiError) as e:
        ranks[1].recv(2, timeout=5)
    assert e.value.cls_name == "AMPI_ERR_REVOKED"
    assert "shrink" in e.value.hint


def test_s9_shrink_produces_one_agreed_communicator_not_several(job):
    """S9.5: two ranks that computed different survivor sets would mismatch forever."""
    ranks = job(4)
    ranks[0].kill(3)
    out = parallel(ranks[:3], lambda r: r.comm_shrink("world", timeout=30))
    assert len({o["name"] for o in out}) == 1, "concurrent shrinks must converge"
    assert all(o["members"] == [0, 1, 2] for o in out)
    assert all(o["dropped"] == [3] for o in out)


def test_s9_in_place_shrink_keeps_the_rank_to_work_mapping(job):
    """FT-MPI's BLANK: renumbering invalidates prompts and artifacts."""
    ranks = job(4)
    ranks[0].kill(1)
    out = parallel([ranks[0], ranks[2], ranks[3]], lambda r: r.comm_shrink("world", in_place=True, timeout=30))
    assert out[0]["members"] == [0, 1, 2, 3]
    assert ranks[0].comm_info(out[0]["name"])["absent"] == []


def test_s9_agreement_works_on_a_revoked_communicator(job):
    """S9.6: it is how survivors coordinate recovery, so it cannot need a live comm."""
    ranks = job(3)
    ranks[0].comm_revoke("world")
    out = parallel(ranks, lambda r: r.comm_agree("recover?", True, timeout=30))
    assert all(o["agreed"] for o in out)


def test_s9_agreement_returns_false_when_any_live_rank_says_so(job):
    ranks = job(3)
    out = parallel(ranks, lambda r: r.comm_agree("built?", r.rank != 1, timeout=30))
    assert all(o["agreed"] is False for o in out)


def test_s9_a_collective_drops_a_failed_ranks_subtree_and_records_it(job):
    """S9: the survivors' contributions are worth more than a perfect one."""
    ranks = job(4)
    ranks[0].kill(3)
    out = parallel(ranks[:3], lambda r: r.gather("d", payload=r.rank, root=0, timeout=30))
    assert out[0]["contributors"] == 3
    assert out[0]["dropped"] == [3]


def test_s9_a_blocked_rank_renews_its_own_lease(job):
    """S9.3: blocking is not evidence of death.

    An early version convicted every rank that arrived at a barrier first, for the
    crime of waiting.
    """
    ranks = job(2)
    before = ranks[0]._rankview(0).lease_until
    with pytest.raises(AmpiError):
        ranks[0].recv(1, timeout=1.2)
    assert ranks[0]._rankview(0).lease_until > before


def test_s9_detection_is_two_phase(job):
    """A thinking executor and a dead one look identical; one phase is not enough."""
    ranks = job(2)
    view = ranks[0]._rankview(1)
    view.lease_until = ranks[0].device.clock() - 1
    ranks[0]._write_rank(view)
    ranks[0].detect_failures()
    assert ranks[0]._rankview(1).state == "suspect", "suspicion first"
    ranks[0].detect_failures(confirm_s=0)
    assert ranks[0]._rankview(1).state == "failed", "conviction only after confirmation"


def test_s9_suspicion_is_retractable_by_a_heartbeat(job):
    ranks = job(2)
    view = ranks[0]._rankview(1)
    view.lease_until = ranks[0].device.clock() - 1
    ranks[0]._write_rank(view)
    ranks[0].detect_failures()
    assert ranks[0]._rankview(1).state == "suspect"
    ranks[1].heartbeat()
    assert ranks[0]._rankview(1).state == "running"


def test_s9_a_rank_that_never_starts_becomes_detectable_at_its_join_deadline(job):
    """S3.4: without this, a launch failure is neither alive nor failed forever."""
    existing = job(1)[0]
    root = str(existing.root) + "-late"
    Ampi.create(root, 3, device=existing.device.name, allow_volatile=True, join_deadline_s=-1)
    r0 = Ampi(root, rank=0, allow_volatile=True)
    r0.init()
    failed = {v.rank for v in r0.detect_failures()}
    assert {1, 2} <= failed
    assert r0._rankview(1).failure_kind == "no_show"


def test_s9_respawn_breaks_the_predecessors_locks_but_keeps_its_messages(job):
    ranks = job(3)
    ranks[0].win_create("w")
    ranks[1].win_lock("w", "k", ttl=600)
    ranks[1].send(2, "still needed by rank 2")
    ranks[0].respawn(1)
    assert ranks[0].device.leases() == [], "a dead holder must not wedge the window"
    assert ranks[2].recv(1, timeout=10, materialize=True)["body"] == "still needed by rank 2"


def test_s9_the_restart_bound_stops_an_expensive_infinite_loop(job):
    """OTP's max restart intensity: an impossible assignment will fail again."""
    ranks = job(2)
    for _ in range(3):
        ranks[0].respawn(1)
    with pytest.raises(AmpiError) as e:
        ranks[0].respawn(1)
    assert e.value.cls_name == "AMPI_ERR_BUDGET"
    assert "impossible" in e.value.hint


def test_s9_the_recovery_briefing_answers_the_five_questions(job):
    ranks = job(3)
    ranks[1].win_create("w")
    ranks[1].put("w", "note", "published")
    ranks[1].send(2, "promised")
    ranks[1].irecv(0)
    ranks[1]._join_collective("world", "open-one", "barrier")
    ranks[1].memo("phase", "finished drafting section 3")

    briefing = ranks[1].recover()
    assert any(p["key"] == "note" for p in briefing["published"])
    assert any(s["dst"] == 2 for s in briefing["sent"])
    assert briefing["outstanding"]["posted_receives"]
    assert "open-one" in briefing["outstanding"]["open_collectives"]
    assert briefing["memos"][0]["value"] == "finished drafting section 3"
    assert any("open-one" in a for a in briefing["advice"])


def test_s9_failure_ack_re_enables_wildcard_receives(job):
    ranks = job(3)
    ranks[0].kill(2)
    with pytest.raises(AmpiError) as e:
        ranks[1].recv(ANY_SOURCE, timeout=2)
    assert e.value.cls_name == "AMPI_ERR_PROC_FAILED_PENDING"
    ranks[1].failure_ack()
    with pytest.raises(AmpiError) as e:
        ranks[1].recv(ANY_SOURCE, timeout=1)
    assert e.value.cls_name == "AMPI_ERR_TIMEOUT", "the failure must no longer mask the timeout"


# ==========================================================================
# S12 -- interface declaration and verification
# ==========================================================================


def test_s12_a_declaration_has_a_place_an_owner_and_a_version(job):
    ranks = job(3)
    ranks[1].iface_publish("parser", {"parse": "text -> ast"})
    listing = ranks[2].iface_list()
    assert listing["interfaces"][0]["provider"] == 1
    assert listing["interfaces"][0]["name"] == "parser"
    assert listing["charged"] < 200, "enumeration must not deliver declarations"


def test_s12_a_late_consumer_gets_the_same_answer_as_an_early_one(job):
    """What a broadcast cannot give it, and why the agents rebuilt this by hand."""
    ranks = job(3)
    ranks[1].iface_publish("parser", {"parse": "text -> ast"})
    got = ranks[2].iface_get(1, "parser")
    assert got["declaration"] == {"parse": "text -> ast"}


def test_s12_two_ranks_claiming_one_name_is_a_visible_fact(job):
    ranks = job(3)
    ranks[0].iface_publish("store", {"v": 1})
    ranks[1].iface_publish("store", {"v": 2})
    listing = ranks[2].iface_list()
    assert listing["contested"] == ["store"]


def test_s12_verification_is_published_so_the_second_consumer_pays_nothing(job):
    ranks = job(3)
    ranks[0].iface_publish("parser", {"parse": "text -> ast"})
    ranks[1].iface_verify(0, "parser", holds=True, evidence="probed with 12 inputs")
    seen = ranks[2].iface_list()["interfaces"][0]["verified_by"]
    assert seen == [{"verifier": 1, "holds": True, "evidence": "probed with 12 inputs"}]


def test_s12_a_refuted_declaration_is_reported_to_the_harness(job):
    ranks = job(3)
    ranks[0].iface_publish("parser", {"parse": "text -> ast"})
    ranks[1].iface_verify(0, "parser", holds=False, evidence="returns a list, not an ast")
    report = ranks[2].iface_report()
    assert report["healthy"] is False
    assert report["refuted"][0]["refuted_by"] == [1]


def test_s12_an_unverified_declaration_is_reported_too(job):
    ranks = job(2)
    ranks[0].iface_publish("parser", {"parse": "text -> ast"})
    assert ranks[1].iface_report()["unverified"] == [{"provider": 0, "name": "parser"}]


# ==========================================================================
# S11/S13 -- tracing, diagnostics, conformance
# ==========================================================================


def test_s11_tracing_is_unconditional(job):
    ranks = job(2)
    ranks[0].send(1, "x")
    ranks[1].recv(0, timeout=10, materialize=True)
    kinds = {e["kind"] for e in ranks[0].events()}
    assert {"job.create", "init", "send", "recv"} <= kinds


def test_s11_error_events_make_retry_behaviour_measurable(job):
    ranks = job(2)
    with pytest.raises(AmpiError):
        ranks[0].recv(1, timeout=0.3)
    assert any(e["kind"] == "init" for e in ranks[0].events(rank=0))


def test_doctor_names_the_rank_that_has_not_entered_a_collective(job):
    from ampitools.doctor import diagnose

    ranks = job(4)
    for r in ranks[:3]:
        r._join_collective("world", "phase-1", "barrier")
    report = diagnose(ranks[0])
    stuck = [f for f in report["findings"] if "collective" in f["what"]]
    assert stuck and stuck[0]["ranks"] == [3]
    assert "rank(s) [3]" in report["summary"]


def test_doctor_reports_healthy_when_nothing_is_wrong(job):
    from ampitools.doctor import diagnose

    ranks = job(2)
    for r in ranks:
        r.finalize()
    assert diagnose(ranks[0])["verdict"] == "healthy"


def test_conformance_names_what_is_provided_and_what_is_omitted(job):
    from ampi import conformance

    c = conformance()
    assert c["level"] in c["levels"]
    assert "reduce_scatter" in c["omits"], "omissions must be named, not discovered at run time"
    assert set(c["devices"]) >= {"sqlite", "journal", "memory"}


def test_errors_carry_a_class_a_hint_and_a_retryable_flag(job):
    ranks = job(2)
    with pytest.raises(AmpiError) as e:
        ranks[0].recv(1, timeout=0.3)
    d = e.value.to_dict()
    assert d["error"] == "AMPI_ERR_TIMEOUT"
    assert d["retryable"] is True
    assert d["hint"], "an error a model reads must say what to do"


def test_appendix_d_the_runtime_is_pinned_by_content_not_by_version(job):
    """Appendix D: pinning by version string is not pinning.

    During our experiments an executor called into the package mid-edit and got an
    ImportError from a module another session was fixing. The version string had
    not changed, because what changed was the code. A content hash catches it, and
    the mismatch is advisory rather than fatal: a developer iterating should not be
    locked out, and a two-hour agent run should not die because a docstring moved.
    What matters is that the run's own journal says its runtime changed underneath
    it.
    """
    ranks = job(2)
    manifest = ranks[0].device.read("job", "manifest")
    assert manifest.value["runtime_fingerprint"], "a job must record its runtime's fingerprint"
    assert not ranks[0].events(kind="runtime.changed")

    ranks[0].device.cas(
        "job", "manifest", None,
        {**manifest.value, "runtime_fingerprint": "0000000000000000"}, writer=-1,
    )
    fresh = Ampi(ranks[0].root, rank=1, allow_volatile=True)
    fresh.init()
    changed = fresh.events(kind="runtime.changed")
    assert changed, "a runtime edited under a live job must be recorded"
    assert changed[0]["pinned"] == "0000000000000000"
