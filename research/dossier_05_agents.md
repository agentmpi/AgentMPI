# Dossier 05 — Multi-Agent LLM Systems, Agent Protocols, and Classical Distributed Programming Models

**Purpose.** Evidence base for the Related Work and Motivation sections of the *AgentMPI* paper, which proposes a *protocol* (not a framework) for multi-agent LLM harnesses, deliberately modeled on MPI.
**Compiled:** 2026-08-30. **Method:** primary-source web retrieval (arXiv, official docs, vendor engineering blogs, standards trackers, GitHub).
**Citation convention:** inline `[Author Year]`, resolved in the References section; BibTeX in `refs_05.bib`. Claims I could not ground in a primary or otherwise checkable source are marked `[UNVERIFIED]`.

---

## 0. Scope, method, and confidence calibration

**(a) Source-quality stratification.** The agent-protocol literature has a heavy tail of low-citation, AI-assisted preprints. [Kang & Diponegoro 2026], [SSVP 2026], [MMP 2026] and [MPAC 2026] are preprints or self-published specs with essentially zero citations and, in one case, a declared AI-assistance statement. I cite them because their *specification-level* claims ("protocol X defines no voting primitive") are independently checkable against the specs, not for authorial authority.

**(b) Recency churn.** Since MCP's launch (Nov 2024) MCP has passed at least five dated revisions and A2A has gone 0.1 → 1.0 with a governance transfer. Every "protocol X lacks feature Y" claim below is dated to the revision named.

**(c) The negative claim is load-bearing.** The paper's contribution rests on an absence: no current agent protocol provides collectives, barriers, group membership, failure detection, or a defined consistency model. Absence claims are epistemically weaker than presence claims, so §3.5 assembles *converging* evidence from four independent directions rather than resting on one source.

---

## 1. Multi-agent LLM frameworks: concurrency and communication models

### 1.1 The actor-model line

**AutoGen v0.4 / autogen-core (Jan 2025).** Microsoft rewrote AutoGen onto an actor model; [Microsoft 2025a] states the intent plainly: "we ended up adopting an actor model for multi-agent orchestration… actors are the computational building blocks that can exchange messages and also perform work." Layering is `autogen-core` (event-driven actor runtime) → `autogen-agentchat` (task-driven teams) → extensions [AutoGen Docs 2025]. Mechanics from [AutoGen Core Docs 2025]:

- **Execution unit:** an agent *instance*, addressed by `AgentId` = (`AgentType`, key). Application code does not instantiate agents; a factory is registered per type and the runtime creates instances lazily on first message delivery — close to Orleans' virtual-actor lifecycle (§4.1).
- **Two modes:** direct `send_message(msg, AgentId)` is request/response (awaiting returns the handler's value); `publish_message(msg, TopicId)` with `TypeSubscription` is fire-and-forget and *always returns `None`*.
- **Failure semantics:** "If an agent raises an exception while handling a published message, this will be logged but will not be propagated back to the publishing agent." Pub/sub is therefore unreliable and unmonitored from the publisher's side: no acknowledgement, no delivery guarantee, no failure detector. Contrast MPI, where a collective's completion is a synchronization point with defined error classes.
- **Context:** `ChatCompletionContext` gives a "virtual view" of history; `BufferedChatCompletionContext` truncates to the last *n* messages [AutoGen Docs 2025] — MAST failure mode FM-1.4, *loss of conversation history*, shipped as a feature (§5.1).

**Microsoft Agent Framework (MAF) 1.0 — AutoGen and Semantic Kernel are now legacy.** Microsoft is merging AutoGen and Semantic Kernel into MAF, with AutoGen "still maintained… stable API… critical bug fixes and security patches — but we will not be adding [new features]" [AutoGen Discussion 2025]. MAF hit 1.0 GA on 2 April 2026 [Microsoft 2026]. It ships a graph-based workflow API on which "orchestration patterns such as sequential, parallel, Magentic and others are built" [AutoGen Discussion 2025], plus checkpointing and pause/resume [MAF Docs 2026]. Secondary reporting lists five stable patterns — sequential, concurrent, handoff, group chat, Magentic [AgentMarketCap 2026]; treat the enumeration as `[UNVERIFIED]`. Related Work should treat AutoGen v0.4's actor model as the intellectual predecessor and MAF as the current artifact.

**AgentScope** imports distributed-systems machinery most explicitly: "an actor-based distribution framework, enabling easy conversion between local and distributed deployments and automatic parallel optimization" [Gao et al. 2024a]. It uses *placeholders* (futures) — control flow needing a real value "temporarily blocks the process to retrieve its actual value," i.e. dataflow-style implicit synchronization, not an explicit barrier. [Gao et al. 2024b] pushes to **1M simulated agents** with strong-scaling data (10,000 agents on Llama3-70B: 22 min → 5.6 min as devices increase). AgentScope 2.0 reframes around a "Harness" layer: a stateless `ReActAgent` kernel plus `HarnessAgent` adding workspace/memory/sandbox/subagents, with per-call mutable state propagated through context so "a single instance can safely serve multiple `(userId, sessionId)` combinations concurrently," and state externalized to Redis/MySQL/Postgres for cross-replica recovery [AgentScope 2.0 Docs 2026]. This is the ecosystem's closest thing to a runtime with a real state-ownership story — still no collectives.

### 1.2 The graph / state-machine line

**LangGraph** is the reference "agent as durable state machine" [LangChain Docs 2026a, 2026b, 2026c]:

- **Execution unit:** a *node* in a `StateGraph`, run in Pregel-style **super-steps**.
- **Substrate:** *shared typed state* with per-channel reducers. Nodes do not address each other; they read and write channels. Dynamic fan-out uses **`Send`** to enqueue node invocations with per-invocation payloads.
- **Persistence:** *checkpointers* snapshot state per super-step, keyed by `thread_id`, with monotonically increasing checkpoint IDs (`MemorySaver`, `SqliteSaver`, `PostgresSaver`, Redis).
- **Failure handling — strongest in the framework set:** beyond super-step checkpoints, LangGraph "persists writes at the node (task) level… if another node in the same super-step fails, the successful nodes' writes are already durable and don't need to be re-run on resume." Durability is tunable: `"exit"`, `"async"`, `"sync"`.
- **Interrupts:** `interrupt()` suspends at an exact point, persists, waits indefinitely; resume via `Command` on the same `thread_id`.
- **Composition wart:** "each subgraph manages its own checkpoint namespace," so "when a subgraph updates state, the parent graph may not see the changes immediately"; the documented fix routes cross-boundary data through a `Store` [LangChain Docs 2026b]. That is an explicit admission of **no coherent shared-memory abstraction across composition boundaries**.

**Google ADK 2.0 (GA 19 May 2026)** replaced a hierarchical agent executor with a **graph execution engine** where "Agents, Tools, and Functions are evaluated as individual nodes within a workflow graph"; `BaseAgent` now subclasses `BaseNode` [ADK Docs 2026a]. Two points matter:

1. **ADK contains an actual barrier.** `JoinNode` is "the fan-in barrier… waits for every predecessor task to complete, and then passes its successor a record keyed by predecessor node name" (Go: `workflow.NewJoinNode`) [ADK Docs 2026b]. This is the closest analogue to `MPI_Barrier`/gather in mainstream agent tooling — and it lives at the *framework graph* level, not the protocol level, over predecessors known within one graph engine.
2. **Dynamic fan-out with replay.** `ctx.run_node()` schedules children imperatively; on resume the parent re-runs from the top but "previous successful `ctx.run_node()` calls are replayed from history (cached outputs are returned)" — memoized replay, the same recovery model as Inngest (§1.5). Parallel children need `use_sub_branch=True` for event isolation; `maxParallelWorkers` bounds concurrency (default 8) [ADK Docs 2026a].

**CrewAI** is dual-model [CrewAI GitHub 2026, CrewAI Docs 2026]: **Crews** (role-based agents under `Process.sequential` or `Process.hierarchical`, the latter with a manager agent — auto-created or supplied via `manager_llm` — that delegates and validates) and **Flows** (`@start`/`@listen`/`@router`, typed Pydantic state, persistence, resume-from-last-step, `@human_feedback`). Crew addressing is by *role*, mediated by the manager; there is no peer addressing primitive.

**LlamaIndex Workflows** is event-first: `@step` methods consume and emit typed events over an async event bus, with loops/retries/conditionals as event routing [ZenML 2026] — the purest publish–subscribe substrate among mainstream Python frameworks. **Dify** exposes agents as *nodes inside a workflow*, including a beta Agent node where an invited published agent "arrives with its saved capabilities" and the workflow supplies only a task description [Dify Docs 2026]: agent-as-remote-procedure, no peer channel.

### 1.3 The free-form chat / blackboard line

**AutoGen GroupChat** (v0.2 lineage, still the most-copied pattern) is a shared message list plus a speaker-selection policy — a blackboard in the Hearsay-II sense (§4.4) whose control component is an LLM choosing the next knowledge source.

**MetaGPT** is the cleanest published blackboard/tuple-space design of the LLM era [Hong et al. 2024]: "we introduce a shared message pool that allows all agents to exchange messages directly. These agents not only publish their structured messages in the pool but also access messages from other entities transparently." Overload is managed by a **subscription mechanism** keyed on role profiles, and "an agent activates its action only after receiving all its prerequisite dependencies." In code, `Message.cause_by` is the subscription tag and `Environment.publish_message` broadcasts [MetaGPT Docs 2026]. Two observations: this is *Linda with typed tuples plus a blocking `in` on a conjunction of tags* — a dependency-triggered barrier, reinvented; and MetaGPT deliberately communicates via **structured documents rather than dialogue**, "preventing irrelevant or missing content." The structured-message argument is thus already in the literature, so AgentMPI must differentiate on *coordination semantics*, not on structured messages.

**ChatDev** uses chat chains with **phase-level passing** and short-memory sharing, versus MetaGPT's **agent-level broadcasting** and contextual retrieval — the projects' own comparison [ChatDev Issue 2023]. **CAMEL** provides role-playing agent-to-agent dialogue with broadcast and history utilities; **AgentVerse** an expert-recruitment/decision/action/evaluation loop. For all four: substrate is a shared or pairwise natural-language transcript, addressing is by role name, no scheduler beyond a turn policy, persistence is the transcript, failure handling absent or a retry.

### 1.4 The orchestrator–worker line (where production lives)

**Magentic-One / Magentic-UI.** An Orchestrator directs four specialists (WebSurfer, FileSurfer, Coder, ComputerTerminal) via **two loops**: an outer loop maintaining a **Task Ledger** (facts, guesses, plan) and an inner loop maintaining a **Progress Ledger** (progress, task assignment, completion check), with **stall detection** triggering re-planning [Microsoft 2025b]. Magentic-UI adds the human as a first-class agent and can wrap MCP servers as custom agents [Fourney et al. 2025]. The ledgers are externalized, LLM-maintained coordination state — a blackboard with a single writer. No group membership, no barrier, no failure detector; "stall" is inferred by the orchestrator LLM from lack of progress over ≥2 cycles.

**Anthropic's Research system** is the most-cited production data point [Anthropic 2025]: orchestrator–worker with a lead agent that plans, saves the plan to memory, and spawns subagents with separate context windows.

- Opus-4 lead + Sonnet-4 subagents beat single-agent Opus 4 by **90.2%** on their internal research eval.
- On BrowseComp, three factors explained **95%** of performance variance; **token usage alone explained 80%**.
- Multi-agent systems use **~15× more tokens than chat**; single agents ~4×.
- Explicit scope limits: "some domains that require all agents to share the same context or involve many dependencies between agents are not a good fit… most coding tasks involve fewer truly parallelizable tasks than research, and LLM agents are not yet great at coordinating and delegating to other agents in real time."

That last sentence is the best vendor-sourced statement of the gap AgentMPI targets and should be quoted.

**Claude Code subagents and agent teams — the most useful evidence in this dossier.** Two distinct mechanisms.

*Subagents* [Claude Code Docs 2026a, 2026b]: the `Agent` tool (renamed from `Task` in v2.1.63) spawns a subagent with its **own context window**; "each subagent starts with a fresh, isolated context window. It doesn't see your conversation history"; only the final message returns to the parent. The sole parent→child channel is the tool's prompt string; `isolation: worktree` gives an isolated git checkout. Fork-join with a one-message return value, deliberately, for context hygiene.

*Agent teams* (experimental, disabled by default, documented as of v2.1.178) is where a real distributed substrate appears, with its limits documented candidly [Claude Code Docs 2026c]:

- **Naming and addressing.** "The lead assigns every teammate a name when it spawns them, and any teammate can message any other by that name." The team config "contains a `members` array with each member's name and agent ID… Teammates can read this file to discover other team members" — a rudimentary **group membership list**, static, file-based, lead-owned.
- **Transport.** "Each agent's mailbox is a JSON file at `~/.claude/teams/{team-name}/inboxes/{agent-name}.json`." Delivery semantics are explicit: "Claude Code reports a message as sent only when the write to the recipient's mailbox file succeeds… When the write fails, for example because the disk is full or the mailbox directory isn't writable, the sending agent receives an error and nothing is sent."
- **No broadcast.** The decisive sentence: **"send a message to one specific teammate by name. To reach everyone, send one message per recipient."** The most sophisticated shipping multi-agent harness of 2026 has no broadcast primitive, let alone a reduction or all-to-all.
- **Shared mutable state with a lock.** A shared task list with dependency edges; "task claiming uses file locking to prevent race conditions when multiple teammates try to claim the same task simultaneously"; completing a task "unblocks the dependent tasks." A hand-rolled blackboard plus mutex plus dependency-triggered release.
- **Failure handling.** "A teammate whose turn ends on an API error notifies the lead that it failed and includes the error text." Notification only — no supervisor tree, no restart, no failure detector. The page opens by warning of "known limitations around session resumption, task coordination, and shutdown behavior."
- **Cost.** "Agent teams use significantly more tokens than a single session… token usage scales with the number of active teammates," and in-process teammates fall outside the main cache TTL bucket (5 min default) unless `subagentPromptCacheTtl` is raised.

**OpenAI Agents SDK** (successor to the archived Swarm) makes the *handoff* the multi-agent primitive: a tool call transferring control to another agent, with `handoff()` supporting `input_filter` and `on_handoff` [OpenAI Agents Docs 2026a]. "Handoffs stay within a single run," and guardrails are asymmetric — input guardrails apply only to the first agent in the chain, output guardrails only to the agent producing the final output. Manager-style orchestration uses `Agent.as_tool`. State lives in **Sessions**; `OpenAIResponsesCompactionSession` wraps any session and calls `responses.compact` (default trigger ≥10 non-user items) to shrink stored history, with nested-handoff compaction an opt-in beta (`RunConfig.nest_handoff_history`) [OpenAI Agents Docs 2026b]. Net: single-threaded control transfer, no concurrency, no peer channel, context managed by server-side compaction.

### 1.5 The durable-execution line

Three engines market "durable agents" and solve the *fault* half of the problem agent protocols ignore [Restate Docs 2026, Inngest Docs 2026, Zylos 2026]:

- **Temporal:** history-based deterministic replay; workflow code must be deterministic, all LLM/tool calls pushed into Activities; timers, signals, retries, task queues are first-class.
- **Restate:** journal-based replay in a single self-hostable binary; **virtual objects** keyed by e.g. session ID serialize all handler calls on that key; `ctx.run()` journals before execution, giving **exactly-once tool execution without application-level idempotency keys**; `awakeables` for callback waits.
- **Inngest:** explicitly *rejects* replay — `step.run()` results are memoized so only the failed step retries; `step.waitForEvent()` suspends the function entirely, holding no process or connection, which is how it does human-in-the-loop and inter-agent coordination.

Restate's virtual objects are the closest existing thing to a per-agent serialized state cell with exactly-once side effects, and are the most plausible reference-implementation substrate for AgentMPI. Related Work should say so rather than implying the fault-tolerance problem is unaddressed.

### 1.6 Contrast case: DSPy is compilation, not orchestration

DSPy is the right foil for "we are not proposing another framework." Its unit is a *module* with a *signature*, and its contribution is **optimizers that compile programs**: `MIPROv2` bootstraps demos, proposes grounded instructions, and searches combinations by Bayesian optimization [DSPy Docs 2026]; `GEPA` mutates instructions using an LM that reads the metric's *textual* feedback and maintains a Pareto frontier over validation tasks, reported to beat MIPROv2 by >10% and GRPO by up to 20% with up to 35× fewer rollouts [Agrawal et al. 2026]. DSPy answers "how do I improve a multi-call LM program?" and is silent on "how do concurrent agents coordinate." AgentMPI is to agent harnesses what MPI is to numerical libraries: a coordination API, not an optimizer and not a framework.

### 1.7 HPC-adjacent substrates

**Ray/Anyscale** publish a reference multi-agent pattern where each component (LLM service on GPU, MCP tool servers, each agent) is an independently autoscaling Ray Serve application, agents talking **over A2A** and tools over MCP [Anyscale 2026, Ray Docs 2026]. Backpressure is real infrastructure: `max_ongoing_requests`, `max_queued_requests`, `BackPressureError` → HTTP 503 [Ray Docs 2026b]. **Parsl/Globus Compute:** [Wang et al. 2025] wire Parsl into LangChain tool calling so tool calls become Parsl futures dispatched to HPC workers, demonstrated for molecular-dynamics ensembles on Polaris/ALCF. This is the existing HPC-community answer and it is *task-parallel dispatch only* — the right citation for "the HPC community has connected LLM agents to schedulers but not given them a communication model."

### 1.8 Scale ceilings actually demonstrated

Two regimes the paper must not conflate:

- **Coordination-heavy agents:** 3–5 subagents [Anthropic 2025]; 5 agents in Magentic-One [Microsoft 2025b]; ~5 roles in MetaGPT; Claude Code teams whose UI collapses rows beyond three idle teammates [Claude Code Docs 2026c]. Nothing credible in the double digits with genuine interdependence.
- **Simulation:** AgentScope 1M [Gao et al. 2024b]; OASIS 1M [Yang et al. 2024]; AgentTorch 8.4M for a New York City COVID model [Chopra et al. 2024]. These scale by making per-agent behavior cheap (archetypes, heuristics) and coordination shallow (environment-mediated, no pairwise reasoning).

The six-order-of-magnitude gap between these regimes is itself an argument that the missing thing is a coordination abstraction, not compute.

---

## Framework comparison

| Framework (version) | Execution unit | Addressing | Communication substrate | Collectives? | Group membership? | Failure handling | Context management | Max agents demonstrated |
|---|---|---|---|---|---|---|---|---|
| AutoGen v0.4 / autogen-core | Actor instance (lazily created by runtime) | `AgentId` = (AgentType, key); `TopicId` for pub/sub | Direct req/resp `send_message`; type-based pub/sub `publish_message` | No — broadcast only, fire-and-forget, no reduction/barrier | No — subscriptions, not membership | Exceptions on published msgs "logged but not propagated"; no detector | `ChatCompletionContext` virtual views; `BufferedChatCompletionContext` truncation | Tens (per docs examples) `[UNVERIFIED]` beyond that |
| Microsoft Agent Framework 1.0 (Apr 2026) | Agent + workflow graph node | Agent handle; workflow edges | Graph workflow API; patterns (sequential, concurrent, handoff, group chat, Magentic) | Concurrent fan-out/fan-in inside one workflow; no cross-process collective | No | Checkpointing, pause/resume, HITL approvals | Session-based state mgmt (from Semantic Kernel) | `[UNVERIFIED]` |
| LangGraph (1.x, 2026) | Graph node, executed in Pregel super-steps | None — nodes read/write shared typed channels; `Send` for dynamic fan-out | Shared typed state + reducers; `Store` for cross-thread | Fan-in via reducers at super-step boundary; no named collective | No | Best-in-class: per-super-step checkpoints + node-level pending writes; `durability` = exit/async/sync; `retry_policy` | Reducers, trimming, `Store`; subgraphs have *separate* checkpoint namespaces | Tens of nodes; agent count not the scaling axis |
| Google ADK 2.0 (May 2026) | Workflow graph node (`BaseAgent` ⊂ `BaseNode`) | Node name; hierarchical agent tree | Graph edges + session state (`OutputKey`); `ctx.run_node()` dynamic dispatch | **Yes, partially:** `JoinNode` is an explicit fan-in barrier keyed by predecessor name | No | Runtime catches exceptions for retries, telemetry, HITL pauses; memoized replay of completed `run_node` calls | Session state; `use_sub_branch` event isolation | `maxParallelWorkers` default 8, configurable |
| CrewAI (2026) | Agent (role) in a Crew; step in a Flow | Role name via manager agent | Manager-mediated delegation; Flow event routing over typed Pydantic state | No | No | Crew: task retries. Flow: resume from last step, `@human_feedback` | Crew memory + `Process` ordering; Flow typed state | Small teams (roles) |
| LlamaIndex Workflows | `@step` method | Typed event class | Async event bus (pub/sub) | No | No | Retries per step | Application-managed | Small |
| MetaGPT | Role, activated when prerequisite deps satisfied | Subscription on `Message.cause_by` tag | **Global shared message pool** (blackboard) + role-profile subscriptions | Implicit dependency-conjunction barrier; no explicit collective | No | Executable-feedback retry loop on code errors | Structured documents instead of dialogue; contextual retrieval | ~5 roles (SOP) |
| ChatDev | Phase; dyadic chat | Role name within phase | Chat chain, phase-level message passing, short-memory sharing | No | No | Interpreter feedback; review/test phases | Short-memory sharing per phase | ~5–7 roles |
| CAMEL / OWL, AgentVerse | Role-playing agent; recruited expert | Role name | Pairwise/broadcast NL transcript | No | Recruitment ≈ ad-hoc membership | Retry / evaluation stage | Transcript + summarization | ~10s `[UNVERIFIED]` |
| Magentic-One / Magentic-UI | Specialist agent directed by Orchestrator | Agent name, chosen by Orchestrator LLM | Orchestrator-mediated request/response + **Task Ledger / Progress Ledger** | No | Fixed roster | LLM stall detection (≥2 cycles) → re-plan; no detector | Two-ledger externalized state | 1 orchestrator + 4 specialists (+ human) |
| Anthropic Research | Subagent with own context window | Lead-assigned task, not a name | Task description down, condensed summary up; plan in lead's memory | No | No | Lead re-plans; no formal semantics disclosed | Separate context windows; plan externalized to memory before overflow; separate citation pass | **3–5 parallel subagents** |
| Claude Code subagents | Subagent process, own context window | None (prompt string only) | Fork-join: prompt down, single final message up | No | No | Parent sees failure text only | Hard isolation; `isolation: worktree`; summary-only return | Panel-limited; parallel spawns supported |
| Claude Code **agent teams** (exp., v2.1.178+) | Full independent Claude Code session | **Lead-assigned name**; peers message any peer by name | **Per-agent JSON mailbox files** + **shared task list with dependency edges** | **No — "to reach everyone, send one message per recipient"** | **Partial** — `members` array in team config, readable by teammates | Idle/error notification to lead; file-lock task claiming; documented gaps in resumption/shutdown | Per-teammate context window; lead synthesizes | 3 shown before idle rows collapse; no documented hard cap |
| OpenAI Agents SDK (v0.17.x, 2026) | Agent turn inside one `Runner.run` | Handoff target agent | Control transfer via handoff tool; `Agent.as_tool` for manager style | No — handoffs are strictly single-threaded | No | Guardrails (asymmetric), tracing; no distributed failure model | `Session` interface; `OpenAIResponsesCompactionSession` (`responses.compact`, ≥10 non-user items) | 1 active agent at a time |
| AgentScope 2.0 | Stateless `ReActAgent` kernel / `HarnessAgent` | Session/user/agent/org-scoped runtime context | Typed event streams (28 event types) + actor-based distribution; abstract FS over Redis/MySQL/OSS | No | No | State store enables cross-replica resume + rolling deploys; sandbox snapshot/resume | Automatic context compaction, tool-result eviction | **1M (simulation)** |
| Temporal / Restate / Inngest (as agent runtimes) | Workflow / handler / step function | Workflow ID / virtual-object key / event match | Journal or memoized steps; signals / awakeables / `waitForEvent` | No | No | **Exactly-once step semantics, crash recovery, retries** — strongest in class | Application's problem | N/A (durability layer) |
| Ray Serve + A2A (Anyscale ref. arch.) | Ray Serve replica per agent | A2A agent card URL | A2A over HTTP/SSE; MCP for tools | No | A2A discovery ≈ directory | Autoscaling, load shedding (`max_queued_requests` → 503), AZ-aware scheduling | Per-agent | Autoscaling-bounded |
| Parsl / Globus Compute + LangChain | Parsl task (future) | Function reference | Futures over HPC workers | Parsl's own dataflow join | No | Parsl retries / checkpointing | N/A | Ensemble-sized (HPC) |
| DSPy (3.2.x) | Module / predictor (**not** a concurrent agent) | N/A | N/A — compilation, not orchestration | N/A | N/A | N/A | Optimizer-chosen demos + instructions | N/A |

---

## 2. The agent OS / scheduler / serving fabric ("our network fabric")

**Agent operating systems.** [Mei et al. 2024] (AIOS) is canonical: an *AIOS kernel* isolating LLM-specific services (scheduling, context management, memory, storage, tool, access control) from agent applications, where "agent queries are decomposed into sub execution units (i.e., AIOS syscalls) to facilitate parallelism," reporting up to **2.1× faster** agent execution across frameworks. The kernel centralizes queues in the scheduler "instead of placing separate queues within each processing module," shipping FIFO and round-robin schedulers, the latter preemptive with time slices and suspend/resume via a context manager [AIOS Docs 2026]. AIOS is the strongest prior art for "agents need OS-style resource abstractions" — and it is deliberately an *OS*, i.e. resource management, not a communication standard. AgentMPI's positioning should mirror the historical relation: AIOS : AgentMPI :: Unix : MPI.

**Agent-aware schedulers.** Three systems establish that application-level structure must be exposed to the serving layer:

- **Parrot** (OSDI'24) introduces the **Semantic Variable**, annotating prompt inputs/outputs so the service can "perform conventional data flow analysis to uncover the correlation across multiple LLM requests," enabling DAG-aware scheduling, prefix sharing, and performance-objective deduction; up to an order-of-magnitude end-to-end improvement [Lin et al. 2024].
- **Autellix** treats *programs* as first-class, showing that "programs submitted to LLM serving engines experience long cumulative wait times, primarily due to head-of-line blocking at both the individual LLM request and the program," and preempts/prioritizes calls using program history: **4–15× throughput at equal latency vs. vLLM**, plus data-locality-aware routing that preserves a program's KV cache [Luo et al. 2025].
- **Teola / Ayo** (ASPLOS'25) decompose workflows into *task primitives* and optimize the full dataflow graph including non-LLM components, with a distributed two-level scheduler [Tan et al. 2024, Tan et al. 2025].

**Why the serving layer is the fabric.** Continuous batching (iteration-level scheduling) originates in **Orca** (OSDI'22) [Yu et al. 2022]; **vLLM**'s PagedAttention pages KV cache like OS memory and makes admit/evict cheap, with automatic prefix caching hashing fixed 16-token blocks in a global table [Kwon et al. 2023, vLLM Docs 2026]; **SGLang**'s RadixAttention indexes all active KV pages in one radix tree, giving variable-length longest-prefix matching with no block alignment [Zheng et al. 2024]. For many concurrent agents this means shared system prompts and tool schemas are computed once and multi-turn histories become a tree walk. Third-party measurements report SGLang advantages concentrated above 60% prefix overlap (e.g. ~37% lower p50 TTFT at concurrency 50) [Spheron 2026] — exact figures `[UNVERIFIED]`, architectural claim well established.

**The argument this supports.** Each of these systems infers coordination structure the application already knows and cannot express: Parrot recovers a DAG from prompt annotations, Autellix recovers program identity by interception, RadixAttention recovers sharing by hashing tokens. A protocol that *declares* communicators, groups, and collective intent would hand the fabric this information directly — probably the strongest performance motivation available to the paper.

---

## 3. Agent interoperability protocols

### 3.1 MCP (Anthropic, Nov 2024 → revision 2026-07-28)

MCP standardizes the *vertical* boundary: a client-server JSON-RPC protocol exposing Tools, Resources, and Prompts. Revisions are date-stamped `YYYY-MM-DD` and bumped only on backwards-incompatible change; the current revision is **2026-07-28**, superseding 2025-11-25 [MCP Versioning 2026, MCP Release 2026].

That revision is the largest change since launch and is **almost entirely about statelessness**, moving MCP *further* from anything MPI-like [MCP Blog 2026]:

- The `initialize`/`notifications/initialized` handshake and the `Mcp-Session-Id` header are **removed** (SEP-2575, SEP-2567). Each request carries its own protocol version and client capabilities in `_meta`, so "any request can now land on any server instance behind a plain round-robin."
- A new `server/discover` RPC replaces handshake-time capability learning and is **mandatory for servers**.
- Streamable HTTP requests must carry `Mcp-Method` and `Mcp-Name` headers so gateways can route and meter without parsing bodies (SEP-2243).
- Server-initiated mid-call interactions — **sampling, elicitation, and roots** — are **deprecated** (SEP-2577, 12-month minimum window) in favor of **Multi Round-Trip Requests** using an `InputRequiredResult` retry pattern. Logging and the legacy HTTP+SSE transport are also deprecated; Dynamic Client Registration gives way to Client ID Metadata Documents.
- Change notifications collapse from a GET endpoint to a single `subscriptions/listen` stream; tasks move to an `io.modelcontextprotocol/tasks` extension with `tasks/get` polling and `tasks/update`.

**What MCP does not provide, by construction:** any notion of a peer, a group, a collective, a barrier, membership, or a consistency model. It is a tool-access protocol travelling toward *less* session state. [Kang & Diponegoro 2026] independently classify MCP as Absent on membership, deliberation, voting, dissent and human escalation, and Partial on audit ("no tamper-evident event log, no hash chain, and no replay guarantee. Audit depends on implementation, not protocol specification"). Governance: MCP is hosted by the **Agentic AI Foundation** (AAIF) under the Linux Foundation, co-founded by Anthropic, OpenAI and Block in December 2025 [AAIF 2026].

### 3.2 A2A (Google, Apr 2025 → v1.0, Mar 2026 → AAIF)

A2A standardizes the *horizontal* boundary: agent discovery via **Agent Cards**, task delegation, artifacts, streaming. Timeline: launched Apr 2025; donated to the Linux Foundation Jun 2025; **IBM's ACP merged into A2A on 29 Aug 2025** [LF AI & Data 2025]; **v1.0 released 12 Mar 2026**, with >150 organizations and production deployments reported at the one-year mark [Linux Foundation 2026]; subsequently hosted by AAIF alongside MCP [AAIF 2026].

v1.0 changes relevant to us [A2A Docs 2026a]: `a2a.proto` elevated to normative source of truth across REST/gRPC/JSON-RPC bindings; `AgentCard` restructured so `supportedInterfaces[]` (each with `url`, `protocolBinding`, `protocolVersion`) replaces `url`/`preferredTransport`/`additionalInterfaces`, enabling simultaneous multi-version support; **Signed Agent Cards** with RFC 8785 canonicalization; native multi-tenancy; an `A2A-Version` header with `VersionNotSupportedError`; a versioned extension mechanism with requirement declarations.

**What A2A does not provide.** The Linux Foundation's own announcement is unexpectedly useful: agents "are now able to work together, delegate sub-tasks, and coordinate complex workflows **without sharing internal memory**" [Linux Foundation 2026] — A2A's design philosophy stated as a feature, and precisely the opposite of a PGAS-style shared abstraction. A2A's unit is a *task* delegated to *one* remote agent: no group object, no collective, no barrier, no membership protocol, no consistency model. [Kang & Diponegoro 2026] rate A2A Partial on membership ("an agent 'exists' by publishing an Agent Card; there is no concept of community membership distinct from existence") and Absent on deliberation, voting, dissent, escalation and audit, noting the Traceability extension "adds correlation IDs for distributed tracing but does not define tamper-evident logs or replay semantics," and that after 6+ months with an active extension ecosystem, **zero governance extensions** had been proposed.

### 3.3 ACP, ANP, AGNTCY, LMOS, agents.json, Agora, Coral

- **ACP (IBM Research / BeeAI, Mar 2025).** REST-first: each agent behind an HTTP API, self-describing manifest, work submitted as *runs* carrying MIME-typed multipart message parts; sync and async; session management, message routing, DID integration [Ehtesham et al. 2025]. **Status: merged into A2A Aug 2025; development wound down; IBM's explainer now carries a migration notice** [LF AI & Data 2025, IBM 2026]. Its FIPA-ACL heritage (performatives propose/accept/reject/counter) makes it historically the most interesting, and it is the only protocol [Kang & Diponegoro 2026] rate even Partial on deliberation — still Absent on voting, dissent, escalation and audit, because "negotiation is bilateral… not multilateral deliberation. The protocol lacks turn-taking governance, relevance enforcement, or synthesis primitives."
- **ANP (Agent Network Protocol).** Three layers — identity (W3C DIDs, end-to-end encryption), meta-protocol/negotiation, application — aiming to be "the HTTP of the agentic web era"; JSON-LD semantics; strongest decentralized trust model, higher negotiation overhead, thinner tooling [Ehtesham et al. 2025, Kong et al. 2026]. Rated Absent on all governance dimensions except identity-adjacent ones [Kang & Diponegoro 2026].
- **AGNTCY / Internet of Agents.** Full-stack: **OASF** (agent description schema for directory/discovery) + **ACP/AConP** (Agent Connect Protocol for invocation) + directory and observability components [Coral 2025, Kong et al. 2026].
- **LMOS** (Eclipse Foundation). Transport-flexible multi-agent infrastructure with a discovery layer and an OS framing for how LLMs and agents meet infrastructure [Kong et al. 2026].
- **agents.json** (Wildcard AI). A manifest layer over OpenAPI describing how existing websites/APIs can be discovered and composed by agents; **stateless** by design, "no shared session context is assumed between consecutive calls" [Semantic View 2026].
- **Agora** (Oxford). The most intellectually adjacent: on-the-fly *protocol negotiation*, where agents share or generate communication schemas before exchanging data, framed against a "Communication Trilemma" of versatility / efficiency / portability [Marro et al. 2025].
- **Coral Protocol.** Thread-based messaging for agent teams, typically realized as an MCP server, with session threads and explicit participant/message synchronization [Georgio et al. 2025].
- **LangChain Agent Protocol (LAP).** A deployment API, not a coordination protocol: `/runs`, `/threads`, `/store` [Semantic View 2026].
- **ERC-8004 (Trustless Agents, draft Aug 2025).** On-chain Identity, Reputation (`giveFeedback`/`revokeFeedback`) and Validation registries, scoped to "discover, choose, and interact with agents" [Kang & Diponegoro 2026].

The consensus reading in the 2026 survey literature is that these are **layers, not competitors**: roughly L1 identity/transport (ANP, AGNTCY, LMOS), L2 discovery/manifests (agents.json, LMOS), L3 tool/context execution (MCP), L4 task/session interaction (A2A, ACP, LAP), L5 negotiation/deliberation (Agora, ANP schema negotiation) [Hückmann 2026].

### 3.4 Standards bodies

- **W3C AI Agent Protocol Community Group.** Mission: "open, interoperable protocols that enable AI agents to discover, identify, and collaborate efficiently across the Web," with deliverables on inter-agent communication, agent identity, metadata formats, security/privacy, and protocol interoperability. Its tentative spec currently defines an **Agent Identity module** over DIDs, specifying `did:wba` (a `did:web` extension binding an Ed25519 key fingerprint into the DID path) and requiring resolution/verification of `did:web` and `did:webvh` [W3C CG 2026a, W3C CG 2026b]. It is a *Community* Group — W3C hosting "does not imply endorsement."
- **IETF.** No agent-protocol working group; individual Internet-Drafts, e.g. `draft-singla-agent-identity-protocol-03` (10 Jun 2026), defining `did:aip`, capability manifests, and cryptographic delegation chains over W3C DID plus RFC 7519/7517/9449 [Singla 2026].

Every standards-track effort here is about **identity, delegation, and discovery**; none is about coordination semantics. That reflects a judgment that the open-network trust problem is prior — but it does mean the coordination layer is unclaimed.

### 3.5 What none of them provide — the evidence, assembled

Four independent lines converge on the paper's central negative claim.

**(i) Direct spec reading.** MCP defines Tools/Resources/Prompts over JSON-RPC and is actively shedding session state [MCP Blog 2026]. A2A defines Agent Cards, Tasks, Messages, Parts, Artifacts and interface bindings [A2A Docs 2026a]. Neither defines a group/communicator object, a collective operation, a barrier, a membership-change protocol, a failure detector, or a memory consistency model. ACP, ANP, agents.json, LAP and Coral likewise.

**(ii) Third-party taxonomies do not even have the dimensions.** [Semantic View 2026] evaluates **18** protocols on nine dimensions across three layers — communication (transport, streaming, security), syntactic (schema, lifecycle, error handling), semantic (clarification, context alignment, verification). **Collectives, group membership, barriers, failure detection and consistency appear nowhere in the taxonomy.** Its finding — "most protocols provide increasingly mature support for transport, streaming, schema definition, and lifecycle management, but offer limited protocol-level mechanisms for clarification, context alignment, and verification… semantic responsibilities are often pushed into prompts, wrappers, or application-specific orchestration logic, creating hidden interoperability [debt]" — is structurally our complaint, aimed one layer over. The earlier comparative survey [Ehtesham et al. 2025] likewise compares interaction modes, discovery, communication patterns and security models. When a field's own comparison frameworks lack a column, the feature is absent not merely from implementations but from the design conversation.

**(iii) An explicit gap analysis.** [Kang & Diponegoro 2026] apply a six-dimension governance taxonomy (membership, deliberation, voting, dissent preservation, human escalation, audit/replay) to MCP v1.1, A2A v1.0.1, ACP, ANP and ERC-8004, classifying each pair Supported/Partial/Absent from specification evidence. **Voting, dissent preservation and human escalation are Absent across all five**; membership and deliberation are at most Partial; audit exists only as a substrate property (blockchain immutability, session state), never as governance-specific design. Their conclusion — "agent community governance constitutes a missing architectural layer above current interoperability standards — not a missing feature within them" — is the same *shape* of argument as ours in a governance rather than parallel-computing vocabulary: voting is a reduction, membership is `MPI_Group`. Two groups reaching for the same absence from different traditions is corroboration. *(Caveat per §0(a): uncited preprint with declared AI assistance; cite for its checkable spec classifications and consider re-deriving the matrix.)*

**(iv) Vendor documentation of the workaround.** Shipping systems document doing collectives by hand:

- Claude Code agent teams: "To reach everyone, send one message per recipient" — no broadcast [Claude Code Docs 2026c].
- The same system implements a barrier-like structure as a **shared task list with dependency edges** and mutual exclusion as **file locking** [Claude Code Docs 2026c].
- Cognition builds manager→child Devin coordination "through an internal MCP" — tunnelling agent coordination through a *tool*-access protocol because no coordination protocol exists — and reports that "agents assume they share state with their children when they don't" and that "cross-agent communication, a sub-agent writing messages back to its manager to be passed to other agents in the agent team, doesn't happen by default" [Yan 2026].
- LangGraph documents separate subgraph checkpoint namespaces and recommends an out-of-band `Store` for cross-boundary data [LangChain Docs 2026b].
- ADK's `JoinNode` shows the primitive *is* needed and is being reinvented per framework rather than standardized [ADK Docs 2026b].

**Honest exceptions.** (1) A2A's extension mechanism is expressive enough that collectives *could* be defined as an extension — [Kang & Diponegoro 2026] make exactly this point about governance, adding that nobody has. The paper should acknowledge AgentMPI could be profiled as an A2A extension and argue on semantics rather than impossibility. (2) [MPAC 2026] does specify Lamport-clock causal ordering, explicit consistency and execution models, atomic batch operations, and fault recovery (`do_claim_intent` to "take over a crashed agent's suspended intent"). It is a self-published spec with no peer review, but it is genuinely in the target space and must be cited, not ignored.

---

## Protocol comparison

| | Layer | Standardizes | Transport | Naming | Collectives | Fault semantics | Consistency model |
|---|---|---|---|---|---|---|---|
| **MCP** (rev. 2026-07-28) | L3: agent↔tool/context (vertical) | Tools, Resources, Prompts; `server/discover`; Multi Round-Trip Requests; `subscriptions/listen`; `tasks/*` extension; `resultType`/`ttlMs`/`cacheScope` | JSON-RPC over stdio or **stateless** Streamable HTTP (`Mcp-Method`, `Mcp-Name` headers); legacy HTTP+SSE deprecated | Server URL + tool/resource/prompt names; **no session ID** (removed) | **None.** No group, broadcast, barrier, or reduction | Per-request JSON-RPC errors (four codes renumbered); no detector, no supervision, no replay guarantee; audit is implementation-defined | **None specified.** Stateless per-request; `ttlMs`/`cacheScope` are cache hints, not a memory model |
| **A2A** (v1.0, Mar 2026) | L4: agent↔agent task delegation (horizontal) | Agent Card (`supportedInterfaces[]`, Signed Cards via RFC 8785), Task lifecycle, Message/Part, Artifacts, streaming, versioned extensions, multi-tenancy | JSON-RPC / gRPC / REST bindings from normative `a2a.proto`; HTTP(S) + SSE; `A2A-Version` header | Agent Card URL at well-known path; task UUIDs; tenant scoping | **None.** Unit is a task to *one* peer; explicitly "without sharing internal memory" | Typed error taxonomy incl. `VersionNotSupportedError`; task states; **no** membership change, failure detector, or replay semantics (Traceability ext. = correlation IDs only) | **None.** Peers share no memory by design |
| **ACP** (IBM/BeeAI; **merged into A2A Aug 2025**) | L4: structured agent messaging | Agent manifest, runs, MIME-typed multipart parts, sync/async, sessions, FIPA-derived performatives (propose/accept/reject/counter) | REST over HTTP | Agent HTTP endpoint + manifest; DID/RBAC integration | **None.** Bilateral negotiation only; no quorum, no preference aggregation | HTTP status + run states; no detector or replay | **None.** Conversation state only |
| **ANP** | L1–L2 (+L5): identity, discovery, negotiation on open networks | W3C DID identity layer with E2E encryption; meta-protocol/schema negotiation; JSON-LD application semantics | HTTP + P2P; DID resolution | **W3C DIDs** (strongest naming of the set) | **None** | No detector; no replay; routing may be logged but not specified | **None** |
| **AgentMPI** *(this paper)* | | | | | | | |

Orientation rows (not required): **agents.json** = L2 manifest over OpenAPI, stateless, no coordination; **AGNTCY** = OASF schema + AConP invocation + directory, full-stack but no collectives; **LMOS** = L1–L2 transport/discovery infrastructure; **Agora** = L5 on-the-fly schema negotiation; **Coral** = thread-based team messaging over MCP; **ERC-8004** = on-chain identity/reputation/validation registries, audit-by-substrate.

### 3.6 The closest related work: the 2026 "missing coordination layer" wave

The paper is **not** first to the intuition, and Related Work must say so. Between roughly Q2 and Q3 2026 several groups independently proposed coordination layers above MCP/A2A:

- **CoAgent / MTPO** [CoAgent 2026] — the most directly competitive. It models tool calls by read/write **footprints** `R(τ)`, `W(τ)`, defines a *Monotonic Trajectory Pre-Order* serialization contract, realizes optimistic concurrency control with three-phase undoable tool calls, and adds a notification mechanism so an agent whose premises are invalidated repairs only the affected actions. This is database concurrency control for agents — a *transactional* answer where AgentMPI proposes a *message-passing* one. Position against it explicitly.
- **MPAC** [MPAC 2026] — a five-layer application protocol (session / intent / operation / conflict / governance) with 21 message types, three normative state machines, **Lamport-clock watermarking for causal ordering**, explicit consistency and execution models, atomic batch operations, and crash takeover.
- **SSVP** [SSVP 2026] — **the most important threat to AgentMPI's thesis.** In controlled experiments, *naive full-broadcast synchronization increased hallucination rate 34% above a no-sync baseline* (0.658 vs 0.492, p=0.0022, d=1.18), attributed to "indiscriminate propagation of erroneous agent states," while a selective divergence-triggered protocol avoided the contamination and used 58% fewer API calls. Read literally: `MPI_Bcast`/`MPI_Allgather` semantics applied to agent belief state can be *actively harmful*. Any AgentMPI design offering unfiltered collectives must address this — perhaps by scoping collectives to *artifacts and control state* rather than beliefs, or requiring a reduction operator that adjudicates rather than concatenates. *(Caveat: single-model, modest-n preprint; the effect did not replicate in their software-planning domain.)*
- **Mesh Memory Protocol** [MMP 2026] — argues for a "semantic infrastructure" layer with field-level acceptance, content-hash lineage so returning claims are recognized as echoes, and cross-session memory. Frames the missing thing as *memory*, not *messaging*.
- **AgentMesh** [AgentMesh 2026] — "TCP/IP for AI agents": Ed25519 agent identity (`agent://name-hash`), signed message envelope (RFC-001), capability registry, reputation. Identity/transport layer, no collectives.

Two takeaways. The gap is real and independently observed — good for motivation. And the space is filling fast, so AgentMPI's differentiation must be crisp: *collective operations with defined semantics over an explicit group/communicator abstraction, with a stated consistency model and fault contract* — not "structured messages," not "shared memory," not "governance," not "transactions."

---

## 4. Classical distributed and parallel programming models

### 4.1 Actors

[Hewitt et al. 1973] introduced actors; [Agha 1986] gave the formal model. **Erlang/OTP** contributed what the LLM-agent world most conspicuously lacks: *let it crash*, supervision trees, links/monitors, and process isolation as the unit of failure. **Akka** guarantees at-most-once delivery and FIFO ordering *per sender–receiver pair*, with actors created in a supervision hierarchy that drives exception handling and location fixed at creation [Bernstein et al. 2014]. **Orleans** introduced **virtual actors**, whose four facets AutoGen v0.4 and AgentScope reinvent: perpetual logical existence, automatic instantiation on message arrival, location transparency, and automatic recovery — "if a server crashes, the [runtime] re-instantiates the actor on another server, eliminating the need for applications to supervise and explicitly re-create failed actors" [Bernstein et al. 2014, Microsoft Research 2014]. **Charm++** [Kalé & Krishnan 1993] contributes migratable *chares* with a measurement-based load balancer and asynchronous method invocation — the closest classical model to "many small, dynamically created, migratable computational agents," and a citation the paper should not miss.

### 4.2 PGAS and the HPC coordination canon

**MPI** is the anchor. The current standard is **MPI 5.0 (5 June 2025)**, whose headline change is a standard **ABI** for cross-implementation interoperability [MPI Forum 2025]. Its chapter list is effectively AgentMPI's design checklist: point-to-point, **collective communication** (including nonblocking §6.12 and persistent §6.13 collectives), **groups/contexts/communicators**, process topologies, one-sided communication, **the Sessions model** (§11.3), neighborhood collectives, I/O, and tool interfaces. That MPI 4.x/5.0 added nonblocking and persistent collectives matters directly: agent collectives will be long-latency, so the split-phase idiom (`MPI_Ibarrier`-style) is the right analogue to borrow rather than invent.

**PGAS:** UPC, Coarray Fortran, OpenSHMEM (one-sided put/get with explicit fences and barriers), **Chapel** (locales, `forall`, domain maps), **X10** (places, `async`/`finish`, where `finish` is precisely a scoped barrier), **Legion** (logical regions with declared privileges/coherence, from which the runtime *derives* dependencies). Legion is the most instructive analogy: if a program declares what data a task reads and writes, a runtime can extract parallelism and enforce coherence automatically — exactly what CoAgent's read/write footprints [CoAgent 2026] and Parrot's Semantic Variables [Lin et al. 2024] grope toward. For a shared-state story defensible at an HPC venue, Legion's privileges/coherence framing is the model to adapt.

### 4.3 Dataflow and big data

MapReduce, Dryad (arbitrary DAGs), Spark RDDs (lineage-based recovery — recompute rather than replicate, the intellectual ancestor of Temporal/Inngest replay), and Naiad/timely dataflow (logical timestamps and *frontiers*, giving progress tracking and coordinated notification without a global barrier). Timely dataflow's frontier is arguably a better model than a hard barrier for agent workloads, where the useful guarantee is "all information up to logical time *t* has arrived" rather than "everyone stop."

### 4.4 Tuple spaces and blackboards — the direct ancestors

**Linda** [Gelernter 1985] specified that "messages be added in tuple-structured form to the computation environment, where they exist as named, independent entities until some process chooses to receive them," yielding time- and space-uncoupling, associative addressing and structured naming — with the noted limitation that "there is no possibility of directing a tuple to a specified receiver only." Compare MetaGPT's global message pool with `cause_by` subscription tags [Hong et al. 2024], AutoGen's `TypeSubscription` [AutoGen Core Docs 2025], and Claude Code's shared task list with file-lock claiming [Claude Code Docs 2026c]. All three are tuple spaces. **None cites Linda.** Naming the pattern — and observing that the field has rebuilt generative communication three times without the vocabulary or the known results — is a genuine contribution the paper can make cheaply.

**Blackboard architectures** [Erman et al. 1980, Fennell & Lesser 1977] gave the canonical structure: multiple diverse, independent, asynchronously executing knowledge sources cooperating by data-directed invocation on a "shared blackboard-like data base." Magentic-One's ledgers and AutoGen GroupChat are blackboards with an LLM as the control component.

**Contract Net** [Smith 1980] gave task announcement → bidding → award → result, a decentralized market for task allocation. CrewAI's hierarchical manager and Anthropic's lead-agent delegation are contract nets *without the bidding step*: the manager assigns rather than solicits bids — an available, unexploited design point.

### 4.5 KQML, FIPA-ACL, JADE — the cautionary precedent

This is the most important historical section, because AgentMPI proposes an agent communication standard and the last two attempts largely failed.

**KQML** structured messages around **performatives** (`ask`, `tell`, `reply`) plus keyword/value parameters. [Labrou et al. 1999] — the definitive retrospective, by KQML's own authors — diagnosed "serious signs of immaturity" after eight years: "(1) in general, different KQML implementations can not interoperate; (2) there is no fixed specification sanctioned by some consensus-creating body; and (3) there is no agreed-upon semantics foundation." There was also no security or authentication model [Huhns & Singh 1997]. **FIPA-ACL** answered with formal speech-act semantics grounded in mental states (belief, intention), a smaller composable performative set, interaction protocols (request, contract-net, query, subscribe), and a security model; **JADE** was its dominant implementation.

**Why they largely failed** — four causes AgentMPI must be checked against:

1. **Unverifiable semantics.** FIPA-ACL's meaning depends on private mental states that cannot be externally verified, so conformance is undecidable in an open system [Labrou et al. 1999, ToAI 2026].
2. **The pragmatics mattered more, and were left out.** [Labrou et al. 1999] are explicit: "the semantics issue is in practice much less important than it sounds as long as the problem of defining and identifying conformance to the semantics is not resolved"; what programmers find much more pressing is naming, registration, authentication, and basic facilitation services. The semantics debate "monopolized… at the expense of other important pragmatic issues."
3. **Wrong substrate assumption.** FIPA presupposed symbolic reasoners with shared ontologies — "a world that never quite arrived" [VIPS 2026]. LLM agents speak natural language plus tool calls.
4. **Chicken-and-egg adoption.** "Your FIPA agent might not find anyone to communicate with" [Huhns & Singh 1997].

**Lesson.** MPI is the right precedent to imitate on exactly the axes where FIPA-ACL failed: its semantics are operational and externally checkable (a collective either completes on all ranks or errors), it standardized the pragmatics (naming via communicators/ranks, group construction, error classes, an ABI in 5.0), it made no claims about participant internals, and it succeeded by standardizing what a library must *do* rather than what a process must *believe*. "AgentMPI is deliberately MPI-shaped rather than FIPA-shaped" is a strong framing — and the survival of FIPA performative vocabulary inside A2A and ACP [VIPS 2026] shows the ideas were not wrong, only mislayered.

### 4.6 Dec-POMDPs and MARL (brief, as instructed)

The theory of decentralized cooperative control is Dec-POMDPs [Bernstein et al. 2002], NEXP-complete in the finite-horizon case, formalizing why coordination without communication is intractable and why explicit communication actions are usually necessary. Relevance is mostly framing, with one concrete tie-in: **τ²-bench models its setting as a dual-control Dec-POMDP in which both the agent and the simulated user act on a shared world state** [Barres et al. 2025] — a benchmark whose formalism already matches the coordination problem, and a good venue for AgentMPI experiments.

---

## 5. Empirical evidence on multi-agent failure

### 5.1 MAST (Cemri et al.)

The essential citation. Method: Grounded Theory analysis of MAS execution traces (each averaging >15,000 lines) across **7 popular open-source MAS frameworks** — AppWorld, HyperAgent, AG2, ChatDev, MetaGPT, Magentic-One, OpenManus — with six expert annotators and inter-annotator agreement **Cohen's κ = 0.88**; the current version reports **MAST-Data**, 1600+ annotated traces, with the taxonomy developed from ~150 traces and distributions computed over 210 [Cemri et al. 2025].

**The 14 modes in 3 categories** (per-mode incidence from MAST-Data):

*FC1 — System Design / Specification Issues (41.8%)*
- FM-1.1 Disobey task specification (11.8%)
- FM-1.2 Disobey role specification (1.5%)
- FM-1.3 Step repetition (15.7%)
- FM-1.4 Loss of conversation history — "unexpected context truncation, disregarding recent interaction history and reverting to an antecedent conversational state" (2.8%)
- FM-1.5 Unaware of termination conditions (12.4%)

*FC2 — Inter-Agent Misalignment (36.9%)*
- FM-2.1 Conversation reset (2.2%)
- FM-2.2 Fail to ask for clarification (6.8%)
- FM-2.3 Task derailment (7.4%)
- FM-2.4 Information withholding (0.85%)
- FM-2.5 Ignored other agent's input (1.9%)
- FM-2.6 Reasoning-action mismatch (13.2%)

*FC3 — Task Verification (21.3%)*
- FM-3.1 Premature termination (6.2%)
- FM-3.2 No or incomplete verification (8.2%)
- FM-3.3 Incorrect verification (9.1%)

**Why this is the best motivation — and the sharpest challenge.** Roughly 37% of failures are communication and coordination failures, and several map nearly one-to-one onto missing MPI primitives: FM-1.5 (unaware of termination) is the absence of a barrier or agreed termination detection; FM-1.4 and FM-2.1 are the absence of a consistency model over shared history; FM-3.1 is the absence of a completion collective; FM-2.4/2.5 are the absence of a defined delivery-and-acknowledgement contract.

But MAST also lands a direct hit on protocol-level fixes, and honesty requires quoting it: "Recent system innovations, such as Model Context Protocol and Agent to Agent, improve agent communication by standardizing message formats… **However, the errors we observe in FC2 occur even when agents within the same framework communicate using natural language.** This signals a deeper agent interaction dynamic challenge: the collapse of 'theory of mind'… Addressing this likely requires structural improvements to the content of agent messages or enhancing models' contextual reasoning." Their Insight 2: "Solutions focused on context or communication protocols are often insufficient for FC2 failures, which demand deeper 'social reasoning' abilities." Yet they also list "establishing a standardized communication protocol" as a structural strategy: "LLM-based agents mainly communicate via unstructured text, leading to ambiguities. Clearly defining intentions and parameters enhances alignment and **enables formal coherence checks during and after interactions**."

The defensible position: AgentMPI targets the *mechanically checkable* subset — termination, delivery, membership, progress, consistency — and claims nothing about theory of mind. Their interventions support that structure matters: improving role specification alone gave ChatDev **+9.4%**, and adding a high-level task-objective verification step gave **+15.6%** on ProgramDev.

### 5.2 Does multi-agent help at all?

The evidence is genuinely mixed and the paper must not overclaim.

Against: [Wang et al. 2024] (EMNLP) show that under matched compute, CoT + self-consistency "often outperforms" multi-agent debate, Reflexion, plan-and-solve, least-to-most and progressive-hint prompting, and that MAD and Reflexion can *degrade* with more budget. [SAS-vs-MAS 2026] argue from the Data Processing Inequality that under a fixed reasoning-token budget with perfect context utilization single agents are more information-efficient, confirming empirically across FRAMES/MuSiQue, three model families and five MAS topologies that "SAS consistently match or outperform MAS when reasoning tokens are held constant, unless context utilization is degraded to a certain point." [MAD-Fail 2025] show both dominant debate paradigms suffer "debate hacking" — competitive MAD degenerates to cheap talk (up to **15 pp** worse than single-agent), consensus-seeking MAD filters out informative disagreement — and that a redesigned collaborative protocol recovers up to **+4 pp**, concluding "proper protocol design is critical." [Consensus-Cost 2026] document sycophantic conformity up to 85.5% (95.4% at 32B), contextual fragility up to 70%, and consensus collapse with oracle gaps up to 32.3 pp, at a 2.1–3.4× token multiplier over isolated self-correction.

For: [Anthropic 2025]'s +90.2%, and [Li et al. 2024]'s sampling-and-voting result.

The synthesis in the critical literature is that **architecture–task alignment, not agent count, determines success**, and that compute normalization is mandatory for fair comparison. Two implications: every AgentMPI experiment must report a compute-matched single-agent baseline; and the defensible claim is not "multi-agent is better" but "*given* that you are running multiple agents, these primitives make it cheaper, more reliable, or more reproducible."

### 5.3 Anthropic vs. Cognition — and Cognition's reversal

This is now a three-post arc; using only the first two would be out of date.

[Yan 2025] ("Don't Build Multi-Agents") argued from Devin experience that "running multiple agents in collaboration only results in fragile systems. The decision-making ends up being too dispersed and context isn't able to be shared thoroughly enough between the agents," giving two principles: share full agent *traces*, not just messages; and actions carry implicit decisions, so conflicting decisions produce bad results. Crucially: "I don't see anyone putting a dedicated effort to solving this difficult cross-agent context-passing problem." [Anthropic 2025], published a day later, reached compatible conclusions from the other direction: multi-agent wins where work is breadth-first and context exceeds one window, and loses where agents must share context or have many dependencies.

[Yan 2026] ("Multi-Agents: What's Actually Working," ~10 months later) is the update the paper needs:

- Revised position: "we've begun to deploy multi-agent systems that actually work… setups where multiple agents contribute intelligence to a task **while writes stay single-threaded**."
- Empirical: Devin Review catches an average of **2 bugs per PR** on Devin-written PRs, ~58% severe — and works *best when coding and review agents share no context beforehand*, justified by context rot and attention math.
- The "smart friend" pattern (weak primary consults a strong model) failed with an asymmetrically weaker primary and worked across frontier models as a *capability router*.
- Manager Devins spawn child Devins "and coordinate their progress **through an internal MCP**."
- Enumerated coordination failures: managers default to over-prescription; "agents assume they share state with their children when they don't"; "cross-agent communication… doesn't happen by default, because models haven't been trained in environments where it needed to."
- Verdict on swarms: "the unstructured-swarm approach, arbitrary networks of agents negotiating with each other, is mostly a distraction. The practical shape is **map-reduce-and-manage**."
- Closing: "**The open problems are all communication problems.**"

Two consequences. "The open problems are all communication problems," from the vendor that originally said don't build multi-agents, is the strongest motivating quote available. And "writes stay single-threaded" plus "map-reduce-and-manage" constrain AgentMPI's design: the demonstrated-safe topology is scatter/gather with a single writer, mapping onto `MPI_Scatter`/`MPI_Gather`/`MPI_Reduce` far better than onto one-sided RMA into shared state. Cognition also believes part of this is a *training* problem rather than an API problem — a limitation to concede.

### 5.4 Context rot, lost-in-the-middle, and error propagation

[Liu et al. 2024] (TACL) is the canonical positional result: performance is highest when relevant information sits at the beginning or end and "significantly degrades when models must access relevant information in the middle of long contexts, even for explicitly long-context models," while "performance substantially decreases as the input context grows longer." Secondary summaries report >30% accuracy drops for mid-context placement in 20-document multi-document QA [Morph 2026] — exact figure `[UNVERIFIED]`, qualitative U-curve solidly established.

[Hong et al. 2025] (Chroma) generalized this to **context rot**: degradation with length holds even when the added tokens are *relevant* and task difficulty is held constant, across 18 models, with needle–question similarity, distractor interference, haystack structure, LongMemEval conversational QA and repeated-word probes. Follow-on work studies context rot in long-horizon agentic search, where hundreds of tool calls accumulate environment feedback plus internal reasoning [ContextRot-Search 2026].

**Why this matters structurally.** Context rot is simultaneously the physical justification for multi-agent architectures (separate windows are the only way to add usable capacity — [Anthropic 2025] measured token usage explaining 80% of variance) and the mechanism that makes coordination hard (every delivered message consumes the receiver's scarce, degrading attention budget). Combined with SSVP's broadcast-contamination finding [SSVP 2026], this yields a precise design constraint: **a collective that delivers O(n) messages to every participant is not merely expensive, it is actively degrading.** AgentMPI's reductions must reduce — with an operator, to a bounded artifact — not gather. If the paper makes one technical argument a systems audience will find novel, this should be it.

### 5.5 Cost

- Multi-agent ≈ **15× chat tokens**; single agents ≈ 4× [Anthropic 2025].
- Debate ≈ **2.1–3.4×** isolated self-correction for statistically comparable or worse accuracy at 7–8B [Consensus-Cost 2026].
- Claude Code agent teams: token usage "scales with the number of active teammates," and in-process teammates fall outside the main conversation's cache TTL bucket (5 min default; `subagentPromptCacheTtl: 1h` at higher write cost) [Claude Code Docs 2026c].
- Prefix caching is the main lever: shared system prompts and tool schemas computed once via APC or RadixAttention [Kwon et al. 2023, Zheng et al. 2024]. **Cache affinity is a first-class protocol design concern:** Autellix already routes long calls back to their program's engine to preserve KV locality [Luo et al. 2025]. If AgentMPI's group/communicator abstraction carries cache-affinity hints, the fabric can exploit them — a concrete, measurable systems win.

---

## 6. Benchmarks and evaluation methodology

### 6.1 The benchmark landscape

**Software engineering.** SWE-bench [Jimenez et al. 2024] → SWE-bench Verified (500 human-validated tasks, OpenAI, Aug 2024) [Chowdhury et al. 2024] → SWE-bench Multimodal (frontend/JS) [Yang et al. 2024b]. **Verified is now saturating:** a third-party leaderboard reports seven of 86 evaluated models at ≥95% and a leader at 97.0% under a minimal bash-only harness [Vals 2026] — specific numbers `[UNVERIFIED]`, but the saturation conclusion is robust and means SWE-bench Verified should **not** be a headline metric in a 2026 paper. **SWE-Lancer** [Miao et al. 2025] is the better-designed successor: real Upwork freelance tasks mapped to economic payout, IC-SWE tasks graded by triple-verified end-to-end Playwright tests, plus SWE-Manager tasks; explicitly motivated by unit-test grader hacking in prior benchmarks. **Terminal-Bench 2.0** is the current agentic-terminal standard and is far from saturated (top entries in the 60s%, with published confidence intervals) [Terminal-Bench 2026].

**General agency / web / tool use.** GAIA [Mialon et al. 2023] (466 questions, easy for humans, hard for systems); **GAIA2** adds event-driven, asynchronous, dynamic environments to test temporal dynamics [Meta 2025]; WebArena [Zhou et al. 2024] (812 long-horizon tasks in self-hosted Docker sites with functional-correctness evaluation; GPT-4 14.41% vs. human 78.24% at publication); AssistantBench [Yoran et al. 2024] (214 realistic time-consuming web tasks over 258 sites; best reported 25.2%); AgentBench [Liu et al. 2023]; OSWorld [Xie et al. 2024]; **τ-bench / τ²-bench** [Yao et al. 2024, Barres et al. 2025] — τ-bench introduced **pass^k** (probability *all k* trials succeed) as a reliability metric distinct from one-shot pass rate, and τ² models a dual-control Dec-POMDP with shared world state.

**Research / ML engineering / reproducibility.** MLE-bench [Chan et al. 2024] (Kaggle competitions, human-relative medals, deliberately more open-ended than SWE-bench); CORE-Bench [Siegel et al. 2024] (270 tasks / 181 questions over 90 reproducible CodeOcean capsules at three difficulty levels; best agent 21% at the hardest level).

**Diagnostic benchmarks — new and directly relevant.** A 2026 survey documents a shift from outcome-only leaderboards toward trajectory diagnosis: AgentRx (115 annotated failed trajectories, nine-category taxonomy), ATBench (1,000 long-horizon traces), AgentProcessBench (1,000 trajectories, 8,509 step annotations), plus security suites (AgentDojo: 97 tasks / 629 prompt-injection cases; MCPSecBench; MCPTox) [AgentAtlas 2026]. Its central methodological warning is the one AgentMPI must answer — **scaffold sensitivity**: "agent-s3 w/ GPT-5 moves 65.6 → 69.9% on OSWorld just by switching single-shot to best-of-10, and Anthropic's Claude Code stack alone spans a 50 pp range on CCBench across four versions… Outcome-only scores increasingly measure agent-system engineering rather than the LLM alone." For a *protocol* paper this cuts both ways: harness variance is exactly the confound that makes a protocol paper hard to evaluate, and exactly the reason a standardized coordination layer is valuable.

### 6.2 Translation-quality metrics (for a possible translation case study)

Lineage: BLEU [Papineni et al. 2002] and chrF [Popović 2015] (character n-grams; both via sacreBLEU [Post 2018] for reproducible tokenization); learned metrics BLEURT [Sellam et al. 2020], COMET [Rei et al. 2020, Rei et al. 2022a] and reference-free COMET-Kiwi [Rei et al. 2022b], MetricX [Juraska et al. 2023, Juraska et al. 2024]; human protocols MQM [Lommel et al. 2014, Freitag et al. 2021] and ESA [Kocmi et al. 2024]; LLM-as-judge via GEMBA [Kocmi & Federmann 2023].

The current citable state comes from WMT25, which unified the Metrics and QE shared tasks into one Automated Translation Evaluation Systems task with three subtasks (segment-level scoring, span-level error annotation, quality-informed error correction) [Lavie et al. 2025]. Its headline findings are what a systems paper should quote when justifying metric choice: "Task 1 results indicate the strong performance of large LLMs at the system level, while reference-based baseline metrics outperform LLMs at the segment level. Task 2 results indicate that accurate error detection and balancing precision and recall are persistent challenges… Robustness across the broad diversity of languages remains a major challenge across all three subtasks."

Practical recommendation: report chrF (cheap, reproducible via sacreBLEU) **and** a learned metric (COMET-22 or MetricX-25) with the model version pinned; do not report BLEU alone; if using LLM-as-judge, report it as *system-level* evidence only, disclose judge model and version, and expect segment-level unreliability — WMT25 says the baselines beat it there.

### 6.3 How to report agent experiments credibly

The strongest recent methodological result: **single-run pass@1 estimates vary by 2.2–6.0 percentage points depending on which run is chosen, with standard deviations exceeding 1.5 pp even at temperature 0**; trajectories diverge within the first few percent of tokens and cascade into different strategies. Concretely, **detecting a 1% improvement at median variance requires ~36 runs**; at the lowest observed variance (σ=0.7%) ~8 runs; a 10% improvement may be detectable in one [Randomness 2026]. Recommendations: estimate pass@1 from multiple independent runs; do power analysis to size the run count; and report the *performance envelope* — pass@1 (expected), pass@k (optimistic with retries) and **pass^k** (pessimistic consistency), where the pass@k − pass^k gap quantifies how much stochasticity helps or hurts.

Additional practices from the same literature:

- **Freeze or replay the environment.** "A repeated run is not the same experiment unless those inputs are fixed or replayed" — seeds control intrinsic sampling only; live web pages, DB rows, clocks and API failures are extrinsic variance seeding cannot touch [Token-Not-Taken 2026].
- **Report cost as a first-class axis**, not a footnote: cost-per-task alongside accuracy, ideally a reliability-per-dollar ratio, since cost-aware ranking can invert accuracy ranking [Deployment-Reliability 2026].
- **Report harness and version.** Given the documented 50 pp swing across versions of one agent stack [AgentAtlas 2026], pin agent version, model version, scaffold and tool set.
- **Guard against runaway cost** with per-episode token budgets and loop detection, and note that *infrastructure* reliability is part of measured reliability — one study had to replace models entirely because a single-provider pin returned HTTP 404 throughout a run, concluding "multi-provider routing is a prerequisite for reproducible long-horizon evaluation" [Reliability-Science 2026].
- **Don't conflate partial credit with resolution.** A macro-averaged hidden-test pass rate of 0.80 co-occurred with 1 of 5 tasks actually resolved in a small SWE-bench Verified pilot [Beyond-Pass-k 2026].

For AgentMPI specifically, a compelling HPC-venue evaluation would report: task success (pass@1 with n≥8–36 runs and CIs, plus pass^k), **tokens per task** and **dollars per task**, **wall-clock and critical-path latency**, **messages and bytes exchanged**, **KV-cache hit rate / prefill saved**, **scaling curves in agent count** (the axis nobody reports), and **variance across seeds** — with a compute-matched single-agent baseline in every table.

---

## 7. Synthesis: the precise shape of the gap

1. **Frameworks have coordination mechanisms; none are portable.** ADK has a fan-in barrier (`JoinNode`), LangGraph checkpointed super-steps with pending-writes recovery, AutoGen typed pub/sub, MetaGPT a subscription-triggered dependency barrier, Claude Code teams a lock-protected shared task list. Each is reinvented per framework, none is expressible across frameworks, and the protocols that span frameworks carry none of them.
2. **Protocols standardize the vertical and horizontal *boundaries*, not the *group*.** MCP is agent↔tool and becoming *more* stateless; A2A is agent↔agent task delegation and explicitly disclaims shared memory. Both have a unit of one: one tool call, one task, one peer.
3. **The field's own taxonomies lack the columns.** An 18-protocol, nine-dimension survey has no dimension for collectives, membership or consistency [Semantic View 2026].
4. **Practitioners hand-roll the primitives and document it.** No broadcast; coordination tunnelled through MCP; file locks for mutual exclusion; dependency graphs for release; "agents assume they share state with their children when they don't."
5. **Failures cluster where the primitives are missing.** ~37% of MAS failures are inter-agent misalignment, and termination-awareness, premature termination, conversation reset and context loss are a large fraction of the rest [Cemri et al. 2025].
6. **The serving fabric already reverse-engineers what a protocol would declare** — Parrot's Semantic Variables, Autellix's program interception, RadixAttention's prefix hashing, Ayo's task primitives.
7. **History says the pragmatics are what matter.** KQML/FIPA-ACL failed on interoperability, conformance, naming, registration and authentication while the community argued about mental-state semantics [Labrou et al. 1999]. MPI succeeded by standardizing operational behavior and, in 5.0, an ABI.

## 8. Threats to the paper's thesis (write these in before a reviewer does)

- **Broadcast can be harmful.** Full-broadcast synchronization increased hallucination 34% over no-sync in one controlled study [SSVP 2026]. Collectives over *belief state* may be the wrong primitive; design for reductions over artifacts and control state, with adjudicating operators.
- **Every delivered message costs scarce, degrading attention** [Liu et al. 2024, Hong et al. 2025]. An O(n²) all-to-all is not just slow, it is cognitively destructive. Argue in terms of *bounded* collectives.
- **MAST's Insight 2 says protocols are insufficient for FC2** [Cemri et al. 2025]. Scope the claim to the mechanically checkable subset.
- **Cognition thinks parts of this are a training problem** [Yan 2026]. Concede that an API cannot make a model know when to escalate.
- **Compute-matched single agents often win** [Wang et al. 2024, SAS-vs-MAS 2026]. Never claim multi-agent superiority; claim improved cost/reliability *within* the multi-agent regime.
- **A2A extensions could host this.** Argue on semantics, and consider shipping an A2A extension profile as evidence of practicality rather than positioning as a rival.
- **The space is filling.** CoAgent (transactional), MPAC (five-layer coordination with Lamport clocks), MMP (semantic memory) and AgentMesh (identity/transport) are all 2026 and all in the neighborhood. Differentiate crisply: *groups/communicators + collectives with defined completion and error semantics + a stated consistency model*.
- **Coordination-heavy multi-agent systems demonstrably run at 3–5 agents, not 300.** MPI's value proposition is scale. If AgentMPI's evaluation cannot show a regime where more agents help, the MPI analogy will read as aesthetic rather than functional.

---

## References

[A2A Docs 2026a] A2A Project. *What's New in v1.0.* a2a-protocol.org, 2026. https://a2a-protocol.org/v1.0.0/whats-new-v1/ (accessed 2026-08-30).

[AAIF 2026] Agentic AI Foundation. *A2A joins AAIF's open agentic stack.* 2026. https://aaif.io/blog/a2a-joins-aaif (accessed 2026-08-30).

[ADK Docs 2026a] Google. *Welcome to ADK 2.0 / Dynamic workflows.* adk.dev, 2026. https://adk.dev/2.0/ , https://adk.dev/graphs/dynamic/ (accessed 2026-08-30).

[ADK Docs 2026b] Google. *Graph routes (JoinNode fan-in barrier).* adk.dev, 2026. https://adk.dev/graphs/routes/ (accessed 2026-08-30).

[AgentAtlas 2026] *AgentAtlas: Beyond Outcome Leaderboards for LLM Agents.* arXiv preprint, 2026. https://doi.org/10.48550/arxiv.2605.20530

[AgentMarketCap 2026] AgentMarketCap. *Microsoft Agent Framework 1.0 GA.* 2026-04-13. (secondary source; orchestration-pattern enumeration `[UNVERIFIED]`).

[AgentMesh 2026] AgentMesh Protocol. *agentmesh-sdk / RFC-001 Message Envelope.* GitHub, 2026. https://github.com/agentmesh-protocol/agentmesh-sdk (accessed 2026-08-30).

[AgentScope 2.0 Docs 2026] AgentScope. *What's AgentScope 2.0? / Harness architecture / Agent Service deep dive.* docs.agentscope.io, java.agentscope.io, 2026.

[Agha 1986] G. Agha. *Actors: A Model of Concurrent Computation in Distributed Systems.* MIT Press, 1986.

[Agrawal et al. 2026] L. Agrawal et al. *GEPA: Reflective Prompt Evolution.* ICLR 2026 (Oral); `dspy.GEPA`. https://github.com/gepa-ai/gepa

[AIOS Docs 2026] AIOS Foundation. *AIOS kernel: scheduler (FIFOScheduler, RRScheduler).* docs.aios.foundation, 2026.

[Anthropic 2025] Anthropic. *How we built our multi-agent research system.* Anthropic Engineering, 2025. https://www.anthropic.com/engineering/multi-agent-research-system (accessed 2026-08-30).

[Anyscale 2026] Anyscale. *AI agents on Ray Serve: Single to multi-agent architecture.* 2026.

[AutoGen Core Docs 2025] Microsoft. *AutoGen Core: Agent and Agent Runtime; Message and Communication.* microsoft.github.io/autogen, 2025.

[AutoGen Discussion 2025] Microsoft AutoGen team. *AutoGen Update* (merger into Microsoft Agent Framework). GitHub Discussion #7066, 2025.

[AutoGen Docs 2025] Microsoft. *Migration Guide for v0.2 to v0.4.* microsoft.github.io/autogen, 2025.

[Barres et al. 2025] V. Barres et al. *τ²-bench: Evaluating Conversational Agents in a Dual-Control Environment.* 2025.

[Bernstein et al. 2002] D. S. Bernstein, R. Givan, N. Immerman, S. Zilberstein. The Complexity of Decentralized Control of Markov Decision Processes. *Mathematics of Operations Research* 27(4), 2002.

[Bernstein et al. 2014] P. A. Bernstein, S. Bykov, A. Geller, G. Kliot, J. Thelin. *Orleans: Distributed Virtual Actors for Programmability and Scalability.* MSR-TR-2014-41, 2014.

[Beyond-Pass-k 2026] *Beyond Pass@k: Measuring Reliability and Security of Agentic Code Generation.* arXiv:2608.14711, 2026.

[Cemri et al. 2025] M. Cemri et al. *Why Do Multi-Agent LLM Systems Fail?* arXiv:2503.13657 (v2 and later MAST-Data revision), 2025. https://github.com/multi-agent-systems-failure-taxonomy/MAST

[Chan et al. 2024] J. S. Chan et al. *MLE-bench: Evaluating Machine Learning Agents on Machine Learning Engineering.* arXiv:2410.07095, 2024.

[ChatDev Issue 2023] OpenBMB/ChatDev. *What's the difference between ChatDev and MetaGPT?* GitHub Issue #24, 2023.

[Chopra et al. 2024] A. Chopra et al. *On the limits of agency in agent-based models* (AgentTorch; 8.4M agents). 2024.

[Chowdhury et al. 2024] N. Chowdhury et al. *Introducing SWE-bench Verified.* OpenAI, 2024.

[Claude Code Docs 2026a] Anthropic. *Create custom subagents.* code.claude.com/docs, 2026.

[Claude Code Docs 2026b] Anthropic. *Subagents in the SDK; Tools reference (Agent tool).* code.claude.com/docs, 2026.

[Claude Code Docs 2026c] Anthropic. *Agent teams.* code.claude.com/docs, 2026 (as of v2.1.178+).

[CoAgent 2026] *CoAgent: Concurrency Control for Multi-Agent Systems* (MTPO protocol). arXiv:2606.15376, 2026.

[Consensus-Cost 2026] *The Cost of Consensus: Isolated Self-Correction Prevails Over Unguided Homogeneous Multi-Agent Debate.* arXiv:2605.00914, 2026.

[ContextRot-Search 2026] *Diagnosing and Mitigating Context Rot in Long-horizon Search.* arXiv:2606.29718, 2026.

[Coral 2025] R. Georgio et al. *The Coral Protocol: Open Infrastructure Connecting the Internet of Agents.* arXiv:2505.00749, 2025.

[CrewAI Docs 2026] CrewAI. *Hierarchical Process; Processes.* docs.crewai.com, 2026.

[CrewAI GitHub 2026] CrewAI Inc. *crewAI README (Crews and Flows).* GitHub, 2026.

[Deployment-Reliability 2026] *Deployment Decision Reliability: A Generalizability-Theory Framework for Sizing Long-Horizon Agent Evaluations.* arXiv:2608.11323, 2026.

[Dify Docs 2026] Dify. *Agent node.* docs.dify.ai, 2026.

[DSPy Docs 2026] Stanford NLP. *MIPROv2 optimizer API.* dspy docs, 2026.

[Ehtesham et al. 2025] A. Ehtesham et al. *A Survey of Agent Interoperability Protocols: MCP, ACP, A2A, and ANP.* arXiv:2505.02279, 2025.

[Erman et al. 1980] L. D. Erman, F. Hayes-Roth, V. R. Lesser, D. R. Reddy. The Hearsay-II Speech-Understanding System. *ACM Computing Surveys* 12(2), 1980.

[Fennell & Lesser 1977] R. D. Fennell, V. R. Lesser. Parallelism in AI Problem Solving: A Case Study of Hearsay II. *IEEE Trans. Computers* C-26(2), 1977.

[Fourney et al. 2025] A. Fourney et al. *Magentic-UI: Towards Human-in-the-loop Agentic Systems.* arXiv:2507.22358, 2025.

[Freitag et al. 2021] M. Freitag et al. Experts, Errors, and Context: A Large-Scale Study of Human Evaluation for Machine Translation. *TACL* 9, 2021.

[Gao et al. 2024a] D. Gao et al. *AgentScope: A Flexible yet Robust Multi-Agent Platform.* arXiv:2402.14034, 2024.

[Gao et al. 2024b] X. Pan, D. Gao et al. *Very Large-Scale Multi-Agent Simulation in AgentScope.* arXiv:2407.17789, 2024.

[Gelernter 1985] D. Gelernter. Generative Communication in Linda. *ACM TOPLAS* 7(1), 1985.

[Georgio et al. 2025] see [Coral 2025].

[Hewitt et al. 1973] C. Hewitt, P. Bishop, R. Steiger. A Universal Modular ACTOR Formalism for Artificial Intelligence. *IJCAI*, 1973.

[Hong et al. 2024] S. Hong et al. *MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework.* ICLR 2024; arXiv:2308.00352.

[Hong et al. 2025] K. Hong, A. Troynikov, J. Huber. *Context Rot: How Increasing Input Tokens Impacts LLM Performance.* Chroma Technical Report, 2025.

[Hückmann 2026] D. Hückmann. *Agent Protocols Are Becoming a Stack, Not a Winner-Takes-All Standard.* 2026-06-22. (secondary; layering framing)

[Huhns & Singh 1997] M. N. Huhns, M. P. Singh. Conversational Agents. *IEEE Internet Computing* 1(2), 1997.

[IBM 2026] IBM. *What is Agent Communication Protocol (ACP)?* (with merger notice). ibm.com/think/topics/agent-communication-protocol, 2026.

[Inngest Docs 2026] Inngest. *Durable Agents.* inngest.com/docs, 2026.

[Jimenez et al. 2024] C. E. Jimenez et al. *SWE-bench: Can Language Models Resolve Real-World GitHub Issues?* ICLR 2024.

[Juraska et al. 2023] J. Juraska et al. *MetricX-23: The Google Submission to the WMT 2023 Metrics Shared Task.* WMT 2023, pp. 756–767.

[Juraska et al. 2024] J. Juraska et al. *MetricX-24.* WMT 2024. (and MetricX-25, WMT 2025)

[Kalé & Krishnan 1993] L. V. Kalé, S. Krishnan. CHARM++: A Portable Concurrent Object Oriented System Based on C++. *OOPSLA*, 1993.

[Kang & Diponegoro 2026] R. Kang, Y. Diponegoro. *Governance Gaps in Agent Interoperability Protocols: What MCP, A2A, and ACP Cannot Express.* arXiv:2606.31498, 2026. *(Preprint; 0 citations; AI-assistance declared. Cite for checkable specification classifications only.)*

[Kocmi & Federmann 2023] T. Kocmi, C. Federmann. *Large Language Models Are State-of-the-Art Evaluators of Translation Quality* (GEMBA). EAMT 2023.

[Kocmi et al. 2024] T. Kocmi et al. *Error Span Annotation (ESA).* WMT 2024.

[Kong et al. 2026] Survey of agent communication protocols incl. LMOS and AGNTCY. *Future Internet* 18(171), 2026. (MDPI)

[Kwon et al. 2023] W. Kwon et al. *Efficient Memory Management for Large Language Model Serving with PagedAttention.* SOSP 2023 (vLLM).

[Labrou et al. 1999] Y. Labrou, T. Finin, Y. Peng. Agent Communication Languages: The Current Landscape. *IEEE Intelligent Systems* 14(2), 1999. DOI 10.1109/5254.757631.

[LangChain Docs 2026a] LangChain. *Checkpointers (durability modes, pending writes).* docs.langchain.com, 2026.

[LangChain Docs 2026b] LangChain. *Persistence (checkpointers vs stores; subgraph namespaces).* docs.langchain.com, 2026.

[LangChain Docs 2026c] LangChain. *Interrupts.* docs.langchain.com, 2026.

[Lavie et al. 2025] A. Lavie, G. Hanneman, S. Agrawal et al. *Findings of the WMT25 Shared Task on Automated Translation Evaluation Systems.* WMT 2025, pp. 436–483. DOI 10.18653/v1/2025.wmt-1.24.

[LF AI & Data 2025] LF AI & Data. *ACP Joins Forces with A2A under the Linux Foundation.* 2025-08-29.

[Li et al. 2024] J. Li, Q. Zhang, Y. Yu, Q. Fu, D. Ye. *More Agents Is All You Need.* arXiv:2402.05120, 2024.

[Lin et al. 2024] C. Lin et al. *Parrot: Efficient Serving of LLM-based Applications with Semantic Variable.* OSDI 2024.

[Linux Foundation 2026] Linux Foundation. *A2A Protocol Surpasses 150 Organizations… in First Year.* Press release, 2026-04-09.

[Liu et al. 2023] X. Liu et al. *AgentBench: Evaluating LLMs as Agents.* arXiv:2308.03688, 2023.

[Liu et al. 2024] N. F. Liu, K. Lin, J. Hewitt, A. Paranjape, M. Bevilacqua, F. Petroni, P. Liang. Lost in the Middle: How Language Models Use Long Contexts. *TACL* 12, 2024; arXiv:2307.03172.

[Lommel et al. 2014] A. Lommel et al. *Multidimensional Quality Metrics (MQM).* 2014.

[Luo et al. 2025] M. Luo et al. *Autellix: An Efficient Serving Engine for LLM Agents as General Programs.* arXiv:2502.13965, 2025.

[MAD-Fail 2025] *When and Why Does Multi-Agent Debate Fail and Does It Really Underperform?* arXiv:2510.20963, 2025.

[MAF Docs 2026] Microsoft. *Agent Framework overview and migration guides.* MicrosoftDocs/semantic-kernel-docs, 2026.

[Marro et al. 2025] S. Marro et al. *Agora Protocol.* 2025.

[MCP Blog 2026] Model Context Protocol. *The 2026-07-28 Specification.* blog.modelcontextprotocol.io, 2026-07-28.

[MCP Release 2026] modelcontextprotocol/modelcontextprotocol. *Release 2026-07-28.* GitHub, 2026.

[MCP Versioning 2026] Model Context Protocol. *Versioning.* modelcontextprotocol.io/docs/2026-07-28/learn/versioning, 2026.

[Mei et al. 2024] K. Mei, Z. Li, S. Xu, R. Ye, Y. Ge, Y. Zhang. *AIOS: LLM Agent Operating System.* arXiv:2403.16971, 2024. https://github.com/agiresearch/AIOS

[Meta 2025] Meta. *GAIA2: event-driven, asynchronous agent benchmark.* 2025. `[UNVERIFIED]` — primary citation not fetched.

[MetaGPT Docs 2026] geekan/MetaGPT-docs. *Agent communication (publish_message, cause_by subscriptions).* GitHub, 2026.

[Mialon et al. 2023] G. Mialon et al. *GAIA: A Benchmark for General AI Assistants.* arXiv:2311.12983, 2023.

[Miao et al. 2025] S. Miao et al. *SWE-Lancer: Can Frontier LLMs Earn $1 Million from Real-World Freelance Software Engineering?* arXiv:2502.12115, 2025.

[Microsoft 2025a] Microsoft Research. *AutoGen v0.4: Reimagining the Foundation of Agentic AI for Scale, Extensibility, and Robustness.* 2025.

[Microsoft 2025b] Microsoft Research. *Magentic-One: A Generalist Multi-Agent System for Solving Complex Tasks.* 2025.

[Microsoft 2026] Microsoft. *Microsoft Agent Framework at BUILD 2026* (confirms 1.0 GA on 2026-04-02). devblogs.microsoft.com/agent-framework, 2026.

[Microsoft Research 2014] Microsoft Research. *Orleans — Virtual Actors* project page. microsoft.com/research, 2014.

[MMP 2026] *Mesh Memory Protocol: Semantic Infrastructure for Multi-Agent LLM Systems.* arXiv:2604.19540, 2026.

[Morph 2026] Morph. *Context Rot: Why LLMs Degrade as Context Grows.* 2026. (secondary; the ">30% mid-context drop" figure is `[UNVERIFIED]`)

[MPAC 2026] *MPAC: Multi-Principal Agent Coordination Protocol* specification. GitHub cafalchio/mpac-protocol, 2026. *(Self-published spec, no peer review.)*

[MPI Forum 2025] Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 5.0.* 2025-06-05. https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report.pdf

[OpenAI Agents Docs 2026a] OpenAI. *Handoffs.* openai.github.io/openai-agents-python/handoffs/, 2026.

[OpenAI Agents Docs 2026b] OpenAI. *Sessions.* openai.github.io/openai-agents-js/guides/sessions/, 2026.

[Papineni et al. 2002] K. Papineni et al. BLEU: a Method for Automatic Evaluation of Machine Translation. *ACL*, 2002.

[Popović 2015] M. Popović. chrF: character n-gram F-score for automatic MT evaluation. *WMT*, 2015.

[Post 2018] M. Post. A Call for Clarity in Reporting BLEU Scores. *WMT*, 2018 (sacreBLEU).

[Randomness 2026] *On Randomness in Agentic Evals.* arXiv:2602.07150, 2026.

[Ray Docs 2026] Ray project. *Multi-agent A2A example.* docs.ray.io, 2026.

[Ray Docs 2026b] Ray project. *Serve production guide: best practices (max_ongoing_requests, max_queued_requests).* docs.ray.io, 2026.

[Rei et al. 2020] R. Rei et al. COMET: A Neural Framework for MT Evaluation. *EMNLP*, 2020.

[Rei et al. 2022a] R. Rei et al. *COMET-22.* WMT 2022.

[Rei et al. 2022b] R. Rei et al. *CometKiwi.* WMT 2022.

[Reliability-Science 2026] *Beyond pass@1: A Reliability Science Framework for Long-Horizon LLM Agents.* arXiv:2603.29231, 2026.

[Restate Docs 2026] Restate. *Durable Agents.* docs.restate.dev/ai/patterns/durable-agents, 2026.

[SAS-vs-MAS 2026] *Single-Agent LLMs Outperform Multi-Agent Systems on Multi-Hop Reasoning Under Equal Thinking Token Budgets.* arXiv:2604.02460, 2026.

[Sellam et al. 2020] T. Sellam, D. Das, A. P. Parikh. BLEURT: Learning Robust Metrics for Text Generation. *ACL*, 2020.

[Semantic View 2026] *Beyond Message Passing: A Semantic View of Agent Communication Protocols.* arXiv:2604.02369, 2026.

[Siegel et al. 2024] Z. S. Siegel et al. *CORE-Bench: Fostering the Credibility of Published Research Through a Computational Reproducibility Agent Benchmark.* arXiv:2409.11363, 2024.

[Singla 2026] P. Singla. *Agent Identity Protocol (AIP): Decentralized Identity and Delegation for AI Agents.* IETF Internet-Draft draft-singla-agent-identity-protocol-03, 2026-06-10.

[Smith 1980] R. G. Smith. The Contract Net Protocol: High-Level Communication and Control in a Distributed Problem Solver. *IEEE Trans. Computers* C-29(12), 1980.

[Spheron 2026] Spheron Network. *vLLM vs SGLang 2026.* (secondary benchmarking; specific latency figures `[UNVERIFIED]`)

[SSVP 2026] *Hallucination as Context Drift: Synchronization Protocols for Multi-Agent LLM Systems* (Shared State Verification Protocol). arXiv:2606.21666, 2026.

[Tan et al. 2024] X. Tan, Y. Jiang, Y. Yang, H. Xu. *Teola: Towards End-to-End Optimization of LLM-based Applications.* arXiv:2407.00326, 2024.

[Tan et al. 2025] X. Tan, Y. Jiang, Y. Yang, H. Xu. *Towards End-to-End Optimization of LLM-based Applications with Ayo.* ASPLOS 2025. DOI 10.1145/3676641.3716278.

[Terminal-Bench 2026] Terminal-Bench. *terminal-bench@2.0 Leaderboard.* tbench.ai, 2026.

[ToAI 2026] *Agent Messages That Mean Something: Speech Acts, Performatives, and ACLs.* Towards AI, 2026. (secondary)

[Token-Not-Taken 2026] *The Token Not Taken: Sampling, State, and the Variability of AI Agent Outputs.* arXiv:2606.08998, 2026.

[Vals 2026] Vals AI. *SWE-bench Verified leaderboard.* vals.ai/benchmarks/swebench, 2026. (third-party evaluation; figures `[UNVERIFIED]`)

[VIPS 2026] VIPS Learn. *FIPA ACL — Agent Communication Language (historical).* 2026. (secondary)

[vLLM Docs 2026] vLLM project. *PagedAttention design; Automatic Prefix Caching.* docs.vllm.ai, 2026.

[W3C CG 2026a] W3C. *AI Agent Protocol Community Group* charter and scope. w3.org/community/agentprotocol/, 2026.

[W3C CG 2026b] W3C AI Agent Protocol CG. *Protocol (Tentative) — Agent Identity, did:wba.* w3c-cg.github.io/ai-agent-protocol/protocol.html, 2026.

[Wang et al. 2024] J. Wang et al. *Reasoning in Token Economies: Budget-Aware Evaluation of LLM Reasoning Strategies.* EMNLP 2024. DOI 10.18653/v1/2024.emnlp-main.1112.

[Wang et al. 2025] *LangChain-Parsl: Connect Large Language Model Agents to High Performance Computing Resource.* SC Workshops, 2025. DOI 10.1145/3731599.3767349.

[Xie et al. 2024] T. Xie et al. *OSWorld.* NeurIPS 2024.

[Yan 2025] W. Yan. *Don't Build Multi-Agents.* Cognition blog, 2025. https://cognition.com/blog/dont-build-multi-agents

[Yan 2026] W. Yan. *Multi-Agents: What's Actually Working.* Cognition blog, 2026. https://cognition.com/blog/multi-agents-working

[Yang et al. 2024] Z. Yang et al. *OASIS: Open Agent Social Interaction Simulations with One Million Agents.* arXiv:2411.11581, 2024.

[Yang et al. 2024b] J. Yang et al. *SWE-bench Multimodal.* 2024.

[Yao et al. 2024] S. Yao et al. *τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains.* arXiv:2406.12045, 2024.

[Yoran et al. 2024] O. Yoran, S. J. Amouyal, C. Malaviya, B. Bogin, O. Press, J. Berant. *AssistantBench: Can Web Agents Solve Realistic and Time-Consuming Tasks?* arXiv:2407.15711, 2024.

[Yu et al. 2022] G.-I. Yu et al. *Orca: A Distributed Serving System for Transformer-Based Generative Models.* OSDI 2022.

[ZenML 2026] ZenML. *LlamaIndex vs CrewAI.* 2026. (secondary; LlamaIndex Workflows event model)

[Zheng et al. 2024] L. Zheng et al. *SGLang: Efficient Execution of Structured Language Model Programs* (RadixAttention). NeurIPS 2024.

[Zhou et al. 2024] S. Zhou et al. *WebArena: A Realistic Web Environment for Building Autonomous Agents.* ICLR 2024.

[Zylos 2026] Zylos Research. *Durable Execution for AI Agent Runtimes: Checkpointing, Replay, and Recovery.* 2026-04-24. (secondary survey)
