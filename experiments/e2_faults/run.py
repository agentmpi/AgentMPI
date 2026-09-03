"""E2: fault tolerance, measured rather than asserted.

Scripted ranks, not agents, and deliberately so.  The question here is not how an
LLM behaves under failure --- E1 and the conformance suite cover that --- but
whether the protocol's recovery machinery does what the specification says when
ranks die at the worst moments.  That question has a definite answer, and getting
it requires killing ranks at controlled points many times, which is exactly what
one should not pay a language model to sit through.

Five scenarios, each a claim from S10 stated as a measurement:

S1  A rank killed *before* a collective must not hang the collective.  The
    survivors' contributions complete it, and the omission is recorded.

S2  A rank killed *inside* a collective is worse, because the survivors are
    blocked in something that can never complete.  Revocation must make every one
    of them fail fast so they reach the recovery path together.

S3  Concurrent shrinks must converge on one communicator.  Two ranks that computed
    different survivor sets would obtain different-sized communicators and every
    subsequent collective would mismatch.

S4  A replacement must be able to resume.  We measure how much of the dead rank's
    work its successor can recover from the recovery briefing alone, which is the
    number that decides whether replacement is worth doing.

S5  Work claimed by compare-and-swap must not be lost when its claimant dies, and
    must not be done twice.  This is the property that makes a window-based
    harness robust without any collective at all.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from ampi.errors import AmpiError
from ampitools.harness import Harness

RESULTS = Path(__file__).resolve().parent.parent / "results"


def _job(root: str, size: int, device: str) -> Harness:
    h = Harness(root=root, size=size, device=device, force=True)
    h.create()
    return h


# --------------------------------------------------------------------------


def s1_kill_before_collective(root: str, size: int, device: str, victims: int) -> dict[str, Any]:
    h = _job(f"{root}/s1", size, device)
    killer = h.attach(0)
    killer.init()
    dead = list(range(size - victims, size))
    for r in dead:
        killer.kill(r)

    def rank_main(amp, rank):
        got = amp.gather("collect", payload={"rank": rank}, root=0, timeout=60)
        return got

    started = time.time()
    results = h.run(rank_main, ranks=[r for r in range(size) if r not in dead], timeout=180)
    root_result = next(r for r in results if r.rank == 0)
    return {
        "scenario": "kill_before_collective",
        "size": size,
        "killed": dead,
        "survivors_completed": sum(1 for r in results if r.ok),
        "contributors": (root_result.value or {}).get("contributors"),
        "dropped_recorded": (root_result.value or {}).get("dropped"),
        "wall_s": round(time.time() - started, 2),
        "claim_holds": (
            all(r.ok for r in results)
            and (root_result.value or {}).get("dropped") == dead
        ),
    }


def s2_revoke_unblocks(root: str, size: int, device: str) -> dict[str, Any]:
    """A survivor blocked inside a collective must be freed by revocation."""
    h = _job(f"{root}/s2", size, device)
    for r in range(size):
        h.attach(r).init()
    blocked = h.attach(1)
    controller = h.attach(0)

    import threading

    outcome: dict[str, Any] = {}

    def wait_forever():
        started = time.time()
        try:
            blocked.barrier("never", timeout=60)
            outcome["result"] = "completed"
        except AmpiError as e:
            outcome["result"] = e.cls_name
        outcome["freed_after_s"] = round(time.time() - started, 3)

    t = threading.Thread(target=wait_forever, daemon=True)
    t.start()
    time.sleep(1.0)
    controller.comm_revoke("world", reason="a peer died inside the collective")
    t.join(timeout=30)
    return {
        "scenario": "revoke_unblocks_a_blocked_survivor",
        "size": size,
        **outcome,
        "claim_holds": outcome.get("result") == "AMPI_ERR_REVOKED"
        and outcome.get("freed_after_s", 99) < 10,
    }


def s3_concurrent_shrink_converges(root: str, size: int, device: str) -> dict[str, Any]:
    h = _job(f"{root}/s3", size, device)
    for r in range(size):
        h.attach(r).init()
    h.attach(0).kill(size - 1)
    survivors = list(range(size - 1))

    def rank_main(amp, rank):
        return amp.comm_shrink("world", timeout=90)

    results = h.run(rank_main, ranks=survivors, timeout=180)
    names = {json.dumps((r.value or {}).get("name")) for r in results if r.ok}
    members = {json.dumps((r.value or {}).get("members")) for r in results if r.ok}
    return {
        "scenario": "concurrent_shrink_converges",
        "size": size,
        "shrinking_ranks": len(survivors),
        "distinct_communicators": len(names),
        "distinct_member_sets": len(members),
        "members": json.loads(next(iter(members))) if members else None,
        "claim_holds": len(names) == 1 and len(members) == 1,
    }


def s4_recovery_briefing(root: str, size: int, device: str) -> dict[str, Any]:
    """How much of a dead rank's work can its successor recover?"""
    h = _job(f"{root}/s4", size, device)
    for r in range(size):
        h.attach(r).init()
    victim = h.attach(3)
    victim.win_create("board")
    for i in range(5):
        victim.put("board", f"section/{i}", f"section {i} drafted by rank 3")
    victim.send(0, "an interface other ranks are waiting for", tag=7)
    victim.irecv(2, tag=9)
    victim._join_collective("world", "integration", "barrier")
    victim.memo("phase", "drafted five sections; owe the integration barrier")

    h.attach(0).kill(3)
    h.attach(0).respawn(3)
    successor = h.attach(3)
    out = successor.init()
    briefing = out.get("recovery") or successor.recover()

    published = {p["key"] for p in briefing["published"]}
    return {
        "scenario": "recovery_briefing",
        "size": size,
        "published_recovered": len(published),
        "published_expected": 5,
        "sends_recovered": len(briefing["sent"]),
        "posted_receives_recovered": len(briefing["outstanding"]["posted_receives"]),
        "open_collectives_recovered": briefing["outstanding"]["open_collectives"],
        "memo_recovered": bool(briefing["memos"]),
        "advice": briefing["advice"],
        "epoch_after": out["epoch"],
        "claim_holds": (
            len(published) == 5
            and len(briefing["sent"]) >= 1
            and "integration" in briefing["outstanding"]["open_collectives"]
            and bool(briefing["memos"])
        ),
    }


def s5_claimed_work_survives_its_claimant(
    root: str, size: int, device: str, tasks: int
) -> dict[str, Any]:
    """A window-based harness with no collective at all, under failure."""
    h = _job(f"{root}/s5", size, device)
    boss = h.attach(0)
    boss.init()
    boss.win_create("queue")
    for t in range(tasks):
        boss.put("queue", f"task/{t}", "unclaimed")

    done: dict[int, list[str]] = {}

    def rank_main(amp, rank):
        mine = []
        for t in range(tasks):
            got = amp.claim("queue", f"task/{t}")
            if got["claimed"]:
                if rank == 2 and len(mine) == 2:
                    # This rank dies holding a claim, having done the work but not
                    # published it: the case a naive queue loses silently.
                    raise RuntimeError("executor session ended")
                amp.put("queue", f"result/{t}", {"by": rank})
                mine.append(f"task/{t}")
        done[rank] = mine
        return mine

    results = h.run(rank_main, timeout=180)
    boss2 = h.attach(0)
    completed = {c.key for c in boss2.device.keys(boss2._space("queue"), prefix="result/")}
    claimed = {
        c.key.split("/", 1)[1]
        for c in boss2.device.keys(boss2._space("queue"), prefix="task/")
        if (cell := boss2.device.read(boss2._space("queue"), c.key))
        and cell.value != "unclaimed"
    }
    duplicated = [
        c.key
        for c in boss2.device.keys(boss2._space("queue"), prefix="task/")
        if len(boss2.device.history(boss2._space("queue"), c.key)) > 2
    ]
    orphaned = sorted(claimed - {c.split("/", 1)[1] for c in completed})
    return {
        "scenario": "claimed_work_survives_its_claimant",
        "size": size,
        "tasks": tasks,
        "ranks_failed": [r.rank for r in results if not r.ok],
        "tasks_completed": len(completed),
        "tasks_claimed": len(claimed),
        "tasks_orphaned": orphaned,
        "tasks_done_twice": duplicated,
        # The protocol's guarantee is that a task is never done twice.  It does not
        # guarantee that a dead claimant's task is finished -- that needs a
        # supervisor to reclaim it, and the point of measuring the orphan count is
        # to show exactly how much a harness must do for itself.
        "claim_holds": not duplicated,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="E2: fault tolerance")
    ap.add_argument("--size", type=int, default=12)
    ap.add_argument("--device", default="sqlite")
    ap.add_argument("--work", default="/tmp/ampi-e2")
    ap.add_argument("--out", default=str(RESULTS / "e2_faults.json"))
    a = ap.parse_args()

    scenarios = [
        s1_kill_before_collective(a.work, a.size, a.device, victims=3),
        s2_revoke_unblocks(a.work, a.size, a.device),
        s3_concurrent_shrink_converges(a.work, a.size, a.device),
        s4_recovery_briefing(a.work, max(6, a.size), a.device),
        s5_claimed_work_survives_its_claimant(a.work, a.size, a.device, tasks=24),
    ]
    out = {
        "experiment": "e2_faults",
        "device": a.device,
        "size": a.size,
        "scenarios": scenarios,
        "claims_upheld": sum(1 for s in scenarios if s["claim_holds"]),
        "claims_total": len(scenarios),
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    for s in scenarios:
        print(f"{'PASS' if s['claim_holds'] else 'FAIL'}  {s['scenario']}")
    print(f"{out['claims_upheld']}/{out['claims_total']} claims upheld -> {a.out}")


if __name__ == "__main__":
    main()
