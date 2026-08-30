import { useMemo, useState } from "react";
import { colourFor, type Trace } from "./trace";

export function Panel({
  title,
  subtitle,
  children,
  className = "",
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-edge bg-panel p-4 ${className}`}
    >
      <header className="mb-3">
        <h2 className="text-sm font-semibold tracking-wide text-slate-100">
          {title}
        </h2>
        {subtitle && (
          <p className="mt-0.5 text-xs leading-relaxed text-muted">{subtitle}</p>
        )}
      </header>
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-edge bg-panel px-3 py-2.5">
      <div className="text-[10px] uppercase tracking-wider text-muted">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-lg leading-tight text-slate-100">
        {value}
      </div>
      {hint && <div className="mt-0.5 text-[10px] text-muted">{hint}</div>}
    </div>
  );
}

/**
 * The Gantt view: one lane per rank, one bar per operation, arrows for
 * matched messages. This is the view MPI tooling has offered since
 * Jumpshot, and it answers the same question -- who was waiting for whom.
 */
export function Timeline({ trace }: { trace: Trace }) {
  const [hover, setHover] = useState<string | null>(null);
  const { spans, arrows, ranks, summary } = trace;
  const laneH = 22;
  const width = 1000;
  const left = 44;
  const height = Math.max(ranks.length * laneH + 26, 90);
  const span = Math.max(summary.wallSeconds, 0.001);
  const x = (t: number) => left + (t / span) * (width - left - 12);
  const y = (rank: number) => 18 + ranks.indexOf(rank) * laneH;

  const ticks = useMemo(() => {
    const n = 6;
    return Array.from({ length: n + 1 }, (_, i) => (span * i) / n);
  }, [span]);

  if (!spans.length && !arrows.length) {
    return <p className="text-xs text-muted">No timed operations in this trace.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ minWidth: 620 }}
      >
        {ticks.map((t, i) => (
          <g key={i}>
            <line
              x1={x(t)}
              x2={x(t)}
              y1={12}
              y2={height - 12}
              stroke="#232a35"
              strokeWidth={1}
            />
            <text x={x(t)} y={height - 2} fontSize={8} fill="#8b97a8" textAnchor="middle">
              {t < 10 ? t.toFixed(2) : t.toFixed(0)}s
            </text>
          </g>
        ))}
        {ranks.map((r) => (
          <g key={r}>
            <text x={4} y={y(r) + 10} fontSize={9} fill="#8b97a8">
              r{r}
            </text>
            <line
              x1={left}
              x2={width - 12}
              y1={y(r) + 7}
              y2={y(r) + 7}
              stroke="#1c222c"
              strokeWidth={laneH - 6}
            />
          </g>
        ))}
        {arrows.map((a, i) => (
          <line
            key={`a${i}`}
            x1={x(a.tSend)}
            y1={y(a.src) + 7}
            x2={x(a.tRecv)}
            y2={y(a.dst) + 7}
            stroke="#3d4756"
            strokeWidth={0.5}
            opacity={0.65}
          />
        ))}
        {spans.map((s, i) => {
          const w = Math.max(x(s.end) - x(s.start), 2);
          const id = `s${i}`;
          return (
            <rect
              key={id}
              x={x(s.start)}
              y={y(s.rank)}
              width={w}
              height={laneH - 8}
              rx={2}
              fill={colourFor(s.op)}
              opacity={hover && hover !== id ? 0.35 : 0.9}
              onMouseEnter={() => setHover(id)}
              onMouseLeave={() => setHover(null)}
            >
              <title>
                {`rank ${s.rank} · ${s.op}${s.algorithm ? ` (${s.algorithm})` : ""}\n` +
                  `${(s.end - s.start).toFixed(3)}s` +
                  (s.steps !== undefined ? ` · ${s.steps} rounds` : "")}
              </title>
            </rect>
          );
        })}
      </svg>
    </div>
  );
}

/** Token volume from rank i to rank j: the classic MPI tool view. */
export function Matrix({ trace }: { trace: Trace }) {
  const { matrix, ranks } = trace;
  const max = Math.max(1, ...matrix.flat());
  const cell = ranks.length > 24 ? 10 : ranks.length > 12 ? 16 : 22;
  return (
    <div className="overflow-auto">
      <table className="border-separate" style={{ borderSpacing: 1 }}>
        <tbody>
          {ranks.map((i) => (
            <tr key={i}>
              <td className="pr-1 text-right font-mono text-[9px] text-muted">
                {i}
              </td>
              {ranks.map((j) => {
                const v = matrix[i]?.[j] ?? 0;
                const a = v === 0 ? 0 : 0.15 + 0.85 * (v / max);
                return (
                  <td key={j}>
                    <div
                      title={`r${i} → r${j}: ${v.toLocaleString()} tokens`}
                      style={{
                        width: cell,
                        height: cell,
                        background:
                          v === 0 ? "#161c24" : `rgba(88,166,255,${a.toFixed(3)})`,
                        borderRadius: 2,
                      }}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
          <tr>
            <td />
            {ranks.map((j) => (
              <td
                key={j}
                className="text-center font-mono text-[9px] text-muted"
              >
                {ranks.length > 16 ? "" : j}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
      <p className="mt-2 text-[10px] text-muted">
        Row <span className="font-mono">i</span>, column{" "}
        <span className="font-mono">j</span>: tokens sent from rank i to rank j.
        A bright first row and column is a root-heavy pattern, which is what
        limits how far a gather scales.
      </p>
    </div>
  );
}

/**
 * Cumulative ingest per rank. The curve that has no counterpart in a
 * conventional profiler: it only ever goes up, because context is consumed
 * rather than occupied, and where it meets the budget the job stops.
 */
export function Pressure({ trace, budget }: { trace: Trace; budget: number }) {
  const { pressure, summary } = trace;
  const width = 1000;
  const height = 190;
  const left = 52;
  const span = Math.max(summary.wallSeconds, 0.001);
  const peak = Math.max(
    budget,
    ...pressure.map((p) => p.points[p.points.length - 1]?.tokens ?? 0),
  );
  const x = (t: number) => left + (t / span) * (width - left - 60);
  const y = (v: number) => height - 24 - (v / peak) * (height - 44);

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" style={{ minWidth: 620 }}>
        <line x1={left} x2={width - 60} y1={y(budget)} y2={y(budget)}
              stroke="#f778ba" strokeDasharray="4 3" strokeWidth={1} />
        <text x={width - 56} y={y(budget) + 3} fontSize={9} fill="#f778ba">
          budget
        </text>
        {[0, peak / 2, peak].map((v, i) => (
          <text key={i} x={left - 6} y={y(v) + 3} fontSize={8} fill="#8b97a8" textAnchor="end">
            {Math.round(v / 1000)}k
          </text>
        ))}
        {pressure.map((p) => {
          const d = p.points
            .map((pt, i) => `${i === 0 ? "M" : "L"}${x(pt.t).toFixed(1)},${y(pt.tokens).toFixed(1)}`)
            .join(" ");
          return (
            <path key={p.rank} d={d} fill="none" stroke="#58a6ff"
                  strokeWidth={p.rank === 0 ? 1.8 : 0.8}
                  opacity={p.rank === 0 ? 1 : 0.5}>
              <title>{`rank ${p.rank}`}</title>
            </path>
          );
        })}
        <text x={left} y={12} fontSize={9} fill="#8b97a8">
          cumulative tokens ingested (bold = rank 0)
        </text>
      </svg>
    </div>
  );
}

export function OpTable({ trace }: { trace: Trace }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wider text-muted">
          <th className="pb-1.5 font-medium">Operation</th>
          <th className="pb-1.5 text-right font-medium">Calls</th>
          <th className="pb-1.5 text-right font-medium">Time</th>
          <th className="pb-1.5 text-right font-medium">Tokens</th>
        </tr>
      </thead>
      <tbody className="font-mono">
        {trace.byOp.map((r) => (
          <tr key={r.op} className="border-t border-edge">
            <td className="py-1.5">
              <span
                className="mr-2 inline-block h-2 w-2 rounded-sm align-middle"
                style={{ background: colourFor(r.op) }}
              />
              {r.op}
            </td>
            <td className="py-1.5 text-right">{r.calls}</td>
            <td className="py-1.5 text-right">{r.seconds.toFixed(3)}s</td>
            <td className="py-1.5 text-right">{r.tokens.toLocaleString()}</td>
          </tr>
        ))}
        {!trace.byOp.length && (
          <tr>
            <td colSpan={4} className="py-3 text-muted">
              No timed operations recorded.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
