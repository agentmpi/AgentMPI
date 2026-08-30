from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

from agentmpi.comm import Communicator
from agentmpi.errors import ContextBudgetExceeded, DeadRankError
from agentmpi.types import Lifecycle


def test_context_budget_trips(tmp_path: Path):
    comm = Communicator(tmp_path, rank=0, size=1, bootstrap=True, context_budget=10)
    comm.send("hi", dest=0, tag=1)
    try:
        comm.recv(source=0, tag=1, timeout_s=2)
        # tiny payload may fit; force a large charge
        comm._charge("word " * 100)
        raised = False
    except ContextBudgetExceeded:
        raised = True
    if not raised:
        try:
            comm._charge("token " * 200)
            raised = False
        except ContextBudgetExceeded:
            raised = True
    assert raised


def test_context_compact_resets_budget(tmp_path: Path):
    comm = Communicator(tmp_path, rank=0, size=1, bootstrap=True, context_budget=50)
    comm._charge("abcd" * 10)
    comm.context_compact("ok")
    assert comm._context_tokens < 50


def test_dead_rank_unblocks_recv(tmp_path: Path):
    home = tmp_path / "dead"
    Communicator(home, rank=0, size=2, bootstrap=True)

    def victim():
        comm = Communicator(home, rank=1, size=2, failure_timeout_s=0.4, poll_s=0.02)
        comm.heartbeat(Lifecycle.ACTIVE)
        time.sleep(0.15)
        comm.heartbeat(Lifecycle.FAILED, note="injected")
        return "failed"

    def waiter():
        comm = Communicator(home, rank=0, size=2, failure_timeout_s=0.4, poll_s=0.02)
        try:
            comm.recv(source=1, tag=7, timeout_s=5)
            return "got"
        except DeadRankError as exc:
            return f"dead:{exc.ranks}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(victim)
        f0 = pool.submit(waiter)
        assert f1.result() == "failed"
        assert f0.result().startswith("dead:")


def test_shrink_renumbers_live_ranks(tmp_path: Path):
    home = tmp_path / "sh"
    Communicator(home, rank=0, size=3, bootstrap=True)

    def run(rank: int):
        comm = Communicator(home, rank=rank, size=3, failure_timeout_s=1.5, poll_s=0.02)
        comm.heartbeat(Lifecycle.ACTIVE)
        if rank == 2:
            comm.heartbeat(Lifecycle.FAILED, note="crash")
            return None
        deadline = time.time() + 4
        while time.time() < deadline:
            comm.heartbeat(Lifecycle.ACTIVE)
            dead = comm.probe_failures()
            st = comm.transport.read_status(2)
            if (st and st.state == Lifecycle.FAILED.value) or 2 in dead or 2 in comm._dead:
                break
            time.sleep(0.05)
        new = comm.shrink()
        return {"old": rank, "new_rank": new.rank, "new_size": new.size, "name": new.name}

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    live = [r for r in results if r]
    assert len(live) == 2
    assert {r["new_size"] for r in live} == {2}
    assert {r["new_rank"] for r in live} == {0, 1}
