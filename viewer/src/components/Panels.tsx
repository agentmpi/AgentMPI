import type { Collective, Dist, Trace } from "../types";

const fmtS = (v: number | null | undefined) =>
  v === null || v === undefined ? "-" : v < 1 ? `${(v * 1000).toFixed(0)}ms` : `${v.toFixed(2)}s`;

const fmtN = (v: number | null | undefined) =>
  v === null || v === undefined ? "-" : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v));

export function DistRow({ label, d, unit }: { label: string; d: Dist; unit: "s" | "n" }) {
  const f = unit === "s" ? fmtS : fmtN;
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{d.n}</td>
      <td className="num">{f(d.p50)}</td>
      <td className="num">{f(d.p90)}</td>
      <td className="num">{f(d.max)}</td>
      <td className="num">{d.cv !== undefined ? d.cv.toFixed(2) : "-"}</td>
    </tr>
  );
}

/** Context occupancy: the resource that actually limits agent scale. */
export function ContextPanel({ trace }: { trace: Trace }) {
  const budget = trace.summary.context.budget || 1;
  const worst = Math.max(budget, ...trace.ranks.map((r) => r.ctx_hwm));
  return (
    <>
      <div className="card">
        <h3>Context occupancy per rank</h3>
        <p className="hint">
          High-water mark of payload tokens delivered into each rank's context window, against its budget.
          This is the quantity that decides whether a harness survives at scale: an agent that fills its
          window stops being able to reason, and no amount of retrying fixes it. Bars turn red past budget.
        </p>
        <table className="t">
          <thead>
            <tr>
              <th>rank</th>
              <th>role</th>
              <th>state</th>
              <th style={{ width: "42%" }}>context high-water mark</th>
              <th className="num">hwm</th>
              <th className="num">budget</th>
              <th className="num">runtime calls</th>
            </tr>
          </thead>
          <tbody>
            {trace.ranks.map((r) => {
              const pct = (r.ctx_hwm / worst) * 100;
              const over = r.ctx_hwm > budget;
              return (
                <tr key={r.rank}>
                  <td>r{r.rank}{r.epoch > 0 ? `.e${r.epoch}` : ""}</td>
                  <td>{r.role ?? "-"}</td>
                  <td>
                    <span
                      className="pill"
                      data-tone={
                        r.state === "failed" ? "bad" : r.state === "finalized" ? "good" : undefined
                      }
                    >
                      {r.state}
                    </span>
                  </td>
                  <td>
                    <div className="bar" data-over={over}>
                      <i style={{ width: `${Math.max(1, pct)}%` }} />
                      <span>{Math.round((r.ctx_hwm / budget) * 100)}%</span>
                    </div>
                  </td>
                  <td className="num">{fmtN(r.ctx_hwm)}</td>
                  <td className="num">{fmtN(r.ctx_budget)}</td>
                  <td className="num">{r.calls}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Distributions</h3>
        <p className="hint">
          Agent latency is heavy-tailed, so the coefficient of variation matters as much as the median: a
          barrier waits for the maximum of P samples, and the gap between p50 and max is what a quorum
          collective buys back.
        </p>
        <table className="t">
          <thead>
            <tr>
              <th>metric</th>
              <th className="num">n</th>
              <th className="num">p50</th>
              <th className="num">p90</th>
              <th className="num">max</th>
              <th className="num">cv</th>
            </tr>
          </thead>
          <tbody>
            <DistRow label="message latency" d={trace.summary.messages.latency_s} unit="s" />
            <DistRow label="time waiting in runtime" d={trace.summary.wait_s} unit="s" />
            <DistRow label="agent merge step" d={trace.summary.agent_merge_s} unit="s" />
            <DistRow label="context hwm (tokens)" d={trace.summary.context.hwm} unit="n" />
            <DistRow label="runtime calls per rank" d={trace.summary.calls} unit="n" />
          </tbody>
        </table>
      </div>
    </>
  );
}

export function CollectivesPanel({
  trace,
  onPick,
}: {
  trace: Trace;
  onPick: (c: Collective) => void;
}) {
  const rows = [...trace.collectives].sort((a, b) => a.t_start - b.t_start);
  return (
    <div className="card">
      <h3>Collectives ({rows.length})</h3>
      <p className="hint">
        Every collective, its chosen algorithm, and the spread between the first and last participant. The
        straggler column is the cost of bulk synchrony: it is how long the fastest rank sat idle waiting for
        the slowest one.
      </p>
      <table className="t">
        <thead>
          <tr>
            <th>t</th>
            <th>op</th>
            <th>algo</th>
            <th>label</th>
            <th className="num">parts</th>
            <th className="num">duration</th>
            <th className="num">straggler gap</th>
            <th className="num">merges</th>
            <th>state</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const dones = c.participants.map((p) => p.t_done).filter((v): v is number => v !== null);
            const joins = c.participants.map((p) => p.t_join);
            const gap = dones.length && joins.length ? Math.max(...dones) - Math.min(...dones) : null;
            const merges = c.participants.reduce((a, p) => a + p.rounds, 0);
            const crit = Math.max(0, ...c.participants.map((p) => p.rounds));
            return (
              <tr key={c.id} onClick={() => onPick(c)} style={{ cursor: "pointer" }}>
                <td>{c.t_start.toFixed(2)}s</td>
                <td>
                  {c.op}
                  {c.reduce_op ? <span style={{ color: "#8a99ad" }}>({c.reduce_op})</span> : null}
                </td>
                <td>
                  <span className="pill" data-tone="coll">
                    {c.algo}
                  </span>
                </td>
                <td>{c.label ?? "-"}</td>
                <td className="num">{c.nparts}</td>
                <td className="num">{c.t_end !== null ? fmtS(c.t_end - c.t_start) : "open"}</td>
                <td className="num">{gap === null ? "-" : fmtS(gap)}</td>
                <td className="num">{merges ? `${merges} (crit ${crit})` : "-"}</td>
                <td>
                  <span
                    className="pill"
                    data-tone={
                      c.state === "closed" ? "good" : c.state === "revoked" ? "bad" : "warn"
                    }
                  >
                    {c.state}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function WindowsPanel({ trace }: { trace: Trace }) {
  if (!trace.windows.length) {
    return (
      <div className="card">
        <h3>Windows</h3>
        <p className="hint">This run used no one-sided shared state.</p>
      </div>
    );
  }
  return (
    <>
      {trace.windows.map((w) => (
        <div className="card" key={w.id}>
          <h3>
            window <code>{w.name}</code> — {w.cells.length} cells,{" "}
            {fmtN(w.cells.reduce((a, c) => a + c.tokens, 0))} tokens
          </h3>
          <p className="hint">
            The shared blackboard. Versions above 1 mean several ranks wrote the same key; the writer column
            attributes every claim to a rank, which is what makes shared agent state auditable instead of
            merely convenient.
          </p>
          <table className="t">
            <thead>
              <tr>
                <th>key</th>
                <th className="num">version</th>
                <th className="num">tokens</th>
                <th className="num">writer</th>
                <th className="num">last write</th>
              </tr>
            </thead>
            <tbody>
              {w.cells.map((c) => (
                <tr key={c.key}>
                  <td>{c.key}</td>
                  <td className="num">
                    {c.version > 1 ? (
                      <span className="pill" data-tone="warn">
                        {c.version}
                      </span>
                    ) : (
                      c.version
                    )}
                  </td>
                  <td className="num">{fmtN(c.tokens)}</td>
                  <td className="num">{c.writer ?? "-"}</td>
                  <td className="num">{c.t.toFixed(2)}s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}

export function FailuresPanel({ trace }: { trace: Trace }) {
  const respawns = trace.events.filter((e) => e.kind === "respawn");
  const revokes = trace.events.filter((e) => e.kind === "comm_revoke");
  const shrinks = trace.events.filter((e) => e.kind === "comm_shrink");
  if (!trace.failures.length && !revokes.length) {
    return (
      <div className="card">
        <h3>Fault tolerance</h3>
        <p className="hint">No failures were detected in this run.</p>
      </div>
    );
  }
  return (
    <div className="card">
      <h3>Fault tolerance</h3>
      <p className="hint">
        Failures detected, and the recovery primitives the harness invoked in response. A revoke followed by a
        shrink is the ULFM recovery pattern: unblock every survivor stuck in a collective, then agree on a new
        communicator over whoever is left.
      </p>
      <table className="t">
        <thead>
          <tr>
            <th className="num">t</th>
            <th>event</th>
            <th className="num">rank</th>
            <th className="num">epoch</th>
            <th>detail</th>
          </tr>
        </thead>
        <tbody>
          {[
            ...trace.failures.map((f) => ({
              t: f.t,
              what: `failure: ${f.kind}`,
              rank: f.rank,
              epoch: f.epoch,
              detail: JSON.stringify(f.detail),
              tone: "bad" as const,
            })),
            ...respawns.map((e) => ({
              t: e.t,
              what: "respawn",
              rank: e.rank ?? -1,
              epoch: e.epoch ?? -1,
              detail: JSON.stringify(e.detail),
              tone: "warn" as const,
            })),
            ...revokes.map((e) => ({
              t: e.t,
              what: "comm revoke",
              rank: e.rank ?? -1,
              epoch: e.epoch ?? -1,
              detail: JSON.stringify(e.detail),
              tone: "bad" as const,
            })),
            ...shrinks.map((e) => ({
              t: e.t,
              what: "comm shrink",
              rank: e.rank ?? -1,
              epoch: e.epoch ?? -1,
              detail: JSON.stringify(e.detail),
              tone: "good" as const,
            })),
          ]
            .sort((a, b) => a.t - b.t)
            .map((r, i) => (
              <tr key={i}>
                <td className="num">{r.t.toFixed(2)}s</td>
                <td>
                  <span className="pill" data-tone={r.tone}>
                    {r.what}
                  </span>
                </td>
                <td className="num">{r.rank}</td>
                <td className="num">{r.epoch}</td>
                <td style={{ color: "#8a99ad" }}>{r.detail === "null" ? "" : r.detail}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
