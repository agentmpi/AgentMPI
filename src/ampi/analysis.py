"""Post-hoc analysis of an AgentMPI job, computed entirely from the PAMPI trace.

Every number reported in the evaluation comes from this module reading a job
database.  Nothing is measured by instrumenting the experiment scripts, which
is the point of mandating a profiling interface in the first place: a claim
about an AgentMPI program is checkable by a third party who has only the job
database, exactly as a claim about an MPI program is checkable by anyone with
the OTF2 trace.
"""

from __future__ import annotations

import json
import os
import statistics
from typing import Any

from .device import SqliteDevice


def open_job(job_dir: str) -> tuple[SqliteDevice, str]:
    device = SqliteDevice(os.path.join(job_dir, "job.db"))
    device.initialize()
    return device, os.path.basename(os.path.abspath(job_dir).rstrip("/"))


def summarise(job_dir: str) -> dict[str, Any]:
    device, job_id = open_job(job_dir)
    try:
        job = device.query_one("SELECT * FROM job WHERE job_id=?", (job_id,))
        ranks = device.query("SELECT * FROM rank WHERE job_id=? ORDER BY rank", (job_id,))
        events = device.query(
            "SELECT * FROM event WHERE job_id=? ORDER BY event_id", (job_id,))
        messages = device.query("SELECT * FROM message WHERE job_id=?", (job_id,))
        colls = device.query("SELECT * FROM coll WHERE job_id=? ORDER BY created_at", (job_id,))
        failures = device.query("SELECT * FROM failure WHERE job_id=?", (job_id,))
        upcalls = device.query("SELECT * FROM pending_op WHERE job_id=?", (job_id,))
        windows = device.query("SELECT * FROM win WHERE job_id=?", (job_id,))

        starts = [r["started_at"] for r in ranks if r["started_at"]]
        ends = [r["finished_at"] for r in ranks if r["finished_at"]]
        wall = (max(ends) - min(starts)) if starts and ends else None

        return {
            "job_id": job_id,
            "world_size": job["world_size"] if job else len(ranks),
            "spec_version": job["spec_version"] if job else None,
            "wall_seconds": round(wall, 3) if wall else None,
            "ranks": _rank_table(ranks),
            "participation": _participation(ranks),
            "messages": _message_stats(messages),
            "collectives": _collective_stats(colls, events),
            "operators": _operator_stats(upcalls),
            "context": _context_stats(ranks, events),
            "failures": _failure_stats(failures, ranks),
            "windows": _window_stats(device, windows),
            "operations": _operation_stats(events),
            "errors": _error_stats(events),
        }
    finally:
        device.close()


def _rank_table(ranks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"rank": r["rank"], "role": r["role"], "state": r["state"],
         "generation": r["generation"], "ctx_peak": r["ctx_peak"], "ctx_limit": r["ctx_limit"],
         "tokens_sent": r["tokens_sent"], "tokens_received": r["tokens_recvd"],
         "seconds": (round(r["finished_at"] - r["started_at"], 2)
                     if r["finished_at"] and r["started_at"] else None)}
        for r in ranks
    ]


def _participation(ranks: list[dict[str, Any]]) -> dict[str, Any]:
    """How many ranks actually spoke the protocol.

    An agent rank can be launched and never call AMPI_Init, or init and then go
    silent.  Reporting the participation rate separately from the results is
    the honest way to present a multi-agent measurement.
    """
    total = len(ranks)
    joined = [r for r in ranks if r["started_at"] is not None]
    finalized = [r for r in ranks if r["state"] == "finalized"]
    failed = [r for r in ranks if r["state"] == "failed"]
    return {
        "launched": total,
        "joined": len(joined),
        "finalized": len(finalized),
        "failed": len(failed),
        "silent": total - len(joined),
        "join_rate": round(len(joined) / total, 4) if total else 0.0,
        "completion_rate": round(len(finalized) / total, 4) if total else 0.0,
    }


def _message_stats(messages: list[dict[str, Any]]) -> dict[str, Any]:
    if not messages:
        return {"count": 0}
    eager = [m for m in messages if m["mode"] == "eager"]
    rendezvous = [m for m in messages if m["mode"] == "rendezvous"]
    delivered = [m for m in messages if m["state"] == "delivered"]
    latencies = [m["matched_at"] - m["sent_at"] for m in messages
                 if m["matched_at"] and m["sent_at"]]
    return {
        "count": len(messages),
        "delivered": len(delivered),
        "undelivered": len(messages) - len(delivered),
        "eager": len(eager),
        "rendezvous": len(rendezvous),
        "rendezvous_fraction": round(len(rendezvous) / len(messages), 4),
        "tokens_total": sum(m["tokens"] for m in messages),
        "tokens_median": statistics.median([m["tokens"] for m in messages]),
        "match_latency_s": _dist(latencies),
    }


def _collective_stats(colls: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    by_op: dict[str, list[float]] = {}
    for event in events:
        if event["phase"] != "exit" or event["dur"] is None:
            continue
        name = event["op"]
        if name.startswith("AMPI_") and name.split("_", 1)[1].lower() in (
            "barrier", "bcast", "reduce", "allreduce", "allgather", "alltoall", "gather",
            "scatter", "scan",
        ):
            by_op.setdefault(name, []).append(event["dur"])
    algos: dict[str, int] = {}
    for coll in colls:
        key = f"{coll['op']}/{coll['algo']}"
        algos[key] = algos.get(key, 0) + 1
    return {
        "invocations": len(colls),
        "complete": sum(1 for c in colls if c["state"] == "complete"),
        "by_algorithm": dict(sorted(algos.items())),
        "duration_s": {name: _dist(values) for name, values in sorted(by_op.items())},
    }


def _operator_stats(upcalls: list[dict[str, Any]]) -> dict[str, Any]:
    if not upcalls:
        return {"upcalls": 0}
    done = [u for u in upcalls if u["state"] == "done"]
    turnaround = [u["settled_at"] - u["created_at"] for u in done
                  if u["settled_at"] and u["created_at"]]
    by_op: dict[str, int] = {}
    for upcall in upcalls:
        by_op[upcall["op_name"]] = by_op.get(upcall["op_name"], 0) + 1
    return {
        "upcalls": len(upcalls),
        "completed": len(done),
        "pending": len(upcalls) - len(done),
        "by_operator": dict(sorted(by_op.items())),
        "turnaround_s": _dist(turnaround),
    }


def _context_stats(ranks: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    peaks = [r["ctx_peak"] for r in ranks if r["ctx_peak"]]
    limits = [r["ctx_limit"] for r in ranks if r["ctx_limit"]]
    exhausted = sum(1 for e in events if e["err"] == "AmpiContextExhausted")
    derefs = [e for e in events if e["op"] == "AMPI_Deref" and e["phase"] == "exit"]
    releases = [e for e in events if e["op"] == "AMPI_Ctx_release"]
    return {
        "peak_tokens": _dist(peaks),
        "limit": limits[0] if limits else None,
        "max_occupancy": round(max(peaks) / limits[0], 4) if peaks and limits else None,
        "exhaustion_errors": exhausted,
        "deref_calls": len(derefs),
        "deref_tokens": sum(e["tokens"] for e in derefs),
        "release_calls": len(releases),
    }


def _failure_stats(failures: list[dict[str, Any]], ranks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "detected": len(failures),
        "ranks": sorted({f["rank"] for f in failures}),
        "reasons": sorted({f["reason"] for f in failures}),
        "respawned": sum(1 for r in ranks if r["generation"] > 0),
        "max_generation": max([r["generation"] for r in ranks], default=0),
    }


def _window_stats(device: SqliteDevice, windows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"count": len(windows), "windows": {}}
    for win in windows:
        cells = device.query("SELECT * FROM win_cell WHERE win_id=?", (win["win_id"],))
        locks = device.query("SELECT * FROM win_lock WHERE win_id=?", (win["win_id"],))
        out["windows"][win["name"]] = {
            "cells": len(cells),
            "tokens": sum(c["tokens"] for c in cells),
            "max_version": max([c["version"] for c in cells], default=0),
            "contended_cells": sum(1 for c in cells if c["version"] > 1),
            "locks_taken": len(locks),
        }
    return out


def _operation_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_op: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["phase"] != "exit":
            continue
        entry = by_op.setdefault(event["op"], {"calls": 0, "tokens": 0, "durations": []})
        entry["calls"] += 1
        entry["tokens"] += event["tokens"] or 0
        if event["dur"] is not None:
            entry["durations"].append(event["dur"])
    return {
        name: {"calls": entry["calls"], "tokens": entry["tokens"],
               "duration_s": _dist(entry["durations"])}
        for name, entry in sorted(by_op.items())
    }


def _error_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    errors: dict[str, int] = {}
    for event in events:
        if event["ok"] == 0 and event["err"]:
            errors[event["err"]] = errors.get(event["err"], 0) + 1
    return {"total": sum(errors.values()), "by_class": dict(sorted(errors.items()))}


def _dist(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    ordered = sorted(values)
    def pct(q: float) -> float:
        if len(ordered) == 1:
            return round(ordered[0], 4)
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return round(ordered[index], 4)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "p50": round(statistics.median(ordered), 4),
        "p95": pct(0.95),
        "max": round(ordered[-1], 4),
        "mean": round(statistics.fmean(ordered), 4),
        "total": round(sum(ordered), 4),
    }


def timeline(job_dir: str) -> list[dict[str, Any]]:
    """Gantt-style spans, one per protocol call, for trace visualisation.

    The same record shape Jumpshot and Vampir consume, with tokens added.
    """
    device, job_id = open_job(job_dir)
    try:
        events = device.query(
            "SELECT * FROM event WHERE job_id=? AND phase='exit' ORDER BY ts", (job_id,))
        base = min([e["ts"] - (e["dur"] or 0) for e in events], default=0.0)
        return [
            {"rank": e["rank"], "op": e["op"],
             "start": round(e["ts"] - (e["dur"] or 0) - base, 4),
             "end": round(e["ts"] - base, 4), "dur": round(e["dur"] or 0, 4),
             "tokens": e["tokens"], "peer": e["peer"], "ok": bool(e["ok"]), "err": e["err"]}
            for e in events
        ]
    finally:
        device.close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="ampi-analyse",
                                     description="Summarise an AgentMPI job from its trace")
    parser.add_argument("job_dir")
    parser.add_argument("--timeline", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    payload = timeline(args.job_dir) if args.timeline else summarise(args.job_dir)
    text = json.dumps(payload, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(json.dumps({"written": args.out}))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
