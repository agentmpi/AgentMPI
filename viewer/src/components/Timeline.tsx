import { useMemo, useRef, useState } from "react";
import type { Collective, Trace, TraceEvent } from "../types";

/**
 * A rank-per-row timeline in the tradition of Jumpshot and Vampir.
 *
 * The design choices that matter for an *agent* trace rather than an MPI trace:
 *
 * - Message arrows are drawn from send time to delivery time, and their opacity
 *   encodes payload size in tokens. Long shallow arrows are exactly the picture
 *   of a rank that sent something nobody read for minutes.
 * - Collective participation is drawn as a per-rank band from join to done, so a
 *   straggler in a barrier is visible as one long band beside many short ones.
 *   This is the single most useful view in practice, because heavy-tailed agent
 *   latency means barriers, not bandwidth, dominate.
 * - Failures and respawns are marked inline, so a recovery episode reads as a
 *   story rather than as a gap.
 */

const KIND_COLOR: Record<string, string> = {
  init: "#7ee0b8",
  finalize: "#8a99ad",
  send: "#58c4ff",
  recv: "#3f9ad8",
  recv_post: "#2b5e80",
  coll_join: "#b58cff",
  coll_exit: "#8f6be0",
  coll_fold: "#d7c4ff",
  reduce_step_issue: "#ffc16b",
  reduce_step_commit: "#ffdca8",
  win_put: "#6be3d6",
  win_get: "#3fada3",
  win_acc: "#a8f0e8",
  win_lock: "#ff9de0",
  win_unlock: "#c76fa8",
  win_fence: "#e0f",
  failure: "#ff7a7a",
  respawn: "#ffb347",
  comm_revoke: "#ff5c5c",
  comm_shrink: "#ffd166",
  comm_agree: "#c9d66b",
  failure_ack: "#9aa66b",
  win_cas: "#7be3a1",
  job_start: "#dbe4f0",
};

const ROW_H = 22;
const PAD_L = 58;
const PAD_T = 34;
const PAD_B = 26;

type Props = {
  trace: Trace;
  onPickMessage: (seq: number) => void;
  onPickCollective: (coll: Collective) => void;
};

type Hover = { x: number; y: number; text: string } | null;

export default function Timeline({ trace, onPickMessage, onPickCollective }: Props) {
  const [zoom, setZoom] = useState<[number, number] | null>(null);
  const [showArrows, setShowArrows] = useState(true);
  const [showColl, setShowColl] = useState(true);
  const [hover, setHover] = useState<Hover>(null);
  const dragRef = useRef<{ x0: number; x1: number } | null>(null);
  const [drag, setDrag] = useState<{ x0: number; x1: number } | null>(null);

  const ranks = trace.ranks;
  const width = Math.max(900, Math.min(2400, 900 + ranks.length * 4));
  const height = PAD_T + ranks.length * ROW_H + PAD_B;
  const plotW = width - PAD_L - 16;

  const [t0, t1] = zoom ?? [0, Math.max(0.001, trace.duration_s)];
  const span = Math.max(1e-6, t1 - t0);
  const x = (t: number) => PAD_L + ((t - t0) / span) * plotW;
  const rowOf = useMemo(() => {
    const m = new Map<number, number>();
    ranks.forEach((r, i) => m.set(r.rank, i));
    return m;
  }, [ranks]);
  const y = (rank: number) => PAD_T + (rowOf.get(rank) ?? 0) * ROW_H + ROW_H / 2;

  const visible = (t: number) => t >= t0 - span * 0.02 && t <= t1 + span * 0.02;

  const ticks = useMemo(() => {
    const target = 8;
    const raw = span / target;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
    const out: number[] = [];
    for (let v = Math.ceil(t0 / step) * step; v <= t1; v += step) out.push(v);
    return out;
  }, [t0, t1, span]);

  const events = trace.events.filter((e) => e.rank !== null && visible(e.t));
  const arrows = trace.messages.filter((m) => visible(m.t_send));

  const fmt = (t: number) => (span < 2 ? `${(t * 1000).toFixed(0)}ms` : `${t.toFixed(span < 20 ? 2 : 1)}s`);

  const onDown = (ev: React.MouseEvent<SVGSVGElement>) => {
    const rect = ev.currentTarget.getBoundingClientRect();
    const px = ev.clientX - rect.left;
    if (px < PAD_L) return;
    dragRef.current = { x0: px, x1: px };
    setDrag({ x0: px, x1: px });
  };
  const onMove = (ev: React.MouseEvent<SVGSVGElement>) => {
    if (!dragRef.current) return;
    const rect = ev.currentTarget.getBoundingClientRect();
    dragRef.current.x1 = ev.clientX - rect.left;
    setDrag({ ...dragRef.current });
  };
  const onUp = () => {
    const d = dragRef.current;
    dragRef.current = null;
    setDrag(null);
    if (!d) return;
    const lo = Math.min(d.x0, d.x1);
    const hi = Math.max(d.x0, d.x1);
    if (hi - lo < 12) return;
    const toT = (px: number) => t0 + ((px - PAD_L) / plotW) * span;
    setZoom([Math.max(0, toT(lo)), Math.min(trace.duration_s, toT(hi))]);
  };

  const describeEvent = (e: TraceEvent) => {
    const bits = [`t=${fmt(e.t)}  rank ${e.rank}  ${e.kind}`];
    if (e.peer !== null) bits.push(`peer=${e.peer}`);
    if (e.tag !== null && e.tag !== undefined) bits.push(`tag=${e.tag}`);
    if (e.tokens) bits.push(`tokens=${e.tokens}`);
    if (e.dur) bits.push(`dur=${fmt(e.dur)}`);
    if (e.status) bits.push(`status=${e.status}`);
    if (e.key) bits.push(`key=${e.key}`);
    if (e.detail) bits.push(JSON.stringify(e.detail));
    return bits.join("\n");
  };

  return (
    <div>
      <div className="tl-toolbar">
        <button onClick={() => setZoom(null)} disabled={!zoom}>
          reset zoom
        </button>
        <button data-active={showArrows} onClick={() => setShowArrows((v) => !v)}>
          message arrows
        </button>
        <button data-active={showColl} onClick={() => setShowColl((v) => !v)}>
          collective bands
        </button>
        <span>
          drag to zoom · window {fmt(t0)}–{fmt(t1)} of {fmt(trace.duration_s)} · {events.length} events ·{" "}
          {arrows.length} messages
        </span>
      </div>

      <div className="tl-wrap">
        <svg
          width={width}
          height={height}
          onMouseDown={onDown}
          onMouseMove={onMove}
          onMouseUp={onUp}
          onMouseLeave={() => {
            onUp();
            setHover(null);
          }}
          style={{ display: "block", cursor: "crosshair" }}
        >
          {ticks.map((t) => (
            <g key={`tick-${t}`}>
              <line x1={x(t)} x2={x(t)} y1={PAD_T - 8} y2={height - PAD_B + 4} stroke="#1e2836" />
              <text x={x(t)} y={PAD_T - 13} fill="#5b6879" fontSize={10} textAnchor="middle">
                {fmt(t)}
              </text>
            </g>
          ))}

          {ranks.map((r, i) => (
            <g key={`row-${r.rank}`}>
              <rect
                x={PAD_L}
                y={PAD_T + i * ROW_H}
                width={plotW}
                height={ROW_H}
                fill={i % 2 ? "#0d1218" : "transparent"}
              />
              <text x={PAD_L - 8} y={y(r.rank) + 3.5} fill="#8a99ad" fontSize={10.5} textAnchor="end">
                r{r.rank}
                {r.epoch > 0 ? `.e${r.epoch}` : ""}
              </text>
              {r.t_init !== null && (
                <line
                  x1={x(r.t_init)}
                  x2={x(r.t_fini ?? trace.duration_s)}
                  y1={y(r.rank)}
                  y2={y(r.rank)}
                  stroke={r.state === "failed" ? "#4a2530" : "#1f2b3a"}
                  strokeWidth={9}
                  strokeLinecap="round"
                />
              )}
            </g>
          ))}

          {showColl &&
            trace.collectives.flatMap((c) =>
              c.participants
                .filter((p) => visible(p.t_join))
                .map((p) => {
                  const x1 = x(p.t_join);
                  const x2 = x(p.t_done ?? c.t_end ?? trace.duration_s);
                  const w = Math.max(1.5, x2 - x1);
                  return (
                    <rect
                      key={`cp-${c.id}-${p.rank}`}
                      x={x1}
                      y={y(p.rank) - 7}
                      width={w}
                      height={14}
                      rx={3}
                      fill="#b58cff"
                      opacity={p.state === "late" ? 0.14 : 0.2}
                      stroke="#b58cff55"
                      onClick={() => onPickCollective(c)}
                      onMouseEnter={(ev) =>
                        setHover({
                          x: ev.clientX,
                          y: ev.clientY,
                          text: `${c.op}${c.reduce_op ? `(${c.reduce_op})` : ""} algo=${c.algo}\nlabel=${
                            c.label ?? "-"
                          }\nrank ${p.rank}: ${p.state}, ${fmt(
                            (p.t_done ?? c.t_end ?? trace.duration_s) - p.t_join,
                          )} inside\nparticipants=${c.nparts}`,
                        })
                      }
                      onMouseLeave={() => setHover(null)}
                      style={{ cursor: "pointer" }}
                    />
                  );
                }),
            )}

          {showArrows &&
            arrows.map((m) => {
              if (!rowOf.has(m.src) || !rowOf.has(m.dst)) return null;
              const x1 = x(m.t_send);
              const x2 = x(m.t_recv ?? t1);
              const y1 = y(m.src);
              const y2 = y(m.dst);
              const op = Math.min(0.8, 0.14 + Math.log10(1 + m.tokens) / 6);
              return (
                <line
                  key={`m-${m.seq}`}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={m.coll ? "#8f6be0" : m.t_recv === null ? "#ff7a7a" : "#58c4ff"}
                  strokeWidth={1.1}
                  opacity={op}
                  strokeDasharray={m.t_recv === null ? "3 3" : undefined}
                  onClick={() => onPickMessage(m.seq)}
                  onMouseEnter={(ev) =>
                    setHover({
                      x: ev.clientX,
                      y: ev.clientY,
                      text: `msg #${m.seq}  ${m.src} -> ${m.dst}  tag=${m.tag}\n${m.tokens} tokens, ${
                        m.mode
                      }\nsent ${fmt(m.t_send)}${
                        m.t_recv === null ? "\nNEVER DELIVERED" : `, delivered ${fmt(m.t_recv)} (+${fmt(
                            m.t_recv - m.t_send,
                          )})`
                      }\nclick to read the payload`,
                    })
                  }
                  onMouseLeave={() => setHover(null)}
                  style={{ cursor: "pointer" }}
                />
              );
            })}

          {events.map((e) => {
            const color = KIND_COLOR[e.kind] ?? "#8a99ad";
            const cx = x(e.t);
            const cy = y(e.rank as number);
            const big = e.kind === "failure" || e.kind === "respawn" || e.kind === "comm_revoke";
            return big ? (
              <path
                key={`e-${e.id}`}
                d={`M${cx - 4},${cy - 4}L${cx + 4},${cy + 4}M${cx + 4},${cy - 4}L${cx - 4},${cy + 4}`}
                stroke={color}
                strokeWidth={1.8}
                onMouseEnter={(ev) => setHover({ x: ev.clientX, y: ev.clientY, text: describeEvent(e) })}
                onMouseLeave={() => setHover(null)}
              />
            ) : (
              <circle
                key={`e-${e.id}`}
                cx={cx}
                cy={cy}
                r={2.2}
                fill={color}
                onMouseEnter={(ev) => setHover({ x: ev.clientX, y: ev.clientY, text: describeEvent(e) })}
                onMouseLeave={() => setHover(null)}
              />
            );
          })}

          {drag && Math.abs(drag.x1 - drag.x0) > 3 && (
            <rect
              x={Math.min(drag.x0, drag.x1)}
              y={PAD_T - 10}
              width={Math.abs(drag.x1 - drag.x0)}
              height={height - PAD_T - PAD_B + 14}
              fill="#58c4ff18"
              stroke="#58c4ff"
              strokeDasharray="3 3"
            />
          )}
        </svg>
      </div>

      <div className="tl-legend">
        {[
          ["init / finalize", KIND_COLOR.init],
          ["send / recv", KIND_COLOR.send],
          ["collective", KIND_COLOR.coll_join],
          ["agent merge step", KIND_COLOR.reduce_step_issue],
          ["window put / get / acc", KIND_COLOR.win_put],
          ["lock", KIND_COLOR.win_lock],
          ["failure / revoke / respawn", KIND_COLOR.failure],
        ].map(([label, c]) => (
          <span key={label}>
            <i className="swatch" style={{ background: c }} />
            {label}
          </span>
        ))}
        <span>dashed red arrow = message never delivered</span>
      </div>

      {hover && (
        <div className="tooltip" style={{ left: Math.min(hover.x + 12, window.innerWidth - 380), top: hover.y + 12 }}>
          {hover.text}
        </div>
      )}
    </div>
  );
}
