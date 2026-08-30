#!/usr/bin/env python3
"""Fault, lock, lifecycle, and context-budget study.

Injects the failure modes the protocol is designed to make programmable:
executor death, lost shared state, lock contention, and context OOM.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentmpi.comm import Communicator
from agentmpi.errors import ContextBudgetExceeded, DeadRankError
from agentmpi.types import Lifecycle, Op
from experiments.common import run_spmd, write_result


def death_and_shrink(n: int, victims: list[int], home: Path) -> dict:
    Communicator(home, rank=0, size=n, bootstrap=True)

    def fn(comm: Communicator):
        comm.heartbeat(Lifecycle.ACTIVE)
        comm.barrier(timeout_s=15)
        if comm.rank in victims:
            comm.heartbeat(Lifecycle.FAILED, note="injected crash")
            return {"rank": comm.rank, "fate": "dead"}
        # Survivors keep heartbeating until the victims are visible, then shrink.
        deadline = time.time() + 8
        while time.time() < deadline:
            comm.heartbeat(Lifecycle.ACTIVE)
            comm.probe_failures()
            if all(v in comm._dead for v in victims):
                break
            time.sleep(0.05)
        new = comm.shrink()
        # Continue the job on the repaired communicator: allreduce a 1.
        total = new.allreduce(1, op=Op.SUM, timeout_s=15)
        return {"rank": comm.rank, "fate": "survived", "new_rank": new.rank, "new_size": new.size, "allreduce": total}

    from concurrent.futures import ThreadPoolExecutor

    def worker(rank: int):
        comm = Communicator(home, rank=rank, size=n, failure_timeout_s=1.5, poll_s=0.02, context_budget=200_000)
        comm.heartbeat(Lifecycle.ACTIVE)
        try:
            return fn(comm)
        finally:
            if comm.rank not in victims:
                comm.finalize()

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(worker, range(n)))
    survivors = [r for r in results if r and r.get("fate") == "survived"]
    return {
        "n": n,
        "victims": victims,
        "survivors": survivors,
        "repaired_size": survivors[0]["new_size"] if survivors else None,
        "allreduce_ok": all(s["allreduce"] == n - len(victims) for s in survivors),
    }


def lock_contention(home: Path, n: int = 8, iters: int = 20) -> dict:
    def fn(comm: Communicator):
        comm.win_create("counter", {"n": 0})
        comm.barrier(timeout_s=15)
        for _ in range(iters):
            comm.win_lock("counter")
            try:
                val = comm.get("counter")
                val["n"] = int(val["n"]) + 1
                comm.put("counter", val)
            finally:
                comm.win_unlock("counter")
        comm.barrier(timeout_s=15)
        return comm.get("counter")["n"]

    results, summary = run_spmd(home, n, fn)
    return {"n": n, "iters": iters, "final": results[0], "expected": n * iters, "ok": results[0] == n * iters, **summary}


def context_oom(home: Path) -> dict:
    comm = Communicator(home, rank=0, size=1, bootstrap=True, context_budget=32)
    tripped = False
    try:
        comm._charge("abcd" * 40)
    except ContextBudgetExceeded:
        tripped = True
    comm.context_compact("summary of prior work")
    after = comm._context_tokens
    # recv-path charge
    comm2_home = home / "recv"
    def fn(c: Communicator):
        if c.rank == 0:
            c.send("word " * 50, dest=1, tag=1)
            return "sent"
        try:
            c.recv(source=0, tag=1, timeout_s=5)
            return "accepted"
        except ContextBudgetExceeded:
            return "oom"

    results, _ = run_spmd(comm2_home, 2, fn, context_budget=20)
    return {"local_trip": tripped, "tokens_after_compact": after, "recv_path": results}


def recv_unblocks_on_death(home: Path) -> dict:
    Communicator(home, rank=0, size=2, bootstrap=True)

    def victim():
        comm = Communicator(home, rank=1, size=2, failure_timeout_s=0.4, poll_s=0.02)
        comm.heartbeat(Lifecycle.ACTIVE)
        time.sleep(0.2)
        comm.heartbeat(Lifecycle.FAILED, note="offline")
        return "offline"

    def waiter():
        comm = Communicator(home, rank=0, size=2, failure_timeout_s=0.4, poll_s=0.02)
        t0 = time.time()
        try:
            comm.recv(source=1, tag=99, timeout_s=6)
            return {"status": "unexpected-success", "dt": time.time() - t0}
        except DeadRankError as exc:
            return {"status": "unblocked", "dead": exc.ranks, "dt": time.time() - t0}

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(victim)
        f0 = pool.submit(waiter)
        return {"victim": f1.result(), "waiter": f0.result()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="experiments/results/fault.json")
    args = parser.parse_args()
    base = ROOT / "experiments/results/.ampi" / "fault"
    payload = {
        "experiment": "fault",
        "death_and_shrink": death_and_shrink(8, [3, 6], base / "shrink"),
        "lock_contention": lock_contention(base / "locks", n=8, iters=25),
        "context_oom": context_oom(base / "oom"),
        "recv_unblocks": recv_unblocks_on_death(base / "unblock"),
    }
    write_result(Path(args.out), payload)
    print("fault study:")
    print("  shrink repaired_size", payload["death_and_shrink"]["repaired_size"], "allreduce_ok", payload["death_and_shrink"]["allreduce_ok"])
    print("  locks", payload["lock_contention"]["final"], "/", payload["lock_contention"]["expected"], "ok", payload["lock_contention"]["ok"])
    print("  oom", payload["context_oom"])
    print("  recv", payload["recv_unblocks"]["waiter"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
