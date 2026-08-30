# MPI: History, Standardization Process, and Design Philosophy

Research memo. Primary sources are the MPI standard documents themselves (whose `Rationale` and `Advice to
users` blocks are the authoritative statement of intent), the MPI Forum procedures documents, the 1992
CRPC Williamsburg workshop report, and the retrospective literature by Forum participants (Gropp, Lusk,
Snir, Dongarra, Walker, Hempel, Skjellum, Träff, Thakur, Hoefler, Bosilca). Claims are keyed to the
reference list at the end. Items I could not confirm against a primary or near-primary source are marked
`[UNVERIFIED]`.

---

## 1. Pre-history: the "one message-passing library per machine" regime

### 1.1 The structural cause

Message passing in the late 1980s and early 1990s was not short of implementations — it was short of
*agreement*. Each distributed-memory vendor shipped a proprietary point-to-point library tuned to its own
interconnect, because the interconnects themselves were radically different (hypercube, mesh, fat tree,
custom NICs) and because there was no standard to implement. The MPI-1.1 standard's own overview describes
the state of the art with clinical brevity: message passing "is a paradigm used widely on certain classes
of parallel machines... Although there are many variations, the basic concept of processes communicating
through messages is well understood... **Each vendor has implemented its own variant**" [mpi11].

The Williamsburg workshop report captures the specific reason a low-level standard was impossible: "the
hardware of different distributed memory computing systems is sufficiently varied that it is difficult to
impose a low-level standard that is efficient on all machines. Therefore, it is more appropriate to define
a standard at an intermediate level, and to implement this as efficiently as possible on each machine"
[walker92]. The workshop framed this as the **"Onion Skin Model"**: a layered stack from channel-addressed
packet movement at the hardware, through a process-addressed level (NX, Vertex, Express, PARMACS), up to
high-level abstractions (Linda, MetaMP, shared objects) [walker92]. The strategic insight that made MPI
possible was choosing the *middle* layer as the standardization target — high enough to be portable across
wildly different hardware, low enough that vendors could implement it without giving up performance.

The workshop also had to argue against a real counter-position: that porting cost was negligible relative
to the cost of writing and tuning a correct parallel program, so a standard "doesn't gain you much"
[walker92]. The rebuttal that carried the day was not primarily about porting effort. It was about
**ecosystem economics**: a standard "would provide vendors with a clearly defined set of routines that they
could implement efficiently at a low level, or even provide hardware support for" [walker92], and it would
make third-party parallel libraries and tools commercially viable [walker17]. Walker's retrospective
reproduces the original rationale slide: "The existence of MPI makes the creation of parallel software
(tools, libraries, applications, etc.) by independent software developers commercially viable" [walker17].
The target of MPI was never really the end user; it was the *library author*.

### 1.2 What each predecessor got right and wrong

MPI's Section 1.2 names its influences explicitly, and the list is the best single map of the pre-history:
"MPI was strongly influenced by work at the IBM T. J. Watson Research Center, Intel's NX/2, Express,
nCUBE's Vertex, p4, and PARMACS. Other important contributions have come from Zipcode, Chimp, PVM,
Chameleon, and PICL" [mpi50 §1.2]. Träff's dissertation gives the most precise attribution of *which idea*
came from where [traff09]; the table below synthesizes [mpi50], [traff09], [dongarra96], [geist96],
and [walker92].

| System | Origin | Got right | Got wrong / limits |
|---|---|---|---|
| **NX / NX/2** | Intel iPSC, Paragon (P. Pierce) | Tagged messages, selective receive; the "buffered send always completes" semantics that became `MPI_Bsend` [traff09] | Vendor-locked; first version had no process groups and no collectives [traff09] |
| **Vertex** | nCUBE | Process-addressed messaging on a hypercube | Vendor-locked; single-machine |
| **CMMD** | Thinking Machines CM-5 (Tucker, Johnsson) | Presented at Williamsburg [walker92] | Notably *absent* from MPI's list of acknowledged influences [mpi50 §1.2] — an artifact of TMC's imminent collapse `[UNVERIFIED as causal]` |
| **IBM EUI / CCL / Vulcan** | IBM Watson | Task groups (a communicator precursor); rich collectives; **nonblocking send/recv with request handles and separate completion calls** [traff09] | Proprietary |
| **p4** | ANL (Butler, Lusk) | Portable across shared *and* distributed memory; efficiency-focused enough to later serve as an MPI *implementation* substrate (`ch_p4`) [traff09, butler94-p4] | Low-level macro flavor; no safe library scoping |
| **PARMACS** | ANL/GMD (Calkin, Hempel, Hoppe, Wypior) | **Virtual process topologies** — adopted by MPI-1 "almost unchanged" [traff09]; sophisticated process→processor mapping [walker92, calkin94-parmacs] | Macro-based; some versions supported weighted graph edges that MPI *failed* to adopt [traff09] |
| **Express** | ParaSoft (commercial) | Stressed building high-level parallel libraries that hide message passing; had communicator and virtual-topology concepts [traff09, express92] | Commercial, closed; not a community standard |
| **Zipcode** | LLNL/Miss. State (Skjellum, Leung) | **The communicator**: safe, interference-free parallel libraries; contributed both the concept and the implementation mechanisms later used in MPI implementations [traff09, skjellum90, skjellum94]; Gropp states flatly that "the context part of the communicator was inspired by Zipcode" [gropp01] | Research-scale adoption |
| **PVM** | ORNL/UTK (Geist, Sunderam, Dongarra et al.) | Dynamic, heterogeneous, *virtual machine* model: add/remove hosts at runtime, spawn tasks, **fault notification**; XDR-style typed data for heterogeneity [traff09, geist96, geist94-pvm] | Deliberately excluded from MPI-1: MPI "explicitly states that resource management and the concept of a virtual machine are outside the scope" [geist96]. PVM's dynamic process management later drove MPI-2 [traff09] |
| **Chameleon** | ANL (Gropp, Smith) | Portability layer; became the "CH" in MPICH [mpich-overview] | Not an interface standard |
| **PICL** | ORNL (Worley et al.) | Portable instrumented communication library; tracing/instrumentation experience [walker92] | Not adopted as the interface |
| **CHIMP** | EPCC Edinburgh | Early full MPI-1 implementation [dongarra96] | — |
| **Linda** | Yale (Carriero, Gelernter) | Associatively addressed **tuple space** — a genuinely different abstraction; cited as an influence precisely *because* it was different [traff09, carriero89-linda] | Too high-level for the chosen standardization layer; performance opaque |
| **occam / CSP** | INMOS transputer | A *genuine* message-passing language with synchronous communication and non-deterministic choice, grounded in CSP [traff09, inmos88-occam] | "No concept of process groups, no collective operations, and little support for parallel libraries" [traff09]; tied to the transputer |

The pattern is clear. The vendor libraries had performance and no portability. The portable research
libraries (p4, PVM, Zipcode, PARMACS, PICL) had portability but, per contemporaneous framing, "did not
address the full spectrum of message-passing issues, lacked vendor support, [and] were not implemented at
the most efficient level" [gropp-slides]. Nobody had all of: performance, portability, vendor buy-in,
*and* library composability. The contemporaneous survey of this entire landscape is the 1994 *Parallel
Computing* special issue on message-passing interfaces edited by Hempel, Hey, McBryan, and Walker [hhmw94].
MPI's actual innovation was the union: "Though much of MPI standardizes the
common practice of existing message-passing systems, MPI goes further to define such advanced features as
user-defined datatypes, persistent communication ports, powerful collective communication operations, and
scoping mechanisms for communication. **No previous system incorporated all these features**" [dongarra96].

---

## 2. The MPI Forum process, 1992–1994

### 2.1 Timeline

- **Summer 1991** — informal discussions at a mountain retreat in Austria `[UNVERIFIED — attested only by Wikipedia; not in any Forum document I could find]`.
- **29–30 April 1992** — First CRPC Workshop on "Standards for Message Passing in a Distributed Memory
  Environment," Hilton Conference Center, Williamsburg, Virginia. **68 invited participants** from
  universities, government labs, and hardware/software vendors; 19 talks in 5 sessions plus a panel
  [walker92]. Two decisions set the whole trajectory: standardize at the *intermediate* level, and
  involve vendors closely "in order to ensure that whatever message-passing standard emerges can and will
  be implemented efficiently on commercial distributed memory computing systems" [walker92]. It was also
  agreed the standard must be **global, not U.S.-only**, with explicit coordination with the European
  ESPRIT effort (Hempel/GMD) [walker92]. A working group and mailing list were established [mpi50 §1.2].
- **August 1992** — Dongarra, Hempel, Hey, and Walker begin drafting a prototype ("MPI-0"/"MPI1") after a
  Gordon Conference meeting in New Hampshire [walker17].
- **November 1992** — "MPI1" presented at a Supercomputing '92 birds-of-a-feather session [walker17]. At a
  Minneapolis working-group meeting the process is put "on a more formal footing," adopting "the
  procedures and organization of the High Performance Fortran Forum"; subcommittees formed per component
  area, each with its own email reflector; target set of a draft by Fall 1993 [mpi50 §1.2]. MPI1 was
  deliberately incomplete — "primarily intended to promote discussion and 'get the ball rolling'" — it
  covered point-to-point only, had **no collectives, and was not thread-safe** [mpi50 §1.2].
- **January 1993** — first MPI Forum meeting, Dallas [walker17]. The Forum met **every 6 weeks for two
  days** throughout the first 9 months of 1993 [mpi50 §1.2].
- **June 1993** — after five meetings, the point-to-point core is complete [walker17].
- **November 1993** — after three more meetings, the draft is presented at Supercomputing '93 and published
  in the proceedings [mpiforum93]; public comment period opens [walker17].
- **January 1994** — European MPI workshop, INRIA Sophia Antipolis; **March 1994** — loose-ends meeting,
  Knoxville; **April 1994** — public comment closes [walker17].
- **5 May 1994** — MPI-1.0 released [mpi40 §1, walker17]; published as a journal special issue [mpiforum94].
- **12 June 1995** — MPI-1.1 released, formally ending MPI-1 standardization [mpi40 §1]. (Walker's slides
  say "12 June 1994," which conflicts with the Forum's own version table [mpi40 §1]; the 1995 date is
  authoritative.)

The fullest participant-written narrative of this whole sequence is Hempel and Walker's *Computer Standards
& Interfaces* retrospective [hempel99].

Elapsed time from first workshop to MPI-1.0: **just over two years**. Elapsed time of the intensive Forum
phase: "developed over a 12-month period in 1993–1994 of intensive meetings" [dongarra96]. This speed was
itself a design decision — features were dropped explicitly because of "the time constraint that was
self-imposed in finishing the standard" [mpi11 §1.5].

### 2.2 Who participated and how work was divided

"About 60 people from 40 organizations mainly from the United States and Europe" per the standard
[mpi50 §1.2]; "more than 80 people from approximately 40 organizations" per the CACM retrospective
[dongarra96]. Chapter ownership (from Walker's retrospective [walker17]):

- Conveners / meeting chairs: Jack Dongarra, David Walker
- Minutes: Ewing (Rusty) Lusk, Bob Knighten
- Editor: Steve Otto
- Point-to-point: Marc Snir, William Gropp, Ewing Lusk
- Collectives: Al Geist, Marc Snir, Steve Otto
- Process topologies: Rolf Hempel
- Language binding: Ewing Lusk
- Environmental management: William Gropp
- Profiling: James Cownie
- Groups, contexts, communicators: Tony Skjellum, Lyndon Clarke, Marc Snir, Richard Littlefield, Mark Sears
- Implementation issues: Steven Huss-Lederman

Vendors of concurrent computers were involved directly — Convex, Cray Research, IBM, Intel, Meiko, nCUBE,
NEC, SGI all had MPI within two years [dongarra96]. Funding was minimal and improvised: "MPI operated on a
very tight budget (in reality, it had no budget when the first meeting was announced). ARPA and NSF have
supported research at various institutions that have made a contribution towards travel for the U.S.
academics. Support for several European participants was provided by ESPRIT" [mpi11, quoted in walker17].
NSF grant ASC-9310330, STC agreement CCR-8809615, and Esprit Project P6643 are the named instruments
[wikipedia-mpi].

### 2.3 Voting rules

The 1993–94 rule, stated by four Forum principals: "Formal voting at the meetings was by a single vote per
organization; in order to vote, an organization needed to have had at least one representative at two of
the last three meetings. To provide guidance for preparing formal proposals, frequent informal votes
including all those present were held" [dongarra96]. This is a carefully engineered incentive: *presence
buys standing*, so drive-by vetoes are impossible and sustained participation is rewarded; *one vote per
organization* prevents a large lab from packing the room; *informal all-present straw polls* let
non-voting attendees shape proposals before they harden.

The modern Forum has formalized this into a two-ballot pipeline [procedures-current]:

1. **Overall Organization Eligibility (OOE)**: an organization may vote if it registered for and had a
   representative present at **two of the last three** voting meetings (including the current one).
2. A proposal must first receive a **formal reading**, with text frozen and published at least two weeks
   before the meeting.
3. **First official ballot** — must be at a *different* meeting than the reading.
4. **Second official ballot** — must be at a *different* meeting than the first ballot.
5. A ballot passes if quorum is met and "the number of 'yes' votes is more than 3/4 of the sum of 'yes' and
   'no' votes" [procedures-22]. Proxies are not permitted; absence is an implicit abstention
   [procedures-current].
6. Post-reading text changes require a special **NO-NO-VOTE** ballot that must draw **zero "no" votes**
   [procedures-30].

The two-ballot-at-separate-meetings rule is the structural guarantee that no proposal can be rushed: it
enforces a minimum of roughly two meeting cycles of reflection, plus a public reading, for every semantic
change. This is arguably the single most transplantable piece of the MPI process.

### 2.4 Why the process is regarded as a success

Walker's own list [walker17]: broad support from vendors, researchers, and academics; both US and European
participants; **limited objectives and a short time frame**; "mpich implementation available early on";
good dissemination through papers, books, and tutorials. Träff adds "MPI was the right interface at the
right time... It helped tremendously that MPI was closely followed by a template implementation that was
already in its first versions of good quality" [traff09]. Gropp adds the process itself: "The open
standards process... was an important component in its success" [gropp01].

Walker's sober coda is worth quoting for a paper about reproducing the process today: asked whether the
MPI-1 effort would have succeeded in the 2010s, he lists "less flexibility in how funding is used," a
focus "on activities that produce research papers," and "Everyone is too busy!" [walker17].

---

## 3. Stated goals and non-goals (verbatim from the standard)

### 3.1 The framing sentence

> "MPI (Message-Passing Interface) is a message-passing library interface specification. **All parts of
> this definition are significant.** ... **MPI is a specification, not an implementation**; there are
> multiple implementations of MPI. This specification is for a library interface; **MPI is not a language**,
> and all MPI operations are expressed as functions, subroutines, or methods, according to the appropriate
> language bindings." [mpi50 §1.1]

### 3.2 The complete goal list (unchanged in substance from MPI-1.1 to MPI-5.0)

> "The goal of the Message-Passing Interface, simply stated, is to develop a widely used standard for
> writing message-passing programs. As such the interface should establish a practical, portable,
> efficient, and flexible standard for message passing.
>
> A complete list of goals follows.
> - Design an application programming interface (not necessarily for compilers or a system implementation library).
> - Allow efficient communication: Avoid memory-to-memory copying, allow overlap of computation and communication, and offload to communication co-processors, where available.
> - Allow for implementations that can be used in a heterogeneous environment.
> - Allow convenient C and Fortran bindings for the interface.
> - Assume a reliable communication interface: the user need not cope with communication failures. Such failures are dealt with by the underlying communication subsystem.
> - Define an interface that is not too different from current practice, such as PVM, NX, Express, p4, etc., and provides extensions that allow greater flexibility.
> - Define an interface that can be implemented on many vendor's platforms, with no significant changes in the underlying communication and system software.
> - Semantics of the interface should be language independent.
> - The interface should be designed to allow for thread safety." [mpi50 §1.1; identical list in mpi11 §1.1 and dongarra96]

Three observations. First, the word "performance" never appears in the goal list — it appears as the
*operational* goal "avoid memory-to-memory copying," which is far more actionable. The frequently quoted
formulation "performance is a primary goal" is **not** a verbatim quotation from the standard's overview;
what the standard actually says is "the interface should establish a practical, portable, efficient, and
flexible standard" [mpi50 §1.1], and Gropp's retrospective elevates performance to one of six necessary
requirements [gropp01]. `[Treat "performance is a primary goal" as a paraphrase, not a quote.]`

Second, "Assume a reliable communication interface: the user need not cope with communication failures" is
the most consequential single line in MPI's history. It licensed thirty years of *not* having fault
tolerance, and every retrofit since (FT-MPI, ULFM) has fought this sentence.

Third, "not too different from current practice" is an explicit anti-innovation clause. MPI was chartered
to standardize, not to invent — which makes its few genuine inventions (communicators, derived datatypes)
stand out.

### 3.3 Included and deliberately excluded

MPI-1 included: point-to-point, collectives, process groups, communication contexts, process topologies,
Fortran 77 and C bindings, environmental management and inquiry, and a profiling interface [mpi11 §1.4].

MPI-1 excluded, verbatim:

> "The standard does not specify:
> - Explicit shared-memory operations
> - Operations that require more operating system support than is currently standard; for example, interrupt-driven receives, remote execution, or active messages
> - Program construction tools
> - Debugging facilities
> - Explicit support for threads
> - Support for task management
> - I/O functions
>
> There are many features that have been considered and not included in this standard. This happened for a
> number of reasons, one of which is the **time constraint that was self-imposed** in finishing the
> standard. Features that are not included can always be offered as extensions by specific
> implementations. Perhaps future versions of MPI will address some of these issues." [mpi11 §1.5]

Note the pressure-release valve: unstandardized features are pushed to *implementation extensions*, not
argued about. Every one of those seven exclusions was eventually addressed (shared memory in MPI-3, task
management in MPI-2, I/O in MPI-2, threads in MPI-2, debugging via the side-document MPIR interface).

### 3.4 Thread-safety posture

MPI-1 designed *for* thread safety without *specifying* threads. MPI-2 introduced the four-level
`MPI_Init_thread` contract — `MPI_THREAD_SINGLE < MPI_THREAD_FUNNELED < MPI_THREAD_SERIALIZED <
MPI_THREAD_MULTIPLE` [mpi31 §12.4.3]. The normative content of thread compliance is two clauses:

> "1. All MPI calls are thread-safe, i.e., two concurrently running threads may make MPI calls and the
> outcome will be as if the calls executed in some order, even if their execution is interleaved.
> 2. Blocking MPI calls will block the calling thread only, allowing another thread to execute, if
> available. ... A blocked thread will not prevent progress of other runnable threads on the same process,
> and will not prevent them from executing MPI calls." [mpi31 §12.4.1]

Clause 2 is a *progress* guarantee, and it has a sharp implementation consequence noted by Gropp and
Thakur: "an implementation cannot implement thread safety by simply acquiring a lock at the beginning of
each MPI function and releasing it at the end" [gropp-thakur07]. Responsibility for races on shared MPI
objects is pushed to the user: "the user is responsible ... for using some mechanism, such as thread locks"
[schuchart-slides quoting mpi50 §11.6.2].

---

## 4. Design principles

### 4.1 Communicator = (group, context): separation of concerns for library safety

This is MPI's signature abstraction and the one most directly relevant to a multi-agent analogue. The
standard states the requirement before the mechanism:

> "The key features needed to support the creation of robust parallel libraries are as follows:
> - **Safe communication space**, that guarantees that libraries can communicate as they need to, without conflicting with communication extraneous to the library,
> - **Group scope for collective operations**, that allow libraries to avoid unnecessarily synchronizing uninvolved MPI processes (potentially running unrelated code),
> - **Abstract naming of MPI processes** to allow libraries to describe their communication in terms suitable to their own data structures and algorithms,
> - The ability to **'adorn' a set of communicating MPI processes with additional user-defined attributes**, such as extra collective operations. This mechanism should provide a means for the user or library writer effectively to extend a message-passing notation.
>
> In addition, a unified mechanism or object is needed for conveniently denoting communication context, the
> group of communicating MPI processes, to house abstract naming of MPI processes, and to store
> adornments." [mpi50 §7.1.1]

And the mechanism:

> "Contexts provide the ability to have separate safe 'universes' of message-passing in MPI. A context is
> akin to an additional tag that differentiates messages. **The system manages this differentiation
> process.** The use of separate communication contexts by distinct libraries (or distinct library
> invocations) insulates communication internal to the library execution from external communication. This
> allows the invocation of the library even if there are pending communication operations or decoupled MPI
> activities on 'other' communicators, and **avoids the need to synchronize entry or exit into library
> code**." [mpi50 §7.2.3]

**The problem it solves.** In PVM, p4, NX, and Express the only message discriminator was the
user-assigned integer tag. Two independently developed libraries — or a library and its caller — could
choose the same tag, and a message would be silently delivered to the wrong recipient. The Cornell
description of the fix is the crispest available: "The problem with tags is that they are given values by
the programmer, and they might use the same tag used by a parallel library using MPI. With communicators,
**the system, not the programmer, assigns identification**" [cornell-comm]. The concrete failure mode named
in the CACM paper: process 0 posts a wildcarded (`MPI_ANY_SOURCE`/`MPI_ANY_TAG`) nonblocking receive just
before entering a library routine — "'promiscuous' posting of receives is a common technique for increasing
performance" — and that receive swallows a message the library sent internally. Symmetrically, a message
sent before library entry may be matched by a receive *inside* the library [dongarra96].

Because matching is on the triple *(source, tag, context)* and **there is no wildcard for the context**
[clustermonkey], `MPI_Comm_dup` at library initialization gives a library a message space provably disjoint
from its caller's. Combined with the **attribute caching** mechanism (`MPI_Comm_set_attr` /
`MPI_Comm_get_attr`), a library can stash its private duplicated communicator *on* the caller's
communicator, so the duplication happens once per (library, communicator) pair and remains invisible to the
application [dongarra96, mpi50 §7.7]. Group scope also means a library invoked on a sub-communicator does
not synchronize uninvolved processes — which is what makes *nested* and *concurrent* parallel libraries
(e.g., four independent matrix multiplies on four disjoint process subgroups) expressible [dongarra96].

Provenance: Zipcode [skjellum94] and IBM's EUI "task groups" [traff09]; Gropp: "The context part of the
communicator was inspired by Zipcode" [gropp01]. Gropp separately flags it as an instance of a broader
principle — economy of concepts: "the MPI communicator both describes the group of communicating processes
and provides a separate communication context that supports component oriented software" [gropp01].

**Inter-communicators** extend this to two disjoint groups, precisely for composing parallel modules and
for client/server patterns: "When an application is built by composing several parallel modules, it is
convenient to allow one module to communicate with another using local ranks for addressing within the
second module. This is especially convenient in a client-server computing paradigm" [mpi50 §7.2].

### 4.2 "Safe" programs and the refusal to guarantee buffering

MPI's most philosophically interesting refusal. Standard-mode send has *non-local* completion semantics —
whether it returns before a matching receive is posted is implementation-defined. The rationale:

> "**Rationale.** The reluctance of MPI to mandate whether standard sends are buffering or not stems from
> the desire to achieve portable programs. Since any system will run out of buffer resources as message
> sizes are increased, and some implementations may want to provide little buffering, **MPI takes the
> position that correct (and therefore, portable) programs do not rely on system buffering in standard
> mode.** Buffering may improve the performance of a correct program, but it doesn't affect the result of
> the program. If the user wishes to guarantee a certain amount of buffering, the user-provided buffer
> system ... should be used, along with the buffered-mode send. (End of rationale.)" [mpi11 §3.4]

The operational definition of safety, and the test for it:

> "A program is 'safe' if no message buffering is required for the program to complete. **One can replace
> all sends in such program with synchronous sends, and the program will still run correctly.** This
> conservative programming style provides the best portability, since program completion does not depend on
> the amount of buffer space available or in the communication protocol used." [mpi11 §3.5]

And the crucial identity: "Within the context of MPI, **'portable' is synonymous with 'safe'**"
[snir96-portable]. Unsafe programs are non-deterministic — "several outcomes are consistent with the MPI
specification, and the actual outcome to occur depends on the precise timing of events" [snir96-portable].

Two further consequences the standard is explicit about. (a) MPI **does not enforce** a safe programming
style: "Many programmers prefer to have more leeway and be able to use the 'unsafe' programming style ...
quality implementations will provide sufficient buffering so that 'common practice' programs will not
deadlock" [mpi11 §3.5]. Correctness is specified strictly; ergonomics is delegated to "quality of
implementation." (b) Lack of buffer space makes standard send *block*, not fail — "in well-constructed
programs, this results in a useful **throttle effect**," automatically rate-limiting a fast producer
against a slow consumer [snir96-buffering]. Buffered mode trades deadlock for a diagnosable buffer-overflow
error, which the standard recommends "for debugging purposes, as buffer overflow conditions are easier to
diagnose than deadlock conditions" [mpi11 §3.5].

The same refusal applies to collectives: "The program should not depend upon whether collective
communication routines, such as `MPI_Bcast()`, act as barrier synchronizations" [snir96-portable]. A
portable program must both *not rely on* and *tolerate* synchronization side effects [dongarra96].

### 4.3 Local vs. non-local; blocking vs. synchronous; the MPI-4 semantic lattice

MPI-1 already separated two axes that most APIs conflate. MPI-4/4.1 formalized this into a small algebra
[mpi50 §2.4.2]:

> "**Nonlocal procedure:** An MPI procedure is nonlocal if returning may require, during its execution,
> some specific semantically-related MPI procedure to be called on another MPI process.
> **Local procedure:** An MPI procedure is local if it is not nonlocal." [mpi50 §2.4.2]

Operations decompose into *stages* (initialization, starting, completion, freeing), and procedures are
classified along two **orthogonal** axes — completeness and locality — with `blocking`/`nonblocking` defined
as *derived* terms:

> "**Nonblocking procedure:** An MPI procedure is nonblocking if it is incomplete and local.
> **Blocking procedure:** An MPI procedure is blocking if it is not nonblocking." [mpi50 §2.4.2]

This yields the non-obvious cells that a naive API would miss, and the standard enumerates them:
`MPI_ISEND`/`MPI_IRECV` are incomplete *and* local; `MPI_SEND`/`MPI_BCAST` are completing *and* nonlocal;
`MPI_MPROBE` and `MPI_BCAST_INIT` are **incomplete but nonlocal**; `MPI_BSEND`, `MPI_RSEND`, `MPI_MRECV`
are **completing but local** [mpi50 §2.4.2].

Separately, "blocking" ≠ "synchronous." Träff's gloss on `MPI_Ssend`: "This notion of a synchronous send is
less strict than the notion found in for example CSP. In MPI the send call may return before the receiver
has completed. **The only semantic guarantee is the start of the receive operation**" [traff09]. The four
send modes exist to expose protocol choice to the programmer: standard (implementation chooses), buffered
(user supplies buffer, always completes locally), synchronous (rendezvous), ready (user asserts the receive
is already posted, saving acknowledgment traffic and eliminating unexpected messages) [traff09,
dongarra96].

### 4.4 Opaque objects and handles

> "MPI manages system memory that is used for buffering messages and for storing internal representations
> of various MPI objects such as groups, communicators, datatypes, etc. This memory is not directly
> accessible to the user, and objects stored there are opaque: their size and shape is not visible to the
> user. Opaque objects are accessed via handles, which exist in user space." [mpi50 §2.5.1]

The rationale gives four distinct reasons:

> "**Rationale.** This design hides the internal representation used for MPI data structures, thus allowing
> similar calls in C and Fortran. It also avoids conflicts with the typing rules in these languages, and
> easily allows **future extensions of functionality**. ... The explicit separation of handles in user space
> and objects in system space allows space-reclaiming and deallocation calls to be made at appropriate
> points in the user program. If the opaque objects were in user space, one would have to be very careful
> not to go out of scope before any pending operation requiring that object completed. The specified design
> allows an object to be marked for deallocation, the user program can then go out of scope, and the object
> itself still persists until any pending operations are complete." [mpi50 §2.5.1]

The deferred-destruction semantics is precise: "A call to a deallocate routine invalidates the handle and
marks the object for deallocation. ... However, MPI need not deallocate the object immediately. Any
operation pending (at the time of the deallocate) ... will complete normally; the object will be deallocated
afterwards" [mpi50 §2.5.1]. And a hard restriction: "An opaque object and its handle are significant only
at the process where the object was created and **cannot be transferred to another process**"
[mpi50 §2.5.1].

Handles must support assignment and comparison "since such operations are common. This restricts the domain
of possible implementations. The alternative in C would have been to allow handles to have been an
arbitrary, opaque type. This would force the introduction of routines to do assignment and comparison,
adding complexity, and was therefore ruled out" [mpi50 §2.5.1]. This is a rare explicit statement of an
ergonomics-over-abstraction-purity trade.

### 4.5 Library-not-language and specification-not-implementation

Gropp lays out the trade-off honestly: "The design of MPI as a library means that MPI operations cannot be
optimized by a compiler. However, it also means that any MPI library can exploit the newest and best
compilers, and that the compiler can be developed without worrying about the impact of MPI on the generated
code — from the compiler's point of view, MPI calls are simply generic function calls" [gropp01]. He names
this property **composability**, and it extends to tools: "because MPI is simply a library, any debugger can
be used with MPI programs" [gropp01].

Hoefler makes the same point about adoption cost: "MPI's library interface requires no compiler support to
be added to most languages and can thus easily be implemented" [hoefler-xrds].

The "no implementation is required to be fast" tension is real but the standard handles it by convention
rather than normatively — it repeatedly uses the phrase "a high-quality implementation" to describe
behavior it wants but cannot mandate (e.g., "A high-quality implementation will provide generous limits on
the important resources" [mpi50 §2.8]; "high-quality implementations will take advantage" [mpi50 §7.4.4];
"quality implementations will provide sufficient buffering so that 'common practice' programs will not
deadlock" [mpi11 §3.5]). Gropp warns about the corollary: "several implementations of MPI fail to achieve
the available asymptotic bandwidth or latency ... They also underscore the **risk in evaluating the design
of a programming model based on a particular implementation**" [gropp01].

### 4.6 Compositionality and modularity as first-class goals

Gropp's six-requirement framework treats **modularity** and **composability** as separate necessary
conditions alongside portability, performance, simplicity/symmetry, and completeness [gropp01, gropp16].
His argument for modularity is the nested-library argument: hierarchical numerical algorithms need "each
level [to] require a different solution algorithm; it is not unusual to have each level require a different
decomposition of processes," and "**without something like a communicator it is possible for a message sent
by one component and intended for that component to be received by another component or by user code. MPI
made reliable libraries possible**" [gropp01].

A subtle corollary he draws: modularity *forbids* certain optimizations. "Certain powerful variable layout
tricks, such as assuming that the variable `a` in an SPMD program is at the same address on all processors,
must be modified... Some programming models have assumed that all processes have the same layout of local
variables, making it difficult or impossible to use those programming models with modern adaptive
algorithms" [gropp01].

**Symmetry** is the discipline that keeps the API learnable despite its size: "wherever possible routines
were added to eliminate any exceptions." His example is `MPI_Issend` — the nonblocking synchronous send —
"rarely used. Eliminating it would have removed a routine, slightly simplifying the MPI documentation and
implementation. It would have created an exception, however... it is easy to forget about a routine that
you never use; it is harder to remember arbitrary decisions on what is and is not available"
[gropp01]. He also concedes where symmetry went too far: the large set of group-manipulation routines
("particularly in MPI, the single routine `MPI_Comm_split` is all that is needed; few users need to
manipulate groups at all"), and cancellation of sends, "where significant implementation complexity is
required for an operation of dubious use" [gropp01].

**Completeness**: "MPI provides a complete programming model. Any parallel algorithm can be implemented
with MPI. Some parallel programming models have sacrificed completeness for simplicity. For example, a
number of programming models have required that synchronization happens only collectively for all processes
or tasks" [gropp01].

### 4.7 Derived datatypes and the zero-copy ambition

Datatypes are the second MPI invention (with communicators) that had no full precursor. They serve two
purposes with one concept: "the MPI datatype solves the two problems of describing the types of data to
allow for communication between systems with different data representations **and** of describing
noncontiguous data layouts to allow an MPI implementation to implement zero-copy data transfers of
noncontiguous data" [gropp01].

Mechanically, users build type descriptors from primitives via constructors (`MPI_Type_vector`,
`MPI_Type_indexed`, `MPI_Type_create_struct`, ...), commit them, and then pass `(buffer, count, datatype)`
triples. "These are not 'types' as far as the programming language is concerned. They are types only in that
MPI is made aware of them through type-constructor functions describing the layout in memory of sets of
primitive types" [dongarra96]. The canonical demonstration is sending the upper triangle of a matrix in a
single `MPI_Send` with an indexed type [dongarra96]. XDR, as used in PVM for heterogeneous transfer,
influenced the design and early implementations [traff09].

Why this counts as a signature innovation: it moves layout description *across the API boundary*, so the
implementation — not the user — decides whether to pack, to pipeline, to use scatter/gather DMA, or to
transfer in place. Hoefler's Lego-block framing: "the transpose of a parallel Fast Fourier Transformation
can be implemented with a single call to a nonblocking `MPI_Alltoall` when using datatypes"
[hoefler-xrds]. The catch is that the *implementation* burden is real and was underestimated: naive
recursive pack/unpack traversals are O(m) in elements rather than O(|T|) in the type DAG, and Träff's
"flattening on the fly" work was specifically motivated by the fact that "the simple, recursive packing and
unpacking implementation was a major problem" [traff09]. Gropp lists improving datatype implementations
among the top open problems as late as 2001 [gropp01].

---

## 5. Evolution across versions

Official version dates and document sizes (dates from [mpi-forum-docs] and [mpi40 §1]; page counts from
[schulz26]):

| Version | Date | Pages | Character |
|---|---|---|---|
| MPI-1.0 | 5 May 1994 | 228 | Original |
| MPI-1.1 | 12 Jun 1995 | 238 | Corrections |
| MPI-1.2 | 18 Jul 1997 | — | Clarifications; published inside the MPI-2 document |
| MPI-2.0 | 18 Jul 1997 | 608 | Major: RMA, dynamic processes, I/O, C++ / F90 bindings, threads, external interfaces |
| MPI-1.3 | 30 May 2008 | — | Consolidation of the MPI-1 line |
| MPI-2.1 | 4 Sep 2008 | 608 | Consolidation of MPI-1.x + MPI-2.0 + errata into one document |
| MPI-2.2 | 4 Sep 2009 | 647 | Minor; "seven new routines" |
| MPI-3.0 | 21 Sep 2012 | 852 | Major: nonblocking + neighborhood collectives, new RMA, MPI_T, F2008, C++ removed |
| MPI-3.1 | 4 Jun 2015 | 868 | Minor; Fortran binding fixes, nonblocking collective I/O |
| MPI-4.0 | 9 Jun 2021 | 1139 | Major: big-count, persistent collectives, partitioned comm., Sessions, error handling |
| MPI-4.1 | 2 Nov 2023 | 1166 | Minor; hardware-resource inquiry; deprecations (`mpif.h`, `MPI_HOST`) |
| MPI-5.0 | 5 Jun 2025 | 1189 | Major: standard **ABI** |

### 5.1 MPI-2.0 (1997)

The Forum resumed in March 1995 with work partitioned into five buckets — corrections; non-disruptive
additions; "completely new types of functionality (dynamic processes, one-sided communication, parallel
I/O, etc.) that are what everyone thinks of as 'MPI-2 functionality'"; Fortran 90 and C++ bindings; and
areas needing more experience, which were exiled to a non-normative **"Journal of Development"**
[mpi50 §1.3]. That JOD mechanism is itself a governance innovation: a standards-adjacent venue for ideas
that are promising but not ready.

Pressures and provenance:

- **One-sided RMA** (`MPI_Put`/`MPI_Get`/`MPI_Accumulate` + windows). Motivated by Cray SHMEM-style
  hardware and by algorithms with irregular, sender-unknown access patterns [snir-review].
- **Dynamic process management** (`MPI_Comm_spawn`, `MPI_Comm_connect`/`_accept`, name publishing).
  Directly PVM-driven: "to remain competitive, MPI-2 acquired dynamic process management available with
  PVM" [snir-review]; "the dynamic process management features of PVM influenced MPI-2" [traff09].
  Inter-communicators had been designed in MPI-1 in part to make this extension possible: they provide "a
  mechanism for the extension of MPI to a dynamic model where not all MPI processes are preallocated at
  initialization time" [mpi50 §7.2].
- **MPI-IO**. Originated *outside* the Forum: a 1994 IBM Research effort (Corbett, Feitelson, Hsu, Prost,
  Snir, with Fineberg, Nitzberg, Traversat, Wong of NASA Ames) built on the idea "that I/O can be modeled
  as message passing: writing to a file is like sending a message, and reading from a file is like
  receiving a message" [corbett94]. An independent "MPI-IO Committee" iterated to spec v0.5, then merged
  into the MPI Forum in summer 1996; Bill Nitzberg chaired the chapter; ROMIO tracked the evolving spec
  "similar to the way MPICH tracked the MPI spec" [thakur-retro]. This is a clean example of *chartering
  an adjacent standard and absorbing it*.
- **Thread support levels** — the four-level `MPI_Init_thread` contract, added because "threaded
  parallelism was seen by the MPI Forum as a likely approach to systems built from a collection of SMP
  nodes," and levels exist because "there are performance tradeoffs in different degrees of threadedness"
  [gropp01].
- **C++ bindings** and Fortran 90 support; **external interfaces** (generalized requests, datatype
  decoding) for tool and library builders.

### 5.2 MPI-2.2 (2009): the admission criteria

MPI-2.2 is small but its *rules* are the most transplantable artifact in the whole evolution:

> "a small number of extensions to MPI-2.1 that met the following criteria:
> - Any correct MPI-2.1 program is a correct MPI-2.2 program.
> - Any extension must have significant benefit for users.
> - **Any extension must not require significant implementation effort. To that end, all such changes are accompanied by an open source implementation.**" [mpi50 §1.5]

A standards body requiring a working open-source implementation as a condition of admission is a strong
anti-speculation device.

### 5.3 MPI-3.0 (2012)

- **Nonblocking collectives** (`MPI_Ibcast`, `MPI_Iallreduce`, ..., including `MPI_Ibarrier`). Eighteen
  years overdue: the CACM paper already noted that "a standing joke at committee meetings concerned the
  nonblocking barrier, [but] such functions can be quite useful and may be included in a future version of
  MPI" [dongarra96]. The evidence base was built outside the Forum: LibNBC and its measurement papers
  [hoefler07-sc, hoefler07-case], the CG-solver case study [hoefler06-cg], and the observation that
  nonblocking collectives mitigate **OS/system noise** as well as enabling overlap [widener-noise].
  Motivation: blocking collectives cause pseudo-synchronization and latency sensitivity that limit scaling
  [hoefler07-sc].
- **Neighborhood collectives** (`MPI_Neighbor_allgather`, `MPI_Neighbor_alltoall{,v,w}` and `I` variants) on
  virtual topologies. Origin: Hoefler & Träff's "sparse collective operations" [hoefler-traff09], whose key
  argument is that a *collective handle* is necessary so the implementation can schedule and optimize with
  global information rather than local information only. Practical payoff: a scalable replacement for
  `MPI_Alltoallv` in stencil codes, which otherwise requires O(P) vectors per process [archer-mpi-evol].
- **RMA overhaul**: `MPI_Win_allocate`, dynamic windows, `MPI_Rput`/`MPI_Rget`/`MPI_Raccumulate` (request-
  returning, locally completable), `MPI_Win_flush{,_all}`, atomics (`MPI_Fetch_and_op`,
  `MPI_Compare_and_swap`), and — crucially — a second memory model. The **separate** model is MPI-2's; the
  new **unified** model exploits the fact that "HW support for coherency [is] now widespread" and
  "simplifies memory consistency rules on cache-coherent machines" [gropp22, archer-mpi-evol]. Formal
  axiomatic models for the new semantics were published because the English prose was too hard to reason
  about: such models "can help users reason about details of the semantics that are hard to extract from the
  English prose in the standard" [hoefler15-rma].
- **`MPI_Comm_split_type` + `MPI_COMM_TYPE_SHARED` and `MPI_Win_allocate_shared`**: the direct answer to
  multicore nodes. Split a communicator into subcommunicators whose members can mutually share memory, then
  allocate a shared window over them. Motivations given: fast intra-node access and reduced memory
  consumption by sharing large static structures (e.g., lookup tables) [hoefler-mpi30-blog,
  hoefler-mpi30-slides]. This is "MPI+MPI" — using MPI at two hierarchy levels [hoefler-xrds].
- **`MPI_Comm_idup`** — nonblocking communicator duplication [mpi-3.0-changes]. (Note: this is an MPI-3.0
  feature, not MPI-4.0; `MPI_Comm_idup_with_info` is the MPI-4.0 addition [mpi50 §7.4.2].) It matters for
  libraries: `MPI_Comm_dup` is collective and therefore a synchronization point at every library entry.
- **`MPI_Comm_create_group`** — non-collective (group-local) communicator creation.
- **Matched probe** (`MPI_Mprobe`/`MPI_Mrecv`) — the thread-safe probe-then-receive idiom, since
  probe-then-recv is racy under `MPI_THREAD_MULTIPLE`.
- **MPI_T tool information interface** — a query-based, implementation-agnostic channel for control and
  performance variables. Its design virtue is that "it does not impose any specific structure or
  implementation choice (such as having an eager protocol) on the MPI implementations... it doesn't really
  have to offer anything to the user" while "a 'high quality' MPI implementation may use this interface to
  expose relevant state" [hoefler-mpi30-blog].
- **C++ bindings removed** (deprecated in MPI-2.2), along with deprecated MPI-1 routines such as
  `MPI_Address`, `MPI_Type_struct`, `MPI_UB`/`MPI_LB` [mpi-3.0-changes, mpi50 §1.6]. The C++ bindings failed
  because they were a thin syntactic wrapper that neither C++ programmers nor implementers wanted; the
  Forum's answer was "make C++ optional... remove the deprecated bindings (any users?)"
  [hoefler-mpi30-slides]. New **Fortran 2008** (`mpi_f08`) bindings with type-safe handles and correct
  asynchrony semantics were added [hoefler-mpi30-slides].

### 5.4 MPI-4.0 (2021), 4.1 (2023), 5.0 (2025)

- **Large-count / "big count"**: `int`/`INTEGER` counts overflow at 2^31 elements. MPI-4.0 adds `_c`-suffixed
  variants in C and Fortran overloads, using `MPI_Count` [mpi50 §1.8, mpi-4.0-changes]. This roughly doubles
  the nominal function count and is the largest single contributor to MPI-4.0's page growth (868 → 1139).
- **Persistent collectives** (`MPI_Bcast_init`, `MPI_Allreduce_init`, ...): amortize schedule/algorithm
  selection and resource setup across repeated identical collectives, and enable offload to network
  hardware. Note their unusual semantic classification: `MPI_BCAST_INIT` is **incomplete and nonlocal**
  [mpi50 §2.4.2].
- **Partitioned communication** (`MPI_Psend_init`/`MPI_Precv_init` + `MPI_Pready`/`MPI_Parrived`): split a
  buffer into equal partitions that can be marked ready independently. Motivation is explicitly
  MPI+threads and MPI+GPU: it reduces lock contention, allows early data transfer, and is "applicable to
  highly threaded CPU side MPI codes but has significant predicted utility for GPU-side MPI kernel calls";
  proposals exist for triggering `MPI_Pready` from inside a GPU kernel [ghafoor21]. The interface
  deliberately makes the *user* define equal-size partitions rather than the library, to avoid pathological
  edge cases [ghafoor21].
- **Sessions model** (`MPI_Session_init`, process sets, `MPI_Group_from_session_pset`,
  `MPI_Comm_create_from_group`): an alternative to `MPI_Init`/`MPI_COMM_WORLD`. The standard's own list of
  World-Model limitations: "MPI cannot be initialized from different application components without a
  priori knowledge or coordination; MPI cannot be initialized more than once; and MPI cannot be
  reinitialized after `MPI_FINALIZE` has been called" [mpi41 §11.3]. The research motivation is broader:
  remove `MPI_COMM_WORLD` as a scalability barrier by "no longer requiring all possible communication peers
  to be included in `MPI_COMM_WORLD`," give components isolated environments with **per-session thread
  support levels**, allow independent libraries to use MPI without coordinating initialization, and scope
  error handling to subsets of processes [holmes16]. The stated goal is to "remove the need for 'heroic
  developer efforts' to adapt MPI implementations and applications for the future scaling challenges"
  [holmes16].
- **Error handling improvements** and **application info assertions** (`mpi_assert_no_any_tag`,
  `mpi_assert_allow_overtaking`, etc.) — user-supplied promises that let the implementation relax
  guarantees, e.g., asserting that "send operations are not required to be matched at the receiver in the
  order in which the send operations were posted" [mpi50 §7.4.4].
- **MPI-4.1**: mostly clarifications; adds hardware-resource inquiry; deprecates `mpif.h`, `MPI_HOST`, and
  several routines [mpi50 §1.9].
- **MPI-5.0**: "The largest change is the addition of a standard Application Binary Interface (ABI) to allow
  interoperability of different implementations" [mpi50 §1.10]. The problem statement is worth quoting in
  full because it is a textbook account of what under-specification costs: the API "defines... a set of
  opaque handle types and named constants **without specifying their memory layout or values**... However,
  this flexibility means that **different implementations are incompatible from the perspective of compiled
  applications**, because the ABI is not specified" [mpi50 §21.1]. ABI v1.0 is versioned independently of
  the API; the library is `libmpi_abi`; MPICH implemented it first (heavily tested via mpi4py); Open MPI
  shipped support in v6.0.0 but deliberately omits a *Fortran* ABI [mpi-bof-isc25, ompi-abi]. MPI-5.0 also
  introduces **side-documents** as a formal category: versioned companion specs that "shall not modify any
  aspects defined in the MPI Standard without providing a mechanism that explicitly enables these
  deviations" [mpi50 §1.14].
- **MPI-6.0 direction** (announced at ISC'25): partitioned communication, new tools interfaces,
  hybrid/accelerated computing including GPU bindings, dynamic resource management via Sessions, **fault
  tolerance**, and revamped I/O and RMA [mpi-bof-isc25].

---

## 6. Criticisms and known weaknesses

### 6.1 "MPI is too big"

The size complaint is as old as the standard and has a well-rehearsed defense. The numbers: MPI-1 has
**128** routines [snir96-whybig] (Gropp's slides say "about 125" [gropp-slides]); MPI-2 adds "about 150
more" [gropp-slides]; by 2003 "MPI-2.0 defined over 300 API functions" [aosa-ompi]; contemporary counts
give "500+ MPI functions" [hpcwiki]; MPI-4.0's large-count variants push higher again. Document length is
the cleaner metric: **228 → 1189 pages** from 1994 to 2025 [schulz26].

The standard's own defense: MPI is big for two reasons — richness of functionality (derived datatypes,
communicators, caching, topologies, full collectives) and hardware diversity (the send modes exist "mainly
as a means of providing a set of the most widely-used communication protocols"). And the alternative is
worse: "One could decrease the number of functions by increasing the number of parameters in each call. But
such approach would increase the call overhead and would make the use of the most prevalent calls more
complex" [snir96-whybig].

Gropp's reframing is the strongest form: "The number of routines is not a relevant measure... A better
measure of complexity is the number of concepts that the user must learn, along with the number of
exceptions and special cases. Measured in these terms, MPI is actually very simple" [gropp01]. The
canonical demonstration — popularized by the *Using MPI* textbook [gropp-usingmpi] — is the "six functions"
subset: `MPI_Init`, `MPI_Finalize`, `MPI_Comm_size`, `MPI_Comm_rank`, `MPI_Send`, `MPI_Recv`, with the
important gloss that there are *multiple* useful six-function subsets (e.g., swap send/recv for `MPI_Bcast`/`MPI_Reduce`), and "one key to the success of MPI
is that these subsets can be used without learning the rest of MPI" [gropp01, gropp-slides]. Träff calls
this "economy of concepts" [traff09]. Empirical support from an early users' poll: "while no one was using
all of the MPI routines, essentially all MPI routines were in use by someone" [gropp01, mpi-poll95].

A 1995 user poll response shows the counter-pressure was real: "There should be a stripped version of MPI
for single group code, which uses only the six basic MPI calls for message passing. A majority of user
codes in MPI are this type, and the less overhead in the language, the better" [mpi-poll95].

### 6.2 MPI-2 RMA is widely considered hard to use

The most concrete design failure. Gropp's own retrospective assessment: "MPI-2 RMA had limited adoption:
complex memory model hard to explain; limitations on passive target memory limit usefulness; limitations on
operations, memory, etc.; poor performance of implementations — often unnecessarily so" [gropp22]. From the
MPI-3 RMA design effort: "even 12 years after its existence, the MPI-2 RMA interface remains scarcely used
for a number of reasons" [icpp09-rma].

The clearest single artifact of the problem is MPI-2's own rationale for forbidding concurrent accesses to
*non-overlapping* locations in a window:

> "The last constraint on correct RMA accesses may seem unduly restrictive, as it forbids concurrent
> accesses to nonoverlapping locations in a window. The reason for this constraint is that, on some
> architectures, explicit coherence restoring operations may be needed at synchronization points... Without
> this constraint, the MPI library will have to track precisely which locations in a window were updated by
> a put or accumulate call. The additional overhead of maintaining such information is considered
> prohibitive." [mpi20 §11.7]

That is a normative user-facing restriction adopted to spare implementations bookkeeping on a subset of
1997 hardware. Bonachea and Duell's IJHPCN critique is the sharpest external verdict: "The newer MPI-RMA
API imposes too many semantic restrictions to be a useful portable compilation target, at least for parallel
languages which allow aliasing, data conflicts and/or the illusion of a single, arbitrarily accessible
shared address space" [bonachea04]. Their broader claim is that neither MPI-1.1 nor MPI-2 RMA is an
adequate compilation target for PGAS languages — a serious charge for something described as "the assembly
language of parallel programming."

MPI-3 fixed much of this but by *addition*, not replacement: "added to MPI-2 RMA — keeping all features from
(then) 15 years before" [gropp22]. The MPI-3 RMA interface did prove implementable and fast — see Dinan et
al.'s implementation and evaluation [dinan16-rma] and Gerstenberger, Besta, and Hoefler's demonstration of
highly scalable MPI-3 one-sided programming [gerstenberger14] — which makes the usability verdict on MPI-2
RMA a *specification* failure rather than an implementation one. Gropp's 2022 conclusion: "25+ years is too long to simply tweak the
programming model to match the hardware — MPI RMA should be rethought from the ground up" [gropp22].

### 6.3 Fault tolerance: the most-cited deficiency

MPI-1's "assume a reliable communication interface" goal [mpi50 §1.1] produced the situation Geist, Kohl,
and Papadopoulos described in 1996: "**The MPI specification states that the only thing that is guaranteed
after an MPI error is the ability to exit the program**" [geist96]. They named it as one of two central MPI
weaknesses (the other being cross-implementation interoperability) and contrasted PVM's notify mechanism,
where "if a task dies, the receiving task will get a notify message in place of any expected message"
[geist96, kluge04].

The remediation history:

- **FT-MPI** (U. Tennessee) [fagg00-ftmpi]: automatic repair of `MPI_COMM_WORLD` under replace/shrink/blank
  policies. But "the shrink and replace modes are synchronous, and the approach is global in nature"
  [bouteiller22].
- **MPI Reinit**, **FMI**, and replication/checkpoint approaches: "One detriment to masking fault tolerance
  is the typically high cost incurred on failure-free operation" [bouteiller22].
- **ULFM (User Level Failure Mitigation)**: a deliberately minimal, low-level API — revoke a damaged
  communicator, `MPI_COMM_AGREE` for consensus on failure knowledge, shrink to obtain a working replacement
  — chosen because "ULFM proposes a flexible low-level API that supports a variety of fault tolerance
  models," whereas the alternatives "propose embracing a monolithic recovery model that supports a single
  mode of recovery, one that is always operating at a global scope" [bouteiller22]. Design requirements:
  consistent fault reporting without unacceptable failure-free cost; building blocks that let independent
  application components "recover without interfering with each other"; and overlap of recovery with useful
  work [bouteiller22]. Implementations must provide the procedures even if they never actually tolerate
  failures — stubs based on `MPI_Allreduce` are permitted [ulfm-issue20, ulfm-issue582].
- **Status as of the most recent Forum activity I could verify**: ULFM is *not yet* in the ratified
  standard. It has been split into "slices" (fault model, agree, shrink) to make Forum review tractable; a
  December 2025 meeting reviewed the fault-model text and agreed to a no-no vote at the following meeting
  [forum-2025-12]. Fault tolerance is listed as an MPI-6.0 direction [mpi-bof-isc25].

Thirty-one years from the 1994 goal statement to a still-unratified fault model is the single strongest
cautionary data point in MPI's history.

### 6.4 "MPI everywhere" vs. "MPI+X", and MPI+threads

"MPI everywhere" (one MPI rank per core) vs. "MPI+X" (MPI between nodes, X within) is the standing
architectural debate. Gropp's position: "MPI as the internode programming system seems likely... There are
no intractable problems here — MPI implementations can be engineered to support Exascale systems, even in
the MPI everywhere approach," but "**MPI+X won't be enough for Exascale if the work for the '+' is not done
very well**" — and the asymmetry is that MPI+X is separable for *users* but not for *developers*: "MPI and
X must either partition or share resources. User must not blindly oversubscribe. Developers must negotiate"
[gropp17]. Thakur, Balaji, Gropp et al. predicted MPI would be used "as part of a 'hybrid' programming
model (MPI+X), much more so than it is today" [thakur10-exascale].

The MPI+threads performance story is the concrete failure. From Argonne's 2022 assessment: "While
MPI+Threads has been the answer to the compatibility side of MPI+X, the performance side has been a
multi-decade struggle" [mpix-stream]. And more bluntly: "the most [pressing bottleneck] is the dismal
communication performance of MPI+threads"; even after MPI-4.0, "MPI 4.0 does not meet the needs of
MPI+threads applications, and... it introduces new problems" [lessons-threads]. The root cause identified is
a *semantic* one — not merely lock engineering, though the lock-contention design space has itself been
studied in depth [amer18-locks]: MPI has no way to express communication independence between threads, so users must abuse
communicators or tags to convey it, and "using communicators, the most explicit existing mechanism, to
expose communication independence is quite complex even for applications with well-structured and regular
communication patterns" [lessons-threads]. The MPIX Stream proposal also documents the API-explosion
hazard: adding a stream argument to every operation "will need... a new API for every MPI operation,"
requires *remote* stream identification for point-to-point, and "an array of stream arguments, one for every
participating process" for collectives [mpix-stream].

### 6.5 Language-design critiques

Per Brinch Hansen's SIGPLAN evaluation is the most pointed published attack. He rewrote three model
programs in MPI/C and concluded that "MPI is a practical programming tool. It does, however, lack the
elegance and security that can only be achieved by a parallel programming language" [brinchhansen98]. His
mechanism-level complaint: "Since C does not support parallelism directly, message passing must be handled
by library procedures. And these procedures must be general enough to handle all possible communications
without any help from a compiler. As a result, the communication procedures now require 6-7 (instead of 2)
parameters." His verdict: "**Personally, I regard the attempt to replace a parallel programming language and
its compiler with insecure procedures as a step backwards in programming technology**" [brinchhansen98]. A
rejoinder from the HPF community noted that Brinch Hansen "devotes two full pages in his MPI article to
explicit message-passing code to manage his shadow edges," all of which an HPF compiler generates
automatically [hpf-response].

Gropp's answer to the "assembly language" framing: "MPI is sometimes called the assembly language of
parallel programming. Those making this statement forget that C and Fortran have also been described as
portable assembly languages. **The generality of the approach should not be mistaken for an unnecessary
complexity**" [gropp01].

### 6.6 "Is MPI dead?"

The recurring debate. Its high-water mark is a 2006 HPCwire piece on DARPA HPCS, reporting that "the
supercomputing community is almost unanimous in its desire to move beyond MPI" and quoting Rusty Lusk —
MPICH's lead — saying: "**Nobody loves MPI. When people criticize it, I'll stand up and defend it. But when
we developed MPI, the idea was that it would be used to write portable libraries; actual users should never
have to confront it. But a user language has never really evolved**" [hpcwire06]. That last sentence is the
key admission: MPI's designers considered direct end-user exposure a *failure mode* of the ecosystem, not
an intended outcome.

The PGAS-side critique: exascale memory hierarchies are "at odds with the 2-level hierarchy of MPI
('local' vs 'remote') or even OpenMP+MPI combinations. This might be the very opening for a new PGAS-like
language" — but coupled with the pragmatic conclusion that "**no programming model today can survive in the
HPC era unless it can complement and succeed MPI**" [almasi-abstract]. Gropp's own list of MPI weaknesses
[gropp16]: no built-in support for user distributions ("Darray and Subarray don't count"); no built-in
support for dynamic execution; "performance cost of interfaces; overhead of calls; rigidity of choice of
functionality"; "I/O is capable but hard to use." Empirically the prediction of death has not held:
MPICH-derived stacks power Aurora, Frontier, and El Capitan [mpich-bof21].

### 6.7 The document itself

MPI's specification is 1189 pages of English prose with normative `Rationale` and `Advice to users` blocks
[mpi50, schulz26]. That the MPI-3 RMA authors felt obliged to publish "formal axiomatic models for data
consistency and access semantics" because "such models can help users reason about details of the semantics
that are **hard to extract from the English prose in the standard**" [hoefler15-rma] is a direct
acknowledgment that natural-language specification hit its limit.

---

## 7. Cultural and sociological observations

### 7.1 Reference implementations as the real standardization mechanism

**MPICH** was not a post-hoc implementation; it was an instrument of the standardization process. "MPICH was
originally developed **during the MPI standards process starting in 1992 to provide feedback to the MPI
Forum on implementation and usability issues**" [mpich-overview]. The name is "MPI over CHameleon"
[mpich-overview]. Walker credits "mpich implementation available early on" as one of five reasons MPI-1
succeeded [walker17]; Träff: "It helped tremendously that MPI was closely followed by a template
implementation that was already in its first versions of good quality" [traff09].

The architectural mechanism that made MPICH both a reference and a production base is the **Abstract Device
Interface**. Two stated design principles: maximize shared code without compromising performance ("a large
amount of the code in any implementation is system independent. Implementation of most of the MPI opaque
objects, including datatypes, groups, attributes, and even communicators, is platform-independent"), and
allow a fast port followed by incremental tuning "by replacing parts of the shared code by platform-specific
code" [gropp96-mpich]. Below the ADI sits a **channel interface** that "can be extremely small (five
functions at minimum) and provides the quickest way to port MPICH to a new environment" [gropp96-mpich].
ROMIO replicated the pattern for I/O with ADIO [romio, thakur99-io]. **A portable core over a minimal
swappable device layer is the reproducible template for turning a specification into a de facto standard.**

Naming discipline note: MPICH-1 → MPICH2 (from ~2001, for MPI-2 features) → renamed back to MPICH at v3.0
in November 2012 [mpich-overview].

### 7.2 Open MPI and the consolidation of the research implementations

**Open MPI** is the merger of **LAM/MPI** (Ohio Supercomputer Center → Notre Dame → Indiana), **LA-MPI**
(Los Alamos), and **FT-MPI** (Tennessee), with **PACX-MPI** (HLRS Stuttgart) joining shortly after
[ompi-history, ompi-faq]; the design paper is Gabriel et al. at EuroPVM/MPI 2004 [gabriel04-ompi]. The
founding story is explicitly social: "The lead developers of these projects
kept bumping into each other at various HPC conferences in 2003... it finally dawned on us that we are doing
a lot of the same things in each of our respective implementations" [ompi-history]. At SC2003 they decided
to "start an entire new code base — leaving all the cruft and legacy code of our prior implementations
behind. **Take the best, leave the rest.**" First commit 22 November 2003; serious development from 5
January 2004; first release Q1 2005 [ompi-history, ompi-sc06].

The reasons for a clean slate rather than a merge are instructive: the four code bases "had radically
different implementation architectures, and would be incredibly difficult (if not impossible) to merge";
each had features worth carrying forward and code worth abandoning; and the scale was daunting — MPI-2.0
"defined over 300 API functions" and LAM/MPI alone "had over 1,900 files of source code, comprising over
300,000 lines" [aosa-ompi]. Note the lineage irony: FT-MPI's fault-tolerance research DNA entered Open MPI,
and Open MPI became the ULFM prototype vehicle [ulfm-issue20].

The resulting duopoly is a genuine two-implementation ecosystem with different missions: "MPICH is supposed
to be high-quality reference implementation of the latest MPI standard and the basis for derivative
implementations to meet special purpose needs. Open-MPI targets the common case, both in terms of usage and
network conduits" [so-mpich-ompi].

### 7.3 Vendor derivatives: "MPICH and its derivatives"

MPICH "is used as the foundation for many other MPI implementations, including IBM MPI (for Blue Gene),
Intel MPI, Cray MPI, Microsoft MPI, CDAC MPI, Myricom MPI, OSU MVAPICH/MVAPICH2" [wikipedia-mpich]. Intel
describes its product as "a multifabric message-passing library that implements the open source MPICH
specification" [intel-mpi]. MPICH's own BoF framing: "MPICH is not just a software; it's an **ecosystem**,"
funded by DOE for 28 years, and "MPICH and its derivatives are the world's most widely used MPI
implementations," powering Aurora (MPICH), Frontier (Cray MPI), and El Capitan (Cray MPI) [mpich-bof21].

The evaluative consequence, per a widely cited practitioner comparison: "if one is willing to define MPICH
as 'MPICH and its derivatives,' then MPICH has extremely broad network support" [so-mpich-ompi]. **A
permissively licensed, well-layered reference implementation became the substrate on which competitors
differentiated at the device layer — exactly what the ADI design anticipated.**

### 7.4 The ABI debate

MPI standardized source portability in 1994 and binary portability in **2025** — a 31-year gap. The
intermediate history is a series of partial fixes:

- **IMPI** (Interoperable MPI), a separate interoperability standard "that provides sufficient
  standardization for some implementation details so that implementations conforming to this standard can
  exchange messages" [gropp02-pvm].
- The **MPICH ABI Compatibility Initiative**, started 2013, with the "explicit goal of maintaining ABI
  compatibility between multiple MPICH derivatives": MPICH (v3.1, 2013), Intel MPI (v5.0, 2014), Cray MPT
  (v7.0, 2014), MVAPICH2 (v2.0, 2017), ParaStation MPI (2017) [mpich-bof21]. This is a *coalition* ABI, not
  a standard ABI — it unified one lineage while leaving Open MPI outside.
- Third-party shims — Mukautuva, wi4mpi, MPItrampoline — existed precisely to paper over the missing ABI
  [mpi-bof-isc25].
- **MPI-5.0 Chapter 21**: a standard ABI, versioned independently from the API, delivered as `libmpi_abi`
  with `#include <mpi.h>` still working. Rationale includes third-party language bindings "that intend to
  interface with MPI through binary symbol names, rather than direct function calls to the C API"
  [mpi50 §21.1, mpi-bof-isc25]. Gropp had earlier flagged this as developer-level rather than user-level
  standardization: "A simple example is the MPI ABI specification — users should ignore but benefit from
  developers supporting" [gropp17].

---

## Direct quotes worth citing in the paper

All verbatim. Source and location given for each.

1. **On what MPI is.** "MPI is a specification, not an implementation; there are multiple implementations of
   MPI. This specification is for a library interface; MPI is not a language, and all MPI operations are
   expressed as functions, subroutines, or methods."
   — MPI-5.0 §1.1 Overview and Goals [mpi50].

2. **On the reliability assumption (the fateful goal).** "Assume a reliable communication interface: the
   user need not cope with communication failures. Such failures are dealt with by the underlying
   communication subsystem."
   — MPI-5.0 §1.1, goal list [mpi50]; identical in MPI-1.1 §1.1 [mpi11].

3. **On standardizing existing practice rather than inventing.** "Define an interface that is not too
   different from current practice, such as PVM, NX, Express, p4, etc., and provides extensions that allow
   greater flexibility."
   — MPI-5.0 §1.1, goal list [mpi50].

4. **On refusing to guarantee buffering.** "The reluctance of MPI to mandate whether standard sends are
   buffering or not stems from the desire to achieve portable programs. Since any system will run out of
   buffer resources as message sizes are increased, and some implementations may want to provide little
   buffering, MPI takes the position that correct (and therefore, portable) programs do not rely on system
   buffering in standard mode."
   — MPI-1.1 §3.4, Rationale [mpi11]; retained through MPI-5.0.

5. **On the definition of a safe program.** "A program is 'safe' if no message buffering is required for the
   program to complete. One can replace all sends in such program with synchronous sends, and the program
   will still run correctly."
   — MPI-1.1 §3.5, Semantics of point-to-point communication [mpi11].

6. **On portability = safety.** "Within the context of MPI, 'portable' is synonymous with 'safe.' Unsafe
   programs may exhibit a different behavior on different systems because they are non-deterministic."
   — Snir, Otto, Huss-Lederman, Walker, Dongarra, *MPI: The Complete Reference*, "Portable Programming with
   MPI" [snir96-portable].

7. **On why libraries need communicators.** "A key feature needed to support robust, parallel libraries is a
   guarantee that communication within a library routine does not conflict with communication extraneous to
   the routine. The concepts encapsulated by an MPI communicator provide this support... Contexts partition
   the communication space. A message sent in one context cannot be received in another context."
   — Dongarra, Otto, Snir, Walker, *CACM* 39(7), 1996 [dongarra96].

8. **On the system, not the programmer, owning namespacing.** "Contexts provide the ability to have separate
   safe 'universes' of message-passing in MPI. A context is akin to an additional tag that differentiates
   messages. The system manages this differentiation process. ... [This] avoids the need to synchronize entry
   or exit into library code."
   — MPI-5.0 §7.2.3 Intra-Communicators [mpi50].

9. **On the requirements list for library support.** "The key features needed to support the creation of
   robust parallel libraries are as follows: Safe communication space... Group scope for collective
   operations... Abstract naming of MPI processes... The ability to 'adorn' a set of communicating MPI
   processes with additional user-defined attributes."
   — MPI-5.0 §7.1.1 Features Needed to Support Libraries [mpi50].

10. **On opaque objects.** "The explicit separation of handles in user space and objects in system space
    allows space-reclaiming and deallocation calls to be made at appropriate points in the user program. If
    the opaque objects were in user space, one would have to be very careful not to go out of scope before
    any pending operation requiring that object completed."
    — MPI-5.0 §2.5.1, Rationale [mpi50].

11. **On locality as a first-class concept.** "Nonlocal procedure: An MPI procedure is nonlocal if returning
    may require, during its execution, some specific semantically-related MPI procedure to be called on
    another MPI process."
    — MPI-5.0 §2.4.2 MPI Procedures [mpi50].

12. **On blocking as a derived, not primitive, notion.** "Nonblocking procedure: An MPI procedure is
    nonblocking if it is incomplete and local. Blocking procedure: An MPI procedure is blocking if it is not
    nonblocking."
    — MPI-5.0 §2.4.2 [mpi50].

13. **On being explicit about the limits of guarantees.** "[These issues] are merely a consequence of the
    desire of MPI to do two things: Allow efficient implementations on a variety of architectures; Be clear
    about exactly what is and what is not guaranteed by the standard."
    — Dongarra, Otto, Snir, Walker, *CACM* 39(7), 1996 [dongarra96].

14. **The design maxim.** "It is more important to make the hard things possible than it is to make the easy
    things easy."
    — Gropp, "Learning from the Success of MPI," Conclusion [gropp01]. Widely quoted in the shorter form
    "MPI was designed not to make easy things easy, but to make difficult things possible" (Gropp,
    EuroPVM/MPI 2004, as recorded in [traff09]).

15. **On portability not meaning lowest common denominator.** "Portability, however, does not require taking
    a lowest common denominator approach. A good design allows the use of performance enhancing features
    without mandating them."
    — Gropp, "Learning from the Success of MPI" [gropp01]. In talk form: "MPI is really a 'Greatest Common
    Denominator' approach" [gropp16].

16. **On symmetry as an anti-exception discipline.** "Each such exception adds to the burden on the user and
    adds complexity; it is easy to forget about a routine that you never use; it is harder to remember
    arbitrary decisions on what is and is not available."
    — Gropp, "Learning from the Success of MPI" [gropp01].

17. **On modularity making libraries possible.** "Without something like a communicator it is possible for a
    message sent by one component and intended for that component to be received by another component or by
    user code. MPI made reliable libraries possible."
    — Gropp, "Learning from the Success of MPI" [gropp01].

18. **On completeness.** "MPI provides a complete programming model. Any parallel algorithm can be
    implemented with MPI. Some parallel programming models have sacrificed completeness for simplicity. For
    example, a number of programming models have required that synchronization happens only collectively for
    all processes or tasks."
    — Gropp, "Learning from the Success of MPI" [gropp01].

19. **On the "assembly language" charge.** "MPI is sometimes called the assembly language of parallel
    programming. Those making this statement forget that C and Fortran have also been described as portable
    assembly languages. The generality of the approach should not be mistaken for an unnecessary complexity."
    — Gropp, "Learning from the Success of MPI" [gropp01].

20. **On who MPI was for.** "Nobody loves MPI. When people criticize it, I'll stand up and defend it. But
    when we developed MPI, the idea was that it would be used to write portable libraries; actual users
    should never have to confront it. But a user language has never really evolved."
    — Ewing "Rusty" Lusk, quoted in *HPCwire*, 25 August 2006 [hpcwire06].

21. **On orthogonality as MPI's real strength.** "MPI's major strength is due to its clear organization
    around a relatively small number of orthogonal concepts. These include communication contexts
    (communicators), blocking/nonblocking, datatypes, collective communications, remote memory access, and
    some more. These concepts work really like Lego blocks and can be combined into powerful functions."
    — Torsten Hoefler, ACM XRDS interview, 2017 [hoefler-xrds].

22. **On why the standard is big.** "There are two fundamental reasons for the size of MPI. The first reason
    is that MPI was designed to be rich in functionality... The second reason for the size of MPI reflects
    the diversity and complexity of today's high performance computers. ... One could decrease the number of
    functions by increasing the number of parameters in each call. But such approach would increase the call
    overhead and would make the use of the most prevalent calls more complex."
    — Snir et al., *MPI: The Complete Reference*, "Why is MPI so big?" [snir96-whybig].

23. **On the MPI-2 RMA restriction that damaged adoption.** "The last constraint on correct RMA accesses may
    seem unduly restrictive, as it forbids concurrent accesses to nonoverlapping locations in a window. The
    reason for this constraint is that, on some architectures, explicit coherence restoring operations may
    be needed at synchronization points... The additional overhead of maintaining such information is
    considered prohibitive."
    — MPI-2.0 §11.7 Semantics and Correctness, Rationale [mpi20].

24. **On starting over vs. tweaking.** "25+ years is too long to simply tweak the programming model to match
    the hardware — MPI RMA should be rethought from the ground up to meet current hardware."
    — Gropp, "MPI RMA: Is It Time To Start Over?", 2022 [gropp22].

25. **On the cost of not specifying the ABI.** "This flexibility means that different implementations are
    incompatible from the perspective of compiled applications, because the Application Binary Interface
    (ABI) is not specified."
    — MPI-5.0 §21.1 Application Binary Interface, Introduction [mpi50].

26. **On the guarantee after an error.** "The MPI specification states that the only thing that is guaranteed
    after an MPI error is the ability to exit the program."
    — Geist, Kohl, Papadopoulos, "PVM and MPI: A Comparison of Features," 1996 [geist96].

27. **On why ULFM is minimal rather than automatic.** "Compared to ULFM — which proposes a flexible low-level
    API that supports a variety of fault tolerance models — these alternatives propose embracing a monolithic
    recovery model that supports a single mode of recovery, one that is always operating at a global scope."
    — Bouteiller et al., "Implicit Actions and Non-blocking Failure Recovery with MPI" [bouteiller22].

28. **On the admission bar for extensions.** "Any extension must not require significant implementation
    effort. To that end, all such changes are accompanied by an open source implementation."
    — MPI-5.0 §1.5, Background of MPI-2.2 [mpi50].

29. **On the language-vs-library objection.** "Personally, I regard the attempt to replace a parallel
    programming language and its compiler with insecure procedures as a step backwards in programming
    technology."
    — Per Brinch Hansen, "An Evaluation of the Message-Passing Interface," ACM SIGPLAN Notices 33(3), 1998
    [brinchhansen98].

30. **On why a low-level standard was rejected.** "The hardware of different distributed memory computing
    systems is sufficiently varied that it is difficult to impose a low-level standard that is efficient on
    all machines. Therefore, it is more appropriate to define a standard at an intermediate level, and to
    implement this as efficiently as possible on each machine."
    — Walker, *Standards for Message-Passing in a Distributed Memory Environment* (CRPC Williamsburg
    workshop report), 1992 [walker92].

---

## Implications for a multi-agent analogue

Twenty observations, each stating what transfers, what does not, and why.

**T1. The communicator abstraction transfers almost unchanged, and is the single highest-value import.**
The exact failure mode MPI invented contexts to prevent — a promiscuous wildcard receive swallowing a
library's internal message [dongarra96] — recurs verbatim in agent harnesses: a sub-agent's tool result, or
a message on a shared bus, gets consumed by the wrong consumer. The design rule to copy is *the system, not
the programmer, assigns the discriminator* [cornell-comm], and there must be **no wildcard for the context**
[clustermonkey]. An `AgentComm` handle carrying (group, context) that a caller passes into a sub-agent
invocation, with an idempotent `dup` at entry, is directly implementable.

**T2. Attribute caching is the mechanism that makes T1 ergonomic, and it is usually omitted.** MPI's caching
lets a library create its private communicator once per (library, caller-communicator) pair and keep it
invisible to the application [dongarra96, mpi50 §7.7]. Without it, every agent library either leaks channel
management into its API or re-establishes a channel per call. Copy the pattern: attach opaque
per-(component, channel) state to the channel object.

**T3. Group scope matters more for agents than for MPI, because uninvolved participants cost money.** MPI
wanted group scope so libraries could "avoid unnecessarily synchronizing uninvolved MPI processes"
[mpi50 §7.1.1]. In an agent system, an over-broad collective doesn't just synchronize idle processes — it
issues LLM inference calls. Broadcasting to a group larger than necessary is a direct dollar cost, which
makes precise group scoping a first-order concern rather than a scalability nicety.

**T4. "Assume a reliable communication interface" must be inverted, not copied.** This is MPI's most
expensive mistake: the 1994 goal statement [mpi50 §1.1] produced a world where "the only thing that is
guaranteed after an MPI error is the ability to exit the program" [geist96], and thirty-one years later the
fault model is still not ratified [forum-2025-12]. LLM calls fail, time out, refuse, hallucinate schema, and
exceed budget as *normal* operation. An agent MPI must ship fault semantics in v1.0 — and it should copy
ULFM's *shape*, not its timing: a low-level, composable API (detect / revoke / agree / shrink) rather than a
monolithic global recovery policy, precisely because "independent components in an application [must be
able to] recover without interfering with each other" [bouteiller22].

**T5. The buffering refusal transfers, with the resource renamed.** MPI's position — "correct (and therefore,
portable) programs do not rely on system buffering" [mpi11 §3.4] — maps onto: *correct agent programs do not
rely on the harness retaining conversation history, queue depth, or context window*. Adopt the safety test
verbatim in spirit: a program is safe if replacing every send with a synchronous (rendezvous) send leaves it
correct [mpi11 §3.5]. That gives a mechanically checkable conformance mode — run the whole system with
zero buffering and no retained history — which is far more useful than prose warnings.

**T6. The throttle effect is a feature worth designing in.** MPI makes a standard send *block* rather than
fail when buffers are exhausted, which "results in a useful throttle effect" against a fast producer
[snir96-buffering]. For agents, backpressure is the natural mechanism for cost and rate-limit control: a
`send` that blocks when the consumer's budget or queue is saturated is strictly better than dropping
messages or unbounded queueing. Also copy the debugging trick: a buffered mode whose overflow raises a
diagnosable error, because "buffer overflow conditions are easier to diagnose than deadlock conditions"
[mpi11 §3.5].

**T7. Local vs. non-local should be *syntactically* visible, and it matters more here than in MPI.** MPI's
definition — non-local means returning "may require... some specific semantically-related MPI procedure to
be called on another MPI process" [mpi50 §2.4.2] — becomes, for agents, "may require another agent to
perform an inference." That is a cost and latency boundary, not just a deadlock boundary. Copy MPI's
`I`-prefix convention or a stronger equivalent (types, effects) so that non-local operations are impossible
to invoke by accident.

**T8. Keep completeness and locality orthogonal; do not collapse them into "async".** MPI-4's four-stage
decomposition (initialize / start / complete / free) crossed with locality produces cells a naive design
misses: incomplete-but-nonlocal (`MPI_Mprobe`, `MPI_Bcast_init`) and completing-but-local (`MPI_Bsend`)
[mpi50 §2.4.2]. Agent operations have exactly the same structure — an operation can be *initialized*
(prompt bound, tools bound) long before it is *started* (tokens committed) and *completed* (result
readable). A protocol with only `await`/`no-await` cannot express "reserve the budget now, fire later,
collect much later."

**T9. Blocking ≠ synchronous is a distinction agent harnesses routinely get wrong.** MPI's synchronous send
guarantees only that the matching receive has *started*, not finished [traff09]. Agent handoffs need the
same three-way distinction: (a) message accepted for delivery, (b) recipient agent has begun processing,
(c) recipient has produced a result. Conflating these is the source of most "the agent said it delegated but
nothing happened" bugs.

**T10. Nonblocking twins for *everything*, from v1.0 — this is MPI's clearest process lesson.** MPI shipped
nonblocking point-to-point in 1994 but nonblocking collectives only in MPI-3.0 (2012), an 18-year gap,
despite the nonblocking barrier being a running joke at the original Forum meetings [dongarra96]. Agent
workloads are latency-dominated far more than MPI workloads ever were, so overlap is the entire performance
story. Apply Gropp's symmetry principle [gropp01]: add the rarely used nonblocking variant rather than
create an exception.

**T11. Collectives transfer directly, and standardizing them is high-value.** Broadcast, scatter, gather,
allgather, alltoall, reduce-with-user-defined-op, and scan [dongarra96] are precisely the fan-out/fan-in/
aggregate patterns every agent framework reimplements. `MPI_Reduce` with a user-supplied operator is exactly
"aggregate N sub-agent outputs with a reducer" — and a reducer can itself be an LLM call. Neighborhood
collectives on a declared topology [hoefler-traff09] map onto agent graphs where each agent talks only to a
fixed set of peers, and the same argument applies: passing the *whole* topology to the runtime as a handle
lets it schedule globally rather than locally [hoefler-traff09].

**T12. But MPI's strict collective contract ("all processes in the group must invoke") does not transfer.**
[mpi50 §2.4.2]. An agent can stall, refuse, exceed budget, or die mid-collective; a hard all-must-call
contract turns any single failure into a system hang. Nonblocking, revocable, and shrinkable collectives
(`agree`, `shrink` in ULFM terms [ulfm-issue582]) must be the *default* form, with the strict form as the
opt-in special case — the inverse of MPI's ordering.

**T13. Ranks are the right idea but the wrong type; use MPI-4 process sets instead.** MPI's abstract integer
rank within a group [mpi50 §7.1.1] correctly stops components from hardcoding peer identity. But agent
identity carries role and capability semantics that an integer cannot. MPI-4's Sessions model already
provides the better primitive: *named* process sets queried from the runtime, from which groups and then
communicators are derived [mpi41 §11.3, holmes16]. Start there.

**T14. Start from Sessions, never from `MPI_COMM_WORLD`.** The standard's own indictment of the World Model —
"MPI cannot be initialized from different application components without a priori knowledge or coordination;
MPI cannot be initialized more than once; and MPI cannot be reinitialized after `MPI_FINALIZE`"
[mpi41 §11.3] — describes every failure mode of a globally initialized agent runtime. Sessions additionally
give per-session thread-support levels and per-process-set error scoping [holmes16], both of which map onto
per-component concurrency and error policies. An agent protocol whose entry point is a global registry will
repeat MPI's 27-year detour.

**T15. Opaque objects + handles transfer as a discipline, but the no-transfer restriction must be dropped.**
Keeping harness state (conversation buffers, KV caches, tool registries, budgets) in system space behind
handles is exactly right, and MPI's deferred-destruction semantics — mark for deallocation, let pending
operations finish, then reclaim [mpi50 §2.5.1] — is precisely what agent state needs. But MPI's rule that "an
opaque object and its handle... cannot be transferred to another process" [mpi50 §2.5.1] is fatal for agents,
where handing a task plus its context to a different process or host is routine. Handles must be
serializable and re-bindable; expect to pay for this with an explicit migration/attach operation.

**T16. "Library, not language" transfers, but a strictly harder version of the problem.** Gropp's
composability argument — a library "can exploit the newest and best compilers," any debugger works, no
compiler support is needed [gropp01, hoefler-xrds] — applies with full force: an agent protocol implemented
as a library rides every model, runtime, and tracing improvement for free. The disanalogy is severe, though:
MPI never had to standardize *message content*, only envelopes and layouts. An agent protocol's payloads are
natural language interpreted by a nondeterministic peer, so the protocol must specify a *dual* contract —
host-language API and prompt-level convention. There is no MPI precedent for this half.

**T17. Datatypes transfer as schemas, but the zero-copy justification does not.** MPI datatypes solved two
problems with one concept: heterogeneous representation conversion and noncontiguous layout description
enabling zero-copy [gropp01]. For agents, the first half survives (typed, validated, cross-model message
contracts) and the second half largely evaporates — there is no memory-bandwidth win to chase. Do not build
a `MPI_Type_create_struct`-scale layout algebra; the implementation burden is real (Träff had to invent
"flattening on the fly" because naive traversal was a "major problem" [traff09]) and the payoff is absent.
The genuine agent analogue of "avoid memory-to-memory copying" is **avoid re-serializing shared context into
every prompt**, which argues for a shared-context primitive (see T18), not for derived datatypes.

**T18. A shared-context primitive is the true RMA analogue, and MPI's history says design it late and
carefully — or design it once, properly.** MPI-2 RMA was shaped to 1997 hardware constraints and "MPI-2 RMA
had limited adoption: complex memory model hard to explain" [gropp22]; MPI-3 fixed it only by addition,
keeping every 15-year-old feature [gropp22]; Gropp now says it should be "rethought from the ground up"
[gropp22]. The transplantable warning is specific: **do not encode today's LLM-serving constraints (context
window sizes, stateless endpoints, current KV-cache mechanics) into normative semantics.** MPI's separate
vs. unified memory models [archer-mpi-evol] are the exact scar tissue of doing that. If a shared-context
window is offered, specify it against a *model* of consistency, not against current serving hardware.

**T19. Standardize interposition on day one — it is the cheapest high-leverage decision available.** MPI-1
included a profiling interface (name-shifted `PMPI_` entry points) in its very first version [mpi11 §1.4],
and that one decision created an entire tools ecosystem — TotalView, Vampir, Jumpshot, the MPIR and message-
queue side-documents [gropp01, mpi-forum-docs]. MPI_T later added query-based introspection whose design
virtue is that it "does not impose any specific structure or implementation choice... on the MPI
implementations" [hoefler-mpi30-blog]. For agents this is even more urgent, because of an *inversion*: Gropp
counted it a strength that "incorrect [MPI] programs are usually deterministic, simplifying the debugging
process" [gropp01], whereas LLM agents are nondeterministic by construction. Record/replay, message-order
capture, and cost/token accounting must therefore be *normative core*, not optional tooling.

**T20. Copy the governance machinery, not just the API design.** Four specific, cheap, transplantable rules.
(a) **Presence buys standing**: voting requires attendance at two of the last three meetings, one vote per
organization [dongarra96, procedures-current] — this simultaneously blocks drive-by vetoes and room-packing.
(b) **Two ballots at separate meetings after a frozen public reading** [procedures-current] — a structural
minimum reflection period that no amount of enthusiasm can compress. (c) **The MPI-2.2 admission bar**: an
extension "must have significant benefit for users," "must not require significant implementation effort,"
and changes are "accompanied by an open source implementation" [mpi50 §1.5] — a hard anti-speculation
device. (d) **A Journal of Development** for promising-but-unready ideas [mpi50 §1.3], plus MPI-5.0's
versioned **side-documents** that "shall not modify any aspects defined in the MPI Standard without
providing a mechanism that explicitly enables these deviations" [mpi50 §1.14] — an escape valve that keeps
the core small. And copy the *implementation* strategy too: a reference implementation developed *during*
standardization "to provide feedback to the Forum on implementation and usability issues" [mpich-overview],
structured as a portable core over a minimal swappable device layer of "five functions at minimum"
[gropp96-mpich]. Finally, get the wire format right in v1.0: MPI needed 31 years to standardize its ABI
[mpi50 §21.1], and cross-vendor interoperability is far closer to the *point* of an agent protocol than it
ever was for MPI.

**A note on the framing risk.** MPI's designers did not intend end users to write MPI: "when we developed
MPI, the idea was that it would be used to write portable libraries; actual users should never have to
confront it. But a user language has never really evolved" [hpcwire06]. A protocol positioned as
"MPI for agents" inherits this hazard. Decide explicitly whether the target audience is harness *authors*
(the MPI-analogous choice, and the one its design principles actually optimize for) or agent *application*
developers — because MPI's own history shows that a substrate designed for the former, used directly by the
latter, produces three decades of "nobody loves it" while remaining indispensable.

---

## References (BibTeX-ready)

```bibtex
@techreport{walker92,
  author      = {David W. Walker},
  title       = {Standards for Message-Passing in a Distributed Memory Environment},
  institution = {Oak Ridge National Laboratory},
  number      = {ORNL/TM-12147},
  year        = {1992},
  month       = aug,
  note        = {Report of the First CRPC Workshop on Standards for Message Passing in a
                 Distributed Memory Environment, Williamsburg, Virginia, April 29--30, 1992;
                 68 attendees},
  url         = {https://www.osti.gov/servlets/purl/10170156}
}

@inproceedings{mpiforum93,
  author    = {{Message Passing Interface Forum}},
  title     = {{MPI}: A Message Passing Interface},
  booktitle = {Proceedings of the 1993 ACM/IEEE Conference on Supercomputing (SC '93)},
  address   = {Portland, Oregon, USA},
  pages     = {878--883},
  year      = {1993},
  publisher = {ACM},
  isbn      = {0-8186-4340-4},
  doi       = {10.1145/169627.169855}
}

@article{mpiforum94,
  author  = {{Message Passing Interface Forum}},
  title   = {{MPI}: A Message-Passing Interface Standard},
  journal = {International Journal of Supercomputer Applications and High Performance Computing},
  volume  = {8},
  number  = {3/4},
  year    = {1994},
  note    = {MPI-1.0, released 5 May 1994}
}

@techreport{mpi11,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 1.1},
  institution = {University of Tennessee, Knoxville},
  year        = {1995},
  month       = jun,
  note        = {12 June 1995. HTML edition cited for \S1.1 Overview and Goals, \S1.4--1.5,
                 \S3.4 Rationale on buffering, \S3.5 safe programs},
  url         = {https://www.mpi-forum.org/docs/mpi-1.1/mpi-11-html/node2.html}
}

@techreport{mpi20,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI-2}: Extensions to the Message-Passing Interface},
  institution = {University of Tennessee, Knoxville},
  year        = {1997},
  month       = jul,
  note        = {18 July 1997. Cited for \S11.7 Semantics and Correctness (RMA rationale)},
  url         = {https://www.mpi-forum.org/docs/mpi-2.0/mpi-20-html/node136.htm}
}

@techreport{mpi31,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 3.1},
  institution = {MPI Forum},
  year        = {2015},
  month       = jun,
  note        = {4 June 2015. Cited for \S12.4 MPI and Threads},
  url         = {https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report/node301.htm}
}

@techreport{mpi41,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 4.1},
  institution = {MPI Forum},
  year        = {2023},
  month       = nov,
  note        = {2 November 2023. Cited for \S11.3 The Sessions Model and the MPI-3.0/4.0 change lists},
  url         = {https://www.mpi-forum.org/docs/mpi-4.1/mpi41-report/mpi41-report.htm}
}

@techreport{mpi40,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 4.0},
  institution = {MPI Forum},
  year        = {2021},
  month       = jun,
  note        = {9 June 2021. Cited for the authoritative version/date table in \S1},
  url         = {https://www.mpi-forum.org/docs/mpi-4.0/mpi40-report.pdf}
}

@techreport{mpi50,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 5.0},
  institution = {MPI Forum},
  year        = {2025},
  month       = jun,
  note        = {5 June 2025. Principal primary source. Cited for \S1.1--1.15 (goals, version
                 backgrounds), \S2.4.2 (local/nonlocal, blocking/nonblocking),
                 \S2.5.1 (opaque objects), \S7.1--7.2 (communicators/contexts),
                 \S7.4.2 (MPI\_COMM\_IDUP), \S11.6 (threads), \S21 (ABI)},
  url         = {https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report.pdf}
}

@misc{mpi-forum-docs,
  author       = {{MPI Forum}},
  title        = {{MPI} Documents},
  howpublished = {\url{https://www.mpi-forum.org/docs/}},
  year         = {2025},
  note         = {Authoritative approval dates for all MPI versions; also lists active
                  side-documents (MPIR, Message Queue Interface, Memory Allocation Kinds,
                  Forum Procedures) and the MPI-2.0 Journal of Development}
}

@misc{procedures-current,
  author       = {{MPI Forum}},
  title        = {{MPI} Forum Procedures (current version)},
  howpublished = {\url{https://www.mpi-forum.org/docs/other/procedures-current.pdf}},
  note         = {Overall Organization Eligibility; two-ballot rule; NO-NO-VOTE; no proxies}
}

@misc{procedures-22,
  author       = {{MPI Forum}},
  title        = {{MPI} Forum Procedures, Version 2.2},
  howpublished = {\url{https://www.mpi-forum.org/docs/other/procedures-22.pdf}},
  note         = {Ballot passes if quorum met and yes votes exceed 3/4 of yes+no}
}

@misc{procedures-30,
  author       = {{MPI Forum}},
  title        = {{MPI} Forum Procedures, Version 3.0},
  howpublished = {\url{https://www.mpi-forum.org/docs/other/procedures-30.pdf}},
  note         = {NO-NO-VOTE requires zero ``no'' votes}
}

@misc{forum-2025-12,
  author       = {{MPI Forum}},
  title        = {December 2025 Meeting Notes},
  howpublished = {\url{https://www.mpi-forum.org/meetings/2025/12/notes}},
  year         = {2025},
  note         = {ULFM fault-model reading; ``fail-stop'' terminology; no-no vote scheduled}
}

@misc{mpi-bof-isc25,
  author       = {{MPI Forum}},
  title        = {The Message Passing Interface ({MPI}): The New {MPI} 5.0 --- Now with ABI Included!},
  howpublished = {MPI Forum Birds-of-a-Feather session, ISC 2025,
                  \url{https://www.mpi-forum.org/bofs/2025-06-MPI-BOF-ISC25.pdf}},
  year         = {2025},
  note         = {ABI versioning; mpi-abi-stubs; MPI 6.0 directions incl. fault tolerance,
                  GPU bindings, Sessions-based dynamic resource management}
}

@article{dongarra96,
  author  = {Jack J. Dongarra and Steve W. Otto and Marc Snir and David Walker},
  title   = {A Message Passing Standard for {MPP} and Workstations},
  journal = {Communications of the ACM},
  volume  = {39},
  number  = {7},
  pages   = {84--90},
  year    = {1996},
  month   = jul,
  doi     = {10.1145/233977.234000},
  note    = {Authoritative on the 1993--94 voting rules, participant counts, funding,
             and the library/communicator rationale},
  url     = {http://snir.cs.illinois.edu/listed/J40.pdf}
}

@article{hempel99,
  author  = {Rolf Hempel and David W. Walker},
  title   = {The emergence of the {MPI} message passing standard for parallel computing},
  journal = {Computer Standards \& Interfaces},
  volume  = {21},
  number  = {1},
  pages   = {51--62},
  year    = {1999},
  doi     = {10.1016/S0920-5489(99)00004-5}
}

@misc{walker17,
  author       = {David W. Walker},
  title        = {Some Reflections on the {MPI} Forum 1992--95},
  howpublished = {Invited talk, 25th anniversary of MPI},
  year         = {2017},
  note         = {Event timeline, chapter-owner assignments, funding quotation, and the
                  ``would it succeed today?'' assessment. Slide deck; the ``12 June 1994''
                  MPI-1.1 date on slide 9 conflicts with the Forum's own table in mpi40}
}

@book{snir96,
  author    = {Marc Snir and Steve Otto and Steven Huss-Lederman and David Walker and Jack Dongarra},
  title     = {{MPI}: The Complete Reference},
  publisher = {MIT Press},
  address   = {Cambridge, MA},
  year      = {1996},
  series    = {Scientific and Engineering Computation}
}

@incollection{snir96-whybig,
  author    = {Marc Snir and Steve Otto and Steven Huss-Lederman and David Walker and Jack Dongarra},
  title     = {Why is {MPI} so big?},
  booktitle = {{MPI}: The Complete Reference},
  publisher = {MIT Press},
  year      = {1996},
  note      = {``In all there are 128 MPI routines''},
  url       = {https://www.netlib.org/utk/papers/mpi-book/node198.html}
}

@incollection{snir96-portable,
  author    = {Marc Snir and Steve Otto and Steven Huss-Lederman and David Walker and Jack Dongarra},
  title     = {Portable Programming with {MPI}},
  booktitle = {{MPI}: The Complete Reference},
  publisher = {MIT Press},
  year      = {1996},
  note      = {``portable'' is synonymous with ``safe''},
  url       = {https://netlib.org/utk/papers/mpi-book/node201.html}
}

@incollection{snir96-buffering,
  author    = {Marc Snir and Steve Otto and Steven Huss-Lederman and David Walker and Jack Dongarra},
  title     = {Buffering and Safety},
  booktitle = {{MPI}: The Complete Reference},
  publisher = {MIT Press},
  year      = {1996},
  note      = {The ``throttle effect''},
  url       = {https://www.netlib.org/utk/papers/mpi-book/node39.html}
}

@article{snir-review,
  title   = {Review: {MPI} --- The Complete Reference, Vols. 1 and 2, 2nd ed.},
  journal = {Scientific Programming},
  year    = {2005},
  doi     = {10.1155/2005/653765},
  note    = {Summarizes MPI-2's competitive motivations: dynamic process management from PVM,
             one-sided transfers from Cray SHMEM, parallel file systems from IBM/Intel}
}

@inproceedings{gropp01,
  author    = {William D. Gropp},
  title     = {Learning from the Success of {MPI}},
  booktitle = {High Performance Computing --- HiPC 2001},
  series    = {Lecture Notes in Computer Science},
  volume    = {2228},
  pages     = {81--92},
  publisher = {Springer},
  year      = {2001},
  doi       = {10.1007/3-540-45307-5_8},
  note      = {arXiv preprint cs/0109017, \url{https://doi.org/10.48550/arXiv.cs/0109017}.
               The six requirements: portability, performance, simplicity and symmetry,
               modularity, composability, completeness. Source of ``It is more important to
               make the hard things possible than it is to make the easy things easy''
               and ``The context part of the communicator was inspired by Zipcode''}
}

@misc{gropp16,
  author       = {William D. Gropp},
  title        = {{MPI}: The Once and Future King},
  howpublished = {Keynote, EuroMPI 2016, Edinburgh,
                  \url{http://www.eurompi2016.ed.ac.uk/sites/default/files/attachments/Gropp-mpi-once-and-future-king.pdf}},
  year         = {2016},
  note         = {``Greatest Common Denominator'' framing; explicit list of MPI weaknesses}
}

@misc{gropp17,
  author       = {William D. Gropp},
  title        = {{MPI+X} for Extreme Scale Computing},
  howpublished = {Talk, PPAM,
                  \url{https://wgropp.cs.illinois.edu/bib/talks/tdata/2017/mpix-exascale-ppam.pdf}},
  year         = {2017},
  note         = {``MPI+X won't be enough for Exascale if the work for the `+' is not done very
                  well''; ABI as developer-level standardization}
}

@misc{gropp22,
  author       = {William D. Gropp},
  title        = {{MPI} {RMA}: Is It Time To Start Over?},
  howpublished = {Talk, \url{http://wgropp.cs.illinois.edu/bib/talks/tdata/2022/mpi-rma-vision.pdf}},
  year         = {2022},
  note         = {Retrospective on MPI-2 RMA's limited adoption; ``25+ years is too long to
                  simply tweak the programming model''}
}

@article{gropp96-mpich,
  author  = {William Gropp and Ewing Lusk and Nathan Doss and Anthony Skjellum},
  title   = {A high-performance, portable implementation of the {MPI} message passing
             interface standard},
  journal = {Parallel Computing},
  volume  = {22},
  number  = {6},
  pages   = {789--828},
  year    = {1996},
  doi     = {10.1016/0167-8191(96)00024-5},
  note    = {The Abstract Device Interface and the five-function channel interface}
}

@inproceedings{gropp02-pvm,
  author    = {William Gropp and Ewing Lusk},
  title     = {Goals Guiding Design: {PVM} and {MPI}},
  booktitle = {Proceedings of the IEEE International Conference on Cluster Computing},
  year      = {2002},
  doi       = {10.1109/CLUSTR.2002.1137753},
  note      = {Portability vs. heterogeneity; the IMPI interoperability standard},
  url       = {https://wgropp.cs.illinois.edu/bib/papers/pdata/2002/mpiandpvm.pdf}
}

@inproceedings{gropp-thakur07,
  author    = {William Gropp and Rajeev Thakur},
  title     = {Test Suite for Evaluating Performance of {MPI} Implementations That Support
               {MPI\_THREAD\_MULTIPLE}},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing Interface
               (EuroPVM/MPI)},
  series    = {Lecture Notes in Computer Science},
  volume    = {4757},
  publisher = {Springer},
  year      = {2007},
  doi       = {10.1007/978-3-540-75416-9_11},
  note      = {Why a global lock held across blocking calls violates MPI's progress requirement},
  url       = {https://wgropp.cs.illinois.edu/bib/papers/pdata/2007/mpi-thread-test.pdf}
}

@book{gropp-usingmpi,
  author    = {William Gropp and Ewing Lusk and Anthony Skjellum},
  title     = {Using {MPI}: Portable Parallel Programming with the Message-Passing Interface},
  publisher = {MIT Press},
  address   = {Cambridge, MA},
  edition   = {2nd},
  year      = {1999}
}

@misc{gropp-slides,
  author       = {William Gropp and Ewing Lusk and Rajeev Thakur},
  title        = {Portable {MPI} and Related Parallel Development Tools: Tutorial slides},
  howpublished = {Tutorial slide material},
  note         = {``MPI-1 has about 125 functions''; ``MPI-2 has about 150 more''; the two
                  alternative six-function subsets; pre-MPI systems ``did not address the full
                  spectrum of message-passing issues, lacked vendor support, were not implemented
                  at the most efficient level''. Widely mirrored; a canonical primary citation
                  could not be located [UNVERIFIED provenance]}
}

@phdthesis{traff09,
  author = {Jesper Larsson Tr\"aff},
  title  = {Aspects of the Efficient Implementation of the Message Passing Interface ({MPI})},
  school = {University of Copenhagen},
  type   = {Doctor Scientiarum (disputats) thesis},
  year   = {2009},
  note   = {The most precise per-system attribution of MPI's precursors (PVM, P4, PARMACS,
            Zipcode, Express, Linda, XDR, IBM EUI/CCL, Intel NX, occam); ``economy of concepts'';
            MPI-2 required sixteen meetings vs. seven for MPI-1; the Gropp ``difficult things
            possible'' quotation attributed to EuroPVM/MPI 2004},
  url    = {http://www.traff-industries.de/docs/disputats.pdf}
}

@article{skjellum94,
  author  = {Anthony Skjellum and Steven G. Smith and Nathan E. Doss and Alvin P. Leung
             and Manfred Morari},
  title   = {The design and evolution of {Zipcode}},
  journal = {Parallel Computing},
  volume  = {20},
  number  = {4},
  pages   = {565--596},
  year    = {1994},
  doi     = {10.1016/0167-8191(94)90029-9}
}

@inproceedings{skjellum90,
  author    = {Anthony Skjellum and Alvin P. Leung},
  title     = {Zipcode: A Portable Multicomputer Communication Library atop the Reactive Kernel},
  booktitle = {Proceedings of the 5th Distributed Memory Concurrent Computing Conference},
  editor    = {David W. Walker and Quentin F. Stout},
  address   = {Charleston, SC},
  pages     = {767--776},
  publisher = {IEEE Press},
  year      = {1990}
}

@article{butler94-p4,
  author  = {Ralph Butler and Ewing Lusk},
  title   = {Monitors, messages, and clusters: The {p4} parallel programming system},
  journal = {Parallel Computing},
  volume  = {20},
  number  = {4},
  pages   = {547--564},
  year    = {1994},
  doi     = {10.1016/0167-8191(94)90028-0}
}

@article{calkin94-parmacs,
  author  = {R. Calkin and Rolf Hempel and Hans-Christian Hoppe and P. Wypior},
  title   = {Portable programming with the {PARMACS} message-passing library},
  journal = {Parallel Computing},
  volume  = {20},
  number  = {4},
  pages   = {615--632},
  year    = {1994},
  doi     = {10.1016/0167-8191(94)90031-0}
}

@book{geist94-pvm,
  author    = {Al Geist and Adam Beguelin and Jack Dongarra and Weicheng Jiang
               and Robert Manchek and Vaidy Sunderam},
  title     = {{PVM}: Parallel Virtual Machine --- A Users' Guide and Tutorial for
               Networked Parallel Computing},
  publisher = {MIT Press},
  address   = {Cambridge, MA},
  year      = {1994}
}

@article{geist96,
  author  = {G. A. Geist and J. A. Kohl and P. M. Papadopoulos},
  title   = {{PVM} and {MPI}: A comparison of features},
  journal = {Calculateurs Parall\`eles},
  volume  = {8},
  number  = {2},
  year    = {1996},
  note    = {Source of ``the only thing that is guaranteed after an MPI error is the ability to
             exit the program''; names lack of interoperability and lack of fault tolerance as
             MPI's two central weaknesses},
  url     = {http://www.rrsg.uct.ac.za/projects/mti/pvmvsmpi.pdf}
}

@misc{kluge04,
  author       = {Michael Kluge},
  title        = {Comparative analysis of {PVM} and {MPI} for the development of physical
                  applications on parallel clusters},
  howpublished = {JASS 2004 course paper, TU M\"unchen},
  year         = {2004},
  note         = {PVM's notify mechanism vs. MPI-1's static task/host model},
  url          = {http://wwwmayr.informatik.tu-muenchen.de/konferenzen/Jass04/courses/2/Papers/Comparison.pdf}
}

@manual{express92,
  title        = {Express User's Guide, Version 3.2.5},
  organization = {ParaSoft Corporation},
  address      = {Monrovia, CA},
  year         = {1992}
}

@article{carriero89-linda,
  author  = {Nicholas Carriero and David Gelernter},
  title   = {Linda in Context},
  journal = {Communications of the ACM},
  volume  = {32},
  number  = {4},
  pages   = {444--458},
  year    = {1989},
  doi     = {10.1145/63334.63337}
}

@manual{inmos88-occam,
  title        = {occam 2 Reference Manual},
  organization = {INMOS Limited},
  publisher    = {Prentice Hall},
  year         = {1988}
}

@article{hhmw94,
  title   = {Special Issue on Message-Passing Interfaces},
  journal = {Parallel Computing},
  volume  = {20},
  number  = {4},
  year    = {1994},
  note    = {Editors Hempel, Hey, McBryan, Walker. The contemporaneous survey of the
             pre-MPI message-passing landscape, cited as [HHMW94] in traff09}
}

@techreport{corbett94,
  author      = {Peter Corbett and Dror Feitelson and Yarsun Hsu and Jean-Pierre Prost
                 and Marc Snir and Sam Fineberg and Bill Nitzberg and Bernard Traversat
                 and Parkson Wong},
  title       = {{MPI-IO}: A Parallel File I/O Interface for {MPI}},
  institution = {IBM T. J. Watson Research Center},
  number      = {RC 19841 (87784)},
  year        = {1994},
  month       = nov,
  note        = {``I/O can be modeled as message passing: writing to a file is like sending a
                 message, and reading from a file is like receiving a message.'' Version 0.3
                 archived at NASA NTRS},
  url         = {https://ntrs.nasa.gov/api/citations/19970026966/downloads/19970026966.pdf}
}

@misc{thakur-retro,
  author       = {Rajeev Thakur},
  title        = {{MPI-IO}: A Retrospective},
  howpublished = {Talk, 25th anniversary of MPI},
  year         = {2019},
  note         = {MPI-IO's origin at IBM Research in 1994; the independent ``MPI-IO Committee'';
                  merger into the MPI Forum in summer 1996; Bill Nitzberg as I/O chapter chair;
                  ROMIO tracking the spec ``similar to the way MPICH tracked the MPI spec''
                  [UNVERIFIED exact venue/date of the talk]}
}

@inproceedings{thakur99-io,
  author    = {Rajeev Thakur and William Gropp and Ewing Lusk},
  title     = {On Implementing {MPI-IO} Portably and with High Performance},
  booktitle = {Proceedings of the Sixth Workshop on I/O in Parallel and Distributed Systems},
  pages     = {23--32},
  year      = {1999},
  doi       = {10.1145/301816.301826}
}

@misc{romio,
  author       = {Rajeev Thakur and Robert Ross and Robert Latham and Ewing Lusk and William Gropp},
  title        = {{ROMIO}: A High-Performance, Portable {MPI-IO} Implementation},
  howpublished = {\url{https://wordpress.cels.anl.gov/romio/}},
  note         = {The ADIO abstract I/O device layer}
}

@inproceedings{hoefler07-sc,
  author    = {Torsten Hoefler and Andrew Lumsdaine and Wolfgang Rehm},
  title     = {Implementation and performance analysis of non-blocking collective
               operations for {MPI}},
  booktitle = {Proceedings of the 2007 ACM/IEEE Conference on Supercomputing (SC '07)},
  year      = {2007},
  doi       = {10.1145/1362622.1362692},
  note      = {LibNBC}
}

@inproceedings{hoefler07-case,
  author    = {Torsten Hoefler and Prabhanjan Kambadur and Richard L. Graham
               and Galen Shipman and Andrew Lumsdaine},
  title     = {A Case for Standard Non-Blocking Collective Operations},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing Interface
               (EuroPVM/MPI)},
  series    = {Lecture Notes in Computer Science},
  volume    = {4757},
  pages     = {125--134},
  publisher = {Springer},
  year      = {2007},
  doi       = {10.1007/978-3-540-75416-9_22},
  url       = {https://www.open-mpi.org/papers/euro-pvmmpi-2007-nb-coll/mpi-vs-nbc.pdf}
}

@article{hoefler06-cg,
  author  = {Torsten Hoefler and Peter Gottschling and Andrew Lumsdaine and Wolfgang Rehm},
  title   = {Optimizing a conjugate gradient solver with non-blocking collective operations},
  journal = {Parallel Computing},
  volume  = {33},
  number  = {9},
  pages   = {624--633},
  year    = {2007},
  doi     = {10.1016/j.parco.2007.06.006}
}

@article{widener-noise,
  author  = {Patrick Widener and Kurt Ferreira and Scott Levy and Torsten Hoefler},
  title   = {On noise and the performance benefit of nonblocking collectives},
  journal = {International Journal of High Performance Computing Applications},
  year    = {2016},
  doi     = {10.1177/1094342015611952},
  url     = {https://htor.inf.ethz.ch/publications/img/widerner-noise-ijhpca.pdf}
}

@inproceedings{hoefler-traff09,
  author    = {Torsten Hoefler and Jesper Larsson Tr\"aff},
  title     = {Sparse collective operations for {MPI}},
  booktitle = {IEEE International Symposium on Parallel and Distributed Processing (IPDPS)},
  year      = {2009},
  doi       = {10.1109/IPDPS.2009.5160935},
  note      = {Origin of MPI-3.0 neighborhood collectives; argues a collective handle is
               necessary so the implementation can optimize with global information}
}

@article{hoefler15-rma,
  author  = {Torsten Hoefler and James Dinan and Rajeev Thakur and Brian Barrett
             and Pavan Balaji and William Gropp and Keith Underwood},
  title   = {Remote Memory Access Programming in {MPI-3}},
  journal = {ACM Transactions on Parallel Computing},
  volume  = {2},
  number  = {2},
  pages   = {9:1--9:26},
  year    = {2015},
  doi     = {10.1145/2780584},
  note    = {Formal axiomatic models because the semantics are ``hard to extract from the
             English prose in the standard''}
}

@inproceedings{icpp09-rma,
  author    = {Vinod Tipparaju and William Gropp and Hubert Ritzdorf and Rajeev Thakur
               and Jesper Larsson Tr\"aff},
  title     = {Investigating High Performance {RMA} Interfaces for the {MPI-3} Standard},
  booktitle = {International Conference on Parallel Processing (ICPP)},
  year      = {2009},
  doi       = {10.1109/ICPP.2009.54},
  note      = {``even 12 years after its existence, the MPI-2 RMA interface remains scarcely used''}
}

@article{dinan16-rma,
  author  = {James Dinan and Pavan Balaji and Darius Buntinas and David Goodell
             and William Gropp and Rajeev Thakur},
  title   = {An implementation and evaluation of the {MPI} 3.0 one-sided communication interface},
  journal = {Concurrency and Computation: Practice and Experience},
  volume  = {28},
  number  = {17},
  pages   = {4385--4404},
  year    = {2016},
  doi     = {10.1002/cpe.3758}
}

@article{gerstenberger14,
  author  = {Robert Gerstenberger and Maciej Besta and Torsten Hoefler},
  title   = {Enabling Highly-Scalable Remote Memory Access Programming with {MPI-3} One Sided},
  journal = {Scientific Programming},
  volume  = {22},
  number  = {2},
  pages   = {75--91},
  year    = {2014},
  doi     = {10.1155/2014/571902}
}

@article{bonachea04,
  author  = {Dan Bonachea and Jason Duell},
  title   = {Problems with using {MPI} 1.1 and 2.0 as compilation targets for parallel
             language implementations},
  journal = {International Journal of High Performance Computing and Networking},
  volume  = {1},
  number  = {1--3},
  pages   = {91--99},
  year    = {2004},
  doi     = {10.1504/IJHPCN.2004.007569},
  note    = {``MPI-RMA imposes too many semantic restrictions to be a useful portable
             compilation target''}
}

@inproceedings{holmes16,
  author    = {Daniel Holmes and Kathryn Mohror and Ryan E. Grant and Anthony Skjellum
               and Martin Schulz and Wesley Bland and Jeffrey M. Squyres},
  title     = {{MPI} Sessions: Leveraging Runtime Infrastructure to Increase Scalability of
               Applications at Exascale},
  booktitle = {Proceedings of the 23rd European MPI Users' Group Meeting (EuroMPI '16)},
  pages     = {121--129},
  year      = {2016},
  doi       = {10.1145/2966884.2966915},
  note      = {``remove the need for `heroic developer efforts' ''; per-session thread levels;
               per-process-set error scoping},
  url       = {https://www.osti.gov/servlets/purl/1373234}
}

@article{ghafoor21,
  author  = {Matthew G. F. Dosanjh and Andrew Worley and Derek Schafer and Prema Soundararajan
             and Sheikh Ghafoor and Anthony Skjellum and Purushotham V. Bangalore
             and Ryan E. Grant},
  title   = {Implementation and evaluation of {MPI} 4.0 partitioned communication libraries},
  journal = {Parallel Computing},
  volume  = {108},
  pages   = {102827},
  year    = {2021},
  doi     = {10.1016/j.parco.2021.102827},
  note    = {GPU-side MPI\_Pready motivation; the decision to make users define equal-size
             partitions},
  url     = {https://par.nsf.gov/servlets/purl/10296871}
}

@inproceedings{mpix-stream,
  author    = {Hui Zhou and Ken Raffenetti and Yanfei Guo and Rajeev Thakur},
  title     = {{MPIX} Stream: An Explicit Solution to Hybrid {MPI+X} Programming},
  booktitle = {Proceedings of the 29th European MPI Users' Group Meeting (EuroMPI/USA '22)},
  year      = {2022},
  doi       = {10.1145/3555819.3555820},
  note      = {``the performance side has been a multi-decade struggle''; the API-explosion
               hazard of per-operation stream arguments},
  url       = {https://export.arxiv.org/pdf/2208.13707v2.pdf}
}

@misc{lessons-threads,
  author       = {Rohit Zambre and Aparna Chandramowlishwaran},
  title        = {Lessons Learned on {MPI+Threads} Communication},
  howpublished = {arXiv:2206.14285},
  year         = {2022},
  doi          = {10.48550/arXiv.2206.14285},
  note         = {``MPI 4.0 does not meet the needs of MPI+threads applications, and it
                  introduces new problems''}
}

@article{amer18-locks,
  author  = {Abdelhalim Amer and Huiwei Lu and Pavan Balaji and Milind Chabbi and Yanjie Wei
             and Jeff Hammond and Satoshi Matsuoka},
  title   = {Lock Contention Management in Multithreaded {MPI}},
  journal = {ACM Transactions on Parallel Computing},
  volume  = {5},
  number  = {3},
  year    = {2018},
  doi     = {10.1145/3275443}
}

@inproceedings{thakur10-exascale,
  author    = {Pavan Balaji and Darius Buntinas and David Goodell and William Gropp
               and Torsten Hoefler and Sameer Kumar and Ewing Lusk and Rajeev Thakur
               and Jesper Larsson Tr\"aff},
  title     = {{MPI} on Millions of Cores},
  journal   = {Parallel Processing Letters},
  volume    = {21},
  number    = {1},
  pages     = {45--60},
  year      = {2011},
  doi       = {10.1142/S0129626411000060},
  note      = {Companion to the ``MPI at Exascale'' position paper (SciDAC 2010),
               \url{http://aegjcef.unixer.de/publications/img/mpi_exascale.pdf}}
}

@misc{bouteiller22,
  author       = {Aur\'elien Bouteiller and George Bosilca and Guillaume Mercier
                  and Thomas H\'erault},
  title        = {Implicit Actions and Non-blocking Failure Recovery with {MPI}},
  howpublished = {arXiv:2212.08755},
  year         = {2022},
  doi          = {10.48550/arXiv.2212.08755},
  note         = {ULFM design requirements; comparison against FT-MPI, MPI Reinit, FMI;
                  ``a flexible low-level API that supports a variety of fault tolerance models''}
}

@misc{ulfm-issue20,
  author       = {{MPI Forum Fault Tolerance Working Group}},
  title        = {User-Level Failure Mitigation (MPI Forum issue \#20)},
  howpublished = {\url{https://github.com/mpi-forum/mpi-issues/issues/20}},
  note         = {Open MPI prototype at \url{http://fault-tolerance.org/}; implementations
                  that never raise failure exceptions need not tolerate failures}
}

@misc{ulfm-issue582,
  author       = {{MPI Forum Fault Tolerance Working Group}},
  title        = {ULFM Fault Tolerance (slice 2: agree) (MPI Forum issue \#582)},
  howpublished = {\url{https://github.com/mpi-forum/mpi-issues/issues/582}},
  note         = {``The monolithic ULFM proposal has been split in morsels so that the MPI Forum
                  can focus on individual topics''}
}

@article{fagg00-ftmpi,
  author  = {Graham E. Fagg and Jack Dongarra},
  title   = {{FT-MPI}: Fault Tolerant {MPI}, Supporting Dynamic Applications in a Dynamic World},
  journal = {Lecture Notes in Computer Science},
  volume  = {1908},
  pages   = {346--353},
  year    = {2000},
  doi     = {10.1007/3-540-45255-9_47}
}

@inproceedings{gabriel04-ompi,
  author    = {Edgar Gabriel and Graham E. Fagg and George Bosilca and Thara Angskun
               and Jack J. Dongarra and Jeffrey M. Squyres and Vishal Sahay
               and Prabhanjan Kambadur and Brian Barrett and Andrew Lumsdaine
               and Ralph H. Castain and David J. Daniel and Richard L. Graham
               and Timothy S. Woodall},
  title     = {Open {MPI}: Goals, Concept, and Design of a Next Generation {MPI} Implementation},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing Interface
               (EuroPVM/MPI)},
  series    = {Lecture Notes in Computer Science},
  volume    = {3241},
  pages     = {97--104},
  publisher = {Springer},
  year      = {2004},
  doi       = {10.1007/978-3-540-30218-6_19}
}

@misc{ompi-history,
  author       = {{The Open MPI Project}},
  title        = {History of Open {MPI}},
  howpublished = {\url{https://docs.open-mpi.org/en/main/history.html}},
  note         = {LAM/MPI + LA-MPI + FT-MPI merger; ``Take the best, leave the rest'';
                  first commit 22 November 2003; development from 5 January 2004}
}

@misc{ompi-faq,
  author       = {{The Open MPI Project}},
  title        = {FAQ: General information about the Open {MPI} Project},
  howpublished = {\url{https://www.open-mpi.org/faq/?category=general}},
  note         = {PACX-MPI contribution from HLRS Stuttgart}
}

@incollection{aosa-ompi,
  author    = {Jeffrey M. Squyres},
  title     = {Open {MPI}},
  booktitle = {The Architecture of Open Source Applications, Volume 2},
  year      = {2012},
  note      = {``In 2003, the current version of the MPI standard, MPI-2.0, defined over 300 API
               functions''; LAM/MPI's 1,900 files / 300,000 lines; why a clean slate beat a merge},
  url       = {https://aosabook.org/en/v2/openmpi.html}
}

@misc{ompi-sc06,
  author       = {{The Open MPI Project}},
  title        = {Open {MPI}: What we've done and where we're going},
  howpublished = {SC 2006 booth talk,
                  \url{https://www-lb.open-mpi.org/papers/sc-2006/iu-booth-ompi-past-and-future.pdf}},
  year         = {2006},
  note         = {Four-parent lineage diagram; Modular Component Architecture; first release Q1 2005}
}

@misc{ompi-abi,
  author       = {{The Open MPI Project}},
  title        = {The {MPI} Forum {ABI}},
  howpublished = {\url{https://docs.open-mpi.org/en/main/building-apps/mpi-forum-abi.html}},
  note         = {libmpi\_abi from Open MPI v6.0.0; deliberate omission of a Fortran ABI}
}

@misc{mpich-overview,
  author       = {{The MPICH Project}},
  title        = {{MPICH} Overview},
  howpublished = {\url{https://www.mpich.org/about/overview/}},
  note         = {``MPICH was originally developed during the MPI standards process starting in
                  1992 to provide feedback to the MPI Forum''; MPI over CHameleon;
                  MPICH-1 / MPICH2 / MPICH 3.0 renaming history}
}

@misc{mpich-bof21,
  author       = {{The MPICH Project}},
  title        = {{MPICH}: Status and Upcoming Releases},
  howpublished = {SC21 Birds-of-a-Feather,
                  \url{https://www.mpich.org/static/docs/slides/2021-sc-bof/2021-11-17-MPICH-BoF.pdf}},
  year         = {2021},
  note         = {``MPICH is not just a software; it's an Ecosystem''; the MPICH ABI Compatibility
                  Initiative (2013) with adoption dates for Intel MPI, Cray MPT, MVAPICH2,
                  ParaStation MPI; Aurora / Frontier / El Capitan}
}

@misc{wikipedia-mpich,
  title        = {{MPICH}},
  howpublished = {Wikipedia, \url{https://en.wikipedia.org/wiki/MPICH}},
  note         = {List of MPICH derivatives: Cray, Microsoft MS-MPI, Intel MPI, MVAPICH,
                  ParaStation MPI, FG-MPI. Secondary source}
}

@misc{intel-mpi,
  author       = {{Intel Corporation}},
  title        = {Intel {MPI} Library},
  howpublished = {\url{https://www.intel.com/content/www/us/en/developer/tools/oneapi/mpi-library.html}},
  note         = {``implements the open source MPICH specification''}
}

@misc{so-mpich-ompi,
  author       = {Jeff Hammond},
  title        = {Answer to ``{MPICH} vs {OpenMPI}''},
  howpublished = {Stack Overflow, \url{https://stackoverflow.com/questions/2427399/mpich-vs-openmpi}},
  note         = {``MPICH is supposed to be high-quality reference implementation... Open-MPI
                  targets the common case''; ``MPICH and its derivatives''. Practitioner source,
                  not peer-reviewed}
}

@article{brinchhansen98,
  author  = {Per Brinch Hansen},
  title   = {An Evaluation of the Message-Passing Interface},
  journal = {ACM SIGPLAN Notices},
  volume  = {33},
  number  = {3},
  pages   = {65--72},
  year    = {1998},
  month   = mar,
  doi     = {10.1145/275168.275174},
  note    = {``a step backwards in programming technology''}
}

@article{hpf-response,
  title   = {Per Brinch Hansen's concerns about High Performance Fortran},
  journal = {ACM SIGPLAN Notices},
  volume  = {33},
  number  = {9},
  year    = {1998},
  doi     = {10.1145/286385.286389},
  note    = {Rejoinder noting the two pages of shadow-edge message-passing code in
             brinchhansen98 that an HPF compiler generates automatically}
}

@article{hoefler-xrds,
  author  = {Torsten Hoefler and {interviewed in} ACM XRDS},
  title   = {The reign and modern challenges of the Message Passing Interface ({MPI}):
             A discussion with Dr.\ Torsten Hoefler},
  journal = {ACM XRDS Blog},
  year    = {2017},
  month   = feb,
  note    = {``clear organization around a relatively small number of orthogonal concepts...
             like Lego blocks''; MPI+X, dCUDA, MPI+MPI},
  url     = {https://blog.xrds.acm.org/2017/02/message-passing-interface-mpi-reign-modern-challenges/}
}

@misc{hoefler-mpi30-blog,
  author       = {Torsten Hoefler},
  title        = {{MPI-3.0} is Coming --- an Overview of new (and old) Features},
  howpublished = {\url{https://htor.inf.ethz.ch/blog/index.php/2012/02/06/mpi-3-0-is-coming-an-overview-of-new-and-old-features/}},
  year         = {2012},
  note         = {MPI\_T's deliberate refusal to impose implementation structure;
                  MPI\_Win\_allocate\_shared and alloc\_shared\_noncontig}
}

@misc{hoefler-mpi30-slides,
  author       = {Torsten Hoefler},
  title        = {New and old Features in {MPI-3.0}: The Past, the Standard, and the Future},
  howpublished = {\url{https://spcl.inf.ethz.ch/Publications/.pdf/hoefler-mpi-3.0-overview.pdf}},
  year         = {2012},
  note         = {Window types; MPI\_T control/performance variables; ``Remove the deprecated
                  [C++] bindings (any users?)''}
}

@misc{archer-mpi-evol,
  author       = {{EPCC / ARCHER}},
  title        = {The Evolution of {MPI}},
  howpublished = {ARCHER Advanced MPI course material, Exeter, April 2018,
                  \url{http://www.archer.ac.uk/training/course-material/2018/04/adv-mpi-exeter/Slides/L02-MPI-Evolution.pdf}},
  year         = {2018},
  note         = {Feature-by-feature MPI-2 / MPI-3 change summary; unified vs. separate
                  memory model; neighborhood collectives as a scalable MPI\_Alltoallv replacement}
}

@misc{mpi-3.0-changes,
  author       = {{Message Passing Interface Forum}},
  title        = {Changes in {MPI-3.0} (Annex B, MPI-4.1 document)},
  howpublished = {\url{https://www.mpi-forum.org/docs/mpi-4.1/mpi41-report/node597.htm}},
  note         = {Authoritative itemized MPI-3.0 change list: C++ binding removal, nonblocking
                  collectives, MPI\_COMM\_SPLIT\_TYPE, neighborhood collectives, MPI\_T}
}

@misc{mpi-4.0-changes,
  author       = {{Message Passing Interface Forum}},
  title        = {Changes in {MPI-4.0} (Annex B, MPI-4.1 document)},
  howpublished = {\url{https://www.mpi-forum.org/docs/mpi-4.1/mpi41-report/node591.htm}},
  note         = {Large-count \_c procedures; the Sessions model additions}
}

@misc{schulz26,
  author       = {Martin Schulz},
  title        = {The State of {MPI}: Current Standard and Future Plans},
  howpublished = {NHR PerfLab Seminar Series, 30 June 2026,
                  \url{https://hpc.fau.de/files/2026/07/2026-06-perflab-stateofmpi.pdf}},
  year         = {2026},
  note         = {Page counts per version: 228 / 238 / 608 / 608 / 647 / 852 / 868 / 1139 /
                  1166 / 1189. Some dates in this deck (MPI-1.1 ``Nov 1995'', MPI-2.0
                  ``Nov 1997'') disagree with the Forum's own approval dates in mpi-forum-docs;
                  prefer the latter}
}

@misc{schuchart-slides,
  author       = {Joseph Schuchart},
  title        = {{MPI} Finally Needs to Deal with Threads},
  howpublished = {EuroMPI, \url{https://eurompi.org/assets/slides/Schuchart-MPI-Threads.pdf}},
  note         = {Quotes MPI-5.0 \S11.6.1 thread-compliance clauses and \S11.6.2 user
                  responsibility for races}
}

@misc{hpcwire06,
  author       = {Michael Feldman},
  title        = {The Search for a New {HPC} Language},
  howpublished = {HPCwire, 25 August 2006,
                  \url{https://www.hpcwire.com/2006/08/25/the_search_for_a_new_hpc_language/}},
  year         = {2006},
  note         = {Source of the Ewing Lusk quotation ``Nobody loves MPI...''}
}

@misc{almasi-abstract,
  author       = {George Alm\'asi},
  title        = {Position paper, DOE ASCR Programming Challenges Workshop},
  howpublished = {\url{https://science.osti.gov/-/media/ascr/pdf/research/cs/Programming_Challenges_Workshop/ProgrammingChallengesAbstract_GeorgeAlmasi.pdf}},
  note         = {``no programming model today can survive in the HPC era unless it can
                  complement and succeed MPI''; the two-level hierarchy critique}
}

@misc{mpi-poll95,
  author       = {{Ohio Supercomputer Center LAM Project}},
  title        = {{MPI} Poll '95 Results},
  howpublished = {\url{https://www.dcs.ed.ac.uk/home/trollius/www.osc.edu/Lam/mpi/mpi_poll_comments.html}},
  year         = {1995},
  note         = {The ``stripped version of MPI ... six basic MPI calls'' user request; the poll
                  Gropp cites for ``essentially all MPI routines were in use by someone''}
}

@misc{cornell-comm,
  author       = {{Cornell Center for Advanced Computing}},
  title        = {Communicators (Cornell Virtual Workshop: Message Passing Interface)},
  howpublished = {\url{https://cvw.cac.cornell.edu/mpi/mpi-messages/mpi-communicators}},
  note         = {``the system, not the programmer, assigns identification''. Tutorial source}
}

@misc{clustermonkey,
  author       = {Jeffrey M. Squyres},
  title        = {{MPI}: Groups and Communicators},
  howpublished = {ClusterMonkey MPI column,
                  \url{https://www.clustermonkey.net/MPI/mpi-groups-and-communicators.html}},
  note         = {``there is no wildcard communicator value''; the communicator as a
                  system-level tag}
}

@misc{hpcwiki,
  title        = {{MPI}},
  howpublished = {HPC Wiki, \url{https://hpc-wiki.info/hpc/MPI}},
  note         = {``500+ MPI functions''. Secondary source}
}

@misc{wikipedia-mpi,
  title        = {Message Passing Interface},
  howpublished = {Wikipedia, \url{https://en.wikipedia.org/wiki/Message_Passing_Interface}},
  note         = {Funding instruments (NSF ASC-9310330, CCR-8809615, Esprit P6643) and the
                  1991 Austrian retreat claim. Secondary source; the retreat claim is
                  [UNVERIFIED] against any Forum document}
}
```

### Verification status summary

Verified against primary sources (MPI standard documents, MPI Forum procedures/meeting records, the
Williamsburg workshop report, or peer-reviewed papers by Forum participants): all version dates and page
counts; the complete goal and non-goal lists; the buffering, opaque-object, RMA, communicator, and
MPI-2.2-criteria rationales; the local/nonlocal and blocking/nonblocking definitions; the 1993–94 and
current voting rules; participant and organization counts; chapter-owner assignments; the MPI-3.0 and
MPI-4.0 itemized change lists; the MPI-5.0 ABI chapter; Open MPI's and MPICH's origin narratives; every
quotation in §8.

Explicitly flagged as unverified or contested in the body: the 1991 Austrian retreat; CMMD's absence from
MPI's influence list being *caused* by TMC's collapse; the exact provenance and date of the widely mirrored
Gropp/Lusk/Thakur tutorial slides `[gropp-slides]`; the exact venue and date of Thakur's MPI-IO
retrospective talk `[thakur-retro]`; date discrepancies between Walker's slides `[walker17]` / Schulz's deck
`[schulz26]` and the Forum's own approval table `[mpi-forum-docs]` (the Forum table is preferred throughout);
and the phrase "performance is a primary goal," which is a common paraphrase rather than a verbatim
quotation from the standard's overview.
