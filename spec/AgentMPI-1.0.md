# AgentMPI: a message passing interface for multi-agent systems

**Specification version AgentMPI/1.0 — normative**

This document is organised like the MPI standard: normative text, with the
reasoning in explicit **Rationale** and **Advice** notes. That structure is the
most useful thing MPI's standardisation process produced, and it is adopted here
for the same reason — a reader who wants to know *why* a rule exists should not
have to reverse-engineer it from an implementation.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are used in the RFC 2119
sense. Where this document and the reference implementation disagree, this
document is the specification and the implementation has a bug.

---

## S0. Status, scope, and philosophy

### S0.1 What this document is

AgentMPI is an interface by which independent LLM agents coordinate. This
document specifies **semantics**, not an implementation. A conforming
implementation may store its state anywhere, use any transport, and select any
algorithm permitted by S7, provided the observable behaviour matches this text.

The reference implementation that accompanies this specification is one such
implementation and is normative only where this document says so. Three transports
ship with it; the conformance suite of S14 runs unchanged against all of them, and
that is the property this document is written to make possible.

### S0.2 What AgentMPI is not

AgentMPI is not a multi-agent system, an agent framework, a prompting technique,
or a tool-access standard. It stands to multi-agent systems as MPI stands to
parallel applications: a library that harness authors call, not a harness. It does
not decide how many agents to run, what they should do, how to prompt them, which
model to use, or how to recover from failure. It provides the mechanisms with
which those decisions are expressed.

AgentMPI is **semantics-thin**. It does not interpret payloads. It has no
ontology, no speech acts, no commitment semantics, no notion of belief or
intention. A message is an opaque body plus an envelope of size and provenance
metadata.

> **Rationale.** This is a deliberate rejection of the KQML/FIPA-ACL lineage,
> whose mentalistic semantics — a message means that the sender *believes* p, and
> *intends* that the receiver come to believe p — proved both unverifiable and
> unnecessary. Wooldridge's objection is decisive and applies with more force to a
> language model than to the symbolic agents it was aimed at: there is no way to
> check, from outside, that an agent's utterance corresponds to any internal
> state, so a semantics defined in terms of that state cannot be conformed to or
> tested against. MPI took the other road — standardise the mechanism, leave
> meaning to the application — and it is the road that produced an interface still
> in use three decades later.

AgentMPI also does not provide Byzantine fault tolerance, cross-organisational
authentication, or agent discovery across administrative domains. It assumes a
single administrative domain, as MPI assumes a single job.

### S0.3 Design goals, in priority order

1. **Portability of harnesses across agent hosts.** A harness written against
   AgentMPI SHOULD run whether its ranks are Cursor subagents, API-driven agent
   loops, or humans at terminals.
2. **Composability.** A harness MUST be able to call another such harness as a
   subroutine without their communication interfering. This is the goal that
   forces communicators to exist, and it is the goal every current agent framework
   fails.
3. **Explicit locality of information.** No hidden data movement. If an agent
   knows something, either it produced it or the harness moved it, visibly.
4. **Context as a first-class, accounted resource.** Context exhaustion is the
   dominant scaling failure of agent harnesses. It is treated here as memory
   footprint is treated in MPI: measured, bounded, and subject to explicit flow
   control.
5. **Cost transparency.** Every operation's cost MUST be attributable — to a rank,
   in tokens, in wall time, and in fold depth. Cost here spans three
   non-interchangeable currencies (S3).
6. **Failure as a normal condition.** Agents fail constantly and in more ways than
   processes do. The interface exposes failure rather than hiding it, and provides
   tools rather than policy — ULFM's stance, adopted wholesale.
7. **Standardise only what is understood.** Where a question is open, this version
   provides a hook and no policy. Appendix B names the omissions.

### S0.4 The five asymmetries with MPI

AgentMPI mirrors MPI's structure but not its cost model. Five differences drive
every deviation in this document.

| # | Property | MPI process | AgentMPI executor |
|---|---|---|---|
| A1 | Determinism | Deterministic given inputs | Nondeterministic; may produce a different, plausible, wrong result |
| A2 | Scarce resource | Memory and network bandwidth | **Context window occupancy** |
| A3 | Cost of an operator | Nanoseconds; free relative to communication | Seconds to minutes; dominates communication entirely |
| A4 | Latency distribution | Tight, predictable | Heavy-tailed; the maximum of `p` samples is far above the median |
| A5 | Failure model | Fail-stop, rare, kills the job | Individual, frequent, partial, and sometimes **silent** |

Consequences, each developed later: A2 forces the eager/rendezvous threshold to be
denominated in tokens and introduces views (S6); A3 inverts MPI's
collective-algorithm selection rules (S7.7); A4 motivates quorum collectives
(S7.8); A5 requires the whole of S10; A1 requires reduction reproducibility — and,
separately, reduction *consistency* — to be explicit, declared properties (S8.4).

### S0.5 Conformance

An implementation conforms to AgentMPI/1.0 at a stated **level** if it implements
every operation of that level with the specified semantics, reports
`AgentMPI/1.0` as its protocol version, names its level and its omissions when
asked, and passes the conformance suite (S14) at that level.

| Level | Requires |
|---|---|
| L1 | Lifecycle, identity, point-to-point, barrier, the context ledger, tracing |
| L2 | L1 plus the full collective catalogue and the runtime reduction operators |
| L3 | L2 plus windows, leased locks, and topologies |
| L4 | L3 plus fault tolerance: revoke, shrink, agree, respawn, recovery briefing |
| L5 | L4 plus agent operators, views, contracts, and interface declaration |

Operations outside an implementation's level MUST fail with
`AMPI_ERR_UNSUPPORTED` rather than be absent, so that a harness discovers the
boundary by asking rather than by crashing.

> **Rationale.** MPI-1's smallness is why every vendor shipped it within about two
> years, and MPI's later size is the most common complaint about it. Levels let a
> minimal implementation be a real one.

---

## S1. The execution model

### S1.1 Ranks, epochs, and the universe

A **job** consists of `P` **ranks**, numbered `0..P-1`. `P` is fixed at job
creation and MUST NOT change; a population that grows does so by creating a new
communicator, not by mutating the world.

A rank is a **durable role**, not a process. It is defined by its number, its
mailbox, its context budget, and its accumulated accounting. Its physical
embodiment is an **executor**: one agent session bound to the rank for a bounded
interval. At most one executor occupies a rank at any instant. An executor
occupying rank `r` is identified by the pair `(r, e)` where `e` is the rank's
**epoch**, a monotonically increasing integer.

> **Rationale.** In MPI a rank *is* a process, and the identification is harmless
> because MPI processes live for the whole job. An agent session does not: it is
> ended by a timeout, by context exhaustion, or by the hosting environment. If rank
> identity were tied to session identity, every such event would force a
> communicator rebuild and invalidate the harness's mapping from rank to work.
> Separating the two makes session termination an ordinary, recoverable event.

The epoch is a **fencing token**. Any operation issued by an executor whose epoch
is not the rank's current epoch MUST fail with `AMPI_ERR_FENCED` and MUST have no
effect.

> **Rationale.** Leases alone are insufficient. An executor cannot know that its
> lease expired while it was mid-step, so between expiry and its next call there is
> a window in which two executors believe they own the rank. A monotone token
> checked on every operation closes that window. This is the standard fencing
> argument for distributed locks, applied to executor identity, and it is what
> makes a *zombie* — an executor still running after being declared failed —
> harmless rather than corrupting.

### S1.2 Executor identity is ambient, and MUST be assertable

An implementation MUST make a rank's identity available to its executor without
the executor having to supply it.

An implementation MUST also provide a way for an executor to **assert** its
identity and have the operation fail, *before taking effect*, if the assertion
does not hold (`AMPI_ERR_IDENTITY`).

An implementation SHOULD additionally:

* **Echo the acting identity on every operation's output.** When identity is
  ambient, the only defence against it having changed is being told, on every
  call, who you just were.
* **Issue a per-rank launch token.** The launcher places a secret in the rank's
  environment and records it. If the ambient rank and the token disagree, the
  runtime MUST fail with `AMPI_ERR_IDENTITY` and SHOULD name the rank the token
  actually belongs to.
* **Prefer an explicit root argument over an environment variable**, so that an
  agent that has noticed its environment drifting has a way to override it.
* **Treat re-initialisation of a *running* rank as a heartbeat**, not a new epoch.

> **Rationale, and a correction.** An earlier draft said that ambient identity
> eliminates the most frequent agent error by a wide margin — passing the wrong
> rank identifier. It does. What it does not do is what that draft implied.
>
> Making identity ambient eliminated the error we designed it to eliminate and
> replaced it with a strictly worse one: agents silently *being* the wrong rank. On
> one agent host, shell sessions turned out to be shared between concurrently
> running agents, so the rank environment variable was overwritten between one call
> and the next. The journals record the result precisely: in each of four
> independent runs, one specific rank accumulated eight or nine initialisation
> events, one from nearly every other agent in the job. In one run the victim
> rank's own calls were credited elsewhere, its lease starved, and it was declared
> failed while actively working.
>
> Ambient identity is still right, because an agent should not have to thread its
> rank through every call and will make mistakes if forced to. But ambient identity
> *alone* assumes the environment is trustworthy, and in a shared host it is not.
> The fix is to make the assertion available and cheap.

### S1.3 The two planes

A conforming implementation has a **control plane** and a **data plane**, and they
have different cost structures.

* The **control plane** carries envelopes, handles, arrival notifications and
  synchronisation. It MAY be a shared medium that every rank can read; its cost is
  measured in operations and rounds.
* The **data plane** carries payload *bodies into executor context windows*. It is
  unavoidably private and per-rank; its cost is measured in **tokens**.

This distinction is the single most important structural fact about AgentMPI. It
means control collectives can be `O(1)` rounds while data collectives whose
operator an agent must evaluate are bounded below by the number of operator
applications on the critical path.

> **Advice to implementors.** A shared control plane changes some of MPI's
> answers. A counting barrier costs `2(p-1)` control-plane operations against a
> dissemination barrier's `p·log₂p` at *every* size, because every rank can read
> every other rank's arrival — so the crossover MPI has does not exist, and the
> counting barrier, which is the only algorithm that can name the ranks that did
> not arrive, is also the cheap one. An implementation whose control plane is not
> shared will find MPI's crossover intact and SHOULD say so.

### S1.4 The two ways to write a harness

**Harness-side (SPMD, recommended).** The harness author writes a `rank_main`
executed once per rank by trusted host-side code. Every AgentMPI call is made by
that code; the agent is invoked as a kernel that transforms artifacts.

**Agent-side.** The executor itself issues AgentMPI operations through a command
binding.

Both MUST be supported. Implementations SHOULD document the harness-side form as
the default.

> **Rationale.** The distinction is *protocol in the harness* versus *protocol in
> the prompt*. In the second form, protocol conformance is a property of model
> behaviour: a rank that forgets to enter a barrier does not merely produce a worse
> answer, it prevents the population from making progress. Confining the protocol
> to host-side code makes conformance a property of the runtime. The agent-side
> form is retained because it is sometimes the only option, and because the
> difference between them is measurable and worth measuring.

---

## S2. Units and the token

The unit of transfer cost is the **token**, not the byte, because the scarce
resource is the receiver's context window rather than its memory.

Implementations MUST report which token estimator they use. Two implementations
using different estimators MAY make different eager/rendezvous decisions for the
same payload; this is permitted, because the decision affects cost, not
correctness.

An implementation that enforces a volume bound MUST also expose the means to
evaluate it, and a task carrying a bounded contract SHOULD carry the concrete
invocation that performs the check.

> **Rationale.** A constraint the constrained party cannot evaluate is not a
> constraint but a guess, and parties guess conservatively. In our own experiments
> a contract bounded a rank's output at 450 tokens and the runtime checked it,
> while the rank had no way to measure a candidate; one rank submitted half the
> items it could have and said so, and another reverse-engineered the counter from
> prompt sizes the runtime had incidentally reported, then fitted a quarter more
> content. Whether the counter matches a particular provider's tokeniser matters
> far less than the producer and the checker using the same one.

---

## S3. The cost model

For an operation moving a payload of `n` tokens:

```
T = alpha + beta*n + gamma*k + lambda
C = c_tok*n + c_op*K
```

* `alpha` — per-operation control-plane latency.
* `beta` — per-token cost.
* `gamma` — the cost of **one operator application by an executor**; `k` the number
  of such applications *on the critical path*.
* `K` — the number of operator applications *in total*.
* `lambda` — the executor's own think time, heavy-tailed (A4).

Three properties distinguish this from the Hockney model.

**`alpha` is enormous relative to MPI, and `gamma` is enormous relative to
everything.** In the reference implementation `alpha` is 0.73 ms and `beta` is
0.48 µs/token, giving a half-bandwidth point of about 1500 tokens — above the size
of a typical agent artifact, so latency-optimal algorithms win over a wider range
of sizes than in MPI. `gamma`, meanwhile, is seconds to minutes, exceeding
`alpha + beta*n` by three to five orders of magnitude.

**Time and price are independent axes.** Running `p` ranks divides time by up to
`p` and divides price by nothing. A redundant operator application off the
critical path is free in wall time and fully charged in tokens. Every collective
MUST therefore report both a critical-path count and a total count.

**Fidelity is a third axis.** For a lossy operator, quality degrades with the
**depth** of the reduction rather than with the number of applications, because
depth counts how many times a contribution is re-summarised on its way to the
root. Every reduction MUST report the fold depth it induced.

> **Selection principle.** In MPI, collective algorithm selection minimises
> `alpha`-terms and `beta`-terms and treats `gamma` as free. In AgentMPI,
> selection MUST minimise `k`, MUST report `K`, and MUST bound context cost.
> Transplanting MPI's selection rules unchanged gives wrong answers (S7.7).

---

## S4. Communicators, groups, and topologies

### S4.1 Communicators

A **communicator** is a pair *(group, context)*. The group is an ordered list of
world ranks; the context partitions the message namespace. All communication is
scoped to a communicator, and traffic on one context MUST NOT be visible on
another. The context MUST NOT be a value the application can observe or forge.

`AMPI_COMM_WORLD` contains every rank of the job and is named `world`. A
communicator carries a **generation** number, incremented by shrink (S10.5).

> **Rationale.** This is MPI's central abstraction and the one worth
> transplanting unchanged. Pre-MPI libraries addressed messages by
> `(destination, tag)`, so a library's messages could be intercepted by its caller
> whenever they collided on an integer, and MPI's designers retrospectively
> identify the private context as the reason MPI could support libraries at all.
> The agent-world version of that collision is not hypothetical: a reviewer's
> critique intended for the author of module A being consumed by the author of
> module B is the same bug, and the "one shared group chat" architecture makes it
> certain rather than merely possible. A communicator also bounds cost: a broadcast
> to a four-member team costs four messages, not `p`.

### S4.2 Construction

* `AMPI_Comm_split(colour, key)` — collective over the parent. Ranks sharing a
  colour form a new communicator ordered by `key`, ties broken by parent rank.
  Membership MUST NOT be decided from a partial vote: the operation completes only
  when every **live** parent rank has registered a colour. Failed ranks are
  excluded rather than waited for.
* `AMPI_Comm_create(group, name)` — non-collective; the caller names an explicit
  member set and no participation is required from non-members.
* `AMPI_Comm_dup(name)` — same group, fresh context. The operation a library
  performs.
* `AMPI_Comm_free` — optional.

> **Rationale.** `create` exists for the reason MPI-3 added `MPI_Comm_create_group`:
> `split` requires every member of the parent to participate, which is impossible
> once some of them are dead — and that is exactly when a harness most needs a new
> group.

### S4.3 Topologies

* `AMPI_Cart_create(dims, periodic)` and `AMPI_Cart_shift(direction, disp)`,
  returning `(source, dest)`, either of which may be `AMPI_PROC_NULL` at a
  non-periodic boundary.
* `AMPI_Dist_graph_create(edges)` — an arbitrary declared adjacency: an
  organisation chart, or a review graph. An implementation MUST record both the
  graph **and its transpose**.
* `AMPI_Neighbor_allgather` — exchange with declared neighbours only.

> **Rationale.** Topologies buy two things in MPI: expressiveness, and the chance
> for the implementation to remap ranks onto hardware. The second does not
> transfer — there is no interconnect to be near. The first becomes far more
> valuable, because the communication pattern *is* the harness's design and it
> determines the cost. A full group conversation over `p` agents costs `O(p²n)`
> tokens; the same information over a review graph costs `O(pn)`. Restricting
> communication to a topology is how a harness buys scalability, and refusing to
> is why group-chat architectures do not scale.
>
> Storing the transpose is not an optimisation. A rank asked "who should I review"
> is answered by the out-edges and a rank asked "who is reviewing me" by the
> in-edges; a harness that keeps only one delivers every critique to the wrong
> author.

---

## S5. Payloads, envelopes, and handles

Every payload has an **envelope**: source, tag, communicator, token count, byte
count, content digest, an optional caller-declared schema, and a cheap
deterministic **summary**. Every payload also has a **handle**, a stable
content-addressed identifier.

Operations that deliver a payload MUST report the envelope and MAY withhold the
body.

Three consequences are normative:

1. **Broadcast is drift-free.** A tree broadcast MUST forward handles, never
   regenerated content. An implementation in which an interior rank retransmits
   text it has restated does not conform.
2. **Duplicate admission is free.** Admitting the same handle twice to a rank's
   context MUST charge the budget once.
3. **Replay is exact.** The event log plus the payload store MUST suffice to
   replay the protocol without invoking any agent.

> **Rationale.** Point 1 is what makes logarithmic-depth collectives usable at all.
> A protocol in which agents relay content by restating it degrades with depth like
> a game of telephone, which would confine such a protocol to flat, root-centred
> patterns — precisely the patterns that make the root a bottleneck. Immutability
> converts depth from a quality risk into a pure latency win. It also draws the
> line this design depends on: **broadcast can be lossless because it does not
> transform; reduction cannot be, because it must** (S8).

The envelope MUST carry a one-line **summary**. Without it, a receiver holding
only an envelope must fetch in order to decide whether to fetch. The summary MUST
be deterministic and MUST be free of model calls.

---

## S6. Flow control, views, and contracts

### S6.1 The context ledger

Every rank has a **context budget** in tokens and a **context used** counter,
which is cumulative rather than a high-water mark of live data, because that is
what an executor's window is: a transcript that only grows.

Delivering a payload body into a rank's context MUST charge the ledger. An
operation whose delivery would exceed the budget MUST NOT silently succeed. It
MUST either fail with `AMPI_ERR_CTX_EXCEEDED` or **degrade**: deliver a bounded
view instead of the body and report that it did so. Implementations SHOULD
degrade, because an agent that receives a truncated message can continue while one
that receives an error usually cannot.

### S6.2 The eager threshold

Let `E` be the implementation's **eager threshold** in tokens.

* A payload of `n ≤ E` tokens is delivered **eagerly**: the body is placed in the
  receiver's context and the ledger is charged `n`.
* A payload of `n > E` tokens is delivered by **rendezvous**: the receiver obtains
  the envelope and handle, is charged only the envelope cost, and MUST perform an
  explicit materialisation to obtain the body.

A caller MAY override the decision per operation. An implementation MAY also
choose rendezvous for a payload under the threshold when the receiver's remaining
budget is small.

> **Rationale.** This is MPI's eager limit, and it exists for the same reason.
> Below the limit, pushing bytes to the receiver unsolicited is cheaper than an
> RTS/CTS handshake; above it, the receiver's unexpected-message buffer is too
> precious to fill without permission. Here the precious resource is the
> receiver's attention. The mechanism transfers exactly; only the units change.
> This is the single adaptation that converts context exhaustion from an emergent
> failure into a flow-control problem with a tunable threshold.
>
> One thing does *not* transfer. In MPI, eager and rendezvous are an invisible
> implementation choice because both deliver the same bytes and only latency
> differs. Here they differ in *what arrives*, and therefore in whether the
> receiving agent's context is consumed. Because they are semantically distinct,
> they MUST be visible.

### S6.3 Unexpected-message credit and context-safe programs

Every rank publishes an **unexpected-message budget**: a bound, in tokens, on the
total volume of unmatched eager messages it will accept. A sender whose eager
message would exceed the destination's budget MUST block, and MUST raise
`AMPI_ERR_CTX_CREDIT` if it cannot proceed by its deadline. Both the stall and its
resolution MUST be traced.

**Definition (context-safe program).** A program is *context-safe* if it completes
for every assignment of unexpected-message budgets, however small.

**Consequence.** A program in which every rank sends before any rank receives is
context-safe if and only if its payloads travel by rendezvous.

> **Rationale.** This is a direct transplant of MPI's *safe program* discipline.
> MPI declines to guarantee that a program depending on buffering will work,
> because such programs run on one implementation and deadlock on another. The
> agent analogue is sharper: the scarce buffer is the receiving agent's context,
> and exceeding it does not merely deadlock, it silently degrades the agent's
> output. So the penalty for an unsafe program is not "it hangs on somebody else's
> machine" but "it produces slightly worse answers as you scale, for reasons that
> never appear in any log". Making the budget explicit and blocking on it converts
> an invisible quality failure into a reported, attributable stall.

> **Advice to implementors.** MPI's advice is to test a program by replacing every
> standard-mode send with a synchronous one. That test is mechanical, and an
> implementation SHOULD provide it as a tool rather than as advice. The reference
> implementation runs the harness's communication skeleton under zero-buffer
> semantics, reports the ranks that wedge and the send cycle among them, and
> re-runs with every send declared rendezvous so it can say whether the harness is
> repairable by transport choice alone.

### S6.4 Views — the derived-datatype analogue

A **view** is a declarative, deterministic projection of a payload into a bounded
number of tokens. Conforming implementations at L5 MUST provide at least: `full`,
`head:n`, `tail:n`, `headtail:n`, `lines:a-b`, `keys:k1,k2`, `shape`, `stat`,
`grep:pattern`, `chunk:i/n`, `outline`.

Views MUST be deterministic and MUST be free of model calls, so that a replay
charges identical context. Semantic (model-evaluated) compression is **not** a
view: it is an agent-evaluated operation whose cost the harness must see.

> **Rationale.** An MPI derived datatype is a *declarative description of how to
> access a buffer*: `MPI_Type_vector` says "ten blocks of four doubles, stride one
> hundred", and the library gathers exactly those elements. The point is that the
> access pattern is data the library can optimise rather than control flow it
> cannot see, and it is the mechanism by which a program communicates the boundary
> column of a matrix without packing a copy. AgentMPI's scarce resource is context
> rather than memory bandwidth, so the analogous question is not "which bytes" but
> "which tokens, and how few". A view answers it declaratively, which is why the
> runtime can cache it, cost it, and reproduce it.

Every operation that hands back a payload MUST offer a way to write the body to
storage **without** charging the ledger.

> **Rationale.** Agents that lacked it reached into the object store directly
> rather than pay to see what was already a file.

### S6.5 Contracts

A **contract** is a typed description of a payload with a structural part and a
semantic part: a name, a kind, `required` keys, `nonempty` keys, token bounds,
regular expressions that must or must not match, an optional natural-language
postcondition, and optional self-identifying fields.

**Matching.** A send's contract and a receive's contract match iff their names and
kinds agree and the receiver's `required` set is a subset of the sender's. Only
name, kind and `required` participate in matching; bounds and semantics are
checked, not matched, because a bound is a property of one endpoint's budget, not
of the wire. A mismatch MUST raise `AMPI_ERR_TYPE`.

**Self-identification.** A contract MAY require a field to equal a per-rank
expansion. When a collective delivers a slice, the contract MUST be checked
against the *kept slice*, never against a block an interior node forwards.

> **Rationale.** MPI datatypes do two jobs. The first is *matching*: a type
> signature on each side, checked at runtime, which turns a class of silent
> corruption into a loud error. The agent analogue is immediate — an executor
> routinely returns prose where an object was requested, or six sections where
> eight were asked for — and catching it at the boundary prevents a malformed
> artifact from poisoning a downstream reduction. Self-identification extends this:
> a slice that says which rank it is for turns a misrouted block into an error at
> the receiver rather than a plausible wrong answer three phases later.
>
> `nonempty` is separated from `required` because conflating them is a trap that
> costs correctness. A term sheet for a section containing no proper nouns
> legitimately has an empty term map; a contract that rejected it would make the
> rank retry, fail, and abandon peers already blocked in a collective — over a
> correct answer.

---

## S7. Point-to-point and collective communication

### S7.1 Matching

A receive matches on `(communicator, source, tag)`, with wildcards
`AMPI_ANY_SOURCE` and `AMPI_ANY_TAG` permitted.

**Delivery rule.** A message is delivered to the *first posted receive that
matches it*.

**Non-overtaking rule.** Among messages that match the same receive, they are
matched in the order they were sent. AgentMPI provides no other ordering guarantee
and no fairness guarantee.

Tags are integers in `0..AMPI_TAG_UB`; implementations MUST reserve a range above
that for their own traffic and MUST reject user use of it. Implementations SHOULD
accept **symbolic tags** and map them deterministically into the user range.

> **Rationale.** Agents use names far more reliably than integers.

### S7.2 Send modes and delivery

Two orthogonal axes. **Mode** is MPI's: `standard` completes when the message is
durably enqueued, `synchronous` when it has been matched, `ready` immediately but
errors unless a matching receive is already posted. **Delivery** is the
agent-specific axis of S6.2. Both are visible to the caller.

### S7.3 Deadlines and idempotent retry — a deliberate departure

Every blocking AgentMPI operation is **deadline-bounded**. On expiry it MUST fail
with `AMPI_ERR_TIMEOUT` and MUST leave its state such that **re-issuing the
identical operation resumes the same wait rather than starting a new one**.

Implementations MUST retry internally at least once by default.

A send MUST be idempotent under retry: re-issuing an identical send MUST NOT
enqueue a second message.

> **Rationale.** `MPI_Recv` blocks forever, which is tolerable when a peer's
> failure kills the whole job. An agent peer may be slow, wedged, or dead, and no
> timeout distinguishes them, so a bounded deadline plus resumable state is the
> only workable primitive.
>
> Internal retry is not a convenience. In a pilot run, an executor instructed to
> retry a timed-out call up to twenty times gave up after two and stalled its
> entire reduction tree. **A protocol that depends on an executor's persistence is
> not a protocol.** Making the default behaviour "keep waiting" means abandoning a
> collective requires the executor to do something rather than nothing.
>
> Send idempotence matters for the same reason: agents retry commands, and a
> duplicated contribution silently doubles a reduction's input.

### S7.4 Nonblocking operations and durable receives

`AMPI_Isend`, `AMPI_Irecv`, `AMPI_Wait`, `AMPI_Test`, `AMPI_Cancel`.

Posted receives MUST be **durable**: an executor may post receives, be replaced,
and have its successor complete them.

### S7.5 Probing

`AMPI_Probe`/`AMPI_Iprobe` inspect the next matching message's envelope without
receiving it. `AMPI_Inbox` enumerates everything pending for the caller with its
token cost.

> **Rationale.** In MPI, probing mainly sizes a buffer. Here it lets an executor
> see *what is waiting and what it would cost* before committing context, which is
> the basis of every context-aware scheduling decision a harness can make.

### S7.6 Collectives

`AMPI_Barrier`, `AMPI_Bcast`, `AMPI_Scatter`, `AMPI_Gather`, `AMPI_Allgather`,
`AMPI_Reduce`, `AMPI_Allreduce`, `AMPI_Reduce_scatter`, `AMPI_Scan`,
`AMPI_Exscan`, `AMPI_Alltoall`, `AMPI_Neighbor_allgather`.

**Collective identity is explicit.** MPI identifies the k-th collective on a
communicator by program order. AgentMPI MUST support an explicit **label**, and
harnesses SHOULD always supply one. A retried call MUST rejoin the caller's
still-open collective rather than start a new one. Calling one label as two
different kinds MUST raise `AMPI_ERR_COLL_MISMATCH`.

> **Rationale.** An executor's program order is not reliable: it may retry a
> command, skip a step, or reorder two independent calls, and relying on order
> silently mismatches ranks — pairing one rank's second call with everyone else's
> first and returning a confidently wrong result. Labels cannot make a reordered
> schedule complete; nothing can, since each collective still needs all its
> members. What they buy is that the mismatch is *named*. Named collectives were
> the single largest robustness improvement in the whole interface.

**Gather MUST NOT default to concatenating bodies.** It MUST return a *manifest*:
one entry per contributor with rank, handle, token count and summary. The caller
then materialises what it needs, or supplies a per-contribution view budget.

> **Rationale.** This is where naive harnesses die. At `p=128` with 4000-token
> contributions, an inlining allgather charges one rank **508,000 tokens** and
> moves 65 million in total, where a handle-based one charges 5,080 and moves
> 650,240. Concatenation is not a reasonable default at any interesting `p`.

**Barrier policies.** A barrier MUST accept a policy declaring what a missing
participant *means*: `wait`, `raise`, `proceed` (continue with those who arrived,
reporting absentees), `shrink`, or `revoke`.

> **Rationale.** An unconditional barrier is a liveness bug waiting to happen: the
> probability that all `p` agent ranks arrive within a fixed window falls off with
> `p`, and "the agents are waiting for each other" is the most frequently reported
> pathology in multi-agent postmortems. A missing chapter degrades a translation,
> whereas a missing module kills a build; only the harness author knows which.

### S7.7 Algorithm selection

An implementation MUST document its algorithm catalogue, MUST let the caller
override the selection, and MUST be able to explain a choice — including why each
alternative was rejected.

Two rules differ from MPI, both derived from S3.

**(a) Runtime operators want no tree.** When the operator can be applied by the
implementation, a shared control plane can fold every contribution in place: one
round, zero messages. A tree adds rounds and buys nothing. This is the
in-network-aggregation regime, and it is the common case for the operators
harnesses should be using.

**(b) Agent operators want MPI's tree, and reject MPI's *best* tree.** When an
executor applies the operator, `k` is the entire cost. A binomial tree puts
`⌈log₂p⌉` applications on the critical path against a chain's `p-1`. But
recursive-doubling allreduce — MPI's standard choice for short messages, because
redundant arithmetic is free — performs `p·⌈log₂p⌉` applications *in total*
against reduce-then-broadcast's `p-1`: 384 against 63 at `p=64`, for the same
critical path. The algorithm that wins for bytes loses badly for agents.

**Admissibility precedes optimisation.** An algorithm whose peak per-rank
residency exceeds a rank's context budget is **infeasible**, not merely slow, and
MUST be rejected with a reason rather than selected.

> **Advice to harness authors.** For a semantic reduction, algorithm selection is
> a *quality* decision, not merely a cost decision, and therefore must be exposed
> rather than hidden behind a tuning table — which is exactly what MPI does,
> correctly, for floating-point addition.

### S7.8 Quorum collectives

A collective MAY carry a **quorum** `q` in `(0,1]` and a deadline. It completes
when `⌈q·live⌉` ranks have contributed.

Reaching quorum **releases** a barrier but MUST NOT **close** it: a straggler
arriving afterwards MUST still pass through. A rank arriving after a data-bearing
collective has closed MUST receive the published result with a `late` indication,
not an error.

> **Rationale.** Executor completion times are heavy-tailed (A4), so a strict
> barrier over `p` executors waits for the maximum of `p` heavy-tailed samples. The
> quorum knob lets the harness choose between bulk-synchronous determinism and
> bounded staleness — the same trade stale-synchronous parameter servers make,
> expressed as a collective rather than as a training-loop hack. Closing on quorum
> would guarantee that precisely the slowest ranks fail, which is the opposite of
> what the knob is for.

---

## S8. Reduction operators, and the locality of merge

### S8.1 Two families

* **Runtime operators** are applied by the implementation: exact, deterministic,
  free. Conforming implementations MUST provide `concat`, `union`, `jsonmerge`,
  `sum`, `max`, `min`, `count`, `and`, `or`, and SHOULD provide `vote`, `maxby`,
  `first`, `last`, `bag`, `topk`.
* **Agent operators**, written `agent:<label>`, are applied by an *executor*. This
  is `MPI_Op_create` with a language model as the callback.

**Prefer exact operators.** `union` — key-wise union with deterministic conflict
handling — is exact, associative, commutative and idempotent. A glossary merged
with `union` is identical at every rank regardless of tree shape; a glossary
merged by an agent is not. Harnesses SHOULD use exact operators wherever the
combination is genuinely set-like, exactly as an MPI programmer prefers `MPI_SUM`
to a user-defined operator.

> **Advice to harness authors.** An exact merge over a keyed map presupposes that
> each key has one correct value, and the resulting agreement is *binding on every
> rank*. That presupposition is stronger than it looks. In our translation
> experiment a glossary bound the English term *pounds* to the rendering for pounds
> sterling, correct nearly everywhere; one section used the word for body weight,
> and the rank — correctly obeying a glossary the harness had declared binding —
> produced a wrong sentence. Conflict handling copes with two ranks *disagreeing*
> about a key; it cannot represent one key having two correct senses. Choose the
> key so that a globally correct answer exists — here, `(term, sense)` rather than
> `term` — and be aware that a consistency metric scores a uniformly wrong decision
> as a perfect one. **Agreement is not correctness, and a protocol that makes
> agreement cheap makes it cheap to agree on something false.**

### S8.2 The operator algebra

An operator declares: `associativity ∈ {EXACT, APPROX, NONE}`, `commutative`
(default **false**), `idempotent`, an `identity`, whether it is `deterministic`,
and its `conflict_policy` (S8.5).

**Normative constraints.**

* An operator declared `NONE` MUST be evaluated only by the serial chain;
  requesting a tree MUST raise `AMPI_ERR_OP_UNSOUND`.
* A non-commutative operator MUST be evaluated in rank order. A binomial tree
  satisfies this only when the root is rank 0; for any other root an
  implementation MUST refuse the tree rather than silently reorder.
* A non-deterministic operator MUST NOT be evaluated by an algorithm in which
  ranks fold independently (S8.3).
* The serial left fold is the **reference semantics**. For an `EXACT` operator,
  every algorithm MUST produce the fold's result.
* Every reduction MUST report the fold depth, the total applications, and the
  critical-path applications it induced.

> **Rationale.** MPI requires a user operator to be associative and then reorders
> freely. The guarantee is deliberately weak — an implementation may pick different
> trees for different process counts, which is why floating-point `MPI_SUM` is not
> bitwise reproducible — and practitioners accept it because the discrepancy is
> bounded by rounding error. An operator implemented by a language model is not
> associative, is lossy, is expensive, and is non-deterministic, and the
> discrepancy is bounded by nothing. Making non-commutativity the *default* is the
> conservative choice.

### S8.3 The divergence hazard

`reduce_bcast` computes one result and broadcasts it, so every rank holds a
byte-identical value. `recursive_doubling` has each rank compute the result
itself. For an `EXACT` operator these are equivalent. For a lossy operator they
are not: each rank performs its own fold sequence, so the `p` results differ and
**the population silently disagrees about the value it just agreed on**.

An implementation MUST refuse this combination, or at minimum record a
divergence-risk flag. The reference implementation refuses it.

### S8.4 Reproducibility is not consistency

When `commutative` is false, an implementation MUST use a canonical, rank-ordered
reduction tree, so that the same inputs on the same rank count produce the same
tree shape.

A canonical tree shape makes a reduction **reproducible**. It does not make it
**consistent**, and conflating the two is a mistake this section exists to
prevent.

> **The observation.** In a real eight-rank reduction over interface proposals,
> two branches of the tree independently encountered the *same* conflict — which
> module defines the shared exception type — and resolved it in *opposite*
> directions. Both resolutions were recorded, so the merged result contained two
> contradictory rulings under one identifier, and the tree had no way to notice:
> each merge saw a locally consistent pair of operands. Two ranks then acted on
> different halves of the result. Separately, the same reduction dropped four of
> eight modules from one section of its output despite an explicit instruction
> never to drop a module — because "never drop" is a global invariant and every
> merge is local.

**The locality of merge.** Let a reduction be a fold of a binary operator over a
tree `T` whose leaves are the ranks' contributions, and call a predicate `φ` a
*global invariant* if its truth depends on the whole leaf multiset. An internal
node of `T` sees exactly two operands. If two nodes `u` and `v`, neither an
ancestor of the other, both encounter evidence bearing on `φ`, then no node of `T`
is in a position to reconcile them: their lowest common ancestor receives `u`'s
and `v`'s *outputs*, and the information that would reveal the inconsistency — what
each decided, and why — is not in the operator's codomain. Enlarging the tree
cannot help. Only enlarging the codomain can.

The specification therefore provides two mechanisms, because the observation
produced two distinct failures and they need different fixes.

### S8.5 Conflict lifting

An operator MAY declare `conflict_policy = LIFT`. Under lifting, the operator's
codomain is `V × C`, where `C` is a set of undecided **conflicts**. An operator
that cannot decide a contested item from its two operands alone MUST lift it into
`C` rather than decide it. `C` merges by set union.

**Property (shape independence).** Because the conflict component is a semilattice
— its join is associative, commutative and idempotent — the set of conflicts
arriving at the root is identical for every tree shape. The value component never
decides a contested item, so it cannot decide one twice. Arbitration at the root
therefore decides each conflict exactly once, and the outcome does not depend on
the schedule.

Implementations MUST provide `AMPI_Op_arbitrate`, which resolves every lifted
conflict at the root and MUST refuse to leave any undecided.

> **Advice to implementors.** Implement the lifted state as a map from key to the
> *set* of distinct values seen, and derive the value/conflict presentation from
> it. The obvious implementation — lift a key on first disagreement and remove it
> from the value — is **not** idempotent: a third contributor finds the key absent
> from the accumulator and reinstates it, so the conflict set depends on fold order
> after all. We shipped that version and its own shape-independence test caught it.

> **Cost.** Lifting buys the "one ruling per key" invariant for one extra operator
> application and `O(|C|)` tokens, against the `p-1` critical-path applications a
> serial chain would cost. At `p=64` that is a factor of ten in latency.

### S8.6 Invariant verification

An operator MAY declare a **global invariant** over `(leaves, result)`. After a
reduction closes, the implementation MUST evaluate it and MUST report violations
(`AMPI_ERR_INVARIANT`), naming the items involved.

> **Rationale.** "Never drop a module" is not a conflict any pair of operands
> exhibits — each local merge is individually faithful — so lifting is blind to it.
> It is a property of the leaf multiset and the result, so it is checked once,
> after the fact. Neither mechanism makes an agent operator correct. They make two
> specific, observed, expensive failure modes into loud errors, which is the most a
> protocol can honestly claim.

### S8.7 The continuation protocol for agent operators

An agent operator cannot complete inside one call, because the operator *is* the
caller. A reduction with an agent operator MUST therefore:

1. return a **merge directive** naming two operand locations and an output
   location, and charge the caller's ledger for the operand tokens it will read;
2. accept `AMPI_Op_commit(label, step, result)`, which records the merged value
   and resumes the schedule;
3. checkpoint the accumulator and schedule position durably, so that a timeout,
   crash, or replacement resumes at the same step.

Operand delivery MAY respect an optional operand budget for **unstructured**
payloads, but MUST NOT clip a structured payload, and MUST report the full
payload's handle in every case.

> **Rationale.** An operand is the *input to the operator*, so removing part of it
> does not degrade the result, it corrupts it. For a structured payload it is worse
> than that: clipping JSON mid-string yields a document the operator cannot parse
> at all. We shipped the naive behaviour and it bit immediately — agents serving as
> internal nodes received operands cut mid-string with an elision marker spliced
> in, and independently invented recovery hacks, one prefix-matching the
> content-addressed store by hand to recover the bytes, another reordering its
> output so that the decisions it could not afford to lose came before the part
> that clipping would take. The asymmetry to respect is that a *result* may be
> summarised, because its consumer is a reader; an *operand* may not, because its
> consumer is a function.
>
> The alternative design — having the runtime call a model itself — would make
> AgentMPI a framework with opinions about models, credentials and prompting. The
> continuation structure keeps the library owning the schedule and the user owning
> the operator, which is exactly MPI's division.

### S8.8 Fault-tolerant reduction

If a contributor is declared failed, its subtree's contributions MUST be dropped,
the omission MUST be recorded, and the collective MUST complete with the
survivors'. `recursive_doubling` is exempt: no variant preserves an identical
result on all ranks under failure, so it MUST fail loudly rather than return
different answers to different ranks.

---

## S9. Windows: shared state

### S9.1 Windows

A **window** is a named, versioned key space exposed to a communicator, divided
into cells. There is no ambient global memory: a rank may only touch state a
window exposes. Windows on different communicators MUST NOT alias.

> **Rationale.** The most-reported failure of real multi-agent systems is that
> executors cannot share what they learn. Pure message passing forces every fact
> discovered by one agent to be routed to every agent that will later need it, by a
> harness author who cannot know in advance who that is. A window inverts it.
> Keeping windows named and explicit is what keeps the shared state auditable — the
> difference between a blackboard and a mess.

### S9.2 Operations

| Operation | Semantics |
|---|---|
| `AMPI_Put(win, key, value, expect_version?)` | Write; with `expect_version`, a conditional write |
| `AMPI_Get(win, key, view?, version?)` | Read, charging the ledger; may read a historical version |
| `AMPI_Accumulate(win, key, value, op)` | Atomically apply a **runtime** operator to the cell |
| `AMPI_Compare_and_swap(win, key, expect, value)` | Atomic conditional write on content |
| `AMPI_Fetch_and_op(win, key, op, value)` | Atomic read-then-modify |
| `AMPI_Win_list(win, prefix?)` | Enumerate keys with sizes and provenance, **without** bodies |
| `AMPI_Win_history(win, key)` | Version history with writer and epoch attribution |

`Accumulate` and `Compare_and_swap` are the load-bearing ones. Accumulate replaces
read-modify-write — three round trips and a race — with one atomic operation, so
"union this finding into the shared findings" needs no lock. Compare-and-swap is
how work is claimed: a task cell holds `unclaimed` and whichever executor swaps it
wins. Unlike a lock, it cannot be held by a dead executor.

`Accumulate` MUST reject a lossy operator.

> **Rationale.** The read-modify-write happens inside an atomic section, and an
> implementation cannot hold one across a model call. A harness needing judgement
> in the combine must lock, get, reason, put with an expected version, and unlock —
> and accept the serialisation that implies. Making that trade-off explicit is the
> point: a harness that puts a semantic critical section on its critical path has
> built a sequential program, and the trace will show it as lock wait time.

`Win_list` is what makes a window usable by an executor with a bounded context: it
can see *what exists* for a few tokens per key and then spend its budget
deliberately.

Every cell MUST record its writer's rank and epoch, so any claim in shared state
is attributable.

### S9.3 Synchronisation

* **Active target.** `AMPI_Win_fence(win, label)` closes an epoch: a barrier plus
  the guarantee that every participant's writes for the phase are in. This turns a
  blackboard into a sequence of bulk-synchronous supersteps — Valiant's BSP
  boundary, and the cheapest way to make shared agent state explicable.
* **Passive target.** `AMPI_Win_lock(win, key, mode, lease)` / `AMPI_Win_unlock`.
  Locks MUST be **leased** and MUST carry a monotone **fencing token**. An expired
  lease MUST be reclaimable, and a write bearing a stale token MUST be rejected.

> **Rationale.** An MPI process holding a window lock cannot wander off; an
> executor can. The lease prevents a dead holder wedging the job; the token
> prevents a revived holder corrupting state after its lease expired. Without both,
> the standard lease-expiry race is merely made unlikely rather than closed.

### S9.4 Consistency

Within one window, operations are **linearizable**. Across windows, no ordering is
guaranteed. Versions are per-cell and monotone.

The memory model is `SEPARATE`: a rank's private copy may be stale and is
reconciled only at synchronisation points. An implementation MUST record a
**staleness violation** when a rank overwrites a cell whose current version it did
not observe.

> **Rationale.** `SEPARATE` is correct because for an agent the private copy is not
> a hardware artefact: it is the copy of the document sitting in the agent's
> context from ten minutes ago, and it *is* stale, because peers have edited it
> since. Recording staleness rather than preventing it is deliberate — a harness
> that legitimately overwrites must still be able to — and it turns the most common
> multi-agent data race into a counted, attributable event.

---

## S10. Fault tolerance

### S10.1 The failure model

| Class | Description | Detector | Response |
|---|---|---|---|
| `crash` | the launcher observed an exit | launcher | respawn |
| `lease_expired` | the lease deadline passed | lease | respawn or shrink |
| `no_show` | the join deadline passed and the rank never initialised | join deadline | start it, or shrink |
| `abort` | the executor aborted | explicit | shrink; do not reassign |
| `ctx_exhausted` | the ledger reached its budget without completion | ledger | respawn with a **smaller** assignment |
| `budget_exhausted` | a cost limit was reached | accounting | shrink |
| `protocol_violation` | output failed its declared contract repeatedly | contract | respawn with a corrected prompt |
| `wrong_answer` | a verifier rejected the result | **independent verification only** | redundancy or a verifier rank |
| `killed` | administrative, or injected | explicit | as configured |
| `zombie` | an operation arrived at a stale epoch | epoch | fence (terminal) |

> **Rationale.** MPI's fault-tolerance work assumes fail-stop, and importing that
> assumption is why so much agent-reliability engineering consists of retry loops
> that do not help. The load-bearing row is `wrong_answer`. A rank returning a
> confident wrong answer is undetectable by any amount of timeout tuning, retry
> logic or schema validation, because none of those examine whether the answer is
> *right*. It is the exact analogue of silent data corruption, and HPC's answer to
> silent data corruption is not checkpoint/restart — which faithfully preserves the
> corruption — but algorithm-based fault tolerance: carry redundant information
> that lets the computation check itself. The quantity to optimise is therefore the
> *verification budget*, not the checkpoint interval.
>
> One caution about redundancy: replicating a language model does not give
> independent failures, because correlated errors are the norm. Agreement is
> evidence, not proof, which is why `vote` reports a consensus *fraction* rather
> than a bare winner.

### S10.2 Detection: lazy, local, two-phase

Failure detection MUST be lease-based and MAY be evaluated lazily — only when some
rank blocks on an operation a failure would prevent. There MUST NOT be a
requirement for a monitoring daemon. It implements an *eventually perfect* (`◇P`)
detector under partial synchrony.

Detection MUST be **two-phase**: a silent rank is first *suspected*, and only
convicted after a further confirmation interval. Suspicion MUST be retractable by
a heartbeat. An administrative kill MUST NOT be retractable by its victim.

Detection is **local**: two ranks MAY hold different views of who has failed, and
no operation may assume a globally consistent view. Where agreement is required,
it MUST be obtained explicitly through `AMPI_Comm_agree`.

Straggler thresholds SHOULD be derived from the run's own latency distribution,
not fixed in advance.

> **Rationale.** A lease-based detector cannot distinguish a thinking executor
> from a dead one, and a single-phase detector convicted 1091 times in twenty
> minutes on a real agent host because executor turn latency is heavy-tailed. The
> kill must not be retractable, however: if the victim's next heartbeat could undo
> it, fault injection would be unobservable and an experiment measuring recovery
> would be measuring nothing.

### S10.3 Leases, heartbeats, and the join deadline

`AMPI_Heartbeat(extend)` renews the caller's lease and, given `extend`, guarantees
it survives for at least that long without another call.

Every rank has a lease deadline set when the rank is **requested**, not when its
executor first calls in.

> **Rationale.** A lease-based detector cannot tell thinking from dead, and making
> the lease longer than the longest legitimate pause is the wrong fix, because the
> lease also bounds how long a blocked peer waits. `Heartbeat(extend)` lets the
> executor supply the information a timeout cannot infer: "I am about to spend ten
> minutes on one step."
>
> A rank whose executor never starts at all has no lease to expire, so without a
> join deadline it is neither alive nor failed and every peer waits for it forever.
> We encountered exactly this: a launcher that could start only 6 of 22 requested
> ranks produced a job in which 16 no-shows were permanently pending, and no
> operation could detect it.

### S10.4 Progress obligations while blocked

A blocked rank MUST, while waiting: renew its own lease, run the failure detector,
and observe revocation.

> **Rationale.** Omitting the first is catastrophic and not obvious. In an early
> version, a rank waiting inside a barrier made no runtime calls, so the detector
> declared it dead for the crime of waiting; every rank that arrived first was
> declared failed and the job cascaded. **Blocking is not evidence of death.**

### S10.5 Revoke, shrink, agree

`AMPI_Comm_revoke` makes a communicator permanently unusable for **every** member.
Operations on it, *including operations already blocked inside a collective*, MUST
fail with `AMPI_ERR_REVOKED`. Revocation is irreversible.

> **Rationale.** ULFM's least obvious and most necessary primitive. When a rank
> fails, the *survivors* are the problem: they are blocked inside collectives that
> can never complete, and each discovers the failure only if it happens to be
> waiting on the dead rank directly. Revocation makes every survivor fail fast,
> everywhere, which is what lets them all reach the recovery path together.
> Irreversibility matters: a communicator that could be un-revoked would let two
> ranks disagree about whether it is usable.

`AMPI_Comm_shrink` derives a new communicator over an **agreed** set of survivors,
densely renumbered preserving relative order, with an incremented generation. The
survivor set MUST be agreed, not locally computed, and the derived communicator's
identity MUST be a function of that set, so that concurrent shrinks converge on
one communicator rather than fragmenting.

An implementation SHOULD also offer **in-place** shrink, which marks absentees
inactive and keeps the numbering.

> **Rationale.** This is FT-MPI's `BLANK` mode rather than its `SHRINK` mode.
> Renumbering makes subsequent collectives cheap again but invalidates the
> harness's rank-to-work mapping — and for agents that mapping is expensive to
> recompute, because "rank 7 owns the parser" is baked into prompts and artifacts.

`AMPI_Comm_agree` is fault-tolerant agreement over a value. It MUST work on a
**revoked** communicator, since that is how survivors coordinate recovery, and it
MAY accept a quorum.

Harnesses MUST treat rank identity as communicator-relative and MUST NOT cache it
across a shrink.

### S10.6 Failure acknowledgement

Acknowledging the currently known failures MUST re-enable wildcard receives, which
would otherwise keep returning `AMPI_ERR_PROC_FAILED_PENDING`.

> **Rationale.** Without an acknowledgement step the error is permanent, because
> there is always some failure the caller has not been told about, and the receive
> can never return a timeout.

### S10.7 Replacement and the recovery briefing

`AMPI_Respawn(rank)` allocates a new epoch, breaks the predecessor's locks, leaves
its posted receives for the successor to inherit, and marks it absent in any open
collective so that the collective can still close. The predecessor's messages are
**not** deleted: a survivor may still need what it sent.

`AMPI_Recover(rank)` returns a **recovery briefing**: the replacement's answers to
five questions it must know and cannot guess.

1. What was I assigned?
2. What did I already publish?
3. What did I already receive?
4. What did I promise that is still outstanding — unmatched sends, posted
   receives, open collectives, held locks, pending merge steps?
5. What did I record for myself?

The briefing MUST include concrete advice, naming the open collectives the
replacement must re-enter, because peers are blocked inside them.

> **Rationale.** There is no memory image to restore, so process checkpointing has
> no analogue. What *does* have an analogue is durable execution: replay the record
> of externally visible commitments rather than a memory snapshot. Question five is
> why harnesses MUST instruct executors to record progress after each phase; one
> cheap call per phase is the difference between a recoverable job and a lost one.

### S10.8 Supervision

A launcher SHOULD act as a supervisor with a bounded restart policy: at most `N`
replacements per rank, then give up on that rank.

> **Rationale.** OTP's max restart intensity. An executor that fails because its
> assignment is impossible will fail again, and an unbounded supervisor turns that
> into an expensive infinite loop.

### S10.9 The harness author's obligation

**A local failure MUST NOT remove a rank from a collective.** A rank that cannot
compute its contribution MUST still enter the collective, contributing a degraded
value or an identity element, and record the degradation.

> **Rationale.** The rule most easily violated and most expensive to violate. If a
> local exception propagates out of a rank's main function, that rank never reaches
> the collective its peers are already blocked inside, and one recoverable local
> failure becomes a whole-population hang. MPI programs are written the other way
> round on purpose. Escalation must be a deliberate decision made by a barrier
> policy or a supervisor, never an accident of exception propagation.

---

## S11. Errors

Error **classes** are stable identifiers a harness may branch on. The default
error behaviour MUST be *return*, not *abort*.

Every error MUST carry a class, a human-readable message, and a **hint** stating
the concrete next action, and MUST mark whether re-issuing is correct.

> **Rationale.** MPI defaults to `MPI_ERRORS_ARE_FATAL` and declares its state
> undefined after an error, which is defensible when errors are rare bugs. Here
> they are routine events with well-defined meanings, so the default is inverted.
> Errors here are read by language models: one that says what to *do* is acted on,
> one that merely says what went wrong often is not. The retryable flag exists for
> the same reason — an executor deciding whether to re-issue should not have to
> infer the answer from prose.

The class list is in Appendix C.

---

## S12. Interface declaration, discovery, and verification

An implementation at L5 MUST provide:

* `AMPI_Iface_publish(name, declaration, version)` — publish a typed interface
  into a communicator-scoped space, keyed by **provider**, so that two ranks
  claiming one name is a visible fact rather than a last-writer-wins race.
* `AMPI_Iface_list(prefix?)` — enumerate declarations with provider, version, size
  and verification status, **without** delivering any declaration.
* `AMPI_Iface_get(provider, name, view?)` — materialise one declaration.
* `AMPI_Iface_wait(name, providers, deadline)` — block until enough providers have
  published.
* `AMPI_Iface_verify(provider, name, holds, evidence)` — record that a consumer
  checked a declaration against actual behaviour.
* `AMPI_Iface_report()` — a whole-job view: declarations nobody verified,
  declarations a consumer refuted, and names more than one rank claims.

AgentMPI does not interpret a declaration. It is a payload with a provider, a
name, and a version.

> **Rationale.** This chapter exists because of a controlled comparison. Eight
> agents were given messages and windows but *no* prescribed coordination phase, and
> asked to build a language interpreter together. Beginning about thirteen minutes
> in, and within roughly ninety seconds of one another, seven of the eight
> independently sent an identical interface-declaration message to every peer — a
> hand-rolled allgather. The integrator then published an agreed interface into a
> shared cell and broadcast integration status twice. Every consumer additionally
> built *runtime discovery*: probing a producer's exported handlers with synthetic
> inputs and settling the calling convention by majority vote, resolving class
> names by searching several plausible spellings across two peers' modules, and
> validating a candidate environment frame by binding a probe into it. The shipped
> source carried roughly five times as many probe- and detection-related
> identifiers as the arm that had a negotiated contract, and about twice the total
> lines for the same externally graded behaviour. Two of the eight introduced
> defects *inside* their own detection logic.
>
> **A protocol whose users independently reimplement a mechanism, at double the
> cost and with defects in the reimplementation, is a protocol missing that
> mechanism.**
>
> The two arms also showed that declaration and verification are complementary
> rather than alternatives. Declaration alone reproduces the failure of S8.4: a
> single agreed artefact can be internally inconsistent, because the agreement was
> reached by local merges. Verification alone cannot be inconsistent but pushes the
> whole cost into every consumer, which is what the ablated arm paid. Publishing
> the *result* of a verification is what makes the pair cheaper than either — the
> second consumer reads an answer instead of running a probe.

---

## S13. Tracing

An implementation MUST record a durable event trace: enter/exit intervals per
rank, send/receive pairs with matched endpoints, collective intervals with
participant sets and per-participant join times, window accesses with keys and
writers, failures and recoveries, context charges and degradations, and error
events with their class.

Tracing is unconditional and is part of the protocol, not an add-on.

> **Rationale.** HPC learned that a parallel program's behaviour is invisible from
> any single process's output, which is why PMPI, SLOG-2, OTF2 and their viewers
> exist. MPI's tooling interfaces are opt-in and out-of-band, which is reasonable
> when a run can be repeated cheaply. An agent run cannot: it is expensive and it
> is not reproducible, so a bug that was not traced is a bug that cannot be
> investigated. Error events in particular are what make retry behaviour
> measurable: the interesting number is not how many calls succeeded but how many
> times a rank had to reissue a deadline-bounded call.

An implementation SHOULD provide a diagnostic that reads only the trace and names
the rank responsible for a stall.

**Executor spans.** An executor that is invoked in-process --- a model API call
made by the rank's own process rather than claimed by an external worker --- MUST
record the interval it occupies as a pair of events (``task.start``,
``task.done``, with ``task.fail`` for an invocation abandoned after its repair
budget), carrying the executor's identity, the model, the exact prompt and
completion token counts the provider reported, the number of calls and tool
calls, and the cost.  An analysis MUST read these as it reads a broker's
claim/submit pair: the two record the same quantity, the time a rank spent inside
its executor, and a run staffed by processes and a run staffed by agent sessions
MUST be measurable with one ruler.

> **Rationale.** The prompt of a raw API call *is* the executor's entire context,
> and the provider reports its size exactly.  That is the first place in this
> protocol where the context ledger of S6.1 can be checked against a measurement
> rather than an estimate, and it is why the token counts are mandatory fields
> rather than optional ones.

**Replayed collectives.** A rank that re-enters a collective it already joined ---
a restarted executor replaying its program, or a retried command --- MUST have
its completion event marked ``replayed``, with the wait measured from the
re-entry rather than from the original join.  An analysis MUST NOT count a
replayed completion as a second invocation or as blocked time.

> **Rationale.** Without the mark, a rank restarted an hour into a run and
> re-entering a barrier that closed fifty-nine minutes earlier records
> fifty-nine minutes of waiting it never did, and the run's coordination share
> exceeds one.  We measured exactly that before the mark existed.

> **Rationale.** MPI's answer to a mismatched collective is undefined behaviour,
> which in practice is a hang with no output. That is survivable when you can
> attach a debugger to every process. It is not survivable when the population is a
> dozen agents on someone else's infrastructure, several of them blocked, all
> billing by the token, and the only artefact is a pile of unordered transcripts.

---

## S14. Bindings and conformance

### S14.1 The command binding

For LLM executors the binding is normally a **command-line tool**, because that is
the only interface an agent reliably has to a stateful library: it cannot hold a
handle across turns, cannot link a shared object, and its "function calls" are
invocations whose output lands in its context window.

A conforming binding for LLM executors SHOULD:

* take identity from the environment, offer an assertion against it (S1.2), and
  echo the acting identity on every operation;
* emit terse structured output with sizes in tokens, and an explicit next-action
  line;
* mark retryable errors as such, in the output;
* be idempotent by default: labelled collectives, idempotency on sends, resumable
  blocking calls;
* provide the protocol manual as a command, so an executor can re-read it;
* **print only commands that exist**, and accept the command a caller will
  actually write. Identity flags SHOULD be accepted both before and after a
  subcommand.
* offer a way to move a payload to disk *without* charging context, on every
  operation that hands back a payload.

> **Rationale for "print only commands that exist".** An early version's reduction
> directive told the agent to run a subcommand spelled with a space where the real
> one is hyphenated. Roughly ten agents reported it, several while peers were
> blocked behind them. A binding that prints a command an agent cannot copy-paste
> is worse than one that prints nothing. An implementation SHOULD test this
> mechanically by walking every emitted command string through its own parser.
>
> The rule has two corollaries we learned the second time round, at greater cost.
> First, *the bootstrap prompt is a command the binding prints*: ours placed the
> identity flags before the subcommand, where a reader expects a global option and
> where our parser did not accept them, and roughly thirty executors each lost
> their first call to `invalid choice`. The mechanical check must cover the prompt,
> not only the runtime's own output. Second, a binding whose flags are
> positional-by-subparser will be got wrong by everyone; accepting both orders
> costs four lines.
>
> A third, sharper one. **Any guard the binding leaves out of a printed command is
> a guard that is only present when the agent thinks of it.** Our emitted `submit`
> command omitted `--expect-rank`; the executors who noticed added it by hand and
> the ones who did not, did not have it. Handing over the exact command is
> supposed to require recognition rather than recall, and a command missing its
> own safety check does not.

### S14.2 The conformance suite

A conforming implementation MUST pass a conformance suite for its level, and the
suite MUST be written against the public interface only.

An implementation that provides more than one transport SHOULD run the same suite
against each.

> **Rationale.** An interface with one implementation is a library. What makes a
> standard is that a program written against the document runs on more than one
> implementation, and that there is a way to tell whether a new one got it right.
> Running one suite against several transports is also how a semantics claimed to
> be transport-independent is checked: the reference implementation's suite found
> that one of its three devices returned integers as strings for four indexed
> fields, so a wildcard receive posted as `-1` read back as `"-1"` and silently
> stopped matching — a divergence that existed on one device only and would
> otherwise have surfaced as an inexplicable protocol bug.

## S15. Process management, and a job across machines

### S15.1 The process manager is not the protocol

As in MPI, how ranks are started is outside the specification.  An implementation
SHOULD nonetheless ship a launcher (``ampirun``) that: creates the job before any
rank starts, so the set of ranks *requested* is recorded independently of the set
that answers; starts one operating-system process per rank with the rank's
identity in its environment (S1.2); distributes ranks over machines by contiguous
blocks, so a harness that partitions contiguous work by rank keeps neighbours on
one machine; and supervises its processes, restarting one that exits before
finalising through the runtime's ``respawn`` (S10.7) rather than by starting a
second process under the same identity.

> **Rationale for restart through the runtime.** A launcher that restarts a dead
> rank's process without telling the runtime hands the successor the
> predecessor's epoch, and the successor then either repeats the predecessor's
> injected failure or is fenced as a zombie.  Going through ``respawn`` gives the
> successor a new epoch, breaks the predecessor's locks and marks it absent in
> its open collectives; the harness then re-enters its program and finds its
> closed collectives returning stored results (S7.6) and its stored task results
> in the window it checkpointed them to.  That is checkpoint and restart, and it
> costs the harness author nothing beyond the discipline of keeping a rank's
> state in the device.

### S15.2 Devices for machines that share nothing

A device MAY be implemented over a shared git remote, with a ref as the
compare-and-swap cell (fetch, apply, commit, push, retry on rejection).  Its
mutations MUST be pure functions of the device state, so a rejected push can
re-apply them against fresher state.

An implementation of such a device SHOULD place a daemon on each machine that
owns the working tree and serves the machine's ranks over a local socket, and
that daemon SHOULD group-commit every mutation it receives within a window into
one push.  The guarantee each rank observes is unchanged --- a mutation returns
only once it is on the remote --- while the number of pushes on the wire is
bounded by the machine's rate of bursts, not its ranks' rate of operations.  A
device MAY additionally offer a *pipelined* mode in which a caller that does not
read a mutation's result sends several without waiting; the runtime SHOULD use
it when requesting the ranks of a job.

> **Rationale.** This is intra-node aggregation, which every production MPI does
> before it touches the interconnect.  Measured before it existed: thirty-two
> ranks on thirty-two machines made 1131 pushes and suffered 13415 rejections in
> forty-two minutes for four collectives.  Measured with it: sixteen ranks on two
> daemons made 470 operations in 98 pushes with no rank aware of the difference.

A daemon's readers MUST NOT wait on the lock its writer holds for the duration
of a push contest.  A reader that has a copy fetched within the device's read
interval SHOULD be served from that copy without taking the lock; only a reader
whose copy is stale takes it, to fetch.  The daemon SHOULD widen its batch
window while its pushes are being rejected and relax it when they land.

> **Rationale.** Measured at 128 ranks over four machines before this rule
> existed: readers and the batching writer shared one lock, the writer
> reacquired it the moment it released it, and with thirty polling readers an
> individual reader could wait longer than its lease.  Fifty-seven live ranks,
> all blocked in the same broadcast, were convicted for silence within twenty
> minutes.  A lock is not a queue, and a rank that cannot complete a read cannot
> renew the lease that would keep it alive.

A daemon's client that loses its connection after sending a mutation cannot
know whether the mutation landed.  The daemon MUST therefore treat a resent
request carrying the same client token and request id as the same request and
return the recorded outcome rather than apply it again; a daemon that has
restarted may have forgotten the table, and an implementation SHOULD say so in
its documentation rather than claim exactly-once across restarts.

The runtime's lease renewal while blocked (S10.4) SHOULD be paced to the
device's mutation cost: a poll loop that renews a lease every few seconds is
correct on a shared filesystem and ruinous on a device whose every write is a
network round trip.

---

## Appendix A. Concept correspondence

| MPI | AgentMPI | Transfers? |
|---|---|---|
| process | rank + epoch (durable role, fenced identity) | with a fencing token added |
| `MPI_COMM_WORLD` | `world` | yes |
| communicator context | communicator context | fully; nothing in the agent ecosystem has an equivalent |
| rank | communicator-relative rank | yes; must not be cached across a shrink |
| tag | integer or symbolic tag | extended |
| byte | **token** | unit change with wide consequences |
| eager limit | eager threshold in tokens | fully; the core adaptation |
| unexpected-message buffer | receiver **context window** | fully |
| unexpected-message credit | context credit; context-safe programs | fully |
| rendezvous RTS/CTS | handle plus explicit materialisation | fully |
| derived datatype | **view** | in spirit: declarative bounded projection |
| datatype matching | **contract** matching | extended with volume and semantics |
| non-overtaking rule | non-overtaking rule | verbatim |
| `MPI_Probe` | probe/inbox with token costs | extended: cost visibility |
| `MPI_Op` predefined | runtime operator | yes |
| `MPI_Op_create` | **agent operator** with a continuation protocol | structure yes, cost model no |
| commutativity flag | commutativity flag, default false | inverted default |
| associativity assumed | associativity **declared**, schedules refused | changed |
| collective by program order | collective by **label** | changed; order is unreliable |
| binomial/ring/recursive-doubling | same catalogue | yes, but selection rules rederived |
| in-network aggregation (SHARP) | journal-mediated `flat` collectives | yes |
| `MPI_Win` | window | yes |
| `MPI_Accumulate` | accumulate, runtime operators only | yes |
| `MPI_Compare_and_swap` | CAS (work claiming) | yes |
| `MPI_Win_fence` | fence (BSP superstep boundary) | yes |
| `MPI_Win_lock` | **leased** lock with a fencing token | extended |
| `MPI_Cart_create` / `_shift` | same | yes |
| distributed graph topology | same, **plus the transpose** | extended |
| neighbourhood collectives | same | yes; the scaling argument sharpens |
| `MPI_Comm_spawn` | respawn plus recovery briefing | repurposed |
| `MPIX_Comm_revoke` | revoke | verbatim |
| `MPIX_Comm_shrink` | shrink, plus FT-MPI's in-place mode | extended |
| `MPIX_Comm_agree` | agree, with an optional quorum | extended |
| checkpoint/restart | recovery briefing (durable-execution replay) | replaced |
| PMPI / MPI_T | unconditional trace, and `doctor` | changed; tracing is not opt-in |
| `MPI_ERRORS_ARE_FATAL` default | errors returned, with hints | inverted |
| blocking forever | deadline plus resumable retry | changed |
| — | **context ledger** | no MPI analogue |
| — | **quorum collectives** | no MPI analogue |
| — | **join deadline** | no MPI analogue |
| — | **`Heartbeat(extend)`** | no MPI analogue |
| — | **conflict lifting and invariant verification** | no MPI analogue |
| — | **interface declaration and verification** | no MPI analogue |

## Appendix B. Deliberate omissions in 1.0

Following MPI's practice of standardising only what is understood, this version
omits: automatic context compaction policy; semantic verification of results;
cost- or model-aware scheduling; inter-communicators; persistent collectives;
partitioned communication; sessions; agent-evaluated `reduce_scatter`; and
agent-evaluated `scan`. Each is a research question, and a standard that answers a
research question prematurely is worse than one that leaves a hook.

Byzantine tolerance is acknowledged and out of scope.

## Appendix C. Error classes

`AMPI_SUCCESS`; argument and naming: `AMPI_ERR_ARG`, `AMPI_ERR_RANK`,
`AMPI_ERR_TAG`, `AMPI_ERR_COMM`, `AMPI_ERR_OP`, `AMPI_ERR_WIN`,
`AMPI_ERR_REQUEST`, `AMPI_ERR_TYPE`, `AMPI_ERR_TRUNCATE`; lifecycle:
`AMPI_ERR_NOT_INIT`, `AMPI_ERR_ALREADY_INIT`, `AMPI_ERR_NO_JOB`,
`AMPI_ERR_RUN_EXISTS`, `AMPI_ERR_VERSION`; identity: `AMPI_ERR_IDENTITY`,
`AMPI_ERR_FENCED` (terminal); flow control: `AMPI_ERR_CTX_EXCEEDED`,
`AMPI_ERR_CTX_CREDIT` (retryable), `AMPI_ERR_BUDGET`; progress and failure:
`AMPI_ERR_TIMEOUT` (retryable), `AMPI_ERR_PROC_FAILED`,
`AMPI_ERR_PROC_FAILED_PENDING` (retryable), `AMPI_ERR_REVOKED`, `AMPI_ERR_LATE`,
`AMPI_ERR_COLL_MISMATCH`, `AMPI_ERR_DEADLOCK`; shared state:
`AMPI_ERR_CONFLICT` (retryable), `AMPI_ERR_LOCK_BUSY` (retryable),
`AMPI_ERR_STALE_LEASE`; operators: `AMPI_ERR_OP_FAILED` (retryable),
`AMPI_ERR_OP_UNSOUND`, `AMPI_ERR_INVARIANT`; implementation:
`AMPI_ERR_UNSUPPORTED`, `AMPI_ERR_INTERN`.

## Appendix D. Operational requirements

Two requirements sit outside the protocol proper but are necessary for a run to
mean anything, and both were learned the hard way.

**The runtime MUST be pinned per job, by content.** Protocol *state* lives outside
the agents and is durable, which is the design's central move. The runtime *code*
is shared mutable state, and the specification says nothing about it. An
implementation MUST record a fingerprint of its own source at job creation and
MUST record a diagnostic event when a later caller's fingerprint differs.

> **Rationale, and a correction.** An earlier draft of this appendix said to pin
> the *version*. That is not pinning. We wrote it after a worker crashed inside the
> runtime because the package was edited while a live population executed against
> it --- and then, in a later run, it happened again in exactly the same way, with
> the version string unchanged throughout, because what changed was the code. An
> executor reported an ``ImportError`` from a module another session was in the
> middle of fixing. A content hash catches this and a version string cannot.
>
> The event is advisory rather than fatal, deliberately. A developer iterating on
> a runtime should not be locked out of their own job, and a two-hour agent run
> should not die because a docstring moved. What matters is that the run's journal
> records that its runtime changed underneath it, which is the difference between
> an inexplicable result and an explained one.

**The verifier MUST be versioned, and results MUST be re-scorable.** A
verification-based fault-tolerance scheme inherits the reliability of its
verifier, so an implementation MUST record which version of an acceptance oracle
produced a result, and a harness SHOULD keep the artifacts needed to re-score a
run offline. Our own oracle once contained a case that contradicted the
specification it tested, and the plumbing that carried its report back to the
population parsed that report incorrectly, telling the population that a passing
build had failed. Neither defect was visible from the population's behaviour; both
were visible immediately once runs could be re-scored from stored artifacts.
