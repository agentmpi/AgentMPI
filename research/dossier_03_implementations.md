# Dossier 03 — MPI Implementation Internals, Tooling, and Software Architecture

**Scope.** The *software architecture* of production MPI implementations (MPICH, Open MPI,
MVAPICH2) and the tooling ecosystem layered on them, written to extract architectural patterns
reusable in a non-HPC message-passing runtime (AgentMPI). It favors structural detail — layer
boundaries, interface signatures, selection mechanisms, data models — over feature enumeration.
Claims not verified against a primary source are marked `[UNVERIFIED]`.

---

## 1. MPICH: the device abstraction and its refactorings

### 1.1 What the ADI buys you

MPICH implements MPI's user-visible API *once*, portably, over the **Abstract Device Interface
(ADI)**: `MPID_`-prefixed functions where `MPID_Send` implements `MPI_Send`. Nearly every MPI
function calls its MPID counterpart, which either supplies full functionality or falls back to
the portable `MPIR_` path [MPICH DevGuide; Gropp et al. 1996]. Two consequences matter more than
portability. First, **vendor forkability without forking**: the stated goal is "to allow
downstream vendors to easily create vendor specific implementations" [MPICH DevGuide], and Intel
MPI, Cray MPI and MVAPICH are derivatives that replace the *device*, not the MPI layer. Second, a
**ladder of porting effort**: MPICH-1 exposed ADI-2 plus a Channel interface, MPICH2 exposed
ADI-3, CH3 (about a dozen functions) and an RDMA Channel (five), which "provide different
trade-offs between communication performance and ease of porting" [Liu et al. 2004; Gropp & Lusk
2001]. The device abstraction is therefore chiefly about letting a *third party* replace the
performance-critical bottom of the stack while inheriting the semantic complexity of the top.

### 1.2 ADI-3 and CH3

ADI-3 "has many functions, so it is not a trivial task to implement all of them" [Liu et al.
2004]. CH3 is an *example implementation of ADI-3* that re-exposes a small "channel" interface in
five groups: channel init, per-virtual-connection init, process-group hooks (`MPI_Comm_spawn`,
`MPI_Comm_spawn_multiple`, `MPI_Join`), data sending, and progress [MPICH CH3 wiki]. Two
mechanisms recur in every serious message-passing runtime:

- **Virtual connections (VCs)** — per-peer state objects. A channel appends its own fields via
  `MPIDI_CH3_VC_DECL`, and likewise extends requests, packet enums and progress state
  (`MPIDI_CH3_REQUEST_DECL`, `MPIDI_CH3_PKT_ENUM`, `MPIDI_CH3_PROGRESS_STATE_DECL`) [MPICH CH3
  wiki]. The generic layer owns the struct; the transport owns an opaque extension region *inside*
  it.
- **The progress engine** — asynchronous operations store a user-buffer pointer in an
  `MPID_Request` queued on the VC, and the engine advances pending requests and sets completion
  flags [Mucci et al. 2005]. All ADI point-to-point operations are nonblocking: they only
  *initiate*, completing inside `MPID_Progress_wait/test` [MPICH DevGuide].

CH3 is in maintenance mode but still fully supported because vendors depend on it; its channels
are `ch3:sock` and `ch3:nemesis` [MPICH DevGuide].

### 1.3 Nemesis: shared memory as the *primary* case

Nemesis inverted the conventional priority order. Its goals, *in order*: scalability,
high-performance intranode, high-performance internode, multi-network internode — and the authors
state that "we strive to minimize the overhead for intranode communication, even if this comes at
some penalty for internode communication," with network modules "designed around the queue
mechanism, rather than the other way around" [Buntinas et al. 2006]. Concretely:

- **One lock-free receive queue per process, not per pair.** A queue per *pair* needs O(N²)
  queues, a single locked queue contends on a large SMP, and polling many queues is itself
  expensive [Buntinas et al. 2006].
- **Multiple-enqueuer, single-dequeuer lock-free queues**, via an algorithm "similar to the MCS
  lock, using swap and compare-and-swap." Measured cost: **six instructions to enqueue, eleven to
  dequeue**. Queue pointers are *relative* offsets, since the shared region need not be mapped at
  the same virtual address in every process [Buntinas et al. 2006].
- **Fastboxes** — "a single buffer with a full/empty flag." The sender writes the fastbox and sets
  the flag instead of using the queue; receivers check fastboxes first, bypassing the queue for
  small messages. It "would not scale well for large SMPs and is used only for SMPs with a small
  number of processors" [Buntinas et al. 2006; Buntinas et al. 2007] — an explicit statement that
  a fast path may be non-general.
- **Unified send path** — a network module has a send queue "analogous to a process's lock-free
  receive queue," so "no special action is taken when sending a message to a process on a remote
  node versus" a local one [Buntinas et al. 2006]. Locality is expressed by *which queue you
  enqueue on*, not by a branch in the send routine.

Nemesis shipped as a CH3 channel rather than an ADI-3 device for time-to-prototype reasons, with
CH3 modified to permit Nemesis-specific optimizations [Buntinas et al. 2007]. It also used
architecture-specific `memcpy` with cache-bypassing non-temporal stores on x86/x86-64.

### 1.4 CH4: collapsing the stack

CH4 was written "from the ground up, keeping low instruction and cycle counts as a primary design
goal," as **ch4 core** plus **netmods** (OFI, UCX, Portals) and **shmmods** (POSIX, XPMEM). The
central principle is *semantic flow-through*: "the netmods and shmmods know what MPI-level call
triggered a particular data movement operation, including all its parameters. Thus, each netmod
and shmmod can decide the best way to implement the operation"; lacking a hardware path, "it
simply falls back to the active-message based implementation provided by the ch4 core," so the
communication semantics "are never lost all the way through the software stack" [Raffenetti et al.
2017]. Walking `MPI_Put`: the MPI layer does argument checking, window lookup and a thread-safety
check selecting the safe/unsafe path; ch4 core performs a **locality check** (self, same-node →
shmmod, remote → netmod); the module chooses native hardware versus active-message fallback and
translates MPI-level parameters into network-level ones (MPI target offset → OS virtual address).

The instruction accounting is the paper's contribution and a template for any runtime wanting a
defensible overhead budget. Default CH4 costs **221 instructions for `MPI_Isend`** and **215 for
`MPI_Put`** versus **253 and 1,342** for CH3 (13% and 84% reductions). The 221 decompose as error
checking 74, thread-safety check 6, MPI call overhead 23, redundant runtime checks 59, and
*mandatory* MPI overhead 59. With link-time inlining and proposed standard relaxations (bulk
completion via `MPI_Isend_noreq`, match-bit elision via `MPI_Isend_nomatch`) they reach **16
instructions**, 94% below CH3 [Raffenetti et al. 2017]. The implication: error checking and
thread-safety checking are *build-time configurable* costs, not intrinsic ones, and the paper
argues for shipping two builds.

### 1.5 The MPIR / MPID / MPIDI / MPIDIG naming ladder

MPICH's layering is legible from its prefixes [MPICH DevGuide]:

| Prefix | Layer |
|---|---|
| `MPI_` / `PMPI_` | Generated binding layer: validation, trivial early returns, error behavior |
| `MPIR_` | Portable implementation, grouped by MPI standard *chapter* (`src/mpi/pt2pt`, `coll`, `rma`) |
| `MPID_` | Abstract Device Interface |
| `MPIDI_NM_` / `MPIDI_SHM_` | CH4's netmod / shmmod API (an "additional ADI-like interface") |
| `MPIDIG_` | CH4's *generic* active-message fallback |
| `MPL_` | Utilities independent of MPICH internals (atomics, threads, logging, GPU) |

CH4's active-message contract is small enough to copy as a template. Data movement:
`MPIDI_[NM|SHM]_am_send_hdr`, `am_isend`, `am_isendv` (iovec of headers), `am_send_hdr_reply` and
`am_isend_reply` (callback-safe variants), `am_recv` (clear-to-send), plus capability queries
`am_hdr_max_sz`, `am_eager_limit`, `am_eager_buf_limit`, `am_check_eager`. Callbacks:
`MPIDIG_am_target_msg_cb`, `MPIDIG_am_target_cmpl_cb`, `MPIDIG_am_origin_cb`. Eager receive is the
classic two-queue rendezvous: the target callback either copies-and-completes against the posted
list or enqueues to an unexpected queue, while `MPIDIG_mpi_irecv` checks the unexpected queue and
either copies or enqueues to the posted queue. Netmods "have direct implementations of netmod API
that skips active message" where the underlying library offers tag matching or RDMA, and fall back
otherwise [MPICH DevGuide].

The boilerplate cost of this layering was solved by *code generation*: the binding layer is
generated by `maint/gen_binding_c.py` from `mpi_standard_api.txt`, and the CH4 API framework is
script-generated to support both fully-inlined and function-table builds [MPICH DevGuide]. The
companion paper reports ~70,000 lines of hand-maintained C and Fortran binding code replaced by
~5,000 lines of Python plus configuration, which is what made prototyping the QMPI profiling
interface tractable [Zhou et al. 2021]. Collective dispatch is a four-level funnel: binding →
`MPIR_Bcast` → `MPID_Bcast` (device may override) → `MPIR_Bcast_impl` (checks control variables) →
`MPIR_Bcast_allcomm_auto`, which "automatically choose[s] a best algorithm based on selection
logic defined by a runtime json file" [MPICH DevGuide]. Algorithm selection is *data*, not code.

---

## 2. Open MPI: the Modular Component Architecture

### 2.1 Layers and the plugin taxonomy

Open MPI unions four prior codebases (LAM/MPI, LA-MPI, FT-MPI, PACX-MPI) whose teams judged their
designs too architecturally divergent to merge, and so started fresh [Gabriel et al. 2004; Graham
et al. 2006]. Three stated goals: group similar functionality into distinct abstraction layers;
use run-time loadable plugins and run-time parameters to choose among multiple implementations of
the same behavior; and "not allowing abstraction to get in the way of performance" [Squyres 2012].

Layers: **OPAL** (per-process portability — lists, string handling, IP interface discovery, shared
memory, affinity, timers), **ORTE** (runtime: launch, monitor, kill parallel jobs) and **OMPI**
(the MPI API and its semantics). Each is a standalone library, and the order OMPI → ORTE → OPAL is
*enforced by the linker*: "applications will fail to link if one layer incorrectly attempts to use
a symbol in a higher layer" [Squyres 2012] — a cheap, mechanical abstraction-violation detector.
Layers may still bypass downward for performance, and the layer diagram explicitly "do[es] not
represent the run-time call stacks."

MCA vocabulary [Open MPI docs, mca]: a **project** is OPAL/OMPI/OSHMEM; a **framework** manages
components of exactly one type for one task; a **component** implements a framework's formal
interface and is bundleable as a run-time-loadable plugin; a **module** is a *runtime instance* of
a component. "An MCA component is analogous to a C++ class" — a node with two Ethernet NICs yields
one TCP BTL component but two TCP BTL modules. Frameworks are self-contained directories
(interface header, a `base/` subdirectory with discovery and loading glue, one subdirectory per
component), and directory names must match framework and component names, giving a mechanical
mapping from `ompi/mca/btl/tcp` to the symbol `mca_btl_tcp_component`, which the MCA core finds
via `dlsym(2)` [Open MPI workshop 2006; Squyres 2012]. Components split into a **component
struct** (one per process: metadata plus `open`, `close`, `query` and *parameter registration*
pointers) and one or more **module structs** (per-resource instances). Frameworks extend the base
component struct by nesting it as the first member — `mca_btl_base_component_2_0_0_t` wraps
`mca_base_component_t` and adds `btl_init`/`btl_progress` — which Squyres calls "a simple
emulation of C++ single inheritance," justified as an exception to Open MPI's no-casting rule
because it "helps enforce abstraction barriers." Everything is versioned (major, minor, release)
for backwards compatibility [Open MPI workshop 2006].

### 2.2 Selection policy is part of the framework contract

Frameworks differ not only in interface but in *lifecycle policy* [Squyres 2012].
**Many-of-many** frameworks open every discoverable component and query each — effectively "do you
want to run?" — with each component inspecting the system (is this network present and active?)
and the framework unloading those that decline; BTL is many-of-many. **One-of-many** frameworks
select a single component (checkpointing, which must be job-consistent). **Dynamic** frameworks
load DSOs at run time, while **static** frameworks force compile-time selection either to allow
direct instead of indirect calls (the `memcpy` framework) or because the component must run
*pre-`main`* (memory-registration hooks replacing libc's allocator).

Selection among available components is by **self-reported priority**, adjustable via MCA
parameters, with a guaranteed `base` fallback for collectives [Open MPI docs, coll-tuned]. The
plugin system therefore has a *total order* and a *default*, so component absence is never a hard
failure. MCA parameters are the other half: developers register string and integer parameters with
defaults and descriptions "rather than hard-coding constants," discoverable via `ompi_info` and
settable on the command line, by environment variable or in INI files, with selection parameters
accepting comma-delimited lists and a `^` negation prefix (`--mca btl ^tcp`) [Squyres 2012; Open
MPI docs, mca]. The retrospective judgement: "One size does not fit all (users)... providing
user-level controls allows a human to figure out — and override — when the software behaves
sub-optimally" [Squyres 2012].

### 2.3 The point-to-point stack: PML / BML / BTL / MTL

Open MPI splits *matching* from *transport*, and offers two different splits [Barrett; Open MPI
docs, mca]:

- **PML (Point-to-point Messaging Layer)** implements MPI point-to-point semantics: `ob1`
  (**software matching**, multi-device striping, drives BTLs), `cm` (**offloaded matching**, single
  device, drives MTLs) and `ucx` (delegates to UCX's UCP layer).
- **BML (BTL Management Layer)** — "a thin multiplexing layer over the BTLs (inline functions)"
  managing peer resource discovery, letting multiple upper layers share BTLs, and round-robining
  across them [Barrett].
- **BTL (Byte Transfer Layer)** — "a simple tag based interface for communication similar to active
  messaging," plus RDMA `put`/`get`, registration preparation and completion callbacks:
  `btl_add_procs`, `btl_alloc`, `btl_free`, `btl_prepare_src`/`prepare_dst`, `btl_send`, `btl_put`,
  `btl_get`. Components: `tcp`, `sm` (shared memory, historically `vader`), `self`, `uct`, `ofi`,
  `openib` (legacy verbs).
- **MTL (Matching Transport Layer)** — "exclusively used as the underlying transports for the `cm`
  PML," e.g. `psm2`, `ofi` [Open MPI docs, mca].

Wire-up: MPI init picks the PML, initializes BTLs/MTLs, discovers resources and creates a BTL
module per endpoint. `add_procs` cascades PML → BML → each BTL; each BTL creates an endpoint struct
caching the peer's addressing information, and the BML caches those endpoints on the peer's
`ompi_proc_t` **grouped by BTL functionality** (e.g. `btl_eager` for low-latency small messages)
[Barrett]. Because BTL modules are per device, "the upper-layer MPI progression engine [can] both
treat all network devices equally, and perform user-level channel bonding": a large message is
fragmented and fragments assigned round-robin across three TCP BTL modules on three NICs [Squyres
2012].

### 2.4 Collectives

The `coll` framework contains `tuned`, `han`, `basic`, `base`, `libnbc` (nonblocking collectives),
`hcoll`, `ucc`, `sm`, `xhc`, `accelerator`, `inter`, `self`, `sync`, `ftagree`, `portals4`. Not all
components implement all collectives; `base` "steps in and takes over when another component fails
to provide an implementation" [Open MPI docs, coll-tuned]. The `tuned` component has three modes:
**fixed decision** (default) — a compiled decision tree with "baked in comm and message size
thresholds derived by measuring performance on existing clusters," which can be markedly wrong on
unlike hardware; **forced algorithm** via MCA parameters, which the docs call "often... an
ineffective means of tuning" because the right choice varies at runtime; and **dynamic decision** —
a user-supplied ASCII rules file mapping (collective, communicator size, message size) → algorithm
with fixed-decision fallback. Per-communicator rules are cached on the module
(`com_rules[COLLCOUNT]`), and forced-algorithm state is per-module rather than global [Open MPI
docs, coll-tuned; ompi coll_tuned.h].

---

## 3. Process launch, bootstrap, and the wire-up problem

### 3.1 PMI-1, PMI-2, Hydra

MPICH's answer is **PMI**, "a carefully defined interface... that allows different process managers
to interact with the MPI library in a standardized way" [Balaji et al. 2010], covering bootstrap
inside `MPI_Init` and MPI-2 dynamic process management (`MPI_Comm_spawn`) [Flux RFC 13]. The data
model is a **key-value space (KVS)**: processes `Put` addressing information, `Fence` (barrier plus
KVS consistency point), and `Get` peers' values. PMI-2 adds job and node attributes, name
publishing and thread-aware semantics — `PMI2_KVS_Put`, `PMI2_KVS_Fence`, `PMI2_KVS_Get`,
`PMI2_Job_Spawn`, `PMI2_Info_GetNodeAttrIntArray`, `PMI2_Nameserv_publish` [mvapich pmi_v2.c].
**Hydra** is the reference implementation of both inside MPICH, evaluated at nearly 6,000 processes
[Balaji et al. 2010]. The PMI-2 wire protocol is line-oriented request/`-response` commands with a
consistent `rc` return-code key and explicit multithreading guidance: tag messages with a thread
id, enqueue by `thrid`, signal a condition variable [MPICH PMI v2 wire protocol]. PMI-1's
shortcomings, per its own designers, concern "scalability for large numbers of cores on a node and
efficient interaction with hybrid programming models that combine MPI and threads" [Balaji et al.
2010].

### 3.2 PMIx: the wire-up scalability problem stated precisely

PMIx decomposes launch into stages and says where the time goes [Castain et al. 2017; Castain et
al. 2018]. **Stage 4 is the modex**: "Global exchange of the published information (often referred
to as the modex) is then executed via a collective operation normally executed over the management
Ethernet... This stage is typically the largest application launch time component." **Stage 5** has
libraries assemble infrastructure from exchanged data, dominated by retrieving connection info from
the local proxy and configuring the NIC library. **Stage 6 is the final barrier**, "the next
largest block of time in the start profile."

PMIx responds by "(a) eliminating some current restrictions that impact scalability, (b) augmenting
the interfaces with extended capabilities, (c) establishing a standards-like body for maintaining
the definitions, and (d) providing a reference implementation," explicitly "programming model
agnostic" and co-developed with vendors, library implementors and tool providers [Castain et al.
2017]. The published target was O(10⁶) processes on O(10⁵) nodes through `MPI_Init` in under 30
seconds [Castain et al. 2015]. The mechanisms AgentMPI needs analogues for:

- **Data-driven exchange instead of barrier-heavy all-to-all** — "data blobs versus encoded
  metakeys," plus *data scoping* to shrink the modex [Castain et al. 2015].
- **Direct modex** — fetch a peer's endpoint data on first contact rather than all-to-all; it
  "significantly outperforms full modex operations for BTL/MTLs that can support this feature," but
  "still scales as O(N)" [Castain et al. 2015].
- **Early wire-up** — daemons wire up in the background immediately after launch rather than on
  first communication, exploiting the window while the application initializes locally
  [Mellanox/SchedMD 2017].
- **Shared-memory KVS per node**, giving "zero-message" data access: PMIx holds runtime environment
  information "typically located in the shared memory of the compute nodes," with parallel
  intensive reads and infrequent updates [Castain et al. 2015; KVDb 2021].
- **Beyond bootstrap** — standardized tool connections (debugger, job submission, query),
  generalized queries (job status, layout, resource availability), **event notification**
  (subscribe, chained handlers; pre-emption, failures, timeout warnings), logging, and job control
  (pause, kill, signal, heartbeat) [Castain et al. 2017 BoF].

Architecturally PMIx is three artifacts: a **standard** (APIs and attribute strings, "nothing about
implementation"), a **reference library**, and a **reference server** — "a full-featured 'shim' to
a non-PMIx RM" [Castain et al. 2017 BoF]. The shim is how you adopt a new interface before every
host system implements it. Open MPI's trajectory closes the loop: ORTE "effectively spun off into
its own sub-project," became the PMIx standard and OpenPMIx, which grew **PRRTE** (PMIx Reference
Run-Time Environment), which "has effectively replaced ORTE" and is now a *third-party dependency*
rather than a bundled project; `mpirun` wraps PRRTE's `prterun`, and Open MPI "translate[s]
configuration directives to PMIx and PRRTE as relevant, hiding such minutia from the end-user"
[Open MPI docs, mca; Open MPI docs, pmix-and-prrte; mpirun(1)]. Slurm integrates by providing PMIx
as a plugin [Castain et al. 2015].

### 3.3 MPI Sessions: fixing the "world model" bootstrap

The World Model's limitations, per the standard: "MPI cannot be initialized from different
application components without a priori knowledge or coordination; MPI cannot be initialized more
than once; and MPI cannot be reinitialized after `MPI_FINALIZE`" [MPI Forum 2021]. Sessions remove
"the known scalability barriers by no longer requiring all possible communication peers to be
included in `MPI_COMM_WORLD`," contributing "a tighter integration of MPI applications with the
underlying runtime system; and a scalable representation of communication groups," with the stated
goal of removing the need for "heroic developer efforts" — moving a scalability fix out of
implementation heroics and into the *interface* [Holmes et al. 2016]. Mechanically, a process calls
`MPI_Session_init` for a handle, queries the runtime for named process sets, derives groups and
then communicators, allocating "MPI resources based on its communication requirements" rather than
for the whole world. `MPI_COMM_WORLD` is invalid under the Sessions Model, and **isolation** is
enforced: objects derived from different session handles (or from the World Model) may not be
intermixed in one MPI call [MPI Forum 2021]. `MPI_Session_init` is local; `MPI_Session_finalize` is
collective over an implicitly-determined group, with advice that implementations "should complete
[it] as a local procedure" when communicators are already disconnected [MPI Forum 2025].

---

## 4. Transports

**TCP/IP.** Open MPI's TCP BTL creates a listening socket and a module per "up" IPv4/IPv6
interface, and registers each `(IP address, port)` tuple "with a central repository so that other
MPI processes know how to contact it"; reachability is decided from networks/netmasks plus
heuristics [Squyres 2012].

**Shared memory: single- versus double-copy.** The baseline is two-copy (copy-in/copy-out via a
shared buffer), which "suffer[s] from high CPU utilization and cache pollution" [Buntinas et al.
2009]. Single-copy mechanisms:

| Mechanism | Nature | Notes |
|---|---|---|
| CMA (Cross-Memory Attach) | built into modern Linux | most widely available but "the lowest performance of the single-copy mechanisms" [Open MPI docs, shared-memory] |
| KNEM | standalone kernel module | maps the source buffer into kernel space; supports noncontiguous and asynchronous transfers plus I/OAT copy offload enabled dynamically from cache characteristics and message size [Buntinas et al. 2009; Goglin & Moreaud 2013] |
| XPMEM | standalone kernel module (from Cray XPMEM) | maps the buffer into the peer's *user* address space, so it needs a registration cache to amortize mapping cost [Open MPI docs, shared-memory] |
| `vmsplice` | Linux syscall | earlier MPICH2-Nemesis single-copy path [Buntinas et al. 2009] |

The crossover rule is consistent across implementations — shared-buffer two-copy for small
messages, memory-mapping single-copy for large — because "kernel-level memory mapping operation for
each page frame... adds a significant per-message overhead for small messages" [Supercomputing 2023
survey]. KNEM improved large-message throughput up to 2× and "suffers less from process placement"
on complex NUMA topologies [Moreaud et al. 2010].

**UCX** has four components, each with a public API usable standalone [UCX design; openucx README;
Shamis et al. 2015]: **UCP** (protocol — tag matching, streams, connection negotiation and
establishment, multi-rail, mixed memory types, fragmentation, collectives), **UCT** (transport —
active messages, RMA, atomics over vendor drivers such as Verbs, shared memory, CUDA, ROCm, uGNI,
with three operation classes: *short* immediate, *bcopy* buffered copy-and-send, *zcopy*
zero-copy), **UCS** (data structures, algorithms, system utilities) and **UCM** (intercepts
allocation/release events, backing the registration cache). UCP "dynamically selects optimal UCT
resources at run time based on requested features and performance criteria."

**Libfabric / OFI.** The core defines the API and "discovery services"; "the bulk of the libfabric
API is implemented by each provider," and "when an application calls a libfabric API, that function
call is routed directly into a specific provider" via function pointers on libfabric objects
[fi_arch(7)]. Four service groups: control, communication, data transfer (message queues, tag
matching, RMA, atomics) and completion (event queues, counters) [Grun et al. 2015]. The negotiation
protocol is the interesting part: the application requests **capabilities** via `fi_getinfo` caps
bits (`FI_MSG`, `FI_TAGGED`, `FI_RMA`, `FI_ATOMIC`, `FI_COLLECTIVE`) and the provider replies with
additional capabilities it can grant for free plus **mode bits** that "encode restrictions on an
application's use of the interface... due to performance reasons based on the internals of a
particular provider's implementation." "The result of the discovery process is that a provider uses
the application's request to select a software path that is best suited for both that
application's needs and the provider's restrictions" [Grun et al. 2015; fi_setup(7)]. The design
was motivated by counting memory traffic: a verbs send writes 84 bytes across `sge`/`send_wr`
structs with ~5 branches, versus 40 bytes and 0 branches for the libfabric equivalent, because
"generic entry points result in additional memory reads/writes" and "interface parameters can force
branches in the provider code" [Grun & Goodell 2015]. Tag semantics are worth copying: each send
carries one tag; each posted receive carries a tag *and a mask* applied before comparison; tags are
conventionally divided into fields (upper 16 bits a virtual group, lower 16 bits a message purpose)
[fi_arch(7)].

**GPU-aware MPI.** MVAPICH2-GPU lets `MPI_Send`/`MPI_Recv` take device pointers directly, using
CUDA Unified Virtual Addressing so "the MPI library can differentiate between device memory and
host memory without any hints from the user" [Wang et al. 2011]. For noncontiguous datatypes,
pack/unpack is *offloaded to the GPU* and pipelined against device-host copies and network RDMA,
with chunk size auto-tuned at install time from measured CUDA-copy and RDMA latencies (up to 88%
latency improvement for vector datatypes at 4 MB) [Wang et al. 2011; Wang et al. 2014]. Intranode
uses **CUDA IPC**; internode uses **GPUDirect RDMA** for direct NIC-GPU peer-to-peer, with a
threshold parameter because GPUDirect RDMA is used "only for messages with size less than or equal
to this limit" to work around P2P bandwidth bottlenecks [MVAPICH2-GDR user guide].

**Tag-matching offload.** Traditional implementations keep a Posted Receive Queue and an Unexpected
Message Queue as linked lists; recent work replaces these with hash binning, and UCX uses 1021 bins
with a hash XORing the tag's upper and lower 32 bits modulo 1021 [Marts et al. 2019]. Hardware
offload (ConnectX-5 and later, InfiniBand only, not RoCE) moves point-to-point matching onto the
NIC, enabling zero-copy delivery into the user buffer and "complete rendezvous progress" so the CPU
can compute while the adapter gathers remote data [HPC-AC 2018]. Crucially it is **hybrid and
threshold-driven**: `UCX_TM_THRESH` (default 1024 B) keeps small messages in software "because
using TM offload implies noticeable performance overhead," and `UCX_TM_FORCE_THRESH` exists because
"UCP does not offload any message if there is any non-offloaded uncompleted receive operation... for
the sake of preserving message ordering" [HPC-AC 2018]. Libfabric's CXI provider makes the fallback
explicit: hardware matching "may [transfer] the responsibility to perform message matching... to
software" under low-resource conditions, controlled by `FI_CXI_RX_MATCH_MODE` [fi_cxi(7)].
**Wildcards are the hard case** — evaluations specifically test transient versus permanent
`MPI_ANY_TAG` receives against the hardware matcher [Marts et al. 2019].

---

## 5. Tooling and observability

### 5.1 PMPI: name shifting

Since MPI-1 the standard has required "a mechanism through which all of the MPI defined functions
may be accessed with a name shift. Thus all of the MPI functions (which normally start with the
prefix `MPI_`) should also be accessible with the prefix `PMPI_`" [MPI Forum 1995]. The other four
requirements are the load-bearing, frequently forgotten ones: unreplaced MPI functions must still
link "without causing name clashes"; the implementation must **document** whether language bindings
are layered, "so that the profiler developer knows whether she must implement the profile interface
for each binding, or can economise by implementing it only for the lowest level routines"; where
bindings *are* layered, wrapper functions must be **separable** from the rest of the library; and a
no-op `MPI_Pcontrol` must exist. Additionally "the standard MPI library [must] be built in such a
way that the inclusion of MPI functions can be achieved one at a time" — in practice one function
per compilation unit — "so that the author of the profiling library need only define those MPI
functions that she wishes to intercept" [MPI Forum 2015]. Two strategies are sanctioned: **weak
symbols** (`#pragma weak MPI_Example = PMPI_Example`, the linker preferring the tool's strong
definition) or, absent weak symbols, compiling the same source twice under a `PROFILELIB` macro,
with link order `cc ... -lmyprof -lpmpi -lmpi`. MPICH does exactly this: weak symbols on Linux,
double compilation on macOS [MPICH DevGuide].

PMPI bought an entire ecosystem — mpiP, Score-P, Extrae, TAU, MUST, ScalaTrace and DAMPI all
interpose without touching application source or the MPI implementation. Its cost is equally clear:
**one tool at a time.** "Since tool libraries are made of wrapper functions that are linked to the
MPI calls... using and linking more than one tool library at a time is not possible. Therefore, all
of the functionality necessary to achieve a profiling goal must be implemented in a single tool,"
which "leads to monolithic tool designs" [Elis 2018].

Two responses exist. **PnMPI** is a *virtualization layer over PMPI*: it patches PMPI tool
binaries' dynamic symbol tables into PnMPI modules, maintains "separate link stacks for each MPI"
routine, and "redirects any MPI routine [into the] stack and independently calls each tool that
contains a wrapper for the routine" — enabling multiple concurrent PMPI tools, activation by
config-file change without relinking, toolset multiplexing within a run, and a publish/subscribe
interface for cooperative tools [Schulz & de Supinski 2005; Schulz & de Supinski 2007; PnMPI
README]. **QMPI** is the proposed successor: like PMPI it intercepts at run time, but it "allows
for simultaneous attachment of multiple tools, which removes a key limitation of PMPI" [Zhou et al.
2021]. Tools receive a *next-function* pointer to continue the chain and keep independent state via
`QMPI_Set_context`/`QMPI_Get_context`; the prototype is itself a PMPI tool acting as chain manager
[Elis et al. 2019; Elis et al. 2020]. QMPI is a proposal, not a ratified requirement.

### 5.2 MPI_T: the tool information interface

MPI-3 added MPI_T, giving tools "access to MPI internal performance and configuration information"
implementation-independently, complementing PMPI [Islam et al. 2016]. Three concepts: **control
variables (cvars)**, "used by the user to fine tune properties and configuration settings of the
MPI implementation," the canonical example being the eager limit — the implementation exports N
cvars indexed 0..N-1, and `MPI_T_cvar_get_info` returns name, verbosity, datatype, enum type,
description, **bind** (the MPI object class it attaches to) and **scope** (when writes are legal),
with MPICH using the same mechanism for environment-variable configuration [MPI Forum 2015; MPICH
DevGuide]; **performance variables (pvars)**, readable and sometimes resettable counters,
watermarks and timers internal to the library; and **categories**, hierarchically grouping cvars,
pvars and (in MPI-4/5) events.

One design detail is worth stealing verbatim: "Handles used in the MPI tool information interface
are distinct from handles used in the remaining parts of the MPI standard because they must be
usable *before* `MPI_INIT` and *after* `MPI_FINALIZE`. Further, accessing handles, in particular for
performance variables, can be time critical and having a separate handle space enables
optimizations" [MPI Forum 2015]. The introspection namespace is deliberately decoupled from the
data plane's namespace and lifecycle. The first MPI_T tools were *Varlist* (query and document the
MPI environment) and *Gyan* (profile using internal pvars), demonstrating capabilities that
"previously required in-depth knowledge of individual MPI implementations" [Islam et al. 2014;
Islam et al. 2016].

**MPI_T events** (MPI-4, refined in MPI-5) add callback-driven introspection on the same principle:
"No events are defined in the standard; all events exposed are decided by the MPI implementation"
[MPI Forum tools BoF 2018; Hermanns et al. 2018]. An **event source** is a clock domain plus
metadata (ordering guarantee, ticks/sec, max ticks); every **event type** belongs to exactly one
source and has a fixed typed element layout; a tool allocates a **registration handle**
(`MPI_T_event_handle_alloc`, optionally bound to a specific MPI object), attaches callbacks keyed by
**safety level** (`NONE`, `MPI_RESTRICTED`, `THREAD_SAFE`, `ASYNC_SIGNAL_SAFE`) plus a
**dropped-event handler**, then reads typed fields with `MPI_T_event_read`/`MPI_T_event_copy` [Open
MPI docs, MPI_T]. Dropped-event accounting and per-callback safety levels are the two most
obviously transferable features.

### 5.3 Trace data models and visualizations

**CLOG2 / SLOG-2 / Jumpshot-4** (MPICH's MPE). CLOG2 is "a low overhead logging format, a simple
collection of single timestamp events"; SLOG-2 is hierarchical and "optimized for the visualization
of very large (multi-Gigabyte) logfiles" [MPE README]. The SLOG-2 data model is three **drawable
topologies** on a *(timeline ID, timestamp)* canvas: a **state** is two points with the *same*
timeline ID (an interval, drawn as a rectangle), an **arrow** is two points with *different*
timeline IDs (a message), and an **event** is a single point [Jumpshot-4 docs]. Composite drawables
aggregate primitives; all display attributes (color, legend name, topology) live in a shared
**Category** object referenced by the drawable, so properties are centralized rather than repeated
per record; nested states encode the call stack. The scalability trick is *bounding boxes*: SLOG-2
stores a tree of drawables in postorder with **preview drawables** at interior nodes, giving
level-of-detail so the viewer can scroll seamlessly at any zoom without loading the whole file
[Chan et al., SLOG-2]. Jumpshot-4 adds a histogram module over user-selected durations for spotting
load imbalance, plus converters for CLOG, CLOG-2, RLOG and UTE [Jumpshot-4 guide].

**OTF2 / Score-P / Cube4.** Score-P is a *shared measurement infrastructure* for Scalasca, Vampir,
TAU and Periscope, created because "each analysis tool had its own instrumentation system, so the
user was commonly forced to repeat the instrumentation procedure"; one instrumented binary switches
between tracing and profiling mode without recompilation [Score-P docs; Knüpfer et al. 2012].
**OTF2** is the joint successor to OTF and EPILOG, "a highly scalable, memory efficient event trace
data format plus support library," now the standard trace format for Scalasca, Vampir and TAU.
Records split into **definition records** (timer resolution, region and process names, metric
definitions) and **event records** (`Enter`, `Leave`, MPI send/receive, `MetricInstance`,
`ThreadFork`, …), each carrying a `location` and a `timestamp` [OTF2 event-record reference].
Compression happens *at runtime*: a timestamp is stored once for runs of identically-timestamped
events, and integers are stored with high-order zero bytes stripped and the width in the leading
byte. The buffer component supports **external memory management** (Score-P backs it with a pool
dynamically distributed across threads, balancing better than static per-thread assignment);
reading holds only three chunks in memory (previous, current, next), so a trace produced on a
supercomputer can be consumed on a laptop; and on-the-fly token translation via mapping tables
(`OTF2_MAPPING_REGION`) "avoid[s] copying during unification of parallel event streams" [Eschweiler
et al. 2012]. **Cube4** is the companion profile format: an XML anchor file plus binary files in one
archive, supporting dynamic loading and incremental writing, aggregating metrics over call-paths
[VI-HPS Score-P docs].

**Vampir** presents two view families [Knüpfer et al. 2008; Vampir user guide; VI-HPS Vampir
material]: **timelines** (location on Y, time on X) — Master Timeline (top-of-stack region per
location, with message arrows), Process Timeline (per-location call stack as a stacked bar chart),
Counter Data Timeline, Performance Radar (one metric across all locations), Summary Timeline
(stacked histogram of how many processes are in MPI versus application state per time bin) — and
**summary charts** that dynamically aggregate over the selected interval (Function Summary, Process
Summary, communication matrix, I/O load). Zooming is the central interaction; the original 1995
paper already emphasized "the powerful zooming feature that allows to identify problems at any
level of detail," plus animation and filtering [Nagel et al. 1996].

**Paraver / Extrae / Dimemas** (BSC). Paraver's distinguishing choice is that it "specifies a trace
format but no actual semantics for the encoded values"; the visualization module "blindly represents
the values and events passed to it, without assigning to them any pre-conceived semantics,"
offering filter and semantic modules as "building blocks to transform the trace in the visualization
process" [BSC Paraver; Pillet et al. 1995]. A trace is three text files: `.prv` (timestamped
records), `.pcf` (labels for numeric values), `.row` (resource naming) [BSC trace generation].
Record kinds are **states** (thread status intervals), **events** (punctual) and
**relations/communications** (linking two objects) — the same state/event/arrow triad as SLOG-2,
arrived at independently. A filtered trace *is itself* a Paraver trace, so the same analysis
configurations apply — a closure property that makes trace reduction composable [VI-HPS BSC tools].
Extrae interposes via `LD_PRELOAD` symbol substitution, static PMPI linking or Dyninst binary
rewriting, and can attach PAPI counters plus source references (function, file, line) at
programming-model calls and sampling points [BSC Extrae]. **Dimemas** is a message-passing simulator
whose trace is "a sequence of resource demands" (computation-burst durations, communication
type/partner/bytes) rather than wall-clock events, convertible by `prv2dim`, and whose output is
again a Paraver trace so simulated and measured runs compare side by side [VI-HPS BSC tools].

**HPCToolkit** takes the opposite measurement stance. Instrumentation "can distort the application
performance by interfering with inlining and template optimization," and the usual mitigation —
skipping small, frequently executed procedures — is self-defeating because "these may be just the
thread synchronization library routines that are critical" [Adhianto et al. 2010]. It therefore uses
**asynchronous statistical sampling** with stack unwinding, plus binary analysis of optimized and
stripped binaries to avoid blind spots in libraries shipped without source. Its data model is a
**calling context tree** distinguishing contexts "precisely by individual call sites," integrated
with recovered static structure (loops, inlined functions); `hpcviewer` presents calling-context
(top-down), caller (bottom-up, apportioning a procedure's cost across contexts) and flat
(context-insensitive, attributing to loops and lines) views, with automatic hot-path expansion and
derived metrics [Adhianto et al. 2010; HPCToolkit overview]. **TAU**, by contrast, is
instrumentation-centric — PDT source instrumentation, DyninstAPI runtime instrumentation, JVM
instrumentation or manual API — maintaining per-thread/context/node data with profiling groups for
selective control [Shende & Malony 2006; TAU user guide].

**mpiP** occupies a deliberate middle point: "it only collects statistical information about MPI
functions [so] mpiP generates considerably less overhead and much less data than tracing tools. All
the information captured by mpiP is task-local. It only uses communication during report
generation, typically at the end of the experiment" [mpiP user guide; Vetter & McCracken 2001]. Its
unit of attribution is the **callsite**, identified by a stack traceback (libunwind or glibc
`backtrace`) of configurable depth (`-k n`, default 1); reports are threshold-filtered (`-t x`) and
can include message-size and per-communicator histograms. As a link-time PMPI library it needs no
recompilation, only `-g` for source attribution.

**Darshan** is the I/O analogue: deployed transparently via `LD_PRELOAD` or link time, designed "to
capture an accurate picture of application I/O behavior... with minimum overhead," light enough for
"full time deployment for workload characterization of large systems" [Darshan project; Carns et al.
2011]. It keeps a hashed per-file record of counters and timestamps (open, close, first I/O, last
I/O) and compresses at job end; **DXT** (Darshan eXtended Tracing) adds run-time-selectable higher
fidelity "without modifying or recompiling applications" [DXT 2019]. Note the self-hosting
subtlety: Darshan instruments MPI-IO *and* uses MPI-IO to write its own log.

**Caliper** inverts the usual tool/application relationship. It is "a general abstraction layer to
provide performance data collection as a service to applications, runtime systems, libraries, and
tools," where components connect "in independent data producer, data consumer, and measurement
control roles, which allows them to share performance data across software stack boundaries"
[Boehme et al. 2016]. Annotations (`CALI_MARK_BEGIN`/`END`, or key:value attributes) update a
**blackboard** and by default do nothing else; **snapshots** of the blackboard plus measurement data
are triggered at configured events; functionality comes from enable/disable-able **services**; and a
`ConfigManager` API with config strings (`CALI_CONFIG=runtime-report`) lets the *application*
configure measurement, so profiling can be always-on [Caliper docs]. Adiak collects build/run
metadata for ensemble analysis in Thicket [SC23 tools poster].

### 5.4 Correctness tools

| Tool | Method | Coverage / limits |
|---|---|---|
| **Umpire** | centralized runtime deadlock detection | detects all actual MPI-1.2 deadlocks and some potential ones; scalability limited by centralized trace communication [Vetter & de Supinski 2000; Hilbrich et al. 2010] |
| **Marmot** | timeout-based deadlock detection plus wide local/global checks | detects recv-recv deadlock; **misses** send-send; detects schedule-dependent deadlock only if it manifests; timeouts admit false positives, no graphical explanation [Hilbrich et al. 2013] |
| **ISP** | centralized scheduler re-executes the program over *all* send/recv interleavings | best coverage — always finds schedule-dependent deadlocks — but interleavings are exponential; "not reported to scale to more than a hundred processes" [Hilbrich et al. 2010; Hilbrich et al. 2013] |
| **DAMPI** | distributed exploration: rewrites MPI calls per an enumeration of interleavings, no central scheduler, per-run timeout detection | removes ISP's central bottleneck; cannot detect send-send deadlock, no graphical view, timeout false positives [Vo et al. 2010; Hilbrich et al. 2013] |
| **MUST** | single-execution runtime checking on **GTI**, a generic event-based tool infrastructure; graph-based blocking model for distributed deadlock detection | no false positives, graphical deadlock explanation, scales to 4,096 processes with ~34% average overhead at 2,048 [Hilbrich et al. 2010; Hilbrich et al. 2012; Hilbrich et al. 2013 SC] |
| **Intel Message Checker** | automated, scalable MPI debugging | [DeSouza et al. 2005] |
| **MPE collective/datatype checking** | argument-consistency checks on collectives (datatype, root), printing a callstack backtrace on violation | ships with MPICH's MPE [MPE README] |

MUST's authors make the architectural point explicitly: separate "tool internal infrastructure and
the actual correctness checks," which predecessors hard-coded, because that separation "is important
in order to enhance existing checks and to add further correctness checks that are used for new
features or new versions of the MPI standard" [Hilbrich et al. 2010]. Checks needing global
knowledge (deadlock, type matching) require a *communication system for tool records*, and making
that substrate reusable (GTI) rather than per-tool plumbing is what let MUST scale [Hilbrich et al.
2012].

### 5.5 Benchmarks and reporting methodology

**OSU Micro-Benchmarks (OMB).** `osu_latency` is a ping-pong using blocking `MPI_Send`/`MPI_Recv`
reporting **average one-way latency**; `osu_bw` sends a window of back-to-back `MPI_Isend`s, waits
for a single reply, and computes bandwidth from elapsed time and bytes, "to determine the maximum
sustained data rate that can be achieved at the network level" [OMB README]. Collective benchmarks
(`osu_allreduce`, `osu_bcast`, `osu_alltoall`, …) report average latency per message size. The
methodological knobs are the interesting part [OMB README; osu_util_options.h]: `-x` sets **warmup
iterations skipped before timing** (default **200**); `-i` sets timed iterations (default **10000**
small, **1000** large, with a colon-separated cutoff); `-m min:max` sets the message-size range
(default up to 1 MB); `-f` requests full format with **MIN/MAX latency and iteration count**, not
just the mean; `-M` caps per-process memory (default 512 MB). The standard reported artifact is
therefore a latency-versus-message-size curve, mean by default, min/max available, with a fixed
warmup skip.

**SKaMPI** aims at "performance portability" and maintains a public cross-platform performance
database [Reussner et al. 2002]. Its distinguishing mechanisms: a domain-specific language for
describing benchmark tests; **window-based process synchronization** (synchronizing distributed
clocks so operations start simultaneously) in addition to `MPI_Barrier`; and an **adaptive iterative
measurement** that repeats a test "until the current relative standard error is smaller than a
predefined maximum," reporting arithmetic mean and standard error [Reussner et al. 1998; Hunold &
Carpen-Amarie 2015]. For contrast, MPICH's `mpptest` measures `nrep` consecutive calls, takes the
mean, repeats k times and reports the **minimum of the means** — a design driven by coarse hardware
clocks; `mpicroscope` and OMB do fixed repetitions reporting min/max/mean; MPIBlib stops when the
sample mean falls within a predefined range of a 95% confidence interval [Hunold & Carpen-Amarie
2015]. **NetPIPE** is a protocol-independent ping-pong evaluator [Snell et al. 1996] `[UNVERIFIED:
exact venue]`; **IMB** (Intel MPI Benchmarks) provides standard point-to-point and collective
metrics `[UNVERIFIED: methodology details]`; **mpiBench** (LLNL) targets collectives
`[UNVERIFIED]`. The reproducibility literature's headline finding is that these measurement-scheme
differences materially change reported numbers, so experimental design must be reported, not
assumed [Hunold & Carpen-Amarie 2015].

**NAS Parallel Benchmarks.** Five kernels and three pseudo-applications derived from CFD, given as a
"pencil-and-paper" specification (NPB 1) with MPI and OpenMP reference implementations (NPB 2, 3)
[Bailey et al. 1991; NASA NPB]. Problem sizes are **predefined classes**: S (sanity), W
(workstation), A, B, C, D, E, F, with class F added in NPB 3.4 alongside dynamic memory allocation.
Scaling is steep and documented per benchmark: CG rows go 1,400 (S) → 14,000 (A) → 150,000 (C) →
9,000,000 (E) → 54,000,000 (F); EP random-number pairs go 2²⁴ (S) → 2²⁸ (A) → 2³² (C) → 2⁴⁴ (F);
NPB-MZ Class F needs ≈5.0 TB [NPB problem sizes]. Class A was described in 1995 as runnable "on a
moderately powerful workstation," class B on high-end workstations or small parallel systems, with C
added "to retain the focus on high-end supercomputing"; some benchmarks require a power-of-two
process count (FT, MG, IS), others a square (SP, BT) [Bailey et al. 1995]. The pattern to copy: a
named, versioned ladder of problem sizes with published validation tolerances, so results are
comparable across papers and machines.

---

## 6. Determinism, reproducibility, and debugging

### 6.1 Why MPI programs are nondeterministic

Two structural sources. **Wildcard receives**: `MPI_ANY_SOURCE` and `MPI_ANY_TAG` make the
receive-to-message binding depend on arrival order, hence on the network — which is why ISP and
DAMPI must explore all interleavings of send/recv pairs to verify deadlock freedom, why some
deadlocks are *schedule-dependent*, and why wildcards are the hard case for hardware tag matching
[Hilbrich et al. 2013; Marts et al. 2019]. **Reduction ordering**: floating-point addition is not
associative, so a reduction's result depends on the tree shape, which MPI does not fix. Buffering
semantics add a third, subtler source: whether `MPI_Send` blocks depends on whether the
implementation buffers, so a send-send pair may or may not deadlock depending on eager limits —
which is why MUST's comparison must assume "the MPI implementation buffers the Send calls" for the
example to run at all [Hilbrich et al. 2013].

### 6.2 Reproducible reductions

ReproBLAS enumerates the cases usefully [ReproBLAS]: (1) with fixed data order, process count,
partitioning, instruction selection, alignment and a deterministically scheduled fixed reduction
tree, results are already reproducible — do nothing; (2) with fixed process count and deterministic
partitioning, local sums can be sequential and deterministic, so **the only remaining source is the
reduction tree shape** and reproducible summation is needed *only in the reduction*; (3) with no
assumption about process count or tree shape but data in fixed-size, fixed-order blocks
(over-decomposition), use per-block sequential sums plus reproducible aggregation. The mechanism is
**binned** floating-point types: numbers are split into slices along predefined exponent boundaries,
and a `k`-fold binned type carries `k` accumulators for the largest consecutive nonzero bins seen so
far, making binned addition order-independent [ReproBLAS; Ahrens et al. 2020]. `binnedMPI.h`
supplies MPI datatypes (`binnedMPI_DOUBLE_BINNED(fold)`) and operators (`binnedMPI_DBDBADD(fold)`)
so a standard `MPI_Allreduce` becomes reproducible "regardless of the reduction tree shape used by
MPI," with design goals of a *single* read-only pass over the data and a *single* parallel
reduction. The transferable insight: identify precisely which layer introduces the nondeterminism
and make only that layer order-independent.

### 6.3 Record/replay

**ScalaTrace** interposes via PMPI and applies **bi-level compression**: on-the-fly node-local
compression of trace records, then a global inter-node compression performed "upon application
completion within the PMPI wrapper for `MPI_Finalize`," bottom-up over a binary tree "to avoid the
creation of local trace files, which would result in linearly increasing disk space requirements"
[Noeth et al. 2009]. Repetitive MPI events in loops with identical parameters compress to constant
size, giving "orders of magnitude smaller, if not near-constant size" traces regardless of node
count while preserving structure. The replay engine "does not actually decompress this trace.
Instead, it interprets the compressed trace on-the-fly to issue communication calls," implementing
the inverse of the compression algorithms, with events annotated with time-preserving information
for deterministic replay [Noeth et al. 2009; ScalaTrace tech report]. ScalaTrace II adds elastic
encoding, support for nondeterministic events and probabilistic replay `[UNVERIFIED: exact feature
attribution]`.

### 6.4 The MPIR process-acquisition interface

MPIR is the *de facto* debugger-attach protocol, standardized retroactively by the MPI Forum after
two decades of implementation. In early 1995 TotalView's Jim Cownie and Argonne's Bill Gropp and
Rusty Lusk developed two interfaces for MPICH — process acquisition (MPIR) and message-queue access
— which became de facto standards implemented by Compaq, LAM/MPI, MPI Software Technologies, Open
MPI, Quadrics, SCALI, SGI and Sun/Oracle among others [MPI Forum 2018]. The protocol is a rendezvous
via well-known symbols in the **starter process** (`mpirun`) [MPI Forum 2018; ANL MPI debug]:
`MPIR_proctable`, a pointer to an array of `MPIR_PROCDESC` structs (`host_name`,
`executable_name`, `pid`); `MPIR_proctable_size`; `MPIR_debug_state`, 0 before the table is
initialized and `MPIR_DEBUG_SPAWNED` after; and `MPIR_Breakpoint()`, a subroutine the starter calls
to notify the tool that an MPIR event occurred. "The tool must set a breakpoint at the
`MPIR_Breakpoint` function, and when a thread running the starter process hits the breakpoint, the
tool must read the value of the `MPIR_debug_state` variable to process an MPIR event." The tool must
be able to read and write the starter's address space and plant breakpoints. The mechanism is crude
— a well-known symbol, a breakpoint and a shared struct layout — and precisely because it is crude
it worked across a decade of independent implementations and two commercial debuggers (TotalView,
DDT).

MPIR is now superseded by the **PMIx tools API**, "an alternative and more extensible tool
interface" [mpir-to-pmix guide]. Backwards compatibility is preserved by the **MPIR shim module**: a
standalone process launched *between* the debugger and `mpirun` that implements `MPIR_Breakpoint`,
extracts the process table and idles until the application terminates, letting legacy tools attach
unchanged [Open MPI docs, mpir-tools]. The shim pattern appears twice in this dossier (PMIx
reference server, MPIR shim) and deserves naming as a first-class migration strategy.

---

## 7. Architectural lessons transferable to a non-HPC message-passing runtime

1. **Define a narrow internal "device" interface and implement the full user-facing API once on top
   of it.** MPICH's ADI lets Intel, Cray and MVAPICH replace the bottom of the stack while
   inheriting all MPI semantics. *(MPICH ADI-3 [MPICH DevGuide])*
2. **Offer a ladder of internal interfaces at different abstraction levels, not one.** ADI-3, CH3
   (~12 functions) and RDMA Channel (5) let a porter choose a performance/effort point instead of
   forcing everyone to the same cost. *(MPICH2 [Liu et al. 2004])*
3. **Separate the matching layer from the transport layer, and allow *two* splits.** `ob1` matches
   in software over byte-moving BTLs while `cm` delegates matching to hardware via MTLs; hardware
   that can match should not be forced through a software matcher. *(Open MPI PML/BTL/MTL
   [Barrett])*
4. **Insert a thin multiplexing layer between protocol and transports so multi-rail is free.** The
   BML round-robins fragments across per-device BTL modules, giving channel bonding with no protocol
   changes. *(Open MPI BML [Barrett; Squyres 2012])*
5. **Make the plugin *instance* (module) distinct from the plugin *implementation* (component), and
   enforce layer boundaries mechanically.** One TCP component yields one module per NIC, and
   building each layer as its own library turns an upward symbol reference into a link error. *(Open
   MPI MCA and OPAL/ORTE/OMPI [Open MPI docs, mca; Squyres 2012])*
6. **Make component selection a priority-ordered query with a guaranteed fallback.** Components
   answer "do you want to run?" by inspecting the environment, priorities are user-overridable, and
   a `base` component always exists, so a missing plugin degrades instead of failing. *(Open MPI
   coll framework [Open MPI docs, coll-tuned])*
7. **Register every tunable constant as a named, described, discoverable run-time parameter instead
   of hard-coding it.** Buffer sizes, timeouts and protocol thresholds become operator-tunable and
   self-documenting via `ompi_info`. *(Open MPI MCA parameters [Squyres 2012])*
8. **Let semantic information flow unmodified to the lowest layer.** CH4's netmods know which MPI
   call triggered a transfer and all its arguments, so any layer can optimize with full context
   rather than reverse-engineering intent from bytes. *(MPICH CH4 [Raffenetti et al. 2017])*
9. **Give every module a generic active-message fallback so partial implementations are legal.** A
   CH4 netmod can implement `am_isend` plus a callback mechanism and be correct, with native fast
   paths added incrementally. *(MPICH MPIDIG [MPICH DevGuide])*
10. **Handle locality by choosing a queue, not by branching in the send path, and give each
    participant one multi-producer/single-consumer lock-free receive queue.** Per-pair queues are
    O(N²) and per-queue polling does not scale, whereas MCS-style swap+CAS enqueue costs six
    instructions. *(Nemesis [Buntinas et al. 2006])*
11. **Add a bypass fast path for the smallest, most common message, and let it be non-general.**
    Fastboxes are explicitly limited to small process counts — a fast path need not cover every
    case. *(Nemesis [Buntinas et al. 2007])*
12. **Budget overhead in instructions and attribute every one to a requirement.** CH4's 221→16
    analysis separates *mandatory* semantic cost from error checking, thread-safety checks and
    redundant runtime checks, the latter three being build-time configurable. *(MPICH CH4
    [Raffenetti et al. 2017])*
13. **Negotiate capabilities and restrictions at connection setup, then commit to a specialized code
    path.** OFI's caps bits plus mode bits let the provider pick its best internal path, avoiding
    per-operation branching. *(Libfabric [Grun et al. 2015])*
14. **Standardize an interception ABI (name shifting) — and design it for tool *composition* from
    day one.** PMPI enabled a whole tool ecosystem, but its one-tool-at-a-time limit forced
    monolithic tools, so build the QMPI/PnMPI next-function chain and per-tool context up front.
    *(PMPI [MPI Forum 1995]; QMPI [Elis et al. 2019]; PnMPI [Schulz & de Supinski 2005])*
15. **Separate the introspection namespace and lifecycle from the data plane's, and expose
    *implementation-declared* control variables, performance variables and events with
    self-describing metadata rather than a fixed metric list.** MPI_T handles work before init and
    after finalize in their own handle space so pvar access can be optimized, and because the
    standard defines no events the runtime can evolve internals without breaking tools. *(MPI_T [MPI
    Forum 2015; Islam et al. 2016; MPI Forum tools BoF 2018])*
16. **Model traces as states, arrows and events over a (location, time) canvas, with display
    attributes factored into shared category objects and level-of-detail previews stored so a viewer
    can open a trace larger than memory — and make trace reduction closed under the trace format.**
    SLOG-2, OTF2 and Paraver converged on the same triad independently, and a filtered Paraver trace
    is a Paraver trace, so filtering composes instead of forking the toolchain. *(SLOG-2 [Jumpshot-4
    docs]; OTF2 [Eschweiler et al. 2012]; Paraver [BSC Paraver; VI-HPS BSC tools])*
17. **Build one shared measurement substrate for many analysis frontends, make
    profiling-versus-tracing a run-time switch on the same binary, and separate tool infrastructure
    from tool policy.** Score-P exists because every tool previously shipped its own
    instrumentation, and MUST scaled past Marmot and Umpire because GTI generalized the
    event-transport plumbing its predecessors hard-coded per check. *(Score-P [Score-P docs];
    MUST/GTI [Hilbrich et al. 2010; Hilbrich et al. 2012])*
18. **Offer an always-on statistical/sampling tier alongside full tracing, and account for dropped
    events explicitly.** mpiP keeps data task-local and communicates only at report time, HPCToolkit
    samples to bound distortion, and MPI_T events require a dropped-event handler. *(mpiP [mpiP user
    guide]; HPCToolkit [Adhianto et al. 2010]; MPI_T events [Open MPI docs, MPI_T])*
19. **Split bootstrap into publish → scoped exchange → on-demand fetch, start wire-up in the
    background before the first message, and ship a standard plus a reference implementation plus a
    shim to non-conforming hosts.** PMIx's direct modex and early wire-up replace the global
    all-to-all and barrier that dominate launch cost at scale, and its reference server and the MPIR
    shim both let a new interface be adopted before the ecosystem implements it. *(PMIx [Castain et
    al. 2017; Mellanox/SchedMD 2017]; MPIR shim [Open MPI docs, mpir-tools])*
20. **Replace the implicit global "world" with explicit, isolated, re-creatable sessions derived from
    runtime-queried process sets.** MPI Sessions fixes multi-component initialization,
    re-initialization and the O(world) footprint at the *interface* level rather than by
    implementation heroics — the direct model for AgentMPI's scoping. *(MPI Sessions [Holmes et al.
    2016; MPI Forum 2021])*
21. **Localize nondeterminism, then make only that layer order-independent.** ReproBLAS shows that
    when partitioning is fixed, only the reduction tree needs reproducible (binned) arithmetic.
    *(ReproBLAS [ReproBLAS; Ahrens et al. 2020])*
22. **Make algorithm selection data, not code, and generate boilerplate layers from a
    machine-readable interface specification.** MPICH picks collective algorithms from a runtime JSON
    file and Open MPI's `tuned` from a rules file mapping (operation, group size, message size) →
    algorithm, while replacing ~70,000 lines of binding code with ~5,000 lines of Python is what made
    a second profiling interface feasible. *(MPICH [MPICH DevGuide; Zhou et al. 2021]; Open MPI
    [Open MPI docs, coll-tuned])*
23. **Ship a named, versioned ladder of benchmark problem sizes and a documented measurement protocol
    (warmup skip, iteration counts, min/median/max, curves versus message size).** NPB classes and
    OMB's `-x`/`-i`/`-f` conventions are why cross-paper comparison is possible, and SKaMPI shows the
    measurement scheme itself changes the numbers. *(NPB [NASA NPB]; OMB [OMB README]; SKaMPI
    [Reussner et al. 2002; Hunold & Carpen-Amarie 2015])*

---

## References

- **[Adhianto et al. 2010]** L. Adhianto, S. Banerjee, M. Fagan, M. Krentel, G. Marin,
  J. Mellor-Crummey, N. R. Tallent. "HPCTOOLKIT: Tools for performance analysis of optimized
  parallel programs." *Concurrency and Computation: Practice and Experience*, 22(6):685–701, 2010.
  DOI: 10.1002/cpe.1553.
- **[Ahrens et al. 2020]** P. Ahrens, J. Demmel, H. D. Nguyen. "Algorithms for Efficient
  Reproducible Floating Point Summation." *ACM Transactions on Mathematical Software*,
  46(3):22:1–22:49, 2020. DOI: 10.1145/3389360.
- **[ANL MPI debug]** Argonne National Laboratory, Mathematics and Computer Science Division.
  "MPI Debugging Interface." https://www.mcs.anl.gov/research/projects/mpi/mpi-debug/ (accessed
  2026-08-30).
- **[Bailey et al. 1991]** D. H. Bailey, E. Barszcz, J. T. Barton, D. S. Browning,
  R. L. Carter, L. Dagum, R. A. Fatoohi, P. O. Frederickson, T. A. Lasinski, R. S. Schreiber,
  H. D. Simon, V. Venkatakrishnan, S. K. Weeratunga. "The NAS Parallel Benchmarks."
  *International Journal of Supercomputer Applications*, 5(3):63–73, 1991.
  DOI: 10.1177/109434209100500306.
- **[Bailey et al. 1995]** D. Bailey, T. Harris, W. Saphir, R. van der Wijngaart, A. Woo,
  M. Yarrow. "The NAS Parallel Benchmarks 2.0." Technical Report NAS-95-020, NASA Ames Research
  Center, 1995.
- **[Balaji et al. 2010]** P. Balaji, D. Buntinas, D. Goodell, W. Gropp, J. Krishna, E. Lusk,
  R. Thakur. "PMI: A Scalable Parallel Process-Management Interface for Extreme-Scale Systems."
  In *Recent Advances in the Message Passing Interface (EuroMPI 2010)*, LNCS 6305, pp. 31–41.
  Springer, 2010. DOI: 10.1007/978-3-642-15646-5_4.
- **[Barrett]** B. Barrett. "Open MPI Data Transfer." Open MPI developer presentation, Sandia
  National Laboratories. https://www.open-mpi.org/video/internals/Sandia_BrianBarrett-1up.pdf
  (accessed 2026-08-30). See also OSTI 1649706.
- **[Boehme et al. 2016]** D. Boehme, T. Gamblin, D. Beckingsale, P.-T. Bremer, A. Gimenez,
  M. LeGendre, O. Pearce, M. Schulz. "Caliper: Performance Introspection for HPC Software
  Stacks." In *Proc. SC'16*, pp. 550–560. IEEE, 2016. DOI: 10.1109/SC.2016.46.
  LLNL-CONF-699263.
- **[BSC Extrae]** Barcelona Supercomputing Center. "Extrae." https://tools.bsc.es/extrae
  (accessed 2026-08-30).
- **[BSC Paraver]** Barcelona Supercomputing Center. "Paraver: a flexible performance analysis
  tool." https://tools.bsc.es/paraver (accessed 2026-08-30).
- **[BSC trace generation]** Barcelona Supercomputing Center. "Paraver — Trace generation."
  https://tools.bsc.es/paraver/trace_generation (accessed 2026-08-30).
- **[Buntinas et al. 2006]** D. Buntinas, G. Mercier, W. Gropp. "Design and Evaluation of
  Nemesis, a Scalable, Low-Latency, Message-Passing Communication Subsystem." In *Proc. 6th IEEE
  International Symposium on Cluster Computing and the Grid (CCGrid 2006)*, pp. 521–530. IEEE
  Computer Society, 2006. DOI: 10.1109/CCGRID.2006.31.
- **[Buntinas et al. 2007]** D. Buntinas, G. Mercier, W. Gropp. "Implementation and evaluation of
  shared-memory communication and synchronization operations in MPICH2 using the Nemesis
  communication subsystem." *Parallel Computing*, 33(9):634–644, 2007.
  DOI: 10.1016/j.parco.2007.06.003. (See also ANL Preprint P1346.)
- **[Buntinas et al. 2009]** D. Buntinas, B. Goglin, D. Goodell, G. Mercier, S. Moreaud.
  "Cache-Efficient, Intranode, Large-Message MPI Communication with MPICH2-Nemesis." In
  *Proc. 38th International Conference on Parallel Processing (ICPP 2009)*, pp. 462–469. IEEE,
  2009. DOI: 10.1109/ICPP.2009.22.
- **[Caliper docs]** Lawrence Livermore National Laboratory. "Caliper: A Performance Analysis
  Toolbox in a Library" (documentation, incl. *Architecture and workflow* and *Caliper Basics*).
  https://llnl.github.io/Caliper/ (accessed 2026-08-30).
- **[Carns et al. 2011]** P. Carns, K. Harms, W. Allcock, C. Bacon, S. Lang, R. Latham,
  R. Ross. "Understanding and improving computational science storage access through continuous
  characterization." *ACM Transactions on Storage*, 7(3):8:1–8:26, 2011.
  DOI: 10.1145/2027066.2027068.
- **[Castain et al. 2015]** R. H. Castain, A. Dasari, J. Ladd, A. Polyakov, E. Shipunova,
  M. Kogteva. "Process Management Interface – Exascale (PMIx)." SC'15 Birds-of-a-Feather
  presentation. https://www.open-mpi.org/papers/sc-2015-pmix/PMIx-BoF.pdf (accessed 2026-08-30).
- **[Castain et al. 2017]** R. H. Castain, D. Solt, J. Hursey, A. Bouteiller. "PMIx: Process
  Management for Exascale Environments." In *Proc. 24th European MPI Users' Group Meeting
  (EuroMPI/USA '17)*, article 14, pp. 1–10. ACM, 2017. DOI: 10.1145/3127024.3127027.
- **[Castain et al. 2017 BoF]** R. H. Castain, D. Solt, J. Hursey, A. Bouteiller. "PMIx: Process
  Management for Exascale Environments." EuroMPI/USA 2017 presentation.
  https://openpmix.org/uploads/2018/11/EuroMPI-2017-Presentation.pdf (accessed 2026-08-30).
- **[Castain et al. 2018]** R. H. Castain, J. Hursey, A. Bouteiller, D. Solt. "PMIx: Process
  management for exascale environments." *Parallel Computing*, 79:9–29, 2018.
  DOI: 10.1016/j.parco.2018.08.002.
- **[Chan et al., SLOG-2]** A. Chan, W. Gropp, E. Lusk. "Scalable Log Files for Parallel Program
  Trace Data" (draft). Mathematics and Computer Science Division, Argonne National Laboratory.
  https://sunsite.icm.edu.pl/pub/programming/mpich/slog2/slog2-paper.pdf (accessed 2026-08-30).
  See also A. Chan, W. Gropp, E. Lusk, "An Efficient Format for Nearly Constant-Time Access to
  Arbitrary Time Intervals in Large Trace Files," *Scientific Programming*, 16(2–3):155–165, 2008.
  DOI: 10.1155/2008/195461.
- **[Darshan project]** Argonne National Laboratory. "Darshan — HPC I/O Characterization Tool."
  https://wordpress.cels.anl.gov/darshan/ (accessed 2026-08-30).
- **[DeSouza et al. 2005]** J. DeSouza, B. Kuhn, B. R. de Supinski, V. Samofalov, S. Zheltov,
  S. Bratanov. "Automated, scalable debugging of MPI programs with Intel Message Checker." In
  *Proc. 2nd International Workshop on Software Engineering for High Performance Computing System
  Applications (SE-HPCS '05)*, pp. 78–82. ACM, 2005. DOI: 10.1145/1145319.1145342.
- **[DXT 2019]** C. Xu, S. Snyder, O. Kulkarni, V. Venkatesan, P. Carns, S. Byna, R. Sisneros,
  K. Chadalavada. "DXT: Darshan eXtended Tracing." Conference paper, 2019. OSTI 1490709.
  `[UNVERIFIED: exact author list and venue]`
- **[Elis 2018]** B. Elis. "Design, Implementation and Testing of a new Profiling Interface for
  MPI." Master's thesis, Technical University of Munich, 2018.
  https://mediatum.ub.tum.de/doc/1455895/document.pdf (accessed 2026-08-30).
- **[Elis et al. 2019]** B. Elis, D. Yang, M. Schulz. "QMPI: a next generation MPI profiling
  interface for modern HPC platforms." In *Proc. 26th European MPI Users' Group Meeting
  (EuroMPI '19)*, article 4, pp. 1–10. ACM, 2019. DOI: 10.1145/3343211.3343215.
- **[Elis et al. 2020]** B. Elis, D. Yang, O. Pearce, K. Mohror, M. Schulz. "QMPI: A next
  generation MPI profiling interface for modern HPC platforms." *Parallel Computing*, 96:102635,
  2020. DOI: 10.1016/j.parco.2020.102635.
- **[Eschweiler et al. 2012]** D. Eschweiler, M. Wagner, M. Geimer, A. Knüpfer, W. E. Nagel,
  F. Wolf. "Open Trace Format 2: The Next Generation of Scalable Trace Formats and Support
  Libraries." In *Applications, Tools and Techniques on the Road to Exascale Computing
  (ParCo 2011)*, Advances in Parallel Computing 22, pp. 481–490. IOS Press, 2012.
  DOI: 10.3233/978-1-61499-041-3-481.
- **[fi_arch(7)]** OpenFabrics Interfaces Working Group. "fi_arch(7) — Libfabric architecture."
  https://ofiwg.github.io/libfabric/main/man/fi_arch.7.html (accessed 2026-08-30).
- **[fi_cxi(7)]** OpenFabrics Interfaces Working Group. "fi_cxi(7) — The CXI Fabric Provider."
  https://ofiwg.github.io/libfabric/v2.1.0/man/fi_cxi.7.html (accessed 2026-08-30).
- **[fi_setup(7)]** OpenFabrics Interfaces Working Group. "fi_setup(7) — libfabric setup and
  initialization." https://ofiwg.github.io/libfabric/main/man/fi_setup.7.html (accessed
  2026-08-30).
- **[Flux RFC 13]** Flux Framework. "RFC 13: Simple Process Manager Interface v1."
  https://flux-framework.readthedocs.io/projects/flux-rfc/en/latest/spec_13.html (accessed
  2026-08-30).
- **[Gabriel et al. 2004]** E. Gabriel, G. E. Fagg, G. Bosilca, T. Angskun, J. J. Dongarra,
  J. M. Squyres, V. Sahay, P. Kambadur, B. Barrett, A. Lumsdaine, R. H. Castain, D. J. Daniel,
  R. L. Graham, T. S. Woodall. "Open MPI: Goals, Concept, and Design of a Next Generation MPI
  Implementation." In *Recent Advances in Parallel Virtual Machine and Message Passing Interface
  (EuroPVM/MPI 2004)*, LNCS 3241, pp. 97–104. Springer, 2004. DOI: 10.1007/978-3-540-30218-6_19.
- **[Goglin & Moreaud 2013]** B. Goglin, S. Moreaud. "KNEM: A generic and scalable
  kernel-assisted intra-node MPI communication framework." *Journal of Parallel and Distributed
  Computing*, 73(2):176–188, 2013. DOI: 10.1016/j.jpdc.2012.09.016.
- **[Graham et al. 2006]** R. L. Graham, T. S. Woodall, J. M. Squyres. "Open MPI: A Flexible
  High Performance MPI." In *Parallel Processing and Applied Mathematics (PPAM 2005)*, LNCS 3911,
  pp. 228–239. Springer, 2006. DOI: 10.1007/11752578_29.
- **[Gropp et al. 1996]** W. Gropp, E. Lusk, N. Doss, A. Skjellum. "A high-performance, portable
  implementation of the MPI message passing interface standard." *Parallel Computing*,
  22(6):789–828, 1996. DOI: 10.1016/0167-8191(96)00024-5.
- **[Gropp & Lusk 2001]** W. Gropp, E. Lusk. "MPICH Abstract Device Interface, Version 3.3
  Reference Manual" (draft). Mathematics and Computer Science Division, Argonne National
  Laboratory, December 2001. `[UNVERIFIED: version numbering; a 3.4 reference manual is also cited
  in the literature]`
- **[Grun et al. 2015]** P. Grun, S. Hefty, S. Sur, D. Goodell, R. D. Russell, H. Pritchard,
  J. M. Squyres. "A Brief Introduction to the OpenFabrics Interfaces — A New Network API for
  Maximizing High Performance Application Efficiency." In *Proc. 23rd IEEE Annual Symposium on
  High-Performance Interconnects (HOTI 2015)*, pp. 34–39. IEEE, 2015. DOI: 10.1109/HOTI.2015.19.
- **[Grun & Goodell 2015]** P. Grun, D. Goodell. "A Brief Introduction to OpenFabrics Interfaces'
  libfabric." HOTI 23 tutorial slides. https://old.hoti.org/hoti23/slides/grun_goodell.pdf
  (accessed 2026-08-30).
- **[Hermanns et al. 2018]** M.-A. Hermanns, N. T. Hjelm, M. Knobloch, K. Mohror, M. Schulz.
  "Enabling callback-driven runtime introspection via MPI_T." In *Proc. 25th European MPI Users'
  Group Meeting (EuroMPI '18)*, article 8, pp. 1–10. ACM, 2018. DOI: 10.1145/3236367.3236370.
  `[UNVERIFIED: exact author list]`
- **[Hilbrich et al. 2010]** T. Hilbrich, M. Schulz, B. R. de Supinski, M. S. Müller. "MUST: A
  Scalable Approach to Runtime Error Detection in MPI Programs." In *Tools for High Performance
  Computing 2009*, pp. 53–66. Springer, 2010. DOI: 10.1007/978-3-642-11261-4_5.
- **[Hilbrich et al. 2012]** T. Hilbrich, M. S. Müller, B. R. de Supinski, M. Schulz,
  W. E. Nagel. "GTI: A Generic Tools Infrastructure for Event-Based Tools in Parallel Systems."
  In *Proc. IEEE 26th International Parallel and Distributed Processing Symposium (IPDPS 2012)*,
  pp. 1364–1375. IEEE, 2012. DOI: 10.1109/IPDPS.2012.123.
- **[Hilbrich et al. 2013]** T. Hilbrich, J. Protze, M. Schulz, B. R. de Supinski,
  M. S. Müller. "MPI Runtime Error Detection with MUST: Advances in Deadlock Detection."
  *Scientific Programming*, 21(3–4):109–121, 2013. DOI: 10.1155/2013/314971.
- **[Hilbrich et al. 2013 SC]** T. Hilbrich, B. R. de Supinski, W. E. Nagel, J. Protze,
  C. Baier, M. S. Müller. "Distributed wait state tracking for runtime MPI deadlock detection."
  In *Proc. SC'13*, article 16, pp. 1–12. ACM, 2013. DOI: 10.1145/2503210.2503237.
- **[Holmes et al. 2016]** D. Holmes, K. Mohror, R. E. Grant, A. Skjellum, M. Schulz, W. Bland,
  J. M. Squyres. "MPI Sessions: Leveraging Runtime Infrastructure to Increase Scalability of
  Applications at Exascale." In *Proc. 23rd European MPI Users' Group Meeting (EuroMPI 2016)*,
  pp. 121–129. ACM, 2016. DOI: 10.1145/2966884.2966915.
- **[HPC-AC 2018]** HPC Advisory Council. "Understanding Tag Matching Offload on ConnectX-5
  Adapters." HPC-Works knowledge base.
  https://hpcadvisorycouncil.atlassian.net/wiki/spaces/HPCWORKS/pages/141230081/ (accessed
  2026-08-30).
- **[HPCToolkit overview]** Rice University. "HPCToolkit Overview" (user manual).
  https://hpctoolkit.gitlab.io/hpctoolkit/users/overview.html (accessed 2026-08-30).
- **[Hunold & Carpen-Amarie 2015]** S. Hunold, A. Carpen-Amarie. "MPI Benchmarking Revisited:
  Experimental Design and Reproducibility." arXiv:1505.07734, 2015. See also S. Hunold,
  A. Carpen-Amarie, "Reproducible MPI Benchmarking Is Still Not as Easy as You Think," *IEEE
  Transactions on Parallel and Distributed Systems*, 27(12):3617–3630, 2016.
  DOI: 10.1109/TPDS.2016.2539167.
- **[Islam et al. 2014]** T. Islam, K. Mohror, M. Schulz. "Exploring the Capabilities of the New
  MPI_T Interface." In *Proc. 21st European MPI Users' Group Meeting (EuroMPI/ASIA '14)*,
  pp. 91–96. ACM, 2014. DOI: 10.1145/2642769.2642781.
- **[Islam et al. 2016]** T. Islam, K. Mohror, M. Schulz. "Exploring the MPI tool information
  interface: features and capabilities." *International Journal of High Performance Computing
  Applications*, 30(2):212–222, 2016. DOI: 10.1177/1094342015600507.
- **[Jumpshot-4 docs]** Argonne National Laboratory. "Understanding the Drawable" (Jumpshot-4
  documentation).
  https://www.mcs.anl.gov/research/projects/perfvis/software/viewers/jumpshot-4/node5.html
  (accessed 2026-08-30).
- **[Jumpshot-4 guide]** A. Chan, D. Ashton, R. Lusk, W. Gropp. "Jumpshot-4 Users Guide."
  Mathematics and Computer Science Division, Argonne National Laboratory.
  http://sunsite2.icm.edu.pl/pub/programming/mpich/slog2/js4-usersguide.pdf (accessed
  2026-08-30).
- **[Knüpfer et al. 2008]** A. Knüpfer, H. Brunst, J. Doleschal, M. Jurenz, M. Lieber,
  H. Mickler, M. S. Müller, W. E. Nagel. "The Vampir Performance Analysis Tool-Set." In *Tools
  for High Performance Computing*, pp. 139–155. Springer, 2008. DOI: 10.1007/978-3-540-68564-7_9.
- **[Knüpfer et al. 2012]** A. Knüpfer, C. Rössel, D. an Mey, S. Biersdorff, K. Diethelm,
  D. Eschweiler, M. Geimer, M. Gerndt, D. Lorenz, A. Malony, W. E. Nagel, Y. Oleynik, P. Philippen,
  P. Saviankou, D. Schmidl, S. Shende, R. Tschüter, M. Wagner, B. Wesarg, F. Wolf. "Score-P: A
  Joint Performance Measurement Run-Time Infrastructure for Periscope, Scalasca, TAU, and
  Vampir." In *Tools for High Performance Computing 2011*, pp. 79–91. Springer, 2012.
  DOI: 10.1007/978-3-642-31476-6_7.
- **[KVDb 2021]** A. Polyakov et al. "Key-Value Database Access Optimization For PMIx Standard
  Implementation." In *Proc. 2021 Ural Symposium on Biomedical Engineering, Radioelectronics and
  Information Technology (USBEREIT)*, 2021. DOI: 10.1109/USBEREIT51232.2021.9455075.
  `[UNVERIFIED: exact author list]`
- **[Liu et al. 2004]** J. Liu, W. Jiang, P. Wyckoff, D. K. Panda, D. Ashton, D. Buntinas,
  W. Gropp, B. Toonen. "Design and Implementation of MPICH2 over InfiniBand with RDMA Support."
  In *Proc. 18th International Parallel and Distributed Processing Symposium (IPDPS 2004)*. IEEE,
  2004. DOI: 10.1109/IPDPS.2004.1302922.
- **[Marts et al. 2019]** W. P. Marts, M. G. F. Dosanjh, S. Levy, W. Schonbein, R. E. Grant,
  P. G. Bridges. "MPI Tag Matching Performance on ConnectX and ARM." In *Proc. 26th European MPI
  Users' Group Meeting (EuroMPI '19)*. ACM, 2019. DOI: 10.1145/3343211.3343224.
  See also OSTI 1641812. `[UNVERIFIED: exact DOI]`
- **[Mellanox/SchedMD 2017]** A. Polyakov, J. Ladd, B. Karasev. "Slurm PMIx/UCX Backend:
  Leveraging InfiniBand to accelerate job start." Slurm User Group / SC17 presentation, 2017.
  https://slurm.schedmd.com/SC17/Mellanox_Slurm_pmix_UCX_backend_v4.pdf (accessed 2026-08-30).
- **[MPE README]** MPICH project. "MPE (MPI Parallel Environment) README."
  https://github.com/pmodels/mpe/blob/master/README (accessed 2026-08-30). See also A. Chan,
  D. Ashton, R. Lusk, W. Gropp, "User's Guide for MPE: Extensions for MPI Programs," Argonne
  National Laboratory.
- **[MPI Forum 1995]** Message Passing Interface Forum. *MPI: A Message-Passing Interface
  Standard, Version 1.1*, June 1995. Chapter 8, "Profiling Interface."
  https://www.netlib.org/mpi/mpi-report-1.1/node153.html
- **[MPI Forum 2015]** Message Passing Interface Forum. *MPI: A Message-Passing Interface
  Standard, Version 3.1*, June 2015. Chapter 14 ("Tool Support"), §14.2 (Profiling Interface,
  incl. "MPI Library Implementation Example"), §14.3 (MPI Tool Information Interface, incl.
  "Control Variables").
- **[MPI Forum 2018]** Message Passing Interface Forum. *The MPIR Process Acquisition Interface,
  Version 1.1*, March 2018. https://www.mpi-forum.org/docs/mpir-specification-03-01-2018.pdf
- **[MPI Forum 2021]** Message Passing Interface Forum. *MPI: A Message-Passing Interface
  Standard, Version 4.0*, June 2021. §11.3, "The Sessions Model."
- **[MPI Forum 2025]** Message Passing Interface Forum. *MPI: A Message-Passing Interface
  Standard, Version 5.0*, 2025. "Session Creation and Destruction Methods"; §14.2 "MPI Library
  Implementation."
- **[MPI Forum tools BoF 2018]** MPI Forum Tools Working Group. "MPI_T Events." SC18
  Birds-of-a-Feather presentation, November 2018.
  https://www.mpi-forum.org/bofs/2018-11-sc/events.pdf (accessed 2026-08-30).
- **[MPICH CH3 wiki]** MPICH project. "CH3 and Channels" (design documentation).
  https://github.com/pmodels/mpich/blob/main/doc/wiki/design/CH3_And_Channels.md (accessed
  2026-08-30).
- **[MPICH DevGuide]** MPICH project. "MPICH Developer Guide."
  https://github.com/pmodels/mpich/blob/main/doc/wiki/developer_guide.md (accessed 2026-08-30).
- **[MPICH PMI v2 wire protocol]** MPICH project. "PMI v2 Wire Protocol" (design draft).
  https://github.com/pmodels/mpich/blob/main/doc/wiki/design/PMI_v2_Wire_Protocol.md (accessed
  2026-08-30).
- **[mpiP user guide]** J. Vetter, C. Chambreau et al. "mpiP: Lightweight, Scalable MPI
  Profiling — User Guide." Lawrence Livermore National Laboratory, UCRL-CODE-223450.
  https://software.llnl.gov/mpiP/ (accessed 2026-08-30).
- **[mpir-to-pmix guide]** LANL/HPC. "MPIR to PMIx Guide." https://github.com/hpc/mpir-to-pmix-guide
  (accessed 2026-08-30).
- **[mpirun(1)]** Open MPI project. "mpirun(1)" manual page (Open MPI 5.x).
  https://man.archlinux.org/man/mpirun.1 (accessed 2026-08-30).
- **[Moreaud et al. 2010]** S. Moreaud, B. Goglin, D. Goodell, R. Namyst. "Optimizing MPI
  communication within large multicore nodes with kernel assistance." In *Proc. IEEE
  International Symposium on Parallel & Distributed Processing, Workshops and PhD Forum
  (IPDPSW 2010)*, pp. 1–7. IEEE, 2010. DOI: 10.1109/IPDPSW.2010.5470849.
- **[Mucci et al. 2005]** "Design Considerations for Shared Memory MPI Implementations on Linux
  NUMA Systems: An MPICH/MPICH2 Case Study." Technical report, 2005.
  https://icl.utk.edu/~mucci/latest/pubs/AMD-MPI-05.pdf (accessed 2026-08-30).
  `[UNVERIFIED: exact author list and publication venue]`
- **[MVAPICH2-GDR user guide]** Network-Based Computing Laboratory, The Ohio State University.
  "MVAPICH2-GDR User Guide." https://mvapich.cse.ohio-state.edu/userguide/gdr/ (accessed
  2026-08-30).
- **[mvapich pmi_v2.c]** MVAPICH 4.1 source, `src/pm/hydra/modules/pmi/src/pmi_v2.c` (Fossies
  Dox). https://fossies.org/dox/mvapich-4.1/hydra_2modules_2pmi_2src_2pmi__v2_8c_source.html
  (accessed 2026-08-30).
- **[Nagel et al. 1996]** W. E. Nagel, A. Arnold, M. Weber, H.-C. Hoppe, K. Solchenbach.
  "VAMPIR: Visualization and Analysis of MPI Resources." *Supercomputer*, 12(1):69–80, 1996.
  https://www.netlib.org/benchmark/top500/reports/report95/vampir/vampir.html
- **[NASA NPB]** NASA Advanced Supercomputing Division. "NAS Parallel Benchmarks."
  https://www.nas.nasa.gov/software/npb.html (accessed 2026-08-30).
- **[Noeth et al. 2009]** M. Noeth, P. Ratn, F. Mueller, M. Schulz, B. R. de Supinski.
  "ScalaTrace: Scalable compression and replay of communication traces for high-performance
  computing." *Journal of Parallel and Distributed Computing*, 69(8):696–710, 2009.
  DOI: 10.1016/j.jpdc.2008.09.001.
- **[NPB problem sizes]** NASA Advanced Supercomputing Division. "Problem Sizes and Parameters in
  NAS Parallel Benchmarks." https://www.nas.nasa.gov/software/npb_problem_sizes.html (accessed
  2026-08-30).
- **[OMB README]** Network-Based Computing Laboratory, The Ohio State University. "OSU
  Micro-Benchmarks README."
  https://mvapich.cse.ohio-state.edu/static/media/osu-micro-benchmarks/README.txt and
  https://mvapich.cse.ohio-state.edu/benchmarks/ (accessed 2026-08-30).
- **[ompi coll_tuned.h]** Open MPI source, `ompi/mca/coll/tuned/coll_tuned.h`.
  https://github.com/open-mpi/ompi/blob/master/ompi/mca/coll/tuned/coll_tuned.h (accessed
  2026-08-30).
- **[Open MPI docs, coll-tuned]** Open MPI project. "Tuning Collectives" (Open MPI 5.0.x / 6.0.x
  documentation). https://docs.open-mpi.org/en/v5.0.x/tuning-apps/coll-tuned.html (accessed
  2026-08-30).
- **[Open MPI docs, mca]** Open MPI project. "The Modular Component Architecture (MCA)."
  https://docs.open-mpi.org/en/main/mca.html (accessed 2026-08-30).
- **[Open MPI docs, MPI_T]** Open MPI project. "MPI_T(3)" and "MPI_T_event_handle_alloc(3)"
  manual pages. https://docs.open-mpi.org/en/main/man-openmpi/man3/MPI_T.3.html (accessed
  2026-08-30). See also open-mpi/ompi PR #14083, "Implement the MPI_T events interface
  (mca_base_event framework + producers)."
- **[Open MPI docs, mpir-tools]** Open MPI project. "Using the MPIR shim module with debuggers
  and tools." https://docs.open-mpi.org/en/main/app-debug/mpir-tools.html (accessed 2026-08-30).
- **[Open MPI docs, pmix-and-prrte]** Open MPI project. "The role of PMIx and PRRTE."
  https://docs.open-mpi.org/en/v5.0.x/launching-apps/pmix-and-prrte.html (accessed 2026-08-30).
- **[Open MPI docs, shared-memory]** Open MPI project. "Shared memory" (tuning networking).
  https://docs.open-mpi.org/en/v6.0.x-pre-release/tuning-apps/networking/shared-memory.html
  (accessed 2026-08-30).
- **[Open MPI workshop 2006]** J. M. Squyres. "Why Components? The Modular Component Architecture
  (Part 1)." Open MPI Developers' Workshop, 2006.
  https://www.open-mpi.org/papers/workshop-2006/mon_06_mca_part_1.pdf (accessed 2026-08-30).
- **[openucx README]** UCX project. "openucx/ucx README."
  https://github.com/openucx/ucx/blob/master/README.md (accessed 2026-08-30).
- **[OTF2 event-record reference]** Jülich Supercomputing Centre / TU Dresden. "Open Trace
  Format 2: List of all event records."
  https://perftools.pages.jsc.fz-juelich.de/cicd/otf2/tags/latest/html/group__records__event.html
  (accessed 2026-08-30).
- **[osu_util_options.h]** MVAPICH 4.1 source, `osu_benchmarks/c/util/osu_util_options.h`
  (Fossies Dox). https://fossies.org/dox/mvapich-4.1/osu__util__options_8h_source.html (accessed
  2026-08-30).
- **[Pillet et al. 1995]** V. Pillet, J. Labarta, T. Cortes, S. Girona. "PARAVER: A tool to
  visualize and analyze parallel code." In *Proc. WoTUG-18: Transputer and occam Developments*,
  pp. 17–31, 1995.
- **[PnMPI README]** Lawrence Livermore National Laboratory. "PnMPI: Virtualization Layer for the
  MPI Profiling Interface." https://github.com/llnl/pnmpi/ (accessed 2026-08-30).
- **[Raffenetti et al. 2017]** K. Raffenetti, A. Amer, L. Oden, C. Archer, W. Bland, H. Fujita,
  Y. Guo, T. Janjusic, D. Durnov, M. Blocksome, M. Si, S. Seo, A. Langer, G. Zheng, M. Takagi,
  P. Coffman, J. Jose, S. Sur, A. Sannikov, S. Oblomov, M. Chuvelev, M. Hatanaka, X. Zhao,
  P. Fischer, T. Rathnayake, M. Otten, M. Min, P. Balaji. "Why is MPI so slow? Analyzing the
  fundamental limits in implementing MPI-3.1." In *Proc. SC'17*, article 62, pp. 1–12. ACM, 2017.
  DOI: 10.1145/3126908.3126963.
- **[ReproBLAS]** University of California, Berkeley (BeBOP group). "ReproBLAS — Reproducible
  Basic Linear Algebra Sub-programs" (incl. `binnedMPI.h` reference).
  https://bebop.cs.berkeley.edu/reproblas/ (accessed 2026-08-30).
- **[Reussner et al. 1998]** R. Reussner, P. Sanders, L. Prechelt, M. Müller. "SKaMPI: A
  detailed, accurate MPI benchmark." In *Recent Advances in Parallel Virtual Machine and Message
  Passing Interface (EuroPVM/MPI 1998)*, LNCS 1497, pp. 52–59. Springer, 1998.
  DOI: 10.1007/BFb0056559.
- **[Reussner et al. 2002]** R. Reussner, P. Sanders, J. L. Träff. "SKaMPI: A Comprehensive
  Benchmark for Public Benchmarking of MPI." *Scientific Programming*, 10(1):55–65, 2002.
  DOI: 10.1155/2002/202839.
- **[ScalaTrace tech report]** M. Noeth, F. Mueller, M. Schulz, B. R. de Supinski. "ScalaTrace:
  Scalable Compression and Replay of Communication Traces for High Performance Computing."
  Technical report, 2008. OSTI 1009216 / OSTI 965094.
- **[SC23 tools poster]** "Sophisticated Tools for Performance Analysis and Auto-tuning of
  Performance Portable Parallel Programming." SC23 research poster, 2023.
  https://sc23.supercomputing.org/proceedings/tech_poster/poster_files/rpost122s3-file3.pdf
  (accessed 2026-08-30). `[UNVERIFIED: exact author list]`
- **[Schulz & de Supinski 2005]** M. Schulz, B. R. de Supinski. "A Flexible and Dynamic
  Infrastructure for MPI Tool Interoperability." In *Proc. 2005 International Conference on
  Parallel Processing (ICPP 2005)*, pp. 193–202. IEEE, 2005. DOI: 10.1109/ICPP.2005.35.
- **[Schulz & de Supinski 2007]** M. Schulz, B. R. de Supinski. "PnMPI Tools: A Whole Lot Greater
  Than the Sum of Their Parts." In *Proc. SC'07*, article 30. ACM, 2007.
  DOI: 10.1145/1362622.1362663.
- **[Score-P docs]** Jülich Supercomputing Centre / TU Dresden / others. "Score-P User Manual —
  Introduction." https://perftools.pages.jsc.fz-juelich.de/cicd/scorep/tags/latest/html/
  (accessed 2026-08-30).
- **[Shamis et al. 2015]** P. Shamis, M. G. Venkata, M. G. Lopez, M. B. Baker, O. Hernandez,
  Y. Itigin, M. Dubman, G. Shainer, R. L. Graham, L. Liss, Y. Shahar, S. Potluri, D. Rossetti,
  D. Becker, D. Poole, C. Lamb, S. Kumar, C. Stunkel, G. Bosilca, A. Bouteiller. "UCX: An Open
  Source Framework for HPC Network APIs and Beyond." In *Proc. 23rd IEEE Annual Symposium on
  High-Performance Interconnects (HOTI 2015)*, pp. 40–43. IEEE, 2015. DOI: 10.1109/HOTI.2015.13.
- **[Shende & Malony 2006]** S. S. Shende, A. D. Malony. "The TAU Parallel Performance System."
  *International Journal of High Performance Computing Applications*, 20(2):287–311, 2006.
  DOI: 10.1177/1094342006064482.
- **[Snell et al. 1996]** Q. O. Snell, A. R. Mikler, J. L. Gustafson. "NetPIPE: A Network
  Protocol Independent Performance Evaluator." In *Proc. IASTED International Conference on
  Intelligent Information Management and Systems*, 1996. `[UNVERIFIED: exact venue and
  pagination]`
- **[Squyres 2012]** J. M. Squyres. "Open MPI." In A. Brown, G. Wilson (eds.), *The Architecture
  of Open Source Applications, Volume 2: Structure, Scale, and a Few More Fearless Hacks*,
  chapter 15. 2012. https://aosabook.org/en/v2/openmpi.html
- **[Supercomputing 2023 survey]** "Exploiting copy engines for intra-node MPI collective
  communication." *The Journal of Supercomputing*, 2023. DOI: 10.1007/s11227-023-05340-x.
  `[UNVERIFIED: exact author list]`
- **[TAU user guide]** S. Shende, A. D. Malony et al. "TAU User's Guide." Performance Research
  Laboratory, University of Oregon.
  http://www.cs.uoregon.edu/research/paracomp/tau/tauprofile/docs/tau-2.13-usersguide.pdf
  (accessed 2026-08-30).
- **[UCX design]** UCX project. "UCX: Design."
  https://openucx.github.io/ucx/api/latest/html/md_docs_2doxygen_2design.html (accessed
  2026-08-30).
- **[Vampir user guide]** Center for Applied Mathematics / Pallas GmbH. "Vampir 4.0 User's
  Guide." http://www.csar.man.ac.uk/user_information/tools/Vampir-userguide.pdf (accessed
  2026-08-30).
- **[Vetter & de Supinski 2000]** J. S. Vetter, B. R. de Supinski. "Dynamic Software Testing of
  MPI Applications with Umpire." In *Proc. SC'00*, article 51. IEEE, 2000.
  DOI: 10.1109/SC.2000.10055.
- **[Vetter & McCracken 2001]** J. S. Vetter, M. O. McCracken. "Statistical scalability analysis
  of communication operations in distributed applications." In *Proc. 8th ACM SIGPLAN Symposium
  on Principles and Practices of Parallel Programming (PPoPP '01)*, pp. 123–132. ACM, 2001.
  DOI: 10.1145/379539.379590.
- **[VI-HPS BSC tools]** Barcelona Supercomputing Center. "Understanding applications with
  Paraver and Dimemas." VI-HPS Tuning Workshop 12 material.
  https://www.vi-hps.org/cms/upload/material/tw12/BSCTools_VI-HPS-TW12.pdf (accessed 2026-08-30).
- **[VI-HPS Score-P docs]** VI-HPS. "Score-P documentation" (incl. OTF2 and Cube 4 format
  descriptions). https://www.vi-hps.org/projects/score-p/documentation/documentation.html
  (accessed 2026-08-30).
- **[VI-HPS Vampir material]** VI-HPS. "Interactive visualization and time-interval statistics
  with Vampir." VI-HPS Tuning Workshop 47 material.
  https://www.vi-hps.org/cms/upload/material/tw47/Vampir.pdf (accessed 2026-08-30).
- **[Vo et al. 2010]** A. Vo, S. Aananthakrishnan, G. Gopalakrishnan, B. R. de Supinski,
  M. Schulz, G. Bronevetsky. "A Scalable and Distributed Dynamic Formal Verifier for MPI
  Programs." In *Proc. SC'10*, pp. 1–10. IEEE, 2010. DOI: 10.1109/SC.2010.7.
- **[Wang et al. 2011]** H. Wang, S. Potluri, M. Luo, A. K. Singh, S. Sur, D. K. Panda.
  "MVAPICH2-GPU: optimized GPU to GPU communication for InfiniBand clusters." *Computer Science —
  Research and Development*, 26(3–4):257–266, 2011. DOI: 10.1007/s00450-011-0171-3. See also
  H. Wang et al., "Optimized Non-contiguous MPI Datatype Communication for GPU Clusters,"
  *Proc. IEEE Cluster 2011*, pp. 308–316. DOI: 10.1109/CLUSTER.2011.42.
- **[Wang et al. 2014]** H. Wang, S. Potluri, D. Bureddy, C. Rosales, D. K. Panda. "GPU-Aware
  MPI on RDMA-Enabled Clusters: Design, Implementation and Evaluation." *IEEE Transactions on
  Parallel and Distributed Systems*, 25(10):2595–2605, 2014. DOI: 10.1109/TPDS.2013.222.
- **[Zhou et al. 2021]** H. Zhou, K. Raffenetti, W. Bland, Y. Guo. "Generating Bindings in
  MPICH." In *Proc. 28th European MPI Users' Group Meeting (EuroMPI '21)*, 2021.
  arXiv:2401.16547.
