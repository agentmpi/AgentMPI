# 05b — Modern LLM Agent Protocols and Orchestration Frameworks

**Purpose.** Related-work survey for *AgentMPI*, a message-passing protocol for LLM multi-agent
harnesses modelled on MPI. The organising claim under test:

> Contemporary LLM agent protocols and frameworks standardise either (a) how one agent invokes a
> *tool* or a *single* remote agent, or (b) an application-level orchestration pattern. Essentially
> none of them provide the guarantees MPI provides: **named groups / communicators**, **collective
> operations**, **consistency and synchronisation for shared state**, or a **failure model**.

**Verdict, stated up front (evidence in §1–§3, sharpened in Gap analysis).** The claim survives, with
three honest qualifications:

1. The *protocol* layer (MCP, A2A, and the 2025–26 interop crowd) is unambiguously point-to-point
   RPC. None of them define a group, a collective, a barrier, or a consistency model. The strongest
   counter-example is not a collective but a *shared-state* primitive: A2A's task/artifact objects
   and MCP resources give a named, fetchable blob — closer to a distributed filesystem handle than
   to `MPI_Bcast`.
2. The *framework* layer does contain real distributed-systems machinery, and this is where the
   claim needs care. LangGraph's checkpointer/durable-execution model is a genuine
   crash-consistency story; Temporal-backed agents inherit exactly-once-ish durable replay; Ray
   gives actor supervision and object-store shared memory; AutoGen v0.4+ is an actor runtime with
   mailboxes. These are *not* MPI, but calling them "no failure model" would be wrong.
3. What is genuinely absent everywhere: a **named, enumerable communicator** with rank identity, a
   **collective algebra** (broadcast / reduce / allgather / scatter over agent groups), **barrier or
   lock primitives with defined semantics**, and a **specified failure model** for partial failure
   of a group (MPI's own weakness historically, and ULFM's subject). Frameworks give durability of a
   *single* orchestration; nobody gives group semantics.

Citation markers are inline like `[mcp2026spec]`; BibTeX at the end. Anything I could not verify from
a primary or strongly corroborated source is marked `[UNVERIFIED]`.

---

## 1. Protocols and standards

### 1.1 MCP — Model Context Protocol (Anthropic → community/steering-committee governance)

**Layer.** Tool-calling / context-provision. MCP standardises the interface between an LLM *host*
application and external capability providers. It is explicitly *not* an agent-to-agent protocol
and explicitly not an orchestration layer.

**Architecture.** Three roles [mcp2026spec]:
- **Host** — the LLM application (IDE, chat client, agent harness) that owns the model and the
  conversation.
- **Client** — a connector instance inside the host, one per server, managing the session.
- **Server** — a process exposing capabilities. Three primitives: **tools** (model-invocable
  functions), **resources** (application-controlled data, addressed by URI), **prompts**
  (user-selectable templates).

The original 2024 announcement and spec [mcp2024announce; mcp2024spec] framed this as "a USB-C port
for AI applications": one client-server protocol replacing N bespoke integrations.

**Wire format.** JSON-RPC 2.0 request/response/notification. Methods are namespaced
(`tools/list`, `tools/call`, `resources/read`, `prompts/get`, …).

**Current spec revision (as of 30 Aug 2026): `2026-07-28`**, released 28 July 2026
[mcp2026spec; mcpblog2026]. This is a *major* revision and is **wire-incompatible with all prior
revisions**. The changes matter for AgentMPI's framing because they push MCP even further away
from anything session- or group-oriented:

- **Statelessness (SEP-2575, SEP-2567).** The mandatory `initialize`/`initialized` handshake and the
  `Mcp-Session-Id` header are **retired**. Every request is now self-describing: it carries its own
  protocol version and client capabilities in an `io.modelcontextprotocol/_meta` object, so any
  request can land on any server instance behind a plain round-robin load balancer
  [mcpblog2026; cloudflare2026mcp]. Capability discovery, if wanted, is an explicit optional
  `server/discover` call rather than a handshake.
- **Transports.** `stdio` (local subprocess, newline-delimited JSON-RPC) and **Streamable HTTP**
  remain. The legacy **HTTP+SSE** transport is now formally **deprecated** with a 12-month offramp
  [mcpblog2026].
- **Routing headers (SEP-2243).** Streamable HTTP requests must carry `Mcp-Method` and `Mcp-Name`
  headers so gateways/WAFs/rate-limiters can route and meter without parsing JSON bodies
  [mcpblog2026].
- **Auth.** Dynamic Client Registration is deprecated in favour of **Client ID Metadata Documents
  (CIMD)**: the client hosts a metadata document, the server fetches and validates it
  [mcpblog2026; cloudflare2026mcp].
- **Multi-Round-Trip Requests (MRTR)** replace ad-hoc server-initiated input; **`elicitation`**-style
  interaction is folded into this. Change notifications move from a GET endpoint to a single
  opt-in `subscriptions/listen` stream, per notification type [mcpblog2026].
- **Tasks** move out of experimental core into an `io.modelcontextprotocol/tasks` **extension**,
  with poll-based `tasks/get` and `tasks/update` (SEP-2663) — i.e. long-running work is an
  *extension*, polled, not a first-class durable execution model [mcpblog2026].
- **Deprecated primitives (SEP-2577):** **Roots**, **Sampling**, **Logging**. Notably *Sampling* was
  the one MCP feature that let a server ask the host's model for a completion — the closest MCP ever
  came to bidirectional agent-ish interaction — and new implementations are told not to adopt it.
- **Feature lifecycle.** Formal Active / Deprecated / Removed classification with a minimum
  12-month deprecation window [cloudflare2026mcp].

**Naming & discovery.** Per-server, host-configured. There is no global agent namespace. Discovery is
*intra*-server (`tools/list`, `resources/list`, `server/discover`) plus, out of band, public server
registries. `tools/list` responses now carry **cache hints** [mcpblog2026].

**State model.** As of `2026-07-28`, **explicitly stateless at the protocol level**. Session state,
if any, is the host's problem; SDKs expose a `requestState` hook instead of per-session state
[mcpts2026migration]. Cloudflare's guidance is blunt about the consequence: "use Durable Objects
when your application actually needs coordinated state" [cloudflare2026mcp] — i.e. coordinated
state is explicitly out of scope and pushed to the platform.

**Groups / collectives / barriers / shared-state consistency / fault tolerance:** **none, by
design.** MCP has no notion of multiple peer agents, therefore no membership, no collectives, no
barriers. Resources are the only shared-state surface: URI-addressed, read-oriented, with
subscription-based change notification — **no consistency model, no versioning guarantee, no
compare-and-swap**. Fault tolerance is request-level retry semantics inherited from HTTP; the 2026
statelessness push makes retry *easier* (any instance can serve) while removing session recovery as
a concept entirely.

**Relevance to AgentMPI.** MCP is the right *southbound* interface for AgentMPI (how an agent reaches
a tool) and a non-competitor *eastbound* (how agents reach each other). The 2026 stateless turn is
strong evidence for the layering claim: the flagship agent protocol of the era deliberately
abandoned even session state.

### 1.2 A2A — Agent2Agent (Google → Linux Foundation)

**Layer.** Agent-to-agent RPC across trust/vendor boundaries. A2A's own documentation states the
division of labour explicitly: "MCP is for agent-to-tool communication … A2A is for agent-to-agent
communication" and the two are "not competitors — they are highly complementary" [a2a2026site].

**Status as of 30 Aug 2026: v1.0 stable**, the first stable specification, released **March 2026**,
with **v1.0.1** following in May 2026 [a2asurvey2026; a2a2026site]. Governance: originally developed
by Google, **donated to the Linux Foundation**; maintained by a Technical Steering Committee with
representatives from **AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP and
ServiceNow**; Apache-2.0 [a2a2026site]. At its one-year mark (April 2026) the LF reported **150+
supporting organisations** and native integration in **Azure AI Foundry, Amazon Bedrock AgentCore
and Google Cloud** [lf2026a2apress].

**Message model.** Three core object types:
- **Task** — the unit of work, with a lifecycle (submitted → working → input-required → completed /
  failed / canceled). Long-running tasks are first-class; clients poll `tasks/get` or subscribe.
- **Message** — a turn between client and remote agent, composed of typed **Parts** (text, file,
  structured data), i.e. multimodal by construction.
- **Artifact** — durable typed output produced by a task, returned incrementally.

Methods on the wire include `message/send`, `message/stream`, `tasks/get` [a2acard2026].

**Wire format.** v1.0 is **multi-binding**: interfaces are Protobuf-defined and each declared
endpoint states its `protocolBinding` — one of **`JSONRPC`**, **`HTTP+JSON`** (REST), or **`GRPC`**
[a2acard2026; gcp2026agentregistry]. Streaming is **SSE** via `message/stream`
(`capabilities.streaming`); asynchronous completion for disconnected clients uses **push
notifications** / webhooks (`capabilities.pushNotifications`). The LF release notes describe v1.0 as
introducing "multi-protocol support, enterprise-grade multi-tenancy, modernized security flows, and
a defined migration path" plus "a web-aligned architecture that supports familiar security and
load-balancing patterns" [lf2026a2apress] — the same stateless-web direction MCP took.

**Naming & discovery.** The **Agent Card**: a JSON document at the well-known URI
**`/.well-known/agent-card.json`**, served as `application/a2a+json` [a2acard2026]. (v0.x used
`/.well-known/agent.json`.) Required fields: `name`, `description`, `version`, `skills` (≥1);
optional `provider`, `capabilities`, `securitySchemes`, `security`, `defaultInputModes` /
`defaultOutputModes` [a2acard2026]. v1.0 restructured addressing significantly:

| v0.x field | v1.0 replacement |
|---|---|
| `url` | `supportedInterfaces[0].url` |
| `preferredTransport` | `supportedInterfaces[]` ordering |
| `additionalInterfaces` | folded into `supportedInterfaces[]` |
| top-level `protocolVersion` | `supportedInterfaces[].protocolVersion` (per-interface) |
| `supportsAuthenticatedExtendedCard` | `capabilities.extendedAgentCard` (RPC renamed `GetExtendedAgentCard`) |
| `capabilities.stateTransitionHistory` | **removed** in v1.0 (model as an extension) |

[a2acard2026; gcp2026agentregistry]. v1.0 adds **Signed Agent Cards** for cryptographic identity
verification [lf2026a2apress] and an authenticated `/extendedAgentCard` endpoint for private
capability detail. Registries exist (e.g. Google Cloud **Agent Registry**, which accepts A2A card
versions 0.3 and 1.0, max 10 KB [gcp2026agentregistry]), but the *protocol's* naming model is
DNS/URL-based: an agent is a URL, not a rank in a group.

**State model.** Per-task, server-held, with a "stateless bias" [a2asurvey2026]. Crucially A2A's
design principle is **opacity**: agents collaborate "without sharing internal memory, thoughts, or
tools." The LF's own framing of the v1.0 win condition is that agents from LangGraph or CrewAI can
"delegate sub-tasks, and coordinate complex workflows **without sharing internal memory**"
[lf2026a2apress]. That is a deliberate anti-shared-state stance — the exact opposite of an MPI
shared-window / RMA model.

**Groups?** No. A2A is strictly client↔remote-agent, one pair at a time. There is no communicator,
no group handle, no membership list, no enumeration of peers, no rank. Multi-agent topologies are
built by the *client* making N independent calls.
**Collectives?** No. No broadcast, scatter/gather, reduce, or allgather. Fan-out is an application
loop; there is no protocol-level aggregation, no completion semantics for a set of peers.
**Barriers / sync primitives?** No. No barrier, lock, semaphore, or ordering guarantee across
multiple peer calls. Ordering is only within a single task's event stream.
**Shared-state consistency?** Artifacts are the shared-state surface, and they are
write-once-ish typed outputs with no consistency model, no concurrent-writer semantics, no
versioning contract. `stateTransitionHistory` — the closest thing to a replicated log — was
*removed* in v1.0.
**Fault tolerance?** Partial: a task lifecycle with `failed`/`canceled` states, ACP-derived **task
persistence, async resumption and webhook progress reporting** now native to v1.0
[techahead2026protocols], and HTTP-level retry. But there is **no failure model for a group**: no
failure detector, no membership-change notification, no agreed-upon semantics for "one of my N peers
died mid-collective." Secondary analysis is explicit that "durable cross-org state is still the gap
[A2A] leaves open" [a2asurvey2026].

**Relevance to AgentMPI.** A2A is the strongest existing competitor for the *transport and naming*
layer and the natural substrate to build AgentMPI over (an AgentMPI communicator could be defined as
a named set of Agent Cards). It is not a competitor for group/collective/consistency semantics,
which it declines to provide on principle.

### 1.3 ACP — Agent Communication Protocol (IBM Research / BeeAI) — **DEPRECATED, ABSORBED**

**Do not cite ACP as a live protocol.** ACP was IBM Research's REST-native, HTTP-first agent
protocol launched **March 2025** as the operational core of the **BeeAI Platform**, donated to the
Linux Foundation later that month [ibm2026acp; lfaidata2025acpmerge]. On **29 August 2025** IBM and
the Linux Foundation announced that **ACP is merging into A2A** under LF AI & Data: the ACP team
wound down active development and contributed its technology to A2A; **Kate Blair (IBM Research)
joined the A2A Technical Steering Committee** [lfaidata2025acpmerge]. The IBM Research project page
now leads with "ACP is now part of A2A under the Linux Foundation!" plus a migration guide
[ibm2026acp]. The `i-am-bee/acp` GitHub repository is **archived** (last push 25 Aug 2025) and the
SDKs are unmaintained; the BeeAI Platform itself now runs on A2A, with `A2AServer` / `A2AAgent`
adapters [lfaidata2025acpmerge; a2asurvey2026]. Where ACP's ideas went: **multimodal messaging,
trajectory metadata, task persistence, async resumption and webhook progress reporting** were folded
into A2A v1.0 [a2asurvey2026; techahead2026protocols].

> **Terminology hazard for the paper.** "ACP" in 2026 developer usage most often means **Zed's Agent
> Client Protocol**, an unrelated and actively developed standard that inherited the acronym
> [a2asurvey2026]. Disambiguate explicitly on first use.

**Group/collective/sync/fault-tolerance:** none (and moot).

### 1.4 Zed ACP — Agent Client Protocol (agent ↔ editor)

**Layer.** Agent-to-editor/IDE. Described as "LSP for agents": a JSON-RPC 2.0 protocol letting any
coding agent run as a subprocess inside any editor [a2asurvey2026]. Stewarded by **Zed Industries**
with **JetBrains**; Apache-2.0; version **v0.13.x**, shipped in Zed 1.0 and JetBrains IDEs, running
Claude Code, Codex CLI and Gemini CLI [a2asurvey2026] `[UNVERIFIED: exact version number and the
specific set of shipped agents come from a single secondary aggregator]`.

**State model.** **Sessions** with create/resume semantics per editor connection. Trust model is
*client-owned capabilities*: file and terminal access sit behind editor-side permission gates
[a2asurvey2026]. Discovery: a public **Agent Registry** co-launched with JetBrains (Jan 2026),
register-once-install-everywhere [a2asurvey2026].

**Groups / collectives / barriers / consistency / fault tolerance:** none. Local subprocess, no
cross-org story. Interesting to AgentMPI only as the one protocol in this space with an explicit
**capability-gating** model, which a group-membership design would want to inherit.

### 1.5 AG-UI (agent ↔ user/frontend)

**Layer.** Agent-to-user. ~16 standard event types over SSE / WebSockets / webhooks, for streaming
chat and generative UI; MIT; stewarded by **CopilotKit**; pre-stable but shipping **first-party in
Microsoft Agent Framework, Google ADK, AWS Strands and Bedrock AgentCore**, with partnership
integrations into LangGraph and CrewAI [a2asurvey2026].

**Notable for AgentMPI:** AG-UI is the only protocol in this survey offering **bi-directional state
synchronisation** as a named feature — but it synchronises agent↔frontend, one session at a time,
with no multi-writer consistency story. **Groups/collectives/barriers/fault tolerance: none.**

### 1.6 AGNTCY / SLIM (Cisco → Linux Foundation) — infrastructure, not a message protocol

**Layer.** Beneath the protocols: a "**Internet of Agents**" stack of **decentralized agent
directory** (OASF records describing A2A agents and MCP servers), **verifiable decentralized
identity**, **observability SDKs**, and **SLIM** messaging (gRPC-based) [a2asurvey2026]. Open-sourced
by Cisco (March 2025), donated to the Linux Foundation (July 2025), **65+ supporting companies**,
Apache-2.0 [a2asurvey2026].

**Important status fact:** AGNTCY **archived its own Agent Connect Protocol (ACP — a third use of the
acronym)** after A2A won the agent-to-agent layer; AGNTCY now positions itself as carrying A2A rather
than competing with it [a2asurvey2026]. Adoption is thin relative to governance (~2K stars across a
48-repo org; "corporate logos, thin grassroots usage") [a2asurvey2026].

**Groups?** AGNTCY's directory is the closest thing in the ecosystem to a **name service** for
agents, which is a prerequisite for named groups — but a directory is not a communicator: no
membership epochs, no group-consistent views, no collective operations.
**Collectives / barriers / consistency:** none. **Fault tolerance:** transport-level (SLIM/gRPC)
only. **Verifiable identity is a genuine contribution** AgentMPI should consume rather than
reinvent.

### 1.7 ANP, Summoner, AITP, Agora — research-grade and unverified

- **ANP (Agent Network Protocol)** — **real but research-grade.** Community-run, Apache-2.0,
  **draft spec**, ~1.3K stars, no known production use. Distinctive design: **W3C DID**-based
  decentralized identity, Semantic-Web-style agent description, and an Agent Discovery Service;
  influence routed through the **W3C AI Agent Protocol Community Group** rather than direct adoption
  [a2asurvey2026]. Relevant to AgentMPI for decentralized *naming* ideas only; no groups,
  collectives, barriers, consistency model, or failure model.
- **Summoner** — real but very early (~68 stars, three-person team, cadence slowing). Focus is
  **cross-org durable transactions** with Ed25519 self-sovereign identity, reputation-aware
  discovery, "SPLT" wire format, and **deeply stateful signed decision graphs** [a2asurvey2026].
  This is the *one* project in the survey whose stated goal overlaps AgentMPI's consistency
  ambitions; it is not a credible baseline, but it is an honest prior-art citation for durable
  cross-agent state.
- **AITP (Agent Interaction & Transaction Protocol)** — `[UNVERIFIED]`. I could not confirm current
  status, spec revision, or maintained implementation from a primary source within this survey's
  search budget. It does not appear in the 2026 consolidation accounts of the agent-to-agent layer
  [a2asurvey2026]. **Do not cite as a live standard without further verification.**
- **Agora** — `[UNVERIFIED]`. Likewise unconfirmed as a live, maintained agent-interop standard as of
  Aug 2026; the name is heavily overloaded (multiple unrelated products and an academic
  meta-protocol proposal). **Do not cite without verification.**

### 1.8 Governance consolidation (2025–2026)

The umbrella fact worth one sentence in the related-work section: **A2A and AGNTCY are both Linux
Foundation projects, and the LF's Agentic AI Foundation — reported at 180+ member organisations
including Stripe, Atlassian and U.S. national labs — now governs the broader open agent-standards
stack** [a2asurvey2026] `[UNVERIFIED: membership count from a secondary aggregator]`. The practical
consequence for the paper: the *interop* layer has consolidated and stabilised, which strengthens
rather than weakens the case for a distinct group-semantics layer above it — there is now a stable
substrate to define AgentMPI on top of.

**Layer map the ecosystem itself now uses** [a2asurvey2026]:

```
USERS    │ AG-UI      (agent ↔ user/frontend)
EDITORS  │ Zed ACP    (agent ↔ editor/IDE)
AGENTS   │ A2A        (agent ↔ agent)          ← point-to-point, no groups
TOOLS    │ MCP        (agent ↔ tools/data)
NETWORK  │ AGNTCY     (discovery, identity, observability, SLIM)
         └ built on top: frameworks (LangGraph, CrewAI, Microsoft Agent Framework)
```

**AgentMPI's claim in one line:** there is no row in this stack between "A2A" and "frameworks" — no
*group communication* layer. Every named layer is a 1:1 boundary.

---

## 2. Orchestration frameworks

> **Methodological note.** The frameworks are where the layering claim is genuinely contestable. Two
> of the biggest (LangGraph and Microsoft Agent Framework) are **Pregel / Bulk-Synchronous-Parallel**
> engines, and BSP has a *real synchronisation barrier*. §2.1 and §2.4 treat this honestly; §2.12
> states precisely what is still missing.

### 2.1 Microsoft Agent Framework (MAF) — **the AutoGen and Semantic Kernel successor**

**This is the single most important "renamed / absorbed" fact for the related-work section.**
Microsoft Agent Framework was introduced **October 2025**, hit **Release Candidate in February
2026** (feature surface locked), and reached **version 1.0 GA on 3 April 2026** for **both .NET and
Python** [maf2026v1]. It explicitly "unif[ies] the enterprise-ready foundations of **Semantic
Kernel** with the innovative orchestrations of **AutoGen** into a single, open-source SDK"
[maf2026v1]. Microsoft ships **migration assistants** for both predecessors that analyse existing
code and generate migration plans, plus dedicated Semantic Kernel and AutoGen migration guides, and
states plainly: "Coming from AutoGen or Semantic Kernel? **Now is the time to migrate**"
[maf2026v1]. Packages: `Microsoft.Agents.AI` (.NET) and the `agent_framework` / `microsoft-agents-ai`
Python distribution; native **MCP and A2A** interop adapters [maf2026guide; maf2026prod]
`[UNVERIFIED: exact Python package name — two secondary sources give slightly different strings]`.

**Coordination model — Pregel/BSP, and this matters.** MAF's `workflow` package is a graph of
**executors** connected by **edges**; a `Workflow` "coordinates executor invocation, message routing,
and event streaming" [maf2026workflows]. The engine is "a **modified Pregel execution model — a Bulk
Synchronous Parallel (BSP) approach with superstep-based processing**" [maf2026workflows]. Each
superstep:

1. collects all pending messages from the previous superstep,
2. routes messages to target executors per the edge definitions,
3. runs all target executors **concurrently**,
4. **waits for all executors to complete before advancing (synchronisation barrier)**,
5. queues newly emitted messages for the next superstep [maf2026workflows].

Microsoft names the guarantees this buys: **deterministic execution** (same input ⇒ same order),
**reliable checkpointing** (state saved at superstep boundaries), and "**no race conditions between
supersteps; each sees a consistent view of messages**" [maf2026workflows].

**This is the closest thing in the LLM-agent ecosystem to an MPI-style barrier, and the paper must
say so.** It is a genuine BSP barrier with a consistency guarantee over message delivery. Its limits,
which is where AgentMPI's contribution survives:
- The barrier is **implicit and global to the graph**, not a callable primitive over a *named
  subgroup*. There is no `barrier(comm)`; you cannot barrier a subset of executors.
- It is **not composable with independent parallelism** — the docs warn that if "you need truly
  independent parallel paths that don't block each other," the recommended workaround is to
  *manually consolidate* steps into a single executor [maf2026workflows]. In MPI terms: no
  sub-communicators, so users hand-inline work to escape the barrier.
- Membership is **static and design-time** (the graph is built by a `Builder`); there is no dynamic
  join/leave, no membership epoch, no failure-induced group reconfiguration.
- It is **single-orchestrator and in-process**: the barrier is enforced by one workflow runtime, not
  agreed among distributed peers. Cross-process/cross-org agents reached via A2A are, from the
  workflow's perspective, just tool calls inside one executor.

**Multi-agent orchestration patterns** (all stable at 1.0): **sequential, concurrent, handoff, group
chat, and Magentic-One**; all support streaming, checkpointing, human-in-the-loop approvals and
pause/resume for long-running workflows [maf2026v1].

**State / persistence model.** Graph state checkpointed at superstep boundaries;
"**checkpointing and hydration ensure long-running processes survive interruptions**" [maf2026v1].
Agents are a first-class primitive with instructions, tools, memory and state [maf2026guide].

**Failure model.** Checkpoint-and-resume of the *whole workflow*, i.e. crash consistency for one
orchestration. There is no failure *detector*, no supervision hierarchy exposed at the workflow
level, no defined semantics for "executor 3 of 8 in this superstep died" beyond restart-from-
checkpoint, and no notion of a surviving group continuing with degraded membership
`[UNVERIFIED: partial-failure semantics within a superstep are not documented in the pages surveyed]`.

**Groups?** Static graph topology only — no named, first-class, enumerable group object with
identity/rank. **Collectives?** Fan-out and "converge results" exist as *graph shapes*
[maf2026v1], which is a hand-rolled scatter/gather, not a typed collective algebra (no reduce
operators, no allgather, no scan, no user-defined reductions). **Sync primitives?** The BSP barrier
(implicit, whole-graph). No locks, no semaphores, no CAS. **Fault tolerance?** Checkpoint/hydrate.
**Context management?** Agent-level memory; see §3(iv).

### 2.2 AutoGen (Microsoft) — **maintenance mode; historically the actor model**

**Status: legacy.** AutoGen is in **maintenance mode**, community-managed, with its **last feature
release in September 2025**; new work goes to Microsoft Agent Framework [a2asurvey2026; maf2026v1].
Its .NET SDK is described as frozen [a2asurvey2026]. Stars ~42–59K depending on source and date
[maf2026prod; a2asurvey2026]. **The paper must not describe AutoGen in the present tense as
Microsoft's agent framework.**

**Why it still matters to AgentMPI: the v0.4 actor-model rewrite.** AutoGen v0.4 (Jan 2025) was a
ground-up re-architecture onto an **asynchronous, event-driven actor model**, with a layered design
(`autogen-core` runtime, `autogen-agentchat` conversational layer, extensions) and support for
distributed agent runtimes across process boundaries [autogen2025v04]. Agents are actors with
**mailboxes**; communication is asynchronous message passing, either direct-addressed or via
**topic-based publish/subscribe**.

This is the *architecturally* closest ancestor to AgentMPI in the LLM space, and it is instructive
that its own successor abandoned it: MAF "replaces AutoGen's `GroupChatManager` with explicit
graph-based control flow" [maf2026prod] — i.e. Microsoft traded the actor model's dynamism for BSP
determinism.

**Groups?** `GroupChat` is a *conversation pattern* (a manager selecting the next speaker), and
pub/sub **topics** are a genuine many-to-many addressing mechanism — the nearest thing to a named
group in any framework here. But a topic is not a communicator: no membership enumeration, no
rank, no group-consistent view, no collective completion. **Collectives?** No. **Sync primitives?**
Mailbox FIFO ordering per actor; no barriers, no locks. **Fault tolerance?** Actor-model supervision
was not surfaced as a durable-execution guarantee; conversation-scoped state
[a2asurvey2026]. **Context management?** Conversation-history summarisation at the AgentChat layer;
see §3(iv).

### 2.3 Semantic Kernel (Microsoft) — **absorbed as MAF's foundation layer**

**Status:** not dead, but no longer the recommended entry point. SK "does not go away — it becomes
the **foundation layer**" of MAF; MAF adopts SK's middleware pattern, OpenTelemetry integration and
provider/connector architecture, and "the biggest change is replacing `Kernel` with `Agent` as the
primary abstraction" [maf2026guide; maf2026prod]. Microsoft publishes an SK→MAF migration guide and
a migration assistant [maf2026v1]. ~28K stars at convergence [maf2026prod].

**Layer.** SDK / dependency-injection kernel + plugin (tool) model + connectors + memory
abstractions. **Coordination model:** originally planners and (later) the Process Framework;
now superseded by MAF workflows. **Groups / collectives / barriers / consistency / group failure
model: none.** SK's contribution to this survey is enterprise plumbing (typed middleware,
telemetry), not distributed semantics.

### 2.4 LangGraph (LangChain) — **the strongest existing durability story**

**Status: 1.0 GA**; Python package at **v1.0.5** in the 1.0.x line as of Aug 2026
[langgraph2026pypi] (a secondary aggregator reports v1.2.4 and ~57M monthly PyPI downloads
[a2asurvey2026] `[UNVERIFIED: version numbers differ between sources; cite the PyPI line]`).
Self-described as "a **low-level orchestration framework** for building, managing, and deploying
long-running, stateful agents," trusted by Klarna, Replit, Elastic and others
[langgraph2026repo].

**Coordination model.** A `StateGraph`: nodes (functions/agents) and edges (static or conditional),
executed by a **Pregel-style BSP engine** over **channels**. State is a typed schema (`TypedDict`)
with **`Annotated` reducers** specifying how concurrent writes to the same key are merged
[langgraph2026prod]. That reducer mechanism is the most MPI-like thing in the framework layer: a
per-channel **user-supplied combining operator** — conceptually `MPI_Op` for a reduction — but it is
applied by the single graph runtime to fan-in edges, not executed as a distributed collective.

**State / persistence model — genuinely strong.** Pluggable **checkpointers** save a snapshot of
graph state **after every node execution (superstep)**; `MemorySaver` for dev, `SqliteSaver` for
local, **`PostgresSaver` for production**; persistence is keyed on a **`thread_id`** passed in the
invocation config, and each `thread_id` keeps independent state history enabling multi-session
agents, audit trails and mid-workflow resumption [langgraph2026prod; langgraph2026guide]. The
engineering rationale is explicit and unusually rigorous: checkpoints "can be resumed on **any
machine**, an arbitrary amount of time after they were saved — i.e. checkpoints that don't rely on
keeping a process running on a specific machine, or keeping any live data in memory"; the runtime
records **serialised channel values (MsgPack, optionally encrypted), their version strings, and a
record of which channel versions each node has most recently seen** [langgraph2026blog]. Version
vectors per channel per node is a real consistency mechanism, and the paper should credit it.

Two memory tiers: **short-term working memory** (thread-scoped graph state) and **long-term
persistent memory across sessions** (the `Store` abstraction) [langgraph2026repo].

**Failure model.** **Durable execution**: "agents that persist through failures and can run for
extended periods, **automatically resuming from exactly where they left off**"
[langgraph2026repo; langgraph2026pypi]. Practically: a shared Postgres checkpoint store gives crash
recovery, **multi-worker coordination**, and **point-in-time replay of any failed run**
[langgraph2026prod]. LangChain's own framing lists the design levers as parallelisation (latency),
streaming (perceived latency), task queue (fewer retries), checkpointing (cheaper retries)
[langgraph2026blog].

**Human-in-the-loop.** `interrupt` / `interrupt_before` / `interrupt_after` pause execution at named
nodes; the paused state is persisted, so an agent "can remain paused indefinitely — hours or days"
and resume on the same `thread_id`, optionally after `graph.update_state`
[langgraph2026guide; langgraph2026prod]. LangChain's rationale is a scalability argument that
AgentMPI should reuse: keeping a process alive while waiting "scales neither in time nor in volume,"
so **actual interruption powered by checkpointing** is the only workable approach
[langgraph2026blog]. This is effectively a *suspendable* execution model — MPI has no equivalent, and
it is a place where existing systems beat AgentMPI's likely design.

**Groups?** No. Nodes are a static graph; there is no named group object, no rank, no dynamic
membership. Parallel superstep execution is anonymous fan-out.
**Collectives?** Only in the weak sense above: fan-in with `Annotated` reducers ≈ a reduce at a
join point. No broadcast/allgather/scatter/scan as protocol operations; no group-scoped completion.
**Sync primitives?** The BSP superstep boundary; no barrier callable, no locks. Notably, **locking is
explicitly an application concern**: production guidance recommends **Redis for per-thread locks**
and per-tool rate limiting on top of LangGraph [langgraph2026prod] — direct evidence that the
framework does not provide synchronisation primitives.
**Fault tolerance?** Best in class among these systems, but scoped to *one graph run on one
`thread_id`* — not a group failure model. No failure detector over peers, no membership change
events, no partial-failure collective semantics.
**Context management?** Short/long-term memory split plus lean-state guidance; see §3(iv).

### 2.5 CrewAI — crews vs flows

**Status:** GA, actively released (docs versioned at **v1.12.0**; a secondary source reports weekly
v1.14.x releases) [crewai2026prodarch; a2asurvey2026]. MIT. Reported adoption is large but
vendor-sourced (53K stars, "63% of Fortune 500" claimed, 450M+ workflows/month)
[a2asurvey2026] `[UNVERIFIED: vendor-reported adoption figures]`.

**Two orchestration primitives, and the distinction is the important part** [crewai2026repo]:

- **Crew** — a team of role-based agents with genuine autonomy; they delegate, decide and
  collaborate, and **the execution path is decided at runtime by the models**. A Crew is
  **stateless by default**: "run it twice, it remembers nothing" [crewai2026flowscrews].
- **Flow** — an event-driven Python class where *you* write the control flow: methods decorated
  `@start()`, `@listen()`, `@router()`, and CrewAI threads state and sequencing deterministically
  [crewai2026flowscrews; crewai2026flowsguide].

The production pattern the ecosystem converged on is **"Flows wrap Crews"**: CrewAI's own docs say
"When building production AI applications with CrewAI, we recommend starting with a Flow"
[crewai2026prodarch].

**Process models within a Crew.** `Process.sequential` (tasks in listed order, each output becoming
the next task's context) and `Process.hierarchical` (a **manager agent**, auto-generated or supplied,
decides at runtime which agent performs each task and in what order — a dispatcher pattern)
[crewai2026techref; crewai2026flowsguide]. Practitioner consensus is that hierarchical is less
predictable and materially more expensive because the manager consumes tokens at every decision
point — CrewAI's own benchmarking is cited at **roughly 30–60% more token cost** depending on crew
size [crewai2026techref] `[UNVERIFIED: benchmark figure is reported second-hand]`.

**Fan-in primitives — the closest thing to collective completion semantics in any framework.** Flows
provide **`or_()`** (fire when *any* listened step finishes) and **`and_()`** (fire only when *all*
do) as join conditions [crewai2026flowscrews]. In MPI terms these are `MPI_Waitany` / `MPI_Waitall`
over statically-named methods. Worth citing precisely, because it is a real (if minimal) synchronised
join; it is not a barrier over a dynamic group, and there is no reduction operator.

**State / persistence model.** A Flow carries structured state as a **Pydantic model on
`self.state`**, so every method reads and writes the same typed object; **`@persist`** makes that
state durable across runs, enabling resume-after-crash and audit
[crewai2026flowscrews; crewai2026prodarch]. Crews additionally have a **memory** subsystem:
`memory=True` on the Crew, with `memory_config` for backends; CrewAI **rebuilt memory in 2025**,
replacing separate short-term / long-term / entity memory types with a **unified `Memory` class**,
and agents share context via scopes [crewai2026flowsguide; crewai2026techref]. Current agent
capabilities listed by the project include "tools, memory, knowledge, **checkpointing**, async
execution, and **MCP/A2A support**" [crewai2026repo].

An honest and quotable criticism from the practitioner literature: production deployments "often
replace CrewAI's default memory subsystem entirely with application-layer state held in Flow state
objects, persisted to PostgreSQL," because "CrewAI's memory primitive is a **black box from an audit
perspective** — what was stored, who stored it, when, and how to redact it on demand are questions
the default subsystem does not answer cleanly" [crewai2026techref]. This is direct evidence for
AgentMPI's motivating claim that *shared state among agents lacks a specified model*.

**Failure model.** Two tiers: **task-level retries** inside a Crew, and **orchestration-level
recovery** in a Flow — "resume from last step" via `@persist` [crewai2026flowsguide]. Human-in-the-
loop is `@human_feedback` at any Flow step, and is explicitly **not built in at the Crew
orchestration level** [crewai2026flowsguide].

**Groups?** A Crew is a *named collection of agents* — the closest the framework layer comes to a
group object — but it has no rank identity, no membership epochs, no dynamic join/leave, and no
group-scoped operations; it is a roster, not a communicator. **Collectives?** `and_()`/`or_()` joins
only; no broadcast/reduce/allgather. **Sync primitives?** None beyond those joins; no locks or
barriers. **Fault tolerance?** Flow-level `@persist` resume + task retries; no failure detector, no
partial-membership semantics. **Context management?** Unified `Memory` + explicit state passing;
see §3(iv).

### 2.6 OpenAI Agents SDK — successor to Swarm

**Status:** the production successor to the experimental **Swarm** project; actively developed
Python (and TypeScript) SDK. Core abstractions: **agents** (LLM + instructions + tools),
**handoffs** (an agent transfers control to another agent, modelled as a tool call), **guardrails**
(input/output validation running alongside the agent), **sessions** (automatic conversation history),
and tracing.

**Coordination model.** Delegation by **handoff** — strictly one-active-agent-at-a-time transfer of
control, plus "agents as tools" for nesting. There is no parallel peer topology in the core model;
concurrency is ordinary Python `asyncio` in user code. This is the purest example in the survey of
"orchestration as an application pattern."

**State / persistence model — unusually broad, and worth a table in the paper.** Session memory
"automatically maintain[s] conversation history across multiple agent runs, eliminating the need to
manually handle `.to_input_list()` between turns" [openai2026sessions]. Documented backends
[openai2026sessions]:

| Session type | Purpose (verbatim intent) |
|---|---|
| `SQLiteSession` | local dev; file-backed or in-memory |
| `AsyncSQLiteSession` | async SQLite via `aiosqlite` |
| **`RedisSession`** | **"Shared memory across workers/services"**; low-latency distributed deployments |
| `SQLAlchemySession` | production apps with existing DBs (Postgres/MySQL/SQLite) |
| `MongoDBSession` | multi-process storage; **atomic sequence counter for ordering** |
| **`DaprSession`** | cloud-native via Dapr sidecars; **multiple state stores plus TTL and consistency controls** |
| `OpenAIConversationsSession` | server-managed history via the OpenAI Conversations API |
| **`OpenAIResponsesCompactionSession`** | **long conversations with automatic compaction** (wraps another backend) |
| `AdvancedSQLiteSession` | SQLite plus **branching**/analytics |
| `EncryptedSession` | encryption + TTL wrapper |

Three of these matter to AgentMPI's argument. `RedisSession` is explicitly *shared memory across
workers* — the closest the SDK gets to inter-executor sharing (§3.i). `MongoDBSession`'s **atomic
sequence counter for ordering** and `DaprSession`'s **consistency controls** are the only places in
this entire survey where a framework names a *consistency* knob for agent state, and even there it is
delegated to the store, not defined by the framework. `OpenAIResponsesCompactionSession` is a
first-class answer to context exhaustion (§3.iv). The docs also warn against mixing client-side
sessions with server-managed `conversation_id` / `previous_response_id` in the same run
[openai2026sessions] — i.e. two competing state authorities, unreconciled.

**Failure model — via Temporal, not natively.** The SDK ships an optional dependency group
**`temporal` (`temporalio==1.26.0`) for "durable execution and long-running workflows"**
[openai2026config]. This is the strongest single piece of evidence for the layering claim in §2:
the flagship vendor SDK does not implement a failure model; it *depends on a workflow engine* for
one. Other extras: `litellm` (100+ providers), `redis>=7`, `sqlalchemy>=2.0`, `voice`, and
**`sandbox-backends` (docker, e2b, modal, runloop, vercel)** for isolated code execution
[openai2026config].

**Groups?** No. **Collectives?** No. **Sync primitives?** None in the framework; ordering delegated
to the session store. **Fault tolerance?** Only by adopting Temporal. **Context management?**
`OpenAIResponsesCompactionSession`; see §3(iv).

### 2.7 Google ADK — Agent Development Kit

**Status: ADK 2.0 stable, released 19 May 2026**, with **breaking changes** to the agent API, event
model and session schema. Compatibility is one-directional: "**Sessions generated by ADK 2.0 are
readable by ADK 1.28+ (extra fields will be ignored), but are incompatible with older 1.x
versions**" [adk2026repo]. Migration is substantial: 1.x `Agent()` constructor signatures changed and
agents must be wrapped as/deployed as Workflow Runtime nodes; `@tool` decorators are superseded by
`@WorkflowNode` patterns with **event-routed rather than LLM-driven** tool invocation; 1.x session
`.state` attributes are replaced by Event-based state persistence [adk2026migration]
`[UNVERIFIED: the migration specifics come from a community post, not Google's own migration guide]`.

**Coordination model.** The **Workflow Runtime**: "a graph-based execution engine for composing
deterministic execution flows for agentic apps, with support for **routing, fan-out/fan-in, loops,
retry, state management, dynamic nodes, human-in-the-loop, and nested workflows**" [adk2026repo].
Applications are built from two main classes, `Agent` and `Workflow`, with `Workflow(edges=[...])`
[adk2026repo]. Notably ADK also has **dynamic nodes**: `await ctx.run_node(node, ...)` runs a node
dynamically at runtime (caller needs `rerun_on_resume=True`) [adk2026stateevents] — the only
*dynamic topology* mechanism found in the framework layer, and the nearest analogue to spawning.

**Task API.** ADK 2.0 adds "**structured agent-to-agent delegation** with multi-turn task mode,
single-turn controlled output, mixed delegation patterns, human-in-the-loop, and **task agents as
workflow nodes**" [adk2026repo], with built-in A2A protocol support [adk2026migration]. This is the
tightest protocol/framework integration in the survey — but delegation is still 1:1.

**State model — the most explicitly specified of any framework, and the most useful precedent for
AgentMPI.** Data moves between nodes as **Events**, with three distinct parameters: **`output`**
(node-to-node), **`message`** (user-facing response), and **`state`** (automatically persisted across
nodes for the session) [adk2026datahandling]. State mutation has a **defined commit protocol**: a
write to `context.state[...]` is "initially recorded locally within the current `InvocationContext`"
and is "**only guaranteed to be persisted** … *after* the `Event` carrying the corresponding
`state_delta` in its `actions` has been `yield`-ed by your code and subsequently processed by the
`Runner`"; on resumption the agent can "reliably access the session state … reflecting the changes
that were committed by the `Runner` from the *previously yielded* event"
[adk2026eventloop]. The Runner uses `SessionService`, `ArtifactService` and `MemoryService` to commit
`state_delta` and `artifact_delta` [adk2026eventloop].

**This is a genuine (if modest) consistency model** — delta-based, commit-on-yield, single-writer,
read-your-committed-writes — and the paper should cite it as the state of the art rather than pretend
nothing exists. Its limits: it is scoped to one session with one Runner as the serialising authority;
there is no multi-writer story, no conflict resolution, no distributed agreement.

ADK also defines **scoped state via key prefixes**: `app:` (global), `user:` (per-user), `temp:`
(ephemeral, discarded after execution) [adk2026stateevents], plus an **event-isolation `branch`**
field on context [adk2026stateevents] — i.e. namespacing, which is the raw material for scoped
communicators. Explicit size caution: `state` "should not be used to persist large amounts of data"
— use **artifacts** or database tools for large resources [adk2026datahandling]. Context also exposes
`save_artifact`/`load_artifact` and **`await search_memory(query)`** for long-term memory lookup
[adk2026stateevents].

**Failure model.** Per-node **retry** (context exposes an `attempt`-style field that is higher on a
retry) and resumability (`rerun_on_resume`) [adk2026repo; adk2026stateevents]; session persistence
via `SessionService`. No group failure model.

**Groups?** Scoped namespaces (`app:`/`user:`/`temp:`, `branch`) but no group object with membership
or rank. **Collectives?** **fan-out/fan-in as graph primitives** [adk2026repo] — the most explicit
collective-shaped feature named by any framework, but still topology, not a typed collective with
reduction operators or group-completion guarantees. **Sync primitives?** The Runner's commit-on-yield
serialisation; no barriers, locks or CAS. **Fault tolerance?** Node retry + resume. **Context
management?** `temp:` prefix, artifact offloading, `search_memory`; see §3(iv).

### 2.8 LlamaIndex Workflows

**Status:** shipped standalone as `llama-index-workflows` (**v2.x line; v2.23.3** observed on PyPI),
also re-exported as `llama_index.core.workflow` to keep the umbrella API stable
[llamaindex2026pypi; llamaindex2026workflows]. The 1.0 release was June 2025 and 2.0 August 2025
[llamaindex2026pypi].

**Coordination model — async-first, event-driven, and the most explicitly MPI-shaped API in the
survey.** "Steps are async functions that process incoming events from an **asyncio queue** and emit
new events to other queues" [llamaindex2026pypi]. The documented decision table is worth quoting
almost verbatim because it maps directly onto collective idioms
[llamaindex2026workflows]:

| Construct | Meaning |
|---|---|
| return `B` | plain step-to-step handoff |
| return `list[A]` | a step has a finite batch and produces all work items (**scatter**) |
| accept `list[A]` | a step needs the **full batch** of results before continuing (**gather/barrier-on-batch**) |
| `ctx.send_event(...)` | emit incrementally, an unknown number of events, or from outside the running workflow |
| **`ctx.collect_events(...)`** | you used `send_event` and need to **manually wait for a known set of events** |
| **`ctx.store`** | **steps need shared per-run state** |
| `Resource(...)` | clients/indexes/models that should *not* live in serialized state |

`ctx.collect_events` is the closest existing analogue to `MPI_Waitall` over a dynamic set, and
`accept list[A]` is a static gather-with-barrier. The library also **statically validates** the graph
implied by type signatures: start/stop events exist, produced events have consumers, consumed events
have producers [llamaindex2026workflows].

**State model.** Each run has a `Context` with a **state store** (`ctx.store`), untyped by default or
a Pydantic model for typing, validation and custom (de)serialisation; the docs stress that
`ctx.store` is for "values that steps need to share during a run," must be JSON-serialisable, and
"is not a place for heavyweight clients, indexes, file handles" — those go in `Resource`s
[llamaindex2026state]. Cross-run continuity: pass the same `ctx` into `.run()`; "if the context is
still running, `run(ctx=ctx)` **resumes** that run and does not send a new `StartEvent`. If the
previous run has completed, `run(ctx=ctx)` starts a new run with the same stored state"
[llamaindex2026state]. Atomic state updates are supported via a context-manager pattern on the typed
store [llamaindex2026state].

**Failure model — explicitly DIY, and unusually candid about it.** "Workflows are ephemeral by
[default] … the state is gone, and the next `run()` starts fresh," and **"There is no built-in
checkpointer to enable"** [llamaindex2026durable]. You build the loop yourself: stream internal
events with `stream_events(expose_internal=True)`, snapshot on `StepStateChanged` with
`StepState.NOT_RUNNING`, and persist `Context.to_dict()` / restore `Context.from_dict()`
[llamaindex2026durable]. The documented resume semantics are precise and directly relevant to
AgentMPI: "On resume, the restored run **re-dispatches the events that were still pending and rebuilds
the partial fan-in buffers**. Completed steps don't re-run … A [step] that was mid-execution when you
captured the state is **rewound and runs again from the top**. **Resume is at-least-once, and step
side effects need to be safe to repeat**" [llamaindex2026durable]. Snapshots are JSON, and a
non-encodable value makes `to_dict()` raise so "the whole snapshot fails, not just that field"
[llamaindex2026durable]. Alternatively a **runtime plugin** owns persistence: the **DBOS runtime**
"journals step transitions to a database, so a crashed workflow can resume without your checkpoint
loop," and `WorkflowServer` can use the same runtime plugin [llamaindex2026durable]. A separate
`WorkflowCheckpointer` utility wraps `Workflow.run()`, stores per-`run_id` checkpoints at step
completion, supports `filter_checkpoints()` and `run_from()` a chosen checkpoint, and per-step
enable/disable [llamaindex2026checkpoint].

**Groups?** No. **Collectives?** Scatter/gather idioms and `collect_events` — the best existing
approximation, still without reduction operators, group identity, or completion guarantees under
failure. **Sync primitives?** `collect_events` / `list[A]` joins; no barrier over named peers, no
locks. **Fault tolerance?** At-least-once resume that *you* implement, or a runtime plugin.
**Context management?** `Resource` vs state separation keeps snapshots small; no LLM-context
strategy of its own.

### 2.9 Pydantic AI — durability as a *pluggable capability*

**Status:** active; v2 has shipped and v3 is planned (deprecations are "slated for removal in v3")
[pydanticai2026runtimecap]. Self-described as "a typed, extensible agent loop" with model-agnostic
providers, built-in MCP client support, and deployment surfaces spanning web frontend, CLI, voice,
and "a durable background queue" [pydanticai2026repo].

**The interesting contribution: durable execution is a first-class, *engine-pluggable* capability.**
Pydantic AI supports **Temporal, DBOS, Prefect and Restate** natively, plus **Restate, Kitaru and
Airflow** integrations, described as "first-party and co-maintained"
[pydanticai2026repo; pydanticai2026site]. The API moved from wrapper agents to capabilities:
`TemporalDurability`, `DBOSDurability`, `PrefectDurability` attached via
`Agent(..., capabilities=[...])`, **deprecating** `TemporalAgent` / `DBOSAgent` / `PrefectAgent`
(removal in v3) [pydanticai2026pr4977]. The semantics are deliberately explicit: the capability makes
an agent *durable-capable*, and "**your workflow/flow decides which runs are durable**" — you must
call `agent.run()` inside your own `@workflow.defn` / `@DBOS.workflow` / `@flow`, and "porting the
constructor arguments but calling `agent.run()` directly produces a run that works but is **not
durable**" [pydanticai2026pr4977; pydanticai2026prefect]. Under Temporal, "**every model and tool call
becomes a durable activity**" [pydanticai2026repo]; under Prefect the capability routes model
requests, tool calls and MCP I/O through Prefect tasks [pydanticai2026prefect].

**Why this matters for the paper.** Pydantic AI's issue tracker documents the *engine-differentiated*
nature of these guarantees with unusual honesty — a lesson AgentMPI should cite when specifying its
own failure model: Temporal rejects per-run capabilities (activities must be worker-registered
upfront) and per-run toolsets; DBOS/Prefect reject runtime/override models because their steps close
over the construction-time model; DBOS defaults to
`parallel_execution_mode='parallel_ordered_events'` to preserve deterministic-replay guarantees, with
`'parallel'` **excluded by type**; and migration continuity differs per engine (Temporal replays
wrapper-era histories transparently, DBOS needs opt-in legacy registration, Prefect re-executes live
on flow retry) [pydanticai2026runtimecap; pydanticai2026pr4977]. The conclusion they draw is the
right one for a protocol designer: "a runtime abstraction should make an engine **state its migration
story explicitly** rather than inherit an implicit 'it replays' assumption"
[pydanticai2026runtimecap].

**Groups / collectives / barriers?** None. Human-in-the-loop tool approval is built in via deferred
tools [pydanticai2026repo]. **Fault tolerance?** Best-in-class *borrowed* durability, via four
engines. **Context management?** Not a differentiator.

### 2.10 smolagents (Hugging Face)

**Layer.** Deliberately minimal agent library, centred on the **code-as-action** pattern: the agent
writes Python code to call tools rather than emitting JSON tool calls (`CodeAgent`), with sandboxed
execution backends, plus a `ToolCallingAgent` for the conventional JSON path. Multi-agent support is
by **nesting** — a manager agent holds managed agents and calls them like tools.

Two agent classes represent "two different paradigms for how agents interact with tools":
**`CodeAgent`** (generates and executes Python code as its action) and **`ToolCallingAgent`** (emits
JSON tool calls, "the common format used in [other] frameworks (OpenAI API)")
[smolagents2026guidedtour].

**Coordination model — hierarchical nesting, and the docs are refreshingly explicit about why.**
"You can easily build hierarchical multi-agent systems with `smolagents`": give a sub-agent `name`
and `description` attributes, which "will then be embedded in the manager agent's system prompt to
let it know how to call this managed agent, **as we also do for tools**", then pass it via the
**`managed_agents`** parameter [smolagents2026guidedtour]. So a sub-agent *is literally a tool* in the
manager's prompt — the clearest possible illustration of the layering claim.

The docs also give the standard motivation for multi-agent decomposition, which happens to be a
**context-window** argument rather than a parallelism argument: "having agents with separate tool sets
and memories allows [one] to achieve efficient specialization. For instance, why fill the memory of
the code generating agent with all the content of webpages visited by the web search agent? It's
better to keep them **separate**" [smolagents2026guidedtour]. Note also that smolagents credits
"Microsoft's framework Autogen" with introducing multi-agent systems [smolagents2026guidedtour].

**State model:** in-process agent memory (step logs); no persistence layer, no checkpointer.
**Failure model:** bounded `max_steps` plus error feedback into the next reasoning step; no durable
execution. Sandboxing/authorised-imports gates code execution (`additional_authorized_imports`)
[smolagents2026multiagents]. **Groups / collectives / sync primitives / shared-state consistency:
none.** `[UNVERIFIED: current version number not confirmed within this survey's search budget.]`

smolagents earns its place in the related work precisely *because* it is minimal: it shows that a
large fraction of production "multi-agent" practice is a manager agent calling sub-agents as tools,
with no distributed-systems machinery at all, and that the *stated* motivation for multi-agent
design in that world is context isolation — one of AgentMPI's four target failures.

### 2.11 DSPy

**Layer.** Not an orchestration framework in the same sense — DSPy is a **programming model and
optimiser** for LLM pipelines: declarative `Signature`s, composable `Module`s (`Predict`,
`ChainOfThought`, `ReAct`), and **optimisers/compilers** that search prompts and few-shot
demonstrations (and can drive fine-tuning) against a metric. Multi-agent structure, where it exists,
is ordinary Python composition of modules.

**Version.** **DSPy 3** was released **August 2025** as `3.0.x` and stabilised through 2026; the
latest stable observed is **`3.2.1` (2026-05-05)**, MIT, Python 3.10–3.14, ~34.5K stars. The 3.0
rewrite **deprecated the old `dspy.Settings` / `dspy.OpenAI` direct-client API in favour of
LiteLLM-backed `dspy.LM`**, so one LM string covers 100+ providers; migrations from 2.5.x require
porting client config and retraining, while signatures and modules generally survive
[dspy2026hivebook] `[UNVERIFIED: version details come from a secondary knowledge-base page, not
PyPI/GitHub directly.]`

**Key property for the paper: optimisation is build-time, not request-time.** Optimisers take
`metric` + `trainset` (+ optional `valset`); `compile(program, trainset=...)` **returns a new module,
leaving the original untouched**; compiled programs serialise to JSON via `compiled.save(...)` /
`.load(...)`, "so optimization runs once at build time" [dspy2026hivebook]. That is a genuine
*artefact* boundary — a compiled program is a versioned, serialisable object — which is a useful
precedent for how AgentMPI might version a communicator's configuration.

**Relevance to AgentMPI: orthogonal but important to acknowledge.** DSPy's contribution is
*optimisation of the agent program*, not *coordination of agent processes*. There is no distributed
state model, no persistence of running state, no failure model, no groups, no collectives, no
synchronisation. If AgentMPI defines a stable coordination substrate, DSPy-style compilation over
that substrate is a natural follow-on (optimising *what each rank says* rather than *how ranks
communicate*) — and the ecosystem already gestures at this: DSPy programs are described as slotting
in "as the per-agent reasoning unit" inside higher-level orchestration patterns, and **AG2 (an
AutoGen fork)** agents "can be replaced by DSPy-compiled programs to add self-improvement on top of
conversation patterns" [dspy2026hivebook]. *(AG2 is worth a footnote in the paper as the community
fork that continued AutoGen's conversational lineage after Microsoft's pivot to MAF.)*

### 2.12 Ray — actors, and the closest thing to real distributed shared memory

**Layer.** General-purpose distributed compute, used as an *agent substrate* rather than an agent
framework. The idiomatic 2026 pattern is: "each agent is a **Ray actor** with state, the orchestrator
is itself a Ray actor that holds handles to specialist actors," and "the **memory actor is itself an
actor** — agents call it via messages rather than sharing a database connection pool"
[callsphere2026actor].

**What Ray genuinely provides that no agent framework does:**
- **Actor supervision and restart policy.** `max_restarts=-1` for unlimited restarts,
  `max_consecutive_restarts` to damp cascades, and `actor_lifetime="detached"` for agents that
  outlive a request [ray2026agentarch]. Reported actor restart time after node failure ~2.3 s
  including state reload [ray2026agentarch] `[UNVERIFIED: single practitioner benchmark]`.
- **Backpressure via actor mailboxes** — "actor mailboxes naturally throttle the system under load"
  [callsphere2026actor].
- **A shared object store** (plasma) giving zero-copy shared immutable objects across actors on a
  node — the only true *shared-memory* mechanism in this whole survey.
- **Fan-out/fan-in with `ray.wait`** (the guidance is explicitly to use `ray.wait` patterns rather
  than nested synchronous `ray.get` inside actors, which stalls the event loop)
  [ray2026agentarch; callsphere2026actor].
- **Heterogeneous placement** — GPU agents and CPU agents in one workflow, with **placement groups**
  to co-locate state with compute [callsphere2026actor; ray2026agentarch].

**What Ray does *not* provide:** LLM-specific semantics of any kind — no context management, no
agent naming/discovery, no message schema, no notion of an agent's conversational state. Durability
is **manual**: "explicit state serialization using `ray.cloudpickle` or an external store (Redis)"
plus "a custom checkpoint method that saves to S3 every N calls" [ray2026agentarch]. And critically:
**Ray has no collectives at the agent level** — its collective communication library exists for
tensor/ML workloads, not for agent groups, and there is no barrier or lock abstraction offered to
agents. The practitioner recommendation is telling: "For most teams in 2026, LangGraph or the OpenAI
Agents SDK is the right starting point … **Reach for Ray when you have heterogeneous compute or
distributed scale that the higher-level frameworks do not handle**" [callsphere2026actor].

**Groups?** Actor handles + placement groups (a *scheduling* group, not a communication group).
**Collectives?** Not for agents. **Sync primitives?** `ray.wait`; no barrier/lock for agents.
**Fault tolerance?** Real supervision, manual state durability. **Context management?** None.

Ray is the single best argument that AgentMPI's *mechanisms* are implementable — and the single best
argument that they don't currently exist at the agent layer, because Ray users hand-roll them.

### 2.13 Temporal + agents — durable execution as a *layer underneath* frameworks

**Layer.** General-purpose durable execution (event-sourced workflow engine), increasingly adopted
as the reliability layer beneath agent frameworks. Temporal's own engineering writing is the sharpest
articulation of the layering thesis in the entire literature I surveyed, and the paper should quote
it:

> "**LangGraph's durability, checkpointer and all, is framework-local: it persists the graph.
> Temporal is general-purpose Durable Execution: it persists the whole system, including the agent
> loops, drivers, human wait, and Timers, across both frameworks at once.**" [temporal2026multiagent]

The same post describes running "the same multi-agent fleet on Google ADK, on LangGraph, and on both
at once, with Temporal as a layer underneath," motivated by the observation that "organizations rarely
choose one framework cleanly" — so when "one team's ADK service and another's LangGraph service have
to cooperate on the same order, Temporal can provide the Durable Execution layer that makes the whole
system reliable without requiring either team to rewrite its own stack" [temporal2026multiagent].

**Mechanism.** The workflow/activity split separates deterministic control flow (replayable) from
non-deterministic LLM/tool calls (activities with per-type retry policies); state lives in the
**Event History**, not RAM. The consequence for human-in-the-loop is a genuine capability no agent
framework matches on its own: "The Workflow **parks on the wait while burning zero compute**, and the
pending decision lives in the Event History … no deploy, eviction, or crash can touch it … Because
each wait is its own parked Workflow, **thousands can wait independently** in an open state without
consuming Worker CPU. Every decision lands in the Event History, so **the audit trail comes free**"
[temporal2026multiagent]. Durable **Timers** give "escalate after N hours" semantics for free.

**Failure modes it targets, stated plainly by practitioners:** "A worker dies mid-workflow and the
agent loses all accumulated context. An LLM API returns a 429 on step 47 of 50 and there's no clean
way to resume. I kept seeing teams **rebuild the same reliability primitives from scratch**:
checkpointing, retry logic, state persistence, crash recovery" [temporal2026deepdive]. The same
source notes Temporal is unnecessary complexity if an agent finishes in 20 seconds, and argues "the
industry is still **underinvesting in the reliability layer** relative to the agent logic itself"
[temporal2026deepdive].

**Groups?** No. Workflows are individually addressed by ID; there is no group object, no collective
over a set of workflows, and signals are point-to-point. **Collectives?** No — a parent workflow can
start N children and await them (a hand-rolled gather), but there is no group-scoped operation or
reduction. **Sync primitives?** Signals, queries, durable timers, and (in some SDKs) update
handlers — plus real **mutual exclusion patterns** built on a single workflow-as-lock-holder, which
is the standard Temporal idiom `[UNVERIFIED: whether an official lock primitive exists in current
SDKs, as opposed to the documented workflow-as-mutex pattern]`. **Fault tolerance?** The strongest
available: event-sourced replay, per-activity retry policy, worker-death survival.
**Context management?** None (agnostic to LLM context).

**Adjacent engines with the same role:** DBOS (journals step transitions to a database)
[llamaindex2026durable], Prefect (3.0 "built-in caching and transactional semantics … making
workflows naturally idempotent") [pydanticai2026prefect], and Restate — all now reachable as
pluggable durability backends from Pydantic AI [pydanticai2026site] and, via the `temporal` extra,
from the OpenAI Agents SDK [openai2026config].

**The layering conclusion this section supports:** the strongest failure models in the LLM-agent
ecosystem are **not in the agent protocols or the agent frameworks at all** — they are borrowed from
pre-existing general-purpose workflow engines, and they are scoped to a *single* durable execution,
not to a *group* of communicating agents.

---

## 3. What current systems actually do about the four failures

This section is the evidence base for the paper's motivation. For each failure I report what exists,
name the mechanism, and state the residual gap.

### (i) Sharing information among executors

**What exists.**

| Mechanism | System | What it actually gives you |
|---|---|---|
| Graph state + `Annotated` reducers on a shared Postgres checkpoint store | LangGraph | Typed shared state per `thread_id`; user-supplied merge operator per channel for concurrent fan-in writes; **multi-worker coordination** off one store [langgraph2026prod; langgraph2026blog] |
| `ctx.store` (typed Pydantic state store) | LlamaIndex Workflows | "Steps need shared per-run state"; atomic updates; must be JSON-serialisable [llamaindex2026state; llamaindex2026workflows] |
| Event `state` with `state_delta`, scoped by `app:` / `user:` / `temp:` prefixes | Google ADK | Delta-based session state committed by the Runner on event yield; explicit "not for large data — use artifacts" [adk2026eventloop; adk2026datahandling; adk2026stateevents] |
| Flow `self.state` (Pydantic) + `@persist`; unified `Memory` class with scopes | CrewAI | Typed run state shared by all Flow methods; cross-run memory for Crews [crewai2026flowscrews; crewai2026flowsguide] |
| `RedisSession` — "shared memory across workers/services"; `MongoDBSession` atomic sequence counter; `DaprSession` consistency controls | OpenAI Agents SDK | Session history shared across processes; **ordering** and **consistency** knobs delegated to the store [openai2026sessions] |
| Actor mailboxes; topic-based pub/sub | AutoGen v0.4+ | Asynchronous message passing; many-to-many *addressing* [autogen2025v04] |
| A dedicated **memory actor** + plasma object store | Ray | Real shared memory (immutable objects); "agents call it via messages rather than sharing a database connection pool" [callsphere2026actor] |
| MCP **resources** (URI-addressed, `subscriptions/listen`) | MCP | A named, fetchable, subscribable blob per server [mcp2026spec; mcpblog2026] |
| A2A **artifacts** | A2A | Typed task outputs returned to the caller — deliberately *without* sharing internal memory [lf2026a2apress] |
| BSP superstep message collection | Microsoft Agent Framework | "No race conditions between supersteps; each sees a **consistent view of messages**" [maf2026workflows] |

**The residual gap.** Every mechanism above is either (a) **shared state owned by a single
orchestrator** that serialises all writes (LangGraph's graph runtime, ADK's Runner, CrewAI's Flow,
MAF's superstep engine), or (b) **a store that agents happen to point at**, where the consistency
properties belong to Redis/Postgres/Mongo/Dapr and are never specified in agent terms
[openai2026sessions]. There is **no multi-writer shared-state abstraction with defined semantics for
peer agents**: no epochs, no version-vector exposure to the application (LangGraph tracks channel
versions internally [langgraph2026blog] but does not expose them as a programming model), no
compare-and-swap, no conflict-resolution contract, no read-your-writes guarantee across *agents*
rather than across *nodes of one graph*. And A2A — the actual inter-agent protocol — makes
non-sharing a **design principle** [lf2026a2apress]. The CrewAI audit critique is the sharpest
empirical statement of the gap: with the default memory subsystem, "what was stored, who stored it,
when, and how to redact it on demand are questions the default subsystem does not answer cleanly"
[crewai2026techref].

### (ii) Surviving executor death

**What exists** — this is the best-served of the four, and the paper should concede it clearly.

- **LangGraph**: pluggable **checkpointers** (`MemorySaver` / `SqliteSaver` / **`PostgresSaver`**)
  snapshot state after **every node execution**, keyed by `thread_id`; **durable execution** means
  agents "persist through failures … automatically resuming from exactly where they left off"
  [langgraph2026repo; langgraph2026prod]. Checkpoints are machine-independent by design (serialised
  channel values in MsgPack, optionally encrypted, plus channel version strings and which versions
  each node last saw) [langgraph2026blog]. Practical wins: crash recovery, multi-worker coordination,
  point-in-time replay of any failed run [langgraph2026prod].
- **Microsoft Agent Framework**: checkpoint at superstep boundaries; "**checkpointing and hydration
  ensure long-running processes survive interruptions**" [maf2026v1; maf2026workflows].
- **CrewAI**: task-level retries inside a Crew; Flow-level `@persist` for resume-from-last-step
  [crewai2026flowsguide; crewai2026prodarch].
- **Google ADK**: per-node **retry**, `rerun_on_resume`, session persistence via `SessionService`
  [adk2026repo; adk2026stateevents].
- **LlamaIndex Workflows**: **no built-in checkpointer** — you snapshot `Context.to_dict()` on
  `StepStateChanged`/`NOT_RUNNING` yourself, or adopt a runtime plugin (DBOS) that journals step
  transitions; resume is **at-least-once** and "step side effects need to be safe to repeat," with
  in-flight steps **rewound and re-run from the top** [llamaindex2026durable].
- **Pydantic AI**: durability as an attachable **capability** over Temporal / DBOS / Prefect /
  Restate (plus Kitaru, Airflow); under Temporal "every model and tool call becomes a durable
  activity" [pydanticai2026repo; pydanticai2026pr4977].
- **OpenAI Agents SDK**: durable execution only via the optional **`temporal`** extra
  (`temporalio==1.26.0`) [openai2026config].
- **Ray**: genuine **supervision** — `max_restarts=-1`, `max_consecutive_restarts` to damp cascade
  failures, detached actor lifetimes — but state durability is manual (cloudpickle / Redis / periodic
  S3 checkpoints) [ray2026agentarch].
- **Temporal**: event-sourced replay; workers can die mid-workflow and the pending state lives in the
  **Event History**, not RAM; thousands of parked workflows consume no worker CPU
  [temporal2026multiagent].

**The residual gap — and it is precisely the MPI-shaped one.** Three things are missing:

1. **Scope.** Durability is per-orchestration, not per-group. Temporal's own engineers draw the line:
   "LangGraph's durability, checkpointer and all, is **framework-local**: it persists the graph"
   [temporal2026multiagent]. Nothing persists *a group of independently-scheduled peer agents* as a
   unit.
2. **No failure detector or membership change.** No system surveyed exposes "peer *k* is
   suspected/dead" as an event to the surviving agents, nor a way to continue a collective with
   reduced membership. This is exactly the problem space of MPI's ULFM work
   [bland2013ulfm], and it is untouched in the LLM-agent world.
3. **Delivery semantics are unspecified or weak.** Where they *are* specified, they are at-least-once
   with idempotency pushed onto the user [llamaindex2026durable], and the guarantees differ per
   backing engine in ways Pydantic AI documents but cannot abstract away (Temporal replays
   transparently; DBOS needs opt-in legacy registration; Prefect re-executes live on flow retry)
   [pydanticai2026runtimecap].

### (iii) Synchronisation and locking between executors

**What exists — the thinnest of the four.**

- **Real barriers, but implicit and whole-graph**: the BSP superstep boundary in **Microsoft Agent
  Framework** ("**waits for all executors to complete before advancing (synchronization barrier)**")
  [maf2026workflows] and the equivalent superstep boundary in **LangGraph**'s Pregel engine.
- **Join/completion conditions**: CrewAI Flows' **`and_()` / `or_()`** [crewai2026flowscrews];
  LlamaIndex's **`ctx.collect_events(...)`** ("manually wait for a known set of events") and
  `accept list[A]` batch-gather [llamaindex2026workflows]; ADK's **fan-out/fan-in** graph primitives
  [adk2026repo]; Ray's `ray.wait` [ray2026agentarch].
- **Serialisation by a single authority**: ADK's commit-on-yield rule — state is guaranteed persisted
  only after the `Event` carrying the `state_delta` has been yielded and processed by the Runner
  [adk2026eventloop]; MongoDB sessions' **atomic sequence counter for ordering**
  [openai2026sessions].
- **Actual locks: pushed to infrastructure.** LangGraph production guidance recommends **Redis for
  per-thread locks** and per-tool rate limiting [langgraph2026prod]. Temporal's idiom is a workflow
  acting as the lock holder. The OpenAI Agents SDK's own SQLAlchemy session code carries
  `threading.Lock`s, SQLite `busy_timeout`, WAL mode, and a lock-retry delay schedule — i.e. the SDK
  is fighting *database* locks, not offering agent-level locks [openai2026sqlalchemy].

**The residual gap.** **No system surveyed offers a callable synchronisation primitive over a named
set of agents.** There is no `barrier(group)`, no lock/mutex/semaphore with agent-visible semantics,
no compare-and-swap on shared agent state, no fence or ordering guarantee between independent agents.
The two barriers that do exist (MAF, LangGraph) are properties of a single in-process scheduler over a
*static* graph, cannot be scoped to a subgroup, and MAF's docs actively tell you to *restructure your
code* to escape the barrier when you want independent parallelism [maf2026workflows]. This is the
strongest and least contestable of AgentMPI's gaps.

### (iv) Context-window exhaustion

**What exists — a mature and genuinely impressive toolkit, and none of it is group-aware.** The
best-specified account is Anthropic's, which frames the problem as **context pollution and
information relevance** rather than window size: "it's likely that for the foreseeable future,
context windows of all sizes will be subject to context pollution and information relevance concerns"
[anthropic2026context]. Four named levers:

1. **Compaction** — "taking a conversation nearing the context window limit, summarizing its
   contents, and reinitiating a new context window with the summary"; "typically … the first lever …
   to drive better long-term coherence" [anthropic2026context]. Now a first-party API primitive,
   **`compact_20260112`** (a January 2026 feature), triggering automatically at a token threshold
   (default ~150K, **minimum 50K**), returning "a typed `compaction` content block that slots into
   the conversation natively" and handling tool-use pairing across the summary boundary; the API
   drops everything before the block on the next request [anthropic2026cookbook]. Lossy by design and
   it charges inference cost [anthropic2026cookbook; tianpan2026context].
2. **Context editing / tool-result clearing** — **`clear_tool_uses_20250919`** "mechanically clears
   old tool results once input passes a threshold (default 100k tokens), keeping a placeholder so
   Claude knows the call was made — it's **lossless for anything re-fetchable** and the cheapest
   option" [anthropic2026ctxeng].
3. **External memory / structured note-taking** — the **`memory_20250818`** tool (file-based, GA)
   "persists notes to files your app stores and Claude reads back just-in-time," letting agents
   "build up knowledge bases over time, maintain project state across sessions, and reference
   previous work without keeping everything in context"
   [anthropic2026context; anthropic2026ctxeng].
4. **Sub-agent architectures** — "specialized sub-agents can handle focused tasks with **clean
   context windows**. The main agent coordinates with a high-level plan while subagents perform deep
   technical work … Each subagent might explore extensively, using **tens of thousands of tokens or
   more, but returns only a condensed, distilled summary of its work (often 1,000–2,000 tokens)**"
   [anthropic2026context]. Reported effect: in Anthropic's production multi-agent research system, an
   **Opus lead with Sonnet sub-agents beat a single-agent baseline by more than 90%**, with
   performance "closely tied to spreading tokens across independent windows"
   [anthropic2026ctxeng] `[UNVERIFIED: the ">90%" figure is quoted second-hand from Anthropic's
   research-system writeup; verify against the primary post before citing a number.]`

Practitioner composition guidance (useful, and cite-worthy as engineering folklore rather than
result): order the levers by what a loss costs — subagents keep bulk out of the orchestrator's window
entirely, context editing evicts re-fetchable results, memory writes the irreplaceable specifics
*outside* the window *before* compaction can summarise them away, and compaction handles the
remaining coherent thread [dreaming2026levers]. Trigger compaction "at ~70% of your effective
window," not at exhaustion, "because once context rot sets in, the summary will itself be degraded
because the model generating it is already impaired" [tianpan2026context]. The sub-agent pattern is
explicitly described as "the **MapReduce pattern** applied to agentic context management"
[tianpan2026context] — a framing AgentMPI should engage with directly, since MapReduce is exactly a
collective.

**Framework-level equivalents.** `OpenAIResponsesCompactionSession` wraps any other session backend
for "long conversations with automatic compaction" [openai2026sessions]; ADK's `temp:` prefix
discards ephemeral values after execution and artifacts offload large data
[adk2026stateevents; adk2026datahandling]; LangGraph splits short-term working memory from a
long-term `Store` [langgraph2026repo]; CrewAI's unified `Memory` distinguishes state (ephemeral,
within a run) from memory (across runs) [crewai2026flowsguide]; LlamaIndex separates serialisable
state from non-serialisable `Resource`s [llamaindex2026state]; smolagents' stated reason for
multi-agent decomposition is keeping the code agent's memory free of the web agent's page content
[smolagents2026guidedtour].

**The residual gap.** Context management is (a) **per-agent and per-session** — every mechanism above
manages *one* window; (b) **uncoordinated** — when a lead agent compacts, nothing informs its peers,
and there is no protocol for agreeing on what the group collectively remembers; (c) **not a resource
in any accounting sense** — no framework treats context as a schedulable, reservable, group-allocated
budget, though practitioners plainly want that ("add token budget tracking to every LLM call from day
one" [tianpan2026context]; "hard ceilings beat heuristics … a maximum step count, an idempotency key
on every tool call" [callsphere2026actor]). The sub-agent/MapReduce pattern is the *de facto*
collective for context, implemented ad hoc in every harness, with no shared abstraction, no typed
reduction, and no failure semantics if a sub-agent dies mid-map.

---

## 4. Layering table

Legend: **✗** = absent; **~** = partial/adjacent, qualified in the cell; **✓** = present with defined
semantics. "Layer" uses the paper's taxonomy: transport / message format / tool-calling /
agent-to-agent / orchestration framework / durable-execution substrate.

| System | Year / current version (Aug 2026) | Layer | Naming & discovery | State model | Groups? | Collectives? | Sync primitives? | Fault tolerance? | Context management? |
|---|---|---|---|---|---|---|---|---|---|
| **MCP** [mcp2026spec] | 2024; spec rev. **2026-07-28** (wire-incompatible with prior revs) | Tool-calling (host/client/server) + message format (JSON-RPC 2.0) | Host-configured servers; `tools/list`, `resources/list`, `server/discover`; external registries | **Stateless** as of 2026-07-28 (`initialize`/`Mcp-Session-Id` retired); resources are URI-addressed | ✗ | ✗ | ✗ (retry only; `tasks/*` is a polled extension) | ~ request-level retry; statelessness aids routing, removes session recovery | ✗ (resources let you *fetch* rather than inline, but no strategy) |
| **A2A** [a2a2026site; lf2026a2apress] | 2025; **v1.0** (Mar 2026), v1.0.1 (May 2026), Linux Foundation | Agent-to-agent RPC | **Agent Card** at `/.well-known/agent-card.json`, signed; `supportedInterfaces[]`; registries | Per-task lifecycle, server-held; **opacity by design** ("without sharing internal memory") | ✗ (strictly 1:1) | ✗ | ✗ | ~ task `failed`/`canceled`, push-notification resumption, ACP-derived task persistence; **no group failure model** | ✗ |
| **ACP (IBM/BeeAI)** [lfaidata2025acpmerge; ibm2026acp] | 2025; **merged into A2A 29 Aug 2025; repo archived** | (was) agent-to-agent REST | (was) HTTP-native | (was) multimodal messages | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Zed ACP (Agent Client Protocol)** [a2asurvey2026] | 2025; v0.13.x `[UNVERIFIED]` | Agent-to-editor (JSON-RPC 2.0) | Public **Agent Registry** (with JetBrains, Jan 2026) | Sessions (create/resume) per editor connection | ✗ | ✗ | ✗ | ✗ | ✗ |
| **AG-UI** [a2asurvey2026] | 2025; pre-stable | Agent-to-user/frontend | ✗ (app-configured) | ~ **bi-directional state sync** with frontend, 1 session | ✗ | ✗ | ✗ | ✗ | ✗ |
| **AGNTCY / SLIM** [a2asurvey2026] | 2025; active, LF-hosted; own ACP archived | Network infrastructure (discovery/identity/observability) | **Decentralized Agent Directory** (OASF records); verifiable decentralized identity | ✗ (carries others' state) | ~ a *directory*, not a communicator | ✗ | ✗ | ~ transport-level (gRPC/SLIM) | ✗ |
| **ANP** [a2asurvey2026] | draft spec; W3C CG path | Agent-to-agent (decentralized) | **W3C DID** + Semantic-Web description + Agent Discovery Service | Negotiated | ✗ | ✗ | ✗ | ✗ | ✗ |
| **Summoner** [a2asurvey2026] | very early (~68 stars) | Cross-org coordination (SPLT wire format) | Reputation-aware discovery; Ed25519 self-sovereign identity | **Deeply stateful signed decision graphs** | ✗ | ✗ | ✗ | ~ durable cross-org transactions (research-grade) | ✗ |
| **AITP** | `[UNVERIFIED]` — could not confirm as a live standard | — | — | — | — | — | — | — | — |
| **Agora** | `[UNVERIFIED]` — name heavily overloaded; unconfirmed | — | — | — | — | — | — | — | — |
| **Microsoft Agent Framework (MAF)** [maf2026v1; maf2026workflows] | **1.0 GA 3 Apr 2026** (.NET + Python); supersedes AutoGen + SK | Orchestration framework (+ MCP/A2A interop) | Design-time graph; `Builder` | Graph state; **checkpoint at superstep boundaries**; hydration | ~ static graph topology; no group object/rank | ~ fan-out + "converge results" as graph shapes; no reduction operators | ~ **real BSP barrier**, implicit & whole-graph; no subgroup scoping, no locks | ~ checkpoint/hydrate one workflow; no failure detector; partial-failure semantics undocumented | ~ agent-level memory |
| **AutoGen** [a2asurvey2026; autogen2025v04] | v0.4 rewrite Jan 2025; **maintenance mode, last feature release Sept 2025** | Orchestration framework (actor model) | Direct actor address + **topic pub/sub** | Conversation-scoped; actor mailboxes | ~ **topics** are named many-to-many addressing — nearest analogue, but no membership/rank | ✗ | ~ per-actor mailbox FIFO; no barrier/lock | ✗ (no durable-execution guarantee surfaced) | ~ conversation summarisation |
| **Semantic Kernel** [maf2026guide; maf2026prod] | **absorbed as MAF's foundation layer**; SK→MAF migration guide + assistant | SDK / kernel + plugins + connectors | Plugin registry (in-process) | Kernel + memory abstractions | ✗ | ✗ | ✗ | ✗ | ~ memory connectors |
| **LangGraph** [langgraph2026repo; langgraph2026prod] | **1.0 GA**; PyPI **1.0.5** (1.0.x line) `[UNVERIFIED: a secondary source reports 1.2.4]` | Orchestration framework (Pregel/BSP) | Static graph nodes; `thread_id` for runs | **Checkpointers** (Memory/Sqlite/**Postgres**) after every node; channel values + **version strings**; short-term + long-term `Store` | ✗ | ~ fan-in with **`Annotated` reducers** ≈ a reduce at a join; no broadcast/allgather | ~ superstep boundary; **locks explicitly delegated to Redis** | ✓ **durable execution**: resume exactly where left off, multi-worker coordination, point-in-time replay — but **framework-local, per-`thread_id`** | ~ short/long-term memory split; lean-state guidance |
| **CrewAI** [crewai2026repo; crewai2026flowscrews] | GA; docs at **v1.12.0** (secondary: v1.14.x weekly) | Orchestration framework (Crews + Flows) | Named roles within a Crew; ✗ external discovery | Crew: **stateless by default**; Flow: Pydantic `self.state` + **`@persist`**; unified `Memory` class (2025 rebuild) | ~ a **Crew is a named roster** — no rank, no epochs, no join/leave | ~ **`and_()` / `or_()`** join conditions ≈ `Waitall`/`Waitany`; no reductions | ~ those joins only; no barrier/lock | ~ task retries + Flow resume-from-last-step; `@human_feedback` | ~ unified `Memory`; state-vs-memory split |
| **OpenAI Agents SDK** [openai2026sessions; openai2026config] | successor to **Swarm**; active | Orchestration framework (handoffs/guardrails/sessions) | ✗ (agents are constructed, not discovered) | **10+ session backends** incl. Redis ("shared memory across workers"), Mongo (**atomic sequence counter**), **Dapr (TTL + consistency controls)**, encrypted, branching | ✗ | ✗ | ~ ordering delegated to the store; SDK code fights *DB* locks, offers none [openai2026sqlalchemy] | ~ **only via the `temporal` extra** (`temporalio==1.26.0`) | ✓ **`OpenAIResponsesCompactionSession`** (automatic compaction) |
| **Google ADK** [adk2026repo; adk2026eventloop] | **2.0 stable 19 May 2026** (breaking: agent API, event model, session schema) | Orchestration framework (Workflow Runtime) + **Task API** with A2A | Workflow node graph; **dynamic nodes** via `ctx.run_node`; A2A-native delegation | **Events with `state_delta`**; commit-on-yield by the Runner; scopes `app:` / `user:` / `temp:`; `branch` isolation; artifacts; `search_memory` | ~ scoped namespaces + `branch`, no group object | ~ **fan-out/fan-in** named as first-class graph primitives; no reduction operators | ~ Runner commit-on-yield serialisation; no barrier/lock/CAS | ~ per-node **retry**, `rerun_on_resume`, `SessionService` persistence | ~ `temp:` discard, artifact offload, `search_memory` |
| **LlamaIndex Workflows** [llamaindex2026pypi; llamaindex2026durable] | v2.x, **2.23.3** observed (1.0 Jun 2025, 2.0 Aug 2025) | Orchestration framework (async event-driven) | Event types; statically validated graph | `Context` + **`ctx.store`** (typed Pydantic, atomic updates); `Resource` for non-serialisables; `run(ctx=ctx)` resumes | ✗ | ~ `list[A]` scatter / gather + **`ctx.collect_events`** ("wait for a known set of events") — the closest existing `Waitall` | ~ `collect_events` / batch-gather; no barrier over peers, no locks | ~ **"no built-in checkpointer"** — DIY snapshot loop, **at-least-once**, in-flight steps rewound and re-run; or DBOS runtime plugin; `WorkflowCheckpointer` + `run_from()` | ~ state/`Resource` split keeps snapshots small; no LLM-context strategy |
| **Pydantic AI** [pydanticai2026repo; pydanticai2026pr4977] | v2 shipped, v3 planned (wrapper agents deprecated, removal in v3) | Orchestration framework (typed agent loop) + durability capabilities | ✗ (constructed); MCP client built in | Engine-owned durable state | ✗ | ✗ | ~ human-in-the-loop tool approval (deferred tools) | ✓ **borrowed & pluggable**: `TemporalDurability` / `DBOSDurability` / `PrefectDurability` (+ Restate, Kitaru, Airflow); every model/tool call a durable activity under Temporal; **guarantees are engine-differentiated and documented as such** | ✗ |
| **smolagents** [smolagents2026guidedtour] | active; version `[UNVERIFIED]` | Orchestration library (minimal; code-as-action) | Sub-agent `name` + `description` **embedded in the manager's prompt, "as we also do for tools"** | In-process step logs; no persistence | ✗ | ✗ | ✗ | ~ bounded `max_steps` + error feedback | ~ **context isolation is the stated reason for multi-agent design** |
| **DSPy** [dspy2026hivebook] | v3.0 Aug 2025; **3.2.1 (2026-05-05)** `[UNVERIFIED: secondary source]` | Programming model + build-time optimiser (orthogonal layer) | Module composition | Module params; **compiled program serialised to JSON** at build time | ✗ | ✗ | ✗ | ✗ | ~ indirect (optimised, shorter prompts) |
| **Ray** [ray2026agentarch; callsphere2026actor] | general-purpose; agent pattern documented for 2026 | Durable-ish compute substrate (actors) | Actor handles; named/detached actors; placement groups | Actor-local state; **plasma object store** (true shared immutable memory); memory-actor pattern | ~ placement groups are a *scheduling* group; actor handles are not a communicator | ✗ at the agent level (collectives exist for tensors, not agent groups); `ray.wait` fan-in | ~ `ray.wait`; **no barrier/lock offered to agents** | ✓ **real supervision**: `max_restarts=-1`, `max_consecutive_restarts`, detached lifetimes — but **state durability is manual** (cloudpickle/Redis/S3) | ✗ |
| **Temporal** [temporal2026multiagent; temporal2026deepdive] | general-purpose; widely adopted as the agent reliability layer 2026 | **Durable-execution substrate beneath frameworks** | Workflow IDs; signals/queries; ✗ agent discovery | **Event History** (event-sourced); state not in RAM | ✗ | ✗ (parent + N children awaited = hand-rolled gather) | ~ signals, queries, **durable timers**, workflow-as-mutex idiom `[UNVERIFIED: official lock primitive]` | ✓ **strongest available**: replay-based recovery, per-activity retry policy, worker death survival, zero-compute parked waits, free audit trail; **persists the whole system, not just one graph** | ✗ (LLM-agnostic) |
| **DBOS / Prefect / Restate** [llamaindex2026durable; pydanticai2026prefect; pydanticai2026site] | active; reachable as pluggable backends | Durable-execution substrates | ✗ | Journaled step transitions (DBOS); Prefect 3.0 caching + transactional semantics ⇒ "naturally idempotent" | ✗ | ✗ | ~ idempotency/caching | ✓ crash resume without a user-written checkpoint loop | ✗ |
| **Anthropic context-management API** [anthropic2026cookbook; anthropic2026context] | `compact_20260112` (Jan 2026), `clear_tool_uses_20250919`, `memory_20250818` (GA) | Model-platform primitive (below all frameworks) | ✗ | Typed `compaction` block in-conversation; memory files **outside** the window | ✗ | ~ the **sub-agent pattern is "MapReduce applied to agentic context management"** [tianpan2026context] — a *de facto* collective with no abstraction | ✗ | ✗ | ✓ **the state of the art**: compaction (auto at threshold, min 50K), lossless tool-result clearing (default 100K), file-based memory, sub-agent window isolation |
| **MPI (reference point)** [mpi41; bland2013ulfm] | MPI-4.1 (2023) | Message-passing standard | **Communicators + ranks** (named, enumerable groups) | Explicit buffers; RMA windows with defined sync epochs | ✓ | ✓ (bcast/reduce/allgather/scatter/scan, user-defined ops) | ✓ (barrier, RMA fence/lock, `MPI_Op`) | ~ historically weak; **ULFM** adds agreement/revoke/shrink | n/a |

**Row count: 26** — 23 surveyed systems, plus 2 unverified placeholder rows (AITP, Agora) retained so
reviewers can see they were checked and not silently dropped, plus MPI as the reference point.

**Reading the table.** The ✓ column entries cluster in exactly two places: **fault tolerance** (and
almost always *borrowed* from Temporal/DBOS/Prefect, or scoped to one graph) and **context
management** (Anthropic's platform primitives, plus the OpenAI SDK's compaction session). The
**Groups / Collectives / Sync** columns contain no unqualified ✓ in any row except MPI. That is the
layering claim, in one table.

---

## Gap analysis

Each gap is stated as a capability, contrasted with the **closest existing system by name**, so
reviewers can check that we are not attacking straw men.

**G1. A named, enumerable communicator with rank identity.**
*Closest existing:* **AutoGen v0.4+ topics** (named many-to-many addressing) [autogen2025v04], with
**CrewAI's Crew** as a named roster [crewai2026repo] and **AGNTCY's Agent Directory** as a name
service [a2asurvey2026]. A topic gives you a mailbox name; a Crew gives you a list of roles; a
directory gives you a lookup. None gives you a *group object* whose membership is enumerable,
ordered, versioned by epoch, and identical from every member's point of view — the property that
makes `MPI_Comm_rank` meaningful and makes collectives definable at all. A2A, the actual inter-agent
standard, has no group concept whatsoever; an agent is a URL [a2a2026site].

**G2. A collective algebra over agent groups.**
*Closest existing:* **LangGraph's `Annotated` reducers** on fan-in channels [langgraph2026prod] and
**LlamaIndex's `ctx.collect_events` / `list[A]` gather** [llamaindex2026workflows]; **ADK** names
"fan-out/fan-in" as a graph primitive [adk2026repo]. These are the real state of the art and they are
genuinely close in *spirit* — a reducer is a combining operator. But they are applied by a single
in-process scheduler at a *statically declared join point* in one graph. There is no
broadcast, no allgather, no scatter, no scan; no typed user-defined reduction over a *dynamic* group;
no completion semantics defined independently of one orchestrator's control flow. Meanwhile the
pattern everyone actually runs — Anthropic's sub-agent fan-out — is described in the literature as
"the **MapReduce pattern** applied to agentic context management" [tianpan2026context] and is
hand-rolled in every harness. AgentMPI's claim is that this deserves an interface, not a recipe.

**G3. Synchronisation primitives with agent-visible semantics.**
*Closest existing:* **Microsoft Agent Framework's BSP superstep barrier** — a real barrier with a real
guarantee ("no race conditions between supersteps; each sees a consistent view of messages")
[maf2026workflows]. This is the strongest counter-example in the survey and we cite it as such. Its
limits are the gap: the barrier is implicit, global to one graph, unschedulable, and **not scopable to
a subgroup** — Microsoft's own guidance for wanting independent parallel paths is to *manually merge
steps into one executor to escape the barrier* [maf2026workflows]. No system offers `barrier(group)`,
and locks are universally delegated: LangGraph's production guidance is **use Redis for per-thread
locks** [langgraph2026prod].

**G4. A specified consistency model for state shared between agents.**
*Closest existing:* **Google ADK's commit-on-yield `state_delta` protocol** — state is guaranteed
persisted only after the `Event` carrying the delta is yielded and processed by the Runner, and the
agent then reliably reads committed writes [adk2026eventloop]. That is a genuine (single-writer,
read-your-committed-writes) model and the best in the field. What is missing: multi-writer semantics,
conflict resolution, epochs, compare-and-swap, and any guarantee that holds when the serialising
authority is not a single Runner. **LangGraph internally tracks channel versions and which versions
each node last saw** [langgraph2026blog] — the machinery exists — but it is not exposed as a
programming model. And **A2A refuses the problem on principle**: its headline v1.0 achievement is
agents coordinating "**without sharing internal memory**" [lf2026a2apress].

**G5. A failure model for partial failure of a group.**
*Closest existing:* **Temporal** [temporal2026multiagent] and **Ray's actor supervision**
[ray2026agentarch]. Temporal survives worker death for a *workflow*; Ray restarts a *actor* under
policy. Neither answers the group question: no failure detector exposing "peer *k* is suspected" to
survivors, no membership-change event, no defined outcome for a collective when a participant dies
mid-operation, no agreement primitive. This is precisely the territory of MPI's ULFM proposal
(revoke / shrink / agree) [bland2013ulfm], and it is entirely unaddressed in the LLM-agent
ecosystem.

**G6. Durability that spans a group rather than one orchestration.**
*Closest existing:* **LangGraph checkpointers** [langgraph2026prod], and the honest assessment comes
from a competitor: "LangGraph's durability, checkpointer and all, is **framework-local**: it persists
the graph. Temporal is general-purpose Durable Execution: it persists the whole system"
[temporal2026multiagent]. Temporal is the better answer and still persists *executions*, keyed by
workflow ID, with no group as a unit of recovery. Nothing checkpoints "communicator *C* at epoch 7"
such that all members resume mutually consistently.

**G7. Specified delivery and idempotency semantics for agent messages.**
*Closest existing:* **LlamaIndex Workflows**, which states its guarantee plainly — "**Resume is
at-least-once, and step side effects need to be safe to repeat**," with in-flight steps rewound and
re-run from the top [llamaindex2026durable] — and **Pydantic AI**, which documents that guarantees are
*engine-differentiated* and concludes that "a runtime abstraction should make an engine **state its
migration story explicitly** rather than inherit an implicit 'it replays' assumption"
[pydanticai2026runtimecap]. This is the field's own admission that message semantics are unspecified
at the agent layer and leak from whichever backend you chose. MPI, by contrast, specifies ordering,
buffering, completion and matching.

**G8. Context as a first-class, group-allocated resource.**
*Closest existing:* **Anthropic's context-management primitives** — `compact_20260112`,
`clear_tool_uses_20250919`, `memory_20250818` [anthropic2026cookbook; anthropic2026ctxeng]. These are
excellent and AgentMPI should not try to beat them at compaction. The gap is *coordination*: every
mechanism manages exactly one window; when a lead compacts, no peer is informed; there is no protocol
for what a group collectively remembers; and nothing treats context as a reservable budget across
ranks, despite practitioners explicitly asking for "token budget tracking [on] every LLM call from day
one" [tianpan2026context] and "hard ceilings [that] beat heuristics" [callsphere2026actor].

**G9. Subgroup formation / communicator splitting.**
*Closest existing:* **Google ADK's scoped state prefixes (`app:` / `user:` / `temp:`) and event
`branch` isolation** [adk2026stateevents] — real namespacing, and the best raw material in the field.
But scoping state is not scoping *communication*: there is no `Comm_split`, no way to derive a
sub-group that has its own collectives and its own barrier. MAF's inability to scope its barrier to a
subgroup [maf2026workflows] is the same gap seen from the synchronisation side.

**G10. Dynamic membership: join, leave, spawn.**
*Closest existing:* **ADK's dynamic nodes** (`await ctx.run_node(...)`, requiring
`rerun_on_resume=True`) [adk2026stateevents] and **Ray's detached actors** [ray2026agentarch]. Both
let you create an executor at runtime. Neither integrates creation with a membership view: a newly
spawned agent does not *join a group* in any sense that affects collective operations or barriers, and
nothing analogous to `MPI_Comm_spawn`'s intercommunicator exists.

**G11. Portability of coordination semantics across harnesses.**
*Closest existing:* **Temporal used as a cross-framework layer** — the documented case of running "the
same multi-agent fleet on Google ADK, on LangGraph, and on both at once, with Temporal as a layer
underneath," precisely because "organizations rarely choose one framework cleanly"
[temporal2026multiagent]; and **Pydantic AI's pluggable durability capabilities**
[pydanticai2026pr4977]. This validates the *need* for a harness-independent layer, and shows the
industry currently satisfies it with a durability substrate that has no group semantics. Note also the
churn this survey documents — MCP wire-incompatible at 2026-07-28, A2A v0.x→v1.0 field renames, ADK
1.x→2.0 breaking changes, AutoGen→MAF, ACP→A2A — which is itself an argument for a stable, narrow
coordination interface.

**G12. A performance/semantics vocabulary for agent communication.**
*Closest existing:* nothing. MPI's value to HPC was partly that it made communication *costed and
discussable* (message size, collective algorithm, synchronisation cost). In the agent world the
equivalent costs are tokens, latency and context pressure, and the literature discusses them only as
folklore: a multi-agent loop "can quietly burn 10x the tokens of a single-LLM design"
[callsphere2026actor]; CrewAI's hierarchical process costs roughly 30–60% more tokens than sequential
[crewai2026techref]. A protocol that names the operations is a precondition for reasoning about their
cost.

### Honest assessment: what existing systems already do better than AgentMPI would

It would be a mistake for the paper to present AgentMPI as strictly dominating this landscape. Several
things these systems do are genuinely hard, genuinely valuable, and not things a message-passing
protocol would provide or should try to.

**Tool ecosystems and network effects.** MCP's actual achievement is not its protocol design — which
is a thin JSON-RPC layer that got *thinner* in 2026 — but that thousands of servers exist and every
major host speaks it [mcp2026spec; mcpblog2026]. A2A has 150+ supporting organisations and native
integration in Azure AI Foundry, Amazon Bedrock AgentCore and Google Cloud [lf2026a2apress]. AgentMPI
will have zero of either on day one, and it should be explicitly designed to *compose* with them (a
communicator over Agent Cards; MCP for tools) rather than to replace them. **Streaming and interactive
UX.** SSE streaming with incremental artifacts (A2A), AG-UI's bi-directional frontend state sync, and
Zed ACP's editor-hosted permission gates deliver user-facing behaviour that MPI's bulk-synchronous
lineage is actively bad at [a2asurvey2026]. **Suspend-and-resume for human-in-the-loop.** LangChain's
argument here is correct and MPI has no answer: keeping a process alive while waiting "scales neither
in time nor in volume," so real interruption via checkpointing is the only approach that supports
thousands of agents parked for days [langgraph2026blog]; Temporal's parked workflows burn **zero
compute** while waiting and get an audit trail for free [temporal2026multiagent]. A rank blocked in a
barrier for three days is an absurdity. **Hosted infrastructure and operational maturity.** Managed
deployment, tracing, evaluation, OpenTelemetry integration, per-activity retry policy tuning, and
enterprise auth (OAuth 2.0, signed Agent Cards, CIMD) represent years of work AgentMPI does not
replicate [maf2026v1; mcpblog2026; lf2026a2apress]. **Determinism and replay.** MAF's BSP determinism
("given the same input, the workflow always executes in the same order") [maf2026workflows] and
Temporal's replay-based recovery are stronger debuggability properties than MPI-style asynchronous
message passing typically offers — and AgentMPI's added expressiveness (dynamic groups, asynchronous
collectives) will make determinism *harder*, which the paper should acknowledge as a real cost rather
than a footnote. **Context engineering.** Anthropic's compaction/clearing/memory primitives are more
sophisticated than anything a coordination protocol would invent
[anthropic2026cookbook; anthropic2026context]; AgentMPI's contribution should be to make context
budgets *group-aware*, not to manage windows itself. **Model and provider portability.** LiteLLM-backed
provider abstraction (DSPy `dspy.LM` over 100+ providers [dspy2026hivebook], the OpenAI SDK's
`litellm` extra [openai2026config], MAF's connector layer [maf2026guide]) is table stakes AgentMPI
inherits rather than provides. **Finally, the honest architectural concession:** for the large majority
of deployments the layering critique does not bite, because most "multi-agent" systems are a manager
agent calling sub-agents as tools in one process [smolagents2026guidedtour] — where a single
orchestrator legitimately *is* the right consistency authority and a graph runtime legitimately *is*
the right barrier. AgentMPI's case must rest on the regime where that breaks down: many independently
scheduled, long-lived, cross-process (possibly cross-organisation) agents that must share state and
synchronise, and where the practitioner literature already shows people rebuilding "the same
reliability primitives from scratch" [temporal2026deepdive].

---

## BibTeX

Notes on provenance, so the citations can be graded:
**Primary / vendor-authoritative:** `mcp2026spec`, `mcpblog2026`, `mcpts2026migration`,
`cloudflare2026mcp`, `a2a2026site`, `lf2026a2apress`, `lfaidata2025acpmerge`, `ibm2026acp`,
`gcp2026agentregistry`, `maf2026v1`, `maf2026workflows`, `langgraph2026repo`, `langgraph2026pypi`,
`langgraph2026blog`, `crewai2026repo`, `crewai2026prodarch`, `openai2026sessions`,
`openai2026sqlalchemy`, `adk2026repo`, `adk2026eventloop`, `adk2026datahandling`,
`adk2026stateevents`, `llamaindex2026pypi`, `llamaindex2026workflows`, `llamaindex2026state`,
`llamaindex2026durable`, `llamaindex2026checkpoint`, `pydanticai2026repo`, `pydanticai2026site`,
`pydanticai2026pr4977`, `pydanticai2026runtimecap`, `pydanticai2026prefect`,
`smolagents2026guidedtour`, `smolagents2026multiagents`, `anthropic2026context`,
`anthropic2026cookbook`, `temporal2026multiagent`, `openai2026config` (DeepWiki-rendered from the
repo's `pyproject.toml`).
**Secondary / community aggregators — verify before relying on any *number* from these:**
`a2asurvey2026`, `techahead2026protocols`, `a2acard2026`, `maf2026guide`, `maf2026prod`,
`langgraph2026prod`, `langgraph2026guide`, `crewai2026techref`, `crewai2026flowscrews`,
`crewai2026flowsguide`, `adk2026migration`, `dspy2026hivebook`, `ray2026agentarch`,
`callsphere2026actor`, `anthropic2026ctxeng`, `dreaming2026levers`, `tianpan2026context`,
`temporal2026deepdive`.
**Not verified in this survey (placeholders retained for the record, cited only as background):**
`mcp2024announce`, `mcp2024spec`, `autogen2025v04`, `mpi41`, `bland2013ulfm` — these are
well-established references I have cited from prior knowledge rather than re-verified here; check
before submission. AITP and Agora have **no entries** because I could not verify them.

```bibtex
@misc{mcp2026spec,
  title        = {Model Context Protocol Specification, revision 2026-07-28},
  author       = {{Model Context Protocol Contributors}},
  year         = {2026},
  month        = jul,
  note         = {Stable release of the 2026-07-28 protocol revision; wire-incompatible with prior revisions},
  url          = {https://modelcontextprotocol.io/specification/2026-07-28},
  urldate      = {2026-08-30}
}

@misc{mcpblog2026,
  title        = {The 2026-07-28 Specification},
  author       = {{Model Context Protocol Maintainers}},
  howpublished = {Model Context Protocol Blog},
  year         = {2026},
  month        = jul,
  day          = {28},
  note         = {Announces stateless transport (SEP-2575, SEP-2567), \texttt{Mcp-Method}/\texttt{Mcp-Name} headers (SEP-2243), CIMD, MRTR, \texttt{subscriptions/listen}, tasks extension (SEP-2663), and deprecation of Roots, Sampling, Logging and HTTP+SSE (SEP-2577)},
  url          = {https://blog.modelcontextprotocol.io/posts/2026-07-28/},
  urldate      = {2026-08-30}
}

@misc{mcpts2026migration,
  title        = {Supporting Protocol Revision 2026-07-28},
  author       = {{MCP TypeScript SDK Maintainers}},
  year         = {2026},
  note         = {Migration guide; documents \texttt{requestState} replacing per-session state, per-era wire codecs, and the 2025-era vs 2026-era behaviour matrix},
  url          = {https://ts.sdk.modelcontextprotocol.io/v2/migration/support-2026-07-28},
  urldate      = {2026-08-30}
}

@misc{cloudflare2026mcp,
  title        = {The Next Generation of {MCP}},
  author       = {{Cloudflare}},
  howpublished = {Cloudflare Blog},
  year         = {2026},
  note         = {MCP 2026-07-28 as a fully stateless protocol; formal Active/Deprecated/Removed feature lifecycle with a 12-month minimum deprecation window; recommends Durable Objects when coordinated state is actually required},
  url          = {https://blog.cloudflare.com/mcp-v2/},
  urldate      = {2026-08-30}
}

@misc{a2a2026site,
  title        = {A2A Protocol: An Open Protocol Enabling Communication and Interoperability Between Opaque Agentic Applications},
  author       = {{A2A Project, Linux Foundation}},
  year         = {2026},
  note         = {Current specification site. Technical Steering Committee includes AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP and ServiceNow; Apache-2.0; states the MCP/A2A division of labour},
  url          = {https://a2a-protocol.org/latest/},
  urldate      = {2026-08-30}
}

@misc{lf2026a2apress,
  title        = {A2A Protocol Surpasses 150 Organizations, Lands in Major Cloud Platforms, and Sees Enterprise Production Use in First Year},
  author       = {{The Linux Foundation}},
  year         = {2026},
  month        = apr,
  day          = {9},
  note         = {v1.0 introduced multi-protocol support, enterprise multi-tenancy, modernized security flows, Signed Agent Cards, and a web-aligned architecture; agents coordinate ``without sharing internal memory''},
  url          = {https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year},
  urldate      = {2026-08-30}
}

@misc{a2acard2026,
  title        = {A2A Agent Card Schema Reference (v1.0)},
  author       = {{AgentCard.net}},
  year         = {2026},
  note         = {Secondary reference. Documents \texttt{/.well-known/agent-card.json}, \texttt{application/a2a+json}, and the v0.x-to-v1.0 field migration (\texttt{url} to \texttt{supportedInterfaces[0].url}; per-interface \texttt{protocolVersion}; removal of \texttt{capabilities.stateTransitionHistory})},
  url          = {https://www.agentcard.net/agent-card-schema},
  urldate      = {2026-08-30}
}

@misc{gcp2026agentregistry,
  title        = {JSON Schemas --- Agent Registry},
  author       = {{Google Cloud}},
  year         = {2026},
  note         = {Agent Registry supports A2A Agent Card versions 0.3 and 1.0; v1.0 declares \texttt{supportedInterfaces} with \texttt{protocolBinding} of \texttt{HTTP+JSON}, \texttt{JSONRPC} or \texttt{GRPC}; 10\,KB maximum card size},
  url          = {https://docs.cloud.google.cn/agent-registry/json-schemas},
  urldate      = {2026-08-30}
}

@misc{lfaidata2025acpmerge,
  title        = {{ACP} Joins Forces with {A2A} Under the Linux Foundation's {LF AI \& Data}},
  author       = {{LF AI \& Data Foundation}},
  year         = {2025},
  month        = aug,
  day          = {29},
  note         = {ACP merges into A2A; ACP team winds down active development; Kate Blair (IBM Research) joins the A2A Technical Steering Committee; BeeAI Platform moves to A2A via \texttt{A2AServer}/\texttt{A2AAgent} adapters},
  url          = {https://lfaidata.foundation/communityblog/2025/08/29/acp-joins-forces-with-a2a-under-the-linux-foundations-lf-ai-data/},
  urldate      = {2026-08-30}
}

@misc{ibm2026acp,
  title        = {Agent Communication Protocol ({ACP})},
  author       = {{IBM Research}},
  year         = {2026},
  note         = {Project page now headed ``ACP is now part of A2A under the Linux Foundation!'' with a migration guide; the \texttt{i-am-bee/acp} repository is archived},
  url          = {https://research.ibm.com/projects/agent-communication-protocol},
  urldate      = {2026-08-30}
}

@misc{a2asurvey2026,
  title        = {Agent Coordination Protocols: A Comparison of 10 Agent Coordination Protocols, Layers, and Frameworks},
  author       = {Walker, Ry},
  year         = {2026},
  month        = jun,
  note         = {Secondary aggregator (AI-assisted; first published February 2026, updated 11 June 2026). Source for the layered stack model (AG-UI / Zed ACP / A2A / MCP / AGNTCY), AutoGen's maintenance-mode status, AGNTCY archiving its own Agent Connect Protocol, and status of ANP and Summoner. GitHub metrics and adoption figures are point-in-time and should be independently checked},
  url          = {https://rywalker.com/research/agent-coordination-protocols},
  urldate      = {2026-08-30}
}

@misc{techahead2026protocols,
  title        = {{MCP} vs {A2A} vs {ACP}: AI Agent Protocols Explained in 2026},
  author       = {{TechAhead}},
  year         = {2026},
  note         = {Secondary. Corroborates the August 2025 ACP-into-A2A merger and that ACP's task persistence, async resumption and webhook patterns are native to A2A v1.0},
  url          = {https://www.techaheadcorp.com/blog/mcp-vs-a2a-vs-acp-ai-agent-interoperability-standards/},
  urldate      = {2026-08-30}
}

@misc{maf2026v1,
  title        = {Microsoft Agent Framework Version 1.0},
  author       = {{Microsoft}},
  howpublished = {Microsoft Agent Framework Dev Blog},
  year         = {2026},
  month        = apr,
  note         = {1.0 GA for .NET and Python (3 April 2026); unifies Semantic Kernel's foundations with AutoGen's orchestrations; stable Agent Workflows with checkpointing and hydration; sequential, concurrent, handoff, group chat and Magentic-One patterns; Semantic Kernel and AutoGen migration assistants},
  url          = {https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/},
  urldate      = {2026-08-30}
}

@misc{maf2026workflows,
  title        = {Microsoft Agent Framework Workflows: Workflow Builder and Execution},
  author       = {{Microsoft}},
  howpublished = {Microsoft Learn},
  year         = {2026},
  note         = {Modified Pregel / Bulk Synchronous Parallel execution model; superstep processing with a synchronization barrier; guarantees of deterministic execution, reliable checkpointing at superstep boundaries, and a consistent per-superstep view of messages; guidance to consolidate steps into one executor for truly independent parallel paths},
  url          = {https://learn.microsoft.com/en-us/agent-framework/workflows/workflows},
  urldate      = {2026-08-30}
}

@misc{maf2026guide,
  title        = {Microsoft Agent Framework 1.0: .NET and Python (2026)},
  author       = {{Digital Applied}},
  year         = {2026},
  note         = {Secondary. Five-layer architecture (connectors, kernel, agents, orchestration, MCP/A2A interop layer); Semantic Kernel retained as the foundation layer},
  url          = {https://www.digitalapplied.com/blog/microsoft-agent-framework-1-0-dotnet-python-guide},
  urldate      = {2026-08-30}
}

@misc{maf2026prod,
  title        = {Microsoft Agent Framework 1.0: Building and Deploying Multi-Agent Workflows in Production},
  author       = {{NiteAgent}},
  year         = {2026},
  note         = {Secondary. Consolidation of Semantic Kernel (approx.\ 28K stars) and AutoGen (approx.\ 42K stars); \texttt{SequentialBuilder}/\texttt{GroupChat}/handoff mapping from AutoGen; the workflow engine replaces AutoGen's \texttt{GroupChatManager} with explicit graph-based control flow; \texttt{Kernel} replaced by \texttt{Agent} as the primary abstraction},
  url          = {https://niteagent.com/blog/microsoft-agent-framework-v1-production-guide/},
  urldate      = {2026-08-30}
}

@misc{autogen2025v04,
  title        = {{AutoGen} v0.4: A Ground-Up Rewrite onto an Asynchronous, Event-Driven Actor Model},
  author       = {{Microsoft AutoGen Team}},
  year         = {2025},
  note         = {UNVERIFIED IN THIS SURVEY --- cited from prior knowledge for the layered \texttt{autogen-core}/\texttt{autogen-agentchat} architecture, actor mailboxes and topic-based publish/subscribe. Verify against the AutoGen 0.4 release notes before submission. AutoGen is now in maintenance mode; see \texttt{a2asurvey2026} and \texttt{maf2026v1}},
  url          = {https://github.com/microsoft/autogen},
  urldate      = {2026-08-30}
}

@misc{langgraph2026repo,
  title        = {{LangGraph}: Low-Level Orchestration Framework for Building Stateful Agents},
  author       = {{LangChain}},
  year         = {2026},
  note         = {Project README. Durable execution, human-in-the-loop, and comprehensive memory (short-term working memory plus long-term persistence across sessions); production users include Klarna, Replit and Elastic},
  url          = {https://github.com/langchain-ai/langgraph},
  urldate      = {2026-08-30}
}

@misc{langgraph2026pypi,
  title        = {\texttt{langgraph} v1.0.5 (Python Package Index)},
  author       = {{LangChain}},
  year         = {2026},
  note         = {Observed release in the 1.0.x line as of August 2026. A secondary aggregator reports v1.2.4; the discrepancy is unresolved and flagged UNVERIFIED in the survey},
  url          = {https://pypi.org/project/langgraph/1.0.5/},
  urldate      = {2026-08-30}
}

@misc{langgraph2026blog,
  title        = {Building {LangGraph}: Designing an Agent Runtime from First Principles},
  author       = {{LangChain}},
  year         = {2026},
  note         = {Design rationale: parallelisation, streaming, task queue and checkpointing as distinct levers; checkpoints resumable on any machine, recording serialised channel values (MsgPack, optionally encrypted), version strings, and which channel versions each node last saw; argues real interruption via checkpointing is the only approach that scales in time and volume for human-in-the-loop},
  url          = {https://www.langchain.com/blog/building-langgraph},
  urldate      = {2026-08-30}
}

@misc{langgraph2026prod,
  title        = {Deploy {LangGraph} to Production: 2026 {Postgres} Tutorial},
  author       = {{Rapid Claw}},
  year         = {2026},
  note         = {Secondary practitioner guide. \texttt{PostgresSaver} for crash recovery, multi-worker coordination and point-in-time replay; recommends Redis for per-thread locks and per-tool rate limiting, i.e.\ synchronisation is delegated outside the framework},
  url          = {https://rapidclaw.dev/blog/deploy-langgraph-production-tutorial-2026},
  urldate      = {2026-08-30}
}

@misc{langgraph2026guide,
  title        = {{LangGraph} Tutorial 2026: Build Stateful AI Agents for Enterprise},
  author       = {{Alice Labs}},
  year         = {2026},
  note         = {Secondary. Checkpointer tiers (SqliteSaver for development, PostgresSaver for production); \texttt{thread\_id}-keyed independent state history; \texttt{interrupt\_before}/\texttt{interrupt\_after} with indefinitely persisted paused state},
  url          = {https://alicelabs.ai/en/insights/langgraph-guide-2026},
  urldate      = {2026-08-30}
}

@misc{crewai2026repo,
  title        = {{CrewAI}: Framework for Orchestrating Role-Playing, Autonomous AI Agents},
  author       = {{CrewAI Inc.}},
  year         = {2026},
  note         = {Project README. Crews (autonomous, role-based collaboration) versus Flows (event-driven control with secure, consistent state management); hierarchical process auto-assigns a manager agent; agent capabilities include tools, memory, knowledge, checkpointing, async execution and MCP/A2A support},
  url          = {https://github.com/crewaiinc/crewai},
  urldate      = {2026-08-30}
}

@misc{crewai2026prodarch,
  title        = {Production Architecture --- {CrewAI} Documentation (v1.12.0)},
  author       = {{CrewAI Inc.}},
  year         = {2026},
  note         = {Recommends starting with a Flow in production; Pydantic state schemas; the \texttt{@persist} decorator to save Flow state to a database so execution can resume after a crash or while awaiting human input},
  url          = {https://docs.crewai.com/v1.12.0/en/concepts/production-architecture},
  urldate      = {2026-08-30}
}

@misc{crewai2026flowscrews,
  title        = {{CrewAI} Flows vs Crews: When to Let Agents Decide and When to Script Them},
  author       = {{dreaming.press}},
  year         = {2026},
  note         = {Secondary. A Crew is stateless by default; a Flow carries typed Pydantic state on \texttt{self.state} with \texttt{@start()}/\texttt{@listen()}/\texttt{@router()}, \texttt{or\_()}/\texttt{and\_()} join conditions, and \texttt{@persist} for durability across runs},
  url          = {https://dreaming.press/posts/crewai-flows-vs-crews.html},
  urldate      = {2026-08-30}
}

@misc{crewai2026flowsguide,
  title        = {{CrewAI} Flows: Production Multi-Agent Guide 2026},
  author       = {{Jahanzaib}},
  year         = {2026},
  note         = {Secondary. Crew-versus-Flow comparison across state, branching, error recovery and human-in-the-loop; CrewAI rebuilt its memory system in 2025, replacing separate short-term, long-term and entity memory with a unified \texttt{Memory} class; \texttt{@human\_feedback} is a Flow-level, not Crew-level, primitive},
  url          = {https://www.jahanzaib.ai/blog/crewai-flows-production-multi-agent-guide},
  urldate      = {2026-08-30}
}

@misc{crewai2026techref,
  title        = {{CrewAI}: A Comprehensive Technical Reference},
  author       = {{Solutions Architecture}},
  year         = {2026},
  note         = {Secondary. Sequential versus hierarchical process semantics; hierarchical reported at roughly 30--60\% higher token cost (figure reported second-hand, UNVERIFIED); production deployments frequently replace the default memory subsystem because it is ``a black box from an audit perspective''},
  url          = {https://solutionsarchitecture.medium.com/crewai-a-comprehensive-technical-reference-d32d31923ea0},
  urldate      = {2026-08-30}
}

@misc{openai2026sessions,
  title        = {Sessions --- {OpenAI} Agents {SDK} Documentation},
  author       = {{OpenAI}},
  year         = {2026},
  note         = {Enumerates session backends: SQLite, AsyncSQLite, Redis (``shared memory across workers/services''), SQLAlchemy, MongoDB (atomic sequence counter for ordering), Dapr (multiple state stores plus TTL and consistency controls), OpenAIConversations, OpenAIResponsesCompaction (automatic compaction), AdvancedSQLite (branching/analytics) and Encrypted; warns against combining sessions with server-managed \texttt{conversation\_id}/\texttt{previous\_response\_id} in one run},
  url          = {https://openai.github.io/openai-agents-python/sessions/},
  urldate      = {2026-08-30}
}

@misc{openai2026sqlalchemy,
  title        = {\texttt{SQLAlchemySession} --- {OpenAI} Agents {SDK} Reference},
  author       = {{OpenAI}},
  year         = {2026},
  note         = {Implementation detail cited as evidence that the SDK manages database-level locking rather than offering agent-level locks: class-level \texttt{threading.Lock} table-init guards, SQLite \texttt{busy\_timeout} and WAL mode, and a lock-retry delay schedule},
  url          = {https://openai.github.io/openai-agents-python/ref/extensions/memory/sqlalchemy_session/},
  urldate      = {2026-08-30}
}

@misc{openai2026config,
  title        = {Configuration and Setup --- \texttt{openai/openai-agents-python}},
  author       = {{OpenAI}},
  howpublished = {DeepWiki rendering of the repository's \texttt{pyproject.toml}},
  year         = {2026},
  note         = {Optional dependency groups: \texttt{voice}, \texttt{litellm} (100+ providers), \texttt{sqlalchemy} (>=2.0), \texttt{redis} (>=7), \texttt{temporal} (\texttt{temporalio==1.26.0}, for durable execution and long-running workflows), and \texttt{sandbox-backends} (docker, e2b, modal, runloop, vercel)},
  url          = {https://deepwiki.com/openai/openai-agents-python/12-configuration-and-setup},
  urldate      = {2026-08-30}
}

@misc{adk2026repo,
  title        = {Agent Development Kit ({ADK}) 2.0},
  author       = {{Google}},
  year         = {2026},
  note         = {Project README. Breaking changes to the agent API, event model and session schema; ADK 2.0 sessions readable by ADK 1.28+ but incompatible with older 1.x; Workflow Runtime supports routing, fan-out/fan-in, loops, retry, state management, dynamic nodes, human-in-the-loop and nested workflows; Task API provides structured agent-to-agent delegation with task agents as workflow nodes},
  url          = {https://github.com/google/adk-python},
  urldate      = {2026-08-30}
}

@misc{adk2026eventloop,
  title        = {Runtime and Event Loop --- {ADK} Documentation},
  author       = {{Google}},
  year         = {2026},
  note         = {The Runner commits \texttt{state\_delta} and \texttt{artifact\_delta} from \texttt{event.actions} via \texttt{SessionService}, \texttt{ArtifactService} and \texttt{MemoryService}; state writes are guaranteed persisted only after the carrying Event is yielded and processed, after which the agent reliably reads the committed state},
  url          = {https://github.com/google/adk-docs/blob/main/docs/runtime/event-loop.md},
  urldate      = {2026-08-30}
}

@misc{adk2026datahandling,
  title        = {Workflows: Data Handling --- {ADK} Documentation},
  author       = {{Google}},
  year         = {2026},
  note         = {Nodes consume and emit Events; the \texttt{output}, \texttt{message} and \texttt{state} parameters are distinguished; \texttt{state} automatically persists across nodes but ``should not be used to persist large amounts of data'' --- use artifacts or database tools instead},
  url          = {https://github.com/google/adk-docs/blob/main/docs/workflows/data-handling.md},
  urldate      = {2026-08-30}
}

@misc{adk2026stateevents,
  title        = {State and Events --- {ADK} Agent Builder Reference},
  author       = {{Google}},
  year         = {2026},
  note         = {\texttt{Context} exposes delta-aware \texttt{state}, \texttt{actions}, and an event-isolation \texttt{branch}; methods include \texttt{run\_node} for dynamic node execution (requiring \texttt{rerun\_on\_resume=True}), \texttt{save\_artifact}/\texttt{load\_artifact}, and \texttt{search\_memory}; state key prefixes \texttt{app:}, \texttt{user:} and \texttt{temp:} scope global, per-user and ephemeral values},
  url          = {https://github.com/google/adk-python/blob/main/.agents/skills/adk-agent-builder/references/state-and-events.md},
  urldate      = {2026-08-30}
}

@misc{adk2026migration,
  title        = {\texttt{google-adk} 2.0 Is Now Stable: Workflow Runtimes, Breaking Changes, and How to Migrate},
  author       = {Green, Peyton},
  howpublished = {DEV Community},
  year         = {2026},
  note         = {Secondary. Reports ADK 2.0.0 stable on 19 May 2026, Events as the primary data primitive, \texttt{@tool} decorators superseded by \texttt{@WorkflowNode} with event-routed invocation, and built-in A2A support. Migration specifics are UNVERIFIED against Google's own migration guide},
  url          = {https://dev.to/peytongreen_dev/google-adk-20-is-now-stable-workflow-runtimes-breaking-changes-and-how-to-migrate-4ah8},
  urldate      = {2026-08-30}
}

@misc{llamaindex2026pypi,
  title        = {\texttt{llama-index-workflows} v2.23.3 (Python Package Index)},
  author       = {{LlamaIndex}},
  year         = {2026},
  note         = {Release history shows 1.0.0 on 25 June 2025 and 2.0.0 on 29 August 2025; async-first, event-driven architecture in which steps are async functions consuming events from an asyncio queue},
  url          = {https://pypi.org/project/llama-index-workflows/},
  urldate      = {2026-08-30}
}

@misc{llamaindex2026workflows,
  title        = {Workflows --- {LlamaIndex} Developer Documentation},
  author       = {{LlamaIndex}},
  year         = {2026},
  note         = {Decision table for concurrency idioms: return \texttt{list[A]} to scatter, accept \texttt{list[A]} to gather a full batch, \texttt{ctx.send\_event} to emit incrementally, \texttt{ctx.collect\_events} to wait for a known set of events, \texttt{ctx.store} for shared per-run state, and \texttt{Resource} for non-serialisable dependencies; the implied graph is statically validated from type signatures},
  url          = {https://developers.llamaindex.ai/python/llamaagents/workflows/index.md},
  urldate      = {2026-08-30}
}

@misc{llamaindex2026state,
  title        = {Managing State --- {LlamaIndex} Developer Documentation},
  author       = {{LlamaIndex}},
  year         = {2026},
  note         = {Each run's \texttt{Context} owns a state store, untyped by default or typed via a Pydantic model with defaults; supports atomic state updates; \texttt{run(ctx=ctx)} resumes a still-running run without a new \texttt{StartEvent}, or starts a fresh run reusing stored state},
  url          = {https://developers.llamaindex.ai/python/llamaagents/workflows/managing_state/},
  urldate      = {2026-08-30}
}

@misc{llamaindex2026durable,
  title        = {Writing Durable Workflows --- {LlamaIndex} Developer Documentation},
  author       = {{LlamaIndex}},
  year         = {2026},
  note         = {``There is no built-in checkpointer to enable''; snapshot on \texttt{StepStateChanged} with \texttt{StepState.NOT\_RUNNING} via \texttt{stream\_events(expose\_internal=True)} and \texttt{Context.to\_dict()}/\texttt{from\_dict()}; on resume, pending events are re-dispatched and partial fan-in buffers rebuilt, completed steps do not re-run, mid-execution steps are rewound and re-run from the top; resume is at-least-once and side effects must be safe to repeat; the DBOS runtime plugin journals step transitions instead},
  url          = {https://developers.llamaindex.ai/python/llamaagents/workflows/durable_workflows/},
  urldate      = {2026-08-30}
}

@misc{llamaindex2026checkpoint,
  title        = {Checkpointing Workflow Runs --- {LlamaIndex} Examples},
  author       = {{LlamaIndex}},
  year         = {2026},
  note         = {\texttt{WorkflowCheckpointer} wraps \texttt{Workflow.run()}, storing checkpoints per \texttt{run\_id} at step completion, with \texttt{filter\_checkpoints()}, \texttt{run\_from()} and per-step \texttt{enable\_checkpoint}/\texttt{disable\_checkpoint}},
  url          = {https://developers.llamaindex.ai/python/examples/workflow/checkpointing_workflows/},
  urldate      = {2026-08-30}
}

@misc{pydanticai2026repo,
  title        = {{Pydantic AI}: A Typed, Extensible Agent Loop},
  author       = {{Pydantic}},
  year         = {2026},
  note         = {Project README. First-party co-maintained durable execution on Temporal, DBOS and Prefect with Restate, Kitaru and Airflow integrations; under Temporal every model and tool call becomes a durable activity; built-in MCP support and human-in-the-loop tool approval},
  url          = {https://github.com/pydantic/pydantic-ai},
  urldate      = {2026-08-30}
}

@misc{pydanticai2026site,
  title        = {{Pydantic AI}: Production-Grade Applications with Generative AI},
  author       = {{Pydantic}},
  year         = {2026},
  note         = {Lists four natively supported durable execution solutions --- Temporal, DBOS, Prefect and Restate --- and notes the integrations use only Pydantic AI's public interface, so they serve as references for integrating other durable systems},
  url          = {https://pydantic.dev/pydantic-ai},
  urldate      = {2026-08-30}
}

@misc{pydanticai2026pr4977,
  title        = {Add \texttt{TemporalDurability}, \texttt{DBOSDurability} and \texttt{PrefectDurability} Capabilities to Replace the Deprecated Wrapper Agents},
  author       = {{Pydantic AI Contributors}},
  year         = {2026},
  howpublished = {GitHub pull request \#4977, \texttt{pydantic/pydantic-ai}},
  note         = {Durability becomes an attachable capability via \texttt{capabilities=[...]}; \texttt{TemporalAgent}/\texttt{DBOSAgent}/\texttt{PrefectAgent} deprecated with removal slated for v3; capabilities deliberately do not wrap the run --- the user's workflow or flow decides which runs are durable; DBOS defaults to \texttt{parallel\_execution\_mode='parallel\_ordered\_events'} to preserve deterministic replay, with \texttt{'parallel'} excluded by type},
  url          = {https://github.com/pydantic/pydantic-ai/pull/4977},
  urldate      = {2026-08-30}
}

@misc{pydanticai2026runtimecap,
  title        = {First-Class \texttt{RuntimeCapability} Extension Point for Durable Execution (post-v2)},
  author       = {{Pydantic AI Contributors}},
  year         = {2026},
  howpublished = {GitHub issue \#5477, \texttt{pydantic/pydantic-ai}},
  note         = {Documents engine-differentiated guarantees: Temporal rejects per-run capabilities and toolsets because activities must be worker-registered upfront; DBOS and Prefect reject runtime/override models; migration continuity differs per engine (Temporal replays transparently, DBOS needs opt-in legacy registration, Prefect re-executes live on flow retry); concludes a runtime abstraction should make each engine state its migration story explicitly},
  url          = {https://github.com/pydantic/pydantic-ai/issues/5477},
  urldate      = {2026-08-30}
}

@misc{pydanticai2026prefect,
  title        = {Durable Execution with {Prefect} --- {Pydantic AI} Documentation},
  author       = {{Pydantic}},
  year         = {2026},
  note         = {\texttt{PrefectDurability} routes model requests, tool calls and MCP communication through Prefect tasks when the agent runs inside a \texttt{@flow}; calling \texttt{agent.run()} outside a flow yields a run that works but is not durable; Prefect 3.0 provides built-in caching and transactional semantics making workflows naturally idempotent},
  url          = {https://pydantic.dev/docs/ai/capabilities/durable_execution/prefect/},
  urldate      = {2026-08-30}
}

@misc{smolagents2026guidedtour,
  title        = {Agents --- Guided Tour ({smolagents} Documentation)},
  author       = {{Hugging Face}},
  year         = {2026},
  note         = {\texttt{CodeAgent} (code-as-action) versus \texttt{ToolCallingAgent} (JSON tool calls); hierarchical multi-agent systems built by giving a sub-agent \texttt{name} and \texttt{description}, which are embedded in the manager's system prompt ``as we also do for tools'', then passing it via \texttt{managed\_agents}; credits AutoGen with introducing multi-agent systems; motivates decomposition by memory/context separation between specialised agents},
  url          = {https://huggingface.co/docs/smolagents/en/guided_tour},
  urldate      = {2026-08-30}
}

@misc{smolagents2026multiagents,
  title        = {Orchestrate a Multi-Agent System ({smolagents} Examples)},
  author       = {{Hugging Face}},
  year         = {2026},
  note         = {Worked example of a \texttt{CodeAgent} manager over a \texttt{ToolCallingAgent} web agent; \texttt{name} and \texttt{description} are mandatory for an agent to be callable by its manager; \texttt{max\_steps} bounds the loop and \texttt{additional\_authorized\_imports} gates code execution},
  url          = {https://huggingface.co/docs/smolagents/main/en/examples/multiagents},
  urldate      = {2026-08-30}
}

@misc{dspy2026hivebook,
  title        = {{DSPy}: Declarative Self-Improving Framework for Programming {LLMs}},
  author       = {{Hivebook}},
  year         = {2026},
  note         = {Secondary knowledge base (version details UNVERIFIED against PyPI/GitHub). Reports latest stable 3.2.1 (5 May 2026), v3.0.0 in August 2025 deprecating \texttt{dspy.Settings}/\texttt{dspy.OpenAI} in favour of LiteLLM-backed \texttt{dspy.LM}, MIT licence, Python 3.10--3.14, approx.\ 34.5K stars; \texttt{compile()} returns a new module leaving the original untouched and compiled programs serialise to JSON, so optimisation runs once at build time; notes AG2 (an AutoGen fork) agents can be replaced by DSPy-compiled programs},
  url          = {https://www.hivebook.wiki/wiki/dspy-declarative-self-improving-framework-for-programming-llms},
  urldate      = {2026-08-30}
}

@misc{ray2026agentarch,
  title        = {{Ray} Agent Architecture: Production Best Practices for Scalable Multi-Agent Systems (2026)},
  author       = {{Markaicode}},
  year         = {2026},
  note         = {Secondary practitioner guide. Each agent as a Ray actor; \texttt{actor\_lifetime="detached"} for persistent agents; \texttt{max\_restarts=-1} for unlimited restarts and \texttt{max\_consecutive\_restarts} to damp cascade failures; manual durability via \texttt{ray.cloudpickle}, Redis or periodic S3 checkpoints; use \texttt{ray.wait} rather than nested synchronous \texttt{ray.get} inside actors; placement groups to co-locate state with compute. The reported approx.\ 2.3\,s actor restart time is a single practitioner benchmark and UNVERIFIED},
  url          = {https://markaicode.com/architecture/ray-agent-architecture/},
  urldate      = {2026-08-30}
}

@misc{callsphere2026actor,
  title        = {The Actor Model for Multi-Agent Systems: {Ray}, {Akka} and Beyond (2026)},
  author       = {{Callsphere}},
  year         = {2026},
  note         = {Secondary. The 2026 pattern of each agent as a Ray actor with the orchestrator itself an actor holding handles to specialists, and the memory store implemented as an actor called via messages; actor mailboxes provide natural backpressure; supervision trees restart failed agents by policy; advises reaching for Ray only when heterogeneous compute or distributed scale exceeds higher-level frameworks; notes a multi-agent loop can burn 10x the tokens of a single-LLM design and that hard ceilings (max step count, idempotency keys) beat heuristics},
  url          = {https://callsphere.ai/blog/actor-model-multi-agent-systems-ray-akka-openai-swarm-2026.md},
  urldate      = {2026-08-30}
}

@misc{temporal2026multiagent,
  title        = {Durable, Flexible Multi-Agent Systems},
  author       = {{Temporal Technologies}},
  howpublished = {Temporal Blog},
  year         = {2026},
  note         = {Runs the same multi-agent fleet on Google ADK, on LangGraph, and on both at once with Temporal underneath; states that ``LangGraph's durability, checkpointer and all, is framework-local: it persists the graph. Temporal is general-purpose Durable Execution: it persists the whole system''; workflows park on waits burning zero compute with pending decisions in the Event History, so thousands can wait independently and the audit trail comes free},
  url          = {https://temporal.io/blog/durable-flexible-multi-agent-systems},
  urldate      = {2026-08-30}
}

@misc{temporal2026deepdive,
  title        = {{Temporal}'s Durable Execution Model for {AI} Agents},
  author       = {Md, Jawad},
  year         = {2026},
  note         = {Secondary practitioner deep dive. Motivating failures: a worker dying mid-workflow loses accumulated agent context, and a 429 on step 47 of 50 has no clean resumption path; observes teams repeatedly rebuilding checkpointing, retry logic, state persistence and crash recovery from scratch; covers the workflow/activity split enabling replay-based recovery; notes Temporal is unnecessary for agents finishing in 20 seconds and argues the industry underinvests in the reliability layer},
  url          = {https://www.linkedin.com/posts/mdjawad_aiagents-aiengineering-durableexecution-activity-7433255451129044992-L9Wv},
  urldate      = {2026-08-30}
}

@misc{anthropic2026context,
  title        = {Effective Context Engineering for {AI} Agents},
  author       = {{Anthropic}},
  year         = {2026},
  note         = {Frames the problem as context pollution and information relevance rather than window size; names compaction, structured note-taking (agentic memory, via the file-based memory tool released with Sonnet 4.5) and multi-agent/sub-agent architectures; sub-agents may consume tens of thousands of tokens but return condensed summaries of roughly 1,000--2,000 tokens},
  url          = {https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents},
  urldate      = {2026-08-30}
}

@misc{anthropic2026cookbook,
  title        = {Context Engineering: Memory, Compaction and Tool Clearing},
  author       = {{Anthropic}},
  howpublished = {Claude Cookbook},
  year         = {2026},
  note         = {\texttt{compact\_20260112} triggers automatically at a token threshold (minimum 50K), returns a typed \texttt{compaction} content block that slots natively into the conversation, handles tool-use pairing across the summary boundary, and causes the API to drop everything before the block on the next request; compaction compresses the whole window, clearing drops stale re-fetchable data inside it, and memory moves information out of the window to survive across sessions},
  url          = {https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools},
  urldate      = {2026-08-30}
}

@misc{anthropic2026ctxeng,
  title        = {Context Engineering the {Anthropic} Way: How {Claude}'s Skills, Compaction and Memory Tools Manage the Window},
  author       = {{dreaming.press}},
  year         = {2026},
  note         = {Secondary. Identifies the three API primitives \texttt{clear\_tool\_uses\_20250919} (clears old tool results past a default 100K-token threshold, keeping a placeholder; lossless for re-fetchable data), \texttt{compact\_20260112} (a January 2026 feature, default 150K-token trigger) and \texttt{memory\_20250818} (now GA); reports an Opus lead with Sonnet sub-agents beating a single agent by more than 90\% in Anthropic's research system --- figure quoted second-hand and UNVERIFIED},
  url          = {https://dreaming.press/posts/context-engineering-anthropic-way-claude-skills-compaction-memory.html},
  urldate      = {2026-08-30}
}

@misc{dreaming2026levers,
  title        = {How to Combine Context Editing, Compaction, Memory and Subagents in One {Claude} Agent {SDK} Loop},
  author       = {{dreaming.press}},
  year         = {2026},
  note         = {Secondary. Composition guidance ordering the four levers by the cost of what is lost: subagents keep bulk out of the orchestrator's window, context editing evicts re-fetchable tool results, the memory tool writes irreplaceable specifics outside the window before compaction can summarise them away, and compaction handles the remaining coherent thread},
  url          = {https://dreaming.press/posts/how-to-combine-context-editing-compaction-memory-subagents-agent-sdk.html},
  urldate      = {2026-08-30}
}

@misc{tianpan2026context,
  title        = {Context Engineering: Memory, Compaction and Tool Clearing for Production Agents},
  author       = {Pan, Tian},
  year         = {2026},
  month        = feb,
  note         = {Secondary. Recommends triggering compaction at roughly 70\% of the effective window rather than at exhaustion, because once context rot sets in the summarising model is already impaired; describes the sub-agent pattern as ``the MapReduce pattern applied to agentic context management''; advises token-budget tracking on every LLM call from day one},
  url          = {https://tianpan.co/blog/2026-02-26-context-engineering-memory-compaction-tool-clearing},
  urldate      = {2026-08-30}
}

@misc{mcp2024announce,
  title        = {Introducing the {Model Context Protocol}},
  author       = {{Anthropic}},
  year         = {2024},
  month        = nov,
  note        = {UNVERIFIED IN THIS SURVEY --- original announcement, cited from prior knowledge for the ``USB-C port for AI applications'' framing and the original client/server architecture. Verify the URL and date before submission; see \texttt{mcp2026spec} for the current revision},
  url          = {https://www.anthropic.com/news/model-context-protocol},
  urldate      = {2026-08-30}
}

@misc{mcp2024spec,
  title        = {Model Context Protocol Specification, revision 2024-11-05},
  author       = {{Anthropic}},
  year         = {2024},
  note         = {UNVERIFIED IN THIS SURVEY --- the original spec revision, cited only for historical contrast with the stateless 2026-07-28 revision. Verify before submission},
  url          = {https://modelcontextprotocol.io/specification/2024-11-05},
  urldate      = {2026-08-30}
}

@article{bland2013ulfm,
  author       = {Bland, Wesley and Bouteiller, Aurelien and Herault, Thomas and Bosilca, George and Dongarra, Jack},
  title        = {Post-Failure Recovery of {MPI} Communication Capability: Design and Rationale},
  journal      = {International Journal of High Performance Computing Applications},
  year         = {2013},
  note         = {UNVERIFIED IN THIS SURVEY --- cited from prior knowledge as the canonical User Level Failure Mitigation (ULFM) reference for revoke/shrink/agree semantics. Confirm the venue (IJHPCA vs.\ EuroMPI), volume, issue, pages and DOI before submission}
}

@misc{mpi41,
  title        = {{MPI}: A Message-Passing Interface Standard, Version 4.1},
  author       = {{Message Passing Interface Forum}},
  year         = {2023},
  note         = {UNVERIFIED IN THIS SURVEY --- cited from prior knowledge as the reference point for communicators, collectives, RMA windows and synchronisation epochs. Confirm the version and publication date against the MPI Forum before submission},
  url          = {https://www.mpi-forum.org/docs/},
  urldate      = {2026-08-30}
}
```

<!-- Sources not separately cited but consulted: A2A Agent Card specification walkthrough
     (stacka2a.dev/learn/agent-card-spec, secondary; corroborates message/send, message/stream,
     tasks/get and the skills/capabilities/securitySchemes field set) and the ACP status page at
     rywalker.com/research/acp-agent-communication-protocol (corroborates the archived repo, the
     August 2025 merger date, and the Zed ACP acronym collision). -->

