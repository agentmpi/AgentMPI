# 04 — The Multi-Agent LLM Landscape: Frameworks, Protocols, and Failure Modes

**Purpose.** Related-work substrate for *AgentMPI: A Message Passing Interface for Multi-Agent Harness Development*. This memo establishes (a) what exists, (b) what is empirically known to break, and (c) precisely which abstraction is missing.

**Date of survey.** August 2026. All version numbers, spec revisions, and leaderboard figures are as of that date and will rot.

**Epistemic conventions.** `[key]` markers resolve to the reference list at the end. Claims I verified by fetching the primary artifact (arXiv abs/PDF/HTML, ACL Anthology, vendor spec page) are unmarked. Claims resting only on a search snippet, a secondary summary, or a third-party blog are marked `[UNVERIFIED]`. **I have not invented any arXiv identifier.** Where an identifier came from a secondary source and I could not retrieve the primary record, I say so explicitly.

---

## 0. Framing: harness frameworks vs. protocols vs. runtimes

Three categories get conflated in the literature, and the paper should separate them in its first paragraph:

1. **Harness frameworks** — libraries you write your agent system *in*. They own the control loop. AutoGen/AG2, LangGraph, CrewAI, MetaGPT, CAMEL, OpenAI Agents SDK, Claude Agent SDK, Google ADK, Microsoft Agent Framework, LlamaIndex Workflows, Pydantic AI, Mastra, smolagents, Agno.
2. **Interoperability protocols** — wire formats that let systems built in *different* harnesses talk. MCP, A2A, ACP, ANP, Coral, agents.json/agents.txt, NLIP, AITP.
3. **Runtimes / substrates** — serving engines, schedulers, durable-execution layers, "agent OS" prototypes. Parrot, Autellix, Teola, AIOS, MemGPT/Letta, Temporal, DBOS, Restate.

MPI belongs to none of these three cleanly, which is the paper's opening. MPI is a *programming-model specification with a reference-quality API surface* — a portable vocabulary of ranks, communicators, and collectives that a program is written against and that many runtimes implement. There is no analogue in the agent world. The closest artifacts are harness frameworks (which own control flow and are not portable) and protocols (which are portable but are transport/RPC specs with no programming model above point-to-point request/response).

---

## 1. Multi-agent LLM harness frameworks

### 1.1 AutoGen / AG2 → Microsoft Agent Framework

AutoGen's original contribution was the *conversable agent* and `GroupChat`: agents are message handlers, and a `GroupChatManager` selects the next speaker from a roster, broadcasting each utterance to the whole group [autogen]. This is the single most influential multi-agent primitive in the field, and also the source of its most reliably reproduced pathology: full-broadcast group chat means every agent's context grows with every other agent's output, so token cost grows superlinearly in rounds × participants, and the "who speaks next" decision is itself an LLM call with no liveness guarantee. Failure handling is essentially termination-condition heuristics (max rounds, a termination string). There is no group beyond the roster, no scoping, no collective operation, and no shared mutable state other than the transcript.

As of 2026 this lineage has consolidated. Microsoft merged Semantic Kernel and AutoGen into the **Microsoft Agent Framework** (announced October 2025); AF reached 1.0 GA on 2 April 2026; the Agent Harness and Foundry Hosted Agents reached GA on 3 August 2026; **Semantic Kernel and AutoGen are both in maintenance mode** [ms-af-launch, ms-af-10, infoq-af-ga]. AF 1.0 ships two orthogonal layers: *Agent Workflows* (a typed graph engine with fan-out/converge, checkpointing, and hydration) and *Multi-Agent Orchestration* (sequential, concurrent, handoff, group chat, and Magentic patterns — all with streaming, checkpointing, HITL approvals, pause/resume) [ms-af-10]. The Harness adds function invocation, per-call history persistence, **context compaction**, plan/execute todo lists, file memory, skills, and tool approval [infoq-af-ga]. This is the strongest existing counterargument to "no one has built a general agent runtime": Microsoft has. But note what AF's orchestration patterns *are* — five named topologies exposed through one API. They are canned choreographies, not composable communication primitives. You cannot express "reduce over this subgroup with this operator" in AF; you can pick `concurrent` and then write the aggregation yourself in a converge node.

### 1.2 LangGraph

LangGraph models an agent system as a state machine over a typed state object, executed in Pregel-style **super-steps**: a tick in which all scheduled nodes run (potentially in parallel) and then their writes are reduced into state [lg-checkpointers]. This is, notably, a **BSP-shaped execution model** — the closest thing in the mainstream to a barrier. Persistence is first-class: a checkpointer snapshots state at every super-step boundary, organized into threads, enabling durability, human-in-the-loop, and time travel (replay from, or fork at, any checkpoint) [lg-checkpointers, lg-timetravel]. The `interrupt()` primitive pauses a node, persists the payload, and resumes months later on a different machine via `Command(resume=...)`; the node re-executes from its start on resume, which is a real semantic wart (side effects before the interrupt re-fire) [lg-interrupt, lg-hitl]. Durability is configurable, including an `"exit"` mode that skips intermediate persistence and therefore cannot recover from a mid-execution crash [lg-checkpointers]. Subgraphs are opaque single super-steps to the parent unless given their own checkpointer [lg-timetravel].

LangGraph's multi-agent story is `langgraph-supervisor` (hub-and-spoke; only the supervisor may reply to the user) and `langgraph-swarm` (peer handoff tools; exactly one agent active at a time) [lc-multiagent-arch]. Both are *routing* patterns. There is a shared `State` object with reducers (`Annotated[list, operator.add]`), which is the closest thing in the ecosystem to a shared-memory window — but it is per-graph, untyped as to ownership, and has no concurrency control beyond the reducer.

**LangChain's own benchmark is one of the most useful honest data points in the field** [lc-benchmark]. On a modified τ-bench with 6 synthetic distractor domains (19 tools each), using `gpt-4o`: a single agent degrades sharply once there are ≥2 distractor domains; supervisor and swarm hold roughly flat in both score and token cost; swarm slightly beats supervisor throughout. The reason supervisor loses is explicitly named as *the translation layer* — sub-agents cannot address the user, so the supervisor paraphrases and corrupts. LangChain's fixes were (i) strip handoff messages from sub-agent context, (ii) add a `forward_message` tool so the supervisor can pass a sub-agent's text through verbatim, (iii) tune the handoff tool name. These changes produced a **~50% relative improvement** over their initial supervisor implementation [lc-benchmark]. Read that carefully: half the performance of a supervisor architecture was recovered by fixing *message-passing semantics* — specifically, by adding a zero-copy forward and by scoping what enters each participant's context. That is an argument for the paper, not against it.

### 1.3 CrewAI, MetaGPT, ChatDev, CAMEL/OWL

- **CrewAI** — role-based abstraction: `Agent` (role/goal/backstory), `Task`, `Crew`, with sequential or hierarchical (manager+worker) process modes, plus `Flows` (`@start`, `@listen`, `@router` over shared state) for event-driven control [crewai-flows-cmp]. Fastest on-ramp; the abstraction "charges interest when you need to debug a stuck handoff" [ctaio-6frameworks]. No groups, no collectives, no formal fault model.
- **MetaGPT** — SOP-driven "software company" assembly line: roles emit standardized *artifacts* (PRD, design doc, API spec) rather than free chat, and a shared message pool with publish/subscribe routes artifacts to subscribers. This is the closest existing thing to typed messages and topic-scoped delivery in the agent world, and it is worth citing as a partial precursor. It is still a fixed pipeline, not a programmable communicator.
- **ChatDev** — waterfall chat chains between paired roles. MAST found ChatDev's failure profile dominated by verification failures; targeted interventions produced **+15.6%** on ChatDev and the authors explicitly conclude that this is *not enough* — "simple fixes are still insufficient for achieving reliable MAS" [mast].
- **CAMEL / OWL** — role-playing dyads with an inception-prompting protocol; OWL extends to a hierarchical multi-agent workforce over a shared toolkit. Fundamentally a two-party conversation abstraction generalized.

### 1.4 Lab SDKs: OpenAI, Anthropic, Google

- **OpenAI Agents SDK** (production successor to the experimental Swarm). Primitives: Agents, **Handoffs** (delegation compiled to a typed tool call, e.g. `transfer_to_agent_b`), Tools (function/MCP/hosted), Guardrails (input, output, and tool-level, running in parallel with the agent and able to abort mid-generation), Sessions (automatic history), Tracing [openai-agents-docs, morph-frameworks]. Critically for our purposes: **"No shared state bus, no message queues"** — handoffs pass control plus conversation history [morph-frameworks]. Recent versions default `nest_handoff_history=True`, collapsing prior turns into one nested assistant summary message on each handoff, with `handoff_input_filter` / `handoff_history_mapper` hooks to override [openai-running-agents]. This is context management as a *transport-layer concern* of the handoff — and it is exactly the design point AgentMPI should engage with: OpenAI has effectively invented a lossy, implicit `MPI_Send` with a built-in compressor and no way for the receiver to negotiate.
- **Anthropic Claude Agent SDK** — subagents (parallel/nested spawn for subtasks), automatic **context compaction** for long-running tasks, `max_budget_usd` per-session cost cap, permission modes [morph-frameworks] `[UNVERIFIED: the specific parameter names come from a third-party comparison, not Anthropic docs]`. Anthropic's own architectural writeup is covered in §3.2.
- **Google ADK** — typed/structured workflow agents (sequential, parallel, loop) plus LLM-driven agents; graph-based conditional/branching/retry pipelines in 2.x; persistent checkpoints and context "rewind"; native A2A for cross-process agent calls [morph-frameworks, composio-cmp] `[UNVERIFIED: 2.x specifics from third-party comparisons]`.

### 1.5 Magentic-One

Microsoft Research's generalist multi-agent system: an Orchestrator maintaining a **Task Ledger** (facts, guesses, plan) and a **Progress Ledger** (per-step tracking, stall detection, replanning), driving specialized agents (WebSurfer, FileSurfer, Coder, ComputerTerminal). Reported 2024 results: **38% GAIA, 27.7% AssistantBench, 32.8% WebArena** [infoq-af-ga]. Magentic is now a stable orchestration pattern in Microsoft Agent Framework [ms-af-10]. The progress ledger is the field's best existing answer to livelock detection, and it is a *heuristic LLM self-report*, not a protocol-level liveness mechanism.

### 1.6 Historical and adjacent

**AutoGPT / BabyAGI** (2023) established the autonomous task-loop-with-memory pattern and, more importantly, established its failure mode: unbounded loops, no termination criterion, no cost ceiling. **DSPy** is not an agent framework but a *compiler* for LLM programs: you declare modules and signatures, never prompts, and an optimizer compiles against a metric. DSPy 3 (2025) added first-class async and native tool calls and shipped **GEPA** alongside MIPROv2 [dspy-optimizers, aicraft-dspy]. GEPA (Agrawal et al., 2025, arXiv:2507.19457, ICLR 2026 oral per secondary source) is a reflective evolutionary optimizer over a Pareto front of candidates, driven by a metric that returns `Prediction(score, feedback)` where `feedback` is free text read by a reflection LM [dspy-gepa, gepa-repo] `[UNVERIFIED: arXiv:2507.19457 taken from DSPy's official docs, not fetched directly; ICLR-2026-oral status from a third-party blog]`. **TextGrad** builds a text computation graph with LLM-generated natural-language "gradients" and `.backward()`; published in *Nature* 639:609–616 (2025) [aicraft-dspy] `[UNVERIFIED: Nature volume/pages from a third-party blog]`. **OPRO** (Yang et al., 2023) uses the LLM itself as the optimizer over a trajectory of scored prompts. These matter to the paper because they occupy the "compile a multi-call LLM program" niche *without* offering any communication abstraction — DSPy's modules compose by Python function call, not by message.

**LlamaIndex Workflows 1.0** (22 June 2026) is event-first: `@step` methods consume and emit typed `Event`s over an async bus with a `Context` that can `send_event()` to fan out and collect back; no built-in agents; Python and TypeScript [crewai-flows-cmp]. This is the most primitive-shaped framework in the list — the closest existing thing to a message-passing layer that is agnostic to who the participants are — and it should be discussed as such. **Pydantic AI** contributes typed agent I/O boundaries and durable execution; **Mastra** is the TypeScript-native equivalent; **Inngest AgentKit** and **Dify** wrap orchestration in durable/step-function and visual-builder skins respectively; **smolagents** minimizes to code-writing agents; **Agno** targets high-throughput swarms [uvik-frameworks, sideguy-cmp] `[UNVERIFIED: per-framework claims in this sentence come from comparison blogs, not primary docs]`. **Ray/Anyscale** supplies actor-model distribution beneath several of these but offers no agent-level semantics.

---

## 2. Interoperability protocols — the closest related work

### 2.1 MCP (Model Context Protocol), Anthropic

**MCP is agent↔tool, not agent↔agent. This is confirmable from the spec itself and from the survey literature, and the paper should assert it flatly.** The current specification revision is **2026-07-28** [mcp-versioning]. Its own overview states MCP standardizes how applications "share contextual information with language models, expose tools and capabilities to AI systems, build composable integrations," between **Hosts** (LLM applications), **Clients** (connectors inside the host), and **Servers** (context/capability providers) — explicitly modeled on the Language Server Protocol [mcp-spec-2026]. Yang et al.'s taxonomy classifies MCP as a **context-oriented, general-purpose** protocol, structurally distinct from **inter-agent** protocols [yang-survey]. A third-party technical guide puts it plainly: "MCP isn't designed for negotiating tasks with another autonomous entity, it's designed for invoking tools" [tyk-a2a].

Technical shape, current revision:
- **RPC:** JSON-RPC 2.0. **Transports:** stdio for local subprocesses, Streamable HTTP for remote [mcp-spec-2026, tyk-a2a].
- **Statelessness:** the 2026-07-28 revision made requests "stateless, self-contained," with **per-request capability negotiation** rather than a session handshake. Every request carries `io.modelcontextprotocol/protocolVersion` in `_meta`; Streamable HTTP mirrors it in the `MCP-Protocol-Version` header; a mandatory `server/discover` RPC returns supported versions, capabilities, and identity in one call; version mismatch yields `UnsupportedProtocolVersionError` listing supported versions [mcp-versioning].
- **Server features:** Resources, Prompts, Tools. **Client features:** Elicitation. **Sampling** and **roots** are *deprecated* in this revision [mcp-spec-2026, mcp-mrtr-blog].
- **MRTR (SEP-2322):** server-initiated requests are no longer separate JSON-RPC requests. A server returns an `InputRequiredResult` carrying `inputRequests` (a map of `ElicitRequest` / `CreateMessageRequest` / `ListRootsRequest`) and/or an opaque `requestState`; the client fulfils them and **re-issues the original call as an independent JSON-RPC request with a new id**, echoing `requestState` and supplying `inputResponses`. Motivation is explicitly operational: avoid shared server-side state, avoid stateful load balancing, and reduce dependence on long-lived SSE streams. This is a **breaking change** [mcp-sep2322, mcp-mrtr-doc].
- **Extensions:** Tasks (async long-running ops with polling, mid-flight input, durable handles), Skills over MCP, MCP Apps [mcp-spec-2026].

Note the direction of travel: MCP has moved *away* from stateful sessions and server-initiated conversation toward stateless request/retry. That is the correct engineering choice for a tool protocol and precisely the wrong shape for a collective-communication substrate. It reinforces the gap rather than closing it.

### 2.2 A2A (Agent2Agent), Google → Linux Foundation

Announced by Google 9 April 2025, donated to the Linux Foundation (hosted since June 2025), **v1.0 stable in 2026** under a committee including Google, Microsoft, AWS, Salesforce, and IBM [mastra-a2a, tyk-a2a, ibm-protocols].

- **Data model:** defined once in Protocol Buffers as the single source of truth, also published as JSON Schema 2020-12 (`a2a.json`) generated from the protos. Five core objects: **Task, Message, AgentCard, Part, Artifact** (plus `AgentSkill`, `Extension`) [mastra-a2a, tyk-a2a].
- **Discovery:** `AgentCard` at `/.well-known/agent-card.json` (RFC 8615), unauthenticated GET. Fields: `name`, `description`, `version`, `provider`, `supportedInterfaces[]` (ordered, first preferred), `capabilities`, `defaultInputModes`/`defaultOutputModes`, `skills[]`, `securitySchemes`, `security`, `signatures` [a2a-defs, tyk-a2a]. Card integrity via `AgentCardSignature` — JWS (RFC 7515) over a JCS-canonicalized (RFC 8785) card [tyk-a2a].
- **Bindings:** three ship in v1.0 — JSON-RPC 2.0 over HTTPS, gRPC, HTTP+JSON/REST — advertised per-interface [tyk-a2a].
- **Task state machine (§4.1.3):** `UNSPECIFIED`, `SUBMITTED`, `WORKING`, `INPUT_REQUIRED`, `AUTH_REQUIRED`, `COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`. `INPUT_REQUIRED`/`AUTH_REQUIRED` are *interrupted*; the other four are *terminal*. **The spec deliberately does not define a transition matrix** — only that terminal states are final and `SUBMITTED` is an entry state; `SUBMITTED → COMPLETED` is legal and emitted by the reference SDKs [a2a-rust-taskstate, tyk-a2a]. Once terminal, a task cannot restart; refinements start a **new task within the same `contextId`** [a2a-life-of-task].
- **Message vs Artifact:** a Message is a communication turn (role + parts) and *may be missed on reconnect*; an Artifact is the task's durable output, held in task state and survives disconnect [mastra-a2a]. A `Part` holds exactly one of text / structured JSON / URL / raw bytes, with optional `mediaType`, `filename`, `metadata`.
- **Progress:** blocking `SendMessage`, SSE streaming (`TaskStatusUpdateEvent`, `TaskArtifactUpdateEvent`), or webhook push notifications; `capabilities` is a set of boolean flags (`streaming`, `pushNotifications`, `extendedAgentCard`), *not* named operations [tyk-a2a].
- **Errors:** standard JSON-RPC error object, or async transition to `FAILED`/`REJECTED`. **Retry logic and circuit-breaking are explicitly left to the client** [tyk-a2a].

**What A2A does not provide — state this precisely in the paper.** No group/communicator concept; `contextId` is a correlation identifier for related tasks, not a membership set with a defined participant list. No collectives: no broadcast, scatter, gather, reduce, allreduce, barrier. No shared memory or one-sided operations. No rank, no topology, no ordering guarantee across peers. No standardized registry API (explicitly a build-it-yourself area) [tyk-a2a]. No per-skill body schema — a caller learns a skill accepts `application/json` but not what JSON [tyk-a2a]. No credential downscoping in delegation chains (the recommended workaround is RFC 8693 token exchange, outside the spec) [tyk-a2a]. A2A is a *delegation* protocol: one client, one server, one task, one artifact stream. Its unit of coordination is the bilateral task.

### 2.3 ACP, AGNTCY/ACP, ANP, and the rest

- **ACP (Agent Communication Protocol)** — IBM/BeeAI, now Linux Foundation. RESTful HTTP; an ACP server fronts many agents behind a single endpoint and routes to the right one; asynchronous-by-default suits long-running work; router-agent topology; the lowest barrier to entry of the four (`curl` is a sufficient client) [ibm-protocols, data443-acp-anp, zylos-protocols].
- **AGNTCY / Agent Connect Protocol** — Cisco-led coalition (LangChain, LlamaIndex, Galileo). Two specs: **OASF** (Open Agent Schema Framework), a machine-readable capability/interface manifest, and **ACP**, an OpenAPI-specified remote-invocation protocol; plus the AGNTCY Directory, a content-addressed registry of signed OASF records as OCI artifacts over a libp2p DHT [coral, awesome-protocols, anp-w3c]. **Naming hazard for the paper: "ACP" refers to two different protocols** (IBM/BeeAI's Agent Communication Protocol and AGNTCY's Agent Connect Protocol). Disambiguate on first use.
- **ANP (Agent Network Protocol)** — decentralized, "the HTTP of the agentic web." Three layers: identity + encrypted communication (W3C DIDs), meta-protocol negotiation, application protocol; JSON-LD payloads over HTTP. Strongest trust model of the four (cryptographic peer verification with no trusted intermediary) at the cost of high connection-establishment overhead and dependence on immature DID tooling [anp-wp, anp-w3c, zylos-protocols].
- **Coral Protocol** — MCP-native coordination layer: `@mention`-addressed threads plus on-chain micropayments [coral, awesome-protocols]. Of everything in this section, threads-with-mentions is the closest thing to *scoped multicast* that exists in a deployed agent protocol, and the paper should acknowledge it directly.
- **agents.json / agents.txt** — capability-declaration layer. `agents.json` (Wildcard) layers flows, links, and auth atop OpenAPI so agents can drive REST APIs; the `agents.txt`/`agents.json` pairing mirrors `llms.txt`/`llms-full.txt` as an announcement-plus-catalog convention, carrying transports, pricing, and chain identifiers [awesome-protocols, agents-txt-spec].
- **NLIP (Natural Language Interaction Protocol)** — lightweight successor to ACLs, cited alongside A2A/MCP as replacing KIF/SL with simple JSON over modern web transports [layered-ioa].
- **AITP** — NEAR AI; cross-trust-boundary Threads with pluggable payment/decision/identity capabilities; draft [awesome-protocols].
- **x402** — Coinbase, now Linux Foundation; HTTP 402 with a stablecoin payment header for pay-per-call APIs [awesome-protocols]. Relevant only if AgentMPI needs a metering story.
- **Standardization activity:** the **W3C AI Agent Protocol Community Group** exists and ships ANP [anp-w3c]. On the IETF side, **DNS-AID** (DNSOP draft) proposes publishing/discovering/verifying agents via standard DNS records, with a Linux Foundation reference stack; **AgentDNS** is an expired draft [awesome-protocols] `[UNVERIFIED: IETF draft names and status from a curated GitHub list, not from datatracker]`. The Linux Foundation's **Agentic AI Foundation** is the current institutional umbrella [woa-survey].

### 2.4 Surveys comparing them

Three real, verifiable surveys:

1. **Ehtesham et al., "A Survey of Agent Interoperability Protocols: MCP, ACP, A2A, ANP" (arXiv:2505.02279)** — side-by-side on nine dimensions plus a phased adoption roadmap; characterizes MCP's client-server model as simple but limiting for complex coordination, ACP as centrally routed, A2A as peer task delegation, ANP as fully decentralized with negotiation overhead [ehtesham-survey].
2. **Yang et al., "A Survey of AI Agent Protocols" (arXiv:2504.16736)** — the two-dimensional taxonomy: *object orientation* (context-oriented vs. inter-agent) × *application scenario* (general-purpose vs. domain-specific); 14 protocols compared on efficiency, security, scalability [yang-survey].
3. **"A Technical Taxonomy of LLM Agent Communication Protocols" (arXiv:2606.19135)** — critiques Yang et al. as too coarse ("a taxonomy with just two dimensions seems insufficient"), and extends the object-orientation axis with a third `hybrid` value; dimensions include state, discovery mechanism, schema flexibility [tech-taxonomy].

Also relevant: **"A Layered Protocol Architecture for the Internet of Agents" (arXiv:2511.19699)**, which argues that A2A/MCP/NLIP "excel at standardizing the *syntactic envelope*" but leave semantic alignment unaddressed, and proposes a dedicated semantic layer [layered-ioa]. And **"LLM Agent Communication Protocol (LACP) Requires Urgent Standardization" (arXiv:2510.13821)**, which names three deficiencies of the current landscape: interoperability gaps from fragmentation, security as an afterthought, and **"monolithic design and lack of transactional integrity" — the general absence of built-in support for atomic transactions across multi-step operations** [lacp]. That last point is a direct assist for AgentMPI's fault-tolerance story.

### 2.5 The classical ancestors — and the paper that says so

The paper needs this section for credibility, and there is a citable source that makes exactly the argument.

**"From Multi-Agent Systems and the Semantic Web to Agentic AI: A Unified Narrative of the Web of Agents" (arXiv:2507.10644)** spans 1990–2026 and frames three generations by where semantic effort is located: platform-side coordination (classical MAS), data-side annotation (Semantic Web), model-side interpretation (LLM era). On the ancestors it is explicit: *"Early attempts at agent communication standards (KQML and FIPA ACL) laid a foundation of structured message protocols with formal semantics, but they were oriented toward controlled environments and did not anticipate the scale and heterogeneity of the open Web. The failure of heavyweight, specialised stacks like FIPA taught the lesson that modern protocols operationalise: pragmatic simplicity and alignment with existing web standards (HTTP, JSON-RPC) beat agent-specific stacks, even at the cost of reduced formal semantic expressiveness."* It also covers the Nov-2024–Aug-2026 institutional convergence (Agentic AI Foundation, A2A v1.0, MCP, payment protocols, EU AI Act phasing, NIST AI Agent Standards Initiative) [woa-survey].

The layered-IoA paper adds the sharpest diagnosis of *why* FIPA failed: heavyweight formal ontologies (KIF, SL) imposed implementation overhead, **and the `:ontology` slot was merely a label — the protocol specified no mechanism for negotiating or aligning ontologies between agents that did not already share an identical pre-shared model. It identified the problem and offered no solution** [layered-ioa]. A textbook treatment makes the "returning idea" claim directly: *"KQML and FIPA-ACL proposed a shared language of speech acts so heterogeneous agents could interoperate; the ecosystem to use it did not exist, so the idea waited. MCP and A2A are that idea, scaled out... The speech-act vocabulary became a JSON-RPC method set and a capability card"* [scalable-book].

The genealogy to cite: **Austin (1962) / Searle (1969)** speech acts → **KQML** (Finin et al., 1994) → **FIPA-ACL** (2002) with performatives `INFORM`/`REQUEST`/`PROPOSE` and an `:ontology` slot → **JADE** as the dominant FIPA-compliant platform. Orthogonally: **Contract Net Protocol** (Smith, 1980) for announce/bid/award task allocation — the direct ancestor of every "supervisor delegates to specialists" pattern in §1; **blackboard systems** (Hearsay-II; Erman et al., 1980) for opportunistic shared-state coordination — the ancestor of LangGraph's shared `State` and MetaGPT's message pool; **Linda tuple spaces** (Gelernter, 1985) for associative shared memory with `out`/`in`/`rd` — the ancestor that *nobody in the LLM agent world has rediscovered yet*, and the most obviously useful one for AgentMPI. Tuple spaces are being reached for outside academia: a production-patterns writeup maps agent primitives directly onto "Process Group ≈ MPI communicator / Gloo process group" and "TupleSpace ≈ MPI ghost-cell exchange, barrier sync" [plexspaces].

---

## 3. Big comparison table

Legend: **Groups/scoping** = is there a first-class named set of participants with membership semantics? **Collectives** = broadcast/scatter/gather/reduce/barrier as protocol- or API-level operations (not "you can write a fan-out node"). **Shared state** = a defined shared mutable region with access semantics.

| System | Kind | Coordination model | Communication primitive | Groups / scoping? | Collectives? | Shared state? | Fault handling | Context management |
|---|---|---|---|---|---|---|---|---|
| AutoGen / AG2 | Framework | LLM-selected speaker in a GroupChat | Broadcast chat message to roster | Roster only (no scoping) | No | Transcript only | Max-rounds / termination string | Full transcript; manual truncation |
| Microsoft Agent Framework 1.0 | Framework + runtime | Typed workflow graph **or** 5 named orchestration patterns (sequential, concurrent, handoff, group chat, Magentic) | Typed edges; chat messages | Pattern-implicit | No (fan-out/converge only) | Workflow state | Checkpointing, hydration, retry, pause/resume, HITL approval | Harness-level context compaction, per-call history persistence |
| LangGraph | Framework | Pregel-style super-steps over typed state (BSP-shaped) | State channel writes + reducers; handoff tools | Subgraphs (opaque unless own checkpointer) | No (parallel nodes + reducer ≈ ad-hoc reduce) | Yes — shared `State` with reducers | Checkpointer per super-step; replay/fork; `interrupt()`; configurable durability incl. crash-unsafe `"exit"` | Manual; state kept lean by convention |
| CrewAI | Framework | Sequential / hierarchical process; Flows event graph | Task I/O; delegation | Crew (a roster) | No | Flow state; memory/knowledge modules | Task-level retry | Built-in memory + RAG |
| MetaGPT | Framework | SOP assembly line | **Typed artifacts** via shared message pool with pub/sub | Subscription = de facto topic scope | No | Message pool | Role-level review stages | Artifacts instead of transcripts |
| ChatDev | Framework | Waterfall dual-role chats | Chat turn | No | No | Phase memory | Review phases (empirically weak) | Phase-scoped |
| CAMEL / OWL | Framework | Role-playing dyad; OWL adds a hierarchical workforce | Inception-prompted turn | No | No | Shared toolkit | Minimal | Dyad transcript |
| OpenAI Agents SDK | Framework | Handoffs (typed tool calls) | Control transfer + history; **explicitly "no shared state bus, no message queues"** | No | No | Sessions (per-thread history) | Guardrails (input/output/tool) abort runs; resumable approvals | `nest_handoff_history` collapses history into one summary; `handoff_input_filter` |
| Claude Agent SDK | Framework | Orchestrator + subagents | Subagent spawn + condensed return | No | No | Filesystem / memory tools | Permission modes; budget caps | Automatic compaction; per-subagent context isolation |
| Google ADK | Framework | Typed workflow agents + graph pipelines | Typed I/O; A2A for cross-process | No | No | Persistent checkpoints | Retry pipelines; state restore; context "rewind" | Typed outputs; memory isolation per A2A |
| Magentic-One | System | Orchestrator with Task Ledger + Progress Ledger | Directed instructions to specialists | No | No | The two ledgers | **Stall detection → replan** (heuristic) | Ledger replaces transcript |
| LlamaIndex Workflows 1.0 | Framework | Event-driven async bus | Typed `Event`; `ctx.send_event()` fan-out + collect | No | No (fan-out/collect only) | `Context` | Step-level | BYO |
| Pydantic AI / Mastra / smolagents / Agno | Frameworks | Loop or graph | Typed function I/O | No | No | Varies | Typed validation; durable exec (Pydantic AI) | BYO |
| AutoGPT / BabyAGI | Framework (historical) | Self-directed task loop | Task queue | No | No | Vector memory | **None** (unbounded loops) | Ad-hoc summarization |
| DSPy (+GEPA) | **Compiler** | Declarative module composition; optimizer compiles | Python call | n/a | n/a | n/a | n/a (offline optimization) | Signature-scoped |
| **MCP 2026-07-28** | **Protocol** | Host↔Client↔Server, **agent↔tool** | JSON-RPC 2.0; stateless self-contained requests; stdio / Streamable HTTP | No | No | Resources (read-only context) | Per-request version errors; MRTR retry-with-state; Tasks extension = durable handles | Out of scope |
| **A2A v1.0** | **Protocol** | Bilateral client↔server task delegation | Message (turn) + Artifact (output), Parts; JSON-RPC / gRPC / REST | `contextId` = correlation id, **not** membership | **No** | **No** | JSON-RPC errors; `FAILED`/`REJECTED` states; **retry & circuit-breaking left to client** | Out of scope |
| ACP (IBM/BeeAI) | Protocol | REST, router fronting many agents | HTTP request/response, async default | Endpoint routing | No | No | HTTP semantics | Out of scope |
| ACP (AGNTCY/Cisco) + OASF | Protocol | OpenAPI remote invocation + capability manifest + signed registry | HTTP | Registry namespace | No | No | HTTP semantics | Out of scope |
| ANP | Protocol | Decentralized peer, DID identity, meta-protocol negotiation | JSON-LD over HTTP | No | No | No | Negotiation-level | Out of scope |
| Coral | Protocol | MCP-native threads | **`@mention`-addressed thread messages** | **Thread ≈ scoped multicast** | No | Thread history | Payment/settlement layer | Out of scope |
| agents.json / agents.txt | Spec | Capability declaration | n/a (discovery) | n/a | n/a | n/a | n/a | n/a |
| KQML / FIPA-ACL | Protocol (historical) | Speech acts | Performatives (`inform`, `request`, `propose`) with `:ontology` slot | Conversation ids | No | No | Protocol-level failure performatives | n/a |
| Contract Net (1980) | Protocol (historical) | Announce → bid → award | Task announcement broadcast | Bidder set | **Broadcast + gather (implicitly)** | No | Re-announce | n/a |
| Linda tuple spaces (1985) | Coordination model | Associative shared memory | `out` / `in` / `rd` | Space | Via space | **Yes** | Blocking `in` semantics | n/a |
| Temporal / DBOS / Restate | Runtime | Durable execution | Workflow/step invocation | Task queues / namespaces | No | Workflow state | **Strong**: replay from event history (Temporal), Postgres step checkpoints (DBOS), per-invocation journal (Restate) | n/a |
| Parrot / Autellix / Teola | Runtime | Program- or dataflow-aware scheduling | Semantic Variables / program deps / task primitives | No | No | KV cache locality | Scheduler-level | Cache/locality, not semantics |
| AIOS | Runtime | Agent OS kernel: scheduler, context, memory, storage, tool, access managers | AIOS syscalls | No | No | Memory/storage managers | Access manager; context snapshot/restore | **Context manager with snapshot & restore** |
| MemGPT / Letta | Runtime | Virtual context paging | Function calls to page in/out | No | No | Tiered memory | n/a | **The canonical OS-analogy context strategy** |

Two rows carry the paper: **no framework and no protocol in this table has a communicator, and no framework and no protocol has a collective.** The only positive cells are historical (Contract Net's broadcast/bid, Linda's tuple space) or degenerate (LangGraph's reducer, Coral's threads).

---

## 4. Empirical failure modes — the hard evidence

### 4.1 MAST (Cemri et al., Berkeley) — the taxonomy and the numbers

*Why Do Multi-Agent LLM Systems Fail?* (arXiv:2503.13657; NeurIPS 2025) is the load-bearing citation. Method: Grounded Theory over execution traces from **7 open-source MAS frameworks (MetaGPT, ChatDev, HyperAgent, OpenManus, AppWorld, Magentic, AG2)** across **200 conversation traces averaging 15,000+ lines each**, six expert annotators, **Cohen's κ = 0.88**; plus a validated LLM-as-judge pipeline. The v2/NeurIPS release scales this to **MAST-Data, 1,600+ annotated traces** (κ derived from 150 traces) across GPT-4, Claude 3, Qwen2.5, and CodeLlama [mast, mast-neurips].

**14 failure modes in 3 categories, with observed frequencies** (from the paper; note these are per-failure-instance shares, and the modes co-occur):

*FC1 — Specification / system-design issues (**41.77%** of failures):*
- FM-1.1 Disobey task specification — **10.98%**
- FM-1.2 Disobey role specification — **0.50%**
- FM-1.3 Step repetition — **17.14%** ← the single largest mode
- FM-1.4 Loss of conversation history (unexpected context truncation) — **3.33%**
- FM-1.5 Unaware of termination conditions — **9.82%**

*FC2 — Inter-agent misalignment (**36.94%**):*
- FM-2.1 Conversation reset — **2.33%**
- FM-2.2 Fail to ask for clarification — **11.65%**
- FM-2.3 Task derailment — **7.15%**
- FM-2.4 Information withholding — **1.66%**
- FM-2.5 Ignored other agent's input — **0.17%**
- FM-2.6 Reasoning–action mismatch — **13.98%**

*FC3 — Task verification (**21.30%**):*
- FM-3.1 Premature termination — **7.82%**
- FM-3.2 No or incomplete verification — **6.82%**
- FM-3.3 Incorrect verification — **6.66%** (FM-3.2 + FM-3.3 = **13.48%**)

Three findings to quote directly. (i) The distribution is **balanced** across categories, which the authors read as evidence the taxonomy is not an artifact of any one system's design. (ii) Per-system profiles differ sharply — AppWorld is dominated by premature termination, OpenManus by step repetition, HyperAgent by step repetition + incorrect verification. (iii) **Targeted interventions helped but did not fix it: +15.6% for ChatDev, and the authors conclude "simple fixes are still insufficient... Mitigating identified failures will require more fundamental changes in system design"** [mast].

For AgentMPI the striking observation is how many of these 14 are *communication-protocol* bugs rather than model bugs. FM-1.3 (step repetition, 17.14%) is "no idempotence/completion record." FM-1.5 (9.82%) and FM-3.1 (7.82%) are "no agreed termination predicate" — i.e., no barrier and no consensus. FM-1.4 (3.33%) and FM-2.1 (2.33%) are "no durable, addressable message log." FM-2.4 (1.66%) and FM-2.5 (0.17%) are "no delivery/ack semantics." That is **~42% of observed failures** located in mechanisms MPI defines and agent frameworks do not. The paper should make exactly this argument, and should also state honestly that FM-2.6 (13.98%) and FM-1.1 (10.98%) are *not* protocol-addressable.

### 4.2 The "don't build multi-agents" camp — and its 2026 revision

**Cognition, "Don't Build Multi-Agents" (Walden Yan, 2025).** Two principles: *(1) Share context — and share full agent traces, not just individual messages. (2) Actions carry implicit decisions, and conflicting decisions carry bad results.* The canonical example: an orchestrator splits "build Flappy Bird" into background and bird; the sub-agents never see each other's work; one renders Super Mario-style art, the other a mismatched bird; the halves cannot be glued. Yan's 2025 conclusion: *"running multiple agents in collaboration only results in fragile systems. The decision-making ends up being too dispersed and context isn't able to be shared thoroughly enough... At the moment, I don't see anyone putting a dedicated effort to solving this difficult cross-agent context-passing problem"* [cognition-dont].

**Cognition, "Multi-Agents: What's Actually Working" (2026, ~10 months later)** is the more important citation because it is a partial retraction with data:
- *"Our original observations still hold today for parallel-writer swarms... But we've found a narrower class of patterns that do [work]: setups where multiple agents contribute intelligence to a task while writes stay single-threaded."*
- **Devin Review catches an average of 2 bugs per PR on PRs written by Devin, ~58% of them severe** (logic errors, missing edge cases, security vulnerabilities).
- Counterintuitively, the generator–verifier loop **works best when the coding and review agents share no context beforehand** — justified explicitly by context rot and "the math of attention": the reviewer skips the coder's accumulated extraneous context, reads only the diff, and rediscovers what it needs.
- Manager-Devin spawning child Devins coordinates **through an internal MCP**, and the named difficulties are all communication: *"Agents assume they share state with their children when they don't. Cross-agent communication — a sub-agent writing messages back to its manager to be passed to other agents in the agent team — doesn't happen by default, because models haven't been trained in environments where it needed to."*
- *"We think the unstructured-swarm approach, arbitrary networks of agents negotiating with each other, is mostly a distraction. The practical shape is map-reduce-and-manage."*
- *"The open problems are all communication problems."* [cognition-working]

The last two bullets are simultaneously the strongest endorsement and the strongest challenge to AgentMPI. "Map-reduce-and-manage" is *literally scatter–compute–gather*. But Cognition also asserts that arbitrary negotiation topologies are a distraction — so the paper must not sell a fully general mesh.

**Anthropic, "How we built our multi-agent research system" (2025).** Orchestrator-worker: a LeadResearcher (Claude Opus 4) plans with extended thinking, spawns 3–5 Sonnet 4 subagents in parallel with self-contained task specs (objective, output format, tool list, stopping boundary), each searching in its own context window and returning a condensed summary; the lead synthesizes and may spawn another wave. Numbers: **+90.2% over single-agent Claude Opus 4 on their internal research eval**; on BrowseComp three factors explain **95% of performance variance, with token usage alone explaining 80%**; agents use ~**4× chat tokens**, multi-agent systems ~**15× chat tokens**. When the lead nears the 200K context limit it writes the plan to memory and hands off to fresh-context subagents. The stated limits are precise and quotable: *"some domains that require all agents to share the same context or involve many dependencies between agents are not a good fit for multi-agent systems today. For instance, most coding tasks involve fewer truly parallelizable tasks than research, and LLM agents are not yet great at coordinating and delegating to other agents in real time."* [anthropic-mas]

**LangChain** occupies the middle: start single-agent, add tools before agents, graduate to multi-agent only at clear limits — the named triggers being context management (specialized knowledge won't fit one prompt) and organizational boundaries (different teams own different capabilities) [lc-multiagent-arch, lc-benchmark].

**Mieczkowski et al., "Language Model Teams as Distributed Systems" (Princeton/MIT/Cambridge/NYU, arXiv:2603.12229)** is the closest academic near-neighbor to AgentMPI and must be cited prominently and handled carefully. It argues distributed computing should be the *principled foundation* for LLM teams, on four shared properties: **independence, communication, concurrency, fallibility**. It also names the disanalogies honestly: *"communication in LLM teams occurs in natural language rather than fixed, formally specified protocols, making it ambiguous or shaped by pragmatic interpretation... traditional distributed systems models often assume well-defined failure modes, whereas LLM failures can be semantic and probabilistic."* Empirically, on three collaborative coding domains × three dependency structures × teams of 1–5 homogeneous agents (Claude Sonnet 4.6, Gemini 3-Flash, GPT-5.2):
- **Amdahl's Law bounds LLM team speedup.** Speedup differed by parallelizability (Kruskal–Wallis H = 61.4, p < 0.001) in the predicted order; but even highly-parallel tasks stayed **significantly below the Amdahl bound (median 2.19×, Wilcoxon p < 0.001)**.
- **Decentralization costs efficiency.** Preassigned median speedup **1.36×** vs. self-coordinating **0.88×** (i.e., *slower than a single agent*), U = 155523, p < 0.001, holding within every model.
- **Consistency violations are real and typed:** concurrent writes (two agents editing one file, silently overwriting), rewrites (overwriting a teammate's prior-round file), temporal violations (implementing a task before its dependency). Median failed tests **19 (decentralized) vs. 4 (preassigned)**, U = 287013, p < 0.001.
- **Coordination overhead:** decentralized teams sent significantly more messages (U = 311551, p < 0.001), rising with team size (r = 0.483), and had significantly more **idle rounds** — steps where agents communicated but made no task progress (U = 289672, p < 0.001) *while still burning tokens*.
- **Stragglers cut the other way:** preassigned teams had a larger straggler gap (median 2.64s vs. 1.42s, U = 8889359, p < 0.001), worse on mixed/serial tasks (mixed 3.91s vs. parallel 1.73s).
- **Cost outpaces speedup:** for serial tasks in preassigned teams, mean **token multiplier 5.83× against speedup 1.13×** (p < 0.001). Decentralized teams showed a consistently larger gap (median excess 1.17), with token cost scaling in team size (Spearman ρ = 0.40, p < 0.001) while speedup did not (ρ = −0.07, p = 0.15) [lm-teams-ds].

**This paper is the reviewer's "hasn't this been done?" objection.** §5 addresses it.

### 4.3 Context degradation

- **Lost in the Middle** (Liu et al., TACL 2024) — U-shaped positional accuracy; relevant information in the middle of a long context is used worst. `[UNVERIFIED: cited from memory and from secondary references in the MAST paper (Liu et al. 2023b); I did not fetch the primary record in this survey.]`
- **LLMs Get Lost in Multi-Turn Conversation** (Laban, Hayashi, Zhou, Neville; arXiv:2505.06120; **ICLR 2026**). 15 top open- and closed-weight LLMs, 200,000+ simulated conversations, 6 generation tasks, comparing FULL (single-turn, fully specified) to SHARDED (multi-turn, underspecified). **Average performance drop of 39%**, decomposed into **aptitude loss ~15–16%** and **unreliability increase of +112%** (more than doubling); performance degrades **~50 percentage points on average between the best and worst simulated run for a fixed instruction**; better models have better aptitude but *all* models have similarly terrible unreliability. Mechanism: models make premature assumptions, generate final solutions too early, then over-rely on their own wrong earlier answers — *"when LLMs take a wrong turn in a conversation, they get lost and do not recover"* [laban-lost].
- **Context Rot** (Chroma, July 2025). 18 frontier models; accuracy degrades with input length **even on trivial tasks and well before the window fills**; degradation is non-uniform; it worsens as needle–question semantic similarity falls and as distractors are added; on LongMemEval, models did **worse with full conversation history than with only the relevant excerpts**. GPT-family models tended to hallucinate under ambiguity, Claude-family to abstain [chroma-rot]. Caveat to note in the paper: vendor technical report, not peer-reviewed, and its model set predates several frontier releases.
- **Anthropic's context-engineering framing:** context is *"a finite resource with diminishing marginal returns"*; models have an **attention budget** that every token depletes; degradation is *"a performance gradient rather than a hard cliff"* [anthropic-ctx-eng]. The four named pathologies in practitioner writing — context poisoning (a hallucination re-ingested as fact), distraction, confusion, clash (contradictory content) — are useful vocabulary `[UNVERIFIED: taxonomy from a practitioner blog, not a paper]`.
- **Sub-agent context isolation as mitigation** is now the consensus fix across Anthropic (separate context windows per subagent) [anthropic-mas], Cognition (clean-context reviewer) [cognition-working], and LangChain (context management as the primary motivator for multi-agent) [lc-multiagent-arch]. **AgentMPI should note that context isolation is exactly the property MPI's private address space provides by construction, and that no agent framework provides it as a guarantee — only as a convention.**
- **Conversation tax in a safety-critical domain:** *Stop Listening to Me!* (arXiv:2603.11394) evaluates 17 LLMs on three clinical benchmarks under a "stick-or-switch" partitioning and finds partitioning an answer space into sequential presentations reduces end-to-end accuracy and abstention-against-incorrect-suggestions **by up to 30% on average, reaching 65% in some models**, with end-to-end accuracy falling below the single-shot baseline for 14/17 models on MedQA, 14/17 on JAMA CC, and 16/17 on MedMCQA; plus "blind switching" at rates near 50% [stop-listening].

### 4.4 Error propagation and cascading hallucination

The evidence here is genuinely mixed, and the paper should say so rather than cherry-pick.

- **From Spark to Fire: Modeling and Mitigating Error Cascades in LLM-Based Multi-Agent Collaboration (arXiv:2603.04474)** abstracts collaboration as a directed dependency graph with an early-stage amplification-risk criterion, and identifies three vulnerability classes across six mainstream frameworks: **cascade amplification, topological sensitivity, consensus inertia**. It demonstrates an attack in which **injecting a single atomic error seed leads to widespread failure**, and its genealogy-graph governance layer (a message-layer plugin, no architecture change) **prevents final infection in at least 89% of runs** [spark-to-fire].
- **Hallucination Cascade (arXiv:2606.07937)** cuts the other way, and honesty requires reporting it: 500 cascade experiments, 10 domains, three heterogeneous models (GPT-5.3, DeepSeek-V3, LLaMA-3-70B-Instruct), 1,250 evaluated responses. Deeper cascades **reduced** normalized hallucination from **0.422 at agent 1 to 0.272 at the final agent** in 3-agent chains — **amplification factor 0.644, i.e. net attenuation** — but at a cost: factual accuracy fell from **0.789 to 0.769**, and each refinement step reduced hallucination by ~0.072 while consistently losing factual content [hallu-cascade]. So chains can *suppress* hallucination while *eroding* facts.
- **PropUQ-MAS (arXiv:2608.22130)** formalizes propagation-aware uncertainty quantification over the MAS communication DAG, combining local uncertainty with inherited upstream uncertainty; reports **+6.10% AUROC and +47.58% PRR** relative gains over UQ methods that ignore propagation [propuq].
- **QUIVER (arXiv:2605.23956)** provides sensitivity matrices and bifurcation thresholds for perturbation propagation in compound AI systems, and — usefully — surveys the numbers: it credits "From Spark to Fire" with spectral-radius cascade analysis under a homogeneous-interaction-matrix assumption, and cites **Kim et al. (2025) measuring 17.2× error amplification in unstructured multi-agent networks** [quiver] `[UNVERIFIED: the 17.2× figure is secondhand via QUIVER's related work; I did not locate the Kim et al. primary source.]`
- **Error Propagation Index (EPI)** — downstream errors caused per upstream failure; voting ≈ 0.3, sequential ≈ 4.0; a chain of five agents with one 5% upstream hallucination degrading end-to-end success from ~97% to 77% `[UNVERIFIED: this framing and these figures come from a LinkedIn practitioner article that attributes EPI to "AgentArch, arXiv:2509.10769"; I could not verify that arXiv record. Do not cite the number without checking.]`

### 4.5 Coordination failures: deadlock, livelock, groupthink

- **Deadlock is real enough to be engineered against.** SyncPlan (arXiv:2608.01652) maintains a **directed wait graph** where an edge i→j means agent i is blocked on agent j, and **detects deadlock by searching for directed cycles at every frame**, triggering replan on detection; a separate Plan Staleness Detector handles non-cyclic environment-driven invalidation. It reports SOTA success on Overcooked and Honor of Kings **using <0.05% of the wall-clock runtime of existing LLM-based coordinators** [syncplan]. This is the single strongest piece of prior art for "agents need explicit synchronization primitives," and the paper should engage it directly: SyncPlan invents `Wait_agents` and cycle detection *for one planner in one embodied setting*, not as a portable primitive.
- **Livelock / idle rounds:** quantified by [lm-teams-ds] — decentralized teams have significantly more rounds in which agents communicate but complete no task, while still consuming tokens.
- **Duplicated work and lost updates:** concurrent writes and rewrites, again quantified in [lm-teams-ds] (median 19 vs. 4 failed tests).
- **Diversity collapse / groupthink.** *Diversity Collapse in Multi-Agent LLM Systems* (ACL 2026 Findings; arXiv:2604.18005) studies MAS ideation at three levels and finds: a **compute-efficiency paradox** (stronger, more-aligned models yield diminishing marginal diversity), **authority-induced collapse** (authority-driven dynamics suppress semantic diversity vs. junior-dominated groups; role-differentiated expert configurations show the *lowest* diversity, Vendi 4.65 — a "sycophancy trap"), and at the system level **diminishing returns to group size with dense communication topologies accelerating premature convergence**. The causal claim is the important one: **collapse arises from *interaction structure*, not model insufficiency** [diversity-collapse].
- **Algorithmic Groupthink** (preprint, Research Square DOI 10.21203/rs.3.rs-10172697/v1): with model and sampling fixed, varying only whether agents can see each other's work, **sharing reduced semantic diversity by 5.9% while isolation raised it by 10.3%** (p = 0.017, Cohen's d = 0.79) across 20 tasks; the effect is dose-dependent in how much is shared, holds across five LLMs from three providers (significant in two), and is not explained by input length, drift, or ordering. Under a multi-workflow protocol where sharing cost **31.8%** of diversity, **one added system-prompt instruction cut the loss to 5.5%** (n = 20 replication, p = 0.0003, d = 1.31) with no measured quality cost [algo-groupthink].
- **Sycophancy propagation.** *Too Polite to Disagree* (arXiv:2604.02668): six open-source LLMs; supplying agents with peer sycophancy priors **reduces the influence of sycophancy-prone peers, mitigates error cascades, and improves final discussion accuracy by an absolute 10.5%**; the mechanism is protocol-agnostic and requires no ground truth [too-polite].

Note the tension the paper should surface: Cognition says *share more context*; the diversity work says *sharing context destroys the exploration that motivated multi-agent in the first place*. Both are right, for different task classes. A protocol that lets the programmer **choose the scope of sharing per operation** is the natural resolution — and that is the communicator argument.

### 4.6 Cost, determinism, and fault tolerance

Cost is covered above: 15× chat tokens [anthropic-mas]; 5.83× tokens for 1.13× speedup on serial tasks [lm-teams-ds]; broadcast group chats grow context quadratically in participants × rounds. One 2026 preprint makes the cost argument architecturally: *Token Coherence* (arXiv:2603.15183) maps MESI cache-coherence onto multi-agent artifact synchronization, formalizes conditional artifact access (tool calls, MCP resources, vector stores, file search) to rebut the objection that LLM agents always embed full context, verifies invariants in TLA+ (single-writer, monotonic versioning, bounded staleness), and simulates **token savings of 95.0% ± 1.3% at write-fraction V=0.05 down to 84.2% ± 1.3% at V=0.50**, with ~81% persisting at V=0.9 [token-coherence]. Treat this as a preprint of unknown provenance — the numbers are simulated, not measured on real systems — but it is direct evidence that the community is independently reaching for classical systems abstractions to fix broadcast cost.

**Determinism/reproducibility** is a chronic problem: LangGraph time travel explicitly warns that replay *re-executes* nodes, so LLM calls and API requests fire again and may return different results [lg-timetravel]; interrupts always re-trigger. Temporal-style replay requires side-effect-free determinism, which LLM calls violate unless recorded. This is why the strongest fault-tolerance stories come from durable-execution systems rather than agent frameworks:

- **Temporal:** externalized workflow service; recovery by **replay against a persisted event history**; scales to polyglot fleets, multi-tenant, multi-region; costs an operational cluster (server + Cassandra/Postgres/MySQL) and tens-to-hundreds of ms per step [dbos-temporal-docs, dreaming-dbos].
- **DBOS:** a **library**, not a server. `@DBOS.workflow()` / `@DBOS.step()`; each step's output checkpointed with a **single Postgres write (~1–2 ms)**; on restart a background thread finds PENDING workflows and resumes from the last completed step; per-step declarative retry policy; durable queues with global/per-worker/per-tenant flow control [dbos-temporal-docs, alatirok-durable].
- **Restate:** journal-based, per-invocation append-only journal, single self-contained binary [alatirok-durable].
- **LangGraph** now documents fault tolerance explicitly (retries, timeouts, error handlers) [lc-fault-tolerance] `[UNVERIFIED: post title and June 2026 date observed in LangChain's related-content sidebar; I did not read the post.]`

No agent protocol defines fault semantics. A2A leaves retry and circuit-breaking to the client [tyk-a2a]. MCP's Tasks extension gives durable handles for long operations but says nothing about participant failure [mcp-spec-2026]. There is no agent analogue of MPI's ULFM (`MPI_Comm_shrink`, `MPI_Comm_agree`) — no way to declare a participant dead, rebuild the group, and continue. **This is the sharpest single gap in the entire landscape.**

---

## 5. Systems-flavored infrastructure

**Serving and scheduling.** **Parrot** (OSDI'24) introduced the **Semantic Variable**: an annotation on an input/output variable in a prompt that creates a data pipeline when it connects multiple LLM requests, exposing application-level dataflow to a public LLM service so it can do conventional dataflow analysis across requests; reports up to an order-of-magnitude end-to-end improvement [parrot]. **Autellix** (arXiv:2502.13965) treats *programs* as first-class scheduling entities, diagnosing head-of-line blocking at both the request and program level; its PLAS/ATLAS schedulers prioritize by a program's previously completed calls and its load balancer respects KV-cache locality by pinning long calls to their program's engine; **4–15× throughput at equal latency vs. vLLM** [autellix]. **Teola** (arXiv:2407.00326) optimizes whole LLM *applications* end-to-end via fine-grained task primitives and topology-aware batching, covering non-LLM components too [teola]. **Ayo, Hermes, Murakkab, ALTO, LLMCompiler, and parallel function calling** occupy adjacent points in this design space `[UNVERIFIED: I did not fetch primary records for Ayo, Hermes, Murakkab, ALTO, or LLMCompiler in this survey; verify before citing.]`

The important observation for AgentMPI: **this entire literature optimizes *below* the semantics.** Parrot and Autellix recover dependency structure that the application already knew but had no way to express. A protocol that names groups and collectives would hand these schedulers the information they currently reverse-engineer. That is a genuine, defensible systems contribution and the paper should lead with it in the evaluation section.

**Agent OS.** **AIOS** (arXiv:2403.16971) isolates LLM-specific services into a kernel providing scheduling, context management, memory, storage, tool management, and access control; agent queries decompose into **AIOS syscalls**; the context manager supports **snapshot and restoration** for LLM context switching; AIOS 0.2 splits kernel from SDK so the kernel can run as a remote RPC service [aios, agent-os-blog]. **MemGPT / Letta** (arXiv:2310.08560) is the canonical virtual-memory analogy: context window as physical memory, external store as disk, function calls as page-in/page-out, with the agent managing its own paging [memgpt]. Both are *single-agent* memory-hierarchy stories. Neither has a notion of inter-agent communication.

**Has anyone already built MPI for agents?** I searched this hard. Findings:

1. **No peer-reviewed paper proposes an MPI-style programming model for LLM agents.** The nearest academic work is [lm-teams-ds], which is an *analytical framework and empirical study* arguing distributed-systems theory should ground LLM team design — it explicitly does **not** propose an API, a protocol, or primitives.
2. **The MPI vocabulary is being reached for informally.** A production-patterns blog maps agent coordination onto MPI concepts by name: "Process Group ≈ MPI communicator / Gloo process group," "TupleSpace ≈ MPI ghost-cell exchange, barrier sync," with shard-group scatter-gather for parallel RAG [plexspaces]. An open-source project (`fak`) has an explicit design epic titled *"MPI-shaped message-passing primitives for fak agent fleets,"* proposing communicator (rank/size/split), non-blocking submit/wait, deterministic reduce, one-sided Put/Get/Accumulate, spawn membership, and **shrink/agree fault tolerance** — with every collective expanding into N independently adjudicated submissions [fak-epic]. This is a GitHub issue in a niche project, not published work, but it is direct evidence of convergent design pressure and should be cited honestly as such.
3. **Message-passing-shaped topology work exists but is a different thing.** **MPAS** (AAAI) generalizes GNN message propagation to agent graphs, replacing DAG-with-topological-sort execution with node-wise parallel propagation and three self-driven aggregators; reports better algorithms in **93.8%** of evaluations, average communication time **84.6s → 14.2s per round on AQuA**, and improved backdoor resilience in **94.4%** of tests [mpas]. **MOC** (arXiv:2606.02359) exposes agents to raw upstream responses at multiple hop distances (MixHop-inspired) with a semantic-topological consolidation operator to fit context budgets [moc]. These optimize *what flows over a fixed graph*; they do not give the programmer primitives.
4. **Collective communication for LLMs means GPUs, not agents.** arXiv:2608.15118 is a tutorial-style taxonomy of AllReduce/ReduceScatter/AllGather/AllToAll for distributed LLM *training and serving* [coll-comm-survey]; SiFAR and near-SoL collectives work are kernel-level. **This is important for the paper's positioning: the phrase "collective communication for LLMs" is currently occupied by GPU-interconnect work, and AgentMPI must disambiguate in its title and abstract.**
5. **Synchronization primitives for agents exist in exactly one place I found:** SyncPlan's `Wait_agents` + wait-graph cycle detection [syncplan]. Domain-specific, not portable.
6. **Agentic HPC orchestration is the inverse problem** — agents *driving* HPC workloads [hpc-agentic, hpc-hier], not HPC abstractions structuring agents.

**Conclusion: the MPI-for-agents design point is unoccupied in the peer-reviewed literature.** It is being groped toward from three directions (distributed-systems analysis, GNN-style topology optimization, ad-hoc engineering practice) without anyone specifying a programming model.

---

## 6. Benchmarks and evaluation

### 6.1 Agentic benchmarks

Current-generation harness benchmarks worth citing: **SWE-bench / SWE-bench Verified** (500 hand-curated Python PRs; now heavily contaminated), **SWE-bench Pro** (1,865 tasks including private repos; contamination-resistant), **SWE-rebench** (freshly mined post-cutoff issues), **Terminal-Bench 2.0** (89 multi-step Docker terminal tasks), **GAIA**, **WebArena**, **τ-bench / τ²-bench**, **AgentBench**, **MLE-bench**, **OSWorld**, **BrowseComp**, **GDPval**. As of mid-2026 several are saturating: τ²-Bench Telecom is at 99.3%, HumanEval is retired as a differentiator, and SWE-bench Verified leaders are in the low-to-mid 90s [codersera-bench, benchlm-agentic] `[UNVERIFIED: leaderboard aggregator content; specific model names and scores from these sources should be re-verified against primary reports before publication, and several model names in them are unfamiliar.]`

The methodologically important finding from the aggregators is the **scaffold gap**: on GAIA, a bare-model leader scores ~44.8% while a scaffolded system scores ~74.6% — a ~30-point gap attributable entirely to harness design [codersera-bench] `[UNVERIFIED — same caveat]`. If defensible, this is the single best justification for a paper about harness *construction* rather than model capability.

**Gaia2 / ARE** (arXiv:2602.11964) is the most relevant new benchmark: event-based, time-driven environments running **asynchronously** from the agent and the user, with splits for Ambiguity, Adaptability, **Time**, and **Noise**. Reported: GPT-5 (high) leads overall at **42.1%**; noise robustness lags with most models below 20 (GPT-5 high reaching 35.4%); only Gemini 2.5 Pro and Claude Sonnet achieve meaningful Time-split scores; and notably **"Agent2Agent collaboration benefits weaker models more than frontier systems"** [gaia2]. An asynchronous, time-sensitive benchmark is the right venue for a synchronization-primitives paper.

**Multi-agent-specific:** **MultiAgentBench / MARBLE** (ACL 2025; arXiv:2503.01935) is the one benchmark that varies *coordination topology* as an independent variable — star, chain, tree, and graph protocols, plus group discussion and cognitive planning strategies — scored with milestone-based KPIs plus separate Planning and Communication scores and a competition score. Findings: **graph topology performs best among coordination protocols in the research scenario**, and **cognitive planning improves milestone achievement by 3%** [multiagentbench]. **This is the natural baseline harness for AgentMPI's evaluation**, because its metrics already separate coordination quality from task success. Also relevant: **TheAgentCompany**, **CollabLLM**, **SWE-Lancer**, **SWE-bench Multimodal** `[UNVERIFIED: not surveyed in detail here.]`

### 6.2 Translation evaluation

For the paper's translation case study:

- **String-overlap metrics** — BLEU (Papineni et al., 2002), chrF (Popović, 2015), TER (Snover et al., 2006), and **d-BLEU** for documents. They "often poorly correlate with human judgment... especially in docMT, where maintaining coherence and logical flow across a document is essential — something n-gram overlap struggles to capture" [docmt-metrics].
- **Learned metrics** — COMET (Rei et al., 2020), COMET-Kiwi (reference-free), MetricX, BLEURT, BERTScore. The critical caveat for a document-level paper: **COMET is trained exclusively on sentence-level data, so applying it to docMT is out-of-distribution and unreliable** [docmt-metrics].
- **Discourse-specific metrics** — BlonDe, cTT (terminology consistency), aZPT (zero-pronoun accuracy). All still rest on lexical alignment and assume that the presence of specific terminology correlates with quality, which limits them [docmt-metrics].
- **MetaDocEval** (EAMT 2026) is the best recent meta-evaluation and its conclusion is blunt: **"no current metric genuinely captures document-level coherence"** — reference-based metrics overfit lexical overlap, reference+source metrics gain little from added context, reference-free encoders show brief context sensitivity before degrading on longer spans, **LLM-based scorers collapse beyond short inputs**, and **reference access can be actively harmful for detecting discourse-level errors**. Practical recommendation: **~3-sentence sliding windows** best trade off discourse-error detection against score dilution [metadoceval]. Adopt this protocol.
- **LLM-as-a-judge** (GEMBA-style, G-Eval rubrics) is the current best option for nuanced/contextual/style-guide criteria but should be combined with COMET/MetricX/chrF rather than used alone; human judgment remains the reference standard [translated-mtqe, docmt-metrics].

**TransAgents is the direct multi-agent baseline and must be cited.** Wu, Yuan, Haffari, Wang, *"(Perhaps) Beyond Human Translation: Harnessing Multi-Agent Collaboration for Translating Ultra-Long Literary Texts"* — arXiv:2405.11804, published in **TACL 2025** (2025.tacl-1.42), with a system demo at **EMNLP 2024** (2024.emnlp-demo.14). A simulated translation company: CEO, Senior Editor, Junior Editor, Translator, Localization Specialist, Proofreader; a preparation stage (assemble team, draft translation guidelines) and an execution stage (chapter-wise translation → localization → proofreading → final QC), the first three run as **Trilateral Collaboration** (Action / Critique / Judgment) plus **Addition-by-Subtraction Collaboration**. Two new evaluation protocols: **MHP** (Monolingual Human Preference, target-language quality and cultural appropriateness only) and **BLP** (Bilingual LLM Preference, GPT-4 direct comparison). Headline result and the reason it matters: **TransAgents achieves the *lowest* d-BLEU of all systems while being significantly preferred by both human evaluators and the LLM judge over GPT-4 translations and over human reference translations**, and significantly outperforms SOTA MT on GEMBA-DA. The authors attribute the d-BLEU collapse to limited reference diversity. The demo paper additionally claims translation **~80× cheaper than professional human translation services**, with wins on literary, legal, and financial test sets [transagents-tacl, transagents-demo]. Caveat to state: an independent reader notes the original work is **not reproducible — code and prompts were not released** [gonzoml-transagents].

TransAgents is the perfect foil for AgentMPI: it is a *hand-built, non-reproducible, role-hardcoded pipeline* whose entire contribution is a coordination structure. If AgentMPI can express TransAgents in a few dozen lines of portable primitives, reproduce its quality, and then vary the topology as a controlled experiment, that is the paper's strongest demonstration.

---

## The gap

**What does not exist today, stated precisely:**

1. **No communicator.** No framework or protocol has a first-class, named, addressable *group* with defined membership. A2A's `contextId` correlates related tasks; it does not enumerate participants. AutoGen's roster is a config field, not an object you can split, sub-scope, or pass. There is no way to say "these five agents form a group; within it, agent 3 is rank 3; here is a sub-communicator over ranks 0–2." Consequently there is no way to scope a message to a subset without hand-rolling routing logic, and no way to reason about who has seen what.
2. **No collectives.** Broadcast, scatter, gather, allgather, reduce, allreduce, and barrier exist nowhere as first-class operations. Every framework offers fan-out plus a hand-written aggregation node. That means (a) every developer re-implements reduction and gets the failure semantics wrong, (b) the runtime cannot see that a fan-out/converge pair is a reduce and therefore cannot schedule, batch, or cache it as one, and (c) there is no shared vocabulary for describing agent topologies in papers — which is exactly why MultiAgentBench had to invent "star/chain/tree/graph" ad hoc [multiagentbench].
3. **No barrier, hence no agreed termination.** MAST's FM-1.5 (unaware of termination conditions, 9.82%) and FM-3.1 (premature termination, 7.82%) are the same bug seen from both sides: the system has no way to establish that all participants agree the phase is done [mast]. Magentic-One's progress ledger and AutoGen's max-rounds are heuristics. SyncPlan's wait-graph cycle detection is the only real synchronization mechanism in the literature and it is domain-specific [syncplan].
4. **No fault model.** A2A explicitly punts retry and circuit-breaking to the client [tyk-a2a]. No protocol defines participant failure, failure detection, group repair, or consistent rollback. There is no agent analogue of ULFM's `MPI_Comm_shrink`/`MPI_Comm_agree`. Durable-execution systems (Temporal, DBOS, Restate) provide crash recovery for a *workflow*, but a workflow is a single logical thread of control — they do not model a peer group in which one member dies and the rest must agree to continue [dbos-temporal-docs, alatirok-durable].
5. **No shared-state primitive with defined semantics.** LangGraph's reducer-merged `State` is the closest thing, and it has no ownership model, no versioning, no bounded-staleness guarantee, and no conflict resolution beyond "the reducer wins." [lm-teams-ds] measured what this costs: concurrent writes, silent overwrites, and out-of-order implementation, producing a **median 19 vs. 4 failed tests** between decentralized and preassigned teams. The Linda tuple space solved the associative-shared-memory problem in 1985 and the agent world has not rediscovered it.
6. **Context scoping is a convention, not a guarantee.** Sub-agent context isolation is now the consensus mitigation for context rot [anthropic-mas, cognition-working, chroma-rot], but no system *guarantees* it. OpenAI's `nest_handoff_history` performs lossy summarization at the transport layer with no receiver-side negotiation [openai-running-agents]. MPI's private address space per rank makes isolation the default and sharing explicit; agent frameworks make sharing the default and isolation a prompt-engineering discipline.
7. **The runtime cannot see the program's communication structure.** Parrot had to invent Semantic Variables to recover dataflow the application already knew [parrot]; Autellix had to intercept calls to reconstruct program dependencies [autellix]. A declarative communication layer would give schedulers this for free.

**The strongest counterarguments a reviewer will make — and the honest responses:**

**(a) "Mieczkowski et al. already did this."** [lm-teams-ds] is the closest work and it is strong: it establishes the four-property correspondence, validates Amdahl's Law as a bound on LLM team speedup, and quantifies consistency conflicts, communication overhead, stragglers, and cost. But it is *analysis*, not *artifact*. It produces predictions and design guidelines, not primitives a developer can call. AgentMPI must cite it as motivation and be explicit that it is the empirical foundation the paper builds an API on top of. If AgentMPI's evaluation does not measurably improve on the numbers in that paper (median speedup 1.36× preassigned / 0.88× decentralized; median 19 vs. 4 test failures; the 5.83× tokens per 1.13× speedup on serial tasks), the paper has no contribution.

**(b) "MPI is the wrong analogy — agents are not SPMD, messages are natural language, failures are semantic not fail-stop."** [lm-teams-ds] itself names these disanalogies. This is the most serious objection and requires a real answer, not a hand-wave. Reasonable position: AgentMPI borrows MPI's *naming and scoping discipline* (communicators, ranks, typed collectives, explicit synchronization, group repair) without its *execution model* (SPMD, symmetric progress, deterministic message ordering, fail-stop faults). Say this in the introduction, not the limitations section. Also concede that MPI's own history is a warning: the parts of MPI-3 that generalized furthest from the original model (one-sided RMA, dynamic process management) are the least used in practice.

**(c) "FIPA-ACL already did this and it failed."** True, and [woa-survey] and [layered-ioa] document exactly why: heavyweight formal ontologies imposed prohibitive overhead, and the `:ontology` slot was a label with no negotiation mechanism [layered-ioa]. The counter is that AgentMPI specifies *coordination structure*, not *message semantics* — MPI never told you what was in the buffer. Whether that distinction survives contact with LLM agents, whose messages are natural language and whose "buffer contents" are exactly the ambiguity that killed FIPA, is a genuine open risk. Address it head-on.

**(d) "Cognition says arbitrary agent negotiation is a distraction; the practical shape is map-reduce-and-manage."** [cognition-working]. This is a gift and a constraint. Map-reduce-and-manage *is* scatter–compute–gather. But it means the paper must not sell a general mesh. Scope the contribution to the collectives that practitioners have converged on, and treat point-to-point as the escape hatch rather than the headline.

**(e) "Models will absorb this; it's a training problem, not a protocol problem."** Cognition says explicitly that cross-agent communication "doesn't happen by default, because models haven't been trained in environments where it needed to," and that they expect the next generation of models to close these gaps [cognition-working]. Anthropic says LLM agents "are not yet great at coordinating and delegating to other agents in real time" [anthropic-mas] — *yet*. Best response: protocols outlive models; the value of `MPI_Barrier` is not that CPUs cannot synchronize but that a portable name for the operation lets a program outlive the machine. Also note that better models will *raise* the payoff to explicit structure by making agents better at using it, and that [diversity-collapse] shows a failure mode caused by interaction structure that *stronger, more-aligned models make worse*, not better.

**(f) "Multi-agent underperforms single-agent, so why build better multi-agent tooling?"** The evidence is genuinely mixed: +90.2% for Anthropic's parallel research [anthropic-mas] vs. 0.88× median speedup for decentralized coding teams [lm-teams-ds]. The honest position is that the *conditions* under which multi-agent wins are known — heavy parallelization, information exceeding one context window, many complex tools [anthropic-mas] — and the paper's contribution is making those conditions expressible and cheap rather than arguing multi-agent always wins.

**(g) "Adding a synchronization layer will just add overhead."** SyncPlan is the counterexample to have ready: explicit wait primitives plus deadlock detection achieved SOTA success at **<0.05% of the wall-clock runtime** of LLM-coordinator baselines, because the alternative to explicit synchronization is repeated LLM invocation [syncplan].

---

## Numbers I can cite

**Failure taxonomy (MAST, arXiv:2503.13657)** — 7 frameworks, 200 traces (~15k lines each), 6 annotators, **κ = 0.88**; v2 dataset 1,600+ traces. Category shares: **FC1 specification 41.77%, FC2 inter-agent misalignment 36.94%, FC3 verification 21.30%**. Top modes: **step repetition 17.14%**, reasoning–action mismatch **13.98%**, fail-to-ask-clarification **11.65%**, disobey task spec **10.98%**, unaware of termination **9.82%**, premature termination **7.82%**, task derailment **7.15%**, no/incomplete verification **6.82%**, incorrect verification **6.66%**, loss of conversation history **3.33%**, conversation reset **2.33%**, information withholding **1.66%**, disobey role spec **0.50%**, ignored other agent's input **0.17%**. Verification failures combined **13.48%**. Targeted fixes yielded **+15.6% on ChatDev** and were judged insufficient.

**Multi-agent wins (Anthropic)** — **+90.2%** over single-agent Claude Opus 4 on internal research eval; **~90% reduction in research time** on complex queries `[the time figure is from a secondary summary of the post]`; on BrowseComp three factors explain **95%** of variance with **token usage alone explaining 80%**; agents use **~4×** chat tokens, multi-agent **~15×** chat tokens.

**Multi-agent losses (Mieczkowski et al., arXiv:2603.12229)** — median speedup **1.36× preassigned vs. 0.88× decentralized** (U=155523, p<0.001); parallel-task speedup **2.19× median, significantly below the Amdahl bound**; median failed tests **19 decentralized vs. 4 preassigned** (U=287013, p<0.001); significantly more messages (U=311551, p<0.001) rising with team size (r=0.483); significantly more idle rounds (U=289672, p<0.001); straggler gap median **2.64s preassigned vs. 1.42s decentralized** (U=8889359, p<0.001), and **3.91s mixed vs. 1.73s parallel**; serial preassigned tasks: **5.83× token multiplier for 1.13× speedup** (p<0.001); decentralized token cost scales with team size (ρ=0.40, p<0.001) while speedup does not (ρ=−0.07, p=0.15).

**Architecture benchmarking (LangChain, modified τ-bench + 6 distractor domains × 19 tools, gpt-4o)** — single agent degrades sharply at ≥2 distractor domains; supervisor and swarm stay flat in score and cost; swarm > supervisor throughout; **~50% relative improvement** to supervisor from removing handoff messages from sub-agent context, adding a verbatim `forward_message` tool, and tuning the handoff tool name.

**Multi-turn degradation (Laban et al., arXiv:2505.06120, ICLR 2026)** — 15 LLMs, 200,000+ simulated conversations, 6 tasks: **−39% average** single-turn → multi-turn underspecified; **aptitude −15/16%**, **unreliability +112%**; **~50 percentage points** spread between best and worst run for a fixed instruction.

**Clinical multi-turn (arXiv:2603.11394)** — "conversation tax" of up to **30% average**, **65% worst-case**, on end-to-end accuracy and abstention; below single-shot baseline for **14/17 (MedQA)**, **14/17 (JAMA CC)**, **16/17 (MedMCQA)** models; blind switching near **50%**.

**Context rot (Chroma, 2025)** — 18 models; degradation with input length even on trivial tasks and before the window fills; worse with low needle–question similarity and with distractors; LongMemEval: **worse with full history than with relevant excerpts only**.

**Error cascades** — single atomic error seed → widespread failure; genealogy governance layer **prevents final infection in ≥89% of runs** (arXiv:2603.04474). Counter-evidence: 3-agent chains **reduce** normalized hallucination **0.422 → 0.272** (amplification factor **0.644**, net attenuation) while **factual accuracy falls 0.789 → 0.769**, ~0.072 hallucination reduction per hop (arXiv:2606.07937). Propagation-aware UQ gains **+6.10% AUROC / +47.58% PRR** (arXiv:2608.22130). **17.2× error amplification in unstructured multi-agent networks** attributed to Kim et al. 2025 `[UNVERIFIED — secondhand via QUIVER]`.

**Groupthink / diversity** — context sharing costs **5.9%** semantic diversity while isolation gains **10.3%** (p=0.017, d=0.79, n=20 tasks); under one protocol sharing cost **31.8%** of diversity, reduced to **5.5%** by one system-prompt line (p=0.0003, d=1.31) [algo-groupthink]. Role-differentiated expert configurations show the lowest diversity (Vendi **4.65**) [diversity-collapse]. Sycophancy priors improve final discussion accuracy by **+10.5% absolute** [too-polite].

**Cost/synchronization** — simulated token savings from lazy artifact invalidation: **95.0%±1.3% (V=0.05)**, **92.3%±1.4% (V=0.10)**, **88.3%±1.5% (V=0.25)**, **84.2%±1.3% (V=0.50)**, ~**81%** at V=0.9 (arXiv:2603.15183, simulation only).

**Synchronization payoff** — SyncPlan: SOTA task success at **<0.05% of the wall-clock runtime** of existing LLM-based coordinators (arXiv:2608.01652).

**Topology optimization** — MPAS: better algorithms in **93.8%** of evaluations, average communication time **84.6s → 14.2s per round** on AQuA, improved backdoor resilience in **94.4%** of tests. MultiAgentBench: graph topology best among star/chain/tree/graph in the research scenario; cognitive planning **+3%** milestone achievement.

**Serving infrastructure** — Autellix: **4–15× throughput** at equal latency vs. vLLM, up to **1.5×** across engines. Parrot: **up to an order of magnitude** end-to-end improvement.

**Durable execution** — DBOS step checkpoint = one Postgres write, **1–2 ms**; Temporal async dispatch adds **tens to hundreds of ms** per step.

**Cognition production data** — Devin Review catches **~2 bugs per Devin-authored PR**, **~58% severe**; enterprise Devin usage grew **~8×** over six months.

**Magentic-One (2024)** — **38% GAIA**, **27.7% AssistantBench**, **32.8% WebArena**.

**Gaia2 / ARE (arXiv:2602.11964)** — GPT-5 (high) overall **42.1%**; noise robustness mostly **<20** (GPT-5 high **35.4%**); Agent2Agent collaboration helps weaker models more than frontier ones.

**Translation (TransAgents, TACL 2025 / arXiv:2405.11804)** — lowest d-BLEU of all systems yet preferred by human evaluators and by an LLM judge over both GPT-4 and human reference translations; significantly outperforms SOTA MT on GEMBA-DA; demo paper claims **~80× cheaper** than professional human translation. Not reproducible (no code/prompts released) `[per an independent reader]`.

**Doc-level MT evaluation (MetaDocEval, EAMT 2026)** — no current metric captures document-level coherence; LLM-based scorers collapse beyond short inputs; reference access can be actively harmful for discourse errors; **~3-sentence windows** are the best trade-off.

**Scaffold gap** — GAIA bare model ~**44.8%** vs. scaffolded ~**74.6%**, a **~30-point** gap `[UNVERIFIED — leaderboard aggregator]`.

---

## References (BibTeX-ready)

Entries marked ⚠ were not retrieved from the primary record during this survey; verify before citing.

**Failure modes and empirical studies**

- `[mast]` Cemri, M., Pan, M. Z., Yang, S., Agrawal, L. A., Chopra, B., Tiwari, R., Keutzer, K., Parameswaran, A., Klein, D., Ramchandran, K., Zaharia, M., Gonzalez, J. E., Stoica, I. **Why Do Multi-Agent LLM Systems Fail?** arXiv:2503.13657, 2025 (v2). https://arxiv.org/abs/2503.13657 — code/data: https://github.com/multi-agent-systems-failure-taxonomy/MAST ; dataset `mcemri/MAD` on HuggingFace.
- `[mast-neurips]` Same authors. **Why Do Multi-Agent LLM Systems Fail?** NeurIPS 2025 poster #121528. https://neurips.cc/virtual/2025/poster/121528
- `[lm-teams-ds]` Mieczkowski, E., Collins, K. M., Sucholutsky, I., Vélez, N., Griffiths, T. L. **Language Model Teams as Distributed Systems.** arXiv:2603.12229, 2026. https://arxiv.org/abs/2603.12229 — code: https://github.com/emieczkowski/distributed-llm-teams
- `[laban-lost]` Laban, P., Hayashi, H., Zhou, Y., Neville, J. **LLMs Get Lost In Multi-Turn Conversation.** arXiv:2505.06120, 2025; ICLR 2026. https://arxiv.org/abs/2505.06120
- `[stop-listening]` **Stop Listening to Me! How Multi-turn Conversations Can Degrade LLM Reliability.** arXiv:2603.11394, 2026. https://arxiv.org/abs/2603.11394 ⚠ (authors not captured)
- `[chroma-rot]` Chroma Research. **Context Rot: How Increasing Input Tokens Impacts LLM Performance.** Technical report, July 2025. https://www.trychroma.com/research/context-rot — code: `chroma-core/context-rot`
- `[spark-to-fire]` Xie et al. **From Spark to Fire: Modeling and Mitigating Error Cascades in LLM-Based Multi-Agent Collaboration.** arXiv:2603.04474, 2026. https://arxiv.org/abs/2603.04474 ⚠ (author list from a citing paper)
- `[hallu-cascade]` **Hallucination Cascade: Analyzing Error Propagation in Multi-Agent LLM Systems.** arXiv:2606.07937, 2026. https://doi.org/10.48550/arxiv.2606.07937 ⚠ (authors not captured)
- `[propuq]` **PropUQ-MAS: Propagation-Aware Uncertainty Quantification for LLM Multi-Agent Systems.** arXiv:2608.22130, 2026. https://arxiv.org/abs/2608.22130 ⚠
- `[quiver]` **QUIVER: A Formal Framework for Quantifying Perturbation Propagation and Bifurcation in Compound AI Systems.** arXiv:2605.23956, 2026. https://doi.org/10.48550/arxiv.2605.23956 ⚠
- `[diversity-collapse]` **Diversity Collapse in Multi-Agent LLM Systems: Structural Coupling and Collective Failure in Open-Ended Idea Generation.** Findings of ACL 2026, pp. 13. DOI 10.18653/v1/2026.findings-acl.13 ; arXiv:2604.18005. Code: https://github.com/Xtra-Computing/MAS_Diversity
- `[algo-groupthink]` **Algorithmic Groupthink: Causal Analysis and Mitigation of Semantic Convergence in Multi-agent LLM Systems.** Research Square preprint, DOI 10.21203/rs.3.rs-10172697/v1, 2026. ⚠ (not peer-reviewed)
- `[too-polite]` **Too Polite to Disagree: Understanding Sycophancy Propagation in Multi-Agent Systems.** arXiv:2604.02668, 2026. https://arxiv.org/abs/2604.02668 ⚠
- `[token-coherence]` **Token Coherence: Adapting MESI Cache Protocols to Minimize Synchronization Overhead in Multi-Agent LLM Systems.** arXiv:2603.15183, 2026. ⚠ (single-author preprint, simulation only)

**Practitioner engineering reports**

- `[cognition-dont]` Yan, W. (Cognition). **Don't Build Multi-Agents.** 2025. https://cognition.com/blog/dont-build-multi-agents
- `[cognition-working]` Yan, W. (Cognition). **Multi-Agents: What's Actually Working.** 2026. https://cognition.com/blog/multi-agents-working
- `[anthropic-mas]` Anthropic. **How we built our multi-agent research system.** Engineering blog, 13 June 2025. https://www.anthropic.com/engineering/multi-agent-research-system
- `[anthropic-ctx-eng]` Anthropic. **Effective context engineering for AI agents.** Engineering blog. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- `[lc-benchmark]` Fu-Hinthorn, W. (LangChain). **Benchmarking Multi-Agent Architectures.** https://www.langchain.com/blog/benchmarking-multi-agent-architectures
- `[lc-multiagent-arch]` LangChain. **Choosing the Right Multi-Agent Architecture.** https://www.langchain.com/blog/choosing-the-right-multi-agent-architecture
- `[lc-fault-tolerance]` Long, Q., Runkle, S. (LangChain). **Fault Tolerance in LangGraph: Retries, Timeouts, and Error Handlers.** 4 June 2026. ⚠

**Protocols and specifications**

- `[mcp-spec-2026]` Model Context Protocol. **Specification, revision 2026-07-28.** https://modelcontextprotocol.io/specification/2026-07-28
- `[mcp-versioning]` Model Context Protocol. **Versioning.** https://modelcontextprotocol.io/specification/versioning
- `[mcp-sep2322]` Model Context Protocol. **SEP-2322: Multi Round-Trip Requests.** https://modelcontextprotocol.io/seps/2322-MRTR
- `[mcp-mrtr-doc]` Model Context Protocol. **Multi Round-Trip Requests (draft basic/patterns/mrtr).** https://modelcontextprotocol.io/specification/draft/basic/patterns/mrtr
- `[mcp-mrtr-blog]` **Designing requestState for Multi Round-Trip Requests.** https://aaif.io/blog/designing-requeststate-for-multi-round-trip-requests ⚠ (third-party)
- `[a2a-defs]` A2A Project (Linux Foundation). **Protocol Definition (v1.0 protobuf).** https://a2a-protocol.org/dev/definitions/
- `[a2a-life-of-task]` A2A Project. **Life of a Task.** https://a2a-protocol.org/dev/topics/life-of-a-task/
- `[a2a-rust-taskstate]` `a2a-protocol-sdk` Rust docs. **TaskState.** https://docs.rs/a2a-protocol-sdk/latest/a2a_protocol_sdk/types/task/enum.TaskState.html
- `[tyk-a2a]` Tyk. **A2A Protocol: Architecture and Technical Specification.** 2026. https://tyk.io/learning-center/a2a-protocol-architecture-and-technical-specification/ ⚠ (vendor learning center; accurate but secondary)
- `[mastra-a2a]` Mastra. **What is the Agent2Agent (A2A) protocol?** https://mastra.ai/blog/what-is-agent-to-agent-protocol ⚠ (vendor)
- `[ibm-protocols]` IBM. **What Are AI Agent Protocols?** https://www.ibm.com/think/topics/ai-agent-protocols
- `[anp-wp]` **Agent Network Protocol Technical White Paper.** arXiv:2508.00007, 2025. https://arxiv.org/abs/2508.00007
- `[anp-w3c]` W3C AI Agent Protocol Community Group. **Agent Network Protocol White Paper.** https://w3c-cg.github.io/ai-agent-protocol/
- `[coral]` **Coral Protocol: Open Infrastructure Connecting The Internet of Agents.** arXiv:2505.00749, 2025. https://arxiv.org/abs/2505.00749
- `[data443-acp-anp]` Data443. **ACP vs ANP: AI Agent Protocols Explained.** ⚠ (vendor blog)
- `[zylos-protocols]` Zylos Research. **The Protocol Layer: Comparing Communication Standards for AI Agent Interoperability.** 5 March 2026. ⚠
- `[awesome-protocols]` `insodimension/awesome-agent-protocols` — curated list (MCP, A2A, ACP, AG-UI, AP2, x402, DNS-AID, AgentDNS, AITP, Coral, OASF, agents.json, +50). ⚠ (community list; use only for pointers)
- `[agents-txt-spec]` **agents.txt / agents.json specification.** https://agents-txt.com/spec ⚠

**Protocol surveys and history**

- `[ehtesham-survey]` Ehtesham, A., et al. **A Survey of Agent Interoperability Protocols: Model Context Protocol (MCP), Agent Communication Protocol (ACP), Agent-to-Agent Protocol (A2A), and Agent Network Protocol (ANP).** arXiv:2505.02279, 2025. https://arxiv.org/abs/2505.02279
- `[yang-survey]` Yang, et al. **A Survey of AI Agent Protocols.** arXiv:2504.16736, 2025 (v3). https://arxiv.org/abs/2504.16736
- `[tech-taxonomy]` **A Technical Taxonomy of LLM Agent Communication Protocols.** arXiv:2606.19135, 2026. https://arxiv.org/abs/2606.19135 ⚠
- `[layered-ioa]` **A Layered Protocol Architecture for the Internet of Agents.** arXiv:2511.19699, 2025. https://arxiv.org/abs/2511.19699 ⚠ (authors not captured)
- `[lacp]` **LLM Agent Communication Protocol (LACP) Requires Urgent Standardization: A Telecom-Inspired Protocol is Necessary.** arXiv:2510.13821, 2025. https://arxiv.org/abs/2510.13821 ⚠
- `[woa-survey]` **From Multi-Agent Systems and the Semantic Web to Agentic AI: A Unified Narrative of the Web of Agents.** arXiv:2507.10644, 2025 (v4, covering 1990–2026). https://arxiv.org/abs/2507.10644 ⚠ (authors not captured)
- `[scalable-book]` Apartsin, A. **Scaling Out AI**, §29.4 and §32.6 (agent communication languages; MCP and A2A). https://scalablebook.apartsin.com/ ⚠ (online book)

**Classical agent communication (cite from primary sources; not re-verified in this survey)** ⚠

- `[austin62]` Austin, J. L. *How to Do Things with Words.* Harvard University Press, 1962.
- `[searle69]` Searle, J. R. *Speech Acts: An Essay in the Philosophy of Language.* Cambridge University Press, 1969.
- `[kqml]` Finin, T., Fritzson, R., McKay, D., McEntire, R. **KQML as an Agent Communication Language.** CIKM 1994.
- `[fipa-acl]` FIPA. **FIPA ACL Message Structure Specification.** SC00061G, 2002.
- `[contractnet]` Smith, R. G. **The Contract Net Protocol: High-Level Communication and Control in a Distributed Problem Solver.** *IEEE Transactions on Computers* C-29(12):1104–1113, 1980.
- `[hearsay]` Erman, L. D., Hayes-Roth, F., Lesser, V. R., Reddy, D. R. **The Hearsay-II Speech-Understanding System: Integrating Knowledge to Resolve Uncertainty.** *ACM Computing Surveys* 12(2):213–253, 1980.
- `[linda]` Gelernter, D. **Generative Communication in Linda.** *ACM TOPLAS* 7(1):80–112, 1985.
- `[jade]` Bellifemine, F., Poggi, A., Rimassa, G. **JADE — A FIPA-compliant Agent Framework.** PAAM 1999.

**Frameworks**

- `[autogen]` Wu, Q., Bansal, G., Zhang, J., Wu, Y., Li, B., Zhu, E., Jiang, L., Zhang, X., Zhang, S., Liu, J., Awadallah, A. H., White, R. W., Burger, D., Wang, C. **AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation.** arXiv:2308.08155, 2023. ⚠ (not re-verified)
- `[ms-af-launch]` Microsoft. **Introducing Microsoft Agent Framework: The Open-Source Engine for Agentic AI Apps.** Foundry Blog, 1 October 2025. https://devblogs.microsoft.com/foundry/introducing-microsoft-agent-framework-the-open-source-engine-for-agentic-ai-apps/
- `[ms-af-10]` Microsoft. **Microsoft Agent Framework Version 1.0.** DevBlogs, 2 April 2026. https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/
- `[infoq-af-ga]` InfoQ. **Microsoft Agent Framework Harness and Hosted Agents Reach General Availability.** August 2026. https://www.infoq.com/news/2026/08/agent-framework-harness-ga/
- `[lg-checkpointers]` LangChain. **LangGraph: Checkpointers.** https://docs.langchain.com/oss/python/langgraph/checkpointers
- `[lg-timetravel]` LangChain. **LangGraph: Use time-travel.** https://docs.langchain.com/oss/python/langgraph/use-time-travel
- `[lg-interrupt]` LangChain. **Making it easier to build human-in-the-loop agents with interrupt.** https://www.langchain.com/blog/making-it-easier-to-build-human-in-the-loop-agents-with-interrupt
- `[lg-hitl]` LangGraph repo. `docs/docs/concepts/human_in_the_loop.md`.
- `[openai-agents-docs]` OpenAI. **Agents SDK guide.** https://developers.openai.com/api/docs/guides/agents
- `[openai-running-agents]` OpenAI Agents SDK. **Running agents** (`RunConfig`, `nest_handoff_history`, `handoff_input_filter`, `handoff_history_mapper`).
- `[morph-frameworks]` Morph. **AI Agent Frameworks (2026 Update): 8 SDKs Compared + the Claude Agent SDK Primitive Reference.** https://www.morphllm.com/ai-agent-framework ⚠
- `[composio-cmp]` Composio. **Claude Agent SDK vs OpenAI Agents SDK vs Google ADK.** ⚠
- `[uvik-frameworks]` Uvik. **Agentic AI Frameworks 2026: Production Comparison.** ⚠
- `[sideguy-cmp]` SideGuy Solutions. **AI Agent Frameworks · Multi-Agent Orchestration Comparison (2026).** ⚠
- `[crewai-flows-cmp]` **CrewAI Flows vs LlamaIndex Workflows.** dreaming.press, 2026 (CrewAI 1.15.0, 25 June 2026; LlamaIndex Workflows 1.0, 22 June 2026). ⚠
- `[ctaio-6frameworks]` **I Built the Same Agent in 6 Orchestration Frameworks.** ctaio.dev. ⚠

**Compilers / optimizers**

- `[dspy-gepa]` DSPy. **GEPA Overview** and **GEPA in depth.** https://dspy.ai/api/optimizers/GEPA/overview/ — cites Agrawal et al., **GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning**, arXiv:2507.19457, 2025. ⚠ (arXiv record not fetched)
- `[dspy-optimizers]` DSPy. **Optimizers: choosing one.** https://dspy.ai/diving-deeper/choosing-an-optimizer/
- `[gepa-repo]` `gepa-ai/gepa`. https://github.com/gepa-ai/gepa
- `[aicraft-dspy]` **DSPy vs TextGrad vs GEPA: Prompt Optimization 2026.** ⚠ — attributes TextGrad to Yuksekgonul et al., *Nature* 639:609–616, 2025. Verify independently.

**Systems / runtimes**

- `[parrot]` Lin, C., Han, Z., Zhang, C., Yang, Y., Yang, F., Chen, C., Qiu, L. **Parrot: Efficient Serving of LLM-based Applications with Semantic Variable.** OSDI '24, pp. 929–945. https://www.usenix.org/conference/osdi24/presentation/lin-chaofan
- `[autellix]` **Autellix: An Efficient Serving Engine for LLM Agents as General Programs.** arXiv:2502.13965, 2025. https://arxiv.org/abs/2502.13965
- `[teola]` **Teola: Towards End-to-End Optimization of LLM-based Applications.** arXiv:2407.00326, 2024. https://arxiv.org/abs/2407.00326
- `[aios]` Mei, K., et al. **AIOS: LLM Agent Operating System.** arXiv:2403.16971, 2024. https://arxiv.org/abs/2403.16971 — code: https://github.com/agiresearch/AIOS
- `[memgpt]` Packer, C., et al. **MemGPT: Towards LLMs as Operating Systems.** arXiv:2310.08560, 2023. https://arxiv.org/abs/2310.08560 (now Letta)
- `[agent-os-blog]` **Agent OS: Autonomous Agent Architecture.** jacar.es. ⚠
- `[dbos-temporal-docs]` DBOS. **Comparing DBOS and Temporal.** https://docs.dbos.dev/explanations/comparing-temporal ⚠ (vendor)
- `[alatirok-durable]` **Durable Execution for AI Agents: Temporal vs Restate vs DBOS.** alatirok.com. ⚠
- `[dreaming-dbos]` **DBOS vs Temporal for Durable Agents.** dreaming.press. ⚠

**Message passing / collectives / synchronization for agents**

- `[syncplan]` Wang, L., Ji, J., Wei, Y., et al. **SyncPlan: Long-Horizon LLM Coordination with Explicit Synchronization and Adaptive Correction.** arXiv:2608.01652, 2026. https://arxiv.org/abs/2608.01652
- `[mpas]` Xuan, R., et al. **MPAS: Breaking Sequential Constraints of Multi-Agent Communication Topologies via Individual-Epistemic Message Propagation.** AAAI (OJS article 40231). Code: https://github.com/rkxuan/MPAS ⚠ (volume/year not captured)
- `[moc]` **MOC: Multi-Order Communication in LLM-based Multi-Agent Systems.** arXiv:2606.02359, 2026. https://arxiv.org/abs/2606.02359 ⚠
- `[coll-comm-survey]` **Collective Communication for Distributed LLM Systems: Planning, Runtime Adaptation, and Computation Coordination.** arXiv:2608.15118, 2026. https://arxiv.org/abs/2608.15118 ⚠ — GPU collectives, not agents.
- `[mpi41]` MPI Forum. **MPI: A Message-Passing Interface Standard, Version 4.1.** 2023. https://www.mpi-forum.org/docs/mpi-4.1/
- `[plexspaces]` Bhatti, S. **20+ Production Patterns for Distributed AI Agents Using Actors and TupleSpaces.** weblog.plexobject.com. ⚠ (blog; explicitly maps agent primitives to MPI communicators and tuple spaces)
- `[fak-epic]` `anthony-chaudhary/fak` issue #639. **epic(comm): MPI-shaped message-passing primitives for fak agent fleets.** ⚠ (open-source design issue, not published work; nearest existing artifact to AgentMPI's design point)
- `[hpc-agentic]` **Agentic Orchestration of HPC Applications in Cloud.** arXiv:2607.02925, 2026. ⚠
- `[hpc-hier]` Sochat, V. **Hierarchical Server Architecture for Agentic Science.** arXiv:2608.05332, 2026. ⚠

**Benchmarks**

- `[multiagentbench]` Zhu, K., et al. **MultiAgentBench: Evaluating the Collaboration and Competition of LLM Agents.** ACL 2025 (2025.acl-long.421); arXiv:2503.01935. Code: https://github.com/MultiagentBench/MARBLE (also `ulab-uiuc/MARBLE`)
- `[gaia2]` **Gaia2: Benchmarking LLM Agents on Dynamic and Asynchronous Environments.** arXiv:2602.11964, 2026. ⚠
- `[codersera-bench]` Codersera. **AI Agent Benchmarks 2026: Who Leads SWE-bench & GAIA.** May 2026. ⚠ (aggregator; re-verify every number)
- `[benchlm-agentic]` BenchLM.ai. **Best LLMs for Agentic — August 2026 Leaderboard.** ⚠ (aggregator)
- Also to cite from primary sources, not surveyed here ⚠: SWE-bench (Jimenez et al., ICLR 2024), SWE-bench Verified (OpenAI, 2024), SWE-bench Multimodal, SWE-Lancer (OpenAI, 2025), Terminal-Bench, GAIA (Mialon et al., 2023), AgentBench (Liu et al., 2023), τ-bench (Yao et al., 2024), τ²-bench (Barres et al., 2025), MLE-bench, WebArena (Zhou et al., 2024), AppWorld (Trivedi et al., 2024), TheAgentCompany, CollabLLM.

**Translation**

- `[transagents-tacl]` Wu, M., Yuan, Y., Haffari, G., Wang, L. **(Perhaps) Beyond Human Translation: Harnessing Multi-Agent Collaboration for Translating Ultra-Long Literary Texts.** *TACL* 2025 (2025.tacl-1.42); arXiv:2405.11804.
- `[transagents-demo]` Wu, M., et al. **TransAgents: Build Your Translation Company with Language Agents.** EMNLP 2024 System Demonstrations (2024.emnlp-demo.14). https://aclanthology.org/2024.emnlp-demo.14.pdf
- `[gonzoml-transagents]` Gonzo ML. Reader review of TransAgents (notes non-reproducibility). ⚠
- `[metadoceval]` Dahan, N., Bawden, R., Yvon, F. **MetaDocEval: A Contrastive Framework for Evaluating Machine Translation Metrics at the Document-Level.** EAMT 2026 (2026.eamt-1.19). https://aclanthology.org/2026.eamt-1.19/
- `[docmt-metrics]` Sun, Y., Zhu, D., Chen, Y., Xiao, E., Chen, X., Shen, X. **Fine-Grained and Multi-Dimensional Metrics for Document-Level Machine Translation.** NAACL 2025 SRW (2025.naacl-srw.1); arXiv:2410.20941.
- `[translated-mtqe]` Translated. **MT Quality Evaluation in the Age of LLM-based MT.** ⚠ (industry)
- Also cite from primary sources ⚠: BLEU (Papineni et al., ACL 2002), chrF (Popović, WMT 2015), TER (Snover et al., AMTA 2006), COMET (Rei et al., EMNLP 2020), BLEURT (Sellam et al., ACL 2020), GEMBA (Kocmi & Federmann, EAMT 2023), BlonDe (Jiang et al., NAACL 2022), Freitag et al. WMT metrics shared tasks.

---

## Follow-ups worth doing before drafting the related-work section

1. **Verify every 2026 arXiv identifier against the arXiv listing API**, not a search engine. Several 2026 preprints in this memo (MOC, PropUQ-MAS, Token Coherence, Hallucination Cascade, the Technical Taxonomy) were reached through search-engine HTML mirrors; the content is real but I did not confirm author lists or version history.
2. **Chase the two unverified quantitative claims** most likely to be challenged: the "17.2× error amplification" (Kim et al. 2025, via QUIVER) and the Error Propagation Index numbers (attributed to "AgentArch, arXiv:2509.10769"). Drop them if they do not resolve.
3. **Read the `fak` #639 epic in full.** It is the closest artifact to the proposed design and a reviewer who finds it and thinks the paper hid it will be unforgiving. Cite it explicitly as independent convergent design.
4. **Get MPI-4.1 ULFM chapter references right** (`MPI_Comm_shrink`, `MPI_Comm_agree`, `MPI_Comm_revoke`), because the fault-tolerance argument in §5 is the paper's strongest and least-contested gap.
5. **Decide the title disambiguation now.** "Collective communication for LLMs" currently means GPU interconnects (arXiv:2608.15118). The paper needs a title that cannot be mistaken for a NCCL optimization.
