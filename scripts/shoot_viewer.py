"""Capture the trace viewer's dashboard for every run, as PNG.

An analysis of a run should include looking at it. Not at a re-plot of it --- at the dashboard
itself, the thing a person would actually open --- because the viewer makes choices the figures
do not: it lays out lanes against a shared clock, colours by role, aggregates the collectives
panel, and shows rank health beside the timeline. Reviewing the real interface is how a reader
catches something the metrics did not think to compute.

Doing that five hundred times by hand is not feasible, and launching five hundred browsers is
worse: each Chrome start here costs minutes, and running them concurrently would thrash the
machine. So one browser is driven over the DevTools Protocol, navigating from run to run and
screenshotting each. One process, one page, five hundred captures.

    python3 scripts/shoot_viewer.py --url http://127.0.0.1:43191
    python3 scripts/shoot_viewer.py --match real- --out analysis/runs

The viewer must already be serving. Static mode is enough --- these come from the committed
traces --- so no trace server is needed.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "analysis" / "runs"
MANIFEST = REPO / "traces" / "manifest.json"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _chrome_binary() -> str:
    for name in ("google-chrome", "chromium", "chromium-browser", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("no Chrome/Chromium binary found")


class Browser:
    """A single headless Chrome driven over the DevTools Protocol.

    Written against the raw protocol rather than a driver library to keep the dependency
    surface at zero: the whole interaction is navigate, wait, screenshot, and adding Playwright
    for that would mean a browser download in every environment that wants to rebuild figures.
    """

    def __init__(self, *, headless: bool = True, width: int = 1600, height: int = 1100) -> None:
        self.port = _free_port()
        self.profile = Path(tempfile.mkdtemp(prefix="ampi-shoot-"))
        self.width, self.height = width, height
        args = [
            _chrome_binary(),
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            f"--window-size={width},{height}",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-background-networking",
            "about:blank",
        ]
        if headless:
            args.insert(1, "--headless=new")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.ws: Any = None
        self._msg_id = 0

    # -- protocol plumbing ------------------------------------------------------------

    def _http(self, path: str, timeout: float = 2.0) -> Any:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())

    def wait_ready(self, timeout: float = 180.0) -> None:
        """Chrome's first start on a cold profile can take minutes here, so this is patient."""
        deadline = time.time() + timeout
        last: Exception | None = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"chrome exited with code {self.proc.returncode}")
            try:
                targets = self._http("/json/list")
                page = next((t for t in targets if t.get("type") == "page"), None)
                if page and page.get("webSocketDebuggerUrl"):
                    self._connect(page["webSocketDebuggerUrl"])
                    return
            except (urllib.error.URLError, OSError, StopIteration) as exc:
                last = exc
            time.sleep(1.0)
        raise RuntimeError(f"chrome devtools never became ready: {last!r}")

    def _connect(self, ws_url: str) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("the websockets package is required: pip install websockets") from exc
        # The connection outlives this call by design -- one socket serves hundreds of captures --
        # so it cannot be held in a `with` block. `legacy=False` opts out of the wrapper that
        # warns about exactly that, since the lifetime here is managed by `close()`.
        self.ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=30).__enter__()

    def call(self, method: str, params: dict | None = None, timeout: float = 120.0) -> dict:
        self._msg_id += 1
        mid = self._msg_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv(timeout=max(1.0, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} failed: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(f"{method} timed out")

    # -- page operations --------------------------------------------------------------

    def goto(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})

    def eval(self, expression: str, timeout: float = 60.0) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 60.0, interval: float = 0.25) -> bool:
        """Poll a JavaScript predicate. Used instead of a fixed sleep so a slow run does not
        produce a screenshot of a loading spinner."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.eval(expression):
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def screenshot(self, path: Path, *, full_page: bool = True) -> Path:
        params: dict[str, Any] = {"format": "png", "captureBeyondViewport": full_page}
        if full_page:
            metrics = self.call("Page.getLayoutMetrics")
            size = metrics.get("contentSize") or {}
            # Capped because a 64-rank run's page is tall enough that the encode dominates the
            # runtime and the result is unreadable at any sensible zoom anyway.
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": min(int(size.get("width", self.width)), 2400),
                "height": min(int(size.get("height", self.height)), 4200),
                "scale": 1,
            }
        data = self.call("Page.captureScreenshot", params).get("data", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        return path

    def close(self) -> None:
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.profile, ignore_errors=True)


#: The viewer selects a run from its sidebar, so a capture has to click the right entry. Doing it
#: through the DOM rather than a URL parameter because the viewer keeps selection in React state;
#: adding a route just for screenshots would mean testing a path no human uses.
SELECT_JS = """
(() => {
  const want = %s;
  const buttons = Array.from(document.querySelectorAll('button.run, button.group-head'));
  // Expand every collapsed campaign group first: the target may be hidden inside one.
  for (const b of buttons) {
    if (b.classList.contains('group-head') && b.getAttribute('aria-expanded') === 'false') b.click();
  }
  const runs = Array.from(document.querySelectorAll('button.run'));
  const hit = runs.find((b) => {
    const n = b.querySelector('.run-name');
    return n && (n.textContent === want || want.endsWith(n.textContent));
  });
  if (!hit) return false;
  hit.click();
  return true;
})()
"""

READY_JS = """
(() => {
  const h1 = document.querySelector('.head h1');
  const svg = document.querySelector('svg.timeline');
  return !!(h1 && h1.textContent === %s && svg && svg.querySelectorAll('rect,line').length > 0);
})()
"""


def capture(browser: Browser, url: str, name: str, out: Path, *, timeout: float) -> bool:
    quoted = json.dumps(name)
    if not browser.eval(SELECT_JS % quoted):
        # Filter the sidebar to surface the run, then retry: with 500 entries the target may not
        # be rendered until the search narrows the list.
        browser.eval(
            "(() => { const i = document.querySelector('input.search');"
            "if (!i) return false;"
            "const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;"
            f"s.call(i, {quoted});"
            "i.dispatchEvent(new Event('input', {bubbles: true})); return true; })()"
        )
        time.sleep(0.6)
        if not browser.eval(SELECT_JS % quoted):
            return False
    if not browser.wait_for(READY_JS % quoted, timeout=timeout):
        return False
    # One frame for the SVG to settle after React commits.
    time.sleep(0.35)
    browser.screenshot(out)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:43191", help="a serving trace viewer")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="per-run output directory root")
    ap.add_argument("--filename", default="viewer.png")
    ap.add_argument("--match", help="only runs whose name contains this")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--skip-existing", action="store_true")
    cfg = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    names = [r["name"] for r in manifest["runs"] if not cfg.match or cfg.match in r["name"]]
    out_root = Path(cfg.out)
    if cfg.skip_existing:
        names = [n for n in names if not (out_root / n / cfg.filename).exists()]
    if cfg.limit:
        names = names[: cfg.limit]
    if not names:
        print("nothing to capture")
        return 0

    print(f"capturing {len(names)} runs from {cfg.url}")
    browser = Browser()
    ok = failed = 0
    try:
        browser.wait_ready()
        browser.call("Page.enable")
        browser.goto(cfg.url)
        if not browser.wait_for("!!document.querySelector('button.run')", timeout=120):
            print("the viewer never rendered a run list; is it serving?")
            return 1
        for i, name in enumerate(names, 1):
            target = out_root / name / cfg.filename
            try:
                if capture(browser, cfg.url, name, target, timeout=cfg.timeout):
                    ok += 1
                else:
                    failed += 1
                    print(f"  MISS {name}")
            except Exception as exc:
                failed += 1
                print(f"  FAIL {name}: {exc!r}")
            if i % 25 == 0:
                print(f"  ... {i}/{len(names)} ({ok} captured, {failed} failed)")
    finally:
        browser.close()
    print(f"captured {ok}, failed {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
