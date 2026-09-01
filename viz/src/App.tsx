import { useEffect, useMemo, useState } from "react";

import {
  EVENT_TYPES,
  type EventType,
  type RunDetail,
  type RunListItem,
  type TraceEvent,
  type VisualEvent,
  styleFor,
} from "./types";

const LEFT = 78;
const LANE_HEIGHT = 34;
const COLLECTIVE_KINDS = new Set([
  "allgather",
  "allreduce",
  "alltoall",
  "barrier",
  "bcast",
  "exscan",
  "gather",
  "neighbor_allgather",
  "reduce",
  "reduce_scatter",
  "scan",
  "scatter",
]);

function numberField(event: TraceEvent, field: string): number | null {
  const value = event[field];
  return typeof value === "number" ? value : null;
}

function stringField(event: TraceEvent, field: string): string {
  const value = event[field];
  return typeof value === "string" ? value : "";
}

function normalize(detail: RunDetail): VisualEvent[] {
  const base = detail.started_at;
  const events: VisualEvent[] = detail.work_spans.map((span) => ({
    type: "work",
    rank: span.rank,
    at: span.start - base,
    end: span.end - base,
    label: span.label || span.aid,
    outcome: span.outcome,
    tokens: span.tokens,
  }));
  for (const source of detail.events) {
    const common = {
      rank: source.rank,
      at: source.ts - base,
      label: stringField(source, "label") || source.kind,
      source,
    };
    if (source.kind === "send" || source.kind === "recv") {
      events.push({
        ...common,
        type: "message",
        direction: source.kind,
        peer: numberField(source, source.kind === "send" ? "dst" : "src"),
        tokens: numberField(source, "tokens") ?? numberField(source, "charged") ?? 0,
      });
    } else if (source.kind === "coll.join") {
      events.push({
        ...common,
        type: "collective",
        operation: stringField(source, "arg_kind") || "collective",
        phase: "join",
      });
    } else if (COLLECTIVE_KINDS.has(source.kind)) {
      events.push({
        ...common,
        type: "collective",
        operation: source.kind,
        phase: "complete",
      });
    } else if (source.kind === "win.lock" || source.kind === "win.unlock") {
      events.push({
        ...common,
        type: "lock",
        action: source.kind === "win.lock" ? "lock" : "unlock",
      });
    } else if (source.kind.startsWith("win.")) {
      events.push({
        ...common,
        type: "rma",
        action: source.kind.slice(4),
        stale: source.kind === "win.stale",
      });
    } else if (
      source.kind.startsWith("failure.") ||
      source.kind === "rank.error" ||
      source.kind === "ctx.stall" ||
      source.kind === "coll.dropped" ||
      ["broker.giveup", "broker.reject", "broker.requeue"].includes(source.kind)
    ) {
      events.push({ ...common, type: "fault", action: source.kind });
    } else if (
      source.kind === "job.create" ||
      source.kind.startsWith("init") ||
      source.kind === "finalize" ||
      source.kind === "respawn" ||
      source.kind === "recover" ||
      source.kind === "fence" ||
      source.kind.startsWith("comm.") ||
      source.kind.endsWith(".create")
    ) {
      events.push({ ...common, type: "lifecycle", action: source.kind });
    }
  }
  return events.sort((a, b) => a.at - b.at);
}

export function App() {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [rankFilter, setRankFilter] = useState("all");
  const [textFilter, setTextFilter] = useState("");
  const [enabled, setEnabled] = useState<Set<EventType>>(() => new Set(EVENT_TYPES));
  const [hover, setHover] = useState<VisualEvent | null>(null);

  useEffect(() => {
    fetch("/api/runs")
      .then(async (response) => {
        if (!response.ok) throw new Error(`runs API returned ${response.status}`);
        return (await response.json()) as RunListItem[];
      })
      .then((items) => {
        setRuns(items);
        if (items[0]) setSelected(items[0].name);
      })
      .catch((caught: unknown) => setError(String(caught)));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setDetail(null);
    setError("");
    fetch(`/api/run?name=${encodeURIComponent(selected)}`)
      .then(async (response) => {
        const body = (await response.json()) as RunDetail | { error: string };
        if (!response.ok || "error" in body) {
          throw new Error("error" in body ? body.error : `run API returned ${response.status}`);
        }
        return body;
      })
      .then(setDetail)
      .catch((caught: unknown) => setError(String(caught)));
  }, [selected]);

  const visualEvents = useMemo(() => (detail ? normalize(detail) : []), [detail]);
  const ranks = useMemo(() => {
    if (!detail) return [];
    return Array.from({ length: detail.world_size }, (_, rank) => rank);
  }, [detail]);
  const filtered = useMemo(() => {
    const needle = textFilter.trim().toLowerCase();
    return visualEvents.filter(
      (event) =>
        enabled.has(event.type) &&
        (rankFilter === "all" || event.rank === Number(rankFilter)) &&
        (!needle ||
          event.label.toLowerCase().includes(needle) ||
          event.type.includes(needle) ||
          JSON.stringify(event.source ?? {}).toLowerCase().includes(needle)),
    );
  }, [enabled, rankFilter, textFilter, visualEvents]);
  const width = Math.max(980, 980 * zoom);
  const scale = (seconds: number) =>
    LEFT + (seconds / Math.max(detail?.duration_s ?? 0, 0.001)) * (width - LEFT - 20);

  function toggle(type: EventType) {
    setEnabled((current) => {
      const next = new Set(current);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <span className="brand">AMPI</span>
          <span className="tagline">trace explorer</span>
        </div>
        <div className="read-only">READ ONLY</div>
      </header>
      <div className="body">
        <aside className="sidebar">
          <h2>Harness runs</h2>
          {runs.map((run) => (
            <button
              className={run.name === selected ? "run selected" : "run"}
              key={run.name}
              onClick={() => setSelected(run.name)}
            >
              <span>{run.name}</span>
              <small>
                {run.world_size} ranks · {run.n_events} events · {formatDuration(run.duration_s)}
              </small>
            </button>
          ))}
          {!runs.length && !error ? <p className="muted">No harness.trace.jsonl files found.</p> : null}
        </aside>
        <main>
          {error ? <Empty title="Trace unavailable" body={error} /> : null}
          {!error && selected && !detail ? <Empty title="Reading trace…" body={selected} /> : null}
          {!error && !selected ? (
            <Empty title="No exported runs" body="Create runs/<name>/harness.trace.jsonl, then reload." />
          ) : null}
          {detail ? (
            <>
              <section className="run-head">
                <div>
                  <h1>{detail.name}</h1>
                  <p>
                    {detail.world_size} ranks · {detail.events.length.toLocaleString()} events ·{" "}
                    {formatDuration(detail.duration_s)}
                  </p>
                </div>
                <label className="zoom">
                  Zoom
                  <input
                    type="range"
                    min="1"
                    max="16"
                    step="0.5"
                    value={zoom}
                    onChange={(event) => setZoom(Number(event.target.value))}
                  />
                </label>
              </section>
              <Concurrency detail={detail} />
              <section className="filters" aria-label="Timeline filters">
                <select
                  aria-label="Rank"
                  value={rankFilter}
                  onChange={(event) => setRankFilter(event.target.value)}
                >
                  <option value="all">All ranks</option>
                  {ranks.map((rank) => (
                    <option value={rank} key={rank}>
                      Rank {rank}
                    </option>
                  ))}
                </select>
                <input
                  aria-label="Search events"
                  type="search"
                  placeholder="Filter label or field…"
                  value={textFilter}
                  onChange={(event) => setTextFilter(event.target.value)}
                />
                <div className="filter-types">
                  {EVENT_TYPES.map((type) => (
                    <label key={type}>
                      <input
                        type="checkbox"
                        checked={enabled.has(type)}
                        onChange={() => toggle(type)}
                      />
                      <i className={`swatch ${type}`} />
                      {type}
                    </label>
                  ))}
                </div>
              </section>
              <section className="timeline-shell">
                <svg
                  className="timeline"
                  width={width}
                  height={42 + ranks.length * LANE_HEIGHT}
                  role="img"
                  aria-label="Timeline with one lane per rank"
                >
                  <TimeAxis duration={detail.duration_s} width={width} scale={scale} />
                  {ranks.map((rank, index) => {
                    const y = 30 + index * LANE_HEIGHT;
                    const visible = rankFilter === "all" || rank === Number(rankFilter);
                    return (
                      <g key={rank} opacity={visible ? 1 : 0.25}>
                        <rect className="lane" x={LEFT} y={y} width={width - LEFT - 20} height={28} />
                        <text className="lane-label" x={10} y={y + 18}>
                          rank {rank}
                        </text>
                        {filtered
                          .filter((event) => event.rank === rank)
                          .map((event, eventIndex) => (
                            <Glyph
                              event={event}
                              key={`${event.type}-${event.at}-${eventIndex}`}
                              scale={scale}
                              y={y}
                              onHover={setHover}
                            />
                          ))}
                      </g>
                    );
                  })}
                </svg>
              </section>
              <div className="timeline-foot">
                Showing {filtered.length.toLocaleString()} of {visualEvents.length.toLocaleString()} visual events
              </div>
              <CollectiveSummary events={visualEvents} />
              <Report report={detail.report} />
              {hover ? <Tooltip event={hover} /> : null}
            </>
          ) : null}
        </main>
      </div>
    </div>
  );
}

function Glyph({
  event,
  scale,
  y,
  onHover,
}: {
  event: VisualEvent;
  scale: (seconds: number) => number;
  y: number;
  onHover: (event: VisualEvent | null) => void;
}) {
  const style = styleFor(event);
  const x = scale(event.at);
  const common = {
    fill: style.color,
    stroke: style.color,
    onMouseEnter: () => onHover(event),
    onMouseLeave: () => onHover(null),
  };
  switch (event.type) {
    case "work":
      return (
        <rect
          {...common}
          x={x}
          y={y + 4}
          width={Math.max(2, scale(event.end) - x)}
          height={20}
          rx={3}
          opacity={0.82}
        />
      );
    case "message":
      return <line {...common} x1={x} x2={x} y1={y + 5} y2={y + 23} strokeWidth={2} />;
    case "collective":
      return event.phase === "join" ? (
        <path {...common} d={`M ${x} ${y + 5} l 5 18 h -10 z`} />
      ) : (
        <circle {...common} cx={x} cy={y + 14} r={4} />
      );
    case "lifecycle":
      return <circle {...common} cx={x} cy={y + 14} r={3.5} fill="none" strokeWidth={2} />;
    case "fault":
      return (
        <g {...common} strokeWidth={2.5}>
          <line x1={x - 4} x2={x + 4} y1={y + 10} y2={y + 18} />
          <line x1={x + 4} x2={x - 4} y1={y + 10} y2={y + 18} />
        </g>
      );
    case "rma":
      return (
        <rect
          {...common}
          x={x - 4}
          y={y + 10}
          width={8}
          height={8}
          transform={`rotate(45 ${x} ${y + 14})`}
        />
      );
    case "lock":
      return (
        <g {...common} fill="none" strokeWidth={1.8}>
          <rect x={x - 4} y={y + 13} width={8} height={7} rx={1} />
          <path d={`M ${x - 2.5} ${y + 13} v -3 a 2.5 2.5 0 0 1 5 0 v 3`} />
        </g>
      );
    default: {
      const exhaustive: never = event;
      return exhaustive;
    }
  }
}

function TimeAxis({
  duration,
  width,
  scale,
}: {
  duration: number;
  width: number;
  scale: (seconds: number) => number;
}) {
  const step = niceStep(duration);
  const ticks: number[] = [];
  for (let value = 0; value <= duration + step / 100; value += step) ticks.push(value);
  return (
    <g>
      <line className="axis" x1={LEFT} x2={width - 20} y1={24} y2={24} />
      {ticks.map((tick) => (
        <g key={tick}>
          <line className="axis" x1={scale(tick)} x2={scale(tick)} y1={19} y2={28} />
          <text className="axis-label" x={scale(tick)} y={14}>
            {formatDuration(tick)}
          </text>
        </g>
      ))}
    </g>
  );
}

function Concurrency({ detail }: { detail: RunDetail }) {
  const summary = detail.concurrency;
  return (
    <section className="stats">
      <Stat label="peak concurrency" value={`${summary.peak} / ${detail.world_size}`} />
      <Stat label="average concurrency" value={summary.average.toFixed(2)} />
      <Stat label="achieved utilization" value={`${(summary.utilization * 100).toFixed(1)}%`} />
      <Stat label="broker work" value={`${summary.busy_rank_seconds.toFixed(1)} rank-s`} />
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function CollectiveSummary({ events }: { events: VisualEvent[] }) {
  const rows = useMemo(() => {
    const counts = new Map<string, { joins: number; completes: number; first: number; last: number }>();
    for (const event of events) {
      if (event.type !== "collective") continue;
      const row = counts.get(event.operation) ?? {
        joins: 0,
        completes: 0,
        first: event.at,
        last: event.at,
      };
      if (event.phase === "join") row.joins += 1;
      else row.completes += 1;
      row.first = Math.min(row.first, event.at);
      row.last = Math.max(row.last, event.at);
      counts.set(event.operation, row);
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [events]);
  if (!rows.length) return null;
  return (
    <section className="panel">
      <h2>Collective activity</h2>
      <table>
        <thead>
          <tr>
            <th>operation</th>
            <th>joins</th>
            <th>completions</th>
            <th>observed window</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([operation, row]) => (
            <tr key={operation}>
              <td className="mono">{operation}</td>
              <td>{row.joins}</td>
              <td>{row.completes}</td>
              <td>{formatDuration(row.last - row.first)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Report({ report }: { report: Record<string, unknown> | null }) {
  if (!report) return null;
  const fields = ["experiment", "arm", "device", "succeeded", "failed", "wall_s", "context_peak"];
  const rows = fields.filter((field) => report[field] !== undefined);
  if (!rows.length) return null;
  return (
    <section className="panel">
      <h2>Harness report</h2>
      <div className="report-grid">
        {rows.map((field) => (
          <div key={field}>
            <span>{field.replaceAll("_", " ")}</span>
            <strong>{String(report[field])}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function Tooltip({ event }: { event: VisualEvent }) {
  let detail = "";
  switch (event.type) {
    case "work":
      detail = `${formatDuration(event.end - event.at)} · ${event.tokens} output tokens · ${event.outcome}`;
      break;
    case "message":
      detail = `${event.direction} ${event.peer === null ? "" : `rank ${event.peer}`} · ${event.tokens} tokens`;
      break;
    case "collective":
      detail = `${event.operation} · ${event.phase}`;
      break;
    case "lifecycle":
      detail = event.action;
      break;
    case "fault":
      detail = event.action;
      break;
    case "rma":
      detail = `${event.action}${event.stale ? " · stale overwrite" : ""}`;
      break;
    case "lock":
      detail = event.action;
      break;
    default: {
      const exhaustive: never = event;
      return exhaustive;
    }
  }
  return (
    <div className="tooltip">
      <strong>{event.label}</strong>
      <span>
        rank {event.rank} · {formatDuration(event.at)}
      </span>
      <span>{detail}</span>
    </div>
  );
}

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty">
      <h1>{title}</h1>
      <p>{body}</p>
    </div>
  );
}

function niceStep(duration: number): number {
  const raw = Math.max(duration, 0.001) / 8;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  for (const multiplier of [1, 2, 5, 10]) {
    if (raw <= multiplier * magnitude) return multiplier * magnitude;
  }
  return 10 * magnitude;
}

function formatDuration(seconds: number): string {
  if (seconds >= 60) return `${(seconds / 60).toFixed(1)}m`;
  if (seconds < 1) return `${seconds.toFixed(2)}s`;
  return `${seconds.toFixed(1)}s`;
}
