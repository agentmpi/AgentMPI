# Experiments

The artifact uses two evidence classes and never conflates them.

1. **Protocol microbenchmarks** use deterministic local processes to measure
   matching, collective, context, and repair mechanics. These isolate runtime
   behavior from model nondeterminism.
2. **Cursor-agent macrobenchmarks** are genuine independent agent executions.
   Executors join durable AgentMPI sessions, receive broadcast/scattered work,
   and contribute gathered results. They test whether the interface is usable
   for real harnesses, not whether one model universally benefits from more
   copies.

## Research questions

- RQ1: Does AgentMPI preserve its stated matching, collective, epoch, context,
  and fencing invariants under concurrency and injected failure?
- RQ2: What latency, storage, and message overhead does the transparent SQLite
  reference binding add as rank count and payload size grow?
- RQ3: Can independent translation agents follow a shared glossary while
  working on disjoint passages, and can a review phase identify boundary and
  terminology defects?
- RQ4: Can agents collaboratively implement a dependency-structured software
  project while exchanging only explicit contracts and artifacts?
- RQ5: After an executor disappears, can a harness identify missing work,
  revoke/shrink the epoch, and reassign without accepting a stale commit?
- RQ6: Does artifact externalization cap prompt-facing mailbox growth without
  changing resolved payload bytes?

## Translation workload

The source is eight contiguous passages from Chapter I of Lewis Carroll's
public-domain *Alice's Adventures in Wonderland*, Project Gutenberg eBook #11.
The target is French.

Protocol schedule:

```text
rank 0 Bcast(style/glossary)
rank 0 Scatter(eight passages)
ranks 1..8 translate independently
all ranks Gather(rank-ordered drafts)
rank 0 Bcast(review contract)
rank 0 Scatter(four adjacent two-passage review windows)
ranks 1..4 review
all ranks Gather(rank-ordered review reports)
```

The adjacent review windows expose boundary continuity problems without sending
the full book to every reviewer. Machine-readable source, prompts, drafts,
reviews, traces, and synthesis live under `tasks/` and `results/translation/`.

Metrics:

- protocol completion, rank order, operation latency, messages, trace events;
- glossary exact-match compliance;
- omitted named entities, numbers, and quoted utterances;
- reviewer issue count by category and revision acceptance;
- cross-boundary terminology disagreement;
- estimated materialized context versus a naive all-to-all/full-book prompt;
- blinded pairwise preference against one-agent and no-review baselines in a
  future replicated study.

The present run is a pilot. Literary quality scores from the same model family
are not treated as independent human judgments.

## Collaborative software workload

Agents build `minidag`, a dependency-free deterministic DAG execution library,
from a versioned API contract. The work DAG separates graph, parser, scheduler,
executor, CLI, tests, documentation, staged review, and final integration into
explicit owners. Dependencies are `DONE` messages; every mutation uses an
AgentMPI lease lock and fencing token. The final integrator receives all eleven
upstream reports, runs tests and the example, and returns a structured result.
Executor death is exercised by the separate 16-process failure experiment,
not this successful software run.

Primary metrics are test pass rate, API-contract violations, integration
defects, conflicting edits, messages, materialized tokens, and wall time. A
future naive condition gives agents
only prose goals; the AgentMPI condition broadcasts a versioned contract and
uses explicit epochs. Because prompt information differs, the pilot reports the
comparison descriptively; a publication experiment must equalize information
content and vary only protocol structure.

## Microbenchmark matrix

The benchmark driver records raw samples and summaries for:

- ping-pong payloads from 16 B through the artifact threshold;
- barriers and allreduces over 2, 4, 8, 16, and 32 OS processes;
- communicator isolation and wildcard match order;
- bounded-mailbox saturation and drain;
- inline versus content-addressed payload context charge;
- executor death during point-to-point and collective operations;
- revoke/shrink/agree and lock takeover with fencing;
- trace growth and SQLite database size.

Each point uses warmups and repeated trials. Raw samples, host metadata, Python
version, commit, and parameters are stored as JSON; paper tables are generated
from those files.

## Reproduction

```bash
. .venv/bin/activate
pytest
python scripts/run_benchmarks.py --output experiments/results/microbench.json
```

The Cursor-agent workload requires an orchestrator capable of launching
independent agents in the same workspace. The exact executor protocol is in
`tasks/translation_agent_protocol.md`; `coordinator.py` owns rank zero.

The completed 100-rank Aesop→Spanish Cursor wave is collected by
`python3 experiments/cursor/collect_scale.py` into
`experiments/results/cursor_scale.json` (100/100 ranks, 200 items).

## Threats to validity

- One provider/model family, one machine, and pilot-scale replication do not
  establish generality.
- Agent scheduling and model output are nondeterministic.
- Token estimates are tokenizer-independent approximations.
- SQLite is an executable semantic oracle, not a cluster transport.
- Translation's decomposition is much more parallel than software integration.
- Agent-generated reviews can share correlated errors.
- Different protocol conditions can accidentally change prompt information.
- A failure hook models fail-stop behavior, not Byzantine or provider-wide
  correlated failure.

