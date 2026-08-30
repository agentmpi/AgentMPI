# AgentMPI: A Message Passing Interface for Multi-Agent Systems

**Specification version AgentMPI/0.1 — normative**

---

## S0. Status, scope and philosophy

### S0.1 What this document is

This document specifies AgentMPI, an interface by which independent LLM agents
coordinate. It specifies **semantics**, not an implementation. A conforming
implementation may store its state anywhere, use any transport, and select any
algorithm permitted by S6, provided the observable behaviour matches this text.

The reference implementation accompanying this specification (the `ampi` runtime)
is one such implementation and is normative only where this document says so.

### S0.2 What AgentMPI is not

AgentMPI is not a multi-agent system, an agent framework, a prompting technique,
or a tool-calling standard. It stands to multi-agent systems as MPI stands to
parallel applications: a library that harness authors call, not a harness. It
does not decide how many agents to run, what they should do, how to prompt them,
which model to use, or how to recover from failure. It provides the mechanisms
with which those decisions are expressed.

In particular, AgentMPI is **semantics-thin**. It does not interpret payloads. It
has no ontology, no speech acts, no commitment semantics, no notion of belief or
intention. A message is an opaque body plus an envelope of size and provenance
metadata. This is a deliberate rejection of the KQML/FIPA-ACL design lineage,
whose mentalistic semantics proved both unverifiable and unnecessary, in favour
of MPI's: standardise the mechanism, leave meaning to the application.

### S0.3 Design goals, in priority order

1. **Portability of harnesses across agent hosts.** A harness written against
   AgentMPI should run whether the ranks are Cursor subagents, API-driven agent
   loops, or humans at terminals.
2. **Explicit locality of information.** No hidden data movement. If an agent
   knows something, either it produced it or the harness moved it, visibly.
3. **Composability.** Communicators give an agent library a private namespace, so
   two independently written sub-protocols cannot interfere. This is the property
   MPI's own designers identify as the reason MPI could support libraries at all.
4. **Context as a first-class, accounted resource.** Context exhaustion is the
   dominant scaling failure of agent harnesses. It is treated here the way memory
   footprint is treated in MPI: measured, bounded, and subject to an explicit
   flow-control threshold.
5. **Failure as a normal condition.** Agents fail constantly and in more ways
   than processes do. The interface exposes failure rather than hiding it, and
   provides tools rather than policy — ULFM's stance, adopted wholesale.
6. **Standardise only what is understood.** Where a question is open (automatic
   context compaction, semantic verification, cost-aware scheduling) this version
   provides a hook and no policy.

### S0.4 Conformance

An implementation conforms to AgentMPI/0.1 if it implements every operation in
S3–S9 with the specified semantics, reports `AgentMPI/0.1` as its protocol
version, and passes the conformance suite. Operations marked *optional* may be
absent, in which case they MUST fail with `AMPI_ERR_UNSUPPORTED`.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be interpreted as
in RFC 2119.

---

## S1. The execution model

### S1.1 Ranks, epochs, and the universe

An AgentMPI **job** consists of `P` **ranks**, numbered `0..P-1`. A rank is a
*role*, not a process: at any time at most one **executor** (an agent instance)
occupies a rank. An executor occupying rank `r` is identified by the pair
`(r, e)` where `e` is the rank's **epoch**, a monotonically increasing integer.

The epoch is a **fencing token**. When a rank's executor is replaced, the epoch
increments. Any operation issued by an executor whose epoch is not the rank's
current epoch MUST fail with `AMPI_ERR_FENCED` and MUST have no effect. This is
what makes a *zombie* — an executor that is still running after being declared
failed — harmless rather than corrupting.

> **Rationale.** Leases alone are insufficient: an executor cannot know that its
> lease expired while it was mid-step, so between expiry and its next call there
> is a window in which two executors believe they own the rank. A monotone token
> checked on every operation closes that window. This is the standard fencing
> argument for distributed locks, applied to executor identity.

### S1.2 Executor identity is ambient

An implementation MUST make a rank's identity available to its executor without
the executor having to supply it (in the reference implementation, the
environment variables `AMPI_RANK` and `AMPI_ROOT`). An implementation SHOULD
reject an operation that names a different rank than the caller's.

> **Rationale.** This is not ergonomics. In early testing, the most frequent
> agent error by a wide margin was passing the wrong rank identifier. Ambient
> identity eliminates the entire class.

### S1.3 The five asymmetries with MPI

AgentMPI mirrors MPI's structure but not its cost model. Five differences drive
every deviation in this specification.

| # | Property | MPI process | AgentMPI executor |
|---|---|---|---|
| A1 | Determinism | Deterministic given inputs | Nondeterministic; may produce a different, plausible, wrong result |
| A2 | Scarce resource | Memory bandwidth, network bandwidth | **Context window occupancy** |
| A3 | Cost of an operator | Nanoseconds; free relative to communication | Seconds to minutes; dominates communication entirely |
| A4 | Latency distribution | Tight, predictable | Heavy-tailed; the maximum of `P` samples is far above the median |
| A5 | Failure model | Fail-stop, rare, kills the job | Individual, frequent, partial, and sometimes *silent* (plausible wrong output) |

Consequences, each developed later: A2 forces the eager/rendezvous split to be
denominated in tokens and the introduction of views (S5); A3 inverts MPI's
collective-algorithm selection rules (S6.7); A4 motivates quorum collectives
(S6.8); A5 requires the whole of S8; A1 requires reduction reproducibility to be
an explicit, declared property (S7.3).

### S1.4 The two planes

A conforming implementation has a **control plane** and a **data plane**, and
they have different cost structures.

* The **control plane** carries envelopes, handles, arrival notifications and
  synchronisation. It MAY be a shared medium that every rank can read; its cost
  is measured in operations and rounds.
* The **data plane** carries payload *bodies into executor context windows*. It
  is unavoidably private and per-rank; its cost is measured in **tokens**.

This distinction is the single most important structural fact about AgentMPI. It
means that control collectives (barrier, notification, handle broadcast) can be
`O(1)` rounds, while data collectives whose operator an agent must evaluate are
bounded below by the number of operator applications on the critical path.

---

## S2. Units, thresholds and the cost model

### S2.1 Tokens

The unit of transfer cost is the **token**. Implementations MUST report which
token estimator they use. The reference estimator is `cl100k_base` when
available and a documented structural estimator otherwise; the structural
estimator's median relative error against `cl100k_base` on the reference corpus
is under 20%.

Because thresholds are denominated in tokens, two implementations using different
estimators MAY make different eager/rendezvous decisions for the same payload.
This is permitted: the decision affects cost, not correctness.

### S2.2 The cost model

For an operation moving a payload of `n` tokens between two ranks:

```
T = alpha + beta * n + gamma * k + lambda
```

* `alpha` — per-operation control-plane latency (reference implementation:
  **5.7 ms**, measured by ping-pong regression).
* `beta` — per-token control-plane cost (**1.15 µs/token**), giving a
  half-bandwidth point `n_1/2 = alpha/beta ≈ 4940` tokens.
* `gamma` — cost of one **operator application by an executor**, and `k` the
  number of such applications on the critical path. For an LLM this is seconds to
  minutes.
* `lambda` — the executor's own think time, heavy-tailed (A4).

The decisive observation is that `gamma` and `lambda` exceed `alpha` and
`beta * n` by three to five orders of magnitude. Therefore:

> **Selection principle.** In MPI, collective algorithm selection minimises
> `alpha`-terms and `beta`-terms and treats `gamma` as free. In AgentMPI,
> selection MUST minimise `k` — the number of operator applications on the
> critical path — and MUST bound context cost. Transplanting MPI's selection
> rules unchanged gives wrong answers (S6.7 gives a measured example where the
> MPI-optimal algorithm moves 36x more data).

### S2.3 The context ledger

Every rank has a **context budget** in tokens and a **context used** counter.
Delivering a payload body into a rank's context MUST charge the ledger.

An operation whose delivery would exceed the budget MUST NOT silently succeed.
It MUST either fail with `AMPI_ERR_CTX_EXCEEDED` or **degrade**: deliver a
bounded view (S5.4) instead of the body and set an advisory note. Implementations
SHOULD degrade rather than fail, because an agent that receives a truncated
message can continue while one that receives an error usually cannot.

---

## S3. Lifecycle

### S3.1 `AMPI_Init`

Joins the universe as the ambient rank. MUST be idempotent with respect to
repeated calls by the same executor at the same epoch (agents retry commands).

If the rank's state is *failed* or *fenced*, or if the caller requests
reinitialisation, `AMPI_Init` MUST increment the epoch and MUST return a
**recovery briefing** (S8.7) rather than a clean slate.

If the rank's state is *spawned* — that is, an epoch has already been allocated
for a replacement — `AMPI_Init` MUST adopt that epoch and MUST NOT increment it.

### S3.2 `AMPI_Finalize`

Marks the rank finalised. Does not block on other ranks.

### S3.3 `AMPI_Heartbeat(extend)`

Renews the caller's lease and, if `extend` is given, guarantees the lease
survives for at least `extend` more seconds without another call.

> **Rationale.** A lease-based detector cannot distinguish a thinking executor
> from a dead one. Making the lease longer than the longest legitimate pause is
> the wrong fix, because the lease also bounds how long a blocked peer waits
> before it may make progress. `AMPI_Heartbeat` lets the executor supply the
> information a timeout cannot infer: "I am about to spend ten minutes on one
> step." This is the same move a long-running task makes when it extends a
> distributed lock instead of acquiring a worst-case one.

Harnesses SHOULD instruct executors to call it before any step expected to take
longer than a quarter of the lease.

### S3.4 Leases and the join deadline

Every rank has a **lease deadline**. It is set when the rank is *requested*, not
when its executor first calls in.

> **Rationale.** A rank whose executor never starts at all has no lease to
> expire, so without a join deadline it is neither alive nor failed and every
> peer waits for it forever. We encountered exactly this: a launcher that could
> start only 6 of 22 requested ranks produced a job in which 16 no-shows were
> permanently pending, and no operation could detect it. Granting the lease at
> request time makes launch failure a detectable failure like any other.

### S3.5 `AMPI_Abort`

Terminates the job. Implementations SHOULD record the reason.

---

## S4. Communicators, groups and topologies

### S4.1 Communicators

A **communicator** is a named, ordered set of ranks plus a private context. All
communication is scoped to a communicator. Ranks within a communicator are
numbered `0..size-1`; the mapping to world ranks is fixed at creation.

`AMPI_COMM_WORLD` contains all ranks of the job and is named `world`.

A communicator carries a **generation** number, incremented by `AMPI_Comm_shrink`
(S8.5). Traffic on one generation MUST NOT be visible on another.

> **Rationale for private contexts.** This is the feature MPI's designers
> retrospectively identify as the reason MPI could support libraries: a reusable
> component operating on a subset of ranks can use a local name space and cannot
> collide with its caller's tags. The context MUST NOT be a value the application
> can observe or forge. No current LLM agent framework provides an equivalent,
> which is why two agent "teams" in one harness invariably share one global bus.

### S4.2 Construction

* `AMPI_Comm_split(color, key)` — collective over the parent. Ranks sharing a
  colour form a new communicator ordered by `key`, ties broken by parent rank. A
  colour of *undefined* means the caller participates but joins nothing.
  Membership MUST NOT be decided from a partial vote: the operation completes
  only when every **live** parent rank has registered a colour. Failed ranks are
  excluded rather than waited for.
* `AMPI_Comm_create(group, name)` — non-collective; the caller names an explicit
  member set. Requires no participation from non-members.
* `AMPI_Comm_dup(name)` — same group, fresh context. The operation a library
  performs.
* `AMPI_Comm_free` — optional.

### S4.3 Topologies

* `AMPI_Cart_create(dims, periodic)` — Cartesian grid.
* `AMPI_Cart_shift(direction, disp)` — returns `(source, dest)`, either of which
  may be *none* at a non-periodic boundary.
* `AMPI_Dist_graph_create(edges)` — an arbitrary declared adjacency, i.e. an
  organisation chart.
* `AMPI_Neighbor_allgather` — exchange with declared neighbours only.

> **Rationale.** Declaring the structure rather than hard-coding peer ranks buys
> three things: the runtime can trace and validate it; a harness that says "my
> neighbours" keeps working after a shrink renumbers everyone; and a
> neighbourhood collective costs `Theta(degree)` context per rank where a full
> allgather costs `Theta(P)`, which is the difference between a review round that
> scales and one that does not.

---

## S5. Payloads, delivery and views

### S5.1 Envelopes and handles

Every payload has an **envelope**: source, tag, communicator, token count, byte
count, content digest, an optional caller-declared **schema** string, and a cheap
deterministic **summary**. Every payload also has a **handle**, a stable
content-addressed identifier.

The envelope is cheap; the body is not. Operations that deliver a payload MUST
report the envelope and MAY withhold the body.

### S5.2 The eager/rendezvous threshold

Let `E` be the implementation's **eager threshold** in tokens (reference default:
700).

* A payload of `n <= E` tokens is delivered **eagerly**: the body is placed in the
  receiver's context and the ledger is charged `n`.
* A payload of `n > E` tokens is delivered by **rendezvous**: the receiver
  obtains the envelope and handle, is charged only the envelope cost, and MUST
  perform an explicit materialisation (S5.3) to obtain the body.

> **Rationale.** This is MPI's eager limit, and it exists for the same reason.
> In MPI, below the limit, pushing bytes to the receiver unsolicited is cheaper
> than an RTS/CTS handshake; above it, the receiver's unexpected-message buffer
> is too precious to fill without permission. In AgentMPI the precious resource
> is the receiver's attention. The mechanism transfers exactly; only the units
> change. This is the single adaptation that converts context exhaustion from an
> emergent failure into a flow-control problem with a tunable threshold.

A caller MAY override the decision per operation.

### S5.3 Materialisation

`AMPI_Get_payload(handle)` delivers a body and charges the ledger. It MUST
respect S2.3: if the charge would exceed the budget it degrades to a view or
fails.

`AMPI_Save_payload(handle, path)` writes a body to storage **without** charging
the ledger, because it does not enter any context window.

### S5.4 Views — the derived-datatype analogue

A **view** is a declarative, deterministic projection of a payload into a bounded
number of tokens. `AMPI_Type_view(handle, spec) -> body` computes it.

Conforming implementations MUST provide at least: `full`, `head:n`, `tail:n`,
`headtail:n`, `lines:a-b`, `keys:k1,k2`, `shape`, `stat`, `grep:pattern`,
`chunk:i/n`, `outline`. Views MUST be deterministic and MUST be free of model
calls, so that a replay charges identical context.

> **Rationale.** An MPI derived datatype is a *declarative description of how to
> access a buffer*: `MPI_Type_vector` says "ten blocks of four doubles, stride
> one hundred", and the library gathers exactly those elements. The point is that
> the access pattern is data the library can optimise rather than control flow it
> cannot see. AgentMPI's scarce resource is context rather than memory bandwidth,
> so the analogous question is not "which bytes" but "which tokens, and how few".
> A view answers it declaratively, which is why the runtime can cache it, cost it
> and reproduce it.

Semantic (model-evaluated) compression is **not** a view. It is an agent-evaluated
operation whose cost the harness must see.

### S5.5 Schemas

A caller MAY attach a free-text or JSON-Schema `schema` to a payload. AgentMPI
does not validate it. It is envelope metadata, so a receiver can decide what a
handle contains without materialising it.

---

## S6. Point-to-point and collective communication

### S6.1 Matching

A receive matches on the triple `(communicator, source, tag)`, with wildcards
`AMPI_ANY_SOURCE` and `AMPI_ANY_TAG` permitted for source and tag.

**Delivery rule.** A message is delivered to the *first posted receive that
matches it*.

**Non-overtaking rule.** Among messages that match the same receive, they are
matched in the order they were sent. AgentMPI provides no other ordering
guarantee and no fairness guarantee.

Tags are integers in `0..AMPI_TAG_UB`. Implementations MUST reserve a private tag
range above that for their own traffic. Implementations SHOULD accept **symbolic
tags** (words) and map them deterministically into the user range; agents use
names far more reliably than integers.

### S6.2 Send modes

| Mode | Completes when | Notes |
|---|---|---|
| standard | the message is durably enqueued | local completion |
| synchronous | the message has been *matched* | non-local; a handshake |
| ready | immediately, but errors unless a matching receive is already posted | catches schedule bugs early |

### S6.3 Deadlines and idempotent retry — a deliberate departure

Every blocking AgentMPI operation is **deadline-bounded**. On expiry it MUST fail
with `AMPI_ERR_TIMEOUT` and MUST leave its state such that **re-issuing the
identical operation resumes the same wait rather than starting a new one**.

Implementations MUST retry internally at least once by default.

> **Rationale.** `MPI_Recv` blocks forever, which is tolerable when a peer's
> failure kills the whole job. An agent peer may be slow, wedged, or dead, and no
> timeout distinguishes them, so a bounded deadline plus resumable state is the
> only workable primitive. Internal retry is not a convenience: in a pilot run,
> an executor instructed to retry a timed-out call up to twenty times gave up
> after two and stalled its entire reduction tree. **A protocol that depends on
> an executor's persistence is not a protocol.** Making the default behaviour
> "keep waiting" means abandoning a collective requires the executor to do
> something rather than nothing.

### S6.4 Nonblocking operations

`AMPI_Isend`, `AMPI_Irecv`, `AMPI_Wait{,all,any,some}`, `AMPI_Test`,
`AMPI_Cancel`, and nonblocking collectives `AMPI_I<coll>`.

Posted receives MUST be durable: an executor may post receives, be replaced, and
have its successor complete them.

### S6.5 Probing

`AMPI_Probe`/`AMPI_Iprobe` inspect the next matching message's envelope without
receiving it. `AMPI_Inbox` enumerates all pending messages for the caller.

> **Rationale.** In MPI, probing mainly sizes a buffer. Here it lets an executor
> see *what is waiting and what it would cost* before committing context, which
> is the basis of every context-aware scheduling decision a harness can make.

### S6.6 Collectives

`AMPI_Barrier`, `AMPI_Bcast`, `AMPI_Scatter`, `AMPI_Gather`, `AMPI_Allgather`,
`AMPI_Reduce`, `AMPI_Allreduce`, `AMPI_Reduce_scatter`, `AMPI_Scan`,
`AMPI_Exscan`, `AMPI_Alltoall`, `AMPI_Neighbor_allgather`.

**Collective identity is explicit.** MPI identifies the k-th collective on a
communicator by program order. AgentMPI MUST support an explicit **label**, and
harnesses SHOULD always supply one; program order MAY be offered as a fallback.
A retried call MUST rejoin the caller's still-open collective rather than start a
new one.

> **Rationale.** An executor's "program order" is not reliable: it may retry a
> command, skip a step, or reorder two independent calls, and relying on order
> silently mismatches ranks. Named collectives were the single largest robustness
> improvement in the whole interface.

**Gather MUST NOT default to concatenating bodies.** It MUST return a *manifest*:
one entry per contributor with rank, handle, token count and summary. The caller
then materialises what it needs, or supplies a per-contribution view budget.

> **Rationale.** This is where naive harnesses die. At `P=128` with 4000-token
> contributions, an inlining allgather charges one rank **501,888 tokens** — far
> beyond any context window — where a handle-based one charges 3,200 (measured;
> S6.9). Concatenation is not a reasonable default at any interesting `P`.

### S6.7 Algorithm selection

An implementation MUST document its algorithm catalogue and MUST let the caller
override the selection. The reference catalogue and selection rules:

| Collective | Algorithms | Default rule |
|---|---|---|
| barrier | `central`, `dissemination`, `linear` | `central` for `P <= 32`, else `dissemination` (measured crossover) |
| bcast | `flat`, `binomial`, `chain`, `relay` | `flat` |
| reduce | `flat`, `binomial`, `chain` | `flat` for runtime operators; `binomial` for agent operators |
| allreduce | `flat`, `reduce_bcast`, `recursive_doubling` | `flat` for runtime operators; `reduce_bcast` for agent operators |
| gather | `flat`, `binomial` | `flat` |
| allgather | `flat`, `ring`, `recursive_doubling` | `flat` |
| scatter | `flat`, `binomial` | `flat` |
| scan | `chain`, `recursive_doubling` | journal prefix fold for runtime operators; `chain` for agent operators |
| exscan | `chain`, `recursive_doubling` | as `scan` |
| alltoall | `flat`, `pairwise` | `flat` |
| reduce_scatter | `flat` | `flat` (runtime operators only) |

The two rules that differ from MPI, both derived from S2.2 and confirmed by
measurement:

**(a) Runtime operators want no tree.** When the operator can be applied by the
implementation, the shared control plane can fold all contributions in place:
one round, zero messages. A tree adds rounds and buys nothing. This is the
in-network-aggregation regime.

**(b) Agent operators want MPI's tree, and reject MPI's *best* tree.** When an
executor applies the operator, `k` (applications on the critical path) is the
entire cost. Measured at `P=64` with a 0.25 s operator: a binomial tree puts 6
applications on the critical path against a chain's 63. But recursive-doubling
allreduce — MPI's standard choice for short messages, because redundant
arithmetic is free — puts `P log P` applications in *total* against
reduce-then-broadcast's `P-1`, and at `P=128` moves **3,266,560 payload tokens
against 89,921**, a factor of 36. The algorithm that wins for bytes loses badly
for agents.

### S6.8 Quorum collectives

A collective MAY carry a **quorum** `q` in `(0,1]` and a deadline. It completes
when `ceil(q * live)` ranks have contributed.

Reaching quorum **releases** a barrier but MUST NOT close it: a straggler
arriving afterwards MUST still pass through. Otherwise a quorum barrier would
guarantee that precisely the slowest ranks fail.

A rank that arrives after a data-bearing collective has closed MUST receive the
published result with a `late` indication, not an error.

> **Rationale.** Executor completion times are heavy-tailed (A4), so a strict
> barrier over `P` executors waits for the maximum of `P` heavy-tailed samples.
> The quorum knob lets the harness choose between bulk-synchronous determinism
> and bounded staleness — the same trade stale-synchronous parameter servers
> make, expressed as a collective rather than as a training-loop hack.

### S6.9 Measured context costs (informative)

Reference implementation, `P=128`, 4000-token payloads:

| Collective | Delivery | Total context tokens | Peak per rank |
|---|---|---|---|
| bcast | inline | 501,888 | 3,921 |
| bcast | handle | 5,120 | 40 |
| bcast | view (400) | 51,200 | 400 |
| allgather | inline | 32,120,832 | 501,888 |
| allgather | handle | 204,800 | 3,200 |
| allgather | view (400) | 491,520 | 7,680 |

---

## S7. Reduction operators

### S7.1 Two families

* **Runtime operators** are applied by the implementation: exact, deterministic,
  free. Conforming implementations MUST provide `concat`, `union`, `jsonmerge`,
  `sum`, `max`, `min`, `count`, `and`, `or`, and SHOULD provide `vote`, `maxby`,
  `first`.
* **Agent operators**, written `agent:<label>`, are applied by an *executor*.
  This is `MPI_Op_create` with a language model as the callback.

### S7.2 The continuation protocol for agent operators

An agent operator cannot complete inside one call, because the operator *is* the
caller. A reduction with an agent operator MUST therefore:

1. return a **merge directive** naming two operand locations and an output
   location, and charge the caller's ledger for the operand tokens it will read;
2. accept `AMPI_Op_commit(step, result)`, which records the merged value and
   resumes the schedule, returning either the next directive or completion;
3. checkpoint the accumulator and schedule position durably, so that a timeout,
   crash, or replacement resumes at the same point.

Operand delivery MUST respect an optional operand budget by delivering views
(S5.4) instead of bodies.

> **Rationale.** The alternative — having the runtime call a model itself — would
> make AgentMPI a framework with an opinion about models, credentials and
> prompting. The continuation structure keeps the library owning the schedule and
> the user owning the operator, which is exactly MPI's division, and it means an
> agent operator needs no infrastructure beyond the executor that already exists.

### S7.3 Reproducibility

An operator declares `commutative` (default **false**) and, informatively,
whether it is deterministic.

When `commutative` is false, an implementation MUST use a canonical, rank-ordered
reduction tree, so that the same inputs on the same rank count produce the same
tree shape.

> **Rationale.** MPI users are warned that floating-point `MPI_SUM` may differ
> between runs and process counts because the tree shape varies. For an agent
> operator the effect is far larger: "merge these two draft glossaries" is
> neither associative nor commutative nor deterministic, and different tree
> shapes give materially different results. Making non-commutativity the
> *default* is the conservative choice; a harness that knows its operator is
> order-insensitive can opt into the faster schedule, as in MPI, but with much
> larger stakes.

### S7.4 Fault-tolerant reduction

If a child in a reduction tree is declared failed, its subtree's contributions
MUST be dropped, the omission MUST be recorded, and the collective MUST complete
with the survivors' contributions. `recursive_doubling` is exempt: no variant of
it preserves an identical result on all ranks under failure, so it MUST fail
loudly rather than return different answers to different ranks.

---

## S8. Shared state: windows

### S8.1 Windows

A **window** is a named, versioned key space exposed to a communicator. There is
no ambient global memory: a rank may only touch state that a window exposes.

> **Rationale.** The most-reported failure of real multi-agent systems is that
> executors cannot share what they learn. Pure message passing forces every fact
> discovered by one agent to be routed to every agent that will later need it, by
> a harness author who cannot know in advance who that is. A window inverts it.
> Keeping windows named and explicit is what keeps the shared state auditable —
> the difference between a blackboard and a mess.

### S8.2 Operations

| Operation | Semantics |
|---|---|
| `AMPI_Put(win, key, value, expect_version?)` | Write. With `expect_version`, succeeds only if the cell is still at that version, else `AMPI_ERR_CONFLICT`. |
| `AMPI_Get(win, key, view?, version?)` | Read, charging the ledger for what is taken. May read a historical version. |
| `AMPI_Accumulate(win, key, value, op)` | Atomically apply a **runtime** operator to the cell. |
| `AMPI_Compare_and_swap(win, key, expect, value)` | Atomic conditional write. |
| `AMPI_Fetch_and_op(win, key, op, value)` | Atomic read-then-modify. |
| `AMPI_Win_list(win, prefix?)` | Enumerate keys with sizes and provenance, **without** reading bodies. |
| `AMPI_Win_history(win, key)` | Version history with writer attribution. |

`AMPI_Accumulate` and `AMPI_Compare_and_swap` are the load-bearing ones.
Accumulate replaces read-modify-write (three round trips and a race) with one
atomic operation, so "union this finding into the shared findings" needs no lock.
Compare-and-swap is how work is claimed: a task cell holds `unclaimed` and
whichever executor swaps it wins — one operation that eliminates an entire class
of duplicated-work bug, and unlike a lock it cannot be held by a dead executor.

`AMPI_Win_list` is what makes a blackboard usable by an executor with a bounded
context: it can see *what exists* for a few tokens per key and then spend its
budget deliberately.

### S8.3 Synchronisation

* **Active target.** `AMPI_Win_fence(win, label)` closes an epoch: a barrier plus
  the guarantee that every participant's writes for the phase are in. This turns
  a blackboard — notoriously hard to reason about — into a sequence of
  bulk-synchronous supersteps.
* **Passive target.** `AMPI_Win_lock(win, key, mode, lease)` /
  `AMPI_Win_unlock`. Locks MUST be **leased** and MUST carry a monotone **fencing
  token**. An expired lease MUST be reclaimable, and a write bearing a stale
  token MUST be rejected.

> **Rationale.** An MPI process holding a window lock cannot wander off; an
> executor can. The lease prevents a dead holder from wedging the job; the token
> prevents a revived holder from corrupting state after its lease expired.
> Without both, the standard lease-expiry race is merely made unlikely rather
> than closed.

### S8.4 Consistency

Within one window, operations are **linearizable**. Across windows, no ordering
is guaranteed. Versions are per-cell and monotone. Every cell records its writer
rank and epoch, so any claim in shared state is attributable.

---

## S9. Fault tolerance

### S9.1 Failure model

AgentMPI distinguishes these failure kinds, because the appropriate recovery
differs:

| Kind | Detection | Appropriate response |
|---|---|---|
| `crash` | launcher observes exit | respawn |
| `lease_expired` | lease deadline passed | respawn or shrink |
| `abort` | executor called `AMPI_Abort` | shrink; do not respawn with the same assignment |
| `ctx_exhausted` | ledger at budget without completion | respawn with a *smaller* assignment |
| `budget_exhausted` | cost limit reached | shrink |
| `protocol_violation` | output failed its declared schema | respawn with a corrected prompt |
| `wrong_answer` | a verifier rejected the result | redundancy or a verifier agent; not a restart |
| `killed` | injected | as configured |
| `zombie` | operation at a stale epoch | fence (terminal) |

### S9.2 Detection: lazy, local, lease-based

Failure detection MUST be lease-based and MAY be evaluated lazily — that is, only
when some rank blocks on an operation that a failure would prevent. It implements
an *eventually perfect* (`<>P`) detector under partial synchrony. There MUST NOT
be a requirement for a monitoring daemon.

Detection is **local**: two ranks MAY hold different views of who has failed, and
no operation may assume a globally consistent view. Where agreement is required
(shrink), it MUST be obtained explicitly through `AMPI_Comm_agree`.

> **Rationale.** ULFM's central design principle, and the reason it is
> implementable at all. Detection runs exactly when somebody cares and never
> otherwise, which is also why its failure-free cost is one timestamp update per
> call.

### S9.3 Progress obligations while blocked

A blocked rank MUST, while waiting: renew its own lease, run the failure
detector, and observe revocation.

> **Rationale.** Omitting the first of these is catastrophic and not obvious. In
> an early version, a rank waiting inside a barrier made no runtime calls, so the
> detector declared it dead for the crime of waiting; every rank that arrived
> first was declared failed and the job cascaded. Blocking is not evidence of
> death.

### S9.4 `AMPI_Comm_revoke`

Any rank may revoke a communicator. After revocation, every pending and future
non-local operation on it MUST fail with `AMPI_ERR_REVOKED`. Revocation is
irreversible; the only way forward is `AMPI_Comm_shrink`.

> **Rationale.** When a rank fails, the *survivors* are the problem: they are
> blocked inside collectives that can never complete, and each discovers the
> failure only if it happens to be waiting on the dead rank directly. Revocation
> makes every survivor fail fast, everywhere, which is what lets them all reach
> the recovery path together.

### S9.5 `AMPI_Comm_shrink`

Derives a new communicator over an **agreed** set of survivors, densely
renumbered preserving relative order, with an incremented generation.

The survivor set MUST be agreed, not locally computed, or two ranks obtain
differently-sized communicators and every subsequent collective mismatches.

Harnesses MUST treat rank identity as communicator-relative and MUST NOT cache it
across a shrink.

### S9.6 `AMPI_Comm_agree`

Fault-tolerant agreement over a flag and optional values. It MUST work on a
**revoked** communicator, since it is how survivors coordinate recovery. It MAY
accept a quorum so that a straggler cannot hold the survivors hostage.

### S9.7 `AMPI_Comm_failure_ack` / `get_failed`

Acknowledging the currently known failures MUST re-enable wildcard receives on
the communicator, which would otherwise keep returning
`AMPI_ERR_PROC_FAILED_PENDING`. `get_failed` reports the failed set with
diagnostic detail (kind, epoch, context used at death).

### S9.8 Replacement and the recovery briefing

`AMPI_Respawn(rank)` allocates a new epoch for a rank, breaks the predecessor's
locks, cancels its posted receives, and marks it absent in any open collective so
that the collective can still close. The predecessor's messages are **not**
deleted: a survivor may still need what it sent.

`AMPI_Recover(rank)` returns a **recovery briefing**: the replacement's answer to
five questions it must know and cannot guess.

1. What was I assigned? (scatter slices, role, claimed tasks)
2. What did I already publish? (window cells written, messages sent)
3. What did I already receive?
4. What did I promise that is still outstanding? (unmatched sends, posted
   receives, open collectives, held locks, pending merge steps)
5. What did I record for myself? (the memo table)

> **Rationale.** There is no memory image to restore, so process checkpointing has
> no analogue. What *does* have an analogue is durable execution: replay the
> record of externally visible commitments rather than a memory snapshot. The
> memo table is the executor's own continuation state, written deliberately —
> which is why harnesses MUST instruct executors to record progress after each
> phase, and why one cheap call per phase is the difference between a recoverable
> job and a lost one.

### S9.9 Supervision

A launcher SHOULD act as a supervisor with a bounded restart policy: at most `N`
replacements per rank, then give up on that rank.

> **Rationale.** OTP's max-restart-intensity. An executor that fails because its
> assignment is impossible will fail again, and an unbounded supervisor turns
> that into an expensive infinite loop.

---

## S10. Error classes

| Class | Meaning | Retryable |
|---|---|---|
| `AMPI_SUCCESS` | — | — |
| `AMPI_ERR_ARG` | malformed or missing argument | no |
| `AMPI_ERR_RANK` | rank out of range, or not a member of the communicator | no |
| `AMPI_ERR_TAG` | tag out of range or in the reserved internal range | no |
| `AMPI_ERR_COMM` | no such communicator, or wrong kind | no |
| `AMPI_ERR_OP` | no such reduction operator | no |
| `AMPI_ERR_WIN` | no such window | no |
| `AMPI_ERR_REQUEST` | no such request, or it was cancelled | no |
| `AMPI_ERR_TRUNCATE` | a payload did not fit the declared shape | no |
| `AMPI_ERR_NOT_INIT` | the caller has not called `AMPI_Init` | no |
| `AMPI_ERR_ALREADY_INIT` | duplicate initialisation at the same epoch | no |
| `AMPI_ERR_NO_JOB` | no job state was found | no |
| `AMPI_ERR_INTERN` | internal error in the implementation | no |
| `AMPI_ERR_UNSUPPORTED` | optional operation absent | no |
| `AMPI_ERR_TIMEOUT` | deadline reached; state preserved | **yes** |
| `AMPI_ERR_PROC_FAILED` | a peer required to complete has failed | no |
| `AMPI_ERR_PROC_FAILED_PENDING` | someone failed; a wildcard receive may still complete | **yes** |
| `AMPI_ERR_REVOKED` | communicator revoked | no — shrink |
| `AMPI_ERR_FENCED` | caller's epoch is stale; it has been replaced | no — **terminal** |
| `AMPI_ERR_CTX_EXCEEDED` | delivery would exceed the context budget | no — use a view |
| `AMPI_ERR_BUDGET_EXHAUSTED` | cost limit reached | no |
| `AMPI_ERR_OP_FAILED` | an agent operator step was abandoned or malformed | no |
| `AMPI_ERR_LOCK_BUSY` | lock held | **yes** |
| `AMPI_ERR_CONFLICT` | versioned write or CAS lost a race | **yes** |
| `AMPI_ERR_LATE` | a quorum collective closed without the caller | no |

Every error MUST carry a class, a human-readable message, and SHOULD carry a
**hint** stating the concrete next action. Errors are read by language models;
one that says what to *do* is acted on, one that merely says what went wrong
often is not.

---

## S11. Tracing

An implementation SHOULD record a durable event trace: enter/exit intervals per
rank, send/receive pairs with matched endpoints, collective intervals with
participant sets and per-participant join/done times, window accesses with keys
and writers, failures and recoveries, and error events with their class.

> **Rationale.** HPC learned that a parallel program's behaviour is invisible from
> any single process's output, which is why PMPI, SLOG-2, OTF2 and their viewers
> exist. It is more true of agents, where the usual debugging artefact is a pile
> of unordered transcripts. Error events in particular are what make retry
> behaviour measurable: the interesting number is not how many calls succeeded
> but how many times a rank had to reissue a deadline-bounded call.

---

## S12. Bindings

An AgentMPI binding is the surface an executor calls. For LLM executors the
binding is normally a **command-line tool**, because that is the only interface
an agent reliably has to a stateful library: it cannot hold a handle across
turns, cannot link a shared object, and its "function calls" are invocations
whose output lands in its context window.

A conforming binding for LLM executors SHOULD:

* take identity from the environment, never from an argument;
* emit terse structured output with sizes in tokens, and an explicit next-action
  line;
* mark retryable errors as such, in the output;
* be idempotent by default: labelled collectives, idempotency keys on sends,
  resumable blocking calls;
* provide the protocol manual as a command, so an executor can re-read it.

---

## Appendix A. Concept correspondence

| MPI | AgentMPI | Transfers? |
|---|---|---|
| process | rank + epoch (role, fenced identity) | with a fencing token added |
| `MPI_COMM_WORLD` | `world` | yes |
| communicator context | communicator context | fully; nothing in the agent ecosystem has an equivalent |
| rank | communicator-relative rank | yes; must not be cached across a shrink |
| tag | integer or symbolic tag | extended |
| byte | **token** | unit change with wide consequences |
| eager limit | eager threshold in tokens | fully; the core adaptation |
| unexpected-message buffer | receiver **context window** | fully |
| rendezvous RTS/CTS | handle + explicit materialisation | fully |
| derived datatype | **view** | in spirit: declarative bounded projection |
| `MPI_Pack`/`Unpack` | payload store + handle | yes |
| non-overtaking rule | non-overtaking rule | verbatim |
| `MPI_Probe` | probe/inbox with token costs | extended: cost visibility |
| `MPI_Op` predefined | runtime operator | yes |
| `MPI_Op_create` | **agent operator** with a continuation protocol | structure yes, cost model no |
| commutativity flag | commutativity flag, default false | inverted default |
| collective by program order | collective by **label** | changed; order is unreliable |
| binomial/ring/recursive-doubling | same catalogue | yes, but selection rules are rederived |
| in-network aggregation (SHARP) | journal-mediated `flat` collectives | yes |
| `MPI_Win` | window | yes |
| `MPI_Accumulate` | accumulate with runtime operators | yes |
| `MPI_Compare_and_swap` | CAS (work claiming) | yes |
| `MPI_Win_fence` | fence (BSP superstep boundary) | yes |
| `MPI_Win_lock` | **leased** lock with fencing token | extended |
| `MPI_Cart_create` / `_shift` | same | yes |
| neighbourhood collectives | same | yes; scaling argument sharpens |
| `MPI_Comm_spawn` | respawn + recovery briefing | repurposed |
| `MPIX_Comm_revoke` | revoke | verbatim |
| `MPIX_Comm_shrink` | shrink | verbatim |
| `MPIX_Comm_agree` | agree, with an optional quorum | extended |
| `MPIX_ERR_PROC_FAILED` etc. | same classes | verbatim |
| checkpoint/restart | recovery briefing (durable-execution replay) | replaced |
| — | **context ledger** | no MPI analogue |
| — | **quorum collectives** | no MPI analogue |
| — | **lease + join deadline** | no MPI analogue |
| — | **`AMPI_Heartbeat(extend)`** | no MPI analogue |
| — | **`relay` broadcast** (payload rewritten in flight) | no MPI analogue |
| blocking forever | deadline + resumable retry | changed |
| `MPI_ERRORS_ARE_FATAL` default | errors returned with hints | changed |

## Appendix B. Deliberate omissions in 0.1

Following MPI's practice of standardising only what is understood, this version
omits: automatic context compaction policy; semantic verification of results;
cost- or model-aware scheduling; inter-communicators; persistent collectives;
partitioned communication; sessions; a full non-power-of-two recursive-halving
reduce-scatter; agent-evaluated `reduce_scatter`; and any notion of agent
capability advertisement or discovery. Each is a research question, and a
standard that answers a research question prematurely is worse than one that
leaves a hook.
