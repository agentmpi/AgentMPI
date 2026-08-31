"""A tiny read-only HTTP server exposing AgentMPI run traces to the viewer.

Kept deliberately small and stdlib-only: it is a development aid, not a component
of the protocol, and it has no business pulling in a web framework. It serves only
GET, reads only from the run directories it is pointed at, and never mutates a
fabric --- a viewer that could write to a live run would be a hazard.

    python3 scripts/trace_server.py --runs runs --port 43118
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ampi import util
from ampi.analysis import summarise
from ampi.device import SqliteDevice

RUNS = Path("runs")

#: Events the timeline draws. Everything else is available through /api/events but
#: is not laid out, because a timeline that draws every event is unreadable.
LANE_KIND = {
    "AMPI_Send": "msg.send",
    "AMPI_Recv": "msg.recv",
    "AMPI_Put": "win.put",
    "AMPI_Get": "win.get",
    "AMPI_Win_lock": "win.lock",
    "AMPI_Accumulate": "win.accumulate",
}


#: Separator used in place of "/" for nested run names. A benchmark that sweeps a
#: parameter creates one fabric per point under a parent directory, and those are the
#: most illustrative traces -- a reduction tree's shape is exactly what a timeline shows
#: -- so they must be reachable. Path separators are kept out of the name so a request
#: cannot traverse.
NEST = "__"


def _candidate_dirs() -> list[Path]:
    """Current v0.2 job directories, at any depth below ``RUNS``."""
    if not RUNS.exists():
        return []
    return sorted(path.parent for path in RUNS.rglob("job.db"))


def _name_of(path: Path) -> str:
    return str(path.relative_to(RUNS)).replace("/", NEST)


def list_runs() -> list[dict[str, Any]]:
    out = []
    for child in _candidate_dirs():
        db = child / "job.db"
        if not db.exists():
            continue
        device = SqliteDevice(str(db))
        try:
            job = device.query_one("SELECT * FROM job LIMIT 1")
            if job is None:
                continue
            n = device.query_one("SELECT COUNT(*) AS n FROM event WHERE job_id=?", (job["job_id"],))
            ranks = device.query_one(
                "SELECT COUNT(*) AS n FROM rank WHERE job_id=?", (job["job_id"],)
            )
            meta = util.loads(job["meta"], {})
            out.append(
                {
                    "name": _name_of(child),
                    "path": str(child),
                    "job_id": job["job_id"],
                    "run_id": job["run_id"],
                    "label": meta.get("label", ""),
                    "experiment": meta.get("experiment", ""),
                    "world_size": int(job["world_size"]),
                    "n_events": int(n["n"]) if n else 0,
                    "n_ranks": int(ranks["n"]) if ranks else 0,
                }
            )
        except Exception as exc:  # a partially written run must not break the list
            out.append({"name": _name_of(child), "path": str(child), "error": repr(exc)})
        finally:
            device.close()
    return out


def run_detail(name: str) -> dict[str, Any]:
    root = RUNS / name.replace(NEST, "/")
    resolved = root.resolve()
    if RUNS.resolve() not in resolved.parents and resolved != RUNS.resolve():
        raise ValueError("run path escapes the configured root")
    device = SqliteDevice(str(root / "job.db"))
    try:
        job = device.query_one("SELECT * FROM job LIMIT 1")
        if job is None:
            raise FileNotFoundError(root / "job.db")
        events = device.query(
            "SELECT * FROM event WHERE job_id=? ORDER BY event_id", (job["job_id"],)
        )
        ranks = device.query(
            "SELECT * FROM rank WHERE job_id=? ORDER BY rank", (job["job_id"],)
        )
        t0 = float(events[0]["ts"]) if events else float(job["created_at"])
        lanes: dict[int, list[dict[str, Any]]] = {}
        entered: dict[tuple[int, str], list[float]] = {}
        collectives: list[dict[str, Any]] = []
        collective_ops = {
            "AMPI_Barrier",
            "AMPI_Bcast",
            "AMPI_Reduce",
            "AMPI_Allreduce",
            "AMPI_Allgather",
            "AMPI_Alltoall",
            "AMPI_Gather",
            "AMPI_Scatter",
            "AMPI_Scan",
            "AMPI_Reduce_scatter",
            "AMPI_Comm_agree",
        }
        for event in events:
            if event["rank"] is None:
                continue
            rank = int(event["rank"])
            op = str(event["op"])
            rel = float(event["ts"]) - t0
            meta = util.loads(event["meta"], {})
            key = (rank, op)
            if event["phase"] == "enter":
                entered.setdefault(key, []).append(rel)
                continue
            starts = entered.get(key, [])
            start = starts.pop() if starts else rel
            kind = LANE_KIND.get(op, "agent.call")
            lanes.setdefault(rank, []).append(
                {
                    "kind": kind,
                    "start": start,
                    "end": max(rel, start),
                    "label": op.removeprefix("AMPI_"),
                    "tokens": int(event["tokens"] or 0),
                    "peer": event["peer"],
                    "mode": meta.get("mode"),
                    "stale": op in {"AMPI_Failure_retracted", "AMPI_Stale_incarnation"},
                }
            )
            if op in collective_ops:
                collectives.append(
                    {
                        "kind": op.removeprefix("AMPI_").lower(),
                        "rank": rank,
                        "t": rel,
                        "algorithm": meta.get("algo"),
                        "rounds": meta.get("rounds"),
                        "messages_sent": meta.get("messages_sent"),
                        "tokens_sent": event["tokens"],
                        "fold_depth": meta.get("fold_depth"),
                        "wall_s": event["dur"],
                        "op": meta.get("operator"),
                    }
                )

        report = summarise(str(root))
        token_total = sum(int(row["tokens_sent"] or 0) for row in ranks)
        received_total = sum(int(row["tokens_recvd"] or 0) for row in ranks)
        context_peak = max((int(row["ctx_peak"] or 0) for row in ranks), default=0)
        calls = {
            int(row["rank"]): int(
                device.query_one(
                    "SELECT COUNT(*) AS n FROM event WHERE job_id=? AND rank=? AND phase='exit'",
                    (job["job_id"], row["rank"]),
                )["n"]
            )
            for row in ranks
        }
        return {
            "name": name,
            "job_id": job["job_id"],
            "run_id": job["run_id"],
            "experiment": util.loads(job["meta"], {}).get("experiment", ""),
            "t_span": (float(events[-1]["ts"]) - t0) if events else 0.0,
            "n_events": len(events),
            "lanes": {str(k): v for k, v in sorted(lanes.items())},
            "collectives": collectives,
            "summary": {
                "wall_s": float(report.get("wall_seconds") or 0),
                "agent_calls": sum(calls.values()),
                "tokens_in": received_total,
                "tokens_out": token_total,
                "usd": 0,
                "messages": int(report.get("messages", {}).get("count") or 0),
                "tokens_sent": token_total,
                "tokens_deferred": 0,
                "agent_latency_p50": 0,
                "agent_latency_p95": 0,
                "agent_latency_max": 0,
                "context_high_water": context_peak,
                "context_rejections": int(
                    report.get("context", {}).get("exhaustion_errors") or 0
                ),
                "contract_violations": int(
                    report.get("errors", {}).get("by_class", {}).get(
                        "AmpiProtocolViolation", 0
                    )
                ),
                "failures": int(report.get("failures", {}).get("detected") or 0),
                "collectives": report.get("collectives", {}),
            },
            "calibration": {
                "alpha_s": 0,
                "alpha_p50": 0,
                "alpha_p99": 0,
                "tokens_per_s": None,
                "alpha_beta_crossover_tokens": None,
                "fabric_s": 0,
                "n_samples": 0,
            },
            "health": [
                {
                    "rank": int(row["rank"]),
                    "state": row["state"],
                    "alive": row["state"] == "alive",
                    "calls": calls[int(row["rank"])],
                    "occupancy": round(
                        int(row["ctx_used"] or 0) / max(1, int(row["ctx_limit"])), 3
                    ),
                    "suspected": (
                        "confirmed"
                        if row["state"] == "failed" and row["failure_confirmed"]
                        else "suspected" if row["state"] == "failed" else None
                    ),
                }
                for row in ranks
            ],
        }
    finally:
        device.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/runs":
                self._send(list_runs())
            elif parsed.path == "/api/run":
                name = parse_qs(parsed.query).get("name", [""])[0]
                if not name or "/" in name or ".." in name:
                    self._send({"error": "bad run name"}, 400)
                    return
                self._send(run_detail(name))
            elif parsed.path == "/api/health":
                self._send({"ok": True, "runs_dir": str(RUNS.resolve())})
            else:
                self._send({"error": "not found"}, 404)
        except FileNotFoundError as exc:
            self._send({"error": f"no such run: {exc}"}, 404)
        except Exception as exc:
            self._send({"error": repr(exc)}, 500)

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
        return


def main() -> int:
    global RUNS
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="runs")
    ap.add_argument("--port", type=int, default=43118)
    ap.add_argument("--host", default="127.0.0.1")
    cfg = ap.parse_args()
    RUNS = Path(cfg.runs)
    server = ThreadingHTTPServer((cfg.host, cfg.port), Handler)
    print(f"trace server on http://{cfg.host}:{cfg.port} serving {RUNS.resolve()}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
