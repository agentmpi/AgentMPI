# Dossier 01 — MPI: History, Standardization Process, and Design Philosophy

**Prepared for:** *AgentMPI: A Message Passing Interface for Multi-Agent Systems*
**Role:** source of truth for the Background and Related Work sections. Every MPI-historical claim in the paper should be traceable to a numbered claim here.
**Companion BibTeX:** `refs/01-history.bib`

## 0. How to read this dossier

Claims are cited inline as `[bibkey, locator]`. Where I could not verify something to the
standard a camera-ready HPC paper requires, the claim is tagged **`[UNVERIFIED]`** with a note on
what would settle it. Section 5 is explicitly *my analysis*, not history.

On length: the dossier runs about 8,800 words, of which roughly 2,900 are verbatim quotation from
primary sources, quoted at length deliberately so the paper can lift them without re-deriving
provenance. The connective and analytical prose is about 5,900 words. If the total must come down,
cut §1's per-system failure analyses and §6 first; do not cut the quotations in §3.3 and §3.4,
which are what the paper's central argument rests on.

Two textual hazards will affect anyone re-checking this work. The circulating PDFs of MPI-1.0 and
MPI-1.1 lose their bibliography cross-references in text extraction, so the standard's own
citations appear as `[?]`. And several convenient survey PDFs — notably the Wiley Encyclopedia
chapter by Dongarra, Fagg, Hempel and Walker [@dongarra2000messagepassing] — survive online only
as OCR in which *digits have been stripped*: "p4" reads as "p", "CM-5" as "CM". I have used that
source only for qualitative claims, taking every date and number from a source where the digits
survive.

---

## 1. Prehistory, 1978–1992: what MPI was assembled from, and what each predecessor failed to solve

MPI's designers were explicit that the message-passing model did not originate with them. The
MPI-1.1 standard opens by saying that "in designing MPI we have sought to make use of the most
attractive features of a number of existing message passing systems, rather than selecting one of
them and adopting it as the standard," then names its influences: work at IBM T. J. Watson,
Intel's NX/2, Express, nCUBE's Vertex, p4, PARMACS, Zipcode, Chimp, PVM, Chameleon and PICL
[@mpiforum1995mpi11, §1.1, p. 1]. That is the single most important methodological statement in
the MPI corpus; it recurs in §3.1.

### 1.1 The theoretical substrate: CSP and Occam

Hoare's *Communicating Sequential Processes* [@hoare1978csp] argued "that input and output are
basic primitives of programming and that parallel composition of communicating sequential
processes is a fundamental program structuring method" [@hoare1978csp, p. 666], supplying the
vocabulary — processes with private state, communication as the only coupling — that message
passing inherited wholesale. Occam, on Inmos transputers, made CSP executable: processes
communicated over statically declared named channels using *only* synchronous, blocking send and
receive [@dongarra2000messagepassing].

**What CSP/Occam failed to solve.** Rigidity: fully synchronous channels forbid overlap of
computation and communication and make many natural algorithms deadlock-prone. The compensating
benefit — Occam programs could be proved correct before execution, which underwrote much early
ESPRIT work [@dongarra2000messagepassing] — is real. MPI kept CSP's process/channel *semantics*
while providing four send modes (standard, buffered, synchronous, ready) and non-blocking variants
of each, making synchrony a per-call choice rather than a language-level commitment.

### 1.2 Vendor-specific systems: CrOS, NX/2, Vertex, EUI, CMMD

The Caltech Hypercube's Crystalline Operating System (CrOS) addressed processes by physical node
and allowed communication only with topological neighbours or the host
[@dongarra2000messagepassing]. Intel's NX, and later NX/2 on the iPSC and Paragon
[@pierce1988nx2], broke that coupling: processes got integer identifiers independent of topology,
messages could be tagged, and non-blocking operations returned message identifiers (*mids*)
testable for completion later. nCUBE's Vertex [@ncube1990vertex] and IBM's External User Interface
(EUI, from the Vulcan project that became the SP series) occupied the same design space; EUI added
logical process groups addressable by a single group ID, with collective operations over them.
Thinking Machines' CMMD for the CM-5 layered a point-to-point library, virtual channels and a
cooperative-communications library over an Active Message Layer [@dongarra2000messagepassing].

**What the vendor systems failed to solve.** Portability, obviously — but the semantic failures are
more instructive. The original NX did not permit filtering a receive by *both* sender identity and
message type at once (the workaround was to smuggle the sender's ID into the type field), and it
silently truncated messages that overran the receive buffer. EUI fixed the two-dimensional
selection problem and introduced the two-phase status-handle discipline for non-blocking operations
that MPI later adopted [@dongarra2000messagepassing]. These are the small semantic defects a
standard exists to eliminate once, everywhere.

### 1.3 The portability APIs: p4, PARMACS, Chameleon, Express, PICL, Zipcode, PVM

p4 (Butler and Lusk, Argonne) descended from Fortran monitor macros called MonMacs, written for the
Denelcor HEP and processed by the Unix `m4` preprocessor; the name comes from the book *Portable
Programs for Parallel Processors* [@butler1994p4; @dongarra2000messagepassing]. The mature system
was procedure-based, spanned shared- and distributed-memory machines and clusters, gave the user
direct access to buffers, and offered collective operations including a user-defined `p4_global_op`
— but had *no* non-blocking or locally-blocking calls at all [@dongarra2000messagepassing]. Its
most consequential legacy was as plumbing: p4 and Chameleon [@gropp1993chameleon] were the
substrates on which MPICH was built [@gropp1996mpich].

PARMACS (Hempel, GMD) grew from the Argonne/GMD macros [@bomans1990argonne] and became the *de
facto* European standard, adopted by the ESPRIT Genesis, PPPE and RAPS projects [@calkin1994parmacs;
@dongarra2000messagepassing]. Its distinctive contribution was **virtual topologies**: a mapping
between process addresses and a torus/grid of up to four dimensions or an arbitrary graph, so a
program could say "send to my successor" rather than compute an absolute address
[@dongarra2000messagepassing]. MPI's process-topologies chapter is PARMACS's idea
[@mpiforum1995mpi11, §1.1].

Express (ParaSoft) commercialised CrOS, with topology-aware collective libraries sufficient that a
well-structured application could be written with *no explicit send or receive calls at all*
[@parasoft1992express; @dongarra2000messagepassing] — the highest-level predecessor most nearly
eliminated the primitive it was built on. **Zipcode** (Skjellum et al., from Caltech in July 1988)
is the most important predecessor here and is treated separately in §3.3.

PVM (Sunderam at Emory, with Geist and Dongarra at ORNL and Tennessee) was, when the MPI process
began, the most widely used message-passing system in the world [@sunderam1990pvm; @geist1994pvm].
Its design centre was unlike everyone else's: heterogeneity (XDR conversion across mixed
architectures), *dynamic* membership (hosts could be added, removed or simply fail while the rest
continued; tasks could be spawned and killed at will), and process-level failure detection and
recovery [@sunderam1990pvm]. PVM sends never blocked, because the system buffered everything
[@dongarra2000messagepassing] — a decision MPI explicitly reversed, for reasons analysed in §3.4.

**What the portability APIs failed to solve.** Three things. (i) They multiplied: portability
across hardware was bought at the cost of incompatibility across APIs, so a program written
against Express could not link a library written against PARMACS
[@dongarra2000messagepassing]. (ii) Performance: PVM chose heterogeneity over speed, and its
per-message buffer management cost enough on MPPs that the authors later added
`pvm_psend`/`pvm_precv` to bypass it [@dongarra2000messagepassing]. (iii) With the sole exception
of Zipcode, none made it safe to *write a library*. That is the argument of §3.3.

### 1.4 Linda: the blackboard ancestor

Gelernter's Linda [@gelernter1985linda] is the one system here that is not message-passing at all.
Processes do not address one another; they add tuples to a shared, associatively addressed "tuple
space" (TS) with `out()`, remove them with `in()`, and read them non-destructively with `rd()`,
matching by template. Communication is *generative*: "until it is explicitly withdrawn, the tuple
generated by A has an independent existence in TS. A tuple in TS is equally accessible to all
processes within TS, but is bound to none" [@gelernter1985linda, p. 81]. The consequences Gelernter
names are **time- and space-uncoupling**: the consumer need not exist when the tuple is created,
and neither party ever learns the other's address [@gelernter1985linda].

This is the crucial ancestor for AgentMPI, because *this is what contemporary multi-agent
frameworks actually are*: shared scratchpads, blackboard memories, shared vector-store "context"
and topic-subscription buses are tuple spaces with worse matching semantics. Linda was elegant,
became competitive for medium-to-large payloads once compile-time analysis of tuple fields was
added [@dongarra2000messagepassing], and *lost*. Williamsburg placed it in the "higher-level
abstractions" tier of the Onion Skin Model and put the standard at an *intermediate* level, below
Linda and above raw channels [@walker1992standards, §3].

**What Linda failed to solve.** Locality and cost transparency. A tuple-space operation has no
statically visible cost, so the programmer cannot reason about communication volume and the
implementation cannot pick an algorithm without global analysis. This is precisely the objection
AgentMPI should reuse against shared-scratchpad agent architectures, where a "read from the
blackboard" has an unbounded and invisible token cost.

---

## 2. The standardization process, 1992–2025

### 2.1 Williamsburg, April 1992

The First CRPC Workshop on "Standards for Message Passing in a Distributed Memory Environment"
was held **29–30 April 1992** at the Hilton Conference Center, Williamsburg, Virginia, sponsored
by the Center for Research on Parallel Computation, and attracted **68 invited participants**
across 19 talks in 5 sessions plus a panel [@walker1992standards, abstract, §1, Appendix A]. The
proceedings summary is **ORNL/TM-12147**, by David W. Walker, dated **August 1992**
[@walker1992standards].

A correction to the framing in the AgentMPI project brief: the workshop was **organised by Jack
Dongarra and David Walker**, not by Kennedy. Ken Kennedy of Rice moderated the closing panel and
"encouraged the message passing community to set up a working group in order to carry the
standardization process forward" [@dongarra2000messagepassing; @walker1992standards, Appendix A].
The distinction matters if the paper credits organisers by name.

Two findings are load-bearing. First, the **Onion Skin Model**: the workshop reasoned explicitly
about *at what level* to standardise — from channel-addressed primitives at the bottom, through
process-addressed systems (NX, Vertex, Express, PARMACS), to Linda, MetaMP and Shared Objects at
the top — and concluded that "the hardware of different distributed memory computing systems is
sufficiently varied that it is difficult to impose a low-level standard that is efficient on all
machines. Therefore, it is more appropriate to define a standard at an intermediate level, and to
implement this as efficiently as possible on each machine" [@walker1992standards, §3]. Second,
**message contexts were in the requirements list on day one**: "Often a parallel program divides
naturally into different computational phases. Message contexts can be used to prevent nonblocking
messages from different phases interfering with one another without the need for a time-consuming
barrier synchronization between phases" [@walker1992standards, §3.1].

The report closes by forming a Working Group of about 30 people with **Dongarra as Chair and
Walker as Executive Director**, and states the method: "Rather than taking one of the existing
message passing systems and anointing it as the standard, the intent is to settle on the
functional and semantic requirements (drawing where appropriate on existing systems for guidance),
and then to define the detailed syntax of the standard" [@walker1992standards, §5]. The group
expected to meet "about once every 4 to 6 *months*" and to finish in about 12 months
[@walker1992standards, §5]. It actually met every six *weeks*; the acceleration is itself a
finding.

### 2.2 Formalisation and cadence, 1992–1994

A preliminary draft, "MPI1", was put forward by **Dongarra, Hempel, Hey and Walker** in November
1992 and revised in February 1993 as ORNL/TM-12231 [@mpiforum1995mpi11, §1.1; @dongarra1993proposal];
it covered point-to-point communication only, had no collectives, and was not thread-safe.

**Note on a common error.** The MPI Forum was *not* constituted at Supercomputing '92 as such. The
decisive event was a **meeting of the MPI working group in Minneapolis in November 1992**, at
which it was decided "to place the standardization process on a more formal footing, and to
generally adopt the procedures and organization of the High Performance Fortran Forum"
[@mpiforum1995mpi11, §1.1]. Minneapolis hosted SC'92 and the working-group meeting took place
during the conference [@dongarra2000messagepassing], so the two statements reconcile — but "the
Forum was formed at SC'92" is a compression. Use the primary wording.

The **procedural rules**, from the Forum's own newcomers' document [@mpiforum1993newcomers]:

- Meetings were **2.5 days, starting Wednesday afternoon, in Dallas**, roughly **6 weeks apart**,
  under "loosely-enforced Robert's Rules of Order."
- Organisations were **limited to 2 attendees each** (not strictly enforced) and were "asked to
  commit to having the same attendee at every meeting" to avoid spending meeting time on "remedial
  education."
- **"Each organization (school, company, lab) gets one vote — note this is on an organization
  basis, not a person."**
- **"An organization was eligible to vote if it had attended 2 of the last 3 meetings, counting the
  current meeting (i.e. you could attend every other meeting and still vote; you could not vote at
  your first meeting)."** Not enforceable at the first two meetings.

A further rule, reported in Huss-Lederman's retrospective, is that items **had to be approved at
two separate meetings** [@husslederman_mpiprocess]. **`[UNVERIFIED]`** — this comes only from a
slide deck of uncertain provenance; confirmation would be the Forum minutes at
`netlib.org/mpi/minutes-93/`, which I did not exhaustively read. Do not state the two-reading rule
for MPI-1 as fact without checking those minutes. (It is uncontroversially true of the modern
Forum.)

Participation was "about 60 people from 40 organizations mainly from the United States and Europe"
[@mpiforum1995mpi11, §1.1], across seven meetings between January and September 1993
[@dongarra2000messagepassing]. The draft was presented at Supercomputing '93 in Portland, followed
by a public comment period; a January 1994 meeting at INRIA Sophia Antipolis brought in the
European community and produced `MPI_Pack`/`MPI_Unpack`, the buffered send mode, portable timers,
error classes and a guaranteed tag range — all for compatibility with PVM and PARMACS
[@dongarra2000messagepassing].

MPICH, maintained by Lusk and Gropp at Argonne in lockstep with the evolving draft, was complete
and portable on the day MPI-1.0 was released — in contrast to HPF, whose implementations were "only
now (February 1996) becoming available, whereas a large community has been using MPI for over a
year" [@gropp1996mpich].

### 2.3 Version chronology

The canonical list, taken verbatim from the front matter of the standard itself
[@mpiforum2021mpi40; @mpiforum2025mpi50, §2]:

| Version | Date | Character |
| --- | --- | --- |
| MPI-1.0 | **May 5, 1994** | first release |
| MPI-1.1 | **June 12, 1995** | clarifications, minor corrections |
| MPI-1.2 | **July 18, 1997** | published inside the MPI-2 document |
| MPI-2.0 | **July 18, 1997** | I/O, RMA, dynamic processes, C++ bindings |
| MPI-1.3 | **May 30, 2008** | consolidation of 1.1 + 1.2 + errata |
| MPI-2.1 | **June 23, 2008** | merged document |
| MPI-2.2 | **September 4, 2009** | clarifications + seven new routines |
| MPI-3.0 | **September 21, 2012** | nonblocking collectives, new RMA, neighbourhood collectives, MPIT |
| MPI-3.1 | **June 4, 2015** | clarifications, minor extensions |
| MPI-4.0 | **June 9, 2021** | large counts, persistent collectives, partitioned communication, Sessions, error-handling |
| MPI-4.1 | **November 2, 2023** | clarifications and minor extensions |
| MPI-5.0 | **June 5, 2025** | **standard ABI** |

**MPI-5.0 verification.** MPI-5.0 was ratified on **5 June 2025** [@mpiforum2025bof]. Its headline
feature is a standard **Application Binary Interface**: "The largest change is the addition of a
standard Application Binary Interface (ABI) to allow interoperability of different
implementations" [@mpiforum2025mpi50, §2]. The ABI is versioned independently of the API from 1.0,
the library must be named `mpi_abi`, and it deliberately excludes everything deprecated in MPI-3.1
or earlier, because "if deprecated features are included in the standard ABI, deleting them will
cause a backwards-incompatibility issue in the ABI" [@mpiforum2025mpi50, §21.2.1].

**ULFM did *not* land in MPI-5.0.** User-Level Failure Mitigation remains a *draft proposal* of the
Forum's Fault Tolerance Working Group. Open MPI documentation describing releases that postdate
MPI-5.0 still states that "ULFM is still an extension to the MPI standard" and that users must
`#include <mpi-ext.h>` to reach its error codes and functions [@openmpi2024ulfm]. The Forum's
issue tracker shows ULFM as an open item with readings in 2022 [@mpiforum_ulfm_issue20], and MPI
Fault Tolerance appears on the Forum's list of targets for **MPI-6.0**, not as delivered work
[@mpiforum2025bof]. Bland et al. wrote in 2015 that ULFM was "the front-running solution for
process fault tolerance in MPI. While not yet adopted into the MPI standard..." [@bland2015ulfm];
that sentence is still accurate a decade later. **This is the single most common secondary-source
error about MPI's fault model, and the paper should say so.**

A second documentary discrepancy a reviewer may catch: MPI-1.1's front matter says "**Version 1.0:
June, 1994**" [@mpiforum1995mpi11], whereas MPI-1.3 and every later version say "**MPI-1.0 (May 5,
1994)**" [@mpiforum2008mpi13; @mpiforum2021mpi40], and the Wiley chapter says the specification
"was then released through the Internet on May [5,] 1994" [@dongarra2000messagepassing]. **Cite
May 5, 1994 — what the standard now says of itself — and footnote the conflict.** Do not write
"June 1994" unqualified, as Wikipedia does [@wikipedia_mpi].

Page counts, for the size critique in §4: MPI-2.1 608pp, 2.2 647pp, 3.0 852pp, 3.1 868pp, 4.0
**1139pp**, 4.1 1166pp, 5.0 1189pp [@schulz2026stateofmpi]; the 1139 is independently confirmed by
the document itself [@mpiforum2021mpi40]. Snir counts 128 functions / 231 pages at MPI-1.1, 330 /
586 at MPI-2.1, 451 / 836 at MPI-3.1 [@snir_mpilevels] — **`[UNVERIFIED]`**, and his page figures
differ from the Forum's (836 vs 868 for MPI-3.1), probably over front matter. Prefer the Forum's.

---

## 3. The design philosophy, as principles with citations

### 3.1 "Standardise existing practice"

The rule is stated in the MPI-1 goal list as a design constraint, not a slogan: **"Define an
interface that is not too different from current practice, such as PVM, NX, Express, p4, etc., and
provides extensions that allow greater flexibility"** [@mpiforum1995mpi11, §1.1, p. 2], reinforced
by the framing sentence quoted at the head of §1 and by Williamsburg's statement of method
[@walker1992standards, §5]. Three refinements the paper should get right.

**(a) It was never "codify only what exists."** The actors are explicit that MPI went beyond
current practice: "although much of MPI standardizes the common practice of existing
message-passing systems, MPI goes further to define such advanced features as user-defined
datatypes, persistent communication ports, powerful collective communication operations, and
scoping mechanisms for communication. **No previous system incorporated all these features**"
[@dongarra1996messagepassingstandard]. The rule is better stated as: *every mechanism must have
been proved somewhere, but the combination may be new.*

**(b) It was, in part, political.** "It would have been very difficult to make one of the existing
message passing interfaces the universal standard. From the technical point of view no interface
before MPI fulfilled the functionality requirements of the whole range of potential users... **At
least as important was the political aspect**, since choosing an existing interface would have
created opposition by vendors and users who preferred other choices" [@dongarra2000messagepassing,
§"Lessons learned"].

**(c) The Forum broke the rule in MPI-2 and knew it.** "One of the MPI-1 guidelines had been to
keep the standard as close as possible to current practice... **In many cases during MPI-2 the
borderline between current practice and research was passed and new features were included in the
standard without any experience with available implementations**" [@dongarra2000messagepassing].
MPI-2 took sixteen meetings to MPI-1's seven, and its implementations arrived slowly: the cleanest
natural experiment in the corpus on the value of the rule.

### 3.2 The non-goals of MPI-1

MPI-1.1 §1.5 lists what "the standard does not specify" verbatim [@mpiforum1995mpi11, §1.5, pp.
3–4]:

> - Explicit shared-memory operations
> - Operations that require more operating system support than is currently standard; for
>   example, interrupt-driven receives, remote execution, or active messages
> - Program construction tools
> - Debugging facilities
> - Explicit support for threads
> - Support for task management
> - I/O functions

And the reason: "There are many features that have been considered and not included in this
standard. This happened for a number of reasons, **one of which is the time constraint that was
self-imposed in finishing the standard**. Features that are not included can always be offered as
extensions by specific implementations" [@mpiforum1995mpi11, §1.5, p. 4].

**Fault tolerance appears as a non-goal in disguise, phrased as a goal:** "**Assume a reliable
communication interface: the user need not cope with communication failures. Such failures are
dealt with by the underlying communication subsystem**" [@mpiforum1995mpi11, §1.1, p. 2]. That is
a statement about *communication* reliability, not process survival. Gropp and Lusk are emphatic
that the standard does *not* mandate that a process death kills the job — that is a consequence of
the default error handler, not of the semantics:

> "A common misconception about MPI is that the MPI Standard itself mandates that if any MPI
> process dies, then all the MPI processes in the job must die as well. **This is not true.** The
> basis for this misconception is easily understandable. The standard says... that the default
> error handler on the communicator `MPI_COMM_WORLD` is the built-in one called
> `MPI_ERRORS_ARE_FATAL`... The MPI Forum decided that this would probably be the most useful
> default behavior, particularly for new users. (And when the MPI Forum was deliberating, all
> users were new.)" [@gropp2004faulttolerance, §3]

They also insist that "fault tolerance is a property of an MPI program coupled with an MPI
implementation," not of an API, and that the assertion "MPI is not fault tolerant" is "not actually
well formed and so is neither true nor false" [@gropp2004faulttolerance, §3]. What the standard
*does* mandate is reliable delivery: a conforming implementation may not deliver a corrupted
message, and "under no circumstances should an MPI application or library need to verify integrity
of data received" [@gropp2004faulttolerance, §4.1].

**What the non-goals bought.** Smallness, and therefore near-universal vendor implementation. The
qualitative claim is well supported — "most vendors if not all support MPI as their primary message
passing interface" [@dongarra2000messagepassing, §"Lessons learned"], and MPICH was complete on
release day and "formed the basis of many hardware vendors' custom implementations"
[@gropp1996mpich] — but I could **not** verify the specific formulation "every vendor shipped it
within ~2 years." **`[UNVERIFIED]`**: settling it needs the vendor-implementation tables from the
mid-1990s MPI Developers Conference proceedings or *MPI: The Complete Reference*
[@snir1996mpicomplete]. Make the weaker, fully supported claim instead.

### 3.3 Communicators and the safe-library argument — the central citation

This is the argument the AgentMPI paper is built on, so I give the full chain of custody.

**Origin: Zipcode (1988).** Skjellum et al. record that "four of the five key contributions:
contexts of communication, static process group support, mailers (called communicators in MPI),
and virtual topology support were all completed (and put in practice) in 1988 and early 1989.
**Contexts, which provide separate, safe 'universes' of message passing, were one of the first
features implemented during August, 1988**" [@skjellum1994zipcode, §1]. The mechanism was a
system-supplied additional tag, *opaque to the user*, bound to a static process group by an object
called a **mailer**: "messages in a given context are guaranteed not to interfere with those in
another context. This enables the development of large applications and libraries **without fear
of message interference between applications and libraries**" [@skjellum1992zipcodesystem].

**The problem statement, retrospectively.** The clearest account of *why* pre-MPI libraries were
unsafe: "As message passing applications became more complex, for example through the use of third
party libraries, insulating the message traffic in the application code from the communication
inside a library became a non-trivial problem, **in particular if wild cards were used in receiving
messages**" [@dongarra2000messagepassing, §"Zipcode"].

**The primary citation to use — Gropp, "Learning from the Success of MPI" (HiPC 2001):**

> "MPI supports component oriented software. Both [to] describe the subset of processes
> participating in a component and to ensure that all MPI communication is kept within the
> component, MPI introduced the communicator. **Without something like a communicator it is
> possible for a message sent by one component and intended for that component to be received by
> another component or by user code. MPI made reliable libraries possible.**"
> [@gropp2001learning, §"Modularity"]

A footnote to that passage supplies the attribution: **"The context part of the communicator was
inspired by Zipcode"** [@gropp2001learning, §"Modularity", n.]. Gropp elsewhere makes the
communicator his exemplar of MPI's economy of concepts: it "both describes the group of
communicating processes and provides a separate communication context that supports component
oriented software" [@gropp2001learning, §"Simplicity and Symmetry"].

**The normative text in the standard.** MPI-1.1 §5.1.1 lists "the key features needed to support
the creation of robust parallel libraries," first among which is "**Safe communication space, that
guarantees that libraries can communicate as they need to, without conflicting with communication
extraneous to the library**" [@mpiforum1995mpi11, §5.1.1, p. 133]. §5.1.2 defines the mechanism:

> "Contexts provide the ability to have separate safe 'universes' of message passing in MPI. **A
> context is akin to an additional tag that differentiates messages. The system manages this
> differentiation process.** The use of separate communication contexts by distinct libraries (or
> distinct library invocations) insulates communication internal to the library execution from
> external communication. This allows the invocation of the library even if there are pending
> communications on 'other' communicators, and **avoids the need to synchronize entry or exit
> into library code**." [@mpiforum1995mpi11, §5.1.2, pp. 134–135]

The standard cites three works for the communicator concept — rendered `[23, 62, 65]` in MPI-5.0
§7.1.2 [@mpiforum2025mpi50, §7.1.2] and lost to `[?]` in extracted MPI-1.1 text. Cross-checking
against the MPI-1.1 bibliography, the referents are almost certainly Feitelson's "Communicators:
Object-based multiparty interactions for parallel programming" (Hebrew University TR 91-12, 1991)
[@feitelson1991communicators] and two Zipcode papers [@skjellum1990zipcode; @skjellum1994zipcode].
**`[UNVERIFIED]`** as to the exact three-way resolution; settle it against the numbered
bibliography of the MPI-5.0 HTML report if the paper needs to name them.

**The contract, stated formally.** MPI-1.1 §5.8.1, "Formalizing the Loosely Synchronous Model,"
states the caller/callee obligation AgentMPI is transplanting:

> "When a caller passes a communicator (that contains a context and group) to a callee, that
> communicator **must be free of side effects throughout execution of the subprogram**: there
> should be no active operations on that communicator that might involve the process. This
> provides one model in which libraries can be written, and work 'safely.' For libraries so
> designated, the callee has permission to do whatever communication it likes with the
> communicator, and under the above guarantee knows that no other communications will interfere...
> **This form of safety is analogous to other common computer-science usages, such as passing a
> descriptor of an array to a library routine.**" [@mpiforum1995mpi11, §5.8.1, p. 175]

**The honest limits.** Hoefler and Snir, in the definitive modern treatment, call contexts "the
most important concept for libraries," offering "spatial and temporal isolation" — a "communication
privatization" analogous to data privatization in object-oriented languages [@hoefler2011libraries,
§3]. But they also record that "**the concept of communicators is not a complete solution because
it has a static scope and does not support reentrant libraries.** Most
parallel libraries are non-reentrant, in the sense that there can be at most one concurrent
invocation per communicator (no recursion, no new invocation on some process while an old
invocation on another process still goes on)" [@hoefler2011libraries, §3.2]. The standard had
already conceded a version of this in a worked example: "despite contexts, subsequent calls to
`lib_call` with the same context need not be safe from one another (colloquially,
'back-masking')... **What this demonstrates is that libraries have to be written carefully, even
with contexts**" [@mpiforum1995mpi11, §5.7].

A context makes concurrent *distinct* callees safe from one another; it does not by itself make a
*single* callee safe against its own re-entry. AgentMPI must inherit that caveat.

### 3.4 Safe programs and the buffering question

MPI declines to promise buffering, and defines "safe" in terms of the refusal. From MPI-1.1 §3.5,
Advice to users:

> "**A program is 'safe' if no message buffering is required for the program to complete.** One
> can replace all sends in such program with synchronous sends, and the program will still run
> correctly. This conservative programming style provides the best portability, **since program
> completion does not depend on the amount of buffer space available or in the communication
> protocol used**." [@mpiforum1995mpi11, §3.5, pp. 33–34]

The same passage explains the pragmatic escape hatch and names the alternative as unsafe: "Many
programmers prefer to have more leeway and be able to use the '**unsafe**' programming style
shown in example 3.9. In such cases, the use of standard sends is likely to provide the best
compromise between performance and robustness: **quality implementations will provide sufficient
buffering so that 'common practice' programs will not deadlock**" [@mpiforum1995mpi11,
§3.5]. Example 3.9 — both ranks send, then both receive — is the canonical unsafe program: "for the
program to complete, it is necessary that at least one of the two messages sent be buffered. Thus,
this program can succeed only if the communication system can buffer at least `count` words of
data" [@mpiforum1995mpi11, §3.5, Ex. 3.9].

The justification for refusing the guarantee is *flow control*, stated in the "Resource
limitations" paragraph of the same section:

> "A buffered send operation that cannot complete because of a lack of buffer space is erroneous...
> On the other hand, **a standard send operation that cannot complete because of lack of buffer
> space will merely block**, waiting for buffer space to become available or for a matching
> receive to be posted. This behavior is preferable in many situations. Consider a situation where
> a producer repeatedly produces new values and sends them to a consumer. Assume that the producer
> produces new values faster than the consumer can consume them. If buffered sends are used, then
> a buffer overflow will result... **If standard sends are used, then the producer will be
> automatically throttled**, as its send operations will block when buffer space is unavailable."
> [@mpiforum1995mpi11, §3.5, "Resource limitations", pp. 31–32]

Note also the three other guarantees §3.5 *does* give, since AgentMPI will need analogues:
**Order** (messages are non-overtaking between a given sender/receiver pair on a matching
receive, which "guarantees that message-passing code is deterministic, if processes are
single-threaded and the wildcard `MPI_ANY_SOURCE` is not used"), **Progress** (of a matching
send/receive pair, at least one completes independently of other system activity), and explicitly
**no Fairness** ("MPI makes no guarantee of fairness in the handling of communication... It is
the programmer's responsibility to prevent starvation") [@mpiforum1995mpi11, §3.5, pp. 30–31].

### 3.5 Performance portability and the α–β cost model

The Williamsburg reasoning — standardise semantics at an intermediate level, let each machine
implement as it likes [@walker1992standards, §3] — becomes, in Gropp's retrospective, the claim
that "portability does not require taking a lowest common denominator approach. A good design
allows the use of performance enhancing features without mandating them. For example, the message
passing semantics of MPI allows for the direct copy of data from the user's send buffer to the
receive buffer without any other copies. However, systems that can't provide this direct copy...
are permitted under the MPI model to make one or more copies" [@gropp2001learning, §"Portability"].
His later slogan: MPI is a *greatest*, not a *lowest*, common denominator approach
[@gropp2016onceandfuture]. He concedes the limit: MPI "does not achieve perfect performance
portability, defined as providing a single source that runs at near achievable peak performance on
all platforms. This lack is sometimes given as a criticism of MPI, but it is a criticism that most
other programming models also share" [@gropp2001learning, §"Performance"].

The community's cost model is Hockney's: the time to send a message of size $m$ is $\alpha + \beta
m$, with $\alpha$ the per-message latency and $\beta$ the reciprocal bandwidth
[@hockney1994communication]; the crossover from latency- to bandwidth-bound is at $n^{*} =
\alpha\beta$. Its acknowledged weaknesses — no congestion, no pipelining — are why LogP
[@culler1993logp] and its descendants exist; see [@rico2019survey] for a survey.

### 3.6 Opaque objects and the handle discipline

MPI-1.1 §2.4.1:

> "MPI manages system memory that is used for buffering messages and for storing internal
> representations of various MPI objects such as groups, communicators, datatypes, etc. **This
> memory is not directly accessible to the user, and objects stored there are opaque: their size
> and shape is not visible to the user. Opaque objects are accessed via handles, which exist in
> user space.**" [@mpiforum1995mpi11, §2.4.1, p. 8]

The rationale follows immediately: "This design hides the internal representation used for MPI data
structures, thus allowing similar calls in C and Fortran. It also avoids conflicts with the typing
rules in these languages, and **easily allows future extensions of functionality**"
[@mpiforum1995mpi11, §2.4.1, Rationale]. Allocation and deallocation are per-type, deallocation
returns a null handle, and comparison against the per-type null constant is the validity test. The
aliasing burden falls on the user: "It is the user's responsibility to avoid adding or deleting
references to opaque objects, except as a result of calls that allocate or deallocate such
objects" [@mpiforum1995mpi11, §2.4.1, Advice to users].

Thirty-one years later, opacity is what made the MPI-5.0 ABI possible at all: because handles were
never structures, an ABI can fix their representation without disturbing any conforming program
[@mpiforum2025mpi50, ch. 21]. A strong empirical vindication of the principle.

### 3.7 The Rationale / Advice to users / Advice to implementors device

The standard defines its own three-voice editorial structure in §2.2:

> "**Rationale.** Throughout this document, the rationale for the design choices made in the
> interface specification is set off in this format. Some readers may wish to skip these sections,
> while readers interested in interface design may want to read them carefully. *(End of
> rationale.)*"
> "**Advice to users.** Throughout this document, material that speaks to users and illustrates
> usage is set off in this format..." [@mpiforum1995mpi11, §2.2, p. 6]

with a parallel *Advice to implementors*. Why it mattered: it let the Forum record *contested design
reasoning inside the normative document* without that reasoning becoming normative. A committee
shipping a specification in nine months can afford neither to relitigate settled questions nor to
lose the arguments. The device also carries load — the definition of a safe program (§3.4), the
aliasing rule for handles (§3.6) and the amortisation hint for context allocation all live in
Advice blocks rather than normative text. Hence a consequence worth flagging: **the "safe program"
definition on which AgentMPI's central discipline is modelled is formally non-normative.**

### 3.8 Retrospectives

**Gropp, "Learning from the Success of MPI" (HiPC 2001, LNCS 2228, pp. 81–92)**
[@gropp2001learning] gives six requirements — **portability, performance, simplicity and symmetry,
modularity, composability, completeness** — of which two sub-arguments are directly reusable. On
size: "The MPI model is often criticized as being large and complex, based on the number of
routines... **The number of routines is not a relevant measure**, however... A better measure of
complexity is the number of concepts that the user must learn, along with the number of exceptions
and special cases. Measured in these terms, MPI is actually very simple." On symmetry: MPI keeps
the "rarely used" `MPI_Issend` because removing it would create an exception, and "each such
exception adds to the burden on the user" [@gropp2001learning, §"Simplicity and Symmetry"]. He
concedes MPI followed symmetry "too far" in the group-manipulation routines and in cancelling
sends.

**Gropp and Lusk, "Fault Tolerance in MPI Programs"** [@gropp2004faulttolerance], quoted in §3.2.
Their taxonomy of four "levels of survival" — implementation recovers transparently; the program is
notified and repairs; some operations become invalid; abort and restart from checkpoint — is a good
scaffold for the AgentMPI fault section.

**Hoefler and Snir, "Writing Parallel Libraries with MPI"** (EuroMPI 2011) [@hoefler2011libraries]
distils what a parallel library needs from its runtime: "performance, scalability, usability, error
handling, **isolation (a 'safe and private' communication space)** for point-to-point and collective
communication, and virtualized process naming" [@hoefler2011libraries, §2.3]. Their best-practice
list transposes directly to AgentMPI: don't use `MPI_COMM_WORLD` in a library; don't synchronise at
entry or exit; cache library state as an attribute on the user's communicator; attach a
library-specific error handler to the library's private communicator [@hoefler2011libraries, §4].

**Laguna et al., "A Large-Scale Study of MPI Usage in Open-Source HPC Applications" (SC'19)**
[@laguna2019study] statically analysed **110 open-source HPC applications**. "The majority of MPI
programs use only a small set of features from the MPI Standard — a considerable number of
applications use only the point-to-point and collective communication features of the standard,
**leaving other parts of the standard totally unused**." Specifically: **42%** of programs rely on
MPI-1.0 features only and about **80%** need no more than MPI-2.0; **67%** use blocking send and
receive, over 90% use point-to-point, and under 10% use persistent point-to-point or one-sided.
"The features provided in subversions of the standard (i.e., 1.3, 2.1, 2.2, and 3.1) are
practically of little value to applications since they are rarely used." The authors' normative
conclusion: "This raises questions about the value of the efforts and costs in standardizing minor
features (perhaps 'syntactic sugar' features) that are ultimately not widely adopted by users."
Incidentally, and against conventional wisdom, **C++ rather than Fortran is the dominant language**
in MPI programs, Fortran dominating only the largest codes.

**A genuine contradiction between primary sources, which the paper should exploit rather than
hide.** Gropp in 2001 defended MPI's completeness by asserting that "an early poll of MPI users in
fact found that **while no one was using all of the MPI routines, essentially all MPI routines were
in use by someone**" [@gropp2001learning, §"Summary"]. Laguna et al. found the opposite at scale:
whole chapters unused, minor versions dead on arrival [@laguna2019study]. The two are not strictly
inconsistent — Gropp's claim is about coverage by *some* user, Laguna's about the median
application, and eighteen years separate them — but the rhetorical uses are incompatible and the
2019 evidence is far better. **This is the strongest single citation for AgentMPI's "small
mandatory core plus optional levels" design.**

**The rejected-subset finding**, which I have not seen used in the modern literature: the Forum
*considered a mandatory core and rejected it*. "At one point in the development of MPI it was
proposed to denote a subset of the MPI routines as being the essential ones that vendors should
implement. **The existence of MPICH demonstrated that this subset MPI was not necessary and the
idea was dropped**" [@dongarra2000messagepassing, §"MPI"]. The Forum could dispense with a
mandatory core precisely because a high-quality free reference implementation of the *whole*
standard existed on release day. AgentMPI's levels argument must therefore either argue that no
such reference implementation is achievable for agent harnesses, or commit to shipping one. This
history supports no third option.

---

## 4. Criticisms of MPI, fairly stated

**Size.** MPI-4.0 is 1139 pages [@mpiforum2021mpi40; @schulz2026stateofmpi]; MPI-5.0 is 1189
[@schulz2026stateofmpi]. Snir's function counts trace the same curve: 128 → 330 → 451
[@snir_mpilevels]. Gropp's rebuttal (§3.8) — count concepts, not routines — is the strongest
defence, and the existence of usable six-function subsets is empirically borne out by Laguna
[@laguna2019study]. The counter-rebuttal is Laguna's own: if the standard is mostly unused,
standardisation effort is misallocated regardless of whether users are *harmed* by the unused
pages. Bangalore et al. offer a structural diagnosis, arguing MPI is "held back... by the close
ties between the concepts that constitute MPI and the languages in which it was expressed" —
instancing the large-count procedures that "doubled 157 of the original MPI 3.1 procedures" — and
propose separating a language-neutral core from language bindings so "the main document should
shrink considerably in length and complexity" [@bangalore2021bindings].

**Dynamic process management went largely unused.** `MPI_Comm_spawn` arrived in MPI-2.0 and did not
take. The mechanism-level reason is in the standard itself: spawn "provide[s] an interface between
MPI and the runtime environment of an MPI application. **The difficulty is that there is an enormous
range of runtime environments and application requirements, and MPI must not be tailored to any
particular one**"; consequently MPI "does not require the existence of an underlying 'virtual
machine' model," and processes spawned by one task may not even be visible to another
[@mpiforum2015mpi31, §10.2]. That vagueness meant no portable contract with the resource manager,
and "major vendors simply don't support `MPI_Comm_spawn`" [@wozniak2019mpilaunch]; Laguna's data
confirm near-zero use [@laguna2019study]. A further provenance argument holds that dynamic
processes were included "at least in part as a political necessity" to answer the PVM community,
without a strong initial technical case [@clustermonkey_spawn]. **`[UNVERIFIED]`** — that last is a
well-informed column, not a primary source; treat it as reported opinion or drop it. The
load-bearing version is the standard's own admission of runtime-environment diversity.

**RMA is hard.** The MPI-2 one-sided chapter is widely held to have been semantically defective;
MPI-3 substantially rewrote it, and the Balaji group's assessment is that MPI-3's additions
"address most of the critiques raised about MPI-2 RMA" [@yang2014caf]. Before that repair, measured
MPI-2 one-sided performance was "significantly worse than PGAS and in fact worse than the MPI
two-sided" on at least one production platform [@shan2012onesided].

**The fault model is inadequate.** This is now the mainstream view inside the Forum, not outside it:
MPI Fault Tolerance is on the published roadmap for MPI-6.0 [@mpiforum2025bof], and ULFM has been
under development since before 2015 without ratification [@bland2015ulfm; @mpiforum_ulfm_issue20].
The most careful *defence* remains Gropp and Lusk's: fault tolerance is a property of a program plus
an implementation, the standard does not require fail-stop-the-world, and useful fault-tolerant MPI
programs can be written today within constraints [@gropp2004faulttolerance]. The most careful
*charge* is ULFM's own design principle, that "no MPI call... can block indefinitely after a
failure, but must either succeed or raise an MPI error" [@openmpi2024ulfm] — precisely the guarantee
MPI does not currently make.

**PGAS alternatives.** UPC, Coarray Fortran, OpenSHMEM, Chapel and UPC++ share a diagnosis: that
two-sided message passing is the wrong default because it forces the programmer to orchestrate both
ends of every transfer, and that a partitioned global address space "combines the programming
convenience of shared memory with the locality and performance control of message passing"
[@dewael2015pgas]. Their key move is one-sided access with the local/remote distinction preserved,
so locality remains visible while the sender/receiver rendezvous disappears [@dewael2015pgas;
@shan2012onesided]. The honest scorecard: PGAS is right that manual packing and two-sided matching
cost programmer effort, and demonstrably wrong that this cost would dominate adoption. "Neither of
them however has been widely adopted by the user community; partly because of the lack of a
developer environment, and partly because of the lack of convincing performance results for real
applications, especially at large scale" [@shan2012onesided]; and "most parallel scientific
applications still rely on MPI as their data movement model," partly because adopting a PGAS model
means running a second runtime alongside MPI, duplicating resources and risking deadlock
[@yang2014caf]. **The composability argument beat the productivity argument** — probably the most
transferable single lesson in §4.

**Mandatory overhead.** Independent of size, a 2017 analysis of a maximally optimised MPICH/CH4
stack found 44–59 instructions of *unavoidable* work on the critical path for `MPI_Isend`/`MPI_Put`,
attributable to six overheads "that cannot be removed without modifying the MPI standard beyond
MPI-3.1" [@raffenetti2017extreme]. Semantics have costs, and a standard that declines to name them
ships them anyway.

---

## 5. What transfers to an agent protocol, and what does not

*This section is my analysis as a researcher, not history. Nothing here is a citation claim.
Every judgment is falsifiable and I have tried to say how.*

**"Standardise existing practice" — does not transfer as stated; must be replaced.** The rule
presupposes that practice has converged enough to be codified, and MPI's own history shows what
happens when it hasn't: MPI-2 crossed "the borderline between current practice and research"
[@dongarra2000messagepassing] and produced chapters that are, per Laguna, still unused thirty years
on. Agent-harness practice is roughly two years old, dominated by a handful of fast-moving
frameworks, and has converged on almost nothing except "there is a list of tool schemas and a
loop"; applying the rule literally would standardise the tool-call JSON envelope and nothing else.
Replace it with its *mechanism-level* form, which is what MPI actually followed: **no mechanism
enters the specification unless it has been implemented and exercised in at least two independent
harnesses, but the combination may be novel** [@dongarra1996messagepassingstandard]. The paper
should be explicit that it is weakening the rule and why; a reviewer who knows the history will
otherwise catch it.

**The non-goals discipline — transfers, and is the most under-appreciated lesson.** MPI-1 shipped
in nine months and was universally implemented because its authors were willing to write down seven
things it would not do and accept the resulting incompleteness [@mpiforum1995mpi11, §1.5]. An
agent-protocol standard faces enormous pressure to specify evaluation, tracing, memory, planning
and cost accounting; it should specify none of them. The specific MPI non-goals map surprisingly
well: no debugging facilities (leave tracing to a profiling interface — MPI's one-page `PMPI`
name-shifting convention is the model); no task management; no I/O. The self-imposed deadline is
not incidental either: it is the forcing function that produced the non-goals.

**No fault tolerance — does NOT transfer. This is the sharpest discontinuity and the paper should
lead with it.** MPI-1's goal list assumes "a reliable communication interface: the user need not
cope with communication failures" [@mpiforum1995mpi11, §1.1] because in 1994, on a single MPP
with a dedicated interconnect, that assumption was nearly true and the residual failure rate was
handled by killing the job and restarting from a checkpoint. For agents it inverts on *three*
independent axes at once. (i) *Rate*: a tool call, model call or sub-agent delegation fails often
enough that failure is a normal control-flow path. (ii) *Kind*: MPI faults are fail-stop and
detectable; agent faults are predominantly **Byzantine and silent** — the callee returns a
well-formed, plausible, wrong answer. Nothing in MPI's fault literature addresses that; ULFM's
entire model is process failure detection and communicator repair [@openmpi2024ulfm;
@bland2015ulfm]. (iii) *Cost of restart*: checkpoint-restart is cheap relative to the compute it
protects, but re-executing an agent run from a checkpoint re-incurs the token cost, so the escape
hatch is far less attractive. My judgment: AgentMPI must put a fault model in the **mandatory
core**, make "operation failed" a first-class return rather than an error handler, and adopt
ULFM's strongest principle — *no operation may block indefinitely after a failure; it must either
succeed or raise* — as a normative requirement rather than an extension. That MPI took thirty-one
years and still has not standardised this is evidence of how hard retrofitting a fault model is,
and therefore an argument for doing it first.

**Communicators and the safe-library argument — transfers cleanly, and is the best part of the
transplant.** The pre-MPI failure mode was: a library posts a wildcard or a colliding-tag receive
and swallows a message intended for its caller [@gropp2001learning;
@dongarra2000messagepassing]. The agent-harness analogue is exact and currently unaddressed: a
sub-agent invoked as a "tool" shares a conversation transcript, a scratchpad or a message bus with
its caller, and no mechanism guarantees that a message the caller emitted will not be consumed or
acted on by the callee. Today's harnesses are in the pre-Zipcode state, and Linda-descended
blackboard architectures are *structurally* incapable of providing the guarantee, because tuple
spaces are globally addressable by construction [@gelernter1985linda]. Two caveats the paper must
inherit rather than paper over. First, Hoefler and Snir's reentrancy limitation
[@hoefler2011libraries, §3.2] applies with more force to agents, because recursive delegation is
common in agent systems and rare in HPC libraries; a context per *invocation*, not per callee, is
probably required. Second, MPI's safety contract — "no side effects on the communicator during the
call" [@mpiforum1995mpi11, §5.8.1] — is enforceable because MPI's state is enumerable, whereas the
agent analogue must say what "free of side effects" means over a context whose state includes model
state the protocol cannot see. State that limit explicitly rather than claiming stronger isolation
than can be delivered.

**Safe programs and the buffering refusal — transfers unusually cleanly, with bytes replaced by
tokens.** This is the strongest structural analogy in the whole dossier and it holds for a
non-obvious reason: MPI's justification for refusing to guarantee buffering was not
implementation convenience but **flow control** — "the producer will be automatically throttled,
as its send operations will block when buffer space is unavailable" [@mpiforum1995mpi11,
§3.5]. An agent protocol has an exactly homologous problem: a context window is a bounded buffer,
and a producer agent that emits faster than a consumer can read will overflow it. MPI's answer
generalises: define a *context-safe* program as one that completes without relying on any
particular context-window capacity, prove it by the same test MPI uses (replace every send with a
synchronous send and check the program still terminates), and provide a buffered mode in which the
user explicitly attaches the buffer and eats the overflow error. Keep MPI's exact rhetorical
structure — safe programs are portable, unsafe programs are permitted and usually work, quality
implementations buffer generously — because it is honest about the tradeoff. Two disanalogies to
state: token buffers are *lossy under compression* in a way byte buffers are not (summarising a
scratchpad is not equivalent to buffering it), and context capacity varies across models by orders
of magnitude within a single deployment, which makes portability *more* valuable, not less. Note
also that MPI's "safe program" definition is formally an *Advice to users* block, i.e.
non-normative [@mpiforum1995mpi11, §3.5]; AgentMPI can improve on MPI by making it normative.

**Performance portability and the α–β model — transfers in form, not in parameters.** The principle
"the standard specifies semantics, the implementation chooses the algorithm" [@walker1992standards,
§3; @gropp2001learning] transfers without modification and is the right architecture. The cost
model does not: the agent analogue of $\alpha + \beta m$ has at least three terms (per-call
latency, per-token cost, and a quality term that is not a cost at all), the "bandwidth" is priced
in dollars and varies by model, and the analogue of a collective algorithm choice — how to fan out
a query to $k$ sub-agents and combine — has a *quality* optimum that may differ from its latency
optimum. Adopt the *discipline* of a published cost model so implementations can be compared, but
do not claim Hockney transfers; proposing and validating a three-parameter agent cost model is a
paper of its own, and AgentMPI should say so rather than gesture.

**Opacity and handles — transfers, and is cheap.** MPI's rationale was language-neutrality and
"easily allows future extensions of functionality" [@mpiforum1995mpi11, §2.4.1]; the
thirty-one-year payoff was a binary interface specified in 2025 without breaking anything
[@mpiforum2025mpi50, ch. 21]. Agent harnesses currently expose everything as JSON — conversation
state, tool registries, memory — so every consumer depends on representation and no representation
can change. Opaque handles for contexts, groups and requests cost almost nothing to specify and buy
exactly the evolvability MPI got: the lowest-risk, highest-return item on the transfer list.

**Rationale / Advice to users / Advice to implementors — transfers, and should be adopted
verbatim.** In a field where the specification will be read mostly by people who disagree with
it, a device for recording *why* a choice was made, inside the document but outside the normative
text, is worth more than in 1994, not less [@mpiforum1995mpi11, §2.2].

**Completeness — does not transfer, and this is where I part company with Gropp.** His argument is
that "any parallel algorithm can be implemented with MPI" and "while many programs may not be easy
under MPI, no program is impossible" [@gropp2001learning, §"Completeness"]. The precondition is a
*stable and enumerable* space of things users want to do; agent systems have none, and attempting
completeness against a moving target is how you get MPI-2's unused chapters — Laguna
[@laguna2019study] shows the cost of over-standardising even in a mature field. Substitute
**extensibility** for completeness (a cheap explicit extension mechanism plus a small core) and
argue the substitution openly. The counter-argument the paper must answer is the rejected-subset
finding [@dongarra2000messagepassing]: the Forum could skip a mandatory core because MPICH
implemented everything on day one. AgentMPI has no MPICH. Either it ships one, or the levels
argument carries the weight alone.

**Composability over productivity — transfers, and is the sleeper lesson.** PGAS lost to MPI
despite a genuine productivity advantage, largely because adopting it meant running a second
runtime beside MPI [@yang2014caf; @shan2012onesided]. Any agent protocol that cannot be adopted
*incrementally* inside an existing harness — one sub-agent boundary at a time, coexisting with
whatever is already there — will lose to whatever can, regardless of how much better it is. This
should shape the paper's implementation section more than its design section.

---

## 6. Register of unverified items

| # | Claim | What would settle it |
| --- | --- | --- |
| 1 | MPI-1 items required approval at two separate meetings | Full read of MPI Forum minutes, `netlib.org/mpi/minutes-93/` |
| 2 | "Every vendor shipped MPI within ~2 years" | Vendor-implementation tables in mid-1990s MPI Developers Conference proceedings, or *MPI: The Complete Reference* |
| 3 | Exact resolution of the three references cited for "Communicators" (MPI-5.0 §7.1.2 `[23, 62, 65]`) | Numbered bibliography of the MPI-5.0 HTML report |
| 4 | Snir's page counts (836pp for MPI-3.1) vs the Forum's (868pp) | Count front matter in the official PDFs; prefer the Forum |
| 5 | Dynamic process management included as a "political necessity" for the PVM community | Forum minutes of the MPI-2 dynamic-process subcommittee, 1995–1997 |
| 6 | MPI-1.0 release date: May 5 vs June 1994 | Not a research gap but a real documentary conflict between MPI-1.1 and MPI-1.3+; cite May 5, 1994, and footnote the conflict |
