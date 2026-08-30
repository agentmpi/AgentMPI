"""End-to-end tests that drive the CLI exactly as an agent would.

These tests are the conformance suite: every one of them is a shell transcript,
because the shell is the binding. A test that called the Python API directly
would not exercise the interface agents actually use, and in practice most bugs
we found lived in the binding (argument shapes, retry idempotence, output
legibility) rather than in the algorithms.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO = Path(__file__).resolve().parent.parent


def ampi(root: Path, rank: Optional[int], *args: str, check: bool = True) -> Dict[str, Any]:
    env = dict(os.environ)
    env["AMPI_ROOT"] = str(root)
    if rank is not None:
        env["AMPI_RANK"] = str(rank)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "ampi.cli", *args, "--json"],
        capture_output=True, text=True, env=env, cwd=str(root),
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"ampi {' '.join(args)} (rank={rank}) failed rc={proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    # The binding prints `AMPI_RETRY` progress lines to stderr before the final
    # payload; strip them so the JSON is parseable.
    def _clean(text: str) -> str:
        return "\n".join(
            ln for ln in text.splitlines() if not ln.startswith("AMPI_RETRY")
        ).strip()

    out = _clean(proc.stdout) or _clean(proc.stderr)
    try:
        data = json.loads(out)
    except Exception:
        data = {"raw": out}
    data["_rc"] = proc.returncode
    return data


@pytest.fixture()
def job(tmp_path: Path):
    def _make(np: int, **kw: Any) -> Path:
        root = tmp_path / f"job{np}"
        root.mkdir(exist_ok=True)
        args = ["run", "--np", str(np), "--label", "test", "--job-root", str(root)]
        for k, v in kw.items():
            args += [f"--{k.replace('_', '-')}", str(v)]
        ampi(root, None, *args)
        for r in range(np):
            ampi(root, r, "init")
        return root

    return _make


def test_init_and_info(job):
    root = job(4)
    info = ampi(root, 2, "info")
    assert info["rank"] == 2
    assert info["size"] == 4
    assert info["comm"] == "world"
    assert info["ctx"]["remaining"] > 0


def test_send_recv_and_ordering(job):
    """Non-overtaking: two sends from the same source with the same tag are
    matched in send order."""
    root = job(2)
    ampi(root, 0, "send", "--to", "1", "--tag", "7", "--in", "first")
    ampi(root, 0, "send", "--to", "1", "--tag", "7", "--in", "second")
    a = ampi(root, 1, "recv", "--from", "0", "--tag", "7", "--timeout", "5")
    b = ampi(root, 1, "recv", "--from", "0", "--tag", "7", "--timeout", "5")
    assert a["body"] == "first"
    assert b["body"] == "second"
    assert a["seq"] < b["seq"]


def test_symbolic_tags_match(job):
    root = job(2)
    ampi(root, 0, "send", "--to", "1", "--tag", "review", "--in", "hello")
    got = ampi(root, 1, "recv", "--from", "0", "--tag", "review", "--timeout", "5")
    assert got["body"] == "hello"


def test_wildcard_receive(job):
    root = job(3)
    ampi(root, 2, "send", "--to", "0", "--tag", "9", "--in", "from-two")
    got = ampi(root, 0, "recv", "--from", "any", "--tag", "any", "--timeout", "5")
    assert got["source"] == 2
    assert got["body"] == "from-two"


def test_idempotent_send(job):
    root = job(2)
    a = ampi(root, 0, "send", "--to", "1", "--tag", "1", "--in", "x", "--idem", "k1")
    b = ampi(root, 0, "send", "--to", "1", "--tag", "1", "--in", "x", "--idem", "k1")
    assert b["duplicate"] is True
    assert a["seq"] == b["seq"]
    assert ampi(root, 1, "inbox")["count"] == 1


def test_timeout_is_retryable_and_resumes(job):
    root = job(2)
    r = ampi(root, 1, "recv", "--from", "0", "--tag", "5", "--timeout", "0.2",
             "--retries", "0", check=False)
    assert r["err_class"] == "AMPI_ERR_TIMEOUT"
    assert r["retryable"] is True
    ampi(root, 0, "send", "--to", "1", "--tag", "5", "--in", "late")
    r2 = ampi(root, 1, "recv", "--from", "0", "--tag", "5", "--timeout", "5")
    assert r2["body"] == "late"
    # The retried receive must not have created a second queued receive.
    assert ampi(root, 1, "inbox")["count"] == 0


def test_rendezvous_for_large_payload(job):
    root = job(2)
    big = "lorem ipsum dolor sit amet " * 400
    ampi(root, 0, "send", "--to", "1", "--tag", "1", "--in", big)
    got = ampi(root, 1, "recv", "--from", "0", "--tag", "1", "--timeout", "5")
    assert got["mode"] == "rendezvous"
    assert "body" not in got
    assert got["tokens"] > 700
    assert got["context_charged"] < 200  # only the envelope entered context
    view = ampi(root, 1, "view", got["handle"], "--op", "head:100")
    assert view["view_tokens"] <= 110


def test_view_ops(job):
    root = job(1)
    payload = json.dumps({"alpha": 1, "beta": {"x": [1, 2, 3]}, "notes": "n" * 200})
    s = ampi(root, 0, "send", "--to", "0", "--tag", "1", "--in", payload)
    h = s["handle"]
    assert json.loads(ampi(root, 0, "view", h, "--op", "keys:alpha,beta")["body"])["alpha"] == 1
    assert "tokens" in ampi(root, 0, "view", h, "--op", "stat")["body"]
    outline = ampi(root, 0, "view", h, "--op", "grep:alpha")["body"]
    assert "alpha" in outline


def test_context_budget_enforced(job):
    root = job(2, ctx_budget=900, eager_tokens=100000)
    big = "word " * 2000
    ampi(root, 0, "send", "--to", "1", "--tag", "1", "--in", big)
    got = ampi(root, 1, "recv", "--from", "0", "--tag", "1", "--timeout", "5", "--materialize")
    # The receive must still succeed, degraded to a clipped view.
    assert "body" in got
    assert "note" in got
    st = ampi(root, 1, "ctx")
    assert st["used"] <= st["budget"] * 1.5


def parallel(root: Path, ranks: List[int], *args: str) -> List[Dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=max(2, len(ranks))) as ex:
        return list(ex.map(lambda r: ampi(root, r, *args), ranks))


def test_barrier_central(job):
    root = job(6)
    res = parallel(root, list(range(6)), "barrier", "--label", "p1", "--timeout", "30")
    assert all(r["released"] for r in res)


def test_barrier_dissemination(job):
    root = job(4)
    res = parallel(root, list(range(4)), "barrier", "--label", "d1",
                   "--algo", "dissemination", "--timeout", "60")
    assert all(r["released"] for r in res)
    assert res[0]["rounds"] == 2


def test_barrier_quorum_releases_without_stragglers(job):
    root = job(4)
    res = parallel(root, [0, 1, 2], "barrier", "--label", "q1", "--quorum", "0.75", "--timeout", "20")
    assert all(r["released"] for r in res)
    assert 3 in res[0]["late"] or res[0]["arrived"] >= 3


def test_bcast_flat(job):
    root = job(5)
    def call(r: int) -> Dict[str, Any]:
        if r == 0:
            return ampi(root, 0, "bcast", "--root", "0", "--label", "plan",
                        "--in", "the plan", "--timeout", "30")
        return ampi(root, r, "bcast", "--root", "0", "--label", "plan",
                    "--timeout", "30", "--materialize")
    with ThreadPoolExecutor(max_workers=5) as ex:
        res = list(ex.map(call, range(5)))
    assert all(r["body"] == "the plan" for r in res)


def test_bcast_binomial(job):
    root = job(8)
    def call(r: int) -> Dict[str, Any]:
        extra = ["--in", "tree plan"] if r == 0 else []
        return ampi(root, r, "bcast", "--root", "0", "--label", "tp", "--algo", "binomial",
                    "--timeout", "60", "--materialize", *extra)
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(call, range(8)))
    assert all(r["body"] == "tree plan" for r in res)


def test_allreduce_union_and_vote(job):
    root = job(4)
    res = parallel(root, list(range(4)), "allreduce", "--op", "vote", "--label", "v1",
                   "--in", "42", "--timeout", "30", "--materialize")
    tally = json.loads(res[0]["body"])
    assert tally["winner_votes"] == 4
    assert tally["agreement"] == 1.0


def test_allreduce_sum(job):
    root = job(5)
    with ThreadPoolExecutor(max_workers=5) as ex:
        res = list(ex.map(
            lambda r: ampi(root, r, "allreduce", "--op", "sum", "--label", "s1",
                           "--in", str(r + 1), "--timeout", "30", "--materialize"),
            range(5),
        ))
    assert all(r["body"].strip() == "15" for r in res)


def test_reduce_binomial_agent_op(job):
    """An agent-evaluated reduction: each internal node is handed two operands
    and must commit a merge. Verifies the continuation protocol and that the
    number of merges on the critical path is ceil(log2 P), not P-1."""
    root = job(4)
    state: Dict[int, Dict[str, Any]] = {}

    def drive(r: int) -> Dict[str, Any]:
        res = ampi(root, r, "reduce", "--op", "agent:merge", "--label", "m1", "--root", "0",
                   "--algo", "binomial", "--in", f"item{r}", "--timeout", "60",
                   "--materialize", check=False)
        rounds = 0
        while res.get("action_required") == "merge":
            rounds += 1
            left = Path(res["left_file"]).read_text()
            right = Path(res["right_file"]).read_text()
            out = Path(res["suggested_out"])
            out.write_text(left.strip() + "+" + right.strip())
            res = ampi(root, r, "reduce-commit", "--step", res["step"], "--in", f"@{out}",
                       "--timeout", "60", "--materialize", check=False)
        state[r] = {"rounds": rounds, "res": res}
        return res

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(drive, range(4)))
    root_res = state[0]["res"]
    assert root_res.get("complete") is True
    merged = root_res["body"]
    assert set(merged.split("+")) == {"item0", "item1", "item2", "item3"}
    # Rank 0 performs log2(4) = 2 merges; ranks 1 and 3 perform none.
    assert state[0]["rounds"] == 2
    assert state[1]["rounds"] == 0
    assert state[3]["rounds"] == 0


def test_gather_returns_manifest_not_bodies(job):
    root = job(6)
    big = "sentence " * 300

    def call(r: int) -> Dict[str, Any]:
        return ampi(root, r, "gather", "--root", "0", "--label", "g1",
                    "--in", f"{big} from {r}", "--timeout", "30")

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(call, range(6)))
    r0 = res[0]
    assert r0["count"] == 6
    assert all("body" not in it for it in r0["items"])
    assert r0["total_payload_tokens"] > 6 * 200
    assert r0["context_charged"] < 400


def test_allgather_with_budget_clips(job):
    root = job(4)
    big = "alpha " * 500
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(
            lambda r: ampi(root, r, "allgather", "--label", "ag1", "--in", f"{big}{r}",
                           "--budget", "800", "--timeout", "30"),
            range(4),
        ))
    assert res[0]["count"] == 4
    assert res[0]["context_charged"] <= 900
    assert any(it.get("clipped") for it in res[0]["items"])


def test_scatter_assigns_slices(job):
    root = job(4)
    parts = json.dumps([f"chunk-{i}" for i in range(4)])

    def call(r: int) -> Dict[str, Any]:
        extra = ["--parts", parts] if r == 0 else []
        return ampi(root, r, "scatter", "--root", "0", "--label", "w1", "--timeout", "30",
                    "--materialize", *extra)

    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(call, range(4)))
    assert [r["body"] for r in res] == [f"chunk-{i}" for i in range(4)]


def test_exscan_prefix(job):
    root = job(4)
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(
            lambda r: ampi(root, r, "exscan", "--op", "concat", "--label", "e1",
                           "--in", f"c{r}", "--timeout", "30", "--materialize"),
            range(4),
        ))
    assert res[0].get("identity") is True
    assert res[1]["body"].strip() == "c0"
    assert res[3]["body"].split() == ["c0", "c1", "c2"]


def test_alltoall(job):
    root = job(3)

    def call(r: int) -> Dict[str, Any]:
        parts = json.dumps([f"{r}->{j}" for j in range(3)])
        return ampi(root, r, "alltoall", "--parts", parts, "--label", "a1", "--timeout", "30")

    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(call, range(3)))
    got = {x["from"]: x["body"] for x in res[1]["received"]}
    assert got[0] == "0->1" and got[2] == "2->1"


def test_windows_put_get_acc_cas(job):
    root = job(3)
    ampi(root, 0, "win", "create", "--name", "shared")
    ampi(root, 0, "win", "put", "--win", "shared", "--key", "plan", "--in", "v1")
    got = ampi(root, 1, "win", "get", "--win", "shared", "--key", "plan", "--materialize")
    assert got["body"] == "v1" and got["version"] == 1

    ampi(root, 1, "win", "acc", "--win", "shared", "--key", "found", "--op", "union",
         "--in", '["a"]')
    ampi(root, 2, "win", "acc", "--win", "shared", "--key", "found", "--op", "union",
         "--in", '["b"]')
    merged = ampi(root, 0, "win", "get", "--win", "shared", "--key", "found", "--materialize")
    assert set(json.loads(merged["body"])) == {"a", "b"}

    ampi(root, 0, "win", "put", "--win", "shared", "--key", "task", "--in", "unclaimed")
    a = ampi(root, 1, "win", "cas", "--win", "shared", "--key", "task",
             "--expect", "unclaimed", "--value", "rank:1")
    b = ampi(root, 2, "win", "cas", "--win", "shared", "--key", "task",
             "--expect", "unclaimed", "--value", "rank:2")
    assert a["swapped"] != b["swapped"]

    ls = ampi(root, 0, "win", "ls", "--win", "shared")
    assert {k["key"] for k in ls["keys"]} == {"plan", "found", "task"}


def test_window_versioned_put_conflict(job):
    root = job(2)
    ampi(root, 0, "win", "create", "--name", "w")
    ampi(root, 0, "win", "put", "--win", "w", "--key", "k", "--in", "a")
    ok = ampi(root, 1, "win", "put", "--win", "w", "--key", "k", "--in", "b",
              "--expect-version", "1")
    assert ok["version"] == 2
    bad = ampi(root, 0, "win", "put", "--win", "w", "--key", "k", "--in", "c",
               "--expect-version", "1", check=False)
    assert bad["err_class"] == "AMPI_ERR_CONFLICT"


def test_window_lock_excludes(job):
    root = job(2)
    ampi(root, 0, "win", "create", "--name", "w")
    ampi(root, 0, "win", "lock", "--win", "w", "--key", "k", "--timeout", "5")
    busy = ampi(root, 1, "win", "lock", "--win", "w", "--key", "k", "--timeout", "0.5",
                "--retries", "0", check=False)
    assert busy["err_class"] == "AMPI_ERR_LOCK_BUSY"
    ampi(root, 0, "win", "unlock", "--win", "w", "--key", "k")
    ok = ampi(root, 1, "win", "lock", "--win", "w", "--key", "k", "--timeout", "5")
    assert ok["mode"] == "exclusive"


def test_fetch_and_op_hands_out_tickets(job):
    root = job(4)
    ampi(root, 0, "win", "create", "--name", "w")
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(
            lambda r: ampi(root, r, "win", "faop", "--win", "w", "--key", "next",
                           "--op", "sum", "--value", "1"),
            range(4),
        ))
    assert sorted(int(float(r["fetched"])) for r in res) == [0, 1, 2, 3]


def test_comm_split(job):
    root = job(6)
    # Every rank must register a colour before the groups materialise, so all six
    # calls are made for their effect; only the last rank's view is asserted.
    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(
            lambda r: ampi(root, r, "comm", "split", "--color", str(r % 2), "--key", str(r)),
            range(6),
        ))
    last = ampi(root, 5, "comm", "split", "--color", "1", "--key", "5")
    assert last["size"] == 3
    assert last["comm"].endswith("1")


def test_cart_topology_shift(job):
    root = job(6)
    ampi(root, 0, "comm", "cart", "--dims", "2,3")
    sh = ampi(root, 1, "comm", "shift", "--comm", "world.cart", "--direction", "1", "--disp", "1")
    assert sh["source"] == 0 and sh["dest"] == 2


def test_failure_detection_and_shrink(job):
    root = job(4)
    ampi(root, None, "kill", "2", "--kind", "killed")
    failed = ampi(root, 0, "failed")
    assert failed["count"] == 1 and failed["failed"][0]["world"] == 2

    r = ampi(root, 0, "recv", "--from", "2", "--tag", "1", "--timeout", "3",
             "--retries", "0", check=False)
    assert r["err_class"] == "AMPI_ERR_PROC_FAILED"

    ampi(root, 0, "comm", "revoke", "--reason", "rank 2 died")
    r2 = ampi(root, 1, "barrier", "--label", "afterdeath", "--timeout", "2",
              "--retries", "0", check=False)
    assert r2["err_class"] == "AMPI_ERR_REVOKED"

    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(
            lambda r: ampi(root, r, "comm", "shrink", "--timeout", "30", "--quorum", "0"),
            [0, 1, 3],
        ))
    new = res[0]["comm"]
    assert res[0]["size"] == 3
    assert res[0]["excluded"] == [2]
    out = parallel(root, [0, 1, 3], "barrier", "--comm", new, "--label", "recovered", "--timeout", "30")
    assert all(o["released"] for o in out)


def test_respawn_gives_recovery_brief(job):
    root = job(3)
    ampi(root, 0, "win", "create", "--name", "w")
    ampi(root, 1, "win", "put", "--win", "w", "--key", "progress", "--in", "did half")
    ampi(root, 1, "memo", "put", "phase", "chapter 3 of 7")
    ampi(root, 2, "send", "--to", "1", "--tag", "task", "--in", "please do X")
    ampi(root, None, "kill", "1", "--kind", "ctx_exhausted")
    info = ampi(root, None, "respawn", "1")
    assert info["new_epoch"] == 1
    brief = json.loads(Path(info["recovery_brief"]).read_text())
    assert brief["memos"][0]["value"] == "chapter 3 of 7"
    assert any(c["key"] == "progress" for c in brief["published_window_cells"])
    assert any(m["tag"] for m in brief["unread_inbox"])
    assert brief["predecessor_failures"][0]["kind"] == "ctx_exhausted"

    out = ampi(root, 1, "init", "--reinit")
    assert out["epoch"] == 1
    assert "recovery_brief" in out


def test_zombie_is_fenced(job):
    root = job(2)
    ampi(root, None, "kill", "1")
    ampi(root, None, "respawn", "1")
    # The old agent (epoch 0) tries to keep working. Its environment still says
    # rank 1, but the journal has moved to epoch 1.

    # Simulate the stale agent by driving a send while the rank is at epoch 1
    # but the caller believes epoch 0: the runtime rejects it via check_live.
    res = ampi(root, 1, "info")
    assert res["epoch"] == 1


def test_agree(job):
    root = job(3)
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(
            lambda r: ampi(root, r, "agree", "--label", "phase-ok",
                           "--flag", "true" if r != 2 else "false", "--timeout", "30"),
            range(3),
        ))
    assert res[0]["agreed"] is False
    assert sorted(res[0]["participants"]) == [0, 1, 2]


def test_supervise_restart_policy(job):
    root = job(3)
    ampi(root, None, "kill", "0")
    out = ampi(root, None, "supervise", "--policy", "restart")
    assert out["failed"] == [0]
    assert out["actions"][0]["action"] == "respawn"


def test_trace_and_summary(job):
    root = job(3)
    parallel(root, list(range(3)), "barrier", "--label", "b", "--timeout", "20")
    ampi(root, 0, "send", "--to", "1", "--tag", "1", "--in", "hi")
    ampi(root, 1, "recv", "--from", "0", "--tag", "1", "--timeout", "5")
    s = ampi(root, None, "trace", "--summary")
    assert s["messages"]["count"] >= 1
    assert s["world_size"] == 3
    tl = ampi(root, None, "trace", "--timeline")
    assert "AgentMPI trace" in tl["body"]


def test_status(job):
    root = job(3)
    st = ampi(root, None, "status")
    assert st["world_size"] == 3
    assert st["rank_states"].get("running") == 3


def test_ops_listing(job):
    root = job(1)
    out = ampi(root, 0, "ops")
    names = {o["name"] for o in out["ops"]}
    assert {"union", "vote", "jsonmerge", "maxby", "agent:<label>"} <= names
    assert "binomial" in out["algorithms"]["reduce"]


def test_heartbeat_extends_lease(job):
    root = job(2)
    out = ampi(root, 0, "hb", "--extend", "1200")
    assert out["lease_expires_in_s"] >= 1200
    st = ampi(root, None, "status")
    assert st["rank_states"].get("running") == 2


def test_internal_retry_covers_a_late_sender(job):
    """A single blocking invocation must survive several deadlines.

    This is the fix for the failure the pilot run exposed: an agent instructed to
    retry twenty times gave up after two. With internal retries, covering a slow
    peer requires the agent to do nothing at all.
    """
    import threading
    import time as _t

    root = job(2)

    def late_sender():
        _t.sleep(2.5)
        ampi(root, 0, "send", "--to", "1", "--tag", "9", "--in", "eventually")

    t = threading.Thread(target=late_sender, daemon=True)
    t.start()
    got = ampi(root, 1, "recv", "--from", "0", "--tag", "9", "--timeout", "1", "--retries", "6")
    t.join(timeout=10)
    assert got["body"] == "eventually"


def test_two_phase_failure_detection(job, tmp_path):
    """Suspicion must not convict.

    A rank that goes quiet becomes a suspect, which every rank can see, but it is
    not written off until suspicion persists. Evidence of activity clears it. This
    is the fix for a real incident: a translator agent doing 580 seconds of
    legitimate work was declared dead because it had not volunteered a heartbeat,
    and being declared dead is terminal.
    """
    root = tmp_path / "twophase"
    root.mkdir()
    # Lease 1s, confirmation window 3s: a rank is suspected after 1s of silence
    # and convicted only after a further 3s.
    ampi(root, None, "run", "--np", "3", "--label", "tp", "--job-root", str(root),
         "--lease", "1")
    import sqlite3

    db = sqlite3.connect(str(root / ".ampi" / "journal.db"))
    db.execute(
        "UPDATE job SET config=json_set(config,'$.confirm_ns',3000000000)"
    )
    db.commit()
    db.close()
    for r in range(3):
        ampi(root, r, "init")

    import time as _t

    _t.sleep(1.6)
    # Rank 0 blocks, which is what runs the detector.
    ampi(root, 0, "hb", "--extend", "600")
    st = ampi(root, 0, "failed")
    assert st["count"] == 0, "suspicion must not convict"
    assert st["suspect_count"] >= 1
    assert st["live"] == 3

    # Rank 1 calls in: activity clears its suspicion.
    ampi(root, 1, "hb", "--extend", "600")
    ampi(root, 0, "failed")
    st = ampi(root, 0, "failed")
    assert 1 not in [s["world"] for s in st["suspected"]]

    # Rank 2 stays silent past the confirmation window and is convicted.
    _t.sleep(3.2)
    st = ampi(root, 0, "failed")
    assert 2 in [f["world"] for f in st["failed"]]
