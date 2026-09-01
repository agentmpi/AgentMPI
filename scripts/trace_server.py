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

#: Events the timeline draws, grouped by what the reader is looking for. Everything else is
#: available through /api/events but is not laid out, because a timeline that draws every
#: event is unreadable.
#:
#: The omissions are deliberate and narrow: ``coll.*`` appears in its own panel, ``job.*``
#: has no rank to draw on, and ``comm.create`` / ``topo.*_create`` / ``win.create`` /
#: ``broker.enqueue`` are setup bookkeeping that says nothing about how the run unfolded.
#:
#: Everything else is drawn, including every failure and recovery event. Those are rare --- a
#: few dozen in fifty thousand --- so they cost nothing in clutter, and they are the reason
#: someone opens a trace at all. Leaving them out made the fault-injection runs render as
#: blank lanes: the runs that exist specifically to show the failure model showed nothing.
WORK_KINDS = ("agent.call",)
MESSAGE_KINDS = ("msg.send", "msg.recv", "msg.fetch")
RMA_KINDS = ("win.put", "win.get", "win.accumulate", "win.cas", "win.lock", "win.unlock", "win.sync", "win.flush")
LIFECYCLE_KINDS = ("rank.init", "rank.finalize", "rank.compact", "proc.spawn", "sup.restart")
#: A rank is in trouble: stalled, timed out, evicted, or contractually wrong.
TROUBLE_KINDS = (
    "rank.error",
    "rank.stuck",
    "rank.version_mismatch",
    "barrier.timeout",
    "win.lock_timeout",
    "broker.expire",
    "broker.fail",
    "broker.reclaim",
    "agent.contract_violation",
    "transport.credit_stall",
    "harness.degraded",
    "ft.declare_failed",
    "ft.agree_timeout",
)
#: The harness responding to trouble.
RECOVERY_KINDS = (
    "ft.agree",
    "ft.revoke",
    "ft.shrink",
    "ft.shrink_in_place",
    "ft.failure_ack",
    "sup.escalate",
    "transport.credit_granted",
)
LANE_KINDS = (
    *WORK_KINDS,
    *MESSAGE_KINDS,
    *RMA_KINDS,
    *LIFECYCLE_KINDS,
    *TROUBLE_KINDS,
    *RECOVERY_KINDS,
    "broker.claim",
    "broker.complete",
)


#: Separator used in place of "/" for nested run names. A benchmark that sweeps a
#: parameter creates one fabric per point under a parent directory, and those are the
#: most illustrative traces -- a reduction tree's shape is exactly what a timeline shows
#: -- so they must be reachable. Path separators are kept out of the name so a request
#: cannot traverse.
NEST = "__"


def _candidate_dirs() -> list[Path]:
    """Fabric directories, scanning one level below `runs/` as well as directly in it."""
    if not RUNS.exists():
        return []
    out: list[Path] = []
    for child in sorted(RUNS.iterdir()):
        if not child.is_dir():
            continue
        if (child / "fabric.sqlite").exists():
            out.append(child)
            continue
        out.extend(sorted(g for g in child.iterdir() if g.is_dir() and (g / "fabric.sqlite").exists()))
    return out


def _name_of(path: Path) -> str:
    return str(path.relative_to(RUNS)).replace("/", NEST)


#: Payload keys worth showing in a tooltip, in the order they read best. Failure events keep
#: their substance in kind-specific keys --- which rank died, who failed to vote, what the
#: contract rejected --- so a diamond with only a colour is close to useless at the moment a
#: reader has found the thing they were looking for.
DETAIL_KEYS = (
    "kind",  # failure class on ft.declare_failed
    "error_class",
    "failed",
    "excluded",
    "missing",
    "absent",
    "policy",
    "result",
    "state",
    "problems",
    "contract",
    "detail",
    "waited_s",
    "mode",
    "contended",
    "win",
    "version",
    "executor",
)


def _detail(payload: dict[str, Any], limit: int = 4) -> str:
    """A compact ``key=value`` summary of the salient parts of an event payload."""
    parts: list[str] = []
    for key in DETAIL_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if value is None or value == "" or value == [] or value is False:
            continue
        if isinstance(value, list):
            shown = ", ".join(str(v) for v in value[:4])
            if len(value) > 4:
                shown += f", +{len(value) - 4}"
            value = f"[{shown}]"
        elif isinstance(value, bool):
            value = "true"  # falsey values are filtered above, so this is always True
        elif isinstance(value, float):
            value = f"{value:g}"
        parts.append(f"{key}={value}")
        if len(parts) >= limit:
            break
    return " · ".join(parts)


def list_runs() -> list[dict[str, Any]]:
    out = []
    for child in _candidate_dirs():
        db = child / "fabric.sqlite"
        if not db.exists():
            continue
        try:
            fabric = ampi.Fabric(child)
            n = fabric.query_one("SELECT COUNT(*) AS n FROM events")
            ranks = fabric.query_one("SELECT COUNT(*) AS n FROM ranks")
            out.append(
                {
                    "name": _name_of(child),
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
            out.append({"name": _name_of(child), "path": str(child), "error": repr(exc)})
    return out


def run_detail(name: str) -> dict[str, Any]:
    root = RUNS / name.replace(NEST, "/")
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
                        "detail": _detail(p),
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
                "detail": _detail(p),
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
