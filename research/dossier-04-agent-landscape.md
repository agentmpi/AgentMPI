# Dossier 04 — The agent and distributed-coordination landscape

**Purpose.** Source material for the Related Work section of *AgentMPI: A Message Passing Interface for Multi-Agent Systems*.
**Compiled:** 31 August 2026. All "current as of" claims refer to that date.
**BibTeX:** `research/refs/04-agents.bib`. Keys are cited inline as `[key]`.

## How to read this dossier

Every system below is assessed against two questions. **(1) Is it a protocol you write a coordination layer *with*, or a framework that makes the coordination decisions *for* you?** MPI is the former: it standardises `MPI_Send`, `MPI_Barrier` and `MPI_Allreduce` and says nothing about what your application computes. A framework is the latter: it decides that your agents talk in a group chat, traverse a graph, or hand off to one another, and your job is to fill in the nodes. **(2) What does it provide, concretely, for the five failure modes AgentMPI targets?** Abbreviated throughout: **F1** information sharing between executors; **F2** robustness to executor death; **F3** synchronisation and mutual exclusion; **F4** executor lifecycle; **F5** context-window exhaustion.

**Conventions.** Primary-source claims are stated plainly; claims resting on secondary reporting are attributed to it; claims I could not establish are marked `[UNVERIFIED]`. Section F is my own analysis.

---

## 0. Executive summary

Four strata that do not compose, and none is the stratum AgentMPI proposes. **Interoperability protocols (§A)** — MCP, A2A, and what remains of ACP/AGNTCY/ANP — standardise *reachability*: how an agent finds a tool or another agent, authenticates, invokes, and reads a result. By mid-2026 they have consolidated under one governance umbrella, which makes the ecosystem tidier without making it more expressive; none has a communicator, a collective, a barrier, a reduction, shared state with atomics, a failure detector, or flow control, and that is their scope rather than an oversight. **Classical ACLs (§B)** did try to standardise *meaning*, and failed in a well-documented way: KQML's and FIPA-ACL's mentalistic semantics are unverifiable by an external observer, the property a standard most needs. AgentMPI is the MPI inversion of that choice, and the critique literature is its strongest historical argument. **Multi-agent LLM frameworks (§C)** each embed one coordination model in their runtime, and in most a substantial part of the coordination logic lives *in the prompt*: whether an agent delegates, when it terminates, whether it reports back, is a model decision rather than an enforced mechanism, and the empirical failure literature — MAST above all — shows this is where the failures are. **Distributed computing models (§D)** supply the mechanisms AgentMPI wants, at a different cost model; Ray needs the most care, because "why not just Ray?" is the reviewer question with the highest cost of a sloppy answer. **Context systems (§E)** supply the evidence that context is scarce and that more of it is not simply better — the grounding for putting flow control in the protocol at all.

---

## A. Agent interoperability protocols

### A.0 The 2025–2026 consolidation

Open Related Work with the governance picture: it is the fact that most changes the field's shape since 2024, and it is easy to get wrong.

- **MCP** was released by Anthropic in November 2024. On **9 December 2025** Anthropic donated it to the **Agentic AI Foundation (AAIF)**, a directed fund under the Linux Foundation co-founded by Anthropic, Block and OpenAI with support from Google, Microsoft, AWS, Cloudflare and Bloomberg. MCP joined `goose` (Block) and `AGENTS.md` (OpenAI) as founding projects [`anthropic2025aaif`, `linuxfoundation2025aaif`, `openai2025aaif`, `mcp2025aaif`].
- **A2A** was launched by Google in April 2025 and donated to the Linux Foundation on **23 June 2025** [`linuxfoundation2025a2a`]. On **17 August 2026** it became an AAIF hosted project, moving from the Linux Foundation's broader portfolio into the agent-specific fund [`axios2026a2aaaif`, `enterpriseai2026a2aaaif`]. This is governance only: no wire format changed and no specification revision accompanied it.
- **ACP** (IBM Research / BeeAI, March 2025) announced on **29 August 2025** that it was merging into A2A under LF AI & Data; the ACP team wound down independent development [`lfaidata2025acpa2a`].
- **AGNTCY** (Cisco/Outshift with LangChain and Galileo) became a Linux Foundation project on **29 July 2025** [`linuxfoundation2025agntcy`]. Its *Agent Connect Protocol* — confusingly also "ACP" — was archived read-only on **11 April 2026** as the invocation layer converged on A2A, per secondary reporting [`alatirok2026agntcy`].

The net: **two live protocols, one governance body, and no coordination layer in either of them.** That last clause is the paper's opening.

### A.1 MCP — Model Context Protocol

**What it is.** A JSON-RPC 2.0 protocol between an MCP *client* (embedded in an LLM host) and an MCP *server*. Servers offer three primitive kinds — **tools** (model-invocable functions), **resources** (addressable context data), **prompts** (user-selectable templates); clients historically offered **elicitation**, **sampling** and **roots** [`mcp2026spec`].

**Current revision.** `2026-07-28` is the largest change since launch [`mcp2026blog`, `mcp2026spec`, `google2026mcpstateless`]. The `initialize`/`initialized` handshake and `Mcp-Session-Id` are **removed**: every request is self-describing, carrying protocol version, client info and capabilities in `_meta`, so any request can land on any server instance behind a load balancer; capability discovery becomes an optional `server/discover` RPC. Server-initiated requests over a held-open stream are replaced by **Multi Round-Trip Requests (MRTR)**, a poll-shaped `input_required` result type. **Roots, sampling and logging are deprecated** under a new formal policy with a twelve-month minimum window, as is the legacy HTTP+SSE transport; tasks moved into an `io.modelcontextprotocol/tasks` extension.

**Against F1–F5.** *F1:* partial and asymmetric — a client can read a resource, and two agents sharing a server can exchange data the way two processes sharing a database do, but there is no message envelope, no source/tag matching, and no addressing of a peer agent; MCP has no notion that the other side is an agent. *F2:* nothing — no liveness model for peers, and the 2026 revision removes the session, which removes even the weak signal a broken session gave. *F3:* nothing; a server may implement a lock as a tool, but that is an application convention with no atomicity, lease or fencing guarantee. *F4:* actively *reduced* — no join, no rank, no identity beyond OAuth client identity, and now no session. *F5:* nothing — MCP transports content into a context window with no accounting for what that costs, no budget, no back-pressure, and no notion of a receiver's remaining capacity.

**Verdict.** MCP is a **client–server tool-access protocol**: the right answer to "how does my agent call a tool it did not ship with", the wrong shape for "how do sixteen executors agree on a decomposition". Its designers never claimed the latter, and a reviewer will punish any implication that they did. MCP occupies the position of a *device driver interface*, not a *communication library*.

### A.2 A2A — Agent2Agent

**What it is.** A protocol for one agent to delegate a task to another across vendor and organisational boundaries. Version **1.0.0** is current, published **12 March 2026** per secondary reporting, with `spec/a2a.proto` as the single normative definition and three bindings: JSON-RPC 2.0, gRPC, and HTTP+JSON/REST [`a2a2026spec`, `packetnebula2026a2a`]. Some secondary sources date v1.0 to January 2026; the discrepancy is `[UNVERIFIED]`.

**The model.** An **Agent Card** declares identity, skills, bindings, transport URLs and security schemes; v1.0 added **signed Agent Cards**, multi-tenancy, and an extension mechanism [`a2a2026spec`, `linuxfoundation2026a2ayear`]. The operation surface is small: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`, `GetAgentCard`, and three push-notification-config methods. A `Task` carries a `TaskState` from a fixed enum — `SUBMITTED`, `WORKING`, `COMPLETED`, `FAILED`, `CANCELED`, `INPUT_REQUIRED`, `REJECTED`, `AUTH_REQUIRED`, `UNSPECIFIED` — four terminal, two interrupted. Messages are composed of typed `Part`s, `Artifact`s are produced outputs, and streaming delivers status and artifact update events.

**Against F1–F5.** *F1:* yes, for **one-to-one client-to-server task delegation**. There is no group abstraction, so "send this to the other fifteen ranks" is fifteen calls made by application code, and there is no source/tag matching — a task is addressed by `taskId` and the receiver has no `ANY_SOURCE` receive. *F2:* weakly. `TASK_STATE_FAILED` genuinely reports that a delegated task failed, and the paper should credit that; absent are detection of a peer that stops responding without failing a task, notification to *third parties* of a death, and any way to continue a group operation over survivors — there is no heartbeat, lease or failure detector. *F3:* nothing — searching the v1.0 specification for barrier, quorum, consensus, mutual exclusion or multicast returns nothing relevant, and the single "broadcast" concerns delivering events to multiple subscribed streams *of one task* [`a2a2026spec`]. *F4:* partial, and better than MCP — Agent Cards give discovery and identity, task states give a delegated unit of work a lifecycle; there is no executor joining a job, no stable rank, no replacement of a dead participant. *F5:* nothing. The LF's own year-one messaging emphasises that A2A lets agents coordinate "without sharing internal memory" [`linuxfoundation2026a2ayear`] — exactly the property that makes context exhaustion invisible to it.

**Verdict.** A2A is a **discovery-plus-RPC protocol with a task lifecycle**, not a coordination protocol: it standardises the *edge* between two agents, not the *structure* of a group. You can build a coordination layer on A2A — AgentMPI could plausibly bind to it as a transport — but A2A supplies none of the coordination. That framing is accurate, generous, and makes AgentMPI's contribution legible: MPI likewise did not invent TCP; it defined what you say over it.

### A.3 ACP — Agent Communication Protocol (IBM/BeeAI)

Launched March 2025 to power the BeeAI Platform, ACP emphasised structured message types for handoff negotiation, persistent state for long-running tasks, and asynchronous interaction. It merged into A2A on 29 August 2025; BeeAI users migrate via `A2AServer`/`A2AAgent` adapters, and the platform was renamed **Agent Stack**, now built on A2A [`lfaidata2025acpa2a`, `tyk2026protocols`, `beeai2026agentstack`]. Cite ACP as evidence of *convergence*, not as a live competitor. Its one distinctive idea — that long-running stateful work needs first-class protocol support — is the idea AgentMPI takes much further with epochs, leases and revocation. Disambiguate the name on first use or a reviewer will assume an error.

### A.4 AGNTCY, ANP, and other 2025–2026 entrants

**AGNTCY.** Linux Foundation project since July 2025 with Cisco, Dell, Google Cloud, Oracle and Red Hat as formative members [`linuxfoundation2025agntcy`]. Live components: **OASF** (schema), **SLIM** (Secure Low-Latency Interactive Messaging), **DIR** (announce and discovery). Best characterised as **discovery, identity, messaging transport and observability infrastructure** — genuinely the layer beneath the protocols, and genuinely not a coordination layer. SLIM is worth a sentence as a transport AgentMPI could bind to.

**ANP — Agent Network Protocol.** A community project building a layered stack for the "Agentic Web": W3C DID identity (`did:wba`), a meta-protocol negotiation layer, and application protocols for description, discovery and payment. Release **1.1** (mid-2026) added `did:wba`, human-readable WNS handles, and a federated messaging suite covering direct messages, **groups**, end-to-end encryption and cross-domain flows, with DIDs assigned to groups and message services as well as agents [`anp2026site`, `changshan2026anp11`]. It feeds the W3C AI Agent Protocol Community Group [`w3ccg2025aiagent`]. Secondary reporting describes it as a draft with the AgentConnect SDK as reference implementation and no named production adopters as of mid-2026 [`rywalker2026anp`, `zylos2026interop`].

ANP's group messaging is the closest thing in this stratum to a communicator and the paper should say so. But it is a *federated chat group with verifiable membership*: no rank, no ordering guarantee across the group, no collective over members, no failure semantics. It gives you a secure named multicast domain, not `Allreduce`.

**Other entrants.** I found no other 2025–2026 general-purpose inter-agent coordination protocol with meaningful adoption. Claims that some vendor "has a barrier" or "has collectives" should be treated as `[UNVERIFIED]` until a specification is produced.

### A.5 OpenAI Agents SDK, Swarm, and the Claude Agent SDK

**Swarm** was OpenAI's 2024 educational prototype introducing *handoffs* and *routines*. Its production successor, the **OpenAI Agents SDK**, has primitives Agents, Handoffs, Guardrails, Sessions and Tracing, and documents two patterns: **handoffs**, where a triage agent transfers ownership to a specialist (which by default sees the whole history; `input_filter` rewrites what it sees, `is_enabled` hides a handoff at runtime, `nest_handoff_history` reduces context bloat), and **agents-as-tools** via `Agent.as_tool()`, where a manager retains ownership [`openai2026orchestration`, `openai2026handoffs`]. The protocol-shaped part is thin but real, and it is "protocol-in-the-prompt" in its purest form: **a handoff is a tool call the model chooses to make.** The runtime provides the mechanism for transfer but not for requiring it, timing it, or detecting its absence; parallelism is left to the host language.

**Claude Agent SDK / Claude Code.** Single-agent-first, with **subagents** (isolated context, spawned via an `Agent` tool, results summarised back), a `Workflow` tool for orchestrating dozens to hundreds of agents from a script, and an experimental **agent teams** feature in which teammates hold independent context windows, message each other directly, and share a task list [`claude2026subagents`, `claude2026agentteams`]. Agent teams are the closest shipping product to an AgentMPI-like world; the differences are that naming is by string rather than rank, there is no group object and so no isolation of one team's traffic from another's, no collectives, no failure detector, and no context accounting beyond the documentation's note that teams cost more than subagents. Anthropic's cookbook makes the shape explicit — `send_message`, `wait_for_message`, `get_status`, `kill_subagents` over a hub — which is a mailbox, not a communicator [`anthropic2026cookbook`].

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

KQML came out of the DARPA Knowledge Sharing Effort. It separates content, message and communication layers, and its central construct is the **performative**: `ask-one`, `tell`, `achieve`, `subscribe`, `advertise`, `broker-one`. The message layer names the performative and content language; the communication layer names sender, receiver and message identifiers [`finin1994kqml`, `labrou1994kqml`]. Two features matter to AgentMPI. KQML separated *envelope* from *content* and refused to standardise the content language — the same instinct as MPI's typed buffers, and the right one. And it included **facilitator** agents (brokers, matchmakers, recruiters), which are a naming service rather than a communicator: they route by advertised capability, not by membership in a group.

KQML's practical failure was fragmentation into mutually unintelligible dialects, so heterogeneous interoperation was never realised [`singh1998acl`]. Cohen and Levesque criticised it for lacking a formal semantics and for omitting commissives — the performatives by which one agent commits to another — which they argued makes many multi-agent scenarios inexpressible [`cohen1995communicative`, `wooldridge1998verifiable`].

### B.2 FIPA-ACL

FIPA's ACL is superficially similar: an outer message language, roughly twenty performatives (`inform`, `request`, `agree`, `refuse`, `propose`, `cfp`, …), no mandated content language [`fipa2002acl`]. The difference is a **formal semantics** in a Semantic Language (SL), a quantified multi-modal logic with operators for belief, uncertain belief and choice, drawing on Cohen–Levesque and Sadek. Each performative is specified by *feasibility preconditions* and a *rational effect* in these mental attitudes: an agent may sincerely `inform` only if it believes the proposition and does not believe the recipient already holds an opinion on it. FIPA also standardised **interaction protocols** — Request, Contract Net, auctions, brokering, subscribe — as choreographies. That is the closest the classical literature comes to a collective: a template for a conversation, not a callable primitive with completion semantics.

### B.3 The unverifiability critique — get this right, the paper leans on it

**Wooldridge** [`wooldridge2000semantic`, and the earlier `wooldridge1998verifiable`] defines what it means for an agent communication framework to be *verifiable*: conformance must be determinable by an independent observer. He then shows FIPA-ACL is not, for two compounding reasons. **Ungroundedness:** "the FIPA semantics are given in terms of mental states, and since we do not understand how such states can be systematically attributed to programs, we cannot verify that such programs respect the semantics." SL's Kripke possible-worlds semantics "are not connected in any principled way with computational systems" — for an arbitrary program there is no known way of attributing an SL formula characterising it in terms of beliefs and desires. **Undecidability:** SL is a quantified multi-modal logic more expressive than first-order logic and therefore undecidable; even granting a grounding, verification would remain intractable. The consequence is the one the paper needs: *if there is no way of determining whether a system claiming to conform to a standard does conform, the value of the standard itself is in question.* Note the venue — Autonomous Agents and Multi-Agent Systems 3(1):9–31 — since the paper is sometimes miscited.

**Singh** [`singh1998acl`] attacks from another direction: mentalistic semantics presuppose "in essence, that agents can read each other's minds. This supposition has never held for people, and for the same reason, it will not hold for agents." He argues for **social semantics** grounded in public commitments, because communication is inherently public and observable — a real positive research line, not a purely destructive critique.

**Why this matters.** The paper rejects the KQML/FIPA lineage in favour of *standardise the mechanism, leave meaning to the application*. The argument is not that KQML and FIPA were badly engineered — they were carefully engineered — but that they chose the one thing that cannot be conformance-tested. `MPI_Barrier` has a verifiable semantics: no process returns until all have entered. Whether the messages ranks exchange are sincere, informative or true, MPI declines to say, and that refusal is what made it implementable by fifteen vendors. `AMPI_Barrier` inherits the property, and AgentMPI's agent-level operators (semantic reduction, contracts) sit outside the conformance-testable core for the same reason. A reviewer who knows this literature will check whether the paper understands *why* FIPA failed; the answer must be "unverifiability", not "complexity".

### B.4 Contract Net

Smith's Contract Net [`smith1980contractnet`] is the ancestor of every task-claiming scheme in the modern literature: announcement, bid, award, report. Negotiation as a control structure, solving *connection* — matching a task to a node not told in advance what it will do.

**Task claiming via compare-and-swap is Contract Net with the negotiation removed.** The announcement is a task record in shared state, the bid is a CAS attempt, the award is the CAS succeeding. What is gained is atomicity: exactly one claimant, decided by the mechanism rather than a manager's judgement, with no round of messages and no manager to fail. What is lost is selecting the *best* bidder rather than the fastest. Say this plainly and credit Smith, rather than presenting CAS-based claiming as new. FIPA later standardised Contract Net as an interaction protocol.

### B.5 Blackboards and tuple spaces

**HEARSAY-II** [`erman1980hearsay`] is the canonical blackboard system: independent knowledge sources communicate solely by reading and writing a shared, structured, multi-level blackboard, with a scheduler choosing which to activate. No knowledge source calls another. **BB1** [`hayesroth1985blackboard`] added explicit control as a blackboard in its own right — the system reasons about what to do next using the mechanism it uses to reason about the domain. **Linda** [`gelernter1985linda`] generalises this into a coordination language: a tuple space with `out`, `in` (destructive, blocking, associative read), `rd` and `eval`. Linda's template matching is a genuine precursor to tag matching, `in` is a genuine atomic operation — exactly one consumer removes a given tuple, which is the task-claiming primitive above — and it cleanly separates coordination language from computation language, as MPI and AgentMPI do.

**The known problems, which the paper inherits and must document.** *Uncontrolled global mutable state:* any knowledge source may write anywhere, so a change to what one writes silently breaks another that reads it, and the coupling is invisible in the code. *No attribution:* an entry records a value, not who wrote it, when, or at what epoch. *No concurrency discipline:* the classical designs assume a scheduler serialising activations, so once activations are genuinely concurrent there is no memory model — no atomicity for read-modify-write, no ordering, no way to say "this update is conditional on what I read". *Scheduling sits outside the model:* the control problem dominated the research and was never solved generally.

**AgentMPI's window is a blackboard with MPI-3 RMA discipline imposed on it,** and should be framed that way: the expressive power of shared state plus the four things blackboards lack — named windows rather than one global surface (scoping); `Put`/`Get`/`Accumulate`/`CAS` as the only access path (a memory model); `Win_fence` and leased locks (synchronisation with defined completion); a journal recording writer, epoch and time (attribution). The claim is not that shared state is new — Hearsay-II is 1980, Linda 1985 — but that shared state *with an enforced access discipline and a failure model* is what the agent world lacks.

---

## C. Multi-agent LLM frameworks

### C.1 The comparison table

The "framework or prompt?" column is the paper's most important and most contestable, so entries are conservative: **framework** means the runtime enforces it without the model's cooperation.

| System | Coordination model | Communicator-like namespace? | Collectives? | Failure handling beyond retry? | Context accounting? | Coordination in framework or prompt? |
|---|---|---|---|---|---|---|
| **AutoGen / AG2** [`wu2023autogen`] | Conversable agents; `GroupChat` with a manager selecting the next speaker | shared conversation, not a namespace; no ranks | no | no; in-memory conversation history by default, durable state needs external integration [`algorithmine2026orchestration`] | token counting available; no cross-agent budget | **prompt** — speaker selection and termination are model decisions |
| **LangGraph** ≥1.2 [`langgraph2026faulttolerance`] | Explicit `StateGraph`; execution proceeds in **supersteps** with checkpointing after each | static, framework-owned topology; no rank namespace | no (fan-out/fan-in only) | **yes, substantially**: `RetryPolicy`, `TimeoutPolicy` (run vs idle), post-retry error handlers routing via `Command` (saga compensation), checkpointed failure provenance, cooperative drain at superstep boundary | trimming/summarisation utilities; no protocol-level budget | **framework** — the exception, and the paper must say so |
| **CrewAI** [`crewai2026processes`, `crewai2026hierarchical`] | `Process.sequential` or `Process.hierarchical` (manager delegates and validates); **Flows** (`@start`/`@listen`/`@router`) add event-driven orchestration with typed persistable state | membership by role name; no isolation | no | task-level retries; Flow resume from last step | no | **both** — Flows are framework, Crews are prompt |
| **MetaGPT** [`hong2024metagpt`] | SOP-encoded assembly line of role agents with structured artefacts | shared message pool with role-based subscription | no | no | no | **prompt**, structured by SOP templates |
| **ChatDev** [`qian2024chatdev`] | Waterfall phases; each a two-agent chat chain with role inversion | no | no | no | no | **prompt** |
| **CAMEL** [`li2023camel`] | Role-playing dyad driven by inception prompting | no | no | no | no | **prompt** |
| **OpenAI Agents SDK** [`openai2026orchestration`, `openai2026handoffs`] | Handoffs (ownership transfer); agents-as-tools (manager retains ownership) | no | no | guardrails; retries; April 2026 durable execution by snapshot-and-rehydrate per secondary reporting `[UNVERIFIED]` | `input_filter`, `nest_handoff_history`; no accounting | **prompt** — a handoff *is* a tool call the model chooses |
| **Microsoft Agent Framework** 1.0 [`msagentframework2026overview`, `msagentframework2026durable`] | Graph `WorkflowBuilder` with typed routing, fan-out/fan-in, shared state, sub-workflows; plus sequential/concurrent/handoff/group-chat/Magentic patterns | framework-owned graph; no rank namespace | fan-out/fan-in only | **yes**: Durable Task extension checkpoints each step, recovers across distributed workers, completed steps not re-executed; idle-session TTL | session state; TTL cleanup; no token budget | **framework** for workflows, **prompt** for group-chat/Magentic |
| **Semantic Kernel agents** | superseded by Microsoft Agent Framework; maintained with fixes for ≥1 year post-GA [`langchain2026frameworks`] | — | no | — | — | — |
| **OpenHands / OpenDevin** [`wang2025openhands`] | Single agent in a sandboxed event-stream loop; delegation via shared workspace | no | no | no | no | **prompt** |
| **Devin (Cognition)** [`cognition2026working`] | Manager Devin decomposes, spawns child Devins, coordinates through an internal MCP; "map-reduce-and-manage" | ad hoc agent team | no | no | context engineering; no protocol accounting | **prompt** |
| **Claude agent teams** [`claude2026agentteams`] | Team lead plus teammates with independent contexts, direct messaging, shared task list | named teams | no | no | documented cost difference only | **prompt** |
| **Ray** (as substrate) [`moritz2018ray`] | tasks + actors + object store; `ray.util.collective` adds real collectives | actor handles; collective **groups** with world size and rank | **yes** — `allreduce`, `broadcast`, `reduce`, `allgather`, `reduce_scatter`, `send`/`recv`, `barrier` [`ray2026collective`] | actor restart, lineage re-execution | no | **framework** — but for tensors, not agents (§D.4) |

Two entries deserve emphasis because they are what a hostile reviewer will use. **LangGraph and Microsoft Agent Framework do have real failure handling.** LangGraph 1.2 (May 2026) ships per-node retries, run-versus-idle timeouts, post-retry error handlers that route to compensation flows, checkpointed failure provenance surviving a process crash, and cooperative drain at a superstep boundary [`langgraph2026faulttolerance`]; the Durable Task extension checkpoints every step, recovers automatically, and does not re-execute completed agent calls [`msagentframework2026durable`]. A table that marks these "no" is wrong.

**But what they provide is durability, not fault tolerance in the MPI sense** [`bland2013ulfm`]. Durable execution answers *the process died; resume the workflow from the last checkpoint*, assuming the orchestrator is the sole locus of control and replay is the right response. It does not answer *rank 7 died while sixteen ranks were inside a barrier; the other fifteen are blocked; what do they observe, when, and what may they do next?* There is no revoke, no shrink, no agreement over the surviving set, no defined state for an in-flight collective, no notification to peers. Checkpoint-and-resume is a supervisor-level answer; revoke/shrink/agree is a participant-level one, and only the latter lets a live population progress without stopping.

### C.2 Protocol-in-the-prompt

The paper claims coordination logic expressed in a prompt is unreliable because a rank that forgets to enter a barrier prevents the population from progressing. The mechanism is visible in the frameworks: in the OpenAI Agents SDK a handoff is a tool the model may call [`openai2026handoffs`]; in AG2 the group-chat manager selects the next speaker by asking a model; in CrewAI's hierarchical process the manager agent decides allocation [`crewai2026hierarchical`]. In each the *mechanism* is in the runtime and the *decision to invoke it* is in the model. That is fine for a genuine judgement (which specialist suits this question) and dangerous for a protocol obligation (every participant must reach this point before any proceeds), because a bad judgement degrades quality locally while an unmet obligation blocks everyone.

Cognition's account of manager/child Devin hierarchies is unusually candid [`cognition2026working`]: "Cross-agent communication, a sub-agent writing messages back to its manager to be passed to other agents in the agent team, doesn't happen by default, because models haven't been trained in environments where it needed to. Each of these took dedicated work to fix, and we're still improving on all of them." That is a first-hand report of a coordination obligation left to the model and not discharged; the same post concludes that "the open problems are all communication problems."

### C.3 MAST — "Why Do Multi-Agent LLM Systems Fail?"

**Provenance.** Cemri et al., UC Berkeley and collaborators; arXiv 2503.13657 (March 2025); published in the **NeurIPS 2025 Datasets and Benchmarks Track** [`cemri2025mast`]. Use the NeurIPS version's numbers; third-party summaries circulate slightly different ones.

**Method.** The taxonomy was built by Grounded Theory over an initial **150 traces** from five frameworks (HyperAgent, AppWorld, AG2, ChatDev, MetaGPT) by six expert annotators, at inter-annotator agreement **κ = 0.88**, then applied at scale by an o1-based LLM annotator calibrated to **κ = 0.77** against human labels (**κ = 0.79** on two unseen systems). **MAST-Data** is **1642 annotated execution traces** from **7 frameworks** (adding OpenManus and Magentic) across coding, math and general-agent tasks, on GPT-4-series and Claude-3-series models.

**The taxonomy: 3 categories, 14 failure modes.** Category prevalences over the 1642 traces are **41.8% / 36.9% / 21.3%**; per-mode prevalences as reported:

| Category | Mode | Name | Prevalence |
|---|---|---|---|
| FC1 — Specification and system design (41.8%) | FM-1.1 | Disobey task specification | 11.8% |
| | FM-1.2 | Disobey role specification | 1.5% |
| | FM-1.3 | Step repetition | 15.7% |
| | FM-1.4 | Loss of conversation history (unexpected truncation, reverting to an antecedent state) | 2.80% |
| | FM-1.5 | Unaware of termination conditions | 12.4% |
| FC2 — Inter-agent misalignment (36.9%) | FM-2.1 | Conversation reset | 2.20% |
| | FM-2.2 | Fail to ask for clarification | 6.80% |
| | FM-2.3 | Task derailment | 7.40% |
| | FM-2.4 | Information withholding | 0.85% |
| | FM-2.5 | Ignored other agent's input | 1.90% |
| | FM-2.6 | Reasoning–action mismatch | 13.2% |
| FC3 — Task verification (21.3%) | FM-3.1 | Premature termination | 6.20% |
| | FM-3.2 | No or incomplete verification | 8.20% |
| | FM-3.3 | Incorrect verification | 9.10% |

**Findings to use.** The absence of a dominant category is itself a finding — the authors read it as balanced coverage rather than bias from a particular design, and note that individual systems have distinct profiles (ChatDev's star topology and lack of a predefined workflow correlate with premature terminations). Tactical interventions help but do not solve: case studies applying improved prompts and topology changes yielded **+9.4%** task success for AG2/MathChat and **+15.6%** for ChatDev on ProgramDev, leaving absolute completion rates low. Their conclusion is explicit — MAS failures "require more than superficial fixes… pointing towards the need for more complex solutions and fundamental MAS redesigns" — and among their structural strategies is **"establishing a standardized communication protocol"**, on the grounds that LLM agents communicate mainly in unstructured natural language. That is the most useful sentence in the paper for AgentMPI and should be quoted.

**Cross-referencing AgentMPI's failure modes against MAST — my analysis, stated carefully.** The mapping is partial and must not be overclaimed: MAST classifies *observed behaviours in traces*, not *mechanisms absent from a runtime*.

| AgentMPI mode | MAST modes plausibly implicated | Caveat |
|---|---|---|
| F1 information sharing | FM-2.4 (0.85%), FM-2.5 (1.90%), FM-1.4 (2.80%) | Individually small; do *not* claim MAST shows information sharing dominates |
| F2 executor death | none directly | The corpus is framework runs, not crash-injected; a crashed executor mostly does not appear as a labelled mode. Evidence about the corpus, not the world |
| F3 synchronisation / mutual exclusion | FM-1.3 step repetition (15.7%) is *consistent with* duplicated work absent mutual exclusion | Causal attribution is not established; say consistent-with, not caused-by |
| F4 lifecycle | FM-1.5 (12.4%), FM-3.1 (6.20%), FM-2.1 (2.20%) | Strongest mapping: ~21% of failures involve not knowing when the interaction should stop |
| F5 context exhaustion | FM-1.4 (2.80%) | MAST measures the symptom; §E is the better evidence |

Defensible summary: *MAST's two largest modes are step repetition (15.7%) and reasoning–action mismatch (13.2%), and its largest structural cluster is termination and verification; roughly a fifth of observed failures concern not knowing when to stop — a lifecycle property no current framework makes mechanically checkable.*

### C.4 Does multi-agent even help? The 2025–2026 evidence

Engage this honestly: the strongest form of the objection in §F.2 is built from it.

**Against.** **Tran & Kiela** [`tran2026singleagent`] (Stanford, April 2026) argue from the **Data Processing Inequality** that under a fixed reasoning-token budget with perfect context utilisation, a single agent is more information-efficient: every inter-agent message is a lossy transformation of the sender's context and cannot increase mutual information with the answer. Empirically, across Qwen3, DeepSeek-R1-Distill-Llama and Gemini 2.5, single-agent systems match or beat multi-agent variants at equal reasoning tokens; they also show Gemini 2.5 under-spending its declared `thinking_budget` in single-agent mode, silently advantaging MAS in naive comparisons. Their theory predicts MAS becomes competitive precisely when single-agent context utilisation degrades. **Google's "Towards a Science of Scaling Agent Systems"** [`google2025scaling`] evaluates 260 configurations over six benchmarks, five architectures and three model families with standardised tools, prompts and compute. Relative change versus single-agent ranges from **+80.8%** (decomposable financial reasoning) to **−70.0%** (PlanCraft sequential planning, where *every* multi-agent variant degraded performance, by 39–70%), with a **capability-saturation effect**: past roughly 45% single-agent success, coordination returns diminish rapidly or go negative. Secondary reporting adds super-linear communication overhead (exponent ≈1.724) and effective team sizes of three to four [`venturebeat2026moreagents`]. **Cognition's "Don't Build Multi-Agents"** [`cognition2025dontbuild`] gives the practitioner version: share full context and full agent traces, not individual messages, because *actions carry implicit decisions* and parallel writers make conflicting implicit choices about style, edge cases and patterns.

**For, and the reconciliation.** **Anthropic's multi-agent research system** [`anthropic2025multiagent`] reports an orchestrator-worker architecture (Opus 4 lead, 3–5 Sonnet 4 subagents each with its own context window) outperforming single-agent Opus 4 by **90.2%** on an internal research eval, at roughly **15× the tokens of a chat interaction** (agents generally use ~4×); they state plainly that domains requiring shared context or with many inter-agent dependencies are not a good fit today. **Cognition's follow-up** [`cognition2026working`] revises without retracting: parallel-*writer* swarms still do not work, but a narrower class does — *multiple agents contributing intelligence while writes stay single-threaded*. A clean-context review agent sharing **no** context with the coder catches ~2 bugs per Devin-written PR, ~58% of them severe, and works better *because* of the shorter context (they cite context rot explicitly); unstructured swarms — "arbitrary networks of agents negotiating with each other" — are "mostly a distraction".

**The synthesis to adopt.** The deciding variable is task coupling and compute accounting, not architecture: multi-agent pays when subtasks are loosely coupled, information exceeds one context window, and task value clears the token premium, and loses when work is tightly coupled and sequential. The paper should not argue that more agents are better, only that where multiple executors are used — now routine, and capable of +80% on decomposable work — the coordination is ad hoc, unverifiable and prompt-resident, and that a protocol making coordination cheap and checkable *also* makes it measurable when coordination is not worth it. Do not get caught claiming multi-agent superiority as a premise.

---

## D. Distributed computing models to position against

### D.1 BSP, Pregel, Giraph

**BSP** [`valiant1990bsp`] structures computation as supersteps — local computation, communication, barrier — with a cost model parameterised by processor count, synchronisation periodicity `L` and throughput ratio `g`. Its contribution is that barrier-separated structure makes performance predictable and state tractable: after a barrier, every process's view of the previous superstep is complete. **Pregel** [`malewicz2010pregel`] applies this to graphs — vertex-centric computation, messages along edges, superstep barriers, vote-to-halt termination, fault tolerance by superstep checkpointing and partition re-execution; Giraph is the open-source implementation.

`AMPI_Win_fence` is a BSP superstep boundary in the agent setting, with the same guarantee. Note that **LangGraph already calls its steps "supersteps"** and checkpoints at their boundaries [`langgraph2026faulttolerance`] — the agent world has independently rediscovered BSP, which is a point in AgentMPI's favour, since the next question is where the collectives are. The honest difference: BSP assumes uniform, cheap, predictable local computation, whereas an agent superstep costs seconds to minutes, has heavy-tailed latency, and may fail. That is why AgentMPI's fences must be deadline-bounded and its barriers must name absentees — a departure from the BSP literature rather than an application of it.

### D.2 The actor model

**Hewitt, Bishop and Steiger** [`hewitt1973actor`] define actors as universal primitives communicating solely by asynchronous message passing, each with a mailbox and private state; **Agha** [`agha1986actors`] gives the standard formal treatment. **Erlang/OTP** [`armstrong2003erlang`] contributes the parts that matter here: process isolation with no shared memory, `link`/`monitor` for failure *notification*, supervision trees with restart strategies and **maximum restart intensity**, and "let it crash". **Akka** carries this to the JVM with cluster membership, sharding and persistence.

**Why the absence of collectives matters.** The actor model gives you `send` and failure notification but neither collectives nor a group abstraction, so anything collective is built by hand each time. Hand-built collectives have three recurring problems: they are re-implemented incompatibly in every system, so no harness is portable across them; naive implementations are linear in the participant count where standard algorithms are logarithmic — which matters far more when each message costs tokens in a receiver's context than bytes on a wire; and their failure behaviour is unspecified, so "what does an in-flight reduction do when a participant dies" has a different answer in every codebase. MPI answered the first two with the collective catalogue and algorithm selection, the third only with ULFM; AgentMPI's claim is that agents need all three at once. State the debt to this lineage plainly: leases and heartbeats are `monitor`, supervision with `MAX_RESTARTS_PER_RANK` is OTP's max restart intensity, "let it crash" is the failure model.

### D.3 MapReduce and Spark

**MapReduce** [`dean2004mapreduce`] fixes one communication pattern — map, shuffle by key, reduce — and in exchange gives transparent fault tolerance by re-executing deterministic tasks plus speculative execution for stragglers. **Spark** [`zaharia2012spark`] generalises to a DAG over RDDs with lineage-based recovery.

The relevance is cautionary. Both obtain fault tolerance from a property agent executors lack: **deterministic, idempotent, re-executable tasks**. Re-running a map task yields the same output; re-running an agent turn does not, and may not be side-effect-free. This is why AgentMPI cannot adopt lineage re-execution and must journal, epoch and fence instead — a decisive answer to "why not just re-execute like Spark". A fixed communication pattern is also a real design option agent frameworks have effectively taken: Cognition's "map-reduce-and-manage" [`cognition2026working`] is MapReduce with an LLM in each slot. The argument against fixing the pattern is the one that motivated MPI over specialised libraries — a *general* interface is what lets a harness be written once.

### D.4 Dataflow and workflow systems, and Ray in particular

**Dryad** [`isard2007dryad`] executes a DAG of sequential programs connected by channels, with runtime graph refinement. **Airflow** [`airflow2015`] schedules DAGs with retries and backfills — a batch scheduler, not a communication library. **Dask** [`rocklin2015dask`] provides dynamic task graphs and blocked algorithms.

**Ray** [`moritz2018ray`] needs a full paragraph, being the closest thing to infrastructure people actually build agent systems on.

*What Ray gives you.* Stateless **tasks** and stateful **actors** as remote-callable units with actor handles for addressing; a distributed **object store** with zero-copy shared memory on a node; futures and `ray.get`/`ray.wait`; automatic **lineage-based re-execution** for tasks and configurable **actor restart**; placement groups for gang scheduling; autoscaling; and in `ray.util.collective` a genuine collective library — `send`, `recv`, `broadcast`, `allreduce`, `reduce`, `allgather`, `reduce_scatter` and `barrier` over GLOO (CPU) and NCCL (GPU), with collective **groups** initialised with a world size and per-actor rank [`ray2026collective`]. `gather`, `scatter` and all-to-all are documented as unsupported in both backends.

*What Ray does not give you.* **Its collectives are over tensors, not agents**: they operate on NumPy/PyTorch/CuPy buffers with numeric `ReduceOp`s, so there is no reduction whose operator is a model call and no way to express "reduce sixteen design proposals into one" — "collectives: yes" is correct for Ray, but the table must carry that qualifier. **A collective group is not a communicator**: no context/tag isolation (a library's internal traffic cannot be made invisible to application receives), no `Comm_split`/`Comm_dup`, no topology, no group-scoped failure semantics. **No agent-specific failure semantics**: actor restart and lineage re-execution are durability plus supervision, with no revoke, no shrink, no agreement over survivors, and no defined outcome for a collective interrupted by a death (its collectives inherit NCCL/GLOO semantics, where a failed participant typically hangs or aborts the group). **No context accounting**: Ray meters CPU, GPU, memory and custom resources, but tokens are not a resource it knows about, so there is no ledger, no eager/rendezvous threshold, no back-pressure on a receiver's remaining window. **Not a portable interface**: a harness written against Ray runs on Ray.

The honest summary: **AgentMPI could be implemented on Ray**, and saying so is a strength, since MPI implementations run on many transports. Ray is a substrate, not an interface, and the abstractions an agent harness needs are not among those Ray defines. Ray is PVM or a vendor library; what was missing in 1992 was the *standard*.

### D.5 Parameter servers and stale-synchronous parallel

**Li et al.** [`li2014parameterserver`] describe a parameter server: a sharded key-value store of parameters with push/pull, user-defined filters, and configurable consistency (sequential, eventual, bounded delay). **Ho et al.** [`ho2013ssp`] give **stale synchronous parallel**: workers proceed at their own pace but none may run more than `s` clocks ahead of the slowest, bounding staleness while eliminating the straggler cost of a hard barrier, with convergence guarantees degrading gracefully in `s`.

**This is the lineage of AgentMPI's quorum collectives, and the paper should say so rather than claim novelty.** A quorum barrier releasing when a fraction `q` of ranks have arrived makes the same trade: bounded inconsistency purchased for immunity to the slowest participant. Two differences matter — the unit (SSP bounds staleness in *clocks*, quorum collectives bound *participation*) and the guarantee (SSP has a convergence proof for SGD, an agent reduction has no analogous theory, so claim engineering lineage, not inherited guarantees). The parameter server's *tunable* consistency is likewise the direct ancestor of a window with a settable consistency mode.

### D.6 Software transactional memory and optimistic concurrency

**Herlihy and Moss** [`herlihy1993tm`] proposed hardware transactional memory; **Shavit and Touitou** [`shavit1995stm`] the software version. **Kung and Robinson** [`kung1981occ`] set out optimistic concurrency control — execute against a read set, validate at commit, restart on conflict — and **Herlihy and Wing** [`herlihy1990linearizability`] supply linearizability, which makes "atomic" mean something checkable.

Compare-and-swap on a window key is optimistic concurrency at single-key granularity. What it does *not* give is multi-key atomicity — an agent that must claim two related tasks together cannot — which is the gap STM fills and which the paper should acknowledge as a deliberate omission with a cost. The STM literature's lessons about aborting long transactions apply with unusual force here, because aborting an agent transaction discards minutes of model work rather than microseconds of CPU. That asymmetry argues for leased locks alongside CAS.

---

## E. Context management and LLM-specific systems work

### E.1 Why more context is not simply better

**Lost in the middle** [`liu2024lostmiddle`] (TACL 2024) is canonical: performance on multi-document QA and key-value retrieval is highest when relevant information sits at the beginning or end of the input and degrades substantially in the middle, producing a U-shaped curve, with degradation as context grows *even for models designed for long contexts*.

**Context rot** [`hong2025contextrot`] (Chroma, July 2025) evaluates 18 frontier models across five experiments holding task complexity constant and varying only input length. Every model degraded. The findings that matter for protocol design: degradation is non-uniform; performance falls faster when needle–question semantic similarity is lower; distractors compound (one hurts, four hurt more); and, counter-intuitively, logically coherent haystacks performed *worse* than randomly shuffled ones across all 18 models. Their LongMemEval experiment shows a large gap between a 300-token focused input and a 113K-token full input, both well inside declared window sizes.

**Long-horizon degradation.** A 2026 study of deep-search agents identifies **premature termination** — models giving up or answering with low confidence long before exhausting the window — at a rate rising with context length after controlling for query difficulty [`gair2026contextrot`]. A separate 2026 result finds frontier monitors miss dangerous coding-agent actions 2× to 30× more often when the action follows 800K tokens of benign activity than in isolation [`classifier2026contextrot`]. Both are the same phenomenon: capability is a function of context occupancy, not just window size.

The synthesis: **the effective context window is smaller than the declared one, by a task-dependent and unpredictable margin** — precisely the condition under which a resource needs *accounting and flow control* rather than a static limit, the argument that produced eager/rendezvous thresholds and unexpected-message buffer limits in MPI, transposed from bytes to tokens.

### E.2 Mechanisms: caching, compression, retrieval

**PagedAttention / vLLM** [`kwon2023vllm`] is the systems-side reference point: KV cache memory managed in non-contiguous blocks under an OS-style paging scheme, near-eliminating fragmentation and enabling sharing across requests for 2–4× throughput. Cite it as the demonstration that *treating context as a managed resource with an explicit allocator is a systems problem with systems answers*, while noting it manages one server's memory, not a population's attention.

**Compression and compaction.** LangChain's write/select/compress/isolate framing [`langchain2025contextengineering`] is useful vocabulary and cites Cognition's practice of summarising at agent–agent boundaries with a fine-tuned model; the 2025 context-engineering survey [`mei2025contextsurvey`] organises the space over 1400+ papers. A 2026 result names a specific hazard, the **compaction cliff**: under a production "compress while keeping safety rules" prompt, safety-rule recall holds at 53% after one round and falls to 10% by round five, motivating type-dependent retention [`compactioncliff2026`]. Secondary reporting of the ACON line describes 26–54% peak-token reductions retaining 95%+ task accuracy via failure-driven optimisation of compression prompts [`zylos2026compression`].

**Retrieval augmentation as context management** [`lewis2020rag`] is the original move of not putting everything in the window. Its AgentMPI analogue is the rendezvous protocol — send a handle, materialise on request — and drawing that parallel makes the eager/rendezvous threshold legible to an ML audience.

### E.3 Token budgeting and cost-aware orchestration

A small but real literature addresses spending a token or money budget across an agent pipeline. **ZEBRA** [`zebra2026`] reduces multi-phase allocation to a continuous nonlinear knapsack problem — an LLM controller estimates per-phase utility curves, water-filling over the Lagrange multiplier returns the split — recovering 94.4% of unconstrained quality at 0.5× the unconstrained spend on a 150-task APPS benchmark, versus 88.1% for letting an LLM allocate directly. **Budget-aware agentic routing** [`bopo2026`] treats per-step cheap-versus-expensive model selection as a sequential, path-dependent problem under strict per-task limits. **Constraint-driven online allocation** [`mcpp2026`] formulates execution under joint budget *and* deadline constraints as a finite-horizon stochastic online allocation problem solved by Monte Carlo portfolio planning.

**The gap AgentMPI addresses.** Each of these is a *policy* for spending a budget, assuming an orchestrator with global knowledge that decides allocations. None is a *mechanism* by which a receiver declines a message it cannot afford, a sender learns a peer is near exhaustion, or exhaustion is reported as a distinguishable failure kind rather than a silent quality collapse. The two are complementary: allocation decides how much each participant may spend, flow control prevents one participant from spending another's budget without permission. That distinction is the cleanest statement of why context flow control belongs in a protocol and not in an orchestrator.

---

## F. Synthesis — my analysis

**Everything in this section is my assessment, not a report of a source.**

### F.1 The master table

Marks: **yes** = a first-class, documented mechanism; **partial** = something adjacent exists, qualified in the cell; **no** = absent. Where a competitor does provide something it is marked, because a reviewer will check.

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
| **LangGraph** ≥1.2 | no | no | no | partial (checkpointed graph state; no atomics) | partial (node timeouts, incl. idle) | partial (error handlers, saga compensation, cooperative drain — no peer notification) | partial (trimming/summarisation utilities) | **yes** (LangSmith) | no |
| **CrewAI** | no | no | no | no | no | partial (Flow resume) | no | partial | no |
| **MetaGPT / ChatDev / CAMEL** | no | partial (MetaGPT role-subscribed message pool) | no | no | no | no | no | no | no |
| **OpenAI Agents SDK** | no | no | no | no | no | partial (guardrails, retries; durable execution `[UNVERIFIED]`) | partial (handoff input filters) | **yes** | no |
| **Microsoft Agent Framework** 1.0 | no | no | partial (typed fan-out/fan-in) | partial (workflow shared state) | partial (durable step failure) | partial (checkpoint/recover; no peer notification) | partial (session TTL) | **yes** (OpenTelemetry) | partial (Python + .NET, one vendor) |
| **Claude agent teams** | partial (named teams) | partial (address a teammate by name) | no | partial (shared task list) | no | no | no | **yes** | no |
| **Ray** + `ray.util.collective` | partial (collective groups w/ ranks; no context isolation) | **yes** (`send`/`recv` between ranks) | **yes** (tensors only; no `gather`/`scatter`/all-to-all) | **yes** (object store; actor state; no cross-object atomics) | partial (actor death detection, restart) | partial (lineage re-execution, actor restart; no revoke/shrink/agree) | no (no token resource) | **yes** (dashboard, timeline) | no (an implementation, not an interface) |
| **BSP / Pregel** | no | no (edge-directed) | **yes** (barrier; aggregators) | partial (aggregators) | partial (superstep failure detection) | partial (checkpoint + partition re-execution) | no | no | model / one implementation |
| **Actors (Erlang/Akka)** | partial (process groups, `pg`) | partial (selective receive by pattern) | no | no (deliberately) | **yes** (`monitor`, exit signals) | **yes** (supervision trees, restart intensity) | no | partial | model, multiple runtimes |
| **Parameter server / SSP** | no | no | **yes** (push/pull aggregation) | **yes** (KV store, tunable consistency) | partial | partial (replica-based) | partial (bounded staleness is flow control of a kind) | no | no |
| **MPI-4 + ULFM** [`mpi2021standard`, `bland2013ulfm`] | **yes** | **yes** | **yes** | **yes** (RMA, `Compare_and_swap`) | **yes** (via ULFM) | **yes** (revoke/shrink/agree) | partial (eager/rendezvous, in bytes) | partial (PMPI, MPI_T) | **yes** |
| **AgentMPI (proposed)** | **yes** | **yes** | **yes** (incl. semantic operators, quorum variants) | **yes** (windows, CAS, leased locks) | **yes** (leases, two-phase suspicion) | **yes** (revoke/shrink/agree, respawn + briefing) | **yes** (token ledger, eager/rendezvous in tokens) | **yes** (mandatory trace) | **yes** (the claim under test) |

Reading the table: the only rows with a majority of "yes" are MPI and the models the paper draws on. Among live agent systems the fullest row is **Ray**, whose yeses are for tensors and processes rather than agents and contexts. Among agent-native systems the fullest rows are **LangGraph** and **Microsoft Agent Framework**, whose yeses are durability and observability rather than group communication. The two columns empty across the entire agent stratum are **collectives** and **context accounting** — a tidy statement of the paper's contribution, and one that survives checking.

### F.2 The strongest objection to the AgentMPI thesis

Stated at full strength, as a reviewer would put it. I think this is the objection that would sink the paper if unanswered, and it has three interlocking parts.

**Part one: MPI's model presupposes conditions agent systems do not satisfy.** MPI assumes SPMD execution over a static, homogeneous rank set; a reliable, low-latency, low-cost interconnect; deterministic operations, so that an operation's meaning does not depend on which rank performs it; and failure rare enough that ULFM took two decades to be treated as urgent. Agent systems satisfy none of these. Executors are heterogeneous in model, tools and price; the population is dynamic by design, because you spawn a subagent when you discover you need one; the same prompt to the same rank yields different output; and "communication" is not a wire transfer but an injection into a scarce, degrading, non-associative resource. Under these conditions the central MPI abstractions may be **category errors**: a rank is meaningful only if ranks are interchangeable; a collective only if its operator is associative and commutative, which no model-mediated reduction can be; a barrier is affordable only if waiting costs are bounded, which for a heavy-tailed agent turn they are not. AgentMPI's own design betrays the strain — deadline-bounded blocking, idempotent retry, quorum collectives, two-phase failure suspicion, and a distinction between reproducible and irreproducible reductions are five departures from MPI, suggesting borrowed abstractions being bent to fit rather than fitting.

**Part two: agent coordination is dominated by semantics, not mechanism, so a semantics-thin protocol cannot help.** MAST is the ammunition. What actually fails is disobeying task specification (11.8%), step repetition (15.7%), reasoning–action mismatch (13.2%), incorrect verification (9.1%) — not one a mechanism failure. No barrier prevents an agent from repeating a step; no communicator prevents it from ignoring its role; no atomic CAS makes its verification correct. These are failures of *understanding what the other agent meant and what the task requires*, precisely the territory KQML and FIPA tried to standardise and precisely the territory AgentMPI declines to enter, so the protocol standardises the part that was not broken. Worse, Tran and Kiela's Data Processing Inequality argument implies the mechanism can only *lose* information: a better-engineered channel makes the loss cheaper and more reliable, not smaller. And the Google scaling study puts a ceiling on the enterprise — past ~45% single-agent success, coordination returns go negative, so the population AgentMPI coordinates is one you should not have built.

**Part three: the historical analogy is doing work it cannot bear.** That multi-agent systems are where parallel computing was in 1991 assumes both fields face the same *kind* of impasse. In 1991 the mechanisms were understood — send, receive, barrier and reduce were well defined and implemented many times — and what was missing was agreement on names and semantics, which is why MPI-1 could be specified in fourteen months and implemented by everyone within two years. In 2026 the mechanisms are not understood: nobody knows the right operator for a semantic reduction, whether a barrier is the right synchronisation for agents at all, or how to specify a context budget stable across model versions. Standardising now freezes a design space still in motion, and the standard would be obsolete within a model generation. The MPI Forum succeeded because it standardised *late*, over a decade of practice with PVM, NX, Express and Chameleon. AgentMPI standardises early, and the right analogy may be not MPI-1 in 1994 but **HPF in 1993**: a carefully designed, broadly endorsed standard for a problem the community did not yet understand well enough to standardise, which was implemented, benchmarked, and abandoned.

**What the paper must do with this.** Part one is answerable: the five departures are documented as departures, each justified by measurement, and MPI itself is not SPMD-only — MPMD and dynamic process management are in the standard. Part two is answerable only by conceding the premise and narrowing the claim: AgentMPI does not fix semantic failures and should not claim to; its claim is that mechanism failures are currently *indistinguishable* from semantic ones because there is no mechanism layer to rule out, and that MAST's own call for "a standardized communication protocol" is a request for exactly this. Part three cannot be answered by argument, only by evidence: a conformance suite that more than one implementation passes, and a harness that runs unmodified across more than one host. Absent that, the HPF comparison stands.

---

## Appendix: open items and unverified claims

- **A2A v1.0 release date** (12 March 2026), **AGNTCY `acp-spec` archival** (11 April 2026), **OpenAI Agents SDK April 2026 additions** (native sandbox execution, durable execution by snapshot-and-rehydrate, subagents), and **ANP tracing support**: all rest on secondary reporting or are unestablished. `[UNVERIFIED]` — check the respective release notes and changelogs before camera-ready.
- **MAST percentages** are from the NeurIPS 2025 camera-ready; earlier arXiv versions and third-party summaries circulate slightly different figures.
- **Author lists** for several 2026 arXiv preprints (`google2025scaling`, `gair2026contextrot`, `classifier2026contextrot`, `compactioncliff2026`, `zebra2026`, `bopo2026`, `mcpp2026`, `mei2025contextsurvey`) were not verified; the BibTeX entries carry notes to that effect.
- **The claim that no 2025–2026 inter-agent protocol provides a barrier or collective** is a negative result from reading the MCP `2026-07-28` and A2A v1.0 specifications and keyword-searching them: strong but not exhaustive across extensions. Re-check the A2A extension registry before camera-ready.
- **Anthropic's 90.2% figure** is from an internal, unpublished evaluation. Cite as a vendor-reported number, never as a benchmark result.
