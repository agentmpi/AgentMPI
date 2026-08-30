/**
 * The AgentMPI trace data model.
 *
 * Deliberately the same event vocabulary MPI tools have used for twenty
 * years -- states with a duration, arrows pairing a send with its receive,
 * counters -- because those are what make a timeline, a communication matrix
 * and a critical path computable. The one addition is that every event
 * carries a token count, since tokens are the resource being profiled.
 */

export type EventKind =
  | "enter"
  | "leave"
  | "send"
  | "recv"
  | "coll"
  | "state"
  | "counter"
  | "note";

export interface TraceEvent {
  kind: EventKind;
  ts: number;
  rank: number;
  op?: string;
  context?: string;
  peer?: number;
  tag?: number;
  tokens?: number;
  bytes?: number;
  dur?: number;
  seq?: number;
  idem?: string;
  algorithm?: string;
  turn?: number;
  state?: string;
  detail?: Record<string, unknown>;
}

export interface Arrow {
  src: number;
  dst: number;
  tSend: number;
  tRecv: number;
  tokens: number;
  op: string;
}

export interface Span {
  rank: number;
  op: string;
  start: number;
  end: number;
  algorithm?: string;
  tokens: number;
  steps?: number;
}

export interface RunSummary {
  ranks: number;
  events: number;
  wallSeconds: number;
  messages: number;
  tokensSent: number;
  tokensReceived: number;
  collectives: number;
  collectiveSeconds: number;
  blockedSeconds: number;
  t0: number;
}

export interface Trace {
  events: TraceEvent[];
  arrows: Arrow[];
  spans: Span[];
  matrix: number[][];
  summary: RunSummary;
  ranks: number[];
  byOp: { op: string; calls: number; seconds: number; tokens: number }[];
  pressure: { rank: number; points: { t: number; tokens: number }[] }[];
  notes: TraceEvent[];
}

export function parseTrace(lines: string): Trace {
  const events: TraceEvent[] = [];
  for (const line of lines.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      events.push(JSON.parse(trimmed) as TraceEvent);
    } catch {
      /* a torn final record after a crash is expected, not exceptional */
    }
  }
  return buildTrace(events);
}

export function buildTrace(events: TraceEvent[]): Trace {
  const ranks = [...new Set(events.map((e) => e.rank))].sort((a, b) => a - b);
  const t0 = events.length ? Math.min(...events.map((e) => e.ts)) : 0;
  const t1 = events.length ? Math.max(...events.map((e) => e.ts)) : 0;

  // Arrows: pair each send with the receive that consumed it.
  const sends = new Map<string, TraceEvent>();
  for (const e of events) {
    if (e.kind === "send" && e.idem) sends.set(e.idem, e);
  }
  const arrows: Arrow[] = [];
  for (const e of events) {
    if (e.kind !== "recv" || !e.idem) continue;
    const s = sends.get(e.idem);
    if (!s) continue;
    arrows.push({
      src: s.rank,
      dst: e.rank,
      tSend: s.ts - t0,
      tRecv: e.ts - t0,
      tokens: e.tokens ?? 0,
      op: s.op ?? "send",
    });
  }

  // Spans: collectives, plus any explicitly instrumented region.
  const spans: Span[] = [];
  for (const e of events) {
    if (e.kind === "coll" || (e.kind === "leave" && e.dur)) {
      const dur = e.dur ?? 0;
      spans.push({
        rank: e.rank,
        op: e.op ?? "?",
        start: e.ts - t0 - dur,
        end: e.ts - t0,
        algorithm: e.algorithm,
        tokens: e.tokens ?? 0,
        steps: (e.detail?.steps as number) ?? undefined,
      });
    }
  }

  const n = ranks.length ? Math.max(...ranks) + 1 : 0;
  const matrix: number[][] = Array.from({ length: n }, () =>
    Array.from({ length: n }, () => 0),
  );
  for (const a of arrows) {
    if (a.src < n && a.dst < n) matrix[a.src][a.dst] += a.tokens;
  }

  const opAgg = new Map<string, { calls: number; seconds: number; tokens: number }>();
  for (const s of spans) {
    const rec = opAgg.get(s.op) ?? { calls: 0, seconds: 0, tokens: 0 };
    rec.calls += 1;
    rec.seconds += s.end - s.start;
    rec.tokens += s.tokens;
    opAgg.set(s.op, rec);
  }

  // Cumulative context pressure: the defining resource curve of an agent
  // job, and the one no conventional profiler plots.
  const pressure = ranks.map((rank) => {
    let acc = 0;
    const points: { t: number; tokens: number }[] = [{ t: 0, tokens: 0 }];
    for (const e of events) {
      if (e.rank !== rank || e.kind !== "recv") continue;
      acc += e.tokens ?? 0;
      points.push({ t: e.ts - t0, tokens: acc });
    }
    points.push({ t: t1 - t0, tokens: acc });
    return { rank, points };
  });

  const collSpans = spans.filter((s) => s.op !== "turn");
  return {
    events,
    arrows,
    spans,
    matrix,
    ranks,
    notes: events.filter((e) => e.kind === "note"),
    byOp: [...opAgg.entries()]
      .map(([op, v]) => ({ op, ...v }))
      .sort((a, b) => b.seconds - a.seconds),
    pressure,
    summary: {
      ranks: ranks.length,
      events: events.length,
      wallSeconds: t1 - t0,
      messages: arrows.length,
      tokensSent: events
        .filter((e) => e.kind === "send")
        .reduce((s, e) => s + (e.tokens ?? 0), 0),
      tokensReceived: events
        .filter((e) => e.kind === "recv")
        .reduce((s, e) => s + (e.tokens ?? 0), 0),
      collectives: collSpans.length,
      collectiveSeconds: collSpans.reduce((s, x) => s + (x.end - x.start), 0),
      blockedSeconds: arrows.reduce((s, a) => s + (a.tRecv - a.tSend), 0),
      t0,
    },
  };
}

export const OP_COLOURS: Record<string, string> = {
  bcast: "#58a6ff",
  scatter: "#7ee787",
  scatterv: "#7ee787",
  gather: "#ffa657",
  allgather: "#ffa657",
  reduce: "#d2a8ff",
  allreduce: "#d2a8ff",
  scan: "#f778ba",
  exscan: "#f778ba",
  barrier: "#8b97a8",
  alltoall: "#79c0ff",
  turn: "#3fb950",
};

export function colourFor(op: string): string {
  return OP_COLOURS[op] ?? "#6e7681";
}
