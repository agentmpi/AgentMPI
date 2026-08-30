import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Matrix, OpTable, Panel, Pressure, Stat, Timeline } from "./components";
import { buildTrace, parseTrace, type Trace } from "./trace";

const BUNDLED = [
  { id: "translation", label: "Book translation, 13 ranks", file: "translation.jsonl" },
  { id: "tinyq", label: "Query engine, 9 ranks", file: "tinyq.jsonl" },
  { id: "microbench", label: "Collectives, 32 ranks", file: "microbench.jsonl" },
];

export default function App() {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [source, setSource] = useState<string>(BUNDLED[0].id);
  const [name, setName] = useState<string>(BUNDLED[0].label);
  const [budget, setBudget] = useState(130000);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async (id: string) => {
    const entry = BUNDLED.find((b) => b.id === id);
    if (!entry) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${import.meta.env.BASE_URL}traces/${entry.file}`);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const text = await res.text();
      if (!text.trim()) throw new Error("the trace file is empty");
      setTrace(parseTrace(text));
      setName(entry.label);
      setSource(id);
    } catch (e) {
      setTrace(null);
      setError(
        `Could not load ${entry.file}: ${e instanceof Error ? e.message : String(e)}. ` +
          `Generate it with "make traces", or drop a .jsonl trace here.`,
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(BUNDLED[0].id);
  }, [load]);

  const onFile = async (file: File) => {
    setLoading(true);
    setError(null);
    try {
      const text = await file.text();
      setTrace(
        file.name.endsWith(".json") && text.trimStart().startsWith("[")
          ? buildTrace(JSON.parse(text))
          : parseTrace(text),
      );
      setName(file.name);
      setSource("custom");
    } catch (e) {
      setError(`Could not parse ${file.name}: ${e instanceof Error ? e.message : e}`);
    } finally {
      setLoading(false);
    }
  };

  const s = trace?.summary;
  const parallelism = useMemo(() => {
    if (!trace || !s || !s.wallSeconds) return null;
    const busy = trace.spans.reduce((a, x) => a + (x.end - x.start), 0);
    return busy / s.wallSeconds;
  }, [trace, s]);

  return (
    <div
      className="min-h-full"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const f = e.dataTransfer.files?.[0];
        if (f) void onFile(f);
      }}
    >
      <header className="border-b border-edge bg-panel/60 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3">
          <div className="mr-auto">
            <h1 className="text-base font-semibold tracking-tight text-slate-100">
              AgentMPI trace viewer
            </h1>
            <p className="text-xs text-muted">
              Who waited for whom, and what it cost in tokens
            </p>
          </div>
          <select
            value={source}
            onChange={(e) => void load(e.target.value)}
            className="rounded-md border border-edge bg-ink px-2.5 py-1.5 text-xs text-slate-200 outline-none focus:border-accent"
          >
            {BUNDLED.map((b) => (
              <option key={b.id} value={b.id}>
                {b.label}
              </option>
            ))}
            {source === "custom" && <option value="custom">{name}</option>}
          </select>
          <button
            onClick={() => fileInput.current?.click()}
            className="rounded-md border border-edge px-2.5 py-1.5 text-xs text-slate-300 transition hover:border-accent hover:text-accent"
          >
            Open trace…
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".jsonl,.json"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onFile(f);
            }}
          />
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-4 px-5 py-5">
        {loading && (
          <div className="rounded-lg border border-edge bg-panel p-8 text-center text-sm text-muted">
            Loading trace…
          </div>
        )}

        {error && !loading && (
          <div className="rounded-lg border border-amber-800/60 bg-amber-950/30 p-4">
            <h2 className="text-sm font-semibold text-amber-300">
              No trace loaded
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-amber-200/80">{error}</p>
          </div>
        )}

        {trace && s && !loading && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <Stat label="Ranks" value={String(s.ranks)} />
              <Stat label="Wall clock" value={`${s.wallSeconds.toFixed(1)}s`} />
              <Stat
                label="Messages"
                value={s.messages.toLocaleString()}
                hint={`${s.collectives} collectives`}
              />
              <Stat
                label="Tokens ingested"
                value={s.tokensReceived.toLocaleString()}
                hint={`${s.tokensSent.toLocaleString()} emitted`}
              />
              <Stat
                label="Collective time"
                value={`${s.collectiveSeconds.toFixed(2)}s`}
                hint={
                  s.wallSeconds
                    ? `${((100 * s.collectiveSeconds) / s.wallSeconds).toFixed(1)}% of wall`
                    : undefined
                }
              />
              <Stat
                label="Parallelism"
                value={parallelism ? `${parallelism.toFixed(1)}x` : "—"}
                hint="busy time / wall clock"
              />
            </div>

            <Panel
              title="Timeline"
              subtitle="One lane per rank; bars are operations, faint lines are matched messages. Long bars are ranks blocked on a peer, which in an agent job is almost all of the wall clock."
            >
              <Timeline trace={trace} />
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
              <Panel
                title="Communication matrix"
                subtitle="Token volume between every pair of ranks."
              >
                <Matrix trace={trace} />
              </Panel>
              <Panel
                title="Time by operation"
                subtitle="Where the run actually went."
              >
                <OpTable trace={trace} />
              </Panel>
            </div>

            <Panel
              title="Context pressure"
              subtitle="Cumulative tokens ingested per rank. Unlike memory this curve never falls, because a token an agent has read is spent; where it meets the budget the rank stops being able to participate."
            >
              <div className="mb-2 flex items-center gap-2 text-xs text-muted">
                <label htmlFor="budget">Budget</label>
                <input
                  id="budget"
                  type="range"
                  min={10000}
                  max={400000}
                  step={10000}
                  value={budget}
                  onChange={(e) => setBudget(Number(e.target.value))}
                  className="w-48 accent-[#f778ba]"
                />
                <span className="font-mono text-slate-300">
                  {(budget / 1000).toFixed(0)}k tokens
                </span>
              </div>
              <Pressure trace={trace} budget={budget} />
            </Panel>

            {trace.notes.length > 0 && (
              <Panel
                title="Runtime notes"
                subtitle="Diagnostics the runtime emitted during the run."
              >
                <ul className="space-y-1 font-mono text-[11px] text-slate-300">
                  {trace.notes.slice(0, 40).map((n, i) => (
                    <li key={i} className="border-t border-edge pt-1">
                      <span className="text-muted">r{n.rank}</span>{" "}
                      {String(n.detail?.message ?? "")}
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </>
        )}

        <footer className="pt-2 text-[11px] leading-relaxed text-muted">
          Traces are the JSONL stream emitted by AgentMPI's profiling
          interface. Produce one from any run with{" "}
          <code className="rounded bg-panel px-1 py-0.5">
            ampi trace export --root &lt;run&gt; --out trace.jsonl
          </code>
          , then drop the file anywhere on this page.
        </footer>
      </main>
    </div>
  );
}
