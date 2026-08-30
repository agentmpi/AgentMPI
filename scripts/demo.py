#!/usr/bin/env python3
"""Produce a demonstration trace that exercises the whole protocol.

This is not an experiment; it is a fixture. It drives every mechanism the
protocol offers -- bulk-synchronous phases, an agent-evaluated reduction tree,
one-sided shared state with contention, an injected failure, and ULFM-style
revoke/shrink recovery -- with stub executors, so that the trace viewer has
something representative to display and so that the interaction between
mechanisms is exercised end to end.

Usage:
    python3 scripts/demo.py [--np 12] [--out runs/demo]
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ampi import collectives, ft, p2p, rma  # noqa: E402
from ampi.core import Config, bind, init_rank  # noqa: E402
from ampi.errors import AmpiError, ErrClass  # noqa: E402
from ampi.journal import Journal  # noqa: E402
from ampi.launcher import create as launcher_create  # noqa: E402

SECTIONS = [
    "Preface: why message passing",
    "The portability crisis of 1992",
    "Communicators and contexts",
    "Collective communication",
    "One-sided operations",
    "Fault tolerance",
    "Derived datatypes",
    "Performance models",
    "The progress engine",
    "Sessions and initialisation",
    "Partitioned communication",
    "Retrospective",
]


def rank_body(root: Path, rank: int, np: int, killed: int, log: List[str]) -> None:
    j = Journal(root)
    init_rank(j, rank, agent_id=f"demo-{rank}", role="coordinator" if rank == 0 else "worker")
    ctx = bind(j, rank=rank)
    rng = random.Random(1000 + rank)

    def think(lo: float, hi: float) -> None:
        """Stand in for a model call. Heavy-tailed, as agent latency is."""
        base = rng.uniform(lo, hi)
        if rng.random() < 0.15:
            base *= rng.uniform(2.5, 5.0)
        time.sleep(base)

    try:
        # ---- phase 1: the coordinator broadcasts the plan ------------------
        plan = json.dumps(
            {
                "goal": "translate a systems textbook into Simplified Chinese",
                "sections": len(SECTIONS),
                "rules": ["keep terminology consistent", "do not translate identifiers"],
            },
            indent=2,
        )
        collectives.bcast(
            ctx, root=0, text=(plan if rank == 0 else None), label="plan",
            timeout_ns=120 * 10 ** 9, materialize=True,
        )

        # ---- phase 2: the coordinator scatters section assignments --------
        parts = None
        if rank == 0:
            parts = [
                json.dumps({"section": i, "title": SECTIONS[i % len(SECTIONS)]})
                for i in range(np)
            ]
        slice_ = collectives.scatter(
            ctx, root=0, parts=parts, label="assign", timeout_ns=120 * 10 ** 9, materialize=True
        )
        assignment = json.loads(slice_["body"])
        ft.memo_put(j, rank, ctx.epoch, "section", str(assignment["section"]))

        # ---- phase 3: work, publishing findings into the shared window ----
        rma.create(ctx, "shared")
        think(0.2, 0.6)
        terms = [
            {"en": "communicator", "zh": "通信器"},
            {"en": f"section-{assignment['section']}-term", "zh": f"术语{assignment['section']}"},
        ]
        rma.accumulate(ctx, "shared", "glossary", json.dumps(terms), op="union",
                       note=f"terms from section {assignment['section']}")
        rma.put(ctx, "shared", f"draft/{assignment['section']:02d}",
                f"# {assignment['title']}\n\n" + ("翻译内容 " * 60), schema="markdown")

        # ---- phase 4: a barrier closing the drafting epoch ----------------
        rma.fence(ctx, "shared", label="drafts-in", timeout_ns=180 * 10 ** 9, quorum=0.9)

        # ---- phase 5: an agent-evaluated reduction over the glossaries ----
        res = collectives.reduce_(
            ctx, op="agent:merge_glossary", text=json.dumps(terms), root=0, label="glossary",
            algo="binomial", timeout_ns=240 * 10 ** 9, materialize=False,
        )
        merges = 0
        while res.get("action_required") == "merge":
            merges += 1
            left = json.loads(Path(res["left_file"]).read_text(encoding="utf-8"))
            right = json.loads(Path(res["right_file"]).read_text(encoding="utf-8"))
            think(0.3, 0.9)  # a real agent reconciles conflicting translations here
            seen: Dict[str, Dict[str, str]] = {}
            for item in list(left) + list(right):
                seen.setdefault(item["en"], item)
            out = Path(res["suggested_out"])
            out.write_text(json.dumps(sorted(seen.values(), key=lambda d: d["en"]),
                                      ensure_ascii=False), encoding="utf-8")
            res = collectives.reduce_commit(ctx, res["step"], out.read_text(encoding="utf-8"),
                                            timeout_ns=240 * 10 ** 9)
        log.append(f"rank {rank}: {merges} glossary merges")

        # ---- phase 6: peer review over point-to-point messages -----------
        peer = (rank + 1) % np
        p2p.send(ctx, peer, 101, f"review request for section {assignment['section']}",
                 idem=f"rev-{rank}")
        try:
            p2p.recv(ctx, (rank - 1) % np, 101, timeout_ns=90 * 10 ** 9, materialize=True)
        except AmpiError as exc:
            if exc.err_class not in (ErrClass.TIMEOUT, ErrClass.PROC_FAILED,
                                     ErrClass.PROC_FAILED_PENDING):
                raise
            log.append(f"rank {rank}: review peer unavailable ({exc.err_class})")

        # ---- phase 7: the failure episode --------------------------------
        # One rank is killed externally; the survivors detect it, revoke the
        # communicator so nobody is stuck in a collective, shrink, and finish.
        if rank == 0:
            time.sleep(0.4)
            ft.declare_failed(j, killed, kind="killed", by=0,
                              detail={"scenario": "demo fault injection"})
            ft.revoke(ctx, reason=f"rank {killed} died during review")
        else:
            time.sleep(0.8)
        try:
            collectives.barrier(ctx, label="review-done", timeout_ns=20 * 10 ** 9)
        except AmpiError as exc:
            if exc.err_class not in (ErrClass.REVOKED, ErrClass.TIMEOUT):
                raise
            log.append(f"rank {rank}: barrier failed with {exc.err_class}, recovering")
        if rank == killed:
            return
        ft.failure_ack(ctx)
        shrunk = ft.shrink(ctx, timeout_ns=120 * 10 ** 9, quorum=0.0)
        new_comm = shrunk["comm"]
        ctx2 = bind(j, rank=rank, comm=new_comm)
        collectives.barrier(ctx2, label="recovered", timeout_ns=120 * 10 ** 9)

        # ---- phase 8: a quorum vote over the survivors -------------------
        collectives.reduce_(
            ctx2, op="vote", text="ship it", root=0, label="ship", all_=True,
            timeout_ns=120 * 10 ** 9, materialize=True, quorum=0.9,
        )
        ft.agree(ctx2, label="done", flag=True, timeout_ns=120 * 10 ** 9, quorum=0.9)
        from ampi.core import finalize_rank

        finalize_rank(j, rank, ctx.epoch)
    except AmpiError as exc:
        # The killed rank is *supposed* to be fenced: being told to stop is the
        # correct outcome for an agent that was declared dead and replaced.
        expected = exc.err_class == ErrClass.FENCED and rank == killed
        log.append(f"rank {rank}: {'fenced as expected' if expected else 'ERROR ' + exc.err_class}"
                   + ("" if expected else f": {exc.message}"))
    except Exception as exc:  # noqa: BLE001 - a demo must report, not crash
        log.append(f"rank {rank}: ERROR {type(exc).__name__}: {exc}")
    finally:
        j.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--np", type=int, default=12)
    ap.add_argument("--out", default="runs/demo")
    args = ap.parse_args()
    root = Path(args.out).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    launcher_create(
        root, np=args.np, label="demo: protocol walkthrough",
        cfg=Config(eager_tokens=500, ctx_budget=20_000, timeout_ns=180 * 10 ** 9,
                   lease_ns=90 * 10 ** 9),
        fresh=True,
    )
    killed = max(1, args.np // 2)
    log: List[str] = []
    threads = [
        threading.Thread(target=rank_body, args=(root, r, args.np, killed, log), daemon=True)
        for r in range(args.np)
    ]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=400)
    wall = time.perf_counter() - t0
    j = Journal(root)
    from ampi.trace import text_timeline

    print(text_timeline(j, width=100))
    print()
    print(f"wall {wall:.1f}s  killed rank {killed}")
    for line in log:
        print(" ", line)
    j.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
