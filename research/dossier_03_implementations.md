# Dossier 03 — MPI Implementation Internals, Tooling, and Software Architecture

**Scope.** The *software architecture* of production MPI implementations (MPICH, Open MPI, MVAPICH2) and
the tooling ecosystem layered on them, read for patterns reusable in a non-HPC message-passing runtime.
It favors layer boundaries, interface contracts, selection mechanisms and data models over feature
lists. Unverified claims are marked `[UNVERIFIED]`.

---

## 1. MPICH: the device abstraction and its refactorings

### 1.1 What the ADI buys you

MPICH implements MPI's user-visible API *once*, portably, over the **Abstract Device Interface (ADI)**:
`MPID_`-prefixed functions where `MPID_Send` implements `MPI_Send`. Nearly every MPI function calls its
MPID counterpart, which either supplies full functionality or falls back to the portable `MPIR_` path
[MPICH DevGuide; Gropp et al. 1996]. Two consequences matter more than portability. First, **vendor
forkability without forking**: the goal is "to allow downstream vendors to easily create vendor specific
implementations," and Intel MPI, Cray MPI and MVAPICH replace the *device*. Second, a
**ladder of porting effort**: MPICH2 exposed ADI-3, CH3 (about a dozen functions) and an RDMA Channel
(five), which "provide different trade-offs between communication performance and ease of porting" [Liu et
al. 2004; Gropp & Lusk 2001].

### 1.2 ADI-3 and CH3

CH3 is an *example implementation of ADI-3* re-exposing a small "channel" interface in five groups:
channel init, per-connection init, process-group hooks, data sending and progress [MPICH CH3 wiki]. Two
mechanisms recur. **Virtual connections** are per-peer state objects that a
channel extends with its own fields via `MPIDI_CH3_VC_DECL`, and likewise for requests, packet enums and
progress state: the generic layer owns the struct, the transport owns an opaque extension region *inside*
it. The **progress engine** advances `MPID_Request`s queued on the VC; ADI point-to-point calls only
*initiate*, completing inside `MPID_Progress_wait/test` [Mucci et al. 2005].

### 1.3 Nemesis: shared memory as the *primary* case

Nemesis inverted the conventional priority order — scalability, then intranode, then internode — its
authors stating they "strive to minimize the overhead for intranode communication, even if this comes at
some penalty for internode communication," with network modules "designed around the queue mechanism,
rather than the other way around" [Buntinas et al. 2006]. Four choices are worth copying:


- **One lock-free receive queue per process, not per pair** — per-pair queues are O(N²), a locked queue
  contends on a large SMP, and multi-queue polling is itself expensive.
- **Multiple-enqueuer, single-dequeuer lock-free queues** using swap and compare-and-swap, costing **six
  instructions to enqueue and eleven to dequeue**; pointers are *relative* offsets because the shared
  region need not map at the same address everywhere.
- **Fastboxes** — one buffer plus a full/empty flag, checked before the queue, explicitly limited to SMPs
  "with a small number of processors" [Buntinas et al. 2007]: a fast path may be non-general.
- **Unified send path** — a network module owns a send queue "analogous to a process's lock-free receive
  queue," so locality is *which queue you enqueue on*.

### 1.4 CH4: collapsing the stack

CH4 was written "from the ground up, keeping low instruction and cycle counts as a primary design goal,"
as **ch4 core** plus **netmods** (OFI, UCX, Portals) and **shmmods** (POSIX, XPMEM). Its central principle
is *semantic flow-through*: netmods and shmmods "know what MPI-level call triggered a particular data
movement operation, including all its parameters," so each picks the best implementation and otherwise
falls back to ch4 core's active-message path [Raffenetti et al. 2017]. In `MPI_Put`, ch4 core does a
**locality check** (self / same-node → shmmod / remote → netmod), then the module chooses native versus
active-message.

Default CH4 costs **221 instructions for `MPI_Isend`** and **215 for `MPI_Put`** versus **253 and 1,342**
for CH3; the 221 decompose as error checking 74, thread-safety check 6, call overhead 23, redundant
runtime checks 59 and *mandatory* MPI overhead 59, reaching **16** with link-time inlining and proposed
semantic relaxations [Raffenetti et al. 2017].

### 1.5 The MPIR / MPID / MPIDI / MPIDIG naming ladder

MPICH's layering is legible from its prefixes [MPICH DevGuide]:

| Prefix | Layer |
|---|---|
| `MPI_` / `PMPI_` | Generated binding layer: validation, trivial early returns, error behavior |
| `MPIR_` | Portable implementation, grouped by MPI standard *chapter* (`pt2pt`, `coll`, `rma`) |
| `MPID_` | Abstract Device Interface |
| `MPIDI_NM_` / `MPIDI_SHM_` | CH4's netmod / shmmod API (an "additional ADI-like interface") |
| `MPIDIG_` | CH4's *generic* active-message fallback |
| `MPL_` | Utilities independent of MPICH internals (atomics, threads, logging, GPU) |

CH4's active-message contract is small enough to copy: `am_send_hdr`, `am_isend`, `am_isendv`,
callback-safe `*_reply` variants, `am_recv` and capability queries, against three callbacks
(target-message, target-completion, origin). Boilerplate is *generated* from `mpi_standard_api.txt`,
turning ~70,000 lines of hand-maintained binding code into ~5,000 lines of Python, which made
prototyping QMPI tractable [Zhou et al. 2021]. Collective dispatch ends in `MPIR_Bcast_allcomm_auto`,
choosing "a best algorithm based on selection logic defined by a runtime json file": selection is *data*.

---

## 2. Open MPI: the Modular Component Architecture

### 2.1 Layers and the plugin taxonomy

Open MPI unions four prior codebases (LAM/MPI, LA-MPI, FT-MPI, PACX-MPI) whose teams judged their designs
too divergent to merge [Gabriel et al. 2004; Graham et al. 2006]. Its stated goals: group similar
functionality into abstraction layers, use run-time plugins and parameters to choose among implementations
of the same behavior, and avoid "allowing abstraction to get in the way of performance" [Squyres 2012]. The layers are **OPAL** (per-process portability — lists, IP interface
discovery, shared memory, affinity, timers), **ORTE** (launch, monitor, kill parallel jobs) and **OMPI**
(the MPI API). Each is a standalone library, and the order OMPI → ORTE → OPAL is *enforced by the
linker*: "applications will fail to link if one layer incorrectly attempts to use a symbol in a higher
layer" — a mechanical abstraction-violation detector.

MCA vocabulary [Open MPI docs, mca]: a **project** is OPAL/OMPI/OSHMEM; a **framework** manages components
of one type for one task; a **component** implements a framework's interface as a loadable plugin; a
**module** is a *runtime instance* of a component — "an MCA component is analogous to a C++ class," so a
node with two Ethernet NICs yields one TCP BTL component but two TCP BTL modules. Directory names must
match symbol names, mapping `ompi/mca/btl/tcp` to `mca_btl_tcp_component`, found via `dlsym(2)` [Open MPI
workshop 2006]. The
per-process **component struct** (metadata plus `open`, `close`, `query` and *parameter registration*
pointers) is separate from the per-resource **module structs**, and frameworks nest the base component
struct as their first member — "a simple emulation of C++ single inheritance," an explicit exception to
the no-casting rule because it "helps enforce abstraction barriers" [Squyres 2012].

### 2.2 Selection policy is part of the framework contract

Frameworks differ not only in interface but in *lifecycle policy* [Squyres 2012]. **Many-of-many**
frameworks (BTL) open every discoverable component and ask each "do you want to run?", each inspecting the
system — is this network present and active? — while the framework unloads those that decline.
**One-of-many** frameworks select a single component (checkpointing must be job-consistent). **Static**
frameworks force compile-time selection, to allow direct rather than indirect calls (`memcpy`) or because
the component must run *pre-`main`*.

Selection is by **self-reported priority**, adjustable via MCA parameters, with a guaranteed `base`
fallback for collectives [Open MPI docs, coll-tuned]: the plugin system has a *total order* and a
*default*. MCA parameters are the other half — named, described, defaulted parameters registered "rather
than hard-coding constants," discoverable via `ompi_info` and settable on the command line, by environment
variable or in INI files, with selection parameters accepting lists and a `^` negation prefix (`--mca btl
^tcp`).

### 2.3 The point-to-point stack: PML / BML / BTL / MTL

Open MPI splits *matching* from *transport*, and offers two different splits [Barrett]:

- **PML (Point-to-point Messaging Layer)** implements MPI point-to-point semantics: `ob1` (**software
  matching**, multi-device striping, drives BTLs), `cm` (**offloaded matching**, single device, drives
  MTLs) and `ucx` (delegates to UCX's UCP layer).
- **BML (BTL Management Layer)** — "a thin multiplexing layer over the BTLs (inline functions)" managing
  peer resource discovery and round-robining across them.
- **BTL (Byte Transfer Layer)** — "a simple tag based interface for communication similar to active
  messaging," plus RDMA `put`/`get` and completion callbacks; components include `tcp`, `sm` (shared
  memory, historically `vader`), `self`, `uct`, `ofi`, `openib`.
- **MTL (Matching Transport Layer)** — "exclusively used as the underlying transports for the `cm` PML,"
  e.g. `psm2`, `ofi`.

At wire-up, `add_procs` cascades PML → BML → each BTL; each BTL creates an endpoint struct caching the
peer's addressing information, and the BML caches those endpoints on the peer's `ompi_proc_t` **grouped by
BTL functionality** (e.g. `btl_eager` for small messages). Because BTL modules are per device, the engine
can "treat all network devices equally, and perform user-level channel bonding" [Squyres 2012].

### 2.4 Collectives

The `coll` framework contains `tuned`, `han`, `basic`, `base`, `libnbc` (nonblocking collectives),
`hcoll`, `ucc`, `sm` and `ftagree`; `base` "steps in and takes over when another component fails to
provide an implementation." `tuned` has three modes: **fixed decision** (default), a compiled decision
tree with thresholds "derived by measuring performance on existing clusters," which can be markedly wrong
on unlike hardware; **forced algorithm** via MCA parameters, "often... an ineffective means of tuning";
and **dynamic decision**, a rules file mapping (collective, communicator size, message size) → algorithm
[Open MPI docs, coll-tuned; ompi coll_tuned.h].

---

## 3. Process launch, bootstrap, and the wire-up problem

### 3.1 PMI-1, PMI-2, Hydra

MPICH's answer is **PMI**, "a carefully defined interface... that allows different process managers to
interact with the MPI library in a standardized way" [Balaji et al. 2010], covering bootstrap inside
`MPI_Init` and MPI-2 dynamic process management [Flux RFC 13; mvapich pmi_v2.c]. The data model is a **key-value space
(KVS)**: processes `Put` addressing information, `Fence` (barrier plus consistency point), then `Get`
peers' values; PMI-2 adds job and node attributes, name publishing and thread-aware semantics over a
line-oriented wire protocol [MPICH PMI v2 wire protocol]. **Hydra** implements both inside MPICH and was
evaluated at nearly 6,000 processes; PMI-1's shortcomings, per its designers, concern "scalability for
large numbers of cores on a node and efficient interaction with hybrid programming models."

### 3.2 PMIx: the wire-up scalability problem stated precisely

PMIx decomposes launch into stages and says where the time goes [Castain et al. 2017; Castain et al. 2018]. **Stage 4 is
the modex**, a global exchange of published endpoint information "executed via a collective operation
normally executed over the management Ethernet"; it "is typically the largest application launch time
component." **Stage 6 is the final barrier**, "the next largest block of time in the start profile." PMIx
responds by removing scalability-limiting restrictions, standing up a standards-like body and providing a
reference implementation — explicitly "programming model agnostic," targeting O(10⁶) processes on O(10⁵)
nodes through `MPI_Init` in under 30 seconds [Castain et al. 2015]. Its mechanisms:

- **Data-driven exchange instead of a barrier-heavy all-to-all** — "data blobs versus encoded metakeys,"
  plus *data scoping* to shrink the modex [Castain et al. 2015].
- **Direct modex** — fetch a peer's "business card" on first contact rather than all-to-all; it
  "significantly outperforms full modex operations" but "still scales as O(N)".
- **Early wire-up** — daemons wire up in the background right after launch, using the window while the
  application initializes locally [Mellanox/SchedMD 2017].
- **Shared-memory KVS per node**, giving "zero-message" access under intensive reads [KVDb 2021].
- **Beyond bootstrap** — tool connections, generalized queries (job status, layout, resources), **event
  notification** (subscribe, chained handlers; pre-emption, failures, timeout warnings) and job control
  [Castain et al. 2017 BoF].

Architecturally PMIx is three artifacts: a **standard** ("nothing about implementation"), a **reference
library** and a **reference server** — "a full-featured 'shim' to a non-PMIx RM" [Castain et al. 2017
BoF]. Open MPI closes the loop: ORTE spun off into the PMIx standard and OpenPMIx, and grew **PRRTE**,
which "has effectively replaced ORTE" and is now a *third-party dependency*; `mpirun` wraps `prterun`
[Open MPI docs, pmix-and-prrte; mpirun(1)], and Slurm integrates PMIx as a plugin. Launch and wire-up traffic is
*out-of-band* (a daemon tree over management Ethernet); application traffic is *in-band* on the fabric the
exchanged data describes.

### 3.3 MPI Sessions: fixing the "world model" bootstrap

The World Model's limitations, per the standard: "MPI cannot be initialized from different application
components without a priori knowledge or coordination; MPI cannot be initialized more than once; and MPI
cannot be reinitialized after `MPI_FINALIZE`" [MPI Forum 2021]. Sessions remove "the known scalability
barriers by no longer requiring all possible communication peers to be included in `MPI_COMM_WORLD`,"
explicitly to eliminate "heroic developer efforts" [Holmes et al. 2016]. A process calls
`MPI_Session_init`, queries the runtime for named process sets, and derives groups and then communicators,
allocating only for its actual peers. `MPI_COMM_WORLD` is invalid, and **isolation** is
enforced: objects from different session handles may not be intermixed [MPI Forum 2025].

---

## 4. Transports

**TCP/IP.** Open MPI's TCP BTL creates a listening socket and a module per "up" IPv4/IPv6 interface and
registers each `(IP address, port)` tuple "with a central repository so that other MPI processes know how
to contact it," deciding reachability from netmasks [Squyres 2012].

**Shared memory.** The baseline is two-copy (copy-in/copy-out via a shared buffer), which "suffer[s] from
high CPU utilization and cache pollution" [Buntinas et al. 2009]. Single-copy alternatives are **CMA**
(most widely available but "the lowest performance of the single-copy mechanisms"), **KNEM** (a kernel
module mapping the source buffer into kernel space, supporting noncontiguous and asynchronous transfers)
and **XPMEM** (mapping the buffer into the peer's *user* address space, so it needs a registration cache)
[Open MPI docs, shared-memory; Goglin & Moreaud 2013]. The crossover rule is consistent: two-copy small,
memory-mapping single-copy large, because page-frame mapping "adds a significant per-message overhead for
small messages" [Supercomputing 2023 survey]. KNEM improved large-message throughput up to 2× and
"suffers less from process placement" on complex NUMA topologies [Moreaud et al. 2010].

**UCX** has four components, each with a standalone public API [UCX design; Shamis et al. 2015]: **UCP**
(protocol — tag matching, streams, connection establishment, multi-rail, mixed memory types), **UCT**
(transport — active messages, RMA and atomics over vendor drivers and InfiniBand verbs, in three operation
classes: *short* immediate, *bcopy* buffered copy-and-send, *zcopy* zero-copy RDMA), **UCS** (utilities)
and **UCM** (intercepts allocation events, backing the registration cache). UCP "dynamically selects
optimal UCT resources at run time based on requested features and performance criteria" [openucx
README].

**Libfabric / OFI.** The core defines the API and "discovery services"; "the bulk of the libfabric API is
implemented by each provider," and a call "is routed directly into a specific provider" via function
pointers, across four service groups: control, communication, data transfer (message queues, tag matching,
RMA, atomics) and completion (event queues, counters) [fi_arch(7)]. The negotiation protocol is the
interesting part: the application requests **capabilities** via `fi_getinfo` caps bits (`FI_MSG`,
`FI_TAGGED`, `FI_RMA`, `FI_ATOMIC`), and the provider replies with extra capabilities it can grant for
free plus **mode bits** encoding "restrictions on an application's use of the interface" arising from that
provider's internals, so it can "select a software path that is best suited for both that application's
needs and the provider's restrictions" [Grun et al. 2015; fi_setup(7)]. Each send carries one tag; each
posted receive carries a tag *and a mask* applied before comparison.

**GPU-aware MPI.** MVAPICH2-GPU accepts device pointers in `MPI_Send`/`MPI_Recv`, using CUDA Unified
Virtual Addressing to "differentiate between device memory and host memory without any hints from the
user"; noncontiguous datatype pack/unpack is *offloaded to the GPU* and pipelined against device-host
copies and network RDMA [Wang et al. 2011; Wang et al. 2014]. Intranode uses **CUDA IPC**, internode **GPUDirect
RDMA** for direct NIC-GPU peer-to-peer, threshold-limited by message size [MVAPICH2-GDR user guide].

**Tag-matching offload.** Implementations traditionally keep a Posted Receive Queue and an Unexpected
Message Queue as linked lists; hash binning replaces these, and UCX uses 1021 bins keyed on the tag
[Marts et al. 2019]. Hardware offload (ConnectX-5 and later) moves matching onto the NIC, enabling
zero-copy delivery into the user buffer and "complete rendezvous progress" so the CPU computes while the
adapter gathers remote data. Crucially it is **hybrid and threshold-driven**: `UCX_TM_THRESH` (default
1024 B) keeps small messages in software "because using TM offload implies noticeable performance
overhead," and "UCP does not offload any message if there is any non-offloaded uncompleted receive
operation... for the sake of preserving message ordering" [HPC-AC 2018]. Libfabric's CXI provider makes
the fallback explicit via `FI_CXI_RX_MATCH_MODE` [fi_cxi(7)]. **Wildcards are the hard case**, evaluated separately
as transient versus permanent `MPI_ANY_TAG` receives.

---

## 5. Tooling and observability

### 5.1 PMPI: name shifting

Since MPI-1 the standard has required "a mechanism through which all of the MPI defined functions may be
accessed with a name shift," so every `MPI_` function is also reachable as `PMPI_` [MPI Forum 1995]. Four
accompanying requirements are load-bearing and frequently forgotten: unreplaced functions must still link
without name clashes; the implementation must **document** whether language bindings are layered, so a
profiler author knows whether to wrap every binding or only the lowest-level routines; wrappers must be
**separable** from the library; and a no-op `MPI_Pcontrol` must exist. The library must also be built so
"the inclusion of MPI functions can be achieved one at a time" — one function per compilation unit — so a
tool defines only what it intercepts [MPI Forum 2015]. Two implementations are sanctioned: **weak
symbols** or double compilation with link order `cc ... -lmyprof -lpmpi -lmpi`.

PMPI bought an ecosystem: mpiP, Score-P, Extrae, TAU, MUST and ScalaTrace all interpose without touching
application source or the MPI implementation. Its cost is **one tool at a time**: "using and
linking more than one tool library at a time is not possible," which "leads to monolithic
tool designs" [Elis 2018]. **PnMPI** answers this as a *virtualization layer over PMPI*, patching tool
binaries' symbol tables into modules and keeping separate link stacks per MPI routine so each registered
tool's wrapper runs in turn [Schulz & de Supinski 2005; PnMPI README]. **QMPI**, the proposed successor, "allows for
simultaneous attachment of multiple tools": tools receive a *next-function* pointer to continue the chain
and keep independent state via `QMPI_Set_context`/`QMPI_Get_context` [Elis et al. 2019; Elis et al. 2020]. QMPI is a
proposal, not a ratified requirement [Zhou et al. 2021].

### 5.2 MPI_T: the tool information interface

MPI-3 added MPI_T, giving tools "access to MPI internal performance and configuration information"
implementation-independently, complementing PMPI [Islam et al. 2016]. **Control variables (cvars)** "fine
tune properties and configuration settings of the MPI implementation" — canonically the eager limit; the
implementation exports N cvars indexed 0..N-1, and `MPI_T_cvar_get_info` returns name, verbosity,
datatype, description, **bind** (the MPI object class it attaches to) and **scope** (when writes are
legal) [MPI Forum 2015]. **Performance variables (pvars)** are counters, watermarks and timers internal to
the library; **categories** hierarchically group cvars, pvars and events [Islam et al. 2014]. One detail
is worth stealing verbatim: MPI_T handles "are distinct from handles used in the remaining parts of the MPI standard because
they must be usable *before* `MPI_INIT` and *after* `MPI_FINALIZE`. Further... having a separate handle
space enables optimizations." 

**MPI_T events** (MPI-4, refined in MPI-5) add callback-driven introspection: "No events are defined in
the standard; all events exposed are decided by the MPI implementation" [MPI Forum tools BoF 2018;
Hermanns et al. 2018]. An **event source** is a clock domain plus metadata; every **event type** belongs to
one source with a fixed typed layout; a tool allocates a **registration handle**, attaches callbacks keyed
by **safety level** (`NONE`, `MPI_RESTRICTED`, `THREAD_SAFE`, `ASYNC_SIGNAL_SAFE`) plus a
**dropped-event handler**, then reads fields with `MPI_T_event_read` [Open MPI docs, MPI_T].

### 5.3 Trace data models and visualizations

Three trace data models converged independently on the same triad. **SLOG-2** (MPICH's MPE, viewed by
Jumpshot-4) defines three **drawable topologies** on a *(timeline ID, timestamp)* canvas: a **state** is
two points with the *same* timeline ID (an interval drawn as a rectangle), an **arrow** is two points with
*different* timeline IDs (a message), and an **event** is a single point [Jumpshot-4 docs]. Display
attributes (color, legend name, topology) live in a shared **Category** object referenced by the drawable
rather than repeated per record, and nested states encode the call stack. Its scalability trick is
*bounding boxes*: a postorder tree of drawables with **preview drawables** at interior nodes, so a viewer
scrolls at any zoom without loading the whole file [Chan et al., SLOG-2].
**Paraver** uses states, events and relations/communications, but "specifies a trace format but no actual
semantics for the encoded values": the viewer "blindly represents the values and events passed to it,"
with filter modules as "building blocks to transform the trace" [BSC Paraver; Pillet et al. 1995] — and a
filtered Paraver trace *is itself* a Paraver trace, so reduction composes. **OTF2** splits records into
**definition records** (timer resolution, region and process names, metric definitions) and **event
records** (`Enter`, `Leave`, MPI send/receive, `MetricInstance`, `ThreadFork`), each carrying a `location`
and `timestamp` [OTF2 event-record reference]; compression happens *at runtime* and reading holds only
three chunks in memory, so supercomputer traces open on a laptop [Eschweiler et al. 2012].

| Tool | Measurement | Data model | Presentation |
|---|---|---|---|
| Jumpshot-4 / MPE | PMPI logging | CLOG2 → SLOG-2 | timeline with message arrows; duration histograms for load imbalance [Jumpshot-4 guide] |
| Score-P | *one* instrumentation for Scalasca, Vampir, TAU and Periscope; profiling-vs-tracing chosen at run time [Knüpfer et al. 2012] | OTF2 traces, Cube4 profiles | via Vampir, Cube, Scalasca [VI-HPS Score-P docs; Score-P docs] |
| Vampir / Intel Trace Analyzer | read OTF2 (resp. Intel STF) | event trace keyed by location | Master/Process timelines, Counter Data, Performance Radar, summary charts, communication matrix [Knüpfer et al. 2008; Nagel et al. 1996; Vampir user guide; VI-HPS Vampir material] |
| Extrae / Paraver / Dimemas | `LD_PRELOAD`, static PMPI or Dyninst rewriting, plus PAPI counters [BSC Extrae] | `.prv` records, `.pcf` labels, `.row` names | Paraver timelines; Dimemas simulates from resource-demand sequences [VI-HPS BSC tools] |
| HPCToolkit | **asynchronous sampling** plus binary analysis, since instrumentation "can distort the application performance by interfering with inlining" | calling context tree keyed "precisely by individual call sites" | `hpcviewer` calling-context, caller and flat views [Adhianto et al. 2010; HPCToolkit overview] |
| TAU | PDT source, DyninstAPI or manual instrumentation | per-thread/context/node profiles, profiling groups | profile browsers; can emit OTF2 [Shende & Malony 2006; TAU user guide] |
| mpiP | link-time PMPI, statistics only; data is "task-local" until report generation | per-**callsite** aggregates keyed by stack traceback | threshold-filtered text report with message-size histograms [mpiP user guide] |
| Darshan | `LD_PRELOAD` or link-time, light enough for "full time deployment" [Carns et al. 2011] | hashed per-file counters compressed at job end; DXT adds tracing [DXT 2019] | I/O summary reports [Darshan project] |
| Caliper | annotations as "performance data collection as a service," with independent producer/consumer/control roles [Boehme et al. 2016] | **blackboard** updated by annotations, **snapshots** at configured events, pluggable **services** | application-configured via `CALI_CONFIG` [Caliper docs] |

### 5.4 Correctness tools

**Umpire** pioneered centralized runtime deadlock detection, but funnelling tool traces through one
manager limited its scale [Vetter & de Supinski 2000].
**Marmot** combined timeouts with local and global checks: it detects recv-recv but **misses** send-send
deadlock and admits timeout false positives [Hilbrich et al. 2013]. **ISP** has a central scheduler
re-execute *all* send/recv interleavings — best coverage, exponential cost, "not reported to scale to more
than a hundred processes" [Hilbrich et al. 2010]; **DAMPI** distributes that exploration [Vo et al. 2010]. **MUST** checks a single execution on **GTI**, a generic
event-based tool infrastructure, with a graph-based blocking model: no false positives, and 4,096
processes at roughly 34% overhead [Hilbrich et al. 2010; Hilbrich et al. 2013 SC]. **Intel Message Checker** targeted
automated, scalable MPI debugging [DeSouza et al. 2005], and MPICH's **MPE checking library** does
collective argument-consistency checks [MPE README].

MUST's authors separate "tool internal infrastructure and the actual correctness checks," which
predecessors hard-coded; checks needing global knowledge (deadlock, type matching) need a scalable
substrate for tool records, and making that substrate reusable (GTI) is what let MUST scale
[Hilbrich et al. 2012].

### 5.5 Benchmarks and reporting methodology

**OSU Micro-Benchmarks (OMB).** `osu_latency` is a blocking ping-pong reporting **average one-way
latency**; `osu_bw` sends a window of back-to-back `MPI_Isend`s, waits for one reply, and computes
bandwidth "to determine the maximum sustained data rate that can be achieved at the network level";
`osu_allreduce` and peers report average latency per message size [OMB README]. The methodological knobs
matter more than the kernels: `-x` sets **warmup iterations skipped before timing** (default **200**), `-i`
timed iterations (default **10000** small, **1000** large), `-m min:max` the size range, and `-f` full
format with **MIN/MAX latency and iteration count** rather than just the mean [osu_util_options.h].

**SKaMPI** targets "performance portability" with a public cross-platform performance database [Reussner
et al. 2002], contributing a DSL for describing tests, **window-based process synchronization** (aligning
distributed clocks so operations start simultaneously), and **adaptive iterative measurement** repeating a
test "until the current relative standard error is smaller than a predefined maximum" [Reussner et al.
1998]. Schemes differ materially — `mpptest` reports the **minimum of the means** of call batches, OMB
min/max/mean over fixed repetitions, MPIBlib stops on a confidence interval — so experimental design must
be reported, not assumed [Hunold & Carpen-Amarie 2015].
**NetPIPE** is a protocol-independent ping-pong evaluator [Snell et al. 1996] `[UNVERIFIED: exact venue]`;
**IMB** standardizes point-to-point and collective metrics; **mpiBench** targets collectives
`[UNVERIFIED: methodology details for both]`.

**NAS Parallel Benchmarks.** Five kernels and three pseudo-applications from CFD, given as a
"pencil-and-paper" specification (NPB 1) with MPI and OpenMP reference implementations (NPB 2, 3) [Bailey
et al. 1991; NASA NPB]. Problem sizes are **predefined classes** S, W, A, B, C, D, E and F, scaling steeply — CG
rows go 1,400 (S) → 14,000 (A) → 150,000 (C) → 54,000,000 (F) — with class C added "to retain the focus
on high-end supercomputing"; some benchmarks require a power-of-two process count (FT, MG, IS), others a
square (SP, BT) [NPB problem sizes; Bailey et al. 1995].

---

## 6. Determinism, reproducibility, and debugging

### 6.1 Why MPI programs are nondeterministic

**Wildcard receives**: `MPI_ANY_SOURCE` and `MPI_ANY_TAG` make the receive-to-message binding depend on
arrival order, hence on the network — which is why ISP and DAMPI must explore all interleavings, why some
deadlocks are *schedule-dependent*, and why wildcards defeat hardware tag matching [Hilbrich et al. 2013;
Marts et al. 2019]. **Reduction ordering**: floating-point addition is not
associative, so a result depends on the tree shape, which MPI does not fix. **Buffering** is a third
source: whether `MPI_Send` blocks depends on eager limits, so a send-send pair may or may not deadlock.

### 6.2 Reproducible reductions

ReproBLAS's case analysis is the useful part [ReproBLAS]: with fixed data order, process count,
partitioning and a deterministically scheduled reduction tree, results are already reproducible; with
fixed process count and deterministic partitioning, local sums can be sequential, so **the only remaining
source is the reduction tree shape**. The mechanism is **binned** floating-point types:
numbers are split into slices along predefined exponent boundaries, and a `k`-fold binned type carries `k`
accumulators for the largest consecutive nonzero bins seen, making binned addition order-independent
[Ahrens et al. 2020]. `binnedMPI.h` supplies MPI datatypes and operators so a standard `MPI_Allreduce`
becomes reproducible "regardless of the reduction tree shape used by MPI."

### 6.3 Record/replay

**ScalaTrace** interposes via PMPI and applies **bi-level compression**: on-the-fly node-local
compression, then global inter-node compression "upon application completion within the PMPI wrapper for
`MPI_Finalize`," bottom-up over a binary tree "to avoid the creation of local trace files, which would
result in linearly increasing disk space requirements." Repetitive loop events with identical parameters
compress to constant size, and the replay engine "does not actually decompress this trace. Instead, it
interprets the compressed trace on-the-fly," with events annotated for time-preserving deterministic
replay [Noeth et al. 2009; ScalaTrace tech report].

### 6.4 The MPIR process-acquisition interface

MPIR is the *de facto* debugger-attach protocol, standardized retroactively after two decades of use: in
early 1995 TotalView's Jim Cownie with Argonne's Bill Gropp and Rusty Lusk built two interfaces for
MPICH — process acquisition (MPIR) and message-queue access — which became de facto standards
implemented by Compaq, LAM/MPI, Open MPI, Quadrics, SGI and Sun/Oracle [MPI Forum 2018]. The protocol is a
rendezvous via well-known symbols in the **starter process** (`mpirun`): `MPIR_proctable`, an array of
`MPIR_PROCDESC` structs (`host_name`, `executable_name`, `pid`); its size;
`MPIR_debug_state`, 0 before initialization and `MPIR_DEBUG_SPAWNED` after; and `MPIR_Breakpoint()`,
which the starter calls to notify the tool, whereupon the tool reads `MPIR_debug_state` "to process an
MPIR event" [ANL MPI debug]. The mechanism is crude — a well-known symbol, a breakpoint, a shared struct
layout — and that crudeness is why it survived a decade of independent implementations and two commercial
debuggers (TotalView, DDT). MPIR is now superseded by the **PMIx tools API**, "an
alternative and more extensible tool interface," with compatibility preserved by the **MPIR shim module**:
a process launched *between* the debugger and `mpirun` that implements `MPIR_Breakpoint` and extracts the
process table [mpir-to-pmix guide; Open MPI docs, mpir-tools].

---
## 7. Architectural lessons transferable to a non-HPC message-passing runtime

1. **Define a narrow internal "device" interface, implement the user API once above it, and offer a ladder
   of such interfaces at several abstraction levels.** Third parties then replace the
   performance-critical bottom while inheriting all semantics, at their chosen effort/performance point.
   *(MPICH ADI-3 and CH3 [MPICH DevGuide; Liu et al. 2004])*
2. **Separate the matching layer from the transport layer, allow *two* splits, and put a thin multiplexing
   layer between them.** Transports that can match should not be forced through a software matcher
   (`ob1`/BTL versus `cm`/MTL), and a per-device multiplexer makes multi-rail need no protocol change.
   *(Open MPI PML/BML/BTL/MTL [Barrett])*
3. **Distinguish the plugin *instance* (module) from the plugin *implementation* (component), and enforce
   layer order mechanically.** Resource multiplicity becomes the framework's problem, and per-layer
   libraries turn an upward symbol reference into a link error. *(Open MPI MCA [Squyres 2012])*
4. **Make component selection a priority-ordered query with a guaranteed fallback, and register every
   tunable constant as a named, described, discoverable run-time parameter.** A missing plugin then
   degrades rather than fails. *(Open MPI MCA [Squyres 2012])*
5. **Let semantic information flow unmodified to the lowest layer, and give every module a generic
   active-message fallback.** Modules then optimize with full context, and a partial implementation is
   still correct. *(MPICH CH4/MPIDIG [Raffenetti et al. 2017])*
6. **Express locality by choosing a queue rather than branching; give each participant one
   multi-producer/single-consumer lock-free receive queue plus a small-message bypass that may be
   non-general.** Per-pair queues are O(N²) and fastboxes are deliberately restricted to small process
   counts. *(Nemesis [Buntinas et al. 2006; Buntinas et al. 2007])*
7. **Budget overhead in instructions and attribute every one to a requirement.** CH4's 221→16 analysis
   separates mandatory semantic cost from checks that can be made build-time configurable. *(MPICH CH4
   [Raffenetti et al. 2017])*
8. **Negotiate capabilities *and restrictions* at setup, commit to a specialized path, and keep every
   offloaded fast path threshold-driven with an order-preserving software fallback.** OFI's mode bits avoid
   per-operation branching. *(Libfabric [Grun et al. 2015]; UCX [HPC-AC 2018])*
9. **Standardize an interception ABI and design it for tool *composition* from day one.** PMPI's
   one-tool-at-a-time limit forced monolithic tools, so build the next-function chain and per-tool context
   up front. *(PMPI [MPI Forum 1995]; QMPI [Elis et al. 2019])*
10. **Separate the introspection namespace and lifecycle from the data plane's, and expose
    implementation-declared control variables, counters and events with self-describing metadata.** An
    implementation-declared metric list lets internals evolve without breaking tools. *(MPI_T [MPI Forum
    2015; Islam et al. 2016])*
11. **Model traces as states, arrows and events over a (location, time) canvas, factor display attributes
    into shared category objects, store level-of-detail previews, and keep reduction closed under the
    format.** Previews open traces larger than memory, and a filtered Paraver trace is itself a Paraver
    trace. *(SLOG-2 [Jumpshot-4 docs]; OTF2 [Eschweiler et al. 2012])*
12. **Build one measurement substrate for many analysis frontends, and separate tool infrastructure from
    tool policy.** Score-P exists because every tool shipped its own instrumentation, and GTI is why MUST
    outscaled its predecessors. *(Score-P [Knüpfer et al. 2012]; MUST/GTI [Hilbrich et al. 2012])*
13. **Offer an always-on statistical tier alongside full tracing, and account for dropped events.** mpiP
    keeps data task-local until report time and HPCToolkit samples to bound distortion. *(mpiP [mpiP user
    guide]; HPCToolkit [Adhianto et al. 2010])*
14. **Split bootstrap into publish → scoped exchange → on-demand fetch, start wire-up in the background
    before first use, and ship a standard plus reference implementation plus a shim for non-conforming
    hosts.** Direct modex and early wire-up remove the all-to-all and barrier that dominate launch cost.
    *(PMIx [Castain et al. 2017]; MPIR shim [Open MPI docs, mpir-tools])*
15. **Replace the implicit global "world" with isolated, re-creatable sessions built from runtime-queried
    process sets.** A component can then initialize the runtime without global coordination, and allocate
    state only for the peers it actually addresses. *(MPI Sessions [Holmes et al. 2016])*
16. **Localize nondeterminism: make aggregation order-independent in one layer instead of pinning the
    topology everywhere.** Binned accumulators make a reduction bit-reproducible regardless of tree shape,
    leaving the runtime free to retune. *(ReproBLAS [Ahrens et al. 2020])*
17. **Make algorithm selection data rather than code, and generate the API boilerplate from a
    machine-readable specification.** A rules file retunes without a rebuild, and generated bindings are
    what made a whole-API tool prototype tractable. *(MPICH [Zhou et al. 2021]; Open MPI `coll/tuned`
    [Open MPI docs, coll-tuned])*
18. **Publish a versioned ladder of benchmark sizes together with the measurement protocol.** Warmup
    counts, iteration counts and min/median/max reporting are what make numbers comparable across
    implementations. *(OSU Micro-Benchmarks [OMB README]; NAS classes [Bailey et al. 1995]; SKaMPI
    [Reussner et al. 1998])*

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
- **[Schulz & de Supinski 2005]** M. Schulz, B. R. de Supinski. "A Flexible and Dynamic
  Infrastructure for MPI Tool Interoperability." In *Proc. 2005 International Conference on
  Parallel Processing (ICPP 2005)*, pp. 193–202. IEEE, 2005. DOI: 10.1109/ICPP.2005.35.
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
