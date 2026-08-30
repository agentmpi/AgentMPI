"""Trace export and analysis.

HPC has a mature tradition of *post-mortem* performance analysis: instrument the
library (PMPI), write an event stream (SLOG-2, OTF2), and look at it in a
timeline viewer (Jumpshot, Vampir, Paraver). The reason the tradition exists is
that a parallel program's behaviour is not visible from any single process's
output, and the same is emphatically true of a multi-agent system -- where the
usual debugging artefact is a pile of unordered chat transcripts.

AgentMPI records the same event vocabulary the HPC tools use: state intervals
per rank, message arrows with matched send/receive endpoints, and collective
intervals with participant sets. Because every event is already in the journal
(the runtime is durable by construction), tracing is free and always on, which
is a luxury an in-memory MPI implementation does not have.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from typing import Any, Dict, List, Optional

from .journal import Journal, now_ns


def export(j: Journal, *, limit: Optional[int] = None) -> Dict[str, Any]:
    """Export the trace in a self-contained JSON form for the viewer."""
    job = j.job_row()
    t0 = int(job["created_ns"])
    rows = j.q(
        "SELECT * FROM event WHERE job=? ORDER BY ts_ns"
        + (f" LIMIT {int(limit)}" if limit else ""),
        (j.job_id,),
    )
    events: List[Dict[str, Any]] = []
    for r in rows:
        events.append(
            {
                "id": int(r["id"]),
                "t": round((int(r["ts_ns"]) - t0) / 1e9, 4),
                "dur": round(int(r["dur_ns"]) / 1e9, 4),
                "rank": int(r["rank"]) if r["rank"] is not None else None,
                "epoch": int(r["epoch"]) if r["epoch"] is not None else None,
                "kind": str(r["kind"]),
                "phase": r["phase"],
                "comm": r["comm"],
                "peer": int(r["peer"]) if r["peer"] is not None else None,
                "tag": int(r["tag"]) if r["tag"] is not None else None,
                "coll": r["coll"],
                "win": r["win"],
                "key": r["wkey"],
                "msg": int(r["msg_seq"]) if r["msg_seq"] is not None else None,
                "tokens": int(r["tokens"]),
                "bytes": int(r["nbytes"]),
                "status": r["status"],
                "detail": json.loads(r["detail"]) if r["detail"] else None,
            }
        )
    msgs = j.q(
        "SELECT seq,comm,src,dst,tag,tokens,mode,sent_ns,matched_ns,delivered_ns,status,coll"
        " FROM msg WHERE job=? ORDER BY seq",
        (j.job_id,),
    )
    arrows = [
        {
            "seq": int(m["seq"]),
            "comm": str(m["comm"]),
            "src": int(m["src"]),
            "dst": int(m["dst"]),
            "tag": int(m["tag"]),
            "tokens": int(m["tokens"]),
            "mode": str(m["mode"]),
            "t_send": round((int(m["sent_ns"]) - t0) / 1e9, 4),
            "t_recv": (round((int(m["delivered_ns"]) - t0) / 1e9, 4) if m["delivered_ns"] else None),
            "status": str(m["status"]),
            "coll": m["coll"],
        }
        for m in msgs
    ]
    colls = j.q("SELECT * FROM coll WHERE job=? ORDER BY created_ns", (j.job_id,))
    collectives = [
        {
            "id": str(k["id"]),
            "comm": str(k["comm"]),
            "op": str(k["op"]),
            "reduce_op": k["reduce_op"],
            "algo": str(k["algo"]),
            "root": int(k["root"]) if k["root"] is not None else None,
            "label": (json.loads(k["params"] or "{}") or {}).get("label"),
            "state": str(k["state"]),
            "t_start": round((int(k["created_ns"]) - t0) / 1e9, 4),
            "t_end": (round((int(k["closed_ns"]) - t0) / 1e9, 4) if k["closed_ns"] else None),
            "nparts": int(k["nparts"]),
            "participants": [
                {
                    "rank": int(p["crank"]),
                    "state": str(p["state"]),
                    "t_join": round((int(p["joined_ns"]) - t0) / 1e9, 4),
                    "t_done": (round((int(p["done_ns"]) - t0) / 1e9, 4) if p["done_ns"] else None),
                    "rounds": int(p["rounds"]),
                }
                for p in j.q("SELECT * FROM coll_part WHERE coll=? ORDER BY crank", (str(k["id"]),))
            ],
        }
        for k in colls
    ]
    ranks = [
        {
            "rank": int(r["rank"]),
            "epoch": int(r["epoch"]),
            "state": str(r["state"]),
            "role": r["role"],
            "ctx_used": int(r["ctx_used"]),
            "ctx_budget": int(r["ctx_budget"]),
            "ctx_hwm": int(r["ctx_hwm"]),
            "calls": int(r["calls"]),
            "t_init": (round((int(r["init_ns"]) - t0) / 1e9, 4) if r["init_ns"] else None),
            "t_fini": (round((int(r["fini_ns"]) - t0) / 1e9, 4) if r["fini_ns"] else None),
        }
        for r in j.q("SELECT * FROM rank WHERE job=? ORDER BY rank", (j.job_id,))
    ]
    failures = [
        {
            "rank": int(f["rank"]),
            "epoch": int(f["epoch"]),
            "kind": str(f["kind"]),
            "t": round((int(f["detected_ns"]) - t0) / 1e9, 4),
            "detail": json.loads(f["detail"] or "{}"),
        }
        for f in j.q("SELECT * FROM failure WHERE job=? ORDER BY id", (j.job_id,))
    ]
    windows = []
    for w in j.q("SELECT * FROM win WHERE job=?", (j.job_id,)):
        cells = j.q(
            "SELECT key,version,tokens,writer,written_ns FROM win_cell WHERE win=? ORDER BY key",
            (str(w["id"]),),
        )
        windows.append(
            {
                "id": str(w["id"]),
                "name": str(w["name"]),
                "cells": [
                    {
                        "key": str(c["key"]),
                        "version": int(c["version"]),
                        "tokens": int(c["tokens"]),
                        "writer": int(c["writer"]) if c["writer"] is not None else None,
                        "t": round((int(c["written_ns"]) - t0) / 1e9, 4),
                    }
                    for c in cells
                ],
            }
        )
    return {
        "job": j.job_id,
        "label": job["label"],
        "world_size": int(job["world_size"]),
        "t0_ns": t0,
        "duration_s": round((now_ns() - t0) / 1e9, 3),
        "config": json.loads(job["config"]),
        "ranks": ranks,
        "events": events,
        "messages": arrows,
        "collectives": collectives,
        "failures": failures,
        "windows": windows,
        "summary": summarize(j),
    }


def summarize(j: Journal) -> Dict[str, Any]:
    """Aggregate metrics: the numbers the evaluation section reports."""
    job = j.job_row()
    t0 = int(job["created_ns"])
    P = int(job["world_size"])
    counters: Dict[str, int] = {
        str(c["name"]): int(c["value"])
        for c in j.q("SELECT name, SUM(value) AS value FROM counter WHERE job=? GROUP BY name", (j.job_id,))
    }
    per_rank_ctx = [
        (int(r["rank"]), int(r["ctx_hwm"]), int(r["ctx_budget"]), int(r["calls"]))
        for r in j.q("SELECT rank,ctx_hwm,ctx_budget,calls FROM rank WHERE job=? ORDER BY rank", (j.job_id,))
    ]
    lat = [
        int(m["delivered_ns"]) - int(m["sent_ns"])
        for m in j.q(
            "SELECT sent_ns,delivered_ns FROM msg WHERE job=? AND delivered_ns IS NOT NULL AND kind='p2p'",
            (j.job_id,),
        )
    ]
    coll_durs: Dict[str, List[float]] = defaultdict(list)
    for k in j.q("SELECT op,algo,created_ns,closed_ns FROM coll WHERE job=? AND closed_ns IS NOT NULL", (j.job_id,)):
        coll_durs[f"{k['op']}/{k['algo']}"].append((int(k["closed_ns"]) - int(k["created_ns"])) / 1e9)
    # Time each rank spent inside the runtime waiting, versus working.
    wait_by_rank: Dict[int, float] = defaultdict(float)
    for e in j.q(
        "SELECT rank,dur_ns FROM event WHERE job=? AND kind IN ('recv','coll_exit') AND rank IS NOT NULL",
        (j.job_id,),
    ):
        wait_by_rank[int(e["rank"])] += int(e["dur_ns"]) / 1e9
    merge_durs = [
        int(e["dur_ns"]) / 1e9
        for e in j.q("SELECT dur_ns FROM event WHERE job=? AND kind='reduce_step_commit'", (j.job_id,))
    ]
    return {
        "world_size": P,
        "wall_s": round((now_ns() - t0) / 1e9, 2),
        "counters": counters,
        "messages": {
            "count": int(j.scalar("SELECT COUNT(*) FROM msg WHERE job=?", (j.job_id,), 0)),
            "p2p": int(j.scalar("SELECT COUNT(*) FROM msg WHERE job=? AND kind='p2p'", (j.job_id,), 0)),
            "coll": int(j.scalar("SELECT COUNT(*) FROM msg WHERE job=? AND kind='coll'", (j.job_id,), 0)),
            "undelivered": int(
                j.scalar("SELECT COUNT(*) FROM msg WHERE job=? AND status='posted'", (j.job_id,), 0)
            ),
            "payload_tokens": int(j.scalar("SELECT COALESCE(SUM(tokens),0) FROM msg WHERE job=?", (j.job_id,), 0)),
            "latency_s": _dist([x / 1e9 for x in lat]),
        },
        "context": {
            "per_rank_hwm": {str(r): h for r, h, _, _ in per_rank_ctx},
            "hwm": _dist([float(h) for _, h, _, _ in per_rank_ctx]),
            "total_delivered_tokens": counters.get("ctx_tokens", 0),
            "budget": per_rank_ctx[0][2] if per_rank_ctx else 0,
            "over_budget_ranks": [r for r, h, b, _ in per_rank_ctx if b and h > b],
        },
        "calls": _dist([float(c) for _, _, _, c in per_rank_ctx]),
        "collectives": {k: _dist(v) for k, v in sorted(coll_durs.items())},
        "agent_merge_s": _dist(merge_durs),
        "wait_s": _dist(list(wait_by_rank.values())),
        "failures": int(j.scalar("SELECT COUNT(*) FROM failure WHERE job=?", (j.job_id,), 0)),
        "rma": {
            "puts": counters.get("rma_puts", 0),
            "gets": counters.get("rma_gets", 0),
            "accumulates": counters.get("rma_accs", 0),
            "cells": int(j.scalar("SELECT COUNT(*) FROM win_cell", (), 0)),
        },
    }


def _dist(xs: List[float]) -> Dict[str, Optional[float]]:
    if not xs:
        return {"n": 0, "mean": None, "p50": None, "p90": None, "p99": None, "max": None, "min": None,
                "sum": 0.0}
    s = sorted(xs)

    def pct(p: float) -> float:
        if len(s) == 1:
            return s[0]
        idx = min(len(s) - 1, max(0, int(math.ceil(p * len(s))) - 1))
        return s[idx]

    return {
        "n": len(s),
        "mean": round(statistics.fmean(s), 4),
        "p50": round(pct(0.50), 4),
        "p90": round(pct(0.90), 4),
        "p99": round(pct(0.99), 4),
        "max": round(s[-1], 4),
        "min": round(s[0], 4),
        "sum": round(sum(s), 4),
        "cv": (round(statistics.stdev(s) / statistics.fmean(s), 4) if len(s) > 1 and statistics.fmean(s) else 0.0),
    }


def text_timeline(j: Journal, *, width: int = 96, max_ranks: int = 64) -> str:
    """A terminal Gantt chart. Crude, but it is the artefact that most often
    reveals a schedule bug at a glance, so it earns its place."""
    data = export(j)
    dur = max(0.001, data["duration_s"])
    lines = [
        f"AgentMPI trace  job={data['job']}  label={data['label']}  P={data['world_size']}"
        f"  wall={dur:.1f}s",
        "",
    ]
    glyph = {
        "init": "I",
        "send": ">",
        "recv": "<",
        "recv_post": ".",
        "coll_join": "[",
        "coll_exit": "]",
        "coll_fold": "F",
        "reduce_step_issue": "m",
        "reduce_step_commit": "M",
        "win_put": "P",
        "win_get": "G",
        "win_acc": "A",
        "win_lock": "L",
        "failure": "X",
        "respawn": "R",
        "comm_revoke": "!",
        "comm_shrink": "S",
        "finalize": "|",
    }
    by_rank: Dict[int, List[str]] = {
        r["rank"]: [" "] * width for r in data["ranks"][:max_ranks]
    }
    for e in data["events"]:
        r = e["rank"]
        if r is None or r not in by_rank:
            continue
        col = min(width - 1, int(e["t"] / dur * (width - 1)))
        g = glyph.get(e["kind"], "?")
        cur = by_rank[r][col]
        by_rank[r][col] = g if cur == " " else ("*" if cur != g else g)
    for r in sorted(by_rank):
        lines.append(f"r{r:<3d} |{''.join(by_rank[r])}|")
    lines.append("")
    lines.append("legend: I init  > send  < recv  [ ] collective enter/exit  F runtime fold")
    lines.append("        m/M agent merge issued/committed  P/G/A window put/get/acc  L lock")
    lines.append("        X failure  R respawn  ! revoke  S shrink  | finalize  * multiple")
    s = data["summary"]
    lines.append("")
    lines.append(
        f"messages: {s['messages']['count']} ({s['messages']['payload_tokens']} payload tokens); "
        f"context hwm p50={s['context']['hwm']['p50']} max={s['context']['hwm']['max']} "
        f"of budget {s['context']['budget']}"
    )
    if s["collectives"]:
        lines.append("collectives (seconds):")
        for k, v in s["collectives"].items():
            lines.append(f"  {k:<28s} n={v['n']:<4d} p50={v['p50']}  p90={v['p90']}  max={v['max']}")
    return "\n".join(lines)
