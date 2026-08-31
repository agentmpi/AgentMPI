# AgentMPI nine-branch synthesis

This note records the consolidation decision made on 31 August 2026. It is a
design provenance artifact, not a claim that commit count or test count implies
scientific quality. Each remote branch was inspected as a separate research
system: specification, runtime, tests, paper, raw evidence, and negative
results. Several branches have unrelated roots or duplicated histories, so
normal ancestry alone is not a valid ranking.

## Decision

`origin/cursor/1M_high` at `3607aa0` is the implementation and evidence base.
It is the tip with the complete v0.2 runtime, E1–E5 artifacts, and the latest
regressions derived from live agents. The consolidation branch merges that
history into `main`, preserving provenance.

The choice is deliberately narrower than “1M_high contains every best idea.”
It does not. Opus 2 is the strongest experimental-science donor; Opus 3 is the
strongest paper/tooling donor; `agentmpi-protocol-c18f` contains lifecycle
safety that the selected line lacked. Those ideas are ported by invariant and
test, not by combining incompatible runtimes.

## Audited branches

| Branch | Tip | Verified tests | Role in consolidation |
|---|---:|---:|---|
| `cursor/1M_high` | `3607aa0` | 121 | Base: completed v0.2 live experiments and latest fault fixes |
| `cursor/agentmpi-protocol-c18f` | `1b83421` | 292 | Safety donor: run fencing, roll call, crash resync, payload contracts |
| `cursor/gemini` | `95b83cd` | 116 collected | Historical intermediate; strictly behind 1M on the same line |
| `cursor/grok` | `959228d` | 36 | Early narrative/evidence snapshot; superseded |
| `cursor/opus_2` | `bf0da86` | 366 | Experimental and algorithmic donor |
| `cursor/opus_3` | `37f90bf` | 50 (+9 optional skips) | Publication and differential-testing donor |
| `cursor/sol_high` | `83ff09a` | 114 | First integrated v0.2 checkpoint; superseded |
| `cursor/sol_xhigh` | `afd56b1` | 34 | Lean dual-binding/formal snapshot; superseded |
| `cursor/sol_xhigh_new` | `8ba4fd5` | 121 | Historically coherent sibling of the selected v0.2 content |

Test totals are not directly comparable: some suites count parameterized cases,
some drive the CLI end to end, and some cover multiple retained runtimes.

## Scarce ideas retained

### From the selected v0.2 line

1. **Six-operation abstract device interface.** `append`, `match`, `cas`,
   `lease`, `scan`, and `clock` form a narrow waist tested over SQLite and
   decentralized POSIX devices (`src/ampi/device/`).
2. **Algebra- and residency-constrained collectives.** Operator declarations
   license schedules; context residency can reject a schedule even when the
   algebra permits it (`src/ampi/core/{ops,collectives}.py`).
3. **Persisted semantic upcalls.** A model-evaluated reduction suspends,
   records its operands and program counter, and resumes when the identical
   collective is reissued.
4. **Confirmed death distinct from suspicion.** Slow model turns make false
   suspicion routine. Administrative death is monotonic until respawn.
5. **Convergent shrink naming.** The survivor set, not a caller-local counter,
   names the repaired communicator.
6. **Trace-derived measurement.** Collectors query protocol state rather than
   accepting agent claims about what happened.
7. **Collective-free work bags.** E5 demonstrates that RMA claim idioms can be
   better than bulk-synchronous phases under a constrained executor pool.

### From Opus 2

1. **Context safety as a program property.** Eager credit and rendezvous are
   semantically visible because admitting content spends a rank's context.
2. **Variadic k-ary semantic reduction.** Agent ranks are not single-ported.
   With a variadic operator, one prompt can combine `k` children, making the
   widest context-admissible tree preferable to MPI's binary tree.
3. **Contributor erasure hidden by aggregate retention.** A bounded semantic
   reducer can retain an acceptable aggregate fraction while dropping entire
   ranks. Per-contributor retention is therefore required.
4. **Exact before semantic.** A deterministic union can match semantic merge
   quality at a fraction of cost; model calls are not a default reduction
   primitive.
5. **Campaign-controlled ablations.** One persistent worker population serves
   an interleaved condition ladder, reducing launch and population confounds.
6. **Interface-publication effect depends on specification precision.** Shared
   publication helps under vague contracts and can be redundant under precise
   contracts; there is no context-free “communication helps” result.
7. **Negative-result discipline.** Refuted fidelity hypotheses, weak USL fit,
   polysemy metric inversion, and whole-rank erasure remain in the record.

### From Opus 3

1. **Label-addressed collective lesson.** Agents retry and reorder steps;
   ordinal-only matching can turn one skipped operation into a permanent
   divergence. Labels are a candidate extension, not silently added semantics.
2. **Differential testing after suite saturation.** Both software arms passed
   174 held-out tests; grammar-generated differential cases exposed a narrow
   formatting defect the suite missed.
3. **Protocol re-emergence in the ablation.** Seven of eight ranks hand-built
   allgather-like interface publication, then invented runtime probing. This is
   evidence for the abstraction and against claiming the current API is
   complete.
4. **Macro-sealed paper and PDF checks.** Numbers flow from JSON through
   generated TeX; a PDF checker catches source tables silently omitted by
   layout.
5. **Instrumented naive baseline.** The baseline retains measurement hooks and
   equal access to the filesystem, avoiding an unobservable control arm.

### From the protocol branch

1. **Run identity is not a path.** Every envelope is fenced by immutable
   `run_id`, and creating over live state is refused.
2. **Never joined is not stopped heartbeating.** A short, configurable roll-call
   deadline classifies launcher shortfall; joined ranks retain a long failure
   timeout suitable for model turns.
3. **Write-ahead collective state.** Issuance is durable before traffic so a
   restarted rank can use peer evidence to re-enter or skip.
4. **Self-identifying payload contracts.** Scatter and receive can assert
   assignment identity instead of trusting that an opaque body is intended for
   the caller.
5. **World/local rank separation.** Sub-communicator envelopes preserve both
   transport-global and communicator-local identities.

The first two were implemented in the consolidation milestone and are covered
by regression tests. The others remain explicit design candidates.

## Ideas intentionally not merged

- Three legacy runtimes are retained only to reproduce cited measurements; they
  are not treated as three conforming v0.2 implementations.
- Full `agent-tools/` logs and transport inbox trees are not source artifacts.
- A 100-rank Aesop file without executor provenance is a dry run, not a live
  agent scaling result.
- “Semantic reduction” is not consensus. Revoke/shrink/agree assumes a known
  participant set and crash-style faults.
- Leases without storage-enforced fencing are not sufficient for external side
  effects.
- Thread-based or in-process rank simulations do not establish process,
  transport, or model scaling.
- A consistency metric is not a correctness metric: globally consistent
  terminology can still be contextually wrong.

## Evidence model for subsequent experiments

Every result declares one executor kind:

1. analytical simulation — schedule predictions only;
2. scripted OS ranks — protocol correctness and implementation cost;
3. transcript replay — runtime comparison under fixed logical behavior;
4. live model ranks — end-to-end behavior.

The unit of replication is a whole multi-rank run. Future treatment studies
must use equal information, tools, model settings, role prompts, task
partitions, call budgets, and launch policy. The control is not deliberately
crippled: an ad-hoc mailbox/lock baseline may transmit the same payloads but
lacks the protocol mechanism under test. Conditions should be interleaved
within task/model/rank-count blocks, with failed runs retained in
intention-to-treat analysis.

Primary protocol invariants are binary and every violation is reported:
non-overtaking, exactly-one match, communicator/run isolation, collective
signature agreement, consistent repaired membership, stale-incarnation
rejection, fenced commit uniqueness, and artifact integrity.

## Immediate consolidation milestones

1. Run identity, live-store refusal, roll-call classification, and respawn
   reset semantics — implemented and tested.
2. Add replayable canonical envelopes and spec/runtime consistency tests.
3. Port variadic k-ary reduction with per-contributor retention accounting.
4. Port symmetric differential tests and generated-claim/PDF validation.
5. Build repeated equal-information translation and software blocks.
6. Add a true distributed transport before making network-partition claims.
7. Seek a second independent implementation and governance process before
   calling AgentMPI a standard.

## Naming

“AMPI” is established in HPC as Charm++'s Adaptive Message Passing Interface.
The project name is written **AgentMPI**. The current lowercase `ampi` command
and `AMPI_*` symbols are prototype prefixes; an eventual ABI must adopt an
unambiguous namespace.
