#!/usr/bin/env python3
"""Metrics for the fault-tolerance experiment.

The run this analyses was not planned. A job was requested at P=22, the agent
host enforced a limit of ten concurrent background agents, and sixteen ranks
never called ``AMPI_Init``. Rather than discard it we let it proceed, because a
73% launch failure exercises every mechanism in the fault-tolerance design at
once and does so without any of the artificiality of injected faults: nobody
chose which ranks would fail, when, or in what state.

What we want out of it is not a headline number but an audit: did each mechanism
fire, and did the survivors produce a coherent artefact? So the script reports
the failure timeline, which collectives closed and with how many contributions,
how subtrees were dropped, and what the survivors actually finished --- all read
from the journal rather than from anything an agent claimed.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ampi.journal import Journal  # noqa: E402
from ampi.trace import summarize  # noqa: E402


def analyse(root: Path) -> Dict[str, Any]:
    j = Journal(root)
    job = j.job_row()
    t0 = int(job["created_ns"])
    P = int(job["world_size"])
    exp_path = root / "experiment.json"
    exp = json.loads(exp_path.read_text()) if exp_path.exists() else {}

    ranks = [dict(r) for r in j.q("SELECT * FROM rank WHERE job=? ORDER BY rank", (j.job_id,))]
    states = Counter(str(r["state"]) for r in ranks)
    active = [r for r in ranks if int(r["calls"] or 0) > 2]

    failures = [
        {
            "rank": int(f["rank"]),
            "epoch": int(f["epoch"]),
            "kind": str(f["kind"]),
            "t": round((int(f["detected_ns"]) - t0) / 1e9, 1),
            "detail": json.loads(f["detail"] or "{}"),
        }
        for f in j.q("SELECT * FROM failure WHERE job=? ORDER BY detected_ns", (j.job_id,))
    ]
    kinds = Counter(f["kind"] for f in failures)

    colls: List[Dict[str, Any]] = []
    for k in j.q("SELECT * FROM coll WHERE job=? ORDER BY created_ns", (j.job_id,)):
        parts = j.q("SELECT * FROM coll_part WHERE coll=?", (str(k["id"]),))
        contributed = sum(1 for p in parts if p["in_obj"] is not None)
        done = sum(1 for p in parts if str(p["state"]) == "done")
        colls.append(
            {
                "op": str(k["op"]),
                "algo": str(k["algo"]),
                "reduce_op": k["reduce_op"],
                "label": (json.loads(k["params"] or "{}") or {}).get("label"),
                "state": str(k["state"]),
                "participants": len(parts),
                "contributed": contributed,
                "done": done,
                "t_start": round((int(k["created_ns"]) - t0) / 1e9, 1),
                "t_end": (round((int(k["closed_ns"]) - t0) / 1e9, 1) if k["closed_ns"] else None),
                "duration_s": (round((int(k["closed_ns"]) - int(k["created_ns"])) / 1e9, 1)
                               if k["closed_ns"] else None),
            }
        )
    closed = [c for c in colls if c["state"] == "closed"]

    dropped = [
        {"rank": int(e["rank"]), "dropped_peer": int(e["peer"]) if e["peer"] is not None else None,
         "t": round((int(e["ts_ns"]) - t0) / 1e9, 1), "why": str(e["status"])}
        for e in j.q(
            "SELECT rank,peer,ts_ns,status FROM event WHERE job=? AND kind='coll_drop_subtree'"
            " ORDER BY ts_ns", (j.job_id,))
    ]
    suspects = [
        {"rank": int(e["rank"]), "t": round((int(e["ts_ns"]) - t0) / 1e9, 1),
         "detail": json.loads(e["detail"] or "{}")}
        for e in j.q("SELECT rank,ts_ns,detail FROM event WHERE job=? AND kind='suspect'"
                     " ORDER BY ts_ns", (j.job_id,))
    ]
    cleared = [
        {"rank": int(e["rank"]), "t": round((int(e["ts_ns"]) - t0) / 1e9, 1)}
        for e in j.q("SELECT rank,ts_ns FROM event WHERE job=? AND kind='suspicion_cleared'"
                     " ORDER BY ts_ns", (j.job_id,))
    ]
    errs = Counter(
        str(e["status"]) for e in j.q(
            "SELECT status FROM event WHERE job=? AND kind='error'", (j.job_id,))
    )

    win_cells: Dict[str, int] = {}
    for w in j.q("SELECT id,name FROM win WHERE job=?", (j.job_id,)):
        win_cells[str(w["name"])] = int(
            j.scalar("SELECT COUNT(*) FROM win_cell WHERE win=?", (str(w["id"]),), 0)
        )
    drafts = int(j.scalar(
        "SELECT COUNT(*) FROM win_cell WHERE key LIKE 'draft/%'", (), 0))
    merges = int(j.scalar("SELECT COUNT(*) FROM reduce_step WHERE state='committed'", (), 0))
    s = summarize(j)
    j.close()

    survivors = sorted(int(r["rank"]) for r in active)
    finalized = sorted(int(r["rank"]) for r in ranks if str(r["state"]) == "finalized")
    return {
        "root": str(root),
        "world_size": P,
        "scenario": exp.get("scenario", {}),
        "wall_s": s["wall_s"],
        "rank_states": dict(states),
        "ranks_that_ever_ran": survivors,
        "ranks_finalized": finalized,
        "launch_failure_rate": round((P - len(survivors)) / P, 4),
        "failures": failures,
        "failure_kinds": dict(kinds),
        "suspicions_raised": suspects,
        "suspicions_cleared": cleared,
        "collectives": colls,
        "collectives_closed": len(closed),
        "collectives_total": len(colls),
        "subtrees_dropped": dropped,
        "agent_merges_committed": merges,
        "window_cells": win_cells,
        "artefact_sections": drafts,
        "errors_by_class": dict(errs),
        "context": s["context"],
        "messages": s["messages"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    res = analyse(Path(args.root).resolve())

    # Macros for the paper.
    P = res["world_size"]
    ran = len(res["ranks_that_ever_ran"])
    res["macros"] = {
        "eThreeRequestedP": P,
        "eThreeStartedP": ran,
        "eThreeNeverStarted": P - ran,
        "eThreeFailureRatePct": int(round(100 * res["launch_failure_rate"])),
        "eThreeFinalized": len(res["ranks_finalized"]),
        "eThreeCollsClosed": res["collectives_closed"],
        "eThreeCollsTotal": res["collectives_total"],
        "eThreeDeclaredFailed": res["rank_states"].get("failed", 0),
        "eThreeMerges": res["agent_merges_committed"],
        "eThreeSections": res["artefact_sections"],
        "eThreeWall": int(res["wall_s"]),
        "eThreeSubtreesDropped": len(res["subtrees_dropped"]),
    }
    text = json.dumps(res, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
        print(f"  requested P={P}, ever ran {ran}, finalized {len(res['ranks_finalized'])}")
        print(f"  declared failed: {res['rank_states'].get('failed', 0)}   "
              f"failure kinds: {res['failure_kinds']}")
        print(f"  collectives closed: {res['collectives_closed']}/{res['collectives_total']}")
        for c in res["collectives"]:
            print(f"    {c['op']:<10} {str(c['label']):<14} {c['algo']:<14} {c['state']:<8} "
                  f"contrib={c['contributed']}/{c['participants']} dur={c['duration_s']}s")
        print(f"  subtrees dropped: {len(res['subtrees_dropped'])}  "
              f"agent merges: {res['agent_merges_committed']}")
        print(f"  artefact sections in window: {res['artefact_sections']}")
        print(f"  suspicions raised {len(res['suspicions_raised'])}, "
              f"cleared {len(res['suspicions_cleared'])}")
        print(f"  errors: {res['errors_by_class']}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
