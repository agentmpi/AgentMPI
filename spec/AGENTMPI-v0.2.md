# AgentMPI 0.2 --- Protocol Specification

**Status:** research draft. Supersedes the 0.1 drafts in `SPEC.md` (SQLite
profile) and `spec/AGENTMPI.md` (filesystem profile), which remain in the
repository because the measurements they produced are cited in the paper.

**Conformance language.** MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are used
in the RFC 2119 sense. An implementation conforms to a *level* if it
implements every MUST of that level and every level below it.

---

## 0. What this document specifies, and what it deliberately does not

AgentMPI is an interface, not a system. It says what an operation means and
what a program may rely on. It says nothing about how agents are started, which
model runs inside them, how they are prompted, or how bytes move between them.
That separation is copied deliberately from MPI, where it is the reason a
program written in 1994 still runs, and where `mpirun` --- the part everybody
touches --- was left outside the standard on purpose.

Three things follow, and they are the whole design.

**Interface, not implementation.** Every operation below is defined by its
observable effect on protocol state. An implementation MAY use any transport,
any storage, and any algorithm whose observable effect matches. The reference
implementation ships two transports precisely so this claim can be tested.

**Explicit, not inferred.** A harness states that a phase boundary exists
(`AMPI_Barrier`), that an artifact is shared (`AMPI_Win`), that a work item is
taken (`AMPI_Win_claim`), that a reduction is associative. AgentMPI never
infers these from prompt text. The cost of inference is that failures become
undebuggable; the cost of explicitness is that the harness author must say what
they mean, once.

**Standardise existing practice.** Every operation here exists because agent
harnesses already do it by hand: fan out work, merge results, share a
scratchpad, take a lock on a file, retry a dead worker, summarise to fit a
context window. AgentMPI does not propose new coordination ideas. It gives the
existing ones names, semantics, and an implementation boundary.

### The three departures from MPI

Where AgentMPI differs from MPI, it is for a stated reason, not for novelty.

1. **The receive buffer is a finite, shared, monotonically filling context
   window.** MPI's receive buffer is supplied by the application and overflow
   is a programmer error. Ours is the model's context, it is shared by
   everything the agent has ever received, and overflowing it does not raise an
   error --- it silently degrades reasoning. Context is therefore a
   first-class, accounted, flow-controlled resource (§7).

2. **Participants are unreliable interpreters of the protocol.** MPI assumes
   correct programs and leaves the interesting mistakes undefined. An LLM rank
   that skips a barrier or sends the wrong payload is a routine event, not a
   bug that is found once and fixed forever. Conditions MPI leaves undefined
   --- mismatched collectives, wait-for cycles --- are named errors here (§9).

3. **Operators may be evaluated by a model, and may not be associative.** MPI
   requires associativity and may therefore always use a tree. AgentMPI accepts
   operators that are neither associative nor commutative, and pays for them in
   depth. The declared algebra constrains the admissible schedules (§6.2).

---

## 1. Model

A **job** is a set of **ranks** that share a **device**. A rank is one
participant: a process running an agent loop, a scripted process, or a thread.
Rank identity is a small dense integer within a communicator.

A rank has an **incarnation** (`generation`), incremented when a failed rank is
replaced. Protocol state addressed to rank *r* generation *g* MUST NOT be
delivered to rank *r* generation *g+1* as though it were live; a replacement
recovers deliberately, through §10.3, not by accident.

A **communicator** is an ordered group of ranks plus an opaque **context id**.
Two communicators over the same group MUST NOT be able to intercept each
other's messages. This is the mechanism that makes an AgentMPI library
composable with the agent code that calls it: a component duplicates the
communicator it was handed and its traffic becomes unreachable from its
caller's, even though the participants are identical.

A **payload** is text or JSON. Its size is measured in **tokens**. An
implementation MUST use a token estimator that is deterministic, monotone
non-decreasing in input length, and identical across all ranks of a job; it
MUST NOT depend on any particular model's vocabulary for correctness.

### 1.1 Abstract device interface

The portable layer is expressed over six device capabilities. An implementation
of the device layer MUST provide exactly these semantics; everything above is
device independent.

| capability | contract |
|---|---|
| `append` | durable, totally ordered insertion into a named stream |
| `match` | atomically claim the first record satisfying a predicate; a record MUST be claimed by at most one claimant |
| `cas` | compare-and-swap on a named cell against an expected version |
| `lease` | time-bounded shared or exclusive ownership of a named cell, which MUST expire |
| `scan` | predicate query that does not consume |
| `clock` | a timestamp shared by all ranks |

The atomicity of `match` and `cas` are the two properties everything else
rests on, and they are the two an implementation is most likely to get subtly
wrong. A conforming implementation MUST demonstrate them under concurrency,
not by inspection.

---

## 2. Lifecycle

| operation | meaning |
|---|---|
| `AMPI_Init(rank, role?, ctx_limit?)` | join the job. MUST be idempotent, and MUST increment the rank's **incarnation** (see below). |
| `AMPI_Finalize(note?)` | leave cleanly. Distinguishable from a crash. |
| `AMPI_Heartbeat(expect_idle?)` | assert liveness, optionally declaring a period during which the rank will be busy and silent. |
| `AMPI_Respawn(rank)` | reset a failed rank so a replacement may take its place; increments its generation. |

**Incarnation fencing.** A rank is a name, and any process holding the job can
claim it, so a call left blocked by an abandoned attempt will happily consume a
later attempt's messages. We observed exactly that: a stale root matched the
next run's contributions and completed a reduction mixing two generations of
ranks, producing a result that looked complete and was not trustworthy. Every
`AMPI_Init` MUST therefore increment the rank's incarnation, and a long-running
operation MUST capture the incarnation it began under and abort with
`AMPI_ERR_STALE_INCARNATION` if it changes. This is the fencing-token pattern,
and it is required rather than advisory because the failure it prevents is
silent.

`AMPI_Respawn` is promoted to a first-class recovery action, unlike MPI's
vestigial `MPI_Comm_spawn`. The reason is economic: starting a process on an
HPC batch system is slow and entangled with the resource manager, while
starting an agent is an API call. What failed in MPI-2 for good reasons
succeeds here for the same reasons inverted.

---

## 3. Communicators, groups, topologies

| operation | meaning |
|---|---|
| `AMPI_Comm_create(name, members)` | build a communicator over explicit ranks; MUST allocate a fresh context id |
| `AMPI_Comm_dup(source, name)` | same group, fresh context id |
| `AMPI_Comm_split(source, colors, keys)` | MPI colour/key semantics; a colour of `AMPI_UNDEFINED` excludes the rank |
| `AMPI_Cart_create(dims, periods)` | grid topology; `AMPI_Cart_shift` yields the (source, dest) pair for a halo exchange |
| `AMPI_Dist_graph_create(adjacency)` | explicit neighbour topology |
| `AMPI_Comm_revoke(comm)` | see §10.1 |
| `AMPI_Comm_shrink(comm, name)` | see §10.2 |

Communicator construction is **not** collective in AgentMPI. MPI requires
every member to call it; we evaluate it centrally from an explicit member list
or colour map. The resulting groups are identical. The difference is that a
silent rank cannot stall communicator creation for everyone else, which is a
deliberate weakening of MPI's collectivity requirement in exchange for
robustness against participants that do not reliably make their calls.

---

## 4. Point-to-point

| operation | meaning |
|---|---|
| `AMPI_Send(comm, dst, tag, payload, projection?)` | buffered, durable, returns once the message is durable |
| `AMPI_Ssend(...)` | returns only once the message has been matched |
| `AMPI_Recv(comm, src, tag, timeout, deref?)` | blocking receive; `src` MAY be `AMPI_ANY_SOURCE`, `tag` MAY be `AMPI_ANY_TAG` |
| `AMPI_Iprobe(comm, src, tag)` | inspect the envelope without consuming |
| `AMPI_Sendrecv(...)` | paired exchange that cannot deadlock against a symmetric partner |
| `AMPI_Deref(handle)` | materialise a payload delivered by reference (§7.2) |

**Matching.** A posted receive matches the message with the smallest arrival
order among those satisfying `(comm, dst, src?, tag?)`. A message MUST be
matched by at most one receive.

**Non-overtaking.** Two messages with the same `(comm, src, dst, tag)` MUST be
matched in the order they were sent. Messages with different tags MAY be
matched out of order; a receive naming a tag MAY skip earlier messages with
other tags. This is MPI's rule unchanged, and collectives implemented over
point-to-point depend on it.

**Buffering is specified.** MPI deliberately leaves it unspecified whether a
standard-mode send buffers, so that portable programs cannot rely on it. The
consequence is the classic deadlock where two ranks each send before receiving
and the program works on one machine and hangs on another. AgentMPI specifies
unbounded, durable buffering: the store must be durable anyway for crash
recovery, so buffering is free, and the eager-send deadlock is a hazard an LLM
agent has no way to diagnose. `AMPI_Ssend` remains available when the
application genuinely wants synchronisation.

**Registered waits.** Before blocking, a rank MUST record its outstanding
receive. This is what makes the wait-for graph in §9.2 constructible.

---

## 5. One-sided operations

An `AMPI_Win` is a named, versioned, key-addressed shared artifact space --- the
blackboard that every agent framework reinvents, with the parts they leave out.

| operation | meaning |
|---|---|
| `AMPI_Win_create(comm, name)` | create or attach |
| `AMPI_Put(win, key, value, if_version?)` | write; with `if_version` this is compare-and-swap |
| `AMPI_Get(win, key)` | read; returns the value and its version |
| `AMPI_Accumulate(win, key, value, op)` | atomic read-modify-write under a structural operator |
| `AMPI_Fetch_and_op(win, key, delta)` | atomic counter; the work-queue primitive |
| `AMPI_Win_claim(win, key)` | compare-and-swap a work item from unclaimed to claimed-by-me |
| `AMPI_Win_lock(win, key, mode, ttl)` | acquire a shared or exclusive **lease** |
| `AMPI_Win_unlock(lock_id)` | release |
| `AMPI_Win_fence(win, comm)` | barrier plus epoch boundary: every write before it is visible to every read after it |

**Locks are leases and MUST expire.** MPI locks are held until released, so a
holder that dies wedges the window. Agents die routinely. The TTL is the
maximum time the protocol is willing to be blocked by a holder that has stopped
making progress.

**`AMPI_Win_claim` is not a convenience.** Two agents picking up the same task
is the single most reported coordination failure of multi-agent harnesses, and
it is exactly what an atomic claim rules out. A harness that takes work by
convention takes it by however many agents happen to look at the same moment.

---

## 6. Collectives

### 6.1 Operations

`AMPI_Barrier`, `AMPI_Bcast`, `AMPI_Scatter`, `AMPI_Gather`, `AMPI_Allgather`,
`AMPI_Alltoall`, `AMPI_Reduce`, `AMPI_Allreduce`,
`AMPI_Reduce_scatter_block`, `AMPI_Scan`.

Only `AMPI_Barrier` is guaranteed to synchronise. A program that relies on any
other collective to synchronise is incorrect, exactly as in MPI.

**Collective ordering.** Ranks MUST issue collectives on a communicator in the
same order. Unlike MPI, an implementation MUST detect a violation and raise
`AMPI_ERR_COLLECTIVE_MISMATCH` rather than hanging. Each rank keeps a private
sequence counter per communicator; the first rank to reach sequence *k* records
which collective it is and every later arrival at *k* must agree.

**Recovering from a mismatch.** Detection is necessary but not sufficient. A
slot's operation is fixed by whichever rank arrives first, so one rank issuing
the wrong collective makes that slot permanently unusable for every other rank,
and the conforming ranks are the ones penalised. An implementation MUST
therefore provide `AMPI_Comm_resync(comm)`, which abandons the in-flight
collectives on a communicator and restarts every rank's sequence counter above
every slot ever used. It is administrative rather than collective, deliberately:
requiring agreement to escape a state that blocks agreement is not a recovery
path. Callers SHOULD agree out of band on which collective was intended before
using it, and MUST then re-issue that collective.

This is not a hypothetical. In a measured eight-rank run one agent issued
`reduce` where the other seven issued `allreduce`; five subsequent ranks
conformed to the mistake rather than to their instructions, and the job had no
way forward. Shrinking would have discarded live ranks and their work for what
was not a failure at all.

**Datatype.** A reduction declares whether its operator applies to the payload
as a whole (`scalar`) or element-wise to a keyed collection (`vector`). This is
MPI's count-and-datatype argument. Only vector payloads may be partitioned, so
this declaration decides whether the reduce-scatter family is available.

### 6.2 Operators and the algebra rule

An operator declares a `kind` (structural or semantic), and the flags
`associative` and `commutative`. These are normative inputs to algorithm
selection, not documentation.

- **Structural** operators execute inside the library: free, exact,
  reproducible. Predefined: `AMPI_CONCAT` (associative, not commutative),
  `AMPI_UNION`, `AMPI_BAG`, `AMPI_MERGE_JSON`, `AMPI_SUM`, `AMPI_MAX`,
  `AMPI_MIN`, `AMPI_MAXLOC`, `AMPI_MINLOC`, `AMPI_LAND`, `AMPI_LOR`,
  `AMPI_TOPK`, `AMPI_VOTE`.
- **Semantic** operators require a model. The library MUST suspend the
  collective and perform an **upcall** to the calling rank, handing it the
  operands and the operator's instructions; the rank submits a result with
  `AMPI_Op_submit` and re-issues the identical collective call to resume.
  Resumption MUST continue at the same step, not restart. Predefined:
  `AMPI_SYNTHESIZE`, `AMPI_RECONCILE`, `AMPI_SUMMARIZE`, `AMPI_CRITIQUE_MERGE`.

An implementation MUST NOT use a schedule that re-associates operands unless
the operator declares `associative`, and MUST NOT use a block-decomposed
schedule (ring, Rabenseifner) unless it also declares `commutative`.

`AMPI_VOTE` has a **finalize** step: it accumulates a commutative multiset and
projects to the mode, reporting the tally and agreement fraction. Pairwise
majority is not associative and MUST NOT be implemented pairwise.

### 6.3 Algorithm selection

An implementation SHOULD expose which schedules it considered and why it
rejected each. Selection MUST respect, in order:

1. re-association requires `associative`;
2. block decomposition additionally requires `commutative`;
3. the schedule's peak residency MUST NOT exceed the rank's context limit.

If no schedule is admissible the implementation MUST fail with an error naming
the constraint rather than proceeding with an unsound one.

### 6.4 Peak residency

Peak residency is the greatest number of tokens a rank must hold
simultaneously during a schedule. For a vector of *n* tokens over *p* ranks:

| schedule | depth | peak residency |
|---|---|---|
| linear | *p* | 2*n* |
| binomial tree | 2 lg *p* | 2*n* |
| recursive doubling | lg *p* | 2*n* |
| ring | 2(*p*−1) | 3*n*/*p* |
| Rabenseifner | 2 lg *p* | 2*n*/*p* |

A schedule that discards a block once it has been forwarded is what makes the
reduce-scatter rows achievable; an implementation that keeps every block
resident does not satisfy this table and MUST NOT report those residencies.

---

## 7. Context

### 7.1 Accounting

Each rank has `ctx_limit` and `ctx_used`. Delivering a payload MUST charge the
receiver. If the charge would exceed the limit the implementation MUST raise
`AMPI_ERR_CONTEXT_EXHAUSTED` rather than truncating: truncation is what an
unprotected harness does implicitly and is undetectable from inside the agent.

`AMPI_Ctx_release(tokens, reason)` is `free()`. An agent that compacts its own
history tells the runtime and its accounted occupancy drops. Without it the
accounting is monotone and every long run eventually reports exhaustion whether
or not the agent compacted.

### 7.2 Eager and rendezvous

MPI implementations switch from eager to rendezvous at a fixed byte threshold
chosen by the implementer, because the constraint is a pre-posted network
buffer. AgentMPI's switch MUST be driven by the **receiver's remaining
context** at the moment of transfer, because that is the resource at risk. The
same message MAY be delivered inline to a fresh rank and by reference to a
nearly-full one.

A rendezvous message carries a handle and a **structural digest**. The digest
MUST be computed without a model call, so the handshake itself is free. The
receiver decides whether to pay for the body with `AMPI_Deref`.

### 7.3 Projections

A projection is to an artifact what an MPI derived datatype is to a buffer: a
description of which part participates in a transfer, so the sender need not
materialise a smaller copy. Defined: `full`, `digest`, `schema`, `ref`.

---

## 8. Failure model

An AgentMPI rank can fail in five ways. Only the first is crash-stop, and only
the first is what MPI's fault literature assumes.

| mode | detectable by the runtime | mechanism |
|---|---|---|
| crash or silence | yes | failure detector (§10) |
| straggling | yes, but see below | declared deadlines |
| context exhaustion | yes | §7.1 |
| protocol violation | yes | §9 |
| confidently wrong output | **no** | `AMPI_VOTE`, application checks |

The last row is the honest boundary. The protocol can guarantee that every
contribution reaches the operator; it cannot guarantee that the operator keeps
them. A harness that needs that guarantee must build it above the protocol, and
`AMPI_VOTE` is the primitive offered for doing so.

---

## 9. Errors

| class | raised when |
|---|---|
| `AMPI_ERR_TIMEOUT` | an operation exceeded its deadline |
| `AMPI_ERR_REVOKED` | the communicator has been revoked |
| `AMPI_ERR_PROC_FAILED` | a peer required to complete the operation has failed |
| `AMPI_ERR_CONTEXT_EXHAUSTED` | delivery would exceed the receiver's budget |
| `AMPI_ERR_COLLECTIVE_MISMATCH` | ranks issued different or misordered collectives |
| `AMPI_ERR_DEADLOCK` | a wait-for cycle was detected |
| `AMPI_ERR_PROTOCOL_VIOLATION` | a call was made in a state that forbids it |
| `AMPI_ERR_STALE_INCARNATION` | another process has since claimed this rank |

The last four have no MPI counterpart. MPI leaves the corresponding
conditions undefined, which is a reasonable choice when participants are
compiled programs debugged once. It is not reasonable when participants are
language models.

### 9.1 Error messages are part of the interface

An error MUST carry its class and SHOULD carry the remedy. An agent told only
`AMPI_ERR_CONTEXT_EXHAUSTED` will improvise; an agent told to retry with
`--projection digest` will do that. This is not a quality-of-implementation
matter when the caller is a model.

### 9.2 Deadlock detection

Because every blocking wait is registered, an implementation MAY construct the
global wait-for graph and report a cycle. It MUST NOT report a cycle if any
edge on it could be satisfied by an already-posted message, and SHOULD NOT
check until a rank has been blocked longer than plausible schedule skew ---
otherwise ordinary skew is reported as deadlock, which is worse than reporting
nothing.

---

## 10. Failure mitigation

The triad is ULFM's, and the reasoning is ULFM's: the library does not recover
for you, it returns control to the survivors in a state they can reason about.

### 10.1 `AMPI_Comm_revoke(comm)`

Makes every outstanding and future operation on the communicator fail at every
rank. It exists because knowledge of a failure is **not uniform**: some ranks
have noticed, others are blocked on the dead peer and will wait forever.
Revocation is the only way to get all survivors to the same place so that
recovery can be collective. It MUST be idempotent, and any rank MAY call it.

### 10.2 `AMPI_Comm_shrink(comm, name)`

Derives a communicator over the survivors, renumbered densely and preserving
relative order. Renumbering is the price of shrinking, and it is why an
application that wants to survive failures MUST NOT hard-code rank identities
into durable state.

### 10.3 `AMPI_Comm_agree(comm, value)`

Fault-tolerant agreement on a boolean over the live members, returning the
survivor set alongside the value. Deliberately weaker than consensus in the
Paxos sense: no leader, no persistent quorum, no log. The question being
decided is small, the participant set is known, and the store is already
durable. Agreement is used to answer "shall we all continue?", which must be
cheap enough to ask after every phase.

An implementation MUST NOT implement agree over a point-to-point schedule on
the communicator being repaired: it would deadlock on precisely the failures
the call exists to handle.

### 10.4 Recovery from the durable log

`AMPI_Inbox_replay(rank)` returns everything ever addressed to a rank.
Delivery MUST NOT destroy the record. This is message logging in the
rollback-recovery sense, and it is what lets a replacement rank reconstruct its
predecessor's inbound history without any cooperation from the senders.
`AMPI_Checkpoint` and `AMPI_Restore` cover the rank's own state.

### 10.5 Failure detection

A failure detector for agent ranks is eventually perfect at best, and a naive
implementation of it is worse than that. A rank's heartbeat only advances when
it calls the library, and an LLM rank can spend minutes inside one turn without
doing so. Turn latency is heavy tailed, so any fixed timeout tight enough to
detect a crash quickly will also condemn healthy agents.

Two mechanisms are therefore REQUIRED:

1. **Declared deadlines.** `AMPI_Heartbeat(expect_idle)` lets a rank that knows
   it is about to be slow say so, and the detector MUST believe it.
2. **Retraction and widening.** A rank that makes a library call is, by direct
   evidence, alive; if it was condemned, the condemnation MUST be withdrawn.
   Each retraction SHOULD widen that rank's timeout, so the detector converges
   on the rank's actual turn latency instead of oscillating against it.

Without the second mechanism the detector does oscillate, badly: a measured
eight-rank, twenty-minute run recorded 1091 condemnations, essentially all
withdrawn moments later.

---

## 11. Profiling

Every operation MUST emit an enter/exit trace record carrying at least: rank,
timestamp, operation, communicator, peer, tag, tokens, duration, and outcome.

This is MPI's PMPI requirement with the two quantities that matter for agents
added. It is a MUST rather than a SHOULD for the same reason MPI made it
normative: a performance claim about an AgentMPI program should be checkable by
a third party who has only the job's trace.

---

## 12. Conformance levels

**Level 1 --- Core.** Lifecycle, point-to-point with wildcards and
non-overtaking, `AMPI_Barrier`, `AMPI_Bcast`, `AMPI_Gather`, `AMPI_Scatter`,
context accounting with eager/rendezvous, the error classes of §9, and the
trace of §11.

**Level 2 --- Collective.** `AMPI_Allgather`, `AMPI_Alltoall`, `AMPI_Reduce`,
`AMPI_Allreduce`, `AMPI_Scan`, structural operators, collective-ordering
detection, and a documented algorithm decision function.

**Level 3 --- Shared state.** Windows with versions, `AMPI_Accumulate`,
`AMPI_Fetch_and_op`, `AMPI_Win_claim`, expiring leases, `AMPI_Win_fence`.

**Level 4 --- Resilient.** Failure detection with declared deadlines and
retraction, `AMPI_Comm_revoke`, `AMPI_Comm_shrink`, `AMPI_Comm_agree`,
`AMPI_Respawn`, inbox replay, checkpoints.

**Level 5 --- Semantic and context-bounded.** Semantic operators with upcall
and exact resumption, `AMPI_Reduce_scatter_block`, algebra-constrained and
residency-constrained algorithm selection, projections, deadlock detection.

The reference implementation (`ampi`, SQLite and filesystem devices) implements
levels 1--5, with the filesystem device conforming at the device layer only.

---

## 13. Open questions

Named here rather than glossed over.

- **Sessions.** MPI-4 decoupled initialisation from a single global
  `MPI_COMM_WORLD` so independent libraries can initialise MPI independently.
  The agent analogue --- an independently authored agent library joining a
  running job without knowing about it --- is not yet specified.
- **Inter-communicators.** Not specified. The natural use is a boundary between
  organisations or trust domains, which interacts with authentication.
- **Nonblocking collectives.** The step machine supports them; the interface
  does not yet expose `AMPI_Ibarrier` and friends, nor the nonblocking sparse
  data exchange that would follow from them.
- **Input attestation.** A measured run found a rank contributing a payload
  that was not the one it was assigned. The protocol delivered it faithfully.
  Whether the interface should let a harness bind a rank's contribution to a
  declared input hash is an open design question with an obvious cost in
  flexibility.
- **Partitioned communication.** MPI-4's `MPI_Pready` has a plausible analogue
  in an agent streaming a long artifact in sections, and no design yet.
