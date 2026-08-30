# Dossier 05 — Verification and Evaluation Plan

Status: 30 August 2026. This dossier turns the independent protocol, actor,
verification, and experimental-design reviews into a falsifiable research
program. It distinguishes evidence already present in the repository from work
required for a conference claim.

## 1. Acceptance bar

AgentMPI is useful only if it improves cost- or time-to-correct-result over a
strong, information-equivalent orchestrator. Faster queue operations alone are
an implementation result. More model calls without quality-conditioned cost
control are not evidence that the protocol helps.

The paper's defensible current claim is:

> AgentMPI makes communication structure, membership, matching, context
> admission, synchronization, and crash-stop repair explicit and executable
> across two reference bindings. The included live runs establish feasibility,
> not a general quality advantage over simpler orchestration.

A stronger claim requires independent bindings, repeated multi-model trials,
equal-information baselines, formal conformance, and human evaluation for
open-ended output.

## 2. Research questions and hypotheses

### Research questions

1. What latency, CPU, memory, storage, and communication overhead does AgentMPI
   add over direct queues?
2. Does it reduce quality-conditioned makespan at equal model, tool, token, and
   monetary budgets?
3. Which task DAGs benefit: independent, pipeline, fan-out/fan-in, imbalanced,
   iterative, or tightly coupled?
4. Which primitives supply value beyond equivalent explicit sends?
5. Does explicit repair reduce silent corruption and incomplete-result
   acceptance under crash, delay, retry, and context pressure?
6. Are results stable across tasks, model families, and independent executions?

### Falsifiable hypotheses

- H1: protocol-only latency follows a declared model, with collective scaling
  matching the selected algorithm rather than an unlabeled centralized path;
- H2: AgentMPI improves time-to-correct-result over a strong naive parallel
  parent under equal total token/cost budgets;
- H3: any gain disappears or reverses on sufficiently fine-grained or
  communication-heavy tasks;
- H4: strict matching, epochs, and fencing reduce silent stale/duplicate commit
  acceptance to zero under the declared crash-stop model;
- H5: bounded artifact/context admission lowers maximum materialized context
  without changing resolved payload bytes;
- H6: effects survive task/model blocking in a mixed-effects analysis rather
  than arising from a few lucky generations.

No arbitrary overhead target should be selected after observing measurements.

## 3. Required baselines

Every semantic study needs:

1. one capable agent with the same tools and aggregate budget;
2. sequential decomposition with the same task packets;
3. independent sampling/sharding plus deterministic gather or vote;
4. a strong parallel parent with the same agents, prompts, assignments, and
   integration logic but ordinary queues;
5. AgentMPI with only the transport instructions changed.

Report both equal-total-budget and equal-per-agent-budget regimes. If another
framework is included, keep model, service, tools, prompts, and evaluator fixed.

For microbenchmarks, compare against raw in-memory and local IPC queues. SQLite
and POSIX files are reference bindings, not lower bounds.

## 4. Separate protocol cost from model variance

Instrument:

```text
T_total =
 T_startup + T_protocol + T_transport_wait +
 T_model + T_tools + T_integration
```

Do not estimate protocol overhead by subtracting two unrelated mean latencies.
Queueing, overlap, and tail effects make that invalid.

Use three experiment classes:

1. **Deterministic workers.** Echo, hash, sleep, fixed-transform, and seeded DAG
   workers isolate protocol mechanics.
2. **Transcript replay.** Feed identical captured agent outputs through
   AgentMPI and baseline schedulers to isolate orchestration/integration.
3. **Live agents.** Repeat actual Cursor/model executions for semantic outcome
   and service variance.

The repository contains classes 1 and 3. A controlled transcript-replay
baseline remains required.

## 5. Current live evidence

The live campaign used:

- eight French translation ranks, four review ranks, one synthesizer, and one
  separate baseline;
- twelve dependency-coupled `minidag` software ranks;
- one hundred Aesop title/moral ranks collected through the filesystem binding.

These runs preserve task packets, rank products, aggregate results, and traces.
They prove that independent executors can use the interface. They are one
execution per condition and therefore do not estimate a quality effect.

The translation run's glossary and issue counts are mechanical checks, not
human literary judgments. The software run's 25 tests measure its stated API,
not general software quality. The 100-rank run measures completion and
collection, not the correctness of every generated Spanish phrase.

## 6. Confirmatory translation design

Use a public-domain German literary source such as Adalbert Stifter's *Das
Haidedorf* (Project Gutenberg 7068), retaining exact bytes, retrieval time,
hashes, notices, and deterministic paragraph/sentence IDs.

Compare:

- full-context single agent;
- budget-matched isolated sharding;
- AgentMPI sharding with terminology/style allgather, deterministic vote,
  broadcast bulletin, boundary-neighborhood exchange, gather, and independent
  audits.

Use four paragraph-aligned excerpts and six independent repetitions per arm
(72 documents). Randomize arm order and shard-to-rank assignment within each
excerpt/repetition block.

Primary endpoint: blinded MQM weighted errors per 1,000 source words. Two
bilingual raters annotate preregistered boundary and interior windows; a third
adjudicates large disagreements. Report Krippendorff's alpha before
adjudication. LLM judging is exploratory only.

Secondary outcomes: unjustified term/entity/voice variants, source coverage,
boundary-versus-interior error, total/model/protocol tokens, source replication
factor, makespan, agent-seconds, and barrier idle time.

## 7. Confirmatory software design

Use 20–30 bounded, isomorphic Python package variants with a preregistered
hidden oracle. Candidate modules cover typed models, strict codecs, append-only
stores, state machines, analytics, CLI, and tests. Owners work in isolated
worktrees and exchange commit/artifact hashes rather than concurrently changing
one directory.

Compare:

- solo;
- static sharding;
- free-form prose relay;
- structured relay with information equivalent to AgentMPI;
- AgentMPI.

The primary endpoint is complete release correctness under hidden tests.
Secondary outcomes include hidden-test fraction, deterministic replay hash,
API violations, merge conflicts, repair rounds, churn, duplicate work, stale
commit acceptance, communication bytes/tokens, and makespan.

Inject at controller-observable events:

- fail-stop after a checkpoint;
- a quarantined straggler result;
- context loss before execution;
- duplicated or late result;
- malformed/schema-valid but semantically wrong output.

Use stable task/attempt IDs and a single fenced commit winner. Timeouts and
incomplete trials remain in intention-to-treat analysis.

## 8. Statistics

For local latency cells use at least ten warmups and 200 measured repetitions;
use 1,000 or more before reporting p99. Publish raw distributions, median, p95,
and bootstrap intervals.

For live studies, the experimental unit is a task-run—not a rank, message, or
file. Prefer at least five independent executions over 20–30 heterogeneous
tasks per primary comparison, with final count determined by pilot-based power
simulation. Block by task and model. Use:

- paired randomization/permutation inference for controlled blocks;
- mixed-effects logistic models for complete success;
- mixed-effects or log-scale models for latency/cost;
- censored-time models for timeouts;
- bootstrap resampling by task;
- Holm correction for secondary comparisons.

Report effects and uncertainty, not only p-values. If provider seeds are
unavailable, call trials independent executions rather than seeded trials.

## 9. Formal semantics and conformance

Use four linked artifacts:

1. normative transition-system prose;
2. bounded TLA+ models;
3. an independent pure executable oracle;
4. machine-readable histories validated against both.

Whole-protocol linearizability is the wrong criterion. Use it selectively for
mailbox match/dequeue, registry/view installation, locks/fencing, and durable
deduplication. Point-to-point communication needs trace refinement,
at-most-once matching, and non-overtaking. Collectives need per-slot descriptor
agreement and functional correctness. Failure knowledge needs explicit
eventual-detection assumptions.

### Required invariants

- every receive corresponds to one compatible send;
- sends and receives match at most once;
- no cross-session/context/generation/incarnation match;
- ordered streams do not overtake when both sends match the same receive;
- wildcard choices are legal and recorded;
- each collective uses one communicator-global slot and one descriptor;
- strict completion has every fixed-view contribution;
- membership does not mutate inside a generation;
- old-generation traffic cannot affect a repaired generation;
- context/mailbox counters stay within bounds;
- fencing tokens never regress across release or expiry.

### TLA+ scope

Separate finite models should cover:

- 2–3 ranks, two tags, two contexts, and bounded queues for matching;
- three ranks and at least two collective kinds for descriptor mismatch;
- three ranks, two generations, one false suspicion/crash, and delayed old
  frames for repair;
- two lock owners and three fencing values for stale writes.

Safety is checked without fairness. Liveness requires named assumptions:
weakly fair delivery/matching, eventual failure detection, no endless new
crashes during repair, and continuing survivor steps.

The committed `formal/AgentMPI.tla` model currently checks the integrated
matching/collective/repair/fencing kernel with three agents and two bounded
operations. TLC explored 45,073,807 generated states and 11,152,584 distinct
states to depth 24 without an invariant violation. This is a bounded result,
not a proof for unbounded channels.

## 10. Trace and replay requirements

Record decisions, not only timestamps:

- operation/request/message IDs;
- communicator, generation, rank, and incarnation;
- local sequence, Lamport clock, and causal parents;
- invocation, admission, match, completion, cancellation;
- collective slot, descriptor, contributions, and terminal decision;
- suspicion, failure certificate, revoke, and installed view;
- logical timeout/tick, scheduler/fault choice, and replay seed;
- payload/artifact digest and measured byte/context units.

Provide exact replay that forces recorded wildcard/fault decisions and
exploratory replay that permutes causally independent events. An offline
checker should report the first impossible transition and shrink the causal
trace.

## 11. Actor and workflow boundary

AgentMPI should be actor-like only at an endpoint: isolated state, logical
identity, incarnation, monitors, bounded serial mailboxes, and explicit
supervision. It should remain MPI-like for groups: immutable communicator
generations, ranks, tags, and strict collectives.

Borrow:

- AMQP receiver credit and settlement stages;
- gRPC schemas, deadlines, status, and cancellation;
- workflow-engine durable histories, activities, and idempotency;
- actor supervision and restart budgets;
- CSP rendezvous and protocol reasoning.

Do not borrow:

- unbounded actor mailboxes;
- transparent reincarnation under an old communicator;
- automatic retry of non-idempotent effects;
- ephemeral pub/sub as a strict broadcast/barrier;
- global ordering;
- ``exactly once'' without an application transaction;
- a workflow DSL, planner, or supervisor policy in the protocol core.

## 12. Remaining publication blockers

1. A second independently implemented network/broker transport and cross-binding
   interoperability test.
2. An executable abstract oracle plus stateful property/schedule tests.
3. Source/destination incarnation and authenticated capability enforcement in
   every binding, not only schemas.
4. Explicit credit grant/reservation semantics and control-lane reserve.
5. Human bilingual evaluation and repeated, budget-controlled baselines.
6. Parent/control-plane crash recovery and partition experiments.
7. Artifact retention/authorization and external-effect reconciliation.
8. Full unsuccessful-run and timeout retention.

## Sources

Foundational references include Lamport's clocks; Chandy–Lamport snapshots;
Fischer–Lynch–Paterson impossibility; Chandra–Toueg failure detectors;
Herlihy–Wing linearizability; Gray–Cheriton leases; Burrows' Chubby; Hoare's
CSP; Hewitt actors; AMQP 1.0; Temporal histories; QuickCheck; PULSE; dynamic
partial-order reduction; ISP, DAMPI, and MUST for MPI checking; MPI 5.0; and
the ULFM revoke/agree/shrink literature. Exact bibliographic records are in the
other dossiers and `paper/references.bib`.

