# Dossier 03 — Implementation Architecture and Fault Tolerance

**Paper:** *AgentMPI: A Message Passing Interface for Multi-Agent Systems*
**Serves:** the section justifying AgentMPI's abstract device interface (the narrow waist separating portable semantics from transport), and the section justifying its fault-tolerance design.
**Bibliography:** `refs/03-implementations.bib`
**Method:** standards claims were checked by downloading the MPI-3.1 and MPI-5.0 reports and searching the extracted text; eager thresholds were read from installed libraries (Open MPI 4.1.6 via `ompi_info`, MPICH 4.2.0 via a purpose-written MPI_T reader), not from documentation. Anything I could not pin to a primary source is marked `[UNVERIFIED]`.

**Two corrections to the brief, both load-bearing:**

1. **ULFM is not in MPI-5.0.** Searching the full MPI-5.0 text (June 5, 2025) for `failure mitigation`, `ULFM`, `MPI_COMM_REVOKE`, and `MPI_Comm_shrink` returns **zero hits**. See §B.1.4.
2. **IBM Spectrum MPI is Open MPI-derived, not MPICH-derived.** The MPICH family is MPICH, Intel MPI, Cray MPICH, MVAPICH2, ParaStation. See §A.1.5.

---

## Part A — Implementation Architecture

### A.1 MPICH's layering

#### A.1.1 ADI generations

MPICH's organizing claim, from the 1996 *Parallel Computing* paper: "All MPI functions are implemented in terms of the macros and functions that make up the ADI. All such code is portable" [gropp1996mpich]. The ADI specifies four capability groups — specify a message to send/receive; move data between API and hardware; manage pending-message lists; report on the execution environment [gropp1994adi]. The economic argument for a narrow waist is stated directly: "although MPI is a relatively large specification, the device-dependent parts are small."

**ADI-1** (1994) was the first generation, with an intermediate *channel device* abstraction [gropp1994adi, gropp1995channel]. **ADI-2** added tree-structured datatype descriptors that the device traverses for non-contiguous buffer translation, plus user-packed buffer support. **ADI-3** (MPICH2) is "a full-featured abstract device interface [with] many functions, so it is not a trivial task to implement all of them" [liu2004mpich2ib, gropp2004adi3] — that admission is the origin of CH3.

#### A.1.2 CH3: a stack of waists

Because ADI-3 was too wide to port to, MPICH2 added **CH3**: "a layer that implements the ADI3 functions, and provides an interface consisting of only a dozen functions. A 'channel' implements the CH3 interface" [liu2004mpich2ib]. A further narrowing, the **RDMA Channel**, exposed five functions. MPICH thus offered three trade-off points, "different trade-offs between communication performance and ease of porting" [liu2004mpich2ib].

**The transferable structural lesson:** MPICH did not choose one waist width; it shipped nested waists, each easier to port to and each giving up more performance because it discards more information from the originating MPI call.

CH3 channels included `sock` (TCP), `shm`/`sshm`, and `nemesis`. CH3 is non-blocking throughout: the user buffer pointer lives in an `MPID_Request` queued on a virtual connection, and a *progress engine* completes requests (§A.4).

#### A.1.3 Nemesis

Nemesis (Buntinas, Mercier, Gropp; CCGrid 2006, pp. 521–530) is "scalable and efficient both in the intranode communication context using shared-memory and in the internode communication case using high-performance networks," and "natively multimethod-enabled" [buntinas2006nemesis]. It was integrated into MPICH2 *as a CH3 channel* — it slots into the waist rather than bypassing it. Its core is a lock-free shared-memory queue with **enqueue and dequeue costs of 6 and 11 instructions**; it beat every other MPI tested on intranode latency for non-zero messages and on intranode bandwidth above 256 KB [buntinas2006nemesis, buntinas2006smpdata].

Nemesis refutes the naive claim that a narrow waist costs performance: the waist was narrow *and* the fast path fast, because it sat where semantics genuinely stopped being transport-specific.

#### A.1.4 CH4, netmods, shmmods

CH4 was built "from the ground up" with "minimum software overhead as the goal" [raffenetti2017ch4]. Three layers: the **ch4 core**, **netmods** (OFI/libfabric, UCX), and **shmmods** (POSIX, XPMEM). A `CH4_CALL` macro dispatches on an `is_local` predicate, or unconditionally to netmod under `MPIDI_CH4_DIRECT_NETMOD` [mpichdevices]. The commitment to quote:

> "the ch4 infrastructure is designed to allow most MPI-level arguments to flow all the way down to the netmods and shmmods. In other words, little to no information is lost from the MPI call that triggered a data movement operation. This enables each netmod and shmmod to independently determine the optimal implementation... If a network or shared-memory-specific optimization is not readily available, the module can simply fall back to the ch4 core's active-message-based implementation." [raffenetti2017ch4]

CH4 inverts CH3's trade-off. CH3 narrowed by *discarding* MPI-level information, forcing channels to re-derive intent; CH4 keeps a wider interface (`netmod_isend`, `netmod_irecv`, `netmod_put`, `netmod_get`) so transports can exploit NIC tag matching, RDMA, and triggered operations, falling back to generic active messages only when they cannot. It supports both multi-netmod dispatch (function pointers) and single-netmod inlining into the device layer (no indirect call). MPICH 4.2.0's default device is `ch4:ofi` [mpichdevices].

#### A.1.5 What lives above the ADI, and what below

| Concern | Side | Why |
| --- | --- | --- |
| Language bindings | Above | Machine-agnostic surface syntax |
| Argument checking, error classes, handlers | Above | Standard-defined, transport-independent |
| Communicator / group / context-ID management | Above | Bookkeeping over rank spaces |
| Derived datatype construction | Above | Layout algebra is portable |
| Machine-independent collectives | Above | Composed from point-to-point |
| Request objects and completion semantics | Above (model) / below (fulfilment) | Object model is standard; who advances it is not |
| Architecture-specific collectives | Below | Topology, hardware multicast, offload |
| Eager/rendezvous thresholds | Below | Per-transport buffer economics |
| Matching engine and queues | Below (in CH4) | Offloadable to the NIC |
| Progress engine | Below | Polls actual completion queues |
| Address/endpoint management, connection setup | Below | Fabric-specific |
| Data movement, registration, GPU-direct | Below | Hardware-determined |
| Active-message fallback | Below (ch4 core) | Generic implementation of anything unsupported natively |

Sources: MPICH layered-structure diagram (SC'21 BoF) and [raffenetti2017ch4, mpichdevices].

**The reusable principle: the ADI boundary sits where a decision stops being determined by the standard's semantics and starts being determined by the cost model of the medium.** Whatever has a *correct* answer fixed by MPI's definition goes above; whatever has a *good* answer determined by buffer sizes, latencies, and offload capability goes below.

For AgentMPI the analogous split is: message envelopes, group algebra, matching and ordering rules, collective *semantics*, and error classes above; model/provider selection, retry and backoff policy, token-budget-driven chunking (the eager/rendezvous analogue), context-window admission control, tool-call transport, and batching/caching below.

#### A.1.6 Derivatives

The MPICH ABI Compatibility Initiative (announced SC13) lists MPICH v3.1 (Feb 2014), Intel MPI Library v5.0 (Jun 2014), Cray MPICH v7.0.0 (Jun 2014), MVAPICH2 2.0 (Jun 2014), and ParaStation MPI 5.1.7-1 (Dec 2016) [mpichabi]. The MPI Forum's own framing is two ABI families — MPICH/Intel/MVAPICH/Cray, and Open MPI/NVIDIA HPC-X/Amazon MPI/**IBM Spectrum MPI** — together covering >90% of HPC platforms. IBM's user guide is explicit: "IBM Spectrum MPI is a complete MPI implementation, based on the Open MPI open source project" [ibmspectrum]. **Fix this in the brief before submission; an HPC PC will catch it.**

MVAPICH2 is the clearest reuse case: MPICH-derived, with InfiniBand support at CH3 level (its parameters apply to "OFA-IB-CH3" and "OFA-iWARP-CH3") [mvapich2userguide, liu2005mvapich]. Cray's Slingshot support is realized as libfabric endpoints in the CH4 OFI netmod [raffenetti2017ch4]. Note the arc: a de facto ABI emerged from shared internals, then MPI-5.0 standardized one (Chapter 20) [mpiforum2025mpi50, mpiforum2021mpi40] — a good template for framing the relationship between AgentMPI's reference implementation and a future portable interface.

---

### A.2 Open MPI's Modular Component Architecture

Open MPI took the opposite bet: many waists, one per concern, with runtime component selection [gabriel2004openmpi, squyres2004mca]. An **MCA framework** "manages zero or more components at run-time and is targeted at a specific task"; each supports one component *type* but many components of it [ompimca].

| Framework | Responsibility | Components |
| --- | --- | --- |
| `pml` | Point-to-point Messaging Layer | `ob1`, `cm` |
| `btl` | Byte Transfer Layer (used by `ob1`, and the `rdma` OSC) | `tcp`, `self`, `sm`/`vader`, `openib`, `ofi` |
| `mtl` | Matching Transport Layer (used exclusively by `cm`) | `psm2`, `ofi`, `portals4` |
| `coll` | Collective algorithms | `tuned`, `basic`, `libnbc`, `han`, `hcoll` |
| `osc` | One-sided / RMA | `rdma`, `sm`, `ucx` |
| `io` | MPI I/O | `romio`, `ompio` |

The `pml`/`btl` versus `pml`/`mtl` split is the structural detail that matters. `ob1` matches *in Open MPI* and drives dumb byte-moving BTLs [woodall2004ob1]; `cm` delegates matching *to the transport*, because some fabrics match in hardware. Open MPI did not choose — it made "who owns matching" runtime-selectable. AgentMPI faces the same fork: does the runtime own conversation matching, or can a provider with native threaded conversations own it?

**Selection.** `mca_base_select()` walks the available components, calls each `mca_query_component()` for a module and integer priority, and takes the highest (initialized to `INT32_MIN`); components without a query function are skipped, and a component may return `OPAL_ERR_FATAL` to abort selection when a user-required element is missing. Each framework also exposes a same-named MCA parameter taking a comma-delimited component list, optionally `^`-prefixed for exclusion; inclusive and exclusive forms cannot be mixed [ompimca].

**`coll` decision functions.** The `tuned` component has three modes: fixed decision (compiled-in trees), forced algorithm, and dynamic decision via a rules file [ompituned]. The rules file "effectively defines, for one or more collectives, a function of two variables, which given communicator and message size, returns an algorithm id." Resolution is two-phase: at communicator construction, a search on communicator size binds a set of message-size rules; at each invocation, a search of those rules picks the algorithm using the nearest rule below the actual message size. Unruled collectives fall back to the fixed tree. That two-phase shape — bind policy at group construction, resolve per call — is directly reusable for AgentMPI collectives, where the variables are group size and payload token count.

**Runtime.** ORTE provided launch, out-of-band communication, and fault/state management, itself built from MCA frameworks [castain2005orte]; it was later factored into PRRTE, with PMIx standardizing the application/runtime/resource-manager interface [castain2018pmix]. Same arc as the ABI: private runtime → standardized interface.

---

### A.3 The eager/rendezvous protocol

**Eager**: sender transmits envelope and payload immediately, assuming the receiver can store it. Reduces synchronization delay and simplifies programming, but "requires significant buffering," "may require active involvement of CPU to drain network at receiver's end," and "may introduce additional copy" [Gropp, CS598 lecture on buffering and message protocols]. **Rendezvous**: sender transmits only an envelope (RTS); the receiver, once it has a matching posted receive with a destination buffer, replies CTS; only then does data move. "Robust and safe (except for limit on the number of envelopes…)", "may remove copy (user to user direct)", but "may introduce synchronization delays" [ibid.].

**The limit exists because of receiver buffer pressure, not latency** — what most secondary sources get backwards. Eager buffering must be "reserved for arbitrary senders," and the "common approach in implementations is to provide same buffering for all members of `MPI_COMM_WORLD`; this is optimizing for non-scaleable computations" [ibid.]. The reservation is O(peers) per process, so at scale aggregate memory, not per-message latency, sets the threshold. Gropp's crossover rule states the latency side: "Eager is faster than rendezvous until data is unexpected: 2 × latency is smaller than the time to copy from buffer."

#### A.3.1 Real thresholds (measured, not documented)

| Implementation | Parameter | Default | Note |
| --- | --- | --- | --- |
| Open MPI 4.1.6 | `btl_tcp_eager_limit` | **65,536 B** | rndv limit also 65,536 |
| Open MPI 4.1.6 | `btl_vader_eager_limit` | **4,096 B** | rndv 32,768 |
| Open MPI 4.1.6 | `btl_openib_eager_limit` | **12,288 B** | rndv also 12,288 |
| Open MPI 4.1.6 | `btl_self_eager_limit` | **1,024 B** | rndv 131,072 |
| Open MPI 4.1.6 | `btl_ofi_eager_limit` | **0** | no eager path |
| MPICH 4.2.0 | `MPIR_CVAR_CH3_EAGER_MAX_MSG_SIZE` | **131,072 B** | CH3 device |
| MPICH 4.2.0 | `MPIR_CVAR_CH4_OFI_EAGER_MAX_MSG_SIZE` | **−1** | inherit provider `max_msg_size` |
| MPICH 4.2.0 | `MPIR_CVAR_NEMESIS_SHM_EAGER_MAX_SZ` | **−1** | Nemesis chooses at runtime |
| MPICH 4.2.0 | `MPIR_CVAR_NEMESIS_SHM_READY_EAGER_MAX_SZ` | **−2** | −1 ⇒ always eager; −2 ⇒ choose |
| MVAPICH2 2.3.7 | `MV2_IBA_EAGER_THRESHOLD` | **HCA-dependent; 12 KB for ConnectX** | [mvapich2userguide §11.24] |
| MVAPICH2 2.3.7 | `MV2_VBUF_TOTAL_SIZE` | **HCA-dependent; 12 KB for ConnectX** | should equal the eager threshold [§11.104] |
| MVAPICH2 2.3.7 | `MV2_SMP_EAGERSIZE` | **Architecture-dependent** | intranode switch [§11.105] |

Three things follow. (1) **The spread is nearly two orders of magnitude** — 1 KiB for `self` to 128 KiB for CH3 — and tracks receiver buffer cost, not wire speed: `self` has the *lowest* eager limit and *highest* rendezvous limit, because there is no wire and copying is pure waste. (2) **Modern implementations refuse to pick a number.** MPICH's CH4/OFI and Nemesis defaults are sentinels (−1, −2) meaning "ask the provider" or "decide at runtime"; the threshold has migrated from constant to negotiated property, and AgentMPI's token-count analogue belongs in provider negotiation, not the spec. (3) **MVAPICH2 ties threshold to buffer size** (`MV2_IBA_EAGER_THRESHOLD` == `MV2_VBUF_TOTAL_SIZE`), making the rationale explicit in the API.

#### A.3.2 The unexpected-message queue is finite, and the safe-program discipline

A message arriving with no matching posted receive goes into a pre-allocated *unexpected* buffer (eager) or leaves an envelope (rendezvous). Both are finite; rendezvous is safe only "except for limit on the number of envelopes." Overflow behaviour is implementation-defined, and the standard's position is that the program was wrong. MPI-5.0's rationale on communication modes:

> "Since any system will run out of buffer resources as message sizes are increased, and some implementations may want to provide little buffering, MPI takes the position that correct (and therefore, portable) programs do not rely on system buffering in standard mode. Buffering may improve the performance of a correct program, but it doesn't affect the result of the program." [mpiforum2025mpi50]

A program is **unsafe** if completion depends on the implementation buffering. The canonical case is a pairwise exchange where every rank blocks in `MPI_Send` before its `MPI_Recv`: it "will succeed only if the communication system will buffer at least `count` words of data. Otherwise, the program will deadlock" [MPI standard, Buffering and Safety]. Disciplined fixes: `MPI_Sendrecv`, non-blocking pairs, or `MPI_Bsend` with a user-sized buffer. `MPI_Ssend` is the diagnostic — synchronous mode cannot complete before a matching receive starts, so substituting it turns latent buffer-dependence into deterministic deadlock. The four send modes are a *flow-control vocabulary*, not four spellings of one thing.

**Transfer.** The analogue of the unexpected-message queue is an agent's inbox and, below it, its context window: both are finite receiver-side resources consumed by messages the receiver has not yet attended to, and overflow is silent (truncation, or eviction of exactly the content that was needed). The discipline transfers verbatim — *an AgentMPI program is unsafe if its correctness depends on the runtime buffering an unbounded number of unattended messages* (table row 27).

---

### A.4 Matching and progress

**Semantics.** Matching is on (source rank, tag, communicator), with `MPI_ANY_SOURCE`/`MPI_ANY_TAG` wildcards. The guarantee is **non-overtaking**: messages from one process to another on the same communicator are received in send order and cannot be overtaken, even if a later-posted receive would otherwise match first. Non-overtaking is what makes the queues *queues*, and it is exactly what makes efficient matching hard.

**Cost.** Implementations keep a posted-receive queue (PRQ) and an unexpected-message queue (UMQ), conventionally linked lists searched oldest-first, so search time grows linearly with depth. Flajslik, Dinan, and Underwood (ISC High Performance 2016, LNCS 9697, pp. 281–299; Hans Meuer Award) diagnosed it: "As applications scale up, communication patterns that [grow] with number [of] processes or threads per process can cause those lists to grow [and] become a performance problem" [flajslik2016matching]. Their **binned matching** replaces the list with a hash map keyed on (source, tag, communicator), carrying extra metadata and timestamps to preserve ordering under wildcards; with *b* bins the average search is O(n/b), degenerating to O(n) if everything hashes together. Reported speedup: **up to 3.5×**, no application changes. Related: trace-based simulation of matching behaviour without perturbation [ferreira2017matchsim]; a hardware queue-processing unit a decade earlier [underwood2005matchunit]. The modern endpoint is NIC-offloaded tag matching, which is why the `cm`/`mtl` and CH4 tagged-netmod paths exist.

**Progress.** MPI does not guarantee that communication advances outside MPI calls. In CH3, "a 'progress engine' updates pending requests and sets a completion flag... [It] can operate synchronously in a single threaded mode or asynchronously as a separate thread of execution." Most implementations progress only inside MPI calls by default, which is why the periodic-`MPI_Test` idiom exists and why non-blocking collectives so often fail to overlap with computation. CH4's `MPIR_CVAR_CH4_OFI_ENABLE_DATA_AUTO_PROGRESS` / `..._CONTROL_AUTO_PROGRESS` (default −1) again defer to the provider.

**Thread levels.** `MPI_Init_thread` requests one of `MPI_THREAD_SINGLE`, `FUNNELED`, `SERIALIZED`, `MULTIPLE`, and returns the level actually *provided*, which may be lower — a negotiation, not a demand. `MPI_THREAD_MULTIPLE` has historically been slow because the naive implementation is a global lock around the progress engine and matching queues; fine-grained locking and per-thread resources were sustained research topics [balaji2010threadsafety, dozsa2010concurrent], and CH4 addresses it with VCIs — virtual communication interfaces realized as a UCP worker (UCX) or libfabric endpoint (OFI), giving threads independent hardware contexts [raffenetti2017ch4].

**Transfer.** Adopt MPI's matching *semantics*, including non-overtaking (agents reason about conversational order in a way numerical kernels do not), but not its matching *optimizations* — agent message rates are orders of magnitude lower, so a linear scan is fine. Adopt the shape of `MPI_Init_thread`: request a concurrency level, receive the level actually provided, so a provider that cannot support concurrent streaming says so at init rather than mid-run.

---

### A.5 Tooling and observability

The premise, worth lifting into AgentMPI's motivation: **a parallel program's behaviour is invisible from any one process's output.** Each rank sees only its local event sequence; the interaction — who waited on whom, which collective had the straggler, which queue grew — exists only in the join across ranks, and no rank can print it.

**PMPI.** The standard mandates that every `MPI_Xxx` also be reachable as `PMPI_Xxx`. A tool defines its own `MPI_Xxx`, does its bookkeeping, and calls `PMPI_Xxx`; at link time the tool's symbol wins and the real implementation stays reachable. "The performance profiler intercepts the MPI operation and performs the necessary timing operations within a wrapper function with the same name... It then calls the corresponding name-shifted PMPI interface... without necessitating application code changes" [ramesh2018mpit]. TAU does this transparently via runtime pre-loading of shared objects [shende2006tau].

The decision to imitate: **the standard reserved a second parallel namespace purely so third parties could interpose without forking the implementation or touching user code.** An agent protocol that mandates an interposition point inherits an observability ecosystem for free.

**MPI_T.** Introduced in MPI-3.0 as the *tool information interface*, with two parts: **control variables** ("through which the MPI implementation tunes its configuration") and **performance variables** ("insight into internal performance information") [mpiforum2015mpi31 §14.3]. MPI-4 added a third: an **events** interface letting tools "query available events within an MPI implementation and register callbacks for them" [mpiforum2025mpi50 §15]. The standard is explicit that the set is implementation-defined and non-portable: "any application relying on a particular variable will not be portable."

Concretely, the MPICH 4.2.0 build I inspected exposes **438 control variables** [mpich420]. The standard's own worked example is a PMPI tool reading a pvar named `MPI_T_UMQ_LENGTH` — unexpected-message-queue length — to "identify receive operations that occur during times with long message queues" [mpiforum2025mpi50 §15]. That is exactly the §A.3.2 hazard, made observable [islam2014mpit, ramesh2018mpit]. The pattern to steal: **a self-describing, discoverable, explicitly non-portable introspection namespace, kept strictly separate from the portable API.**

**Formats and tools.** Trace formats: SLOG-2, with near-constant-time access to arbitrary time intervals in large traces [chan2000slog2, chan2008jumpshot], and its successor OTF2 [eschweiler2011otf2]. Measurement: Score-P, a *shared* runtime for Periscope, Scalasca, TAU, and Vampir [knupfer2012scorep]; TAU [shende2006tau]. Visualization: Vampir [nagel1996vampir], Jumpshot [chan2008jumpshot]. Lightweight profiling: mpiP, from Vetter and McCracken's statistical scalability analysis [vetter2001mpip]; Caliper, for cross-stack introspection [boehme2016caliper].

The Score-P story is the one to tell: four independent tool suites converged on a shared measurement runtime and trace format rather than each instrumenting MPI separately. Agent tracing is currently pre-Score-P, every framework emitting its own shape. A standardized operation vocabulary is the precondition for a shared trace format, exactly as MPI's was.

---

## Part B — Fault Tolerance

### B.1 The MPI standard's position

#### B.1.1 MPI-1's exclusion

MPI-1 (May 1994) specified no fault tolerance [mpiforum1994mpi10]. Gropp and Lusk's 2004 reconstruction is authoritative [gropp2004ftmpiprograms]:

> "A common misconception about MPI is that the MPI Standard itself mandates that if any MPI process dies, then all the MPI processes in the job must die as well. This is not true. ... The standard says that the default error handler on the communicator `MPI_COMM_WORLD` is the built-in one called `MPI_ERRORS_ARE_FATAL`. ... The MPI Forum decided that this would probably be the most useful default behavior, particularly for new users. (And when the MPI Forum was deliberating, all users were new.)"

And the framing AgentMPI should quote: "Fault tolerance is a property; what is it a property of? It is not a property of MPI itself, since MPI is a specification of an API. ... Is fault tolerance thus a property of an MPI implementation? No... We claim that fault tolerance is a property of an MPI program coupled with an MPI implementation."

The consequence of exclusion: fault tolerance moved outside the model, to job-granularity checkpoint/restart run by the batch system. That failure has a specific shape — the recovery mechanism could not see the program's structure, so it had to save and restore everything [snir2014exascalefailures, dongarra2015ftsurvey].

#### B.1.2 "Undefined state," and its removal

MPI-3.1 §8.3 (p. 340), verbatim: "After an error is detected, the state of MPI is undefined. That is, using a user-defined error handler, or `MPI_ERRORS_RETURN`, does not necessarily allow the user to continue to use MPI after an error is detected. ... An MPI implementation is free to allow MPI to continue after an error but is not required to do so." [mpiforum2015mpi31] §2.8 adds: "This document does not specify the state of a computation after an erroneous MPI call has occurred."

**MPI-4 removed this.** Searching MPI-5.0 for "state of MPI is undefined" returns zero hits. The replacement, §9.3 (pp. 448–449):

> "Some errors might prevent MPI from completing further API calls successfully and those functions will continue to report errors until the cause of the error is corrected or the user terminates the application. **The user can make the determination of whether or not to attempt to continue when handling such an error.**" [mpiforum2025mpi50, emphasis added]

And §9.2 (p. 457): "When an operation raises an error, it may not satisfy its specification... and the content of the output buffers, targeted memory, or output parameters is undefined. However, a valid error code shall always be set." A genuine shift: from "the library's state is undefined" to "the failed operation's outputs are undefined, the error code is always valid, and continuing is the user's call."

#### B.1.3 What MPI-4 added

From the MPI-5.0 change log (Appendix B, item 4, §§2.8, 9.3, 9.5, 11.2.1), recording changes introduced in MPI-4.0 and MPI-4.1 [mpiforum2025mpi50, mpiforum2021mpi40, mpiforum2023mpi41]:

- Calls unrelated to any object now attach to `MPI_COMM_SELF`, not `MPI_COMM_WORLD` — **localizing error impact**, the precondition for non-fatal handling.
- `MPI_ERRORS_ARE_FATAL` clarified to cover all *connected* processes.
- **`MPI_ERRORS_ABORT`**, "created to limit the scope of aborting": it aborts on the communicator it is invoked on; invoked on a session, "the operation aborts only the local MPI process" (§9.3, p. 447).
- The **initial error handler** is settable pre-initialization via the `mpi_initial_errhandler` info key, and "a high-quality implementation shall not deadlock during MPI initialization, even in the presence of failures" (§11.2, p. 478).
- **Sessions** (§B.7) give each component its own error configuration and failure scope.
- `MPI_SUCCESS` now "indicates only the result of the operation, not the state of the MPI library."

MPI-4/4.1/5.0 thus built the *substrate* — localized scope, non-global abort, guaranteed-valid error codes, per-session handlers — without adopting a recovery interface.

#### B.1.4 The true status of ULFM — verified

**ULFM is not part of any ratified MPI standard, including MPI-5.0.** Evidence:

1. I downloaded the MPI-5.0 report (98,983 lines of extracted text) and searched case-insensitively for `failure mitigation`, `ULFM`, `MPI_COMM_REVOKE`, `MPI_Comm_shrink`: **zero matches for all four** [mpiforum2025mpi50].
2. Open MPI v5.0.x documentation: "This implementation conforms to the User Level Failure Mitigation (ULFM) MPI Standard **draft proposal**," and "As ULFM is still an **extension** to the MPI standard, you will need to `#include <mpi-ext.h>`... to access the supplementary error codes and functions" [ompiulfmdocs].
3. The Fault Tolerance Working Group describes its document as "a specification for a Process Fault Tolerance chapter in the MPI Standard," based on MPI-4.1, "currently under evaluation by the MPI standardization body" [ulfmspec].

Real code therefore spells them `MPIX_Comm_revoke`, `MPIX_Comm_shrink`, `MPIX_Comm_agree` — `MPIX_` being the conventional non-standard-extension marker. **Use the `MPIX_` names for implementations and the `MPI_` names only when quoting the draft.** Claiming ratification is the commonest secondary-source error here.

Forum work targets three coexisting models: fine-grained (ULFM, for new applications), coarse-grained (ReInit, for existing C/R-based applications), and session-based isolation supporting shrinking and non-shrinking recovery; as of mid-2026 the working group reports it is "still stuck on [the] exact fault mode" [MPI Forum, "State of MPI," June 2026].

---

### B.2 FT-MPI

FT-MPI (Fagg and Dongarra, EuroPVM/MPI 2000) came out of HARNESS, implemented all of MPI-1.2 plus parts of MPI-2, and extended MPI semantics so applications could survive process loss [fagg2000ftmpi, fagg2005ftmpiharness]. "FT-MPI survives the crash of n−1 processes in an n-process job, and, if required, can respawn them. However, it is still the responsibility of the application to recover the data structures and the data on the crashed processes" [ftmpioverview].

Recovery is triggered by rebuilding the communicator via a modified `MPI_Comm_{create,split,dup}`; semantics depend on the **communicator mode**, chosen at launch [fagg2003ftmpiuse, ftmpioverview]:

| Mode | Rank space | Size | Failed ranks |
| --- | --- | --- | --- |
| **ABORT** | — | — | Graceful abort; user cannot trap it |
| **BLANK** | Unchanged | Unchanged (the *extent*) | Left as holes; addressing one raises invalid-rank |
| **SHRINK** | Renumbered contiguous | Reduced | Removed; must re-call `MPI_Comm_rank` |
| **REBUILD** | Unchanged | Unchanged | Respawned into empty ranks |

**Message modes** govern in-flight traffic: `NOP` (no user-level message operations after an error; everything returns an error so the application can unwind fast) and `CONT` (communication not involving the failed node continues) [fagg2003ftmpiuse]. REBUILD was the default and best-tested; SHRINK was fully supported only with CONT.

**Why BLANK can beat SHRINK.** SHRINK renumbers — cheap for the runtime, catastrophic for anything caching a rank-to-work mapping: `MPI_COMM_SIZE` changes, `MPI_COMM_RANK` changes, every rank-indexed structure is silently wrong. BLANK preserves the rank space at the cost of a discontinuous index set, where `MPI_COMM_SIZE` "will return the extent of the communicator, not the number of valid processes within it." REBUILD exists because some algorithms need a specific size (the cited example is power-of-two FFT solvers) and prefer paying for a respawn.

**Why AgentMPI cares.** For an agent harness the rank-to-work mapping is not merely cached — it is **baked into prompts**. "You are worker 3 of 8; handle shard 3" is a durable artifact of the rank space. Renumbering invalidates text already sent to a model, possibly already reasoned over, possibly already prefix-cached (with billing consequences). SHRINK is therefore *strictly worse* for agents than for numerical codes, and BLANK is the better default. MPI's own literature does not make this argument, because MPI has no notion of an identity that is expensive to re-establish. **This should be a named contribution.**

---

### B.3 ULFM

#### B.3.1 Primitives

Draft names (`MPI_`), with Open MPI's `MPIX_` forms [bland2013postfailure, ompiulfmdocs]:

- **`MPI_Comm_revoke(comm)`** — "Interrupts any communication pending on the communicator at all ranks." Non-collective: any one rank may revoke; the effect propagates. Subsequent operations return `MPI_ERR_REVOKED`.
- **`MPI_Comm_shrink(comm, &newcomm)`** — "creates a new communicator by eliminating all failed processes from a revoked communicator. The operation is collective and performs a consensus algorithm to ensure that all participating processes complete the operation with equivalent groups... This function cannot return an error due to process failure. Instead, such errors are absorbed as part of the consensus algorithm." Survivors are renumbered contiguously.
- **`MPI_Comm_agree(comm, &flag)`** — "an agreement algorithm which can be used to determine a consistent state between processes... collective and forms an agreement over a boolean value, even when failures have happened or the communicator has been revoked." Bitwise AND over contributions; a non-blocking `MPI_Comm_iagree` exists.
- **`MPI_Comm_failure_ack`** / **`MPI_Comm_failure_get_acked`** — the cheap *local* path: acknowledge locally known failures (also re-enabling `MPI_ANY_SOURCE` on the communicator) and retrieve the acknowledged-failed group. No consensus, so usable in an inner loop.
- New classes: `MPI_ERR_PROC_FAILED`, `MPI_ERR_PROC_FAILED_PENDING`, `MPI_ERR_REVOKED`.

Overarching guarantee: "no MPI call (point-to-point, collective, RMA, IO, …) can block indefinitely after a failure, but must either succeed or raise an MPI error" [ompiulfmdocs].

#### B.3.2 Mechanisms, not policy

The Working Group describes ULFM as "a minimal set of changes necessary for applications and libraries to include fault tolerance techniques and to construct more forms of fault tolerance (transactions, strongly consistent collectives, etc.)" [ulfmspec]. Losada et al. confirm the outcome: ULFM "does not include any specialized, non-portable mechanism to recover the application state at failed processes, providing developers... the flexibility to implement the most optimal methodology," and "the large and varied number of approaches in the literature proves that ULFM provides the necessary flexibility" [losada2020ulfmsurvey]. Applications whose pattern means a failure will never deadlock them "should not have to pay for the cost of complete recovery" [bland2013postfailure] — hence revoke is opt-in.

#### B.3.3 Why revoke is necessary and non-obvious

The deepest idea in ULFM, and the one most worth transplanting [bland2013postfailure]:

> "four processes are communicating in a point-to-point pattern. Process 2 is waiting to receive a message from process 3, which is waiting to receive a message from process 0, itself waiting to receive a message from process 1. In the meantime, process 1 has failed, but this condition is detected only by process 0, as other processes do not communicate with process 1 directly. At this point, without a new construct, the algorithm would reach a deadlock: the messages that processes 2 and 3 are waiting for will never arrive because process 0 has branched to enter recovery."

**Failure notification is inherently non-uniform.** Only the dead process's direct peers learn of the death; everyone else is blocked on operations that can never complete and has no way to find out. A local error return is therefore insufficient — you need a mechanism to *poison the communication context globally* so survivors are ejected from unsatisfiable operations. Revoke is that mechanism, and it is not the first thing a designer thinks of, because it looks like an escalation (permanently disabling the communicator) rather than a recovery.

The subtlety that makes it safe: if the initiator itself dies mid-propagation, "the Revoke notification is indeed lost, but the observed behavior, from the view of the application, is indiscernible from a failure at the initiator before the propagation started. As the algorithm still ensures agreement, there are no opportunities for inconsistent views" [bland2013postfailure]. Later releases replaced naive flooding with a reliable broadcast of fixed maximum output degree, logarithmic in rank count [ompiulfmdocs]. Shrink is "algorithmically, an agreement on which the consensus is done on the group of failed processes," and in the prototype shared one implementation with agree [hursey2011agreement, buntinas2012consensus].

#### B.3.4 Agreement cost: ERA

Agreement was the practical obstacle: "Previous uses of the ULFM constructs spotlighted the overhead of the agreement operation as one of the major obstacles preventing a larger adoption." **ERA (Early Returning Agreement)**, Herault et al., SC'15, fixed it [herault2015era]. The early-returning property: "as soon as a process can determine that the decision value is fixed (except if it fails itself), the process is allowed to return" — but because it returned early, later failures may compel further participation, so "the decision must remain available after the processes returned." Failure model: permanent crash in a pseudo-synchronous system.

Measured results [herault2015era]:

- **Logarithmic failure-free scaling.** On the Cray XC30 *darter*, bin/bin topology, 16 processes/node (average non-leaf branching degree 2.125), the fit is `era(x) = 6.7 · log₂.₁₂₅(x)` with **0.6% asymptotic standard error**. `[UNVERIFIED]` — the y-axis units are almost certainly microseconds, but the axis label was not recoverable from my text extraction; re-check the coefficient against the published figure.
- **~2× an optimized `MPI_Allreduce`**: "the latency is doubled, which is a logical consequence of the need for the ERA operation to sequentialize a reduce and a broadcast that do not overlap (to ensure the consistent decision criterion in the failure case)." The prior state of the art (two-phase commit) "exhibits a linear scaling with the number of nodes, despite the expected theoretical bound," and was abandoned at scale.
- Topology matters counterintuitively: bin/star (one representative with 16 local children) was *worse* than a flat binary tree, because "the resulting 16 sequential memcopy operations... take longer than the latency to cross the supplementary long-range links." Only bin/bin, which parallelizes intra-node copies, scaled logarithmically.
- **Robustness:** a 24 h stress run on 128 processors (16 nodes × 8 cores, TCP over GigE), looping agreements and replacing each killed process, "completed **969,739 agreements** successfully while tolerating **146,213 failures**." Post-failure tree rebalancing costs linearly in failures, so it should be conditional on topology degeneration.
- With agreement fixed, "the next largest overhead is the failure detection."

#### B.3.5 Failure-free overhead

Bland et al. measured the cost of *having* ULFM compiled in, on applications that do not use it [bland2013ulfmjournal, bland2012ulfm]. Platform: ORNL *Smoky* (quad-core 2.0 GHz Opterons, GigE + InfiniBand), shared-memory tests on *Romulus*; vanilla Open MPI r26237 versus the same revision with ULFM. NetPIPE v3.7, 1-byte latency (µs, cache hot) and bandwidth (Mbps):

| Interconnect | Latency vanilla | Latency ULFM | Δ | Bandwidth vanilla | Bandwidth ULFM | Δ |
| --- | --- | --- | --- | --- | --- | --- |
| Shared memory | 0.8008 | 0.8016 | **+0.0008** (+0.10%) | 10,625.92 | 10,602.68 | **−23.24** (−0.22%) |
| TCP | 10.2564 | 10.2776 | **+0.0212** (+0.21%) | 6,311.38 | 6,302.75 | **−8.63** (−0.14%) |
| OpenIB | 4.9637 | 4.9650 | **+0.0013** (+0.03%) | 9,688.85 | 9,689.13 | **+0.28** (+0.003%) |

Every difference is at or below the standard deviation (e.g. shared-memory latency std. devs. 0.0093 and 0.0161 against a 0.0008 delta). On IMB v3.2.3, "the duration difference of all the benchmarks (point-to-point and collective) remains below 5%, thus within the standard deviation." A weak-scaling Sequoia AMG study to 512 processes showed negligible difference.

**Headline: ULFM's failure-free overhead is below measurement noise (≤0.21% on 1-byte latency, ≤0.22% on bandwidth); its cost is paid only when failures occur.** This is the number to cite when arguing a fault-tolerance interface need not tax the common case.

In Open MPI v5.0.x ULFM is built by default (disable with `--without-ft`) but inactive unless enabled at runtime [ompi416]. Open MPI's supported-techniques page notes ULFM is now the *only* actively developed resilience approach there; coordinated and uncoordinated checkpoint/restart and data reliability were deprecated "due to lack of adoption and lack of maintenance."

---

### B.4 Detection

**FLP and the escape.** Fischer, Lynch, and Paterson proved that in a fully asynchronous message-passing system no deterministic protocol solves consensus if even one process may crash [fischer1985flp]. The obstruction is epistemic, not computational: a crashed process and an arbitrarily slow one are observationally identical. Dwork, Lynch, and Stockmeyer supply the escape — **partial synchrony**: either delay and relative-speed bounds exist but are unknown, or known bounds hold only after an unknown Global Stabilization Time [dwork1988partialsync]. Every practical system lives here, including ERA, which explicitly targets "pseudo-synchronous systems."

**Chandra–Toueg.** Two axes [chandra1996faildetectors]. *Completeness* — strong: every crashed process is eventually permanently suspected by every correct process; weak: by some correct process. *Accuracy* — strong: no correct process is ever suspected; weak: some correct process is never suspected; plus eventually-strong and eventually-weak. Eight classes; weak completeness can simulate strong, so the four named ones are strongly complete:

| Class | Accuracy | Character |
| --- | --- | --- |
| **P** (Perfect) | Strong | Never wrong. Realizable only in synchronous systems |
| **S** (Strong) | Weak | Some correct process is never suspected |
| **◇P** (Eventually Perfect) | Eventually strong | May be wrong for a while, then stops |
| **◇S** (Eventually Strong) | Eventually weak | Weakest of the four |

Consensus is solvable with any of them; **◇S requires a majority of correct processes** while S and P tolerate any number of crashes; and ◇W (equivalently ◇S) is *the weakest* failure detector for consensus [chandra1996weakest]. Consensus and atomic broadcast are reducible to each other under crash failures. Chen, Toueg, and Aguilera give the QoS framework — detection time, mistake recurrence, mistake duration — which is the right vocabulary for arguing about timeouts [chen2002qos]. For HPC specifically, [bosilca2018faildetector] designs a detector motivated by ERA's finding that detection had become the dominant post-failure cost.

**Heartbeats and leases.** Heartbeat detectors suspect a peer after *k* missed beats, trading detection latency against false positives. Gray and Cheriton introduced leases as "a consistency protocol that handles host and communication failures using physical clocks," where "after the lease expires, a read of the datum requires that the cache first extend the lease"; short-term leases give near-optimal efficiency despite the fault-tolerance provisions [gray1989leases].

**The limit is FLP restated in engineering terms: a lease-based detector cannot distinguish a slow process from a dead one.** An expired lease may mean a crash, a stop-the-world GC pause, a partition, or plain slowness. What real systems do: (1) **fail safe, not accurate** — declare expiry and let the still-alive holder discover it later, converting liveness into a safety problem, which makes fencing mandatory; (2) **fence** — Kleppmann's canonical treatment is "you need to include a fencing token with every write request... a fencing token is simply a number that increases every time a client acquires the lock," and the resource tracks the highest token seen and rejects anything lower [kleppmann2016locking]; (3) **mint the token from a consensus log** — Chubby's *sequencers* carry lock name, mode, and generation number, with `GetSequencer()`/`CheckSequencer()` and a lock-delay fallback for downstream services that cannot check [burrows2006chubby], while ZooKeeper's `zxid` and sequential znodes serve the same role [hunt2010zookeeper], as does etcd's revision; (4) **persist the counter**, or a restarted lock server lets a stale holder's old token pass.

Kleppmann's Redlock critique turns on exactly this: even if the algorithm were otherwise perfect, "it would not be safe to use, because you cannot prevent the race condition between clients in the case where one client is paused or its packets are delayed" — Redlock produces random values, not monotonic tokens. Generating a correct fencing token essentially requires consensus, because the token must be totally ordered with respect to grants.

**Transfer.** All of this transfers, and the LLM setting makes slow-versus-dead *worse*: a 300-second model call is entirely ordinary, so any timeout short enough for prompt detection fires on healthy work. AgentMPI should (a) not attempt a Perfect detector, (b) declare its detector ◇P at best and specify mistake-duration behaviour in Chen–Toueg terms, and (c) **mandate fencing tokens on every side-effecting tool call**, so an agent whose lease expired while it was merely thinking cannot commit a duplicate write.

---

### B.5 Recovery approaches

**Coordinated checkpoint/restart.** A consistent global snapshot (Chandy–Lamport [chandy1985snapshots]) with full restart on failure. **BLCR** is a Linux *kernel module*, "particularly notable because of its widespread usage," which "can only checkpoint processes on a single machine," distributed checkpointing being achieved by MPI libraries (some Open MPI, LAM/MPI, MVAPICH2, MPICH-V versions) integrating with it [hargrove2006blcr]. **DMTCP** is a *user-level* package needing no kernel modules or root, demonstrated on 20+ applications including MATLAB, Python, MPICH2, Open MPI, and runCMS (a 680 MB image with 540 dynamic libraries): "On 128 distributed cores (32 nodes), checkpoint and restart times are typically **2 seconds**, with negligible run-time overhead. Typical checkpoint times are reduced to **0.2 seconds** when using forked checkpointing," and checkpoint time "remains nearly constant as the number of nodes increases" [ansel2009dmtcp]. Multi-level checkpointing (SCR) exploits the fact that most failures are recoverable from node-local or neighbour storage, reserving the parallel file system for rare catastrophes [moody2010scr].

**Uncoordinated checkpointing and the domino effect.** If processes checkpoint independently, rollback cascades: A rolls back past a send to B, forcing B past its receipt, forcing B past a send to C, potentially to the start. Randell named this the **domino effect** [randell1975domino]; Elnozahy et al. is the canonical taxonomy [elnozahy2002rollback].

**Message logging** escapes the domino by making re-execution deterministic: log the non-deterministic events (chiefly receipt order) so a restarted process replays from its last local checkpoint. Three families [elnozahy2002rollback]. **Pessimistic** logs the determinant to stable storage *before* the receiver acts on it: simplest recovery (no orphans, survivors never roll back), highest failure-free cost. **Optimistic** logs asynchronously: cheap when nothing fails, but determinants can be lost, so survivors may become orphans and must roll back. **Causal** piggybacks determinants on outgoing messages, replicating the causal history in the processes it influenced, combining pessimistic's no-orphan guarantee with most of optimistic's low overhead at the cost of message growth. MPICH-V explored several protocols [bouteiller2006mpichv]; the model was later redesigned for performance [bouteiller2010messagelogging].

**Optimal checkpoint interval.** **Young (1974)**: τ_opt = √(2δM), with δ the checkpoint write time and M the mean time to interrupt [young1974checkpoint]. **Daly** extended it: the 2003 model gives τ = √(2δ(M+R)) − δ for δ ≪ M with R the restart time [daly2003model], and the 2006 paper derives "a more complete cost function and... a perturbation solution that provides accurate high order approximations" for Poisson single-component failures [daly2006checkpoint] — a perturbation series in √(δ/2M), reducing to Young in the small-δ limit and switching to τ_opt = M once δ ≥ 2M. `[UNVERIFIED]` — I confirmed the structure from the abstract, the LANL record, and citing papers, but not the paginated equation; check the commonly quoted coefficients against Daly's own numbered equation.

**Fenix** provides "online (i.e., without disrupting the job) and transparent recovery from process, node, blade, and cabinet failures" on ULFM, with process, data, and message recovery components [gamell2014fenix, gamell2016fenixspec]. Notably, "failure detection is delegated to ULFM-enabled MPI," and error codes "are detected in Fenix using MPI's profiling interface. As a result, no changes in the MPI runtime itself are required" — PMPI (§A.5) is load-bearing for fault tolerance too. On ORNL Titan with S3D [gamell2014fenix]: coordination-less checkpointing scaled to **250K cores**, sustaining **~17 TB/s** at an **18-second** interval with **0.41% overhead** versus a checkpoint-free run; 31+ billion grid points, **2+ TB per checkpoint**, 8.58 MB/core; tolerated MTBFs **under one minute** (47 s, 94 s, 189 s tested) "with lower overhead [than] coordinated C/R with failure rates of ~2.5 hours"; and **fewer than 35 new, changed, or rearranged lines** of S3D. Young's formula was used to set the interval (δ = 0.0748 s at 2197 cores) and validated by injecting single failures at varying wall times. Related ULFM-based work is surveyed in [losada2020ulfmsurvey, laguna2014evalulfm].

**Charm++/AMPI** virtualizes MPI ranks as migratable user-level threads over Charm++ objects [huang2004ampi]. Because the runtime owns placement, fault tolerance becomes an application of the migration machinery: FTC-Charm++ gives in-memory checkpoint-based tolerance for both Charm++ and MPI [zheng2004ftccharm], and proactive migration away from nodes predicted to fail is supported [acun2014charm]. **Over-decomposition makes recovery cheap:** with many more work units than processors, losing a processor loses a small relocatable fraction, and the runtime already knows how to relocate.

**ABFT, and the argument that matters.** Huang and Abraham (IEEE TC C-33(6):518–528, June 1984) introduced checksum-based fault tolerance for matrix operations on systolic arrays [huang1984abft]: encode with checksums and redesign the algorithm so "similar mathematical operations are applied to both the data and the checksum so that the checksum relationship is kept invariant during the course of the algorithm" [bosilca2015abftfactorizations] — ECC generalized from static data to data *under transformation*. A fault-tolerant matrix–matrix multiply achieved **1.4 TFLOP/s on 484 processors**, returning a correct result despite a process failure, at **65% of machine peak** and **under 12% overhead** versus the fastest failure-free implementation, with overhead falling as processor count rises [bosilca2009abft]. The generic framework of [bosilca2015abftfactorizations] extends to LU and QR by protecting the right factor with a conventional checksum and the left factor with a "vertical checkpointing scheme," and notes that ABFT's relative cost "asymptotically tends toward zero."

ABFT was introduced specifically "to deal with silent error" [bosilca2015abftfactorizations] — corruption that produces a wrong answer with no error signal. Checkpoint/restart cannot touch this class:

> **Checkpoint/restart faithfully preserves silent corruption.** A checkpoint is a bit-for-bit snapshot. If corruption occurred before the snapshot, the snapshot contains it, and restarting reproduces it exactly. Checkpointing has no notion of *correct*, only of *the same as before*. ABFT carries a redundant invariant alongside the data, so a violated checksum *detects* the corruption and often suffices to correct it.

This is the distinction AgentMPI needs, because **an agent's dominant failure mode is a confident wrong answer** — the LLM analogue of silent data corruption. An agent that hallucinates a fact, mis-parses a tool result, or fabricates a citation does not crash; it emits plausible, well-formed, wrong output and continues. Every crash-recovery mechanism in Part B is blind to this and will faithfully preserve it. Only mechanisms with an *independent invariant* — a verifier, a test suite, a schema check, a re-derivation by another method, a cross-check against a retrieved source — can detect it. State it as: **the HPC mechanism AgentMPI most needs is the one HPC uses least, and the one HPC uses most is the one AgentMPI can least use.**

**Replication does not substitute for ABFT.** Triple modular redundancy works because replicas fail *independently*. Three calls to one model with one prompt are not independent — the errors are functions of the same weights and input; across models, training-data overlap and benchmark contamination correlate them. Voting over correlated wrong answers yields a confidently wrong majority. Models are also poor judges of their own errors [stechly2024selfverification, huang2024selfcorrect], and [cemri2025multiagentfail] documents these failure classes at the system level.

---

### B.6 Supervision from outside HPC

**Erlang/OTP supervision trees.** Erlang's answer to partial failure is architectural: processes are cheap and isolated, expected to crash, and the structure that restarts them is a first-class artifact [armstrong2003erlang, armstrong2007erlangbook]. "Let it crash" means: do not write defensive handling for states you did not anticipate; crash cleanly and let a supervisor with a wider view decide. Restart strategies [otpsupervisor]:

| Strategy | Behaviour on a child's termination |
| --- | --- |
| `one_for_one` | Only that child restarts. **Default.** |
| `one_for_all` | All remaining children terminate; then all, including the terminated one, restart |
| `rest_for_one` | Children *after* the terminated one in start order terminate; then it and they restart |
| `simple_one_for_one` | Simplified `one_for_one` for dynamically added instances of one process type |

The strategies encode a dependency model: independent children, mutually dependent children, and a start-order chain. Children are additionally `permanent` (always restarted), `transient` (only on abnormal termination), or `temporary` (never).

**Maximum restart intensity** is the most-overlooked and most agent-relevant part. "If more than `MaxR` restarts occur in the last `MaxT` seconds, the supervisor terminates all the child processes and then itself. The termination reason for the supervisor itself in that case will be `shutdown`." Defaults: `intensity` = **1**, `period` = **5** seconds, "chosen to be safe for most systems, even with deep supervision hierarchies" [otpsupervisor]. Because the supervisor's own death propagates upward, exceeding intensity *escalates* rather than absorbs.

**Why an unbounded restart loop is worse than a crash.** A crash terminates, is visible, produces a trace, and gives something outside a chance to act. An unbounded restart loop is invisible progress-free work — the system looks alive, health checks pass, resources burn, logs fill with identical errors, nothing advances. For agents this is sharper still, because every restart is a *billable* model call: a loop of wrong tool call, error, retry consumes a budget in minutes with zero progress. **AgentMPI should make max restart intensity a required supervision parameter with a conservative default, expressed both as restarts-per-interval and as cumulative cost.**

**Durable execution is the right analogue.** For recovering an agent, the correct model is not checkpoint/restart but durable execution, because *there is no memory image to restore*. An agent's state is a conversation history plus side effects already committed externally; you cannot snapshot a provider's server-side state, only replay the interaction that produced it.

Temporal's model: "The system functions via event sourcing: an append-only history of events is stored for each workflow execution, and all required workflow state can be recreated at any time by replaying this history" [temporaldocs]. On worker failure a new worker re-executes the workflow function from line one; the event history supplies the results of previously completed activities, which are therefore *not* re-executed; on exhausting the history, execution continues.

| Temporal concept | Requirement | AgentMPI analogue |
| --- | --- | --- |
| Workflow code | **Deterministic** — no RNG, no wall clock, no unguarded I/O | Orchestration: who is asked what, in what order |
| Activity | **Idempotent** (at-least-once) or explicitly non-retryable | Tool invocations |
| Event history | Append-only, durable, the source of truth | Transcript plus tool-result log |
| Side Effect | Non-deterministic snippet whose result is recorded once and returned from the record on replay | Model sampling call |
| Retry policy | Applied at the leaf; propagates to the parent only when exhausted | Per-agent retry budget, escalating to the supervisor |

Lineage for related work: durable recoverable distributed computation descends from **Argus** (Liskov's guardians and atomic actions, CACM 1988 [liskov1988argus]) and **Camelot/Avalon** [eppinger1991camelot], through 1990s workflow systems, to today's "durable execution": **Temporal** (descended from Amazon SWF, Microsoft's Durable Task Framework, and Uber's Cadence), **AWS Step Functions** [awsstepfunctions], and **Azure Durable Functions** [azuredurable].

The critical adaptation: **a model sampling call is a Side Effect, not an Activity.** It is non-deterministic by construction, so replay must return the *recorded* completion rather than re-sampling, which would diverge from the recorded history and collapse the scheme. That is precisely why record-and-replay is the right recovery primitive for agents and snapshot-and-restore is not.

---

### B.7 Elastic and malleable jobs

**Dynamic process management (MPI-2, 1997)** added `MPI_Comm_spawn` [gropp1998dynamicprocess]. The limiting semantics are in the specification itself: spawn "is collective over `comm`, and also may not return until `MPI_Init` has been called in the children. Similarly, `MPI_Init` in the children may not return until all parents have called `MPI_Comm_spawn`. In this sense, `MPI_Comm_spawn` in the parents and `MPI_Init` in the children form a collective operation over the union of parent and child processes." Children get their *own* `MPI_COMM_WORLD`, joined to the parents' only by an inter-communicator.

Why it went largely unused: (1) the **resource model does not fit** — batch schedulers allocate a fixed node set, spare capacity is discoverable only through `MPI_UNIVERSE_SIZE`, which is "installation-dependent" and often unset, and exceeding the hardware means time-slicing with no gain; (2) it is **heavily collective and synchronizing**, so it cannot be used opportunistically; (3) the **rank space fragments** — a separate child world plus an inter-communicator is an awkward object for SPMD codes written against one flat rank space. `[UNVERIFIED]` — implementation quality and portability are also widely cited as barriers, but I found no primary quantitative study.

**MPI Sessions (MPI-4)** attack from the other end: `MPI_COMM_WORLD` is itself the barrier. Sessions "remove the known scalability barriers by no longer requiring all possible communication peers to be included in `MPI_COMM_WORLD`," delivering "a tighter integration of MPI applications with the underlying runtime system; and a scalable representation of communication groups" [holmes2016sessions]. `MPI_Session_init` creates a session; process sets are queried by URI from the runtime; communicators are built from those groups; and independent software components can each initialize their own session without coordination, so MPI can effectively be initialized and finalized multiple times in one application. Each session carries its own `info` (permitting per-session settings of previously global properties such as thread level) and its own error handler.

Three consequences for AgentMPI. (1) **Failure scope becomes compositional** — a failure can be confined to one session rather than poisoning a global context, which is why the Forum's fault-tolerance work now includes a session-based model. (2) **Elasticity becomes expressible** — Sessions enable "the ability to shrink or grow the number of participating processes during an application's execution." (3) **Multiple independent initializations** is exactly what a composite agent system needs, where a planner, a tool executor, and an evaluator want independent groups with independent policies.

**Malleability research.** Feitelson and Rudolph distinguish *rigid*, *moldable* (size fixed at launch), *evolving* (application-initiated resize), and *malleable* (scheduler-initiated resize) jobs [feitelson1996malleable]. Elastic MPI added infrastructure and API extensions for elastic execution with scheduler cooperation [compres2016elasticmpi]; Invasive Computing proposed resource-aware programming with invade/infect/retreat [teich2011invasive]; DMR integrates malleability with the batch system [prabhakaran2015dmr]. Agent systems are *natively* malleable — adding an agent costs an API key and a rate-limit slot, not a node allocation, and there is no data redistribution because there is no distributed array — so this literature is mostly a source of *vocabulary*. The one mechanism worth importing is Sessions' compositional scoping.

---

## Transfer Table: HPC Fault-Tolerance Mechanisms → LLM Agents

| # | Mechanism | Verdict | Reasoning |
| --- | --- | --- | --- |
| 1 | `MPI_ERRORS_ARE_FATAL` default | Adapt | Fail-fast suits a new system's users, but MPI-4's `MPI_ERRORS_ABORT` (abort a scope, not the world) is better: agent runs are long and expensive. Adopt MPI-4's localization, not MPI-1's default. |
| 2 | Localized error scope (`COMM_SELF` attachment, per-session handlers) | **Transfers** | Pure semantics. A failed tool call should attach to that agent's scope. |
| 3 | FT-MPI **BLANK** (hole in rank space) | **Transfers; right default** | Preserves prompt-embedded identity (§B.2). Strongest single transfer here. |
| 4 | FT-MPI **SHRINK** (renumber) | **Does not transfer** | Renumbering invalidates mappings baked into already-sent prompts, cached prefixes, and completed reasoning. Cheap in MPI, catastrophic here. |
| 5 | REBUILD-style respawn into the vacated rank | Transfers | A new instance can occupy the identity: no memory image to reconstruct, only a transcript to replay. Cheaper for agents than for MPI. |
| 6 | `MPI_Comm_revoke` | **Transfers; contribution-grade** | The pathology recurs verbatim: C awaits B, B awaits A, A dies, only A's peer knows. Without revoke, C and B hang until a wall-clock timeout — unbounded, since slow-vs-dead is undecidable (§B.4). |
| 7 | `MPI_Comm_agree` / survivor consensus | Adapt | Needed for "did the round complete?", but the cost calculus inverts: ERA's ~µs-scale agreement is negligible against a 300–3000 ms model call, so a much simpler (even centralized) agreement suffices. Take the semantics, drop the algorithm. |
| 8 | `failure_ack` / `failure_get_acked` (local, no consensus) | **Transfers** | The cheap local tier matters more when the expensive tier is a network round trip among agents. |
| 9 | "Mechanisms not policy" | **Transfers** | Agent recovery policy is even more application-specific than HPC's; the variety of ULFM-based frameworks is evidence the principle works. |
| 10 | Coordinated checkpoint/restart (BLCR, DMTCP) | **Does not transfer** | **No memory image exists.** State is a transcript plus committed external effects; provider server-side state cannot be snapshotted, and re-running tool calls from a snapshot double-commits. Replaced by row 16. |
| 11 | Uncoordinated checkpointing / domino effect | Does not transfer as mechanism; transfers as hazard | No checkpoints, so no domino — but cascading rollback through causal dependencies recurs if one agent's replay can invalidate another's already-consumed output. State it as a constraint on replay design. |
| 12 | Message logging (pessimistic / optimistic / causal) | **Transfers essentially as-is** | The classification is directly usable. Logging completions and tool results *before* acting on them (pessimistic) makes replay exact; async logging (optimistic) is cheaper but can orphan downstream agents. The determinant is the completion text, and it is small. |
| 13 | Young/Daly optimal checkpoint interval | **Does not transfer** | The formulas trade checkpoint write cost δ against expected rework. With event-sourced logging δ is microseconds (append a record), so the optimum degenerates to "log everything." Cite for honesty, not use. |
| 14 | Replication / triple modular redundancy | **Does not transfer** | TMR needs *independent* replica failures. Repeated sampling from one model gives strongly correlated errors; different models share training data and contamination. Voting over correlated errors yields a confident wrong majority. Most important negative result here. |
| 15 | **ABFT** | **Transfers; most important transfer** | The only mechanism that detects *silent corruption* — a wrong answer with no error signal — which is the agent's dominant failure mode. The analogue of a checksum invariant is an independent verifier: test suite, schema check, re-derivation by another method, cross-check against a source. The essential property is *independence of the invariant from the computation*. |
| 16 | Durable execution / event-sourced replay | **Transfers; correct primary recovery mechanism** | Deterministic orchestration + recorded non-deterministic results + idempotent activities. Model calls are Side Effects (replay the recording); tool calls are Activities. §B.6. |
| 17 | Erlang supervision trees | **Transfers** | Direct dependency model: independent workers (`one_for_one`), a debate group where one death invalidates shared context (`one_for_all`), a pipeline (`rest_for_one`); plus `permanent`/`transient`/`temporary`. |
| 18 | Max restart intensity | **Transfers; should be mandatory** | Restarts are billable; an unbounded retry loop is invisible progress-free spend. Required parameter, conservative default (OTP's is 1 per 5 s), expressed in both count and cost. |
| 19 | "Let it crash" | Adapt | Right for *infrastructure* errors (transport, rate limit, malformed tool response). Wrong for *semantic* errors, because agents do not crash on those (row 15). Only a complete strategy when errors are self-announcing. |
| 20 | Perfect (class P) failure detectors | **Does not transfer** | P needs synchrony bounds. A 300 s model call is routine, so any timeout tight enough for prompt detection false-positives on healthy work. AgentMPI's detector is ◇P at best; say so. |
| 21 | Heartbeats / leases | Transfers | Necessary, but cannot distinguish slow from dead, so must be paired with row 22. Specify tuning in Chen–Toueg QoS terms. |
| 22 | Fencing tokens | **Transfers; mandatory on side-effecting operations** | The only sound answer to a lease expiring on a live-but-slow agent. Every externally effectful tool call carries a monotonic token; the resource rejects stale ones. Without this, timing out a slow agent is unsafe. |
| 23 | Charm++/AMPI migration and over-decomposition | Adapt | Over-decomposition makes recovery cheap; migration is *easier* for agents (move a transcript and a task handle, not a memory image). But there is no locality-driven load-balance benefit, so the motivation is purely resilience. |
| 24 | `MPI_Comm_spawn` | Does not transfer | Its collective, synchronizing semantics and dependence on pre-reserved capacity are artifacts of static allocation. Agent instantiation is genuinely cheap and local; make spawning local, and say explicitly that this departs from MPI and why. |
| 25 | MPI Sessions (compositional scoping) | **Transfers** | Independent groups with independent lifetimes, error handlers, and settings, with no global world. Exactly what lets a planner subsystem fail without poisoning an evaluator. |
| 26 | Malleability taxonomy | Transfers as vocabulary | Rigid/moldable/evolving/malleable is a useful classification for agent topologies; the mechanisms do not transfer, since resizing needs no data redistribution. |
| 27 | Eager/rendezvous + finite UMQ + safe-program discipline | **Transfers** | Not usually filed under fault tolerance, but it is: inbox and context window are finite receiver-side resources, overflow is silent, and "a correct program does not rely on unbounded runtime buffering" transfers verbatim (§A.3.2). The threshold analogue is a token count and, following CH4, should be provider-negotiated. |
| 28 | PMPI-style mandated interposition | **Transfers** | A reserved parallel namespace gave MPI its whole tool ecosystem, and let Fenix detect ULFM errors without touching the runtime. Mandate an interposition point and get observability for free. |

---

## Consolidated numbers

| Quantity | Value | Source |
| --- | --- | --- |
| Open MPI 4.1.6 `btl_tcp_eager_limit` | 65,536 B | measured |
| Open MPI 4.1.6 `btl_vader_eager_limit` | 4,096 B (rndv 32,768) | measured |
| Open MPI 4.1.6 `btl_openib_eager_limit` | 12,288 B | measured |
| Open MPI 4.1.6 `btl_self_eager_limit` | 1,024 B (rndv 131,072) | measured |
| MPICH 4.2.0 `CH3_EAGER_MAX_MSG_SIZE` | 131,072 B | measured (MPI_T) |
| MPICH 4.2.0 CH4/OFI, Nemesis eager limits | −1 / −2 sentinels | measured (MPI_T) |
| MVAPICH2 2.3.7 `MV2_IBA_EAGER_THRESHOLD` | HCA-dependent; 12 KB for ConnectX | [mvapich2userguide] |
| MPICH 4.2.0 MPI_T control variables | 438 | measured |
| Nemesis lock-free queue | 6 instructions enqueue, 11 dequeue | [buntinas2006nemesis] |
| Binned matching speedup | up to 3.5× | [flajslik2016matching] |
| ULFM failure-free latency delta (shm/TCP/IB) | +0.10% / +0.21% / +0.03% | [bland2013ulfmjournal] |
| ULFM failure-free bandwidth delta | −0.22% / −0.14% / +0.003% | [bland2013ulfmjournal] |
| ULFM IMB delta (pt2pt + collective) | < 5%, within std. dev. | [bland2013ulfmjournal] |
| ERA failure-free fit | 6.7 · log₂.₁₂₅(x), 0.6% asymptotic std. error (units `[UNVERIFIED]`) | [herault2015era] |
| ERA vs. optimized `MPI_Allreduce` | ~2× | [herault2015era] |
| ERA 24 h stress run (128 cores) | 969,739 agreements, 146,213 tolerated failures | [herault2015era] |
| Fenix / S3D on Titan | 250K cores, ~17 TB/s, 18 s interval, 0.41% overhead, 2+ TB/checkpoint | [gamell2014fenix] |
| Fenix programming cost | < 35 changed lines of S3D | [gamell2014fenix] |
| Fenix tolerated MTBF | < 1 min (47 / 94 / 189 s tested) | [gamell2014fenix] |
| DMTCP, 128 cores | ~2 s checkpoint/restart; 0.2 s forked | [ansel2009dmtcp] |
| ABFT matrix multiply | 1.4 TFLOP/s on 484 procs, 65% peak, < 12% overhead | [bosilca2009abft] |
| Young's optimal interval | τ_opt = √(2δM) | [young1974checkpoint] |
| OTP default restart intensity | 1 restart per 5 seconds | [otpsupervisor] |
| MPICH ABI members | MPICH 3.1, Intel MPI 5.0, Cray MPICH 7.0.0, MVAPICH2 2.0, ParaStation 5.1.7-1 | [mpichabi] |

---

## Claims marked `[UNVERIFIED]`

1. **ERA's `6.7 · log₂.₁₂₅(x)` units** — almost certainly microseconds, but the axis label was not recoverable from my text extraction. Check the published SC'15 figure before quoting the coefficient.
2. **Daly's higher-order coefficients** — structure confirmed, paginated equation not obtained (§B.5).
3. **Implementation-quality barriers to `MPI_Comm_spawn` adoption** — widely asserted, no primary quantitative study found; the specification-level barriers in §B.7 are verified and sufficient.
4. **MPICH 4.2.0 Nemesis runtime-chosen eager threshold** — the CVAR default is the sentinel −1; the value actually chosen would require instrumenting a live run.

---

## Open questions for the AgentMPI authors

1. **Which waist are we building?** MPICH shipped three nested ones (ADI-3 → CH3 → RDMA Channel); Open MPI shipped one per concern with runtime selection. The CH4 lesson — keep the interface wide enough that no call-level information is lost, and supply a generic fallback — is probably right for a young interface whose providers differ wildly in capability.
2. **Is `AgentMPI_Comm_revoke` genuinely needed?** The argument (row 6) depends on agents blocking on receives from peers rather than routing through a coordinator. If the reference implementation is star-topology, the deadlock cannot arise and revoke is unmotivated. Resolve before claiming it.
3. **What is the AgentMPI checksum?** Row 15 calls ABFT the most important transfer, but ABFT's power comes from an algebraic invariant preserved under a specific transformation. The paper needs a concrete general answer to what plays that role — and "ask another model" is not it (row 14).
