/** Shapes returned by the `ampi serve` API. Mirrors ampi/trace.py::export. */

export type Dist = {
  n: number;
  mean: number | null;
  p50: number | null;
  p90: number | null;
  p99: number | null;
  max: number | null;
  min: number | null;
  sum: number;
  cv?: number;
};

export type RunInfo = {
  name: string;
  path: string;
  job?: string;
  label?: string | null;
  world_size?: number;
  wall_s?: number;
  messages?: number;
  failures?: number;
  context_hwm?: number | null;
  error?: string;
};

export type TraceEvent = {
  id: number;
  t: number;
  dur: number;
  rank: number | null;
  epoch: number | null;
  kind: string;
  phase: string | null;
  comm: string | null;
  peer: number | null;
  tag: number | null;
  coll: string | null;
  win: string | null;
  key: string | null;
  msg: number | null;
  tokens: number;
  bytes: number;
  status: string | null;
  detail: Record<string, unknown> | null;
};

export type MessageArrow = {
  seq: number;
  comm: string;
  src: number;
  dst: number;
  tag: number;
  tokens: number;
  mode: string;
  t_send: number;
  t_recv: number | null;
  status: string;
  coll: string | null;
};

export type CollParticipant = {
  rank: number;
  state: string;
  t_join: number;
  t_done: number | null;
  rounds: number;
};

export type Collective = {
  id: string;
  comm: string;
  op: string;
  reduce_op: string | null;
  algo: string;
  root: number | null;
  label: string | null;
  state: string;
  t_start: number;
  t_end: number | null;
  nparts: number;
  participants: CollParticipant[];
};

export type RankInfo = {
  rank: number;
  epoch: number;
  state: string;
  role: string | null;
  ctx_used: number;
  ctx_budget: number;
  ctx_hwm: number;
  calls: number;
  t_init: number | null;
  t_fini: number | null;
};

export type Failure = {
  rank: number;
  epoch: number;
  kind: string;
  t: number;
  detail: Record<string, unknown>;
};

export type WindowCell = {
  key: string;
  version: number;
  tokens: number;
  writer: number | null;
  t: number;
};

export type WindowInfo = { id: string; name: string; cells: WindowCell[] };

export type Summary = {
  world_size: number;
  wall_s: number;
  counters: Record<string, number>;
  messages: {
    count: number;
    p2p: number;
    coll: number;
    undelivered: number;
    payload_tokens: number;
    latency_s: Dist;
  };
  context: {
    per_rank_hwm: Record<string, number>;
    hwm: Dist;
    total_delivered_tokens: number;
    budget: number;
    over_budget_ranks: number[];
  };
  calls: Dist;
  collectives: Record<string, Dist>;
  agent_merge_s: Dist;
  wait_s: Dist;
  failures: number;
  rma: { puts: number; gets: number; accumulates: number; cells: number };
};

export type Trace = {
  job: string;
  label: string | null;
  world_size: number;
  t0_ns: number;
  duration_s: number;
  config: Record<string, number>;
  ranks: RankInfo[];
  events: TraceEvent[];
  messages: MessageArrow[];
  collectives: Collective[];
  failures: Failure[];
  windows: WindowInfo[];
  summary: Summary;
};
