# The History, Standardization Process, and Design Philosophy of MPI

**Research note for the AgentMPI paper.** Background material on how MPI came to exist, how the MPI Forum
operated, what the Forum explicitly said it was trying to achieve, what it deliberately refused to
standardize, and why retrospectives credit those choices for MPI's durability. Inline markers like
`[gropp2001learning]` correspond to BibTeX entries in the [BibTeX](#bibtex) section at the end.

Claims I could not verify against a primary or reliable secondary source are marked `[UNVERIFIED]`.

---

## 1. The portability crisis of early-1990s parallel computing

### 1.1 The structural problem

By the late 1980s, distributed-memory MIMD machines had displaced both vector supercomputers and
SIMD arrays as the scaling path of choice, but the price of that architectural simplicity was paid
entirely in software: two processors that needed to share data had to exchange explicit messages,
and performance depended on getting both the data placement and the placement of the message-passing
calls right [kennedy2007hpf]. Message passing was thus the *de facto* programming model for MIMD
distributed-memory systems well before any standard existed.

The problem was that every machine had its own message-passing library, and they were mutually
incompatible. Marc Snir and colleagues, writing about the IBM SP1/SP2 communication software, put the
situation bluntly: at the time development started on the SP1 Parallel Operating Environment "there
was no accepted standard for such libraries: parallel system vendors and third-party software vendors
supported proprietary, incompatible libraries" [snir1995sp2]. The consequence for users was that
porting an application between two supercomputers meant rewriting its entire communication layer.
For ISVs it meant that the addressable market for any parallel application was a single vendor's
installed base. For vendors it meant no software ecosystem, which in turn suppressed hardware sales —
a classic coordination failure.

The MPI-1 standard itself frames its own motivation in exactly these terms: the "main advantages of
establishing a message-passing standard are portability and ease of use," and standardization
"provides vendors with a clearly defined base set of routines that they can implement efficiently, or
in some cases for which they can provide hardware support, thereby enhancing scalability"
[mpiforum1995mpi11]. Note the two-sided argument: a standard is valuable to users because it makes
code portable, *and* valuable to vendors because it tells them precisely which small set of
primitives is worth accelerating in hardware. This is a recurring theme worth transferring to
AgentMPI: a good standard is simultaneously a portability contract for consumers and an optimization
target for producers.

### 1.2 Vendor-specific libraries

**Intel NX / NX-2 (iPSC/2, iPSC/860, Touchstone Delta, Paragon).** NX/2 was the node operating system
for Intel's second-generation hypercubes, described by Paul Pierce at the Third Conference on
Hypercube Concurrent Computers and Applications [pierce1988nx2]. The MPI-1 standard explicitly names
"Intel's NX/2" as one of the systems that strongly influenced MPI [mpiforum2025mpi50, §2.2]. NX
contributed the basic shape of the point-to-point interface that MPI users would recognize:
typed/tagged messages, blocking and non-blocking send/receive pairs, and probe-style inquiry, all
addressed by absolute node number.

**nCUBE Vertex.** Vertex was the nCUBE node kernel and message-passing interface; the MPI Forum cites
the *nCUBE 2 Programmers Guide* directly [ncube1990vertex] and names "nCUBE's Vertex" among MPI's
influences [mpiforum2025mpi50, §2.2].

**Thinking Machines CMMD (CM-5).** CMMD was the message-passing library for the CM-5, supporting both
a host/node programming model and a "hostless" (SPMD) model [tmc1993cmmd]. CMMD is notable in this
history for two reasons. First, it demonstrated that a MIMD machine from a SIMD-heritage vendor still
needed an explicit message-passing layer. Second, Thinking Machines retained the data-parallel CM
Fortran language on the MIMD CM-5, making the CM-5 the main proving ground for the data-parallel
alternative that became High Performance Fortran [kennedy2007hpf].

**Meiko (CS-1/CS-2, transputer heritage, later ELAN).** Meiko appears in this history mainly as one of
the vendors present at the Forum table — Cray, IBM, Intel, Meiko, NEC and Thinking Machines are all
named among the 40+ participating organizations [ref2014casestudy] — and as a measured data point in
early cross-machine latency comparisons alongside the SP1, Paragon, CM-5 and Touchstone Delta
[gropp1995sp1]. `[UNVERIFIED]` I did not locate a primary specification document for the Meiko CS-2
message-passing interface, so I would avoid making specific claims about what MPI borrowed from Meiko.

**IBM EUI / MPL (SP1, SP2).** This is the best-documented case and the most instructive. IBM built its
own message-passing library for the SP1 rather than adopting PVM, because "the design of PVM, which
is optimized for IP communication in a networked environment, requires data copying operations that
can be avoided with a library more directly targeted to an SP environment," and because PVM version 2
lacked collective communication [snir1995sp2]. The library acquired "two equally unimaginative names":
External User Interface (EUI) and Message-Passing Library (MPL) [snir1995sp2]. Its design was
influenced by IBM Research's Vulcan operating environment and the **Venus** collective communication
library, and the final specification was completed in the summer of 1992, with first implementations
operational by fall 1992 — including an SP1 prototype demonstrated at Supercomputing '92
[snir1995sp2]. The Venus work on process groups as a coordination mechanism [bala1992venus] and the
companion work on scalable portable collective communication libraries [bala1992collective] are both
cited by the MPI standard as "work at the IBM T. J. Watson Research Center" that strongly influenced
MPI [mpiforum2025mpi50, §2.2].

EUI/MPL is also the cleanest demonstration of the *scale gap* MPI closed. EUI comprised 33 functions
in three groups (task management, point-to-point, collective) [snir1995sp2], of which nine were basic
point-to-point [franke1994mpisp]. MPI's point-to-point layer alone was "much richer than the EUI one
(53 functions)" [franke1994mpisp]. The IBM implementers' own account of porting to MPI names the
single most consequential difference precisely:

> "One significant change introduced by MPI is the use of communicators. In EUI (as in most other
> message passing libraries) the `dest` parameter is an absolute index that identifies the message
> destination. In contrast, in MPI, `dest` is the relative index of the destination within an ordered
> group of processes that is identified by the `comm` argument. This mechanism provides important
> support for modular development of large codes and libraries: a module running on a subset of
> processes can use a local name space for its communication." [franke1994mpisp]

Gropp and Lusk's account of the 128-node SP1 at Argonne adds the user-side counterpart: EUI's
performance problems led IBM to replace it "almost immediately" with a user-space implementation
(EUIH), then with MPL release 2 — and *users of the portability libraries saw no changes in their
codes* [gropp1995sp1]. That is the value proposition of an interface layer, empirically demonstrated
on a production machine one year before MPI-1.0 shipped.

### 1.3 Portable research systems

**PVM (Parallel Virtual Machine).** Sunderam's 1990 paper describes PVM as "a programming environment
for the development and execution of large concurrent or parallel applications" over "a collection of
heterogeneous computing elements interconnected by one or more networks," with facilities for
concurrent/sequential/conditional execution of components and "certain forms of error detection and
recovery" [sunderam1990pvm]. The 1994 MIT Press book is the canonical reference [geist1994pvm]. PVM
was the most widely used public-domain message-passing system at the time MPI was designed
[snir1995sp2] and MPI cites it as an influence [mpiforum2025mpi50, §2.2]. Critically, PVM was a
*virtual machine*: PVM daemons provided a lightweight distributed operating system, with
`pvm_spawn` for process creation, `pvm_config` for resource inquiry, and `pvm_reg_tasker` as a hook
for external resource managers [gropp2002goals]. MPI deliberately declined to standardize any of
this (see §5.9).

**p4.** Butler and Lusk's p4 was "a portable library of C and Fortran subroutines for programming
parallel computers ... in use since 1984," covering shared-memory monitors, distributed-memory message
passing, and *clusters* — shared-memory multiprocessors communicating by message passing
[butler1994p4]. p4's ancestor was the m4-based "Argonne macros" system [butler1994p4]. p4 is the
direct source of two things MPI inherited: the *portability-layer discipline* (a single API implemented
over many transports) and, through MPICH's `ch_p4` device, the actual transport that carried early MPI
on workstation clusters [gropp1996mpich, gropp2002goals].

**Chameleon.** Gropp and Smith's Chameleon [gropp1993chameleon] was explicitly a thin portability
layer over vendor message-passing libraries, designed so that the abstraction cost approached zero.
The MPI standard names Chameleon among its influences [mpiforum2025mpi50, §2.2], and MPICH's
architecture — a small Abstract Device Interface below a portable upper layer — is Chameleon's idea
carried into the reference implementation [gropp1996mpich]. The name "MPICH" itself signals the
lineage ("CH" for Chameleon).

**Zipcode.** This is the single most important predecessor for AgentMPI's purposes. Zipcode began as
"a portable multicomputer communication library atop the reactive kernel" [skjellum1990zipcode] and
matured into a system organized around *mailers*: objects that bundle a process group, a communication
context, and a virtual topology, with "invoices" describing non-contiguous data layouts
[skjellum1994zipcode]. These are, respectively, MPI's communicator, its context/tag-space isolation,
its Cartesian and graph topologies, and its derived datatypes. Zipcode's authors were simultaneously
arguing the case for context-based isolation as a *library-writing* discipline: Skjellum, Doss and
Bangalore's "Writing Libraries in MPI" appeared at the Scalable Parallel Libraries Conference in
October 1993, while MPI-1 was still being drafted [skjellum1993writinglibraries]. Anthony Skjellum
led the MPI-1 subcommittee on Groups, Contexts and Communicators [mpiforum1995mpi11, Acknowledgments].
The MPI standard cites both Zipcode documents as influences [mpiforum2025mpi50, §2.2].

**PARMACS.** PARMACS descended from the Argonne/GMD macros for portable parallel Fortran
[bomans1990argonnegmd] and matured into a message-passing library documented by Calkin, Hempel, Hoppe
and Wypior [calkin1994parmacs]. Rolf Hempel, a PARMACS author, co-wrote the initial MPI1 draft and
chaired the MPI-1 Process Topologies subcommittee [mpiforum1995mpi11, Acknowledgments]; MPI's process
topology chapter is recognizably PARMACS' contribution. PARMACS is cited as an MPI influence
[mpiforum2025mpi50, §2.2] and, alongside PVM, is named by the HPF historians as one of the
message-passing libraries "already being used for programming MIMD systems" when HPF began
[kennedy2007hpf].

**Express.** Parasoft's Express was a commercial portable communication environment for parallel
computers [parasoft1992express], cited as an MPI influence [mpiforum2025mpi50, §2.2].

**TCGMSG.** Harrison's Theoretical Chemistry Group Message-Passing toolkit was a deliberately minimal,
domain-driven portable toolkit for quantum chemistry codes, described in the *International Journal of
Quantum Chemistry* [harrison1991tcgmsg]. TCGMSG is the strongest early evidence for the claim that a
*small* interface can carry large production applications — a claim later confirmed quantitatively for
MPI itself [laguna2019study].

**Linda.** Carriero and Gelernter's Linda took the opposite approach: coordination through a
content-addressable shared tuple space rather than explicit point-to-point messages
[carriero1989linda]. Linda was available on the SP2 through third-party vendors [snir1995sp2], and its
existence is important negative evidence for AgentMPI: the MPI Forum knew about associative,
content-addressed coordination and chose explicit, addressed messaging instead. MPI does not have
tuple spaces, blackboards, or any implicit rendezvous mechanism.

**CHIMP and PICL.** Edinburgh Parallel Computing Centre's CHIMP [epcc1992chimp] and the Oak Ridge
PICL instrumented communication library [geist1990picl] are both named as contributors
[mpiforum2025mpi50, §2.2]. PICL matters for a specific reason: instrumentation was a first-class
concern from the start, and MPI-1 shipped with a standardized *profiling interface* (PMPI) in the
standard itself [mpiforum1995mpi11, §1.4].

**Feitelson's communicators.** A 1991 Hebrew University technical report proposing "communicators:
object-based multiparty interactions for parallel programming" [feitelson1991communicators] is cited
in the MPI bibliography and is part of the intellectual pedigree of the communicator abstraction
alongside Zipcode's mailers.

### 1.4 What MPI took from whom

| Source | Contribution absorbed into MPI |
| --- | --- |
| Intel NX/NX-2 [pierce1988nx2] | Shape of the point-to-point interface: tagged messages, blocking/non-blocking pairs, probe |
| nCUBE Vertex [ncube1990vertex] | Node-level message-passing primitives |
| IBM EUI/MPL, Venus, Vulcan [snir1995sp2, bala1992venus, bala1992collective] | Process groups as a coordination mechanism; portable scalable collectives; the "small but complete" sizing instinct |
| PVM [sunderam1990pvm, geist1994pvm] | Heterogeneity handling (pack/unpack → datatypes); dynamic process creation as a *deferred* goal; the resource-management problems MPI chose not to solve |
| p4 [butler1994p4] | Portability-layer discipline; the actual cluster transport under early MPICH |
| Chameleon [gropp1993chameleon] | Near-zero-cost abstraction layer over vendor libraries; the ADI idea in MPICH |
| Zipcode [skjellum1990zipcode, skjellum1994zipcode] | Communicators (mailers) = group + context + topology; "invoices" → derived datatypes; library-safety argument |
| PARMACS [bomans1990argonnegmd, calkin1994parmacs] | Process topologies (Cartesian/graph virtual topologies) |
| Express [parasoft1992express] | Commercial portable-environment precedent |
| TCGMSG [harrison1991tcgmsg] | Evidence that a minimal interface suffices for real applications |
| PICL [geist1990picl] | Instrumentation as a first-class concern → PMPI profiling interface |
| CHIMP [epcc1992chimp] | Portable interface design input from EPCC |
| Linda [carriero1989linda] | Considered and *not* adopted: no shared tuple space, no content-addressed rendezvous |

---

## 2. The MPI Forum process

### 2.1 Williamsburg, April 1992

The standardization process began at the **Workshop on Standards for Message-Passing in a Distributed
Memory Environment**, sponsored by the Center for Research on Parallel Computing and held **April
29–30, 1992, in Williamsburg, Virginia** [mpiforum2025mpi50, §2.2]. The workshop discussed "the basic
features essential to a standard message passing interface" and established a working group to
continue the process [mpiforum2025mpi50, §2.2].

David Walker's ORNL technical report from August 1992 is the workshop's substantive output
[walker1992standards]. Two of its ideas deserve emphasis for AgentMPI. First, Walker articulates an
**"onion skin" layering model** in which a small, efficiently implementable inner core is wrapped by
successively more convenient outer layers, so that expert users can drop down and casual users need
not. Second, the report already identifies **message contexts** and **non-contiguous message
descriptors** as required features [walker1992standards] — i.e. the two mechanisms that became
communicators and derived datatypes were on the requirements list before any draft existed.

### 2.2 The MPI1 draft, November 1992 / February 1993

"A preliminary draft proposal, known as MPI-1, was put forward by Dongarra, Hempel, Hey, and Walker
in November 1992, and a revised version was completed in February 1993" [mpiforum2025mpi50, §2.2];
the revised version is ORNL/TM-12231 [dongarra1993proposal]. The standard's own assessment of this
draft is candid and worth quoting for process reasons:

> "Since MPI-1 was primarily intended to promote discussion and 'get the ball rolling,' it focused
> mainly on point-to-point communications. MPI-1 brought to the forefront a number of important
> standardization issues, but did not include any collective communication routines and was not
> thread-safe." [mpiforum2025mpi50, §2.2]

So the seed document was deliberately incomplete and deliberately wrong in known ways. Its function
was to make the design space concrete enough to argue about. (Note the naming collision: this
*draft* was called "MPI1"; the eventual *standard* is MPI-1.0.)

### 2.3 Minneapolis, November 1992: adopting a governance model

At a November 1992 working-group meeting in Minneapolis it was decided "to place the standardization
process on a more formal footing, and to generally adopt the procedures and organization of the High
Performance Fortran Forum" [mpiforum2025mpi50, §2.2]. Subcommittees were formed for the major
component areas, each with its own email discussion list, and a target was set of producing a draft
standard by fall 1993 [mpiforum2025mpi50, §2.2]. HPFF, in turn, had begun in 1991 at the
Supercomputing conference in Albuquerque, with a one-year target schedule and a rule that HPF "would
include only features that had been demonstrated in at least one language and compiler, including
research compilers" [kennedy2007hpf]. MPI inherited both the open-forum machinery and the
demonstrated-implementation instinct from HPFF — while, as §6.3 discusses, avoiding the failure modes
HPFF fell into.

### 2.4 Cadence and participation

- **Meeting rhythm:** "the MPI working group met every 6 weeks for two days throughout the first 9
  months of 1993" and presented the draft standard at Supercomputing '93 in November 1993
  [mpiforum2025mpi50, §2.2].
- **Scale:** "about 60 people from 40 organizations mainly from the United States and Europe," including
  most major concurrent-computer vendors plus university, government-lab and industry researchers
  [mpiforum2025mpi50, §2.2]. The MPI-1.1 front matter says MPIF had "participation from over 40
  organizations" and "has been meeting since January 1993" [mpiforum1995mpi11].
- **Openness:** "These meetings and the email discussion together constituted the MPI Forum, membership
  of which has been open to all members of the high performance computing community"
  [mpiforum2025mpi50, §2.2].
- **Non-sanctioned:** "MPIF is not sanctioned or supported by any official standards organization"
  [mpiforum1995mpi11]. MPI's authority is entirely reputational and adoption-driven, not conferred by
  ISO/ANSI/IEEE. This is a striking and directly transferable fact.
- **Dates:** MPI-1.0 was the document of **May 5, 1994**, generally released June 1994; MPI-1.1 followed
  in **June 1995** after the Forum "reconvened" beginning March 1995 to correct errors and make
  clarifications [mpiforum1995mpi11]. Laguna et al. tabulate MPI-1.0 as 05/1994 and MPI-1.1 as
  06/12/1995 [laguna2019study]. MPI-1.0 also appeared as a journal publication — a special double
  issue of the *International Journal of Supercomputer Applications and High Performance Computing*,
  8(3–4):165–414 [mpiforum1994] — which is the citation most 1990s papers use and which gave the
  standard academic visibility beyond the Forum's own web site.

Hempel and Walker, two of the four MPI1-draft authors, later wrote their own account of the
standardization effort, which is the best participant-authored narrative of the period
[hempel1999emergence].

### 2.5 Who did what

The MPI-1.1 acknowledgments assign responsibility explicitly, which is unusually useful for a
process study [mpiforum1995mpi11]:

| Role | People |
| --- | --- |
| Conveners and Meeting Chairs | Jack Dongarra, David Walker |
| Minutes | Ewing (Rusty) Lusk, Bob Knighten |
| Point-to-Point Communications | Marc Snir, William Gropp, Ewing Lusk |
| Collective Communications | Al Geist, Marc Snir, Steve Otto |
| Editor | Steve Otto |
| Process Topologies | Rolf Hempel |
| Language Binding | Ewing Lusk |
| Environmental Management | William Gropp |
| Profiling | James Cownie |
| Groups, Contexts, and Communicators | Tony Skjellum, Lyndon Clarke, Marc Snir, Richard Littlefield, Mark Sears |
| Initial Implementation Subset | Steven Huss-Lederman |

Two of these entries are structurally interesting. First, **Profiling had a named owner from the
start** — observability was a chapter, not an afterthought. Second, there was a subcommittee for the
**"Initial Implementation Subset"**: someone was formally responsible for identifying the subset an
implementer should build first. Tony Hey (a co-author of the MPI1 draft [dongarra1993proposal]) and
Nathan Doss (co-author of MPICH [gropp1996mpich], of "Writing Libraries in MPI"
[skjellum1993writinglibraries], and of the Zipcode design paper [skjellum1994zipcode]) were central to
the surrounding effort without appearing in this particular table.

### 2.6 Governance rules

The Forum's current written procedures [mpiforumprocedures] formalize what was originally convention.
The key mechanisms:

- **One organization, one vote.** Individuals declare at registration which organization they represent;
  ballots poll organizations, not people. Proxies are not permitted; an absent organization has
  implicitly abstained [mpiforumprocedures].
- **Attendance-earned eligibility (OOE).** An organization is generally eligible to vote only if it
  registered for and had a representative present at **two out of the last three** voting meetings,
  including the current one [mpiforumprocedures]. Voting rights are earned by sustained participation,
  not by membership fee or by showing up once for a contentious vote.
- **Per-meeting eligibility (IMOVE).** An organization votes at a given meeting only if it is OOE, has
  registered before the first ballot, and had a representative present during the meeting. Once IMOVE,
  it stays IMOVE for the rest of that meeting even if its representative leaves
  [mpiforumprocedures].
- **Meeting quorum:** more than **2/3 of OOE organizations** must have registered for the meeting
  [mpiforumprocedures].
- **Ballot quorum:** more than **3/4 of IMOVE organizations** must cast a vote (as opposed to
  abstaining). The stated rationale is that this "prevents large numbers of abstentions from skewing
  results" [mpiforumprocedures].
- **Supermajority to pass:** "yes" votes must exceed **3/4 of the sum of yes and no votes**; the stated
  rationale is that this "sets a high requirement for consensus" [mpiforumprocedures].
- **Two readings, two ballots.** A general text proposal requires a *formal reading* at a quorate
  meeting with the exact text published in advance, then two separate ballots. Between the readings,
  text changes require a special "NO-NO-VOTE" ballot that passes only with **zero** no votes. If either
  ballot fails, the proposal starts over from the reading [mpiforumprocedures].
- **Working groups need sponsors and renewal.** A working group is established only when at least four
  IMOVE organizations indicate support, and is re-authorized roughly annually by the same mechanism
  [mpiforumprocedures].
- **Meetings are public.** Non-voting meetings are open and connection information is published to
  anyone who asks the Secretary [mpiforumprocedures].

The design intent is legible: make it cheap to *participate*, expensive to *change the text*, and
impossible to win by ambush. Any single organization can block a text change during the amendment
window; passing anything requires broad, repeated, in-person consensus.

### 2.7 A rule worth stealing: MPI-2.2's admission criteria

For MPI-2.2 the Forum wrote down explicit admission criteria for extensions
[mpiforum2025mpi50, §2.5]:

1. Any correct MPI-2.1 program is a correct MPI-2.2 program.
2. Any extension must have significant benefit for users.
3. Any extension must not require significant implementation effort — **"to that end, all such changes
   are accompanied by an open source implementation."**

Backward compatibility as a hard gate, demonstrated user benefit, and a working open-source
implementation as the price of admission. The same section records that proposals floated for MPI-2.2
but not meeting these bars "were later moved to MPI-3" [mpiforum2025mpi50, §2.5] — a documented
escalation path rather than a rejection.

### 2.8 The document as an artifact

Two structural features of the MPI standard document itself are load-bearing. First, the text
distinguishes **normative specification** from **"Rationale"** and **"Advice to users" / "Advice to
implementors"** blocks; the rationale blocks record *why* a decision was made, which is why we can
still reconstruct the Forum's reasoning three decades later (all the "Rationale" quotations in this
note come from such blocks, e.g. [mpiforum2025mpi50, §21.2.1]). Second, every version carries an
explicit **compatibility statement**: "Any valid MPI-2.2 program not using any of these removed MPI
procedures or objects is a valid MPI-3.0 program" [mpiforum2025mpi50, §2.6]; "Any valid MPI-4.1
program is a valid MPI-5.0 program" [mpiforum2025mpi50, §2.10]. Version deltas are stated as
program-level compatibility guarantees, not as changelogs.

The Forum also invented a **"Journal of Development"** for material where "the MPI process and
framework seem likely to be useful, but where more discussion and experience are needed before
standardization" — explicitly *not part of the standard* [mpiforum2025mpi50, §2.3;
mpiforum1997jod]. This is the institutional embodiment of "standardize only what is well understood":
speculative ideas got a published home that carried no conformance obligation.

---

## 3. Version timeline with feature deltas

Release dates below are as stated by the MPI Forum's document index [mpiforumdocs] and the standard's
own background sections [mpiforum2025mpi50], cross-checked against Laguna et al.'s table
[laguna2019study].

### MPI-1.0 — document of May 5, 1994 (released June 1994)

**Included** [mpiforum1995mpi11, §1.4]: point-to-point communication; collective operations; process
groups; communication contexts; process topologies; Fortran 77 and C bindings; environmental
management and inquiry; profiling interface.

**Motivation:** end the portability crisis (§1.1) with an interface vendors could implement natively
and efficiently [mpiforum1995mpi11, §1.1]. Note what is on the "included" list that is not
communication: contexts, topologies, and profiling. The Forum shipped the *composability* and
*observability* machinery in v1.0, not later.

### MPI-1.1 — June 1995

The Forum reconvened in March 1995 to correct errors and clarify the May 5, 1994 document; "the
changes from Version 1.0 are minor" [mpiforum1995mpi11]. A change-marked edition was published
alongside the clean one [mpiforum1995mpi11].

### MPI-1.2 — part of the MPI-2 document (1997)

MPI-1.2 is not a standalone document: corrections and clarifications to MPI-1.1 were "collected in
Chapter 3 of the MPI-2 document: 'Version 1.2 of MPI'," which "also contains the function for
identifying the version number" (`MPI_GET_VERSION`) [mpiforum2025mpi50, §2.3]. Making the standard
*self-describing at runtime* was itself a v1.2 feature.

### MPI-1.3 — approved July 1, 2008 (final vote September 4, 2008)

"The document MPI-1.3 was released as final end of the MPI-1 series. It was developed for technical and
historical reasons in the framework of the development of MPI-2.1. It does not introduce a new
(version, subversion) number" [mpiforumdocs]. It consolidated MPI-1.1 + MPI-1.2 + errata so that
"MPI-1 compliance" had a single unambiguous referent [mpiforum2025mpi50, §2.3].

### MPI-2.0 — July 18, 1997

The MPI-2 effort, running from March 1995, organized itself into five explicit buckets
[mpiforum2025mpi50, §2.3]: (1) corrections/clarifications to MPI-1.1; (2) additions that do not
significantly change the *types* of functionality (new datatype constructors, language
interoperability); (3) "completely new types of functionality (dynamic processes, one-sided
communication, parallel I/O, etc.) that are what everyone thinks of as 'MPI-2 functionality'";
(4) Fortran 90 and C++ bindings; (5) areas needing more experience — moved to the Journal of
Development [mpiforum2025mpi50, §2.3; mpiforum1997jod].

Major additions and their motivations [mpiforum1997mpi2]:

- **One-sided / remote memory access (RMA)** — `MPI_Put`, `MPI_Get`, `MPI_Accumulate` with window
  objects and synchronization epochs. Motivated by irregular applications where the target does not
  know what it will receive, and by hardware that could do RDMA without remote CPU involvement.
- **Dynamic process management** — `MPI_Comm_spawn`, `MPI_Comm_connect`/`MPI_Comm_accept`,
  client/server rendezvous by published port name. MPI-1 had frozen the process set; MPI-2 unfroze it
  *without* introducing a virtual machine (§5.9).
- **Parallel I/O (MPI-IO)** — files as another target for the same derived-datatype machinery,
  including collective and non-contiguous access. Explicit reuse of an existing abstraction rather
  than a new one [gropp2002goals].
- **C++ bindings and Fortran 90 support** — plus language interoperability rules.
- **Thread support levels** — `MPI_Init_thread` with `MPI_THREAD_SINGLE`, `MPI_THREAD_FUNNELED`,
  `MPI_THREAD_SERIALIZED`, `MPI_THREAD_MULTIPLE`. MPI-1 had refused to standardize threads but had
  been careful to be *thread-safe by design* (§5.9); MPI-2 exposed a negotiated contract instead of
  mandating a level.
- **Extended collectives** — collective operations over intercommunicators.

Compliance was redefined cleanly: "MPI-2 compliance will mean compliance with all of MPI-2.1," and
forward compatibility is preserved throughout [mpiforum2025mpi50, §2.3].

### MPI-2.1 — approved September 4, 2008

A **merge**, not an extension: MPI-1.3 and MPI-2.0 were combined into a single coherent document
[mpiforumdocs]. The MPI-2.1 book runs 608 pages [mpiforumdocs]. The point of the exercise was that
users should no longer have to read two documents and a set of errata to know what MPI is.

### MPI-2.2 — approved September 4, 2009

A minor update fixing residual errors and ambiguities plus a small set of extensions meeting the three
criteria in §2.7 [mpiforum2025mpi50, §2.5]. The MPI-2.2 book is 647 pages [mpiforumdocs].
MPI-2.2 is also where the C++ bindings were deprecated (they were removed in MPI-3.0
[mpiforum2025mpi50, §2.6]; Laguna et al. note that "no C++ interface exists after MPI 2.1"
[laguna2019study]).

### MPI-3.0 — approved September 21, 2012

A major update [mpiforum2025mpi50, §2.6]. The 3.0 book is 852 pages [mpiforumdocs].

- **Nonblocking collectives** (`MPI_Ibcast`, `MPI_Iallreduce`, …). Motivated by the desire to overlap
  communication with computation and to break the "pseudo-synchronization" that blocking collectives
  impose on otherwise independent processes; Hoefler, Lumsdaine and Rehm's SC07 paper is the canonical
  motivation and implementation study [hoefler2007nbc], with application-level evidence from conjugate
  gradient solvers [hoefler2007cg] and quantum-mechanical codes [hoefler2008sparsenbc].
- **Neighborhood collectives** (`MPI_Neighbor_allgather`, `MPI_Neighbor_alltoall` and variants). A
  collective over the *sparse* neighbor set defined by a Cartesian or graph topology, so that stencil
  and graph exchange patterns can be expressed once and optimized by the implementation rather than
  hand-coded as O(neighbors) point-to-point calls [hoefler2009sparse, hoefler2014advancedmpi]. This
  retroactively gave MPI-1's topology chapter real performance teeth.
- **Revised RMA plus shared-memory windows.** New window creation flavors, request-based RMA, remote
  atomics, and unified/separate memory models. `MPI_Comm_split_type` with `MPI_COMM_TYPE_SHARED` plus
  `MPI_Win_allocate_shared` lets ranks on the same node get direct load/store access to each other's
  memory [hoefler2014advancedmpi, hoefler2010hybrid] — MPI absorbing the on-node sharing case rather
  than ceding it entirely to threads.
- **Matched probe (`MPI_Mprobe`, `MPI_Improbe`, `MPI_Mrecv`, `MPI_Imrecv`).** `MPI_Probe` followed by
  `MPI_Recv` is racy in a multithreaded program: another thread can receive the message the first
  thread just probed. Matched probing atomically removes the message from the matching queue and hands
  back a handle [gregor2009probe, mpiforum2015mpi31, §3.8.2]. This is a pure *semantic* fix — no new
  data movement, just closing a race the original API permitted.
- **Tools information interface (`MPI_T`).** A standardized way for tools to enumerate and access an
  implementation's performance variables and control variables, complementing the PMPI interception
  interface that had existed since MPI-1 [schulz2018mug, mpiforum2012mpi30]. PMPI lets you see calls;
  `MPI_T` lets you see *inside*.
- **Fortran 2008 bindings** using ISO/IEC TS 29113 interoperability, with proper type checking and
  asynchronous-attribute handling.
- **Removals:** the deprecated C++ bindings and many deprecated routines and objects (e.g. `MPI_UB`)
  were deleted [mpiforum2025mpi50, §2.6]. MPI does eventually remove things — after a full
  deprecate-then-delete cycle spanning major versions.

### MPI-3.1 — approved June 4, 2015

A minor update: mostly corrections and clarifications, especially to the Fortran bindings. New
functions cover portable manipulation of `MPI_Aint` values, nonblocking collective I/O, and
name-to-index lookup for `MPI_T` performance and control variables; a general index was added
[mpiforum2025mpi50, §2.7]. "Any valid MPI-3.0 program is a valid MPI-3.1 program"
[mpiforum2025mpi50, §2.7]. The MPI-3.1 book runs 868 pages [mpiforumdocs] and defines **444 MPI
functions** [laguna2019study].

### MPI-4.0 — approved June 9, 2021

A major update [mpiforum2021mpi40; mpiforum2025mpi50, §2.8]. Per the standard's own summary, "the largest changes are the
addition of large-count versions of many routines to address the limitations of using an `int` or
`INTEGER` for the count parameter, persistent collectives, partitioned communications, an alternative
way to initialize MPI, application info assertions, and improvements to the definitions of error
handling" [mpiforum2025mpi50, §2.8].

- **Big count.** `int`/`INTEGER` counts cap a single operation at ~2^31 elements. MPI-4.0 adds
  `_c` variants (e.g. `MPI_Send_c`) with `MPI_Count` counts. A 27-year-old type choice finally became
  a correctness bug at scale, and the fix was additive: new symbols, old ones untouched.
- **Persistent collectives.** `MPI_Bcast_init`, `MPI_Allreduce_init`, … : pay the schedule-planning and
  resource-binding cost once, then `MPI_Start` repeatedly. Motivated by the observation that
  communication patterns in iterative codes are known in advance and re-derived every iteration
  [morgan2017persistent, holmes2019persistent].
- **Partitioned communication.** `MPI_Psend_init` / `MPI_Precv_init` with `MPI_Pready` per partition: a
  single logical message contributed to by many threads, each marking its own partition ready, so
  transmission can begin before all contributions arrive. Motivated directly by the MPI+threads
  performance problem — the "Finepoints" line of work [grant2019finepoints, grant2015lightweight].
- **Sessions.** `MPI_Session_init` and friends provide "an alternative way to initialize MPI"
  [mpiforum2025mpi50, §2.8] that does not go through `MPI_Init`/`MPI_COMM_WORLD`. Motivated by the
  observation that a single global initial communicator is both a scalability bottleneck and a
  resource-isolation failure: independent libraries in one process should be able to acquire their own
  MPI resources from named process sets without agreeing on a global world [holmes2016sessions]. For
  AgentMPI this is the most interesting MPI-4 feature: it is MPI retrofitting *away* from its one
  global namespace after 24 years.
- **Application info assertions.** `MPI_Info` assertions (e.g. `mpi_assert_no_any_tag`) let an
  application promise it will not use certain generality, enabling the implementation to specialize.
  Optional promises rather than mandatory restrictions.
- **Error handling improvements.** Better-defined error semantics and the ability to attach error
  handlers to more object kinds — groundwork for, but still not, fault tolerance.

### MPI-4.1 — approved November 2, 2023

A minor update [mpiforum2023mpi41]: "mostly corrections and clarifications to the MPI-4.0 document. Several routines, the
attribute key `MPI_HOST`, and the `mpif.h` Fortran include file are deprecated. A new routine provides
a way to inquire about the hardware on which the MPI program is running" [mpiforum2025mpi50, §2.9].
Compatibility carries a caveat: "Any valid MPI-4.0 program is a valid MPI-4.1 program with the
exception of semantic changes listed in Chapter Semantic Changes and Warnings"
[mpiforum2025mpi50, §2.9] — even minor releases account for behavioural drift in a dedicated chapter.
Hierarchical hardware-topology awareness had been argued for in the literature beforehand
[goglin2018hierarchical].

### MPI-5.0 — approved June 5, 2025

"MPI-5.0 is a major update to the MPI standard. The largest change is the addition of a standard
Application Binary Interface (ABI) to allow interoperability of different implementations. In addition,
there are a number of smaller improvements and corrections. Any valid MPI-4.1 program is a valid
MPI-5.0 program" [mpiforum2025mpi50, §2.10]. The Forum's document index confirms approval on
**June 5, 2025** [mpiforumdocs]. (The unofficial HTML rendering is footered "MPI-5.0 of June 9, 2025";
I treat the docs-index date of June 5, 2025 as authoritative for approval.)

The ABI is the most conceptually significant addition since communicators, and it is the clearest
possible admission of a *limit* of source-level standardization. For 31 years MPI standardized source
compatibility, so a binary compiled against one MPI could not run against another; every downstream
package had to be rebuilt per MPI implementation. MPI-5.0 mandates a header named `mpi.h` and a
library named `mpi_abi`, requires that "ABI-compliant implementations must not require more than
`mpi_abi` or its versioned variant as the sole direct dependency of the application binary," and
forbids mixing ABIs in one application [mpiforum2025mpi50, §21.2.1]. The standard ABI also
**excludes** everything deprecated in MPI-3.1 or earlier, with an explicit rationale: "If deprecated
features are included in the standard ABI, deleting them will cause a backwards-incompatibility issue
in the ABI. Removing them from the ABI now makes it straightforward for them to be deleted from MPI in
the future" [mpiforum2025mpi50, §21.2.1]. The EuroMPI 2023 paper by Hammond et al. is the design
study behind it [hammond2023abi].

---

## 4. Design philosophy: the Forum's stated goals

### 4.1 The canonical goal list

The standard's own goal list has been carried forward essentially unchanged from MPI-1 to MPI-5
[mpiforum2025mpi50, §2.1; mpiforum1995mpi11, §1.1]:

> "The goal of the Message-Passing Interface, simply stated, is to develop a widely used standard for
> writing message-passing programs. As such the interface should establish a practical, portable,
> efficient, and flexible standard for message passing."

followed by:

1. Design an **application programming interface** — "not necessarily for compilers or a system
   implementation library."
2. **Allow efficient communication:** "Avoid memory-to-memory copying, allow overlap of computation and
   communication, and offload to communication co-processors, where available."
3. Allow implementations usable in a **heterogeneous** environment.
4. Allow **convenient C and Fortran bindings**.
5. **Assume a reliable communication interface:** "the user need not cope with communication failures.
   Such failures are dealt with by the underlying communication subsystem."
6. Define an interface **implementable on many vendors' platforms** "with no significant changes in the
   underlying communication and system software."
7. Semantics of the interface should be **language independent**.
8. The interface "should be designed to allow for **thread safety**."

Goal 5 is the most quietly consequential sentence in the entire document, and §5.9 returns to it.
Goal 6 is a genuine constraint on ambition: no feature that would require vendors to rewrite their
system software could be standardized.

### 4.2 Gropp and Lusk's expanded reconstruction

Gropp and Lusk, writing in 2002 specifically to explain PVM/MPI differences, reconstruct the goals the
Forum set for itself *before* specifying details, and this list is the sharpest available statement of
MPI's philosophy [gropp2002goals]:

- "MPI would be **a library for writing application programs, not a distributed operating system**."
- "MPI would **not mandate thread-safe implementations, but its specification would allow them**. Thread
  safety implies that there can be no notion of a 'current' buffer, message, error code, and so on."
- "MPI would be capable of **delivering high performance on high-performance systems**. Hence, **no
  memory copies would be mandated by the design**. Scalability, combined with correctness, for
  collective operations required that groups be 'static'."
- "MPI would be **modular, to accelerate the development of portable parallel libraries**. Modularity
  has many implications. For example, **all references must be relative to a module, not the entire
  program** ... Hence, process source/destination must be specified by rank in a group rather than by
  an absolute identifier, and **context must not be a visible value**."
- "MPI would be **extensible** to meet future needs and developments. This requirement led to an
  object-oriented approach without a commitment to an object-oriented language."
- "MPI would **support heterogeneous computing** ... although it would not require that all
  implementations be heterogeneous."
- "MPI would require **well-defined behavior** (no race conditions or avoidable
  implementation-specific behavior)."

And the economizing principle: "For simplicity, the MPI Forum sought to make **each approach solve as
many of these goals as possible**. For example, datatypes solve both heterogeneity and noncontiguous
data layouts, both for messages and for files. Similarly, communicators combine both process groups
with communications contexts" [gropp2002goals]. That is: minimize the number of *concepts*, not the
number of functions. Each concept is made to earn its place by discharging several requirements.

---

## 5. Design philosophy: nine principles, in detail

### 5.1 "MPI is a standard, not an implementation"

The standard says it in its own overview: "MPI is a specification, not an implementation; there are
multiple implementations of MPI. This specification is for a library interface; MPI is not a language,
and all MPI operations are expressed as functions, subroutines, or methods, according to the
appropriate language bindings" [mpiforum2025mpi50, §2.1].

Gropp and Lusk devote an entire section to the consequences, arguing that most PVM-vs-MPI confusion
"comes from comparing the specification of MPI with the implementation of PVM"
[gropp2002goals]. Their observations:

- "**Standards specifications tend to specify the minimum level of compliance, while any implementation
  offers more functionality.**" The Forum handled this by listing many features as "expected of a
  *high-quality implementation*" without mandating them [gropp2002goals]. This gave the standard a
  *third* normative tier between "required" and "unmentioned."
- Error recovery is the worked example: "Standards tend not to mandate specific behavior on errors,
  other than to list error indicator values. The expectation is that high-quality implementations will
  give users what they expect" [gropp2002goals].
- MPI refused to standardize implementation internals. On the question of a standard debugger interface
  to message queues: "By not specifying a model of the internals of an MPI implementation, such as
  defining a 'message queue' does, the MPI standard allows MPI implementations to make tradeoffs
  between the performance and functionality that the users want" [gropp2002goals]. The queue-inspection
  interface was instead developed as a *side document* outside the standard [mpiforumdocs;
  cownie1999debugger].
- Organizational corollary: "PVM was the effort of a single research group ... Moreover, the
  implementation team was the same as the design team, so design and implementation could interact
  quickly. In contrast, MPI was designed by the MPI Forum ... quite independently of any specific
  implementation but with the expectation that all of the participating vendors would implement it.
  Hence, all functionality had to be negotiated among the users and a wide range of implementors, each
  of whom had a quite different implementation environment in mind" [gropp2002goals].

The Forum did not, however, design in a vacuum. MPICH's own history records that "at the
organizational meeting of the MPI Forum at the Supercomputing '92 conference, Gropp and Lusk
volunteered to develop an immediate implementation that would track the Standard definition as it
evolved. The purpose was to quickly expose problems that the specification might pose for implementors
and to provide early experimenters with an opportunity to try ideas being proposed for MPI **before
they became fixed**" [gropp1996mpich, gropp1992testimpl]. The specification and a tracking
implementation were co-developed from the first organizational meeting onward.

### 5.2 Performance portability, not lowest common denominator

The standard's efficiency goal is stated operationally: avoid memory-to-memory copying, allow overlap
of computation and communication, allow offload to communication co-processors
[mpiforum2025mpi50, §2.1]. Gropp and Lusk sharpen it: "no memory copies would be mandated by the
design" [gropp2002goals].

This is why MPI's send has *four* flavors (standard, buffered, synchronous, ready) with three of them
existing solely to let the programmer state which buffering/synchronization contract they will accept,
and why non-blocking operations are split into initiation and completion. It is also why groups are
static: "Scalability, combined with correctness, for collective operations required that groups be
'static'" [gropp2002goals]. A performance/scalability requirement dictated a semantic restriction —
and the Forum recorded that dynamic groups meeting the same requirements remained "an open research
problem" [gropp2002goals].

Gropp identifies this as one of six reasons for MPI's success, and later frames the trade-off
honestly: MPI "achieves performance ... in part by not getting in the way of locality management" but
"loses productivity" because "user has no choice but to manage locality, which is both hard and
tricky" [gropp2001learning, gropp2022exampi].

### 5.3 Standardize what is well understood

MPI-1's own account of its omissions cites "the time constraint that was self imposed in finishing the
standard" and adds: "Features that are not included can always be offered as extensions by specific
implementations. Perhaps future versions of MPI will address some of these issues"
[mpiforum1995mpi11, §1.5].

The Forum then built institutions around this principle:

- The **Journal of Development** for material needing "more discussion and experience ... before
  standardization" — published, but explicitly not part of the standard [mpiforum2025mpi50, §2.3;
  mpiforum1997jod].
- The **MPI-2.2 criteria** requiring an accompanying open-source implementation
  [mpiforum2025mpi50, §2.5].
- **Side documents** for interfaces that were "commonly implemented" but that the Forum did not want to
  make normative — MPIR process acquisition, the message queue dumping interface, memory allocation
  kinds [mpiforumdocs].
- Inherited from HPFF: features had to have been demonstrated in at least one implementation
  [kennedy2007hpf].
- **Deprecate before delete.** C++ bindings were deprecated in MPI-2.2 and removed in MPI-3.0
  [mpiforum2025mpi50, §2.6]; MPI-4.1 deprecated `mpif.h` and `MPI_HOST`
  [mpiforum2025mpi50, §2.9]; MPI-5.0's ABI excludes anything deprecated in MPI-3.1 or earlier to make
  future deletion clean [mpiforum2025mpi50, §21.2.1].

Gropp attributes MPI's success partly to "a combination of forward-looking features, precise
definition, and judgment based on the experience of developers, vendors and users"
[gropp2012mpi3beyond].

### 5.4 Composability and library-friendliness: communicators and contexts

**This is the most transferable idea in MPI and deserves to be the centerpiece of the AgentMPI
analogy.**

An MPI communicator binds together a *process group* (an ordered set of participants, giving each a
local rank) and a *communication context* (an opaque isolation domain). Two properties do the work:

1. **All addressing is relative to the communicator.** Ranks are indices within the group, not global
   identifiers. The IBM implementers' comparison with EUI states the significance exactly: in EUI
   "the `dest` parameter is an absolute index"; in MPI it is "the relative index of the destination
   within an ordered group of processes ... This mechanism provides important support for modular
   development of large codes and libraries: a module running on a subset of processes can use a local
   name space for its communication" [franke1994mpisp].
2. **The context is not a visible value.** Gropp and Lusk list this as a direct consequence of the
   modularity goal: "process source/destination must be specified by rank in a group rather than by an
   absolute identifier, and **context must not be a visible value**" [gropp2002goals]. Because the
   user cannot name, guess, or forge a context, there is no way for a library's messages to be
   intercepted by application code — even code using `MPI_ANY_TAG` and `MPI_ANY_SOURCE`. Tag
   hygiene conventions ("libraries use tags above 1000") are unnecessary because isolation is
   structural rather than conventional.

Zipcode had prototyped this as *mailers* [skjellum1994zipcode], Feitelson had proposed
object-based multiparty interactions under the name "communicators"
[feitelson1991communicators], and Skjellum, Doss and Bangalore had made the library-writing case in
1993 while MPI-1 was in draft [skjellum1993writinglibraries]. Skjellum, Clarke, Snir, Littlefield and
Sears owned the corresponding chapter [mpiforum1995mpi11].

Gropp lists composability among his six reasons for MPI's success and identifies communicators as the
mechanism [gropp2001learning]; twenty years later he still describes MPI's most important property as
being "designed to support 'programming in the large' — creation of libraries and tools"
[gropp2019llnl].

Hoefler and Snir codify the resulting practice for library authors [hoefler2011libraries]:

- A library should **duplicate a communicator** (`MPI_Comm_dup`) it is handed and use the private
  duplicate internally, never the caller's communicator and never `MPI_COMM_WORLD`.
- A library should never *assume* `MPI_COMM_WORLD`: it should accept a communicator as a parameter, so
  it can be instantiated on a subset of processes or several times concurrently on disjoint subsets.
- **Attribute caching** on communicators lets a library attach its own per-communicator state (and a
  copy/delete callback) without a side table keyed by communicator, so state lifetime tracks
  communicator lifetime automatically.
- Communicator creation is itself a cost, which motivated later work on non-collective communicator
  creation [dinan2011noncollective] and hierarchical/hardware-aware communicators
  [goglin2018hierarchical].

There is a real cost: for most of MPI's history communicator creation was collective over the parent
communicator, which makes dynamic, fine-grained scoping expensive. MPI-4.0's Sessions
[holmes2016sessions] and non-collective creation work [dinan2011noncollective] are both responses.
An AgentMPI designed today can make scoped, cheaply-createable channels the default rather than a
retrofit.

### 5.5 Explicit locality; no hidden data movement

MPI's model is that "data is moved from the address space of one process to that of another process
**through cooperative operations on each process**" [mpiforum2025mpi50, §2.1]. There is no global
address space in MPI-1, no compiler-inserted communication, and no runtime that migrates data behind
the programmer's back. Every byte that crosses a process boundary does so because the programmer
wrote a call that moved it.

This was the deliberate antithesis of the HPF bet. HPF put "the large data structures of applications
... part of a global name space that can be laid out across the memories of a distributed-memory
machine," with the compiler and runtime responsible for "explicit generation of communication"
[kennedy2007hpf]. HPF programs were shorter and clearer; the trouble was that "for some data-parallel
applications it was difficult to achieve the all-important goal of high target-code performance," and
"frustrated users, particularly in the U.S., switched to MPI" [kennedy2007hpf].

The debugging consequence is as important as the performance consequence. Because every HPF compiler
"translated the HPF source to Fortran plus MPI," with "a large number of transformations ... making the
relationship between the original source program and the corresponding target program less than obvious
to the programmer," it became "very difficult to identify and correct performance problems." Worse,
even when tooling identified the bottleneck, "the user might well understand what was causing the
performance problem but have no idea how to change the HPF source to address it"
[kennedy2007hpf]. Explicit locality is not only about achieving performance; it is about maintaining a
**tractable mapping from source text to observed behavior**. Gropp's summary: MPI achieves performance
"in part by not getting in the way of locality management," at the cost that "user has no choice but to
manage locality, which is both hard and tricky" [gropp2022exampi].

### 5.6 Small orthogonal core, large convenience surface

**MPI-1 contains 128 routines**, on the authority of the Forum members' own reference book: "One aspect
of concern, particularly to novices, is the large number of routines comprising the MPI specification.
In all there are 128 MPI routines" [snir1996completeref]. MPI-3.1 grew that to **444 functions**
[laguna2019study] (the same paper says "roughly 443 distinct routines" elsewhere; treat the figure as
~444). Against that, Gropp's standard tutorial states: "Many parallel programs can be written using
just these six functions, only two of which are non-trivial: `MPI_INIT`, `MPI_FINALIZE`,
`MPI_COMM_SIZE`, `MPI_COMM_RANK`, `MPI_SEND`, `MPI_RECV`" — and a second six-function set
(substituting `MPI_BCAST` and `MPI_REDUCE`) suffices for collective-style programs
[gropp2004tutorial].

*MPI: The Complete Reference* answers the "why so big?" question with two reasons and one explicit
design trade-off [snir1996completeref]. The reasons: "MPI was designed to be rich in functionality" —
derived datatypes, modular communication via communicators, caching, topologies, a full collective
set — and the size "reflects the diversity and complexity of today's high performance computers,"
especially for point-to-point, where "the different communication modes ... arise mainly as a means of
providing a set of the most widely-used communication protocols." The trade-off is stated outright:

> "One could decrease the number of functions by increasing the number of parameters in each call. But
> such approach would increase the call overhead and would make the use of the most prevalent calls
> more complex. **The availability of a large number of calls to deal with more esoteric features of MPI
> allows one to provide a simpler interface to the more frequently used functions.**"
> [snir1996completeref]

That is the onion-skin principle in one sentence: *many narrow entry points keep the common ones
narrow.* The opposite design — few functions with many options each — pushes complexity into every
call site including the trivial ones.

The empirical evidence that this layering worked is strong. Laguna et al.'s static analysis of 110
open-source MPI programs finds [laguna2019study]:

- "A large portion of programs (42%) rely on the features in MPI version 1.0 only," and "for most
  programs (about 80%), the minimum version they require is MPI version 2.0."
- "The majority of MPI programs use only a small set of features from the MPI Standard — a considerable
  number of applications use only the point-to-point and collective communication features of the
  standard, leaving other parts of the standard totally unused."
- "Applications below 100,000 lines of code rarely use more than forty functions," while NAMD — treated
  as an outlier — "uses 314 of the 444 MPI functions present in MPI 3.1."
- "Few applications use less than nine MPI functions, which suggests a minimum requirement for a
  functional usage of MPI."

Walker's original workshop report had described the intended shape as an **"onion skin" model**: a
small efficient core with more convenient layers wrapped around it [walker1992standards]. The
"128 functions but you can write real programs with 6" observation is not an accident or an
embarrassment; it is the onion-skin design working as intended, with a heavy tail of specialists' tools
that most users correctly ignore.

Laguna et al. do draw a critical conclusion from the same data: the low uptake of advanced features
"raises questions about the value of the efforts and costs in standardizing minor features (perhaps
'syntactic sugar' features) that are ultimately not widely adopted by users," and they note that
"features provided in subversions of the standard (i.e., 1.3, 2.1, 2.2, and 3.1) are practically of
little value to applications since they are rarely used" [laguna2019study].

### 5.7 Thin semantics: opaque buffers plus datatypes

MPI never interprets the *meaning* of a payload. A message is described by a (buffer, count, datatype)
triple, where the datatype describes only *layout and primitive element type* — enough to do byte-order
and word-length conversion in a heterogeneous environment, and enough to gather/scatter
non-contiguous memory without an intermediate copy. There is no schema registry, no global ontology, no
notion of a message "kind" beyond an integer tag whose meaning is entirely the application's business.

MPI also uses *opaque objects with handles* throughout — communicators, groups, datatypes, requests,
windows, files, info objects, error handlers. Gropp and Lusk explain the choice as a consequence of the
extensibility goal: "This requirement led to an object-oriented approach without a commitment to an
object-oriented language. This approach required functions to manipulate the objects, and was one minor
reason for the relatively large number of functions in MPI" [gropp2002goals]. Opacity is what let MPI
change datatype internals, add window flavors, and eventually specify a binary ABI without breaking
source compatibility.

The economy of the datatype concept is the exemplar of the "each concept solves several problems" rule:
"datatypes solve both heterogeneity and noncontiguous data layouts, **both for messages and for
files**" [gropp2002goals], and derived datatypes were subsequently shown to be the right vehicle for
I/O performance too [thakur1998datatypes].

### 5.8 Nondeterminism control

MPI is not a deterministic model, but it constrains nondeterminism precisely and in a way that is
*checkable by the programmer*.

**The non-overtaking rule.** MPI guarantees that messages are non-overtaking: if a sender posts two
sends to the same destination and both could match the same receive, the receive matches the first;
if a receiver posts two receives and both could match the same message, the first posted receive
matches it. The standard's order guarantee is scoped: it holds for messages sent on the same
communicator by the same sender, and the standard is explicit that "if a process has a single thread of
execution, then any two communications executed by this process are ordered," while "if the process is
multithreaded, then the semantics of thread execution may not define a relative order between two send
operations executed by two distinct threads. The operations are logically concurrent, even if one
physically precedes the other" [mpiforum2015mpi31, §3.5]. Thus MPI provides pairwise FIFO ordering per
(sender, receiver, communicator) and *nothing stronger* — no global total order, no causal ordering
across senders.

**Wildcards as opt-in nondeterminism.** `MPI_ANY_SOURCE` and `MPI_ANY_TAG` let a receiver accept
whatever arrives first. This is the only source of receive-side nondeterminism in a single-threaded MPI
program, and it is syntactically visible: a program that never writes `MPI_ANY_SOURCE` has a
deterministic message-matching order. MPI-4.0 made this promise machine-readable via info assertions
such as `mpi_assert_no_any_tag` [mpiforum2025mpi50, §2.8]. Note the asymmetry: nondeterminism is
**opt-in at the point of use** and **locally auditable**.

**The reduction reproducibility debate.** Floating-point addition is not associative, so
`MPI_Reduce` results can differ between runs if the implementation varies the reduction tree — and can
differ between different numbers of processes. The standard does not require bitwise reproducibility;
it advises implementers to use a fixed, deterministic order for a given communicator size and
placement so that repeated runs agree, while acknowledging that results may legitimately differ across
process counts and that users needing exact reproducibility must arrange it themselves
[mpiforum2009mpi22, §5.9.1]. This is a candid example of the Forum choosing **performance freedom over
strict determinism**, documenting the choice rather than hiding it, and pushing the stronger guarantee
into "advice to implementors" — the third normative tier again.

**Progress and completion.** MPI's progress semantics are deliberately weak: a non-blocking operation
is not guaranteed to advance without further MPI calls, and the standard permits implementations that
make progress only inside MPI calls [mpiforum2015mpi31, §3.7.4]. Whether to progress messages in a
separate thread was studied and found to be a genuine trade-off, not a clear win
[hoefler2008threadornot]. Weak progress semantics is what allows a single-threaded, poll-driven
implementation to be conformant.

### 5.9 Deliberate omissions and their justifications

MPI-1.5 lists what the standard does *not* specify [mpiforum1995mpi11, §1.5]:

> "• Explicit shared-memory operations
> • Operations that require more operating system support than is currently standard; for example,
> interrupt-driven receives, remote execution, or active messages
> • Program construction tools • Debugging facilities • Explicit support for threads • Support for task
> management • I/O functions"

Item by item, with reasons:

**Fault tolerance.** Never in the omissions list, because it was excluded by *assumption*, in the goals:
"Assume a reliable communication interface: the user need not cope with communication failures. Such
failures are dealt with by the underlying communication subsystem" [mpiforum2025mpi50, §2.1]. This is
the single most criticized MPI decision and the most instructive. It bought enormous simplification —
no send needs a failure return path, no collective needs an agreement protocol, no communicator needs a
membership-change protocol — at the cost of making MPI structurally unable to survive process loss.
Attempts to add it (FT-MPI [gabriel2004openmpi], HARNESS, later ULFM) have never made it into the
standard, and MPI-4.0 delivered only "improvements to the definitions of error handling"
[mpiforum2025mpi50, §2.8], not recovery. The ECP survey found fault tolerance to be a recurring
concern among exascale projects [bernholdt2020ecp]. **For AgentMPI this omission is a warning rather
than a model**: LLM-agent harnesses experience the analogue of process failure (timeouts, refusals,
malformed output, rate limits) on nearly every call, so the reliability assumption cannot simply be
imported.

**Active messages and interrupt-driven receive.** Excluded under "operations that require more
operating system support than is currently standard" [mpiforum1995mpi11, §1.5]. This preserved goal 6
— implementability "with no significant changes in the underlying communication and system software"
[mpiforum2025mpi50, §2.1]. It also preserved a valuable property: in MPI, *the receiver decides when to
receive*. No peer can cause code to run in your address space. Message arrival never preempts you.

**Debugging facilities.** Excluded from the standard, but *not* ignored: MPI-1 standardized the PMPI
profiling interface in v1.0 [mpiforum1995mpi11, §1.4], MPI-3.0 added `MPI_T` for introspection into
implementation internals [mpiforum2012mpi30, schulz2018mug], and the debugger/message-queue interfaces
were developed as side documents outside the standard [mpiforumdocs, cownie1999debugger]. The pattern
is: standardize the *observation seam*, not the tool.

**I/O.** Excluded in MPI-1 as insufficiently understood; added in MPI-2.0 as MPI-IO once collective and
two-phase I/O research had matured [mpiforum1997mpi2, delrosario1993twophase, thakur1996extended,
kotz1994diskdirected]. A textbook case of deferral followed by informed adoption — and when it was
added, it reused derived datatypes rather than inventing a parallel description mechanism
[gropp2002goals, thakur1998datatypes].

**Explicit shared memory.** Excluded in MPI-1 to keep the model uniform. Reintroduced carefully in
MPI-3.0 as *windows* — `MPI_Comm_split_type(MPI_COMM_TYPE_SHARED)` plus `MPI_Win_allocate_shared` — so
that shared memory entered as an explicitly-scoped resource rather than as an implicit global property
[hoefler2014advancedmpi, hoefler2010hybrid].

**Threads.** MPI-1 provided no explicit thread support but was careful that "the interface has been
designed so as not to prejudice their use" [mpiforum1995mpi11, §1.3], and thread safety was a stated
design goal [mpiforum2025mpi50, §2.1]. Gropp and Lusk spell out the concrete API consequence: "Thread
safety implies that there can be no notion of a 'current' buffer, message, error code, and so on"
[gropp2002goals] — no hidden per-process mutable state anywhere in the interface. MPI-2.0 then added
negotiated thread levels rather than a mandate. MPI-3.0's matched probe closed a residual race
[gregor2009probe]; MPI-4.0's partitioned communication addressed multithreaded send throughput
[grant2019finepoints]. `MPI_THREAD_MULTIPLE` nonetheless remains a known performance liability due to
contention on internal MPI state, motivating endpoint proposals [zambre2018endpoints,
dinan2013endpoints].

**Task management / scheduling / load balancing.** Excluded [mpiforum1995mpi11, §1.5]. MPI is a
communication library; it does not decide what work runs where. The reasoning is developed at length
in the PVM comparison [gropp2002goals]. PVM offered a virtual machine with `pvm_config` for resource
inquiry; MPI declined, because "the information that any command can provide on the environment is
immediately out of date" [gropp2002goals]. And when MPI-2 needed to express resource requirements for
spawning, the Forum enumerated three options — "(a) pick a small subset that all systems can support,
(b) define a general and generic, but fully expressive, system, or (c) provide an interface that allows
information to be passed, in an implementation-specific manner, to the resource system" — noted that
PVM chose (a), that "(b) has two drawbacks — it isn't extensible, and it assumes that there is a
well-defined interface that users agree on," and that these drawbacks "led the MPI Forum, which spent a
great deal of time trying to find a solution like (b), to choose (c)" [gropp2002goals]. The result is
`MPI_Info`: an open key/value channel with a few standard keys, where **"MPI implementations are
required to ignore unrecognized fields; this strategy encourages users to provide extra information
when possible"** [gropp2002goals]. `MPI_Info` was then reused for I/O hints and, later, for
application assertions — the same "one concept, many jobs" economy [gropp2002goals,
mpiforum2025mpi50, §2.8]. **This is an extremely good pattern for AgentMPI**: a typed core plus an
extensible, must-ignore-unknown-keys metadata channel gives you an escape hatch that cannot fragment
the core.

**No mandated daemons.** "MPI does not mandate or define a virtual machine, even in MPI-2 ... But we
emphasize that daemons are not required by the MPI specification. This feature is important for
extreme-scale architectures, where the very existence of local daemons may be impractical"
[gropp2002goals]. MPI specified no runtime architecture at all — which is precisely why the same
standard runs over TCP on a laptop and over a proprietary switch on a leadership-class machine.

---

## 6. Retrospectives: why MPI succeeded

### 6.1 Gropp's six requirements (HiPC 2001)

Gropp's keynote argues MPI "has succeeded because it addresses *all* of the important issues in
providing a parallel programming model" and enumerates six [gropp2001learning], which he restated
essentially unchanged twenty-one years later [gropp2022exampi]:

1. **Portability** — the same source runs everywhere.
2. **Performance** — portability without a performance penalty relative to native interfaces.
3. **Simplicity and symmetry** — few concepts, applied uniformly; operations come in regular families
   rather than special cases.
4. **Modularity** — programs can be decomposed into parts with private communication.
5. **Composability** — the parts can be combined without interfering, via communicators.
6. **Completeness** — the model can express what applications need without dropping to a lower level.

The structure of the argument is what matters: it is a claim about *conjunction*. Competitors each
satisfied a subset. PVM had portability and simplicity but not native-level performance
[snir1995sp2, gropp2002goals]; HPF had simplicity and (in principle) portability but not performance
[kennedy2007hpf]; vendor libraries had performance but not portability [snir1995sp2]. MPI's claim is
that a programming model is only adopted when *none* of the six is badly missing. Gropp adds a
provocative note about the level of abstraction, paraphrasing Snir: MPI "is neither high nor low"
level, and yet "is that part of MPI's success — it does both high and low level, and the tradeoff in
greater use (mostly) makes up for loss of performance/function" [gropp2022exampi].

### 6.2 Why MPI won over PVM

Gropp and Lusk's central thesis is that PVM and MPI "often are solving different problems," and that
where they overlap, differences "can be traced to explicit differences in the goals of the two
systems, their origins, or the relationship between their specifications and their implementations"
[gropp2002goals]. The specific arguments:

1. **Library vs. distributed operating system.** MPI's first stated goal was to be "a library for
   writing application programs, not a distributed operating system" [gropp2002goals]. PVM's daemons
   provided "a simple yet useful distributed operating system" [gropp2002goals]. Being a library meant
   MPI could be implemented natively on any machine, including ones where daemons were impractical.
2. **No mandated copies.** PVM's socket/IP-oriented design "requires data copying operations that can
   be avoided with a library more directly targeted to an SP environment" [snir1995sp2]; MPI mandated
   no memory copies [gropp2002goals]. This is why IBM built EUI/MPL rather than adopting PVM, and why
   vendors could deliver near-hardware MPI performance.
3. **Collectives from the start.** PVM version 2 "was missing support for important functions, such as
   collective communication" [snir1995sp2]. MPI had collectives in 1.0 with a dedicated subcommittee
   [mpiforum1995mpi11].
4. **Contexts and modularity.** PVM's identifiers were absolute task ids; MPI's are ranks within a
   group plus an invisible context [gropp2002goals, franke1994mpisp]. PVM later added contexts and
   static groups [gropp2002goals, dongarra1995pvmcontext] — convergence toward MPI's design.
5. **Thread safety by construction.** MPI's specification banned any notion of "current" state
   [gropp2002goals]; retrofitting thread safety onto PVM was harder, and TPVM was "more a lightweight
   process model than a fully threaded model" [gropp2002goals, ferrari1995tpvm].
6. **Vendor buy-in as a design input.** MPI was "designed by the MPI Forum ... with the expectation
   that all of the participating vendors would implement it" [gropp2002goals]. PVM was one group's
   design; MPI was a negotiated commitment from the people who had to ship it.
7. **Where PVM was better, MPI conceded.** PVM placed "greater emphasis on providing a distributed
   computing environment and on handling communication failures" [gropp2002goals] — genuinely better
   for loosely-coupled, failure-prone, heterogeneous networks. MPI's dominance is domain-specific, not
   universal. *This caveat matters for AgentMPI, whose deployment environment resembles PVM's more than
   MPI's.*

### 6.3 Why MPI won over HPF

Kennedy, Koelbel and Zima's own post-mortem is unusually frank [kennedy2007hpf, kennedy2011hpfcacm].
HPF began in 1991 at Supercomputing in Albuquerque, one year before Williamsburg; its 1.0 language
definition was published as a handbook in 1993 [koelbel1993hpf], and at its peak "17 vendors offered
HPF products and more than 35 major applications were written in HPF, at least one with more than
100,000 lines of code" [kennedy2007hpf]. It nonetheless lost. Their stated reasons:

1. **Performance shortfall on real applications.** "HPF's expressive power led to early success, but
   program performance could not always match that of MPI-based programs, resulting in failure to win
   over a broad user community" [kennedy2007hpf]. HPF "did best on simple, regular problems (such as
   dense linear algebra and partial differential equations on regular meshes)" but struggled elsewhere
   [kennedy2007hpf].
2. **Inadequate data distributions.** "The data distributions provided by HPF could not adequately
   support large classes of important applications. No particular set of built-in distributions can
   satisfy all these requirements" [kennedy2007hpf]. A fixed menu of high-level abstractions cannot
   cover an open-ended application space; what was needed was "a general mechanism for generating
   programmer-defined distributions" [kennedy2007hpf].
3. **Immature compiler technology.** HPF was defined atop the brand-new Fortran 90, so "building a
   Fortran 90 compiler meant a substantial implementation effort ... a huge obstacle on the way to
   HPF," and HPF's own features "demanded new compilation strategies that in 1993 ... had been
   implemented only in research compilers and the CM Fortran compiler" [kennedy2007hpf]. The standard
   outran the implementations. MPI, by contrast, was *easier* to implement than the vendor libraries it
   replaced in some respects, and a working portable implementation existed at release.
4. **No reference implementation.** "The HPF Library could have been used to address some of the
   usability and performance problems ... but there was no open-source reference implementation for the
   library, so each compiler project had to implement its own version ... The end result was that the
   implementations were inconsistent and often exhibited poor performance; **users were again forced to
   code differently for different target machines**" [kennedy2007hpf]. The standard failed at the very
   thing it existed to provide.
5. **Untraceable performance.** Every HPF compiler "translated the HPF source to Fortran plus MPI,"
   after "a large number of transformations ... making the relationship between the original source
   program and the corresponding target program less than obvious to the programmer," so it was "very
   difficult to identify and correct performance problems" — and even when identified, the user "might
   well understand what was causing the performance problem but have no idea how to change the HPF
   source to address it" [kennedy2007hpf].
6. **Feature-set fragmentation.** HPF 2.0 defined "approved extensions" that vendors could implement
   selectively; "not surprisingly, the result was confusion, as the feature sets offered by different
   vendors diverged" [kennedy2007hpf]. Optional feature tiers destroyed the portability guarantee.
7. **MPICH's existence.** Their conclusion names it directly: HPF's performance shortfall "became
   apparent, particularly after MPICH ... a portable, efficient reference implementation of MPI, became
   available" [kennedy2007hpf].

The HPF authors also note the paths not taken: HPF's `EXTRINSIC` interface let users "call subroutines
written in MPI in a way that made it possible to recode HPF subprograms for more efficiency"
[kennedy2007hpf] — i.e. HPF's own escape hatch pointed at MPI, and users walked through it. And they
observe that MPI's cost was tolerable to the Fortran/C community but that "it seems unlikely that the
large group of programmers of high-level scripting languages (such as Matlab, Python, and R) would be
willing to do the same. Simplicity is part of the reason for the popularity of these languages"
[kennedy2007hpf]. That observation applies with full force to a protocol aimed at LLM-agent
developers.

### 6.4 Snir's retrospective, MPI+X, and the "assembly language" critique

Snir's 2018 CACM technical perspective revisits MPI 25 years on, in the context of MPI's difficulties
with very high thread counts and accelerators [snir2018future]. `[UNVERIFIED]` I could not retrieve the
full text (ACM blocked automated access), so I am not quoting specific figures from it; the frequently
cited comparison of MPI-1.1's page/function count against MPI-3.1's should be attributed to a source
you can check directly. What *is* verifiable about the growth: the MPI-2.1 book is 608 pages, MPI-2.2
647 pages, MPI-3.0 852 pages, MPI-3.1 868 pages [mpiforumdocs], and MPI-3.1 defines 444 functions
[laguna2019study].

Gropp's EuroMPI 2012 keynote gives the balanced version. MPI succeeded through "a combination of
forward-looking features, precise definition, and judgment based on the experience of developers,
vendors and users," but "faces many challenges as the nature of parallel computing changes more
radically than at any time in the history of MPI" [gropp2012mpi3beyond]. By 2022 he was more pointed:
MPI "avoided being tied too closely to HW at a moment in time" and "benefitted from stability in
architecture," but "that era of stability has ended," so it is "time to identify and rethink
assumptions" — including "rethink building blocks" and "consider streams, notification, subsets"
[gropp2022exampi].

**"MPI as the assembly language of parallel computing."** This characterization is widely attributed
and Gropp's 2001 paper engages with the framing of message passing as difficult and low-level
[gropp2001learning]. `[UNVERIFIED]` I did not find a definitive first attribution of the exact phrase,
so I recommend citing the *substance* — Snir's remark, via Gropp, that MPI "is neither high nor low"
level [gropp2022exampi], and Gropp's observation that MPI's performance comes at the cost of forcing
the user to manage locality [gropp2022exampi] — rather than the slogan.

**Popular-press exposition.** Jeff Squyres — an Open MPI co-founder [gabriel2004openmpi] and LAM/MPI
contributor [squyres2003lam] — wrote the *MPI Mechanic* column in *ClusterWorld* magazine and its
successor *MPI Monkey* on Cluster Monkey, which are the main practitioner-facing exposition of MPI
concepts from an implementer's viewpoint (including columns on why so many MPI implementations exist
and on when dynamic process spawning is worthwhile) [squyres_mpimechanic]. These are magazine columns
rather than peer-reviewed sources and should be cited for framing rather than for facts.

**MPI+X.** The dominant practice is MPI between nodes and something else within a node. Gropp: "MPI
remains the most viable internode programming system," supporting "multiple parallel programming
models, including one-sided and shared memory," and containing "features for 'programming in the
large' (tools, libraries, frameworks) that make it particularly appropriate for the internode
programming system," while the intranode gap is where the productivity and performance problems live
[gropp2019llnl]. The technical friction is concrete: `MPI_THREAD_MULTIPLE` serializes on internal MPI
state, motivating endpoints [zambre2018endpoints, dinan2013endpoints], and partitioned communication in
MPI-4.0 [grant2019finepoints]. Empirically, Laguna et al. find "74.5% of the programs use some form of
hybrid code (i.e., MPI+X)," with OpenMP "found in nearly 3/4 of the sampled applications and 42.7%
exclusively" [laguna2019study], and Bernholdt et al. report that MPI+OpenMP integration is essentially
universal among ECP projects [bernholdt2020ecp].

### 6.5 Usage studies: what people actually use

**Laguna et al., SC19** [laguna2019study] — static analysis of 110 open-source MPI programs, 1,400 to
7 million lines:

- 42% need only MPI-1.0 features; ~80% need only MPI-2.0 features; sub-version features (1.3, 2.1,
  2.2, 3.1) are "practically of little value to applications since they are rarely used."
- "A large portion of applications (67%) use blocking send and received operations," and while
  non-blocking point-to-point calls are the most common, "point-to-point (blocking and non-blocking)
  routines are more prominently used than persistent point-to-point or one-sided routines" — features
  that "could potentially improve performance."
- `MPI_Allreduce` is the most used collective.
- "C++ is the dominant programming language in MPI programs" — 44% of analyzed lines are C++ vs 19%
  Fortran and 16% C, with only 2.7% of applications Fortran-only — and since no C++ binding exists
  after MPI-2.1, "usage from C++ is most likely through the C interface."
- Larger applications use more distinct MPI functions; NAMD is the outlier at 314 of 444.

**Bernholdt et al., ECP survey** [bernholdt2020ecp] — 77 responses from 97 ECP projects, 56 using MPI:
MPI is often consumed *through* libraries and abstraction layers rather than called directly by
application code; RMA, neighborhood collectives and process topologies attract interest for exascale;
MPI+OpenMP is ubiquitous; fault tolerance and scalable initialization recur as concerns.

The joint message is uncomfortable but important: **the standard's long tail is mostly unused, and the
features experts believe are best are not the features practitioners adopt.** Simple, obvious,
well-documented primitives win on adoption even when better ones exist. A protocol designer should
expect the same and design the core accordingly.

---

## 7. Adoption mechanics: the role of reference implementations

### 7.1 MPICH

MPICH is the decisive artifact in MPI's adoption story. Gropp, Lusk, Doss and Skjellum describe it as
"unique among existing implementations in its design goal of combining portability with high
performance" [gropp1996mpich]. Three facts about its timing and architecture matter:

- **It existed before the standard did.** At the MPI Forum's organizational meeting at Supercomputing
  '92, "Gropp and Lusk volunteered to develop an immediate implementation that would track the Standard
  definition as it evolved," in order "to quickly expose problems that the specification might pose for
  implementors and to provide early experimenters with an opportunity to try ideas being proposed for
  MPI before they became fixed" — and the first version "implemented the prespecification ... within a
  few days," a speed "due to the existing portable systems p4 and Chameleon"
  [gropp1996mpich, gropp1992testimpl]. The standard was validated by implementation *during* drafting,
  and prior portability layers made the first implementation nearly free.
- **It reused predecessor code, not just predecessor ideas.** "Algorithms for the collective operations
  and topologies, together with code for attribute management, were borrowed from Zipcode and tuned as
  the months went by" [gropp1996mpich]. The reference implementation inherited working code from the
  research systems whose designs the standard had absorbed.
- **It was free, complete, and available at release time.** The contrast with HPF, whose vendors
  "naturally waited until" the standard settled [gropp1996mpich] and which never got a reference
  library implementation [kennedy2007hpf], is the causal claim the HPF authors themselves make
  [kennedy2007hpf].
- **Its architecture made porting cheap.** MPICH is layered over an **Abstract Device Interface (ADI)**
  and, below that, a **Channel Interface**, so a vendor could get a working MPI by implementing a small
  number of primitives and then incrementally specialize upward for performance
  [gropp1996mpich]. This is Chameleon's idea [gropp1993chameleon] turned into an adoption strategy: the
  *cost of the first conformant implementation* was engineered to be low. MPICH also shipped with
  tools, "which constitute the beginnings of a portable parallel programming environment"
  [gropp1996mpich], and the `ch_p4` device carried it onto workstation clusters [gropp2002goals].

MPICH's own retrospective frames the project as translational research — moving results into
production software — over three decades [balaji2020mpich].

### 7.2 LAM/MPI

LAM began as "an open cluster environment for MPI" [burns1994lam] and grew a component architecture
[squyres2003lam] that prefigured Open MPI's. LAM is one of the two implementations Gropp and Lusk cite
as demonstrating that MPI's heterogeneity and MIMD support were real and not theoretical
[gropp2002goals]. It provided the second independent implementation that a standard needs in order to
be a standard rather than a description of one program.

### 7.3 Open MPI (2004)

Open MPI was formed by consolidating four projects. Its EuroPVM/MPI 2004 paper states the problem it
was created to solve: "A large number of MPI implementations are currently available, each of which
emphasize different aspects of high-performance computing or are intended to solve a specific research
problem. The result is a myriad of incompatible MPI implementations, all of which require separate
installation, and the combination of which present significant logistical challenges for end users"
[gabriel2004openmpi]. It was "influenced by experience gained from the code bases of the LAM/MPI,
LA-MPI, and FT-MPI projects" and is "an all-new, production-quality MPI-2 implementation that is
fundamentally centered around component concepts" [gabriel2004openmpi]. (PACX-MPI is generally counted
as the fourth ancestor; the abstract names three, and the author list spans Tennessee, Indiana and Los
Alamos.) Its **Modular Component Architecture** "provides both a stable platform for third-party
research as well as enabling the run-time composition of independent software add-ons"
[gabriel2004openmpi].

The pattern is worth noting: after a decade, the *implementation* ecosystem consolidated even though
the *standard* did not change. Fragmentation was resolved by merging code bases and making the
extension points explicit — an MCA plugin per network, per collective algorithm, per process launcher.

### 7.4 MVAPICH and vendor derivatives

MVAPICH began in 2001–2002 to exploit InfiniBand, built on MPICH, and its RDMA-based design
[liu2003rdma] became the template for high-performance MPI over InfiniBand; the project reports very
large cumulative download and deployment numbers [mvapich2024roadmap, mvapichweb].

Vendor MPIs are almost all derivatives rather than independent implementations: Intel MPI and MVAPICH
derive from MPICH, Cray MPT derives from MPICH, and IBM Spectrum MPI derives from Open MPI
[chpc2024mpilibs, intelmpi, ibmspectrummpi]. The ecosystem is therefore effectively two upstream code
bases plus vendor specializations — which is exactly what MPICH's ADI and Open MPI's MCA were designed
to enable. It is also precisely the situation that made a binary ABI both necessary and feasible in
MPI-5.0: with two source lineages and many binary-incompatible builds, every downstream package needed
per-MPI rebuilds until MPI-5.0 mandated `libmpi_abi` [hammond2023abi, mpiforum2025mpi50, §21.2.1].

### 7.5 The general lesson

A standard is adopted when the marginal cost of conforming is lower than the marginal cost of not
conforming, for *every* party:

- **Users** got a free, complete, portable implementation on day one [gropp1996mpich], so writing to
  MPI was never a bet on future availability.
- **Vendors** got a small, well-defined primitive set worth hardware acceleration
  [mpiforum1995mpi11, §1.1] plus a layered open-source base (ADI/MCA) they could specialize instead of
  starting from scratch [gropp1996mpich, gabriel2004openmpi].
- **Library authors** got context-based isolation, so they could ship MPI-using libraries that compose
  safely with unknown application code [skjellum1993writinglibraries, hoefler2011libraries].
- **Tool builders** got PMPI in v1.0 and `MPI_T` in v3.0 [mpiforum1995mpi11, §1.4;
  mpiforum2012mpi30].

HPF failed the first of these and, through optional extension tiers, undermined the portability
promise itself [kennedy2007hpf].

---

---

## BibTeX

```bibtex
@article{sunderam1990pvm,
  author  = {V. S. Sunderam},
  title   = {{PVM}: A framework for parallel distributed computing},
  journal = {Concurrency: Practice and Experience},
  volume  = {2},
  number  = {4},
  pages   = {315--339},
  year    = {1990},
  month   = dec,
  doi     = {10.1002/cpe.4330020404}
}

@book{geist1994pvm,
  author    = {Al Geist and Adam Beguelin and Jack Dongarra and Weicheng Jiang and
               Robert Manchek and Vaidy Sunderam},
  title     = {{PVM}: Parallel Virtual Machine---A Users' Guide and Tutorial for
               Networked Parallel Computing},
  publisher = {MIT Press},
  address   = {Cambridge, MA},
  series    = {Scientific and Engineering Computation},
  year      = {1994},
  isbn      = {9780262571081}
}

@article{butler1994p4,
  author  = {Ralph M. Butler and Ewing L. Lusk},
  title   = {Monitors, messages, and clusters: The p4 parallel programming system},
  journal = {Parallel Computing},
  volume  = {20},
  number  = {4},
  pages   = {547--564},
  year    = {1994},
  month   = apr,
  doi     = {10.1016/0167-8191(94)90028-0}
}

@techreport{gropp1993chameleon,
  author      = {William D. Gropp and Barry F. Smith},
  title       = {Chameleon Parallel Programming Tools Users Manual},
  institution = {Argonne National Laboratory},
  number      = {ANL-93/23},
  year        = {1993},
  month       = mar
}

@inproceedings{skjellum1990zipcode,
  author    = {Anthony Skjellum and Alvin P. Leung},
  title     = {Zipcode: A Portable Multicomputer Communication Library atop the
               Reactive Kernel},
  booktitle = {Proceedings of the Fifth Distributed Memory Concurrent Computing
               Conference},
  editor    = {David W. Walker and Quentin F. Stout},
  pages     = {767--776},
  publisher = {IEEE Press},
  year      = {1990}
}

@article{skjellum1994zipcode,
  author  = {Anthony Skjellum and Steven G. Smith and Nathan E. Doss and
             Alvin P. Leung and Manfred Morari},
  title   = {The design and evolution of {Zipcode}},
  journal = {Parallel Computing},
  volume  = {20},
  number  = {4},
  pages   = {565--596},
  year    = {1994},
  month   = apr,
  note    = {Special issue on message passing}
}

@inproceedings{skjellum1993writinglibraries,
  author    = {Anthony Skjellum and Nathan E. Doss and Purushotham V. Bangalore},
  title     = {Writing Libraries in {MPI}},
  booktitle = {Proceedings of the Scalable Parallel Libraries Conference},
  editor    = {Anthony Skjellum and Donna S. Reese},
  pages     = {166--173},
  publisher = {IEEE Computer Society Press},
  year      = {1993},
  month     = oct
}

@article{calkin1994parmacs,
  author  = {Robin Calkin and Rolf Hempel and Hans-Christian Hoppe and Peter Wypior},
  title   = {Portable programming with the {PARMACS} message-passing library},
  journal = {Parallel Computing},
  volume  = {20},
  number  = {4},
  pages   = {615--632},
  year    = {1994},
  month   = apr
}

@article{bomans1990argonnegmd,
  author  = {Luc Bomans and Rolf Hempel},
  title   = {The {Argonne/GMD} macros in {FORTRAN} for portable parallel programming
             and their implementation on the {Intel iPSC/2}},
  journal = {Parallel Computing},
  volume  = {15},
  pages   = {119--132},
  year    = {1990}
}

@manual{parasoft1992express,
  title        = {Express User's Guide, version 3.2.5},
  organization = {Parasoft Corporation},
  address      = {Pasadena, CA},
  year         = {1992}
}

@article{harrison1991tcgmsg,
  author  = {Robert J. Harrison},
  title   = {Portable tools and applications for parallel computers},
  journal = {International Journal of Quantum Chemistry},
  volume  = {40},
  number  = {6},
  pages   = {847--863},
  year    = {1991},
  doi     = {10.1002/qua.560400612}
}

@article{carriero1989linda,
  author  = {Nicholas Carriero and David Gelernter},
  title   = {Linda in context},
  journal = {Communications of the ACM},
  volume  = {32},
  number  = {4},
  pages   = {444--458},
  year    = {1989},
  month   = apr,
  doi     = {10.1145/63334.63337}
}

@manual{epcc1992chimp,
  title        = {{CHIMP} Version 1.0 Interface},
  organization = {Edinburgh Parallel Computing Centre, University of Edinburgh},
  year         = {1992},
  month        = may
}

@techreport{geist1990picl,
  author      = {G. A. Geist and M. T. Heath and B. W. Peyton and P. H. Worley},
  title       = {{PICL}: A Portable Instrumented Communications Library,
                 C Reference Manual},
  institution = {Oak Ridge National Laboratory},
  number      = {ORNL/TM-11130},
  address     = {Oak Ridge, TN},
  year        = {1990},
  month       = jul
}

@techreport{feitelson1991communicators,
  author      = {Dror G. Feitelson},
  title       = {Communicators: Object-Based Multiparty Interactions for Parallel
                 Programming},
  institution = {Department of Computer Science, The Hebrew University of Jerusalem},
  number      = {91-12},
  year        = {1991},
  month       = nov
}

@inproceedings{pierce1988nx2,
  author    = {Paul Pierce},
  title     = {The {NX/2} Operating System},
  booktitle = {Proceedings of the Third Conference on Hypercube Concurrent Computers
               and Applications},
  pages     = {384--390},
  publisher = {ACM Press},
  year      = {1988}
}

@manual{ncube1990vertex,
  title        = {{nCUBE 2} Programmers Guide, r2.0},
  organization = {nCUBE Corporation},
  year         = {1990},
  month        = dec
}

@manual{tmc1993cmmd,
  title        = {{CMMD} User's Guide, Version 3.0},
  organization = {Thinking Machines Corporation},
  address      = {Cambridge, MA},
  year         = {1993}
}

@techreport{bala1992venus,
  author      = {Vasanth Bala and Shlomo Kipnis},
  title       = {Process Groups: A Mechanism for the Coordination of and
                 Communication among Processes in the {Venus} Collective
                 Communication Library},
  institution = {IBM T. J. Watson Research Center},
  year        = {1992},
  month       = oct,
  note        = {Preprint}
}

@techreport{bala1992collective,
  author      = {Vasanth Bala and Shlomo Kipnis and Larry Rudolph and Marc Snir},
  title       = {Designing Efficient, Scalable, and Portable Collective
                 Communication Libraries},
  institution = {IBM T. J. Watson Research Center},
  year        = {1992},
  month       = oct,
  note        = {Preprint}
}

@article{snir1995sp2,
  author  = {Marc Snir and Peter Hochschild and D. D. Frye and K. J. Gildea},
  title   = {The communication software and parallel environment of the {IBM SP2}},
  journal = {IBM Systems Journal},
  volume  = {34},
  number  = {2},
  pages   = {205--221},
  year    = {1995},
  doi     = {10.1147/sj.342.0205},
  url     = {https://bitsavers.trailing-edge.com/pdf/ibm/IBM_Systems_Journal/342/snir.pdf}
}

@article{gropp1995sp1,
  author  = {William D. Gropp and Ewing Lusk},
  title   = {Experiences with the {IBM SP1}},
  journal = {IBM Systems Journal},
  volume  = {34},
  number  = {2},
  pages   = {249--262},
  year    = {1995},
  doi     = {10.1147/sj.342.0249},
  url     = {https://bitsavers.trailing-edge.com/pdf/ibm/IBM_Systems_Journal/342/gropp.pdf}
}

@inproceedings{franke1994mpisp,
  author    = {Hubertus Franke and Peter Hochschild and Pratap Pattnaik and
               Marc Snir},
  title     = {{MPI} on {IBM SP1/SP2}: Current Status and Future Directions},
  booktitle = {Proceedings of the 1994 Scalable Parallel Libraries Conference},
  publisher = {IEEE Computer Society Press},
  year      = {1994},
  url       = {https://snir.cs.illinois.edu/listed/C43.pdf}
}

@techreport{walker1992standards,
  author      = {David W. Walker},
  title       = {Standards for Message Passing in a Distributed Memory Environment},
  institution = {Oak Ridge National Laboratory},
  number      = {ORNL/TM-12147},
  year        = {1992},
  month       = aug
}

@techreport{dongarra1993proposal,
  author      = {Jack J. Dongarra and Rolf Hempel and Anthony J. G. Hey and
                 David W. Walker},
  title       = {A Proposal for a User-Level, Message Passing Interface in a
                 Distributed Memory Environment},
  institution = {Oak Ridge National Laboratory},
  number      = {ORNL/TM-12231},
  year        = {1993},
  month       = feb
}

@article{mpiforum1994,
  author  = {{Message Passing Interface Forum}},
  title   = {{MPI}: A Message-Passing Interface Standard},
  journal = {The International Journal of Supercomputer Applications and High
             Performance Computing},
  volume  = {8},
  number  = {3--4},
  pages   = {165--414},
  year    = {1994},
  note    = {Special issue on MPI},
  url     = {https://journals.sagepub.com/toc/hpca/8/3-4}
}

@techreport{mpiforum1995mpi11,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard (Version 1.1)},
  institution = {University of Tennessee, Knoxville},
  year        = {1995},
  month       = jun,
  note        = {Document of June 12, 1995; supersedes the MPI-1.0 document of
                 May 5, 1994},
  url         = {https://www.mpi-forum.org/docs/mpi-1.1/mpi-11-html/mpi-report.html}
}

@techreport{mpiforum1997mpi2,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI-2}: Extensions to the Message-Passing Interface},
  institution = {University of Tennessee, Knoxville},
  year        = {1997},
  month       = jul,
  note        = {Document of July 18, 1997},
  url         = {https://www.mpi-forum.org/docs/mpi-2.0/mpi2-report.pdf}
}

@techreport{mpiforum1997jod,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI-2} Journal of Development},
  institution = {University of Tennessee, Knoxville},
  year        = {1997},
  note        = {Explicitly not part of the MPI standard},
  url         = {https://www.mpi-forum.org/docs/}
}

@techreport{mpiforum2009mpi22,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 2.2},
  institution = {University of Tennessee, Knoxville},
  year        = {2009},
  month       = sep,
  note        = {Approved September 4, 2009},
  url         = {https://www.mpi-forum.org/docs/mpi-2.2/mpi22-report.pdf}
}

@techreport{mpiforum2012mpi30,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 3.0},
  institution = {University of Tennessee, Knoxville},
  year        = {2012},
  month       = sep,
  note        = {Approved September 21, 2012},
  url         = {https://www.mpi-forum.org/docs/mpi-3.0/mpi30-report.pdf}
}

@techreport{mpiforum2015mpi31,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 3.1},
  institution = {University of Tennessee, Knoxville},
  year        = {2015},
  month       = jun,
  note        = {Approved June 4, 2015},
  url         = {https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report.pdf}
}

@techreport{mpiforum2021mpi40,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 4.0},
  institution = {University of Tennessee, Knoxville},
  year        = {2021},
  month       = jun,
  note        = {Approved June 9, 2021},
  url         = {https://www.mpi-forum.org/docs/mpi-4.0/mpi40-report.pdf}
}

@techreport{mpiforum2023mpi41,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 4.1},
  institution = {University of Tennessee, Knoxville},
  year        = {2023},
  month       = nov,
  note        = {Approved November 2, 2023},
  url         = {https://www.mpi-forum.org/docs/mpi-4.1/mpi41-report.pdf}
}

@techreport{mpiforum2025mpi50,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 5.0},
  institution = {University of Tennessee, Knoxville},
  year        = {2025},
  month       = jun,
  note        = {Approved June 5, 2025; adds a standard Application Binary Interface},
  url         = {https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report.pdf}
}

@misc{mpiforumdocs,
  author       = {{Message Passing Interface Forum}},
  title        = {{MPI} Documents},
  howpublished = {\url{https://www.mpi-forum.org/docs/}},
  year         = {2025},
  note         = {Authoritative index of MPI standard versions and approval dates;
                  accessed 2026-08-30}
}

@misc{mpiforumprocedures,
  author       = {{Message Passing Interface Forum}},
  title        = {{MPI} Forum Procedures},
  howpublished = {\url{https://www.mpi-forum.org/docs/}},
  year         = {2024},
  note         = {Side document defining OOE/IMOVE voting eligibility, quorum,
                  reading and ballot procedures; accessed 2026-08-30}
}

@article{hempel1999emergence,
  author  = {Rolf Hempel and David W. Walker},
  title   = {The emergence of the {MPI} message passing standard for parallel
             computing},
  journal = {Computer Standards \& Interfaces},
  volume  = {21},
  number  = {1},
  pages   = {51--62},
  year    = {1999},
  doi     = {10.1016/S0920-5489(99)00004-5}
}

@inproceedings{gropp2001learning,
  author    = {William D. Gropp},
  title     = {Learning from the Success of {MPI}},
  booktitle = {High Performance Computing---{HiPC} 2001, 8th International
               Conference},
  editor    = {Burkhard Monien and Viktor K. Prasanna and Sriram Vajapeyam},
  series    = {Lecture Notes in Computer Science},
  volume    = {2228},
  pages     = {81--92},
  publisher = {Springer},
  year      = {2001},
  month     = dec,
  doi       = {10.1007/3-540-45307-5_8}
}

@inproceedings{gropp2002goals,
  author    = {William D. Gropp and Ewing Lusk},
  title     = {Goals Guiding Design: {PVM} and {MPI}},
  booktitle = {Proceedings of the 2002 IEEE International Conference on Cluster
               Computing (CLUSTER 2002)},
  pages     = {257--265},
  publisher = {IEEE Computer Society},
  address   = {Chicago, IL},
  year      = {2002},
  month     = sep,
  doi       = {10.1109/CLUSTR.2002.1137754},
  url       = {https://wgropp.cs.illinois.edu/bib/papers/pdata/2002/mpiandpvm.pdf}
}

@inproceedings{gropp2012mpi3beyond,
  author    = {William D. Gropp},
  title     = {{MPI 3} and Beyond: Why {MPI} Is Successful and What Challenges It
               Faces},
  booktitle = {Recent Advances in the Message Passing Interface, 19th European
               {MPI} Users' Group Meeting (EuroMPI 2012)},
  editor    = {Jesper Larsson Tr{\"a}ff and Siegfried Benkner and Jack J. Dongarra},
  series    = {Lecture Notes in Computer Science},
  volume    = {7490},
  pages     = {1--9},
  publisher = {Springer},
  address   = {Vienna, Austria},
  year      = {2012},
  month     = sep,
  doi       = {10.1007/978-3-642-33518-1_1}
}

@misc{gropp2004tutorial,
  author       = {William D. Gropp},
  title        = {Parallel Programming with {MPI}},
  howpublished = {Half-day tutorial},
  year         = {2004},
  note         = {States that many parallel programs can be written with six MPI
                  functions: MPI\_INIT, MPI\_FINALIZE, MPI\_COMM\_SIZE,
                  MPI\_COMM\_RANK, MPI\_SEND, MPI\_RECV},
  url          = {https://wgropp.cs.illinois.edu/courses/}
}

@misc{gropp2019llnl,
  author       = {William D. Gropp},
  title        = {Challenges in Intranode and Internode Programming for Exascale
                  Systems},
  howpublished = {Invited talk, Lawrence Livermore National Laboratory},
  year         = {2019},
  url          = {https://wgropp.cs.illinois.edu/bib/talks/tdata/2019/llnl-gropp.pdf}
}

@misc{gropp2022exampi,
  author       = {William D. Gropp},
  title        = {Challenges for {MPI} in Its Third Decade},
  howpublished = {Keynote, Workshop on Exascale MPI (ExaMPI)},
  year         = {2022},
  url          = {https://wgropp.cs.illinois.edu/bib/talks/tdata/2022/exampi-final.pdf}
}

@article{snir2018future,
  author  = {Marc Snir},
  title   = {Technical Perspective: The Future of {MPI}},
  journal = {Communications of the ACM},
  volume  = {61},
  number  = {10},
  pages   = {105},
  year    = {2018},
  month   = oct,
  doi     = {10.1145/3264415}
}

@inproceedings{kennedy2007hpf,
  author    = {Ken Kennedy and Charles Koelbel and Hans Zima},
  title     = {The Rise and Fall of {High Performance Fortran}: An Historical
               Object Lesson},
  booktitle = {Proceedings of the Third ACM SIGPLAN Conference on History of
               Programming Languages (HOPL III)},
  pages     = {7-1--7-22},
  publisher = {ACM},
  address   = {San Diego, CA},
  year      = {2007},
  month     = jun,
  doi       = {10.1145/1238844.1238851}
}

@article{kennedy2011hpfcacm,
  author  = {Ken Kennedy and Charles Koelbel and Hans Zima},
  title   = {The rise and fall of {High Performance Fortran}},
  journal = {Communications of the ACM},
  volume  = {54},
  number  = {11},
  pages   = {74--82},
  year    = {2011},
  month   = nov,
  doi     = {10.1145/2018396.2018415}
}

@book{snir1996completeref,
  author    = {Marc Snir and Steve W. Otto and Steven Huss-Lederman and
               David W. Walker and Jack Dongarra},
  title     = {{MPI}: The Complete Reference},
  publisher = {MIT Press},
  address   = {Cambridge, MA},
  year      = {1996},
  note      = {States that MPI-1 comprises 128 routines and gives the Forum's
                rationale for the interface size (``Why is MPI so big?'').
                Online edition:
                \url{https://netlib.org/utk/papers/mpi-book/mpi-book.html}}
}

@book{koelbel1993hpf,
  author    = {Charles H. Koelbel and David B. Loveman and Robert S. Schreiber and
               Guy L. Steele Jr. and Mary E. Zosel},
  title     = {The {High Performance Fortran} Handbook},
  publisher = {MIT Press},
  address   = {Cambridge, MA},
  year      = {1993}
}

@article{gropp1996mpich,
  author  = {William Gropp and Ewing Lusk and Nathan Doss and Anthony Skjellum},
  title   = {A high-performance, portable implementation of the {MPI} message
             passing interface standard},
  journal = {Parallel Computing},
  volume  = {22},
  number  = {6},
  pages   = {789--828},
  year    = {1996},
  month   = sep,
  doi     = {10.1016/0167-8191(96)00024-5}
}

@techreport{gropp1992testimpl,
  author      = {William Gropp and Ewing Lusk},
  title       = {A Test Implementation of the {MPI} Draft Message-Passing Standard},
  institution = {Argonne National Laboratory},
  number      = {ANL-92/47},
  year        = {1992},
  doi         = {10.2172/10132586}
}

@article{balaji2020mpich,
  author  = {Pavan Balaji and Yanfei Guo and Rajeev Thakur and Ken Raffenetti and
             Hui Zhou and others},
  title   = {Translational research in the {MPICH} project},
  journal = {Journal of Computational Science},
  volume  = {52},
  pages   = {101203},
  year    = {2020},
  doi     = {10.1016/j.jocs.2020.101203},
  note    = {Author list abbreviated; verify full author order against the
             publisher record before final submission}
}

@inproceedings{burns1994lam,
  author    = {Greg Burns and Raja Daoud and James Vaigl},
  title     = {{LAM}: An Open Cluster Environment for {MPI}},
  booktitle = {Proceedings of Supercomputing Symposium '94},
  editor    = {John W. Ross},
  pages     = {379--386},
  publisher = {University of Toronto},
  address   = {Toronto, Canada},
  year      = {1994}
}

@inproceedings{squyres2003lam,
  author    = {Jeffrey M. Squyres and Andrew Lumsdaine},
  title     = {A Component Architecture for {LAM/MPI}},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing
               Interface (EuroPVM/MPI 2003)},
  series    = {Lecture Notes in Computer Science},
  volume    = {2840},
  pages     = {379--387},
  publisher = {Springer},
  year      = {2003},
  doi       = {10.1007/978-3-540-39924-7_52}
}

@inproceedings{gabriel2004openmpi,
  author    = {Edgar Gabriel and Graham E. Fagg and George Bosilca and
               Thara Angskun and Jack J. Dongarra and Jeffrey M. Squyres and
               Vishal Sahay and Prabhanjan Kambadur and Brian Barrett and
               Andrew Lumsdaine and Ralph H. Castain and David J. Daniel and
               Richard L. Graham and Timothy S. Woodall},
  title     = {Open {MPI}: Goals, Concept, and Design of a Next Generation {MPI}
               Implementation},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing
               Interface, 11th European PVM/MPI Users' Group Meeting},
  editor    = {Dieter Kranzlm{\"u}ller and P{\'e}ter Kacsuk and Jack Dongarra},
  series    = {Lecture Notes in Computer Science},
  volume    = {3241},
  pages     = {97--104},
  publisher = {Springer},
  address   = {Budapest, Hungary},
  year      = {2004},
  month     = sep,
  doi       = {10.1007/978-3-540-30218-6_19}
}

@inproceedings{liu2003rdma,
  author    = {Jiuxing Liu and Jiesheng Wu and Sushmitha P. Kini and Pete Wyckoff
               and Dhabaleswar K. Panda},
  title     = {High Performance {RDMA}-Based {MPI} Implementation over {InfiniBand}},
  booktitle = {Proceedings of the 17th Annual International Conference on
               Supercomputing (ICS '03)},
  pages     = {295--304},
  publisher = {ACM},
  year      = {2003},
  doi       = {10.1145/782814.782855}
}

@misc{mvapichweb,
  author       = {{The MVAPICH Team, The Ohio State University}},
  title        = {{MVAPICH}: {MPI} over {InfiniBand}, {Omni-Path}, {Ethernet/iWARP},
                  {RoCE}, and {Slingshot}},
  howpublished = {\url{https://mvapich.cse.ohio-state.edu/}},
  year         = {2025},
  note         = {Accessed 2026-08-30}
}

@misc{mvapich2024roadmap,
  author       = {{The MVAPICH Team, The Ohio State University}},
  title        = {{MVAPICH} Project Overview and Roadmap},
  howpublished = {\url{https://mvapich.cse.ohio-state.edu/}},
  year         = {2024},
  note         = {Records MVAPICH's 2001--2002 origins over InfiniBand, its MPICH
                  base, and cumulative download statistics; accessed 2026-08-30}
}

@misc{intelmpi,
  author       = {{Intel Corporation}},
  title        = {{Intel MPI} Library},
  howpublished = {\url{https://www.intel.com/content/www/us/en/developer/tools/oneapi/mpi-library.html}},
  year         = {2025},
  note         = {Accessed 2026-08-30}
}

@misc{ibmspectrummpi,
  author       = {{IBM}},
  title        = {{IBM Spectrum MPI}},
  howpublished = {\url{https://www.ibm.com/products/spectrum-mpi}},
  year         = {2025},
  note         = {Accessed 2026-08-30}
}

@misc{chpc2024mpilibs,
  author       = {{Center for High Performance Computing, University of Utah}},
  title        = {{MPI} Libraries},
  howpublished = {\url{https://www.chpc.utah.edu/documentation/software/mpilibraries.php}},
  year         = {2024},
  note         = {Documents vendor MPI lineage: Intel MPI and MVAPICH from MPICH,
                  Cray MPT from MPICH, IBM Spectrum MPI from Open MPI;
                  accessed 2026-08-30}
}

@inproceedings{hammond2023abi,
  author    = {Jeff R. Hammond and Lisandro Dalcin and Erik Schnetter and
               Marc P{\'e}rache and Jean-Baptiste Besnard and Jed Brown and
               Gonzalo Brito Gadeschi and Simon Byrne and Joseph Schuchart and
               Hui Zhou},
  title     = {{MPI} Application Binary Interface Standardization},
  booktitle = {Proceedings of EuroMPI 2023: the 30th European MPI Users' Group
               Meeting (EuroMPI '23)},
  publisher = {ACM},
  address   = {Bristol, United Kingdom},
  year      = {2023},
  month     = sep,
  doi       = {10.1145/3615318.3615319}
}

@inproceedings{hoefler2007nbc,
  author    = {Torsten Hoefler and Andrew Lumsdaine and Wolfgang Rehm},
  title     = {Implementation and Performance Analysis of Non-Blocking Collective
               Operations for {MPI}},
  booktitle = {Proceedings of the 2007 ACM/IEEE Conference on Supercomputing (SC '07)},
  publisher = {ACM/IEEE},
  year      = {2007},
  month     = nov,
  doi       = {10.1145/1362622.1362692}
}

@article{hoefler2007cg,
  author  = {Torsten Hoefler and Peter Gottschling and Andrew Lumsdaine and
             Wolfgang Rehm},
  title   = {Optimizing a conjugate gradient solver with non-blocking collective
             operations},
  journal = {Parallel Computing},
  volume  = {33},
  number  = {9},
  pages   = {624--633},
  year    = {2007},
  month   = sep,
  doi     = {10.1016/j.parco.2007.06.004}
}

@inproceedings{hoefler2008sparsenbc,
  author    = {Torsten Hoefler and Florian Lorenzen and Andrew Lumsdaine},
  title     = {Sparse Non-Blocking Collectives in Quantum Mechanical Calculations},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing
               Interface, 15th European PVM/MPI Users' Group Meeting},
  series    = {Lecture Notes in Computer Science},
  volume    = {5205},
  pages     = {55--63},
  publisher = {Springer},
  year      = {2008},
  month     = sep
}

@inproceedings{hoefler2009sparse,
  author    = {Torsten Hoefler and Jesper Larsson Tr{\"a}ff},
  title     = {Sparse Collective Operations for {MPI}},
  booktitle = {Proceedings of the 23rd IEEE International Parallel and Distributed
               Processing Symposium (IPDPS), HIPS Workshop},
  publisher = {IEEE},
  year      = {2009},
  month     = may,
  doi       = {10.1109/IPDPS.2009.5160935}
}

@inproceedings{hoefler2010hybrid,
  author    = {Torsten Hoefler and Greg Bronevetsky and Brian Barrett and
               Bronis R. de Supinski and Andrew Lumsdaine},
  title     = {Efficient {MPI} Support for Advanced Hybrid Programming Models},
  booktitle = {Recent Advances in the Message Passing Interface (EuroMPI 2010)},
  series    = {Lecture Notes in Computer Science},
  volume    = {6305},
  pages     = {50--61},
  publisher = {Springer},
  year      = {2010},
  month     = sep
}

@misc{hoefler2014advancedmpi,
  author       = {Torsten Hoefler},
  title        = {Advanced {MPI}: New Features of {MPI-3}},
  howpublished = {Tutorial},
  year         = {2014},
  note         = {Covers neighborhood collectives, revised RMA, and shared-memory
                  windows (MPI\_Win\_allocate\_shared, MPI\_COMM\_TYPE\_SHARED)},
  url          = {https://htor.inf.ethz.ch/teaching/}
}

@inproceedings{hoefler2011libraries,
  author    = {Torsten Hoefler and Marc Snir},
  title     = {Writing Parallel Libraries with {MPI}---Common Practice, Issues, and
               Extensions},
  booktitle = {Recent Advances in the Message Passing Interface, 18th European
               {MPI} Users' Group Meeting (EuroMPI 2011)},
  editor    = {Yiannis Cotronis and Anthony Danalis and Dimitrios S. Nikolopoulos
               and Jack Dongarra},
  series    = {Lecture Notes in Computer Science},
  volume    = {6960},
  pages     = {345--355},
  publisher = {Springer},
  address   = {Santorini, Greece},
  year      = {2011},
  month     = sep
}

@inproceedings{hoefler2008threadornot,
  author    = {Torsten Hoefler and Andrew Lumsdaine},
  title     = {Message Progression in Parallel Computing---To Thread or Not to
               Thread?},
  booktitle = {Proceedings of the 2008 IEEE International Conference on Cluster
               Computing},
  publisher = {IEEE Computer Society},
  year      = {2008},
  month     = oct
}

@inproceedings{dinan2011noncollective,
  author    = {James Dinan and Sriram Krishnamoorthy and Pavan Balaji and
               Jeff R. Hammond and Manojkumar Krishnan and Vinod Tipparaju and
               Abhinav Vishnu},
  title     = {Noncollective Communicator Creation in {MPI}},
  booktitle = {Recent Advances in the Message Passing Interface, 18th European
               {MPI} Users' Group Meeting (EuroMPI 2011)},
  series    = {Lecture Notes in Computer Science},
  volume    = {6960},
  pages     = {282--291},
  publisher = {Springer},
  year      = {2011}
}

@inproceedings{dinan2013endpoints,
  author    = {James Dinan and Pavan Balaji and David Goodell and Douglas Miller
               and Marc Snir and Rajeev Thakur},
  title     = {Enabling {MPI} Interoperability through Flexible Communication
               Endpoints},
  booktitle = {Proceedings of the 20th European {MPI} Users' Group Meeting
               (EuroMPI '13)},
  pages     = {13--18},
  publisher = {ACM},
  year      = {2013},
  doi       = {10.1145/2488551.2488553}
}

@inproceedings{zambre2018endpoints,
  author    = {Rohit Zambre and Aparna Chandramowlishwaran and Pavan Balaji},
  title     = {Scalable Communication Endpoints for {MPI}+Threads Applications},
  booktitle = {Proceedings of the 24th IEEE International Conference on Parallel
               and Distributed Systems (ICPADS)},
  publisher = {IEEE},
  year      = {2018},
  note      = {Verify venue and page numbers against the publisher record}
}

@techreport{gregor2009probe,
  author      = {Douglas Gregor and Torsten Hoefler and Brian Barrett and
                 Andrew Lumsdaine},
  title       = {Fixing Probe for Multi-Threaded {MPI} Applications},
  institution = {Indiana University},
  number      = {674},
  year        = {2009},
  month       = jan
}

@inproceedings{holmes2016sessions,
  author    = {Daniel Holmes and Kathryn Mohror and Ryan E. Grant and
               Anthony Skjellum and Martin Schulz and Wesley Bland and
               Jeffrey M. Squyres},
  title     = {{MPI} Sessions: Leveraging Runtime Infrastructure to Increase
               Scalability of Applications at Exascale},
  booktitle = {Proceedings of the 23rd European {MPI} Users' Group Meeting
               (EuroMPI 2016)},
  pages     = {121--129},
  publisher = {ACM},
  year      = {2016},
  doi       = {10.1145/2966884.2966915}
}

@inproceedings{morgan2017persistent,
  author    = {Bradley Morgan and Daniel J. Holmes and Anthony Skjellum and
               Purushotham Bangalore and Srinivas Sridharan},
  title     = {Planning for Performance: Persistent Collective Operations for {MPI}},
  booktitle = {Proceedings of the 24th European {MPI} Users' Group Meeting
               (EuroMPI '17)},
  pages     = {4:1--4:11},
  publisher = {ACM},
  year      = {2017},
  doi       = {10.1145/3127024.3127028}
}

@article{holmes2019persistent,
  author  = {Daniel J. Holmes and Bradley Morgan and Anthony Skjellum and
             Purushotham V. Bangalore and Srinivas Sridharan},
  title   = {Planning for performance: Enhancing achievable performance for {MPI}
             through persistent collective operations},
  journal = {Parallel Computing},
  volume  = {81},
  pages   = {32--57},
  year    = {2019},
  doi     = {10.1016/j.parco.2018.12.001},
  note    = {Verify DOI against the publisher record}
}

@inproceedings{grant2019finepoints,
  author    = {Ryan E. Grant and Matthew G. F. Dosanjh and Michael J. Levenhagen
               and Ron Brightwell and Anthony Skjellum},
  title     = {Finepoints: Partitioned Multithreaded {MPI} Communication},
  booktitle = {ISC High Performance 2019},
  series    = {Lecture Notes in Computer Science},
  volume    = {11501},
  pages     = {330--350},
  publisher = {Springer},
  year      = {2019},
  doi       = {10.1007/978-3-030-20656-7_17}
}

@inproceedings{grant2015lightweight,
  author    = {Ryan E. Grant and Anthony Skjellum and Purushotham V. Bangalore},
  title     = {Lightweight Threading with {MPI} Using Persistent Communications
               Semantics},
  booktitle = {Workshop on Exascale MPI (ExaMPI), held in conjunction with SC15},
  year      = {2015}
}

@article{goglin2018hierarchical,
  author  = {Brice Goglin and Emmanuel Jeannot and Farouk Mansouri and
             Guillaume Mercier},
  title   = {Hardware topology management in {MPI} applications through
             hierarchical communicators},
  journal = {Parallel Computing},
  volume  = {76},
  pages   = {70--90},
  year    = {2018},
  doi     = {10.1016/j.parco.2018.05.006}
}

@misc{schulz2018mug,
  author       = {Martin Schulz},
  title        = {Just Writing a Standard Is Not Enough!},
  howpublished = {Keynote, MVAPICH User Group (MUG) Meeting},
  year         = {2018},
  note         = {Discusses the MPI\_T tools information interface added in MPI-3.0
                  and its relationship to PMPI}
}

@inproceedings{cownie1999debugger,
  author    = {James Cownie and William Gropp},
  title     = {A Standard Interface for Debugger Access to Message Queue
               Information in {MPI}},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing
               Interface},
  series    = {Lecture Notes in Computer Science},
  volume    = {1697},
  pages     = {51--58},
  publisher = {Springer},
  year      = {1999}
}

@inproceedings{thakur1998datatypes,
  author    = {Rajeev Thakur and Ewing Lusk and William Gropp},
  title     = {A Case for Using {MPI}'s Derived Datatypes to Improve {I/O}
               Performance},
  booktitle = {Proceedings of SC98: High Performance Networking and Computing},
  year      = {1998},
  month     = nov
}

@article{thakur1996extended,
  author  = {Rajeev Thakur and Alok Choudhary},
  title   = {An extended two-phase method for accessing sections of out-of-core
             arrays},
  journal = {Scientific Programming},
  volume  = {5},
  number  = {4},
  pages   = {301--317},
  year    = {1996}
}

@inproceedings{delrosario1993twophase,
  author    = {Juan Miguel del Rosario and Rajesh Bordawekar and Alok Choudhary},
  title     = {Improved Parallel {I/O} via a Two-Phase Run-Time Access Strategy},
  booktitle = {IPPS '93 Workshop on Input/Output in Parallel Computer Systems},
  pages     = {56--70},
  year      = {1993}
}

@inproceedings{kotz1994diskdirected,
  author    = {David Kotz},
  title     = {Disk-Directed {I/O} for {MIMD} Multiprocessors},
  booktitle = {Proceedings of the 1994 Symposium on Operating Systems Design and
               Implementation (OSDI)},
  pages     = {61--74},
  year      = {1994},
  month     = nov
}

@inproceedings{laguna2019study,
  author    = {Ignacio Laguna and Ryan Marshall and Kathryn Mohror and
               Martin Ruefenacht and Anthony Skjellum and Nawrin Sultana},
  title     = {A Large-Scale Study of {MPI} Usage in Open-Source {HPC}
               Applications},
  booktitle = {Proceedings of the International Conference for High Performance
               Computing, Networking, Storage and Analysis (SC '19)},
  publisher = {ACM},
  address   = {Denver, CO},
  year      = {2019},
  month     = nov,
  doi       = {10.1145/3295500.3356176}
}

@article{bernholdt2020ecp,
  author  = {David E. Bernholdt and Swen Boehm and George Bosilca and
             Manjunath Gorentla Venkata and Ryan E. Grant and Thomas Naughton and
             Howard P. Pritchard and Martin Schulz and Geoffroy R. Vall{\'e}e},
  title   = {A survey of {MPI} usage in the {US} exascale computing project},
  journal = {Concurrency and Computation: Practice and Experience},
  volume  = {32},
  number  = {3},
  pages   = {e4851},
  year    = {2020},
  doi     = {10.1002/cpe.4851}
}

@misc{squyres_mpimechanic,
  author       = {Jeffrey M. Squyres},
  title        = {The {MPI} Mechanic Columns},
  howpublished = {\url{https://cw.squyres.com/}},
  year         = {2006},
  note         = {Archive of the MPI Mechanic column from ClusterWorld magazine;
                  continued as the MPI Monkey column at
                  \url{https://www.clustermonkey.net/Columns/MPI/}.
                  Accessed 2026-08-30}
}

@techreport{dongarra1995pvmcontext,
  author      = {Jack J. Dongarra and G. Al Geist and Robert J. Manchek and
                 Philip M. Papadopoulos},
  title       = {Adding Context and Static Groups into {PVM}},
  institution = {Oak Ridge National Laboratory},
  year        = {1995},
  month       = jul
}

@inproceedings{ferrari1995tpvm,
  author    = {Adam J. Ferrari and Vaidy S. Sunderam},
  title     = {{TPVM}: Distributed Concurrent Computing with Lightweight Processes},
  booktitle = {Proceedings of the Fourth IEEE International Symposium on High
               Performance Distributed Computing (HPDC)},
  pages     = {211--218},
  publisher = {IEEE Computer Society Press},
  year      = {1995},
  month     = aug
}

@misc{ref2014casestudy,
  author       = {{Research Excellence Framework}},
  title        = {Impact Case Study: {MPI} and the Message Passing Interface Forum},
  howpublished = {\url{https://impact.ref.ac.uk/casestudies/CaseStudy.aspx?Id=35271}},
  year         = {2014},
  note         = {Records that MPIF comprised over 40 organisations including Cray,
                  IBM, Intel, Meiko, NEC and Thinking Machines; accessed 2026-08-30}
}
```

### Notes on citation hygiene

- **Verified directly against primary sources:** all MPI Forum version dates and background sections
  (from `mpi-forum.org/docs/` and the MPI-5.0 report §§2.1–2.10, 21.2.1); the MPI-1.1 goals,
  included/excluded lists, and acknowledgments table; the MPI Forum Procedures voting rules; the
  Williamsburg date, the MPI1 draft attribution, the 6-week meeting cadence, and the 60-people /
  40-organizations figures; the MPI-4.1 bibliography entries for all predecessor systems; Gropp &
  Lusk's MPI goal list and PVM comparison; the Laguna et al. statistics; the Kennedy/Koelbel/Zima HPF
  post-mortem reasons; the IBM Systems Journal accounts of EUI/MPL and the SP1; the Gropp tutorial's
  six-function claim.
- **DOIs verified:** `gropp2001learning`, `gropp2012mpi3beyond`, `gropp2002goals`, `snir2018future`,
  `kennedy2007hpf`, `kennedy2011hpfcacm`, `gropp1996mpich`, `gabriel2004openmpi`, `hammond2023abi`,
  `laguna2019study`, `bernholdt2020ecp`, `butler1994p4`, `sunderam1990pvm`, `harrison1991tcgmsg`,
  `hempel1999emergence`, `carriero1989linda`, `liu2003rdma`, `hoefler2007nbc`, `hoefler2009sparse`,
  `holmes2016sessions`, `morgan2017persistent`, `grant2019finepoints`, `goglin2018hierarchical`,
  `dinan2013endpoints`, `squyres2003lam`, `gropp1992testimpl`, `balaji2020mpich`.
- **Please re-check before submission:** `holmes2019persistent` (DOI), `zambre2018endpoints` (venue and
  pages), `balaji2020mpich` (full author list and order), `snir1995sp2` and `gropp1995sp1` (IBM Systems
  Journal DOIs are constructed from the standard `10.1147/sj.VVI.PPPP` pattern and page ranges — the
  PDFs are verified but confirm the DOIs), `mpiforum1994` (the 1994 journal article has no DOI I could
  confirm; it is IJSAHPC 8(3–4):165–414, sometimes cited as ending at page 416),
  `skjellum1994zipcode` and `calkin1994parmacs` (Parallel Computing 20(4) DOIs not confirmed),
  `thakur1996extended`, `hoefler2007cg` (DOI constructed from the volume/pages — verify).
- **Marked `[UNVERIFIED]` in the text:** the Meiko CS-2 message-passing interface specifics; the
  specific page/function figures often attributed to Snir's 2018 CACM perspective (ACM blocked
  automated retrieval of the full text); the first attribution of the phrase "assembly language of
  parallel computing."
- **MPI-1's 128-routine count** is verified against the Forum members' own reference book
  [snir1996completeref], whose online edition at
  `https://netlib.org/utk/papers/mpi-book/node198.html` carries the "Why is MPI so big?" discussion
  quoted in §5.6. Confirm the print edition's page number for that section before final submission.
- `squyres_mpimechanic` is included because the column series was requested; it is a magazine-column
  archive rather than a peer-reviewed source, and none of the substantive claims in this note rest on
  it. Squyres is, separately, a co-author of `gabriel2004openmpi`, `squyres2003lam`, and
  `holmes2016sessions`, which are the citable venues for his technical contributions.

---

## Design lessons for AgentMPI

1. **Ship a free, complete reference implementation simultaneously with the specification — and build
   it while drafting.** MPICH existed as a test implementation of the *draft* standard in 1992, was
   free and portable at release, and its ADI/Channel layering deliberately minimized the cost of the
   first conformant port [gropp1992testimpl, gropp1996mpich]. HPF's post-mortem names the absence of an
   open-source reference implementation as a direct cause of divergence and poor performance across
   vendors, and cites MPICH's availability as the moment HPF's disadvantage became visible
   [kennedy2007hpf]. For AgentMPI: the spec and a working harness must be one artifact, and writing the
   harness must be allowed to change the spec.

2. **Standardize the interface, not the runtime.** MPI is "a specification, not an implementation"
   [mpiforum2025mpi50, §2.1], is "a library for writing application programs, not a distributed
   operating system," and mandates no daemons — a choice justified explicitly by extreme-scale
   deployments where "the very existence of local daemons may be impractical" [gropp2002goals]. This is
   why the same standard spans a laptop and a leadership-class machine. AgentMPI should specify message
   semantics and naming, and say nothing about process supervisors, schedulers, queues, or storage.

3. **Make scoped, context-isolated channels the primary abstraction, and never expose the context as a
   value.** MPI's communicator = group + opaque context, with all addressing relative to the group,
   is what makes it safe to compose libraries written by strangers: a module on a subset of processes
   "can use a local name space for its communication" [franke1994mpisp], and because "context must not
   be a visible value" [gropp2002goals], no wildcard receive in application code can capture a
   library's traffic. Gropp names composability as one of six causes of MPI's success
   [gropp2001learning], and library practice is codified as: duplicate the communicator you are given,
   never assume the global one, cache your state on it [hoefler2011libraries]. For AgentMPI, sub-agent
   channels must be first-class, cheap to create, and unforgeably isolated — a convention like
   "sub-agents use topic prefixes" is exactly the tag-hygiene failure mode communicators eliminated.

4. **Learn from MPI's mistake and design the global namespace out from the start.** MPI shipped
   `MPI_COMM_WORLD` as the mandatory root of all communication and spent twenty-four years retreating
   from it: MPI-4.0's Sessions exist to provide "an alternative way to initialize MPI"
   [mpiforum2025mpi50, §2.8] because a single global initial communicator is both a scalability
   bottleneck and a resource-isolation failure [holmes2016sessions], and non-collective communicator
   creation had to be added because creation was collective over the parent
   [dinan2011noncollective]. AgentMPI should let a component acquire a scoped channel from a named
   process set without any global rendezvous.

5. **Keep semantics thin: opaque payloads plus a layout description, and no global ontology.** MPI
   describes messages as (buffer, count, datatype), where the datatype conveys only layout and
   primitive element type — enough for heterogeneity and zero-copy gather/scatter, and nothing about
   meaning [mpiforum2025mpi50, §2.1]. Opaque handles throughout came from the extensibility goal
   [gropp2002goals] and are what allowed MPI to change internals for thirty years and eventually add a
   binary ABI without breaking source compatibility [mpiforum2025mpi50, §21.2.1]. AgentMPI should not
   attempt to standardize what an agent message *means*; a tag and an opaque body are enough.

6. **Minimize concepts, not functions; make each concept discharge several requirements.** The Forum
   "sought to make each approach solve as many of these goals as possible," so datatypes serve
   heterogeneity *and* non-contiguous layout, for messages *and* for files, and communicators serve
   grouping *and* isolation [gropp2002goals]; `MPI_Info` later served spawn hints, I/O hints, and
   application assertions [gropp2002goals, mpiforum2025mpi50, §2.8]. MPI has hundreds of functions but
   roughly a dozen concepts. AgentMPI should be judged on concept count, and should resist adding a new
   noun whenever an existing one can be stretched honestly.

7. **Build an onion: a six-function core that a beginner can learn in an hour, with expert layers
   above.** Walker's founding workshop report already proposed the "onion skin" layering
   [walker1992standards]; Gropp's tutorial demonstrates six functions sufficing for many real programs
   [gropp2004tutorial]; and measurement thirty years later confirms it worked — 42% of open-source MPI
   applications need only MPI-1.0 features and ~80% only MPI-2.0, while applications under 100k lines
   "rarely use more than forty functions" [laguna2019study]. The Forum's own defense of MPI-1's 128
   routines states the mechanism: "the availability of a large number of calls to deal with more
   esoteric features of MPI allows one to provide a simpler interface to the more frequently used
   functions" [snir1996completeref] — many narrow entry points, rather than few functions with many
   options each. Expect the same distribution for AgentMPI, and make the tiny core beautiful before
   enriching the tail.

8. **Expect your advanced features to go unused, and price standardization accordingly.** Laguna et al.
   find that persistent and one-sided operations are far less used than plain point-to-point despite
   potentially better performance, that 67% of applications use blocking send/receive, and that
   sub-version features are "practically of little value to applications"; they conclude this "raises
   questions about the value of the efforts and costs in standardizing minor features"
   [laguna2019study]. Bernholdt et al. similarly find MPI is largely consumed through libraries rather
   than called directly [bernholdt2020ecp]. AgentMPI should gate new surface area behind demonstrated
   demand, and should invest in the small set of primitives that libraries will wrap.

9. **Make nondeterminism opt-in and locally auditable; guarantee only the ordering you can cheaply
   keep, and put stronger promises in advice rather than in the mandate.** MPI guarantees
   non-overtaking per (sender, receiver, communicator) and nothing stronger, stating plainly that with
   multiple threads two sends "are logically concurrent, even if one physically precedes the other"
   [mpiforum2015mpi31, §3.5]; all receive-side nondeterminism enters through explicit
   `MPI_ANY_SOURCE`/`MPI_ANY_TAG`, whose absence MPI-4.0 made a machine-checkable assertion
   [mpiforum2025mpi50, §2.8]. Where a stronger guarantee was too costly to mandate — bitwise
   reproducibility of floating-point reductions — the Forum declined to require it but advised
   implementers to fix the reduction order for a given communicator so repeated runs agree, while
   conceding that results may differ across process counts [mpiforum2009mpi22, §5.9.1]. That
   three-tier structure — required / "expected of a high-quality implementation" / advice
   [gropp2002goals] — let MPI ask for good behavior without erecting a conformance barrier, and is the
   right shape for AgentMPI's aggregation and merge semantics over nondeterministic model outputs.

10. **Prefer explicit locality and traceable data movement over convenience that breaks the
    source-to-behavior mapping.** MPI moves data only "through cooperative operations on each process"
    [mpiforum2025mpi50, §2.1]. HPF's compilers hid the movement and, in doing so, destroyed
    debuggability: transformations made "the relationship between the original source program and the
    corresponding target program less than obvious," so users "might well understand what was causing
    the performance problem but have no idea how to change the HPF source to address it"
    [kennedy2007hpf]. For agent harnesses, where "data movement" is context assembly and token cost,
    hidden context injection is the exact analogue — and the same debugging catastrophe.

11. **Standardize the observation seam in v1.0, and keep the tool out of the standard.** MPI-1 listed
    the profiling interface among the things the standard *includes* [mpiforum1995mpi11, §1.4] while
    listing debugging facilities among the things it excludes [mpiforum1995mpi11, §1.5]; a named
    subcommittee owned profiling from the start [mpiforum1995mpi11]. MPI-3.0 added `MPI_T` for
    introspection into implementation internals, complementing call interception
    [mpiforum2012mpi30, schulz2018mug], and debugger/queue interfaces live in side documents
    [mpiforumdocs]. AgentMPI should specify interception and introspection hooks — every message, every
    channel operation, every named counter — as normative, and let tracing/eval/replay tools be
    unstandardized.

12. **Provide an extensible must-ignore-unknown-keys metadata channel instead of a fixed resource
    vocabulary.** Facing spawn-time resource requirements, the Forum enumerated three options,
    "spent a great deal of time trying to find" a fully expressive generic vocabulary, and chose
    instead to pass implementation-specific information through `MPI_Info`, with the rule that
    "MPI implementations are required to ignore unrecognized fields; this strategy encourages users to
    provide extra information when possible" [gropp2002goals]. The justification for rejecting a fixed
    vocabulary is exactly AgentMPI's situation: "the information that any command can provide on the
    environment is immediately out of date" [gropp2002goals]. Model names, sampling parameters, tool
    permissions and budgets belong in such a channel, not in the core type system.

13. **Do not import MPI's reliability assumption.** MPI's goals state: "Assume a reliable communication
    interface: the user need not cope with communication failures. Such failures are dealt with by the
    underlying communication subsystem" [mpiforum2025mpi50, §2.1]. That assumption bought enormous
    simplification and became MPI's most criticized limitation: FT-MPI and successors never entered the
    standard [gabriel2004openmpi], MPI-4.0 delivered only "improvements to the definitions of error
    handling" rather than recovery [mpiforum2025mpi50, §2.8], and fault tolerance remains an open
    exascale concern [bernholdt2020ecp]. Agent calls fail constantly — timeouts, refusals, malformed
    output, rate limits — so AgentMPI must treat participant failure as a first-class, in-band outcome
    from v1.0. Note also that PVM, which *did* emphasize "handling communication failures"
    [gropp2002goals], was better suited to loosely-coupled failure-prone environments — which is the
    environment AgentMPI actually inhabits.

14. **Make the governance rules cheap to join, expensive to change the text, and impossible to win by
    ambush; require a working implementation as the price of admission.** MPI's meetings are open to
    anyone [mpiforum2025mpi50, §2.2; mpiforumprocedures], but voting is one-organization-one-vote,
    earned by attending two of the last three meetings, gated by a 2/3 meeting quorum and a 3/4 ballot
    quorum, and requires a 3/4 supermajority plus *two* readings and *two* ballots, with any interim
    text change needing a zero-no-votes ballot [mpiforumprocedures]. MPI-2.2 additionally required that
    every extension preserve backward compatibility, show significant user benefit, and be
    "accompanied by an open source implementation" [mpiforum2025mpi50, §2.5]. Speculative ideas went to
    a published-but-non-normative Journal of Development [mpiforum2025mpi50, §2.3]. And MPI held no
    official standards-body sanction at all [mpiforum1995mpi11] — its authority came entirely from
    adoption. AgentMPI's process document should be written before its second version, not after.

15. **Do not defer binary/wire-level compatibility for thirty-one years.** MPI standardized source
    compatibility in 1994 and only mandated a standard ABI in MPI-5.0 in June 2025
    [mpiforum2025mpi50, §2.10; mpiforumdocs], after the ecosystem had settled into two upstream code
    bases with many mutually incompatible binary builds [chpc2024mpilibs, hammond2023abi]. Note the
    accompanying discipline: the standard ABI deliberately excludes everything deprecated in MPI-3.1 or
    earlier, with the rationale that including deprecated features in an ABI makes them undeletable
    [mpiforum2025mpi50, §21.2.1]. AgentMPI is a *protocol*, so its wire format is its ABI: specify it
    normatively and versioned from v1.0, and keep deprecated surface out of it.
