# Dossier 04 — The agent and distributed-coordination landscape

**Purpose.** Source material for the Related Work section of *AgentMPI: A Message Passing Interface for Multi-Agent Systems*.
**Compiled:** 31 August 2026. All "current as of" claims refer to that date.
**BibTeX:** `research/refs/04-agents.bib`. Keys are cited inline as `[key]`.

## How to read this dossier

Every system below is assessed against two questions, because those are the two the paper turns on.

1. **Is it a protocol you write a coordination layer *with*, or a framework/product that makes the coordination decisions *for* you?** MPI is the former: it standardises `MPI_Send`, `MPI_Barrier` and `MPI_Allreduce` and says nothing about what your application computes or how it decomposes work. A framework is the latter: it decides that your agents talk in a group chat, or traverse a graph, or hand off to one another, and your job is to fill in the nodes.

2. **What does it provide, concretely, for the five failure modes AgentMPI targets?** Abbreviated throughout as:
   - **F1 — information sharing** between executors (point-to-point and collective data movement)
   - **F2 — robustness to executor death** (detection, notification, and continued progress)
   - **F3 — synchronisation and mutual exclusion** (barriers, locks, atomic operations on shared state)
   - **F4 — executor lifecycle** (join, identity, replacement, termination)
   - **F5 — context-window exhaustion** (accounting for and flow-controlling the scarce resource)

**Verification conventions.** Claims traceable to a primary source (specification text, paper, official documentation, vendor announcement) are stated plainly. Claims I could establish only from secondary reporting are attributed to that reporting. Claims I could not establish are marked `[UNVERIFIED]` and should not be put in the paper without a check. Section F is explicitly my own analysis and is marked as such.

---

## 0. Executive summary

The landscape divides into four strata that do not compose, and none of them is the stratum AgentMPI proposes.

**Interoperability protocols (§A)** — MCP, A2A, and what remains of ACP/AGNTCY/ANP — standardise *reachability*: how an agent finds a tool or another agent, authenticates to it, invokes it, and reads a result. As of mid-2026 they have consolidated under a single governance umbrella (the Linux Foundation's Agentic AI Foundation), which makes the ecosystem tidier without making it any more expressive. None of them has a communicator, a group, a collective operation, a barrier, a reduction, shared state with atomic operations, a failure detector, or flow control. This is not an oversight; it is their scope. MCP is client–server tool access; A2A is agent-to-agent task delegation. Both are, in MPI terms, transport-and-naming layers with a task lifecycle bolted on.

**Classical agent communication languages (§B)** — KQML and FIPA-ACL — did attempt to standardise *meaning*, and the attempt is instructive precisely because it failed in a way that is well documented. Their mentalistic semantics were shown to be unverifiable by an external observer, which is the property a standard most needs. AgentMPI's thesis is the MPI inversion of this: standardise the mechanism, leave meaning to the application. The critique literature (Wooldridge, Singh) is the paper's strongest historical argument and must be cited precisely.

**Multi-agent LLM frameworks (§C)** — AutoGen/AG2, LangGraph, CrewAI, MetaGPT, ChatDev, CAMEL, the OpenAI Agents SDK, Microsoft Agent Framework, OpenHands, and the 2026 entrants — all make the coordination decisions for you. Each embeds one coordination model (graph, group chat, hierarchy, handoff) in its runtime. Crucially, in most of them a substantial part of the coordination logic lives *in the prompt*: whether an agent delegates, when it terminates, whether it reports back, is a decision the model makes, not a mechanism the runtime enforces. The empirical failure literature — MAST above all — shows this is where the failures are.

**Distributed computing models (§D)** — BSP/Pregel, actors, MapReduce/Spark, dataflow and workflow systems, parameter servers, STM — supply the mechanisms AgentMPI wants, but for a different cost model. Ray is the closest live infrastructure and deserves the most careful treatment, because a reviewer will ask "why not just Ray?" and the answer has to be exact.

**Context systems (§E)** supply the evidence that context is the scarce resource and that more of it is not simply better, which is the empirical grounding for putting flow control in the protocol at all.

---

## A. Agent interoperability protocols

### A.0 The 2025–2026 consolidation, briefly

The paper should open Related Work with the governance picture, because it is the single fact that most changes the shape of the field since 2024, and because it is easy to get wrong.

- **MCP** was released by Anthropic in November 2024. On **9 December 2025** Anthropic donated it to the **Agentic AI Foundation (AAIF)**, a directed fund under the Linux Foundation co-founded by Anthropic, Block and OpenAI with support from Google, Microsoft, AWS, Cloudflare and Bloomberg. MCP joined `goose` (Block) and `AGENTS.md` (OpenAI) as founding projects [`anthropic2025aaif`, `linuxfoundation2025aaif`, `openai2025aaif`, `mcp2025aaif`].
- **A2A** was launched by Google in April 2025 and donated to the Linux Foundation on **23 June 2025** at Open Source Summit North America [`linuxfoundation2025a2a`]. On **17 August 2026** it became a hosted project of the AAIF, moving from the Linux Foundation's broader portfolio into the agent-specific fund [`axios2026a2aaaif`, `enterpriseai2026a2aaaif`]. This is a governance move only: no wire format changed and no specification revision accompanied it.
- **ACP** (IBM Research / BeeAI, launched March 2025) announced on **29 August 2025** that it was merging into A2A under LF AI & Data; the ACP team wound down independent development and contributed its assets to A2A [`lfaidata2025acpa2a`].
- **AGNTCY** (Cisco/Outshift with LangChain and Galileo, open-sourced March 2025) was welcomed by the Linux Foundation on **29 July 2025** [`linuxfoundation2025agntcy`]. Its *Agent Connect Protocol* — confusingly also abbreviated ACP — was archived read-only on **11 April 2026** as the invocation layer converged on A2A, per secondary reporting [`alatirok2026agntcy`]. AGNTCY's surviving components are OASF (schema), SLIM (messaging transport), and DIR (discovery/directory).

The net: **two live protocols, one governance body, and no coordination layer anywhere in either of them.** That last clause is the paper's opening.

### A.1 MCP — Model Context Protocol

**What it is.** A JSON-RPC 2.0 protocol between an MCP *client* (embedded in an LLM host application) and an MCP *server* (which exposes capabilities). The server offers three primitive kinds: **tools** (model-invocable functions), **resources** (addressable context data), and **prompts** (user-selectable templates). Client-side features historically included **elicitation**, **sampling** (server asks client for a model completion), and **roots** (client discloses filesystem locations) [`mcp2026spec`].

**Current revision.** The `2026-07-28` specification is the largest change since launch and is materially relevant to how the paper characterises MCP [`mcp2026blog`, `mcp2026spec`, `google2026mcpstateless`]:

- The `initialize`/`initialized` handshake and the `Mcp-Session-Id` header are **removed**. Every request is self-describing, carrying protocol version, client info and client capabilities in a `_meta` field. Capability discovery is now an optional `server/discover` RPC. The stated motivation is that any request can land on any server instance behind a round-robin load balancer without shared storage.
- Server-initiated requests over a held-open stream are replaced by **Multi Round-Trip Requests (MRTR)**, a poll-shaped `input_required` result type.
- Streamable HTTP requests must carry `Mcp-Method` and `Mcp-Name` headers so gateways can route and meter without parsing bodies.
- **Roots, sampling and logging are deprecated** under a new formal deprecation policy with a minimum twelve-month window. The legacy HTTP+SSE transport is likewise deprecated.
- Tasks moved out of the experimental core into an `io.modelcontextprotocol/tasks` extension with poll-based `tasks/get` and `tasks/update`.

**What it does for F1–F5.** Precisely and fairly:

- **F1 (information sharing):** partial and asymmetric. A client can *read* a resource a server exposes, and two agents sharing an MCP server can therefore exchange data through it — but only in the way any two processes sharing a database exchange data. There is no message envelope, no source/tag matching, no addressing of a peer agent. MCP has no notion that the other side of the connection is an agent at all.
- **F2 (executor death):** nothing. There is no liveness model for peers. The 2026 revision deliberately removes the session, which removes even the weak signal a broken session provided. A dead MCP *server* surfaces as a failed request; a dead *peer agent* is invisible because MCP does not model peer agents.
- **F3 (synchronisation):** nothing in the protocol. A server may of course implement a lock as a tool, but that is an application convention, not a protocol guarantee, and the protocol supplies no atomicity, no lease, and no fencing.
- **F4 (lifecycle):** actively *reduced* in the current revision. There is no join, no rank, no identity beyond OAuth client identity, and — as of `2026-07-28` — no session.
- **F5 (context):** nothing. MCP transports content into a context window and has no accounting for what that costs. `resource_link` and tasks-as-extension reduce inline payload in practice, but there is no budget, no back-pressure and no notion of a receiver's remaining capacity.

**Verdict.** MCP is a **client–server tool-access protocol**. It is the right answer to "how does my agent call a tool it did not ship with", and the wrong shape entirely for "how do sixteen executors agree on a decomposition". The paper should be scrupulous here: MCP's designers never claimed the latter, and a reviewer will punish any implication that they did. The useful framing is that MCP occupies the position of a *device driver interface*, not a *communication library*: it standardises access to a peripheral, not communication between peers.

### A.2 A2A — Agent2Agent

**What it is.** A protocol for one agent (client role) to delegate a task to another agent (server role) across vendor and organisational boundaries. Version **1.0.0** is the current stable specification, published **12 March 2026** per secondary reporting, with `spec/a2a.proto` as the single normative definition and three protocol bindings: JSON-RPC 2.0, gRPC, and HTTP+JSON/REST [`a2a2026spec`, `packetnebula2026a2a`]. Note that some secondary sources date v1.0 to January 2026; the March date is better supported and the discrepancy is `[UNVERIFIED]`.

**The model.**

- **Agent Card** — a discovery document declaring the agent's identity, skills, protocol bindings, transport URLs, and security schemes. v1.0 added **signed Agent Cards** for cryptographic identity verification, multi-tenancy (`tenant`), and an extension mechanism (`A2A-Extensions` header) [`a2a2026spec`, `linuxfoundation2026a2ayear`].
- **Operations** — the full surface is small: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`, `GetAgentCard`, and three push-notification-config methods [`a2a2026spec`].
- **Task lifecycle** — a `Task` carries a `TaskState` drawn from a fixed enum: `SUBMITTED`, `WORKING`, `COMPLETED`, `FAILED`, `CANCELED`, `INPUT_REQUIRED`, `REJECTED`, `AUTH_REQUIRED`, plus `UNSPECIFIED`. `COMPLETED`, `FAILED`, `CANCELED` and `REJECTED` are terminal; `INPUT_REQUIRED` and `AUTH_REQUIRED` are interrupted states [`a2a2026spec`].
- **Messages and artifacts** — a `Message` is composed of typed `Part`s; an `Artifact` is a produced output. Streaming delivers `TaskStatusUpdateEvent` and `TaskArtifactUpdateEvent`.
- **Version negotiation** — clients MUST send `A2A-Version`; unsupported versions yield `VersionNotSupportedError`.

**What it does for F1–F5.**

- **F1:** yes, for **one-to-one, client-to-server task delegation**. A2A can carry a message from agent A to agent B and return artifacts. It has no group abstraction, so it cannot express "send this to the other fifteen ranks" except as fifteen independent calls made by application code. There is no source/tag matching: a task is addressed by `taskId`, and the receiver has no `ANY_SOURCE` receive.
- **F2:** weakly. `TASK_STATE_FAILED` reports that a delegated task failed, which is genuine and worth crediting. What A2A does *not* provide is detection of a peer that has stopped responding without failing a task, notification to *third parties* that a peer has died, or any way to continue a group operation in the presence of a failed member. There is no heartbeat, no lease, no failure detector.
- **F3:** nothing. Grepping the v1.0 specification for barrier, quorum, consensus, mutual exclusion or multicast returns nothing relevant; the single occurrence of "broadcast" is about delivering events to multiple subscribed streams *of one task* [`a2a2026spec`].
- **F4:** partial, and better than MCP. Agent Cards give discovery and identity; task states give a delegated unit of work a lifecycle. There is no notion of an executor *joining a job*, of a stable rank, or of replacing a dead participant.
- **F5:** nothing. There is no token accounting, no budget, no back-pressure. Interestingly, the LF's own year-one messaging emphasises that A2A lets agents coordinate "without sharing internal memory" [`linuxfoundation2026a2ayear`] — which is exactly the property that makes context exhaustion invisible to it.

**Verdict.** A2A is a **discovery-plus-RPC protocol with a task lifecycle**, not a coordination protocol. The distinction the paper should draw: A2A standardises the *edge* between two agents; it does not standardise the *structure* of a group of agents. You can build a coordination layer on A2A — and AgentMPI could plausibly be bound to A2A as a transport — but A2A supplies none of the coordination itself. That framing is both accurate and generous, and it is also the framing that makes AgentMPI's contribution legible: MPI likewise did not invent TCP, it defined what you say over it.

### A.3 ACP — Agent Communication Protocol (IBM/BeeAI)

Launched March 2025 by IBM Research to power the BeeAI Platform, ACP emphasised structured message types for handoff negotiation, persistent state for long-running tasks, and asynchronous interaction. On 29 August 2025 it merged into A2A under LF AI & Data; the ACP team ceased independent development and contributed its stateful/long-running concepts to A2A. BeeAI users migrate via `A2AServer`/`A2AAgent` adapters, and the BeeAI Platform was subsequently renamed **Agent Stack**, now built on A2A [`lfaidata2025acpa2a`, `tyk2026protocols`, `beeai2026agentstack`].

For the paper: ACP is best cited as evidence of *convergence*, not as a live competitor. Its one distinctive idea — that long-running stateful work needs first-class protocol support — is exactly the idea AgentMPI takes much further with epochs, leases and revocation. Note carefully that "ACP" also names AGNTCY's Agent Connect Protocol; the paper must disambiguate on first use or a reviewer will assume an error.

### A.4 AGNTCY, ANP, and other 2025–2026 entrants

**AGNTCY.** Open-sourced by Outshift (Cisco) in March 2025 with LangChain and Galileo; Linux Foundation project since 29 July 2025 with Cisco, Dell, Google Cloud, Oracle and Red Hat as formative members [`linuxfoundation2025agntcy`]. Live components: **OASF** (Open Agentic Schema Framework), **SLIM** (Secure Low-Latency Interactive Messaging), and **DIR** (announce and discovery). Its Agent Connect Protocol repository was archived in April 2026 as the invocation layer converged on A2A, per secondary reporting [`alatirok2026agntcy`]. AGNTCY is therefore best characterised as **discovery, identity, messaging transport and observability infrastructure** — genuinely the layer beneath the protocols, and genuinely not a coordination layer. SLIM is worth one sentence in the paper as a transport AgentMPI could bind to.

**ANP — Agent Network Protocol.** A community project (founded by Gaowei Chang, primarily China-based contributors) building a layered stack for the "Agentic Web": W3C DID-based identity (`did:wba`), a meta-protocol negotiation layer, and application protocols for description, discovery and payment. Release **1.1** (mid-2026) added `did:wba`, human-readable WNS handles, and a federated messaging suite covering direct messages, **groups**, end-to-end encryption and cross-domain flows, with DIDs assigned to groups and message services as well as agents [`anp2026site`, `changshan2026anp11`]. It feeds the **W3C AI Agent Protocol Community Group** [`w3ccg2025aiagent`]. As of mid-2026 secondary reporting describes it as a draft specification with the AgentConnect SDK as the main reference implementation and no named production adopters [`rywalker2026anp`, `zylos2026interop`].

ANP's group messaging is the closest thing in the interoperability stratum to a communicator, and the paper should say so honestly. But it is a *federated chat group with verifiable membership*, not a communicator: there is no rank, no ordering guarantee across the group, no collective operation over group members, and no failure semantics. It gives you a secure named multicast domain; it does not give you `Allreduce`.

**Other entrants.** I found no other 2025–2026 general-purpose inter-agent coordination protocol with meaningful adoption. Claims that some vendor "has a barrier" or "has collectives" should be treated as `[UNVERIFIED]` until a specification is produced.

### A.5 OpenAI Agents SDK, Swarm, and the Claude Agent SDK

**Swarm** was OpenAI's 2024 educational prototype introducing *handoffs* and *routines*. Its production successor is the **OpenAI Agents SDK**, whose primitives are Agents, Handoffs, Guardrails, Sessions and Tracing [`openai2026orchestration`, `openai2026handoffs`].

Two orchestration patterns are documented, and the distinction matters for the paper:

- **Handoffs** — a triage agent transfers ownership of the conversation to a specialist, which becomes the active agent. By default the specialist sees the entire conversation history; an `input_filter` can rewrite what it sees; `is_enabled` can hide a handoff from the model at runtime; `nest_handoff_history` exists to reduce context bloat [`openai2026handoffs`].
- **Agents as tools** — `Agent.as_tool()`, where a manager retains ownership and calls specialists as bounded capabilities [`openai2026orchestration`].

The protocol-shaped part is thin but real: a handoff is *implemented as a tool call the model chooses to make*. That is the crux of the paper's "protocol-in-the-prompt" argument in its purest form. Whether control transfers is a decision the model makes; the runtime provides the mechanism for transfer but not for requiring it, timing it, or detecting its absence. Parallelism is left to the host language (`asyncio.gather`).

**Claude Agent SDK / Claude Code.** Anthropic's SDK is single-agent-first with **subagents** (isolated context, spawned via an `Agent` tool, results summarised back to the caller), a `Workflow` tool for orchestrating dozens to hundreds of agents from a script rather than turn-by-turn, and an experimental **agent teams** feature (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) in which teammates each hold an independent context window, message each other directly, and share a task list [`claude2026subagents`, `claude2026agentteams`].

Agent teams are the closest thing in a shipping product to an AgentMPI-like world: named peers, direct messaging, a shared task structure. The differences the paper should name: naming is by string, not rank; there is no group/communicator object and therefore no isolation of one team's traffic from another's; there are no collectives; there is no failure detector (a teammate that stops responding is simply a teammate that stops responding); there is no context accounting beyond the documentation's observation that teams cost more tokens than subagents; and the whole feature is off by default and experimental. Anthropic's own multi-agent cookbook makes the shape explicit — `send_message`, `wait_for_message`, `get_status`, `kill_subagents` over a hub — which is a mailbox, not a communicator [`anthropic2026cookbook`].

### A.6 Protocol capability audit

| | Communicator/group | P2P matching (src/tag) | Collectives | Shared state + atomics | Failure detection | Failure mitigation | Flow control / ctx accounting | Tracing | Portable across hosts |
|---|---|---|---|---|---|---|---|---|---|
| **MCP** `2026-07-28` | no | no | no | no (server may fake it) | no | no | no | partial (headers for metering) | yes (that is its point) |
| **A2A** v1.0 | no | no (task-id addressed) | no | no | task-level only | no | no | optional OpenTelemetry in SDKs | yes |
| **ACP** (merged) | no | no | no | no | no | no | no | — | historical |
| **AGNTCY** (OASF/SLIM/DIR) | no (transport groups only) | no | no | no | no | no | no | yes (observability SDKs) | yes |
| **ANP** 1.1 | messaging groups w/ DIDs | no | no | no | no | no | no | `[UNVERIFIED]` | draft |
| **OpenAI Agents SDK** | no | no | no | no | no | retries/guardrails | context filters, no accounting | yes | no (library, not protocol) |
| **Claude agent teams** | named teams (experimental) | direct messages by name | no | shared task list | no | no | no | yes (session traces) | no |

---

## B. Classical agent communication languages

### B.1 KQML

KQML (Knowledge Query and Manipulation Language) emerged from the DARPA Knowledge Sharing Effort. Its design separates three layers — content, message, and communication — and its central construct is the **performative**: `ask-one`, `tell`, `achieve`, `subscribe`, `advertise`, `broker-one`, and so on. The message layer names the performative and the content language; the communication layer names sender and receiver and message identifiers [`finin1994kqml`, `labrou1994kqml`].

Two features are directly relevant to AgentMPI. First, KQML explicitly separated *envelope* from *content* and refused to standardise the content language — the same instinct as MPI's typed buffers, and the right one. Second, KQML included **facilitator** agents (brokers, matchmakers, recruiters), which are a naming service, not a communicator: they route by advertised capability rather than by membership in a group.

KQML's practical failure was fragmentation: implementations diverged into mutually unintelligible dialects, so the promise of heterogeneous interoperation was never realised [`singh1998acl`]. Cohen and Levesque criticised early KQML for lacking a formal semantics and for omitting commissives — the performatives by which one agent commits to another — which they argued makes many multi-agent scenarios inexpressible [`cohen1995communicative`, `wooldridge1998verifiable`].

### B.2 FIPA-ACL

FIPA (Foundation for Intelligent Physical Agents, from 1995; later an IEEE Computer Society standards committee) produced an ACL superficially similar to KQML: an outer message language, ~20 performatives (`inform`, `request`, `agree`, `refuse`, `propose`, `cfp`, ...), and no mandated content language [`fipa2002acl`]. The difference is that FIPA-ACL was given a **formal semantics** in a Semantic Language (SL), a quantified multi-modal logic with operators for belief, uncertain belief, and choice/intention, drawing on Cohen–Levesque and Sadek. Each performative is specified by *feasibility preconditions* and a *rational effect* expressed in these mental attitudes: an agent may sincerely `inform` only if it believes the proposition and does not believe the recipient already has an opinion about it.

FIPA also standardised **interaction protocols** — Request, Contract Net, Iterated Contract Net, English/Dutch Auction, Brokering, Subscribe — as choreographies over these performatives. This is the closest the classical literature comes to a "collective operation": Contract Net is a structured fan-out/fan-in. It is a template for a conversation, not a callable primitive with completion semantics.

### B.3 The unverifiability critique — get this right, the paper leans on it

This is the load-bearing citation of the section, and the paper should quote it rather than paraphrase.

**Wooldridge** [`wooldridge2000semantic`, and the earlier `wooldridge1998verifiable`] defines what it means for an agent communication framework to be *verifiable*: conformance to the semantics must be determinable by an independent observer. He then shows FIPA-ACL is not, for two compounding reasons:

1. **Ungroundedness.** "The FIPA semantics are given in terms of mental states, and since we do not understand how such states can be systematically attributed to programs, we cannot verify that such programs respect the semantics." SL's Kripke possible-worlds semantics "are not connected in any principled way with computational systems": for an arbitrary program there is no known way of attributing to it an SL formula characterising it in terms of beliefs and desires.
2. **Undecidability.** SL is a quantified multi-modal logic with greater expressive power than first-order logic and is therefore undecidable; even granting a grounding, the verification problem would remain intractable.

The consequence Wooldridge draws is the one the paper needs: *if there is no way of determining whether a system claiming to conform to a standard does conform, the value of the standard itself is in question.*

**Singh** [`singh1998acl`] attacks from a different direction. Mentalistic semantics presuppose "in essence, that agents can read each other's minds. This supposition has never held for people, and for the same reason, it will not hold for agents." He argues for **social semantics** grounded in public commitments, because communication is inherently public and observable. Singh's positive programme (commitment-based semantics) is a real research line and the paper should not imply the critique was purely destructive.

**Why this matters to AgentMPI.** The paper explicitly rejects the KQML/FIPA lineage in favour of MPI's discipline: *standardise the mechanism, leave meaning to the application*. The argument to make is not that KQML and FIPA were badly engineered — they were carefully engineered — but that they chose the one thing that cannot be conformance-tested. `MPI_Barrier` has a verifiable semantics: no process returns until all have entered. Whether the messages ranks exchange are sincere, or informative, or true, MPI declines to say, and that refusal is what made it implementable by fifteen vendors. AgentMPI's `AMPI_Barrier` inherits the same property, and its agent-level operators (semantic reduction, contracts) are deliberately pushed outside the conformance-testable core for exactly this reason. A reviewer who knows this literature will look for whether the paper understands *why* FIPA failed; the answer must be "unverifiability", not "complexity".

### B.4 Contract Net

Smith's Contract Net Protocol [`smith1980contractnet`] is the ancestor of every task-claiming scheme in the modern literature: a manager announces a task, potential contractors submit bids, the manager awards the contract, and the contractor reports back. Announcement–bid–award–report is negotiation as a control structure, and it solves *connection*: matching a task to a node that has not been told in advance what it will do.

The lineage matters to AgentMPI because **task claiming via compare-and-swap is Contract Net with the negotiation removed**. In a window-based scheme, the "announcement" is a task record in shared state, the "bid" is a CAS attempt, and the "award" is the CAS succeeding. What is gained is atomicity: exactly one claimant, decided by the mechanism rather than by a manager's judgement, with no round of messages and no manager to fail. What is lost is the manager's ability to select the *best* bidder rather than the fastest. The paper should say this plainly — it strengthens the design by showing the trade was deliberate — and should credit Smith rather than presenting CAS-based claiming as new. FIPA later standardised Contract Net as an interaction protocol, which is worth a clause.

### B.5 Blackboards and tuple spaces

**HEARSAY-II** [`erman1980hearsay`] is the canonical blackboard system: independent knowledge sources (acoustic, lexical, syntactic) communicate solely by reading from and writing to a shared, structured, multi-level blackboard, with a scheduler choosing which knowledge source to activate. No knowledge source calls another; all interaction is mediated by shared state. **BB1** [`hayesroth1985blackboard`] added explicit control as a blackboard in its own right — the system reasons about which action to take next using the same mechanism it uses to reason about the domain.

**Linda** [`gelernter1985linda`] generalises the idea into a coordination language: a *tuple space* with `out` (write a tuple), `in` (destructively read a matching tuple, blocking until one exists), `rd` (non-destructive read), and `eval` (create a live tuple). Linda's associative matching by template is a genuine precursor to tag matching, and `in` is a genuine atomic operation: exactly one consumer removes a given tuple, which is precisely the task-claiming primitive above. Linda also cleanly separates the coordination language from the computation language — the same separation MPI makes and the same one AgentMPI makes.

**The known problems, which the paper must document because it inherits them.** The blackboard literature and its critics identify a consistent set:

- **Uncontrolled global mutable state.** Any knowledge source may write anywhere on the blackboard. There is no encapsulation, so a change to what one knowledge source writes can silently break another that reads it, and the coupling is invisible in the code.
- **No attribution.** A blackboard entry records a value, not usually who wrote it, when, on what evidence, or at what epoch. Debugging becomes archaeology.
- **No concurrency discipline.** The classical designs assume a scheduler serialising knowledge-source activations. Once activations are genuinely concurrent, there is no defined memory model: no atomicity for read-modify-write, no ordering, no way to express "this update is conditional on what I read".
- **Scheduling is the hard part and it is outside the model.** The control problem — which knowledge source next — dominated the research and was never solved generally; BB1's answer was to reify it, which is honest but does not eliminate it.

**AgentMPI's window is a blackboard with MPI-3 RMA discipline imposed on it,** and the paper should frame it exactly that way: the expressive power of shared state, plus the four things blackboards lack. Named, allocated windows rather than one global surface (scoping); `Put`/`Get`/`Accumulate`/`CAS` as the only access path (a defined memory model); `Win_fence` and leased locks (synchronisation with a defined completion semantics); and a journal that records writer, epoch and time (attribution). The claim to make is not that shared state is new — Hearsay-II is from 1980 and Linda from 1985 — but that shared state *with an enforced access discipline and a failure model* is what the agent world currently lacks.

---

## C. Multi-agent LLM frameworks

### C.1 The comparison table

The "coordination in framework or in prompt?" column is the most important one for the paper's argument and the one most likely to be contested, so the entries below are deliberately conservative: "framework" means the runtime enforces it without the model's cooperation.

| System | Coordination model | Communicator-like namespace? | Collectives? | Failure handling beyond retry? | Context accounting? | Coordination logic in framework or prompt? |
|---|---|---|---|---|---|---|
| **AutoGen / AG2** [`wu2023autogen`] | Conversable agents; `GroupChat` with a manager that selects the next speaker | Group chat is a shared conversation, not a namespace; no ranks | no | no; state is in-memory conversation history by default, durable state needs external integration [`algorithmine2026orchestration`] | token counting available; no budget enforced across agents | **prompt** — speaker selection and termination are model decisions |
| **LangGraph** ≥1.2 [`langgraph2026faulttolerance`] | Explicit `StateGraph`; execution proceeds in **supersteps** with checkpointing after each | graph topology is static and framework-owned; no rank namespace | no (fan-out/fan-in only) | **yes, substantially**: `RetryPolicy`, `TimeoutPolicy` (run vs idle), post-retry error handlers routing via `Command` (saga compensation), checkpointed failure provenance, cooperative drain at superstep boundary | message trimming/summarisation utilities; no protocol-level budget | **framework** — this is the exception, and the paper must say so |
| **CrewAI** [`crewai2026processes`, `crewai2026hierarchical`] | `Process.sequential` or `Process.hierarchical` (manager agent delegates and validates); **Flows** (`@start`/`@listen`/`@router`) add deterministic event-driven orchestration with typed persistable state | crew membership by role name; no namespace isolation | no | task-level retries; Flow-level resume from last step | no | **both** — Flows are framework, Crews are prompt (the manager's delegation is a model decision) |
| **MetaGPT** [`hong2024metagpt`] | SOP-encoded assembly line of role agents (PM, architect, engineer, QA) with structured artefacts | shared message pool with role-based subscription | no | no | no | **prompt**, structured by SOP templates |
| **ChatDev** [`qian2024chatdev`] | Waterfall phases; each phase a two-agent "chat chain" with role inversion | no | no | no | no | **prompt** |
| **CAMEL** [`li2023camel`] | Role-playing dyad (AI user / AI assistant) driven by inception prompting | no | no | no | no | **prompt** |
| **OpenAI Agents SDK** (Swarm successor) [`openai2026orchestration`, `openai2026handoffs`] | Handoffs (ownership transfer) and agents-as-tools (manager retains ownership) | no | no | guardrails; retries; April 2026 added durable execution by snapshot-and-rehydrate per secondary reporting `[UNVERIFIED]` | `input_filter`, `nest_handoff_history` reduce context; no accounting | **prompt** — a handoff *is* a tool call the model chooses |
| **Microsoft Agent Framework** 1.0 [`msagentframework2026overview`, `msagentframework2026durable`] | Graph-based `WorkflowBuilder` with typed routing, fan-out/fan-in, shared state, sub-workflows; plus sequential/concurrent/handoff/group-chat/Magentic patterns | workflow graph is framework-owned; no rank namespace | fan-out/fan-in only | **yes**: Durable Task extension checkpoints each executor/agent step, recovers across distributed workers, completed steps not re-executed; idle-session TTL | session state management; TTL cleanup; no token budget | **framework** for workflows, **prompt** for group-chat/Magentic patterns |
| **Semantic Kernel agents** | superseded by Microsoft Agent Framework; maintained with fixes for ≥1 year post-GA [`langchain2026frameworks`] | — | no | — | — | — |
| **OpenHands / OpenDevin** [`wang2025openhands`] | Single agent in a sandboxed event-stream loop; delegation to sub-agents via a shared workspace | no | no | no | no | **prompt** |
| **Devin (Cognition)** [`cognition2026working`] | Manager Devin decomposes, spawns child Devins, coordinates through an internal MCP; "map-reduce-and-manage" | agent team, ad hoc | no | no | context engineering; no protocol accounting | **prompt** |
| **Claude agent teams** (experimental) [`claude2026agentteams`] | Team lead plus teammates with independent contexts, direct messaging, shared task list | named teams | no | no | documented cost difference only | **prompt** |
| **Ray** (as substrate) [`moritz2018ray`] | tasks + actors + object store; `ray.util.collective` adds real collectives | actor handles; collective **groups** with world size and rank | **yes** — `allreduce`, `broadcast`, `reduce`, `allgather`, `reduce_scatter`, `send`/`recv`, `barrier` [`ray2026collective`] | actor restart, lineage re-execution | no | **framework** — but for tensors, not for agents (see §D.4) |

Two entries deserve emphasis because they are the ones a hostile reviewer will use.

**LangGraph and Microsoft Agent Framework do have real failure handling.** LangGraph 1.2 (May 2026) ships per-node retries, run-vs-idle timeouts, post-retry error handlers that can route to compensation flows, checkpointed failure provenance that survives a process crash, and cooperative drain at a superstep boundary [`langgraph2026faulttolerance`]. The Durable Task extension for Microsoft Agent Framework checkpoints every step, recovers automatically, and does not re-execute completed agent calls [`msagentframework2026durable`]. Any table that marks these "no" is wrong and the paper will be attacked for it.

**But what they provide is durability, not fault tolerance in the MPI sense.** The distinction is precise and worth a paragraph in the paper. Durable execution answers: *the process died; resume the workflow from the last checkpoint.* It assumes the orchestrator is the sole locus of control and that the correct response to a failure is to replay. It does not answer: *rank 7 died while sixteen ranks were inside a barrier; the other fifteen are blocked; what do they observe, when, and what may they do next?* There is no revoke, no shrink, no agreement over the surviving set, no notification to peers, and no defined state for a collective that was in flight. Checkpoint-and-resume is a supervisor-level answer; ULFM-style revoke/shrink/agree is a participant-level answer, and only the latter lets a live population make progress without stopping.

### C.2 Protocol-in-the-prompt

The paper's claim is that coordination logic expressed in a prompt is unreliable because a rank that forgets to enter a barrier prevents the population from progressing. The literature supports this more directly than one might expect.

The mechanism is visible in the frameworks themselves. In the OpenAI Agents SDK a handoff is a tool the model may call [`openai2026handoffs`]; in AG2 the group-chat manager selects the next speaker by asking a model; in CrewAI's hierarchical process the manager agent decides task allocation [`crewai2026hierarchical`]. In each case the *mechanism* is in the runtime and the *decision to invoke it* is in the model. That is fine for a decision that is genuinely a judgement (which specialist is best for this question) and dangerous for a decision that is a protocol obligation (every participant must reach this point before any proceeds), because the failure modes differ: a bad judgement degrades quality locally, while an unmet obligation blocks everyone.

Cognition's account of building manager/child Devin hierarchies is unusually candid on this point [`cognition2026working`]: "Cross-agent communication, a sub-agent writing messages back to its manager to be passed to other agents in the agent team, doesn't happen by default, because models haven't been trained in environments where it needed to. Each of these took dedicated work to fix, and we're still improving on all of them." That is a first-hand report that a coordination obligation left to the model is an obligation not discharged. It also records that "the open problems are all communication problems."

MAST supplies the quantitative version, below.

### C.3 MAST — "Why Do Multi-Agent LLM Systems Fail?"

**Provenance.** Cemri et al., UC Berkeley and collaborators; arXiv 2503.13657 (March 2025); published in the **NeurIPS 2025 Datasets and Benchmarks Track** [`cemri2025mast`]. Report the NeurIPS version's numbers, which differ slightly from early arXiv summaries circulating online.

**Method.** The taxonomy was built by Grounded Theory analysis of an initial **150 traces** from five frameworks (HyperAgent, AppWorld, AG2, ChatDev, MetaGPT) by six expert annotators (>20 hours of annotation per expert), iterated to theoretical saturation, with inter-annotator agreement **κ = 0.88**. It was then applied at scale via an LLM-as-a-judge annotator (built on OpenAI's o1) calibrated to **κ = 0.77** against human labels, and validated on two unseen systems at **κ = 0.79**. The resulting dataset, **MAST-Data**, comprises **1642 annotated execution traces** from **7 frameworks** (adding OpenManus and Magentic) across coding, math and general-agent tasks, with GPT-4-series and Claude-3-series models.

**The taxonomy: 3 categories, 14 failure modes.** Category prevalences over the 1642 traces are **41.8% / 36.9% / 21.3%**; per-mode prevalences as reported in the paper:

*FC1 — Specification and system design issues (41.8%)*
| Mode | Name | Prevalence |
|---|---|---|
| FM-1.1 | Disobey task specification | 11.8% |
| FM-1.2 | Disobey role specification | 1.5% |
| FM-1.3 | Step repetition | 15.7% |
| FM-1.4 | Loss of conversation history (unexpected context truncation, reverting to an antecedent conversational state) | 2.80% |
| FM-1.5 | Unaware of termination conditions | 12.4% |

*FC2 — Inter-agent misalignment (36.9%)*
| Mode | Name | Prevalence |
|---|---|---|
| FM-2.1 | Conversation reset | 2.20% |
| FM-2.2 | Fail to ask for clarification | 6.80% |
| FM-2.3 | Task derailment | 7.40% |
| FM-2.4 | Information withholding | 0.85% |
| FM-2.5 | Ignored other agent's input | 1.90% |
| FM-2.6 | Reasoning–action mismatch | 13.2% |

*FC3 — Task verification and termination (21.3%)*
| Mode | Name | Prevalence |
|---|---|---|
| FM-3.1 | Premature termination | 6.20% |
| FM-3.2 | No or incomplete verification | 8.20% |
| FM-3.3 | Incorrect verification | 9.10% |

**Findings the paper should use.**

- The absence of a single dominant category is itself a finding: the authors argue it indicates balanced coverage rather than bias from a particular system design, and note that individual systems have distinct failure profiles (ChatDev's star topology and lack of a predefined workflow correlate with premature terminations).
- Tactical interventions help but do not solve. Case studies applying improved prompts and topology changes yielded **+9.4%** task success for AG2/MathChat (a workflow change giving the CEO the final say) and **+15.6%** for ChatDev on ProgramDev — real, but leaving absolute completion rates low. The authors' conclusion is explicit: MAS failures "require more than superficial fixes… pointing towards the need for more complex solutions and fundamental MAS redesigns."
- Among structural strategies the authors propose, one is **"establishing a standardized communication protocol"**, on the grounds that LLM agents communicate mainly in unstructured natural language. This is the single most useful sentence in the paper for AgentMPI, and it should be quoted.

**Cross-referencing AgentMPI's five failure modes against MAST — my analysis, stated carefully.** The mapping is partial and the paper must not overclaim it, because MAST classifies *observed behaviours in traces*, not *mechanisms absent from the runtime*.

| AgentMPI failure mode | MAST modes plausibly implicated | Honest caveat |
|---|---|---|
| F1 information sharing | FM-2.4 information withholding (0.85%), FM-2.5 ignored other agent's input (1.90%), FM-1.4 loss of conversation history (2.80%) | Individually small; the paper should *not* claim MAST shows information sharing is the dominant failure |
| F2 executor death | none directly | MAST's corpus is framework runs, not crash-injected. A crashed executor mostly does not appear as a labelled failure mode; the absence is evidence about the corpus, not about the world |
| F3 synchronisation / mutual exclusion | FM-1.3 step repetition (15.7%) is *consistent with* duplicated work absent mutual exclusion | Causal attribution is not established by MAST; state it as consistent-with, not caused-by |
| F4 lifecycle | FM-1.5 unaware of termination conditions (12.4%), FM-3.1 premature termination (6.20%), FM-2.1 conversation reset (2.20%) | This is the strongest mapping: ~21% of failures involve not knowing when the interaction should stop, which is a lifecycle-semantics gap |
| F5 context exhaustion | FM-1.4 loss of conversation history (2.80%) | MAST measures the symptom in traces; the long-context literature in §E is the better evidence |

The defensible summary sentence: *MAST's two largest modes are step repetition (15.7%) and reasoning–action mismatch (13.2%), and its largest structural cluster is termination and verification; taken together roughly a fifth of observed failures concern not knowing when to stop — a lifecycle property that no current framework makes mechanically checkable.*

### C.4 Does multi-agent even help? The 2025–2026 evidence

The paper must engage this literature honestly, because the strongest form of the objection in §F.2 is built from it.

**Against.**
- **Tran & Kiela** [`tran2026singleagent`] (Stanford, arXiv 2604.02460, April 2026) argue from the **Data Processing Inequality** that under a fixed reasoning-token budget and perfect context utilisation, a single agent is more information-efficient: every inter-agent message is a lossy transformation of the sender's context and cannot increase mutual information with the answer. Empirically, across Qwen3, DeepSeek-R1-Distill-Llama and Gemini 2.5, single-agent systems match or outperform multi-agent variants when reasoning tokens are held constant. They also document a methodological artifact — Gemini 2.5 under-spends its declared `thinking_budget` in single-agent mode, silently advantaging MAS in naive comparisons — and introduce an SAS-L scaffold to neutralise it. Their theory predicts MAS becomes competitive precisely when single-agent context utilisation degrades.
- **Google's "Towards a Science of Scaling Agent Systems"** [`google2025scaling`] (arXiv 2512.08296) evaluates 260 configurations across six benchmarks, five architectures (single-agent plus Independent, Centralized, Decentralized, Hybrid) and three model families with standardised tools, prompts and compute. Relative performance change versus single-agent ranges from **+80.8%** (decomposable financial reasoning) to **−70.0%** (PlanCraft sequential planning, where *every* multi-agent variant degraded performance, by 39–70%). They report a **capability-saturation effect**: once a single agent exceeds roughly 45% success, coordination returns diminish rapidly or go negative. Secondary reporting adds that communication overhead grows super-linearly (exponent ≈1.724) and that effective team sizes today are around three or four agents [`venturebeat2026moreagents`].
- **Cognition's "Don't Build Multi-Agents"** [`cognition2025dontbuild`] (Walden Yan, 2025) gives the practitioner's version: share full context and full agent traces, not individual messages; and *actions carry implicit decisions*, so parallel writers make conflicting implicit choices about style, edge cases and patterns. Conclusion at the time: keep it single-threaded.

**For, and the reconciliation.**
- **Anthropic's multi-agent research system** [`anthropic2025multiagent`] reports an orchestrator-worker architecture (Opus 4 lead, 3–5 Sonnet 4 subagents each with its own context window) outperforming single-agent Opus 4 by **90.2%** on their internal research eval, cutting research time by up to 90% on complex queries — at roughly **15× the tokens of a chat interaction** (agents generally use ~4×). They state plainly that domains requiring all agents to share the same context, or with many dependencies between agents, are not a good fit today.
- **Cognition's follow-up, "Multi-Agents: What's Actually Working"** [`cognition2026working`] (2026) revises the earlier position without retracting it: parallel-*writer* swarms still do not work, but a narrower class does — *multiple agents contributing intelligence while writes stay single-threaded*. Reported patterns: a clean-context review agent that shares **no** context with the coder catches ~2 bugs per Devin-written PR of which ~58% are severe, and works better *because* of the shorter context (they cite context rot explicitly); a "smart friend" escalation tool that works across frontier models but not with an asymmetrically weaker primary; and manager/child Devin hierarchies coordinated through an internal MCP, which they describe as "map-reduce-and-manage". They dismiss unstructured swarms — "arbitrary networks of agents negotiating with each other" — as "mostly a distraction".

**The synthesis the paper should adopt.** The deciding variable is task coupling and compute accounting, not architecture. Multi-agent pays when subtasks are loosely coupled, when the information exceeds one context window, and when task value clears the token premium; it loses when work is tightly coupled and sequential. This is *good* for AgentMPI's positioning if the framing is right: the paper is not arguing that more agents are better. It is arguing that where multiple executors are used — which is now routine, and which the Google study shows can produce +80% on decomposable work — the coordination is currently ad hoc, unverifiable, and prompt-resident. A protocol that makes coordination cheap and checkable *also* makes it possible to measure when coordination is not worth it. But the paper must not be caught claiming multi-agent superiority as a premise.

---

## D. Distributed computing models to position against

### D.1 BSP, Pregel, Giraph

**BSP** [`valiant1990bsp`] structures computation as supersteps: concurrent local computation, then communication, then a barrier synchronisation, with a cost model parameterised by the number of processors, a synchronisation periodicity `L`, and a throughput ratio `g`. Its contribution is that a barrier-separated structure makes performance *predictable* and reasoning about state *tractable* — after a barrier, every process's view of the previous superstep is complete.

**Pregel** [`malewicz2010pregel`] applies BSP to graphs: vertex-centric "think like a vertex" computation, message passing along edges, superstep barriers, and vote-to-halt termination, with fault tolerance by checkpointing at superstep boundaries and re-execution of lost partitions. **Apache Giraph** is the open-source implementation.

**Relevance.** `AMPI_Win_fence` is a BSP superstep boundary in the agent setting: it separates an epoch in which ranks write to a window from an epoch in which they read what others wrote, and it gives the same guarantee — after the fence, everything written before it is visible. It is worth noting that **LangGraph's execution model already calls its steps "supersteps"** and checkpoints at their boundaries [`langgraph2026faulttolerance`], which is evidence that the agent world has independently rediscovered BSP — and a point in AgentMPI's favour, since the natural next question is where the collectives are.

The honest difference to acknowledge: BSP assumes uniform, cheap, predictable local computation. An agent superstep costs seconds to minutes, has heavy-tailed latency, and may fail. That is why AgentMPI's fences must be deadline-bounded and its barriers must name absentees, which is a real departure from the BSP literature rather than an application of it.

### D.2 The actor model

**Hewitt, Bishop and Steiger** [`hewitt1973actor`] define actors as universal primitives that communicate solely by asynchronous message passing, each with a mailbox and its own state; **Agha** [`agha1986actors`] gives the standard formal treatment. **Erlang/OTP** [`armstrong2003erlang`] is the industrial realisation and contributes the parts most relevant here: process isolation with no shared memory, `link`/`monitor` for failure *notification* (a linked process receives an exit signal when its peer dies), supervision trees with restart strategies and **maximum restart intensity**, and the "let it crash" discipline. **Akka** carries this to the JVM with cluster membership, sharding and persistence.

**Why the absence of collectives matters — the argument the paper should make.** The actor model gives you `send` and it gives you failure notification; it gives you neither collectives nor a group abstraction. Anything collective must be built by hand each time, and hand-built collectives have three recurring problems: (i) they are re-implemented incompatibly in every system, so no harness is portable across them; (ii) the naive implementations are linear in the number of participants (a manager fanning out to *p* actors and awaiting *p* replies), where the standard collective algorithms are logarithmic — which matters much more in the agent setting, where each "message" costs tokens in a receiver's context, than it does for bytes on a wire; and (iii) their failure behaviour is unspecified, so the question "what does an in-flight reduction do when a participant dies" has a different answer in every codebase. MPI's answer to (i) and (ii) was the collective catalogue plus algorithm selection; its answer to (iii) took until ULFM. AgentMPI's claim is that agents need all three at once.

What AgentMPI *takes* from the actor lineage should be stated as debt, not novelty: leases and heartbeats are `monitor`; supervision with `MAX_RESTARTS_PER_RANK` is OTP's max restart intensity; "let it crash" is the failure model.

### D.3 MapReduce and Spark

**MapReduce** [`dean2004mapreduce`] fixes one communication pattern — map, shuffle by key, reduce — and, in exchange, gives transparent fault tolerance by re-executing deterministic tasks, plus straggler mitigation by speculative execution. **Spark** [`zaharia2012spark`] generalises to a DAG of transformations over RDDs, with lineage-based recovery instead of replication.

The relevance is a cautionary one. Both systems obtain their fault tolerance from a property agent executors do not have: **deterministic, idempotent, re-executable tasks**. Re-running a map task yields the same output; re-running an agent turn does not, and may not even be free of side effects. This is why AgentMPI cannot simply adopt lineage re-execution and must instead journal, epoch, and fence. The paper should state this explicitly, because "why not just re-execute like Spark" is a natural reviewer question with a short, decisive answer.

The second point is that a fixed communication pattern is a real design option, and agent frameworks have effectively taken it: Cognition's "map-reduce-and-manage" [`cognition2026working`] is MapReduce with an LLM in each slot. The argument against fixing the pattern is the same one that motivated MPI over specialised libraries: it is the *general* interface that lets a harness be written once.

### D.4 Dataflow and workflow systems, and Ray in particular

**Dryad** [`isard2007dryad`] executes a DAG of sequential programs connected by channels, with runtime graph refinement. **Airflow** [`airflow2015`] schedules DAGs of tasks with retries and backfills; it is a batch scheduler, not a communication library. **Dask** [`rocklin2015dask`] provides dynamic task graphs and blocked algorithms for out-of-core arrays.

**Ray** [`moritz2018ray`] deserves a full paragraph because it is the closest thing to infrastructure people actually build agent systems on, and because a reviewer will propose it as the alternative.

*What Ray gives you.* Stateless **tasks** and stateful **actors** as remote-callable units, with actor handles for addressing; a distributed **object store** (Plasma) with zero-copy shared memory on a node and automatic object transfer between nodes; futures (`ObjectRef`) and `ray.get`/`ray.wait` for asynchronous composition; a global control store; automatic **lineage-based re-execution** for tasks and configurable **actor restart**; placement groups for gang scheduling; autoscaling; and, in `ray.util.collective`, a genuine collective communication library: `send`, `recv`, `broadcast`, `allreduce`, `reduce`, `allgather`, `reduce_scatter` and `barrier`, over GLOO (CPU) and NCCL (GPU) backends, with explicit collective **groups** initialised with a world size and per-actor rank [`ray2026collective`]. `gather`, `scatter` and all-to-all are documented as not supported in either backend.

*What Ray does not give you, stated exactly.* This matters, and glib claims here will be caught.

1. **Its collectives are over tensors, not over agents.** `ray.util.collective` operates on NumPy/PyTorch/CuPy buffers with numeric `ReduceOp`s (SUM, PROD, MIN, MAX). There is no reduction whose operator is a model call, no notion of combining natural-language artefacts, and no way to express "reduce sixteen design proposals into one" — which is the operation an agent system actually needs. Crediting Ray with "collectives: yes" in a table is correct; leaving it there is misleading, and the paper should carry the qualifier in the cell.
2. **A collective group is not a communicator.** Ray's groups are named collections with ranks for the purpose of a collective call. There is no context/tag isolation (a library's internal collective traffic cannot be made invisible to application receives), no `Comm_split`/`Comm_dup`, no topology, and no group-scoped failure semantics.
3. **No agent-specific failure semantics.** Ray restarts actors and re-executes lineage; that is durability plus supervision. There is no revoke (make every operation on a communicator fail fast so blocked peers unblock), no shrink (form a new group over survivors), no agreement over the surviving set, and no defined outcome for a collective interrupted by a participant's death. Ray's own collectives inherit NCCL/GLOO semantics, where a failed participant typically hangs or aborts the group.
4. **No context accounting.** Ray meters CPU, GPU, memory and custom resources. Tokens are not a resource Ray knows about; there is no budget, no ledger, no eager/rendezvous threshold, no back-pressure keyed on a receiver's remaining window.
5. **Not a portable interface.** Ray is an implementation. A harness written against Ray runs on Ray. The MPI analogy is exact: Ray is PVM or a vendor library; the thing that was missing in 1992 was the *standard*.

The honest summary for the paper: **AgentMPI could be implemented on Ray**, and saying so is a strength, not a concession — MPI implementations run on many transports. The claim is that Ray is a substrate, not an interface, and that the abstractions an agent harness needs (a communicator, a semantic reduction, a context ledger, revoke/shrink) are not among the ones Ray defines.

### D.5 Parameter servers and stale-synchronous parallel

**Li et al.** [`li2014parameterserver`] describe a parameter server for distributed machine learning: a sharded key-value store of parameters with push/pull, user-defined filters, and configurable consistency — sequential, eventual, and bounded delay. **Ho et al.** [`ho2013ssp`] give the **stale synchronous parallel** model: workers proceed at their own pace but no worker may be more than `s` clocks ahead of the slowest, which bounds staleness while eliminating the straggler cost of a hard barrier, with convergence guarantees that degrade gracefully in `s`.

**This is the lineage of AgentMPI's quorum collectives, and the paper should say so in as many words rather than claiming novelty.** A quorum barrier that releases when a fraction `q` of ranks have arrived is the same trade SSP makes: bounded inconsistency purchased in exchange for immunity to the slowest participant. The differences worth stating are (i) the unit — SSP bounds staleness in *clocks*, quorum collectives bound *participation* — and (ii) the guarantee — SSP has a convergence proof for SGD, whereas an agent reduction has no analogous convergence theory, so the paper should claim engineering lineage, not inherited guarantees. The parameter server's *tunable* consistency is also directly the ancestor of a window with a settable consistency mode.

### D.6 Software transactional memory and optimistic concurrency

**Herlihy and Moss** [`herlihy1993tm`] proposed hardware transactional memory as an alternative to lock-based synchronisation; **Shavit and Touitou** [`shavit1995stm`] gave the software version. **Kung and Robinson** [`kung1981occ`] set out optimistic concurrency control: execute against a read set, validate at commit, restart on conflict. **Herlihy and Wing** [`herlihy1990linearizability`] supply linearizability, the correctness condition that makes "atomic" mean something checkable.

The relevance to the window/CAS story is narrow but real. Compare-and-swap on a window key is optimistic concurrency at single-key granularity: a claimant reads a value, decides, and attempts a conditional write that fails if the value changed. What it does *not* provide is multi-key atomicity — an agent that must claim two related tasks together cannot do so — which is exactly the gap STM fills, and which the paper should acknowledge as a deliberate omission with a cost. The STM literature's hard-won lessons about composability and about the cost of aborting long transactions apply with unusual force here, because aborting an agent transaction means discarding minutes of model work rather than microseconds of CPU. That asymmetry is a good argument for leased locks alongside CAS, and the paper can make it.

---

## E. Context management and LLM-specific systems work

The paper claims context is the scarce resource and that flow control belongs in the protocol. The evidence for the first half is now strong; the second half is the contribution.

### E.1 Why more context is not simply better

- **Lost in the middle** [`liu2024lostmiddle`] (TACL 2024) is the canonical result: performance on multi-document QA and key-value retrieval is highest when relevant information is at the beginning or end of the input and degrades substantially when it sits in the middle, producing a U-shaped curve; performance also degrades as context grows, *even for models explicitly designed for long contexts*.
- **Context rot** [`hong2025contextrot`] (Chroma, July 2025) evaluates 18 frontier models across five experiments holding task complexity constant and varying only input length. Every model degraded with input length. Findings that matter for a protocol design: degradation is non-uniform; performance falls faster when needle–question semantic similarity is lower; distractors compound (one distractor hurts, four hurt more); and — counter-intuitively — logically coherent haystacks produced *worse* performance than randomly shuffled ones across all 18 models. The LongMemEval experiment shows a large gap between a 300-token focused input and a 113K-token full input, both well inside declared window sizes.
- **Long-horizon degradation.** A 2026 study of context rot in deep-search agents identifies **premature termination** — models giving up or answering with low confidence long before exhausting the window — and shows the premature-termination rate rises with context length after controlling for query difficulty [`gair2026contextrot`]. A separate 2026 result on agent monitoring finds frontier models miss dangerous actions 2× to 30× more often when the action occurs after 800K tokens of benign activity than in isolation [`classifier2026contextrot`]. Both are cases of the same phenomenon: capability is a function of context occupancy, not just of window size.

The synthesis for the paper: **the effective context window is smaller than the declared one, by a task-dependent and unpredictable margin.** That is precisely the condition under which a resource needs *accounting and flow control* rather than a static limit — the same argument that produced eager/rendezvous thresholds and unexpected-message buffer limits in MPI, transposed from bytes to tokens.

### E.2 Mechanisms: caching, compression, retrieval

- **PagedAttention / vLLM** [`kwon2023vllm`] (SOSP 2023) is the systems-side reference point: KV cache memory is managed in non-contiguous blocks with an OS-style paging scheme, near-eliminating internal and external fragmentation and enabling sharing across requests, for 2–4× throughput gains. It is worth citing as the demonstration that *treating context as a managed resource, with an explicit allocator, is a systems problem with systems answers* — which is the analogy the paper wants. It is not, of course, a coordination mechanism: vLLM manages one server's memory, not a population's attention.
- **Compression and compaction.** Auto-compaction (summarise the trajectory when the window nears full) is now standard practice in agent products; LangChain's write/select/compress/isolate framing [`langchain2025contextengineering`] is a useful vocabulary and explicitly cites Cognition's practice of summarising at agent–agent boundaries with a fine-tuned model. The 2025 survey of context engineering [`mei2025contextsurvey`] organises the space (retrieval and generation; processing; management — memory hierarchies, compression, optimisation) over 1400+ papers. A 2026 result names a specific hazard: the **compaction cliff**, where type-blind compaction degrades safety-rule recall from 53% after one round to 10% by round five, motivating type-dependent retention policies [`compactioncliff2026`]. Secondary reporting of the ACON line describes 26–54% peak-token reductions with 95%+ task accuracy retained via failure-driven optimisation of compression prompts [`zylos2026compression`].
- **Retrieval augmentation as context management** [`lewis2020rag`] is the original move of *not* putting everything in the window and fetching on demand. The AgentMPI analogue is the rendezvous protocol: send a handle and materialise on request. The paper should draw that parallel explicitly, since it makes the eager/rendezvous threshold legible to an ML audience.

### E.3 Token budgeting and cost-aware orchestration

There is now a small but real literature on spending a token or money budget across an agent pipeline, and the paper should cite it to show the problem is recognised while noting that all of it sits *above* the protocol layer, in the orchestrator's policy.

- **ZEBRA** [`zebra2026`] reduces multi-phase budget allocation to a continuous nonlinear knapsack problem: an LLM controller estimates per-phase utility curves and a water-filling search over the Lagrange multiplier returns the split. At 0.5× the unconstrained spend it recovers 94.4% of unconstrained quality on a 150-task APPS benchmark versus 88.1% for letting an LLM allocate directly.
- **Budget-aware agentic routing** [`bopo2026`] treats per-step model selection (cheap vs expensive) as a sequential, path-dependent problem under strict per-task spending limits, trained with boundary-guided policy optimisation.
- **Constraint-driven online allocation** [`mcpp2026`] formulates workflow execution under an explicit budget *and* deadline as a finite-horizon stochastic online allocation problem, solved by Monte Carlo portfolio planning with replanning after observed outcomes.

**The gap AgentMPI addresses.** Every one of these is a *policy* for spending a budget, and each assumes an orchestrator with global knowledge that decides allocations. None of them is a *mechanism* by which a receiver declines a message it cannot afford, or by which a sender learns that a peer is near exhaustion, or by which exhaustion is reported as a distinguishable failure kind rather than a silent quality collapse. Budget allocation and flow control are complementary: the former decides how much each participant may spend, the latter prevents one participant from spending another's budget without permission. The paper should make that distinction crisply, because it is the cleanest statement of why context flow control belongs in a protocol and not in an orchestrator.

---

## F. Synthesis — my analysis

**Everything in this section is my assessment, not a report of a source.**

### F.1 The master table

Rows are the systems; columns are the capabilities AgentMPI claims. Marks: **yes** = provided as a first-class, documented mechanism; **partial** = something adjacent exists, qualified in the cell; **no** = absent. Where a competitor does provide something, it is marked, because a reviewer will check.

| System | Communicator / namespace isolation | P2P matching (src, tag, ANY_SOURCE) | Collectives | Shared state + atomics | Failure detection | Failure mitigation (revoke/shrink-like) | Flow control / context accounting | Tracing | Portability across hosts |
|---|---|---|---|---|---|---|---|---|---|
| **MCP** 2026-07-28 | no | no | no | no | no | no | no | partial (routing/metering headers) | **yes** (multi-vendor, standardised) |
| **A2A** v1.0 | no | no (task-id addressed) | no | no | partial (task FAILED state only) | no | no | partial (OpenTelemetry in SDKs) | **yes** |
| **AGNTCY** (SLIM/DIR/OASF) | partial (transport groups) | no | no | no | no | no | no | **yes** (observability SDKs) | yes |
| **ANP** 1.1 | partial (DID-identified messaging groups) | no | no | no | no | no | no | `[UNVERIFIED]` | draft |
| **KQML** | no (facilitators are naming) | partial (`:sender`, `:receiver`, `:reply-with`, `:in-reply-to`) | no | no | no | no | no | no | attempted, defeated by dialects |
| **FIPA-ACL** | no | partial (`conversation-id`, `reply-with`) | partial (Contract Net as a choreography) | no | no | no | no | no | yes on paper; unverifiable semantics |
| **Contract Net** | no | n/a | partial (structured fan-out/fan-in) | no | partial (unanswered award) | no | no | no | pattern, not implementation |
| **Blackboards / Linda** | Linda: multiple tuple spaces | Linda: associative template matching | no | **yes** (`in` is atomic destructive read) | no | no | no | no | Linda: yes (language-level) |
| **AutoGen / AG2** | no | no | no | no | no | no | no | partial | no |
| **LangGraph** ≥1.2 | no | no | no | partial (checkpointed graph state; no atomics) | partial (node timeouts, incl. idle) | partial (error handlers, saga compensation, cooperative drain — but no peer notification) | partial (trimming/summarisation utilities) | **yes** (LangSmith) | no |
| **CrewAI** | no | no | no | no | no | partial (Flow resume) | no | partial | no |
| **MetaGPT / ChatDev / CAMEL** | no | partial (MetaGPT role-subscribed message pool) | no | no | no | no | no | no | no |
| **OpenAI Agents SDK** | no | no | no | no | no | partial (guardrails, retries; durable execution `[UNVERIFIED]`) | partial (handoff input filters) | **yes** | no |
| **Microsoft Agent Framework** 1.0 | no | no | partial (typed fan-out/fan-in) | partial (workflow shared state) | partial (durable step failure) | partial (checkpoint/recover; no peer notification) | partial (session TTL) | **yes** (OpenTelemetry) | partial (Python + .NET, one vendor) |
| **Claude agent teams** | partial (named teams) | partial (address a teammate by name) | no | partial (shared task list) | no | no | no | **yes** | no |
| **Ray** + `ray.util.collective` | partial (collective groups w/ ranks; no context isolation) | **yes** (`send`/`recv` between ranks) | **yes** (tensors only; no `gather`/`scatter`/all-to-all) | **yes** (object store; actor state; no cross-object atomics) | partial (actor death detection, restart) | partial (lineage re-execution, actor restart; no revoke/shrink/agree) | no (no token resource) | **yes** (dashboard, timeline) | no (an implementation, not an interface) |
| **BSP / Pregel** | no | no (edge-directed) | **yes** (barrier; aggregators) | partial (aggregators) | partial (superstep failure detection) | partial (checkpoint + partition re-execution) | no | no | model / one implementation |
| **Actors (Erlang/Akka)** | partial (process groups, `pg`) | partial (selective receive by pattern) | no | no (deliberately) | **yes** (`monitor`, exit signals) | **yes** (supervision trees, restart intensity) | no | partial | model, multiple runtimes |
| **Parameter server / SSP** | no | no | **yes** (push/pull aggregation) | **yes** (KV store, tunable consistency) | partial | partial (replica-based) | partial (bounded staleness is flow control of a kind) | no | no |
| **MPI-3 + ULFM** | **yes** | **yes** | **yes** | **yes** (RMA, `Compare_and_swap`) | **yes** (via ULFM) | **yes** (revoke/shrink/agree) | partial (eager/rendezvous, in bytes) | partial (PMPI, MPI_T) | **yes** |
| **AgentMPI (proposed)** | **yes** | **yes** | **yes** (incl. semantic operators, quorum variants) | **yes** (windows, CAS, leased locks) | **yes** (leases, two-phase suspicion) | **yes** (revoke/shrink/agree, respawn + briefing) | **yes** (token ledger, eager/rendezvous in tokens) | **yes** (mandatory trace) | **yes** (the claim under test) |

Reading the table: the only rows with a majority of "yes" are MPI and the models the paper draws on. Among live agent systems the fullest row is **Ray**, and its yeses are for tensors and processes rather than for agents and contexts. Among agent-native systems the fullest rows are **LangGraph** and **Microsoft Agent Framework**, and their yeses are durability and observability rather than group communication. The two columns that are empty across the entire agent stratum are **collectives** and **context accounting** — which is a tidy statement of the paper's contribution, and one that survives checking.

### F.2 The strongest objection to the AgentMPI thesis

Stated at full strength, without hedging, as a reviewer would put it. I think this is the objection that would sink the paper if unanswered, and it has three interlocking parts.

**Part one: MPI's model presupposes conditions agent systems do not satisfy.** MPI is designed for SPMD execution over a static, homogeneous rank set, where every rank runs the same program, has the same capabilities, and costs the same to run; where communication is over a reliable, low-latency, low-cost interconnect; where operations are deterministic, so an operation's meaning does not depend on which rank performs it; and where failure is rare enough that ULFM took two decades to be treated as urgent. Agent systems satisfy none of these. Executors are heterogeneous (different models, different tools, different prices), the population is dynamic by design (you spawn a subagent because you discovered you needed one, not because you sized the job in advance), operations are non-deterministic (the same prompt to the same rank yields different output), and "communication" is not a wire transfer but an injection into a scarce, degrading, non-associative resource. Under these conditions the central MPI abstractions may not merely be inconvenient — they may be **category errors**. A rank is only meaningful if ranks are interchangeable; a collective is only meaningful if the operator is associative and commutative, which no model-mediated reduction can be; a barrier is only affordable if the cost of waiting is bounded, which for a heavy-tailed agent turn it is not. The paper's own design betrays the strain: it needs deadline-bounded blocking, idempotent retry, quorum collectives, two-phase failure suspicion, and a distinction between reproducible and irreproducible reductions — five departures from MPI that together suggest the borrowed abstractions are being bent to fit rather than fitting.

**Part two: agent coordination is dominated by semantics, not mechanism, so a semantics-thin protocol cannot help.** This is the empirical form of the objection and MAST is the ammunition. Look at what actually fails: disobeying task specification (11.8%), step repetition (15.7%), reasoning–action mismatch (13.2%), incorrect verification (9.1%). Not one of these is a mechanism failure. No barrier prevents an agent from repeating a step it has already done; no communicator prevents it from ignoring its role; no atomic CAS makes its verification correct. The failures are failures of *understanding what the other agent meant and what the task requires* — which is precisely the territory KQML and FIPA tried to standardise and precisely the territory AgentMPI declines to enter. A protocol that standardises the mechanism and leaves meaning to the application is therefore standardising the part that was not broken. Worse, Tran and Kiela's Data Processing Inequality argument suggests the mechanism can only *lose* information: every inter-agent message is a lossy transformation of the sender's context, so a better-engineered channel does not add information, it only makes the loss cheaper and more reliable. And the Google scaling study says the whole enterprise has a ceiling: once a single agent exceeds ~45% success on a task, coordination returns go negative, so the population AgentMPI is designed to coordinate is a population you should not have built.

**Part three: the historical analogy is doing work it cannot bear.** The claim that multi-agent systems are where parallel computing was in 1991 assumes the two fields are at the same *kind* of impasse. In 1991 the mechanisms were understood — send, receive, barrier, reduce were all well defined and implemented many times — and the missing thing was agreement on names and semantics, which is why MPI-1 could be specified in fourteen months and implemented by everyone within two years. In 2026 the mechanisms are not understood: nobody knows what the right operator for a semantic reduction is, whether a barrier is the right synchronisation for agents at all, or how to specify a context budget that is stable across model versions. Standardising now would freeze a design space that is still moving, and the standard would be obsolete within a model generation. The MPI Forum succeeded because it standardised *late*, over a decade of accumulated practice with PVM, NX, Express and Chameleon. AgentMPI is standardising early, over eighteen months of practice, and the correct historical analogy may not be MPI-1 in 1994 but **HPF in 1993**: a carefully designed, broadly endorsed standard for a problem the community had not yet understood well enough to standardise, which was implemented, benchmarked, and abandoned.

**What the paper must do with this.** It cannot dismiss any of the three parts. My assessment of where the answers are: Part one is answerable — the five departures are documented as departures and each is justified by measurement, and MPI itself is not SPMD-only (MPMD and dynamic process management are in the standard). Part two is answerable only by conceding the premise and narrowing the claim: AgentMPI does not fix semantic failures and should not claim to; its claim is that mechanism failures are currently *indistinguishable* from semantic ones because there is no mechanism layer to rule out, and that MAST's own recommendation of "a standardized communication protocol" as a structural strategy is a request for exactly this. Part three is the hardest and I do not think it can be answered by argument, only by evidence: the paper needs a conformance suite that more than one implementation passes, and a harness that runs unmodified across more than one host. Absent that, the HPF comparison stands, and a reviewer is entitled to make it.

---

## Appendix: open items and unverified claims

- A2A v1.0 release date: 12 March 2026 per secondary reporting; some sources say January 2026. `[UNVERIFIED]` — check the A2A GitHub release notes before citing a date.
- AGNTCY `acp-spec` archival date (11 April 2026): secondary reporting only. `[UNVERIFIED]`.
- OpenAI Agents SDK April 2026 additions (native sandbox execution, durable execution by snapshot-and-rehydrate, subagents): secondary reporting only. `[UNVERIFIED]` — check the SDK changelog.
- ANP tracing/observability support: not established. `[UNVERIFIED]`.
- MAST per-mode percentages are taken from the NeurIPS 2025 camera-ready; earlier arXiv versions and third-party summaries circulate slightly different figures (e.g. FM-3.3 at 9.1% vs 9.10%, and category splits quoted to one decimal). Cite the NeurIPS version.
- The claim that no 2025–2026 inter-agent protocol provides a barrier or collective is a negative result established by reading the MCP `2026-07-28` and A2A v1.0 specifications and by keyword search over them. It is strong but not exhaustive across extensions; an extension registry entry could exist. Recommend re-checking the A2A extension registry before camera-ready.
- Anthropic's 90.2% figure is from an internal, unpublished evaluation. Cite it as a vendor-reported number, never as a benchmark result.
