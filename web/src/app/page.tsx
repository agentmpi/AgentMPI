import Link from "next/link";
import { findRow, ms, results } from "../data/results";

const mapping = [
  ["MPI_Comm / rank", "Communicator + rank", "A harness, not a chat thread. Isolation via context id."],
  ["MPI_Send / Recv", "send / recv + tags", "Envelope matching. ANY_SOURCE / ANY_TAG."],
  ["Eager / rendezvous", "Eager / artifact path", "Large work products never enter a prompt."],
  ["MPI_Bcast", "bcast (binomial tree)", "Share a plan, rubric, or style guide."],
  ["MPI_Scatter / Gather", "scatter / gather", "Partition work; collect results."],
  ["MPI_Reduce / Allreduce", "reduce / allreduce + SYNTHESIZE", "Hierarchical merge, vote, or stitch."],
  ["MPI_Barrier", "barrier (Bruck)", "Integration checkpoint. Do not busy-wait on hope."],
  ["MPI_Win + lock", "windows + exclusive lock", "One editor of a file or design doc."],
  ["MPI_Comm_split / spawn", "split / spawn / sessions", "Specialist teams; join without world restart."],
  ["ULFM revoke / shrink / agree", "revoke / shrink / agree", "Executor death is visible and programmable."],
  ["Process memory", "Context budget + compact", "OOM is a first-class error, not a silent truncation."],
];

export default function Home() {
  const barrier16 = findRow("barrier", 16);
  const bcast16 = findRow("bcast_small", 16);
  return (
    <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 md:px-6 md:py-14">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">A protocol, not a framework</p>
      <h1
        className="mt-3 max-w-4xl text-4xl font-semibold leading-tight md:text-6xl"
        style={{ fontFamily: "var(--font-source-serif), Georgia, serif" }}
      >
        Agent Message Passing Interface
      </h1>
      <p className="mt-6 max-w-3xl text-lg leading-8 text-muted">
        AgentMPI is to multi-agent harnesses what MPI is to parallel programs: a small,
        portable set of sends, receives, collectives, windows, and failure calls that
        people use to write <em>their own</em> systems. It is not a crew, a graph, or a
        product. It is the interface.
      </p>
      <div className="mt-8 flex flex-wrap gap-3">
        <Link
          href="/paper"
          className="rounded-full bg-ink px-5 py-2.5 text-sm text-paper no-underline hover:bg-accent"
        >
          Read the paper
        </Link>
        <Link
          href="/spec"
          className="rounded-full border border-ink px-5 py-2.5 text-sm text-ink no-underline hover:border-accent hover:text-accent"
        >
          Protocol spec
        </Link>
        <Link
          href="/experiments"
          className="rounded-full border border-ink px-5 py-2.5 text-sm text-ink no-underline hover:border-accent hover:text-accent"
        >
          Experiment traces
        </Link>
      </div>

      <section className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["16-rank barrier", ms(barrier16), "Bruck distance-doubling"],
          ["16-rank broadcast", ms(bcast16), "Binomial tree, short message"],
          ["100-rank map-reduce", `${results.scale.elapsed_s.toFixed(2)} s`, `${results.scale.root.n_reports} fables, allreduce agreed`],
          ["Fault shrink 8→6", `${results.fault.recv_unblocks.waiter.dt.toFixed(2)} s`, "Recv unblocks; allreduce on survivors"],
        ].map(([k, v, s]) => (
          <article key={k} className="rounded-xl border border-rule bg-card p-4">
            <p className="text-xs uppercase tracking-wide text-muted">{k}</p>
            <p className="mt-2 text-3xl font-semibold tabular-nums">{v}</p>
            <p className="mt-2 text-sm text-muted">{s}</p>
          </article>
        ))}
      </section>

      <section className="mt-16">
        <h2 className="text-2xl font-semibold" style={{ fontFamily: "var(--font-source-serif), Georgia, serif" }}>
          Every MPI primitive has an agent reading
        </h2>
        <p className="mt-3 max-w-3xl text-muted">
          The claim is not metaphorical. The implementation uses the same algorithms
          MPICH documented for short-message collectives (Thakur, Rabenseifner, Gropp,
          2005) and the same failure vocabulary ULFM added to MPI.
        </p>
        <div className="mt-6 overflow-x-auto rounded-xl border border-rule bg-card">
          <table className="w-full min-w-[40rem] text-left text-sm">
            <thead className="border-b border-rule text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-3">MPI</th>
                <th className="px-4 py-3">AgentMPI</th>
                <th className="px-4 py-3">Why a harness author cares</th>
              </tr>
            </thead>
            <tbody>
              {mapping.map(([a, b, c]) => (
                <tr key={a} className="border-b border-rule/70 last:border-0">
                  <td className="px-4 py-3 font-mono text-xs">{a}</td>
                  <td className="px-4 py-3 font-mono text-xs">{b}</td>
                  <td className="px-4 py-3 text-muted">{c}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-16 grid gap-6 md:grid-cols-2">
        <article className="rounded-xl border border-rule bg-card p-6">
          <h3 className="text-xl font-semibold">What you write</h3>
          <pre className="mt-4 overflow-x-auto rounded-lg bg-ink p-4 text-xs leading-6 text-paper">{`from agentmpi import Init, Finalize, COMM_WORLD, Op

Init()
shard = COMM_WORLD.scatter(chapters if rank==0 else None)
text  = translate(shard)          # your agent, any model
book  = COMM_WORLD.gather(text)
COMM_WORLD.barrier()
Finalize()`}</pre>
        </article>
        <article className="rounded-xl border border-rule bg-card p-6">
          <h3 className="text-xl font-semibold">What usually breaks</h3>
          <ul className="mt-4 space-y-3 text-sm leading-6 text-muted">
            <li>
              <strong className="text-ink">No shared info.</strong> Broadcast and
              windows replace “hope the prompt mentioned it.”
            </li>
            <li>
              <strong className="text-ink">Dead executors hang the job.</strong>{" "}
              Heartbeats, <code>DeadRankError</code>, revoke, shrink.
            </li>
            <li>
              <strong className="text-ink">Lost locks.</strong> Exclusive directory
              locks on artifact windows.
            </li>
            <li>
              <strong className="text-ink">No lifecycle.</strong> init / active /
              suspended / failed / finalized.
            </li>
            <li>
              <strong className="text-ink">Context OOM.</strong> Token budget,
              rendezvous artifacts, <code>context_compact</code>.
            </li>
          </ul>
        </article>
      </section>
    </main>
  );
}
