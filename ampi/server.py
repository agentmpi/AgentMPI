"""A small HTTP API over job journals, for the trace viewer.

Deliberately stdlib-only and read-only. The viewer is a diagnostic tool in the
lineage of Jumpshot and Vampir, and the reason it exists is that a multi-agent
run produces no artefact a human can read: the per-agent transcripts are
unordered, the interesting behaviour is in the *relationships* between them, and
the questions one actually asks ("who was waiting on whom when the job stalled",
"which rank's context filled up first", "did the reduction tree have the shape I
asked for") are questions about a timeline.

Endpoints
---------
``GET /api/runs``
    List discovered job roots with a one-line summary each.
``GET /api/trace?run=<name>``
    The full trace for one run (see :func:`ampi.trace.export`).
``GET /api/summary?run=<name>``
    Just the aggregate metrics.
``GET /api/object?run=<name>&id=<handle>&budget=<tokens>``
    A bounded view of one payload, so the viewer can show what a message
    actually contained without shipping megabytes.
``GET /api/health``
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import trace, views
from .journal import STATE_DIR, Journal


def discover(runs_dir: Path) -> List[Dict[str, Any]]:
    """Find job roots under ``runs_dir`` (and ``runs_dir`` itself if it is one)."""
    found: List[Dict[str, Any]] = []
    candidates: List[Path] = []
    if (runs_dir / STATE_DIR / "journal.db").exists():
        candidates.append(runs_dir)
    if runs_dir.exists():
        for child in sorted(runs_dir.iterdir()):
            if child.is_dir() and (child / STATE_DIR / "journal.db").exists():
                candidates.append(child)
            elif child.is_dir():
                for g in sorted(child.iterdir()):
                    if g.is_dir() and (g / STATE_DIR / "journal.db").exists():
                        candidates.append(g)
    for root in candidates:
        try:
            j = Journal(root)
            job = j.job_row()
            s = trace.summarize(j)
            found.append(
                {
                    "name": str(root.relative_to(runs_dir)) if root != runs_dir else root.name,
                    "path": str(root),
                    "job": j.job_id,
                    "label": job["label"],
                    "world_size": int(job["world_size"]),
                    "created_ns": int(job["created_ns"]),
                    "wall_s": s["wall_s"],
                    "messages": s["messages"]["count"],
                    "failures": s["failures"],
                    "context_hwm": s["context"]["hwm"]["max"],
                }
            )
            j.close()
        except Exception as exc:  # noqa: BLE001 - a corrupt run must not hide the others
            found.append({"name": root.name, "path": str(root), "error": str(exc)})
    found.sort(key=lambda r: r.get("created_ns", 0), reverse=True)
    return found


class Handler(BaseHTTPRequestHandler):
    runs_dir: Path = Path("runs")
    server_version = "ampi-trace-api"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep the console quiet
        pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _root_for(self, q: Dict[str, List[str]]) -> Optional[Path]:
        name = (q.get("run") or [""])[0]
        if not name:
            return None
        cand = (self.runs_dir / name).resolve()
        try:
            cand.relative_to(self.runs_dir.resolve())
        except ValueError:
            return None
        return cand if (cand / STATE_DIR / "journal.db").exists() else None

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            if parsed.path in ("/api/health", "/health"):
                return self._json({"ok": True, "runs_dir": str(self.runs_dir)})
            if parsed.path == "/api/runs":
                return self._json({"runs": discover(self.runs_dir)})
            root = self._root_for(q)
            if parsed.path in ("/api/trace", "/api/summary", "/api/object"):
                if root is None:
                    return self._json({"error": "unknown or missing ?run="}, 404)
                j = Journal(root)
                try:
                    if parsed.path == "/api/summary":
                        return self._json(trace.summarize(j))
                    if parsed.path == "/api/trace":
                        limit = int((q.get("limit") or ["0"])[0]) or None
                        return self._json(trace.export(j, limit=limit))
                    oid = (q.get("id") or [""])[0]
                    budget = int((q.get("budget") or ["1200"])[0])
                    spec = views.parse_spec((q.get("op") or ["headtail"])[0])
                    spec["budget"] = budget
                    v = views.render_view(j, oid, spec)
                    meta = j.object_meta(oid)
                    return self._json({"handle": oid, "tokens": meta["tokens"],
                                       "summary": meta["summary"], "view_tokens": v["tokens"],
                                       "body": v["body"]})
                finally:
                    j.close()
            return self._json({"error": "not found",
                               "endpoints": ["/api/runs", "/api/trace", "/api/summary",
                                             "/api/object", "/api/health"]}, 404)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()


def serve(a: argparse.Namespace) -> Dict[str, Any]:
    runs = Path(a.runs).resolve()
    runs.mkdir(parents=True, exist_ok=True)
    Handler.runs_dir = runs
    httpd = ThreadingHTTPServer((a.host, int(a.port)), Handler)
    url = f"http://{a.host}:{a.port}"
    print(f"AMPI trace API on {url}  (runs from {runs})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    return {"served": url}
