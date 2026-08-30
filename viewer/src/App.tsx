import { useCallback, useEffect, useMemo, useState } from "react";
import { getPayload, getTrace, listRuns } from "./api";
import Timeline from "./components/Timeline";
import { CollectivesPanel, ContextPanel, FailuresPanel, WindowsPanel } from "./components/Panels";
import type { Collective, RunInfo, Trace } from "./types";

type Tab = "timeline" | "collectives" | "context" | "windows" | "faults";

const TABS: { id: Tab; label: string }[] = [
  { id: "timeline", label: "timeline" },
  { id: "collectives", label: "collectives" },
  { id: "context", label: "context" },
  { id: "windows", label: "shared state" },
  { id: "faults", label: "fault tolerance" },
];

const fmtN = (v: number | null | undefined) =>
  v === null || v === undefined ? "-" : v >= 1_000_000 ? `${(v / 1e6).toFixed(1)}M` : v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(Math.round(v));

export default function App() {
  const [runs, setRuns] = useState<RunInfo[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("timeline");
  const [drawer, setDrawer] = useState<{ title: string; body: string } | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      const rs = await listRuns();
      setRuns(rs);
      setRunsError(null);
      setSelected((cur) => cur ?? (rs.length ? rs[0].name : null));
    } catch (e) {
      setRunsError(e instanceof Error ? e.message : String(e));
      setRuns([]);
    }
  }, []);

  useEffect(() => {
    void refreshRuns();
  }, [refreshRuns]);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTrace(selected)
      .then((t) => {
        if (!cancelled) setTrace(t);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const onPickMessage = useCallback(
    async (seq: number) => {
      if (!trace || !selected) return;
      const m = trace.messages.find((x) => x.seq === seq);
      if (!m) return;
      const ev = trace.events.find((e) => e.msg === seq && e.kind === "send");
      const handleFromEvent = (ev?.detail as { handle?: string } | undefined)?.handle;
      setDrawer({
        title: `message #${seq}: rank ${m.src} → rank ${m.dst}`,
        body: `loading payload…`,
      });
      // The trace carries handles on window cells but not on arrows, so re-read
      // the message body through the object endpoint using the summary path.
      const handle = handleFromEvent;
      const header =
        `src=${m.src}  dst=${m.dst}  tag=${m.tag}\n` +
        `tokens=${m.tokens}  delivery=${m.mode}  status=${m.status}\n` +
        `sent=${m.t_send.toFixed(3)}s  delivered=${m.t_recv === null ? "never" : `${m.t_recv.toFixed(3)}s`}\n` +
        (m.coll ? `collective=${m.coll}\n` : "") +
        "\n";
      if (!handle) {
        setDrawer({ title: `message #${seq}`, body: header + "(payload handle not present in the trace)" });
        return;
      }
      try {
        const p = await getPayload(selected, handle, 1500);
        setDrawer({
          title: `message #${seq}: rank ${m.src} → rank ${m.dst}`,
          body: `${header}handle=${p.handle}  payload_tokens=${p.tokens}  shown=${p.view_tokens}\n\n${p.body}`,
        });
      } catch (e) {
        setDrawer({
          title: `message #${seq}`,
          body: header + `could not read payload: ${e instanceof Error ? e.message : String(e)}`,
        });
      }
    },
    [trace, selected],
  );

  const onPickCollective = useCallback((c: Collective) => {
    const lines = c.participants
      .map(
        (p) =>
          `  rank ${String(p.rank).padStart(3)}  ${p.state.padEnd(9)} join=${p.t_join.toFixed(2)}s  ` +
          `done=${p.t_done === null ? "  -   " : `${p.t_done.toFixed(2)}s`}  merges=${p.rounds}`,
      )
      .join("\n");
    setDrawer({
      title: `${c.op}${c.reduce_op ? `(${c.reduce_op})` : ""} — ${c.algo}`,
      body:
        `id=${c.id}\ncomm=${c.comm}\nlabel=${c.label ?? "-"}\nroot=${c.root ?? "-"}\n` +
        `state=${c.state}\nstart=${c.t_start.toFixed(3)}s  end=${
          c.t_end === null ? "open" : `${c.t_end.toFixed(3)}s`
        }\nparticipants=${c.nparts}\n\n${lines}`,
    });
  }, []);

  const stats = useMemo(() => {
    if (!trace) return [];
    const s = trace.summary;
    const over = s.context.over_budget_ranks.length;
    return [
      { k: "ranks", v: String(trace.world_size) },
      { k: "wall", v: `${s.wall_s.toFixed(1)}`, unit: "s" },
      { k: "messages", v: fmtN(s.messages.count), unit: `${fmtN(s.messages.undelivered)} unread` },
      { k: "payload", v: fmtN(s.messages.payload_tokens), unit: "tokens" },
      { k: "into context", v: fmtN(s.context.total_delivered_tokens), unit: "tokens" },
      {
        k: "ctx hwm",
        v: fmtN(s.context.hwm.max),
        unit: `of ${fmtN(s.context.budget)}`,
        tone: over ? "bad" : "good",
      },
      { k: "collectives", v: String(trace.collectives.length) },
      { k: "rma ops", v: fmtN(s.rma.puts + s.rma.gets + s.rma.accumulates), unit: `${s.rma.cells} cells` },
      { k: "failures", v: String(s.failures), tone: s.failures ? "bad" : "good" },
    ];
  }, [trace]);

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <h1>AgentMPI trace viewer</h1>
          <p>
            Post-mortem analysis for multi-agent runs, in the lineage of Jumpshot and Vampir. Every AgentMPI
            job records its own trace, so this is always available.
          </p>
        </div>
        <div className="runs">
          {runs === null && <div className="run"><span className="name">loading runs…</span></div>}
          {runs !== null && runs.length === 0 && (
            <div className="run">
              <span className="name">no runs found</span>
              <span className="meta">{runsError ?? "start a job with `ampi run`"}</span>
            </div>
          )}
          {runs?.map((r) => (
            <button
              key={r.name}
              className="run"
              data-active={selected === r.name}
              onClick={() => {
                setSelected(r.name);
                setTab("timeline");
              }}
            >
              <span className="name">{r.name}</span>
              <span className="meta">
                {r.error ? (
                  <b style={{ color: "var(--bad)" }}>{r.error}</b>
                ) : (
                  <>
                    <b>P={r.world_size}</b>
                    <b>{r.wall_s?.toFixed(0)}s</b>
                    <b>{fmtN(r.messages)} msg</b>
                    {r.failures ? <b style={{ color: "var(--bad)" }}>{r.failures} fail</b> : null}
                  </>
                )}
              </span>
            </button>
          ))}
        </div>
        <div style={{ padding: 10, borderTop: "1px solid var(--line-strong)" }}>
          <button onClick={() => void refreshRuns()} style={{ width: "100%" }}>
            refresh
          </button>
        </div>
      </aside>

      <main className="main">
        {!selected ? (
          <div className="empty">
            <h3>No run selected</h3>
            <p>
              AgentMPI writes a durable journal for every job, so any job directory can be inspected here.
              Create one and it will appear on the left:
            </p>
            <pre>{`# create a 16-rank job\nampi run --np 16 --label demo --job-root runs/demo\n\n# serve the API this viewer reads\nampi serve --runs runs --port 47913`}</pre>
          </div>
        ) : (
          <>
            <div className="topbar">
              <h2>{trace?.label ?? selected}</h2>
              <span className="sub">
                {trace ? `job ${trace.job} · ${selected}` : selected}
                {trace ? ` · eager threshold ${trace.config.eager_tokens} tokens` : ""}
              </span>
            </div>

            {error && (
              <div className="empty">
                <h3>Could not load this run</h3>
                <p>{error}</p>
              </div>
            )}

            {trace && (
              <>
                <div className="stats">
                  {stats.map((s) => (
                    <div className="stat" key={s.k} data-tone={s.tone}>
                      <div className="k">{s.k}</div>
                      <div className="v">
                        {s.v} {s.unit ? <small>{s.unit}</small> : null}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="tabs">
                  {TABS.map((t) => (
                    <button key={t.id} data-active={tab === t.id} onClick={() => setTab(t.id)}>
                      {t.label}
                    </button>
                  ))}
                </div>

                <div className="panel">
                  {tab === "timeline" && (
                    <div className="card">
                      <h3>Execution timeline</h3>
                      <p className="hint">
                        One row per rank. Purple bands are time spent inside a collective; blue lines are
                        messages, drawn from send to delivery with opacity scaled by payload size. Drag
                        horizontally to zoom. Click a message to read its payload, or a band to inspect the
                        collective.
                      </p>
                      <Timeline
                        trace={trace}
                        onPickMessage={onPickMessage}
                        onPickCollective={onPickCollective}
                      />
                    </div>
                  )}
                  {tab === "collectives" && <CollectivesPanel trace={trace} onPick={onPickCollective} />}
                  {tab === "context" && <ContextPanel trace={trace} />}
                  {tab === "windows" && <WindowsPanel trace={trace} />}
                  {tab === "faults" && <FailuresPanel trace={trace} />}
                </div>
              </>
            )}

            {loading && !trace && <div className="empty">loading trace…</div>}
          </>
        )}
      </main>

      {drawer && (
        <div className="drawer">
          <header>
            <h3>{drawer.title}</h3>
            <button onClick={() => setDrawer(null)}>close</button>
          </header>
          <div className="body">{drawer.body}</div>
        </div>
      )}
    </div>
  );
}
