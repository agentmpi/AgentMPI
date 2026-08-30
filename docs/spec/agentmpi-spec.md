# AgentMPI: protocol specification, version 0.1

This document specifies AgentMPI. It is organised like the MPI standard — normative
text, with the reasoning in explicit **Rationale** and **Advice to implementors**
notes — because that structure is the most useful thing MPI's standardisation
process produced. A reader who wants to know *why* a rule exists should not have to
reverse-engineer it from an implementation.

Conformance language: **MUST**, **MUST NOT**, **SHOULD**, **MAY** are used in the
RFC 2119 sense.

The reference implementation is `src/agentmpi/`. Where this document and the
implementation disagree, this document is the specification and the implementation
has a bug.

---

## 1. Scope and design goals

AgentMPI is a **library-level protocol for writing multi-agent systems**. It is not
a multi-agent system, not an agent framework, and not an interoperability protocol
for connecting agents built by different vendors. It occupies the position MPI
occupies with respect to parallel programs: the thing you write the program *with*.

### 1.1 Goals

**G1 — Composability.** A harness written against AgentMPI MUST be able to call
another such harness as a subroutine without their communication interfering. This
is the goal that forces communicators to exist, and it is the goal that every
current agent framework fails.

**G2 — Cost transparency.** Every operation's cost MUST be attributable: which rank
paid it, in tokens, in wall time, and in fold depth. An agent harness whose cost
cannot be attributed cannot be optimised, and cost here spans three
non-interchangeable currencies (§10).

**G3 — Failure as the normal case.** Every blocking operation MUST admit a deadline
and a policy. A protocol in which the failure of one participant can only be
handled by aborting the job is not usable when participants fail routinely.

**G4 — Context as an accounted resource.** The receiving agent's context window MUST
be modelled explicitly, and operations that would exceed it MUST fail visibly rather
than silently truncate. This has no MPI counterpart and is the single most common
cause of quality collapse in deployed agent systems.

**G5 — Protocol state outside the agent.** No protocol state may be entrusted to an
agent's working memory. An agent holds only handles (§2.4).

**G6 — Determinism of the protocol.** The sequence of protocol operations MUST be
reproducible from the event log even though the agents' outputs are not.

### 1.2 Non-goals

**N1** AgentMPI does not specify how an agent is implemented, prompted, hosted or
billed. That is the executor's business (§12), exactly as process launch is
`mpiexec`'s business and not MPI's.

**N2** AgentMPI does not specify agent-to-agent *discovery* or authentication across
organisational boundaries. That is what A2A-class protocols are for; AgentMPI assumes
a single administrative domain, as MPI assumes a single job.

**N3** AgentMPI does not provide Byzantine fault tolerance. Failure class F6 (§8) is
acknowledged and out of scope.

**N4** AgentMPI does not attempt to make agent output deterministic.

> **Rationale.** MPI's own non-goals were as important as its goals: by declining to
> be a language, to guarantee buffering, or to handle faults, MPI-1 stayed small
> enough to be implemented by every vendor within two years. The temptation in this
> setting is to specify prompting, model selection, memory architecture and tool
> use, which would produce a framework rather than a protocol and would be obsolete
> within a year. The line drawn here is: AgentMPI owns *communication and
> coordination among ranks*, and nothing else.

---

## 2. Model of computation

### 2.1 Ranks

A **job** consists of *p* **ranks**, numbered `0..p-1`. `p` is fixed at job creation
and MUST NOT change; populations that grow use `spawn` (§11), which produces a new
communicator rather than mutating the world.

A rank is a **durable role**, not a process. It is defined by its number, its
mailbox, its context budget and its accumulated accounting. Its physical embodiment
is an **incarnation**: one agent session bound to the rank for a bounded interval.
The relationship is one-to-many over time and exactly-one at any instant, enforced
by a lease (§8.2).

> **Rationale.** In MPI a rank *is* a process, and the identification is harmless
> because MPI processes live for the whole job. An agent session does not: it is
> ended by a timeout, by context exhaustion, or by the hosting environment. If rank
> identity were tied to session identity, every such event would force a
> communicator rebuild and would invalidate the harness's mapping from rank to work.
> Separating the two makes session termination an ordinary, recoverable event: the
> rank persists, its mailbox persists, and a fresh incarnation resumes it.

### 2.2 Communicators

A **communicator** is a pair *(group, context)*. The **group** is an ordered list of
world ranks; the **context** is an integer that partitions the message namespace.
Messages match only within a context (§4.3).

Communicators are created by `split` (collective, partition by colour), `dup` (same
group, fresh context), `create_group` (non-collective, from a known member list) and
`shrink` (§8.4). The world communicator has context `0` and contains every rank.

> **Rationale.** This is MPI's central abstraction and the one worth transplanting
> unchanged. Pre-MPI libraries addressed messages by `(destination, tag)`, so a
> library's messages could be intercepted by its caller whenever they collided on an
> integer. The agent-world version of that collision is not hypothetical: a reviewer's
> critique intended for the author of module A being consumed by the author of module
> B is the same bug, and the "one shared group chat" architecture makes it certain
> rather than merely possible. A communicator also bounds cost: a broadcast to a
> 4-member team costs four messages, not *p*.

`create_group` exists for the reason MPI-3 added it: `split` requires every member of
the parent to participate, which is impossible once some of them are dead.

### 2.3 Artifacts

All data crossing a communicator is an **artifact**: an immutable byte string with a
canonical serialisation, addressed by the SHA-256 of that serialisation. Artifacts
are stored once and referred to by **digest**.

Three consequences are normative:

1. **Broadcast is drift-free.** A tree broadcast MUST forward digests, never
   re-generated content. An implementation in which an interior rank retransmits
   text it has regenerated does not conform.
2. **Duplicate admission is free.** Admitting the same digest twice to a rank's
   context MUST cost the budget once.
3. **Replay is exact.** The event log plus the artifact store MUST be sufficient to
   replay the protocol without invoking any agent.

> **Rationale.** Point 1 is what makes logarithmic-depth collectives usable at all.
> A protocol in which agents relay content by restating it degrades with depth like
> a game of telephone, which would confine such a protocol to flat, root-centred
> patterns — precisely the patterns that make the root a bottleneck. Immutability
> converts depth from a quality risk into a pure latency win. It also draws the sharp
> line this design depends on: **broadcast can be lossless because it does not
> transform; reduction cannot be, because it must** (§6.3).

### 2.4 Handles

An agent MUST NOT be required to remember protocol state. Everything an agent is
given is a **handle**: a rank number, a communicator id, a window name, a slot name,
an artifact digest, an invocation id. Handles are short, opaque and verifiable.

> **Advice to implementors.** Take this further than seems necessary. Where an agent
> must issue a command, emit the *exact command string* rather than the parameters
> from which it could be constructed. Measured protocol-violation rates in agent
> populations are dominated by recall failures, not by comprehension failures, so a
> protocol surface that requires recognition rather than recall is materially more
> reliable.

### 2.5 The two ways to write a harness

**Harness-side (SPMD, recommended).** The harness author writes a `rank_main`
function executed once per rank by trusted host-side code. Every AgentMPI call is
made by that code; the agent is invoked as a kernel that transforms artifacts.

**Agent-side.** The agent itself issues AgentMPI operations through a command
interface.

Both MUST be supported. Implementations SHOULD document the harness-side form as the
default.

> **Rationale.** The distinction is *protocol in the harness* versus *protocol in the
> prompt*. In the second form, protocol conformance is a property of model behaviour:
> a rank that forgets to enter a barrier does not merely produce a worse answer, it
> prevents the population from making progress. Confining the protocol to host-side
> code makes conformance a property of the runtime. The agent-side form is retained
> because it is sometimes the only option, and because the difference between them is
> measurable and worth measuring.

---

## 3. Sessions, initialisation, finalisation

`init` associates the calling context with a job and a rank and returns a
**session**. There MUST be no process-global runtime state; two independent sessions
MUST be able to coexist in one process.

`finalize` releases the rank's lease and records its final accounting. A rank that
terminates without calling `finalize` MUST eventually be detected as failed (§8.2).

> **Rationale.** MPI-4 added sessions because `MPI_Init` is global state, so a library
> cannot initialise MPI without interfering with its caller. The defect is worse here,
> because an AgentMPI harness is very often itself a component invoked by a larger
> agent system. A protocol whose runtime is a process-wide singleton cannot compose,
> and composability is goal G1.

---

## 4. Point-to-point communication

### 4.1 Operations

| operation | semantics |
| --- | --- |
| `send(payload, dest, tag)` | Standard mode. Completes when the payload is stored and the envelope is in `dest`'s mailbox. Says nothing about the receiver. |
| `ssend` | Synchronous mode. Completes only once a matching receive has been posted. |
| `isend` / `irecv` | Nonblocking; return a request. |
| `recv(source, tag)` | Blocking receive. |
| `sendrecv` | Combined; MUST NOT deadlock when used symmetrically by all ranks in a shift. |
| `probe` / `iprobe` | Report an incoming message's metadata — critically its token count — without consuming it. |
| `mprobe` / `mrecv` | Matched probe: claim a message, receive it later. |
| `fetch(handle, view)` | Materialise all or part of an artifact. |
| `release(handle)` | Evict an artifact from this rank's context. |

`release` has no MPI counterpart: an MPI receive buffer is reused by the application
at will, whereas an agent's context is freed only by an explicit decision.

### 4.2 Ordering

**Non-overtaking.** If a rank sends two messages to the same destination on the same
communicator, and both match a given receive, the receive MUST select the earlier.
Implementations MAY provide the stronger guarantee that a mailbox is globally FIFO.

`ANY_SOURCE` receives provide no ordering guarantee across distinct senders.

> **Advice to implementors.** The reference implementation orders by a monotone
> message id, which yields the stronger global-FIFO property. It is worth having: agent
> harnesses are debugged by reading traces, and a globally ordered mailbox makes a
> trace explicable.

### 4.3 Matching

A receive with criteria `(ctx, source, tag)` matches the earliest queued message with
the same `ctx`, a `source` equal to the criterion or `ANY_SOURCE`, and a `tag` equal
to the criterion or `ANY_TAG`.

Tags beginning `_ampi:` are reserved for the implementation. User code using them MUST
be rejected with `ERR_TAG`.

> **Rationale.** MPI hides collective traffic by giving each collective a duplicated
> communicator. A reserved tag prefix achieves the same isolation for one fewer round
> trip, at the cost of one rule that user code must obey — and the rule is mechanically
> enforced, so it cannot be violated silently.

### 4.4 Transport modes

| mode | payload delivered? | charged to receiver's context on `recv`? |
| --- | --- | --- |
| `EAGER` | yes | yes |
| `RENDEZVOUS` | no; envelope only (digest, tokens, contract, synopsis) | no |
| `SYNCHRONOUS` | yes | yes |
| `AUTO` | `EAGER` at or below the eager limit, else `RENDEZVOUS` | as chosen |

The default is `AUTO`.

> **Rationale.** In MPI, eager and rendezvous are an invisible implementation choice
> because both deliver the same bytes; only latency differs. Here they differ in *what
> arrives*, and therefore in whether the receiving agent's context is consumed.
> Because they are semantically distinct, they MUST be visible. Making the mode
> explicit also gives the harness author the vocabulary for the most important
> decision they make: whether a rank needs the artifact or merely a reference to it.

A rendezvous envelope MUST carry a one-line **synopsis**. Without it, a receiver must
fetch in order to decide whether to fetch.

### 4.5 Eager credit and context safety

Every rank publishes an **unexpected-message budget**: a bound, in tokens, on the
total volume of unmatched eager messages it will accept. A sender whose eager message
would exceed the destination's budget MUST block, and MUST raise
`ERR_CONTEXT_OVERFLOW` if it cannot proceed by its deadline. Both the stall and its
resolution MUST be traced.

**Definition (context-safe program).** A program is *context-safe* if it completes for
every assignment of unexpected-message budgets, however small.

**Consequence.** A program in which every rank sends before any rank receives is
context-safe if and only if its payloads travel `RENDEZVOUS`.

> **Rationale.** This is a direct transplant of MPI's *safe program* discipline. MPI
> declines to guarantee that a program depending on buffering will work, because such
> programs run on one implementation and deadlock on another. The agent analogue is
> sharper: the scarce buffer is the receiving agent's context, and exceeding it does
> not merely deadlock, it silently degrades the agent's output. Making the budget
> explicit and blocking on it converts an invisible quality failure into a reported,
> attributable stall — which is the most useful thing the protocol can do about it.

---

## 5. Contracts and views

### 5.1 Contracts

A **contract** is a typed description of an artifact with a structural part and a
semantic part:

- `name`, `kind` (`text`/`json`/`patch`/`none`)
- `required`: keys that MUST be present
- `nonempty`: keys that MUST be present and carry a value
- `min_tokens`, `max_tokens`
- `must_match`, `must_not_match`: regular expressions
- `semantics`: a natural-language postcondition, carried to the consumer

**Matching.** A send's contract and a receive's contract match iff their names and
kinds agree and the receiver's `required` set is a subset of the sender's. Only
`name`, `kind` and `required` participate in matching. A mismatch MUST raise
`ERR_TYPE`.

**Checking.** A send MUST validate its payload against its contract. An agent
invocation MUST validate the agent's output and MAY retry with the diagnosis
appended.

**Evaluability.** An implementation that enforces a volume bound MUST also expose the
means to evaluate it, and a task carrying a bounded contract SHOULD carry the concrete
invocation that performs the check.

> **Rationale.** A constraint the constrained party cannot evaluate is not a constraint
> but a guess, and parties guess conservatively. In our own experiments a contract bounded
> a rank's output at 450 tokens and the runtime checked it, while the rank had no way to
> measure a candidate; one rank submitted half the items it could have and said so, and
> another reverse-engineered the counter from prompt sizes the runtime had incidentally
> reported, then fitted a quarter more content. The runtime owned the counter, used it to
> accept or reject, and did not expose it. Whether the counter matches a particular
> provider's tokeniser matters far less than the producer and the checker using the same
> one, which exposing it guarantees.

> **Rationale.** MPI datatypes do two jobs. The first is *matching*: a type signature
> on each side, checked at runtime, which turns a class of silent corruption into a
> loud error. The agent analogue is immediate — an agent routinely returns prose where
> an object was requested, or six sections where eight were asked for — and catching
> it at the boundary prevents a malformed artifact from poisoning a downstream
> reduction.
>
> `nonempty` is separated from `required` because conflating them is a trap that costs
> correctness. A term sheet for a section containing no proper nouns legitimately has
> an empty term map; a contract that rejected it would make the rank retry, fail, and
> abandon peers already blocked in a collective — over a correct answer.

### 5.2 Views

A **view** names a projection of an artifact without materialising it: `contiguous`,
`vector(offset, count, stride)`, `indexed`, `keys`, `jsonpath`, `head`, `tail`,
`digest_only`.

> **Rationale.** This is the *second*, and more valuable, job of MPI datatypes:
> `MPI_Type_vector` exists so a program can communicate the boundary column of a
> matrix without packing a copy. It is the job every agent framework omits, and it is
> the mechanism by which an AgentMPI program stays inside its context budget: a rank
> receives *what it needs* out of a 400k-token artifact rather than the artifact.
> `vector` in particular expresses a block-cyclic decomposition — give rank *r* every
> *p*-th chapter — with no coordinator materialising the whole.
>
> `head`/`tail` have no MPI analogue and are the escape hatch that makes budget
> compliance always achievable. Because a silent truncation is a correctness hazard,
> an implementation MUST trace when they fire.

### 5.3 Validators

A **validator** is a checker with a declared cost and a declared failure class.
`verify` runs validators cheapest-first.

> **Rationale.** Structural checks are cheap, local and deterministic, and catch
> failure class F3. Only an *independent computation* catches F4 — a compiler, a test
> suite, a cross-checking rank, or agreement among replicas. Because F4 dominates in
> agent systems, verification is not an optional extra but the principal fault-tolerance
> mechanism (§8.6), and its cost belongs in the cost model.

---

## 6. Collective communication

### 6.1 General rules

Collectives MUST be invoked in the same order by every member of a communicator.
Every collective MUST accept a deadline. Collective algorithms MUST be expressed in
terms of point-to-point operations so that their message counts, rounds and fold
depths are observable.

> **Advice to implementors.** Do not short-circuit a collective through the fabric as
> a shared variable. The whole value of the abstraction is that the *shape* of the
> communication determines cost and quality, and a centralised implementation hides
> exactly the effects a harness author needs to see. The one exception is the
> arrival-counting barrier (§6.2), which is centralised precisely because it must be
> able to name the ranks that did not arrive.

### 6.2 Barrier

`barrier(deadline, policy)`. Policies:

| policy | behaviour |
| --- | --- |
| `WAIT` | Block indefinitely. MPI semantics. |
| `RAISE` | Raise `ERR_TIMEOUT` at the deadline. |
| `PROCEED` | Continue with those who arrived; report the absentees. |
| `SHRINK` | Mark absentees failed and renumber in place. |
| `REVOKE` | Mark absentees failed, revoke the communicator, raise. |

Algorithms: `dissemination` (⌈log₂p⌉ rounds, `p⌈log₂p⌉` messages, correct for any
*p*), `linear` (`2(p−1)` messages), `central` (arrival counting; the only algorithm
that can identify absentees, and therefore required for `PROCEED`/`SHRINK`/`REVOKE`).

> **Rationale.** An unconditional barrier is a liveness bug waiting to happen: the
> probability that all *p* agent ranks arrive within a fixed window falls off with *p*.
> "The agents are waiting for each other" is the most frequently reported pathology in
> multi-agent postmortems, and it is what an MPI-style barrier does by design. The
> policy is the harness author's declaration of what a missing participant *means*: a
> missing chapter degrades a translation, whereas a missing module kills a build.

### 6.3 Reduction and the algebra of operators

An **operator** declares: `commutative`, `associativity` ∈ {`EXACT`, `APPROX`,
`NONE`}, `idempotent`, and an `identity`.

**Normative constraints.**

- An operator declared `NONE` MUST be evaluated only by the serial chain. Requesting
  a tree MUST raise `ERR_ARG`.
- A non-commutative operator MUST be evaluated in rank order. A binomial tree
  satisfies this only when the root is rank 0; for any other root, an implementation
  MUST refuse the tree rather than silently reorder.
- The serial left fold is the **reference semantics**. For an `EXACT` operator, every
  algorithm MUST produce the fold's result.
- Every reduction MUST report the **fold depth** it induced.

| algorithm | rounds | applications | fold depth |
| --- | --- | --- | --- |
| `chain` | p−1 | p−1 | p−1 |
| `flat` | 1 | p−1, all at the root | p−1 |
| `binomial` | ⌈log₂p⌉ | p−1 | ⌈log₂p⌉ |

> **Rationale.** This is where AgentMPI differs from MPI most sharply.
>
> MPI requires a user operator to be associative and then reorders freely. The
> guarantee is deliberately weak — an implementation may pick different trees for
> different process counts or different runs, which is why floating-point `MPI_SUM` is
> not bitwise reproducible — and practitioners accept it because the discrepancy is
> bounded by rounding error.
>
> An operator implemented by a language model is not associative, is lossy, is
> expensive, and is non-deterministic. The loss compounds with the **depth** of the
> reduction rather than with the number of applications, because depth counts how many
> times an item is re-summarised on its way to the root. So the three algorithms above
> differ in fidelity even though all perform p−1 applications, and the prediction —
> which the accompanying experiments test — is that the tree wins decisively, because
> it wins on rounds *and* on depth.
>
> The consequence for the interface is that **algorithm selection for a semantic
> reduction is a quality decision, not merely a cost decision**, and therefore MUST
> be exposed rather than hidden behind a tuning table.
>
> A second, subtler effect: a summarising operator given a fixed output budget
> compresses two inputs and sixteen inputs to the same length, so the root's answer
> over-represents whichever subtree merged last. `MPI_SUM` cannot exhibit this — it
> weights every contribution equally by construction. The operator interface therefore
> exposes the number of leaves an accumulator represents, so a budget can be allocated
> proportionally.

**Prefer exact operators.** `UNION` — set union, or key-wise union with
deterministic conflict retention — is exact, associative, commutative and idempotent.
A glossary merged with `UNION` is identical at every rank regardless of tree shape; a
glossary merged by an agent is not. Harnesses SHOULD use exact operators wherever the
combination is genuinely set-like, exactly as an MPI programmer prefers `MPI_SUM` to
a user-defined operator.

> **Advice to harness authors.** An exact merge over a keyed map presupposes that each
> key has one correct value, and the resulting agreement is *binding on every rank*.
> That presupposition is stronger than it looks. In our translation experiment a
> glossary bound the English term *pounds* to the rendering for pounds sterling,
> correct nearly everywhere; one section used the word for body weight, and the rank
> — correctly obeying a glossary the harness had declared binding — produced a wrong
> sentence. `UNION`'s conflict retention handles two ranks *disagreeing* about a key;
> it cannot represent one key having two correct senses.
>
> The lesson generalises past glossaries. Whenever a collective imposes a decision
> globally, choose the key so that a globally correct answer exists — here, key on
> (term, sense) rather than on term — and be aware that a consistency metric will
> score a uniformly wrong decision as a perfect one. Agreement is not correctness, and
> a protocol that makes agreement cheap makes it cheap to agree on something false.

### 6.4 Allreduce and the divergence hazard

`reduce_bcast` computes one result and broadcasts it, so every rank holds a
byte-identical value. `recursive_doubling` has each rank compute the result itself.

For an `EXACT` operator these are equivalent. For a lossy operator they are not:
under `recursive_doubling`, each rank performs its own fold sequence, so the *p*
results differ and **the population silently disagrees about the value it just
agreed on**. An implementation MUST record a divergence-risk flag when a lossy
operator is combined with an algorithm in which ranks compute independently.

> **Advice to implementors.** Default to `reduce_bcast` for lossy operators. It costs
> a factor of two in rounds and buys the property that "agreement" means agreement.

### 6.5 Scan

`scan` / `exscan`: rank *i* receives the reduction over ranks `0..i` (inclusive) or
`0..i−1` (exclusive).

> **Rationale.** Scan is the most under-used collective for agent work. A task with
> sequential dependence but parallel bulk — translate chapter *i* consistently with the
> names and register established in `0..i−1`, write a section that must not contradict
> earlier ones — *is* a prefix computation. A harness that runs such a task strictly
> sequentially pays *p* agent latencies; one that ignores the dependence produces
> inconsistent output. `recursive_doubling` delivers every prefix in ⌈log₂p⌉ rounds at
> the price of `p log p` operator applications: it trades *more* operator work for
> *fewer* rounds, which is the opposite of the usual trade and the right direction
> whenever per-invocation latency dominates (§10).

### 6.6 Other collectives

`bcast` (`flat`, `binomial`, `chain`, `scatter_allgather`); `scatter`/`scatterv` and
`gather` (`linear`, `binomial`); `allgather` (`ring`, `recursive_doubling`, `bruck`,
`gather_bcast`); `alltoall` (`pairwise`, `linear`, `bruck`); `reduce_scatter`.

`gather` MUST default to `AUTO` transport and non-admission, so that a fan-in of *p*
artifacts of *n* tokens does not present the root with *pn* tokens of context.

`alltoall` costs `p(p−1)` messages and is the natural expression of全-way peer review.
Harnesses SHOULD prefer a neighbourhood collective on a review topology (§7) unless
the full cross-product genuinely carries information.

`scatterv` (variable counts) SHOULD be preferred over `scatter` for work
decomposition: per-item agent cost varies by an order of magnitude, so an
equal-*count* split is limited by its largest item.

---

## 7. Topologies

A communicator MAY carry a virtual topology: **Cartesian** (`dims`, `periods`, with
`shift` returning `(source, destination)` and `PROC_NULL` at non-periodic
boundaries) or **distributed graph** (explicit in- and out-neighbour lists).

Neighbourhood collectives — `neighbor_allgather`, `neighbor_alltoall`,
`halo_exchange` — communicate only with neighbours, at cost Θ(degree) rather than
Θ(p).

> **Rationale.** MPI's topologies buy expressiveness *and* the chance for the
> implementation to remap ranks onto hardware. The second benefit does not transfer:
> there is no interconnect to be near. The first becomes far more valuable, because
> the communication pattern *is* the harness's design and it determines the cost. A
> full group conversation over *p* agents costs Θ(p²) messages and Θ(p²n) tokens; the
> same information over a ring or a review graph costs Θ(p) and Θ(pn). Restricting
> communication to a topology is how a harness buys scalability, and refusing to make
> that restriction is why group-chat architectures do not scale.
>
> Halo exchange is the most valuable non-obvious transfer from HPC. A task whose parts
> each depend on their neighbours *looks* sequential; expressed as a stencil it is one
> barrier-separated parallel step plus a Θ(1)-degree boundary exchange.

---

## 8. Failure model and fault tolerance

### 8.1 Failure classes

| class | description | detector | mitigation |
| --- | --- | --- | --- |
| **F1** fail-stop | crashed, killed, or past a hard deadline | lease expiry | re-incarnate, or shrink |
| **F2** fail-slow | alive but past its latency budget | operation deadline, distribution-derived | hedge with a duplicate |
| **F3** fail-noisy | violates its structural contract | contract check | retry with the diagnosis |
| **F4** fail-plausible | well-formed, confident, wrong | **independent verification only** | replicate and compare; check with an oracle |
| **F5** fail-greedy | exhausted its context budget | context accounting | rendezvous transport plus views |
| **F6** adversarial | deviates from the protocol deliberately | — | out of scope |

> **Rationale.** MPI's fault-tolerance work assumes fail-stop, and importing that
> assumption is why so much agent-reliability engineering consists of retry loops that
> do not help. The load-bearing row is **F4**. A rank returning a confident wrong
> answer is undetectable by any amount of timeout tuning, retry logic or schema
> validation, because none of those examine whether the answer is *right*. It is the
> exact analogue of silent data corruption, and the HPC answer to silent data
> corruption is not checkpoint/restart — which faithfully preserves the corruption —
> but algorithm-based fault tolerance: carry redundant information that lets the
> computation check itself. Hence §5.3, and hence the position that the quantity to
> optimise is the *verification budget*, not the checkpoint interval.

### 8.2 Detection

A rank holds a **lease** and MUST renew it. An expired lease makes the rank a
candidate for F1. The detector is *eventually perfect*, not perfect: a slow rank is
indistinguishable from a dead one.

Straggler thresholds (F2) MUST be derived from the run's own latency distribution,
not fixed in advance.

> **Rationale.** FLP says consensus is unattainable with an unreliable detector in an
> asynchronous system. AgentMPI takes the same escape as HPC: assume partial synchrony
> with a known lease bound, accept that a rank whose lease expires is *declared* dead
> whether or not it is, and use `revoke` to make the declaration self-fulfilling so
> that the population's view stays consistent even when the detector was wrong.
>
> A fixed straggler timeout is either useless or harmful, because the spread between
> the median and the tail of an agent invocation is routinely an order of magnitude
> and is driven by output length rather than queueing.

### 8.3 Revoke

`revoke(comm)` makes a communicator permanently unusable for **every** member.
Operations on it, *including operations already blocked inside a collective*, MUST
raise `ERR_REVOKED`. Revocation MUST be irreversible, and a blocked operation MUST
consult shared state rather than a local cache so that it actually learns.

> **Rationale.** This is ULFM's least obvious and most necessary primitive. Without it
> a rank that notices a failure cannot rescue the others, because they are all blocked
> waiting for the dead peer — and it cannot first reach agreement, because the group is
> broken. Irreversibility matters: a communicator that could be un-revoked would let
> two ranks disagree about whether it is usable.

### 8.4 Shrink

`shrink(comm)` returns a new communicator over the survivors, renumbered
contiguously. Callers MUST agree on the survivor set.

`shrink_in_place(comm, absent)` marks ranks inactive and keeps the numbering.

> **Rationale.** Renumbering makes subsequent collectives cheap again but invalidates
> the harness's rank-to-work mapping — and for agents that mapping is usually expensive
> to recompute, because "rank 7 owns the parser" is baked into prompts and artifacts.
> In-place shrink is FT-MPI's `BLANK` mode rather than its `SHRINK` mode, and it is
> often the better trade here even though it leaves holes in the rank space.

### 8.5 Agree

`agree(comm, value) -> bool` returns the conjunction over live ranks, consistently:
either every survivor returns the same value or all of them raise.

> **Rationale.** This is what makes "did the integration step succeed?" answerable when
> some builders died mid-step. An allreduce over logical AND would simply hang.
> Implementations route agreement through a reliable shared service; the specification
> states that requirement openly rather than pretending to have circumvented FLP.

### 8.6 Redundancy and supervision

`replicate_and_compare(outputs, key)` reports the modal answer, the number of
distinct answers, and the consensus fraction. Comparison MUST be on a
harness-supplied projection, since byte equality is meaningless for prose.

Supervisors implement OTP's restart strategies — `one_for_one`, `one_for_all`,
`rest_for_one` — bounded by a restart intensity, exceeding which escalates.

> **Rationale.** MPI contributes the communication algebra and has no recovery policy;
> Erlang/OTP contributes the recovery discipline and no collectives. The two are
> orthogonal, each field solved what the other ignored, and AgentMPI takes both. The
> intensity bound matters especially here: an unbounded restart loop converts a local
> fault into a cost overrun, which is the agent-world version of the crash loop OTP's
> limit was invented to stop.
>
> One caution about replication: replicating a language model does not give independent
> failures, because correlated errors are the norm. Agreement is evidence, not proof,
> which is why the consensus *fraction* is reported rather than a bare boolean.

### 8.7 The harness author's obligation

**A local failure MUST NOT remove a rank from a collective.** A rank that cannot
compute its contribution MUST still enter the collective, contributing a degraded
value or an identity element, and record the degradation.

> **Rationale.** This is the rule most easily violated and most expensive to violate.
> If a local exception propagates out of a rank's main function, that rank never
> reaches the collective its peers are already blocked inside, and one recoverable
> local failure becomes a whole-population hang. MPI programs are written the other way
> round on purpose. Escalation must be a deliberate decision made by a barrier policy
> or a supervisor, never an accident of exception propagation.

---

## 9. Errors

Error **classes** are stable identifiers a harness may branch on: `ERR_ARG`,
`ERR_RANK`, `ERR_TAG`, `ERR_COMM`, `ERR_TRUNCATE`, `ERR_CONTEXT_OVERFLOW`,
`ERR_TYPE`, `ERR_TIMEOUT`, `ERR_REVOKED`, `ERR_PROC_FAILED`, `ERR_RMA_CONFLICT`,
`ERR_FABRIC`, `ERR_VALIDATION`, `ERR_OTHER`.

The default error behaviour MUST be *return*, not *abort*.

> **Rationale.** MPI defaults to `MPI_ERRORS_ARE_FATAL` and declares its state
> undefined after an error, which is defensible when errors are rare bugs. Here they
> are routine events with well-defined meanings, so the default is inverted.

---

## 10. Cost model

Volume is measured in **tokens**. A message of *n* tokens costs

> T(n) = α + nβ,  C(n) = n·γ

where α is per-invocation latency, β is marginal time per token and γ is marginal
price per token. Three properties distinguish this from the Hockney model.

**α is enormous.** For MPI, α is microseconds and bandwidth is gigabytes per second,
so the ratio rewards reducing message *volume*. For agent ranks α is seconds and
effective bandwidth is on the order of 10¹–10² tokens/s, so the ratio rewards
reducing the number of **rounds**. Latency-optimal algorithms therefore win over a
wider range of sizes than in MPI. The measured α/β crossover is a few hundred
tokens — the size of a typical agent artifact — so neither term may be neglected, and
every collective MUST report both a message count and a token volume.

**Price is a second, independent axis.** Wall time and money are not proportional:
running *p* ranks divides time by up to *p* and divides price by nothing, and
coordination messages are pure added price. A harness therefore has two objectives
and the model MUST report both.

**Fidelity is a third axis.** For a lossy operator, modelled surviving fidelity after
a fold of depth *d* is `F(d) = (1−δ)^d` for a measured per-application loss δ.

Scaling laws the specification expects implementations to report:

- Amdahl `S(p) = 1/(f + (1−f)/p)`, where *f* is dominated by phases that must be done
  by one rank — plan, integrate, arbitrate — and those do not shrink.
- Universal Scalability Law `S(p) = p / (1 + σ(p−1) + κp(p−1))`. The **κ** term is the
  one that matters: an all-to-all agent conversation has κ > 0 by construction, so its
  throughput has a maximum and then *declines*. Fitting κ tells a harness author
  whether their coordination pattern has a ceiling.
- Karp–Flatt, which reveals a growing coordination cost that a plain speedup plot
  hides.

> **Advice to implementors.** Ship closed-form cost formulas *and* check them against
> measured message counts. An implementation whose measured counts disagree with its
> own model has a bug in one of the two, and having both is how the discrepancy gets
> found.

---

## 11. One-sided operations

A **window** is a named, explicitly created region of shared state over a
communicator, divided into **slots**. Slots, not byte offsets, are the unit of
versioning and locking.

| operation | semantics |
| --- | --- |
| `get` | Read a slot and record the version observed. |
| `put` | Overwrite. With `expect_version`, a compare-and-swap. |
| `accumulate(op)` | Atomic read-modify-write. The operator MUST be exact. |
| `get_accumulate`, `fetch_and_op` | Atomic fetch-and-modify. |
| `compare_and_swap` | Conditional swap on the canonical digest. |
| `lock` / `unlock` | Passive-target epoch, shared or exclusive, **with a lease**. |
| `fence` | Collective epoch boundary; ends the previous epoch and invalidates private copies. |
| `sync` | Discard this rank's private copies. |
| `flush` | Complete outstanding operations. |

**Memory model.** The default MUST be `SEPARATE`: each rank has a private copy that
may be stale and is reconciled only at synchronisation points.

An implementation MUST record a **staleness violation** when a rank issues a `put`
based on a version that is no longer current.

`accumulate` MUST reject a lossy operator.

Locks MUST carry leases, and reclamation of an expired lease MUST be traced.

> **Rationale.** Every multi-agent system grows a shared scratchpad — a design
> document, a glossary, an interface file, a task board — and every one gets the
> concurrency wrong the same way: two agents read it, both edit a private copy, and
> the second write discards the first's work. MPI-3's one-sided chapter supplies the
> vocabulary for that bug and the tools to avoid it. The distinction between `put`
> (blind overwrite, the operation that loses work), `accumulate` (combine, so
> concurrent contributions cannot clobber) and `compare_and_swap` (optimistic
> concurrency) is exactly what such a harness needs and never articulates.
>
> `SEPARATE` is the correct default because for an agent the private copy is not a
> hardware artefact: it is the copy of the document sitting in the agent's context from
> ten minutes ago, and it *is* stale, because peers have edited it since. `sync` is
> "re-read before you edit". Recording staleness violations turns the most common
> multi-agent data race into a counted, attributable event — which is more valuable
> than preventing it, because a harness that legitimately overwrites must still be
> able to.
>
> `accumulate` rejects lossy operators because the read-modify-write happens inside an
> atomic section, and an implementation cannot hold one across a model call. A harness
> needing judgement in the combine must `lock`, `get`, reason, `put` with
> `expect_version`, `unlock` — and accept the serialisation that implies. Making that
> trade-off explicit is the point: a harness that puts a semantic critical section on
> its critical path has built a sequential program, and the trace will show it as lock
> wait time.
>
> Leases have no MPI counterpart because a dead MPI process takes the job with it. Here
> a lock held by a vanished agent would deadlock the window forever.

**Dynamic processes.** `spawn(comm, count)` appends ranks to the world rank space and
returns a communicator over the parent plus the children. AgentMPI does not model
intercommunicators: the distinction buys little without distinct address spaces, and
the complexity is part of why MPI-2's dynamic process management went unused.
Spawning matters far more here than in MPI, where a batch scheduler fixes the node
count: "the reviewer found three more subsystems needing owners" is an ordinary event.

---

## 12. Executors and the process manager

An **executor** turns a prompt into an artifact. The protocol MUST NOT depend on how.
Conforming executor kinds include a deterministic function, a calibrated simulator, a
recorded replay, and a **broker** that publishes invocations for external workers to
claim.

The broker's queue MUST be **per rank**, claims MUST carry a deadline, and a claim
whose holder disappears MUST return to the queue.

> **Rationale.** MPI separates the interface from the process manager, and that
> separation is why the same program runs under Slurm, under Hydra, and inside a unit
> test. The same separation lets an AgentMPI harness run against real agents, against
> a simulator, and against a recorded replay without changing a line — which is the
> only way protocol behaviour can be regression-tested, since real agent runs are
> neither free nor reproducible.
>
> The queue is a *pull* queue rather than a push because a pushed invocation would
> require the harness to know how to start an agent, coupling it to a vendor. Pulling
> lets the population be launched, scaled and re-incarnated entirely outside the
> harness — which is required, because an agent session's lifetime is controlled by its
> host, not by the program that wants its output.
>
> The queue is per rank because a rank is a durable role with accumulated state; a
> worker stealing another rank's work would destroy the identity the design rests on.

---

## 13. Tracing and replay

Every state transition MUST append an event to a totally ordered log, in the same
transaction as the transition it describes. The log plus the artifact store MUST
suffice to replay the protocol and to compute every cost and quality measure.

> **Rationale.** MPI's tooling interfaces are opt-in and out-of-band, which is
> reasonable when a run can be repeated cheaply. An agent run cannot: it is expensive
> and it is not reproducible, so a bug that was not traced is a bug that cannot be
> investigated. The trace is therefore part of the protocol rather than an add-on, and
> tracing is unconditional.

---

## 13a. Operational requirements

Two requirements sit outside the protocol proper but are necessary for a run to mean
anything, and both were learned the hard way.

**The runtime version MUST be pinned per job.** Protocol *state* lives outside the
agents and is durable (§2.5), which is the design's central move. The runtime *code*
is shared mutable state, and the specification says nothing about it. An
implementation SHOULD record its version in the fabric at job creation and workers
SHOULD refuse to serve a job whose recorded version differs from their own. During our
own experiments a worker crashed inside the runtime because the package was edited
while a live population executed against it; the honest description is that we
hot-patched a running job, and no amount of durable protocol state protects against
that.

**The verifier MUST be versioned, and results MUST be re-scorable.** A
verification-based fault-tolerance scheme (§8.6) inherits the reliability of its
verifier, so an implementation MUST record which version of an acceptance oracle
produced a result, and a harness SHOULD keep the artifacts needed to re-score a run
offline. Our own oracle contained a case that contradicted the specification it
tested, and the plumbing that carried its report back to the population parsed that
report incorrectly — telling the population that a passing build had failed. Neither
defect was visible from the population's behaviour; both were visible immediately once
runs could be re-scored from stored artifacts.

## 14. Conformance checklist

An implementation conforms if it provides: communicators with private contexts and
`split`/`dup`/`create_group`; non-overtaking point-to-point with wildcard matching and
a reserved tag namespace; visible eager/rendezvous modes with published
unexpected-message budgets and blocking credit; contracts with the matching rule of
§5.1 and views; every collective of §6 expressed over point-to-point, with declared
algorithms, reported fold depth, and the operator-algebra constraints enforced;
barriers with all five policies; windows with slots, leases, epochs, the `SEPARATE`
default and staleness reporting; `revoke`, `shrink`, `agree`, and lease-based
detection; the error classes of §9 with return-not-abort default; the cost accounting
of §10; a pluggable executor including a broker with per-rank pull queues; and an
unconditional, totally ordered event log.
