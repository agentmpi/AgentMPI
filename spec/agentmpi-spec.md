# The AgentMPI protocol, version 0.1

This document specifies AgentMPI. It is normative: an implementation
conforms if it provides these operations with these semantics. It does not
specify an implementation, a transport, a scheduling policy, or anything
about what agents do — those are deliberately out of scope, for the same
reason MPI declines to specify them.

Keywords **MUST**, **MUST NOT**, **SHOULD** and **MAY** are used in the
RFC 2119 sense.

---

## 1. Model

A **job** is a fixed-membership set of **ranks** cooperating through a shared
**run**. A rank is an *identity*, not a process: an index within a
communicator plus a `RankSpec` describing its role, model, provider, host,
context store, context capacity, price and tool permissions. A rank MAY be
implemented by any number of processes over its lifetime; implementations
MUST NOT assume a rank is one long-lived process.

A **communicator** is an ordered **group** of ranks together with an opaque
**context**. The central guarantee:

> **C1.** A message sent on one communicator MUST NOT be matched by a receive
> on any other communicator.

A rank's index within a communicator is its **local rank**; its index in
`AMPI_COMM_WORLD` is its **world rank**. Envelopes carry both: matching and
ordering are defined over local ranks, transports address world ranks.

### 1.1 Resources

Three resources are first class and MUST be accounted:

| Resource | Unit | Property |
|---|---|---|
| **Context** | tokens | *Cumulative*: consumed on ingest, never released except by explicit compaction |
| **Turns** | count | An agent think-act step; the dominant cost |
| **Currency** | implementation-defined | Monotone, bounded per rank |

Context is the one that changes the protocol. Unlike a memory buffer, a
token that a rank has ingested is spent for the remainder of that rank's
life.

---

## 2. Identity and lifecycle

```
AMPI_Init(root, rank [, size, spec, cvars]) -> Runtime
AMPI_Finalize()
AMPI_Abort(comm, code)
AMPI_Comm_rank(comm) -> int
AMPI_Comm_size(comm) -> int
AMPI_Wtime() -> float
```

`AMPI_Init` MUST be idempotent and resumable: a rank that has previously
initialised, exited, and initialised again with the same `(root, rank)` MUST
rejoin the same job at the current epoch, and MUST NOT re-consume any
message it previously consumed.

Identity resolution order MUST be: explicit argument, then environment
(`AMPI_ROOT`, `AMPI_RANK`, `AMPI_SIZE`), then the run manifest.

A rank is in exactly one **state**: `unborn`, `init`, `active`, `blocked`,
`finalized`, `failed`, `stalled`, `drifted`, `bankrupt`, `evicted`.

---

## 3. Groups and communicators

```
AMPI_Comm_group(comm) -> Group
AMPI_Group_incl / _excl / _union / _intersection / _difference / _translate_ranks
AMPI_Comm_create(comm, group) -> Comm | null
AMPI_Comm_dup(comm) -> Comm
AMPI_Comm_split(comm, colour, key) -> Comm | null
AMPI_Comm_split_type(comm, type, key) -> Comm | null
AMPI_Comm_free(comm)
AMPI_Intercomm_create(comm, remote_group) -> Comm
AMPI_Intercomm_merge(intercomm, high) -> Comm
```

Groups are local objects; manipulating one requires no communication.
`AMPI_Comm_dup` and `AMPI_Comm_split` are collective.

`AMPI_Comm_split_type` types: `MODEL`, `PROVIDER`, `HOST`, `STORE`, `ROLE`.
It partitions the communicator by ranks sharing a resource with coherent
cost, and is the analogue of MPI's `MPI_COMM_TYPE_SHARED`.

**Deterministic context derivation.** An implementation MAY derive a child
communicator's context identifier from the parent's context and a replicated
per-communicator collective counter, avoiding an extra round trip. This is
sound only because §5 requires collectives to be issued in a consistent
order on every rank.

---

## 4. Datatypes

A datatype answers three questions: how a payload is rendered into an
agent's context (**layout**), what the receiver may assume (**contract**),
and how many tokens it may cost (**bound**).

Predefined: `AMPI_TEXT`, `AMPI_JSON`, `AMPI_PATCH`, `AMPI_ARTIFACT`,
`AMPI_TOOLCALL`, `AMPI_DIGEST`, `AMPI_NULL`.

```
AMPI_Type_contract(base, schema, validators) -> Datatype
AMPI_Type_bounded(base, max_tokens, digest, lossy) -> Datatype
AMPI_Type_struct(fields, required) -> Datatype
AMPI_Type_contiguous(count, base) -> Datatype
```

* A sender MUST validate a payload against its datatype's contract before
  sending, and MUST raise `AMPI_ERR_CONTRACT` on violation.
* A receiver MUST re-check on arrival and MUST report violations in the
  status. It MUST raise `AMPI_ERR_CONTRACT` if `ampi_strict_contracts` is
  set.
* If a payload exceeds `max_tokens`, an implementation MUST apply `digest`
  when the type is lossy, and MUST raise `AMPI_ERR_CONTEXT_OVERFLOW`
  otherwise. A digested payload MUST remain valid for its base type; a
  digested JSON document MUST be wrapped in a well-formed envelope recording
  the loss.

---

## 5. Point-to-point

```
AMPI_Send / AMPI_Ssend / AMPI_Bsend / AMPI_Rsend (value, dest, tag, datatype)
AMPI_Recv(source, tag, datatype) -> (value, status)
AMPI_Isend / AMPI_Irecv -> Request
AMPI_Sendrecv(value, dest, source, sendtag, recvtag)
AMPI_Probe / AMPI_Iprobe / AMPI_Mprobe / AMPI_Mrecv
AMPI_Wait / AMPI_Test / AMPI_Waitall / AMPI_Waitany / AMPI_Waitsome
AMPI_Psend_init / AMPI_Pready / AMPI_Parrived
```

Wildcards: `AMPI_ANY_SOURCE`, `AMPI_ANY_TAG`. `AMPI_PROC_NULL` is a no-op
source and destination. Application tags are in `[0, AMPI_TAG_UB)`; the range
above is reserved for the runtime.

### 5.1 Matching rules

* **M1.** A receive matches the first available message with the same
  `(context, source, tag)`, wildcards permitting.
* **M2 (non-overtaking).** Two messages sent by the same rank to the same
  rank on the same communicator MUST be matched in send order.
* **M3 (deduplication).** Delivery is at-least-once. A message MUST be
  matched at most once per idempotency key.
* **M4 (epoch).** A message whose epoch precedes the receiver's current
  epoch for that context MUST NOT be matched.
* **M5 (admission).** A match completes only after the payload is charged
  against the receiver's context budget.
* **M6 (bounded ordering).** An implementation MAY, after
  `ampi_gap_timeout_s`, advance past a missing sequence number and MUST
  record that it did. This weakens M2 in exchange for liveness when a sender
  has died, and MUST be visible in the trace.

### 5.2 Send modes

| Mode | Completes when |
|---|---|
| standard | the runtime has taken ownership of the payload |
| buffered | immediately; the payload is spilled to the payload plane |
| **synchronous** | the destination has **ingested** the message into its context |
| ready | as standard; erroneous if no matching receive is posted |

Synchronous completion on *ingestion* is strictly stronger than MPI's
`MPI_Ssend`, which completes when the matching receive has begun.

### 5.3 Consumption

**Polling MUST NOT consume.** An implementation MUST distinguish observing a
message from matching it, and only matching may be durable. A rank whose
process exits after polling but before matching MUST find the message again
in its next process.

Consumption state MUST be bounded: an implementation SHOULD keep a
per-`(context, source, dest)` watermark plus the out-of-order exceptions
above it, rather than a set of identifiers.

---

## 6. Collectives

```
AMPI_Barrier(comm)
AMPI_Bcast(value, root [, relay])
AMPI_Scatter / AMPI_Scatterv (values, root)
AMPI_Gather / AMPI_Gatherv / AMPI_Allgather (value [, root])
AMPI_Reduce / AMPI_Allreduce / AMPI_Reduce_scatter (value, op [, root])
AMPI_Scan / AMPI_Exscan (value, op)
AMPI_Alltoall(values)
AMPI_Neighbor_allgather / AMPI_Neighbor_alltoall
```

* **K1.** Collectives on a communicator MUST be issued in the same order by
  every rank of that communicator.
* **K2.** An implementation MUST label its internal collective traffic with
  the collective's name and its per-communicator sequence number, and MUST
  raise `AMPI_ERR_COLL_MISMATCH` when a peer's label for a sequence number it
  has already executed disagrees. Where two or more peers agree against the
  local rank, the error MUST say so.
* **K3.** The collective sequence counter MUST be made durable **before** the
  messages it labels are sent. An implementation that persists it afterwards
  will replay a collective after a mid-operation crash and desynchronise
  silently.
* **K5.** Issuance and completion MUST be recorded as separate durable facts.
  A rank restarted inside a collective cannot otherwise tell whether to
  re-enter it or move past it, and both answers are wrong half the time.
* **K6.** A rank that finds a collective issued and not completed MUST
  resolve it from evidence, not assumption: if any peer durably recorded that
  collective as complete, it completed and the rank MUST advance past it;
  if no peer did, the rank MUST re-enter it with the same sequence number.
  The case that makes this necessary is a rootless collective, where a rank's
  messages can satisfy every peer before the rank itself returns, so the
  operation completes for the group while leaving the interrupted rank no
  local record of it.
* **K4.** Algorithm selection MUST NOT change an operation's result. Where an
  operator's declared algebra does not permit a schedule, the implementation
  MUST refuse the schedule rather than compute a different answer.

### 6.1 Operators

An `AMPI_Op` declares `commute`, `associative`, `idempotent` and an optional
`output_tokens` bound.

```
AMPI_Op_create(fn, commute, associative, idempotent, output_tokens, nary)
```

Predefined: `AMPI_CONCAT`, `AMPI_UNION`, `AMPI_FIRST`, `AMPI_LAST`,
`AMPI_MERGE_JSON`, `AMPI_VOTE`, `AMPI_MAXLOC`, `AMPI_PATCH_MERGE`,
`AMPI_SUM`, `AMPI_MAX`, `AMPI_MIN`, `AMPI_LAND`, `AMPI_LOR`.

* A **non-associative** operator MUST be evaluated by a flat reduction only.
* A **non-contracting** operator (no `output_tokens`, or a bound not smaller
  than its inputs) MUST NOT be used in a tree of depth greater than one.
* A **commutative** operator cannot express precedence and therefore MUST NOT
  be used for a collective whose purpose is agreement on a shared
  convention. Use `AMPI_FIRST`.

### 6.2 Capacity

Before running a collective an implementation SHOULD compute its peak
per-rank ingest and MUST report infeasibility rather than failing partway.

* `AMPI_Allgather` peak ingest is `(p-1)·s` at every rank, by definition.
* A `k`-ary reduction tree of depth `d` costs the root
  **`(k-1)·d·m`** tokens *cumulatively*, where `m` is the operator's output
  bound. Implementations MUST plan against this figure and not against the
  per-round figure `(k-1)·m`.

---

## 7. One-sided operations

```
AMPI_Win_create(comm, name) -> Win
AMPI_Put(win, key, value) -> Reference
AMPI_Get(win, key) -> Reference          # NOT content
AMPI_Win_materialize(win, ref [, budget]) -> value
AMPI_Accumulate(win, key, value, op) -> Reference
AMPI_Get_accumulate / AMPI_Fetch_and_op / AMPI_Compare_and_swap
AMPI_Win_index(win) -> [{key, tokens, version, owner, preview}]
AMPI_Win_query(win, question, budget) -> {returned, tokens, omitted}
AMPI_Win_fence(win)                       # collective epoch
AMPI_Win_post / _start / _complete / _wait   # scoped epoch
AMPI_Win_lock / _unlock / _flush             # passive target
```

* **W1.** `AMPI_Get` MUST return a reference, not content. Materialisation is
  a separate operation and MUST charge the caller's context budget.
* **W2.** `AMPI_Accumulate`, `AMPI_Fetch_and_op` and `AMPI_Compare_and_swap`
  MUST be atomic with respect to concurrent accesses to the same key.
* **W3.** Locks MUST be leased. A lock whose holder dies MUST become
  acquirable, and the theft MUST be recorded.
* **W4.** `AMPI_Win_query` MUST return within its token budget and MUST
  report what it omitted. It is a projection, not the truth.

---

## 8. Collective I/O

```
AMPI_File_open(comm, path, aggregators) -> File
AMPI_File_set_view(file, view)
AMPI_File_write_at_all(file, content, op)
AMPI_File_write_shared(file, content)
AMPI_File_read_all(file) -> content
```

`AMPI_File_write_at_all` MUST be two-phase: contributions are redistributed
to a deterministically elected aggregator per path, which performs a single
atomic publish. Exactly one rank writes each artifact.

Overlapping file views MUST be detected at `set_view` time.

---

## 9. Failure model

### 9.1 Detection

An implementation MUST distinguish **liveness** from **progress** using two
independent signals and two independent timeouts
(`ampi_failure_timeout_s`, `ampi_stall_timeout_s`). A rank that cannot emit
liveness while working MUST be reported as such, so that a harness can set
its timeout above the maximum turn duration.

### 9.2 Error classes

`AMPI_ERR_PROC_FAILED`, `AMPI_ERR_PROC_FAILED_PENDING`, `AMPI_ERR_REVOKED`,
`AMPI_ERR_STALLED`, `AMPI_ERR_CONTRACT`, `AMPI_ERR_DRIFT`,
`AMPI_ERR_BUDGET`, `AMPI_ERR_CONTEXT_OVERFLOW`, `AMPI_ERR_COLL_MISMATCH`,
`AMPI_ERR_TIMEOUT`, plus the MPI-derived classes.

Default error handler is `AMPI_ERRORS_RETURN`, not `AMPI_ERRORS_ARE_FATAL`.

### 9.3 Recovery

```
AMPI_Comm_revoke(comm)
AMPI_Comm_shrink(comm) -> Comm
AMPI_Comm_agree(comm, value, op) -> value
AMPI_Comm_failure_ack(comm) / AMPI_Comm_failure_get_acked(comm)
AMPI_Comm_replace(comm, spawn) -> Comm
AMPI_Checkpoint(comm, state) / AMPI_Restore(comm)
```

* **F1.** After `AMPI_Comm_revoke`, every operation on that communicator at
  every rank MUST terminate with `AMPI_ERR_REVOKED` rather than block. The
  revocation MUST survive the revoker's own death.
* **F2.** `AMPI_Comm_shrink` MUST agree on the survivor set before
  constructing the new communicator, and MUST **intersect** rather than union
  the proposals. The result MUST carry a new epoch.
* **F3.** `AMPI_Comm_agree` MUST return the same value at every surviving
  rank. Its decision MUST be written with compare-and-swap so that a second
  decider cannot overwrite one a peer has already read.
* **F4.** `AMPI_Comm_replace` preserves rank indices. The protocol does not
  specify how an executor is created; `spawn` is supplied by the launcher.
* **F5.** Checkpoints are local and uncoordinated. This is sound only
  because every message is durable and content-addressed, so a restarted
  rank reads what it missed instead of forcing peers to re-send.

---

## 10. Tools

### 10.1 Profiling (`PAMPI`)

Every operation MUST be interceptable, and the event stream MUST be
documented. The event model is states (`enter`/`leave` with a duration),
arrows (matched `send`/`recv` pairs, joined by idempotency key), counters,
and notes. Every event carries `tokens`.

Traces SHOULD be streamed rather than buffered: a buffered trace dies with
the rank that produced it.

### 10.2 Control and performance variables (`AMPI_T`)

Control variables include at least: `ampi_device`, `ampi_eager_chars`,
`ampi_strict_contracts`, `ampi_admission_control`, `ampi_auto_digest`,
`ampi_heartbeat_s`, `ampi_failure_timeout_s`, `ampi_stall_timeout_s`,
`ampi_gap_timeout_s`, `ampi_coll_mismatch_grace_s`, `ampi_coll_algorithm`,
`ampi_context_capacity`, `ampi_context_reserve`, `ampi_lifetime_tokens`,
`ampi_currency_budget`.

Performance variables include at least: messages, tokens sent and ingested,
tokens digested, context pressure, collective calls and rounds, turns,
stalls and failures detected, contract violations, retries, cache hits,
currency spent.

---

## 11. Launch

Out of scope, deliberately. A conforming launcher MUST provide each rank
with `AMPI_ROOT` and `AMPI_RANK`; everything else MUST be discoverable from
the run.

A launcher that cannot supply `p` executors MUST NOT start the job. A
collective's all-participants requirement makes launcher capacity a
*correctness* property: a partially fulfilled request produces a job that
starts, looks healthy, and never finishes.

---

## 12. Bindings

A conforming implementation MUST provide a command-line binding, so that any
process able to run a shell command can be a rank. Errors MUST be emitted on
stderr as JSON with a machine-readable `error` field.

---

## Appendix A. Differences from MPI

| Area | MPI | AgentMPI |
|---|---|---|
| Buffer | reusable; peak matters | cumulative; total matters |
| Datatype | layout | layout + contract + token bound |
| `Ssend` | receive has begun | receiver has ingested |
| `Get` | returns content | returns a reference |
| Operators | associative, commutative, size-preserving | declared algebra; schedules refused if unsound |
| Failure | fail-stop | crashed, stalled, drifted, bankrupt |
| Recovery | shrink | replace preferred; shrink as fallback |
| Checkpoint | coordinated | uncoordinated; journal removes the domino effect |
| Cost | `α·messages + β·bytes` | `γ·turns`, with a hard feasibility constraint |
| Default errhandler | fatal | return |
| Endpoint | one long-lived process | a succession of short-lived processes |
