# 05a — Classical Agent Communication Languages, Coordination Protocols, and Platforms

*Related-work dossier for AgentMPI. Scope: pre-LLM agent communication languages (ACLs), coordination
protocols, blackboard architectures, tuple spaces, agent platforms, and coordination theory.*

**Framing for the paper.** AgentMPI's central philosophical claim is that it follows MPI's
*mechanism-level, semantics-thin* path: the protocol standardises **who can send what to whom, when a
transfer is complete, and how endpoints are named**, and says nothing about what the payload *means*.
The classical ACL tradition (KQML, FIPA-ACL) took the opposite path: it standardised the *illocutionary
force* of messages and grounded that force in a mentalistic (BDI) semantics of beliefs, desires and
intentions. This document assembles the primary sources, the message flows, and — critically — the
*documented critiques* of the semantics-heavy path, so that the related-work section can make the
comparison fairly rather than by caricature.

Conventions: inline citation markers like `[smith1980contractnet]` map to the `## BibTeX` block at the
end. Claims I could not confirm against a primary or reliable secondary source are marked
`[UNVERIFIED]`.

---

## 1. KQML — Knowledge Query and Manipulation Language

### 1.1 Origins and institutional context

KQML emerged from the **ARPA Knowledge Sharing Effort (KSE)**, specifically its *External Interfaces
Working Group*, in the early 1990s [kse1991; kqmlspec1993]. The KSE was an attempt to solve knowledge
*reuse*: it produced KIF (Knowledge Interchange Format) as a common logical content language, Ontolingua
for ontology definition, and KQML as the transport-and-speech-act envelope. This genealogy matters for
the AgentMPI argument: **KQML was not designed as a communication substrate for distributed programs; it
was designed as an interoperability layer for knowledge bases.** Its problem statement was "how can two
independently built expert systems exchange assertions and queries about a shared domain," not "how can
N processes exchange bytes with well-defined completion semantics." Every subsequent design decision
follows from that framing.

The canonical descriptions are Finin, Fritzson, McKay & McEntire, *KQML as an Agent Communication
Language* (CIKM 1994) [finin1994kqml], the *Specification of the KQML Agent-Communication Language*
working paper [kqmlspec1993], Labrou & Finin's revised proposal [labrou1997kqmlspec], and the retrospective
survey Labrou, Finin & Peng, *Agent Communication Languages: The Current Landscape* (IEEE Intelligent
Systems 1999) [labrou1999landscape].

### 1.2 The three layers

KQML is explicitly described as three layers [finin1994kqml; kqmloverview]:

| Layer | Carries | Notes |
|---|---|---|
| **Content layer** | The actual message payload, in *some other* language | KIF, KRSL, LOOM, Prolog, SQL — KQML is deliberately agnostic. Payload may be entirely opaque to the transport. |
| **Message layer** | The **performative** plus content-describing metadata: `:language`, `:ontology`, `:content` | This is the "speech act layer." It names the illocutionary force the sender attaches to the content. |
| **Communication layer** | Low-level parameters: `:sender`, `:receiver`, a unique message identifier (`:reply-with` / `:in-reply-to`) | Encodes the identity of the endpoints and the reply-linkage. |

The key architectural virtue — and the one AgentMPI should note approvingly — is **content opacity**:
because the content language and ontology are declared as *metadata* rather than being fixed by the
protocol, "KQML implementations [can] analyze, route and properly deliver messages even though their
content is inaccessible" [kqmlspec1993]. Routers and facilitators operate on the envelope alone. This is
structurally the same discipline MPI applies with opaque buffers plus a datatype descriptor: the runtime
needs enough type information to move and match, and no more.

Syntax is a balanced-parenthesis list, Lisp-derived: the first element is the performative, the rest are
keyword/value pairs. The canonical example:

```lisp
(ask-one
  :content (PRICE IBM ?price)
  :receiver stock-server
  :language LPROLOG
  :ontology NYSE-TICKS)
```

The KQML authors were themselves relaxed about the surface syntax: "Because the language is relatively
simple, the actual syntax is not significant and can be changed if necessary in the future"
[kqmlspec1993]. Notably, the wire encoding was *transport-dependent* — TCP-stream implementations put the
communication-layer fields in the message body, while an email-based implementation put them in mail
headers [kqmlspec1993]. **KQML never pinned down a single wire format.** Hold that thought for §1.5.

### 1.3 Performatives

KQML's designers described performatives as "the permissible actions (operations) that agents may attempt
in communicating with each other" [kqmlspec1993]. The performative has two jobs: (i) supply the speech
act the sender attaches to the content, and (ii) *identify the protocol* by which replies will be
delivered [finin1994kqml] — i.e. it is simultaneously a semantic annotation and a control-flow selector.
That conflation is a design smell worth naming in the related-work section.

Grouping the performatives of the revised specification [labrou1997kqmlspec]:

- **Assertion / basic informative:** `tell`, `untell`, `deny`, `insert`, `uninsert`, `delete-one`,
  `delete-all`, `undelete`
- **Query:** `evaluate`, `ask-if`, `ask-about`, `ask-one`, `ask-all`
- **Multi-response / streaming:** `stream-all`, `stream-about`, `eos` (end-of-stream), `standby`,
  `ready`, `next`, `rest`, `discard`, `generator`
- **Directive / goal-oriented:** `achieve`, `unachieve`
- **Notification / publish-subscribe:** `subscribe`, `monitor`
- **Capability advertisement:** `advertise`, `unadvertise`
- **Networking / registration:** `register`, `unregister`, `transport-address`, `forward`, `broadcast`,
  `pipe`, `break`
- **Mediation via a third party:** `broker-one`, `broker-all`, `recommend-one`, `recommend-all`,
  `recruit-one`, `recruit-all`
- **Meta / exception:** `error` (message was malformed), `sorry` (message understood but no answer will
  be given), `reply`, `cancel`

The mediation family deserves emphasis because it is the *closest classical analogue to modern
orchestrator patterns*, and the distinctions are sharper than anything in today's agent frameworks:

- `broker-one X`: sender asks the facilitator to *find* a suitable agent, forward `X` to it, and relay the
  answer back. The facilitator sits in the data path for both directions.
- `recommend-one X`: the facilitator does *not* forward; it merely replies with the *name* of an agent
  that advertised the ability to handle `X`. The requester then contacts that agent directly.
- `recruit-one X`: the facilitator forwards `X` to a suitable agent, but instructs that agent to reply
  **directly to the original sender**, not back through the facilitator.

So KQML already distinguished **routing through a mediator**, **name resolution then direct connection**,
and **third-party introduction with direct reply**. Those are three genuinely different
locality/latency/trust regimes, and a modern protocol that offers only "orchestrator relays everything"
has *lost* expressive power relative to 1994. `[Flag for the paper: this is one of the "things they got
right that we lost."]`

A subtle and often-missed semantic commitment: the KQML specification states that because agents are
autonomous and may have conflicting agendas, "the meaning of a KQML message is defined in terms of
constraints on the message **sender** rather than the message **receiver**" [kqmlspec1993]. The receiver
remains free to choose any compatible course of action. This is a *deliberately weak* obligation model —
weaker than usually credited — and it prefigures the later "social commitment" repairs (§2.5).

### 1.4 Facilitators

KQML's answer to discovery and connection management is a distinguished agent class: the **facilitator**
[finin1994kqml; kqmloverview]. Facilitators "bridge the world of host names" and provide:

- maintaining a **registry of service names** (naming),
- **forwarding** messages to named services,
- **content-based routing**,
- **matchmaking** between information providers and clients,
- **mediation and translation** services.

Agents describe their information requirements and capabilities using the meta-data performatives
(`advertise`, `subscribe`, `register`), and the facilitator uses those descriptions to satisfy
`broker-*` / `recommend-*` / `recruit-*` requests. Implementations also included a *Facilitator Interface
Library (FIL)* that translated between a host system's internal KB transactions (definitions, queries,
assertions) and KQML traffic [kqmloverview].

Two observations for AgentMPI:

1. The facilitator is a **soft, discretionary** name service. There is no notion of a *closed group with
   a fixed membership snapshot*, which is exactly what MPI's communicator provides and what makes MPI
   collectives and deterministic matching possible. KQML's dynamic-open-world assumption is what
   forecloses collective operations.
2. The **advertise → matchmake → invoke** loop is the direct ancestor of today's tool/agent registries
   and "agent cards." It is worth citing so the paper does not present dynamic capability discovery as
   a 2024 invention.

### 1.5 The critiques

This subsection is the load-bearing one for the paper's argument, so I keep the claims tightly sourced.

**(a) Cohen & Levesque, "Communicative Actions for Artificial Agents" (ICMAS 1995)**
[cohen1995communicative]. The paper examines KQML's semantics and reports **three general difficulties**
with the draft specification:

1. **Ambiguity and vagueness.** "The meaning of the reserved or standard performatives is rather
   unclear." Performatives are given *English glosses*, which are often imprecise
   [cohen1995communicative].
2. **Performatives that are not actions.** Cohen & Levesque argue that the specification's treatment
   implies "performatives do in fact have truth values, and are not actions after all" — i.e. the
   semantic type of a KQML performative slides toward *proposition*, undermining the whole speech-act
   framing on which the design is justified [cohen1995communicative].
3. **Missing performatives — no commissives.** "A most important class of communication actions seems to
   be missing entirely — the commissives, which commit an agent to a course of action. The prototypical
   example of a commissive is promising; other examples include accepting a proposal, and agreeing to
   perform a requested action. Without these actions, it is hard to see how any multiagent system could
   work robustly" [cohen1995communicative]. They specifically rebut the defence that a `tell` about one's
   own future action suffices, arguing the logical form is wrong and that the base language should supply
   a generic commissive that designers specialise.

They also propose an **adequacy criterion — compositionality** — showing how a *question* can be composed
from a *request* and an *inform*, and argue a proper ACL semantics must support such composition
[cohen1995communicative]. And they note a genuine cost of KQML's content opacity: it "prevents the content
from being checked for compatibility with the speech act type" — e.g. an agent should only be able to
promise actions it will itself perform, but with an opaque payload nothing can enforce that
[cohen1995communicative]. **This is the sharpest technical statement of the trade-off AgentMPI is making
on purpose**: semantics-thin envelopes buy transport freedom and pay in un-checkable payloads.

**(b) Labrou & Finin's own semantics attempt** [labrou1994semantics]. The KQML team responded with a
pre-/post-condition semantics in the style of Cohen & Perrault's action-theoretic account of speech acts
[cohen1979elements]: each performative is given preconditions, postconditions and completion conditions
framed over the *mental states* (belief, knowledge, want, intention) of sender and receiver — e.g.
`tell(S,R,X)` means S believes X and wants R to know that S believes X; `ask-if(S,R,X)` concerns whether R
believes X [labrou1994semantics; vieira2007formalsemantics]. Note the fateful move: **the fix for
"vague English glosses" was to reach for a modal logic of mental states.** That is precisely the choice
Wooldridge then showed to be unverifiable.

**(c) Wooldridge, "Semantic Issues in the Verification of Agent Communication Languages"** (Autonomous
Agents and Multi-Agent Systems 3(1):9–31, 2000) [wooldridge2000semantic]. This is the decisive critique.
Wooldridge asks for a **verifiable semantics**: "a semantics where conformance or otherwise to the
semantics could be determined by an independent observer." He formalises what verifiability means for an
agent communication framework and then identifies the blocking problem:

> "We must be able to characterize the properties of an agent program as a formula of the language L_S
> used to give a semantics to the communication language. L_S is often a multimodal logic, referring to
> (in the FIPA-97 case, for example) the beliefs, desires, and uncertainties of agents. We currently have
> very little idea about systematic ways of attributing such mentalistic descriptions to programs — the
> state of the art is considerably behind what would be needed for anything like practical verification,
> and this situation is not likely to change in the near future." [wooldridge2000semantic]

And the standards-level consequence, in his words: "if there is no way of determining whether or not a
system that claims to conform to a standard does indeed conform to it, then the value of the standard
itself must be questioned" [wooldridge2000semantic]. Practically: because the semantics is stated over an
agent's *private internal state*, you cannot verify that an agent is sincere, and — more damagingly —
**you cannot detect insincerity** [wooldridge2000semantic; wooldridge2009intro].

**This is the single most important citation for AgentMPI's thesis.** A protocol whose conformance
condition is unobservable cannot support an interoperability ecosystem, because there is no test suite,
no conformance badge, and no way to attribute blame when two implementations disagree. MPI's conformance
conditions, by contrast, are all observable at the interface: did the buffer contain the right bytes, did
the call return, did the collective produce the same result on all ranks.

**(d) Why adoption stalled — documented accounts.** Wooldridge's own teaching materials on ACLs
enumerate the problems with KQML as [wooldridge2009intro; comp310acl]:

- **The performative set was fluid** — it drifted between drafts, so implementations diverged.
- **Different implementations were not interoperable** in practice.
- **Transport mechanisms for messages were not precisely defined** — again defeating interoperability.
- **Semantics were not rigorously defined**; the resulting ambiguity actively "impair[ed]
  interoperability."
- **No commissives**, so agents could not commit to tasks (echoing Cohen & Levesque).
- **The performative set was arguably ad-hoc and overly large.**

Note how few of these are about knowledge representation and how many are about ordinary protocol
engineering: unstable vocabulary, unspecified transport, no conformance story. `[UNVERIFIED: I have not
found a single authoritative post-mortem paper titled as such on KQML's decline; the account above is
assembled from the critique literature and from Labrou/Finin's own retrospective [labrou1999landscape].
Treat "adoption stalled because X" claims as synthesis, not as a cited finding.]`

---

## 2. FIPA-ACL and the FIPA standards

### 2.1 The standardisation effort

FIPA — the **Foundation for Intelligent Physical Agents** — was founded in 1996 as a Swiss non-profit
standards body, and produced successive specification sets known as **FIPA97**, **FIPA98** and
**FIPA2000** [fipa2002acl; fipa2002cal]. FIPA97 was the first complete set; FIPA2000 was the mature one
and is what "FIPA-ACL" normally means today. The relevant specification numbers (those I verified
directly against fipa.org are marked ✔):

| Spec | Title |
|---|---|
| **FIPA00061** ✔ | *ACL Message Structure Specification* — the message parameters |
| **FIPA00037** ✔ | *Communicative Act Library Specification* — the 22 acts and their formal semantics |
| **FIPA00008** | *SL Content Language Specification* (FIPA-SL) |
| **FIPA00023** | *Agent Management Specification* — AMS, DF, MTS, agent lifecycle |
| **FIPA00029** ✔ | *Contract Net Interaction Protocol Specification* |
| **FIPA00026 / 00027 / 00028** | Request / Query / Request-When interaction protocols |
| **FIPA00030 / 00033 / 00034 / 00035 / 00036** | Iterated-Contract-Net / Brokering / Recruiting / Subscribe / Propose |
| **FIPA00070 / 00069 / 00071** | ACL message representations: String, Bit-Efficient, XML |

`[UNVERIFIED: the spec numbers not marked ✔ are from memory of the fipa.org numbering scheme; check each
before the camera-ready.]`

Compared with KQML, FIPA-ACL is a **much more professional standards artefact**: it fixed a normative
parameter set, gave every act a formal semantics, defined three concrete wire encodings, specified a
transport envelope, and specified the platform services. Almost every KQML engineering complaint in §1.5(d)
was addressed. **FIPA still lost.** That fact is the strongest possible argument for the paper's thesis,
because it shows the failure was not sloppiness — it was the choice of *what to standardise*.

### 2.2 Message parameters (FIPA00061)

"A FIPA ACL message contains a set of one or more message parameters. Precisely which parameters are
needed for effective agent communication will vary according to the situation; the only parameter that is
mandatory in all ACL messages is the `performative`, although it is expected that most ACL messages will
also contain `sender`, `receiver` and `content` parameters" [fipa2002acl].

The normative parameter set, grouped by the specification's own categories [fipa2002acl]:

| Parameter | Category | Description (paraphrasing FIPA00061) |
|---|---|---|
| `performative` | Type of communicative act | The type of the communicative act; reserved values in FIPA00037. **Only mandatory parameter.** |
| `sender` | Participant | Identity of the sender — "the name of the agent of the communicative act." |
| `receiver` | Participant | Identity of the *intended recipients* (plural — multicast is in the model). |
| `reply-to` | Participant | Agent to which subsequent replies should be directed, instead of `sender`. |
| `content` | Content | The payload; opaque to the ACL layer. |
| `language` | Description of content | The content language (e.g. FIPA-SL). |
| `encoding` | Description of content | The encoding of the content expression. |
| `ontology` | Description of content | The ontology giving meaning to symbols in the content. |
| `protocol` | Control of conversation | The interaction protocol this message belongs to. |
| `conversation-id` | Control of conversation | Identifies "the ongoing sequence of communicative acts that together form a conversation." |
| `reply-with` | Control of conversation | Expression the responder will use to identify *this* message; for disambiguating simultaneous dialogues. |
| `in-reply-to` | Control of conversation | "References an earlier action to which this message is a reply." |
| `reply-by` | Control of conversation | "A time and/or date expression which indicates the latest time by which the sending agent would like to have received a reply." |

Three of these are directly relevant to AgentMPI's design vocabulary:

- **`conversation-id` is the classical ancestor of a communication context / session tag.** It is
  strictly weaker than an MPI communicator: it is a correlation label, not a *closed group with agreed
  membership*. There is no way to ask "who is in this conversation," no rank ordering, no barrier.
- **`reply-with` / `in-reply-to` are an explicit request/response correlation mechanism**, motivated in
  the spec precisely by concurrency ("a situation where multiple dialogues occur simultaneously"
  [fipa2002acl]). This is functionally MPI's *tag*. FIPA understood tag-based matching.
- **`reply-by` is a first-class, in-band deadline.** FIPA00061 carefully distinguishes it from the
  timeout for protocol termination [fipa2002acl]. **Modern LLM agent protocols almost universally lack an
  in-band deadline parameter**; timeouts live in the client library, invisible to the peer. This is a
  concrete "they got it right, we lost it" item.

Note also that FIPA separated the **ACL message** from the **transport envelope** (`to`, `from`,
`acl-representation`, `date`, `payload-length`, `payload-encoding`, `received`, `security-object`)
[fipa2002acl; jadetutorialfipa]. This layering — envelope routing fields distinct from application
addressing fields — is good design and is exactly the discipline a modern protocol needs if it wants
relays, gateways and audit points.

### 2.3 The 22 communicative acts (FIPA00037)

Verified directly against the Communicative Act Library table of contents [fipa2002cal]:

1. `accept-proposal`
2. `agree`
3. `cancel`
4. `cfp` (call for proposal)
5. `confirm`
6. `disconfirm`
7. `failure`
8. `inform`
9. `inform-if`
10. `inform-ref`
11. `not-understood`
12. `propagate`
13. `propose`
14. `proxy`
15. `query-if`
16. `query-ref`
17. `refuse`
18. `reject-proposal`
19. `request`
20. `request-when`
21. `request-whenever`
22. `subscribe`

Structurally, FIPA is *much* tidier than KQML's ~40+ performatives: `inform` and `request` are the two
primitives, and most other acts are formally **composed** from them (`propose` and `accept-proposal` are
defined in terms of `inform`, inheriting its feasibility preconditions and effects [boella2016defeasible;
fipa2002cal]). Note that FIPA **fixed KQML's missing-commissives problem**: `agree`, `propose`,
`accept-proposal` and `refuse` are genuine commissives/decliners. Cohen & Levesque's critique was
substantially answered.

Two acts worth calling out for AgentMPI:

- **`not-understood`** is a *protocol-level* error act, and the spec adds a termination rule to prevent
  livelock: "it is not permissible to respond to a `not-understood` message with another
  `not-understood` message!" [fipainteractionprotocols]. A tiny detail, but it is exactly the kind of
  loop-prevention invariant that modern agent harnesses rediscover the hard way.
- **`proxy`** and **`propagate`** are explicit *forwarding-with-intent* acts: the sender asks a receiver
  to pass a message onward according to a selection expression. This is a routing primitive expressed in
  the ACL rather than hidden in the transport — arguably a layering violation, and worth mentioning as a
  cautionary example if AgentMPI is tempted to expose routing in the message vocabulary.

### 2.4 FIPA-SL, the mentalistic semantics, and the unverifiability critique

**FIPA-SL** (Semantic Language) is the standard content language: a first-order logic with modal
operators for the mental attitudes, allowing content expressions to be propositions, action expressions
or identifying reference expressions (`iota`/`any`/`all`) [fipa2002sl]. In FIPA's logic, agents either
*believe* propositions or are *uncertain* about them; belief and uncertainty are mutually exclusive, and
"knowledge in FIPA's logic is an abbreviation for 'belief or uncertainty'" [pitt1999remarks].

Every communicative act in FIPA00037 is given a semantics as a pair:

- **FP — Feasibility Precondition:** what must hold (of the *sender's mental state*, and its beliefs about
  the receiver's) for the act to be legitimately performed.
- **RE — Rational Effect:** the effect the sender intends to bring about by performing the act.

Canonically, for `inform(s, r, φ)`: FP is roughly that *s* believes φ and *s* does not believe that *r*
already has a belief or uncertainty about φ; RE is that *r* comes to believe φ [fipa2002cal;
pitt1999remarks]. Crucially, FIPA00037 is explicit that the RE is *not guaranteed*: "whether or not the
receiver does, indeed, adopt belief [in] the proposition [will] be a function of the … of the sender"
(trust/credibility) [fipa2002cal]. So FIPA already conceded that the rational effect is not a
postcondition an implementation can be held to — which quietly removes the RE from anything checkable.

**The unverifiability critique.** Wooldridge's argument (§1.5(c)) targets FIPA-97 directly: because L_S is
a multimodal logic over beliefs, desires and uncertainties, and we have "very little idea about
systematic ways of attributing such mentalistic descriptions to programs," conformance to the FIPA
semantics **cannot be determined by an independent observer** [wooldridge2000semantic]. In teaching
terms: we cannot be sure an agent is sincere, and we cannot detect insincerity [comp310acl].

There is a second, sharper critique specific to FIPA's *protocol-level* semantics, from the model-checking
literature. Verifying the Contract Net Protocol against FIPA's own semantics, Bentahar-style analyses
found [tanetal2004verifying]:

- The FIPA semantics for `accept-proposal` **does not encode the conversational context**: "There is no
  notion in either the FP or in the RE, that *s* is accepting a proposal that *r* must have sent. These
  FP and RE could also hold in other speech-acts such as `tell` and [do] not distinguish an
  `accept-proposal` from them" [tanetal2004verifying].
- For `cfp`, the FP "includes that both sender and receiver intend for the receiver to perform the
  request. However, these intentions are premature given that *r* has yet to propose and *s* to accept…
  It does not leave the possibility for refusal or rejection. The rest of the semantics for `cfp` is so
  complicated that its meaning is unclear" [tanetal2004verifying].

That is a devastating finding for the paper's purposes: **the mentalistic semantics was simultaneously
unverifiable *and* insufficiently discriminating** — two distinct acts could satisfy the same FP/RE pair,
so even a cooperative implementer could not use the semantics to decide what to send. Meanwhile the
*interaction protocol* diagrams — plain finite-state message flows — were what implementers actually
coded against (see JADE's `ContractNetInitiator`/`ContractNetResponder`, §6). **The part of FIPA that got
used was the mechanism layer; the part that got ignored was the semantics layer.** This is the empirical
core of AgentMPI's argument and should be stated in exactly those terms.

### 2.5 Social-commitment semantics: the attempted repair

The mainstream response was to move from **mentalistic** to **social** semantics: rather than defining an
act by conditions on private mental state, define it by the **public commitment** it creates. Singh is the
canonical proposer [singh1998socialsemantics]; the "commitments and penalties" line
[mallya2007commitments] and role-based public-attitude accounts [boella2016defeasible] follow. The
key property: a commitment-based semantics "[doesn't] stop an agent lying, but it allows you to detect
when it does" [comp310acl].

**AgentMPI should engage with this seriously rather than dismiss it.** Social semantics is the intellectual
bridge between "semantics-thin mechanism" and "semantics-heavy ontology": it standardises *observable
obligations* (what you owe, to whom, by when) without standardising *belief*. If AgentMPI's one-sided
window has any semantic annotation at all, the commitment-based framing (rather than BDI) is the
defensible place to borrow from.

### 2.6 The interaction protocols

FIPA's interaction protocols (IPs) are finite-state message-flow templates named in the `:protocol`
parameter. The spec is refreshingly pragmatic about why they exist: notionally agents should negotiate
which protocol to use, but "providing the mechanism to do this would negate a key purpose of protocols,
which is to simplify the agent implementation," so by convention putting the protocol name in
`:protocol` is equivalent to (and more efficient than) an explicit `inform` that the initiator intends
the protocol be done [fipainteractionprotocols]. **Protocol identification is out-of-band metadata, not a
negotiated handshake** — a good pattern for AgentMPI.

The protocol family: **Request**, **Query**, **Request-When**, **Contract-Net**, **Iterated-Contract-Net**,
**Brokering**, **Recruiting**, **Subscribe**, **Propose**, plus **English** and **Dutch Auction**
protocols. Two general rules apply across all of them: (i) an agent asked to use a protocol it cannot or
will not support "should send back a `refuse` message explaining this," and (ii) an agent receiving a
message outside the expected response set "should respond with a `not-understood` message"
[fipainteractionprotocols].

#### 2.6.1 FIPA-Request — precise message flow

`FIPA00026`. "The FIPA-request protocol simply allows one agent to request another to perform some action,
and the receiving agent to perform the action or reply, in some way, that it cannot"
[fipainteractionprotocols].

```
Initiator                                   Participant
   |                                             |
   |------------------ request ----------------->|
   |                                             |
   |   (branch 1) <---- not-understood ----------|   [terminate]
   |   (branch 2) <---- refuse ------------------|   [terminate]
   |   (branch 3) <---- agree -------------------|   [continue; commissive]
   |                                             |
   |            ... Participant executes ...     |
   |                                             |
   |   (3a)       <---- failure -----------------|   [terminate: attempted, not done]
   |   (3b)       <---- inform-done -------------|   [terminate: done, no result value]
   |   (3c)       <---- inform-result -----------|   [terminate: done, with result]
   |                                             |
```

Notes with direct AgentMPI relevance:

- The response set after `request` is an **XOR-branch** of exactly three acts (`not-understood`, `refuse`,
  `agree`) — the spec explicitly discusses needing AND/OR/XOR parallelism to describe this
  [fipainteractionprotocols]. This is a *typed, closed* response alphabet per state. Most modern agent
  protocols have an open-ended response space, which is why their state machines are unanalysable.
- `agree` is an explicit **acknowledgement of acceptance distinct from completion**. The three-phase
  shape — *accepted* / *in progress* / *completed-or-failed* — separates "the peer has taken ownership"
  from "the work is done." That is precisely the distinction between MPI's *local completion* (the
  request handle is satisfied) and *remote completion/consistency*. Worth citing when motivating
  AgentMPI's completion semantics.
- `failure` is defined as "informing that an act was considered feasible by the sender, but was not
  completed for some reason," and the receiver is entitled to believe both that "the action has not been
  done" and that "the action is (or, when [the sender] attempted to perform the action, was) feasible"
  [fipa2002cal]. **FIPA distinguished "I won't" (`refuse`) from "I tried and it broke" (`failure`).**
  That is exactly the `refuse`-vs-`failure` distinction that error taxonomies in modern harnesses
  collapse into a single "error" field, destroying retry logic. Strong "they got it right" item.

#### 2.6.2 FIPA-Contract-Net — precise message flow

`FIPA00029` [fipa2002contractnet]. FIPA describes it as "a minor modification of the original contract net
protocol in that it adds rejection and confirmation communicative acts" [fipainteractionprotocols]. One
agent takes the role of **manager/Initiator**; it "wishes to have some task performed by one or more
other agents, and further wishes to optimise a function that characterises the task … commonly expressed
as the price … but could also be soonest time to completion, fair distribution of tasks, etc."
[fipainteractionprotocols].

Quantified exactly as in FIPA00029: the Initiator solicits *m* proposals, receives *n* responses of which
*j* are `propose` and *i = n − j* are `refuse`; it selects *l* winners and rejects *k*
[fipa2002contractnet].

```
Initiator (manager)                         m Participants (potential contractors)
   |                                             |
   |--- cfp (task spec + conditions + reply-by) ->|   [multicast to m participants]
   |                                             |
   |<-- propose (price/time/preconditions) ------|   [j participants]
   |<-- refuse ----------------------------------|   [i = n - j participants]
   |<-- not-understood --------------------------|   [protocol error]
   |                                             |
   |  === deadline (reply-by) expires ===        |
   |  Initiator evaluates the j proposals        |
   |                                             |
   |--- accept-proposal ------------------------>|   [l winners; proposal is BINDING]
   |--- reject-proposal ------------------------>|   [k losers]
   |                                             |
   |            ... winners execute ...          |
   |                                             |
   |<-- inform-done / inform-result -------------|   [success]
   |<-- failure ---------------------------------|   [could not complete]
   |                                             |
```

Three details from the normative text that matter to the paper:

1. **Binding proposals.** "The proposals are binding on the Participant, so that once the Initiator
   accepts the proposal, the Participant acquires a commitment to perform the task"
   [fipa2002contractnet]. This is a *social* obligation, expressed in the protocol rather than in BDI
   logic. It is the good part of FIPA.
2. **The termination / quorum problem is explicitly acknowledged and explicitly solved by a deadline.**
   "This IP requires the Initiator to know when it has received all replies. In the case that a
   Participant fails to reply with either a `propose` or a `refuse` act, the Initiator may potentially be
   left waiting indefinitely. To guard against this, the `cfp` act includes a deadline by which replies
   should be received … Proposals received after the deadline are automatically rejected with the given
   reason that the proposal was late. The deadline is specified by the `reply-by` parameter"
   [fipa2002contractnet]. **This is the single most transferable engineering idea in the whole FIPA corpus
   for AgentMPI**: a fan-out/fan-in collective over an *open* participant set is only well-defined if the
   protocol carries a deadline and a normative rule for late arrivals. MPI solves the same problem the
   other way — by *closing* the group (a communicator has fixed membership, so "all replies" is
   decidable). AgentMPI must pick one of these two answers explicitly; there is no third option.
3. **Late-reply handling is normative, not implementation-defined.** Late proposals are rejected *with a
   stated reason*. Compare with modern harnesses where a late worker response is typically dropped
   silently.

**Iterated-Contract-Net** (`FIPA00030`) "differs from the basic version … by allowing multi-round
iterative bidding. As above, the manager issues the initial call for proposals with the `cfp` act. The
contractors then answer with their bids as `propose` acts. The manager may then accept one or more of the
bids, rejecting the others, **or may iterate the process by issuing a revised `cfp`**… The process
terminates when the manager refuses all proposals and does not issue a new call, accepts one or more of
the bids, or the contractors all refuse to bid" [fipainteractionprotocols]. Note the three explicitly
enumerated termination conditions — again, a discipline worth copying.

**Brokering** (`FIPA00033`) and **Recruiting** (`FIPA00034`) are the FIPA descendants of KQML's
`broker-one` and `recruit-one`, using `proxy` to hand a message plus a receiver-selection expression to a
broker; in brokering the broker relays results back, in recruiting the found agent replies directly to the
original requester `[UNVERIFIED: I did not re-read SC00033/SC00034 in this pass; the broker/recruit
distinction here is inferred from the KQML lineage and should be re-checked]`. **Subscribe**
(`FIPA00035`) establishes a persistent interest such that the participant sends `inform-result` whenever
the referenced object changes. **Propose** (`FIPA00036`) inverts Request: the initiator offers to perform
an action and the participant accepts or rejects.

### 2.7 FIPA's move to IEEE and its decline

**Verified against fipa.org's own front page** [fipaieee2005]: "FIPA, the standards organization for
agents and multi-agent systems was officially accepted by the IEEE as its **eleventh standards committee
on 8 June 2005**. FIPA was originally formed as a Swiss based organization in 1996 to produce software
standards specifications for heterogeneous and interacting agents and agent based systems." The mechanism:
"In March 2005, the FIPA Board of Directors presented this opportunity to the entire FIPA membership, who
unanimously voted to join the IEEE Computer Society." The stated rationale is itself revealing: "Now, it is
time to move standards for agents and agent-based systems into the wider context of software development.
In short, **agent technology needs to work and integrate with non-agent technologies**" [fipaieee2005].
That is a standards body publicly acknowledging that its constituency had become too narrow.

And the current status, in FIPA's own words on the same page: "**FIPA is currently not active**; however,
its collection of standards is open to all" [fipaieee2005]. Under IEEE governance FIPA "no longer
maintained an independent board or membership structure" and no FIPA-specific committee activity has been
formed in recent years [fipagrokipedia — *secondary source, low confidence*].

So the timeline is: FIPA97 (first specification set, October 1997), FIPA98, FIPA2000 (the mature set),
transfer to IEEE June 2005, dormancy thereafter. **The move to IEEE was not a promotion followed by
decline; the vote to move was itself the admission that the standalone agent-standards project had
stalled.** That is the fair reading and it is supported by the primary text.

Honest assessment of the decline (synthesis, flagged as such):

- The **implementations outlived the standard**. JADE remained widely used in teaching and in some
  industrial deployments long after FIPA stopped evolving — but users adopted JADE's *behaviours and
  protocol classes*, not FIPA's semantics.
- The problem FIPA solved was **partly solved away** by the web-services and later REST/RPC stack: naming,
  transport, envelopes, discovery and typed request/response were provided by WSDL/SOAP/UDDI and then by
  HTTP+JSON, with far larger constituencies and no ontological commitments.
- The **ontology requirement was the adoption tax.** Two FIPA-compliant agents that do not share an
  ontology cannot usefully talk, so FIPA-compliance bought interoperability of *envelopes* while the hard
  part — agreeing on domain vocabulary — remained per-deployment. Compliance therefore delivered much
  less than it appeared to promise.

`[UNVERIFIED: the three bullets above are my synthesis of the standards history, not claims sourced to a
specific post-mortem paper. If the paper needs a citable account of FIPA's decline, the most defensible
citations are Wooldridge's verification critique [wooldridge2000semantic] plus Labrou/Finin's own
landscape survey [labrou1999landscape], and the argument should be framed as analysis rather than as
reported fact.]`

---

## 3. The Contract Net Protocol (Smith, 1980)

Reid G. Smith, *The Contract Net Protocol: High-Level Communication and Control in a Distributed Problem
Solver*, **IEEE Transactions on Computers C-29(12):1104–1113, December 1980**
[smith1980contractnet]. (DOI `10.1109/TC.1980.1675516`. Note: many bibliographies date this 1981; the
issue is December 1980. The companion journal article is Davis & Smith, *Negotiation as a Metaphor for
Distributed Problem Solving*, Artificial Intelligence 20(1):63–109, 1983 [davis1983negotiation], and
Smith's thesis work at Stanford predates both.)

From the abstract: "The contract net protocol has been developed to specify problem-solving communication
and control for nodes in a distributed problem solver. Task distribution is affected by a negotiation
process, a discussion carried on between nodes with tasks to be executed and nodes that may be able to
execute those tasks" [smith1980contractnet]. The driving application was distributed sensing (a
distributed sensor network locating vehicles), which is why the running example involves nodes with
positions and sensor types.

Note the title: **"High-Level Communication *and Control*."** Contract Net is not a message format; it is
a *control regime* expressed through messages. Roles (manager, contractor) are **transient and per-task**,
not architectural — the same node is manager for one task and contractor for another, and contracts nest
recursively. This is the single most important structural point for the paper's discussion of modern
orchestrator/worker patterns, most of which hard-wire the role into the topology.

### 3.1 Task announcement

"A node that generates a task normally initiates contract negotiation by advertising existence of that
task to the other nodes with a task announcement message. It then acts as the manager of the task"
[smith1980contractnet]. Addressing modes:

- **general broadcast** — to all nodes,
- **limited broadcast** — to a subset,
- **point-to-point** — to a single node.

The latter two Smith calls **focused addressing**, and his justification is a *systems* justification, not
a knowledge-representation one: it "reduce[s] message processing overhead by allowing nonaddressed nodes to
ignore task announcements after examining only the addressee slot. The saving is small, but is useful
because it allows a node's communication processor alone to decide whether the rest of the message should
be examined and further processed. It is also useful for reducing message traffic when the nodes of the
problem solver are not interconnected with broadcast communication channels" [smith1980contractnet].

**This is a genuinely MPI-flavoured argument in a 1980 AI paper**: put the discriminating field early in
the envelope so a cheap engine can filter without parsing the payload, and don't assume broadcast is free.
Cite it — it shows that the mechanism-level sensibility AgentMPI advocates *existed* in the agent
literature and was subsequently abandoned by KQML/FIPA, rather than never having been discovered.

A task announcement has **four main slots** [smith1980contractnet]:

| Slot | Content |
|---|---|
| **task abstraction** | A brief description of the task, sufficient for a node to decide whether to bid. *Not* the full task. |
| **eligibility specification** | "A list of criteria that a node must meet to be eligible to submit a bid," e.g. `MUST-HAVE SENSOR`, `MUST-HAVE POSITION AREA A`. Reduces extraneous bidding. |
| **bid specification** | "A description of the expected form of a bid… enables the manager to specify the kind of information that it considers important about a node that wants to execute the task," e.g. `POSITION LAT LONG`, `EVERY SENSOR NAME TYPE`. |
| **expiration time** | "A deadline for receiving bids," e.g. `28 1730Z FEB 1979`. |

Two design ideas here are worth lifting wholesale:

- **Task abstraction vs. full task specification.** The announcement carries only an *abstraction*; the
  complete task detail is transferred in the **award** message, to the winner only. This is a
  bandwidth/attention optimisation with an obvious LLM-era analogue: **do not broadcast the full context
  window to every candidate worker.**
- **The bid specification is a schema for the response.** The requester tells responders *what fields
  their reply must contain*. This is a typed, requester-controlled response format — precisely what
  modern agent frameworks reimplement as ad-hoc "please reply in this JSON schema" prompt text, without
  protocol support.

### 3.2 Bidding

Eligible nodes evaluate the task abstraction against their own state and submit a **bid** message whose
**node abstraction** slot is "filled with a brief specification of the capabilities of the node that are
relevant to the announced task… written in the form indicated by the bid specification of the
corresponding task announcement" [smith1980contractnet]. A bid may also include `REQUIRE` statements for
additional transferable resources (e.g. procedures) [smith1980contractnet]. **A node that judges the task
unsuitable simply does not bid** — silence is a legal response.

That silence creates the same termination problem FIPA later confronted (§2.6.2), and Smith solves it
differently and more richly, with **immediate response bids**: "A node receiving a task announcement whose
bid specification asks for an immediate response bid does not deal with [it in the normal way]" but
replies in a special short form. Three such responses are identified, including *not eligible* and
*eligible but busy* [smith1980contractnet]. The purpose is diagnostic: "The immediate response mechanism
permits a manager to take a more appropriate course of action if a task announcement elicits no bids. The
normal procedure is to simply reissue the task announcement. If this continues to elicit no bids, then the
manager can specify an immediate response bid. If the response is uniformly BUSY, then the manager can
wait" rather than retry [smith1980contractnet].

**Smith distinguished "nobody is capable" from "everybody is busy" and made that distinction a protocol
feature, because the correct recovery action differs.** This is a *backpressure and admission-control*
mechanism from 1980. Modern agent harnesses conflate both cases into "no response, retry with backoff."
Flag as a strong lesson.

### 3.3 Awarding, execution, reporting

- **award** — sent to the selected contractor(s); carries the **task specification**, i.e. the full task
  detail withheld from the announcement.
- **acknowledgment / refusal** — for **directed contracts** (see below), the recipient runs an
  *Acknowledgment Procedure* whose "role is to decide whether to accept or reject the contract. The
  default is to accept"; a *Refusal Processing Procedure* handles rejection [smith1980contractnet]. So
  award is *not* unconditionally binding on the contractor in the directed case — contrast FIPA, where an
  accepted proposal *is* binding (§2.6.2).
- **interim report / final report** — "used by a contractor to inform the manager (and other report
  recipients, if any) that a task has been partially executed (an interim report) or completed (a final
  report). The result description slot contains the results of the execution" [smith1980contractnet]. Note
  **"and other report recipients, if any"** — results can be routed to third parties, not only to the
  manager. A contractor "can be set to work on a task and instructed to issue interim reports whenever
  the next result is ready. It then suspends the task until it is instructed by the [manager to
  continue]" [smith1980contractnet] — i.e. **streaming partial results with explicit flow control**.
- **termination message** — the manager terminates a contract in progress. `[UNVERIFIED: I confirmed
  interim/final report, award, bid, announcement, acknowledgment, refusal, directed contract,
  request/response and node-available in the text; "termination message" is from the protocol's message
  taxonomy but I did not read its section in this pass.]`

### 3.4 The shortcuts: directed contracts, request–response, node availability

Smith is explicit that **full negotiation is overhead to be avoided when possible**: "specialized
interactions, like directed contracts and requests, reduce communication for transactions that do not
require the complexity of negotiation" [smith1980contractnet].

- **Directed contract**: "If a manager knows exactly which node is appropriate for execution of a task, a
  directed contract can be awarded. No task announcement is made and no bids are [solicited]"
  [smith1980contractnet]. This is the degenerate one-round case — and it is exactly a point-to-point
  send.
- **Request–response**: a simple information-transfer exchange with no negotiation at all; knowledge can
  be "distributed dynamically as part of the negotiation process or with a request-response mechanism"
  [smith1980contractnet].
- **Node-available message**: idle nodes can advertise availability, *reversing the initiative* so that
  workers pull rather than managers push.

**The existence of a graceful degradation path from full auction → directed contract → plain
request/response is the most AgentMPI-relevant property of Contract Net.** It means the protocol has a
*cheap common case*, which is the thing MPI has (a two-line `MPI_Send`/`MPI_Recv`) and which KQML/FIPA
conspicuously lack (there is no cheap case; every message carries an ontology commitment).

Messages use "a common internode language based on object–attribute–value triples, with domain-independent
terms for protocol elements and task-specific details" [smith1980contractnet]; the paper gives a **BNF
specification of the protocol** in an appendix, and marks message types "that need not be included in a
basic implementation" with an asterisk [smith1980contractnet]. **A normative BNF plus an explicitly
labelled mandatory core and optional extensions** is exactly the profile structure that made MPI
implementable and that KQML lacked.

### 3.5 Relation to modern "manager delegates to workers" patterns

Correspondence table for the related-work section:

| Contract Net (1980) | Modern LLM harness analogue |
|---|---|
| Manager / contractor as **per-task transient roles** | Orchestrator / sub-agent, usually a **fixed** architectural role |
| Task announcement with **task abstraction** | Broadcasting the sub-task prompt — usually with full context, not an abstraction |
| **Eligibility specification** | Tool/agent selection filters, capability tags, routing rules |
| **Bid specification** | "Respond in this JSON schema"; structured-output constraints |
| **Bid** (self-assessed capability + cost) | Rarely present — most harnesses *assume* the selected worker can do the task |
| **Award** carrying the full task specification | Passing the full context only to the chosen sub-agent |
| **Interim report** with suspend-until-instructed | Streaming tokens / partial results — but without flow control |
| **Immediate response bid** (ineligible vs. busy) | Absent; conflated into errors/timeouts |
| **Directed contract** | Direct tool call / direct sub-agent invocation |
| **Recursive nesting** of contracts | Recursive sub-agent spawning |

The honest observation: **modern agent orchestration is Contract Net with the bidding removed.** The
manager assigns rather than solicits, which eliminates the market mechanism that made Contract Net
load-adaptive. Whether that is a loss depends on whether workers have private information about their own
suitability — with LLM workers they largely do not (they are near-identical), which is a *legitimate*
reason the bidding step atrophied. Make this argument rather than simply lamenting the loss; it is the
fair version.

---

## 4. Blackboard architectures

### 4.1 HEARSAY-II

Erman, Hayes-Roth, Lesser & Reddy, *The Hearsay-II Speech-Understanding System: Integrating Knowledge to
Resolve Uncertainty*, **ACM Computing Surveys 12(2):213–253, June 1980** [erman1980hearsay]. The system
was a continuous-speech understanding system (roughly a 1000-word vocabulary, connected speech); the
architecture outlived the application entirely.

**Knowledge sources (KSs).** "Because each KS is an independent condition–action module, KSs communicate
through a global database called the blackboard" [erman1980hearsay]. In the September 1976 configuration
the KSs performed acoustic parameter extraction, segment classification into phonetic classes, word
recognition, phrase parsing, and generating/evaluating predictions for undetected words or syllables
[erman1980hearsay].

**Two roles of the blackboard.** This is the key sentence for AgentMPI's shared-window discussion: "In
this framework the blackboard serves in two roles: It represents intermediate states of problem-solving
activity, and it communicates messages (hypotheses) from one KS that activate other KSs"
[erman1980hearsay]. **The blackboard is simultaneously the state and the channel.** Any design that
replaces messages with shared memory inherits exactly this conflation, and with it the loss of the ability
to distinguish "this datum exists" from "this datum was communicated to you."

**Structure.** The blackboard is "subdivided into a set of information levels corresponding to the
intermediate representation levels of the decoding processes (phrase, word, syllable, etc.)." Each
hypothesis sits at one level with a defining label, plus "its time coordinates within the spoken utterance
and a credibility rating." Levels form "a loose hierarchical structure: hypotheses at each level aggregate
or abstract elements at the adjacent lower level" [erman1980hearsay]. So the shared structure is
**two-dimensional (abstraction level × time interval) and every entry carries a confidence score** — not
an undifferentiated log.

### 4.2 The control problem: who acts next, and how conflicts are resolved

This is the subsection you asked to be specific about, so I quote the primary sources closely.

**The problem statement.** KSs are independent and data-driven; a KS's condition may be satisfied by many
blackboard configurations, so at any moment there are far more executable actions than can be run. Lesser
& Erman report that in practice "there are, in general, a number of pending tasks to execute — both
invoked KSs and triggered preconditions. (In practice, the number of pending tasks often exceeds 200.)"
[lesser1977retrospective]. The control problem is therefore a *scheduling* problem over a large pending
set, and it is the architecture's central difficulty — not an implementation detail.

**HEARSAY-II's answer: a heuristic priority scheduler.** "Selective attention is accomplished in the
Hearsay-II system by a heuristic scheduler which calculates a priority for each action and executes, at
each time, the waiting action with the highest priority. The priority calculation attempts to estimate the
usefulness of the action in fulfilling the overall system goal of recognizing the utterance"
[erman1980hearsay]. Its inputs:

1. **The stimulus frame** — "the set of hypotheses that satisfied the condition" of the KS.
2. **The response frame** — "a stylized description of the blackboard modifications that the KS action is
   likely to perform." Example: a syllable-based word hypothesiser's stimulus frame includes the matching
   syllable hypothesis, and its response frame "would specify the expected action of generating word
   hypotheses in a time interval spanning that of the stimulus frame" [erman1980hearsay].
3. **Global state information** — "especially the credibility and duration of the best hypotheses in each
   level and time region and the amount of processing required from the time the current best hypotheses
   were generated," which "allows the system to reappraise its confidence in its current best hypotheses
   if they are not [being confirmed]" [erman1980hearsay].

**The response frame is the crucial mechanism and it should be central to your one-sided-window
discussion.** Before running, a KS declares *what region of the blackboard it intends to write and at
what abstraction level*. That is a **declared write set** — an intent declaration over a shared address
space, submitted to a scheduler for arbitration. It is structurally the same object as an RMA
epoch/access-declaration: a promise about which locations will be touched, made in advance so the runtime
can order or exclude conflicting operations. If AgentMPI's one-sided window has anything resembling
declared-extent access, `[erman1980hearsay]` is the right ancestor citation and a far more interesting one
than a generic "blackboard systems used shared memory" gesture.

**Execution discipline / how conflicts are handled.** Two normative facts from Lesser & Erman
[lesser1977retrospective]:

- **KS executions run to completion:** "Each KS execution goes to completion; that is, the KS cannot put
  itself to 'sleep', waiting for some other event (on the blackboard) to occur." A KS wanting to react to
  later events must terminate and be re-triggered, and "whenever a KS executes, it uses the stimulus frame
  specific to that invocation."
- Triggering became **interrupt-driven rather than polling**: a KS registers interest and "is then given
  pointers to all of [the changes that create such situations]. This changes a polling action into an
  interrupt-driven one and is more efficient, especially for a large number of KSs" [erman1980hearsay].

So the conflict-resolution answer is: **serialise at the granularity of a whole KS activation, and resolve
contention by priority rather than by locking.** There are no transactions, no rollback, and no mutual
exclusion primitives exposed to KSs; the scheduler's serialisation *is* the concurrency control. Conflicts
between competing hypotheses are not "resolved" at the data layer at all — **contradictory hypotheses
coexist on the blackboard with credibility ratings, and the resolution is emergent**: better-supported
hypotheses accumulate credibility and attract scheduling attention, worse ones starve. That is a
fundamentally different conflict model from consensus or locking, and it is worth saying explicitly
because it is the model most "shared scratchpad" LLM designs implicitly adopt without acknowledging that
it requires (a) confidence scores and (b) a priority scheduler to work.

**Design goals of the data-directed control structure**, in Lesser & Erman's own list
[lesser1977retrospective]:

1. "The quick refocusing of attention to appropriate hypotheses in the blackboard."
2. "The flexible reconfiguration of the system with different sets of independent (and possibly competing)
   KSs, and different global control strategies."
3. "The exploration of parallel processing."

Goal 2 is the *interoperability* argument — a blackboard is a plug-in bus, and KSs are mutually ignorant.
Goal 3 is the *parallelism* argument, and it is where blackboards ran into trouble: a single global
mutable database is a contention bottleneck, which is precisely why Lesser's own subsequent work moved to
*distributed* problem solving with explicit inter-node communication (DVMT, partial global planning —
§7.3). **Cite this trajectory: the person who built the canonical blackboard system spent the following
decade building message-passing coordination instead.** That is strong, honest support for AgentMPI's
thesis, and it is a matter of publication record rather than interpretation.

### 4.3 Opportunistic reasoning

The blackboard model's signature claim is **opportunistic problem solving**: control is neither top-down
(goal-driven refinement) nor bottom-up (data-driven aggregation), but decided moment-to-moment by whatever
looks most promising. HEARSAY-II's islands-of-reliability model makes this concrete: "Processing can be
organized in terms of the incremental additions of small units of information to a limited number of
alternative hypotheses. The limited number of alternatives derives from the view that there are islands of
reliability in the acoustic data that can be used to anchor the search. Each small increment of
information should help to verify, refute, or augment (expand) an hypothesis. A KS action, though performed
in a local context, could also have the side effect of contributing information useful in the evaluation of
alternative hypotheses (i.e., in other contexts)" [lesser1977retrospective].

The last clause is the honest cost of the design: **any action can have globally relevant side effects, so
no action's effects can be locally bounded.** That is the same property that makes shared-memory
programming hard and message-passing programming tractable, stated in 1977 in AI vocabulary.

### 4.4 BB1 — control as a first-class blackboard problem

Barbara Hayes-Roth, *A Blackboard Architecture for Control*, **Artificial Intelligence 26(3):251–321,
1985** [hayesroth1985bb1]; see also the Stanford technical report CS-TR-84-1034, *BB1: An Architecture
for Blackboard Systems that Control, Explain, and Learn about their own Behavior* (December 1984)
[hayesroth1984bb1tr].

A BB1 system comprises "a user-defined **domain blackboard**, a pre-defined **control blackboard**,
user-defined domain and control **knowledge sources**, a few generic control knowledge sources, and a
pre-defined **basic control loop**" [hayesroth1984bb1tr].

The mechanism:

- Executable KS activations are recorded on an **agenda**. "Each record of a knowledge source and its
  triggering context on the agenda is called a **knowledge source activation record** (KSAR). A scheduler
  selects the next knowledge source to execute" [bbinstructional].
- The innovation: "BB1 differs from earlier blackboard architectures (e.g., Hearsay-II) in its approach to
  scheduling. Rather than using a fixed algorithmic scheduler, another blackboard called the **control
  blackboard** records the heuristics that form the scheduling function. By adding to and altering the
  records on this blackboard, BB1 can vary the scheduler, and thus the choice of knowledge sources
  executed by the blackboard system. Essentially, **the blackboard paradigm is applied recursively in BB1
  to solve its control problem — deciding which KSAR to execute next**" [bbinstructional].
- How priorities are computed: "The scheduler interprets the control plan blackboard to determine a rank
  ordering of the KSARs on the agenda. Records on the control plan indicate preferences. For example, one
  record might indicate a preference for presentation actions or assessment actions. Another record ranks
  KSARs generated from control knowledge sources over those generated from domain knowledge sources.
  Together the records on the control plan blackboard form the pieces of a **heuristic evaluation function
  that are weighted then summed** together to prioritize KSARs" [bbinstructional].
- Meta-level capability: control KSs "could then monitor whether planning and problem-solving were
  proceeding as expected or stalled. If stalled, BB1 could switch from one strategy to another as
  conditions — such as the goals being considered or the time remaining — changed" [wikiblackboard].
  BB1 also "explains problem-solving actions by showing their roles in the underlying control plan" and
  "learns new control heuristics from experience" [hayesroth1984bb1tr]. Application domains included
  construction-site planning, protein-structure inference from X-ray crystallography, intelligent tutoring
  and real-time patient monitoring [wikiblackboard].

**Why this matters to AgentMPI.** BB1 is the reductio of the "let the scheduler decide" approach: once you
accept that a shared-state architecture needs a scheduler, you discover the scheduler needs a policy, the
policy needs to be domain-specific, and so the policy itself becomes an AI problem requiring its own
blackboard, its own knowledge sources, and its own explanation and learning machinery. **Control
complexity is not eliminated by shared state; it is relocated into the scheduler and amplified.** In
contrast, explicit message passing distributes the control decision into the participants: a process's
next action is determined by its own program plus the messages it has received, and no global arbiter is
needed. This is the cleanest possible argument for the one-sided-window design being *paired with*
explicit synchronisation rather than left to a scheduler, and BB1 is the citation that makes it.

### 4.5 GBB

Corkill, Gallagher & Murray, *GBB: A Generic Blackboard Development System*, **AAAI-86**
[corkill1986gbb]; also Corkill, Gallagher & Johnson, *Achieving Flexibility, Efficiency, and Generality in
Blackboard Architectures*, AAAI-87 [corkill1987flexibility].

GBB's contribution is engineering, not epistemology: it "unifies many characteristics of the blackboard
systems constructed to date," and "consists of two distinct [parts]: [a database/storage subsystem] and a
**control shell**" [corkill1986gbb]. Its distinguishing features:

- "A strong emphasis was made on efficient insertion [and retrieval from the blackboard] storage
  structure. This allows a blackboard database implementation [to be changed] without changing the
  [rest of the system]" [corkill1986gbb].
- **Multi-dimensional blackboards**, where dimensions may be "ordered [or] enumerated" (unordered), with
  correspondingly efficient pattern matching [corkill1986gbb; wikiblackboard].
- Control shells are separable; **GBB1** is a GBB control shell that "implements BB1's style of control
  while adding efficiency improvements" [wikiblackboard].

The separation of **blackboard storage** from **control shell** is the clean architectural lesson: GBB
recognised that "how the shared structure is indexed and accessed" and "who runs next" are independent
concerns with independent implementations. If AgentMPI exposes a window, that separation should be
explicit in the paper's architecture diagram, with `[corkill1986gbb]` as the precedent. GBB's other
transferable insight is that **efficient associative retrieval over a shared structure requires the
structure to be dimensioned and indexed in advance** — you cannot make unrestricted content-based lookup
fast. That observation connects directly to the tuple-space cost argument in §5.

---

## 5. Tuple spaces

### 5.1 Linda

David Gelernter, *Generative Communication in Linda*, **ACM TOPLAS 7(1):80–112, January 1985**
[gelernter1985linda]. Companion expositions: Carriero & Gelernter, *Linda in Context*, CACM 32(4), 1989
[carriero1989lindaincontext], and *How to Write Parallel Programs: A Guide to the Perplexed*, ACM
Computing Surveys 21(3), 1989 [carriero1989howto].

**The model.** "An executing Linda program is regarded as occupying an environment called 'tuple space' or
TS. However many concurrent processes make up a distributed program, all are encompassed within one TS.
Consider two communicating processes A and B. To send data to B, A generates tuples and adds them to TS;
B withdraws them" [gelernter1985linda].

**Why "generative."** "This communication model is said to be generative because, until it is explicitly
withdrawn, the tuple generated by A has an independent existence in TS. A tuple in TS is equally
accessible to all processes within TS, but is bound to none" [gelernter1985linda]. The tuple **outlives
its producer**; a tuple never removed "will, in the abstract, remain in TS forever" [gelernter1985linda].

**The primitives** (four basic, plus two predicate variants) [carriero1989howto; lindamodel]:

| Primitive | Semantics |
|---|---|
| `out(t)` | "causes tuple `t` to be added to TS; the executing process continues immediately." Non-blocking. |
| `in(s)` | "causes some tuple `t` that matches template `s` to be withdrawn from TS; the values of the actuals in `t` are assigned to the formals in `s`." **Destructive.** "If no matching `t` is available when `in(s)` executes, the executing process suspends until one is." "If many matching `t`'s are available, one is chosen arbitrarily." |
| `rd(s)` | "the same as `in(s)`, with actuals assigned to formals as before, except that the matched tuple remains in TS." **Non-destructive**, also blocking. |
| `eval(t)` | "the same as `out(t)`, except that `t` is evaluated after rather than before it enters TS; `eval` implicitly forks a new process to perform the evaluation. When computation of `t` is complete, `t` becomes an ordinary passive tuple, which may be `in`ed or `rd`d like any other tuple." |
| `inp(s)` / `rdp(s)` | Predicate versions: "attempt to locate a matching tuple and return 0 if they fail; otherwise, they return 1 and perform actual-to-formal assignment." |

An important and rarely-quoted caveat on the predicate forms, straight from Gelernter's own text: "If and
only if it can be shown that, irrespective of relative process speeds, a matching tuple must have been
added to TS before the execution of `inp` or `rdp` and cannot have been withdrawn by any other process
until the `inp` or `rdp` is complete, the predicate operations are guaranteed to find a matching tuple"
[carriero1989howto]. **The non-blocking probe is only well-defined under a global argument about race
freedom that the model itself gives you no way to express.** That is a first-rate cautionary citation for
any AgentMPI "test/probe" operation: non-blocking queries over shared state have semantics you cannot
localise.

**Associative matching.** "There are no tuple addresses in an associative memory." Matching rules: "any
values included in the `in` or `rd` must be matched identically; formal parameters must be matched by
values of the same type" — so matching is by **arity + positional type + literal value equality**, with
`?x` formals as typed wildcards. "When a matching tuple is found it is removed, the value of its second
field is assigned to `f` and its third field to `i`. If there are no matching tuples when `in` executes,
the `in` statement blocks until a matching tuple appears. If there are many, one is chosen
nondeterministically" [carriero1989lindaincontext]. Formals may also appear *in* tuples, "serv[ing] only
as wildcards, expanding the range of possible matches" — but "values are not communicated from the `in`
statement 'backward' to the tuple" [carriero1989lindaincontext], i.e. matching is not unification.

**The two uncouplings.** Gelernter identifies **space uncoupling** — "a tuple in TS tagged 'P' may be
input by any number of address-space-disjoint processes… j processes executing on j distinct network nodes
may all accept tuples tagged with one name" — and **time uncoupling**: "while distributed programming
languages ordinarily allow communication between processes that are space-disjoint, Linda allows
communication between time-disjoint processes as well" [gelernter1985linda]. Communication is
**orthogonal**: "just as the receiver has no prior knowledge about the sender, the sender has none about
the receiver" [gelernter1985linda].

**The implementation problem Gelernter himself names.** "A name space that is global to all modules of a
distributed Linda program makes it impossible to resolve global-name references statically before
runtime… At time q, processes are suspended at `in()` statements on some (possibly empty) subset S of the
j nodes occupied by the program, but it is impossible to determine the identity of S at compile- or
load-time. Some method must therefore be provided for matching tuples to `in()` statements at runtime, or,
in other words, for implementing a **dynamic global name space**" [gelernter1985linda]. **This is the
crux of the whole §5 comparison and it comes from the model's inventor:** anonymity forces a runtime
global rendezvous, because you cannot know statically who will receive what.

### 5.2 JavaSpaces and TSpaces

- **JavaSpaces** (Sun, part of Jini) — a tuple space for Java objects with `write`, `read`, `take`,
  `notify`, plus **leases** (entries expire), **transactions** (multi-operation atomicity via the Jini
  transaction service), and **distributed events** [freeman1999javaspaces]. Matching is by *entry type*
  plus field-value equality on a template object, with `null` fields as wildcards. The additions —
  leasing and transactions — are exactly the missing pieces Linda's semantics implied: without leases,
  orphaned tuples accumulate forever (Gelernter's "remain in TS forever"), and without transactions a
  multi-tuple update is not atomic.
- **TSpaces** (IBM Almaden) — a tuple space with database-flavoured extensions: indexing, richer queries,
  persistence, event notification, and access control, positioned as "network middleware"
  [wyckoff1998tspaces].

For AgentMPI: **the historical trajectory of tuple-space systems is toward re-adding the things message
passing never needed** — leases (because anonymous data has no owner to free it), transactions (because
associative updates aren't atomic), notifications (because blocking `in` doesn't compose), access control
(because a global space has no natural authority boundary). Note that a shared-window design faces the
same four pressures and should say up front how it answers each.

### 5.3 Why message passing won for HPC — the actual arguments

This is where I want to be scrupulously fair, because the folk story ("associative matching is too
expensive") is **not** what the primary sources say.

**(a) The Linda authors' own head-to-head evaluation.** Carriero & Gelernter, *Linda and Message Passing:
What Have We Learned?* (Yale TR-984, 1993) [carriero1993learned] compared Linda against PVM. Their
conclusions, verbatim:

> "(1) Linda is in general more expressive than message passing, and (2) the performance of Linda and of
> message passing programs is generally comparable… (3) Linda appears to offer a smoother transition than
> message passing to adaptive parallelism." [carriero1993learned]

And on expressiveness: "we believe that Linda is more expressive than message passing essentially always:
this follows from the fact that message passing operations are trivially expressible in Linda"
[carriero1993learned]. So **the strong-form claim "tuple spaces were too slow" is refuted by the
proponents' own measurements**, and the paper should not make it.

**(b) The precise performance exception they concede — and it is a locality argument.** This is the
citation the paper actually needs:

> "The exceptions are those [applications]… In an application relying on 'regular' point-to-point
> communication, [Linda can detect the] set of recipients. Linda detects patterns like this, and supports
> [such] applications as efficiently as a direct message-passing system would. (In practice, most
> message-passing-style applications seem to fall into this category.) **But when message destinations
> are unpredictable — when, for example, processes generate messages and send them to randomly-chosen
> recipients — then there is no pattern for Linda to detect, and Linda's realization of such a program is
> less efficient than a direct message-passing version would be.**" [carriero1993learned]

Read that carefully, because it is the deepest point in this whole document. Linda achieves
message-passing performance **only when a compiler can statically recover the communication pattern that
the programmer deliberately erased by using a tuple space.** The abstraction's whole selling point is
anonymity; its performance depends on that anonymity being undone by static analysis. When analysis fails,
you pay for a distributed associative lookup. **Explicit message passing wins not because it is faster in
the common case but because it is faster in the *worst* case, and because its cost is *predictable from
the source text*.** For HPC, where performance models and hand-tuned communication schedules are the
working method, predictability *is* the requirement. Frame AgentMPI's mechanism-thin choice in exactly
these terms and this is a strong, honest argument rather than a slogan.

**(c) Where the cost actually sits.** Carriero & Gelernter, *The Linda Alternative to Message-Passing
Systems* [carriero1994alternative], state that "the principal argument against such an approach for
portable software has always been that efficient implementations could not scale to massively-parallel,
distributed memory machines," and locate performance in two factors: "the effectiveness of the tuple
classification strategies in reducing or eliminating expensive searches in tuple space, and the efficiency
of the machine-dependent implementations of data transfer." They then report Bjornson's finding
[bjornson1992linda] that "over a number of different applications and problem sizes, Linda's compile- and
link-time analysis and optimization were so effective that the cost of 'searching' was insignificant — in
most cases, in fact, the first tuple examined (as a candidate match for a template) was the correct one"
[carriero1994alternative].

**So: associative matching per se was cheap, given a whole-program compiler.** The honest statement of the
cost is therefore *not* "matching is expensive" but:

1. **The cost is conditional on a whole-program compiler**, which requires the tuple space to be a
   *language* feature, not a *library*. MPI is a library with a fixed ABI, implementable by anyone against
   any transport, and usable from Fortran, C, C++ and Python without a Linda-style pre-compiler. That is
   an ecosystem property, and it is decisive.
2. **The remaining cost is extra messages.** Once searching is optimised away, "the quality of Linda
   performance will depend primarily on the degree to which the run-time system can avoid extra messages
   (relative to message-passing systems) and exploit the underlying low-level communication system"
   [carriero1994alternative]. A logically-shared space needs a rendezvous; a point-to-point send does
   not.
3. **Independent measurements on distributed-memory hardware found the overheads real.** An
   implementation study on Transputer networks concluded that "in general, the communication overheads
   imposed by the system are significant, and that the overall overhead of the implementation
   (attributable to the collective effects of communication, TS search, synchronization…) [is]
   substantial," and that the implementation "as it stands, is too inefficient to be of practical use"
   [xlinda1994]. `[UNVERIFIED: this is a thesis-length implementation study, not a peer-reviewed
   benchmark of a production Linda; treat as suggestive.]`

**(d) The ecosystem explanation, which I think is the truest one.** A recent systematic evaluation of
tuple-space implementations opens by noting that the Linda paradigm "is one of the least used, despite the
fact of being intuitive, easy to understand, and easy to use," and diagnoses the cause bluntly: "**the lack
of a reference implementation for this paradigm has prevented its wide spreading**"
[buravlev2018evaluating]. It then finds enough performance variation across four implementations to
recommend "future work towards building an effective implementation of the tuple space paradigm"
[buravlev2018evaluating].

**Set that against MPI.** MPI shipped a specification *plus* MPICH as a free, portable, high-quality
reference implementation that vendors forked and tuned. Tuple spaces had a beautiful model and no
reference implementation; KQML had a fluid vocabulary and no normative transport; FIPA had normative
everything and no reference-quality free implementation until JADE, by which time the web-services stack
had taken the constituency. **The recurring cause of failure across all three is the same, and it is not
philosophical: no canonical implementation.** That deserves to be said plainly in the paper, because it is
the one lesson that constrains AgentMPI's *release strategy* rather than its design.

### 5.4 Comparison table for the paper

| Property | Tuple space (Linda) | Explicit message passing (MPI) |
|---|---|---|
| Addressing | Anonymous, associative by content/type | Named endpoint (rank) in a closed group |
| Coupling | Space- and time-uncoupled | Space-coupled; time-decoupled only via buffering |
| Data lifetime | Tuple outlives producer; persists until `in` | Buffer ownership returns to sender on completion |
| Who resolves the destination | Runtime (dynamic global name space) | The programmer, statically |
| Cost predictability from source | Poor without whole-program analysis | Direct; underpins HPC performance models |
| Multi-consumer fan-out | Natural (`rd` by many) | Requires explicit broadcast/collective |
| Worst-case behaviour | Degrades when patterns are unanalysable | Unchanged — cost is what the source says |
| Reference implementation | None canonical [buravlev2018evaluating] | MPICH / Open MPI |

---

## 6. Agent platforms and the agent lifecycle

### 6.1 JADE

Bellifemine, Poggi & Rimassa, *JADE — A FIPA-Compliant Agent Framework* (PAAM 1999)
[bellifemine1999jade]; *JADE — A White Paper* (EXP journal, 2003) [bellifemine2003jadewhitepaper];
Bellifemine, Caire & Greenwood, *Developing Multi-Agent Systems with JADE* (Wiley, 2007)
[bellifemine2007jadebook]. JADE (Java Agent DEvelopment framework, originally from Telecom Italia Lab) is
the reference FIPA implementation and by far the most-used artefact of the whole FIPA programme.

**Runtime structure.** A JADE *platform* consists of one or more **containers**, each a JVM hosting agents,
with exactly one **main container**. Agents are Java threads; concurrency *within* an agent is modelled by
**behaviours** (`SimpleBehaviour`, `CyclicBehaviour`, `SequentialBehaviour`, `FSMBehaviour`, and the
protocol classes `AchieveREInitiator`, `ContractNetInitiator`/`ContractNetResponder`, etc.) scheduled
cooperatively (non-preemptively) by the agent's own scheduler. **This is the part practitioners actually
adopted**: a state machine per conversation, driven by message arrival.

**The three mandatory platform services** (from FIPA Agent Management, `FIPA00023`):

1. **AMS — Agent Management System.** Mandatory, exactly one per platform. It is the **naming authority
   and white pages**: it supervises access to and use of the platform, assigns and resolves agent
   identifiers (AIDs), maintains the directory of agents and **their lifecycle states**, and provides the
   lifecycle-management operations (create, suspend, resume, kill). JADE models AMS entries as
   `AMSAgentDescription`, whose state constants are the lifecycle states listed below
   [jadeapiams]. An agent is not addressable until it is registered with the AMS.
2. **DF — Directory Facilitator.** The **yellow pages**: agents `register` service descriptions
   (service type, name, ontologies, languages, properties) and others `search` with a template; supports
   subscription to registration events, and DFs can federate. This is FIPA's descendant of the KQML
   facilitator's matchmaking role (§1.4), with the routing/brokering role split out into the MTS and the
   Brokering IP.
3. **MTS / ACC — Message Transport Service / Agent Communication Channel.** Delivers ACL messages within
   and between platforms. It handles the **transport envelope** (§2.2) separately from the ACL message,
   selects an **MTP** (Message Transport Protocol — HTTP and IIOP were the standard ones), and chooses an
   ACL **representation** (String, XML, bit-efficient). JADE additionally optimises intra-platform
   delivery by passing Java object references rather than serialising.

**This three-way split — naming (AMS) / capability discovery (DF) / transport (MTS) — is clean and worth
adopting explicitly.** It is the correct decomposition, and it is notable that MPI needs only the first
(and gets it from communicators plus the process manager) because capability discovery is not a
requirement when all ranks run the same program. If AgentMPI's participants are heterogeneous, it needs a
DF-shaped component, and it should say so and cite JADE rather than inventing a name for it.

### 6.2 The agent lifecycle state machine — precise states and transitions

The states are normatively FIPA's (Agent Platform Life Cycle, in FIPA Agent Management); JADE implements
them directly and the JADE Programmer's Guide gives the clearest prose definitions
[jadeprogrammersguide]. Quoting:

| State | Definition [jadeprogrammersguide] |
|---|---|
| **INITIATED** | "the Agent object is built, but hasn't registered itself yet with the AMS, has neither a name nor an address and cannot communicate with other agents." |
| **ACTIVE** | "the Agent object is registered with the AMS, has a regular name and address and can access all the various JADE features." |
| **SUSPENDED** | "the Agent object is currently stopped. Its internal thread is suspended and no agent behaviour is being executed." |
| **WAITING** | "the Agent object is blocked, waiting for something. Its internal thread is sleeping on a Java monitor and will wake up when some condition is met (typically when a message arrives)." |
| **TRANSIT** | "a mobile agent enters this state while it is migrating to the new location. The system continues to buffer messages that will then be sent to its new location." |
| **DELETED** | "the Agent is definitely dead. The internal thread has terminated its execution and the Agent is no more registered with the AMS." |

Plus one JADE-specific extra state: **LATENT** — "JADE specific state indicating an agent waiting to be
restored after a crash of the main container" [jadeapiams]. (JADE internals also carry an `AP_IDLE` state
used alongside `AP_WAITING` in the thread logic [jadeagentsource]; it is an implementation artefact, not
part of the FIPA model.)

**The transitions.** The `Agent` class "provides public methods to perform transitions between the various
states; these methods take their names from a suitable transition in the Finite State Machine shown in
FIPA specification Agent Management" [jadeprogrammersguide]. Verified from the JADE API and source
[jadeapiagent; jadeagentsource]:

| Method | Transition | Notes |
|---|---|---|
| *(construction + AMS registration)* | INITIATED → ACTIVE | FIPA calls this transition **invoke**. Until it completes the agent has no name/address and cannot communicate. |
| `doWait()` / `doWait(long millis)` | **ACTIVE → WAITING** | "causes the agent to block, stopping all its activities until a message arrives" (or the timeout expires). |
| `doWake()` | **WAITING → ACTIVE** | "Calling `doWake()` when an agent is not waiting has no effect." In source, fires from `AP_WAITING` or `AP_IDLE`, re-activates all behaviours and notifies the message queue. |
| `doSuspend()` | **ACTIVE or WAITING → SUSPENDED** | "the original agent state is saved and will be restored by a `doActivate()` call… stops all agent activities. **Incoming messages for a suspended agent are buffered by the Agent Platform and are delivered as soon as the agent resumes.** Calling `doSuspend()` on a suspended agent has no effect." |
| `doActivate()` | **SUSPENDED → ACTIVE or WAITING** | "whichever state the agent was in when `doSuspend()` was called." |
| `doMove(Location)` | **ACTIVE → TRANSIT** | Migration; platform buffers messages and forwards to the new location. |
| `doDelete()` | **ACTIVE, SUSPENDED or WAITING → DELETED** | "thereby destroying the agent. This method can be called either from the Agent Platform or from the agent itself. Calling `doDelete()` on an already deleted agent has no effect." `Agent.takeDown()` runs just before entry to DELETED. |

Two invariants stated normatively, both of which matter to AgentMPI:

- **"an agent is allowed to execute its behaviours (i.e. its tasks) only when it is in the ACTIVE state"**
  [jadeprogrammersguide]. Execution is gated on lifecycle state, and the gate is enforced by the platform,
  not by convention.
- **Messages are never lost across state transitions.** In SUSPENDED they are buffered and delivered on
  resume; in TRANSIT they are buffered and forwarded to the new location [jadeprogrammersguide;
  jadeapiagent]. **Addressability is decoupled from executability**: an agent remains a valid destination
  while it is not running.

**Why this state machine is the most directly transferable artefact in this whole document.** It is a
*complete* answer to "what can happen to a participant," and every state has (i) a defined effect on
message delivery, (ii) defined legal transitions, and (iii) a defined authority that may trigger them
(platform, peer, or self). Contrast MPI, which historically had *no* participant lifecycle at all: ranks
exist for the duration of `MPI_COMM_WORLD` and a failure is undefined behaviour — which is exactly the gap
ULFM addresses. And contrast modern LLM harnesses, where sub-agent lifecycle is typically implicit in
process/coroutine lifetime with no notion of *suspended-but-addressable*.

Three specific design questions AgentMPI should answer, phrased against this table:

1. Is there a state in which a participant is **addressable but not executing** (JADE's SUSPENDED)? If so,
   who owns the buffer, and is it bounded? JADE's answer — platform buffers, unbounded — is the
   easy answer and it is a memory-exhaustion hazard at LLM context sizes.
2. Is WAITING distinguished from SUSPENDED? JADE's distinction is meaningful: WAITING is
   *self-imposed and message-triggered* (the agent chose to block on a receive), SUSPENDED is
   *externally imposed* (someone else stopped it). Those need different recovery semantics, and collapsing
   them — as most frameworks do — loses the ability to tell "blocked waiting on a peer" from "paused by
   the controller," which is precisely the distinction you need for deadlock detection.
3. Is there a TRANSIT analogue — a participant that has moved but whose messages must follow? For LLM
   agents this is *migration of a context/session between workers*, which is a real operational need
   (rebalancing, preemption, spot-instance eviction) and has an exact 1990s precedent.

### 6.3 Other platforms and agent-programming languages

- **Jason / AgentSpeak(L).** Rao's **AgentSpeak(L)** (ATAL/MAAMAW 1996) [rao1996agentspeak] is an
  abstract BDI agent-programming language: an agent is a set of **beliefs** plus a **plan library**, where
  plans have the form `triggering_event : context <- body`. Events are belief/goal additions and
  deletions (`+!g`, `+b`, …); the interpreter runs a reasoning cycle of event selection → relevant plan
  retrieval → applicable plan selection → intention creation → intention execution. **Jason** (Bordini,
  Hübner & Wooldridge, Wiley 2007) [bordini2007jason] is the practical interpreter, extending AgentSpeak
  with strong negation, internal actions, and — relevantly here — **speech-act-based communication**:
  Jason agents exchange messages with an illocutionary force (`tell`, `achieve`, `askOne`, …) and the
  receiver's handling is defined operationally by how the message updates its belief base or event queue.
  **This is the important move for AgentMPI to cite:** Jason gives communicative acts an *operational*
  semantics grounded in the interpreter's own state transitions rather than in an abstract modal logic —
  verifiable, because the interpreter is the definition [vieira2007formalsemantics]. It is the third
  option beside "mentalistic" and "purely mechanical."
- **JACK Intelligent Agents.** A commercial BDI platform (Agent Oriented Software, Australia) — Java
  extended with agent constructs (`agent`, `capability`, `plan`, `event`, `beliefset`), compiled to Java
  [winikoff2005jack]. Notable for shipping into real industrial and defence simulation use; its
  **capability** construct (a reusable bundle of plans, events and beliefs) is a genuine modularity idea
  with no clean modern analogue.
- **RETSINA.** Sycara et al. (Autonomous Agents and Multi-Agent Systems, 2003) [sycara2003retsina] — a
  CMU multi-agent infrastructure organised around four agent *types*: **interface agents** (user
  interaction), **task agents** (planning and problem decomposition), **information/resource agents**
  (wrapping external sources), and **middle agents** (matchmaking/brokering). RETSINA deliberately
  rejected centralised control: there is no single facilitator through which all traffic flows, and
  discovery is provided by *matchmakers* that agents may or may not use. Sycara's group also produced
  **LARKS** for capability advertisement/matching. `[UNVERIFIED: agent-type taxonomy and the LARKS
  association are from memory of the RETSINA papers; verify names against the 2003 AAMAS article.]`
- **Cougaar.** *Cognitive Agent Architecture*, from the DARPA ALP/UltraLog programmes (Helsinger, Thome &
  Wright, IEEE SMC 2004) [helsinger2004cougaar]. Java, designed for very large-scale
  (military logistics) deployments, with emphasis on **survivability, scalability and robustness under
  attack** rather than on ACL semantics. Its architecture is agent-as-container-of-**plugins** sharing a
  local **blackboard** with publish/subscribe, and inter-agent work is expressed as **tasks** flowing
  through a task network. **Cougaar is the most AgentMPI-relevant of the platforms** because it is the one
  that took HPC-style concerns (scale, fault tolerance, resource management) seriously and paid almost no
  attention to speech-act semantics — and it is also, interestingly, a *blackboard-per-agent* design
  rather than a global one, which is exactly the "windows, not one shared board" intuition.
  `[UNVERIFIED: the plugin/blackboard/task-network description is from memory of the Cougaar
  architecture documents; verify against the IEEE SMC 2004 paper.]`

---

## 7. Coordination theory (brief)

### 7.1 Joint intentions (Cohen & Levesque)

Cohen & Levesque, *Teamwork*, **Noûs 25(4):487–512, 1991** [cohen1991teamwork]; with Levesque, *On Acting
Together*, AAAI-90 [levesque1990actingtogether]; built on *Intention Is Choice with Commitment*,
Artificial Intelligence 42(2–3):213–261, 1990 [cohen1990intention].

The thesis: "Joint action by a team does not consist merely of simultaneous and coordinated individual
actions; to act together, a team must be aware of and care about the status of the group effort as a
whole" [levesque1990actingtogether]. They define a **joint persistent goal (JPG)** via **weak achievement
goals (WAG)**: agent *a_i* has a WAG relative to motivation *M* to bring about *G* if either (i) it does
not yet believe *G* holds and has *G* eventually-true as a goal, **or (ii) it believes *G* is true, will
never be true, or is irrelevant (*M* false), but has a goal of making the status of *G* mutually believed
by all team members**. A team has a JPG iff they mutually believe *G* is currently false, mutually believe
they all want *G* eventually true, and until they come to mutually believe *G* is true / will never be
true / *M* is false, they each hold *G* as a WAG relative to *M* [cohen1991teamwork; jennings1996commitments].

**Clause (ii) is the whole point and it is a protocol requirement, not a philosophical one.** Discovering
that a goal has succeeded, become impossible, or become irrelevant creates an **obligation to notify the
team**. Cohen & Levesque state this consequence explicitly: "An important consequence of the theory is the
types of communication among the team members that it predicts will often be necessary"
[levesque1990actingtogether]. **A formal theory of teamwork derives, as a theorem, that you need a
mandatory termination-broadcast primitive.** Cite this when motivating AgentMPI's completion/abort
notification: it is the strongest available argument that "tell everyone when you stop, and why" is not
mere engineering hygiene but a correctness condition for joint activity.

The documented weakness, from the COM-MTDP analysis: "joint intentions theory prescribes that team members
attain mutual beliefs in key circumstances, but **it ignores the cost of attaining mutual belief (e.g.,
via communication)**" [pynadath2002commtdp]. Note the shape of that critique — it is the *same* critique
HPC makes of any coordination scheme that assumes free synchronisation, and Pynadath & Tambe's COM-MTDP
framework was built precisely to reason about communication cost within team theory. This is a natural
bridge citation between the agent-teamwork literature and a performance-oriented protocol paper.

### 7.2 SharedPlans (Grosz & Kraus)

Grosz & Kraus, *Collaborative Plans for Complex Group Action*, **Artificial Intelligence 86(2):269–357,
1996** [grosz1996sharedplans]; originating in Grosz & Sidner's *Plans for Discourse* (1990)
[grosz1990plans].

SharedPlans is "based on a mental-state view of plans. It was originally conceived to provide the basis
for modeling the structure of discourse… [and] subsequently generalized… to accommodate more than two
participating agents and to support the construction of teams of collaboration-capable agents"
[grosz1999planningacting]. It "provides a revised and expanded version of SharedPlans… to handle cases in
which a single agent has only partial knowledge," and "the new definitions also allow for **contracting out
certain actions**" [grosz1996sharedplans]. Structurally, for each subgoal of *G* the team must mutually
believe that some member is capable of the action, intends to achieve the subgoal, and intends to achieve
*G* *by* performing the subgoal — plus mutual belief about how subgoals generate the parent goal
[jennings1996commitments].

Two points to carry into the paper:

- **Grosz & Kraus explicitly disclaim the logic as an implementation:** "The formalization is not [intended
  as an algorithm]. Rather, it is intended to be used as **a specification for agent design**. In this
  role, the model constrains certain planning processes… and **provides guidance about the information
  that collaborating agents must establish for themselves and communicate with one another**"
  [grosz1996sharedplans]. This is a more modest and more defensible use of mentalistic logic than FIPA's:
  as a *derivation tool for figuring out what must be communicated*, not as a conformance criterion.
  **AgentMPI can borrow this stance wholesale** — use BDI theory to decide which messages the protocol must
  provide, then specify those messages operationally. That is the fair synthesis of the two traditions and
  it neutralises the objection that a mechanism-thin protocol is theory-free.
- Downstream, Tambe's **STEAM** "incorporated some elements of SharedPlans… used to build systems for
  Robocup Soccer tournaments and for military simulations" [grosz1999planningacting], demonstrating the
  theory *did* transfer to working systems — a point of fairness the related-work section should concede.

### 7.3 Partial Global Planning (Durfee & Lesser)

Durfee & Lesser, *Partial Global Planning: A Coordination Framework for Distributed Hypothesis Formation*,
IEEE Transactions on Systems, Man and Cybernetics 21(5):1167–1183, 1991 [durfee1991pgp]; developed in the
**Distributed Vehicle Monitoring Testbed (DVMT)**, the distributed successor to HEARSAY-II
[lesser1983dvmt]. Later generalised as **Generalized PGP** in Decker & Lesser's TÆMS work
[decker1995taems]. `[UNVERIFIED: exact volume/page numbers for durfee1991pgp, lesser1983dvmt and
decker1995taems are from memory; verify.]`

The mechanism: each node builds **local plans**; nodes exchange *abstractions* of those plans; a node
merges received abstractions into a **partial global plan (PGP)** — a partial view of the group's activity
— detects redundancy and harmful interactions, and proposes reordering or reassignment back to the others.
Coordination is thus achieved by **exchanging plan abstractions rather than sharing state**, and every
node's global view is explicitly *partial* and possibly inconsistent.

**This is the single most important entry in §7 for AgentMPI's philosophical claim.** PGP came from
Victor Lesser's own group — the group that built the canonical blackboard system — and its central design
decision is to **replace shared state with explicit exchange of abstracted plans**, accepting inconsistent
local views as the price of scalability. The trajectory HEARSAY-II → DVMT → PGP is a documented, decade-long
migration by the field's own leaders from global shared memory to explicit message passing with partial
knowledge. Nothing else in this document supports the paper's thesis so directly, and it is a matter of
publication record.

### 7.4 Market / auction-based task allocation

The Contract Net lineage (§3) matured into explicit market mechanisms. Key entry points: Dias, Zlot,
Kalra & Stentz, *Market-Based Multirobot Coordination: A Survey and Analysis*, Proceedings of the IEEE
94(7):1257–1270, 2006 [dias2006marketsurvey]; Gerkey & Matarić's task-allocation taxonomy (IJRR 2004)
classifying problems as single/multi-task robots × single/multi-robot tasks × instantaneous/time-extended
assignment [gerkey2004taxonomy]; Wellman's market-oriented programming [wellman1993marketoriented];
and sequential-auction / combinatorial-auction allocation with bundle bids [koenig2006auctions].
`[UNVERIFIED: volume/page details for gerkey2004taxonomy, wellman1993marketoriented and koenig2006auctions
are from memory; verify.]`

The transferable results are quantitative rather than conceptual: greedy sequential auctions give bounded
approximation ratios for some team objectives; combinatorial bids improve solution quality at
super-polynomial winner-determination cost; and auction rounds cost O(bidders) messages per task, which is
the reason large systems batch or cluster tasks. For AgentMPI, the relevance is that **market-based
allocation has a known message-complexity, and it is not free** — an argument for making bidding an
optional layer above the protocol rather than a protocol primitive.

### 7.5 DCOP

Distributed Constraint Optimisation formalises coordination as an optimisation problem over variables held
by different agents with shared constraints. Landmarks: **Adopt** (Modi, Shen, Tambe & Yokoo, *Asynchronous
distributed constraint optimization with quality guarantees*, Artificial Intelligence 161(1–2):149–180,
2005) [modi2005adopt], which achieves asynchronous, complete search with bounded-error guarantees; and
**DPOP** (Petcu & Faltings, IJCAI 2005) [petcu2005dpop], a dynamic-programming approach on a DFS
pseudo-tree that uses a **linear number of messages** but messages whose size is exponential in the
induced width. `[UNVERIFIED: Adopt's exact volume/pages and DPOP's IJCAI page numbers are from memory;
verify.]`

**The DPOP/Adopt contrast is a genuinely useful citation for an HPC audience** because it is a clean
message-count-versus-message-size trade-off with proven bounds — exactly the currency HPC protocol design
trades in, and a rare point where the multi-agent literature offers something an HPC reader will
immediately respect. It also makes the general point that once coordination is posed as an explicit
distributed algorithm rather than as shared mental state, you get complexity results; the mentalistic
tradition never produced any.

---

## 8. Lessons for AgentMPI

### 8.1 Mechanisms AgentMPI should adopt, or already resembles

**(1) Content opacity with declared descriptors — from KQML's three layers.** KQML's best idea was that
routing, delivery and analysis operate on the *envelope*, with `:language` and `:ontology` as declared
metadata so that "KQML implementations [can] analyze, route and properly deliver messages even though
their content is inaccessible" [kqmlspec1993]. This is structurally identical to MPI's buffer + datatype
descriptor. **Adopt it as the explicit layering principle** and cite KQML as the precedent: AgentMPI
carries a payload descriptor, not a payload semantics. Note honestly the cost Cohen & Levesque identified:
opacity "prevents the content from being checked for compatibility with the speech act type"
[cohen1995communicative].

**(2) An in-band deadline on every request — from FIPA's `reply-by` and Contract Net's expiration time.**
Both protocols make the deadline a *message parameter*, and both give a *normative rule for late
arrivals*: FIPA rejects late proposals "with the given reason that the proposal was late"
[fipa2002contractnet]; Smith's task announcements carry an explicit expiration time
[smith1980contractnet]. Modern harnesses keep timeouts client-side and invisible to the peer, which makes
distributed timeout reasoning impossible. **AgentMPI should carry the deadline on the wire and specify
late-arrival behaviour normatively.**

**(3) Distinguish "won't", "can't", "busy", and "tried and failed" — from FIPA `refuse`/`failure` and
Smith's immediate response bids.** FIPA's `failure` means the act "was considered feasible by the sender,
but was not completed," entitling the receiver to conclude the action was *feasible* and *not done*
[fipa2002cal] — semantically distinct from `refuse`. Smith went further with immediate response bids
distinguishing *not eligible* from *eligible but busy*, precisely so "the manager can take a more
appropriate course of action": reissue vs. wait [smith1980contractnet]. **Four distinct outcomes with four
distinct correct recoveries.** AgentMPI's error taxonomy should preserve all four; collapsing them into
"error" destroys retry and backpressure logic. This is the highest-value, lowest-cost item on this list.

**(4) Graceful degradation to a cheap common case — from Contract Net's directed contracts.** Smith
provides a full path from auction → directed contract (no announcement, no bids) → plain request/response,
justified as reducing "communication for transactions that do not require the complexity of negotiation"
[smith1980contractnet]. **AgentMPI must have a two-line common case**, as MPI has `MPI_Send`/`MPI_Recv`.
KQML and FIPA had no cheap case, and that alone plausibly explains a large fraction of their
non-adoption.

**(5) Declared write-extent before access — from HEARSAY-II's response frame.** Before a knowledge source
runs, it declares "a stylized description of the blackboard modifications that the KS action is likely to
perform" [erman1980hearsay]. For a one-sided window this is exactly an access declaration submitted in
advance so the runtime can order or exclude conflicting operations. **If AgentMPI's window supports
declared-extent epochs, `[erman1980hearsay]` is the ancestor citation** — and it is a far more interesting
one than a generic nod to blackboards.

**(6) Separate storage from control — from GBB.** GBB split the blackboard database subsystem from the
control shell, so that the blackboard implementation could change "without changing the [rest of the
system]" [corkill1986gbb], and it established that efficient associative retrieval requires the shared
structure to be **dimensioned and indexed in advance** (ordered and enumerated dimensions)
[corkill1986gbb; wikiblackboard]. Both lessons apply directly: a window's addressing structure is a
first-class design object, and unrestricted content-based lookup over it cannot be made fast.

**(7) A complete participant lifecycle with defined message-delivery semantics per state — from
FIPA/JADE.** The six-state machine (INITIATED / ACTIVE / SUSPENDED / WAITING / TRANSIT / DELETED) with
`doWait`/`doWake`/`doSuspend`/`doActivate`/`doMove`/`doDelete` transitions [jadeprogrammersguide;
jadeapiagent] is the most directly transferable artefact in this document. Adopt three of its properties
specifically: **behaviours execute only in ACTIVE**; **messages are buffered (never dropped) in SUSPENDED
and forwarded in TRANSIT**; and **WAITING (self-imposed, message-triggered) is distinct from SUSPENDED
(externally imposed)** — a distinction you need for deadlock detection and which most modern frameworks
collapse.

**(8) Three-way service split: naming / capability discovery / transport — from JADE's AMS, DF and MTS.**
Naming authority, yellow pages, and message transport are independent concerns with independent failure
modes. MPI needs only naming because ranks are homogeneous; **heterogeneous agents need the DF, and
AgentMPI should name that component rather than reinvent it implicitly.**

**(9) Termination and impossibility must be broadcast, as a correctness condition — from joint
intentions.** The weak-achievement-goal clause makes notifying the team of success, impossibility, or
irrelevance a *derived obligation* of joint activity [cohen1991teamwork; levesque1990actingtogether], and
Cohen & Levesque note the theory *predicts* what communication is necessary. Cite this to justify
mandatory completion/abort notification — with the honest caveat that the theory "ignores the cost of
attaining mutual belief" [pynadath2002commtdp], which is exactly the cost AgentMPI must budget.

**(10) Coordinate by exchanging abstractions, not by sharing state — from Partial Global Planning.** PGP
exchanges *plan abstractions* and tolerates partial, possibly inconsistent local views
[durfee1991pgp]. Combined with Smith's task-abstraction-then-full-spec-on-award pattern
[smith1980contractnet], the rule for AgentMPI is: **broadcast abstractions, unicast payloads.** At LLM
context sizes this is a first-order cost decision, not a stylistic one.

**(11) Closed groups, or an explicit deadline — pick one, from Contract Net vs. MPI communicators.** A
fan-in over responses is only well-defined if either the participant set is closed (MPI's answer) or the
protocol carries a deadline plus a late-arrival rule (FIPA/Smith's answer) [fipa2002contractnet;
smith1980contractnet]. There is no third option, and the choice should be stated explicitly and early.

**(12) Ship a reference implementation.** Not a mechanism, but the empirically decisive one — see §8.2(6).

### 8.2 Why KQML and FIPA-ACL failed to achieve MPI-like adoption

I take this seriously as a question with multiple genuine causes, and I want to resist the temptation to
reduce it to "they were too philosophical."

**(1) The conformance condition was unobservable — the deepest cause.** Wooldridge's formulation is the
one to quote: a semantics is valuable only if "conformance or otherwise to the semantics could be
determined by an independent observer," and FIPA's is not, because L_S is a multimodal logic over beliefs
and desires and "we currently have very little idea about systematic ways of attributing such mentalistic
descriptions to programs" [wooldridge2000semantic]. His standards-level conclusion is the load-bearing
sentence: "if there is no way of determining whether or not a system that claims to conform to a standard
does indeed conform to it, then the value of the standard itself must be questioned"
[wooldridge2000semantic]. **Without an observable conformance condition there can be no test suite, no
conformance badge, and no way to assign blame when two implementations disagree — which means there can be
no interoperability ecosystem, regardless of how good the specification is.** MPI's conformance conditions
are all observable at the interface: did the buffer contain the right bytes, did the call return, do all
ranks agree on the collective's result. That difference, not elegance, is why one standard produced dozens
of interoperable implementations and the other did not.

**(2) The semantics was also insufficiently discriminating, which cost them even the cooperative
implementers.** This is the underrated point. Model-checking FIPA's Contract Net semantics found that the
FP/RE pair for `accept-proposal` "could also hold in other speech-acts such as `tell` and [does] not
distinguish an `accept-proposal` from them," and that for `cfp` the preconditions posit intentions that are
"premature… [and do] not leave the possibility for refusal or rejection," concluding "the rest of the
semantics for `cfp` is so complicated that its meaning is unclear" [tanetal2004verifying]. So a
well-intentioned implementer consulting the normative semantics could not use it to decide what to send.
**The formal semantics failed on its own terms, not merely on verifiability grounds.**

**(3) There was no cheap common case; every message carried an ontological commitment.** To send a
FIPA-ACL message properly you choose a communicative act from 22, a content language, an ontology, an
interaction protocol, and a conversation id. Two FIPA-compliant agents that do not share an ontology still
cannot usefully talk — so compliance bought interoperability of *envelopes* while the hard part, agreeing
on domain vocabulary, remained per-deployment. **Compliance therefore delivered far less than it appeared
to promise, while costing far more than `MPI_Send`.** MPI's entry cost is two calls and a datatype; the
protocol demands no agreement about meaning whatsoever, and the semantic agreement happens in the
application where it belongs.

**(4) KQML specifically failed on ordinary protocol engineering, before any of the philosophy mattered.**
The documented list [wooldridge2009intro; comp310acl]: the performative set was **fluid** across drafts;
implementations were **not interoperable**; **transport mechanisms were not precisely defined**; semantics
were not rigorous and "ambiguity resulted in impairing interoperability"; there were **no commissives**;
and the performative set was "arguably ad-hoc and overly large." Note that KQML never fixed a single wire
format — the communication-layer fields lived in the body over TCP and in mail headers over email
[kqmlspec1993]. **A protocol without a normative wire format is not a protocol.** FIPA fixed nearly all of
this (three normative encodings, a transport envelope, a tidy 22-act library composed from two primitives,
real commissives), **and still failed** — which is precisely why cause (1) must be the primary explanation
rather than sloppiness.

**(5) The problem was solved away by adjacent stacks with larger constituencies.** Naming, transport,
envelopes, typed request/response and discovery were delivered by WSDL/SOAP/UDDI and then HTTP+JSON to
audiences orders of magnitude larger, with no ontological commitments. FIPA's own 2005 vote acknowledged
this in as many words: "agent technology needs to work and integrate with non-agent technologies"
[fipaieee2005]. A standard that must be adopted *wholesale* competes badly against one that can be adopted
*incrementally*.

**(6) No canonical, free, high-quality reference implementation at the right moment.** MPI shipped with
MPICH, which vendors forked and tuned; the specification and a working implementation arrived together.
KQML had research prototypes and a fluid vocabulary. FIPA's reference-quality free implementation, JADE,
arrived in 1999 [bellifemine1999jade] — two years after FIPA97 and into a market the web-services stack
was already taking. The same diagnosis is made explicitly of tuple spaces: the paradigm is "one of the
least used, despite… being intuitive, easy to understand, and easy to use," because "the lack of a
reference implementation for this paradigm has prevented its wide spreading" [buravlev2018evaluating].
**Across KQML, FIPA and Linda the common failure is the absence of a canonical implementation, and this is
the one lesson that constrains AgentMPI's release strategy rather than its design.**

**(7) The standardised unit was wrong: they standardised *meaning*, when the durable thing to standardise
is *mechanism*.** Meaning is domain-specific and changes with every application; mechanism is universal
and changes rarely. By standardising illocutionary force and mentalistic preconditions, FIPA bound its
specification to a theory of agency that was itself contested and evolving; when the BDI research
programme moved on, the standard was stranded. MPI standardised transfer, completion, naming and
collective structure — facts about *moving bytes between processes* that were as true in 2024 as in 1994.
**Standardise what will not change.**

#### What they got right that modern systems have lost

Fairness requires this list, and it is not short. Each item is a live regression in current LLM agent
frameworks:

- **The four-way outcome taxonomy** (`refuse` / `failure` / not-eligible / busy) [fipa2002cal;
  smith1980contractnet] — now collapsed into a single "error", destroying principled retry and
  backpressure.
- **In-band deadlines with normative late-arrival semantics** (`reply-by`; expiration time)
  [fipa2002acl; fipa2002contractnet] — now client-side and invisible to the peer.
- **Three distinct mediation regimes**: broker (relay both ways), recommend (name resolution then direct
  contact), recruit (third party replies directly to the original sender) [finin1994kqml] — modern
  harnesses typically offer only "orchestrator relays everything," which is strictly less expressive and
  strictly worse for latency and locality.
- **Explicit acknowledgement separate from completion** (`agree` before `inform-done`/`failure`)
  [fipainteractionprotocols] — the ownership/completion distinction that MPI expresses as local vs. remote
  completion.
- **Requester-specified response schema** (Smith's bid specification) [smith1980contractnet] — now ad-hoc
  prompt text rather than a protocol field.
- **Closed per-state response alphabets** with loop-prevention invariants ("it is not permissible to
  respond to a `not-understood` message with another `not-understood` message") [fipainteractionprotocols]
  — modern response spaces are open-ended, which is why their state machines cannot be analysed.
- **A complete, documented participant lifecycle** in which suspended participants remain addressable and
  their messages are buffered rather than dropped [jadeprogrammersguide] — modern sub-agent lifecycle is
  usually implicit in process lifetime.
- **Streaming partial results with explicit flow control** (interim reports, with the contractor
  suspending until instructed to continue) [smith1980contractnet] — we stream tokens but without
  backpressure.
- **Separation of transport envelope from application message** [fipa2002acl] — necessary for relays,
  gateways and audit, and frequently absent today.
- **Binding commitments as a protocol-level (not mentalistic) notion**: "the proposals are binding on the
  Participant, so that once the Initiator accepts the proposal, the Participant acquires a commitment to
  perform the task" [fipa2002contractnet].

#### The synthesis to argue in the paper

The clean statement of the position, which I believe is both defensible and fair:

> FIPA's failure was not that it had a semantics; it was that its semantics was stated over *private*
> state. The verifiable alternative was already identified in the same literature — **social-commitment
> semantics** [singh1998socialsemantics], which defines an act by the public obligation it creates and
> therefore "doesn't stop an agent lying, but… allows you to detect when it does" [comp310acl] — and,
> independently, **operational semantics grounded in an interpreter's own state transitions**, as in
> Jason/AgentSpeak [bordini2007jason; vieira2007formalsemantics].

AgentMPI should therefore claim the *mechanism-level* path **not** as a rejection of semantics but as a
choice of **which semantics are observable**: transfer, completion, naming, group membership, deadlines
and outcome classes are all externally checkable; belief and intention are not. And it should adopt
Grosz & Kraus's stance on the role of the mentalistic theories: use them as "a specification for agent
design" that "provides guidance about the information that collaborating agents must establish for
themselves and communicate with one another" [grosz1996sharedplans] — i.e. to *derive the required
message set* — and then specify those messages operationally. That framing lets the paper use the BDI
literature as a design resource while declining its conformance model, which is a stronger and more honest
position than dismissing forty years of work as ontology-obsessed.

One caution against overclaiming: **MPI's own adoption owed much to a homogeneity that AgentMPI will not
enjoy.** MPI ranks run the same program, are launched together, and are trusted equally, which is why MPI
needs no capability discovery, no lifecycle, and no commitment model. LLM agents are heterogeneous,
independently deployed, mutually untrusted and dynamically discovered — the conditions FIPA was actually
designed for. **AgentMPI cannot be MPI; it can be MPI's discipline applied to FIPA's problem.** That
formulation concedes what must be conceded and is, I think, the sharpest available version of the paper's
central claim.

---

<!-- APPEND-POINT -->
