# Agent Message Passing Interface 0.1

Status: research draft. Normative terms **MUST**, **SHOULD**, and **MAY** follow
RFC 2119.

## 1. Purpose and non-goals

AgentMPI is a portable coordination interface for programs whose executors are
AI agents. It specifies observable communication, membership, synchronization,
flow-control, failure, and tracing behavior. It deliberately does not specify:

- the model, vendor, prompt, planner, or reasoning algorithm;
- the representation of private executor state;
- a task ontology or universal agent-to-agent natural language;
- a required transport, broker, scheduler, or deployment environment;
- agreement on arbitrary facts, Byzantine consensus, or exactly-once effects.

The distinction follows MPI: an interface constrains what applications can
observe while allowing implementations to optimize transports and algorithms.

## 2. Abstract model

A *session* is an isolated allocation domain. A session contains executor
records, communicators, messages, requests, artifacts, collectives, locks, and
an event trace. Resources from separate sessions MUST NOT be mixed.

An *executor* is a fallible stateful process capable of consuming a bounded
context, calling protocol operations, and producing values or external effects.
It has a world rank, incarnation, state, heartbeat lease, context budget, and
capabilities. A model invocation, long-lived agent loop, human, or deterministic
program can serve as an executor.

A *communicator* is:

```
Comm = (session_id, context_id, generation, ordered_membership, attributes)
```

Ranks are indices/names within its immutable ordered membership. The
`context_id` prevents messages from one library/team/workflow from matching
another even when source, destination, and tag coincide. A repair creates a new
generation; it never silently mutates an old communicator.

A message envelope is:

```
Envelope = {
  protocol_version, message_id, session_id, communicator_id, generation,
  source_rank, source_incarnation, destination_rank, tag, sequence,
  delivery_mode, content_type, schema_id?, payload_inline?, artifact_ref?,
  payload_bytes, estimated_tokens, deadline?, trace_context?,
  capability_proof?, checksum, created_at
}
```

The reference implementation currently persists the semantic subset and derives
checksums for artifacts. Production bindings MUST authenticate immutable header
fields and SHOULD encrypt confidential values.

## 3. Point-to-point operations

### 3.1 Matching

`Send(value, destination, tag, communicator)` matches
`Recv(source, tag, communicator)`. Source may be `ANY_SOURCE`; tag may be
`ANY_TAG`. Destination and communicator are never wildcards.

For a fixed `(communicator, generation, source, destination, tag)`, matching
messages MUST NOT overtake. Wildcards, concurrent receive threads,
`WaitAny`-style operations, cancellation, retries, and executor nondeterminism
can make an application nondeterministic. Implementations MUST record the
actual match.

No fairness is implied by the MPI-compatible core. A quality implementation
SHOULD offer fair-queue attributes because expensive long-lived agents make
starvation operationally dangerous.

### 3.2 Delivery modes

| AgentMPI mode | Completion condition | Intended use |
|---|---|---|
| standard | implementation safely accepts the value | ordinary tasks/results |
| buffered | caller-provided/runtime quota accepts a copy | decouple bursts |
| synchronous | receiver matches and acknowledges | phase handoff/backpressure |
| ready | matching receive was already posted | optimized known schedule |

Send completion does not imply task completion. Receive completion does not
imply that downstream external effects are committed. Applications needing
effect atomicity MUST use idempotency keys or a transactional service.

### 3.3 Requests and progress

Nonblocking operations return opaque requests. `Test`, `Wait`, `WaitAny`,
`WaitSome`, and `WaitAll` drive or observe completion. A portable application
MUST tolerate weak progress: an implementation may require periodic protocol
calls to advance transfers or failure knowledge. A binding MAY provide strong
progress using a broker, scheduler, or background thread, and MUST expose this
attribute.

Timeout is an application policy, not proof of executor death. A timeout MAY
trigger suspicion; only the configured failure detector changes membership
state.

### 3.4 Probe and cancellation

`Probe` inspects a matching envelope without consuming it. `MatchedProbe`
reserves that exact envelope. Cancellation succeeds only if matching/transfer
has not crossed the implementation's documented commit point. The result MUST
state whether cancellation succeeded.

## 4. Collective operations

Every member invokes collectives in the same order on a communicator.
`(communicator, generation, operation, epoch)` identifies one instance. Mismatched
order is a protocol error and SHOULD produce a diagnostic rather than hang.

| MPI concept | AgentMPI operation | Agent-harness meaning |
|---|---|---|
| barrier | `Barrier` | no member begins the next phase early |
| broadcast | `Bcast` | one policy/glossary/plan reaches all members |
| scatter | `Scatter` | root distributes one partition per member |
| gather | `Gather` | root collects rank-ordered products |
| allgather | `Allgather` | every member obtains all summaries/proposals |
| reduce | `Reduce(op)` | root obtains deterministic aggregate |
| allreduce | `Allreduce(op)` | every member obtains aggregate |
| all-to-all | `Alltoall` | personalized peer review/exchange |
| scan | `Scan(op)` | prefix state, useful for ordered document context |

Reductions MUST define a value schema, identity, associativity, and conflict
policy. Natural-language "merge" is neither associative nor deterministic and
therefore is not a core reduction. It is an application task implemented by a
designated executor after a deterministic `Gather`. Built-in prototype
operators are numeric sum/product/min/max, boolean all/any, list concatenation,
conflict-detecting map merge, and set union.

Strict collectives wait for every live member. Optional quorum collectives MUST
use distinct names/types and return the participating rank set; silently
weakening a barrier or allreduce is forbidden.

### 4.1 Algorithm selection

Bindings MAY choose algorithms by measured agent cost:

```
T = critical_path_model_latency
  + alpha * communication_rounds
  + beta_bytes * transferred_bytes
  + beta_tokens * materialized_tokens
  + gamma * merge_work
  + expected_recovery_cost
```

Small broadcasts favor binomial trees (`O(log p)` rounds). Large values favor
scatter plus ring allgather. Allreduce may use recursive doubling for small
values or reduce-scatter plus allgather for large values. Agent hierarchies
should aggregate summaries locally before crossing expensive model/team
boundaries. These optimizations cannot alter rank order or result semantics.

Persistent collectives bind a repeated topology, schemas, prompts, and resource
reservations once, then execute multiple epochs. Partitioned communication lets
an agent mark sections of a large artifact ready independently.

## 5. Lifecycle and membership

The executor state machine is:

```
ALLOCATED -> JOINING -> ACTIVE -> DRAINING -> FINALIZED
                         |   ^
                         v   |
                      SUSPECT
                         |
                         v
                       FAILED
```

`Join` authenticates the executor and increments an incarnation number.
Messages from stale incarnations MUST be rejected after replacement. `Finalize`
is graceful and releases resources after outstanding operations satisfy the
configured policy. `Spawn` creates named child process sets and an
intercommunicator; protocol users cannot assume a global `WORLD`.

Sessions permit independent libraries/workflows to initialize and finalize
without global coordination. Process sets are queryable resource groups from
which communicators are created.

## 6. Failure model and repair

The core assumes crash-stop executors and an eventually accurate lease failure
detector. Byzantine executors are outside the core. Detection is local and
asynchronous; members can temporarily disagree about failures.

AgentMPI adopts the ULFM repair pattern:

1. `AckFailed` records locally observed failures.
2. `Revoke(comm)` makes pending/future ordinary operations fail promptly with
   `COMM_REVOKED`, propagating recovery intent.
3. `Agree(comm, flag)` performs failure-tolerant boolean agreement among
   survivors.
4. `Shrink(comm)` creates a fresh communicator generation with a consistently
   ordered survivor set.
5. The application restores lost state from messages/artifacts/checkpoints,
   redistributes incomplete tasks, or substitutes spare executors.

The protocol repairs communication capability, not application state. A task is
replayable only when its inputs and side effects are recorded and idempotent.
The event trace SHOULD retain assignment attempt, executor incarnation,
artifact checksum, completion, and commit.

Speculative duplicate execution uses one logical task ID and multiple attempt
IDs. Exactly one fenced commit wins; other attempts are cancelled or discarded.

## 7. Synchronization and shared state

Message passing is preferred for ownership transfer. Where external mutable
state is unavoidable, `Lock(name, lease)` returns a monotonically increasing
fencing token. Every protected write MUST present that token to a storage system
that rejects tokens older than the latest accepted token. A lease without
fencing is unsafe: a paused executor may resume after expiration and overwrite
its successor.

One-sided operations can expose typed windows of artifacts or key-value state:
`Put`, `Get`, `Accumulate`, compare-and-swap, fetch-and-add. A window declares
public/private epochs and memory consistency. This draft implements fenced
locks but leaves interoperable RMA for a later version.

## 8. Context and memory safety

GPU/host OOM in HPC has an agent analogue: context-window, mailbox, model quota,
and harness-process exhaustion. AgentMPI makes these explicit resources.

Each destination advertises credits in bytes, inline tokens, message count, and
optional cost units. Senders MUST NOT exceed accepted credits. A receiver
materializes values against a context budget. Large values are stored once as
content-addressed artifacts; messages carry bounded metadata, summaries, and
references. Fetching an artifact is explicit and separately charged.

Recommended hierarchy:

1. fixed-size authenticated envelope;
2. bounded inline control value;
3. short application summary with provenance;
4. immutable artifact reference;
5. selective ranges/chunks fetched on demand.

`Compact(checkpoint)` releases context credits only after durable essential
state exists. Dropping context without a checkpoint is application-visible data
loss. Implementations SHOULD report high-water marks and rejected sends.

## 9. Isolation, security, and provenance

Communicators are capability boundaries, not merely integer groups.
Implementations MUST prevent cross-session/context matching and source-rank
spoofing. Each message SHOULD carry a content schema, sender identity and
incarnation, integrity digest, trace context, and delegated capability set.

Receiving text never grants authority. Prompts and artifact content are
untrusted data. Tool-capable executors MUST intersect delegated capabilities
with local policy. Reductions over untrusted members require validation,
provenance, and (for adversarial settings) robust aggregation outside this core.

Replay protection combines unique message IDs, sender incarnation, monotonically
increasing sequence numbers, and retention windows. Audit logs SHOULD be
append-only and redact secret payloads while retaining hashes and metadata.

## 10. Observability

Every implementation exposes a profiling interface analogous to PMPI. Required
events include session and executor lifecycle, communicator creation/revocation/
repair, message enqueue/match/ack, collective enter/exit, context admission,
artifact put/get, lock acquire/release, timeout, and failure observation.

Events carry monotonic local sequence, wall time, rank/incarnation, operation
identity, byte/token counts, outcome, and distributed trace context.
Conformance concerns semantic traces rather than internal algorithm steps.

## 11. Reference transport

The Python runtime maps protocol objects to SQLite tables in WAL mode. Short
`BEGIN IMMEDIATE` transactions linearize enqueue, match, collective contribution,
membership transition, and lock ownership. Payloads above the inline threshold
are SHA-256-addressed files atomically renamed into an adjacent artifact store.

This is an executable semantic oracle for local harness development. It offers:

- independent OS processes without a resident broker;
- durable restart inspection and deterministic event export;
- standard/synchronous/ready send modes and wildcard receive;
- barrier, broadcast, scatter, gather, allgather, reduce, allreduce, and agree;
- failure injection, revoke/shrink, heartbeat leases, bounded context/mailboxes;
- lease locks with fencing tokens.

It does not yet offer network authentication, RMA windows, true asynchronous
requests, optimized tree/ring collectives, or Byzantine tolerance.

## 12. Conformance

A conforming binding publishes:

1. protocol version and supported operations;
2. progress and thread-safety levels;
3. delivery durability and cancellation commit points;
4. limits and credit units;
5. failure detector and repair behavior;
6. collective algorithm/result-order guarantees;
7. security identity and capability model;
8. a machine-readable event trace.

Required tests cover non-overtaking, communicator isolation, wildcard match
recording, all collective results and epoch mismatch diagnostics, mailbox/context
admission, artifact integrity, executor death during each operation, revoke
termination, consistent shrink membership, stale-incarnation rejection, fenced
lock takeover, duplicate task commit, and trace completeness.

## 13. Versioning

Minor versions add optional operations or fields. Major versions may alter
semantics. Unknown optional envelope fields are ignored; unknown required
features fail negotiation. Implementations negotiate at session creation and
record the result in traces.
