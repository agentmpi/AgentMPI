import { cursorScaleSummary, findRow, ms, results } from "../../data/results";

const kernels = ["barrier", "bcast_small", "bcast_large", "allreduce_sum", "allgather", "pingpong_small"];
const ns = [2, 4, 8, 16];

export default function ExperimentsPage() {
  const cursor = cursorScaleSummary();
  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 md:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">Measured on this repository</p>
      <h1
        className="mt-3 text-4xl font-semibold"
        style={{ fontFamily: "var(--font-source-serif), Georgia, serif" }}
      >
        Experiments
      </h1>
      <p className="mt-4 max-w-3xl text-muted leading-7">
        Four jobs, all written against AgentMPI, all executed as true multi-rank
        runs. Process-mode numbers characterize the protocol. Cursor-subagent
        runs (see the paper) replace the mock translator and the templated
        modules with live executors.
      </p>

      <section className="mt-10">
        <h2 className="text-2xl font-semibold">Microbenchmarks</h2>
        <p className="mt-2 text-sm text-muted">
          Filesystem transport, median of 5 iterations. Barrier and allgather
          follow ⌈log<sub>2</sub> p⌉ growth; ping-pong is essentially constant
          in p, as it should be.
        </p>
        <div className="mt-4 overflow-x-auto rounded-xl border border-rule bg-card">
          <table className="w-full min-w-[36rem] text-left text-sm">
            <thead className="border-b border-rule text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-3 py-2">Kernel</th>
                {ns.map((n) => (
                  <th key={n} className="px-3 py-2">
                    p={n}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {kernels.map((k) => (
                <tr key={k} className="border-b border-rule/70">
                  <td className="px-3 py-2 font-mono text-xs">{k}</td>
                  {ns.map((n) => (
                    <td key={n} className="px-3 py-2 tabular-nums">
                      {ms(findRow(k, n))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-12 grid gap-6 md:grid-cols-2">
        <article className="rounded-xl border border-rule bg-card p-5">
          <h3 className="text-lg font-semibold">E1 · Book translation (data-parallel)</h3>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <dt className="text-muted">Ranks</dt>
            <dd>{results.translation.n}</dd>
            <dt className="text-muted">Fables</dt>
            <dd>{results.translation.fables}</dd>
            <dt className="text-muted">Elapsed</dt>
            <dd>{results.translation.elapsed_s.toFixed(3)} s</dd>
            <dt className="text-muted">Sends / rendezvous</dt>
            <dd>
              {results.translation.sends} / {results.translation.rendezvous}
            </dd>
          </dl>
          <p className="mt-3 text-sm text-muted">
            Scatter of Vernon Jones’s 1912 Aesop (Gutenberg 11339), gather of
            translations, reduce of a table of contents. Six payloads crossed
            the eager threshold and travelled as artifacts. A live Cursor
            campaign in this repo also translated Alice into French with eight
            draft ranks and four reviewers (glossary exact-hit 1.0).
          </p>
        </article>
        <article className="rounded-xl border border-rule bg-card p-5">
          <h3 className="text-lg font-semibold">E2 · Collaborative kvstore (coupled)</h3>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <dt className="text-muted">Roles</dt>
            <dd>architect, store, cli, tests, docs, reviewer</dd>
            <dt className="text-muted">Files</dt>
            <dd>{results.collab.files.length}</dd>
            <dt className="text-muted">Tests</dt>
            <dd>{results.collab.tests_passed ? "passed" : "failed"}</dd>
            <dt className="text-muted">Sends</dt>
            <dd>{results.collab.sends}</dd>
          </dl>
          <ul className="mt-3 list-disc pl-5 text-sm text-muted">
            {results.collab.review[0]?.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </article>
        <article className="rounded-xl border border-rule bg-card p-5">
          <h3 className="text-lg font-semibold">E3 · Fault, lock, context OOM</h3>
          <ul className="mt-3 space-y-2 text-sm text-muted">
            <li>
              Kill ranks 3 and 6 of 8 → shrink to {results.fault.death_and_shrink.repaired_size},
              allreduce {results.fault.death_and_shrink.allreduce_ok ? "agrees" : "fails"}.
            </li>
            <li>
              8 ranks × 25 locked increments = {results.fault.lock_contention.final} (expected{" "}
              {results.fault.lock_contention.expected}).
            </li>
            <li>
              Context budget 32 trips; compact leaves {results.fault.context_oom.tokens_after_compact} tokens.
              Recv path of a 20-token budget returns <code>oom</code>.
            </li>
            <li>
              Blocking recv unblocks in {results.fault.recv_unblocks.waiter.dt.toFixed(2)}s after source
              death — not a hang.
            </li>
          </ul>
        </article>
        <article className="rounded-xl border border-rule bg-card p-5">
          <h3 className="text-lg font-semibold">E4 · 100-rank corpus reduce</h3>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
            <dt className="text-muted">Ranks</dt>
            <dd>{results.scale.n}</dd>
            <dt className="text-muted">Reports</dt>
            <dd>{results.scale.root.n_reports}</dd>
            <dt className="text-muted">Words (allreduce)</dt>
            <dd>{results.scale.root.total_words.toLocaleString()}</dd>
            <dt className="text-muted">Agreement</dt>
            <dd>{results.scale.agreement ? "all ranks" : "split-brain"}</dd>
            <dt className="text-muted">Elapsed</dt>
            <dd>{results.scale.elapsed_s.toFixed(2)} s</dd>
            <dt className="text-muted">p=32 compare</dt>
            <dd>{results.scale32.elapsed_s.toFixed(2)} s</dd>
          </dl>
          <p className="mt-3 text-sm text-muted">
            Process-mode latency. The live Cursor wave below uses the same
            scatter shape with language-model ranks.
          </p>
        </article>
      </section>

      <section className="mt-12">
        <article className="rounded-xl border border-rule bg-card p-5">
          <h3 className="text-lg font-semibold">Live Cursor · 100-rank Aesop → Spanish</h3>
          <p className="mt-2 text-sm text-muted">
            Each of 100 Cursor subagents was a COMM_WORLD rank: read a work
            packet, write Spanish titles and one-sentence morals, heartbeat{" "}
            <code>finalized</code>. No missing ranks; no cross-rank collisions.
          </p>
          <dl className="mt-3 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
            <dt className="text-muted">Ranks completed</dt>
            <dd>
              {cursor.completed}/{cursor.n}
            </dd>
            <dt className="text-muted">Titles + morals</dt>
            <dd>{cursor.fables}</dd>
            <dt className="text-muted">Missing ranks</dt>
            <dd>{cursor.missing}</dd>
            <dt className="text-muted">Record</dt>
            <dd>
              <code>cursor_scale.json</code>
            </dd>
          </dl>
          <ul className="mt-4 space-y-2 text-sm text-muted">
            {cursor.sample.map((item) => (
              <li key={item.title_en}>
                <span className="text-ink">{item.title_en}</span>
                {" → "}
                <em>{item.title_es}</em>
                {" — "}
                {item.moral_es}
              </li>
            ))}
          </ul>
        </article>
      </section>
    </main>
  );
}
