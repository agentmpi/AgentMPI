"""A live trace viewer: the analysis, served, and refreshing while a job runs.

A long agent run is the case where a static report is least useful.  The run this
was written during took hours, degraded twice, and was rescued three times by
adding executors mid-flight --- and every one of those decisions needed an answer
to "what is the population doing *right now*", which a report generated after the
fact cannot give.

So this serves the same :mod:`ampi.analysis` measurements over HTTP, reading the
live job rather than a finished trace, and refreshing on a timer.  Three
properties are deliberate:

*It reads the job, not a snapshot.*  Pointing it at ``--job-root`` re-reads the
event log on every poll, so the timeline grows as the run does.  Pointed at a
``.trace.jsonl`` it serves that file instead, which is what you want for a
post-mortem.

*It has no build step.*  One stdlib HTTP server and one self-contained HTML page
with vanilla JavaScript.  A viewer that needs ``npm install`` before it can show
you why your sixty-four rank job is wedged is a viewer you will not use, and the
whole point is to be reachable at the moment something is going wrong.

*It shares the taxonomy.*  Colour encodes role and glyph encodes duration, from
:mod:`ampi.analysis.style`, so a shape found here means the same thing as in the
figures and in the terminal digest.  A viewer that disagreed with the figures
about what red means would be worse than no viewer.

It also shows the broker queue when a job root is given, because on a long run the
first question is usually not about the protocol at all: it is whether anybody is
actually working, and a rank blocked because its peers are busy looks exactly like
a rank blocked because nobody ever claimed its task.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import style as st
from .model import analyse, load_events
from .report import findings

__all__ = ["serve", "TraceSource"]


class TraceSource:
    """Where the viewer gets its events, and how often it re-reads them.

    Cached for ``min_interval`` seconds because a browser polling every two
    seconds must not turn into a scan of a growing event table every two seconds:
    on the p=16 production run the log was already thousands of rows while the job
    was still writing it, and a viewer that made the run slower would be a viewer
    that changed what it was measuring.
    """

    def __init__(
        self,
        *,
        job_root: str | Path | None = None,
        trace: str | Path | None = None,
        name: str = "",
        campaign: str = "",
        min_interval: float = 2.0,
    ) -> None:
        if not job_root and not trace:
            raise SystemExit("give either --job-root (live) or --trace (a finished run)")
        self.job_root = str(job_root) if job_root else ""
        self.trace = str(trace) if trace else ""
        self.name = name or (Path(self.trace).name if self.trace else Path(self.job_root).parent.name)
        self.campaign = campaign
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0

    def _read_events(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.trace:
            return load_events(self.trace), {}
        from ampi import Ampi

        amp = Ampi(self.job_root, rank=0, allow_volatile=True)
        try:
            events = amp.events()
            queue: dict[str, Any] = {}
            if self.campaign:
                rows = amp.device.scan("task", {"campaign": self.campaign})
                by_state: dict[str, int] = {}
                for r in rows:
                    by_state[r["state"]] = by_state.get(r["state"], 0) + 1
                queue = {
                    "tasks": len(rows),
                    "by_state": by_state,
                    "queued_ranks": sorted({r["rank"] for r in rows if r["state"] == "queued"}),
                    "claimed_ranks": sorted({r["rank"] for r in rows if r["state"] == "claimed"}),
                    "executors": sorted({r["worker_id"] for r in rows if r.get("worker_id")}),
                    "requeued": sum(1 for r in rows if r.get("requeued")),
                }
            return events, queue
        finally:
            amp.close()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            if self._cached is not None and now - self._cached_at < self.min_interval:
                return self._cached
            try:
                events, queue = self._read_events()
            except Exception as exc:  # a live job mid-write, or a job not created yet
                return {"error": str(exc), "name": self.name, "ready": False}
            if not events:
                return {"error": "no events yet", "name": self.name, "ready": False}

            a = analyse(events, name=self.name)
            payload = {
                "ready": True,
                "name": self.name,
                "live": bool(self.job_root),
                "generated_at": now,
                "metrics": a.to_dict(),
                "findings": findings(a),
                "queue": queue,
                "t0": a.t0,
                "wall_s": a.wall_s,
                "world_size": a.world_size,
                # The timeline needs every instant, not the aggregates, so it is
                # built here rather than in the browser: shipping the raw log to a
                # page that then has to classify ten thousand events would move
                # the taxonomy into JavaScript, where it could drift.
                "spans": [
                    {"rank": r, "t0": round(s, 3), "t1": round(e, 3), "label": lab}
                    for r, s, e, lab in a.work_spans
                ],
                "instants": _instants(a),
                "roles": {r: {"color": st.ROLE_COLOR[r], "label": st.ROLE_LABEL[r]}
                          for r in st.ROLE_ORDER},
                "phases": [p.to_dict() for p in a.phases],
                "collectives": [c.to_dict() for c in a.collectives],
            }
            self._cached, self._cached_at = payload, now
            return payload


#: Instants worth drawing per rank.  Work spans are drawn as bars from ``spans``
#: and ``coll.join`` is omitted: it fires once per rank per collective and would
#: bury the failures under a wall of ticks that carries no extra information.
_SKIP = {"broker.claim", "broker.submit", "task.start", "task.done", "coll.join"}
_MAX_INSTANTS = 8000


def _instants(a: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in a.events:
        rank = int(e.get("rank", -1))
        if rank < 0 or e["kind"] in _SKIP:
            continue
        style = st.style_for(e["kind"])
        out.append({
            "rank": rank,
            "t": round(e["ts"] - a.t0, 3),
            "kind": e["kind"],
            "color": style.color,
            "glyph": style.glyph,
            "label": str(e.get("label") or e.get("note") or ""),
        })
        if len(out) >= _MAX_INSTANTS:
            break
    return out


def _handler(source: TraceSource, refresh: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:  # keep the console for the run
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(PAGE.replace("__REFRESH__", str(int(refresh * 1000))).encode(),
                           "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send(json.dumps(source.snapshot(), default=str).encode(),
                           "application/json; charset=utf-8")
            elif path == "/healthz":
                self._send(b"ok", "text/plain")
            else:
                self._send(b"not found", "text/plain", status=404)

    return Handler


def serve(
    *,
    job_root: str | Path | None = None,
    trace: str | Path | None = None,
    campaign: str = "",
    name: str = "",
    host: str = "0.0.0.0",  # noqa: S104 - the point is to be reachable
    port: int = 7842,
    refresh: float = 5.0,
) -> None:
    source = TraceSource(job_root=job_root, trace=trace, name=name, campaign=campaign)
    server = ThreadingHTTPServer((host, port), _handler(source, refresh))
    print(f"AgentMPI trace viewer on http://{host}:{port}  ({'live' if job_root else 'file'})")
    server.serve_forever()


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentMPI trace viewer</title>
<style>
:root{
  --bg:#0b1020; --panel:#131a2e; --panel2:#1a2338; --line:#263352;
  --fg:#e8edf7; --muted:#94a3b8; --accent:#60a5fa;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{padding:14px 18px;border-bottom:1px solid var(--line);
 display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;position:sticky;top:0;
 background:var(--bg);z-index:5}
h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.3px}
.tag{font-size:11px;color:var(--muted)}
.live{color:#10b981}
.stale{color:#ef4444}
main{padding:18px;display:grid;gap:18px;max-width:1500px}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
h2{font-size:12px;margin:0 0 10px;color:var(--muted);font-weight:600;
 text-transform:uppercase;letter-spacing:.8px}
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.stat{background:var(--panel2);border-radius:6px;padding:9px 11px}
.stat .k{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.5px}
.stat .v{font-size:18px;margin-top:2px}
ul.find{list-style:none;margin:0;padding:0;display:grid;gap:6px}
ul.find li{padding:7px 10px;border-radius:6px;background:var(--panel2);
 border-left:3px solid var(--muted)}
li.error{border-left-color:#ef4444}
li.warn{border-left-color:#f59e0b}
li.note{border-left-color:#60a5fa}
li.ok{border-left-color:#10b981}
.lvl{color:var(--muted);margin-right:7px;text-transform:uppercase;font-size:10.5px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:var(--muted);font-weight:600;padding:5px 8px;
 border-bottom:1px solid var(--line);font-size:10.5px;text-transform:uppercase;letter-spacing:.4px}
td{padding:5px 8px;border-bottom:1px solid rgba(38,51,82,.5)}
tr:hover td{background:var(--panel2)}
.num{text-align:right;font-variant-numeric:tabular-nums}
.bad{color:#ef4444}
.dim{color:var(--muted)}
#tl{width:100%;overflow-x:auto}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px;font-size:11px;color:var(--muted)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;
 vertical-align:-1px}
.pill{display:inline-block;padding:1px 7px;border-radius:10px;background:var(--panel2);
 margin-right:5px;font-size:11px}
</style></head><body>
<header>
  <h1>AgentMPI trace viewer</h1>
  <span class="tag" id="run"></span>
  <span class="tag" id="mode"></span>
  <span class="tag" id="updated"></span>
</header>
<main>
  <section><h2>Run</h2><div class="stats" id="stats"></div></section>
  <section><h2>Queue</h2><div id="queue" class="dim">no campaign given</div></section>
  <section><h2>Findings</h2><ul class="find" id="findings"></ul></section>
  <section><h2>Timeline</h2><div id="tl"></div><div class="legend" id="legend"></div></section>
  <section><h2>Phases</h2><table id="phases"></table></section>
  <section><h2>Collectives</h2><table id="colls"></table></section>
  <section><h2>Ranks</h2><table id="ranks"></table></section>
</main>
<script>
const REFRESH = __REFRESH__;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const fmt = (n, d=0) => (n===null||n===undefined) ? "-" :
  Number(n).toLocaleString(undefined,{minimumFractionDigits:d,maximumFractionDigits:d});
const dur = s => s<90 ? fmt(s,1)+"s" : s<5400 ? fmt(s/60,1)+"m" : fmt(s/3600,2)+"h";

function stats(m){
  const c = m.concurrency || {};
  const rows = [
    ["world size", m.world_size], ["ranks seen", m.n_ranks_seen],
    ["wall clock", dur(m.wall_s)], ["events", fmt(m.n_events)],
    ["agent tasks", fmt((m.tasks||{}).submitted||0)],
    ["executor sessions", fmt(Object.keys(m.executors||{}).length)],
    ["collectives", m.n_collectives],
    ["work rank-s", fmt(m.work_rank_seconds)],
    ["blocked rank-s", fmt(m.collective_rank_seconds)],
    ["coordination", fmt(m.coordination_share*100,1)+"%"],
    ["parallelism", fmt(c.achieved_parallelism,2)+"x"],
    ["efficiency", fmt(c.parallel_efficiency*100,1)+"%"],
    ["imbalance", fmt(m.imbalance,2)],
    ["conflicts lifted", m.conflicts_lifted],
    ["starved tasks", (m.starved_tasks||[]).length],
    ["degraded", m.degraded ? "yes" : "no"],
  ];
  $("stats").innerHTML = rows.map(([k,v]) =>
    `<div class="stat"><div class="k">${esc(k)}</div><div class="v${
      (k==="degraded"&&v==="yes")||(k==="starved tasks"&&v>0)?" bad":""
    }">${esc(v)}</div></div>`).join("");
}

function queue(q){
  if(!q || !q.tasks){ $("queue").innerHTML = '<span class="dim">no campaign given</span>'; return; }
  const st = Object.entries(q.by_state||{}).map(([k,v])=>
    `<span class="pill">${esc(k)}: ${v}</span>`).join("");
  $("queue").innerHTML = st +
    `<span class="pill">executors: ${(q.executors||[]).length}</span>` +
    `<span class="pill">requeued: ${q.requeued||0}</span>` +
    (q.queued_ranks?.length ? `<div class="dim" style="margin-top:8px">queued ranks: ${
      q.queued_ranks.join(", ")}</div>` : "") +
    (q.claimed_ranks?.length ? `<div class="dim">working ranks: ${
      q.claimed_ranks.join(", ")}</div>` : "");
}

function findings(f){
  $("findings").innerHTML = (f||[]).map(x =>
    `<li class="${esc(x.level)}"><span class="lvl">${esc(x.level)}</span>${esc(x.text)}</li>`
  ).join("") || '<li class="ok"><span class="lvl">ok</span>nothing flagged</li>';
}

function timeline(d){
  const ranks = [...new Set([
    ...d.spans.map(s=>s.rank), ...d.instants.map(i=>i.rank)
  ])].sort((a,b)=>a-b);
  if(!ranks.length){ $("tl").innerHTML = '<div class="dim">no per-rank activity yet</div>'; return; }
  const H = 17, PAD_L = 52, PAD_T = 8, W = Math.max(900, $("tl").clientWidth - 20);
  const h = ranks.length*H + PAD_T*2 + 20;
  const wall = Math.max(d.wall_s, 1e-6);
  const x = t => PAD_L + (t/wall)*(W-PAD_L-14);
  const y = r => PAD_T + ranks.indexOf(r)*H;
  let s = `<svg width="${W}" height="${h}" style="display:block">`;
  ranks.forEach(r => {
    s += `<rect x="${PAD_L}" y="${y(r)+2}" width="${W-PAD_L-14}" height="${H-4}"
           fill="#1a2338"/>`;
    s += `<text x="6" y="${y(r)+H/2+4}" fill="#94a3b8" font-size="10">r${r}</text>`;
  });
  // Gridlines every ~10% of wall, labelled, so a gap can be read off directly.
  for(let i=0;i<=10;i++){
    const t = wall*i/10;
    s += `<line x1="${x(t)}" y1="${PAD_T}" x2="${x(t)}" y2="${h-20}"
           stroke="#263352" stroke-width="0.5"/>`;
    s += `<text x="${x(t)}" y="${h-6}" fill="#94a3b8" font-size="9"
           text-anchor="middle">${dur(t)}</text>`;
  }
  d.spans.forEach(sp => {
    const w = Math.max(x(sp.t1)-x(sp.t0), 1.5);
    s += `<rect x="${x(sp.t0)}" y="${y(sp.rank)+3}" width="${w}" height="${H-6}"
           fill="#3b82f6" opacity="0.9"><title>r${sp.rank} ${esc(sp.label)} ${
           dur(sp.t1-sp.t0)}</title></rect>`;
  });
  d.instants.forEach(it => {
    const px = x(it.t);
    if(it.glyph === "diamond"){
      const cy = y(it.rank)+H/2;
      s += `<polygon points="${px},${cy-3.4} ${px+3.4},${cy} ${px},${cy+3.4} ${px-3.4},${cy}"
             fill="${it.color}"><title>r${it.rank} ${esc(it.kind)} ${esc(it.label)} @${
             dur(it.t)}</title></polygon>`;
    } else {
      s += `<line x1="${px}" y1="${y(it.rank)+3}" x2="${px}" y2="${y(it.rank)+H-3}"
             stroke="${it.color}" stroke-width="1"><title>r${it.rank} ${esc(it.kind)} ${
             esc(it.label)} @${dur(it.t)}</title></line>`;
    }
  });
  s += "</svg>";
  $("tl").innerHTML = s;
  $("legend").innerHTML = Object.entries(d.roles).map(([k,v]) =>
    `<span><i style="background:${v.color}"></i>${esc(v.label)}</span>`).join("");
}

function table(el, cols, rows){
  el.innerHTML = "<thead><tr>" + cols.map(c=>`<th>${esc(c[0])}</th>`).join("") +
    "</tr></thead><tbody>" + rows.map(r =>
      "<tr>" + cols.map(c => {
        const v = c[1](r);
        return `<td class="${c[2]||""}">${v}</td>`;
      }).join("") + "</tr>").join("") + "</tbody>";
}

function render(d){
  if(!d.ready){ $("updated").textContent = d.error || "waiting"; return; }
  const m = d.metrics;
  $("run").textContent = d.name;
  $("mode").innerHTML = d.live ? '<span class="live">live</span>' : "file";
  $("updated").textContent = "updated " + new Date().toLocaleTimeString();
  stats(m); queue(d.queue); findings(d.findings); timeline(d);

  table($("phases"),
    [["phase", r=>esc(r.name)], ["starts", r=>dur(r.t_start), "num"],
     ["duration", r=>dur(r.duration_s), "num"], ["ranks", r=>r.ranks, "num"]],
    d.phases);

  const colls = [...d.collectives].sort((a,b)=>b.rank_wait_s-a.rank_wait_s);
  table($("colls"),
    [["collective", r=>esc(r.op+":"+r.label)],
     ["algorithm", r=>esc(r.algorithm||"journal")],
     ["arrived", r=>`<span class="${r.complete?"":"bad"}">${r.n_participants}/${r.size}</span>`, "num"],
     ["blocked", r=>dur(r.rank_wait_s), "num"],
     ["slowest", r=>dur(r.max_wait_s), "num"],
     ["arrival skew", r=>dur(r.arrival_skew_s), "num"],
     ["last in", r=>r.straggler===null?"-":"r"+r.straggler, "num"],
     ["conflicts", r=>r.conflicts||"", "num"]],
    colls);

  table($("ranks"),
    [["rank", r=>"r"+r.rank], ["tasks", r=>r.n_tasks, "num"],
     ["busy", r=>dur(r.busy_s), "num"], ["blocked", r=>dur(r.blocked_s), "num"],
     ["occupancy", r=>fmt(r.occupancy*100,0)+"%", "num"],
     ["context", r=>r.context_budget?`${fmt(r.context_used)}/${fmt(r.context_budget)}`:"-", "num"],
     ["trouble", r=>r.n_trouble?`<span class="bad">${r.n_trouble}</span>`:"", "num"],
     ["executor", r=>esc((r.executors||[]).join(", "))]],
    m.ranks);
}

async function tick(){
  try{
    const r = await fetch("/api/state", {cache:"no-store"});
    render(await r.json());
  }catch(e){
    $("updated").innerHTML = '<span class="stale">disconnected</span>';
  }
}
tick(); setInterval(tick, REFRESH);
window.addEventListener("resize", () => tick());
</script></body></html>
"""
