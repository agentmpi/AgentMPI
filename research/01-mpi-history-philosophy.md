# MPI: History, Standardization Process, and Design Philosophy

**Research notes for the AgentMPI paper.**
Compiled from primary sources: the MPI Standard documents themselves (MPI-1.1 through MPI-5.0), MPI Forum records, Argonne/ORNL technical reports, and the EuroMPI / SC / TOPC literature.

**Provenance note on quantitative claims.** Where this document reports page counts and function counts, they were measured directly from the official PDFs downloaded from `https://www.mpi-forum.org/docs/` (`pdfinfo` for page counts; regex extraction of C prototypes from Annex A "Language Bindings Summary" for function counts). The measurement methodology is stated inline so the numbers can be reproduced or corrected. Facts that could not be verified against a primary or strong secondary source are marked `[UNVERIFIED]`.

---

## 1. Pre-history: the portability crisis of 1990–1992

### 1.1 The landscape

By 1992 essentially every distributed-memory machine shipped with its own, mutually incompatible message-passing library. The MPI standard itself enumerates its own ancestry in the "Background of MPI-1.0" section, and this list is the single most authoritative statement of which systems mattered:

> "MPI sought to make use of the most attractive features of a number of existing message-passing systems, rather than selecting one of them and adopting it as the standard. Thus, MPI was strongly influenced by work at the IBM T. J. Watson Research Center, Intel's NX/2, Express, nCUBE's Vertex, p4, and PARMACS. Other important contributions have come from Zipcode, Chimp, PVM, Chameleon, and PICL."
> — [MPI Forum, *MPI: A Message-Passing Interface Standard, Version 5.0*, §1.2, June 5, 2025]

Systems, with dates and provenance:

| System | Origin | Key reference | Character |
|---|---|---|---|
| **NX / NX/2** | Intel Supercomputer Systems Division (iPSC/2, iPSC/860, Paragon) | Pierce, "The NX/2 Operating System," *Proc. 3rd Conf. on Hypercube Concurrent Computers and Applications*, ACM, 1988, pp. 384–390 | Vendor-native, `csend`/`crecv`, tag+node addressing |
| **Vertex** | nCUBE Corporation (nCUBE 2) | *nCUBE 2 Programmers Guide, r2.0*, December 1990 | Vendor-native hypercube messaging |
| **CMMD** | Thinking Machines Corporation (CM-5) | *CMMD Reference Manual / CMMD User's Guide*, v3.0, TMC, 1993 | `CMMD_send_block`, `CMMD_send_async`, `CMMD_send_noblock`; host/node and hostless models; built over Active Messages |
| **Express** | ParaSoft Corporation (commercial) | *Express Version 1.0: A Communication Environment for Parallel Computers*, ParaSoft, 1988 | Commercial, cross-vendor, but proprietary |
| **p4** | Argonne National Laboratory (Butler & Lusk) | Butler & Lusk, "Monitors, messages, and clusters: the p4 parallel programming system," *Parallel Computing* 20(4):547–564, April 1994; also ANL TM-ANL-92/17 (1992) | Descendant of the m4-based "Argonne macros"; message passing *and* shared-memory monitors in one library |
| **PARMACS** | GMD (Germany) + Argonne | Calkin, Hempel, Hoppe, Wypior, "Portable programming with the PARMACS message-passing library," *Parallel Computing* 20(4):615–632, April 1994; Bomans & Hempel, *Parallel Computing* 15:119–132, 1990 | The European de-facto draft standard; ESPRIT-backed |
| **PICL** | Oak Ridge National Laboratory | Geist, Heath, Peyton, Worley, *PICL: A Portable Instrumented Communications Library*, ORNL TM-11130, July 1990 | Portable subroutine layer **plus timestamped execution tracing** |
| **Chameleon** | Argonne (Gropp & Smith) | Gropp & Smith, *Chameleon Parallel Programming Tools Users Manual*, ANL-93/23, March 1993 | A *portability layer over portability layers*: p4, PICL, PVM, Intel NX, IBM EUI, TMC CMMD. Explicitly "very lightweight (low overhead)". Much of MPICH's early implementation technology came from Chameleon |
| **Zipcode** | Skjellum et al. (Caltech → LLNL → Mississippi State) | Skjellum, Smith, Doss, Leung, Morari, "The design and evolution of Zipcode," *Parallel Computing* 20(4):565–596, April 1994; Skjellum & Leung, DMCC5, 1990 | **The direct ancestor of MPI communicators** (see §3.6) |
| **CHIMP** | Edinburgh Parallel Computing Centre | *CHIMP Concepts*, June 1991; *CHIMP Version 1.0 Interface*, May 1992 | European portable layer |
| **PVM** | ORNL / Emory / UTK | Sunderam, "PVM: a framework for parallel distributed computing," *Concurrency: Practice and Experience* 2(4):315–339, December 1990 | The dominant portable system; see §1.2 |
| **TCGMSG** | Harrison (theoretical chemistry community; later part of Global Arrays / PNNL) | Harrison, "Portable tools and applications for parallel computing," *Int. J. Quantum Chemistry* 40(6), 1991 | Deliberately *minimal and robust*; explicitly written as a simpler, more robust reaction to PARMACS |
| **Venus / IBM EUI / MPL** | IBM T. J. Watson | Bala & Kipnis, "Process groups: a mechanism for the coordination of and communication among processes in the Venus collective communication library," IBM TJW tech. report, Oct. 1992; Bala, Kipnis, Rudolph, Snir, "Designing efficient, scalable, and portable collective communication libraries," IBM TJW, Oct. 1992 | **The direct ancestor of MPI process groups and the collective-operation design** |
| **Linda** | Gelernter (Yale) | Gelernter, "Generative communication in Linda," *ACM TOPLAS* 7(1):80–112, January 1985 | Associative tuple space, not point-to-point. Explicitly discussed at Williamsburg as a *higher* layer than the one to standardize. (A `p4-Linda` bridge existed: Butler, Leveton, Lusk, HPDC 1993) |
| **Occam / CSP / transputers** | Hoare; INMOS | Hoare, "Communicating Sequential Processes," *CACM* 21(8):666–677, August 1978 | Synchronous rendezvous channels baked into a *language*, not a library — the road MPI explicitly did not take |

### 1.2 PVM specifically

PVM began in summer 1989 when Vaidy Sunderam (Emory) visited ORNL to work with Al Geist on heterogeneous distributed computing [Geist & Sunderam, "PVM and MPI: a Comparison of Features"]. Bob Manchek (UTK) implemented the portable PVM 2.0 in 1991; Jack Dongarra made it publicly available; PVM 3.0 with a wholly new API shipped in 1993. Its architecture had four parts: a per-host **daemon**, an interactive **console**, a **group server**, and the **library** [CERN, "An Introduction to Message Passing Paradigms," CERN 98-08, p. 165].

Critically for our purposes, the PVM authors state their own priority ordering explicitly:

> "Portability was considered much more important than performance for two reasons: communication across the internet was slow; and, the research was focused on problems with scaling, fault tolerance, and heterogeneity of the virtual machine."
> — [Geist & Sunderam, "PVM and MPI: a Comparison of Features"]

This is the opposite trade-off from MPI, and it is why the two systems coexisted rather than one killing the other. PVM had a **virtual machine** abstraction (a mutable, dynamically-reconfigurable set of hosts), **dynamic process spawn**, and **fault detection**; MPI had a static process world and no failure model, but far better raw performance on MPP hardware.

### 1.3 Why portability was a crisis — and how the Forum framed it

The most useful primary source is the workshop report itself: D. Walker, *Standards for Message Passing in a Distributed Memory Environment*, ORNL TM-12147, August 1992 (also OSTI 10170156 / 7104668).

The report is unusually candid: it opens by **stating the counter-argument** before rebutting it.

> "It could be argued that the most difficult and time-consuming aspects of implementing an application on a distributed memory computing system are (1) devising a correct parallel program, and (2) optimizing the code to get efficient and scalable performance. Thus, the argument goes, in porting a code between two distributed memory computing systems the time spent in replacing the message-passing calls of one system with those of the other is negligible, and hence a standard doesn't gain you much."
> — [Walker, ORNL TM-12147, §2]

The rebuttal is a *forward-looking* one: the Forum argued that hardware and tooling would improve, at which point the message-passing layer would become the *binding constraint*, so the standard should be defined now in anticipation. The stated objectives are "portability and ease-of-use," plus two secondary benefits: high-level abstractions reduce programming errors (correctness), and a fixed interface gives vendors "a clearly defined set of routines that they could implement efficiently at a low level, or even provide hardware support for" (performance).

**Quantifying the lock-in.** The honest quantification is *structural rather than numeric*: no published figure for "person-months lost to porting" appears in the primary record `[UNVERIFIED]`. What can be stated precisely:

- At least **7 mutually incompatible vendor-native APIs** were in production use (Intel NX/NX-2, nCUBE Vertex, TMC CMMD, IBM EUI/MPL, Meiko, Cray, Fujitsu), plus at least **8 portable-layer projects** (PVM, p4, PARMACS, PICL, Express, Zipcode, CHIMP, TCGMSG), plus a **meta-portability layer** (Chameleon) whose entire reason for existing was to paper over the other layers. Chameleon's abstract states the crisis directly: *"the lack of a standard for message passing has hampered the construction of portable and efficient parallel programs. In an attempt to remedy this problem, a number of groups have developed their own message-passing systems, each with its own strengths and weaknesses. Chameleon is a second-generation system of this type."* [Gropp & Smith, ANL-93/23]
- The n-squared problem is the real cost: with *V* vendor APIs and *L* portable layers, every layer needs a backend per vendor and every application chooses a layer it can then not escape. Chameleon alone maintained backends for p4, PICL, PVM, Intel NX, IBM EUI (SP-1), and TMC CMMD (CM-5), with nCUBE in progress.
- The Williamsburg workshop drew **68 attendees** including representatives of the major hardware and software vendors [Walker, ORNL TM-12147, Abstract], which is itself evidence of how widely the pain was felt.

### 1.4 The "Onion Skin Model" — the single most transferable idea from the pre-history

The Williamsburg report contains an explicit architectural argument about *at which layer to standardize*, which the Forum called the **Onion Skin Model**:

> "At the lowest level, closest to the hardware, might be syntactically simple routines for moving packets along wires. Above this channel-addressed level might be a process-addressed level (where a 'process' may, or may not, be equivalent to a 'processor'), such as that defined by NX or Vertex on the iPSC and nCUBE machines, the commercially-available Express communication environment, or the PARMACS message-passing macros... Higher-level abstractions, for example, Linda, MetaMP, or Shared Objects, would lie above this level. Each level could be built using the level beneath, provided that the overhead in doing this was sufficiently low... These successive software levels form a series of layers, that, with some stretch of the imagination, resemble the multiple skins of an onion, with the hardware being at the center."
> — [Walker, ORNL TM-12147, §3]

And the decision:

> "However, it was pointed out that the hardware of different distributed memory computing systems is sufficiently varied that it is difficult to impose a low-level standard that is efficient on all machines. Therefore, it is more appropriate to define a standard at an intermediate level, and to implement this as efficiently as possible on each machine. There is still the possibility of defining higher-level standards on top of this intermediate level. Thus, the intermediate-level standard will be open and extendable."
> — [Walker, ORNL TM-12147, §3]

The workshop also already identified **message contexts** as a required feature, with exactly the motivation MPI later adopted: *"Often a parallel program divides naturally into different computational phases. Message contexts can be used to prevent nonblocking messages from different phases interfering with one another without the need for a time-consuming barrier synchronization between phases."* [Walker, ORNL TM-12147, §3.1]

---

## 2. The MPI Forum: process, cadence, governance

### 2.1 Chronology of formation

| Date | Event |
|---|---|
| Summer 1989 | PVM begun at ORNL (Sunderam/Geist) |
| Apr 29–30, 1992 | **Workshop on Standards for Message Passing in a Distributed Memory Environment**, Hilton Conference Center, Williamsburg, VA. Sponsored by the NSF **Center for Research on Parallel Computation (CRPC)**; Ken Kennedy agreed to CRPC sponsorship. 68 attendees. A working group and email list were established. [Walker, ORNL TM-12147; MPI-5.0 §1.2] |
| Aug 1992 | Dongarra, Hempel, Hey, and Walker begin the prototype "MPI-0" after a Gordon Conference in New Hampshire [Walker, "Some Reflections on the MPI Forum 1992-95"] |
| **Nov 1992** | **Birds-of-a-Feather session at Supercomputing '92, Minneapolis.** The MPI Forum is formally established. Decision: "place the standardization process on a more formal footing, and to generally adopt the procedures and organization of the **High Performance Fortran Forum**." Subcommittees formed per component area, each with its own email list. Target: draft by Fall 1993. [MPI-5.0 §1.2] |
| Nov 1992 / Feb 1993 | Preliminary draft "MPI-1" by Dongarra, Hempel, Hey, Walker (Nov 1992), revised February 1993 as ORNL TM-12231. Point-to-point only; **no collectives; not thread-safe**. [MPI-5.0 §1.2] |
| Jan 1993 | First MPI Forum meeting, Dallas |
| 1993 | Forum meets **every 6 weeks for two days** through the first 9 months of 1993 |
| Nov 1993 | Draft MPI standard presented at Supercomputing '93; public comment period opens |
| **May 1994** | **MPI-1.0 released** |

The 1994 CACM/IJSA-era retrospective gives the participation numbers and the budget reality:

> "The MPI standard was developed over a 12-month period in 1993-1994 of intensive meetings involving more than 80 people from approximately 40 organizations... The MPI meetings operated on a tight budget (actually no budget when the first meeting was announced). DARPA provided partial travel support for U.S. academic participants through the National Science Foundation. Support for several European participants was provided by the European Commission through its Esprit program."
> — [Message Passing Interface Forum / Dongarra et al., "MPI: A Message-Passing Interface Standard," *Int. J. Supercomputer Applications and High Performance Computing* 8(3/4), 1994]

The standard's own text says "about 60 people from 40 organizations" [MPI-5.0 §1.2]. Both figures appear in primary sources; the discrepancy is between "regular participants" and "everyone who attended". Cite the range **60–80 people from ~40 organizations**.

### 2.2 Operating rules

The rules are the most under-appreciated part of MPI's success, and they are stated crisply:

**Open membership.** *"Membership of [the MPI Forum] has been open to all members of the high performance computing community."* [MPI-5.0 §1.2] There is no membership fee — only per-meeting fees. [MPI Forum, "New to the MPI Forum," https://www.mpi-forum.org/new/]

**One vote per organization, earned by attendance.**

> "Formal voting at the meetings was by a single vote per organization; in order to vote, an organization needed to have had at least one representative at two of the last three meetings. To provide guidance for preparing formal proposals, frequent informal votes including all those present were held."
> — [Dongarra et al., IJSA 1994]

This rule is still in force: *"An organization has to be present two out of the last three meetings (incl. the current one) to be eligible to vote."* [Schulz, "The State of MPI: Current Standard and Future Plans," NHR PerfLab Seminar, June 30, 2026]

**Read-once, vote-twice.** A proposal is *read* at one meeting, then voted at a *later* meeting; a ballot needs quorum and a **3/4 majority of voting organizations**; and *"the vote is then repeated at a future meeting. After the second vote passes, the proposal is accepted."* [MPI Forum, "New to the MPI Forum"] In modern practice a "no-no vote" (a straw poll checking for hard objections) usually precedes the two formal votes — visible in the ULFM ballots (no-no 28-0-1 on 2022-09-30; first vote passed; second vote 25-0-6 on 2023-02-08) [github.com/mpi-forum/mpi-issues/issues/581].

**"Standardize existing practice, do not invent."** This is the Forum's most-cited norm. It is not phrased as a single sentence in the standard, but it is enacted in two places: (a) the goal *"Define an interface that is not too different from current practice, such as PVM, NX, Express, p4, etc., and provides extensions that allow greater flexibility"* [MPI-1.1 §1.1 — note this goal was **dropped** from the goal list by MPI-5.0]; and (b) the statement that MPI "sought to make use of the most attractive features of a number of existing message-passing systems, rather than selecting one of them and adopting it as the standard." In modern Forum practice the norm survives as a requirement for **prototype implementations** before standardization: *"Development of full proposal, in many cases accompanied with prototype development work."* [Schulz, State of MPI, 2026]

**Chapter committees.** Each chapter has named authors/owners responsible for cross-chapter consistency. The MPI-2.1 merge is the clearest documented example:

> Bill Gropp — Front Matter, Introduction, Bibliography · Richard Graham — Point-to-Point · Adam Moody — Collective · Richard Treumann — Groups, Contexts, and Communicators · Jesper Larsson Träff — Process Topologies, Info-Object, One-Sided · George Bosilca — Environmental Management · David Solt — Process Creation and Management · Bronis R. de Supinski — External Interfaces and Profiling · Rajeev Thakur — I/O · Jeffrey M. Squyres — Language Bindings · Rolf Rabenseifner — Deprecated Functions and Annex Change-Log · Alexander Supalov and Denis Nagorny — Annex Language Bindings
> — [MPI-2.2 §"MPI-1.3 and MPI-2.1", reproduced in MPI-4.0 front matter]

Modern structure: standing **working groups** (point-to-point, collectives, RMA, fault tolerance, sessions, tools, hybrid/accelerator, ABI) do the technical work between plenaries; plenary readings and votes happen at the (typically 4/year, 2 virtual + 2 hybrid) Forum meetings. [Schulz, State of MPI, 2026]

### 2.3 Version cadence, with dates taken from the standards' own change logs

All dates below are quoted from the "Version" entries in the front matter of *MPI: A Message-Passing Interface Standard, Version 5.0* (June 5, 2025) unless noted.

| Version | Date | Content |
|---|---|---|
| MPI-1.0 | **May 1994** (the document is dated May 5, 1994) | Original standard |
| MPI-1.1 | **June 1995** (doc dated June 12, 1995) | Errata/clarifications; "the changes from Version 1.0 are minor" |
| MPI-1.2 | **July 18, 1997** | Published as Chapter 3 of the MPI-2 document. **Exactly one new function**: `MPI_GET_VERSION` |
| MPI-2.0 | **July 18, 1997** | Dynamic processes, one-sided (RMA), parallel I/O, extended collectives, external interfaces, C++ bindings, F90 bindings, language interoperability |
| MPI-1.3 | **May 30, 2008** | Consolidation of MPI-1.1 + MPI-1.2 + errata. Formal end of the MPI-1 line |
| MPI-2.1 | **June 23, 2008** | Merge of MPI-1.3 and MPI-2.0 into a single document |
| MPI-2.2 | **September 4, 2009** | Corrections + a small number of extensions ("seven new routines" per the change log) |
| MPI-3.0 | **September 21, 2012** | Nonblocking collectives, neighborhood collectives, redesigned RMA, `MPI_T` tools interface, Fortran 2008 bindings, `MPI_Comm_idup`, `MPI_Comm_split_type`, removal of C++ bindings |
| MPI-3.1 | **June 4, 2015** | Corrections, portable `MPI_Aint` arithmetic, nonblocking collective I/O, `MPI_T` index-by-name |
| MPI-4.0 | **June 9, 2021** | Big-count (`_c` variants), persistent collectives, partitioned communication, **Sessions**, error-handling improvements, hardware-topology `MPI_COMM_SPLIT_TYPE`, info assertions |
| MPI-4.1 | **November 2, 2023** | Clarifications; memory-kind (GPU) info keys; automatic `Bsend` buffering; status/request query routines; deprecates `mpif.h` and `MPI_HOST` |
| MPI-5.0 | **June 5, 2025** | **Standard ABI** (new chapter + redone Annex A). Ratified at the March 2025 Forum meeting cycle; announced at ISC'25 |

**The ~decade dormancy.** MPI-2.0 (1997) → MPI-2.1 (2008) is an 11-year gap in which the Forum did not convene for new work. Schulz's growth chart labels this period simply *"10 year break"* [Schulz, State of MPI, 2026]. The Forum reconvened in 2008 first to *merge and errata* (MPI-1.3, MPI-2.1, MPI-2.2) and only then to extend (MPI-3.0). This ordering — consolidate the corpus before extending it — is itself a governance lesson.

**Officers.** Current: Chair **Martin Schulz** (TUM/LRZ), Secretary **Wes Bland** (Meta), Treasurer **Brian Smith** (ORNL), Document Editor **Bill Gropp** (NCSA/UIUC). Per-version chairs: **Rolf Rabenseifner** (MPI-2.1), **Bill Gropp** (MPI-2.2), **Richard Graham** (MPI-3.0), **Martin Schulz** (MPI-3.1, 4.0, 4.1). Steering Committee: Dongarra, Geist, Graham, Gropp, Lumsdaine, Lusk, Rabenseifner. For MPI-1.3/2.1, **Richard Graham** was Convener and Meeting Chair. [https://www.mpi-forum.org/ ; MPI-2.2 front matter] The original 1992–94 effort had no single "chair" in the modern sense; Dongarra, Hempel, Hey and Walker authored the seed draft, Lusk chaired the Language Binding subcommittee, and Steve Otto served as editor `[UNVERIFIED — Otto's exact title]`.

---

## 3. Design principles and stated philosophy

> **This is the section most load-bearing for AgentMPI.**

### 3.1 The goals, quoted verbatim

MPI-5.0 §1.1 "Overview and Goals" opens with a sentence that is itself a design manifesto:

> "MPI (Message-Passing Interface) is a message-passing library interface **specification**. All parts of this definition are significant. MPI addresses primarily the message-passing parallel programming model, in which data is moved from the address space of one process to that of another process through cooperative operations on each process... **MPI is a specification, not an implementation**; there are multiple implementations of MPI. This specification is for a **library interface**; **MPI is not a language**, and all MPI operations are expressed as functions, subroutines, or methods, according to the appropriate language bindings... The standard has been defined through an **open process** by a community of parallel computing vendors, computer scientists, and application developers."
> — [MPI-5.0, §1.1]

The explicit goal list (MPI-1.1 §1.1 through MPI-5.0 §1.1, with drift noted):

1. "Design an application programming interface (**not necessarily for compilers or a system implementation library**)."
2. "**Allow efficient communication**: Avoid memory-to-memory copying, allow overlap of computation and communication, and offload to communication co-processors, where available."
3. "Allow for implementations that can be used in a **heterogeneous** environment."
4. "Allow convenient **C and Fortran** bindings for the interface."
5. "**Assume a reliable communication interface**: the user need not cope with communication failures. Such failures are dealt with by the underlying communication subsystem."
6. *(MPI-1.1 only, dropped by MPI-5.0)* "Define an interface that is **not too different from current practice**, such as PVM, NX, Express, p4, etc., and provides extensions that allow greater flexibility."
7. "Define an interface that can be implemented on **many vendors' platforms, with no significant changes in the underlying communication and system software**."
8. "**Semantics of the interface should be language independent**."
9. "The interface should be designed to **allow for thread safety**."

Two of these are worth dwelling on. Goal 7 is the *anti-heroic-implementation* rule: MPI must be implementable by a vendor without them rewriting their OS. Goal 5 is the assumption that later became MPI's most-criticized weakness (§4.4).

### 3.2 Performance portability

MPI's framing of portability is deliberately *not* "write once, run anywhere at whatever speed you get." It is: define a surface that (a) is identical everywhere and (b) *does not semantically preclude* the fastest hardware path. The standard says this directly:

> "In order to be attractive to this wide audience, the standard must provide a simple, easy-to-use interface for the basic user **while not semantically precluding the high-performance message-passing operations available on advanced machines**."
> — [MPI-3.1 §1.8 "Who Should Use This Standard?"]

Mechanisms that implement performance portability:
- **Multiple send modes** (standard, buffered `B`, synchronous `S`, ready `R`) exist precisely so that the *portable* interface can express the *protocol* the user wants. Dongarra's own defence of MPI's size makes this explicit: *"the different communication modes arise mainly as a means of providing a set of the most widely-used communication protocols. For example, the synchronous communication mode corresponds closely to a protocol that minimizes the copying and buffering of data through a rendezvous mechanism."* [Dongarra et al., *MPI: The Complete Reference* / netlib "Why is MPI so big?"]
- **Derived datatypes** let the user *describe* a noncontiguous layout rather than pack it, so a smart implementation can use scatter/gather DMA (§4.7 for how this went).
- **Persistent requests** (MPI-1) and **persistent collectives** (MPI-4) let setup cost be amortized.
- **`MPI_Info` assertions** (MPI-4) let the user *relax* semantics they don't need, e.g. `mpi_assert_allow_overtaking`, `mpi_assert_exact_length`, `mpi_assert_no_any_tag` [MPI-4.1, communicator info keys].

### 3.3 The library approach: not a language, not a compiler

MPI is "just" a library with language bindings. The consequences the Forum bought with that decision:

- **No compiler dependency.** Any C or Fortran compiler works. There is no MPI compiler; `mpicc` is a wrapper script, not a translator.
- **Incremental adoption.** Existing serial code can be parallelized function-by-function.
- **Semantics defined language-independently, then projected onto bindings.** Goal 8 above. In MPI-4.0 the Forum went further and made this mechanical: *"All bindings generated via embedded Python... machine readable description of all MPI routines, automatic extraction of the interface"* [Schulz, State of MPI, 2026]. The Forum is now actively discussing *"split[ting] the MPI standard into semantics and bindings — one central semantics document, one document per language."*
- **The cost:** MPI cannot see program structure, so it cannot do the analyses a parallel *language* (HPF, Chapel, UPC) can, and it cannot optimize across call boundaries. This is the substance of the "assembly language" critique (§4.2).

### 3.4 Interface vs. implementation; protocol vs. policy

The standard is scrupulous about not specifying implementation. Beyond "MPI is a specification, not an implementation," the clearest statement of *protocol vs. policy* separation is in dynamic process management:

> "In developing the Dynamic Process Model, the MPI Forum decided **not to address resource control** because it was not able to design a portable interface that would be appropriate for the broad spectrum of existing and potential resource and process controllers. **MPI assumes that resource control is provided externally.**"
> — [MPI-4.1 §11.1; substantively identical text at MPI-3.1 §10.1]

Likewise the scope exclusions. MPI-1.1 §1.5 "What Is Not Included In The Standard?" lists:
- Explicit shared-memory operations
- "Operations that require more operating system support than is currently standard; for example, interrupt-driven receives, remote execution, or active messages"
- Program construction tools
- Debugging facilities
- Explicit support for threads
- Support for task management
- I/O functions

By MPI-3.1 §1.11 the list has shrunk to just three items — OS-heavy operations, program construction tools, debugging facilities — because **shared memory (MPI-3 `MPI_Win_allocate_shared`), threads (MPI-2 `MPI_Init_thread`), task management (MPI-2 spawn), and I/O (MPI-2 MPI-IO) were all pulled in**. The standard's own justification for the original exclusions is remarkably honest:

> "There are many features that have been considered and not included in this standard. This happened for a number of reasons, one of which is **the time constraint that was self-imposed in finishing the standard**. Features that are not included can always be offered as extensions by specific implementations."
> — [MPI-1.1 §1.5 / MPI-3.1 §1.11]

**Note the escape valve**: "can always be offered as extensions by specific implementations." That is how `MPIX_` prefixed extensions became the standard incubation path (e.g. `MPIX_Stream` in MPICH, ULFM in Open MPI).

### 3.5 Opaque objects and handles

> "MPI manages system memory that is used for buffering messages and for storing internal representations of various MPI objects such as groups, communicators, datatypes, etc. This memory is **not directly accessible to the user**, and objects stored there are **opaque**: their size and shape is not visible to the user. Opaque objects are accessed via **handles**, which exist in user space."
> — [MPI-4.1 §2.5.1 "Opaque Objects"]

Additional normative properties worth stealing:

- **Explicit allocate/free per object type**, with an `MPI_*_NULL` "invalid handle" constant per type, and handle-vs-null comparison as the validity test.
- **Deferred destruction with reference semantics**: *"A call to a deallocate routine invalidates the handle and marks the object for deallocation. The object is not accessible to the user after the call. However, MPI need not deallocate the object immediately. Any operation pending (at the time of the deallocate)... that involves this object will complete normally; the object will be deallocated afterwards."*
- **Handles are process-local and non-transferable**: *"An opaque object and its handle are significant only at the process where the object was created and cannot be transferred to another process."*
- **Predefined static handles** (`MPI_COMM_WORLD`, `MPI_INT`, `MPI_SUM`, …) that the user must not free.

**Why opacity mattered.** It is what made 30 years of implementation innovation possible without breaking source compatibility: MPICH represents `MPI_Comm` as an `int`, Open MPI as a `struct ompi_communicator_t*`, and both are conforming. The cost of this freedom was that MPI had **no ABI for 31 years** — which is exactly the debt MPI-5.0 paid off. The ABI chapter names the trade-off precisely:

> "The other chapters of the MPI standard specify an API that defines... a set of opaque handle types and named constants **without specifying their memory layout or values**. This allows implementations to choose these according to different types of requirement. However, this flexibility means that different implementations are **incompatible from the perspective of compiled applications**, because the ABI is not specified."
> — [MPI-5.0 §21.1 (chapter numbering: Application Binary Interface)]

The MPI-5.0 ABI is versioned independently of the API (starting at 1.0), uses header `abi/mpi.h` and library `libmpi_abi`, and coexists with each implementation's native ABI. Motivations given: third-party language bindings (Python, Julia, Rust) that bind by symbol name; package distribution (Spack, apt); implementation-agnostic tools; containers; build-once testing. Justification for doing it *now*: *"Architectural reasons not to are gone. Two platform ABIs [MPICH-family and Open MPI-family] cover >90% of HPC platforms."* [Schulz, State of MPI, 2026; MPI Forum ISC'25 BOF slides]

### 3.6 Communicators as safe communication contexts — the library-safety motivation

**This is the crown jewel of MPI's design and the idea most directly transferable to an agent protocol.**

The provenance: contexts came from **Zipcode**, which had them together with static process groups and virtual topologies in one object called a "mailer":

> "Features of Zipcode that were originally unique to it were its simultaneous support of static process groups, communication contexts, and virtual topologies, forming the 'mailer' data structure. Point-to-point and collective operations reference the underlying group, and use contexts to avoid mixing up messages... **Key features in Zipcode appear in the forthcoming MPI standard.**"
> — [Skjellum, Smith, Doss, Leung, Morari, "The Design and Evolution of Zipcode," March 8, 1994 / *Parallel Computing* 20(4), 1994]

Zipcode's enforcement rules are worth quoting because MPI inherited them exactly:

> "To enforce safe programming, the following strictures are placed on message-passing in Zipcode: Send/receive (point-to-point) and collective communication work **only within contexts of communication**; **Wildcarding, where permitted, does not violate context boundaries**."
> — [Skjellum et al., "The Multicomputer Toolbox"]

MPI's own chapter on the subject is explicitly framed as a *library-support* chapter, not a naming chapter:

> "This chapter introduces MPI features that support the **development of parallel libraries**... The key features needed to support the creation of robust parallel libraries are as follows:
> • **Safe communication space**, that guarantees that libraries can communicate as they need to, **without conflicting with communication extraneous to the library**,
> • **Group scope for collective operations**, that allow libraries to avoid unnecessarily synchronizing uninvolved MPI processes (potentially running unrelated code),
> • **Abstract naming of MPI processes** to allow libraries to describe their communication in terms suitable to their own data structures and algorithms,
> • The ability to **'adorn' a set of communicating MPI processes with additional user-defined attributes**, such as extra collective operations."
> — [MPI-4.1 §7.1, §7.1.1]

And the definition of a context:

> "**Contexts** provide the ability to have separate safe 'universes' of message-passing in MPI. **A context is akin to an additional tag that differentiates messages. The system manages this differentiation process.** The use of separate communication contexts by distinct libraries (or distinct library invocations) **insulates communication internal to the library execution from external communication**. This allows the invocation of the library **even if there are pending communication operations** on 'other' communicators, and **avoids the need to synchronize entry or exit into library code**."
> — [MPI-4.1 §7.1.2]

The problem being solved: before communicators, a library that used tag `42` internally would silently corrupt a user program that also used tag `42`, or would be corrupted by a user's `MPI_ANY_SOURCE`/`MPI_ANY_TAG` receive. The only safe workarounds were (a) documenting a reserved tag range and hoping, or (b) barrier-synchronizing on entry and exit from every library call. Communicators eliminate both. Note the four distinct guarantees bundled into one object:

1. **Isolation** — a context is a namespace for matching that the user cannot forge and wildcards cannot escape.
2. **Membership** — the group defines both the rank namespace and the collective scope.
3. **Collective/point-to-point separation** — implementations allocate a *hidden* second context per communicator so that the point-to-point messages used to implement a collective cannot be intercepted by user point-to-point receives. MPICH does exactly this: *"an additional 'collective' context is allocated for each communicator... used during communicator construction to create a 'hidden' communicator (`comm_coll`) that cannot be accessed directly by the user."* [Gropp, Lusk, Doss, Skjellum, "A High-Performance, Portable Implementation of the MPI Message Passing Interface Standard," *Parallel Computing* 22(6):789–828, 1996]
4. **Extensibility via attribute caching** — `MPI_Comm_set_attr`/`get_attr` let a library attach state to a communicator "on par with MPI built-in features", which is how virtual topologies are implemented.

**Context ID allocation is a distributed consensus problem.** MPICH allocates context IDs collectively: *"all processes involved agree on a context that is currently not in use by any of the processes. One of the algorithms used... involves passing the highest context currently used by a process to an `MPI_Allreduce` with the `MPI_MAX` operation."* [Gropp et al., 1996] This makes communicator *creation* a synchronizing operation — which is precisely why MPI-3 added `MPI_Comm_idup` (§5.6).

Additional cited influences on the communicator design: D. Feitelson, *Communicators: Object-Based Multiparty Interactions for Parallel Programming*, Hebrew University TR 91-12, November 1991; and Skjellum, Doss, Bangalore, "Writing Libraries in MPI," *Proc. Scalable Parallel Libraries Conf.*, IEEE CS Press, Oct. 1993, pp. 166–173.

### 3.7 Thread safety and the `MPI_THREAD_*` levels

MPI-1 explicitly excluded threads (§3.4) but was designed *not to preclude* them — hence the reentrant, handle-based API with no global implicit state other than the world communicator. MPI-2 added `MPI_Init_thread(required, provided)` with four monotonic levels:

> - **`MPI_THREAD_SINGLE`**: Only one thread will execute.
> - **`MPI_THREAD_FUNNELED`**: The process may be multithreaded, but the application must ensure that **only the main thread makes MPI calls**.
> - **`MPI_THREAD_SERIALIZED`**: The process may be multithreaded, and multiple threads may make MPI calls, **but only one at a time**.
> - **`MPI_THREAD_MULTIPLE`**: Multiple threads may call MPI, with no restrictions.
>
> "These values are monotonic; i.e., `MPI_THREAD_SINGLE` < `MPI_THREAD_FUNNELED` < `MPI_THREAD_SERIALIZED` < `MPI_THREAD_MULTIPLE`. Different processes in `MPI_COMM_WORLD` may require different levels of thread support."
> — [MPI-4.1 §11.4.3 `MPI_INIT_THREAD`]

The design pattern is **negotiated capability with graceful degradation**: the application *requests* a level, the implementation *reports* what it provides, and the application checks `provided >= required` and adapts or aborts. The standard's own examples do exactly this (`if (provided < MPI_THREAD_MULTIPLE) ...`).

### 3.8 What is deliberately unspecified, and why that is a feature

MPI-4.1 §3.5 "Semantics of Point-to-Point Communication" specifies exactly four properties and nothing more:

**Order (non-overtaking) — SPECIFIED:**
> "Messages are **nonovertaking**: If a sender sends two messages in succession to the same destination, and both match the same receive, then this operation cannot receive the second message if the first one is still pending. If a receiver posts two receives in succession, and both match the same message, then the second receive operation cannot be satisfied by this message, if the first one is still pending. This requirement facilitates matching of sends to receives. It **guarantees that message-passing code is deterministic, if MPI processes are single-threaded and the wildcard `MPI_ANY_SOURCE` is not used** in receives."

Note the scope: non-overtaking is **pairwise (per source-destination-context), not global**. There is no total order across senders. And note the honesty: the standard flags its own ambiguity about the multithreaded case with an *"Advice to users: The MPI Forum believes the following paragraph is ambiguous and may clarify the meaning in a future version."*

**Progress — SPECIFIED, but minimally:**
> "If a pair of matching send and receive operations have been initiated, then **at least one of these two operations will complete**, independently of other actions in the system: the send operation will complete, unless the receive is satisfied by another message, and completes; the receive operation will complete, unless the message sent is consumed by another matching receive that was started at the same destination MPI process."

This is a weak liveness guarantee: it says a matched pair cannot both stall forever, but says nothing about *when*, nothing about progress without a matching pair, and nothing about whether progress requires the application to re-enter MPI. That last gap is the notorious "does MPI make progress in the background?" question, on which implementations differ wildly and portable applications must therefore call `MPI_Test` periodically.

**Fairness — DELIBERATELY UNSPECIFIED:**
> "**MPI makes no guarantee of fairness** in the handling of communication. Suppose that a send is started. Then it is possible that the destination MPI process repeatedly posts a receive that matches this send, yet the message is never received, because it is each time overtaken by another message, sent from another source... **It is the programmer's responsibility to prevent starvation in such situations.**"

(Exception: `MPI_WAITSOME`/`MPI_TESTSOME` *do* carry an explicit fairness requirement, which is precisely why they exist alongside `WAITANY`/`TESTANY`.)

**Buffering — DELIBERATELY UNSPECIFIED (with an escape hatch):**
> "Any pending communication operation... consumes system resources that are limited. Errors may occur when lack of resources prevent the execution of an MPI call. **High-quality implementations will use a (small) fixed amount of resources** for each pending send in the ready or synchronous mode and for each pending receive. However, **buffer space may be consumed to store messages sent in standard mode**... The amount of space available for buffering will be much smaller than program data memory on many systems. Then, **it will be easy to write programs that overrun available buffer space**."

The escape hatch: buffered mode (`MPI_Bsend` + `MPI_Buffer_attach`) has a *specified operational model* — "MPI specifies a detailed operational model for the use of this buffer. An MPI implementation is required to do no worse than implied by this model." So the standard's structure is: **unspecified by default (for performance), specified on demand (for predictability)**.

**Why leaving things unspecified is a feature.** Three reasons visible in the record: (i) it permits an implementation to choose eager vs. rendezvous per message size and per fabric without violating the standard (§7.5); (ii) it prevents the standard from mandating something a vendor's hardware cannot do cheaply, which is Goal 7; (iii) it makes the *specified* guarantees credible, because they are few enough to actually hold everywhere. The standard also uses "**high-quality implementation**" as a normatively-soft term to express expectations it declines to mandate — a useful rhetorical device.

### 3.9 The profiling interface (PMPI)

MPI-1 mandated, in the *standard itself*, that every MPI function also be reachable under a name-shifted `PMPI_` alias. The requirements are normative:

> To meet the MPI profiling interface, an implementation must:
> 1. "provide a mechanism through which all of the MPI defined functions... may be accessed with a name shift. This requires, in C and Fortran, an **alternate entry point name, with the prefix `PMPI_`** for each MPI function."
> 2. "ensure that those MPI functions that are not replaced may still be linked into an executable image **without causing name clashes**."
> 3. document layering between language bindings, so a profiler author knows at which level to intercept;
> 4. ensure wrapper functions are **separable** from the rest of the library;
> 5. "provide a **no-op routine `MPI_PCONTROL`** in the MPI library."
> — [MPI-2.2 §14.2.2 "Requirements"; identical in later versions]

The standard even specifies the *implementation techniques*: weak symbols (`#pragma weak MPI_Example = PMPI_Example`) where the toolchain supports them, otherwise a preprocessor-macro two-build approach; and it **requires one-function-per-compilation-unit granularity**: *"It is required that the standard MPI library be built in such a way that the inclusion of MPI functions can be achieved one at a time... This is necessary so that the author of the profiling library need only define those MPI functions that need to be intercepted."* [MPI-5.0 §"MPI Library Implementation"]

**Why it was mandated rather than left to implementations.** Because a tool ecosystem only exists if the interception mechanism is *portable*. PMPI is why TAU, mpiP, Scalasca, Vampir, Score-P, Intel Trace Analyzer, and Darshan all work on every MPI without vendor cooperation. It is the single clearest case of the standard spending complexity budget on *observability*.

**Known limitations, and successors.** PMPI is (i) single-tool — only one wrapper can own the symbol, so tool composition requires hacks like PN MPI [Schulz & de Supinski, "PN MPI Tools: A Whole Lot Greater Than the Sum of Their Parts," SC'07]; (ii) passive — a profiler sees arguments and timings but no *internal* implementation state; and (iii) awkward in non-C languages. MPI-3 added **`MPI_T`**, a control-variable / performance-variable introspection interface, to address (ii) [Ramesh et al., "MPI performance engineering with the MPI tool interface: the integration of MVAPICH and TAU," EuroMPI 2017 / *Parallel Computing*]. **QMPI** is the proposed successor for (i) and (iii): language-independent, dynamic-wrapper based, supporting **chains of multiple tools** [Elis, Yang, Schulz, "QMPI: A Next Generation MPI Profiling Interface for Modern HPC Platforms," EuroMPI 2019]. Schulz notes the open difficulty: *"Challenge: Sessions (!)"* — because a session-scoped world breaks the assumption of a single global interception point.

### 3.10 Errors: fatal by default, error handlers as attachable objects

> "MPI provides the user with **reliable message transmission**. A message sent is always received correctly, and the user does not need to check for transmission errors, time-outs, or other error conditions... If the MPI implementation is built on an unreliable underlying mechanism, then it is **the job of the implementor of the MPI subsystem to insulate the user from this unreliability**...
>
> Similarly, **MPI itself provides no mechanisms for handling MPI process failures**...
>
> **By default, an error detected during the execution of the MPI library causes the parallel computation to abort**, except for file operations. However, MPI provides mechanisms for users to change this default and to handle recoverable errors. The user may specify that no error is fatal, and handle error codes returned by MPI calls by themselves. Also, the user may provide **user-defined error-handling routines**, which will be invoked whenever an MPI call returns abnormally."
> — [MPI-4.1 §2.8 "Error Handling"]

Design points:
- **`MPI_ERRORS_ARE_FATAL` is the default** for communicators and windows; **`MPI_ERRORS_RETURN`** is the opt-in. Files default to `MPI_ERRORS_RETURN`. MPI-4 added **`MPI_ERRORS_ABORT`**, which aborts only the processes in the associated communicator/window/file rather than the whole job.
- **Error handlers are first-class opaque objects, attached per-object** (`MPI_Comm_set_errhandler`, `MPI_Win_set_errhandler`, `MPI_File_set_errhandler`, and in MPI-4 `MPI_Session_set_errhandler`), and inherited by derived objects. This is *scoped* error policy, not global.
- MPI distinguishes **program errors** (bad argument) from **resource errors** (out of buffers), and adds a normative expectation rather than a rule: *"A high-quality implementation will provide generous limits on the important resources so as to alleviate the portability problem this represents."*
- Error **classes** (a small portable set) are separated from error **codes** (implementation-specific, mapped to classes by `MPI_Error_class`) — a portability/expressiveness split worth copying.
- MPI-4 tightened the semantics substantially: *"Specify that `MPI_SUCCESS` indicates only the result of the operation, not the state of the MPI library"*; *"Localize error impact of some MPI operations — `MPI_ALLOC_MEM` will now raise an error on `COMM_SELF`, not `COMM_WORLD`"*; *"Specify that MPI should avoid fatal errors when the user doesn't use `MPI_ERRORS_ARE_FATAL`"*; and the default error handler can now be set at `mpiexec` time. Schulz's summary of what this buys: *"Point-to-point communication with sockets-like error handling; enables master/worker and other non-traditional types of applications... **BUT: Not full fault tolerance for MPI!**"* [Schulz, State of MPI, 2026]

---

## 4. Criticisms and known design failures, honestly reported

### 4.1 Verbosity and API surface

MPI-1 already drew this complaint, and Dongarra's rebuttal is the canonical defence:

> "One aspect of concern, particularly to novices, is the large number of routines comprising the MPI specification. In all there are **128 MPI routines**... There are two fundamental reasons for the size of MPI. The first is that MPI was designed to be rich in functionality... The second reason for the size of MPI reflects the diversity and complexity of today's high performance computers... **One could decrease the number of functions by increasing the number of parameters in each call. But such approach would increase the call overhead and would make the use of the most prevalent calls more complex.** The availability of a large number of calls to deal with more esoteric features of MPI allows one to provide a simpler interface to the more frequently used functions."
> — [Dongarra et al., netlib MPI book, "Why is MPI so big?", Sept. 1995]

The empirical counterweight, repeated by every tutorial since: *"Most MPI programs can be written using a dozen or less routines"* [LLNL HPC Tutorials, "What is MPI?"]. The **6-function subset** (`MPI_Init`, `MPI_Comm_size`, `MPI_Comm_rank`, `MPI_Send`, `MPI_Recv`, `MPI_Finalize`) is a real, teachable core. **The design lesson is the layered-difficulty property**: a small learnable core, with the long tail reachable only when needed.

### 4.2 "MPI is the assembly language of parallel computing"

The phrase is widely used but **has no single canonical origin**. Documented attributions:
- Bonachea & Duell attribute the sentiment to MPI's own developers: *"It has also been described by some of its developers as providing an 'assembly language for parallel processing'"* [Bonachea & Duell, "Problems with using MPI 1.1 and 2.0 as compilation targets for parallel language implementations," *IJHPCN* 1(1/2/3), 2004, doi:10.1504/IJHPCN.2004.007569].
- A dated attribution exists to Brad Chamberlain (Cray), 2000: *"MPI is often considered the 'portable assembly language' of parallel computing"* [quoted in Heroux et al., "Toward portable programming of numerical linear algebra on manycore nodes," OSTI 1109301].
- Pacheco's textbook uses it as a teaching framing [Pacheco, *Parallel Programming with MPI*, Morgan Kaufmann, 1997, p. 7].
- Attribution to Thomas Sterling appears in popular writing but I could not verify it `[UNVERIFIED]`; Sterling's documented position is a different (stronger) critique — that MPI's coarse-grained global-barrier model is inadequate for exascale, motivating ParalleX/HPX [HPCwire, "XPRESS: Route to Exascale," Feb. 28, 2013].

The substantive critique (Bonachea & Duell) is worth reading in full: MPI-1's two-sided model and MPI-2's RMA are both *poor compilation targets* for PGAS languages, because two-sided requires receiver cooperation and MPI-2 RMA's memory-access restrictions "would require conflict and alias analysis that is beyond the reach of current compilers." The counter-argument from the MPI community is that MPI is a good *library* foundation, not a good *compiler* foundation, and that the existence of Parallel HDF5, PnetCDF, Elemental, Global Arrays, PETSc and Trilinos on top of MPI proves the layering worked [see the 2015 mpich-devel "mpi is dying?" thread, lists.mpich.org].

### 4.3 MPI-2 dynamic process management (`MPI_Comm_spawn`) — slow/failed adoption

Squyres' retrospective is blunt about the political origin:

> "Although strong technical cases were not initially presented as to why dynamic processes needed to be included in the MPI-2 standard, it was seen as a **political necessity to address the PVM community's concerns**. In typical MPI fashion, the MPI-2 standard includes not only spawning, but a **total of three different models** for dynamic process management."
> — [Squyres, "The Spawn of MPI," ClusterMonkey]

The standard itself discourages the most obvious use: *"It is possible in MPI to start a static SPMD or MPMD application by first starting one process and having that process start its siblings with `MPI_COMM_SPAWN`. **This practice is discouraged primarily for reasons of performance.**"* [MPI-3.1 §10.3.2]

Concrete technical failures, catalogued by Dorier et al.:
1. **No wait/reap.** *"contrary to a fork(2)/execv(3) sequence that is usually followed by a wait(3)..., there is no function to check whether the spawned application has terminated. The only way the parent application can 'wait' for the child application is by having the child send a termination message... and call `MPI_Comm_disconnect`."* Resource reusability after a spawned app terminates is simply **not discussed in the standard**.
2. **No resource control** (by explicit Forum decision, §3.4) — so *where* children run is up to an unspecified external resource manager, coordinated only through `MPI_Info` keys like `hosts`/`hostfile`. To spawn onto idle cores the batch scheduler must be told to hold nodes back; to spawn onto busy cores the OS must oversubscribe, "which is often not the case on supercomputers."
3. **Vendors declined to implement it.** *"For these reasons IBM and Cray's implementations of MPI do not support `MPI_Comm_spawn`. IBM BlueGene/Q, in particular, has hardware limitations that make it impossible to implement."*
4. **No fault isolation** — parent and children share failure fate.
> — [Dorier, Dreher, Peterka, Ross, "MPI Jobs within MPI Jobs: A Practical Way of Enabling Task-level Fault-Tolerance in HPC Workflows," *FGCS*, 2019 / OSTI 1559603]

Also non-portable by design: **soft spawn** ("give me up to N") is optional — *"this is not completely portable, as implementations are not required to support soft spawning."* [MPI-3.1 §10.3.4]

**The lesson**: a feature standardized for political rather than technical reasons, without a companion resource-management interface and without a completion/reaping model, gets implemented badly, used rarely, and eventually superseded (by Sessions + malleability work, §5.1 and §6).

### 4.4 MPI-2 one-sided (RMA) — a genuine design failure, redesigned in MPI-3

MPI-2's RMA assumed **no** hardware coherence, which forced a conservative "separate memory model" with logically distinct public and private window copies:

> "MPI-2's RMA model is the direct predecessor to MPI-3's RMA model... However, **MPI-3 defines a completely new memory model and access mode that can rely on hardware coherence instead of MPI-2's expensive and limited software-coherence mechanisms.**"
> — [Hoefler, Dinan, Thakur, Barrett, Balaji, Gropp, Underwood, "Remote Memory Access Programming in MPI-3," *ACM TOPC* 2(2), 2015]

> "No coherence in the memory subsystem or network interface is assumed by the MPI-2 RMA 'separate' memory model... **This conservative model is a poor match for computers with coherent memory subsystems, as it does not provide access to the system's full performance and programmer productivity potential.**"
> — [Dinan, Balaji, Buntinas, Goodell, Gropp, Thakur, "An implementation and evaluation of the MPI 3.0 one-sided communication interface," *CCPE* 28(17), 2016]

MPI-3's fixes: the **unified memory model** (public == private, hardware propagates; queryable via the `MPI_WIN_MODEL` attribute returning `MPI_WIN_UNIFIED` or `MPI_WIN_SEPARATE`), **passive-target `MPI_Win_lock_all`/`MPI_Win_flush`** epochs, **new atomics** (`MPI_Fetch_and_op`, `MPI_Compare_and_swap`, `MPI_Get_accumulate`), **request-based RMA** (`MPI_Rput`/`MPI_Rget`), and new window flavours (`MPI_Win_allocate`, `MPI_Win_allocate_shared`, `MPI_Win_create_dynamic`). Note the escape hatch design: the unified model is **not required**; portable programs *query* and adapt. That is the same negotiated-capability pattern as `MPI_THREAD_*`.

### 4.5 The fault-tolerance gap

MPI's founding assumption — Goal 5, "assume a reliable communication interface," and §2.8's "MPI itself provides no mechanisms for handling MPI process failures" — is its most consequential omission. The Forum's own summary of the record:

> "**Initial approaches failed** — too heavy weight, too much oriented to one model... Nevertheless, interest is large. Continued work in the working group. Goal: minimal building blocks. First step: better error handling in MPI 4.0. Support for several FT models: fine grained → **ULFM** (for new apps); coarse grained → **ReInit** (to support existing checkpoint/restart-based apps); session-based. **BUT: still stuck on exact fault model.**"
> — [Schulz, State of MPI, 2026]

ULFM (User Level Failure Mitigation) has been in the Forum since ~2012 and is being standardized **in slices** rather than monolithically [github.com/mpi-forum/mpi-issues/issues/20]:
- **Slice 1** (`MPI_COMM_REVOKE`, `MPI_COMM_GET_FAILED`, `MPI_COMM_ACK_FAILED`, error classes, post-error semantics): passed no-no 28-0-1 (2022-09-30) and a second vote 25-0-6 (2023-02-08) [issue #581].
- **Slice 2** (`MPI_COMM_AGREE`): passed a no-no vote 27-0-3 (2023-12-04) but **failed ballot quorum** on the first vote (17 yes / 4 no / 9 abstain, 2023-12-05) and remains open [issue #582].
- Slice 3 (communicator repair / respawn) is still to come.

The design shape of ULFM is itself instructive: it does **not** provide recovery; it provides *detection and isolation primitives* (revoke a communicator so no process can hang on it; agree on a consistent view of who failed; shrink to a working group) and leaves policy to the application. `MPI_Comm_revoke` is essentially a distributed "poison the channel" primitive. Implementations may opt out: *"an implementation that never raises an exception related to process failures does not have to actually tolerate failures."* [issue #20]

### 4.6 MPI+X hybrid complexity, and the failure of the Endpoints proposal

MPI's per-process rank model fits badly with on-node threading. The proposed fix — **Endpoints** — would have let a process create additional addressable ranks bound to threads:

> "This proposal introduces a new communicator creation function that can be used to create additional ranks, or **endpoints**, at an existing MPI process. These new endpoints behave the same as processes and can be associated with threads, allowing threads to fully participate in MPI operations."
> — [`MPI_Comm_create_endpoints` proposal, mpi-forum-historic issue #380; and Dinan, Balaji, Goodell, Miller, Snir, Thakur, "Enabling MPI interoperability through flexible communication endpoints," EuroMPI 2013, pp. 13–18]

It was **suspended, not adopted**:

> "The MPI Forum had deliberated the MPI Endpoints proposal but **ultimately suspended it** on the prospect that existing MPI objects such as communicators and windows can expose the same level of logical communication parallelism as user-visible endpoints. In cases where MPI's semantics prevent users from exposing communication independence, this school of thought advocates the use of **MPI Info hints to relax the limiting MPI semantics**... These studies demonstrate that existing MPI mechanisms indeed perform as well as user-visible endpoints, and they are the basis for the introduction of new hints... in the latest MPI 4.0 standard."
> — [Zambre & Chandramowlishwaran, "Lessons Learned on MPI+Threads Communication," arXiv:2206.14285 / SC'22]

The MPICH team's objection is a *conceptual-integrity* argument worth quoting:

> "A key flaw in the endpoint proposal, from our view, is the **inflation of thread context into virtual processes**. To a multithreaded programmer, a process and a thread are separate concepts... With the endpoints proposal, the process becomes less identifiable. Users may have to manage their own endpoint ranks... The endpoints proposal also makes interthread messages equally accessible as interprocess messages. Since users rarely need interthread messages, **this inflation makes MPI more difficult to understand and use**."
> — [pmodels/mpich issue #3591]

What shipped instead: **implicit VCI (virtual communication interface) mapping** inside MPICH, which infers parallelism from the communicator/tag the user already supplies [Zambre et al., "How I learned to stop worrying about user-visible endpoints and love MPI," ICS 2020, doi:10.1145/3392717.3392773]; **`MPI_Info` assertions** in MPI-4.0; and the ongoing `MPIX_Stream` proposal in MPICH (arXiv:2208.13707).

**The lesson for us**: a proposal that adds a *new kind of nameable entity* to a protocol will lose to a proposal that re-uses existing entities plus hints, unless the new entity earns clearly better performance. It didn't.

### 4.7 The datatype engine

MPI derived datatypes are the design's most elegant idea and its most persistent implementation disappointment.

> "In practice, however, **few MPI implementations implement derived datatypes in a way that performs better than what the user can achieve by manually packing data** into a contiguous buffer and then calling an MPI function."
> — [Byna, Gropp, Sun, Thakur, "Improving the Performance of MPI Derived Datatypes by Optimizing Memory-Access Cost," *IEEE Cluster* 2003]

> "Data packing before and after communication can make up as much as **90% of the communication time** on modern computers. Despite MPI's well-defined datatype interface for non-contiguous data access, **many codes use manual pack loops for performance reasons**... MPI implementations in use today **interpret** datatypes at pack time, resulting in high overheads."
> — [Schneider, Kjolstad, Hoefler, "MPI Datatype Processing using Runtime Compilation," EuroMPI 2013]

Causes: recursive tree interpretation at pack time (mitigated by stack-based parsers), no memory-hierarchy-aware packing, and no normalization of equivalent type trees at commit time. Fixes explored: commit-time **normalization**, and **runtime compilation** to vectorized native pack code (`libpack`, LLVM-based), reported ~7× faster than prevalent implementations for 73% of the datatypes in a real application. Note also that finding an *optimal* tree representation is NP-hard for general constructors, with polynomial algorithms only for restricted subsets `[reported in the datatype-normalization literature; exact citation UNVERIFIED]`.

**The lesson**: giving users a *declarative description* of data layout is only a win if the runtime actually exploits it. A declarative interface with an interpreting (rather than compiling) backend is worse than no abstraction at all, because users route around it.

---

## 5. MPI-4 and MPI-5: the modern additions

### 5.1 Sessions (MPI-4.0)

**The problem.** The "World Model" has three hard limits, enumerated by the ORNL/Forum report:

> "MPI cannot be initialized within an MPI process from **different application components without a priori knowledge or coordination**; MPI **cannot be initialized more than once**; and MPI **cannot be reinitialized after `MPI_Finalize`** has been called."
> — [MPI Sessions: Second Demonstration and Evaluation of MPI Sessions Prototype, OSTI/DOE, doi:10.2172/1566099]

Plus the scalability argument that motivated it originally:

> "MPI includes all processes in `MPI_COMM_WORLD`; **this is untenable for reasons of scale, resiliency, and overhead.**... [Sessions] makes two key contributions: a tighter integration with the underlying runtime system; and a scalable route to communication groups."
> — [Holmes, Mohror, Grant, Skjellum, Schulz, Bland, Squyres, "MPI Sessions: Leveraging Runtime Infrastructure to Increase Scalability of Applications at Exascale," EuroMPI 2016, pp. 121–129, doi:10.1145/2966884.2966915]

**The model.** Four steps replace `MPI_Init`:
1. `MPI_Session_init(info, errhandler, &session)` — a **local**, non-collective handle to the MPI library. No global state, no synchronization.
2. `MPI_Session_get_num_psets` / `MPI_Session_get_nth_pset` — **query the runtime** for named *process sets* (e.g. `"mpi://WORLD"`, `"mpi://SELF"`, or site-defined names).
3. `MPI_Group_from_session_pset(session, pset_name, &group)` — materialize a group from a named set.
4. `MPI_Comm_create_from_group(group, stringtag, info, errhandler, &comm)` — build a communicator **without a parent communicator**.

Then `MPI_Session_finalize`. Sessions can be created and destroyed multiple times; multiple sessions coexist in one process with **isolated** settings and error handlers. Backwards compatibility is preserved by redefining `MPI_Init`/`MPI_Finalize` as the constructor/destructor of the built-in communicators; an application can use both models concurrently, and *"a component such as a library can make use of the Sessions Model to instantiate MPI resources without impacting the rest of the application"* [MPI-4.1 §11.1].

**Honest assessment.** A 2026 evaluation argues the original scalability motivation was overstated: *"concerns over the scalability of `MPI_COMM_WORLD` appear to have been overstated. Since the petascale era, MPI has proven its ability to scale to over a million processes, while the anticipated billion-process scale has yet to materialize... Empirical evidence suggests that `MPI_COMM_WORLD` has not posed a fundamental limitation to scalability."* [Kumar et al. (attribution approximate), "Implementing True MPI Sessions and Evaluating MPI Initialization Scalability," arXiv:2605.03983] The *durable* value of Sessions turns out to be **isolation and composition**, not scale — and it is now the vehicle for **malleability** and **fault isolation ("bubbles")** work [Schulz, State of MPI, 2026]. That paper also notes a real implementation cost: *"a true Sessions model demands a device-independent, standardized process-addressing mechanism"* and *"breaks the long standing assumption that a single, global initialization phase always occurs — an assumption that MPICH relies on for efficient global setup."*

### 5.2 Partitioned communication (MPI-4.0)

**Motivation**: in MPI+OpenMP or MPI+CUDA, many threads/kernels contribute to one message. Before MPI-4 you had to either serialize at a thread barrier before a single `MPI_Isend`, or issue many small messages (paying matching overhead and thread contention).

**Mechanism**:
- `MPI_Psend_init(buf, partitions, count, datatype, dest, tag, comm, info, &request)` / `MPI_Precv_init(...)` — persistent, declares the buffer split into `partitions` equal partitions. Called **once**, by a single thread.
- `MPI_Start(&request)` — arms the operation; all partitions become inactive.
- `MPI_Pready(i, request)` (also `MPI_Pready_range`, `MPI_Pready_list`) — called **from inside the parallel region**, marks partition `i` ready. Local, nonblocking, lightweight.
- `MPI_Parrived(request, partition, &flag)` — receiver-side early-arrival query, also callable from a parallel region.
- `MPI_Wait`/`MPI_Test` in a serial region completes the whole transfer; `MPI_Start` again reuses the same setup.

Key semantic choices:
- **No wildcards.** *"MPI Partitioned does not support wildcards as communication is initialized preemptively. This is beneficial for highly-threaded codes as it avoids matching list overheads."* [Worley et al., "Micro-Benchmarking MPI Partitioned Point-to-Point Communication," ICPP Workshops 2022, doi:10.1145/3545008.3545088]
- **Matching happens at init time, in posting order**, not at data-transfer time.
- **`MPI_Pready` does not mean "send now."** *"Marking data as ready does not necessarily mean the data is sent at that moment; the timing of when the data is actually sent is determined by the MPI runtime."* [Afsahi et al., ExaMPI 2024] The implementation may aggregate for bandwidth or ship immediately for latency: *"MPI can optimize for latency or bandwidth (or shift)"* [Schulz, State of MPI, 2026].
- **User defines the partitioning, not the library** — a deliberate simplification: *"The official MPI 4.0 interface asks the user to define the equal-size partitions rather than asking the library to handle all edge cases."* [Grant et al., "Implementation and evaluation of MPI 4.0 partitioned communication libraries," *Parallel Computing*, 2021]

Forward work: **device-side bindings** so `MPI_Pready` can be called from a CUDA/SYCL kernel (`__device__ int MPI_Pready(...)`), receiver-readiness guarantees, and collective variants [Schulz, State of MPI, 2026].

### 5.3 Persistent collectives (MPI-4.0)

For every nonblocking collective `MPI_I<op>`, MPI-4 adds `MPI_<op>_init` with identical parameters plus an `MPI_Info`. Rationale: *"a collective operation is done many times... The specific sends and receives represented never change (size, type, lengths, transfers). Opportunities: fixed cost for making optimizations can be amortized; static resource allocation can be done; special limited hardware can be allocated if available."* [Schulz, State of MPI, 2026] All arguments are frozen at init; persistent collectives cannot match non-persistent ones.

### 5.4 Big count (MPI-4.0)

`int` count arguments cap a single operation at 2^31−1 elements. Options considered: change `int`→`MPI_Count` (breaks ABI/source), polymorphic bindings (not possible in C), or **duplicate the interface with a `_c` suffix**. The Forum chose duplication. Measured: MPI-4.0's Annex A adds **154 `_c` variants**; MPI-4.1 and MPI-5.0 each have **159**. This was also the trigger for "Pythonization" — all bindings are now generated from a machine-readable specification embedded in the LaTeX source, which *"uncovered errors"*, gives *"more consistency"*, and opens the door to auto-generated PMPI tool wrappers [Schulz, State of MPI, 2026].

### 5.5 Error handling improvements (MPI-4.0/4.1)

See §3.10. The headline is `MPI_ERRORS_ABORT`, localized error scope, and the decoupling of `MPI_SUCCESS` from library liveness.

### 5.6 `MPI_Comm_idup` (MPI-3.0)

> "`MPI_COMM_IDUP` is a **nonblocking variant of `MPI_COMM_DUP`**. With the exception of its nonblocking behavior, the semantics of `MPI_COMM_IDUP` are as if `MPI_COMM_DUP` was executed at the time that `MPI_COMM_IDUP` is called. For example, attributes changed after `MPI_COMM_IDUP` will not be copied to the new communicator. All restrictions and assumptions for nonblocking collective operations apply... It is erroneous to use the communicator `newcomm` as an input argument to other MPI functions before the `MPI_COMM_IDUP` operation completes."
> — [MPI-4.1 §7.4.2]

Why it exists: `MPI_Comm_dup` is collective and (because of distributed context-ID allocation, §3.6) synchronizing. A library that dups a communicator on entry therefore imposes a barrier on its caller — exactly the cost communicators were supposed to eliminate. `MPI_Comm_idup` lets a library *begin* acquiring its private context and overlap that with useful work. MPI-4 adds `MPI_Comm_idup_with_info`.

### 5.7 MPI-5.0: the ABI

See §3.5. Ratified June 5, 2025. Single-feature release: one new chapter plus a redone Annex A. Header `abi/mpi.h` (with `#include <mpi.h>` still working — no source changes required), library `libmpi_abi`, independently versioned starting at 1.0. MPICH had implemented it at ratification time (heavily tested via mpi4py); Open MPI landed it in v6.0.0, building both `libmpi` (native ABI) and `libmpi_abi` by default, and **intentionally omitting Fortran ABI support** [docs.open-mpi.org, "MPI Forum ABI"]. Existing shim projects (Mukautuva, wi4mpi, MPItrampoline) can support it directly. A stubs repo exists at github.com/mpi-forum/mpi-abi-stubs. The Forum has begun MPI 6.0.

---

## 6. Quantitative facts (measured, with methodology)

### 6.1 Page counts

Measured with `pdfinfo` on the official PDFs from `mpi-forum.org/docs/`; MPI-1.0/1.1 taken from the `%%Pages:` header of the official PostScript.

| Version | Pages (measured) | Pages (Schulz 2026) |
|---|---|---|
| MPI-1.0 (May 1994) | 236 (PS) | 228 |
| MPI-1.1 (June 1995) | 239 (PS) | 238 |
| MPI-2.0 — *extensions document only* (July 1997) | **370** | — |
| MPI-1.1 + MPI-2.0 combined corpus | 238 + 370 = **608** | 608 |
| MPI-1.3 (May 2008) | 245 | — |
| MPI-2.1 (June 2008) | **608** | 608 |
| MPI-2.2 (Sept 2009) | **647** | 647 |
| MPI-3.0 (Sept 2012) | **852** | 852 |
| MPI-3.1 (June 2015) | **868** | 868 |
| MPI-4.0 (June 2021) | **1139** | 1139 |
| MPI-4.1 (Nov 2023) | **1166** | 1166 |
| MPI-5.0 (June 2025) | **1189** | 1189 |

My measurements agree exactly with Schulz's from MPI-2.1 onward. Note the MPI-2.0 subtlety: the 1997 document is 370 pages of *extensions*; the "608 pages" figure for that era is the combined MPI-1.1 + MPI-2 corpus a programmer had to read, which is why MPI-2.1's merged single document is also 608 pages. **Growth factor 1994 → 2025: ≈5.2× in pages.** Schulz's word-count chart gives roughly **70k words (MPI-1) → 180k (MPI-3) → 250k (MPI-4/5)** [Schulz, State of MPI, 2026 — read off a chart, treat as approximate].

### 6.2 Function counts

Methodology: extract unique `MPI_*` C prototypes (lines matching `^(int|double|MPI_<type>)\s+MPI_Name(`) from **Annex A "Language Bindings Summary → C Bindings"** of each official PDF, deduplicated. Counts include `MPI_T_*` tools routines, `MPI_*_f2c`/`c2f` conversion routines, and deprecated bindings listed in the annex.

| Version | C prototypes in Annex A | of which `_c` (big-count) variants | Base routines |
|---|---|---|---|
| MPI-1.1 / MPI-1.2 / MPI-1.3 | **129** (MPI-1.3) | — | 129 |
| MPI-2.1 | **316** | — | 316 |
| MPI-2.2 | **323** | — | 323 |
| MPI-3.0 | **414** | — | 414 |
| MPI-3.1 | **424** | — | 424 |
| MPI-4.0 | **647** | 154 | 493 |
| MPI-4.1 | **676** | 159 | 517 |
| MPI-5.0 | **700** | 159 | 541 |

**Reconciliation with the folklore numbers.** The famous "128 routines" figure for MPI-1 is correct: my MPI-1.3 extraction yields exactly 129 names, and MPI-1.2 added exactly one function (`MPI_Get_version`) to MPI-1.1 — so **MPI-1.1 = 128, MPI-1.3 = 129**. This independently confirms Dongarra's "in all there are 128 MPI routines." The Wikipedia claim that "MPI-2's LIS specifies over 500 functions" counts C **and** C++ **and** Fortran bindings together, not distinct C routines; the distinct-C-routine count for MPI-2.1 is 316. LLNL's "over 430 routines defined in MPI-4" understates MPI-4.0 (should be ~493 base / 647 including `_c` variants) and appears to be a stale MPI-3.1-era figure. **Use my measured numbers and state the methodology; do not cite the tutorial numbers.**

**Summary line for the paper**: MPI grew from **128 routines / 236 pages (1994)** to **~700 C prototypes / 1189 pages (2025)** — roughly **5.5× in routines and 5× in pages over 31 years**, while remaining **source-compatible throughout** (any valid MPI-3.1 program is a valid MPI-4.0 program).

### 6.3 Forum participation

| Effort | Institutions listed as supporting (measured from the acknowledgements) | Notes |
|---|---|---|
| MPI-1.0 (1993–94) | ~40 organizations, 60–80 people | Per §1.2 of the standard and Dongarra et al. IJSA 1994 |
| MPI-2 (1995–97) | **52** | Includes TMC, Cray Research, Convex, DEC, SGI, Hughes Aircraft, Pratt & Whitney, US Navy — note how many no longer exist |
| MPI-2.2 (2008–09) | **59** | |
| MPI-3.0 (2008–12) | **61** | Peak |
| MPI-3.1 (2012–15) | **55** | |
| MPI-4.0 (2015–21) | **54** | ATOS, Arm, NVIDIA, Lenovo, Microsoft, RIKEN, Fujitsu, TUM, ... |
| MPI-4.1 (2021–23) | **53** | |
| MPI-5.0 (2023–25) | **~57** | Line-parsed; treat as ±2 |

Institution counts extracted by parsing the "The following institutions supported the MPI-x effort through time and travel support" blocks in the MPI-4.0/4.1/5.0 front matter. Roughly **50–60 organizations per major revision, stable for 30 years** — which is a remarkable governance datum in itself.

### 6.4 Other numbers worth quoting

- **68 attendees** at the Williamsburg workshop (April 29–30, 1992).
- Forum met **every 6 weeks for 2 days** through the first 9 months of 1993; ~1.5 years total to MPI-1.0.
- Voting threshold: **3/4 of voting organizations**, twice, at separate meetings.
- Dormancy: **11 years** between MPI-2.0 (1997) and MPI-2.1 (2008).
- MPI-1.2 contained exactly **one** new function.
- Google Scholar returns **>9,000 items** for "MPICH" [Balaji et al., "Translational research in the MPICH project," *J. Computational Science* 52:101203, 2021].

---

## 7. Key implementations and their layering

### 7.1 MPICH — the Abstract Device Interface

MPICH (Gropp, Lusk, Doss, Skjellum) was written **concurrently with the standard** and released the day MPI-1.0 was published — it was a existence proof as much as a product. Its lasting contribution is a layering discipline.

**The layers**, top to bottom:

1. **MPI API layer** — the `MPI_*` entry points; argument checking; the PMPI name-shift.
2. **MPIR** — device-independent reference implementations of everything: collective algorithms, datatype engine, communicator/group/context management, request objects.
3. **ADI (Abstract Device Interface)** — a set of `MPID_`-prefixed functions. *"A key design goal of MPICH is to allow downstream vendors to easily create vendor specific implementations. This is achieved by [the] Abstract Device Interface. ADI is a set of MPID prefixed functions that implements the functionality of MPI operations. For example, `MPID_Send` implements `MPI_Send`. **Nearly all MPI functions will call MPID counterparts first, allowing the device layer to either supply full functionality or simply fall back by calling `MPIR` implementations.**"* [pmodels/mpich, `doc/wiki/developer_guide.md`]

   That fallback property is the crucial architectural trick: **the device implements what it can accelerate and inherits the rest.** ADI has evolved through ADI-1 → ADI-2 → ADI-3; ADI-2 added tree-structured datatype descriptors traversed by the device for buffer translation, and support for user-packed buffers [Foster, Geisler, Gropp, Karonis, Lusk, Thiruvathukal, Tuecke, "Wide-Area Implementation of the Message Passing Interface," *Parallel Computing*, 1998].

   Performance nuance: *"For performance critical path, e.g. pt2pt and rma path, we call `MPID` layer directly from binding. This allows full inline build to achieve maximum compiler optimization. The other ADIs are not performance critical, but provided as **hooks** — `MPIR` layer will call these hooks at key points — to allow the device to properly set up and control implementation behaviors."*

4. **Devices**:
   - **CH3** — *"an example implementation of the MPICH ADI3 that provides an implementation of the ADI3 using a relatively small number of functions. These implement communication **channels**. A channel provides routines to send data between two MPI processes and to make progress on communications. A channel may define additional capabilities (optional features) that the CH3 device may use to provide better performance or additional functionality."* [MPICH `CH3_And_Channels.md`] Now in maintenance mode; *"still fully supported since there are vendors still basing on ch3."*
   - **CH4** — the current device. *"designed from the ground up to replace CH3... to achieve high performance by minimizing the runtime software overhead and by having an internal API that is well aligned with MPI functions."* CH4 defines its own second-level API: *"With most `MPID` functions, ch4-layer will check whether the communication is local (can be carried out using shared memory) and calls into either the **shm API** or the **netmod API**. It is possible to disable shm entirely."* Netmods target OFI/libfabric and UCX. Reported gains over CH3: >3× put/get latency and bandwidth on Omni-Path [Guo et al., "Efficient implementation of MPI-3 RMA over OpenFabrics interfaces," *Parallel Computing*, 2019]. New features (per-VCI threading, GPU IPC, partitioned communication) are CH4-only.
5. **Channels / netmods / shmmods**:
   - **Nemesis** — the default CH3 channel and the intra-node substrate. *"Our primary design goals, in order of priority, were **scalability, high-performance intranode communication, high-performance internode communication, and multimethod internode communication.** We ranked the design goals in order of priority to help us resolve conflicts between these goals."* Architecture: *"a shared-memory queue for scalable and efficient intranode message passing. To this we added **network modules** that interface with the queue mechanism, providing a unified method for sending and receiving messages."* [Buntinas, Mercier, Gropp, "Design and Evaluation of Nemesis, a Scalable, Low-Latency, Message-Passing Communication Subsystem," CCGrid 2006 / HAL hal-00344350]
6. **MPL** — a utility layer independent of MPI internals (`MPL_` prefix).
7. **ROMIO** — MPI-IO implemented *entirely on top of other MPI functions*, using no MPICH internals. Deliberately layered like the Fortran bindings.

**MPICH as a "vendor base."** This is a first-order fact for our paper:

> "As the leading implementation used by vendors (Intel, Cray, Microsoft, Tianhe, Sunway, etc.) for their own products. MPICH-derived implementations are used on practically all the largest supercomputers today as well as on clusters. The popular **Intel MPI, Cray MPI, and MVAPICH** implementations of MPI are also derived from MPICH... the first three exascale systems in the U.S. (**Aurora** at ANL, **Frontier** at ORNL, and **El Capitan** at LLNL) will use MPICH-based MPI implementations."
> — [Balaji, Gropp, Lusk, Thakur, "Translational research in the MPICH project," *J. Computational Science* 52:101203, 2021, doi:10.1016/j.jocs.2020.101203]

Corroborating: Intel MPI's own release notes state *"The Intel(R) MPI Library is based on MPICH2 from Argonne National Laboratory (ANL) and MVAPICH2 from Ohio State University (OSU)."* MVAPICH's own site: *"MVAPICH2 ... is an MPI-3.1 implementation **based on MPICH ADI3 layer**."* Cray's eager threshold knob is literally `MPICH_GNI_MAX_EAGER_MSG_SIZE`.

The **MPICH ABI Compatibility Initiative** (pre-dating the MPI-5.0 standard ABI) got MPICH, Intel MPI, Cray MPT and MVAPICH2 to agree on a common binary interface within the MPICH family — the empirical basis for the MPI-5 claim that "two platform ABIs cover >90% of HPC platforms."

### 7.2 Open MPI — the Modular Component Architecture

Open MPI (2004–, merging LAM/MPI, LA-MPI, FT-MPI and PACX-MPI) took the opposite structural bet: instead of one abstract device with fallbacks, **plugins everywhere**.

> "Open MPI's primary software design motif is a component architecture called the **Modular Component Architecture (MCA)**... Open MPI is comprised of three main functional areas: **MCA**, the backbone component architecture that provides management services for all other layers; **Component frameworks**, one per major functional area, which manage modules; and **Components**, self-contained software units that export well-defined interfaces and can be deployed and composed with other components."
> — [Graham, Woodall, Squyres, "Open MPI: A Flexible High Performance MPI," PPAM 2005]

Hierarchy: **Project → Framework → Component → Module**. A *component* is code on disk (often a `.so`); a *module* is a runtime instance of it. Frameworks are selected/excluded at run time via MCA parameters with the framework's own name (`--mca btl tcp,self`, `--mca pml ob1`, `^` to exclude). *"Each framework has different policies and usage scenarios; some will only use one component at a time while others will use all available components simultaneously."*

Projects: **OMPI** (MPI layer), **OPAL** (Open Portable Access Layer — OS/CPU portability), and historically **ORTE** (runtime, now PRRTE/PMIx).

The **point-to-point stack**, bottom-up [Graham et al., PPAM 2005; Shipman/Bosilca et al., ICL-UT-487-2006]:

- **BTL (Byte Transfer Layer)** — *"provides a uniform method of data transfer for numerous interconnects, both send/receive and RDMA based. **The BTL components are MPI agnostic acting as simple byte movers** with facilities for both local and remote completion... Local completion facilities are provided by a simple callback mechanism for each fragment scheduled on the BTL. Remote completion is accomplished via an **Active Message** style facility where Active Message callbacks are registered along with an Active Message Tag (AM-Tag) value during BTL initialization."*
- **BML (BTL Management Layer)** — *"In order to allow multiple components to use the BTL components, the BML provides facilities for BTL initialization and resource discovery... **After BTL initialization the BML layer is effectively bypassed via inline functions to the BTL**."* (A zero-cost-at-steady-state management layer — a nice pattern.)
- **PML (Point-to-point Messaging Layer)** — *"As the BTL components provide simple byte transfer services, **higher level MPI point-to-point semantics are implemented by the PML. Message scheduling and progression is located in the PML as well as MPI specific protocols such as short and long message protocols.** This isolation of higher level semantics allows the BTL components to be fairly simple and lightweight which allows easier adoption of new interconnect technologies. This structure also allows for **fine grain scheduling of messages across multiple interconnects** as well as the ability to change scheduling policies based on interconnect properties."*
  - **`ob1`** — the general-purpose PML for reliable interconnects; drives BTLs; does fragmentation, multi-rail striping, eager/rendezvous protocol selection.
  - **`cm`** — a thin PML for fabrics that implement MPI matching *in the network/library*; drives **MTL** (Matching Transport Layer) components instead of BTLs.
  - (`dr`, a network-fault-tolerant PML, existed historically.)
- **UCX** — in modern Open MPI, UCX is used via the `ucx` PML (matching offloaded into UCX) rather than as a BTL. Libfabric/OFI is available both as an MTL and as a BTL. *"UCX became the next generation, higher-abstraction InfiniBand support"* [Squyres, "The ABCs of Open MPI," EasyBuild Tech Talk, 2020].

Other frameworks worth naming for analogy: `coll` (collective algorithms — multiple components active simultaneously, selected per-communicator by priority), `io` (MPI-IO: ROMIO or OMPIO), `osc` (one-sided), `topo`, `sharedfp`.

**The architectural contrast to steal:**

| | MPICH | Open MPI |
|---|---|---|
| Extension mechanism | One abstract interface (`MPID_*`) with **fallback to generic `MPIR_*`** | Many typed **frameworks** with pluggable components |
| Binding time | Mostly compile time (inlined for the fast path) | Mostly **run time** (dlopen, MCA parameters) |
| Selection | Build-time device/channel choice | Run-time priority + include/exclude lists |
| Optimizing for | Minimal call-path overhead | Maximal composability and vendor independence |
| Semantics location | Split: `MPIR` generic + `MPID` device | Concentrated in PML; BTLs are "MPI agnostic byte movers" |

Both got to the same place — a thin, semantics-free data-movement layer under a thick, semantics-rich protocol layer — by different means.

### 7.3 Other implementations

- **MVAPICH / MVAPICH2 / MVAPICH2-GDR** (Ohio State, D.K. Panda) — MPICH-ADI3-based, specializing in InfiniBand/Omni-Path/RoCE/Slingshot, GPUDirect RDMA, and GPU-aware collectives for deep-learning workloads.
- **Intel MPI** — MPICH2 + MVAPICH2 derived; OFI/libfabric-based transport in modern versions.
- **Cray MPI (Cray MPICH / HPE MPT)** — MPICH-derived, tuned for Gemini/Aries/Slingshot.
- **Microsoft MPI**, **Tianhe MPI**, **Sunway MPI** — MPICH-derived.
- Historic: LAM/MPI, LA-MPI, FT-MPI, PACX-MPI (all merged into Open MPI); IBM Spectrum MPI (Open MPI derived); Fujitsu MPI.

### 7.4 The `MPIX_` extension convention

Not in the standard, but universal practice: implementations prefix non-standard extensions with `MPIX_` to signal "this is an incubating feature, not portable." ULFM shipped as `MPIX_Comm_revoke` in Open MPI for a decade before standardization; MPICH ships `MPIX_Stream`, `MPIX_Async`, `MPIX_Query_cuda_support`. This gives the ecosystem a **staging area** between "someone's fork" and "in the standard" — and it is where the "standardize existing practice" rule gets its raw material.

### 7.5 Eager vs. rendezvous protocols, and the crossover threshold

Neither protocol is in the standard — this is entirely an implementation concern that MPI's under-specification of buffering makes legal (§3.8).

- **Eager**: the sender transmits header+payload immediately, assuming the receiver can absorb it. If no matching receive is posted, the receiver buffers it as an "unexpected message." **Pro**: no handshake, minimum latency, sender decoupled from receiver. **Con**: consumes receiver buffer memory proportional to in-flight messages × peers; can exhaust memory at scale; may add a buffer→user copy.
- **Rendezvous**: the sender transmits only an envelope (RTS); the receiver replies CTS when a matching receive is posted and its buffer address is known; then data moves, typically by RDMA **directly into the user buffer (zero copy)**. **Pro**: bounded memory, scalable, no extra copy. **Con**: 3-message handshake latency; sender's completion now depends on receiver's progress.

> "Eager: reduces synchronization delays, best for latency; con: significant buffering may be required... can cause memory exhaustion. Rendezvous: scalable, robustness by preventing memory exhaustion; con: delay due to handshaking. **It is an implementation technique, it is not part of the MPI standard.**"
> — [Intel, "All the Things You Need to Know About Intel MPI Library"]

**How the threshold is chosen.** It is a tunable, defaulted per-transport (because the crossover is where RDMA setup cost equals the cost of the extra copy plus buffer pressure), and exposed as an environment variable / MCA parameter:

| Implementation | Knob | Typical default |
|---|---|---|
| Open MPI | `btl_sm_eager_limit` | ~4 KB (shared memory) |
| Open MPI | `btl_openib_eager_limit` (and `btl_openib_rndv_eager_limit`) | ~12 KB |
| Open MPI | `btl_tcp_eager_limit` | ~64 KB |
| Intel MPI | `I_MPI_EAGER_THRESHOLD`, `I_MPI_INTRANODE_EAGER_THRESHOLD` | ~256 KB |
| Intel MPI | `I_MPI_RDMA_EAGER_THRESHOLD` | ~16 KB |
| MVAPICH2 | `MV2_IBA_EAGER_THRESHOLD` | platform-specific |
| Cray MPICH | `MPICH_GNI_MAX_EAGER_MSG_SIZE` | platform-specific |

[SciNet, "Tuning Your MPI Application Without Writing Code"; Open MPI FAQ "Tuning the run-time characteristics of MPI shared memory communications"; Intel MPI docs. Defaults drift across versions — cite as illustrative, not normative.]

A visible user-facing consequence of the threshold: below the eager limit, `MPI_Send` typically returns without the receiver having posted a receive, so a program with mutual `MPI_Send` "works"; above the limit it deadlocks. This is the single most common MPI bug, and it is a *direct consequence of buffering being deliberately unspecified*. The standard's answer is `MPI_Ssend` (force rendezvous semantics) or `MPI_Bsend` (guaranteed buffering with a specified model).

---

## 8. Design lessons transferable to an agent protocol

Each lesson: one sentence of MPI fact, one sentence of implication for a protocol whose "processes" are LLM agents.

1. **Standardize at the intermediate layer.** The Williamsburg workshop explicitly rejected both a low-level (packet/channel) and a high-level (Linda / shared-objects) standard in favour of an "intermediate level" that others could layer on, on the grounds that hardware variation made a low-level standard non-portable and application variation made a high-level one non-general [Walker, ORNL TM-12147, §3]. → AgentMPI should specify agent-to-agent message semantics and grouping, **not** prompt formats or planner policies above it, and **not** HTTP/gRPC/queue mechanics below it; publish the "onion skin" explicitly so people know which ring they are extending.

2. **Standardize existing practice, do not invent.** The Forum required that features be drawn from working systems (NX, Express, p4, PARMACS, Zipcode, PVM, Venus) and, in modern practice, requires a prototype implementation before a proposal is read [MPI-5.0 §1.2; Schulz 2026]. → Every AgentMPI primitive should be traceable to a pattern already shipping in LangGraph / AutoGen / CrewAI / MCP / A2A, and the paper should include a provenance table; primitives with no prior art should be flagged as speculative.

3. **A specification is not an implementation, and saying so is load-bearing.** *"MPI is a specification, not an implementation; there are multiple implementations of MPI"* [MPI-5.0 §1.1] — this sentence is what let MPICH and Open MPI diverge architecturally for 20 years while remaining interchangeable. → AgentMPI must define conformance in terms of observable message semantics, and must ship at least two structurally different reference implementations to prove the spec is not accidentally describing one codebase.

4. **A library, not a language or a framework.** MPI added no syntax, required no compiler, and could be adopted function-by-function into existing code [MPI-5.0 §1.1, Goal 1]. → AgentMPI should be adoptable one call at a time inside an existing agent framework, with no requirement to restructure the agent loop or adopt a DSL; if adoption requires rewriting the agent, adoption will not happen.

5. **Communicators: bundle isolation + membership + naming + extensibility into one opaque object.** MPI's communicator simultaneously provides a forgeable-proof matching context, the rank namespace, the collective scope, and an attribute-cache for user extensions [MPI-4.1 §7.1.2]. → AgentMPI's central object should be an **agent-group-context** that at once isolates a sub-conversation, defines who is in it, defines the local names agents use for each other, and carries attached metadata (budget, policy, provenance) — resisting the temptation to split these into four unrelated concepts.

6. **Library safety is the killer application of contexts.** Communicators exist so that a library's internal messages cannot collide with the caller's, "even if there are pending communication operations on other communicators," and **without synchronizing entry or exit into library code** [MPI-4.1 §7.1.2]. → When agent A invokes a reusable sub-agent workflow B, B's internal messages must be unable to match A's outstanding receives, and invoking B must not require quiescing A — this is precisely the failure mode in current multi-agent systems where a sub-agent's output leaks into a parent's message stream.

7. **Wildcards must not escape the context.** Zipcode's rule, inherited by MPI, is that "wildcarding, where permitted, does not violate context boundaries" [Skjellum et al.] — `MPI_ANY_SOURCE` and `MPI_ANY_TAG` are bounded by the communicator. → An agent's "receive from anyone" primitive must be scoped to its group; a global "listen to everything" primitive is the single fastest way to destroy compositional safety and should not exist in the base spec.

8. **Give collectives a hidden private context.** Implementations allocate a second, user-inaccessible context per communicator so that the point-to-point messages implementing a collective cannot be intercepted by user receives [Gropp et al., 1996]. → AgentMPI's group operations (broadcast a task, gather results, vote/reduce) must run on an internal channel invisible to agent-level receives, or a single sloppy "receive any" in one agent will silently consume another agent's ballot.

9. **Opaque handles buy 30 years of implementation freedom — and cost you an ABI.** Handles let MPICH use `int` and Open MPI use a pointer while both conformed, but the absence of a specified layout meant no cross-implementation binaries for 31 years, finally fixed in MPI-5.0 [MPI-5.0 §21.1]. → AgentMPI should make group/session/message handles opaque *and* specify the wire representation from day one, because agent systems are polyglot and cross-vendor by nature — the ABI debt would be paid much sooner and much more painfully than MPI's.

10. **Specify the minimum liveness and ordering; leave the rest unspecified on purpose.** MPI specifies only pairwise non-overtaking and a weak two-party progress rule, and explicitly refuses to guarantee fairness or buffering [MPI-4.1 §3.5]. → AgentMPI should guarantee only per-channel FIFO between a given sender and receiver, state plainly that there is no global order, no fairness, and no bounded queueing, and require applications to prevent starvation — over-promising ordering will make every scalable implementation non-conformant.

11. **But provide a specified-behaviour escape hatch for the unspecified thing.** Buffering is unspecified in standard mode, yet `MPI_Bsend` + `MPI_Buffer_attach` come with "a detailed operational model" that implementations must not do worse than [MPI-4.1 §3.5]. → Alongside best-effort delivery, AgentMPI should offer an explicit *bounded-queue* mode where the buffer is user-supplied and the overflow behaviour is normative, so that agents which need predictability can pay for it.

12. **Negotiated capability with graceful degradation, not mandatory features.** `MPI_Init_thread(required, provided)` returns what the implementation actually offers on a monotonic scale, and the application checks and adapts [MPI-4.1 §11.4.3]; the same pattern reappears in `MPI_WIN_MODEL` (unified vs. separate) and MPI-4.1's `mpi_memory_alloc_kinds`. → AgentMPI should let a client *request* levels of guarantee (e.g. exactly-once vs. at-least-once delivery, tool-call transactionality, streaming-token support, context-window class) and let the runtime *report* what it provides, rather than mandating a level that half of implementations will fake.

13. **Mandate the profiling interface in the standard, not in the implementations.** PMPI's name-shifted `PMPI_` entry points, weak symbols, one-function-per-object-file granularity, and `MPI_Pcontrol` no-op are all *normative requirements* [MPI-2.2 §14.2.2], which is why a portable tool ecosystem exists. → AgentMPI must make interception normative — every operation reachable under a shifted name or through a mandated middleware chain — because in agent systems tracing, cost accounting, and safety auditing are not optional, they are the product.

14. **Design the interception mechanism for tool *chaining* from the start.** PMPI's single-owner symbol model means only one tool can wrap MPI at a time, which required hacks (PN MPI) and a proposed replacement (QMPI) [Elis, Yang, Schulz, EuroMPI 2019]. → AgentMPI's observability layer should be a composable middleware **chain** (tracer + cost meter + safety filter + replay recorder simultaneously), specified as such, rather than a single wrapper slot.

15. **Errors are fatal by default, but error policy is a first-class object attached per scope.** MPI defaults to `MPI_ERRORS_ARE_FATAL`, lets the user attach `MPI_ERRORS_RETURN`, `MPI_ERRORS_ABORT`, or a custom handler **per communicator/window/file/session**, and separates portable error *classes* from implementation-specific error *codes* [MPI-4.1 §2.8]. → AgentMPI should default to loud failure (a silently-degrading agent is worse than a dead one), but let each agent group carry its own error handler — so a speculative sub-agent's failure aborts only its bubble — and should define a small portable class taxonomy (`AGENT_ERR_BUDGET`, `AGENT_ERR_TOOL`, `AGENT_ERR_UNREACHABLE`, `AGENT_ERR_REFUSED`) over free-form codes.

16. **Localize the blast radius of an error.** MPI-4 explicitly changed `MPI_ALLOC_MEM` to raise on `COMM_SELF` rather than `COMM_WORLD`, and added `MPI_ERRORS_ABORT` to kill only the associated group [Schulz 2026]. → An AgentMPI error must name the narrowest scope it invalidates, so that one agent's tool failure does not tear down an unrelated concurrent branch.

17. **Assuming reliability was MPI's most expensive mistake — do not repeat it.** Goal 5 ("the user need not cope with communication failures") and §2.8 ("MPI itself provides no mechanisms for handling MPI process failures") produced a 14-year, still-unfinished fault-tolerance effort in which "initial approaches failed — too heavy weight, too much oriented to one model" and the Forum is *"still stuck on exact fault mode"* [Schulz 2026; ULFM issues #20, #581, #582]. → AgentMPI must have a fault model **in v1**, because LLM agents fail constantly and semantically (timeouts, refusals, hallucinated tool calls, budget exhaustion, rate limits) — retrofitting failure semantics onto a protocol that assumed reliability is provably a decade-scale project.

18. **When you do add fault tolerance, ship detection and isolation primitives, not recovery policy.** ULFM provides `MPI_Comm_revoke` (poison a context so nobody hangs on it), `MPI_Comm_get_failed`/`ack_failed` (get a consistent view), and `MPI_Comm_agree` (agree on that view), and leaves recovery entirely to the application; it is also **optional to actually tolerate failures** [ULFM issue #20]. → AgentMPI should give agents a way to revoke a group, enumerate failed peers, and reach agreement on the survivor set — and leave "retry / replan / escalate to human" to the application, which is the only layer that knows the task's semantics.

19. **Do not standardize a feature for political reasons without its companion interfaces.** `MPI_Comm_spawn` was added "as a political necessity to address the PVM community's concerns" [Squyres], shipped with three models, no reaping/wait primitive, no resource-control interface (explicitly out of scope), and no fault isolation — and consequently IBM and Cray declined to implement it [Dorier et al., 2019]. → If AgentMPI standardizes dynamic agent spawning, it must ship *with* a completion/reaping model, a budget/quota interface, and a failure-isolation boundary — or vendors will stub it and the feature will be dead-on-arrival.

20. **Separate protocol from policy, and say where policy lives.** *"The MPI Forum decided not to address resource control because it was not able to design a portable interface that would be appropriate for the broad spectrum of existing and potential resource and process controllers. MPI assumes that resource control is provided externally"* [MPI-4.1 §11.1]. → AgentMPI should specify how agents talk and be explicit that model selection, rate limiting, cost budgeting, and scheduling are the runtime's job — but, learning from spawn, it must at least define the *hook* (an `Info`-like key-value channel to the runtime) rather than leaving a hole.

21. **Provide an untyped key-value side-channel to the runtime, and let it grow.** `MPI_Info` started as a hint bag and became the standard's main extension point: hardware-topology guidance, `mpi_assert_allow_overtaking`, `mpi_memory_alloc_kinds` for GPU memory, session configuration [MPI-4.1]. → AgentMPI needs an `Info`-equivalent from v1 — the place where model preferences, temperature, cost ceilings, tool allowlists, and future unforeseen attributes live without a spec revision.

22. **Semantic relaxation via assertions beats new object types.** The Endpoints proposal (a new nameable entity per thread) was suspended in favour of `MPI_Info` assertions that relax semantics on existing communicators, after studies showed the existing mechanisms performed as well [Zambre & Chandramowlishwaran, arXiv:2206.14285]; MPICH's objection was the "inflation of thread context into virtual processes" making MPI "more difficult to understand and use." → Before AgentMPI adds a new first-class entity (a "sub-agent handle," a "thread of thought"), check whether an assertion on an existing group achieves the same thing — conceptual integrity is a performance feature for a protocol humans and LLMs must both reason about.

23. **Global, once-only initialization is a composability bug.** The World Model's three limits — "MPI cannot be initialized... from different application components without a priori knowledge or coordination; MPI cannot be initialized more than once; MPI cannot be reinitialized after `MPI_Finalize`" [OSTI doi:10.2172/1566099] — took 24 years to fix via Sessions. → AgentMPI must have **no global init**: obtaining a handle to the runtime must be local, non-collective, repeatable, and independently finalizable, so that a library that internally uses AgentMPI does not fight the application that also does.

24. **Query the runtime for named sets rather than assuming a fixed world.** Sessions replace `MPI_COMM_WORLD` with `MPI_Session_get_nth_pset` → `MPI_Group_from_session_pset` → `MPI_Comm_create_from_group`, so membership comes from the runtime by name and is not baked into the protocol [MPI-4.1 §11.3]. → AgentMPI should let an agent ask the runtime "which agent sets exist?" (`agents://all`, `agents://tool-users`, `agents://reviewers`) and materialize a group from a name, which is exactly what dynamic, heterogeneous agent populations need and what a static world model cannot express.

25. **Isolation enables recovery.** The Forum's current fault-tolerance direction is that "Sessions can support isolation of failed processes" — a failed session-bubble can be popped and recreated, giving both shrinking and non-shrinking recovery [Schulz 2026]. → In AgentMPI, the same object that gives you compositional isolation (the group/session) should be the unit of failure containment and restart; do not design two separate mechanisms.

26. **Partitioned communication: decouple "the data is ready" from "send it."** `MPI_Pready` marks a partition ready but "does not necessarily mean the data is sent at that moment; the timing... is determined by the MPI runtime," which may optimize for latency or bandwidth [Afsahi et al., ExaMPI 2024; Schulz 2026]. → An agent producing output incrementally (streaming tokens, partial tool results) should signal readiness per chunk while the runtime decides whether to forward immediately (latency) or coalesce (cost/throughput) — this is exactly the right abstraction for streaming LLM output and it already exists in MPI-4.

27. **Set up expensive matching once, then reuse.** Partitioned and persistent operations do all matching, buffer registration, and algorithm selection at `_init` time, so the hot loop is just `MPI_Start` + `MPI_Pready` + `MPI_Wait`; matching is by posting order and **wildcards are prohibited** to keep the fast path fast [Worley et al., ICPP-W 2022]. → For repeated agent interaction patterns (a fixed reviewer loop, a fixed map-reduce over documents), AgentMPI should let the topology and routing be established once and re-armed cheaply, and should forbid wildcards on those channels.

28. **Nonblocking object creation matters when creation is collective.** `MPI_Comm_dup` is synchronizing because context-ID allocation is a distributed `Allreduce`; MPI-3 added `MPI_Comm_idup` so libraries can overlap acquiring their private context with useful work [MPI-4.1 §7.4.2; Gropp et al. 1996]. → If AgentMPI group creation requires agreement among agents (it will, if group IDs must be unique), provide a nonblocking creation call from the start — otherwise every library that isolates itself imposes a barrier on its caller, which defeats the point.

29. **Layer the API by difficulty: a 6-function core, a 700-function tail.** MPI has ~700 C prototypes but "most MPI programs can be written using a dozen or less routines," and Dongarra's defence is that collapsing functions into parameterized mega-calls "would increase the call overhead and would make the use of the most prevalent calls more complex" [LLNL; netlib "Why is MPI so big?"]. → AgentMPI should have an explicitly-labelled Core Profile (send, receive, group-create, broadcast, gather, finalize) that is teachable in one page, with the rest of the surface clearly marked as opt-in — and should resist "one universal `agent_call()` with 40 options."

30. **A declarative data-description interface is worthless if the runtime only interprets it.** Derived datatypes were designed so implementations could scatter/gather without copying, but "few MPI implementations implement derived datatypes in a way that performs better than what the user can achieve by manually packing," and packing can be 90% of communication time until you compile the datatype at commit time [Byna et al. 2003; Schneider et al. 2013]. → If AgentMPI lets agents declare structured message schemas, the runtime must *compile* them (validators, serializers, projections generated once at registration) rather than interpret them per message — otherwise agent authors will bypass the schema and hand-serialize, and the abstraction will be dead weight.

31. **Provide a thin, semantics-free transport layer under a thick, semantics-rich protocol layer.** Both major implementations converged on this: Open MPI's BTLs are "MPI agnostic, acting as simple byte movers" with all MPI semantics in the PML; MPICH's ADI lets the device supply what it can and **fall back to generic `MPIR_` code for the rest** [Graham et al. PPAM 2005; MPICH developer guide]. → AgentMPI should define an "Agent Transport Layer" (move bytes/JSON between endpoints, report local and remote completion) and an "Agent Messaging Layer" (matching, contexts, groups, collectives, retries) — and adopt MPICH's fallback rule so a new transport can implement three functions and inherit everything else.

32. **Make the management layer bypassable at steady state.** Open MPI's BML does BTL discovery and initialization, and then "the BML layer is effectively bypassed via inline functions to the BTL" [Shipman et al., ICL-UT-487-2006]. → AgentMPI's provider-selection/negotiation layer should run at setup and then get out of the data path; per-message provider dispatch is a tax you pay on every token.

33. **Let the protocol layer schedule across multiple heterogeneous transports.** The PML "allows for fine grain scheduling of messages across multiple interconnects as well as the ability to change scheduling policies based on interconnect properties" [Graham et al.]. → AgentMPI's messaging layer should be able to route the same logical message over different substrates (in-process function call, local IPC, HTTP to a remote model, message queue) and stripe or choose per message, exactly as `ob1` stripes across rails.

34. **Have a threshold, expose it, and default it per-transport.** Eager vs. rendezvous is nowhere in the MPI standard, yet every implementation has it, and every one exposes it as a tunable with transport-specific defaults (`btl_sm_eager_limit` ~4 KB, `btl_openib_eager_limit` ~12 KB, `btl_tcp_eager_limit` ~64 KB, `I_MPI_EAGER_THRESHOLD` ~256 KB). → AgentMPI will have an analogous crossover — push the whole payload into the peer's context vs. send a reference/summary and let the peer pull — and it should be an implementation-tunable **outside** the spec, with per-substrate defaults, because the right threshold depends on context-window price and model, not on protocol semantics.

35. **Reserve an extension prefix and use it as a staging area.** Every MPI implementation ships non-standard features under `MPIX_` (ULFM lived there for a decade; `MPIX_Stream` lives there now), which gives the "standardize existing practice" rule something to standardize. → AgentMPI should reserve `AGENTX_`/`x-` and state normatively that experimental features go there, so that the spec has a documented on-ramp rather than a fork culture.

36. **Governance is a design artifact: open membership, one vote per organization earned by attendance, read once, vote twice, 3/4 majority.** These rules produced 50–60 participating organizations *per revision for 30 years* and a standard that has never broken source compatibility [MPI Forum "New to the MPI Forum"; measured institution counts §6.3]. → If AgentMPI is proposed as a standard rather than a library, the paper should specify the governance mechanism with the same seriousness as the wire format, because the vendor-lock-in problem in agent frameworks in 2026 is structurally identical to the message-passing problem in 1992 and will be solved by the same kind of process or not at all.

37. **Consolidate before you extend.** After an 11-year dormancy, the Forum's first act in 2008 was not new features but merging MPI-1.3, MPI-2.1 and MPI-2.2 into a single consistent document with named per-chapter owners; only then came MPI-3.0 [MPI-5.0 front matter; MPI-2.2 acknowledgements]. → An agent-protocol standard that accretes features without a periodic consolidation-and-errata pass will fragment; budget for merge releases and assign chapter owners.

38. **Backward compatibility is non-negotiable and achievable.** "Any valid MPI-3.1 program is compatible with MPI-4.0"; even the Sessions model was made backward compatible by redefining `MPI_Init`/`MPI_Finalize` as constructors/destructors of the built-in communicators [Wikipedia summary of MPI-4.0; Holmes et al., EuroMPI 2016]. → AgentMPI should treat "the previous version's programs still run" as a hard constraint, and should design new models (like Sessions) so the old model becomes a *derived special case* of the new one rather than a parallel track.

---

## Bibliography

*Formatted for BibTeX conversion. Entries verified against the source unless marked.*

**Standards documents**

1. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard*. Version 1.0, May 1994. (236 pp. PostScript.) https://www.mpi-forum.org/docs/mpi-1.0/
2. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard*. Version 1.1, June 12, 1995. (239 pp.) https://www.mpi-forum.org/docs/mpi-1.1/mpi-11-html/mpi-report.html
3. Message Passing Interface Forum. "MPI: A Message-Passing Interface Standard." *International Journal of Supercomputer Applications and High Performance Computing*, vol. 8, no. 3/4, 1994.
4. Message Passing Interface Forum. *MPI-2: Extensions to the Message-Passing Interface*. July 18, 1997. (370 pp.) https://www.mpi-forum.org/docs/mpi-2.0/mpi2-report.pdf
5. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 1.3*. May 30, 2008. (245 pp.) https://www.mpi-forum.org/docs/mpi-1.3/mpi-report-1.3-2008-05-30.pdf
6. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 2.1*. June 23, 2008. (608 pp.) https://www.mpi-forum.org/docs/mpi-2.1/mpi21-report.pdf
7. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 2.2*. September 4, 2009. (647 pp.) https://www.mpi-forum.org/docs/mpi-2.2/mpi22-report.pdf
8. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 3.0*. September 21, 2012. (852 pp.) https://www.mpi-forum.org/docs/mpi-3.0/mpi30-report.pdf
9. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 3.1*. June 4, 2015. (868 pp.) https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report.pdf
10. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 4.0*. June 9, 2021. (1139 pp.) https://www.mpi-forum.org/docs/mpi-4.0/mpi40-report.pdf
11. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 4.1*. November 2, 2023. (1166 pp.) https://www.mpi-forum.org/docs/mpi-4.1/mpi41-report.pdf
12. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 5.0*. June 5, 2025. (1189 pp.) https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report.pdf

**Forum process and retrospectives**

13. Walker, D. W. *Standards for Message Passing in a Distributed Memory Environment*. Technical Report ORNL/TM-12147, Oak Ridge National Laboratory, August 1992. (Report of the First CRPC Workshop, Williamsburg VA, April 29–30, 1992.) OSTI 10170156 / 7104668. https://www.osti.gov/servlets/purl/10170156
14. Dongarra, J. J., Hempel, R., Hey, A. J. G., Walker, D. W. *A Proposal for a User-Level, Message Passing Interface in a Distributed Memory Environment*. Technical Report ORNL/TM-12231, Oak Ridge National Laboratory, February 1993.
15. Walker, D. W. "Some Reflections on the MPI Forum 1992–95." Presentation slides. (Secondary source; used only for the Aug 1992 Gordon Conference and Jan 1993 Dallas meeting dates.)
16. MPI Forum. "New to the MPI Forum." https://www.mpi-forum.org/new/ (voting rules, reading/voting procedure)
17. MPI Forum. Officers and previous-effort chairs. https://www.mpi-forum.org/
18. Schulz, M. *The State of MPI: Current Standard and Future Plans*. NHR PerfLab Seminar Series, June 30, 2026. https://hpc.fau.de/files/2026/07/2026-06-perflab-stateofmpi.pdf
19. MPI Forum. *The Message Passing Interface (MPI): The New MPI 5.0 — Now with ABI Included!* BOF slides, ISC'25, June 2025. https://www.mpi-forum.org/bofs/2025-06-MPI-BOF-ISC25.pdf

**Pre-MPI systems**

20. Sunderam, V. S. "PVM: A framework for parallel distributed computing." *Concurrency: Practice and Experience*, 2(4):315–339, December 1990. doi:10.1002/cpe.4330020404
21. Geist, A., Beguelin, A., Dongarra, J., Jiang, W., Manchek, R., Sunderam, V. *PVM: Parallel Virtual Machine — A Users' Guide and Tutorial for Networked Parallel Computing*. MIT Press, 1994.
22. Geist, G. A., Kohl, J. A., Papadopoulos, P. M. "PVM and MPI: A Comparison of Features." *Calculateurs Parallèles*, 8(2), 1996. http://www.rrsg.uct.ac.za/projects/mti/pvmvsmpi.pdf
23. Butler, R., Lusk, E. "Monitors, messages, and clusters: the p4 parallel programming system." *Parallel Computing*, 20(4):547–564, April 1994. doi:10.1016/0167-8191(94)90028-0
24. Butler, R., Lusk, E. *User's Guide to the p4 Programming System*. Technical Report TM-ANL-92/17, Argonne National Laboratory, 1992.
25. Gropp, W. D., Smith, B. *Chameleon Parallel Programming Tools Users Manual*. Technical Report ANL-93/23, Argonne National Laboratory, March 1993. doi:10.2172/10191159
26. Geist, G. A., Heath, M. T., Peyton, B. W., Worley, P. H. *PICL: A Portable Instrumented Communications Library, C Reference Manual*. Technical Report ORNL/TM-11130, Oak Ridge National Laboratory, July 1990.
27. Skjellum, A., Smith, S. G., Doss, N. E., Leung, A. P., Morari, M. "The design and evolution of Zipcode." *Parallel Computing*, 20(4):565–596, April 1994. Preprint: https://surface.syr.edu/cgi/viewcontent.cgi?article=1025&context=npac
28. Skjellum, A., Leung, A. "Zipcode: a portable multicomputer communication library atop the Reactive Kernel." In *Proc. Fifth Distributed Memory Concurrent Computing Conference (DMCC5)*, pp. 767–776, IEEE Press, 1990.
29. Skjellum, A., Doss, N. E., Bangalore, P. V. "Writing Libraries in MPI." In *Proc. Scalable Parallel Libraries Conference*, pp. 166–173, IEEE Computer Society Press, October 1993.
30. Pierce, P. "The NX/2 Operating System." In *Proc. Third Conference on Hypercube Concurrent Computers and Applications*, pp. 384–390, ACM Press, 1988.
31. nCUBE Corporation. *nCUBE 2 Programmers Guide, r2.0*. December 1990.
32. Thinking Machines Corporation. *CMMD Reference Manual* and *CMMD User's Guide*, Version 3.0. 1993. http://bitsavers.org/pdf/thinkingMachines/CM5/CMMDUsersGuide.pdf
33. ParaSoft Corporation. *Express Version 1.0: A Communication Environment for Parallel Computers*. 1988.
34. Calkin, R., Hempel, R., Hoppe, H.-C., Wypior, P. "Portable programming with the PARMACS message-passing library." *Parallel Computing*, 20(4):615–632, April 1994.
35. Bomans, L., Hempel, R. "The Argonne/GMD macros in FORTRAN for portable parallel programming and their implementation on the Intel iPSC/2." *Parallel Computing*, 15:119–132, 1990. doi:10.1016/0167-8191(90)90036-9
36. Edinburgh Parallel Computing Centre. *CHIMP Concepts*, June 1991; *CHIMP Version 1.0 Interface*, May 1992.
37. Bala, V., Kipnis, S. *Process Groups: A Mechanism for the Coordination of and Communication Among Processes in the Venus Collective Communication Library*. IBM T. J. Watson Research Center technical report, October 1992.
38. Bala, V., Kipnis, S., Rudolph, L., Snir, M. *Designing Efficient, Scalable, and Portable Collective Communication Libraries*. IBM T. J. Watson Research Center technical report, October 1992.
39. Harrison, R. J. "Portable tools and applications for parallel computing." *International Journal of Quantum Chemistry*, 40(6):847–863, 1991. (TCGMSG)
40. Gelernter, D. "Generative communication in Linda." *ACM Transactions on Programming Languages and Systems*, 7(1):80–112, January 1985. doi:10.1145/2363.2433
41. Hoare, C. A. R. "Communicating Sequential Processes." *Communications of the ACM*, 21(8):666–677, August 1978. doi:10.1145/359576.359585
42. Feitelson, D. *Communicators: Object-Based Multiparty Interactions for Parallel Programming*. Technical Report 91-12, Dept. of Computer Science, The Hebrew University of Jerusalem, November 1991.
43. Butler, R., Leveton, A. L., Lusk, E. "p4-Linda: a portable implementation of Linda." In *Proc. 2nd Int. Symp. on High Performance Distributed Computing (HPDC)*, pp. 50–58, 1993. doi:10.1109/HPDC.1993.263858

**Implementations**

44. Gropp, W., Lusk, E., Doss, N., Skjellum, A. "A high-performance, portable implementation of the MPI message passing interface standard." *Parallel Computing*, 22(6):789–828, September 1996. doi:10.1016/0167-8191(96)00024-5
45. Balaji, P., Gropp, W., Lusk, E., Thakur, R. "Translational research in the MPICH project." *Journal of Computational Science*, 52:101203, 2021. doi:10.1016/j.jocs.2020.101203
46. Buntinas, D., Mercier, G., Gropp, W. "Design and Evaluation of Nemesis, a Scalable, Low-Latency, Message-Passing Communication Subsystem." In *Proc. IEEE/ACM CCGrid 2006*. HAL: hal-00344350
47. MPICH Project. *CH3 and Channels* and *Developer Guide*. https://github.com/pmodels/mpich/blob/main/doc/wiki/design/CH3_And_Channels.md ; .../doc/wiki/developer_guide.md
48. Guo, Y., Archer, C. J., Blocksome, M., Parker, S., Bland, W., Raffenetti, K., Balaji, P. "Efficient implementation of MPI-3 RMA over OpenFabrics Interfaces." *Parallel Computing*, 2019. doi:10.1016/j.parco.2018.12.005 (MPICH-OFI / CH4)
49. Foster, I., Geisler, J., Gropp, W., Karonis, N., Lusk, E., Thiruvathukal, G., Tuecke, S. "Wide-Area Implementation of the Message Passing Interface." *Parallel Computing*, 24(12–13):1735–1749, 1998. (MPICH ADI-1/ADI-2 description)
50. Graham, R. L., Woodall, T. S., Squyres, J. M. "Open MPI: A Flexible High Performance MPI." In *Proc. 6th Int. Conf. on Parallel Processing and Applied Mathematics (PPAM 2005)*, LNCS 3911, Springer, 2006. https://www.open-mpi.org/papers/ppam-2005/ppam-2005.pdf
51. Shipman, G. M., Woodall, T. S., Graham, R. L., Maccabe, A. B., Bridges, P. G. "Infiniband Scalability in Open MPI." / Bosilca et al., ICL-UT-487-2006. https://icl.utk.edu/files/publications/2006/icl-utk-487-2006.pdf (PML/BML/BTL layering)
52. Open MPI Project. *Modular Component Architecture (MCA)*. https://docs.open-mpi.org/en/main/mca.html
53. Open MPI Project. *MPI Forum ABI*. https://docs.open-mpi.org/en/main/building-apps/mpi-forum-abi.html
54. Squyres, J. M. *The ABCs of Open MPI*. EasyBuild Tech Talk #1, June 23, 2020. https://easybuild.io/files/easybuild-tech-talks/easybuild_tech_talks_01_OpenMPI_part1_20200623.pdf
55. Network-Based Computing Laboratory, Ohio State University. *MVAPICH: A High Performance MPI Implementation — Features*. https://mvapich.cse.ohio-state.edu/features/
56. Intel Corporation. *All the Things You Need to Know About Intel MPI Library*. (eager/rendezvous thresholds)
57. SciNet, University of Toronto. *Tuning Your MPI Application Without Writing Code*. https://oldwiki.scinet.utoronto.ca/images/f/f5/Mpi-tuning-parameters.pdf
58. Open MPI Project. *FAQ: Tuning the run-time characteristics of MPI shared memory communications*. https://www.open-mpi.org/faq/?category=sm

**MPI-3/4/5 features and evaluations**

59. Hoefler, T., Dinan, J., Thakur, R., Barrett, B., Balaji, P., Gropp, W., Underwood, K. "Remote Memory Access Programming in MPI-3." *ACM Transactions on Parallel Computing (TOPC)*, 2(2):9, 2015. doi:10.1145/2780584
60. Dinan, J., Balaji, P., Buntinas, D., Goodell, D., Gropp, W., Thakur, R. "An implementation and evaluation of the MPI 3.0 one-sided communication interface." *Concurrency and Computation: Practice and Experience*, 28(17):4385–4404, 2016. doi:10.1002/cpe.3758
61. Holmes, D., Mohror, K., Grant, R. E., Skjellum, A., Schulz, M., Bland, W., Squyres, J. M. "MPI Sessions: Leveraging Runtime Infrastructure to Increase Scalability of Applications at Exascale." In *Proc. 23rd European MPI Users' Group Meeting (EuroMPI 2016)*, pp. 121–129. doi:10.1145/2966884.2966915
62. Pritchard, H., Holmes, D., et al. *MPI Sessions: Second Demonstration and Evaluation of MPI Sessions Prototype*. DOE technical report, 2019. doi:10.2172/1566099
63. "Implementing True MPI Sessions and Evaluating MPI Initialization Scalability." arXiv:2605.03983, 2026. `[author list not fully verified]`
64. Grant, R. E., Dosanjh, M. G. F., Levenhagen, M. J., Brightwell, R., Skjellum, A. "Finepoints: Partitioned Multithreaded MPI Communication." In *ISC High Performance 2019*, LNCS 11501. (Precursor of MPI-4 partitioned communication.) `[cited from secondary sources; DOI unverified]`
65. Grant, R. E., et al. "Implementation and evaluation of MPI 4.0 partitioned communication libraries." *Parallel Computing*, 2021. https://sghafoor10.github.io/publications/pdfs/2021/
66. Worley, A., et al. "Micro-Benchmarking MPI Partitioned Point-to-Point Communication." In *Proc. 51st Int. Conf. on Parallel Processing Workshops (ICPP-W 2022)*. doi:10.1145/3545008.3545088
67. Temuçin, Y. H., Sojoodi, A. H., Alizadeh, P., Afsahi, A., et al. "Design and Implementation of MPI-Native GPU-Initiated MPI Partitioned Communication." In *ExaMPI Workshop @ SC 2024*. https://www.queensu.ca/academia/afsahi/pprl/papers/ExaMPI-2024.pdf
68. Holmes, D., et al. "Design of a portable implementation of partitioned point-to-point communication primitives." *Concurrency and Computation: Practice and Experience*, 2023. doi:10.1002/cpe.7655
69. Dinan, J., Balaji, P., Goodell, D., Miller, D., Snir, M., Thakur, R. "Enabling MPI interoperability through flexible communication endpoints." In *Proc. 20th European MPI Users' Group Meeting (EuroMPI 2013)*, pp. 13–18. doi:10.1145/2488551.2488553
70. Zambre, R., Chandramowlishwaran, A., Balaji, P. "How I learned to stop worrying about user-visible endpoints and love MPI." In *Proc. 34th ACM International Conference on Supercomputing (ICS 2020)*. doi:10.1145/3392717.3392773
71. Zambre, R., Chandramowlishwaran, A. "Lessons Learned on MPI+Threads Communication." In *Proc. SC 2022*. arXiv:2206.14285
72. MPI Forum. *Endpoints* (issue #56) and *MPI_Comm_create_endpoints Proposal* (historic issue #380). https://github.com/mpi-forum/mpi-issues/issues/56 ; https://github.com/mpi-forum/mpi-forum-historic/issues/380
73. MPI Forum. *User-Level Failure Mitigation* (issue #20); *ULFM slice 1* (#581); *ULFM slice 2: agree* (#582). https://github.com/mpi-forum/mpi-issues/issues/20
74. Bland, W., Bouteiller, A., Herault, T., Bosilca, G., Dongarra, J. "Post-failure recovery of MPI communication capability: Design and rationale." *International Journal of High Performance Computing Applications*, 27(3):244–254, 2013. doi:10.1177/1094342013488238

**Criticism and analysis**

75. Bonachea, D., Duell, J. "Problems with using MPI 1.1 and 2.0 as compilation targets for parallel language implementations." *International Journal of High Performance Computing and Networking*, 1(1/2/3):91–99, 2004. doi:10.1504/IJHPCN.2004.007569
76. Dongarra, J., et al. "Why is MPI so big?" In *MPI: The Complete Reference* / netlib MPI book, September 1995. https://netlib.org/utk/papers/mpi-book/node198.html
77. Dorier, M., Dreher, M., Peterka, T., Ross, R. "MPI Jobs within MPI Jobs: A Practical Way of Enabling Task-level Fault-Tolerance in HPC Workflows." *Future Generation Computer Systems*, 2019. OSTI 1559603
78. Squyres, J. M. "The Spawn of MPI." ClusterMonkey MPI column. https://www.clustermonkey.net/MPI/the-spawn-of-mpi.html
79. Byna, S., Gropp, W., Sun, X.-H., Thakur, R. "Improving the Performance of MPI Derived Datatypes by Optimizing Memory-Access Cost." In *Proc. IEEE Cluster 2003*. https://wgropp.cs.illinois.edu/bib/papers/pdata/2003/memcon-final.pdf
80. Schneider, T., Kjolstad, F., Hoefler, T. "MPI Datatype Processing using Runtime Compilation." In *Proc. 20th European MPI Users' Group Meeting (EuroMPI 2013)*. http://fredrikbk.com/publications/datatype_compilation.pdf
81. Schneider, T., Gerstenberger, R., Hoefler, T. "Micro-Applications for Communication Data Access Patterns and MPI Datatypes." In *EuroMPI 2012*. https://htor.inf.ethz.ch/publications/img/mpi-ddt-benchmark.pdf

**Tools interfaces**

82. Vetter, J., Chambreau, C. *mpiP: Lightweight, Scalable MPI Profiling*. 2005.
83. Ramesh, S., Mahéo, A., Shende, S., Malony, A. D., Subramoni, H., Panda, D. K. "MPI performance engineering with the MPI tool interface: The integration of MVAPICH and TAU." *Parallel Computing*, 77:19–37, 2018. doi:10.1016/j.parco.2018.05.003 (also EuroMPI 2017)
84. Elis, B., Yang, D., Schulz, M. "QMPI: A next generation MPI profiling interface for modern HPC platforms." In *Proc. 26th European MPI Users' Group Meeting (EuroMPI 2019)*. doi:10.1145/3343211.3343215
85. Schulz, M., de Supinski, B. R. "PN MPI Tools: A Whole Lot Greater Than the Sum of Their Parts." In *Proc. SC 2007*. doi:10.1145/1362622.1362663

**Textbooks (for framing quotes)**

86. Gropp, W., Lusk, E., Skjellum, A. *Using MPI: Portable Parallel Programming with the Message-Passing Interface*. MIT Press, 1994 (3rd ed. 2014).
87. Snir, M., Otto, S., Huss-Lederman, S., Walker, D., Dongarra, J. *MPI: The Complete Reference*. MIT Press, 1996 (2nd ed., 2 vols., 1998).
88. Gropp, W., Hoefler, T., Thakur, R., Lusk, E. *Using Advanced MPI: Modern Features of the Message-Passing Interface*. MIT Press, 2014.
89. Pacheco, P. S. *Parallel Programming with MPI*. Morgan Kaufmann, 1997. (Source of the "assembly language of parallel computing" teaching framing, p. 7.)

---

## Appendix: open items and things to verify before submission

- `[UNVERIFIED]` Steve Otto's exact formal title in the 1993–94 Forum (he is a co-author of *MPI: The Complete Reference* and is referred to as an editor in secondary sources).
- `[UNVERIFIED]` A quantified figure (person-months, dollars) for pre-MPI porting cost. The primary record argues the case structurally, not numerically. If the paper needs a number, either derive one from the count of incompatible APIs (§1.3) or state that none exists.
- `[UNVERIFIED]` Attribution of "MPI is the assembly language of parallel computing" to Thomas Sterling. Use the Bonachea & Duell or Chamberlain attributions instead.
- `[UNVERIFIED]` Exact NP-hardness result and citation for optimal MPI datatype-tree normalization.
- `[UNVERIFIED]` Full author list and venue for arXiv:2605.03983 ("Implementing True MPI Sessions...").
- `[UNVERIFIED]` DOI for Grant et al., "Finepoints" (ISC 2019).
- **Chapter number for the MPI-5.0 ABI chapter.** The HTML edition renders it as **Chapter 21**; the ISC'25 BOF slide says "Chapter 20 is new." Check the final PDF before citing a chapter number; citing it as "the Application Binary Interface chapter" is safe.
- **MPI-4.0 approval date.** The LLNL tutorial says "Sep 2023: The MPI-4.0 standard was approved," which contradicts the standard's own change log ("Version 4.0: June 9, 2021"). Use the standard's date.
- **MPI-1.1 date.** The document is dated June 12, 1995; Schulz's slide says November 1995. Use the document date.
- Institution counts in §6.3 were extracted by line-parsing the acknowledgement blocks; the MPI-5.0 figure (~57) may include one or two stray lines. Re-count by hand if the exact number is used in the paper.
