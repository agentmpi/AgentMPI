# Dossier 01 — MPI: History, Standardization Process, and Design Philosophy

**Purpose.** Citation-grounded background for the "AgentMPI" paper. Every non-obvious factual
claim carries an inline `[Key Year]` citation resolving to the **References** section. Claims I could
not confirm against a primary or strong secondary source are marked `[UNVERIFIED]`.

**Methodological note on dates.** The MPI Forum's own documents disagree with each other about
MPI-1.0. The MPI-1.1 report front matter is headed "Version 1.0: June, 1994" but in the very next
paragraph refers to "the MPI document of May 5, 1994, referred to below as Version 1.0"
[MPIForum 1995]. Later Forum documents settle on **May 5, 1994** as the MPI-1.0 document date
[MPIForum 2015; MPIForum 2025], while the Forum's document index page lists approval dates rather
than document dates [MPIForum Docs]. A paper citing "MPI-1.0, June 1994" and one citing
"MPI-1.0, May 1994" are both defensible; the *document* is dated May 5, 1994 and the journal
publication is 1994 [MPIForum 1994]. I flag every remaining date conflict below rather than
silently picking one.

---

## 1. Pre-history: the portability crisis and the precursor systems (c. 1985–1993)

### 1.1 The portability crisis

By the late 1980s distributed-memory concurrent computers were commercially viable but each vendor
shipped its own incompatible message-passing interface. The consequence was not merely
inconvenience: an application encoded a machine's communication API throughout its source, so
porting meant rewriting. The Williamsburg workshop report frames the goal of standardization as
"making it easier to develop efficient, portable application codes for such machines," and records
that the two headline advantages the attendees identified were "portability and ease-of-use"
[Walker 1992]. The MPI-1.1 standard repeats this framing verbatim in its own overview
[MPIForum 1995].

The economics mattered as much as the ergonomics. The Williamsburg report notes the workshop
"generally agreed that vendors should be closely involved in the standardization effort, in order to
ensure that whatever message-passing standard emerges can and will be implemented efficiently on
commercial distributed memory computing systems," and that a standard "would provide vendors with a
clearly defined set of routines" they could implement efficiently or support in hardware
[Walker 1992]. This is the crucial asymmetry that made MPI possible: a standard is *cheaper* for
vendors than a proprietary API, because it lets them compete on implementation quality rather than
on API lock-in, while shifting the cost of application porting off their customers.

Two structural features of the crisis are worth transposing to the multi-agent setting. First,
application lifetimes greatly exceed hardware lifetimes — Gropp puts application lifetime at "often
ten to twenty years, rarely less than five years," against much shorter hardware generations
[Gropp 2001]. Second, portability alone was insufficient: sockets were equally portable and equally
available, and were in fact used as the transport under PVM and p4, yet sockets did not become the
HPC programming model [Gropp 2001]. Portability is necessary but the binding constraint is
*portability with performance*.

The Williamsburg report also records an early architectural metaphor, the **"Onion Skin model"** —
the observation that different constituencies (application programmers, library writers, compiler
writers) want different layers, and that "if the Onion Skin model is valid, then it makes sense to
impose a standard that is also layered" [Walker 1992]. This layering instinct survives into MPI as
the small-orthogonal-core plus convenience-layer structure discussed in §4(h).

### 1.2 What MPI itself says it borrowed

The standard is unusually explicit about its debts. MPI-1.1 §1.1 states: "In designing MPI we have
sought to make use of the most attractive features of a number of existing message passing systems,
rather than selecting one of them and adopting it as the standard. Thus, MPI has been strongly
influenced by work at the IBM T. J. Watson Research Center, Intel's NX/2, Express, nCUBE's Vertex,
p4, and PARMACS. Other important contributions have come from Zipcode, Chimp, PVM, Chameleon, and
PICL" [MPIForum 1995]. The CACM overview adds vendor systems from "IBM, Intel, Meiko Scientific,
Cray Research, and nCube" [Dongarra et al. 1996].

Walker's design paper states the governing rule for *when* to borrow: "while it would be imprudent
to include new and untested features in the standard, concepts that have been tested in a research
environment should be considered for inclusion" [Walker 1994]. This is the operative form of
"standardize existing practice" — not "only what is deployed," but "only what has been demonstrated."

### 1.3 System-by-system

**PVM (Parallel Virtual Machine), ORNL / Univ. Tennessee / Emory, 1989–.** Begun in summer 1989 at
Oak Ridge National Laboratory; the PVM 1.0 prototype was built by Vaidy Sunderam and Al Geist and
never released outside the lab. Version 2 was written at the University of Tennessee and released
publicly in 1991; version 3 was completed February 1993 [Geist et al. 1994]. Note a date conflict
inside PVM's own documentation: the book's history chapter says version 2 was "released in March
1991," while the version appendix in the same book lists "PVM 2.0 (Feb. 1991)" and "PVM 2.1 (Mar.
1991)" [Geist et al. 1994]. Key contributions: the *virtual machine* abstraction over a
heterogeneous pool of hosts; dynamic process spawning; XDR-based heterogeneous data encoding;
daemon-mediated resource management. MPI-1 deliberately did **not** take PVM's dynamic process
management (it arrived only in MPI-2, §3.2) but did take the goal of heterogeneous operation, which
appears in the MPI-1 goal list as "Allow for implementations that can be used in a heterogeneous
environment" [MPIForum 1995]. PVM remained MPI's principal rival through the 1990s, and the
EuroPVM workshop series (1994–1996) evolved into EuroPVM/MPI and then EuroMPI [HPCwire 2017].

**p4, Argonne National Laboratory, in use since 1984.** By Ralph Butler and Ewing "Rusty" Lusk.
Its predecessor was the m4-macro-based "Argonne macros" described in *Portable Programs for
Parallel Processors* by Lusk, Overbeek et al., "from which p4 takes its name" [Netlib p4]. p4 is
notable for spanning *both* models: monitors for shared memory and send/receive for distributed
memory, plus "clusters" meaning shared-memory multiprocessors communicating by message passing
[Butler & Lusk 1994]. Contributions visible in MPI: global operations (broadcast, global sum, max)
as first-class library calls; explicit support for **both** master–slave and SPMD structuring
[Netlib p4]; and, via the monitor lineage, a culture of thinking about progress and synchronization
semantics. The exact publication year of the Lusk/Overbeek Holt, Rinehart & Winston book is
`[UNVERIFIED]` — the p4 README cites it without a date [Netlib p4]; a related Lusk & Overbeek
technical report on implementing monitors with macros dates to 1983 [Butler & Lusk 1994].

**PARMACS, GMD (Germany) with Argonne, c. 1987–1994.** Rolf Hempel and colleagues. Originally "a
set of macro extensions to the p4 system developed at GMD… It originated in an effort to provide
Fortran interfaces to the P4 system, but is now a significantly enhanced package that provides a
variety of high-level abstractions, mostly dealing with global operations" [Netlib P4Parmacs]. Its
distinctive contribution is **logical process topologies**: a `torus` macro that emits a
configuration placing p4 processes in a 3-D torus, plus general graphs, and macros used with
send/recv "to achieve topology-specific communications" [Netlib P4Parmacs]. This is the direct
ancestor of MPI's process-topology chapter, and it is no accident that **Rolf Hempel was the
MPI-1.0/1.1 coordinator for Process Topologies** [MPIForum 2025]. The Argonne/GMD macros were
published for the Intel iPSC/2 in 1990 [Bomans et al. 1990]. PARMACS also carried the European
ESPRIT-funded standardization agenda into the Forum: Hempel gave two of the Williamsburg talks,
including "PARMACS: the ANL/GMD Portability Macros for Message Passing," and discussed "the role
played by the European Community in fostering parallel computing standards through its ESPRIT
research program" [Walker 1992].

**Chameleon, Argonne, 1993.** William Gropp and Barry Smith [Gropp & Smith 1993]. A thin
(largely C-macro) portability layer over vendor message-passing systems — Intel NX, TMC CMMD, IBM
MPL — plus p4 and PVM [Gropp et al. 1996]. Chameleon's contribution is *methodological rather than
semantic*: it demonstrated that a macro-thin portability layer costs essentially nothing, which is
the empirical premise behind MPI's claim that a standard need not be a lowest-common-denominator
performance sacrifice. Its practical legacy is the name and the substrate of MPICH: "the CH is for
Chameleon, symbolizing adaptability and portability, and for Bill's library" [Gropp & Lusk 2012],
and "a substantial amount of Chameleon technology is incorporated into MPICH" [Gropp et al. 1996].

**Zipcode, LLNL / Caltech / Mississippi State, 1990–1994.** Anthony Skjellum, Steven G. Smith,
Nathan E. Doss, Alvin P. Leung, and Manfred Morari [Skjellum & Leung 1990; Skjellum et al. 1994].
**This is the single most important precursor for the argument the AgentMPI paper needs to make.**
Zipcode's own retrospective states: "Features of Zipcode that were originally unique to it were its
simultaneous support of static process groups, communication contexts, and virtual topologies,
forming the 'mailer' data structure. Point-to-point and collective operations reference the
underlying group, and use contexts to avoid mixing up messages" [Skjellum et al. 1994]. The MPICH
paper is equally direct: "Zipcode is a portable system for writing scalable libraries. It
contributed several concepts to the design of the MPI Standard — in particular contexts, groups, and
mailers (the equivalent of MPI communicators). Zipcode also contains extensive collective operations
with group scope as well as virtual topologies, and this code was heavily borrowed from in the first
version of MPICH" [Gropp et al. 1996]. Gropp's retrospective adds the footnote: "The context part
of the communicator was inspired by Zipcode" [Gropp 2001]. Skjellum was a coordinator of the MPI-1
*Groups, Contexts, and Communicators* subgroup [MPIForum 2025]; the companion Multicomputer Toolbox
work motivated contexts from the needs of *scalable libraries* specifically [Skjellum et al. 1993a],
and "Writing Libraries in MPI" made the argument to the MPI community directly
[Skjellum et al. 1993b].

**NX/2, Intel, 1988 (iPSC/2, later Paragon).** Paul Pierce [Pierce 1988]; Pierce later published a
retrospective on the whole NX interface family [Pierce 1994]. NX/2 was the node OS of the iPSC/2 and
supplied "a simple, effective set of synchronous calls" plus "advanced asynchronous calls which
allow overlap of message passing and processing as well as interrupt-driven message handling"
[Pierce 1988]. The NX call vocabulary — `csend`/`crecv` synchronous, `isend`/`irecv` asynchronous
with `msgwait`/`msgdone` completion testing, `gcol` concatenation, `gsync` barrier — is recognizably
the shape of MPI's point-to-point layer: the **blocking/nonblocking split with an explicit
completion-test operation** is NX's most direct bequest. Pierce attended Williamsburg and spoke on
"Enhancements to NX/2 Message Passing for Portable Communications Libraries," and is listed among
active MPI-1 participants [Walker 1992; MPIForum 2025].

**Vertex, nCUBE, c. 1990.** The nCUBE 2 node operating system's message-passing layer; MPI-1 cites
the *nCUBE 2 Programmers Guide*, r2.0, December 1990 [nCUBE 1990]. Named by the standard as a
strong influence [MPIForum 1995]. I could not obtain the Vertex manual itself, so the specific
abstractions MPI took from Vertex (as distinct from NX) are `[UNVERIFIED]`; the safe claim is that
nCUBE's system is one of the six named "strong influences" on MPI-1 and that nCUBE was among the
vendors in whose products MPI "has roots" [MPIForum 1995; Dongarra et al. 1996].

**Express, ParaSoft Corporation, c. 1988–1992.** Commercial product growing out of Caltech's
hypercube work; MPI-1 cites the *Express User's Guide* v3.2.5, 1992 [ParaSoft 1992]. Express's
distinctive contributions were around *portable parallel I/O and the host–node model*: the `cubix`
server allowed a program written for the host to run on the parallel processor while still using the
host's file system and terminals, and Express explicitly handled heterogeneous host/node data
representation (word length, byte order, floating-point format) [ParaSoft 1992]. MPI-1's
"heterogeneous environment" goal and MPI's datatype machinery — which describes *type* so that
implementations can convert between representations, as well as *layout* — inherit this concern
[MPIForum 1995; Gropp 2001].

**CHIMP, Edinburgh Parallel Computing Centre, 1991–1992.** MPI-1 cites *CHIMP Concepts* (June 1991)
and *CHIMP Version 1.0 Interface* (May 1992) [EPCC 1991; EPCC 1992]. Named by both the standard and
Walker's design paper as one of the "more recent and innovative" systems whose ideas broadened MPI
[MPIForum 1995; Walker 1994]. **Lyndon Clarke of EPCC was a coordinator of the MPI-1 Groups,
Contexts, and Communicators subgroup** [MPIForum 2025], which is the concrete channel by which
CHIMP's ideas entered the communicator design. The precise CHIMP abstractions adopted are
`[UNVERIFIED]` — I could not retrieve the CHIMP documents.

**PICL, ORNL, 1990.** G. A. Geist, M. T. Heath, B. W. Peyton, P. H. Worley, "A user's guide to
PICL: a portable instrumented communication library" [Geist et al. 1990]. Named as an "important
contribution" by MPI-1 [MPIForum 1995]. The salient word is *instrumented*: PICL paired a portable
communication layer with built-in tracing. MPI's answer to the same need is the **profiling
interface (PMPI)**, whose MPI-1 coordinator was James Cownie [MPIForum 2025].

**IBM T. J. Watson: EUI / Venus / collective-communication work, 1992.** MPI-1 cites Bala & Kipnis
on "Process groups: a mechanism for the coordination of and communication among processes in the
Venus collective communication library" and Bala, Kipnis, Rudolph & Snir on "Designing efficient,
scalable, and portable collective communication libraries" [Bala & Kipnis 1992; Bala et al. 1992],
and lists work at IBM Watson *first* among strong influences [MPIForum 1995]. Walker separately
credits "the IBM External User Interface" [Walker 1994]. Marc Snir was at IBM Watson and coordinated
both Point-to-Point and (with Geist and Otto) Collective Communication for MPI-1 [MPIForum 2025].
Also cited: Feitelson's "Communicators: Object-based multiparty interactions for parallel
programming" (Hebrew University TR 91-12, November 1991) — the earliest use I can document of the
word *communicator* in this sense [Feitelson 1991].

**TCGMSG, Theoretical Chemistry Group (Argonne, later PNNL), c. 1988–1991.** Robert J. Harrison.
A deliberately minimal message-passing library for computational chemistry. Its enduring
contribution is a *negative* datapoint: PNNL documents which TCGMSG operations have **no MPI
counterpart**, chiefly `nxtval`, "a shared memory counter with atomic updates, often used in dynamic
load balancing algorithms," plus `pfcopy` (broadcast a sequential file to all processes) and portable
Fortran `sizeof` equivalents [PNNL TCGMSG]. `nxtval` had to be re-implemented over MPI by ten
different platform-specific mechanisms, including a dedicated server process that "removes one
process from the MPI process group" [PNNL TCGMSG]. **This is the cleanest documented case of MPI's
"standardize existing practice" discipline excluding a genuinely useful primitive** — an
atomic-shared-counter/work-queue idiom — because it did not fit the two-sided model. MPI only
acquired the machinery to do this natively with MPI-3 RMA atomics. Harrison is listed among active
MPI-1 participants [MPIForum 2025]. The canonical TCGMSG *paper* citation is `[UNVERIFIED]`; I
verified the interface and its MPI gap from PNNL's documentation only.

**CMMD, Thinking Machines Corporation, CM-5, c. 1992–1993.** "CMMD is the software library used for
interprocessor communication — that is, for the message passing between nodes in the hostless model,
and between host and nodes in the host/node model," supporting C, C++, Fortran 77, CM Fortran and
C* [TMC CMMD]. CMMD is significant for the *host/node vs. hostless* distinction — hostless being
what MPI standardized as SPMD — and for **active messages**, a receiver-side-handler mechanism that
MPI pointedly did **not** adopt. CMMD was one of the vendor systems Chameleon and early MPICH sat on
top of [Gropp et al. 1996]. Note that CMMD does not appear in MPI-1's list of named influences
[MPIForum 1995]; the safe claim is that CMMD was contemporaneous, was an MPICH porting target, and
represents the road not taken on active messages.

**Hoare's CSP, 1978, and occam / the transputer, 1980s.** C. A. R. Hoare, "Communicating Sequential
Processes," CACM 21(8):666–677 [Hoare 1978]; developed into a book in 1985 and into a process algebra
by Brookes, Hoare and Roscoe [Hoare 1985; Brookes et al. 1984]. CSP posits "sequential processes,
each with a private internal state, operating concurrently and communicating by synchronized message
passing," explicitly as "an alternative and striking contrast to shared-memory parallelism"
[Roscoe & Brookes, in CSP-FDR]. Hoare's own retrospective ties CSP to occam and the INMOS transputer
and singles out "the guarded choice, which appears as the ALT command in occam" [Hoare 1991]. What
MPI takes from CSP is *philosophical rather than syntactic*: the commitment to explicit locality and
no shared address space, and the idea that synchronous rendezvous is a meaningful semantic primitive
(MPI's `MPI_Ssend` is precisely a CSP-style rendezvous send). What MPI conspicuously does **not**
take is CSP's channels-as-first-class-objects, its guarded/alternative choice construct, and its
formal algebra. MPI matches on `(communicator, source, tag)` rather than on a named channel, and has
no ALT. **Note for the AgentMPI paper: CSP is not cited in the MPI-1 bibliography** [MPIForum 1995];
any claim of direct influence should be phrased as intellectual lineage, not documented borrowing.

**The Actor model, Hewitt/Bishop/Steiger 1973; Agha 1986.** "A Universal Modular ACTOR Formalism for
Artificial Intelligence" proposes an architecture "conceptually based on a single kind of object:
actors," with asynchronous message passing to named addresses and no goto/interrupt/semaphore
primitives [Hewitt et al. 1973]; Agha's 1986 monograph supplied the fuller algebraic treatment
[Agha 1986]. Actors differ from MPI on three axes that matter for agent harnesses: messaging is
*asynchronous and identity-coupled* (you address an actor, not a rank-in-a-group); actor
creation is dynamic and unbounded; and there are no collective operations. Hoare's CSP paper
explicitly lists "actors (Hewitt)" among the prior program structures it is responding to
[Hoare 1978]. Actor is also **not** in the MPI-1 bibliography [MPIForum 1995]. The interesting
observation for AgentMPI is that contemporary multi-agent harnesses are natively Actor-shaped
(dynamic, named, asynchronous), which is exactly the shape MPI-1 rejected and MPI-2 partially
readmitted via `MPI_Comm_spawn`/`connect`/`accept`.

**Linda, Gelernter 1985.** "Generative communication in Linda," ACM TOPLAS 7(1):80–112
[Gelernter 1985]. Linda decouples communication in both space and time via an associative *tuple
space* with `out`/`in`/`rd`/`eval`: messages "exist as named, independent entities until some process
chooses to receive them," and the model "is fully distributed in space and distributed in time"
[Gelernter 1985]. Relevance to MPI is mostly by contrast — MPI has no persistent shared medium, no
content-addressable matching, and no anonymous rendezvous — with one exception: p4 had a Linda
implementation (p4-Linda), so the Argonne group knew the model well [Butler & Lusk 1994]. Linda's
`nxtval`-like `in`/`out` on a counter tuple is the same idiom TCGMSG needed and MPI-1 lacked.
Linda is not in the MPI-1 bibliography [MPIForum 1995].

---

## 2. The MPI Forum process

### 2.1 Timeline of formation

| Date | Event | Source |
|---|---|---|
| Apr 29–30, 1992 | First CRPC "Workshop on Standards for Message Passing in a Distributed Memory Environment," Hilton Conference Center, Williamsburg, VA. 68 attendees incl. major hardware and software vendors. A working group is established. | [Walker 1992] |
| Aug 1992 | Workshop report published as ORNL/TM-12147 (Walker) | [Walker 1992] |
| Nov 1992 | Preliminary draft **"MPI1"** put forward by Dongarra, Hempel, Hey, and Walker; point-to-point only, no collectives, not thread-safe; explicitly intended to "get the ball rolling" | [MPIForum 1995] |
| Nov 1992 | MPI working group meeting, Minneapolis: decision to formalize the process and "generally adopt the procedures and organization of the High Performance Fortran Forum"; subcommittees formed per component area, each with an email list; target set of a draft standard by Fall 1993 | [MPIForum 1995] |
| Feb 1993 | Revised MPI1 draft completed, published as ORNL/TM-12231 | [Dongarra et al. 1993] |
| Jan 1993 – 1994 | MPIF meets **every 6 weeks for two days** through the first 9 months of 1993; participation open to the whole HPC community | [MPIForum 1995] |
| Nov 1993 | **Draft MPI standard presented at Supercomputing '93** | [MPIForum 1995] |
| May 5, 1994 | MPI-1.0 document date | [MPIForum 2015; MPIForum 2025] |
| 1994 | MPI-1 published as a journal special issue, *Int. J. Supercomputer Applications* 8(3/4):165–414 | [MPIForum 1994] |

**Correcting a common error.** The task brief refers to a "'Supercomputing 93' release of MPI-1.0
(May 1993 draft / June 1994 final)." The standard's own account is different and should be used: the
*draft* was **presented** at SC'93 in **November 1993** (not released as 1.0), and the final MPI-1.0
document is dated **May 5, 1994** [MPIForum 1995; MPIForum 2015]. I found no evidence of a "May
1993" draft; the pre-Forum draft dates are November 1992 and February 1993 [MPIForum 1995;
Dongarra et al. 1993]. `[UNVERIFIED]` — no source supports a May 1993 MPI-1.0 draft.

### 2.2 Scale and funding

MPI-1 involved "about 60 people from 40 organizations mainly from the United States and Europe"
per the standard [MPIForum 1995]; the CACM overview says "more than 80 people from approximately 40
organizations" over "a 12-month period in 1993–1994" [Dongarra et al. 1996]. Both are the Forum's own
numbers and they conflict; cite whichever with attribution. Funding was thin: "The MPI meetings
operated on a tight budget (actually no budget when the first meeting was announced). DARPA provided
partial travel support for U.S. academic participants through the National Science Foundation.
Support for several European participants was provided by the European Commission through its Esprit
program" [Dongarra et al. 1996]. The standard's acknowledgements record ARPA and NSF grant
ASC-9310330, NSF STC Cooperative Agreement CCR-8809615, and Esprit project P6643 (PPPE)
[MPIForum 2025]. The University of Tennessee and ORNL distributed the draft by anonymous FTP and
mail servers [MPIForum 2025].

### 2.3 Governance: original rules

The MPI-1 rules, as stated by four Forum principals:

> "Formal voting at the meetings was by a single vote per organization; in order to vote, an
> organization needed to have had at least one representative at two of the last three meetings. To
> provide guidance for preparing formal proposals, frequent informal votes including all those
> present were held." [Dongarra et al. 1996]

Richard Graham's later Forum-overview slides give the MPI-2.2/3.0-era elaboration: one vote per
organization, present at the meeting when the vote is taken; eligibility requires presence at two of
the last three meetings; **"Votes are taken twice, at separate meetings"**, each preceded by a
"reading" at an earlier meeting at which straw votes may be taken; measures pass on **simple
majority**; and **"Only items consistent with the charter can be considered"** [Graham n.d.].
Committee rules in the same deck: a minimum of 4 organizations must support a proposal;
**"Semantics before API"**; and **"Need prototype implementation, with source code, for a given
proposed feature. Ideally, this would be in one of the widely used Open Source implementations, such
as MPICH and/or Open MPI"** [Graham n.d.]. The Graham deck is undated in the copy I retrieved, so
its exact vintage is `[UNVERIFIED]`; treat it as MPI-3-era.

That prototype-implementation requirement is the operational teeth of "standardize existing
practice," and the AgentMPI paper should note it: the Forum's rule is not "no research" in the
abstract but "no unimplemented feature."

### 2.4 Governance: current rules

The Forum's current written procedures are considerably more formal than the MPI-1 folkways
[MPIForum Procedures]:

- **Overall Organization Eligibility (OOE):** an organization is eligible to vote if it registered
  for and had one or more representatives present at **two out of the last three voting MPI Forum
  meetings (including the current meeting)**.
- **Individual Meeting Organization Voting Eligibility (IMOVE):** requires a registered
  representative at that meeting.
- **Meeting Quorum:** established when **more than 2/3 of OOE organizations** have registered for
  the meeting.
- **Individual Ballot Quorum:** established when **more than 3/4 of IMOVE organizations** at the
  meeting cast a vote (as opposed to abstaining), counted at the beginning of each ballot.
- **Passage:** a ballot passes if it meets individual ballot quorum **and** the number of "yes" votes
  is **more than 3/4 of the sum of "yes" and "no" votes**.
- **Procedure:** a **formal reading** at a quorate voting meeting must precede the first ballot, with
  the proposal text published at least two weeks ahead; there is "no criteria for 'passing' or
  'failing' a formal reading"; **two ballots at separate meetings** are required, and if either fails
  its quorum the proposal starts over from a formal reading.
- **No proxies**; absence at ballot time is an implicit abstention (in the earlier v2.2 text)
  [MPIForum 2022].
- Chapters have standing **Chapter Committees** with a Chapter Chair ("Chapter Author") responsible
  for integrating and reviewing approved changes, plus an explicit release cadence including a
  Release Candidate Meeting [MPIForum Procedures].

**The supermajority migrated upward.** Graham's slides say "simple majority" [Graham n.d.]; the
current procedures require >3/4 of yes+no [MPIForum Procedures]. The v2.2 procedures already state
the 3/4 rule [MPIForum 2022], so the tightening happened between the Graham deck and procedures
v2.2; the exact date of that change is `[UNVERIFIED]`.

### 2.5 Key people, by role

MPI-1.0/1.1 primary coordinators, verbatim from the standard's acknowledgements
[MPIForum 2025]:

- **Jack Dongarra, David Walker** — Conveners and Meeting Chairs
- **Ewing Lusk, Bob Knighten** — Minutes
- **Marc Snir, William Gropp, Ewing Lusk** — Point-to-Point Communication
- **Al Geist, Marc Snir, Steve Otto** — Collective Communication
- **Steve Otto** — Editor
- **Rolf Hempel** — Process Topologies
- **Ewing Lusk** — Language Binding
- **William Gropp** — Environmental Management
- **James Cownie** — Profiling
- **Tony Skjellum, Lyndon Clarke, Marc Snir, Richard Littlefield, Mark Sears** — Groups, Contexts,
  and Communicators
- **Steven Huss-Lederman** — Initial Implementation Subset

Note the mapping from precursor to person: Hempel (PARMACS) got topologies; Skjellum (Zipcode) and
Clarke (CHIMP) got contexts and communicators; Snir (IBM Watson) got point-to-point and collectives;
Lusk (p4) got language bindings; Cownie got profiling. The Forum's structure *encoded* the precursor
systems. **Bill Saphir** does not appear in the MPI-1 coordinator list or the named-participant table
in the source I retrieved; his documented role (later Forum work, notably as an MPI-2 contributor) is
`[UNVERIFIED]` here.

For contrast, the **MPI-5.0** organization [MPIForum 2025]: William Gropp (Editor, Steering
Committee, Front Matter, Introduction, One-Sided, Bibliography), Martin Schulz (Chair, Info Object,
External Interfaces), Wes Bland (Secretary), Brian Smith (Treasurer), Purushotham V. Bangalore
(Language Bindings), Claudia Blaas-Schenner (Terms and Conventions), George Bosilca (Datatypes,
Environmental Management), Ryan E. Grant (Partitioned Communication), Marc-André Hermanns (Tool
Support), Tobias Haas (Change-Log), **Jeff Hammond (ABI)**, Dan Holmes (Point-to-Point, Sessions),
Guillaume Mercier (Groups, Contexts, Communicators, Caching), Christoph Niethammer (Process
Topologies), Howard Pritchard (Process Creation and Management), Anthony Skjellum (Collective
Communication, I/O). **Skjellum and Gropp span 1992–2025**, a 33-year continuity that is itself a
finding. MPI-5.0 working-group leads include ABI (Hammond, Quincey Koziol), Fault Tolerance
(Aurélien Bouteiller, Ignacio Laguna), Hybrid & Accelerator (James Dinan), RMA (Joseph Schuchart,
Gropp), Sessions (Pritchard, Holmes), Tools (Hermanns, Bill Williams, Joachim Jenke)
[MPIForum 2025].

---

## 3. Version-by-version evolution, with dates

The Forum's own consolidated version list [MPIForum 2025; MPIForum Docs]:

| Version | Document date | Approval date (Forum) | Length |
|---|---|---|---|
| MPI-1.0 | May 5, 1994 | — | ~228 pp. |
| MPI-1.1 | June 12, 1995 | — | ~238 pp. |
| MPI-1.2 | July 18, 1997 | — | (published inside MPI-2.0) |
| MPI-2.0 | July 18, 1997 | — | ~608 pp. |
| MPI-1.3 | May 30, 2008 | Sept 4, 2008 (2nd/final vote; 1st vote July 1, 2008) | — |
| MPI-2.1 | June 23, 2008 | Sept 4, 2008 | ~608 pp. |
| MPI-2.2 | Sept 4, 2009 | Sept 4, 2009 | ~647 pp. |
| MPI-3.0 | Sept 21, 2012 | Sept 21, 2012 | ~852 pp. |
| MPI-3.1 | June 4, 2015 | June 4, 2015 | ~868 pp. |
| MPI-4.0 | June 9, 2021 | June 9, 2021 | ~1139 pp. |
| MPI-4.1 | Nov 2, 2023 | Nov 2, 2023 | ~1166 pp. |
| MPI-5.0 | June 5, 2025 | June 5, 2025 | ~1189 pp. |

Page counts are from Schulz's "State of MPI" talk [Schulz 2026], which gives MPI-1.1 as "Nov 1995"
and MPI-2.0 as "Nov 1997" — both inconsistent with the Forum documents (June 12, 1995 and July 18,
1997) [MPIForum 2015]. **Prefer the Forum document dates; treat Schulz's page counts as the
citable figures and his dates as approximate.** Note also that the MPI-5.0 HTML rendering carries
the footer "MPI-5.0 of June 9, 2025" while the Forum's docs page and the standard's own version
history say **June 5, 2025** [MPIForum 2025; MPIForum Docs]; the June 9 footer appears to be a
carry-over artifact and the approval date is June 5, 2025. Also: the LLNL tutorial states "Sep 2023:
The MPI-4.0 standard was approved," which is simply wrong — MPI-4.0 was approved June 9, 2021
[LLNL Tutorial; MPIForum Docs]. Do not cite LLNL for dates.

**MPI-1.3 / MPI-2.1 ordering caution.** MPI-1.3 (May 30, 2008) *combines* MPI-1.1 and MPI-1.2 plus
errata into one historical document; MPI-2.1 (June 23, 2008) then combines MPI-1.3 and MPI-2.0
[MPIForum 2008]. So MPI-1.3 is a 2008 consolidation of 1990s content, not a 1990s release — a
frequent citation error.

### 3.1 MPI-1.x: the core

MPI-1.0/1.1 established: **point-to-point communication** with four send modes (standard, buffered,
synchronous, ready) each in blocking and nonblocking form, plus persistent requests;
**communicators, contexts, and groups** (§4d); **derived datatypes** describing both type and
noncontiguous layout; **collective communication** with group scope; **process topologies**
(Cartesian and general graph); the **profiling interface (PMPI)**; and **error handling** with
attachable error handlers [MPIForum 1995; Dongarra et al. 1996]. The CACM overview names as advances
beyond then-current practice "user-defined datatypes, persistent communication ports, powerful
collective communication operations, and scoping mechanisms for communication," adding: "No previous
system incorporated all these features" [Dongarra et al. 1996]. MPI-1.1 (June 12, 1995) is
corrections and clarifications only — "The changes from Version 1.0 are minor" [MPIForum 1995].
MPI-1.2 (July 18, 1997) is clarifications and small additions, published as part of the MPI-2
document [MPIForum 2008].

### 3.2 MPI-2.0 (July 18, 1997)

Focused on "process creation and management, one-sided communications, extended collective
communications, external interfaces and parallel I/O," with a miscellany chapter covering "in
particular language interoperability" [MPIForum 2008]. Specifically:

- **Dynamic process management**: `MPI_Comm_spawn`, `MPI_Comm_connect`, `MPI_Comm_accept` — the PVM
  capability MPI-1 declined.
- **One-sided / RMA**: `MPI_Win` windows, `MPI_Put`/`MPI_Get`/`MPI_Accumulate`, with three
  synchronization regimes — fence (`MPI_Win_fence`), generalized active target
  post/start/complete/wait (PSCW), and passive target `MPI_Win_lock`/`unlock`.
- **MPI-IO**: parallel I/O with file views built from MPI datatypes, collective and independent
  operations, and data representations.
- **C++ bindings** (later removed; see §3.4).
- **Thread support levels**: `MPI_Init_thread` and the four levels `MPI_THREAD_SINGLE`,
  `MPI_THREAD_FUNNELED`, `MPI_THREAD_SERIALIZED`, `MPI_THREAD_MULTIPLE`, with the user requesting a
  level and the implementation reporting what it provides — "the idea being that the implementation
  need not incur the cost for a higher level of thread safety than the user needs"
  [Balaji et al. 2010]. Under `MPI_THREAD_MULTIPLE`, concurrent calls behave "as if the calls
  executed sequentially in some (any) order," and blocking calls block only the calling thread
  [Balaji et al. 2010].
- **Extended collectives** (e.g. collective operations on intercommunicators) and **language
  interoperability**.

MPI-2 was harder to implement than MPI-1: "The MPI-2 spec, started in '95 and released in '97, was
much more difficult to implement, and we did not track the development" — MPICH1 tracked MPI-1
release-by-release but MPICH2 was written from scratch afterward [Gropp & Lusk 2012].

### 3.3 MPI-2.1 (June 23, 2008) and MPI-2.2 (Sept 4, 2009)

MPI-2.1 is a consolidation: it merges MPI-1.3 and MPI-2.0 into a single document, folding parts of
MPI-2.0's Miscellany and Extended Collective Operations chapters into the MPI-1.3 chapters, and adds
collected errata [MPIForum 2008]. MPI-2.2 "added additional clarifications and seven new routines"
[MPIForum 2015]. The nine-year gap between MPI-2.0 and MPI-2.1 is itself notable: "MPICH2 became a
major research vehicle for research in parallel computing implementation, while the MPI standard
remained relatively static for 10 years" [Gropp & Lusk 2012].

### 3.4 MPI-3.0 (Sept 21, 2012)

The Forum's own characterization: "significant extensions to MPI functionality, including nonblocking
collectives, new one-sided communication operations, and Fortran 2008 bindings. Unlike MPI-2.2, this
standard is considered a major update… As with previous versions, new features have been adopted only
when there were compelling needs for the users" [MPIForum 2015]. From the change log
[MPIForum 2015]:

- **Nonblocking collectives**: "Added nonblocking interfaces to all collective operations"
  (`MPI_Ibcast` etc.). Ticket #109 was the first proposal voted into MPI-3.0, in October 2010, and
  "was used to define much of the process for MPI-3.0" [Hoefler 2012].
- **Neighborhood collectives**: `MPI_NEIGHBOR_ALLGATHER(V)`, `MPI_NEIGHBOR_ALLTOALL(V,W)` and
  nonblocking `MPI_INEIGHBOR_*`, "to support sparse communication on virtual topology grids"
  [MPIForum 2015].
- **New RMA**: `MPI_Win_allocate`, `MPI_Win_allocate_shared` (a shared-memory segment mapped into
  all participating processes' address spaces), `MPI_Win_create_dynamic` with `MPI_Win_attach`/
  `MPI_Win_detach`, **request-based RMA** (`MPI_Rput` etc., returning request handles),
  **flush** operations, `MPI_Win_sync`, and RMA atomics. Dinan et al. summarize the motivation:
  MPI-2 RMA had no request-generating operations, no dynamic window memory, and no shared-memory
  path [Dinan et al. 2016].
- **`MPI_Comm_split_type`** with `MPI_COMM_TYPE_SHARED` — split a communicator into
  shared-memory-capable subcommunicators; the enabler for MPI-3 shared-memory windows
  [MPIForum 2015; Hoefler 2012].
- **Noncollective communicator creation** (`MPI_Comm_create_group`) [Hoefler 2012].
- **MPI_T tool information interface**: programmatic access to implementation control variables and
  performance variables.
- **Fortran 2008 bindings** via the `mpi_f08` module, with choice buffers "assumed-type and
  assumed-rank according to Fortran 2008 TS 29113," `MPI_SUBARRAYS_SUPPORTED`, compile-time argument
  checking, and unique handle types; `mpif.h` "strongly discouraged" [MPIForum 2015; Rabenseifner
  2013].
- **Removal of the C++ bindings**: "The C++ bindings were removed from the standard… This change may
  affect backward compatibility" [MPIForum 2023].
- Also: matched probe (`MPI_Mprobe`/`MPI_Mrecv`) for thread-safe probing, `MPI_Type_create_hindexed_block`,
  and large-datatype support groundwork [Hoefler 2012].

### 3.5 MPI-3.1 (June 4, 2015)

"Adds clarifications and minor extensions to MPI-3.0" [MPIForum 2015]. Notable additions include
nonblocking collective I/O and `MPI_Aint` arithmetic helpers `[UNVERIFIED — I did not retrieve the
MPI-3.1 change log itself]`.

### 3.6 MPI-4.0 (June 9, 2021)

The Forum's summary: "a major update… The largest changes are the addition of large-count versions of
many routines to address the limitations of using an int or INTEGER for the count parameter,
persistent collectives, partitioned communications, an alternative way to initialize MPI, application
info assertions, and improvements to the definitions of error handling" [MPIForum 2025]. From the
MPI-4.0 change log and the Forum's SC20 BoF [MPIForum 2023; MPIForum 2020]:

- **Big count / "embiggenment"**: new `MPI_*_c` large-count functions in C and Fortran `mpi_f08`
  overloads, plus large-count callbacks `MPI_User_function_c`, `MPI_Datarep_conversion_function_c`,
  and `MPI_CONVERSION_FN_NULL_C`.
- **Persistent collectives**: `MPI_ALLGATHER_INIT` etc., including **persistent neighborhood**
  collectives `MPI_NEIGHBOR_*_INIT`. Semantics: initialization is non-local and all communicator
  members must call it; the request may be started zero or more times with `MPI_Start`/`MPI_Startall`;
  ordering constraints apply to the `_init` calls, not the `MPI_Start` calls; the `info` argument
  carries implementation-specific optimization hints [Pritchard et al. 2023]. Voted in at the
  Barcelona Forum meeting, September 2018 [Pritchard et al. 2023].
- **Partitioned point-to-point communication** (new chapter): `MPI_PSEND_INIT`/`MPI_PRECV_INIT`,
  `MPI_PREADY`, `MPI_PARRIVED`. A persistent buffer is partitioned into chunks; the user marks a
  partition ready and must not then modify it. Designed to be "thread agnostic with a minimal
  synchronization overhead" and to enable "early bird communication" where data transfers before the
  last compute thread finishes — explicitly targeted at accelerator use
  [MPIForum 2020; Pritchard et al. 2023].
- **MPI Sessions**: `MPI_SESSION_INIT`/`MPI_SESSION_FINALIZE`, `MPI_GROUP_FROM_SESSION_PSET`,
  `MPI_COMM_CREATE_FROM_GROUP`, `MPI_INTERCOMM_CREATE_FROM_GROUPS`, plus the `MPI_Session` handle
  type and session error handlers [MPIForum 2023]. The motivation is to remove the dependence on
  `MPI_COMM_WORLD`, viewed as an exascale scalability bottleneck, by letting applications derive
  groups from runtime-provided **process sets** [Holmes et al. 2016; Zhou et al. 2026].
- **Error handling improvements**: new error handler `MPI_ERRORS_ABORT`; new classes
  `MPI_ERR_VALUE_TOO_LARGE`, `MPI_ERR_PROC_ABORTED`; the clarification that `MPI_SUCCESS` indicates
  only the result of the operation, not the state of the MPI library; non-object calls (e.g.
  `MPI_ALLOC_MEM`) now raise errors on `MPI_COMM_SELF` rather than `MPI_COMM_WORLD`; the ability to
  set the default error handler at `mpiexec` time [MPIForum 2020; MPIForum 2023]. These are the
  **fault-tolerance-adjacent** additions: they localize failure impact without adopting a full
  fault-tolerance model.
- **`MPI_Comm_idup_with_info`**: info-key propagation was removed from `MPI_Comm_dup`, and this new
  nonblocking constructor gives explicit control over the info attached to the duplicate
  [MPIForum 2023; MPIForum 2020].
- **Topology / hardware info**: two new `MPI_Comm_split_type` values,
  `MPI_COMM_TYPE_HW_GUIDED` (info key names the hardware level) and `MPI_COMM_TYPE_HW_UNGUIDED`
  (iteratively descend the hardware hierarchy from the input communicator to the leaf)
  [MPIForum 2020; MPICH CHANGES].
- Also: `MPI_Isendrecv`/`MPI_Isendrecv_replace`, `MPI_Info_get_string`, MPI_T **events**
  [MPICH CHANGES; MPIForum 2020].

### 3.7 MPI-4.1 (Nov 2, 2023)

"Mostly corrections and clarifications to the MPI-4.0 document. Several routines, the attribute key
`MPI_HOST`, and the `mpif.h` Fortran include file are deprecated" [MPIForum 2025]. Schulz's summary
adds: terminology cleanup, an automatic buffer for `MPI_Bsend`, and **support for different memory
kinds, including GPU memory** [Schulz 2026] — the `mpi_memory_alloc_kinds` info key, whose semantics
MPI-5.0 then clarified [MPIForum 2025]. The MPI-4.1 errata list is long and detailed, including a
fix to an MPI-4.0 partitioned-communication example that could deadlock [MPIForum 2025].

### 3.8 MPI-5.0 (June 5, 2025) — released

**MPI-5.0 has been released.** "MPI-5.0 was approved by the MPI Forum on June 5, 2025"
[MPIForum Docs]; the standard describes itself as "a major update" [MPIForum 2025]. Its headline
content is a **single feature: a standard Application Binary Interface**. The change log
[MPIForum 2025]:

1. Fixed-size Fortran logical datatypes added.
2. A restriction on the provenance of the process-set name given to `MPI_GROUP_FROM_SESSION_PSET`
   removed.
3. **"Chapter Application Binary Interface (ABI) was added,"** defining `MPI_ABI_GET_VERSION` and
   `MPI_ABI_GET_INFO`, the layout of the `MPI_Status` object, and the type of MPI object handles
   (e.g. `MPI_Comm`), with extensive additions to the Defined Constants section giving literal values
   for many MPI constants.
4. A **Fortran Type Registration** section adding `MPI_ABI_SET_FORTRAN_INFO`,
   `MPI_ABI_GET_FORTRAN_INFO`, `MPI_ABI_SET_FORTRAN_BOOLEANS`, the error code `MPI_ERR_ABI`, sections
   on the MPI ABI Fortran modules and shared library, the Status object, and **handle serialization**
   "to obviate the need for `MPI_Fint`."

The ABI is **Chapter 21** of MPI-5.0 in the Forum's HTML rendering [MPIForum 2025 §21]. (An Open MPI
tracking issue refers to it as "Chapter 20"; the Forum document says 21 — prefer 21
[MPIForum 2025 §21].) Mechanics: the ABI header must be named `mpi.h` and the library `mpi_abi`
(e.g. `libmpi_abi.so`); "ABI-compliant implementations must not require more than `mpi_abi` or its
versioned variant as the sole direct dependency of the application binary"; applications must not mix
ABIs; and the ABI-associated `mpi.h` **excludes everything deprecated in MPI-3.1 or earlier**, with
the stated rationale that including deprecated features would make their eventual deletion an
ABI break [MPIForum 2025 §21.2.1]. Type layouts, symbol names and calling conventions "behave as if
they have been compiled with the system C compiler toolchain" [MPIForum 2025 §21.3.6]. The ABI is
versioned independently of the API, starting at 1.0 [Hammond & Dalcin 2025].

The problem it solves, in the ABI working group's own framing: "MPI is an API standard… The compiled
representation of MPI features is implementation-defined. If you compile with one of the following
MPI families, you MUST run with the same: (1) MPICH / Intel MPI / MVAPICH / Cray MPI; (2) Open MPI /
NVIDIA HPC-X / Amazon MPI / IBM Spectrum MPI" — and family 1 exists only because of ISV demand for
Intel MPI interoperability, while family 2 "is not guaranteed to be consistent, especially across
major versions" [Hammond & Dalcin 2025]. Authors of the ABI chapter: **Jeff Hammond and Lisandro
Dalcin**, with Hammond and Quincey Koziol leading the ABI working group [Hammond & Dalcin 2025;
MPIForum 2025]. Status at release: MPICH had implemented it and it was "heavily tested by mpi4py";
Open MPI's implementation was in flight; the shim layers Mukautuva, wi4mpi and MPItrampoline "can
support this immediately" [Hammond & Dalcin 2025]. Open MPI now ships `libmpi_abi` alongside its
native `libmpi` by default, with `mpicc_abi` and an `ompi-abi-c` pkg-config file
[Open MPI ABI docs].

MPI-5.0 was "previously targeted as MPI 4.2" and renumbered to reflect the ABI's significance
[Schulz 2026].

---

## 4. Design philosophy

### 4.1 The stated goals, verbatim

MPI-1.1 §1.1: "The goal of the Message Passing Interface simply stated is to develop a widely used
standard for writing message-passing programs. As such the interface should establish a practical,
portable, efficient, and flexible standard for message passing." The complete goal list, verbatim
[MPIForum 1995]:

> - Design an application programming interface (not necessarily for compilers or a system
>   implementation library).
> - Allow efficient communication: Avoid memory-to-memory copying and allow overlap of computation
>   and communication and offload to communication co-processor, where available.
> - Allow for implementations that can be used in a heterogeneous environment.
> - Allow convenient C and Fortran 77 bindings for the interface.
> - Assume a reliable communication interface: the user need not cope with communication failures.
>   Such failures are dealt with by the underlying communication subsystem.
> - Define an interface that is not too different from current practice, such as PVM, NX, Express,
>   p4, etc., and provides extensions that allow greater flexibility.
> - Define an interface that can be implemented on many vendor's platforms, with no significant
>   changes in the underlying communication and system software.
> - Semantics of the interface should be language independent.
> - The interface should be designed to allow for thread-safety.

Two of these deserve emphasis for the AgentMPI paper. **"Assume a reliable communication interface:
the user need not cope with communication failures"** is a load-bearing simplification that MPI has
been paying for ever since — it is the root of MPI's fault-tolerance difficulties (§5.4) and is
almost certainly *wrong* for LLM agent harnesses, where individual agent failures are routine.
**"The interface should be designed to allow for thread-safety"** — designed to *allow*, not
required to *provide*: MPI-1 was not thread-safe, and the actual mechanism arrived in MPI-2 as
opt-in levels.

### 4.2 (a) MPI is a specification, not an implementation

MPICH's authors state it plainly: "MPI (Message Passing Interface) is a specification for a standard
library for message passing that was defined by the MPI Forum" [Gropp et al. 1996]. The LLNL tutorial
puts it as "MPI is a specification for the developers and users of message passing libraries"
[LLNL Tutorial]. The consequences are structural: multiple competing implementations of the same
source-level contract; vendors free to "exploit native hardware features"; and — until MPI-5.0 — **no
binary compatibility whatever**, which is exactly the gap the ABI closes (§3.8). The MPI-1 goal
"Design an application programming interface (not necessarily for compilers or a system
implementation library)" is the decision that makes this true [MPIForum 1995]. The Forum also states
that MPIF "is not sanctioned or supported by any official standards organization"
[MPIForum 2008] — MPI's authority is entirely de facto.

### 4.3 (b) Portability with performance

Gropp's formulation: "Portability, however, does not require taking a lowest common denominator
approach. A good design allows the use of performance enhancing features without mandating them. For
example, the message passing semantics of MPI allows for the direct copy of data from the user's send
buffer to the receive buffer without any other copies. However, systems that can't provide this
direct copy because of hardware limitations or operating system restrictions are permitted under the
MPI model to make one or more copies. Thus MPI programs remain portable while exploiting hardware
capabilities" [Gropp 2001]. This is the key technique and it is imitable: **specify semantics
permissively enough that the fast path is legal but the slow path is also legal.**

Gropp is also candid that MPI does *not* achieve full performance portability, "defined as providing
a single source that runs at near achievable peak performance on all platforms," and argues this is
unreasonable to demand of a parallel model when uniprocessor models cannot deliver it either — citing
the six ways to write matrix–matrix multiply in Fortran, none optimal on cache-based systems, and the
continued existence of hand-tuned BLAS [Gropp 2001]. His later talk names the residual cost: "The
tyranny of 'Common Denominator' approach… Take advantage of community consensus… but stifles
innovation. Features not already adopted are hard to fully [standardize]" [Gropp 2022].

### 4.4 (c) Standardize existing practice

The MPI-1 goal "Define an interface that is not too different from current practice, such as PVM, NX,
Express, p4, etc." [MPIForum 1995], sharpened by Walker into "while it would be imprudent to include
new and untested features in the standard, concepts that have been tested in a research environment
should be considered for inclusion" [Walker 1994], and enforced procedurally by the requirement for a
prototype implementation with source code before a feature can be voted [Graham n.d.]. But the Forum
was *not* purely conservative: "Though much of MPI standardizes the common practice of existing
message-passing systems, MPI goes further to define such advanced features as user-defined datatypes,
persistent communication ports, powerful collective communication operations, and scoping mechanisms
for communication. No previous system incorporated all these features" [Dongarra et al. 1996].

The timing argument is worth quoting for the AgentMPI paper's own framing: "The timing of MPI seems
to have been about right. Trying to establish such a standard earlier might have failed to benefit
from research into multiple approaches. Indeed, some feared that adoption of a standard would shut
down research into the message-passing model. In fact, the opposite happened. Having a fairly
complete, performance-enabling, portable interface target stimulated a wealth of research into
implementation approaches, tool development, and application algorithms" [HPCwire 2017]. Lusk's
version: MPI "has always been a vehicle for computer science research. The Argonne group alone has
published more than 100 peer-reviewed papers on MPI-related topics over that period"
[Argonne 2017].

### 4.5 (d) Safe library composition via communication contexts — **the crucial point**

**The problem.** In a flat tag-based message-passing system, a library's internal messages and the
application's messages live in the same namespace. If a library uses tag 42 and the application also
uses tag 42, or if the application has an outstanding wildcard receive (`MPI_ANY_TAG`,
`MPI_ANY_SOURCE`) when it calls into the library, the library's message can be stolen by the
application's receive or vice versa. Because MPI-style receives are *selective* on
`(source, tag)`, and because tags are a flat integer space with no allocator, there is no
convention-free way for independently-developed libraries to coexist. Gropp: "Without something like
a communicator, it is possible for a message sent by one component and intended for that component to
be received by another component or by user code. MPI made reliable libraries possible" [Gropp 2001].

**Why it mattered.** The reason this was the load-bearing design decision, rather than a nicety, is
that the whole *point* of a portable message-passing standard is to enable a **library ecosystem**.
MPI-1's own introduction to the communicator chapter says so: "Parallel libraries are needed to
encapsulate the distracting complications inherent in parallel implementations of key algorithms.
They help to ensure consistent correctness of such procedures, and provide a 'higher level' of
portability than MPI itself can provide" [MPIForum 1995]. Without contexts, every library would need
a global tag-allocation convention, and no library could be called while communication was pending.

**The requirements MPI-1 enumerates.** §5.1.1 "Features Needed to Support Libraries," verbatim
[MPIForum 1995]:

> - Safe communication space, that guarantees that libraries can communicate as they need to, without
>   conflicting with communication extraneous to the library,
> - Group scope for collective operations, that allow libraries to avoid unnecessarily synchronizing
>   uninvolved processes (potentially running unrelated code),
> - Abstract process naming to allow libraries to describe their communication in terms suitable to
>   their own data structures and algorithms,
> - The ability to "adorn" a set of communicating processes with additional user-defined attributes,
>   such as extra collective operations…
>
> In addition, a unified mechanism or object is needed for conveniently denoting communication
> context, the group of communicating processes, to house abstract process naming, and to store
> adornments.

**The solution.** §5.1.2 lists the five supporting concepts — contexts of communication, groups of
processes, virtual topologies, attribute caching, and communicators — and states that
"Communicators encapsulate all of these ideas in order to provide the appropriate scope for all
communication operations in MPI" [MPIForum 1995]. The context semantics, verbatim:

> "Contexts provide the ability to have separate safe 'universes' of message passing in MPI. A
> context is akin to an additional tag that differentiates messages. The system manages this
> differentiation process. The use of separate communication contexts by distinct libraries (or
> distinct library invocations) insulates communication internal to the library execution from
> external communication. This allows the invocation of the library even if there are pending
> communications on 'other' communicators, and avoids the need to synchronize entry or exit into
> library code. Pending point-to-point communications are also guaranteed not to interfere with
> collective communications within a single communicator." [MPIForum 1995]

Four properties are doing the work here, and an AgentMPI analogue must reproduce all four:

1. **The context is system-allocated, not user-chosen.** "The system manages this differentiation
   process." This is what makes it safe against independently-developed components. In MPICH, "a
   context is allocated through a collective operation over the group of processes involved in
   communicator construction. Through this collective operation, [the processes agree on] a context
   that is currently not in use by any of [them]" — one algorithm being an `MPI_Allreduce` with
   `MPI_MAX` over per-process context bitmaps [Gropp et al. 1996].
2. **Contexts are attached to *both* send and receive.** "Each communicator has a send context and a
   receive context. For intracommunicators, these two contexts are equal; for intercommunicators,
   these contexts may be different… MPI point-to-point operations attach the send_context to all
   outgoing messages and use the recv_context when matching contexts upon receipt"
   [Gropp et al. 1996]. Crucially, there is **no wildcard for context** — `MPI_ANY_SOURCE` and
   `MPI_ANY_TAG` exist, `MPI_ANY_CONTEXT` does not. That absence is the safety property.
3. **No synchronization at library entry/exit** — the property that makes composition cheap.
4. **Point-to-point and collective traffic on the same communicator cannot interfere** — a separate
   guarantee, effectively a second context.

The lineage is Zipcode's "mailer" (§1.3), by way of Skjellum and Clarke on the MPI-1 subgroup
[Skjellum et al. 1994; Gropp 2001; MPIForum 2025], with Feitelson's 1991 "communicators" report as a
naming antecedent [Feitelson 1991].

Gropp also flags the *economy* of the design: "Another sign of the effective design in MPI is the use
of a single concept to solve multiple problems… the MPI communicator both describes the group of
communicating processes and provides a separate communication context that supports component
oriented software" [Gropp 2001].

### 4.6 (e) The SPMD model

MPI standardizes single-program-multiple-data as the default structuring: all processes run the same
program, discover their rank in a group, and branch on it. p4 supported "both master-slave and SPMD
models" [Netlib p4] and CMMD distinguished host/node from hostless [TMC CMMD]; MPI-1 chose the
hostless/SPMD side, with no distinguished host process and no dynamic process creation. Gropp notes
the discipline this imposes: "certain powerful variable layout tricks, such as assuming that the
variable `a` in an SPMD program is at the same address on all processors, must be modified to handle
the case where each process may have a different stack use history and variables may be dynamically
allocated with different base addresses. Some programming models have assumed that all processes have
the same layout of local variables, making it difficult or impossible to use those programming models
with modern adaptive algorithms" [Gropp 2001]. MPI is SPMD but *not* lockstep-symmetric — an
important distinction.

### 4.7 (f) Explicit locality, no shared address space

"MPI addresses the message-passing parallel programming model: data is moved from the address space
of one process to that of another process through cooperative operations on each process"
[LLNL Tutorial]. Gropp's defense is that this matches the hardware: "The separate processes of the
MPI programming model provide a natural and effective match to this property of the hardware," and
even models that hide the distinction, like OpenMP, "have implementations that often require
techniques such as first touch to ensure that operations make effective use of cache" [Gropp 2001].
The model survived the shift to hybrid distributed/shared hardware: "The programming model clearly
remains a distributed memory model however, regardless of the underlying physical architecture of the
machine" [LLNL Tutorial]. Note the tension: MPI-3's `MPI_Win_allocate_shared` partially readmits
shared memory, providing "a complete, portable interprocess shared-memory programming system"
[Dinan et al. 2016] — locality remained explicit, but the no-shared-memory rule did not.

### 4.8 (g) Opaque objects and handles

MPI objects — communicators, groups, datatypes, requests, windows, files, sessions — are manipulated
only through opaque handles (`MPI_Comm`, `MPI_Datatype`, …) whose representation is
implementation-defined. This is precisely why no cross-implementation binary compatibility existed
before MPI-5.0: "`MPI_Datatype` and `MPI_Comm` are unspecified types," realized as
`typedef struct ompi_datatype_t * MPI_Datatype` in the Open MPI family and `typedef int MPI_Datatype`
in MPICH [Schulz 2026; Hammond & Dalcin 2025]. Opacity bought thirty years of implementation freedom
and cost binary portability; MPI-5.0 Chapter 21 buys back the binary portability by fixing "the type
of MPI object handles" and the `MPI_Status` layout [MPIForum 2025]. **This is a clean, dated,
quantifiable instance of the abstraction/ABI tradeoff — extremely useful for the AgentMPI paper.**

### 4.9 (h) A small orthogonal core plus a large convenience layer

MPI-1 had ~128 routines and MPI-2 ~200 more, and this is the standard complexity criticism. Gropp's
rebuttal: "The number of routines is not a relevant measure however. Fortran, for example, has a
large number of intrinsic functions; C and Java rely on a large suite of library routines… A better
measure of complexity is the number of concepts that the user must learn, along with the number of
exceptions and special cases. Measured in these terms MPI is actually very simple. Using MPI requires
learning only a few concepts. Many MPI programs can be written with only a few routines; several
subsets of routines are commonly recommended, including ones with as few as six functions. Note the
plural: for different purposes, different subsets of MPI are used… One key to the success of MPI is
that these subsets can be used without learning the rest of MPI" [Gropp 2001]. The Forum
institutionalized this from the start: **Steven Huss-Lederman's MPI-1 role was literally "Initial
Implementation Subset"** [MPIForum 2025].

The complementary principle is **symmetry**: "wherever possible routines were added to eliminate any
exceptions. An example is the routine `MPI_Issend`… To maintain symmetry, MPI also provides the
nonblocking synchronous send. This send mode is meaningful… but is rarely used. Eliminating it would
have removed a routine, slightly simplifying the MPI documentation and implementation. It would have
created an exception, however… it is easy to forget about a routine that you never use; it is harder
to remember arbitrary decisions on what is and is not available" [Gropp 2001]. Gropp also admits
where symmetry went too far: "A place where MPI may have followed the principle of symmetry too far
is in the large collection of routines for manipulating groups of processes… Another place is in
canceling of sends, where significant implementation complexity is required for an operation of
dubious use" [Gropp 2001]. And empirically: "An early poll of MPI users in fact found that while no
one was using all of the MPI routines, essentially all MPI routines were in use by someone"
[Gropp 2001].

### 4.10 (i) Thread safety and progress

MPI-1 stated only that the interface "should be designed to allow for thread-safety"
[MPIForum 1995]; MPI-2 supplied the four levels (§3.2). The design decision is that thread safety is
**requested, negotiated, and paid for** rather than assumed: "the idea being that the implementation
need not incur the cost for a higher level of thread safety than the user needs" [Balaji et al. 2010].
A portable program that does not call `MPI_Init_thread` must assume only `MPI_THREAD_SINGLE`
[Balaji et al. 2010].

`MPI_THREAD_MULTIPLE`'s semantics — "the outcome will be as if the calls executed sequentially in
some (any) order," with blocking calls blocking only the calling thread — are clean but expensive to
implement [Balaji et al. 2010]. In practice "most MPI implementations use a combination of 'Global'
and 'Lock-free'" critical-section granularity, and "Regardless of the granularity, however,
contention to enter a critical section can still occur, and serialization is inevitable"
[Amer et al. 2015]. Empirically this is the biggest gap between what users want and what they get:
the ECP survey found "high preference for `MPI_THREAD_MULTIPLE`" but cited performance as the barrier
to adoption [Bernholdt et al. 2020]. MPI-4.0's partitioned communication is explicitly a response —
"thread agnostic with a minimal synchronization overhead" [MPIForum 2020].

**Progress** is the subtler issue: MPI's progress semantics are notoriously underspecified, and
MPI-4.1 had to add an erratum clarifying that "a call to `MPI_WIN_SYNC` does not complete pending RMA
operations and… does not guarantee any progress of MPI operations" [MPIForum 2025]. **A full
treatment of MPI's progress rule is `[UNVERIFIED]` here** — I did not retrieve the standard's
progress section or Skjellum's "About Progress" analysis, though his 25-years talk devotes a section
to it and to a taxonomy of implementation progress models attributed to Dimitrov
[Skjellum 2017].

### 4.11 (j) "MPI is the assembly language of parallel computing"

The phrase is folklore with no single provenance. The earliest attribution I could verify is to
**Brad Chamberlain (Cray), 2000**: "MPI is often considered the 'portable assembly language' of
parallel computing" [Heroux 2010, quoting Chamberlain]. Pacheco's textbook uses "MPI can be thought
of as 'the assembly language of parallel computing,' because of this generality"
[Pacheco, via BYU ACME]. Eadline's variant: "MPI has often been called the machine code for parallel
computers. I would have to agree. It is portable, powerful, and unfortunately, in my opinion, too
close to the wires for everyday programming" [Eadline n.d.]. A related sharpening from the PGAS
community is Bonachea & Duell's argument that MPI 1.1/2.0 are inadequate as a "portable network
assembly language," i.e. as a *compilation target* for global-address-space languages, because
simulating one-sided access over two-sided MPI is too expensive and MPI-2 RMA imposes memory-access
restrictions the language must expose [Bonachea & Duell 2004].

**Gropp's response**, which the AgentMPI paper should quote if it uses the epithet at all: "MPI is
sometimes called the assembly language of parallel programming. Those making this statement forget
that C and Fortran have also been described as portable assembly languages. The generality of the
approach should not be mistaken for an unnecessary complexity" [Gropp 2001]. And, later and more
pointedly: "Saying that MPI is the problem is like saying C (or C++) is the problem, and if we just
eliminated MPI (or C or C++) in favor of a high productivity framework everyone's problems would be
solved. In some ways, MPI is too usable — many people can get their work done with it, which has
reduced the market for other tools" [Gropp 2022].

---

## 5. Retrospectives and critiques

### 5.1 The canonical retrospective: Gropp, "Learning from the Success of MPI" (2001)

HiPC 2001 keynote (delivered under the title "Whither MPI: Lessons From and the Future of MPI"),
published in LNCS 2228 [Gropp 2001; HiPC 2001]. **Pagination conflict**: Springer's own page gives
81–92, DBLP gives 81–94 [Springer HiPC 2001; BibSonomy 2001]. A preprint is on arXiv as
`cs/0109017` [Gropp 2001].

The thesis: "six requirements must all be satisfied for a parallel programming model to succeed"
[Gropp 2001]. The six, as Gropp himself lists them: **Portability, Performance, Simplicity and
Symmetry, Modularity, Composability, Completeness** [Gropp 2022; Gropp 2001]. Note that some
secondary summaries render this as a different six by splitting "simplicity and symmetry" and
dropping "composability" — use Gropp's own enumeration. His conclusion on each: portability and
performance are obviously required; "Simplicity and symmetry cater to the user and make it easy to
learn and use safely"; "Composibility is required to prevent the approach from being left behind by
the advance of other tools such as compilers and debuggers"; "Modularity, like completeness, is
required to ensure that tools can be built on top of the programming model. Without modularity a
programming model is suitable only for turnkey applications"; and "Completeness… is required to
ensure that the model supports a large enough community." He credits the process itself: "The open
standards process… was an important component in its success" [Gropp 2001].

On completeness he is explicit about the tradeoff other models made: "Some parallel programming
models have sacrificed completeness for simplicity. For example, a number of programming models have
required that synchronization happens only collectively for all processes or tasks… Such applications
are difficult if not impossible to build using restrictive programming models. Another way to look at
this is that while many programs may not be easy under MPI, no program is impossible"
[Gropp 2001]. **This is the single most directly transferable argument for AgentMPI.**

### 5.2 Other named retrospectives and surveys

- **Dongarra, Otto, Snir & Walker, "A message passing standard for MPP and workstations," CACM
  39(7):84–90, July 1996** [Dongarra et al. 1996]. The authoritative short account of the process,
  the governance rules, and the goals, by four Forum principals.
- **Walker, "The design of a standard message passing interface for distributed memory concurrent
  computers"** [Walker 1994] — the design-rationale paper, and the source for the
  "tested-in-research-not-untested" inclusion rule.
- **Gropp, Lusk, Doss & Skjellum, "A high-performance, portable implementation of the MPI
  message-passing interface standard," Parallel Computing 22(6):789–828, 1996** [Gropp et al. 1996].
  The MPICH paper; also the best single source on what MPICH borrowed from Zipcode and Chameleon and
  on how contexts are actually allocated.
- **Balaji, Gropp, Lusk, Thakur et al., "Translational research in the MPICH project," J.
  Computational Science, 2020** [Balaji et al. 2020]. Thirty-year view: MPICH "began in 1992 as an
  effort to develop a portable, high-performance implementation of the emerging MPI Standard. It has
  enabled the widespread adoption of MPI… Today, most supercomputing vendors use MPICH in order to
  develop their own proprietary implementation of MPI." Names the ADI as the key design feature:
  "Higher-level MPI features were implemented portably on top of the ADI, and only the ADI needed to
  be implemented and tuned separately for different platforms and networks. This design enabled
  vendors to take the MPICH code and easily tune it for their platforms."
- **Thakur, Balaji, Buntinas et al., "MPI at Exascale," SciDAC 2010** [Thakur et al. 2010]. On
  scaling MPI to millions of cores: memory consumption of communicator/group state, the linear
  scaling of process-startup, collective algorithm scalability. `[UNVERIFIED]` — I confirmed the
  paper's existence, authors and venue via citations but did not retrieve the full text; the exact
  author list and pagination should be checked before final citation.
- **Gropp & Snir, "Programming for Exascale Computers," Computing in Science & Engineering
  15(6):27–35, 2013** [Gropp & Snir 2013]. Argues that "MPI+OpenMP supports this model: each node
  becomes an MPI process that executes an OpenMP program," while warning that exascale concurrency,
  power and heterogeneity may require more. Secondary summaries note the specific MPI scalability
  concerns: communicator description memory and non-scalable (linear) startup
  [Gropp & Snir 2013; Georgiou et al. 2018].
- **Bernholdt, Boehm, Bosilca, Gorentla Venkata, Grant, Naughton, Pritchard, Schulz & Vallée, "A
  survey of MPI usage in the U.S. exascale computing project," Concurrency and Computation: Practice
  and Experience 32(3):e4851, 2020** [Bernholdt et al. 2020]; ECP milestone report version
  ORNL/SPR-2018/790 [Bernholdt et al. 2018]. Of 97 active ECP projects surveyed (2017), 77 responded
  and **56 reported using MPI**. Findings: point-to-point and collectives dominate actual usage;
  strong stated preference for `MPI_THREAD_MULTIPLE` blocked by performance; significant demand for
  better GPU integration, including MPI calls from within GPU kernels; latency and collectives named
  as top optimization priorities. **This is the best hard-data citation on what MPI features are
  actually used** — directly useful if AgentMPI wants to argue about core vs. periphery.
- **DOE ASCR Exascale Programming Challenges workshop report** [Amarasinghe et al. 2011]. The
  official statement of the MPI+X position and its limits: "MPI can be used as the software substrate
  for inter-node communication, but will need to be extended to support resilience, increasing
  importance of topology-aware communication… the shrinking memory space per core and the likelihood
  of heterogeneous compute nodes with accelerators make MPI impractical as the intra-node programming
  model for exascale."
- **Snir, Otto, Huss-Lederman, Walker & Dongarra, *MPI: The Complete Reference*, MIT Press, 1996;
  2nd ed. 1998 in two volumes** (Vol. 1 *The MPI Core*, 426 pp.; Vol. 2 *The MPI Extensions*, 344
  pp.) [Snir et al. 1996; Snir et al. 1998]. The second edition "starting point is a chronology of
  the MPI Forum, with a discussion of what was, and was not, included in MPI-1" — a useful primary
  historical source [Gropp 2005 review].
- **Gropp, Lusk & Skjellum, *Using MPI*, MIT Press, 1994; 3rd ed. 2014**, with companion *Using
  Advanced MPI* (Gropp, Hoefler, Thakur, Lusk), 2014 [Gropp et al. 1994; Gropp et al. 2014a;
  Gropp et al. 2014b]. Chapter 1 of the 3rd edition contains "Evolution of Message-Passing Systems"
  and "The MPI Forum" sections [Gropp et al. 2014a].

### 5.3 The 25-year mark, and the 30-year question

The **"25 Years of MPI" symposium** was held in conjunction with **EuroMPI/USA 2017 at Argonne
National Laboratory, September 25–28, 2017** — the first time the long-running EuroMPI series
convened in the United States [EuroMPI 2017; Argonne 2017]. Talks included **Steven Huss-Lederman,
"Reflections on the MPI Process"** (Sept 25, 2017) and **Anthony Skjellum (with Ron Brightwell and
Rossen Dimitrov), "MPI: 25 Years of Progress"** [Huss-Lederman 2017; Skjellum 2017]. HPCwire's
preview article is a compact and quotable account of MPI's longevity, including the conference
lineage: the Euro-* workshops "started as PVM (Parallel Virtual Machine) user group meetings, became
EuroPVM workshops from 1994 to 1996, EuroPVM/MPI from [1997] to 2009, and EuroMPI from 2010 to 2017"
[HPCwire 2017] — **the printed article gives "EuroPVM/MPI from 2007 to 2009," which is inconsistent
with the 1996→2010 span and appears to be a typo; treat the EuroPVM/MPI start year as
`[UNVERIFIED]`.**

**On the 30-year mark**: I found **no evidence** of a formal 30-year retrospective symposium or
commemorative keynote at EuroMPI 2023 or EuroMPI 2024. `[UNVERIFIED]` — do not assert one. The
closest available current-state retrospectives are the Forum's ISC25 BoF [Hammond & Dalcin 2025] and
Schulz's "The State of MPI: Current Standard and Future Plans" seminar, which includes the full
version/page-count history and forward plans [Schulz 2026]. There is a "25 Year Symposium" archive at
`mcs.anl.gov/mpi-symposium` referenced by Schulz [Schulz 2026].

### 5.4 Criticisms

**Complexity and low-level-ness.** Gropp himself catalogs the standard complaints: "the complexity of
MPI, often as measured by the number of functions; performance issues, particularly the latency or
cost of communicating short messages; and the lack of compile or runtime help… More subtle issues
such as the complexity of nonblocking communication and the lack of elegance relative to a parallel
programming language" [Gropp 2001]. The HPC Wiki is blunter: "There is no doubt that MPI is a
bloated, complicated and sometimes confusing library standard" [HPC Wiki]. Eadline's version is that
MPI "does represent a barrier to the domain expert. That is, programming in MPI is too much of an
investment for Joe Sixpack programmer. It requires not only code changes, but testing and debugging
are harder, and possible major re-writes may be necessary" [Eadline n.d.].

**The PGAS critique, and the empirical rebuttal.** PGAS languages (UPC, Coarray Fortran, Chapel, X10,
Titanium) and libraries (Global Arrays, UPC++, GASNet) argue that a partitioned global address space
is a cleaner and more productive abstraction. The strongest counter-evidence is that tuned PGAS code
converges on MPI code: "PGAS programs deliver scalable performance only when they are carefully
tuned. Often, after initial coding, the programmer tunes the source code to produce a more scalable
version. However, the reality is that, at the end of these modifications, the PGAS code resembles
very much his MPI equivalent, often nullifying the ease-of-coding advantage of these languages"
[Alvanos 2013]. The HPC Wiki concurs: "Hopes that the integration of distributed memory
communication into the [language] allows for advanced optimizations by the compiler or a runtime
system were disappointed. A high performance PGAS program looks very [similar] to a high performance
MPI program but with a nicer implementation interface" [HPC Wiki]. Bonachea & Duell's critique is
narrower and sharper: MPI is inadequate *as a compilation target* for GAS languages
[Bonachea & Duell 2004].

**"Alternatives to MPI" as an institutional position.** The PAW-ATM workshop (Parallel Applications
Workshop, Alternatives To MPI) exists precisely to collect case studies of higher-level models —
Chapel, Fortran coarrays, Julia, Charm++, UPC++, Coarray C++, HPX, Legion, Global Arrays, Spark,
TensorFlow, Dask — against MPI+X, on the argument that "the MPI + X approach inherently saddles the
developer with low-level details that might better be handled by high-level abstractions"
[PAW-ATM]. Note that the workshop's own framing concedes performance parity is the open question,
asking for "characterizations of scalability and performance, of expressiveness and programmability,
as well as any downsides" [PAW-ATM].

**"MPI is dead / long live MPI."** I could **not** verify a specific Jeff Squyres publication or talk
under that title. `[UNVERIFIED]` — do not cite it as a named work. The debate it names is real and is
well-documented through the sources above (Gropp 2001/2022, HPC Wiki, PAW-ATM, Eadline), and the
sober summary is: MPI remains "the de facto programming model for distributed memory architectures"
[Alvanos 2013] and "the only message passing library that can be considered 'standard' for HPC…
supported on virtually all HPC platforms" [LLNL Tutorial].

**MPI+X.** The mainstream position is that intra-node parallelism belongs to something other than
MPI. "A common case on a cluster with large shared memory nodes it is natural to combine MPI with
OpenMP or any other threaded programming model. Mixing MPI [with] another programming model in any
case adds significant complexity on the implementation level as well as later when running the
application. There must therefore be a good reason for going hybrid. If an MPI code scales very well
one should not try to be better with an hybrid code" [HPC Wiki]. Gropp & Snir describe MPI+OpenMP as
the concrete instantiation of a node-level programming model [Gropp & Snir 2013]. The ASCR report is
the strongest institutional statement that MPI-as-intra-node is untenable at exascale
[Amarasinghe et al. 2011].

**Fault tolerance.** MPI-1's explicit goal was to *assume reliability* [MPIForum 1995], and MPI has
never fully retreated from that. `MPI_COMM_WORLD` makes it worse: "The `MPI_COMM_WORLD` communicator
is always impacted by [an] error because its functionality depends on the set of all processes
involved in the job" [Holmes et al. 2016]. The community's answer has been **ULFM (User Level Failure
Mitigation)**, proposed to the Forum and evaluated at EuroMPI 2012 [Bland et al. 2012], plus
mechanisms "to determine whether communicators have experienced a fault, to remove faulty members of
a communicator, and to repair a communicator after a fault without triggering a complete application
abort" [Holmes et al. 2016]. MPI-4.0's error-handling changes and Sessions are the *shipped* partial
answer: Sessions "break this guaranteed impact by not requiring the existence of a communicator that
spans the entire job," so "only processes which need to communicate with the failed node are required
to react to a failure" [Holmes et al. 2016]. ULFM itself is **not** in the standard as of MPI-5.0
`[UNVERIFIED — I did not find ULFM in the MPI-5.0 change log, and the MPI-5.0 Fault Tolerance
working group under Bouteiller and Laguna remains active, which is consistent with ULFM still being
outside the standard]` [MPIForum 2025].

**Gropp's own forward-looking critique (2022).** "MPI has been remarkably successful. Powerful
abstractions, avoided being tied too closely to HW at a moment in time. Benefitted from stability in
architecture. That era of stability has ended. MPI needs to adapt. HPC no longer driving all
high-performance HW, SW. Time to identify and rethink assumptions… Rethink building blocks. Consider
streams, notification, subsets. Explicit consideration of latency and bandwidth separately"
[Gropp 2022].

---

## 6. Adoption and ecosystem

### 6.1 MPICH lineage

**Origins.** "MPICH was originally developed during the MPI standards process starting in 1992 to
provide feedback to the MPI Forum on implementation and usability issues. This original
implementation was based on the Chameleon portability system to provide a light-weight implementation
layer (hence the name MPICH from MPI over CHameleon)" [MPICH Overview]. Argonne and Mississippi State
jointly developed MPICH1 as public-domain software [Wikipedia MPICH]. Pronunciation, per the authors:
"'em-pee-eye-see-aitch', not 'empitch', but even we have given up by now" [Gropp & Lusk 2012].

**MPICH1's role in the standardization process is a central historical fact.** "Our reference
implementation tracked the specification as it evolved, with a new release every six weeks, often
undoing things that were done in the previous cycle… the idea of subsetting was rendered irrelevant
as it was shown that the whole thing could be implemented quickly. When the spec was finished, the
implementation was ready to go (compare with HPF)" [Gropp & Lusk 2012]. The final MPICH1 release was
1.2.7p1 [MPICH Overview]. Early MPICH borrowed heavily from Zipcode for "algorithms for the
collective operations and topologies, together with code for attribute management," and was initially
"a thin [layer] (mostly C macros) over vendor message-passing [systems] (Intel's NX, TMC's CMMD,
IBM's MPL)" [Gropp et al. 1996].

**Versioning history.** Development on MPICH2 began around 2001, restarting version numbers at 0.9
and running to 1.5; "Starting with the major release in November 2012, the project is renamed back to
MPICH with a version number of 3.0" [MPICH Overview]. MPICH v3.0 implements MPI-3.0; v4.x implements
MPI-4.x [Wikipedia MPICH]. MPICH2 was "a completely [new] implementation from scratch (mkdir) — new
architecture, all new code, implementation of lessons learned in MPICH1, but same layered approach"
[Gropp & Lusk 2012].

**Layering: ADI → CH3/CH4 → channels/netmods.** The **Abstract Device Interface (ADI)** is the
portability layer: "the key to combining portability and performance is a specification we call the
abstract device interface (ADI)… MPICH contains many implementations of the ADI, [giving] portability,
ease of implementation, and an incremental [path for] trading portability for performance"
[Gropp et al. 1996; Balaji et al. 2020]. In MPICH2, "The ADI3 layer presents the MPI interface to the
application layer above it, and the ADI3 interface to the device layer below it. MPICH2 can be
[configured with] the CH3 device. The CH3 device presents the CH3 interface to the layer below"
[Buntinas et al. 2009]. **Nemesis** is the low-latency channel: "A new low-latency channel called
Nemesis has been added. It can be selected by specifying the option `--with-device=ch3:nemesis`.
Nemesis uses shared memory for intranode communication and various networks for internode
communication" — implemented first as a CH3 channel to allow rapid prototyping, with a full ADI3
device as future work [MPICH CHANGES; Buntinas et al. 2009]. **CH4** replaced CH3: "ch4 replaces ch3
as the default device configuration. If no network module is specified at configuration-time, MPICH
will search the user environment in order to select one" [MPICH CHANGES]; CH4's aim is to minimize
software overhead by mapping MPI operations closely onto network APIs such as OFI and UCX
[Balaji et al. 2020].

### 6.2 Open MPI

**Formation.** Open MPI is the merger of **LAM/MPI** (originally from the Ohio State University
supercomputing center, later migrated to the University of Notre Dame — and subsequently associated
with Indiana University), **LA-MPI** (Los Alamos National Laboratory), **FT-MPI** (University of
Tennessee, Knoxville), with the **PACX-MPI** team (University of Stuttgart) joining shortly after
inception — "One of the UTK developers moved back to the University of Stuttgart in late 2004, which
effectively added their team into the project" [Open MPI History; Open MPI FAQ]. Note that Open MPI's
own history text says "the merger of three prior MPI implementations" and then counts PACX-MPI
separately; the FAQ and Wikipedia both describe four founding institutions
[Open MPI History; Open MPI FAQ; Wikipedia Open MPI].

**Dates.** "At SC2003, we decided to start an entire new code base — leaving all the cruft and legacy
code of our prior implementations behind. Take the best, leave the rest. **The source tree's first
commit was on November 22, 2003; development work started in earnest on January 5, 2004.** Since
then, we have met together as a group once a month (for at least a week)" [Open MPI History]. The
design was presented at **EuroPVM/MPI 2004** as "Open MPI: Goals, Concept, and Design of a Next
Generation MPI Implementation" [Gabriel et al. 2004].

**Why a rewrite rather than a merge**, in the developers' words: the four code bases "had radically
different implementation architectures, and would be incredibly difficult (if not impossible) to
merge"; each had significant strengths and significant weaknesses; the four teams "had not worked
directly together before," so starting fresh "put all developers on equal ground"
[Open MPI AOSA]. Scale of the problem: MPI-2.0 defined "over 300 API functions," and LAM/MPI alone
"had over 1,900 files of source code, comprising over 300,000 lines of code" [Open MPI AOSA]. The
project's stated goals include "to help prevent the 'forking problem' common to other MPI projects"
[Wikipedia Open MPI]. Open MPI's architectural signature is its **component architecture** (MCA),
decomposing point-to-point, collectives, and run-time environment support into pluggable components
[Open MPI History].

**Deployment.** Open MPI was used by Roadrunner (world's fastest June 2008–November 2009) and the
K computer (fastest June 2011–June 2012) [Wikipedia Open MPI].

### 6.3 MVAPICH

Developed at **Ohio State University in 2002** by Dhabaleswar K. Panda with Pete Wyckoff of the Ohio
Supercomputer Center, to bridge MPI and InfiniBand — "Until Panda and Pete Wyckoff… developed
MVAPICH in 2002, InfiniBand and MPI were hopelessly incompatible." The name means "MPI for InfiniBand
on VAPI Layer," VAPI being Mellanox's software interface; pronounced "em-vah-peach." Primary funding
from Sandia, DOE and NSF, with partial Intel funding and donated Mellanox hardware
[ScienceDaily 2003]. MVAPICH2 is an MPICH derivative and, on Mellanox InfiniBand, is widely regarded
as the preferred implementation [StackOverflow MPICH-vs-OpenMPI; CHPC Utah]. The canonical MVAPICH
*paper* citation is `[UNVERIFIED]` — likely Liu, Wu, Kini, Wyckoff & Panda on RDMA-based MPI over
InfiniBand, but I did not confirm venue or year.

### 6.4 Vendor derivatives

MPICH is "used as the foundation for many other MPI implementations, including IBM MPI (for Blue
Gene), Intel MPI, Cray MPI, Microsoft MPI, CDAC MPI (C-MPI), Myricom MPI, OSU MVAPICH/MVAPICH2"
[Wikipedia MPICH]; the derivative list also includes **ParTec ParaStation MPI**, **RIKEN MPI**, and
UBC's coroutine-based Fine-Grain MPI [Wikipedia MPICH; MPICH ABI]. **Cray MPICH / HPE**: Cray MPT is
MPICH-based [Wikipedia MPICH; MPICH ABI]. **Microsoft MPI (MS-MPI)** is MPICH-derived
[Wikipedia MPICH]. **IBM Spectrum MPI** is an Open MPI derivative with IBM-specific optimizations
`[UNVERIFIED — asserted by secondary sources; the ABI BoF places IBM Spectrum MPI in the Open MPI ABI
family, which is consistent]` [Hammond & Dalcin 2025]. Also in the Open MPI ABI family: **NVIDIA
HPC-X** and **Amazon MPI** [Hammond & Dalcin 2025]. The MPICH 2014 BoF's TOP500 census shows the
breadth: of the top ten machines, MPICH-derived implementations covered Tianhe-2 (TH-MPI), Titan
(Cray MPI), Sequoia and Mira and Vulcan (IBM PE MPI), Piz Daint (Cray MPI), Stampede (Intel MPI and
MVAPICH), and Juqueen (IBM PE) [MPICH BoF 2014].

### 6.5 The two ABI initiatives

**MPICH ABI Compatibility Initiative (announced at SC13, Denver, November 17–22, 2013).** "The goal
of the initiative is for all participating implementations to be binary compatible, and to agree on a
schedule for necessary ABI changes in future releases" [MPICH 2013]. Argonne's initial collaborators
were **IBM, Intel and Cray** [HPCwire 2013]. Participating releases and dates: **MPICH v3.1
(February 2014), IBM PE MPI v1.4 (April 2014), Intel MPI Library v5.0 (2014), Cray MPT v7.0.0 (June
2014), MVAPICH2 2.0 (June 2014), RIKEN MPI 1.0 (August 2016), ParaStation MPI 5.1.7-1 (December
2016)** [MPICH ABI; MPICH BoF 2014]. Mechanism: a shared libtool ABI string (`12:x:0`), agreed
library names (`libmpi`, `libmpicxx`, `libmpifort`), and versioning rules under which an application
depends only on `libmpi.so.12` and therefore runs against any partner release exposing the same
SONAME [MPICH ABI; Sarus docs]. Intel's stated motivation was ISV demand and "a binary-compatible
upgrade path between the MPI-2 and MPI-3 standards" [HPCwire 2013].

**MPI-5.0 standard ABI (2025).** See §3.8. The relationship between the two is that the MPICH
initiative created ABI stability *within one family* and thereby demonstrated both the demand and the
feasibility; MPI-5.0 Chapter 21 generalizes it across families as an **opt-in, separately versioned,
deprecation-free** binary contract [Hammond & Dalcin 2025; MPIForum 2025 §21]. Supporting artifacts:
the MPI ABI stubs repository (`github.com/mpi-forum/mpi-abi-stubs`) and the pre-existing
translation shims Mukautuva, wi4mpi and MPItrampoline [Hammond & Dalcin 2025].

### 6.6 Language bindings above MPI

**mpi4py.** The dominant Python binding: "MPI for Python (mpi4py) has evolved to become the most used
Python binding for the message passing interface." Its two stated principles are worth transposing
directly to an agent-harness API: "(a) being feature-complete and exposing to Python as much of MPI
as possible, and (b) staying close to the MPI standard in both syntax and semantics, without
reinventing the wheel with new or foreign APIs, and maximizing user convenience by following common
Python idioms and practice" [Dalcin & Fang 2021]. Lineage: Dalcin, Paz, Storti & D'Elia, "MPI for
Python: Performance improvements and MPI-2 extensions," JPDC 68(5):655–662, 2008
[Dalcin et al. 2008]; Dalcin, Paz, Kler & Cosimo, "Parallel distributed computing using Python,"
Advances in Water Resources 34(9):1124–1139, 2011 [Dalcin et al. 2011]; and the 12-year status update
covering MPI-3.1 support and CUDA-aware MPI [Dalcin & Fang 2021]. Note the ecosystem role mpi4py now
plays: MPICH's MPI-5.0 ABI implementation was "heavily tested by mpi4py" [Hammond & Dalcin 2025], and
Dalcin co-authored the ABI chapter.

**Boost.MPI.** Douglas Gregor and Matthias Troyer. "Boost.MPI is not a completely new parallel
programming library. Rather, it is a C++-friendly interface to the standard Message Passing
Interface… Although there exist C++ bindings for MPI, they offer little functionality over the C
bindings. The Boost.MPI library provides an alternative C++ interface to MPI that better supports
modern C++ development styles, including complete support for user-defined data types and C++
Standard Library types, arbitrary function objects for collective algorithms, and the use of modern
C++ library techniques to maintain maximal efficiency." It supports "the majority of functionality in
MPI 1.1" and can also be used from Python [Boost.MPI docs]. Its existence is itself evidence for the
weakness of MPI's own C++ bindings, which MPI-3.0 removed [MPIForum 2023]. The formal publication
venue and year for Boost.MPI are `[UNVERIFIED]`; cite the Boost documentation.

---

## 7. Transferable findings for AgentMPI (analytical summary)

1. **The standardization window matters.** MPI succeeded partly because it arrived after multiple
   research systems had been built and compared, not before: "Trying to establish such a standard
   earlier might have failed to benefit from research into multiple approaches" [HPCwire 2017].
2. **Contexts, not tags, are the composability primitive.** The system must allocate opaque,
   non-wildcardable scopes, attached to both send and receive, requiring no synchronization at
   component entry/exit [MPIForum 1995; Gropp et al. 1996]. An agent harness that gives components
   only string topic names has the pre-MPI tag problem.
3. **A prototype-implementation requirement is the enforceable form of "standardize practice"**
   [Graham n.d.].
4. **Permissive semantics, not mandated mechanism**, is how portability and performance coexist
   [Gropp 2001].
5. **Assuming reliability is a defensible 1994 simplification and an indefensible 2020s one.** MPI is
   still paying: ULFM outside the standard 13+ years on, and Sessions/error-handling changes as
   partial mitigation [MPIForum 1995; Bland et al. 2012; Holmes et al. 2016].
6. **Opacity buys 30 years of implementation freedom and costs binary portability**, and the bill
   comes due exactly once, with a date: MPI-5.0, June 5, 2025 [MPIForum 2025].
7. **Size is the wrong complexity metric; concept count and exception count are the right ones**, and
   a designated "initial implementation subset" should exist from day one
   [Gropp 2001; MPIForum 2025].

---

## References

- **[Agha 1986]** Agha, G. *Actors: A Model of Concurrent Computation in Distributed Systems*. MIT
  Press, Cambridge, MA, 1986.
- **[Alvanos 2013]** Alvanos, M. *Optimization Techniques for Fine-Grained Communication in PGAS
  Environments*. PhD thesis, Universitat Politècnica de Catalunya, 2013. DOI:
  10.5821/dissertation-2117-95212. https://webdocs.cs.ualberta.ca/~amaral/thesis/MichailAlvanosPhD.pdf
- **[Amarasinghe et al. 2011]** Amarasinghe, S., Hall, M., Lethin, R., Pingali, K., Quinlan, D.,
  Sarkar, V., et al. *Exascale Programming Challenges: Report of the 2011 Workshop on Exascale
  Programming Challenges*. U.S. Department of Energy, Office of Advanced Scientific Computing
  Research, 2011.
  https://science.osti.gov/-/media/ascr/pdf/program-documents/docs/ProgrammingChallengesWorkshopReport.pdf
- **[Amer et al. 2015]** Amer, A., Lu, H., Wei, Y., Balaji, P., Matsuoka, S. "MPI+Threads: Runtime
  Contention and Remedies." In *Proc. 20th ACM SIGPLAN Symposium on Principles and Practice of
  Parallel Programming (PPoPP '15)*, 2015.
  https://pavanbalaji.github.io/pubs/2015/ppopp/ppopp15.mpi_threads.pdf
- **[Argonne 2017]** Argonne National Laboratory. "Pioneers of high-performance computing library
  reunite." Press release / EurekAlert, 2017. https://e3.eurekalert.org/news-releases/621208
- **[Bala & Kipnis 1992]** Bala, V., Kipnis, S. "Process groups: a mechanism for the coordination of
  and communication among processes in the Venus collective communication library." Technical report,
  IBM T. J. Watson Research Center, October 1992 (preprint).
- **[Bala et al. 1992]** Bala, V., Kipnis, S., Rudolph, L., Snir, M. "Designing efficient, scalable,
  and portable collective communication libraries." Technical report, IBM T. J. Watson Research
  Center, October 1992 (preprint).
- **[Balaji et al. 2010]** Balaji, P., Buntinas, D., Goodell, D., Gropp, W., Thakur, R.
  "Fine-Grained Multithreading Support for Hybrid Threaded MPI Programming." *International Journal
  of High Performance Computing Applications*, 2010.
  https://pavanbalaji.github.io/pubs/2010/jhpca/jhpca10.multithreading.pdf
- **[Balaji et al. 2020]** Balaji, P., Gropp, W., Lusk, E., Thakur, R., et al. "Translational research
  in the MPICH project." *Journal of Computational Science*, 2020. DOI: 10.1016/j.jocs.2020.101203.
  Preprint: https://www.osti.gov/servlets/purl/1854523
- **[Bernholdt et al. 2018]** Bernholdt, D. E., Boehm, S., Bosilca, G., Gorentla Venkata, M., Grant,
  R. E., Naughton, T., Pritchard, H. P., Schulz, M., Vallée, G. R. *A Survey of MPI Usage in the
  U.S. Exascale Computing Project*. ECP Milestone Report ORNL/SPR-2018/790, Oak Ridge National
  Laboratory, 2018. DOI: 10.2172/1462877.
  https://info.ornl.gov/sites/publications/Files/Pub108588.pdf
- **[Bernholdt et al. 2020]** Bernholdt, D. E., Boehm, S., Bosilca, G., Gorentla Venkata, M., Grant,
  R. E., Naughton, T., Pritchard, H. P., Schulz, M., Vallée, G. R. "A survey of MPI usage in the US
  exascale computing project." *Concurrency and Computation: Practice and Experience* 32(3):e4851,
  2020. DOI: 10.1002/cpe.4851.
- **[BibSonomy 2001]** BibSonomy record for Gropp, "Learning from the Success of MPI," HiPC 2001,
  LNCS 2228, pp. 81–94 (DBLP key `conf/hipc/Gropp01`).
  https://www.bibsonomy.org/bibtex/100e9899b6a2dce889b45fefa4c6fc216
- **[Bland et al. 2012]** Bland, W., Bouteiller, A., Herault, T., Hursey, J., Bosilca, G., Dongarra,
  J. J. "An Evaluation of User-Level Failure Mitigation Support in MPI." In *Recent Advances in the
  Message Passing Interface: 19th European MPI Users' Group Meeting (EuroMPI 2012)*, Vienna, Austria,
  September 23–26, 2012, LNCS, pp. 193–203. Springer, 2012.
- **[Bomans et al. 1990]** Bomans, L., Roose, D., Hempel, R. "The Argonne/GMD macros in FORTRAN for
  portable parallel programming and their implementation on the Intel iPSC/2." *Parallel Computing*
  15(1–3):119–132, 1990. DOI: 10.1016/0167-8191(90)90036-9.
- **[Bonachea & Duell 2004]** Bonachea, D., Duell, J. "Problems with using MPI 1.1 and 2.0 as
  compilation targets for parallel language implementations." *International Journal of High
  Performance Computing and Networking*, 2004 (also 2nd Workshop on Hardware/Software Support for
  High Performance Scientific and Engineering Computing, 2003).
- **[Boost.MPI docs]** Gregor, D., Troyer, M. *Boost.MPI* documentation. Boost C++ Libraries.
  http://boost.cowic.de/rc/pdf/mpi.pdf
- **[Brookes et al. 1984]** Brookes, S. D., Hoare, C. A. R., Roscoe, A. W. "A Theory of Communicating
  Sequential Processes." *Journal of the ACM* 31(3):560–599, 1984. DOI: 10.1145/828.833.
- **[Buntinas et al. 2009]** Buntinas, D., Mercier, G., Gropp, W. (and collaborators).
  "Implementation and Shared-Memory Evaluation of MPICH2 over the Nemesis Communication Subsystem."
  HAL preprint hal-00344339. https://hal.science/hal-00344339/document
- **[Butler & Lusk 1994]** Butler, R. M., Lusk, E. L. "Monitors, messages, and clusters: The p4
  parallel programming system." *Parallel Computing* 20(4):547–564, 1994. DOI:
  10.1016/0167-8191(94)90028-0.
- **[BYU ACME]** Brigham Young University ACME program. "Parallel Programming (MPI)" lab notes,
  quoting Pacheco, *Parallel Programming with MPI*, p. 7.
  https://acme.byu.edu/00000180-6d94-d2d1-ade4-6ff4c7cf0001/mpi
- **[CHPC Utah]** Center for High Performance Computing, University of Utah. "MPI Libraries."
  https://www.chpc.utah.edu/documentation/software/mpilibraries.php
- **[Dalcin et al. 2008]** Dalcin, L., Paz, R., Storti, M., D'Elia, J. "MPI for Python: Performance
  improvements and MPI-2 extensions." *Journal of Parallel and Distributed Computing* 68(5):655–662,
  2008. DOI: 10.1016/j.jpdc.2007.09.005.
- **[Dalcin et al. 2011]** Dalcin, L. D., Paz, R. R., Kler, P. A., Cosimo, A. "Parallel distributed
  computing using Python." *Advances in Water Resources* 34(9):1124–1139, 2011.
  https://ri.conicet.gov.ar/bitstream/handle/11336/13349/CONICET_Digital_Nro.16517_A.pdf
- **[Dalcin & Fang 2021]** Dalcin, L., Fang, Y.-L. L. "mpi4py: Status Update After 12 Years of
  Development." *Computing in Science & Engineering*, 2021. DOI: 10.1109/MCSE.2021.3083216.
- **[Dinan et al. 2016]** Dinan, J., Balaji, P., Buntinas, D., Goodell, D., Gropp, W., Thakur, R.
  "An implementation and evaluation of the MPI 3.0 one-sided communication interface." *Concurrency
  and Computation: Practice and Experience*, 2016.
  https://pavanbalaji.github.io/pubs/2016/ccpe/ccpe16.rma.pdf
- **[Dongarra et al. 1993]** Dongarra, J. J., Hempel, R., Hey, A. J. G., Walker, D. W. *A Proposal
  for a User-Level, Message-Passing Interface in a Distributed Memory Environment*. Technical Report
  ORNL/TM-12231, Oak Ridge National Laboratory, February 1993.
- **[Dongarra et al. 1996]** Dongarra, J. J., Otto, S. W., Snir, M., Walker, D. "A message passing
  standard for MPP and workstations." *Communications of the ACM* 39(7):84–90, July 1996. DOI:
  10.1145/233977.234000.
  https://www.netlib.org/utk/people/JackDongarra/PAPERS/076_1996_a-message-passing-standard-for-mpp-and-workstations.pdf
- **[Eadline n.d.]** Eadline, D. "You (Still) Can't Always Get What You Want." ClusterMonkey
  opinion column, n.d.
  https://www.clustermonkey.net/Opinions/you-still-can-t-always-get-what-you-want.html
- **[EPCC 1991]** Edinburgh Parallel Computing Centre, University of Edinburgh. *CHIMP Concepts*,
  June 1991.
- **[EPCC 1992]** Edinburgh Parallel Computing Centre, University of Edinburgh. *CHIMP Version 1.0
  Interface*, May 1992.
- **[EuroMPI 2017]** EuroMPI/USA 2017 conference website, Argonne National Laboratory, Chicago,
  September 25–28, 2017. https://www.mcs.anl.gov/eurompi2017/
- **[Feitelson 1991]** Feitelson, D. *Communicators: Object-Based Multiparty Interactions for
  Parallel Programming*. Technical Report 91-12, Department of Computer Science, The Hebrew
  University of Jerusalem, November 1991.
- **[Gabriel et al. 2004]** Gabriel, E., Fagg, G. E., Bosilca, G., Angskun, T., Dongarra, J. J.,
  Squyres, J. M., Sahay, V., Kambadur, P., Barrett, B., Lumsdaine, A., Castain, R. H., Daniel, D. J.,
  Graham, R. L., Woodall, T. S. "Open MPI: Goals, Concept, and Design of a Next Generation MPI
  Implementation." In *Proc. 11th European PVM/MPI Users' Group Meeting (EuroPVM/MPI 2004)*,
  Budapest, Hungary, September 2004, LNCS 3241, pp. 97–104. Springer.
- **[Geist et al. 1990]** Geist, G. A., Heath, M. T., Peyton, B. W., Worley, P. H. *A User's Guide to
  PICL: A Portable Instrumented Communication Library*. Technical Report ORNL/TM-11616, Oak Ridge
  National Laboratory, October 1990.
- **[Geist et al. 1994]** Geist, A., Beguelin, A., Dongarra, J., Jiang, W., Manchek, R., Sunderam,
  V. *PVM: Parallel Virtual Machine — A Users' Guide and Tutorial for Networked Parallel Computing*.
  MIT Press, 1994. Online: https://www.netlib.org/pvm3/book/node2.html (history);
  https://netlib.org/pvm3/book/node156.html (version history)
- **[Georgiou et al. 2018]** Georgiou, K., et al. "Programming at Exascale: Challenges and
  Innovations." arXiv:1809.10023, 2018. https://arxiv.org/pdf/1809.10023
- **[Gelernter 1985]** Gelernter, D. "Generative communication in Linda." *ACM Transactions on
  Programming Languages and Systems* 7(1):80–112, January 1985. DOI: 10.1145/2363.2433.
- **[Graham n.d.]** Graham, R. L. *MPI Forum — Overview*. Presentation slides, MPI Forum Chairman,
  n.d. (MPI-2.2/MPI-3 era). https://www.sambuz.com/doc/mpi-forum-overview-ppt-presentation-815524
- **[Gropp 2001]** Gropp, W. D. "Learning from the Success of MPI." In *High Performance Computing —
  HiPC 2001: 8th International Conference*, Hyderabad, India, December 17–20, 2001. LNCS 2228,
  pp. 81–92 (Springer pagination; DBLP gives 81–94). Springer, 2001. DOI: 10.1007/3-540-45307-5_8.
  Preprint: arXiv:cs/0109017, DOI: 10.48550/arXiv.cs/0109017.
- **[Gropp 2005 review]** Review of Snir, M., Otto, S., Huss-Lederman, S., Walker, D., Dongarra, J.,
  *MPI — The Complete Reference, Vol. 1: The MPI Core, 2nd ed.* *Scientific Programming*, 2005. DOI:
  10.1155/2005/653765.
- **[Gropp 2022]** Gropp, W. *[ExaMPI keynote slides]*. University of Illinois Urbana-Champaign,
  2022. https://wgropp.cs.illinois.edu/bib/talks/tdata/2022/exampi-final.pdf
- **[Gropp & Lusk 2012]** Gropp, W., Lusk, E. *20 Years of MPICH*. MPICH Birds-of-a-Feather session,
  SC12, 2012.
  https://www.mpich.org/static/docs/slides/2012-sc-bof/MPICH_BOF_SC12-20_Years_of_MPICH.pdf
- **[Gropp & Smith 1993]** Gropp, W. D., Smith, B. *Chameleon Parallel Programming Tools Users
  Manual*. Technical Report ANL-93/23, Argonne National Laboratory, March 1993.
- **[Gropp & Snir 2013]** Gropp, W., Snir, M. "Programming for Exascale Computers." *Computing in
  Science & Engineering* 15(6):27–35, 2013. DOI: 10.1109/MCSE.2013.96.
  https://snir.cs.illinois.edu/listed/J55.pdf
- **[Gropp et al. 1994]** Gropp, W., Lusk, E., Skjellum, A. *Using MPI: Portable Parallel Programming
  with the Message-Passing Interface*. MIT Press, 1994.
- **[Gropp et al. 1996]** Gropp, W., Lusk, E., Doss, N., Skjellum, A. "A high-performance, portable
  implementation of the MPI message-passing interface standard." *Parallel Computing* 22(6):789–828,
  1996. https://ucbrise.github.io/cs262a-spring2018/notes/MPI.pdf
- **[Gropp et al. 2014a]** Gropp, W., Lusk, E., Skjellum, A. *Using MPI: Portable Parallel
  Programming with the Message-Passing Interface*, 3rd ed. MIT Press, 2014.
  https://wgropp.cs.illinois.edu/usingmpiweb/
- **[Gropp et al. 2014b]** Gropp, W., Hoefler, T., Thakur, R., Lusk, E. *Using Advanced MPI: Modern
  Features of the Message-Passing Interface*. MIT Press, 2014.
- **[Hammond & Dalcin 2025]** Hammond, J., Dalcin, L., et al. *The Message Passing Interface (MPI):
  The New MPI 5.0 — Now with ABI Included!* MPI Forum Birds-of-a-Feather session, ISC 2025, June
  2025. https://www.mpi-forum.org/bofs/2025-06-MPI-BOF-ISC25.pdf
- **[Heroux 2010]** Heroux, M. A., et al. *Toward Portable Programming of Numerical Linear Algebra on
  Manycore Nodes*. Sandia National Laboratories / SOS14 presentation, 2010 (quoting B. Chamberlain,
  Cray, 2000). https://www.osti.gov/servlets/purl/1109301
- **[Hewitt et al. 1973]** Hewitt, C., Bishop, P., Steiger, R. "A Universal Modular ACTOR Formalism
  for Artificial Intelligence." In *Proc. 3rd International Joint Conference on Artificial
  Intelligence (IJCAI '73)*, pp. 235–245, 1973.
- **[HiPC 2001]** HiPC 2001 conference program (Gropp keynote listed as "Whither MPI: Lessons From
  and the Future of MPI"). http://www.hipc.org/c2001/program.html
- **[Hoare 1978]** Hoare, C. A. R. "Communicating sequential processes." *Communications of the ACM*
  21(8):666–677, 1978. DOI: 10.1145/359576.359585.
- **[Hoare 1985]** Hoare, C. A. R. *Communicating Sequential Processes*. Prentice Hall International,
  1985.
- **[Hoare 1991]** Hoare, C. A. R. "The transputer and occam: A personal story." *Concurrency:
  Practice and Experience* 3(4):249–264, 1991. DOI: 10.1002/cpe.4330030403.
- **[Hoefler 2012]** Hoefler, T. "MPI-3.0 is Coming — an Overview of new (and old) Features." Blog
  post, ETH Zürich, February 6, 2012.
  https://htor.inf.ethz.ch/blog/index.php/2012/02/06/mpi-3-0-is-coming-an-overview-of-new-and-old-features/
- **[Holmes et al. 2016]** Holmes, D., Mohror, K., Grant, R. E., Skjellum, A., Schulz, M., Bland, W.,
  Squyres, J. M. "MPI Sessions: Leveraging Runtime Infrastructure to Increase Scalability of
  Applications at Exascale." In *Proc. 23rd European MPI Users' Group Meeting (EuroMPI 2016)*, 2016.
  https://www.osti.gov/servlets/purl/1373234
- **[HPC Wiki]** HPC Wiki. "MPI." https://hpc-wiki.info/hpc/MPI
- **[HPCwire 2013]** HPCwire. "Argonne Researchers Establish MPICH ABI Compatibility Initiative."
  December 4, 2013.
  https://www.hpcwire.com/off-the-wire/argonne-researchers-establish-mpich-abi-compatibility-initiative/
- **[HPCwire 2017]** HPCwire. "MPI Is 25 Years Old!" May 1, 2017.
  https://www.hpcwire.com/2017/05/01/mpi-25-years-old/
- **[Huss-Lederman 2017]** Huss-Lederman, S. *Reflections on the MPI Process*. "Celebrating 25 Years
  of MPI" symposium, EuroMPI/USA 2017, Chicago, IL, September 25, 2017.
  https://www.sambuz.com/doc/reflections-on-the-mpi-process-steven-huss-lederman-ppt-presentation-1032998
- **[LLNL Tutorial]** Lawrence Livermore National Laboratory. "What is MPI?" LLNL HPC Tutorials.
  https://hpc-tutorials.llnl.gov/mpi/what_is_mpi/ *(Note: contains a date error for MPI-4.0; do not
  cite for dates.)*
- **[MPICH 2013]** MPICH Project. "MPICH ABI Compatibility Initiative Announced at SC13." December 2,
  2013. https://www.mpich.org/2013/12/02/mpich-abi-compatibility-initiative-announced-at-sc13/
- **[MPICH ABI]** MPICH Project. "ABI Compatibility Initiative." `doc/wiki/testing/
  ABI_Compatibility_Initiative.md`.
  https://github.com/pmodels/mpich/blob/main/doc/wiki/testing/ABI_Compatibility_Initiative.md
- **[MPICH BoF 2014]** MPICH Project. *MPICH Birds-of-a-Feather*, SC14, November 18, 2014.
  https://www.mpich.org/static/docs/slides/2014-sc-bof/2014-11-18-sc-mpich-bof.pdf
- **[MPICH CHANGES]** MPICH Project. `CHANGES` file, `main` branch.
  https://github.com/pmodels/mpich/blob/main/CHANGES
- **[MPICH Overview]** MPICH Project. "MPICH Overview." https://www.mpich.org/about/overview/
- **[MPIForum 1994]** Message Passing Interface Forum. "MPI: A Message-Passing Interface Standard."
  *International Journal of Supercomputer Applications and High Performance Computing*, Special Issue
  on MPI, 8(3/4):165–414, 1994.
- **[MPIForum 1995]** Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard*,
  Version 1.1. University of Tennessee, Knoxville, June 12, 1995.
  https://www.mpi-forum.org/docs/mpi-1.1/mpi1-report.pdf
- **[MPIForum 2008]** Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard*,
  Version 2.1. University of Tennessee, Knoxville, June 23, 2008.
  https://www.mpi-forum.org/docs/mpi-2.1/mpi21-report-bw/mpi21-report-bw.htm
- **[MPIForum 2015]** Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard*,
  Version 3.1. University of Tennessee, Knoxville, June 4, 2015.
  https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report/mpi31-report.htm
- **[MPIForum 2020]** Message Passing Interface Forum. *MPI Forum Birds-of-a-Feather*, SC20, November
  2020. https://www.mpi-forum.org/bofs/2020-11-mpi-bof.pdf
- **[MPIForum 2022]** Message Passing Interface Forum. *MPI Forum Procedures*, Version 2.2.
  https://www.mpi-forum.org/docs/other/procedures-22.pdf
- **[MPIForum 2023]** Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard*,
  Version 4.1. November 2, 2023. https://www.mpi-forum.org/docs/mpi-4.1/mpi41-report/
- **[MPIForum 2025]** Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard*,
  Version 5.0. June 5, 2025. https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report/mpi50-report.htm
  (Chapter 21, Application Binary Interface:
  https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report/node520.htm)
- **[MPIForum Docs]** Message Passing Interface Forum. "MPI Documents" (approval dates for all
  versions). https://www.mpi-forum.org/docs/
- **[MPIForum Procedures]** Message Passing Interface Forum. *MPI Forum Procedures* (current
  version). https://www.mpi-forum.org/docs/other/procedures-current.pdf
- **[nCUBE 1990]** nCUBE Corporation. *nCUBE 2 Programmers Guide*, r2.0, December 1990.
- **[Netlib p4]** Argonne National Laboratory. "The p4 Parallel Programming System." Netlib.
  https://netlib.org/p4/ and https://www.netlib.org/p4/readme
- **[Netlib P4Parmacs]** "P4 and Parmacs." In *Computational Physics* survey, Netlib.
  https://www.netlib.org/utk/papers/comp-phy7/node4.html
- **[Open MPI ABI docs]** Open MPI Project. "MPI Forum ABI." Open MPI documentation.
  https://docs.open-mpi.org/en/main/building-apps/mpi-forum-abi.html
- **[Open MPI AOSA]** Squyres, J. M. "Open MPI." In *The Architecture of Open Source Applications,
  Volume 2*, 2012. https://aosabook.org/en/v2/openmpi.html
- **[Open MPI FAQ]** Open MPI Project. "FAQ: General information about the Open MPI Project."
  https://www.open-mpi.org/faq/?category=general
- **[Open MPI History]** Open MPI Project. "History of Open MPI." Open MPI documentation.
  https://docs.open-mpi.org/en/main/history.html
- **[ParaSoft 1992]** ParaSoft Corporation, Pasadena, CA. *Express User's Guide*, version 3.2.5,
  1992. (Related: *Express C — User's Guide, Version 3.0*,
  http://www.transputer.net/prog/express/expcusr.pdf)
- **[PAW-ATM]** ACM SIGARCH. "Parallel Applications Workshop, Alternatives To MPI (PAW-ATM)" call for
  contributions.
  https://www.sigarch.org/call-contributions/parallel-applications-workshop-alternatives-to-mpi/
- **[Pierce 1988]** Pierce, P. "The NX/2 operating system." In *Proc. Third Conference on Hypercube
  Concurrent Computers and Applications*, Vol. 1, pp. 384–390. ACM Press, 1988. DOI:
  10.1145/62297.62341.
- **[Pierce 1994]** Pierce, P. "The NX message passing interface." *Parallel Computing*
  20(4):1285–1302, April 1994. DOI: 10.1016/0167-8191(94)90023-X.
- **[PNNL TCGMSG]** Pacific Northwest National Laboratory. "TCGMSG-MPI." Global Arrays
  documentation. https://hpc.pnl.gov/globalarrays/tcgmsg-mpi/index.shtml
- **[Pritchard et al. 2023]** Pritchard, H., et al. *MPI 4 Features: Sessions, Persistent Collectives,
  Partitioned Communication*. Los Alamos National Laboratory presentation, OSTI 2003046.
  https://www.osti.gov/servlets/purl/2003046
- **[Rabenseifner 2013]** Rabenseifner, R., et al. *MPI 3.0 And Beyond*. HPC Advisory Council
  presentation, Barcelona Supercomputing Center, 2013.
  https://bsc.es/sites/default/files/public/mare_nostrum/2013hpcac-10.pdf
- **[Roscoe & Brookes, in CSP-FDR]** Roscoe, A. W., Brookes, S. D. "CSP: a practical process
  algebra." University of Oxford. https://www.cs.ox.ac.uk/files/12724/cspfdrstory.pdf
- **[Sarus docs]** CSCS. "ABI compatibility and its implications." Sarus 1.7.0 documentation.
  https://sarus.readthedocs.io/en/stable/user/abi_compatibility.html
- **[Schulz 2026]** Schulz, M. *The State of MPI: Current Standard and Future Plans*. NHR PerfLab
  Seminar Series, June 30, 2026 (FAU Erlangen-Nürnberg).
  https://hpc.fau.de/files/2026/07/2026-06-perflab-stateofmpi.pdf
- **[ScienceDaily 2003]** ScienceDaily. "Unique Software Speeds Calculations On One Of World's
  Fastest Supercomputers, Other Applications." November 18, 2003 (on MVAPICH, Panda and Wyckoff).
  https://www.sciencedaily.com/releases/2003/11/031118073440.htm
- **[Skjellum 2017]** Skjellum, A., Brightwell, R., Dimitrov, R. *MPI: 25 Years of Progress*.
  "Celebrating 25 Years of MPI" symposium, EuroMPI/USA 2017, Chicago, IL, September 2017.
  https://www.sambuz.com/doc/mpi-25-years-of-progress-ppt-presentation-898143
- **[Skjellum & Leung 1990]** Skjellum, A., Leung, A. "Zipcode: a portable multicomputer
  communication library atop the reactive kernel." In Walker, D. W., Stout, Q. F. (eds.), *Proc.
  Fifth Distributed Memory Concurrent Computing Conference*, pp. 767–776. IEEE Press, 1990.
- **[Skjellum et al. 1993a]** Skjellum, A., Leung, A., Smith, S. G., Falgout, R. D., Still, C. H.,
  Baldwin, C. H. "The Multicomputer Toolbox — First-Generation Scalable Libraries." Northeast
  Parallel Architectures Center, 1993. https://surface.syr.edu/npac/31
- **[Skjellum et al. 1993b]** Skjellum, A., Doss, N. E., Bangalore, P. V. "Writing Libraries in MPI."
  In Skjellum, A., Reese, D. S. (eds.), *Proc. Scalable Parallel Libraries Conference*, pp. 166–173.
  IEEE Computer Society Press, October 1993.
- **[Skjellum et al. 1994]** Skjellum, A., Smith, S. G., Doss, N. E., Leung, A. P., Morari, M. "The
  Design and Evolution of Zipcode." *Parallel Computing*, Special Issue on Message Passing, 1994
  (invited paper; preprint dated March 8, 1994).
  https://surface.syr.edu/cgi/viewcontent.cgi?article=1025&context=npac
- **[Snir et al. 1996]** Snir, M., Otto, S., Huss-Lederman, S., Walker, D., Dongarra, J. *MPI: The
  Complete Reference*. MIT Press, Cambridge, MA, 1996.
  https://www.netlib.org/utk/papers/mpi-book/mpi-book.html
- **[Snir et al. 1998]** Snir, M., Otto, S., Huss-Lederman, S., Walker, D., Dongarra, J. *MPI — The
  Complete Reference*, 2nd ed., 2 vols. (Vol. 1: *The MPI Core*; Vol. 2: *The MPI Extensions*). MIT
  Press, 1998.
- **[Springer HiPC 2001]** Springer Nature. *High Performance Computing — HiPC 2001*, LNCS 2228
  (table of contents; Gropp keynote at pp. 81–92).
  https://link.springer.com/book/10.1007/3-540-45307-5
- **[StackOverflow MPICH-vs-OpenMPI]** "MPICH vs OpenMPI." Stack Overflow answer (J. Hammond).
  https://stackoverflow.com/questions/2427399/mpich-vs-openmpi
- **[Thakur et al. 2010]** Thakur, R., Balaji, P., Buntinas, D., Goodell, D., Gropp, W., Hoefler, T.,
  Kumar, S., Lusk, E., Träff, J. L. "MPI at Exascale." In *Proc. SciDAC 2010*, 2010.
- **[TMC CMMD]** Thinking Machines Corporation. *CMMD User's Guide* (CM-5). Bitsavers archive.
  https://mirrors.meulie.net/bitsavers.org/pdf/thinkingMachines/CM5/CMMDUsersGuide.pdf
- **[Walker 1992]** Walker, D. W. *Standards for Message Passing in a Distributed Memory
  Environment*. Technical Report ORNL/TM-12147, Oak Ridge National Laboratory, August 1992. (Report
  of the First CRPC Workshop on Standards for Message Passing in a Distributed Memory Environment,
  April 29–30, 1992, Williamsburg, VA.) OSTI ID 10170156.
  https://www.osti.gov/servlets/purl/10170156
- **[Walker 1994]** Walker, D. W. *The Design of a Standard Message Passing Interface for Distributed
  Memory Concurrent Computers*. Oak Ridge National Laboratory, 1994. DOI: 10.2172/10193294.
- **[Wikipedia Intel iPSC]** "Intel iPSC." Wikipedia. https://en.wikipedia.org/wiki/Intel_iPSC
- **[Wikipedia MPICH]** "MPICH." Wikipedia. https://en.wikipedia.org/wiki/MPICH
- **[Wikipedia Open MPI]** "Open MPI." Wikipedia. https://en.wikipedia.org/wiki/Open_MPI
- **[Zhou et al. 2026]** "Implementing True MPI Sessions and Evaluating MPI Initialization
  Scalability." arXiv:2605.03983. https://arxiv.org/html/2605.03983v1
