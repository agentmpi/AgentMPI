# Dossier 03 — Implementation Architecture and Fault Tolerance

**Paper:** *AgentMPI: A Message Passing Interface for Multi-Agent Systems*
**Scope:** source of truth for (a) the section justifying AgentMPI's abstract device interface — the narrow waist separating portable semantics from transport — and (b) the section justifying AgentMPI's fault-tolerance design.
**References:** `refs/03-implementations.bib`
**Verification convention:** every attribution, date, number, and standard section reference below was checked against a primary source (standards PDFs downloaded and searched locally, author-hosted paper PDFs, or project documentation). Claims I could not pin to a primary source are marked `[UNVERIFIED]`.

Two factual corrections to the brief, established below and load-bearing for how we write the related-work section:

1. **ULFM is not in MPI-5.0.** It remains a draft proposal of the MPI Forum Fault Tolerance Working Group. I searched the full text of the MPI-5.0 report (June 5, 2025) for `failure mitigation`, `ULFM`, `MPI_COMM_REVOKE`, and `MPI_Comm_shrink`: **zero hits.** See §B.1.
2. **IBM Spectrum MPI is not an MPICH derivative.** It is derived from Open MPI, and inherits MCA rather than ADI/CH4. The MPICH-derivative family is MPICH, Intel MPI, Cray MPICH, MVAPICH2, and ParaStation MPI. See §A.1.5.

---

## Part A — Implementation Architecture

### A.1 MPICH's layering

#### A.1.1 What the ADI is for

MPICH's organizing idea, present from the original 1996 *Parallel Computing* paper, is that "all MPI functions are implemented in terms of the macros and functions that make up the ADI," and that "all such code is portable" [gropp1996mpich]. The ADI was specified as a set of four capability groups: specifying a message to be sent or received; moving data between the API and the message-passing hardware; managing lists of pending messages (both sent and received); and providing basic information about the execution environment [gropp1996mpich, gropp1994adi]. Crucially, MPICH's authors observed that although MPI is a large specification, "the device-dependent parts are small" — this is the whole economic argument for a narrow waist, and it is exactly the argument AgentMPI needs to make about its own device layer.

The generations:

- **ADI-1** — the first-generation interface used in early MPICH [gropp1994adi]. Portability layer with an intermediate *channel device* abstraction [gropp1995channel].
- **ADI-2** — second generation, contemporaneous with the Nexus-based MPICH; extended ADI-1 with tree-structured datatype descriptors that the device traverses to perform buffer translation for non-contiguous data, and with support for MPI's user-packed buffers.
- **ADI-3** — the MPICH2 interface: "a full-featured abstract device interface" with many functions, hence "not a trivial task to implement all of them" [liu2004mpich2ib, gropp2004adi3]. This admission is the origin of CH3.

#### A.1.2 CH3 and the channel escape hatch

Because ADI-3 was too wide to be a practical porting target, MPICH2 introduced **CH3**: "a layer that implements the ADI3 functions, and provides an interface consisting of only a dozen functions. A 'channel' implements the CH3 interface" [liu2004mpich2ib]. A further narrowing, the **RDMA Channel interface**, exposed only five functions. The hierarchy therefore offered three distinct portability/performance trade-off points — ADI-3 (widest, fastest ceiling), CH3 (a dozen functions), RDMA Channel (five functions) [liu2004mpich2ib].

**This is the single most transferable structural lesson in the dossier.** MPICH did not pick one waist width. It shipped a *stack of waists*, each a strictly narrower and easier porting target than the one above, with the understanding that a narrower waist gives up peak performance because it loses information from the original MPI call. AgentMPI should say explicitly which of these it is doing.

CH3 channels included `sock` (TCP), `shm`/`sshm` (shared memory), and `nemesis`. CH3 is asynchronous and non-blocking throughout: a pointer to the user buffer is stored in an `MPID_Request` structure, queued on the virtual connection, and a *progress engine* updates pending requests and sets completion flags [MPICH2/CH3 design notes; see §A.4.3].

#### A.1.3 Nemesis

Nemesis (Buntinas, Mercier, Gropp; CCGrid 2006, pp. 521–530) is a low-level communication subsystem "designed and implemented to be scalable and efficient both in the intranode communication context using shared-memory and in the internode communication case using high-performance networks," and is "natively multimethod-enabled" [buntinas2006nemesis]. It was integrated into MPICH2 *as a CH3 channel* — that is, it slots into the narrow waist rather than replacing it.

The concrete mechanism is a lock-free shared-memory queue with **enqueue and dequeue costs of 6 and 11 instructions respectively** [buntinas2006nemesis]. The evaluation showed better intranode latency for non-zero messages than all other MPI implementations tested, and better intranode bandwidth than all others for messages larger than 256 KB. A companion study characterized the underlying SMP data-transfer mechanisms [buntinas2006smpdata].

The design lesson AgentMPI should extract: Nemesis's win came from making the *common local case* not pay for the general remote case, while still being reachable through the same narrow interface. Nemesis is a counterexample to the naive claim that a narrow waist costs performance — the waist was narrow *and* the fast path was fast, because the waist was placed where the semantics genuinely stopped being transport-specific.

#### A.1.4 CH4, netmods, and shmmods

CH4 is the successor device, designed with "minimum software overhead as the goal" and built "from the ground up" [raffenetti2017ch4]. Its architecture is three layers: the **ch4 core**, the **network modules (netmods)**, and the **shared-memory modules (shmmods)**. Netmods are OFI/libfabric and UCX; shmmods are POSIX and XPMEM. The CH4 core routes by locality: a `CH4_CALL` macro dispatches to netmod or shm based on an `is_local` predicate (or unconditionally to netmod when compiled with `MPIDI_CH4_DIRECT_NETMOD`) [mpichdevices].

The key CH4 design commitment, and the one AgentMPI should quote directly:

> "the ch4 infrastructure is designed to allow most MPI-level arguments to flow all the way down to the netmods and shmmods. In other words, little to no information is lost from the MPI call that triggered a data movement operation. This enables each netmod and shmmod to independently determine the optimal implementation for the operation. If a network or shared-memory-specific optimization is not readily available, the module can simply fall back to the ch4 core's active-message-based implementation."

CH4 thus inverts the CH3 trade-off. CH3 narrowed the interface by *discarding* MPI-level information, forcing the channel to re-derive intent. CH4 keeps a wider interface — high-level `netmod_isend` / `netmod_irecv` / `netmod_put` / `netmod_get` — precisely so the transport can exploit hardware capabilities (tag matching in the NIC, RDMA, triggered operations) and falls back to a generic active-message path only when it cannot. CH4 also supports two dispatch modes: multiple netmods (function pointers retained) or a single netmod inlined directly into the device layer, eliminating indirect-call overhead [raffenetti2017ch4].

MPICH 4.2.0's default device is `ch4:ofi`; `ch4:ucx` and the legacy `ch3:nemesis` remain configurable [mpichdevices]. (The build I inspected locally was configured `--with-device=ch4:ucx`.)

#### A.1.5 What lives above the ADI and what lives below

This is the exact question the AgentMPI paper asks about its own waist, so I state MPICH's answer as a table. Sources: the MPICH layered-structure diagram from the MPICH SC'21 BoF and the ch4 architecture description [raffenetti2017ch4, mpichdevices].

| Concern | Side of the ADI | Why |
| --- | --- | --- |
| Language bindings (C, Fortran) | **Above** | Machine-agnostic; pure surface syntax |
| Argument checking, error classes, error handlers | **Above** | Standard-defined semantics, no transport dependence |
| Communicator / group / context-ID management | **Above** | Pure bookkeeping over rank spaces |
| Derived datatype construction and management | **Above** | Layout algebra is transport-independent |
| Machine-independent collective algorithms | **Above** | Composed from point-to-point; portable by construction |
| Request objects and completion semantics | **Above** (interface); **below** (fulfilment) | The object model is standard; who advances it is not |
| Architecture-specific collectives | **Below** | Depend on topology, hardware multicast, offload engines |
| Eager/rendezvous protocol selection and thresholds | **Below** | Depends on per-transport buffer economics |
| Message matching engine and queues | **Below** (in practice) | Can be offloaded to the NIC; CH4 lets netmods own it |
| Progress engine | **Below** | Polls the actual transport completion queues |
| Address/endpoint management, connection establishment | **Below** | Fabric-specific |
| Data movement, registration, GPU-direct paths | **Below** | Entirely hardware-determined |
| Active-message fallback | **Below**, in the ch4 core | The generic implementation of anything a module cannot do natively |

The reusable principle: **the ADI boundary is drawn where a decision stops being determined by the standard's semantics and starts being determined by the cost model of the medium.** Everything whose *correct* answer is fixed by MPI's definition sits above; everything whose *good* answer depends on buffer sizes, latencies, and offload capability sits below.

Applied to AgentMPI, the analogous split is: message envelopes, communicator/group algebra, matching and ordering rules, collective *semantics*, and error classes go above the waist; model/provider selection, retry and backoff policy, token-budget-driven chunking (the eager/rendezvous analogue), context-window admission control, tool-call transport, and batching/caching go below it.

#### A.1.6 Reuse by derivatives

The MPICH ABI Compatibility Initiative, announced at SC13, lists the collaborating MPICH-derived implementations with their first ABI-compatible releases [mpichabi]:

| Implementation | First ABI-compatible release | Date |
| --- | --- | --- |
| MPICH | v3.1 | February 2014 |
| Intel MPI Library | v5.0 | June 2014 |
| Cray MPICH | v7.0.0 | June 2014 |
| MVAPICH2 | 2.0 | June 2014 |
| ParaStation MPI | 5.1.7-1 | December 2016 |

The MPI Forum's own 2025 framing is that there are two ABI families: (1) MPICH / Intel MPI / MVAPICH / Cray MPI, and (2) Open MPI / NVIDIA HPC-X / Amazon MPI / **IBM Spectrum MPI**, and that these two families "cover >90% of HPC platforms" [mpiforum2025mpi50 discussion; MPI BoF ISC25]. IBM's own user guide is unambiguous: "IBM Spectrum MPI is a complete MPI implementation, based on the Open MPI open source project" [ibmspectrum]. **The brief's placement of IBM Spectrum in the MPICH family is incorrect and should be fixed before submission** — an HPC PC will catch it immediately.

Note also that MPI-5.0's headline feature is a *standard* ABI (Chapter 20), which supersedes the private MPICH-family agreement [mpiforum2025mpi50]. The narrative arc is: a de facto ABI emerged from shared internals, then got standardized. That arc is a good template for how AgentMPI should describe the relationship between its reference implementation and a future portable interface.

MVAPICH2 is the clearest case of derivative reuse: it is MPICH-derived and implements its InfiniBand support at the CH3 level (the parameters are documented as applying to the "OFA-IB-CH3" and "OFA-iWARP-CH3" interfaces) [mvapich2userguide, liu2005mvapich]. Intel MPI and Cray MPICH exploit CH4/netmod: Cray's Slingshot support is realized as libfabric endpoints in the OFI netmod [raffenetti2017ch4].

---

### A.2 Open MPI's Modular Component Architecture

Open MPI took the opposite structural bet from MPICH: rather than one narrow waist with a channel escape hatch, it defined *many* waists, one per concern, and made component selection a runtime decision [gabriel2004openmpi, squyres2004mca].

**Terminology.** An **MCA framework** "manages zero or more components at run-time and is targeted at a specific task"; each framework supports exactly one *type* of component but may support many components of that type [ompimca]. The frameworks relevant to us:

| Framework | Responsibility | Representative components |
| --- | --- | --- |
| `pml` | Point-to-point Messaging Layer — implements MPI point-to-point semantics | `ob1`, `cm` |
| `btl` | Byte Transfer Layer — underlying transports, used by the `ob1` PML (and the `rdma` OSC) | `tcp`, `self`, `sm`/`vader`, `openib`, `ofi` |
| `mtl` | Matching Transport Layer — transports that do matching themselves, used exclusively by the `cm` PML | `psm2`, `ofi`, `portals4` |
| `coll` | Collective algorithms | `tuned`, `basic`, `libnbc`, `han`, `hcoll` |
| `osc` | One-sided communication (RMA) | `rdma`, `sm`, `ucx` |
| `io` | MPI I/O | `romio`, `ompio` |

The `pml`/`btl` versus `pml`/`mtl` split is the important structural detail, and the exact analogue of a question AgentMPI must answer. `ob1` implements matching *in Open MPI* and drives dumb byte-moving BTLs. `cm` delegates matching *to the transport* (an MTL such as PSM2 or OFI tagged), because some fabrics match in hardware. Open MPI did not choose; it made "who owns matching" a runtime-selectable property of the stack. AgentMPI faces the same fork: does the runtime own conversation matching, or can a provider that natively supports threaded conversations own it?

**Selection and priority.** `mca_base_select()` traverses the list of available components, calls each component's `mca_query_component()` function to obtain a module and an integer priority, and selects the highest priority (initialized to `INT32_MIN`); components without a query function are skipped, and a component may return `OPAL_ERR_FATAL` to abort selection outright when a user-required element is missing [ompimca; `opal/mca/base/mca_base_components_select.c`]. Every framework additionally exposes a top-level MCA parameter of the same name taking a comma-delimited component list, optionally prefixed with `^` for exclusion; inclusive and exclusive forms cannot be mixed [ompimca].

**`coll` decision functions.** Open MPI's `tuned` collective component has three modes: *fixed decision* (compiled-in decision trees), *forced algorithm* (user pins one algorithm), and *dynamic decision* (a rules file) [ompituned]. The rules file "effectively defines, for one or more collectives, a function of two variables, which given communicator and message size, returns an algorithm id." Resolution is two-phase: as communicators are constructed, a search using the communicator size selects a set of message-size rules to associate with that communicator; later, at each collective invocation, a search of those message-size rules selects the algorithm, using the nearest rule whose message size is less than the actual [ompituned]. Collectives with no rule fall back to the fixed decision tree.

That two-phase structure — bind a policy at communicator construction, resolve the specific choice per call — is directly reusable for AgentMPI's collective operations, where the analogous variables are group size and payload token count, and the analogous algorithms are (say) flat gather-to-one versus tree-structured hierarchical summarization.

**Runtime.** ORTE (the Open Run-Time Environment) provided process launch, out-of-band communication, and fault/state management across heterogeneous clusters, itself built out of MCA frameworks [castain2005orte]. ORTE was later factored out into PRRTE (PMIx Reference RunTime Environment), with PMIx providing the standardized process-management interface between application, runtime, and resource manager [castain2018pmix]. The trajectory — an implementation's private runtime becoming a standardized cross-implementation interface — mirrors the ABI story in §A.1.6 and is worth one sentence in AgentMPI's related work.

---

### A.3 The eager/rendezvous protocol

#### A.3.1 Mechanism

Two protocols, selected by message size against a threshold (the *eager limit*):

- **Eager.** The sender transmits envelope and payload immediately, assuming the receiver can store it. Advantages: reduces synchronization delay, simplifies programming (plain `MPI_Send` always completes), and avoids a round trip. Costs: it "requires significant buffering," "may require active involvement of CPU to drain network at receiver's end," and "may introduce additional copy (buffer to final destination)" [Gropp, CS598 lecture notes on buffering and message protocols].
- **Rendezvous.** The sender transmits only the envelope (a request-to-send, RTS). The receiver, once it has a matching posted receive with a destination buffer, replies with a clear-to-send (CTS); only then does the data move. "Robust and safe (except for limit on the number of envelopes…)", "may remove copy (user to user direct)", but "may introduce synchronization delays (waiting for receiver to ok send)" [ibid.].

**The eager limit exists because of receiver buffer pressure, not latency.** This is the point most secondary sources get backwards, and the point AgentMPI most needs. The buffering must be "reserved for arbitrary senders," and the "common approach in implementations is to provide same buffering for all members of `MPI_COMM_WORLD`; this is optimizing for non-scaleable computations" [ibid.]. Eager buffer space is O(peers) per process, so at scale the aggregate reservation, not the per-message latency, sets the limit. Latency argues for a *high* threshold; memory argues for a low one; the threshold is where the memory constraint binds.

Gropp's own crossover rule states the latency side precisely: "Eager is faster than rendezvous until data is unexpected: 2 × latency is smaller than the time to copy from buffer" [ibid.]. That is, once the extra copy induced by unexpected buffering costs more than the RTS/CTS round trip, rendezvous wins even on pure speed.

#### A.3.2 Real eager thresholds

All Open MPI and MPICH numbers below were **read directly from installed libraries on the machine used to prepare this dossier**, not taken from documentation: Open MPI 4.1.6 via `ompi_info --all --parsable`, MPICH 4.2.0 via a purpose-written MPI_T control-variable reader.

| Implementation | Parameter | Default | Notes |
| --- | --- | --- | --- |
| Open MPI 4.1.6 | `btl_tcp_eager_limit` | **65,536 B** (64 KiB) | `btl_tcp_rndv_eager_limit` also 65,536 |
| Open MPI 4.1.6 | `btl_vader_eager_limit` | **4,096 B** (4 KiB) | `btl_vader_rndv_eager_limit` 32,768 |
| Open MPI 4.1.6 | `btl_openib_eager_limit` | **12,288 B** (12 KiB) | `btl_openib_rndv_eager_limit` also 12,288 |
| Open MPI 4.1.6 | `btl_self_eager_limit` | **1,024 B** | `btl_self_rndv_eager_limit` 131,072 |
| Open MPI 4.1.6 | `btl_ofi_eager_limit` | **0** | OFI BTL does not use an eager path |
| MPICH 4.2.0 | `MPIR_CVAR_CH3_EAGER_MAX_MSG_SIZE` | **131,072 B** (128 KiB) | CH3 device |
| MPICH 4.2.0 | `MPIR_CVAR_CH4_OFI_EAGER_MAX_MSG_SIZE` | **−1** | Negative ⇒ inherit the provider's `max_msg_size` |
| MPICH 4.2.0 | `MPIR_CVAR_NEMESIS_SHM_EAGER_MAX_SZ` | **−1** | −1 ⇒ Nemesis chooses at runtime |
| MPICH 4.2.0 | `MPIR_CVAR_NEMESIS_SHM_READY_EAGER_MAX_SZ` | **−2** | −1 ⇒ always eager; −2 ⇒ choose |
| MVAPICH2 2.3.7 | `MV2_IBA_EAGER_THRESHOLD` | **HCA-dependent; 12 KB for ConnectX** | Documented default [mvapich2userguide §11.24] |
| MVAPICH2 2.3.7 | `MV2_VBUF_TOTAL_SIZE` | **HCA-dependent; 12 KB for ConnectX** | Should be set equal to the eager threshold [§11.104] |
| MVAPICH2 2.3.7 | `MV2_SMP_EAGERSIZE` | **Architecture-dependent** | Intranode eager/rendezvous switch [§11.105] |

Three observations worth a paragraph in the paper.

1. **The spread is nearly two orders of magnitude** (1 KiB for `self` to 128 KiB for CH3), and it tracks the cost of the receiver's buffer, not the wire. `self` (loopback within one process) has the *lowest* eager limit and the *highest* rendezvous limit, because there is no wire at all and copying is pure waste.
2. **Modern implementations increasingly refuse to pick a number.** MPICH's CH4/OFI and Nemesis defaults are sentinel values (−1, −2) meaning "ask the provider" or "decide at runtime." The eager limit has migrated from a constant to a negotiated property of the transport. AgentMPI should follow this: the token threshold at which a message body is inlined versus passed by reference should be a provider-negotiated property, not a constant in the spec.
3. **MVAPICH2 ties the eager threshold to the buffer size** (`MV2_IBA_EAGER_THRESHOLD` == `MV2_VBUF_TOTAL_SIZE`), making the buffer-pressure rationale explicit in the API surface.

#### A.3.3 The unexpected-message queue is a finite resource

When a message arrives with no matching posted receive, the implementation must put it somewhere. Eager messages go into pre-allocated *unexpected* buffers; rendezvous messages leave only an envelope. Both are finite. Gropp notes that rendezvous is "robust and safe" only "except for limit on the number of envelopes" [Gropp lecture notes] — the envelope queue can itself overflow.

What happens on overflow is implementation-defined, and the standard's position is that the program was wrong. MPI-5.0 §3.4 states the rationale plainly:

> "The reluctance of MPI to mandate whether standard sends are buffering or not stems from the desire to achieve portable programs. Since any system will run out of buffer resources as message sizes are increased, and some implementations may want to provide little buffering, MPI takes the position that correct (and therefore, portable) programs do not rely on system buffering in standard mode. Buffering may improve the performance of a correct program, but it doesn't affect the result of the program." [mpiforum2025mpi50, Communication Modes]

This is the **safe-program discipline**: a program is *unsafe* if its completion depends on the implementation providing buffering. The canonical unsafe program is a pairwise exchange in which every rank calls a blocking `MPI_Send` before its matching `MPI_Recv`; it "will succeed only if the communication system will buffer at least `count` words of data. Otherwise, the program will deadlock. The success of this program will depend on the amount of buffer space available in a particular implementation, on the buffer allocation policy used, and on other concurrent communication occurring in the system" [MPI standard, Buffering and Safety]. The disciplined fixes are `MPI_Sendrecv`, non-blocking `MPI_Isend`/`MPI_Irecv` pairs, or `MPI_Bsend` with a user-attached buffer whose size the program controls.

`MPI_Ssend` is the diagnostic tool: because synchronous mode cannot complete until a matching receive has started, replacing every `MPI_Send` with `MPI_Ssend` turns latent buffer-dependence into a deterministic deadlock, which is far easier to debug than an intermittent one. Gropp's guidance — "Ready can force Eager, but requires prepost of receive… Synchronous good when MPI implementation has inadequate flow control and messages are large" — makes explicit that the four send modes are a *flow-control vocabulary*, not four ways to say the same thing.

**Transfer to AgentMPI.** The direct analogue of the unexpected-message queue is an agent's inbox and, one level down, its context window. Both are finite; both are consumed by messages the receiver has not yet decided to attend to; and overflow is silent and catastrophic (truncation, or eviction of the very content the agent needed). The safe-program discipline transfers almost verbatim: *an AgentMPI program is unsafe if its correctness depends on the runtime buffering an unbounded number of unattended messages.* This is one of the strongest arguments in the paper, because unbounded inbox growth is a real and common failure mode in agent frameworks, and MPI has a thirty-year-old vocabulary for talking about it.

---

### A.4 Matching and progress

#### A.4.1 Matching semantics and non-overtaking

MPI matches an incoming message against posted receives on the triple (source rank, tag, communicator), with `MPI_ANY_SOURCE` and `MPI_ANY_TAG` wildcards. The ordering guarantee is **non-overtaking**: messages sent from one process to another on the same communicator are received in the order sent, and cannot be overtaken even if a later-posted receive would otherwise match first. Non-overtaking is what makes the queues *queues* rather than sets, and it is precisely what makes efficient matching hard.

#### A.4.2 The cost of matching

Implementations maintain two structures: the **posted-receive queue** (PRQ) and the **unexpected-message queue** (UMQ). Both are conventionally linked lists searched from the oldest entry, so search time grows linearly with queue depth.

Flajslik, Dinan, and Underwood formulated the problem and the fix (ISC High Performance 2016, LNCS 9697, pp. 281–299; Hans Meuer Award) [flajslik2016matching]:

> "To satisfy MPI ordering semantics in the presence of wildcards, current implementations store posted receive operations and unexpected messages [in] linked lists. As applications scale up, communication patterns that [grow] with number [of] processes or threads per process can cause those lists to grow [and] become a performance problem."

Their **binned matching** algorithm replaces the linked list with a hash map keyed on (source rank, tag, communicator), storing additional per-entry metadata and timestamps to preserve ordering in the presence of wildcards. With *b* bins the average search is O(n/b), degenerating to O(n) if all entries hash to one bin. Reported result: application speedups of **up to 3.5×** with no application changes [flajslik2016matching].

Complementary work: Ferreira et al. characterize matching behaviour via trace-based simulation, obtaining PRQ/UMQ search lengths and queue residence times without perturbing the application [ferreira2017matchsim]; Underwood et al. built a hardware acceleration unit for MPI queue processing a decade earlier [underwood2005matchunit]. The modern endpoint is NIC-offloaded tag matching, which is why Open MPI's `cm`/`mtl` path and MPICH's CH4 tagged-netmod path exist at all.

**Transfer to AgentMPI.** Conversation matching in a multi-agent runtime is structurally the same problem — match an arriving message against outstanding awaits on (sender, tag, group) — with two differences that make it *easier*: agent message rates are many orders of magnitude lower, so an O(n) linear scan is fine; and the non-overtaking guarantee may be more valuable per unit cost, because agents reason about conversational order in a way that numerical kernels do not. AgentMPI should therefore adopt MPI's matching semantics (including non-overtaking) and explicitly note that the matching-engine literature's *optimizations* do not transfer, only its *semantics*.

#### A.4.3 Progress

MPI does not guarantee that communication advances outside MPI calls. The MPICH2/CH3 design records the mechanism: "A 'progress engine' updates pending requests and sets a completion flag so that completed requests can be returned to the application. The progress engine can operate synchronously in a single threaded mode or asynchronously as a separate thread of execution" [MPICH2/CH3 design notes]. In practice most implementations make progress *only inside MPI calls* by default, which is why the idiom of periodically calling `MPI_Test` on outstanding requests exists at all, and why non-blocking collectives frequently fail to overlap with computation in the way users expect.

CH4's `MPIR_CVAR_CH4_OFI_ENABLE_DATA_AUTO_PROGRESS` and `..._CONTROL_AUTO_PROGRESS` control variables (default −1, "ask the provider") expose whether the fabric itself makes progress asynchronously — again, the CH4 pattern of deferring the decision to the transport.

#### A.4.4 Thread-safety levels

`MPI_Init_thread` requests one of four levels, in increasing strength: `MPI_THREAD_SINGLE` (one thread), `MPI_THREAD_FUNNELED` (only the thread that initialized MPI makes MPI calls), `MPI_THREAD_SERIALIZED` (multiple threads, but not concurrently in MPI), and `MPI_THREAD_MULTIPLE` (unrestricted concurrent calls). The implementation returns the level actually *provided*, which may be lower than requested — a negotiation, not a demand.

`MPI_THREAD_MULTIPLE` has historically cost real performance because the naive implementation is a global critical section around the progress engine and matching queues. Fine-grained locking and per-thread communication resources were the subject of sustained work [balaji2010threadsafety, dozsa2010concurrent], and modern CH4 addresses it with VCIs (virtual communication interfaces): a VCI is realized as a UCP worker in the UCX netmod or a libfabric endpoint in the OFI netmod, giving threads independent hardware contexts rather than contending on one [raffenetti2017ch4].

**Transfer to AgentMPI.** The four-level negotiated thread model is a good template for an agent runtime, where the analogous question is whether a single agent handle may be driven concurrently by multiple tool-call executions. AgentMPI should adopt the *shape* of `MPI_Init_thread` — request a level, receive the level actually provided — because it lets a provider that cannot support concurrent streaming say so at initialization rather than failing mid-run.

---

### A.5 Tooling and observability

The premise behind all of it, and the sentence AgentMPI should lift into its own motivation:

> A parallel program's behaviour is not visible from any single process's output. Each rank sees only its own local sequence of events; the *interaction* — who waited on whom, which collective was the straggler, which queue grew — exists only in the join across ranks, and no rank can print it.

**PMPI.** The MPI standard mandates that every `MPI_Xxx` entry point also be reachable under the name-shifted symbol `PMPI_Xxx`. A tool therefore defines its own `MPI_Xxx` that does its bookkeeping and then calls `PMPI_Xxx`; at link time the tool's symbol wins, and the real implementation is still reachable. As the MVAPICH/TAU integration paper puts it, "the performance profiler intercepts the MPI operation and performs the necessary timing operations within a wrapper function with the same name as the MPI operation. It then calls the corresponding name-shifted PMPI interface… This technique generates accurate profiles without necessitating application code changes" [ramesh2018mpit]. TAU performs PMPI profiling transparently via runtime pre-loading of shared objects [ramesh2018mpit, shende2006tau].

PMPI is a remarkable design decision for AgentMPI to imitate: **the standard reserved a second, parallel namespace purely so that third parties could interpose without forking the implementation or modifying user code.** An agent protocol that does the same — a mandated interposition point on every operation — gets its entire observability ecosystem for free.

**MPI_T.** Introduced in MPI-3.0 as the *MPI tool information interface*, with two parts: **control variables** (cvars), "through which the MPI implementation tunes its configuration," and **performance variables** (pvars), which "provide insight into internal performance information of the MPI implementation" [mpiforum2015mpi31 §14.3]. MPI-4 added a third part: an **events** interface letting tools "query available events within an MPI implementation and register callbacks for them" [mpiforum2025mpi50 §15]. The standard is explicit that the variable set is implementation-defined and non-portable: "any application relying on a particular variable will not be portable," and application programmers should "avoid being dependent on the existence of a particular control or performance variable" [mpiforum2015mpi31].

Concretely: the MPICH 4.2.0 build I inspected exposes **438 control variables** through MPI_T. The standard's own worked example is a PMPI tool that reads a pvar named `MPI_T_UMQ_LENGTH` — the unexpected-message-queue length — to "identify receive operations that occur during times with long message queues" [mpiforum2025mpi50 §15, Performance Variables]. That is exactly the §A.3.3 hazard, made observable. Practical experience with MPI_T is documented in [islam2014mpit] and the MVAPICH2/TAU integration [ramesh2018mpit].

The MPI_T design pattern worth stealing: **a self-describing, discoverable, explicitly non-portable introspection namespace, kept strictly separate from the portable API.** It lets implementations expose whatever they actually have without the standard having to guess, and it tells tool authors up front not to hard-code names.

**Tracing formats and tools.**

| Layer | Artifacts |
| --- | --- |
| Trace formats | SLOG-2 (scalable log files with near-constant-time access to arbitrary time intervals) [chan2000slog2, chan2008jumpshot]; OTF2, the successor format underpinning Score-P [eschweiler2011otf2] |
| Measurement infrastructure | Score-P — a joint measurement runtime shared by Periscope, Scalasca, TAU, and Vampir [knupfer2012scorep]; TAU [shende2006tau] |
| Visualization | Vampir [nagel1996vampir]; Jumpshot (the SLOG-2 viewer) [chan2008jumpshot] |
| Lightweight profiling | mpiP, from Vetter and McCracken's statistical scalability analysis of communication operations [vetter2001mpip]; Caliper, which provides performance introspection across an HPC software stack [boehme2016caliper] |

The Score-P story is the one to tell: four independently developed tool suites converged on a *shared measurement runtime and a shared trace format* rather than each instrumenting MPI separately [knupfer2012scorep]. The agent-tracing ecosystem (LangSmith, W&B Weave, OpenTelemetry GenAI semantic conventions, and others) is currently at the pre-Score-P stage, with every framework emitting its own trace shape. AgentMPI can plausibly argue that a standardized operation vocabulary is the precondition for a shared trace format, exactly as MPI's was.

---

## Part B — Fault Tolerance

### B.1 The MPI standard's position

#### B.1.1 MPI-1's exclusion and its consequences

MPI-1 (May 1994) specified no fault-tolerance mechanism [mpiforum1994mpi10]. Gropp and Lusk's 2004 IJHPCA paper is the authoritative reconstruction of what that did and did not mean [gropp2004ftmpiprograms]:

> "A common misconception about MPI is that the MPI Standard itself mandates that if any MPI process dies, then all the MPI processes in the job must die as well. This is not true. The basis for this misconception is easily understandable. The standard says … that the default error handler on the communicator `MPI_COMM_WORLD` is the built-in one called `MPI_ERRORS_ARE_FATAL`. … Thus, if one takes no particular action with respect to error handling, when a process exits before calling `MPI_Finalize`, the others are indeed required to detect this condition and exit as well. The MPI Forum decided that this would probably be the most useful default behavior, particularly for new users. (And when the MPI Forum was deliberating, all users were new.)"

And the framing that AgentMPI should quote:

> "Fault tolerance is a property; what is it a property of? It is not a property of MPI itself, since MPI is a specification of an API. … Is fault tolerance thus a property of an MPI implementation? No, since no implementation can ensure that any program is immune from all faults. We claim that fault tolerance is a property of an MPI program coupled with an MPI implementation." [gropp2004ftmpiprograms]

The consequence of the exclusion was that fault tolerance moved *outside* the model entirely: to coordinated checkpoint/restart at job granularity, executed by the batch system. That is a design failure with a specific shape — the recovery mechanism could not see the program's structure, so it had to save and restore everything.

#### B.1.2 The "undefined state" language, and its removal

MPI-3.1 §8.3 (p. 340) says, verbatim:

> "After an error is detected, the state of MPI is undefined. That is, using a user-defined error handler, or `MPI_ERRORS_RETURN`, does not necessarily allow the user to continue to use MPI after an error is detected. The purpose of these error handlers is to allow a user to issue user-defined error messages and to take actions unrelated to MPI (such as flushing I/O buffers) before a program exits. An MPI implementation is free to allow MPI to continue after an error but is not required to do so." [mpiforum2015mpi31]

MPI-3.1 §2.8 (p. 20) adds: "This document does not specify the state of a computation after an erroneous MPI call has occurred."

**MPI-4 removed this.** I searched the MPI-5.0 text for the phrase "state of MPI is undefined": zero hits. The replacement language in MPI-5.0 §9.3 (pp. 448–449) is materially different:

> "When an error is raised, MPI will provide the user information about that error using an error code. Some errors might prevent MPI from completing further API calls successfully and those functions will continue to report errors until the cause of the error is corrected or the user terminates the application. **The user can make the determination of whether or not to attempt to continue when handling such an error.**" [mpiforum2025mpi50, emphasis added]

And in §9.2 (p. 457): "All MPI function calls shall return `MPI_SUCCESS` if and only if the specification of that function has been fulfilled at the point of return. … When an operation raises an error, it may not satisfy its specification … and the content of the output buffers, targeted memory, or output parameters is undefined. However, a valid error code shall always be set when an operation raises an error."

That is a genuine semantic shift: from "the library's state is undefined" to "the *failed operation's outputs* are undefined, the error code is always valid, and continuing is the user's call."

#### B.1.3 What MPI-4 added

Per the MPI-5.0 change log (Appendix B, item 4, referencing §§2.8, 9.3, 9.5, 11.2.1) [mpiforum2025mpi50]:

- MPI calls not related to any object are now considered attached to `MPI_COMM_SELF` rather than `MPI_COMM_WORLD` — this *localizes* error impact, which is the precondition for any non-fatal handling.
- `MPI_ERRORS_ARE_FATAL` was clarified to cover all *connected* processes.
- A new predefined error handler, **`MPI_ERRORS_ABORT`**, "was created to limit the scope of aborting": it aborts on the communicator it is invoked on, rather than globally; invoked on a *session*, "the operation aborts only the local MPI process" [mpiforum2025mpi50 §9.3, p. 447].
- The **initial error handler** is now settable before initialization via the `mpi_initial_errhandler` info key on `mpiexec` or `MPI_COMM_SPAWN`, and "a high-quality implementation shall not deadlock during MPI initialization, even in the presence of failures" [mpiforum2025mpi50 §11.2, p. 478].
- **Sessions** (§B.7) give each component its own error-handling configuration and isolate failure scope.
- The standard also now specifies that `MPI_SUCCESS` "indicates only the result of the operation, not the state of the MPI library."

So MPI-4/4.1/5.0 built the *substrate* for fault tolerance — localized error scope, non-global abort, guaranteed-valid error codes, per-session error handlers — without adopting a recovery interface.

#### B.1.4 The true status of ULFM — verified

**ULFM is not part of any ratified MPI standard, including MPI-5.0.** Evidence:

1. I downloaded the MPI-5.0 report (June 5, 2025; 98,983 lines of extracted text) and searched case-insensitively for `failure mitigation`, `ULFM`, `MPI_COMM_REVOKE`, and `MPI_Comm_shrink`. **Zero matches for all four.** [mpiforum2025mpi50]
2. Open MPI's own v5.0.x documentation: "This implementation conforms to the User Level Failure Mitigation (**ULFM**) MPI Standard **draft proposal**. The ULFM proposal is developed by the MPI Forum's Fault Tolerance Working Group…" and "As ULFM is still an **extension** to the MPI standard, you will need to `#include <mpi-ext.h>` in C, or `use mpi_ext` in Fortran to access the supplementary error codes and functions." [ompiulfmdocs]
3. The Fault Tolerance Working Group's own specification page describes the document as "a specification for a Process Fault Tolerance chapter in the MPI Standard," based on MPI-4.1, and "currently under evaluation by the MPI standardization body." [ulfmspec]

The functions are therefore correctly spelled `MPIX_Comm_revoke`, `MPIX_Comm_shrink`, `MPIX_Comm_agree` in real code — the `MPIX_` prefix being the conventional marker for a non-standard extension. **The paper must use the `MPIX_` names when discussing implementations and the `MPI_` names only when quoting the draft specification's own text.** Claiming ratification is the single most common secondary-source error in this area.

Ongoing Forum work targets three coexisting models: fine-grained (ULFM, for new applications), coarse-grained (ReInit, to support existing checkpoint/restart-based applications), and session-based isolation ("bubbles" supporting shrinking and non-shrinking recovery); as of mid-2026 the working group reports it is "still stuck on [the] exact fault model" [MPI Forum "State of MPI" presentation, June 2026].

---

### B.2 FT-MPI

FT-MPI (Fagg and Dongarra, EuroPVM/MPI 2000) grew out of the HARNESS project and implemented all of MPI-1.2 plus parts of MPI-2, extending MPI semantics so applications could survive process loss [fagg2000ftmpi, ftmpioverview]. "FT-MPI survives the crash of n−1 processes in an n-process job, and, if required, can respawn them. However, it is still the responsibility of the application to recover the data structures and the data on the crashed processes" [ftmpioverview].

Recovery is triggered by rebuilding the communicator with a modified `MPI_Comm_{create,split,dup}`; the resulting semantics depend on the **communicator mode**, selected at job launch [fagg2003ftmpiuse, ftmpioverview]:

| Mode | Rank space after failure | Size after failure | Failed ranks |
| --- | --- | --- | --- |
| **ABORT** | n/a | n/a | Graceful abort; the user cannot trap it |
| **BLANK** | Unchanged — survivors keep their ranks | Unchanged (the *extent*) | Left as holes; communicating with a hole raises an invalid-rank error |
| **SHRINK** | Renumbered — contiguous | Reduced | Removed; the application must re-call `MPI_Comm_rank` |
| **REBUILD** | Unchanged | Unchanged | Respawned into the empty ranks (or the communicator shrinks and new processes are appended) |

FT-MPI additionally defined **message modes** governing in-flight messages: `NOP` (no user-level message operations allowed after an error; everything returns an error code, so the application can unwind to a safe point as fast as possible) and `CONT` (all communication *not* involving the failed node continues normally) [fagg2003ftmpiuse]. REBUILD was the default and best-tested mode; SHRINK was fully supported only with CONT [ftmpioverview].

**Why BLANK can beat SHRINK.** SHRINK renumbers. That is cheap for the runtime and catastrophic for anything that had cached a rank-to-work mapping: `MPI_COMM_SIZE` changes, `MPI_COMM_RANK` changes, and every derived data structure indexed by rank is silently wrong. FT-MPI's documentation is explicit that under SHRINK "processes might have a new rank after recovery," which is why the application "must re-call `MPI_COMM_RANK`" [fagg2003ftmpiuse]. BLANK preserves the rank space at the cost of a discontinuous, partially invalid index set — `MPI_COMM_SIZE` "will return the extent of the communicator, not the number of valid processes within it" [fagg2003ftmpiuse]. REBUILD exists because some algorithms need a specific size (the cited example is power-of-two FFT solvers) and would rather pay for a respawn than adapt.

**Why AgentMPI cares.** For an agent harness, the rank-to-work mapping is not merely cached — it is **baked into prompts**. A prompt that says "you are worker 3 of 8; handle shard 3" is a durable artifact of the rank space. Renumbering does not just invalidate a lookup table; it invalidates text that has already been sent to a model, possibly already reasoned over, possibly already cached (with attendant billing implications for prefix caching). SHRINK is therefore *strictly worse* for agents than it is for numerical codes, and BLANK — a hole in the rank space, with a well-defined error on attempts to address it — is the better default. This is a genuinely novel argument that MPI's own literature does not make, because MPI's literature has no notion of an identity that is expensive to re-establish. **This should be one of the paper's named contributions.**

---

### B.3 ULFM

#### B.3.1 The primitives

Draft-specification names (the `MPI_` forms), with the `MPIX_` forms used in Open MPI [bland2013postfailure, ompiulfmdocs]:

- **`MPI_Comm_revoke(comm)`** — "Interrupts any communication pending on the communicator at all ranks." Non-collective: any single rank may revoke, and the effect propagates to all. All subsequent operations on the communicator return `MPI_ERR_REVOKED` (`MPIX_ERR_REVOKED`).
- **`MPI_Comm_shrink(comm, &newcomm)`** — "creates a new communicator by eliminating all failed processes from a revoked communicator. The operation is collective and performs a consensus algorithm to ensure that all participating processes complete the operation with equivalent groups in the new communicator. This function cannot return an error due to process failure. Instead, such errors are absorbed as part of the consensus algorithm and will be excluded from the resulting communicator." Survivors are renumbered contiguously.
- **`MPI_Comm_agree(comm, &flag)`** — "provides an agreement algorithm which can be used to determine a consistent state between processes when such strong consistency is necessary. The function is collective and forms an agreement over a boolean value, even when failures have happened or the communicator has been revoked." The result is a bitwise AND over contributed values. A non-blocking form, `MPI_Comm_iagree`, exists.
- **`MPI_Comm_failure_ack(comm)`** / **`MPI_Comm_failure_get_acked(comm, &group)`** — the local, cheap path: acknowledge locally known failures (which also re-enables `MPI_ANY_SOURCE` receives on the communicator) and retrieve the group of acknowledged-failed processes. These are *local* operations and involve no consensus, which is what makes them usable in an inner loop.
- New error classes: `MPI_ERR_PROC_FAILED`, `MPI_ERR_PROC_FAILED_PENDING`, `MPI_ERR_REVOKED`.

The overarching guarantee: "no MPI call (point-to-point, collective, RMA, IO, …) can block indefinitely after a failure, but must either succeed or raise an MPI error" [ompiulfmdocs].

#### B.3.2 Mechanisms, not policy

ULFM's stated design principle is that the standard should supply the minimal set of building blocks and let applications and libraries construct the policy. The Fault Tolerance Working Group describes ULFM as "a minimal set of changes necessary for applications and libraries to include fault tolerance techniques and to construct more forms of fault tolerance (transactions, strongly consistent collectives, etc.)" [ulfmspec]. Losada et al.'s survey confirms the outcome: ULFM "does not include any specialized, non-portable mechanism to recover the application state at failed processes, providing developers … the flexibility to implement the most optimal methodology," and "the large and varied number of approaches in the literature proves that ULFM provides the necessary flexibility" [losada2020ulfmsurvey].

The *flexibility principle* is stated explicitly in the design-and-rationale paper: applications whose communication pattern means a failure will never deadlock them "should not have to pay for the cost of complete recovery when they can simply continue to operate on the communicator without further involving the failed processes" [bland2013postfailure]. Hence revoke is opt-in, not automatic.

#### B.3.3 Why revoke is necessary and non-obvious

This is the deepest idea in ULFM and the one most worth transplanting. The motivating scenario, verbatim from the design-and-rationale paper [bland2013postfailure]:

> "four processes are communicating in a point-to-point pattern. Process 2 is waiting to receive a message from process 3, which is waiting to receive a message from process 0, itself waiting to receive a message from process 1. In the meantime, process 1 has failed, but this condition is detected only by process 0, as other processes do not communicate with process 1 directly. At this point, without a new construct, the algorithm would reach a deadlock: the messages that processes 2 and 3 are waiting for will never arrive because process 0 has branched to enter recovery."

The insight: **failure notification is inherently non-uniform.** Only the peers that were actually talking to the dead process learn of the death; everyone else is blocked on operations that can now never complete, and they have no way to find out. A local error return is therefore insufficient — you need a mechanism to *poison the communication context globally* so that survivors are ejected from operations that have become unsatisfiable. Revoke is that mechanism, and it is emphatically not the first thing a designer thinks of, because it looks like an escalation (it makes the communicator permanently unusable) rather than a recovery.

Bland et al. also note the algorithmic subtlety that makes revoke safe: if the revoke initiator itself fails partway through propagation, "the Revoke notification is indeed lost, but the observed behavior, from the view of the application, is indiscernible from a failure at the initiator before the propagation started. As the algorithm still ensures agreement, there are no opportunities for inconsistent views" [bland2013postfailure]. Later ULFM releases replaced the naive flooding implementation with a reliable broadcast of fixed maximum output degree, scaling logarithmically in rank count [ompiulfmdocs].

Shrink is "algorithmically, an agreement on which the consensus is done on the group of failed processes. Hence, the two operations have the same algorithmic complexity"; in the prototype, `MPI_COMM_AGREE` and `MPI_COMM_SHRINK` shared one internal agreement implementation [bland2013postfailure].

#### B.3.4 Agreement cost: ERA

The agreement was initially the practical obstacle: "Previous uses of the ULFM constructs spotlighted the overhead of the agreement operation as one of the major obstacles preventing a larger adoption of the concepts" [herault2015era]. **ERA (Early Returning Agreement)**, Herault et al., SC'15, fixed it [herault2015era].

The *early returning* property: "the capacity of an early deciding algorithm to return before the stopping condition (early or not) is guaranteed: as soon as a process can determine that the decision value is fixed (except if it fails itself), the process is allowed to return." Because processes return early, later failures may compel a returned process to participate in further exchanges, so "the decision must remain available after the processes returned." The failure model is permanent crash in a pseudo-synchronous system (no Byzantine behaviour, no data corruption, no message loss — those are handled by the transport).

Measured results [herault2015era]:

- **Failure-free scaling is logarithmic.** On the Cray XC30 *darter* with a bin/bin hierarchical topology at 16 processes per node (average branching degree of non-leaf nodes 2.125), the measured cost fits `era(x) = 6.7 · log₂.₁₂₅(x)` with an asymptotic standard error of **0.6%**. `[UNVERIFIED]` — the y-axis units are almost certainly microseconds, but the axis label was not recoverable from the text extraction I performed, so the coefficient 6.7 should be re-checked against the published figure before it appears in the paper.
- **ERA costs roughly 2× an optimized `MPI_Allreduce`**: "When compared with the fully optimized, non fault tolerant Allreduce, the latency is doubled, which is a logical consequence of the need for the ERA operation to sequentialize a reduce and a broadcast that do not overlap (to ensure the consistent decision criterion in the failure case)."
- The prior state of the art, a two-phase-commit agreement, "exhibits a linear scaling with the number of nodes, despite the expected theoretical bound," and was abandoned at larger scale.
- Hierarchy matters, counterintuitively: the bin/star topology (one representative per node with 16 local children) performed *worse* than a flat binary tree, because "the resulting 16 sequential memcopy operations … take longer than the latency to cross the supplementary long-range links." Only bin/bin, which parallelizes the intra-node copies, scaled logarithmically.
- **Robustness:** a 24-hour stress run on 128 processors (16 nodes × 8 cores, TCP over Gigabit Ethernet), looping agreements and replacing each killed process, "completed **969,739 agreements** successfully while tolerating **146,213 failures**."
- Post-failure: rebalancing the ERA tree after a failure costs linearly in the number of failures, but a rebalanced post-failure agreement is "indistinguishable from a failure-free agreement." With a single failure, rebalancing is not worth it — suggesting tree rebuilding should be conditional on topology degeneration.
- The authors' closing observation: with agreement fixed, "the next largest overhead is the failure detection."

#### B.3.5 Failure-free overhead

Bland et al. measured the cost of *having* ULFM support compiled in, on an application that does not use it [bland2013ulfmjournal, bland2012ulfm]. Platform: ORNL *Smoky* (four quad-core 2.0 GHz AMD Opteron per node, 2 GB/core, GigE + InfiniBand); the shared-memory tests on *Romulus* (6 × 8-core AMD Opteron 6180 SE, 256 GB). Comparison: vanilla Open MPI r26237 versus the same revision with ULFM.

NetPIPE v3.7, 1-byte latency (µs, cache hot):

| Interconnect | Vanilla | Std. dev. | ULFM-enabled | Std. dev. | Difference |
| --- | --- | --- | --- | --- | --- |
| Shared memory | 0.8008 | 0.0093 | 0.8016 | 0.0161 | **+0.0008** (+0.10%) |
| TCP | 10.2564 | 0.0946 | 10.2776 | 0.1065 | **+0.0212** (+0.21%) |
| OpenIB | 4.9637 | 0.0018 | 4.9650 | 0.0022 | **+0.0013** (+0.03%) |

Bandwidth (Mbps, cache hot):

| Interconnect | Vanilla | ULFM-enabled | Difference |
| --- | --- | --- | --- |
| Shared memory | 10,625.92 | 10,602.68 | **−23.24** (−0.22%) |
| TCP | 6,311.38 | 6,302.75 | **−8.63** (−0.14%) |
| OpenIB | 9,688.85 | 9,689.13 | **+0.28** (+0.003%) |

Every difference is at or below the standard deviation. On the IMB suite (v3.2.3) on Romulus, "the duration difference of all the benchmarks (point-to-point and collective) remains below 5%, thus within the standard deviation of the implementation on that machine." A weak-scaling study of the Sequoia AMG benchmark to 512 processes showed negligible difference across Solve, Setup, and SStruct phases [bland2013ulfmjournal].

**The headline claim, stated carefully: ULFM's failure-free overhead is below measurement noise (≤0.21% on 1-byte latency, ≤0.22% on bandwidth).** ULFM's cost is paid only when failures occur. This is the number AgentMPI should cite when arguing that a fault-tolerance interface need not tax the common case.

As of Open MPI v5.0.x, ULFM is built by default (disable with `--without-ft`) but inactive unless enabled at runtime [ompiulfmdocs]. Open MPI's supported-techniques page notes that ULFM is the *only* actively developed resilience approach there; coordinated and uncoordinated checkpoint/restart and data-reliability support have been deprecated and removed "due to lack of adoption and lack of maintenance."

---

### B.4 Detection

#### B.4.1 FLP and the escape

Fischer, Lynch, and Paterson proved that in a fully asynchronous message-passing system, no deterministic protocol solves consensus if even one process may crash [fischer1985flp]. The obstruction is not computational; it is epistemic. In an asynchronous system, a crashed process and an arbitrarily slow process are *observationally identical*, so no protocol can safely decide.

Dwork, Lynch, and Stockmeyer supply the escape: **partial synchrony** [dwork1988partialsync]. Either (a) message delay and relative process-speed bounds exist but are unknown, or (b) known bounds hold only after some unknown Global Stabilization Time. Under either variant, consensus becomes solvable. Every practical system — Paxos, Raft, ZooKeeper, Chubby, and ULFM's ERA (which explicitly targets "pseudo-synchronous systems") — lives in this model.

#### B.4.2 The Chandra–Toueg classification

Chandra and Toueg [chandra1996faildetectors] characterize failure detectors by two axes:

- **Completeness** — *strong*: every crashed process is eventually permanently suspected by *every* correct process; *weak*: by *some* correct process.
- **Accuracy** — *strong*: no correct process is ever suspected; *weak*: some correct process is never suspected; *eventually strong* / *eventually weak*: the corresponding property holds after some time.

Two completeness values × four accuracy values = eight classes. Weak completeness can simulate strong completeness, so the four strongly-complete classes are the ones named:

| Class | Completeness | Accuracy | Character |
| --- | --- | --- | --- |
| **P** (Perfect) | Strong | Strong | Never wrong. Realizable only in synchronous systems |
| **S** (Strong) | Strong | Weak | Some correct process is never suspected |
| **◇P** (Eventually Perfect) | Strong | Eventually strong | May be wrong for a while, then stops being wrong |
| **◇S** (Eventually Strong) | Strong | Eventually weak | Weakest of the four |

Key results: consensus is solvable with any of these; **◇S requires a majority of correct processes**, whereas **S** and **P** tolerate any number of crashes; and the companion paper proves ◇W (equivalently ◇S) is *the weakest* failure detector for consensus [chandra1996weakest]. Chandra and Toueg also prove consensus and atomic broadcast reducible to each other under crash failures. Chen, Toueg, and Aguilera later gave the quality-of-service framework for tuning detectors — detection time, mistake recurrence, mistake duration — which is the right vocabulary for arguing about timeout choice [chen2002qos].

For HPC specifically, Bosilca et al. designed a failure detector for HPC platforms, motivated directly by the ERA result that detection had become the dominant post-failure cost [bosilca2018faildetector].

#### B.4.3 Heartbeats, leases, and their fundamental limit

Two implementation families:

- **Heartbeat detectors.** Processes periodically emit liveness signals; a peer that misses *k* consecutive heartbeats is suspected. Tuning the interval and *k* trades detection latency against false-positive rate — precisely Chen–Toueg's QoS metrics [chen2002qos].
- **Lease-based detectors.** Gray and Cheriton introduced leases as "a consistency protocol that handles host and communication failures using physical clocks": a lease grants a time-bounded right, and "after the lease expires, a read of the datum requires that the cache first extend the lease" [gray1989leases]. Their analytic model showed short-term leases give near-optimal efficiency despite the fault-tolerance provisions.

**The limit is structural, and it is FLP restated in engineering terms: a lease-based detector cannot distinguish a slow process from a dead one.** When a lease expires, the holder may have crashed, or may be in a stop-the-world GC pause, or may be behind a network partition, or may simply be slow. The detector must choose, and it will sometimes choose wrong.

What real systems do about it:

1. **Fail safe rather than fail accurate.** Declare the lease expired and let the (still-alive) holder discover on its next interaction that it has lost its lease. This converts a liveness problem into a safety problem — which is why (2) is mandatory.
2. **Fence.** Kleppmann's canonical popular treatment [kleppmann2016locking]: "you need to include a fencing token with every write request to the storage service. In this context, a fencing token is simply a number that increases … every time a client acquires the lock." The protected resource tracks the highest token it has seen and rejects anything lower. Kleppmann's critique of Redlock rests on exactly this: even if the algorithm were otherwise perfect, "it would not be safe to use, because you cannot prevent the race condition between clients in the case where one client is paused or its packets are delayed" — Redlock produces random values, not monotonic tokens.
3. **Get the token from a consensus log.** Chubby [burrows2006chubby] supplies *sequencers* — opaque tokens carrying the lock name, mode, and a generation number that increments on each acquisition — with `GetSequencer()` / `SetSequencer()` / `CheckSequencer()`, plus a *lock-delay* fallback for downstream services that cannot check sequencers. ZooKeeper's `zxid` and ephemeral sequential znode numbers serve the same role [hunt2010zookeeper], as does etcd's revision number. The observation Kleppmann makes, and that matters for us: generating a correct fencing token essentially requires consensus, because the token must be totally ordered with respect to lock grants.
4. **Persist the counter.** A lock server that restarts must restore its token counter from durable storage; restarting at zero would let a stale holder's old token pass the fencing check.

**Transfer to AgentMPI.** Every one of these transfers, and the LLM setting makes the slow-versus-dead ambiguity *worse*, not better: a model call that takes 300 seconds is entirely ordinary, so any timeout short enough to detect real failure promptly will fire on healthy-but-slow work. AgentMPI should therefore (a) not attempt a Perfect detector, (b) make its detector explicitly ◇P at best and document the mistake-duration behaviour, and (c) **mandate fencing tokens on every side-effecting tool call**, so that a resurrected-or-never-dead agent whose lease expired cannot commit a duplicate write. This is a concrete, checkable protocol requirement, and it is the honest answer to "what happens when you time out an agent that was merely thinking hard."

---

### B.5 Recovery approaches

#### B.5.1 Coordinated checkpoint/restart

Coordinated checkpointing takes a consistent global snapshot (in the Chandy–Lamport sense [chandy1985snapshots]) and, on failure, restarts everything from it. Implementations:

- **BLCR** (Berkeley Lab Checkpoint/Restart) — a Linux *kernel module*, "particularly notable because of its widespread usage," which "can only checkpoint processes on a single machine," with distributed checkpointing achieved by MPI libraries (some versions of Open MPI, LAM/MPI, MVAPICH2, MPICH-V) integrating with it [hargrove2006blcr, ansel2009dmtcp].
- **DMTCP** (Distributed MultiThreaded CheckPointing) — a *user-level* package requiring no kernel modules or root privileges, demonstrated on 20+ applications including MATLAB, Python, MPICH2, Open MPI, and runCMS (a 680 MB in-memory image with 540 dynamic libraries, from CERN's CMS experiment). "On 128 distributed cores (32 nodes), checkpoint and restart times are typically **2 seconds**, with negligible run-time overhead. Typical checkpoint times are reduced to **0.2 seconds** when using forked checkpointing." Checkpoint time "remains nearly constant as the number of nodes increases on a medium-size cluster" [ansel2009dmtcp].

Multi-level checkpointing (SCR) exploits the observation that most failures are recoverable from a checkpoint written to node-local or neighbour storage, reserving the parallel file system for rare catastrophic failures [moody2010scr].

#### B.5.2 Uncoordinated checkpointing and the domino effect

If processes checkpoint independently, a rollback can cascade: process A rolls back past the point where it sent a message to B, so B must roll back past its receipt, which may force B past a send to C, and so on — potentially all the way to the start. Randell named this the **domino effect** [randell1975domino]. Elnozahy et al.'s survey is the canonical taxonomy of the whole space [elnozahy2002rollback].

#### B.5.3 Message logging

Message logging escapes the domino effect by making re-execution deterministic: log the non-deterministic events (principally message receipt order) so a restarted process can be replayed to its pre-failure state from its last local checkpoint. Three families [elnozahy2002rollback]:

- **Pessimistic** — the determinant is logged to stable storage *before* the receiving process acts on it. Simplest recovery (no orphan processes, no rollback of survivors); highest failure-free cost.
- **Optimistic** — determinants are logged asynchronously. Cheap in the failure-free case; may lose determinants on failure, so survivors can become orphans and must roll back.
- **Causal** — piggybacks determinants on outgoing messages, so the causal history needed to recover any process is replicated in the processes it influenced. Combines pessimistic's no-orphan guarantee with (most of) optimistic's low overhead, at the cost of message-size growth and bookkeeping complexity.

MPICH-V explored several of these protocols in an automatic fault-tolerant MPI [bouteiller2006mpichv]; Bouteiller et al. later redesigned the model for high performance [bouteiller2010messagelogging]. Open MPI classifies its message-logging support as "research / non-production usage only."

#### B.5.4 Optimal checkpoint interval

**Young (1974)** gave the first-order approximation: τ_opt = √(2δM), where δ is the time to write a checkpoint and M the mean time to interrupt [young1974checkpoint]. The derivation minimizes total lost time = (checkpointing overhead) + (expected rework since the last checkpoint).

**Daly (2003, 2006)** extended it. The 2003 model already gives τ = √(2δ(M + R)) − δ for δ ≪ M, with R the restart time [daly2003model]. The 2006 paper "examines methods of approximating the optimum checkpoint restart strategy for minimizing application run time on a system exhibiting Poisson single component failures," deriving "a more complete cost function and … a perturbation solution that provides accurate high order approximations" [daly2006checkpoint]. Daly's higher-order result is a perturbation series in √(δ/2M), reducing to Young's formula in the small-δ limit, and switching to τ_opt = M once δ ≥ 2M. `[UNVERIFIED]` — I confirmed the structure (perturbation series, Lambert-W connection, the δ ≥ 2M regime switch) from the abstract, the LANL record, and multiple citing papers, but did not obtain the paginated equation itself; the exact coefficients (commonly quoted as 1 + ⅓√(δ/2M) + ⅑(δ/2M)) should be re-checked against Daly's Eq. 20 before the paper quotes them.

**Fenix** [gamell2014fenix] used Young's formula in anger, computing the interval from δ = 0.0748 s (at 2197 cores) and M ∈ {47, 94, 189} s, and validating it empirically by injecting a single failure at varying wall times.

#### B.5.5 ULFM-based frameworks

**Fenix** provides "online (i.e., without disrupting the job) and transparent recovery from process, node, blade, and cabinet failures," built on ULFM, with three components: process recovery (repairing communicators), data recovery (in-memory checkpoint/restart), and message recovery (log-and-replay for localized fault tolerance) [gamell2014fenix, gamell2016fenixspec]. Notably, "failure detection is delegated to ULFM-enabled MPI," and error codes "are detected in Fenix using MPI's profiling interface. As a result, no changes in the MPI runtime itself are required" — a nice demonstration that PMPI (§A.5) is load-bearing for fault tolerance too.

Measured on ORNL Titan (Cray XK7) with the S3D combustion code [gamell2014fenix]:

- Coordination-less checkpointing scales to **250K cores**, sustaining **~17 TB/s** checkpoint bandwidth at an **18-second** interval with **0.41% overhead** versus a checkpoint-free run.
- S3D simulated 31+ billion grid points, **2+ TB per checkpoint**, 8.58 MB/core.
- Tolerated MTBFs of **under one minute** (tests at 47 s, 94 s, 189 s) "with lower overhead [than] coordinated C/R with failure rates of ~2.5 hours."
- Programming cost: **fewer than 35 new, changed, or rearranged lines** in S3D.

Other ULFM-based work surveyed in [losada2020ulfmsurvey] includes LFLR (local failure, local recovery), resilient X10 over ULFM, resilient Coarray Fortran, CRAFT, and sparse-grid combination-technique approaches. Laguna et al. evaluated ULFM's practical usability on real applications [laguna2014evalulfm].

#### B.5.6 Charm++ / AMPI

Adaptive MPI virtualizes MPI ranks as migratable user-level threads on top of Charm++ objects [huang2004ampi]. Because the runtime already owns object placement and can migrate work, fault tolerance becomes an application of the migration machinery rather than a separate subsystem: FTC-Charm++ provides in-memory checkpoint-based fault tolerance for both Charm++ and MPI [zheng2004ftccharm], and Charm++ supports proactive migration away from nodes predicted to fail [acun2014charm].

The lesson: **over-decomposition makes recovery cheap.** If there are many more work units than processors, losing a processor loses a small, relocatable fraction of the work, and the runtime already knows how to relocate. This is directly applicable to AgentMPI — an agent runtime that over-decomposes tasks relative to agent instances can recover a failed agent by reassigning its outstanding tasks, no state transfer required.

#### B.5.7 ABFT, and the argument that matters

Algorithm-based fault tolerance was introduced by Huang and Abraham (IEEE TC, C-33(6):518–528, June 1984) as a checksum scheme for matrix operations on systolic arrays [huang1984abft]. The idea: encode the data with checksums, and redesign the algorithm so that "similar mathematical operations are applied to both the data and the checksum so that the checksum relationship is kept invariant during the course of the algorithm" [bosilca2015abftfactorizations]. It is ECC generalized from static data to data *under transformation*.

Costs and results:

- Bosilca, Delmas, Dongarra, and Langou's fault-tolerant matrix–matrix multiply achieved **1.4 TFLOP/s on 484 processors** (NERSC *jacquard*), returning a correct result despite a process failure — **65% of machine peak** and **under 12% overhead** relative to the fastest failure-free implementation, with overhead dropping as processor count rises [bosilca2009abft].
- The generic framework of [bosilca2015abftfactorizations] extends ABFT to LU and QR (not just Cholesky and HPL) by protecting the right factor with a conventional ABFT checksum and the left factor with a "vertical checkpointing scheme," making the result "a hybrid between ABFT and algorithm-driven checkpointing."
- ABFT's overhead is asymptotically vanishing: "the computation complexity of the checksum operations scales similarly to the related matrix operation (and the ratio of extra computation is small and asymptotically tends toward zero)" [bosilca2015abftfactorizations].

**The load-bearing argument.** ABFT was introduced specifically "to deal with silent error" [bosilca2015abftfactorizations] — silent data corruption, the bit flip that produces a wrong answer with no error signal. Checkpoint/restart cannot address this class at all, and the reason is worth stating sharply:

> **Checkpoint/restart faithfully preserves silent corruption.** A checkpoint is a bit-for-bit snapshot. If the corruption occurred before the snapshot, the snapshot contains it, and restarting reproduces it exactly. Checkpointing has no notion of *correct*; it only has a notion of *the same as before*. ABFT, by contrast, carries a redundant invariant alongside the data, so a violated checksum *detects* the corruption, and in many cases the same redundancy suffices to correct it.

This distinction is exactly the one AgentMPI needs, because **an agent's dominant failure mode is a confident wrong answer** — the LLM analogue of silent data corruption. An agent that hallucinates a fact, mis-parses a tool result, or fabricates a citation does not crash; it produces a plausible, well-formed, wrong output and continues. Every crash-recovery mechanism in Part B is blind to this, and every one of them will faithfully preserve it. Only mechanisms with an *independent invariant* — a verifier, a checksum, a test suite, a cross-check against a ground-truth source, a structural constraint on the output — can detect it. The paper should state this as: **the HPC mechanism AgentMPI most needs is the one HPC uses least, and the mechanism HPC uses most is the one AgentMPI can least use.**

A caution against the obvious wrong answer: **replication does not substitute for ABFT here.** Triple modular redundancy detects errors because independent replicas fail independently. Three LLM calls with the same prompt to the same model are not independent — the errors are strongly correlated, because they are functions of the same weights and the same input. Even across different models, training-data overlap and shared benchmark contamination correlate the errors. Voting over correlated wrong answers produces a confidently wrong majority. Empirical work supports the related point that models are poor judges of their own errors [stechly2024selfverification, huang2024selfcorrect], and the multi-agent failure taxonomy of [cemri2025multiagentfail] documents these failure classes at the system level.

---

### B.6 Supervision from outside HPC

#### B.6.1 Erlang/OTP supervision trees

Erlang's answer to partial failure is architectural rather than algorithmic: processes are cheap and isolated, they are expected to crash, and the *structure* that restarts them is a first-class artifact — a supervision tree [armstrong2003erlang, armstrong2007erlangbook]. "Let it crash" means: do not write defensive error handling for states you did not anticipate; crash cleanly, and let a supervisor with a wider view decide what to restart. Handling only the expected cases keeps the happy path readable, and the supervisor turns an unhandled error into a bounded, well-defined recovery.

Restart strategies, from the OTP documentation [otpsupervisor]:

| Strategy | Behaviour on a child's termination |
| --- | --- |
| `one_for_one` | Only that child is restarted. **Default.** |
| `one_for_all` | All remaining children are terminated; then all children, including the terminated one, are restarted |
| `rest_for_one` | Children *after* the terminated one in start order are terminated; then the terminated child and all children after it are restarted |
| `simple_one_for_one` | Simplified `one_for_one` where all children are dynamically added instances of the same process type |

The strategies encode a dependency model: `one_for_one` for independent children, `one_for_all` for mutually dependent ones, `rest_for_one` for a start-order dependency chain. Children are additionally `permanent` (always restarted), `transient` (restarted only on abnormal termination), or `temporary` (never restarted) [otpsupervisor].

**Maximum restart intensity** is the part most often overlooked and most important for agents. "If more than `MaxR` restarts occur in the last `MaxT` seconds, the supervisor terminates all the child processes and then itself. The termination reason for the supervisor itself in that case will be `shutdown`." Defaults: `intensity` = **1**, `period` = **5** seconds — "chosen to be safe for most systems, even with deep supervision hierarchies" [otpsupervisor]. Because the supervisor's own death propagates to *its* supervisor, exceeding the intensity escalates the failure up the tree rather than absorbing it.

**Why an unbounded restart loop is worse than a crash.** A crash is a signal: it terminates, it is visible, it produces a stack trace, and a human or an outer supervisor gets a chance to act. An unbounded restart loop is *invisible progress-free work*: the system appears alive, health checks pass, resources are consumed, logs fill with identical errors, and nothing advances. Erlang's design says that if restarting is not working, restarting harder is not the answer — escalate. For AgentMPI this is close to mandatory, because a restarting agent is not merely burning CPU: every restart is a *billable* model call, and a loop of a wrong tool call → error → retry can consume a budget in minutes with zero progress. **AgentMPI should adopt max restart intensity as a first-class, required parameter of any supervision construct, with a conservative default, expressed in both restarts-per-interval and cumulative-cost terms.**

#### B.6.2 Durable execution — the right analogue

Here is the central claim of this subsection: **for recovering an agent, the correct analogue is not checkpoint/restart but durable execution.** The reason is simple and is the same reason checkpoint/restart fails for agents generally: *there is no memory image to restore.* An agent's "state" is a conversation history plus a set of side effects already committed to the outside world. You cannot snapshot a provider's server-side state; you can only replay the interaction that produced it.

Durable execution systems do exactly that. Temporal's model: "The system functions via event sourcing: an append-only history of events is stored for each workflow execution, and all required workflow state can be recreated at any time by replaying this history" [temporaldocs]. When a worker crashes, a new worker re-executes the workflow function from the first line; the event history supplies the results of every previously completed activity, so those activities are *not* re-executed. Reaching the end of the history restores the state, and execution continues.

The discipline this imposes maps cleanly onto agents:

| Temporal concept | Requirement | AgentMPI analogue |
| --- | --- | --- |
| Workflow code | Must be **deterministic** — no `Math.random()`, no wall-clock reads, no unguarded I/O | The orchestration logic: which agent is asked what, in what order |
| Activity | Must be **idempotent** (at-least-once) or explicitly non-retryable (at-most-once) | Model calls and tool invocations |
| Event history | Append-only, durably persisted; the source of truth | The conversation transcript plus tool-result log |
| Side effect | A non-deterministic snippet whose result is recorded once and returned from the record on replay | A model sampling call: record the completion, replay the recorded one |
| Retry policy | Applied at the leaf; failure propagates to the parent only when exhausted | Per-agent retry budget; escalation to the supervisor on exhaustion |

The research lineage is worth one sentence in related work: durable, recoverable distributed computation descends from **Argus** (Liskov's guardians and atomic actions, CACM 1988 [liskov1988argus]) and **Camelot/Avalon** (Spector et al.'s distributed transaction facility [eppinger1991camelot]), through the workflow systems of the 1990s, to the current generation branded as "durable execution": **Temporal** (itself descended from Amazon SWF, Microsoft's Durable Task Framework, and Uber's Cadence), **AWS Step Functions** [awsstepfunctions], and **Azure Durable Functions** [azuredurable].

The critical adaptation for AgentMPI: **a model sampling call is a Temporal Side Effect, not an Activity.** It is non-deterministic by construction (temperature > 0), so replay must return the *recorded* completion rather than re-sampling. If it re-sampled, the replayed execution would diverge from the recorded history and the whole scheme collapses. This is the precise technical reason "record and replay" is the right recovery primitive for agents and "snapshot and restore" is not.

---

### B.7 Elastic and malleable jobs

**Dynamic process management (MPI-2, 1997)** added `MPI_Comm_spawn` and friends. The semantics that limited adoption are visible in the specification itself: spawn "is collective over `comm`, and also may not return until `MPI_Init` has been called in the children. Similarly, `MPI_Init` in the children may not return until all parents have called `MPI_Comm_spawn`. In this sense, `MPI_Comm_spawn` in the parents and `MPI_Init` in the children form a collective operation over the union of parent and child processes." Children get their *own* `MPI_COMM_WORLD`, separate from the parents', connected only by an inter-communicator [Open MPI `MPI_Comm_spawn` man page].

Why it went largely unused:

1. **The resource model does not fit.** Batch schedulers allocate a fixed node set at job start. Spawning requires spare capacity, discoverable only through the `MPI_UNIVERSE_SIZE` attribute, which is "installation-dependent" and frequently unset. Exceeding the hardware means time-slicing with no performance gain.
2. **It is heavily collective and synchronizing**, so it cannot be used opportunistically.
3. **The rank space fragments.** The children's separate `MPI_COMM_WORLD` plus an inter-communicator is a much more awkward object than a single grown communicator, and most SPMD codes are written against a single flat rank space.
4. **`[UNVERIFIED]`** — implementation quality and portability were also widely cited as barriers; I found this asserted in secondary sources but did not locate a primary quantitative study.

**MPI Sessions (MPI-4)** attack the problem from the other end. The motivation is that `MPI_COMM_WORLD` is itself the scalability barrier: Sessions "remove the known scalability barriers by no longer requiring all possible communication peers to be included in `MPI_COMM_WORLD`," giving "a tighter integration of MPI applications with the underlying runtime system; and a scalable representation of communication groups" [holmes2016sessions]. Concretely: `MPI_Session_init` creates a session; process sets are queried by URI from the runtime; communicators are built from those groups; and multiple independent software components can each initialize their own session without coordination, so MPI can effectively be initialized and finalized multiple times in one application [holmes2016sessions; arXiv:2605.03983]. Each session carries its own `info` (allowing per-session settings of what were previously global properties, such as thread level) and its own error handler.

Three consequences matter to AgentMPI:

1. **Failure scope becomes compositional.** With Sessions, a failure can be isolated to one session rather than poisoning a global context. This is why the Forum's fault-tolerance work now includes a session-based model with "bubbles" supporting shrinking and non-shrinking recovery [MPI Forum "State of MPI", 2026].
2. **Elasticity becomes expressible.** "The dynamic nature of MPI Sessions enables innovative solutions for fault tolerance and resource management, including the ability to shrink or grow the number of participating processes during an application's execution" [arXiv:2605.03983].
3. **Multiple independent initializations** is exactly what a multi-agent system needs, where different subsystems (a planner, a tool executor, an evaluator) may want independent groups with independent policies.

**Malleability research.** Feitelson and Rudolph's taxonomy distinguishes *rigid*, *moldable* (size fixed at launch), *evolving* (application-initiated size change), and *malleable* (scheduler-initiated size change) jobs [feitelson1996malleable]. Elastic MPI / the iCal work of Comprés et al. added infrastructure and API extensions for elastic MPI execution with scheduler cooperation [compres2016elasticmpi]. Invasive Computing proposed resource-aware programming where applications "invade," "infect," and "retreat" from resources [teich2011invasive]. Dynamic Resource Management (DMR) integrates malleability with the batch system [prabhakaran2015dmr].

**Transfer to AgentMPI.** Agent systems are *natively* malleable in a way MPI jobs are not: adding an agent costs an API key and a rate-limit slot, not a node allocation; there is no data redistribution because there is no distributed array. The MPI malleability literature is therefore mostly a source of *vocabulary* (rigid/moldable/evolving/malleable is a genuinely useful classification for agent topologies) rather than mechanism. The one mechanism worth importing is Sessions' compositional scoping — independent groups with independent error handlers and independent lifetimes — because that is the structure that lets a subsystem fail without taking down the run.

---

## Transfer Table: HPC Fault-Tolerance Mechanisms → LLM Agents

Verdicts: **Transfers** (adopt largely as-is), **Transfers with adaptation** (the idea holds, the mechanism changes), **Does not transfer** (the precondition is absent).

| # | HPC mechanism | Verdict | Reasoning |
| --- | --- | --- | --- |
| 1 | **`MPI_ERRORS_ARE_FATAL` as default** | Transfers with adaptation | Fail-fast is the right default for a *new* system's users (the MPI Forum's own reasoning [gropp2004ftmpiprograms]). But MPI-4's `MPI_ERRORS_ABORT` — abort a scope, not the world — is the better model, because agent runs are long and expensive and losing all of one is unacceptable. Adopt MPI-4's localization, not MPI-1's default. |
| 2 | **Localized error scope (MPI-4 `COMM_SELF` attachment, per-session handlers)** | Transfers | Pure semantics, no hardware dependence. An error in one agent's tool call should attach to that agent's scope. Cheap and unambiguously correct. |
| 3 | **FT-MPI BLANK (hole in the rank space)** | **Transfers, and is the right default** | Preserving the rank space preserves prompt-embedded identity. See §B.2. Strongest single transfer in the dossier. |
| 4 | **FT-MPI SHRINK (renumber survivors)** | **Does not transfer** | Renumbering invalidates rank-to-work mappings that for agents are baked into already-sent prompts, cached prefixes, and completed reasoning. Cheap in MPI, catastrophic here. |
| 5 | **FT-MPI/ULFM REBUILD-style respawn into the vacated rank** | Transfers | A new agent instance can occupy the vacated identity, since an agent has no memory image to reconstruct — only a transcript to replay (§B.6.2). Cheaper for agents than for MPI. |
| 6 | **`MPI_Comm_revoke`** | **Transfers, and is non-obvious enough to be a contribution** | The motivating pathology — survivors blocked on operations that can never complete, with non-uniform failure knowledge — occurs verbatim in agent systems: agent C awaits a reply from B, which awaits A, which has died. Only A's direct peer knows. Without a revoke primitive, C and B hang until a wall-clock timeout, which is unbounded because slow-vs-dead is undecidable (§B.4.3). |
| 7 | **`MPI_Comm_agree` / consensus over survivors** | Transfers with adaptation | Needed for "did the round complete?" decisions, but the cost calculus inverts: ERA's ~6.7·log(n) µs is negligible against a 300–3000 ms model call, so AgentMPI can afford a much simpler (even centralized) agreement. Adopt the *semantics*; discard the algorithm. |
| 8 | **`MPI_Comm_failure_ack` / `failure_get_acked` (local, no consensus)** | Transfers | The cheap local path is even more valuable when the expensive path is a network round trip among agents. Keep the two-tier structure. |
| 9 | **"Mechanisms not policy"** | Transfers | The core ULFM design principle. Agent recovery policy is even more application-specific than HPC recovery policy, and the variety of ULFM-based frameworks [losada2020ulfmsurvey] is evidence the principle works. |
| 10 | **Coordinated checkpoint/restart (BLCR, DMTCP)** | **Does not transfer** | **There is no memory image.** An agent's state is a conversation history plus committed external side effects. A provider's server-side state cannot be snapshotted, and re-executing tool calls from a snapshot double-commits side effects. The replacement is durable execution (row 16). |
| 11 | **Uncoordinated checkpointing / domino effect** | Does not transfer (as a mechanism); transfers as a *hazard* | No checkpoints, so no domino. But the underlying hazard — cascading rollback through causal dependencies — recurs if AgentMPI ever lets one agent's replay invalidate another's already-consumed output. Worth stating as a constraint on replay design. |
| 12 | **Message logging (pessimistic / optimistic / causal)** | **Transfers, essentially as-is** | This is the mechanism agents actually need, and the classification is directly usable. Logging model completions and tool results *before* acting on them (pessimistic) makes replay exact; asynchronous logging (optimistic) is cheaper but can orphan downstream agents. The determinant to log is the *completion text*, and it is small. |
| 13 | **Young/Daly optimal checkpoint interval** | **Does not transfer** | The formulas optimize the trade-off between checkpoint write cost δ and expected rework M. With event-sourced logging, δ is microseconds (append a JSON record), so the optimum interval degenerates to "log everything." Cite for intellectual honesty, not for use. |
| 14 | **Replication / triple modular redundancy** | **Does not transfer** | TMR works because replicas fail *independently*. Repeated sampling from one model gives strongly correlated errors (same weights, same prompt); different models share training data and contamination. Voting over correlated errors yields a confident wrong majority. This is the most important negative result in the dossier. |
| 15 | **ABFT (algorithm-based fault tolerance)** | **Transfers, and is the most important transfer** | ABFT is the only mechanism here that detects *silent corruption* — a wrong answer with no error signal — which is the agent's dominant failure mode. The agent analogue of a checksum invariant is an independent verifier: a test suite, a type/schema check, a re-derivation by a different method, a cross-check against a retrieved source. The essential property is *independence of the invariant from the computation*, not the specific checksum algebra. |
| 16 | **Durable execution / event-sourced replay (Temporal, Step Functions, Durable Functions)** | **Transfers; the correct primary recovery mechanism** | Replaces checkpoint/restart. Deterministic orchestration + recorded non-deterministic results + idempotent activities. Model calls are Side Effects (replay the recording, do not re-sample); tool calls are Activities (idempotent or non-retryable). §B.6.2. |
| 17 | **Erlang supervision trees (`one_for_one` / `one_for_all` / `rest_for_one`)** | **Transfers** | A direct dependency model for agent groups: independent workers (`one_for_one`), a tightly coupled debate group where one death invalidates the shared context (`one_for_all`), a pipeline where downstream stages depend on upstream ones (`rest_for_one`). Also the `permanent`/`transient`/`temporary` child classification. |
| 18 | **Max restart intensity** | **Transfers, and should be mandatory** | Restarts are *billable*. An unbounded retry loop is invisible progress-free spend. Adopt as a required supervision parameter with a conservative default (OTP's is 1 restart per 5 s), expressed in both count-per-interval and cumulative-cost terms. |
| 19 | **"Let it crash"** | Transfers with adaptation | Adopt for *infrastructure* errors (transport, rate limit, malformed tool response): fail fast, let the supervisor decide. Do **not** adopt for *semantic* errors, because agents do not crash on those — see row 15. "Let it crash" is only a complete strategy when errors are self-announcing. |
| 20 | **Perfect (class P) failure detectors** | **Does not transfer** | P requires synchrony bounds. A model call taking 300 s is routine, so any timeout tight enough for prompt detection will false-positive on healthy work. AgentMPI's detector is ◇P at best, and the paper should say so. |
| 21 | **Heartbeats / leases** | Transfers | Standard and necessary. But cannot distinguish slow from dead (§B.4.3), so must be paired with row 22. Chen–Toueg QoS metrics (detection time, mistake recurrence, mistake duration) are the right way to specify the tuning. |
| 22 | **Fencing tokens** | **Transfers, and should be mandatory on side-effecting operations** | The only sound answer to a lease that expires on a live-but-slow agent. Every tool call with an external effect must carry a monotonic token, and the resource must reject stale ones. Without this, timing out a slow agent is unsafe (Kleppmann's Redlock critique applies verbatim). |
| 23 | **Charm++/AMPI migration & over-decomposition** | Transfers with adaptation | Over-decomposition makes recovery cheap: more tasks than agents means a failed agent loses a relocatable fraction. Migration is *easier* for agents (no memory image to move, just a transcript and a task handle). But there is no load-balance benefit from data locality, so the motivation is purely resilience. |
| 24 | **`MPI_Comm_spawn` (collective dynamic process management)** | Does not transfer | Its collective, synchronizing semantics and dependence on pre-reserved capacity are artifacts of static node allocation. Agent instantiation is genuinely cheap and local; AgentMPI should make spawning a local operation, and should say explicitly that it is departing from MPI here and why. |
| 25 | **MPI Sessions (compositional scoping)** | **Transfers** | Independent groups with independent lifetimes, error handlers, and settings, with no global world communicator. Exactly the structure a composite agent system needs so a planner subsystem's failure does not poison an evaluator subsystem. |
| 26 | **Malleability taxonomy (rigid/moldable/evolving/malleable)** | Transfers as vocabulary | Genuinely useful classification for agent topologies. The *mechanisms* do not transfer, because resizing an agent group requires no data redistribution. |
| 27 | **Eager/rendezvous + finite unexpected-message queue + safe-program discipline** | **Transfers** | Not usually filed under fault tolerance, but it is: the inbox and the context window are finite receiver-side resources, overflow is silent, and the discipline "a correct program does not rely on unbounded runtime buffering" transfers verbatim (§A.3.3). The threshold analogue is a token count, and — following CH4 — it should be provider-negotiated, not a constant. |
| 28 | **PMPI-style mandated interposition** | **Transfers** | Reserving a parallel name-shifted namespace so third parties can interpose without forking gave MPI its entire tool ecosystem, and Fenix used it to detect ULFM errors without touching the runtime. An agent protocol that mandates an interposition point gets observability for free. |

---

## Consolidated numbers

| Quantity | Value | Source |
| --- | --- | --- |
| Open MPI 4.1.6 `btl_tcp_eager_limit` | 65,536 B | measured, `ompi_info` |
| Open MPI 4.1.6 `btl_vader_eager_limit` | 4,096 B (rndv 32,768 B) | measured |
| Open MPI 4.1.6 `btl_openib_eager_limit` | 12,288 B | measured |
| Open MPI 4.1.6 `btl_self_eager_limit` | 1,024 B (rndv 131,072 B) | measured |
| MPICH 4.2.0 `MPIR_CVAR_CH3_EAGER_MAX_MSG_SIZE` | 131,072 B | measured, MPI_T |
| MPICH 4.2.0 CH4/OFI and Nemesis eager limits | −1 / −2 sentinels (provider- or runtime-chosen) | measured, MPI_T |
| MVAPICH2 2.3.7 `MV2_IBA_EAGER_THRESHOLD` | HCA-dependent; 12 KB for ConnectX | [mvapich2userguide §11.24] |
| MPICH 4.2.0 MPI_T control variables exposed | 438 | measured |
| Nemesis lock-free queue cost | 6 instructions enqueue, 11 dequeue | [buntinas2006nemesis] |
| Binned matching speedup | up to 3.5× | [flajslik2016matching] |
| ULFM failure-free 1-byte latency delta (shm / TCP / IB) | +0.10% / +0.21% / +0.03% | [bland2013ulfmjournal] |
| ULFM failure-free bandwidth delta (shm / TCP / IB) | −0.22% / −0.14% / +0.003% | [bland2013ulfmjournal] |
| ULFM IMB point-to-point + collective delta | < 5% (within std. dev.) | [bland2013ulfmjournal] |
| ERA failure-free scaling fit | 6.7 · log₂.₁₂₅(x), 0.6% asymptotic std. error (units `[UNVERIFIED]`) | [herault2015era] |
| ERA latency vs. optimized `MPI_Allreduce` | ~2× | [herault2015era] |
| ERA 24 h stress run (128 cores) | 969,739 agreements, 146,213 tolerated failures | [herault2015era] |
| Fenix / S3D on Titan | 250K cores, ~17 TB/s, 18 s interval, 0.41% overhead, 2+ TB/checkpoint | [gamell2014fenix] |
| Fenix programming cost in S3D | < 35 changed lines | [gamell2014fenix] |
| Fenix tolerated MTBF | < 1 minute (47 / 94 / 189 s tested) | [gamell2014fenix] |
| DMTCP checkpoint/restart, 128 cores | ~2 s; 0.2 s with forked checkpointing | [ansel2009dmtcp] |
| ABFT matrix–matrix multiply | 1.4 TFLOP/s on 484 procs, 65% peak, < 12% overhead | [bosilca2009abft] |
| Young's optimal checkpoint interval | τ_opt = √(2δM) | [young1974checkpoint] |
| OTP default max restart intensity | 1 restart per 5 seconds | [otpsupervisor] |
| MPICH ABI initiative members | MPICH 3.1, Intel MPI 5.0, Cray MPICH 7.0.0, MVAPICH2 2.0, ParaStation 5.1.7-1 | [mpichabi] |

---

## Claims marked `[UNVERIFIED]`

1. **ERA's `6.7 · log₂.₁₂₅(x)` units.** Almost certainly microseconds, but the figure's axis label was not recoverable from my text extraction of the technical report. Re-check against the published SC'15 figure before quoting the coefficient.
2. **Daly's higher-order coefficients.** The structure (perturbation series in √(δ/2M), Lambert-W connection, τ_opt = M for δ ≥ 2M) is confirmed from the abstract, the LANL record, and citing papers. The commonly quoted form τ_opt = √(2δM)·[1 + ⅓√(δ/2M) + ⅑(δ/2M)] − δ should be checked against Daly's own numbered equation.
3. **Implementation-quality barriers to `MPI_Comm_spawn` adoption.** Widely asserted; I found no primary quantitative study. The *specification-level* barriers in §B.7 (collective semantics, `MPI_UNIVERSE_SIZE` installation dependence, separate child `MPI_COMM_WORLD`) are verified from the standard and the Open MPI man page and are sufficient for the argument.
4. **MPICH 4.2.0 Nemesis runtime-chosen eager threshold.** The CVAR default is the sentinel −1 ("Nemesis will choose an appropriate value"); I did not determine the value actually chosen on the test machine, which would require instrumenting a live run.

---

## Open questions for the AgentMPI authors

1. **Which waist are we building?** MPICH shipped three nested ones (ADI-3 → CH3 → RDMA Channel) with an explicit portability/performance trade-off at each. Open MPI shipped one per concern with runtime selection. AgentMPI must state which model it follows and defend the choice; the CH4 lesson — keep the interface wide enough that no MPI-level information is lost, and supply a generic fallback — is probably the right one for a young interface whose providers differ wildly in capability.
2. **Who owns conversation matching?** Open MPI made this a runtime choice (`ob1`+`btl` versus `cm`+`mtl`). If some providers natively support threaded conversations, AgentMPI faces the identical fork and should probably make the same choice: both, selected at runtime.
3. **Is `AgentMPI_Comm_revoke` genuinely needed?** The dossier argues yes (row 6), because failure knowledge is non-uniform. But the argument depends on agents blocking on receives from peers rather than always going through a coordinator. If the reference implementation is star-topology, the deadlock scenario cannot arise and revoke is unmotivated. Resolve this before claiming it.
4. **What is the AgentMPI checksum?** Row 15 says ABFT is the most important transfer, but ABFT's power comes from an algebraic invariant preserved under the specific transformation. The paper needs a concrete, general answer to what plays that role for an agent — and "ask another model" is not it (row 14).
5. **How is the eager-threshold analogue negotiated?** Following CH4, the token count at which a message body is inlined rather than passed by reference should be a provider-negotiated property. What is the negotiation protocol?
