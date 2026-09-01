export interface TraceEvent {
  kind: string;
  rank: number;
  seq: number;
  ts: number;
  comm?: string | null;
  run?: string;
  [field: string]: unknown;
}

export interface WorkSpan {
  aid: string;
  rank: number;
  label: string;
  start: number;
  end: number;
  outcome: "submit" | "giveup" | "reject";
  tokens: number;
}

interface VisualBase {
  rank: number;
  at: number;
  label: string;
  source?: TraceEvent;
}

export interface WorkEvent extends VisualBase {
  type: "work";
  end: number;
  outcome: WorkSpan["outcome"];
  tokens: number;
}

export interface MessageEvent extends VisualBase {
  type: "message";
  direction: "send" | "recv";
  peer: number | null;
  tokens: number;
}

export interface CollectiveEvent extends VisualBase {
  type: "collective";
  operation: string;
  phase: "join" | "complete";
}

export interface LifecycleEvent extends VisualBase {
  type: "lifecycle";
  action: string;
}

export interface FaultEvent extends VisualBase {
  type: "fault";
  action: string;
}

export interface RmaEvent extends VisualBase {
  type: "rma";
  action: string;
  stale: boolean;
}

export interface LockEvent extends VisualBase {
  type: "lock";
  action: "lock" | "unlock";
}

/** Closed discriminated union used by every timeline rendering switch. */
export type VisualEvent =
  | WorkEvent
  | MessageEvent
  | CollectiveEvent
  | LifecycleEvent
  | FaultEvent
  | RmaEvent
  | LockEvent;

export type EventType = VisualEvent["type"];

export interface ConcurrencySummary {
  peak: number;
  average: number;
  busy_rank_seconds: number;
  utilization: number;
}

export interface RunDetail {
  name: string;
  events: TraceEvent[];
  report: Record<string, unknown> | null;
  ranks: number[];
  world_size: number;
  started_at: number;
  ended_at: number;
  duration_s: number;
  work_spans: WorkSpan[];
  concurrency: ConcurrencySummary;
}

export interface RunListItem {
  name: string;
  n_events: number;
  n_ranks: number;
  world_size: number;
  job_id: string;
  duration_s: number;
  trace_bytes: number;
  has_report: boolean;
}

export interface EventStyle {
  color: string;
  glyph: "bar" | "tick" | "circle" | "triangle" | "diamond" | "cross" | "lock";
}

/** Workspace policy: additions to VisualEvent must fail compilation here. */
export function styleFor(event: VisualEvent): EventStyle {
  switch (event.type) {
    case "work":
      return { color: event.outcome === "submit" ? "#4f8cff" : "#ff5e6c", glyph: "bar" };
    case "message":
      return {
        color: event.direction === "send" ? "#35d39a" : "#69e5b8",
        glyph: "tick",
      };
    case "collective":
      return {
        color: event.phase === "join" ? "#c084fc" : "#e9b5ff",
        glyph: event.phase === "join" ? "triangle" : "circle",
      };
    case "lifecycle":
      return { color: "#8ba3c7", glyph: "circle" };
    case "fault":
      return { color: "#ff5e6c", glyph: "cross" };
    case "rma":
      return { color: event.stale ? "#ff5e6c" : "#f4b942", glyph: "diamond" };
    case "lock":
      return { color: "#ff8c42", glyph: "lock" };
    default: {
      const exhaustive: never = event;
      return exhaustive;
    }
  }
}

export const EVENT_TYPES: EventType[] = [
  "work",
  "message",
  "collective",
  "lifecycle",
  "fault",
  "rma",
  "lock",
];
