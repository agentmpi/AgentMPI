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
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import agentmpi as ampi  # noqa: E402

RUNS = Path("runs")

#: Events the timeline draws. Everything else is available through /api/events but
#: is not laid out, because a timeline that draws every event is unreadable.
LANE_KINDS = (
    "agent.call",
    "msg.send",
    "msg.recv",
    "win.put",
    "win.get",
    "win.lock",
    "win.accumulate",
    "broker.claim",
    "broker.complete",
)


def list_runs() -> list[dict[str, Any]]:
    out = []
    for child in sorted(RUNS.iterdir() if RUNS.exists() else []):
        db = child / "fabric.sqlite"
        if not db.exists():
            continue
        try:
            fabric = ampi.Fabric(child)
            n = fabric.query_one("SELECT COUNT(*) AS n FROM events")
            ranks = fabric.query_one("SELECT COUNT(*) AS n FROM ranks")
            out.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "job_id": fabric.job_id,
                    "label": fabric.get_meta("label") or "",
                    "experiment": fabric.get_meta("experiment") or "",
                    "world_size": int(fabric.get_meta("world_size") or 0),
                    "n_events": int(n["n"]) if n else 0,
                    "n_ranks": int(ranks["n"]) if ranks else 0,
                }
            )
        except Exception as exc:  # a partially written run must not break the list
            out.append({"name": child.name, "path": str(child), "error": repr(exc)})
    return out


def run_detail(name: str) -> dict[str, Any]:
    root = RUNS / name
    fabric = ampi.Fabric(root)
    events = fabric.events()
    t0 = events[0]["ts"] if events else 0.0
    lanes: dict[int, list[dict[str, Any]]] = {}
    #: Agent invocations are drawn as spans; everything else as instants. Spans are
    #: reconstructed by pairing a broker claim with its completion, because that is
    #: the interval during which a rank was actually occupied.
    open_claims: dict[int, dict[str, Any]] = {}
    for e in events:
        kind = e["kind"]
        rank = e["rank"]
        if rank is None:
            continue
        p = e["payload"]
        rel = e["ts"] - t0
        if kind == "broker.claim":
            open_claims[int(p.get("aid", -1))] = {"start": rel, "label": p.get("label", ""), "rank": rank}
            continue
        if kind == "broker.complete":
            aid = int(p.get("aid", -1))
            claim = open_claims.pop(aid, None)
            if claim is not None:
                lanes.setdefault(rank, []).append(
                    {
                        "kind": "work",
                        "start": claim["start"],
                        "end": rel,
                        "label": claim["label"],
                        "tokens": p.get("result_tokens", 0),
                        "aid": aid,
                    }
                )
            continue
        if kind not in LANE_KINDS:
            continue
        lanes.setdefault(rank, []).append(
            {
                "kind": kind,
                "start": rel,
                "end": rel,
                "label": p.get("label") or p.get("kind_label") or p.get("tag") or p.get("slot") or "",
                "tokens": p.get("tokens") or p.get("output_tokens") or 0,
                "peer": p.get("dst") if kind == "msg.send" else p.get("src"),
                "mode": p.get("mode"),
                "stale": bool(p.get("stale_write")),
            }
        )
    colls = [
        {
            "kind": e["kind"].split(".", 1)[1],
            "rank": e["rank"],
            "t": e["ts"] - t0,
            **{k: e["payload"].get(k) for k in ("algorithm", "rounds", "messages_sent", "tokens_sent", "fold_depth", "wall_s", "op")},
        }
        for e in events
        if e["kind"].startswith("coll.")
    ]
    return {
        "name": name,
        "job_id": fabric.job_id,
        "experiment": fabric.get_meta("experiment") or "",
        "t_span": (events[-1]["ts"] - t0) if events else 0.0,
        "n_events": len(events),
        "lanes": {str(k): v for k, v in sorted(lanes.items())},
        "collectives": colls,
        "summary": ampi.cost.summarise(fabric).as_dict(),
        "calibration": ampi.cost.calibrate(fabric).as_dict(),
        "health": [
            {"rank": h.rank, "state": h.state, "alive": h.alive, "calls": h.n_calls,
             "occupancy": round(h.context_occupancy, 3), "suspected": h.suspected.value if h.suspected else None}
            for h in ampi.ft.health(fabric)
        ],
    }


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
