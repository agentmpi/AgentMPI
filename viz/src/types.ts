/**
 * Shapes served by scripts/trace_server.py.
 *
 * `EventKind` is a closed union rather than `string` on purpose: the timeline
 * assigns a colour and a shape per kind, and a new event kind in the runtime must
 * force a compile error here instead of silently rendering as an untyped blob.
 */

export type EventKind =
  | "work"
  | "agent.call"
  | "msg.send"
  | "msg.recv"
  | "win.put"
  | "win.get"
  | "win.lock"
  | "win.accumulate";

export interface Span {
  kind: EventKind;
  start: number;
  end: number;
  label: string;
  tokens: number;
  peer?: number | null;
  mode?: string | null;
  stale?: boolean;
  aid?: number;
}

export interface CollectiveEvent {
  kind: string;
  rank: number;
  t: number;
  algorithm?: string | null;
  rounds?: number | null;
  messages_sent?: number | null;
  tokens_sent?: number | null;
  fold_depth?: number | null;
  wall_s?: number | null;
  op?: string | null;
}

export interface RunSummary {
  wall_s: number;
  agent_calls: number;
  tokens_in: number;
  tokens_out: number;
  usd: number;
  messages: number;
  tokens_sent: number;
  tokens_deferred: number;
  agent_latency_p50: number;
  agent_latency_p95: number;
  agent_latency_max: number;
  context_high_water: number;
  context_rejections: number;
  contract_violations: number;
  failures: number;
  collectives: Record<string, unknown>;
}

export interface Calibration {
  alpha_s: number;
  alpha_p50: number;
  alpha_p99: number;
  tokens_per_s: number | null;
  alpha_beta_crossover_tokens: number | null;
  fabric_s: number;
  n_samples: number;
}

export interface RankHealth {
  rank: number;
  state: string;
  alive: boolean;
  calls: number;
  occupancy: number;
  suspected: string | null;
}

export interface RunDetail {
  name: string;
  truncated?: boolean;
  job_id: string;
  run_id?: string;
  experiment: string;
  t_span: number;
  n_events: number;
  lanes: Record<string, Span[]>;
  collectives: CollectiveEvent[];
  summary: RunSummary;
  calibration: Calibration;
  health: RankHealth[];
}

export interface RunListItem {
  name: string;
  path: string;
  job_id?: string;
  run_id?: string;
  label?: string;
  experiment?: string;
  world_size?: number;
  n_events?: number;
  n_ranks?: number;
  error?: string;
}

/** Colour and glyph per event kind. Exhaustive by construction. */
export function styleFor(kind: EventKind): { color: string; glyph: "bar" | "tick" | "diamond" } {
  switch (kind) {
    case "work":
      return { color: "#3b82f6", glyph: "bar" };
    case "agent.call":
      return { color: "#2563eb", glyph: "bar" };
    case "msg.send":
      return { color: "#10b981", glyph: "tick" };
    case "msg.recv":
      return { color: "#34d399", glyph: "tick" };
    case "win.put":
      return { color: "#f59e0b", glyph: "diamond" };
    case "win.get":
      return { color: "#fbbf24", glyph: "diamond" };
    case "win.lock":
      return { color: "#ef4444", glyph: "diamond" };
    case "win.accumulate":
      return { color: "#a78bfa", glyph: "diamond" };
    default: {
      // Exhaustiveness check: a new EventKind must be handled above.
      const _never: never = kind;
      return _never;
    }
  }
}
