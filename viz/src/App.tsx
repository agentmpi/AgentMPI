import { useEffect, useMemo, useRef, useState } from "react";
import type { RunDetail, RunListItem, Span } from "./types";
import { styleFor } from "./types";

const LANE_H = 26;
const LANE_GAP = 4;
const LEFT = 68;

/**
 * A Gantt-style timeline of an AgentMPI run, one lane per rank.
 *
 * The MPI community has read parallel programs this way for thirty years —
 * Jumpshot, Vampir, TAU — and the reason is that a message-passing bug is almost
 * always a *shape* in time: a rank idle while its peers work, a fan-in serialising
 * at a root, a barrier whose last arrival is minutes after its first. Those shapes
 * are invisible in a log and obvious in a picture, and they are more pronounced for
 * agent ranks than for CPU cores because the spread between the fastest and slowest
 * participant is an order of magnitude rather than a few percent.
 */
export function App() {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [hover, setHover] = useState<{ x: number; y: number; span: Span; rank: string } | null>(null);
  const [zoom, setZoom] = useState(1);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/runs")
      .then((r) => r.json())
      .then((data: RunListItem[]) => {
        if (cancelled) return;
        setRuns(data);
        setLoading(false);
        const best = [...data].sort((a, b) => (b.n_events ?? 0) - (a.n_events ?? 0))[0];
        if (best) setSelected(best.name);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selected) return;
    setDetail(null);
    fetch(`/api/run?name=${encodeURIComponent(selected)}`)
      .then((r) => r.json())
      .then((d) => (d.error ? setError(d.error) : setDetail(d)))
      .catch((e) => setError(String(e)));
  }, [selected]);

  const ranks = useMemo(() => (detail ? Object.keys(detail.lanes).sort((a, b) => +a - +b) : []), [detail]);
  const width = Math.max(900, 900 * zoom);
  const scale = (t: number) => LEFT + (t / Math.max(detail?.t_span ?? 1, 0.001)) * (width - LEFT - 16);

  if (loading) {
    return (
      <Shell>
        <div className="empty">
          <div className="spinner" />
          <p>Loading runs…</p>
        </div>
      </Shell>
    );
  }

  if (error) {
    return (
      <Shell>
        <div className="empty">
          <h2>Cannot reach the trace server</h2>
          <p className="mono">{error}</p>
          <p>
            Start it with <code>python3 scripts/trace_server.py --runs runs</code> and reload.
          </p>
        </div>
      </Shell>
    );
  }

  if (runs.length === 0) {
    return (
      <Shell>
        <div className="empty">
          <h2>No runs yet</h2>
          <p>
            Every AgentMPI job writes a durable, totally ordered event log. Produce one with
          </p>
          <pre>make microbench</pre>
          <p>or</p>
          <pre>python3 experiments/translation.py --ranks 4 --executor function</pre>
          <p>then reload this page.</p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      <aside className="sidebar">
        <h2>Runs</h2>
        <ul>
          {runs.map((r) => (
            <li key={r.name}>
              <button className={r.name === selected ? "run active" : "run"} onClick={() => setSelected(r.name)}>
                <span className="run-name">{r.name}</span>
                <span className="run-meta">
                  {r.experiment || "—"} · {r.n_ranks ?? 0} ranks · {(r.n_events ?? 0).toLocaleString()} events
                </span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <main className="main">
        {!detail ? (
          <div className="empty">
            <div className="spinner" />
            <p>Reading the event log…</p>
          </div>
        ) : (
          <>
            <header className="head">
              <div>
                <h1>{detail.name}</h1>
                <p className="sub">
                  {detail.experiment || "run"} · {detail.n_events.toLocaleString()} events ·{" "}
                  {detail.t_span.toFixed(1)} s span
                </p>
              </div>
              <div className="zoom">
                <label htmlFor="zoom">zoom</label>
                <input
                  id="zoom"
                  type="range"
                  min={1}
                  max={12}
                  step={0.5}
                  value={zoom}
                  onChange={(e) => setZoom(+e.target.value)}
                />
              </div>
            </header>

            <section className="stats">
              <Stat label="wall" value={`${detail.summary.wall_s.toFixed(1)} s`} />
              <Stat label="agent calls" value={detail.summary.agent_calls.toLocaleString()} />
              <Stat label="messages" value={detail.summary.messages.toLocaleString()} />
              <Stat
                label="tokens in / out"
                value={`${(detail.summary.tokens_in / 1000).toFixed(1)}k / ${(detail.summary.tokens_out / 1000).toFixed(1)}k`}
              />
              <Stat label="cost" value={`$${detail.summary.usd.toFixed(3)}`} />
              <Stat
                label="agent latency p50 / p95"
                value={`${detail.summary.agent_latency_p50.toFixed(1)} / ${detail.summary.agent_latency_p95.toFixed(1)} s`}
                hint="the spread, not the mean, is what a barrier waits for"
              />
              <Stat
                label="tokens deferred"
                value={(detail.summary.tokens_deferred / 1000).toFixed(1) + "k"}
                hint="kept out of context by rendezvous transfer"
              />
              <Stat
                label="contract violations"
                value={String(detail.summary.contract_violations)}
                warn={detail.summary.contract_violations > 0}
              />
              <Stat label="failures" value={String(detail.summary.failures)} warn={detail.summary.failures > 0} />
            </section>

            <div className="timeline-wrap" ref={scrollRef}>
              <svg width={width} height={ranks.length * (LANE_H + LANE_GAP) + 34} className="timeline">
                <TimeAxis span={detail.t_span} scale={scale} width={width} />
                {ranks.map((rank, i) => {
                  const y = 24 + i * (LANE_H + LANE_GAP);
                  return (
                    <g key={rank}>
                      <rect x={LEFT} y={y} width={width - LEFT - 16} height={LANE_H} className="lane-bg" />
                      <text x={8} y={y + LANE_H / 2 + 4} className="lane-label">
                        rank {rank}
                      </text>
                      {detail.lanes[rank].map((s, j) => {
                        const st = styleFor(s.kind);
                        const x = scale(s.start);
                        const w = Math.max(st.glyph === "bar" ? 2 : 1.5, scale(s.end) - x);
                        if (st.glyph === "bar") {
                          return (
                            <rect
                              key={j}
                              x={x}
                              y={y + 3}
                              width={w}
                              height={LANE_H - 6}
                              rx={2}
                              fill={st.color}
                              opacity={0.85}
                              onMouseEnter={(e) => setHover({ x: e.clientX, y: e.clientY, span: s, rank })}
                              onMouseLeave={() => setHover(null)}
                            />
                          );
                        }
                        if (st.glyph === "tick") {
                          return (
                            <line
                              key={j}
                              x1={x}
                              x2={x}
                              y1={y + 4}
                              y2={y + LANE_H - 4}
                              stroke={st.color}
                              strokeWidth={1.6}
                              onMouseEnter={(e) => setHover({ x: e.clientX, y: e.clientY, span: s, rank })}
                              onMouseLeave={() => setHover(null)}
                            />
                          );
                        }
                        return (
                          <rect
                            key={j}
                            x={x - 3}
                            y={y + LANE_H / 2 - 3}
                            width={6}
                            height={6}
                            transform={`rotate(45 ${x} ${y + LANE_H / 2})`}
                            fill={s.stale ? "#dc2626" : st.color}
                            onMouseEnter={(e) => setHover({ x: e.clientX, y: e.clientY, span: s, rank })}
                            onMouseLeave={() => setHover(null)}
                          />
                        );
                      })}
                    </g>
                  );
                })}
              </svg>
            </div>

            <Legend />
            <Collectives events={detail.collectives} />
            <Health rows={detail.health} />
          </>
        )}
      </main>

      {hover && (
        <div className="tip" style={{ left: Math.min(hover.x + 12, window.innerWidth - 280), top: hover.y + 12 }}>
          <strong>{hover.span.label || hover.span.kind}</strong>
          <div className="mono">
            rank {hover.rank} · {hover.span.kind}
          </div>
          <div className="mono">
            {hover.span.start.toFixed(2)}s
            {hover.span.end > hover.span.start ? ` → ${hover.span.end.toFixed(2)}s (${(hover.span.end - hover.span.start).toFixed(2)}s)` : ""}
          </div>
          {hover.span.tokens ? <div className="mono">{hover.span.tokens.toLocaleString()} tokens</div> : null}
          {hover.span.mode ? <div className="mono">mode {hover.span.mode}</div> : null}
          {hover.span.stale ? <div className="warn">stale write — lost-update risk</div> : null}
        </div>
      )}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app">
      <div className="topbar">
        <span className="brand">AgentMPI</span>
        <span className="tagline">run trace viewer</span>
      </div>
      <div className="body">{children}</div>
    </div>
  );
}

function Stat({ label, value, hint, warn }: { label: string; value: string; hint?: string; warn?: boolean }) {
  return (
    <div className={warn ? "stat warn-stat" : "stat"} title={hint}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function TimeAxis({ span, scale, width }: { span: number; scale: (t: number) => number; width: number }) {
  const step = niceStep(span);
  const ticks: number[] = [];
  for (let t = 0; t <= span + 1e-9; t += step) ticks.push(t);
  return (
    <g>
      <line x1={LEFT} x2={width - 16} y1={18} y2={18} className="axis" />
      {ticks.map((t) => (
        <g key={t}>
          <line x1={scale(t)} x2={scale(t)} y1={13} y2={18} className="axis" />
          <text x={scale(t)} y={10} className="axis-label">
            {t >= 60 ? `${(t / 60).toFixed(1)}m` : `${t.toFixed(t < 1 ? 2 : 0)}s`}
          </text>
        </g>
      ))}
    </g>
  );
}

function niceStep(span: number): number {
  const raw = span / 8;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-6))));
  for (const m of [1, 2, 5, 10]) if (raw <= m * mag) return m * mag;
  return 10 * mag;
}

function Legend() {
  const items: Array<[string, string]> = [
    ["agent invocation", "#3b82f6"],
    ["message sent", "#10b981"],
    ["message received", "#34d399"],
    ["window put", "#f59e0b"],
    ["window get", "#fbbf24"],
    ["lock acquired", "#ef4444"],
    ["accumulate", "#a78bfa"],
    ["stale write", "#dc2626"],
  ];
  return (
    <div className="legend">
      {items.map(([label, color]) => (
        <span key={label}>
          <i style={{ background: color }} /> {label}
        </span>
      ))}
    </div>
  );
}

function Collectives({ events }: { events: RunDetail["collectives"] }) {
  const agg = useMemo(() => {
    const m = new Map<string, { n: number; rounds: number; msgs: number; depth: number; algs: Set<string> }>();
    for (const e of events) {
      const key = e.kind;
      const cur = m.get(key) ?? { n: 0, rounds: 0, msgs: 0, depth: 0, algs: new Set<string>() };
      cur.n += 1;
      cur.rounds = Math.max(cur.rounds, e.rounds ?? 0);
      cur.msgs += e.messages_sent ?? 0;
      cur.depth = Math.max(cur.depth, e.fold_depth ?? 0);
      if (e.algorithm) cur.algs.add(e.algorithm);
      m.set(key, cur);
    }
    return [...m.entries()].sort((a, b) => b[1].n - a[1].n);
  }, [events]);

  if (agg.length === 0) return null;
  return (
    <section className="panel">
      <h3>Collectives</h3>
      <table>
        <thead>
          <tr>
            <th>operation</th>
            <th>invocations</th>
            <th>algorithms</th>
            <th>max rounds</th>
            <th>messages</th>
            <th>max fold depth</th>
          </tr>
        </thead>
        <tbody>
          {agg.map(([kind, v]) => (
            <tr key={kind}>
              <td className="mono">{kind}</td>
              <td>{v.n}</td>
              <td className="mono">{[...v.algs].join(", ") || "—"}</td>
              <td>{v.rounds}</td>
              <td>{v.msgs}</td>
              <td>{v.depth}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note">
        Fold depth is the number of successive operator applications on the longest path from a leaf to the
        result. For an exact operator it is irrelevant; for a reduction performed by a model it is what
        governs how much of the input survives.
      </p>
    </section>
  );
}

function Health({ rows }: { rows: RunDetail["health"] }) {
  if (rows.length === 0) return null;
  return (
    <section className="panel">
      <h3>Ranks</h3>
      <table>
        <thead>
          <tr>
            <th>rank</th>
            <th>state</th>
            <th>alive</th>
            <th>agent calls</th>
            <th>context occupancy</th>
            <th>suspected</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.rank} className={r.suspected ? "row-warn" : undefined}>
              <td>{r.rank}</td>
              <td className="mono">{r.state}</td>
              <td>{r.alive ? "yes" : "no"}</td>
              <td>{r.calls}</td>
              <td>{(r.occupancy * 100).toFixed(0)}%</td>
              <td className="mono">{r.suspected ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
