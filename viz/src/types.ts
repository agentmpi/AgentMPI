/**
 * Shapes served by scripts/trace_server.py.
 *
 * `EventKind` is a closed union rather than `string` on purpose: the timeline
 * assigns a colour and a shape per kind, and a new event kind in the runtime must
 * force a compile error here instead of silently rendering as an untyped blob.
 */

export type EventKind =
  // Occupancy: a rank actually doing something, drawn as a bar with real duration.
  | "work"
  | "agent.call"
  // Communication.
  | "msg.send"
  | "msg.recv"
  | "msg.fetch"
  // One-sided operations on a window.
  | "win.put"
  | "win.get"
  | "win.accumulate"
  | "win.cas"
  | "win.lock"
  | "win.unlock"
  | "win.sync"
  | "win.flush"
  // Lifecycle: when a rank joined, left, compacted, or was restarted.
  | "rank.init"
  | "rank.finalize"
  | "rank.compact"
  | "proc.spawn"
  | "sup.restart"
  // Trouble. Drawn in red, because these are what a reader is hunting for.
  | "rank.error"
  | "rank.stuck"
  | "rank.version_mismatch"
  | "barrier.timeout"
  | "win.lock_timeout"
  | "broker.expire"
  | "broker.fail"
  | "broker.reclaim"
  | "agent.contract_violation"
  | "transport.credit_stall"
  | "harness.degraded"
  | "ft.declare_failed"
  | "ft.agree_timeout"
  // The harness responding to trouble.
  | "ft.agree"
  | "ft.revoke"
  | "ft.shrink"
  | "ft.shrink_in_place"
  | "ft.failure_ack"
  | "sup.escalate"
  | "transport.credit_granted";

export interface Span {
  kind: EventKind;
  start: number;
  end: number;
  label: string;
  /** Compact `key=value` summary of the salient payload fields, for the tooltip. */
  detail?: string;
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
  label?: string;
  experiment?: string;
  world_size?: number;
  n_events?: number;
  n_ranks?: number;
  error?: string;
}

/** What class of thing an event is. Drives colour, and groups the legend. */
export type Role = "work" | "message" | "rma" | "lifecycle" | "trouble" | "recovery";

export const ROLE_LABEL: Record<Role, string> = {
  work: "agent working",
  message: "messages",
  rma: "one-sided window ops",
  lifecycle: "rank lifecycle",
  trouble: "failure / timeout",
  recovery: "fault recovery",
};

export const ROLE_COLOR: Record<Role, string> = {
  work: "#3b82f6",
  message: "#10b981",
  rma: "#f59e0b",
  lifecycle: "#64748b",
  trouble: "#ef4444",
  recovery: "#a78bfa",
};

/** How long the thing took: a bar has real extent, a tick and a diamond are instants. */
export type Glyph = "bar" | "tick" | "diamond";

/**
 * The one place the event taxonomy is written down: colour, glyph, and role per kind.
 *
 * Declared as `Record<EventKind, ...>` rather than built from a switch, because that makes it
 * exhaustive *by construction* --- adding a kind to `EventKind` without describing it here is
 * a compile error, and there is no default branch that could silently absorb it. Both the
 * timeline and the legend read from this table, so they cannot disagree about what a colour
 * means.
 *
 * Glyph encodes duration and colour encodes role, which keeps the two independent: trouble is
 * red wherever it appears, and a reader scanning a lane dense with green message ticks still
 * picks out a red diamond immediately.
 */
export const STYLE: Record<EventKind, { color: string; glyph: Glyph; role: Role }> = {
  work: { color: ROLE_COLOR.work, glyph: "bar", role: "work" },
  "agent.call": { color: "#2563eb", glyph: "bar", role: "work" },

  "msg.send": { color: ROLE_COLOR.message, glyph: "tick", role: "message" },
  "msg.recv": { color: "#34d399", glyph: "tick", role: "message" },
  "msg.fetch": { color: "#6ee7b7", glyph: "tick", role: "message" },

  "win.put": { color: ROLE_COLOR.rma, glyph: "diamond", role: "rma" },
  "win.get": { color: "#fbbf24", glyph: "diamond", role: "rma" },
  "win.accumulate": { color: "#a78bfa", glyph: "diamond", role: "rma" },
  "win.cas": { color: "#c4b5fd", glyph: "diamond", role: "rma" },
  "win.lock": { color: "#fb923c", glyph: "diamond", role: "rma" },
  "win.unlock": { color: "#fdba74", glyph: "diamond", role: "rma" },
  "win.sync": { color: "#fcd34d", glyph: "tick", role: "rma" },
  "win.flush": { color: "#fcd34d", glyph: "tick", role: "rma" },

  "rank.init": { color: ROLE_COLOR.lifecycle, glyph: "tick", role: "lifecycle" },
  "rank.finalize": { color: ROLE_COLOR.lifecycle, glyph: "tick", role: "lifecycle" },
  "rank.compact": { color: "#94a3b8", glyph: "diamond", role: "lifecycle" },
  "proc.spawn": { color: "#38bdf8", glyph: "diamond", role: "lifecycle" },
  "sup.restart": { color: "#38bdf8", glyph: "diamond", role: "lifecycle" },

  "rank.error": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "rank.stuck": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "rank.version_mismatch": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "barrier.timeout": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "win.lock_timeout": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "broker.expire": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "broker.fail": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "broker.reclaim": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "agent.contract_violation": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "transport.credit_stall": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "harness.degraded": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "ft.declare_failed": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },
  "ft.agree_timeout": { color: ROLE_COLOR.trouble, glyph: "diamond", role: "trouble" },

  "ft.agree": { color: ROLE_COLOR.recovery, glyph: "diamond", role: "recovery" },
  "ft.revoke": { color: ROLE_COLOR.recovery, glyph: "diamond", role: "recovery" },
  "ft.shrink": { color: ROLE_COLOR.recovery, glyph: "diamond", role: "recovery" },
  "ft.shrink_in_place": { color: ROLE_COLOR.recovery, glyph: "diamond", role: "recovery" },
  "ft.failure_ack": { color: ROLE_COLOR.recovery, glyph: "diamond", role: "recovery" },
  "sup.escalate": { color: ROLE_COLOR.recovery, glyph: "diamond", role: "recovery" },
  "transport.credit_granted": { color: ROLE_COLOR.recovery, glyph: "diamond", role: "recovery" },
};

/** Fallback keeps an unrecognised kind visible rather than dropping it silently. */
const UNKNOWN = { color: "#64748b", glyph: "tick" as Glyph, role: "lifecycle" as Role };

export function styleFor(kind: EventKind): { color: string; glyph: Glyph; role: Role } {
  return STYLE[kind] ?? UNKNOWN;
}
