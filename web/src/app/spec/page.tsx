export default function SpecPage() {
  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-10 md:px-6">
      <p className="text-xs uppercase tracking-[0.2em] text-accent">AgentMPI 1.0</p>
      <h1
        className="mt-3 text-4xl font-semibold"
        style={{ fontFamily: "var(--font-source-serif), Georgia, serif" }}
      >
        Protocol specification
      </h1>
      <p className="mt-4 leading-7 text-muted">
        Language-independent rules. The Python package and CLI are bindings. A
        conforming implementation may use any transport that preserves matching,
        progress, and failure.
      </p>

      <section className="mt-10 space-y-4 leading-7">
        <h2 className="text-2xl font-semibold">Matching</h2>
        <p>
          A receive with <code>(source, tag)</code> matches the oldest unmatched
          message whose envelope agrees on communicator and whose source/tag are
          equal or wildcards (<code>ANY_SOURCE</code>, <code>ANY_TAG</code>). This
          is MPI matching, including the hidden context identifier that lets two
          harnesses share a group without mixing messages.
        </p>
        <h2 className="text-2xl font-semibold">Eager and rendezvous</h2>
        <p>
          Payloads ≤ 8192 bytes travel in the mailbox file. Larger payloads leave
          only an envelope and an artifact path. Receivers materialize artifacts
          on match. That is how a rank avoids ingesting another rank’s entire
          transcript into its context window.
        </p>
        <h2 className="text-2xl font-semibold">Collectives</h2>
        <p>
          All ranks invoke the same collective in the same order. The
          implementation uses binomial trees for broadcast, scatter, gather, and
          reduce; Bruck distance-doubling for barrier and allgather; recursive
          doubling for power-of-two allreduce; pairwise exchange for alltoall.
          Agent-native reduction operators are <code>CONCAT</code>,{" "}
          <code>MERGE</code>, and <code>SYNTHESIZE</code>.
        </p>
        <h2 className="text-2xl font-semibold">Windows and locks</h2>
        <p>
          Named artifacts with exclusive or shared locks. Exclusive locks are
          POSIX directory creates. A window is a design document, a source file,
          or a blackboard — the MPI-2 passive-target analog.
        </p>
        <h2 className="text-2xl font-semibold">Faults</h2>
        <p>
          The fabric is reliable; executors are not. Heartbeat timeout or an
          explicit <code>failed</code> state declares a rank dead. A receive
          whose source is dead raises <code>DeadRankError</code>.{" "}
          <code>revoke</code>, <code>agree</code>, and <code>shrink</code> follow
          ULFM. Recovery policy belongs to the harness.
        </p>
        <h2 className="text-2xl font-semibold">Context budget</h2>
        <p>
          Incoming messages are charged in estimated tokens. Exceeding the budget
          is an error. <code>context_compact</code> pages the rank;{" "}
          <code>context_put</code> publishes a summary into a shared window.
        </p>
        <p className="pt-4 text-sm text-muted">
          The normative text lives in <code>spec/AGENTMPI.md</code> in this
          repository. Bindings: <code>from agentmpi import COMM_WORLD</code> and{" "}
          <code>python -m agentmpi</code>.
        </p>
      </section>
    </main>
  );
}
