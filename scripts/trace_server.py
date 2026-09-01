"""Read-only HTTP API for exported AMPI harness traces.

The server deliberately reads the harness artifacts rather than opening a live
runtime.  It has no write endpoints and depends only on the Python standard
library.

    python3 scripts/trace_server.py --runs runs --port 43118
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

RUNS = Path("runs")
RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
TRACE_NAME = "harness.trace.jsonl"
REPORT_NAME = "report.json"
SCHEMA_FIELDS = {
    "required": {"kind": "string", "rank": "integer", "seq": "integer", "ts": "number"},
    "common": {"comm": "string|null", "run": "string"},
    "additionalProperties": True,
}


class TraceError(ValueError):
    """An invalid or unsafe trace artifact."""


def _safe_run_dir(name: str) -> Path:
    if not RUN_NAME.fullmatch(name) or name in {".", ".."}:
        raise TraceError("bad run name")
    root = RUNS.resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root:
        raise TraceError("run escapes runs directory")
    return candidate


def _trace_path(name: str) -> Path:
    run_dir = _safe_run_dir(name)
    trace = (run_dir / TRACE_NAME).resolve()
    if trace.parent != run_dir:
        raise TraceError("trace escapes run directory")
    if not trace.is_file():
        raise FileNotFoundError(name)
    return trace


def _read_events(name: str) -> list[dict[str, Any]]:
    trace = _trace_path(name)
    events: list[dict[str, Any]] = []
    with trace.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TraceError(f"{TRACE_NAME}:{line_no}: invalid JSON") from exc
            if not isinstance(event, dict):
                raise TraceError(f"{TRACE_NAME}:{line_no}: event is not an object")
            if not isinstance(event.get("kind"), str):
                raise TraceError(f"{TRACE_NAME}:{line_no}: event has no string kind")
            if not isinstance(event.get("ts"), (int, float)):
                raise TraceError(f"{TRACE_NAME}:{line_no}: event has no numeric timestamp")
            events.append(event)
    return events


def _optional_report(name: str) -> dict[str, Any] | None:
    run_dir = _safe_run_dir(name)
    report_path = (run_dir / REPORT_NAME).resolve()
    if report_path.parent != run_dir or not report_path.is_file():
        return None
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return report if isinstance(report, dict) else None


def _ranks(events: list[dict[str, Any]]) -> list[int]:
    return sorted(
        {
            rank
            for event in events
            if isinstance((rank := event.get("rank")), int) and rank >= 0
        }
    )


def _world_size(events: list[dict[str, Any]], report: dict[str, Any] | None) -> int:
    if report and isinstance(report.get("size"), int):
        return report["size"]
    for event in events:
        if event["kind"] == "job.create" and isinstance(event.get("size"), int):
            return event["size"]
    ranks = _ranks(events)
    return ranks[-1] + 1 if ranks else 0


def _work_spans(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair current broker claim/submit events into occupied-rank intervals."""
    open_claims: dict[str, dict[str, Any]] = {}
    spans: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda item: (float(item["ts"]), item.get("seq", 0))):
        aid = event.get("aid")
        if not isinstance(aid, str):
            continue
        if event["kind"] == "broker.claim":
            open_claims[aid] = event
        elif event["kind"] in {"broker.submit", "broker.giveup", "broker.reject"}:
            claim = open_claims.pop(aid, None)
            if claim is None:
                continue
            spans.append(
                {
                    "aid": aid,
                    "rank": claim.get("rank", event.get("rank", -1)),
                    "label": claim.get("label", event.get("label", "")),
                    "start": float(claim["ts"]),
                    "end": max(float(claim["ts"]), float(event["ts"])),
                    "outcome": event["kind"].split(".", 1)[1],
                    "tokens": event.get("tokens", 0),
                }
            )
    return spans


def _concurrency(
    spans: list[dict[str, Any]], start: float, end: float, world_size: int
) -> dict[str, Any]:
    points: list[tuple[float, int]] = []
    busy_seconds = 0.0
    for span in spans:
        span_start, span_end = float(span["start"]), float(span["end"])
        busy_seconds += max(0.0, span_end - span_start)
        points.extend(((span_start, 1), (span_end, -1)))
    active = peak = 0
    for _, delta in sorted(points, key=lambda point: (point[0], point[1])):
        active += delta
        peak = max(peak, active)
    duration = max(0.0, end - start)
    average = busy_seconds / duration if duration else 0.0
    capacity = duration * world_size
    return {
        "peak": peak,
        "average": round(average, 3),
        "busy_rank_seconds": round(busy_seconds, 3),
        "utilization": round(busy_seconds / capacity, 4) if capacity else 0.0,
    }


def run_detail(name: str) -> dict[str, Any]:
    events = _read_events(name)
    report = _optional_report(name)
    times = [float(event["ts"]) for event in events]
    start, end = (min(times), max(times)) if times else (0.0, 0.0)
    size = _world_size(events, report)
    spans = _work_spans(events)
    return {
        "name": name,
        "schema": SCHEMA_FIELDS,
        "events": events,
        "report": report,
        "ranks": _ranks(events),
        "world_size": size,
        "started_at": start,
        "ended_at": end,
        "duration_s": end - start,
        "work_spans": spans,
        "concurrency": _concurrency(spans, start, end, size),
    }


def list_runs() -> list[dict[str, Any]]:
    root = RUNS.resolve()
    if not root.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not RUN_NAME.fullmatch(child.name):
            continue
        try:
            trace = _trace_path(child.name)
            events = _read_events(child.name)
            report = _optional_report(child.name)
            times = [float(event["ts"]) for event in events]
            runs.append(
                {
                    "name": child.name,
                    "n_events": len(events),
                    "n_ranks": len(_ranks(events)),
                    "world_size": _world_size(events, report),
                    "job_id": (
                        next(
                            (event.get("job_id") for event in events if event["kind"] == "job.create"),
                            None,
                        )
                        or next((event.get("run") for event in events if event.get("run")), "")
                    ),
                    "duration_s": max(times) - min(times) if times else 0.0,
                    "trace_bytes": trace.stat().st_size,
                    "has_report": report is not None,
                }
            )
        except (FileNotFoundError, OSError, TraceError):
            # Symlinks, partial writes, and corrupt runs are not advertised.
            continue
    return runs


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                self._send({"ok": True})
            elif parsed.path == "/api/runs":
                self._send(list_runs())
            elif parsed.path == "/api/run":
                values = parse_qs(parsed.query, keep_blank_values=True).get("name", [])
                if len(values) != 1:
                    self._send({"error": "exactly one run name is required"}, 400)
                    return
                self._send(run_detail(values[0]))
            else:
                self._send({"error": "not found"}, 404)
        except TraceError as exc:
            self._send({"error": str(exc)}, 400)
        except FileNotFoundError:
            self._send({"error": "run not found"}, 404)
        except OSError as exc:
            self._send({"error": f"cannot read trace: {exc}"}, 500)

    def _read_only(self) -> None:
        self._send({"error": "read-only server"}, 405)

    do_DELETE = _read_only
    do_PATCH = _read_only
    do_POST = _read_only
    do_PUT = _read_only

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> int:
    global RUNS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=RUNS)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=43118)
    args = parser.parse_args()
    RUNS = args.runs
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"trace API at http://{args.host}:{args.port} ({RUNS.resolve()})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
