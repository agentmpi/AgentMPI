# 03a — MPI Point-to-Point Semantics and the Eager/Rendezvous Protocol Split

**Purpose.** Background section for the AgentMPI paper. AgentMPI borrows MPI's
point-to-point *matching* model and its *two-protocol* transfer model:
`eager` → *inline the payload directly into the receiving agent's context
window*; `rendezvous` → *transfer a handle/descriptor, and let the receiver
materialise the payload on demand*. This document establishes the normative
semantics, the implementation mechanics (match lists, queues, thresholds), and
the measured costs, so that the analogy in the paper is precise rather than
decorative.

**Status legend.** `[UNVERIFIED]` marks any claim I could not pin to a primary
source in the time budget. Verbatim standard text is fenced and labelled with
version + section.

---

## 1. Matching Semantics

### 1.1 The matching triple (and the fourth, implicit, field)

MPI point-to-point communication is *not* addressed by channel or by queue
identifier. Every message carries an **envelope**, and a receive operation
specifies a **pattern** over that envelope. In MPI-4.1 the envelope fields are
`source`, `destination`, `tag`, `communicator` [mpi41]. From the receiver's
point of view the *matching triple* is therefore
`(communicator, source, tag)` — `destination` is implicitly the receiver itself.

> **Verbatim (MPI-4.1, §4.2.4 "Blocking Receive"; identical text in MPI-2.2
> §3.2.4 and MPI-3.1 §3.2.4 modulo the editorial `process` → `MPI process`
> rename):**
> "The selection of a message by a receive operation is governed by the value of
> the message envelope. A message can be received by a receive operation if its
> envelope matches the `source`, `tag` and `comm` values specified by the
> receive operation. The receiver may specify a wildcard `MPI_ANY_SOURCE` value
> for `source`, and/or a wildcard `MPI_ANY_TAG` value for `tag`, indicating that
> any source and/or tag are acceptable. It cannot specify a wildcard value for
> `comm`. Thus, a message can be received by a receive operation only if it is
> addressed to the receiving process, has a matching communicator, has matching
> source unless `source=MPI_ANY_SOURCE` in the pattern, and has a matching tag
> unless `tag=MPI_ANY_TAG` in the pattern." [mpi41]

Three structural facts matter for the AgentMPI analogy:

1. **The communicator cannot be wildcarded.** It is a hard namespace boundary —
   an isolation mechanism, not a filter. This is exactly why MPI libraries
   (e.g. collectives built on point-to-point) can be composed safely with user
   code: a library duplicates the communicator and is thereafter immune to tag
   collisions [mpi41]. Contrast: agent harnesses conventionally have *one*
   global namespace of "messages in the conversation", i.e. a single
   communicator, which is why library-vs-user tag collisions (tool call ids
   colliding with orchestration control messages) are endemic.
2. **Send is `push`, receive is `pull`-shaped.** MPI-4.1 remarks on the
   asymmetry explicitly:

   > **Verbatim (MPI-4.1 §4.2.4):** "Note the asymmetry between send and receive
   > operations: A receive operation may accept messages from an arbitrary
   > sender, on the other hand, a send operation must specify a unique receiver.
   > This matches a 'push' communication mechanism, where data transfer is
   > effected by the sender (rather than a 'pull' mechanism, where data transfer
   > is effected by the receiver)." [mpi41]

3. **Tag range.** `tag` is a non-negative integer, and the standard guarantees
   an upper bound of at least 32767 via the attribute `MPI_TAG_UB`; `source` is
   a rank in `{0, ..., n-1} ∪ {MPI_ANY_SOURCE}` [mpi41]. Tags are *flat
   integers*, not structured keys — all structure must be encoded by hand. This
   is the single biggest ergonomic weakness MPI has and the one AgentMPI should
   not copy verbatim.

### 1.2 Order: the non-overtaking rule

This is the load-bearing normative guarantee. Quoted exactly:

> **Verbatim (MPI-4.1, §4.5 "Semantics of Point-to-Point Communication",
> paragraph "Order"):**
> "Messages are nonovertaking: If a sender sends two messages in succession to
> the same destination, and both match the same receive, then this operation
> cannot receive the second message if the first one is still pending. If a
> receiver posts two receives in succession, and both match the same message,
> then the second receive operation cannot be satisfied by this message, if the
> first one is still pending. This requirement facilitates matching of sends to
> receives. It guarantees that message-passing code is deterministic, if MPI
> processes are single-threaded and the wildcard `MPI_ANY_SOURCE` is not used in
> receives. (Some of the calls described later, such as `MPI_CANCEL` or
> `MPI_WAITANY`, are additional sources of nondeterminism.)" [mpi41]

> **Verbatim (MPI-4.1 §4.5, continuing):** "If an MPI process has a single
> thread of execution, then any two communication operations executed by this
> MPI process are ordered." [mpi41]

Note the MPI-3.1 wording is textually identical except for `processes` in place
of `MPI processes` [mpi31], and MPI-1.1 §3.5 already carried the identical
sentence [mpi11] — this guarantee has been stable since 1994.

**Precise reading (this is where papers usually get it wrong).** Non-overtaking
constrains **matching order**, not **completion order**. Two messages sent in
succession that match the same receive pattern are *assigned to receive buffers*
in send order; they may traverse different network paths, arrive out of order,
and *complete* out of order. The MPICH maintainers state this explicitly on the
mailing list: "The standard specifies the order in which messages match, not
complete. They can complete in any order." [mpichdiscuss2020]. Consequently
non-overtaking is a statement about a *deterministic mapping from sends to
receives*, i.e. about **program semantics**, and costs an implementation an
ordered match list — not about wire-level FIFO.

Also note the scope conditions: order is *pairwise per (sender, destination)*.
There is no ordering between messages from different senders, and no ordering
between operations issued by distinct threads of a multithreaded process. MPI-4.1
even flags its own multithreading paragraph as ambiguous:

> **Verbatim (MPI-4.1 §4.5, "Advice to users"):** "The MPI Forum believes the
> following paragraph is ambiguous and may clarify the meaning in a future
> version of the MPI Standard." [mpi41]

followed, after the paragraph, by the mirror-image note in "Advice to
implementors" [mpi41]. That an ambiguity acknowledgement survives into a 2023
release is itself worth citing: *ordering under concurrency is genuinely hard to
specify*, which is the same wall an agent-message protocol hits the moment the
harness is concurrent.

### 1.3 Progress

> **Verbatim (MPI-4.1 §4.5, paragraph "Progress"):**
> "If a pair of matching send and receive operations have been initiated, then
> at least one of these two operations will complete, independently of other
> actions in the system: the send operation will complete, unless the receive is
> satisfied by another message, and completes; the receive operation will
> complete, unless the message sent is consumed by another matching receive that
> was started at the same destination MPI process." [mpi41]

This is a weak, *disjunctive* liveness property: **at least one** of the pair
completes. It is deliberately weak enough to permit an implementation in which
progress only happens while the application is inside an MPI call (see §5).

### 1.4 No fairness

> **Verbatim (MPI-4.1 §4.5, paragraph "Fairness"):**
> "MPI makes no guarantee of fairness in the handling of communication. Suppose
> that a send is started. Then it is possible that the destination MPI process
> repeatedly posts a receive that matches this send, yet the message is never
> received, because it is each time overtaken by another message, sent from
> another source. Similarly, suppose that a receive was started by a
> multithreaded MPI process. Then it is possible that messages that match this
> receive are repeatedly received, yet the receive is never satisfied, because
> it is overtaken by other receives started at this MPI process (by other
> executing threads). It is the programmer's responsibility to prevent
> starvation in such situations." [mpi41]

So: **deterministic matching, but no anti-starvation guarantee.** Starvation
avoidance is pushed entirely to the application. `MPI_ANY_SOURCE` is precisely
the construct that makes starvation possible, and it is also precisely the
construct that makes wildcard-tolerant matching expensive to implement (§2).

### 1.5 Resource limitations (normative acknowledgement of the buffering problem)

> **Verbatim (MPI-4.1 §4.5, paragraph "Resource limitations"):**
> "Any pending communication operation and decoupled MPI activity consumes
> system resources that are limited. Errors may occur when lack of resources
> prevent the execution of an MPI call. High-quality implementations will use a
> (small) fixed amount of resources for each pending send in the ready or
> synchronous mode and for each pending receive. However, buffer space may be
> consumed to store messages sent in standard mode, and must be consumed to
> store messages sent in buffered mode, when no matching receive is available.
> The amount of space available for buffering will be much smaller than program
> data memory on many systems. Then, it will be easy to write programs that
> overrun available buffer space." [mpi41]

This paragraph is the standard's own admission that the eager protocol (§3) has
an unbounded memory footprint in the worst case, and it is the direct analogue of
"unsolicited inlined agent messages can blow the context budget".

## 2. Match-List Implementation: PRQ, UMQ, and the Cost of Linear Scan

### 2.1 The two queues

Because MPI does *not* require that a receive be posted before the matching send
arrives, an implementation must maintain **two ordered lists** per process (per
"matching context"; see below):

- **Posted-receive queue (PRQ)**, a.k.a. *posted receive list / expected queue /
  "receive queue (RQ)"*: receive requests the application has posted that have
  not yet been satisfied.
- **Unexpected-message queue (UMQ)**, a.k.a. *unexpected list / "unexpected
  queue (UQ)"*: messages (or message *headers*, in the rendezvous case) that
  arrived with no matching posted receive.

The invariant is a mutual search:

> "When a message arrives, a queue of expected messages (posted receive
> queue—PRQ) is searched for a match of metadata (source, MPI communicator, and
> tag). Otherwise the metadata is appended to a second queue called the
> unexpected message queue (UMQ). When a receive is posted, the UMQ is searched
> for a match using the same criteria before the operation can be added to the
> PRQ." [groves2021bmm]

Both searches must return the **first** entry satisfying the pattern, in posting
/ arrival order, because that is what the non-overtaking rule of §1.2 demands.
This is why the canonical implementation is a **singly linked list scanned
linearly**: the list *is* the order witness.

### 2.2 Why wildcards make it hard to do better

The obvious optimisation is to hash on `(communicator, source, tag)` and get
O(1) matching. Wildcards break this, because a wildcard receive is a *pattern
that spans buckets* and must retain its ordering position relative to entries in
every bucket it could match:

> "The ability to match wildcard fields further complicates the matching
> process. Even if more complex data structures are used for message matching
> (e.g., a hashmap based off of source, tag, and communicator), those structures
> must account for wildcard entries. Entries with wildcards in the fields being
> hashed must be handled in a separate linked list. Thus, in the limit, this
> degrades to where performance is limited by the number of outstanding messages
> with wildcard fields." [groves2021bmm]

So `MPI_ANY_SOURCE` is not a free convenience: it is the feature that forces the
data structure back toward a global ordered list. Every accelerated matching
scheme in the literature is, at bottom, a scheme for *partitioning the list while
preserving a global order across the partitions in the presence of wildcards*.

**Design point for AgentMPI:** if the protocol admits a wildcard receive
("give me the next message from anyone"), it inherits exactly this cost
structure, including a "wildcard bin" whose length bounds achievable performance.

### 2.3 Known accelerated designs

| Design | Structure | Source |
|---|---|---|
| Baseline | single singly-linked list per queue, linear scan | [groves2021bmm], [levy2019simulation] |
| Open MPI | ~three-level tree of linked lists; leaves are per-`(communicator, source rank)` lists; extra code for wildcards. Cost per match attempt is *not* reliably logarithmic when a queue is large | [levy2019simulation] |
| Binned matching (BMM) | hash/bin on match fields + extended message annotations to preserve order; separate wildcard bin | [flajslik2016matching], [groves2021bmm] |
| Adaptive/dynamic tag matching | choose the matching structure *per rank at runtime* from observed traversal depth, to avoid paying memory for ranks that don't need it | [bayatpour2016tagmatching] |
| Hardware offload | NIC-resident match engine (Mellanox ConnectX-5, Atos/Bull BXI); also enables asynchronous progress | [groves2021bmm] |

Cray/HPE MPICH exposes BMM as a non-default option; the knobs are
`MPICH_USE_BINNING_MSG_MATCH=1`, `MPICH_NUM_POST_RECV_BINS`,
`MPICH_NUM_UNEXPECTED_BINS`, with defaults reported as **four bins for the
receive queue and one bin for the unexpected queue** [groves2021bmm].

### 2.4 Measured numbers

These are the concrete figures worth citing; they are what make the "match list
length is a real scaling variable" claim non-vacuous.

**Flajslik, Dinan & Underwood (ISC 2016), "Mitigating MPI Message Matching
Misery"** [flajslik2016matching] — Hans Meuer Award paper:

- Replaces linked lists with a hash map plus extended message annotations,
  preserving MPI semantics *including wildcards*.
- Up to **3.5x application speedup** on FDS (Fire Dynamics Simulator), attributed
  to the deep queue depths produced by `MPI_Allgather` traffic.
- **10x reduction** in match attempts per message for NAS Integer Sort (`IS`,
  which uses large-message `MPI_Alltoall`); up to **50x** reduction in match
  attempts in the reported sweep over bin counts [nextplatform2016].
- Reported **per-entry search time for LAMMPS (Rhodopsin) of about 30 ns**, as
  quoted by [levy2019simulation].

**Groves et al. (CCPE 2021), "Not all applications have boring communication
patterns: Profiling message matching with BMM"** [groves2021bmm] — instrumented
Cray MPICH, NERSC Cori KNL. *Average comparisons per match* (i.e. mean search
depth), which is the number that actually multiplies the per-entry cost:

| Application | Scale | PRQ avg comparisons/match | UMQ | Wildcards |
|---|---|---|---|---|
| AMReX | 2176 ranks, 2 AMR levels | (PRQ histogram; avg 7 across all ranks) | ~80 avg, peak ~500 | none used |
| Chombo (original math libs) | 512 / 4096 / 16384 ranks | bimodal: subset <20, remainder **40–140** | most <20, minority >50 | wildcard-bin high-water mark up to **25** (unexpected by devs) |
| Chombo (after swapping math libs) | 512 ranks | avg depth **13** (vs **52** before) | — | **zero** wildcard usage |
| E3SM (atmosphere-only, PIO2) | 169 / 323 / 1350 nodes, up to **86,400 ranks** | avg **17 / 15 / 20**; 16–32 ranks peak at **~120** | same avg as PRQ | only rank 0; avg **38** attempts/match in the wild bin, invariant across scale |
| MILC | 32–256 ranks | avg **9→12**, peak **14** | 6–12 | yes; wild-bin avg **9–14** (from `MPI_ANY_SOURCE` in `do_gather`) |

Two findings from this table are directly transferable to AgentMPI:

1. **Match-list length is a property of the software stack, not the
   application.** Swapping a math library in Chombo cut average PRQ depth from
   52 to 13 and eliminated wildcard usage entirely [groves2021bmm]. Users had no
   idea their solver was using `MPI_ANY_SOURCE`. The agent analogue: a tool or
   sub-agent library you did not write determines your matching cost and your
   context pressure.
2. **Depth is extremely rank-skewed.** In E3SM only 16–32 processes out of
   ~86,400 hit ~120-deep searches, and only rank 0 ever used wildcards
   [groves2021bmm]. Matching cost is a *tail* phenomenon, i.e. a straggler
   problem, which is exactly how it becomes a critical-path problem.

**Levy et al. (2019), "Using Simulation to Examine the Effect of MPI Message
Matching Costs on Application Performance"** [levy2019simulation] — validated
simulation, Sandia; this is the important *negative* result:

- Per-entry search cost on modern systems is "**10s of nanoseconds**"; Barrett
  et al. measured **below 10 ns** per entry on modern multicore even for long
  searches, while simpler cores (ARM Cortex-A9) showed per-entry cost growth
  with queue length [levy2019simulation], [barrett2013matching].
- With **100 ns** queue operations, median per-match-attempt cost in the receive
  queue was **93–108 ns** for LAMMPS-lj, CTH-st and MILC, and **250–381 ns** for
  the remaining workloads; in the posted queue, ~**163 ns** (HPCG) and ~**113
  ns** (MILC), versus **33–63 ns** for the others [levy2019simulation].
- Even at **1 µs** per queue operation the performance impact was **<1%** on all
  studied workloads. At **100 µs**: CTH-st 4.9%, LAMMPS-lj 0.4%, miniFE 3.8%
  slower. At **1 ms**: HPCG ~3x, MILC >10x, LULESH >20x slower
  [levy2019simulation].
- **Increasing match queue length by 100x costs <9% on all workloads**; for
  CTH-st, LAMMPS-lj and miniFE, <5% even at **1000x** longer queues. Differences
  between fixed-cost, logarithmic, linear and superlinear queue models are not
  visible until queues hold "more than a few hundred elements"
  [levy2019simulation].
- Multithreading is the real risk: a 27-point stencil with **32 processes x 72
  threads = 2304 threads** showed ~**57% slowdown** at only **1 µs** per-entry
  search cost [levy2019simulation]. Schonbein et al. showed naive
  multithreading can substantially increase match queue lengths
  [levy2019simulation], [schonbein2019matching] `[UNVERIFIED: exact Schonbein
  citation details — cited as ref [10] in levy2019simulation, not independently
  retrieved]`.

**Bayatpour et al. (IEEE Cluster 2016)** [bayatpour2016tagmatching]: tag matching
strategy selected dynamically per process from "the number of request objects
that must be traversed before hitting on the required one". On HPCG, reported
**20%, 32%, and up to 2x** improvement in tag matching time over the default,
bin-based, and rank-based schemes respectively, at minimal memory overhead;
results normalised at **512 processes**. Best Paper Nominee.

**Klenk & Fröning (ISC 2017), "An Overview of MPI Characteristics of Exascale
Proxy Applications"** [klenk2017overview]: dynamic MPI characterisation of **18
exascale proxy applications** (LNCS 10266, pp. 217–236). Message-size
distributions are strongly skewed toward small messages, which is what makes the
eager-limit placement (§3) consequential. `[UNVERIFIED: specific per-application
match-list-length or message-size percentile numbers from this paper — I could
not retrieve the full text within budget; cite only the qualitative claim, or
retrieve the PDF before submission.]`

### 2.5 Synthesis for the paper

The honest summary of the scalability literature is a **tension**, and the paper
should present it as such rather than as a one-sided "matching is a bottleneck"
story:

- *Structurally*, matching is O(queue depth) per attempt and depth grows with
  concurrency, unexpected-message backlog, and wildcard use.
- *Empirically*, on today's HPC workloads the absolute cost is small: 10s of ns
  per entry, depths in the 10s (sometimes low 100s), so matching is a few percent
  at worst [levy2019simulation].
- *But* the regimes where it explodes are exactly the regimes that resemble an
  agent harness: many concurrent logical senders per endpoint (threads), irregular
  and adaptive communication patterns, deep unexpected backlogs, and wildcard
  receives. For AgentMPI the per-entry cost is not 10 ns — a "match attempt"
  against an agent message may involve semantic comparison, so the constant is
  6–9 orders of magnitude larger, and the *asymptotics that HPC can afford to
  ignore become the dominant term*.

## 3. Eager vs. Rendezvous — the Core Section

### 3.1 The standard does not mandate either protocol

Critically for the paper's framing: **eager and rendezvous are not in the MPI
standard.** They are implementation techniques that the standard deliberately
leaves open, and it says so in an *Advice to implementors*:

> **Verbatim (MPI-4.1 §4.4 "Communication Modes", Advice to implementors;
> identical in MPI-3.1 §3.4):**
> "A possible communication protocol for the various communication modes is
> outlined below.
> ready send: The message is sent as soon as possible.
> synchronous send: The sender sends a request-to-send message. The receiver
> stores this request. When a matching receive is posted, the receiver sends
> back a permission-to-send message, and the sender now sends the message.
> standard send: First protocol may be used for short messages, and second
> protocol for long messages.
> buffered send: The sender copies the message into a buffer and then sends it
> with a nonblocking send (using the same protocol as for standard send).
> Additional control messages might be needed for flow control and error
> recovery. Of course, there are many other possible protocols." [mpi41]

That single sentence — *"First protocol may be used for short messages, and
second protocol for long messages"* — is the entire normative basis of the
eager/rendezvous split. Everything else is engineering. Intel's own training
material states the point plainly: "It is an implementation technique, it is not
part of the MPI standard" [intelmpi-eager].

The corresponding *permission* is in the definition of standard mode:

> **Verbatim (MPI-4.1 §4.4):** "In this mode, it is up to MPI to decide whether
> outgoing messages will be buffered. MPI may buffer outgoing messages. In such
> a case, the send call may complete before a matching receive is invoked. On
> the other hand, buffer space may be unavailable, or MPI may choose not to
> buffer outgoing messages, for performance reasons. In this case, the send call
> will not complete until a matching receive has been posted, and the data has
> been moved to the receiver." [mpi41]

and the rationale is an explicit *portability* argument:

> **Verbatim (MPI-4.1 §4.4, Rationale):** "The reluctance of MPI to mandate
> whether standard sends are buffering or not stems from the desire to achieve
> portable programs. Since any system will run out of buffer resources as
> message sizes are increased, and some implementations may want to provide
> little buffering, MPI takes the position that correct (and therefore,
> portable) programs do not rely on system buffering in standard mode.
> Buffering may improve the performance of a correct program, but it doesn't
> affect the result of the program." [mpi41]

**This is the deepest lesson for AgentMPI.** MPI's answer to "should the payload
be inlined or handed over as a handle?" is: *the protocol refuses to say, and
declares any program that depends on the answer to be incorrect.* An AgentMPI
that wants both eager-inlining and rendezvous-handles should adopt the same
discipline — programs must be correct under either, with the choice being a
performance/context-budget decision made by the runtime.

### 3.2 Eager

**Mechanism.** The sender transmits the envelope *and the full payload*
unsolicited, without any prior indication that the receiver has posted a matching
receive. On arrival the receiver searches the PRQ; on a hit it copies into the
user buffer, on a miss it **must** buffer the payload in a pre-posted /
runtime-owned buffer and enqueue it on the UMQ.

> "In the 'eager' protocol for 'short' messages, the entire message is sent to
> the receiver immediately to minimize latency. Since the receiver may not have
> posted receives that match the sends, the receiver posts buffers to capture
> unexpected messages. This ensures that the data that is sent across the
> network is not lost." [barrett2013reducing]

**Cost:** receiver-side memory, plus one memory-to-memory copy (buffer → user
buffer) on the unexpected path. **Benefit:** minimum latency; a single network
traversal; and, crucially, the sender does not have to wait for the receiver to
do anything.

### 3.3 Rendezvous

**Mechanism.** The sender sends only a header — a *request-to-send* (RTS) —
carrying the envelope plus enough information to fetch the payload later. The
receiver, once a matching receive is known to be posted, replies *clear-to-send*
(CTS) or issues an RDMA `GET` directly. Then the bulk transfer happens, usually
zero-copy RDMA straight into the user's receive buffer.

> "In contrast, the rendezvous protocol for long messages simply sends a header
> and transfers the data when a matching receive is known to be posted. This
> requires a round-trip across the network to transfer a message — even if the
> receiver has already posted the matching receive." [barrett2013reducing]

Variants matter for the analogy:

- **Sender-initiated (RTS/CTS + RDMA write).** Classic.
- **Receiver-initiated / RDMA-read based.** The receiver pulls with an RDMA
  `GET`, removing a control round trip and enabling asynchronous progress
  [sur2006rdmaread].
- **Hybrid / dual-threshold three-protocol.** Barrett & Hemmert propose an
  *"eager medium"* protocol between eager-short and rendezvous-long: medium
  messages are sent eagerly but the receiver is permitted to **drop** an
  unexpected medium message and fall back to rendezvous, so the sender
  retransmits [barrett2013reducing]. Yuan et al. similarly define `EAGER`,
  `HYBRID`, `SEND_RNDV`, `RECV_RNDV` with two thresholds
  (`EAGER_THRESHOLD`, `HYBRID_THRESHOLD`) [yuan2009maximizing].

**Cost:** at minimum one extra network round trip, *paid even when the receive
was already posted*. **Benefit:** bounded receiver memory; zero-copy; unexpected
large messages consume only a header slot in the UMQ.

### 3.4 The eager limit / crossover threshold, and *why* it exists

The crossover is where the extra round trip stops mattering relative to transfer
time. Barrett & Hemmert give the model directly: the eager and rendezvous
transfer times "differ by twice the network latency (2 × L)", so

- `T_eager ≈ L + size/BW`
- `T_rndv ≈ 3L + size/BW` (i.e. `T_eager + 2L`)

and therefore

> "From a performance perspective, the optimal message length to cross from short
> to long messages is the point at which the latency of a round-trip network
> delay is negligible in terms of the total transfer time."
> [barrett2013reducing]

Equivalently: **the threshold tracks the network's bandwidth–delay product
(BDP)**. Since bandwidth grows much faster than latency falls, the BDP grows, so
the *performance-optimal* threshold grows, so *eager buffer memory* grows — and
memory per core is not growing. That is the central scaling pathology
[barrett2013reducing].

Their concrete numbers:

- Simulated network: **2.5 GB/s** with a **2.8 µs** round trip → round-trip BDP
  of **7 KB**. Setting the switch at **4 KB** (about half the BDP) produced "a
  dramatic drop in the ping-pong bandwidth at the protocol switch"; even at
  **32 KB** (over 4x the BDP) there was still a noticeable dip right after the
  switch [barrett2013reducing]. **This is the reason for the notorious "bandwidth
  notch" in MPI ping-pong curves.**
- Exascale projection: 500 ns best-case nearest-neighbour latency, but buffers
  must be sized for 1–2 µs one-way, putting round-trip BDP between **200 KB**
  (100 GB/s × 2 µs) and **1 MB** (250 GB/s × 4 µs) [barrett2013reducing].
- With 256k nodes, unexpected buffer count scaling as `N·log2(P)` for `N` in
  8–64, and ~10 ranks per node, "the buffer space required per node when using an
  optimized eager/rendezvous cross-over could easily reach **gigabytes**"
  [barrett2013reducing].
- Trade-off sweep for the three-protocol design: with the first threshold at
  **4 KB**, up to **50%** of messages can be unexpected and the
  dual-threshold design still wins; by **32 KB** messages, the plain
  two-protocol design is superior if only **25%** are unexpected; and at even
  **6.25%** unexpected the two designs break even at just under **128 KB**
  [barrett2013reducing].

### 3.5 Actual default thresholds in real implementations

**Take the "typical range" claim from a citable source:**

> "In modern interconnects, the typical threshold between eager and rendezvous
> messages is between 4 KB and 32 KB. On some networks, the default is higher
> (e.g. 128 KB on the Cray Seastar interconnect used in the Cray XT5 due to the
> particularly high bandwidth delay product), but 4 KB to 32 KB represents the
> current typical range." [barrett2013reducing]

Per-implementation, with sources and caveats:

| Implementation | Knob | Default | Source / caveat |
|---|---|---|---|
| **MPICH (CH4/OFI)** | `MPIR_CVAR_CH4_OFI_EAGER_MAX_MSG_SIZE` | `-1` = "whatever the provider gives (which might be unlimited for socket provider)" | CVAR description in `ofi_init.c` [mpich-cvars]. So there is *no* portable MPICH default; it is delegated to libfabric. |
| **MPICH (CH4/OFI, active-message path)** | — | OFI has "a provider independent eager limit of **16 KB**" | MPICH PR #4791 discussion [mpich-pr4791]. |
| **MPICH (CH4/OFI native RNDV)** | `MPIR_CVAR_CH4_OFI_EAGER_THRESHOLD` | unset by default; setting it *enables* the native RNDV path (protocols: pipeline, read, write, direct) | MPICH release notes [mpich-news]. |
| **Open MPI (openib BTL)** | `btl_openib_eager_limit` | **12288 bytes (12 KB)** | Open MPI OpenFabrics tuning FAQ [ompi-faq-of]; corroborated by [lccanon2016ompi]. `btl_openib_rndv_eager_limit` and `btl_openib_memalign_threshold` default to the same value; `btl_openib_max_send_size` defaults to 65536. Note: the `openib` BTL is deprecated in current Open MPI in favour of UCX; `[UNVERIFIED]` for a current UCX-based default (UCX has its own `UCX_RNDV_THRESH`, default `auto`). |
| **MVAPICH2 (OFA-IB-CH3)** | `MV2_IBA_EAGER_THRESHOLD` | "Host Channel Adapter (HCA) dependent (**12 KB for ConnectX HCAs**)"; in the older table, "Architecture dependent (**12 KB for IA-32**)" | MVAPICH2 2.3.7 User Guide §11.24 and §12.5 [mvapich2ug]. Should be set equal to `MV2_VBUF_TOTAL_SIZE`; the MVAPICH team's tuning advice uses **131072 (128 KB)** for both [mvapich-bestpractice]. `MPIR_CVAR_IBA_EAGER_THRESHOLD` reports default `-1`, i.e. resolved at runtime [mvapich-envvar]. |
| **MVAPICH2 (intra-node shared memory)** | `MV2_SMP_EAGERSIZE` | "Architecture dependent" — the eager→rendezvous switch for intra-node transfers | MVAPICH2 2.3.7 User Guide §11.105 [mvapich2ug]. Note **two separate thresholds**, one per transport. |
| **Intel MPI (≤2018)** | `I_MPI_EAGER_THRESHOLD` | **262144 bytes (256 KB)**, and identical across platforms (unlike MVAPICH2/Open MPI) | RWTH tuning deck [rwth-impi-tips] and Intel Community confirmation of the documented 256 kB default [intel-community-eager]; Intel's own deck notes the default "could be platform specific (MVAPICH2, OpenMPI) or identical for all platforms (IMPI)" [intelmpi-eager]. |
| **Intel MPI (≥2019)** | — | **Removed.** "Since Intel(R) MPI Library version 2019 environment variable `I_MPI_EAGER_THRESHOLD` is not supported, please use corresponding libfabric controls instead" | Intel release-notes text as quoted in [intel-community-eager]. |
| **Intel MPI intra-node** | `I_MPI_INTRANODE_EAGER_THRESHOLD` | defaults to `I_MPI_EAGER_THRESHOLD` (~256 KB) | [rwth-impi-tips]. |
| **Cray/HPE MPICH (matching, not threshold)** | `MPICH_USE_BINNING_MSG_MATCH`, `MPICH_NUM_POST_RECV_BINS`, `MPICH_NUM_UNEXPECTED_BINS` | binning off by default; when on, **4** receive bins and **1** unexpected bin | [groves2021bmm]. |

**Observation worth putting in the paper:** the industry has converged on
"12 KB, HCA-dependent" for RDMA fabrics and "delegate to the transport layer"
for the newest stacks (MPICH→libfabric, Intel MPI→libfabric, Open MPI→UCX). The
threshold has migrated *out of* the message-passing layer entirely. The AgentMPI
analogue: the eager-inline vs. handle decision will not stay in the protocol
spec; it will migrate into whatever layer owns the context budget.

### 3.6 Unexpected-message memory blowup at scale

The failure mode: eager sends with no matching receive posted accumulate in the
UMQ, consuming receiver memory proportional to (number of peers) × (in-flight
depth) × (eager limit).

- The standard itself warns of it (§1.5 above): "it will be easy to write
  programs that overrun available buffer space" [mpi41].
- Quantitatively: buffer requirement grows with BDP *and* (at least
  logarithmically, empirically sometimes linearly) with node count, reaching
  gigabytes per node in exascale projections [barrett2013reducing].
- Intel's training deck states the operational consequence: eager "can cause
  memory exhaustion / program termination when receive process buffer is
  exceeded" [intelmpi-eager].
- Second-order cost: a deep UMQ *also* lengthens the match list, so eager
  buffering and matching cost are coupled — every posted receive must scan the
  UMQ (§2.1). Deep unexpected queues are exactly the AMReX case with ~80 average
  and ~500 peak comparisons per match in the UMQ [groves2021bmm].

This is the single most transferable result in this document: **unsolicited
eager delivery converts a latency win into an unbounded receiver-side memory
liability, and the liability grows with the number of peers.** Replace "receiver
memory" with "receiving agent's context window" and the argument is unchanged,
except that the context window is ~4–6 orders of magnitude smaller in units of
"messages it can hold" and cannot be paged.

### 3.7 Credit-based flow control

Because eager buffers are finite, implementations throttle senders with
**credits**: a sender may have at most *k* unacknowledged eager messages
outstanding to a given peer; receiving/reposting a buffer returns a credit;
running out of credits forces the sender to block, or to demote the message to
rendezvous, or to piggyback credit-return on other traffic. The standard
anticipates this in the Advice to implementors of §4.4: "Additional control
messages might be needed for flow control and error recovery" [mpi41].

Concrete instances:

- **MVAPICH2 shared receive queue (SRQ) with flow control.** `MV2_SRQ_SIZE`
  default **256** buffers, doubling on each SRQ-limit event up to
  `MV2_SRQ_MAX_SIZE` default **4096** (recommended **8192** for very large
  process counts); `MV2_SRQ_LIMIT` is the low-water mark: "If the number of
  available work entries on the SRQ drops below this limit, the flow control
  will be activated." Lowering it reduces interrupt count [mvapich2ug].
  MVAPICH2 lists "Shared Receive Queue (SRQ) with flow control" as a
  memory-reduction feature [mvapich2ug].
- **Open MPI openib BTL** sizes its eager buffer pools as
  `2 × btl_openib_free_list_max × (btl_openib_eager_limit + overhead)`, with
  `btl_openib_free_list_max` defaulting to `-1` = **unbounded** ("Open MPI will
  try to allocate as many registered buffers as it needs"), plus per-peer eager
  RDMA buffers `btl_openib_eager_rdma_num` (default **16** peers) ×
  `btl_openib_max_eager_rdma` × eager_limit; a new eager-RDMA buffer set is
  created for a peer on receiving the `btl_openib_eager_rdma_threshold`'th
  message from it [ompi-faq-of]. Note the *adaptive* structure: peers that talk
  a lot get promoted to dedicated eager buffers.
- The Barrett & Hemmert "eager medium" protocol is effectively **credit-free
  flow control by discard-and-retry**: the receiver may drop an unexpected
  medium message and the sender re-sends via rendezvous [barrett2013reducing].
  This is a *very* attractive design for AgentMPI: attempt the inline, and if the
  receiver has no context room, drop it and fall back to a handle.

### 3.8 `MPI_Bsend` / `MPI_Buffer_attach`: user-managed eager

Buffered mode is MPI's *explicit, user-visible* eager buffer: the application
donates memory and thereby *guarantees* local completion of sends.

- `MPI_BUFFER_ATTACH(buffer, size)`:
  > **Verbatim (MPI-2.2 §3.6; retained in MPI-4.1 §4.6):** "Provides to MPI a
  > buffer in the user's memory to be used for buffering outgoing messages. The
  > buffer is used only by messages sent in buffered mode. Only one buffer can be
  > attached to a process at a time." [mpi22]
- `MPI_BUFFER_DETACH(buffer_addr, size)`:
  > **Verbatim (MPI-2.2 §3.6):** "Detach the buffer currently associated with
  > MPI. The call returns the address and the size of the detached buffer. This
  > operation will block until all messages currently in the buffer have been
  > transmitted. Upon return of this function, the user may reuse or deallocate
  > the space taken by the buffer." [mpi22]

  Note the idiom in the standard's own Example 3.11: `detach` then immediately
  `attach` the same buffer is the sanctioned way to **drain** ("Buffer size
  reduced to zero" / flush semantics) [mpi22].
- **`MPI_Bsend` is *local*.** From §4.4: a buffered send "may complete before a
  matching receive is posted. However, unlike the standard send, this operation
  is local, and its completion does not depend on the occurrence of a matching
  receive... An error will occur if there is insufficient buffer space" [mpi41].
  And per §4.5, "A buffered send operation that cannot complete because of a lack
  of buffer space is erroneous" — i.e. overflow is an *error*, not
  backpressure, whereas standard mode "will merely block, waiting for buffer
  space to become available or for a matching receive to be started" [mpi41].
  The standard explicitly prefers the blocking behaviour, giving the
  producer/consumer example: "If standard sends are used, then the producer will
  be automatically throttled" [mpi41].
- **Accounting is normatively specified.** MPI-2.2 §3.6.1 "Model Implementation
  of Buffered Mode" defines a circular queue of *pending message entries* (PME),
  each holding a request handle, a next pointer and the packed message data; a
  `MPI_Bsend` first garbage-collects completed entries from head toward tail,
  then computes the entry size as `MPI_PACK_SIZE(count, datatype, comm)` plus
  `MPI_BSEND_OVERHEAD`, and raises a buffer-overflow error if no contiguous space
  of that size exists [mpi22]. `MPI_BSEND_OVERHEAD` is defined as an upper bound
  on the fixed per-message overhead [mpi22]. The standard binds the
  implementation to this model: "An MPI implementation is required to do no worse
  than implied by this model" [mpi41].
- **Interaction with cancel (foreshadowing §4.5).** MPI-2.2 warns: "Successful
  return of `MPI_WAIT` after a `MPI_IBSEND` implies that the user send buffer can
  be reused — i.e., data has been sent out or copied into a buffer attached with
  `MPI_BUFFER_ATTACH`. Note that, at this point, we can no longer cancel the send.
  If a matching receive is never posted, then the buffer cannot be freed. This
  runs somewhat counter to the stated goal of `MPI_CANCEL`" [mpi22].
- If no `MPI_BUFFER_DETACH` occurs before `MPI_FINALIZE`, "the `MPI_FINALIZE`
  implicitly supplies the `MPI_BUFFER_DETACH`" [mpi22].

**Why this matters for AgentMPI.** `MPI_Bsend` is the closest existing analogue
of "I will pay, from my own explicitly budgeted pool, for the privilege of
inlining this message and not waiting for the recipient." Its design lessons:
(a) a single global pool per process is too coarse (MPI allows exactly one
attached buffer), (b) overflow-as-error is worse than overflow-as-backpressure,
(c) the accounting must be *specified*, including per-message overhead, or users
cannot size the pool.

## 4. Communication Modes and Completion

### 4.1 The four send modes

MPI has **one** receive but **four** sends. The mode is signalled by a one-letter
prefix: `B` buffered, `S` synchronous, `R` ready; no prefix = standard
[mpi41].

| Mode | Call | May start before matching recv posted? | Completion is **local**? | Completion tells you what? |
|---|---|---|---|---|
| Standard | `MPI_Send` | yes | **no** ("non-local") | send buffer reusable; *nothing* about receiver |
| Buffered | `MPI_Bsend` | yes | **yes** (local) | send buffer reusable; message is in *your* attached buffer |
| Synchronous | `MPI_Ssend` | yes | **no** (non-local) | receiver "has started executing the matching receive" |
| Ready | `MPI_Rsend` | **no** — erroneous, outcome undefined | (as standard/synchronous) | only that the send buffer can be reused |

Verbatim anchors for the two that matter most:

> **Verbatim (MPI-4.1 §4.4):** "Thus, a send in standard mode can be started
> whether or not a matching receive has been posted. It may complete before a
> matching receive is posted. The standard mode send is non-local: successful
> completion of the send operation may depend on the occurrence of a matching
> receive." [mpi41]

> **Verbatim (MPI-4.1 §4.4):** "A send that uses the synchronous mode can be
> started whether or not a matching receive was posted. However, the send will
> complete successfully only if a matching receive is posted, and the receive
> operation has started to receive the message sent by the synchronous send.
> Thus, the completion of a synchronous send not only indicates that the send
> buffer can be reused, but it also indicates that the receiver has reached a
> certain point in its execution, namely that it has started executing the
> matching receive. If both sends and receives are blocking operations then the
> use of the synchronous mode provides synchronous communication semantics: a
> communication does not complete at either end before both processes rendezvous
> at the communication. A send executed in this mode is non-local." [mpi41]

> **Verbatim (MPI-4.1 §4.4):** "A send that uses the ready communication mode
> may be started only if the matching receive is already posted. Otherwise, the
> operation is erroneous and its outcome is undefined. On some systems, this
> allows the removal of a hand-shake operation that is otherwise required and
> results in improved performance." [mpi41]

Two subtleties that matter for the analogy:

1. **`MPI_Ssend` is the only mode that gives the sender information about the
   receiver's *program state*.** It is a synchronisation primitive disguised as a
   send. `MPI_Rsend` is its dual: the sender *asserts* receiver state and the
   implementation may skip the handshake. In a correct program `MPI_Rsend` is
   substitutable by `MPI_Send` with no semantic change: "In a correct program,
   therefore, a ready send could be replaced by a standard send with no effect on
   the behavior of the program other than performance" [mpi41]. The same is true
   in reverse: "A standard send can be implemented as a synchronous send. In such
   a case, no data buffering is needed. However, users may expect some buffering"
   [mpi41].
2. **The "local vs non-local" distinction is the real semantic axis**, not
   blocking vs nonblocking. *Local* means completion depends only on this
   process's own actions. Only buffered mode is local, and it is local precisely
   because the user pre-paid with memory.

### 4.2 Blocking vs nonblocking, and `MPI_Request`

Blocking means: on return, the buffer may be reused; it does **not** mean the
message was delivered, and it does **not** mean a receive was matched. This is
the most persistent confusion in MPI teaching, and MPI's own text is careful:

> **Verbatim (MPI-4.1 §4.4, opening):** "The send call described in Section
> Blocking Send is blocking: it does not return until the message data and
> envelope have been safely stored away so that the sender is free to modify the
> send buffer. The message might be copied directly into the matching receive
> buffer, or it might be copied into a temporary system buffer." [mpi41]

Nonblocking operations (`MPI_Isend`, `MPI_Ibsend`, `MPI_Issend`, `MPI_Irsend`,
`MPI_Irecv`) return an `MPI_Request` handle immediately. Terminology, verbatim:

> **Verbatim (MPI-2.2 §3.7.3, retained in later versions):** "A null handle is a
> handle with value `MPI_REQUEST_NULL`. A persistent request and the handle to it
> are inactive if the request is not associated with any ongoing communication.
> A handle is active if it is neither null nor inactive. An empty status is a
> status which is set to return tag = `MPI_ANY_TAG`, source = `MPI_ANY_SOURCE`,
> error = `MPI_SUCCESS`, and is also internally configured so that calls to
> `MPI_GET_COUNT` and `MPI_GET_ELEMENTS` return count = 0 and
> `MPI_TEST_CANCELLED` returns false." [mpi22]

Note the *status asymmetry*: for a **send** request, the status fields are
undefined except the error field (when `MPI_ERR_IN_STATUS`) and
`MPI_TEST_CANCELLED` [mpi22]. Only receives yield source/tag/count. This is
because the sender never learns *which* receive matched it — matching state lives
entirely at the receiver.

### 4.3 The completion family

- `MPI_Wait(request, status)` — blocks until that one request completes;
  deallocates the request (sets it to `MPI_REQUEST_NULL`) unless persistent.
- `MPI_Test(request, flag, status)` — local, returns immediately with `flag`.
- `MPI_Waitany` / `MPI_Testany` — completes exactly one of an array; returns its
  index. **This is a source of nondeterminism**, and the standard names it as
  such alongside `MPI_CANCEL` in §4.5 [mpi41].
- `MPI_Waitall` / `MPI_Testall` — all of an array.
- `MPI_Waitsome` / `MPI_Testsome` — completes *at least one*, returns the set.
  `Waitsome` is the fairness-friendly variant: `Waitany` can starve a request
  that keeps losing, whereas `Waitsome` reports everything currently completable.
  Given MPI's explicit no-fairness position (§1.4), `Waitsome` is the primitive
  you use to *build* fairness yourself.
- Error reporting differs: the multiple-completion calls may return
  `MPI_ERR_IN_STATUS`, pushing per-request errors into the array of statuses.

**For AgentMPI:** this five-way family is the vocabulary for "how does an
orchestrator wait on N in-flight sub-agents?" and the `any`/`all`/`some`
distinction maps exactly onto the (first-to-finish / barrier / harvest-all-ready)
patterns that agent harnesses reinvent ad hoc.

### 4.4 Persistent requests

`MPI_Send_init`, `MPI_Bsend_init`, `MPI_Ssend_init`, `MPI_Rsend_init`,
`MPI_Recv_init` create an **inactive** request binding the full argument list
(buffer, count, datatype, peer, tag, comm) once; `MPI_Start`/`MPI_Startall`
activates it; completion returns it to inactive, ready to be started again;
`MPI_Request_free` destroys it. The point is to amortise argument checking,
datatype processing, and (in RDMA implementations) memory registration and
match-list setup across many identical transfers — the classic use is a halo
exchange in a time-stepping loop.

MPI-4.0 generalised this to **partitioned communication** (`MPI_Psend_init`,
`MPI_Precv_init`, `MPI_Pready`, `MPI_Parrived`), where a persistent buffer is
split into partitions that can be filled and declared ready independently — the
explicit motivation being multithreaded senders contributing to one logical
message without inflating the match list [mpi41].

**For AgentMPI:** persistent requests are the analogue of a *pre-negotiated
channel between two agents* — the matching triple, the schema, and the transfer
mode are agreed once and then reused, so the per-message overhead is only the
payload.

### 4.5 `MPI_Cancel` and why it is notoriously hard

> **Verbatim (MPI-2.2 §3.8, retained in MPI-4.1 §4.8):** "A call to `MPI_CANCEL`
> marks for cancellation a pending, nonblocking communication operation (send or
> receive). The cancel call is local. It returns immediately, possibly before the
> communication is actually canceled. It is still necessary to complete a
> communication that has been marked for cancellation, using a call to
> `MPI_REQUEST_FREE`, `MPI_WAIT` or `MPI_TEST`." [mpi22]

> **Verbatim (MPI-2.2 §3.8):** "Either the cancellation succeeds, or the
> communication succeeds, but not both. If a send is marked for cancellation,
> then it must be the case that either the send completes normally, in which case
> the message sent was received at the destination process, or that the send is
> successfully canceled, in which case no part of the message was received at the
> destination. Then, any matching receive has to be satisfied by another send. If
> a receive is marked for cancellation, then it must be the case that either the
> receive completes normally, or that the receive is successfully canceled, in
> which case no part of the receive buffer is altered. Then, any matching send has
> to be satisfied by another receive." [mpi22]

Why this is brutal to implement — enumerate precisely, because the same
difficulties recur for "cancel this agent's in-flight message":

1. **It demands a distributed atomic decision, but is specified as a *local*
   call.** Cancelling a *send* requires knowing whether any byte reached the
   destination. That is receiver-side knowledge. The standard nonetheless says
   the cancel call is local and returns immediately, so the implementation must
   run a hidden protocol and resolve it later at `MPI_Wait`.
2. **All-or-nothing on the receive buffer.** "No part of the receive buffer is
   altered" forbids partial delivery. With eager protocols the payload may
   already be in a runtime buffer (fine — nothing user-visible altered), but with
   rendezvous RDMA the NIC may already be writing directly into the user buffer,
   which cannot be un-written. Implementations therefore typically refuse to
   cancel once the rendezvous transfer has begun.
3. **The matching state must be rolled back consistently.** "Any matching
   receive has to be satisfied by another send" means the cancelled operation
   must be removed from the match list *without* violating the non-overtaking
   rule for the surviving operations. Cancelling a wildcard receive that has
   already provisionally matched is the nasty case.
4. **`MPI_CANCEL` is itself listed as a source of nondeterminism** in §4.5
   [mpi41] — it can change which send matches which receive, so it interacts with
   the very determinism guarantee that motivates the ordered match list.
5. **It conflicts with buffered mode.** "Successful return of `MPI_WAIT` after a
   `MPI_IBSEND` implies that the user send buffer can be reused... Note that, at
   this point, we can no longer cancel the send. If a matching receive is never
   posted, then the buffer cannot be freed. This runs somewhat counter to the
   stated goal of `MPI_CANCEL` (always being able to free program space that was
   committed to the communication)" [mpi22]. So the mechanism designed to reclaim
   resources cannot reclaim the resource most likely to be stuck.
6. **It may require asynchronous execution inside a peer.** The standard's own
   advice to implementors notes that although cancel is local, "If processing is
   required on another process, this should be transparent to the application
   (hence the need for an interrupt and an interrupt handler)" [mpi22].
7. **Semantics require checking cancellation *before* other status fields.** "If
   a receive operation might be canceled then one should call
   `MPI_TEST_CANCELLED` first, to check whether the operation was canceled, before
   checking on the other fields of the return status" [mpi22] — a footgun by
   construction.
8. **Persistent requests survive cancellation.** "A successful cancellation
   cancels the active communication, but not the request itself" [mpi22] — the
   request returns to inactive and can be restarted, so cancellation is a
   *state transition*, not a destruction.

Practical consequence: `MPI_Cancel` support in real implementations is partial
and, for sends, frequently amounts to "we will let it complete". `[UNVERIFIED:
specific per-implementation statements about refusing to cancel sends — widely
reported on MPICH/Open MPI issue trackers and mailing lists but not retrieved
here; verify before asserting in the paper.]`

**For AgentMPI:** cancellation of an in-flight agent message has exactly the
same shape and exactly the same trap. If eager = "inlined into the receiver's
context", then *cancellation after eager delivery is impossible in principle* —
you cannot un-read a token from a context window. Cancel is therefore only
meaningful in the rendezvous case, before materialisation. This is a genuinely
novel and defensible claim for the paper: **the eager/rendezvous choice
determines whether cancellation is expressible at all.**

### 4.6 `MPI_Probe` / `MPI_Iprobe`, and the race that `MPI_Mprobe` fixed

**What probe does.**

> **Verbatim (MPI-2.2 §3.8, retained in later versions):**
> "`MPI_IPROBE(source, tag, comm, flag, status)` returns flag = true if there is
> a message that can be received and that matches the pattern specified by the
> arguments source, tag, and comm. The call matches the same message that would
> have been received by a call to `MPI_RECV(..., source, tag, comm, status)`
> executed at the same point in the program, and returns in status the same value
> that would have been returned by `MPI_RECV()`. Otherwise, the call returns flag
> = false, and leaves status undefined." [mpi22]

The canonical use is **receiving a message of unknown size**: probe, read the
count out of `status` via `MPI_Get_count`, `malloc` a buffer of that size, then
`MPI_Recv`.

**Probe's own progress guarantee** (note it is conditional, and note what the
condition reveals):

> **Verbatim (MPI-3.1 §3.8.1 "Probe", Advice to implementors):**
> "The MPI implementation of `MPI_PROBE` and `MPI_IPROBE` needs to guarantee
> progress: if a call to `MPI_PROBE` has been issued by a process, and a send
> that matches the probe has been initiated by some process, then the call to
> `MPI_PROBE` will return, unless the message is received by another concurrent
> receive operation (that is executed by another thread at the probing process).
> Similarly, if a process busy waits with `MPI_IPROBE` and a matching message has
> been issued, then the call to `MPI_IPROBE` will eventually return flag = true
> unless the message is received by another concurrent receive operation or
> matched by a concurrent matched probe." [mpi31]

**The race, precisely.** Probe is a **stateless query**: it *inspects* the match
list but does **not** consume, reserve, or lock the message it reports. The
message remains "the earliest pending message" and remains eligible for matching
by *any* subsequent receive in the process — including one issued by a different
thread. So the sequence

```
Thread A: MPI_Probe(ANY_SOURCE, ANY_TAG, comm, &status);   // reports message M
Thread A: MPI_Get_count(&status, dtype, &n);               // n = size of M
Thread A: buf = malloc(n * sizeof(elem));
Thread A: MPI_Recv(buf, n, dtype, status.MPI_SOURCE, status.MPI_TAG, comm, ...);
```

has a window between the probe and the receive. Because the message queue is
**global to the MPI process, not per-thread**, another thread `B` executing its
own probe+recv (or a bare `MPI_Recv` with the same pattern) can match and consume
`M` in that window. Thread A's `MPI_Recv` then matches a *different* message
`M'`. If `M'` is larger than `n`, this is a truncation error / buffer overflow;
if smaller, silent data corruption of program logic; and in either case, A's
carefully computed buffer size is wrong. Note that using `MPI_ANY_SOURCE` /
`MPI_ANY_TAG` makes it worse (A's `MPI_Recv` re-issued with the *concrete*
source/tag from `status` narrows but does not close the window, because two
messages from the same source with the same tag are indistinguishable to the
pattern). MPI-2.2 already documented the single-threaded version of this hazard
(its Example 3.19: substituting `MPI_ANY_SOURCE` into the receives after a
`MPI_PROBE` makes the program "incorrect: the receive operation may receive a
message that is distinct from the message probed by the preceding call to
`MPI_PROBE`") [mpi22].

The standard's statement of the multithreaded case:

> **Verbatim (MPI-3.1 §3.8.1, Advice to users):**
> "In a multithreaded MPI program, `MPI_PROBE` and `MPI_IPROBE` might need
> special care. If a thread probes for a message and then immediately posts a
> matching receive, the receive may match a message other than that found by the
> probe since another thread could concurrently receive that original message.
> `MPI_MPROBE` and `MPI_IMPROBE` solve this problem by matching the incoming
> message so that it may only be received with `MPI_MRECV` or `MPI_IMRECV` on the
> corresponding message handle." [mpi31]

and MPI-5.0 states the root cause even more bluntly:

> **Verbatim (MPI-5.0, "Matching Probe"):** "The function `MPI_PROBE` checks for
> incoming messages without receiving them. Since the list of incoming messages
> is global among the threads of each MPI process, it can be hard to use this
> functionality in threaded environments." [mpi50]

**The MPI-3 fix: matched probe.** `MPI_Mprobe` / `MPI_Improbe` **atomically match
and remove** the message from the matching stream, returning an opaque
`MPI_Message` handle. The message is then receivable *only* via that handle:

> **Verbatim (MPI-5.0, "Matching Probe"):** "A matched receive (`MPI_MRECV` or
> `MPI_IMRECV`) executed with the message handle will receive the message that
> was matched by the matching probe. Unlike `MPI_IPROBE`, no other probe or
> receive operation may match the message returned by `MPI_IMPROBE`. Each message
> handle returned by `MPI_IMPROBE` must be received with either `MPI_MRECV` or
> `MPI_IMRECV`." [mpi50]

So the fix is exactly: **turn a stateless query into a stateful reservation.**
The message handle is a *capability* — a token conferring exclusive right to
materialise one specific message. (`MPI_MESSAGE_NO_PROC` is returned for a
`MPI_PROC_NULL` source [ompi-mprobe-man].)

The workarounds available before MPI-3 were all bad, and this was argued in the
literature that motivated the MPI-3 addition: Hoefler et al. showed that the
obvious fixes "fail in practice by either limiting the available parallelism
unnecessarily, consuming resources in a nonscalable way, or promoting global
deadlocks", proposed fine-grained locking and matching-outside-MPI as the two
viable MPI-2.2-era approaches, and then proposed the stateless→stateful interface
change that became `MPI_Mprobe` [hoefler2010hybrid]; the companion technical
report is [gregor2009fixingprobe].

**For AgentMPI this is the single most important design precedent in the whole
document.** `MPI_Mprobe` + `MPI_Mrecv` *is* the rendezvous/handle model exposed
as a first-class API: **inspect metadata, decide, then materialise on demand via
a handle that cannot be stolen.** AgentMPI's "pass a handle, receiver
materialises on demand" should be specified as a matched-probe capability, not as
a bare pointer, and the reason is precisely the race above: with multiple
concurrent consumers in a harness, a non-reserving "peek" is unimplementable
correctly.

## 5. The Progress Engine and Threading

### 5.1 The progress rule: "progress requires entering the library"

MPI's liveness guarantees are stated *relative to MPI calls*. For nonblocking
operations the normative text is:

> **Verbatim (MPI-2.2 §3.7.4 "Semantics of Nonblocking Communications",
> paragraph "Progress"; retained in later versions):**
> "A call to `MPI_WAIT` that completes a receive will eventually terminate and
> return if a matching send has been started, unless the send is satisfied by
> another receive. In particular, if the matching send is nonblocking, then the
> receive should complete even if no call is executed by the sender to complete
> the send. Similarly, a call to `MPI_WAIT` that completes a send will eventually
> return if a matching receive has been started, unless the receive is satisfied
> by another send, and even if no call is executed to complete the receive."
> [mpi22]

Read the quantifiers carefully. The guarantee is conditioned on *a call to
`MPI_WAIT`*. The standard promises that **the peer** need not call anything ("even
if no call is executed by the sender to complete the send"), but it says nothing
that obliges the implementation to make progress while the local process is
outside the library. Hence the folk rule, which is a *correct* reading of the
standard's silence rather than an explicit clause:

> **Progress happens (only) when the application enters the MPI library.**

Corollary: a process that posts `MPI_Irecv` and then computes for 10 seconds
without calling any MPI function may leave a rendezvous handshake unanswered for
10 seconds — and the *sender* stalls, because the CTS never comes. The
computation of one rank becomes the latency of another rank's communication. Note
how the eager protocol partially escapes this: eager delivery needs no
receiver-side action beyond the NIC depositing bytes in a pre-posted buffer,
whereas rendezvous fundamentally needs receiver-side *software* participation
unless the NIC can do matching and RDMA itself.

MPI-4.x/5.0 promoted progress to a dedicated, cross-referenced section of the
standard (MPI-5.0's matched-probe text says "The implementation of `MPI_MPROBE`
and `MPI_IMPROBE` needs to guarantee progress in the same way as in the case of
`MPI_PROBE` and `MPI_IPROBE`. See also Section Progress on progress." [mpi50]).
`[UNVERIFIED: exact section number of the general "Progress" section in MPI-4.1 /
MPI-5.0 — confirm against the PDF before citing a number.]` The one-sided chapter
already had its own: MPI-2.2 §11.7.2 states "One-sided communication has the same
progress requirements as point-to-point communication: once a communication is
enabled, then it is guaranteed to complete" [mpi22].

### 5.2 The four thread levels

`MPI_Init_thread(required, &provided)` negotiates one of four monotonically
ordered levels [mpi22]:

| Level | Meaning (paraphrase; verbatim below where quoted) |
|---|---|
| `MPI_THREAD_SINGLE` | Only one thread will execute. |
| `MPI_THREAD_FUNNELED` | "The process may be multi-threaded, but the application must ensure that only the main thread makes MPI calls" [mpi22] |
| `MPI_THREAD_SERIALIZED` | "The process may be multi-threaded, and multiple threads may make MPI calls, but only one at a time: MPI calls are not made concurrently from two distinct threads (all MPI calls are 'serialized')" [mpi22] |
| `MPI_THREAD_MULTIPLE` | "Multiple threads may call MPI, with no restrictions." [mpi22] |

> **Verbatim (MPI-2.2 §12.4.3):** "These values are monotonic; i.e.,
> `MPI_THREAD_SINGLE` < `MPI_THREAD_FUNNELED` < `MPI_THREAD_SERIALIZED` <
> `MPI_THREAD_MULTIPLE`." [mpi22]

An implementation may always return `MPI_THREAD_MULTIPLE` regardless of what was
requested, and a non-thread-compliant one may always return
`MPI_THREAD_SINGLE` — so `provided` must always be checked [mpi22]. The level is
"a global property of the MPI process that can be specified only once, when MPI
is initialized" [mpi22]. (Note MPICH later changed `MPI_Session_init` to default
to `MPI_THREAD_MULTIPLE`, with thread levels remaining global and set by whichever
init runs first [mpich-news].)

**Threads are not addressable.** This is the crux, and it is what makes the
`MPI_Probe` race of §4.6 possible:

> **Verbatim (MPI-2.2 §12.4.1):** "In a thread-compliant implementation, an MPI
> process is a process that may be multithreaded. Each thread can issue MPI
> calls; however, threads are not separately addressable: a rank in a send or
> receive call identifies a process, not a thread. A message sent to a process
> can be received by any thread in this process." [mpi22]

The two normative requirements on a thread-compliant implementation:

> **Verbatim (MPI-2.2 §12.4.1):**
> "1. All MPI calls are thread-safe, i.e., two concurrently running threads may
> make MPI calls and the outcome will be as if the calls executed in some order,
> even if their execution is interleaved.
> 2. Blocking MPI calls will block the calling thread only, allowing another
> thread to execute, if available. The calling thread will be blocked until the
> event on which it is waiting occurs. Once the blocked communication is enabled
> and can proceed, then the call will complete and the thread will be marked
> runnable, within a finite time. A blocked thread will not prevent progress of
> other runnable threads on the same process, and will not prevent them from
> executing MPI calls." [mpi22]

And the sanctioned user-level workaround for the lack of thread addressing — which
is *exactly* the "give each agent its own communicator" idea:

> **Verbatim (MPI-2.2 §12.4.1, Advice to users):** "It is the user's
> responsibility to prevent races when threads within the same application post
> conflicting communication calls. The user can make sure that two threads in the
> same process will not issue conflicting communication calls by using distinct
> communicators at each thread." [mpi22]

Other threading clarifications worth citing:

- **A request can be completed only once.** "A program where two threads block,
  waiting on the same request, is erroneous. Similarly, the same request cannot
  appear in the array of requests of two concurrent
  `MPI_{WAIT|TEST}{ANY|SOME|ALL}` calls. In MPI, a request can only be completed
  once." [mpi22]
- **`MPI_Finalize` must be called on the initializing ("main") thread**, after all
  threads have finished their MPI calls [mpi22].
- **Error/exception handlers may run on a different thread** than the one that
  made the failing call [mpi22].
- **Cancelling or signalling a thread inside an MPI call is undefined**: "The
  outcome is undefined if a thread that executes an MPI call is cancelled (by
  another thread), or if a thread catches a signal while executing an MPI call"
  [mpi22]. Rationale: it avoids requiring the library to be async-cancel-safe
  [mpi22].
- The standard explicitly permits server threads inside the library:
  "Concurrency can also be achieved by having some of the MPI protocol executed
  by separate server threads." [mpi22]

### 5.3 Asynchronous progress threads

The standard's blessing above is the hook for **asynchronous progress**: the
library spawns one or more helper threads whose job is to poll the network and
drive protocol state machines (answer RTS with CTS, complete rendezvous, repost
eager buffers, service credit returns) while the application computes.

Costs and knobs:

- **CPU contention.** A progress thread per rank steals a core, or oversubscribes
  and gets descheduled. MVAPICH2 lists "Support to enable affinity with
  asynchronous progress thread" as a distinct feature, i.e. pinning the progress
  thread is a first-class concern [mvapich2ug]. Common knobs (names only, exact
  defaults `[UNVERIFIED]`): MPICH `MPIR_CVAR_ASYNC_PROGRESS`, MVAPICH2
  `MV2_ASYNC_PROGRESS`, Intel MPI `I_MPI_ASYNC_PROGRESS`.
- **Locking.** Async progress generally requires `MPI_THREAD_MULTIPLE`-grade
  internal locking, which taxes *every* MPI call. Levy et al. flag this as the
  scenario where matching cost actually becomes visible: multithreading raises
  match-queue lengths and adds locking, and a 2304-thread stencil slowed ~57% at
  1 µs per-entry search cost [levy2019simulation].
- **Offload avoids the trade.** Hardware matching in the NIC (ConnectX-5,
  Bull/Atos BXI) both removes the CPU cost of matching and "enables asynchronous
  progress that can improve application performance" without a software thread
  [groves2021bmm]. RDMA-read-based rendezvous similarly improves "performance due
  to asynchronous progress" [barrett2013reducing], [sur2006rdmaread].

### 5.4 Polling vs interrupts

- **Polling (busy-wait):** lowest latency, but burns a core and, in
  `MPI_THREAD_MULTIPLE`, contends for the internal lock. `MPI_Iprobe` in a spin
  loop is the user-level version, and the standard specifically guarantees it
  terminates ("if a process busy waits with `MPI_IPROBE` and a matching message
  has been issued, then the call to `MPI_IPROBE` will eventually return flag =
  true unless..." [mpi31]).
- **Interrupt / event-driven:** frees the core, adds interrupt-delivery latency.
  MPI's own text invokes interrupts as the mechanism of last resort for local
  semantics: for `MPI_Cancel`, "If processing is required on another process, this
  should be transparent to the application (hence the need for an interrupt and
  an interrupt handler)" [mpi22]. Flow-control designs are tuned against
  interrupt count: MVAPICH2's `MV2_SRQ_LIMIT` low-water mark "can be reduced if
  your aim is to reduce the number of interrupts" [mvapich2ug].
- Real implementations do hybrid: spin for N iterations, then block on an event
  fd / completion channel.

### 5.5 Consequences for nonblocking collectives

Nonblocking collectives (`MPI_Ibcast`, `MPI_Iallreduce`, `MPI_Ibarrier`, ...,
MPI-3) are the place where the progress rule bites hardest, because a collective
is internally a *multi-stage schedule* (e.g. a recursive-doubling or tree
pipeline) rather than a single transfer:

1. **A collective's later stages cannot start until earlier stages complete.**
   Without asynchronous progress, the schedule only advances when the owning rank
   enters MPI. So "post `MPI_Iallreduce`, compute, then `MPI_Wait`" frequently
   degenerates into "compute, then do the *entire* allreduce inside `MPI_Wait`" —
   zero overlap achieved, which is the single most common disappointment with
   nonblocking collectives.
2. **One late rank serialises everyone.** Because the schedule is a dependency
   graph across ranks, a single rank that stays out of the library stalls the
   whole tree. This is the collective version of §5.1's "your computation is my
   latency", amplified by the tree depth.
3. **`MPI_Ibarrier` is the standard workaround pattern** for non-uniform
   termination detection (post `Ibarrier`, then loop on `Iprobe` + `Test`), but it
   is exactly the pattern that demands frequent library entry.
4. **The unexpected-queue interaction.** Collectives implemented over
   point-to-point generate deep, bursty match lists — recall the 3.5x FDS speedup
   attributed to "the deeper queue depths incurred through the use of the
   `MPI_Allgather` operation" [nextplatform2016], [flajslik2016matching], and NAS
   IS's `MPI_Alltoall` giving a 10x reduction in match attempts under binned
   matching. So a nonblocking collective left un-progressed piles up unexpected
   eager messages, which lengthens the match list, which slows every other match.
   **Deferred progress and eager buffering compound.**
5. Collectives are protected from tag collisions by using a duplicated
   communicator, not by tags (§1.1) — which is why the communicator is the
   non-wildcardable field.

**For AgentMPI.** The progress rule is the sharpest part of the analogy, because
an LLM agent is *structurally* a process that leaves the library for a very long
time: an inference call is seconds to minutes of not-polling. Any AgentMPI
rendezvous protocol in which the receiver must actively answer an RTS will have
handshake latencies on the order of the receiver's turn length. Three
consequences follow directly from §5.3–5.4:

- **Eager/inline is attractive precisely because it needs no receiver-side
  participation** — the harness deposits the payload into the context and the
  agent finds it there on its next turn. This is the direct analogue of a
  pre-posted eager buffer.
- **Rendezvous in AgentMPI should be receiver-initiated (RDMA-read-like), not
  RTS/CTS**, i.e. the sender registers a handle and the receiver *pulls* on its
  next turn. This eliminates the "sender waits for a peer that is mid-inference"
  stall, mirroring why RDMA-read rendezvous was adopted in MPI over InfiniBand
  [sur2006rdmaread].
- **The harness is the asynchronous progress thread.** The right architectural
  move is the offload one: put matching and materialisation in the harness (the
  "NIC"), not in the agent's turn (the "CPU"), exactly as ConnectX-5/BXI moved
  matching off the host to get asynchronous progress for free [groves2021bmm].

## Mapping to AgentMPI

Focused on §1–§5 of this file. "Where the analogy breaks" is the column that
matters for reviewers; a background section that only lists similarities invites
the "this is a metaphor, not a contribution" rejection.

| MPI concept | Precise semantics | Agent analogue | Where the analogy breaks |
|---|---|---|---|
| **Matching triple** `(communicator, source, tag)` | A message is receivable iff its envelope matches all three; `comm` cannot be wildcarded [mpi41] | `(task/session scope, sending agent id, intent label)` as the addressing key for an inter-agent message | MPI tags are flat non-negative integers with a guaranteed range of only ≥32767 (`MPI_TAG_UB`); agent "intents" are open-vocabulary natural language. Exact-equality matching is not the right predicate, and semantic matching is neither transitive nor cheap nor deterministic |
| **`MPI_ANY_SOURCE` / `MPI_ANY_TAG`** | Wildcard the source and/or tag; the receiver accepts the earliest matching pending message [mpi41] | "Give me the next message from any sub-agent" — the orchestrator's harvest loop | In MPI the wildcard still resolves to *exactly one* concrete message deterministically by arrival order. In an agent harness "any" usually means "whichever is most *relevant*", which is a ranking, not a queue position — so the wildcard becomes a retrieval problem |
| **Non-overtaking rule** | Two sends from the same sender that match the same receive are *matched* in send order; ordering constrains matching, not completion [mpi41], [mpichdiscuss2020] | Messages from one agent to another are consumed in the order that agent produced them | MPI's guarantee is pairwise and per-(sender,destination). Agent context is a *shared totally-ordered* transcript, so the receiver observes a global interleaving of all senders — a stronger and more expensive guarantee than MPI ever provides, and one that leaks cross-sender ordering information |
| **No fairness guarantee** | A matching send may be starved indefinitely by messages from other sources; "It is the programmer's responsibility to prevent starvation" [mpi41] | A chatty sub-agent monopolises the orchestrator's attention; a quiet one is never read | Starvation in MPI is a liveness bug with a well-defined fix (`MPI_Waitsome`, explicit round-robin). In an agent harness, "starvation" is also a *quality* failure: the unread message may have been the important one, and no scheduling policy can detect that without reading it |
| **Progress guarantee (disjunctive)** | Of a matched send/receive pair, *at least one* completes regardless of other system activity [mpi41] | At least one side of an agent-to-agent exchange makes forward progress | MPI's guarantee is unconditional on the *peer*, conditional on the *local* process entering the library. An agent's "library entry" is a whole inference turn, so the constant factor is 10⁶–10⁹x larger and the guarantee is operationally vacuous within a turn |
| **Posted-receive queue (PRQ)** | Ordered list of receives the process has posted but not yet satisfied | The set of "expectations" an agent has declared — awaited tool results, pending sub-agent answers | MPI receives are *typed and sized in advance* (buffer, count, datatype). An agent rarely knows the shape of the answer it awaits, which is why `MPI_Probe`-style size discovery (§4.6) is the honest model rather than `MPI_Recv` |
| **Unexpected-message queue (UMQ)** | Ordered list of arrived messages with no matching posted receive; holds full payload (eager) or just a header (rendezvous) | The harness inbox: messages delivered to an agent that has not asked for them | The UMQ is invisible to the MPI application and bounded only by memory. An agent's UMQ, if inlined, is *visible* — it competes for the same scarce context the agent needs to reason — so the "invisible spillover buffer" abstraction collapses |
| **Linear match-list scan** | Both queues are scanned in order to find the first matching entry; measured 10s of ns per entry, mean depths ~7–140 in real apps, tails to ~500 [groves2021bmm], [levy2019simulation] | Scanning the inbox to decide which pending message answers the current need | Per-entry cost differs by ~10⁸: an MPI match attempt is an integer compare, an AgentMPI match attempt may be an embedding lookup or an LLM judgement. HPC's conclusion ("matching is <1% of runtime") inverts completely; the asymptotics MPI can ignore dominate |
| **Eager protocol** | Sender pushes envelope + full payload unsolicited; receiver buffers into the UMQ on a miss [barrett2013reducing] | **Inline the payload directly into the receiving agent's context window** | Eager's cost in MPI is fungible RAM that can be pooled, reposted, and paged. Context is non-fungible, non-pageable, and *semantically* costly — inlined noise degrades reasoning quality, not just memory headroom. There is no MPI equivalent of "the buffer made the program dumber" |
| **Rendezvous protocol** | RTS header only; receiver replies CTS (or issues RDMA read) once a matching receive is posted; then bulk zero-copy transfer [barrett2013reducing] | **Pass a handle/descriptor; the receiver materialises the payload on demand** | MPI rendezvous is *semantically transparent*: the receiver always ends up with the identical bytes. An agent that declines to materialise a handle ends up with *different information*, so rendezvous in AgentMPI is a semantic choice, not a transport optimisation |
| **Eager limit / crossover threshold** | Switch point where the extra round trip (≈2L) becomes negligible against `size/BW`; tracks the network bandwidth–delay product. Typically 4–32 KB; 12 KB default for Open MPI `openib` and MVAPICH2 on ConnectX; 256 KB for Intel MPI ≤2018 [barrett2013reducing], [ompi-faq-of], [mvapich2ug], [rwth-impi-tips] | A token/relevance budget above which a message is handed over as a handle instead of inlined | MPI's threshold is one scalar derived from two measurable hardware constants (BW, L). The agent threshold depends on the *content's expected utility* to the receiver, which is not a size and not knowable by the sender. AgentMPI's "eager limit" is a policy, not a physical constant |
| **Standard mode's deliberate underspecification** | "It is up to MPI to decide whether outgoing messages will be buffered"; portable programs must not rely on buffering [mpi41] | AgentMPI programs must be correct whether a message is inlined or handed over | Only holds if materialisation is guaranteed-on-demand and free of side effects. If materialising a handle can fail (expired, permission revoked, source agent terminated) then eager and rendezvous are *not* interchangeable and MPI's portability discipline cannot be borrowed wholesale |
| **Unexpected-message memory blowup** | Buffer need scales with BDP × peers; exascale projections reach GB/node [barrett2013reducing]; overflow is easy to provoke [mpi41] | Context exhaustion from unsolicited inlined messages, scaling with the number of peer agents | MPI can respond by lowering the threshold, adding a third protocol, or buying RAM. A context window cannot be enlarged at runtime, and eviction is lossy and irreversible — so AgentMPI *must* have the discard-and-fallback behaviour that MPI treats as an exotic optimisation |
| **Credit-based flow control** | Bounded outstanding eager messages per peer; credits returned on buffer repost; SRQ low-water mark triggers throttling [mvapich2ug], [ompi-faq-of] | Per-sender quota on unsolicited inlined messages, replenished as the receiver consumes them | MPI credits are counted in fixed-size buffers. Agent "credits" would have to be counted in tokens *and* in attention/relevance, and the receiver's consumption of a message does not reliably free the context it occupied |
| **`MPI_Bsend` + `MPI_Buffer_attach`** | User donates an explicit buffer, making send completion *local*; overflow is an **error**; accounting specified down to `MPI_BSEND_OVERHEAD` and a PME circular queue [mpi22], [mpi41] | A sender-owned, explicitly budgeted allowance for inlining into a peer's context | MPI allows exactly one attached buffer per process and makes overflow erroneous — both are acknowledged design mistakes to *not* copy. The standard itself prefers standard mode's blocking backpressure ("the producer will be automatically throttled") [mpi41] |
| **`MPI_Ssend` (synchronous)** | Completes only once the receiver has *started* the matching receive; non-local; gives the sender information about the receiver's program state [mpi41] | "Block until the receiving agent has actually attended to this message" | MPI can detect "the receive has started" cheaply and unambiguously. "The agent has attended to it" is not observable — token presence in a context is not attention, so the completion condition is unverifiable |
| **`MPI_Rsend` (ready)** | May be started *only if* the matching receive is already posted; otherwise erroneous with undefined outcome; lets the implementation skip the handshake [mpi41] | Push a payload on the asserted precondition that the receiver is already waiting for exactly this | Violating `MPI_Rsend`'s precondition is *undefined behaviour* — an acceptable bargain in a language with a compiler and a debugger. In an agent system the failure is silent and probabilistic (the payload is simply misread), so "undefined outcome" is not an acceptable contract |
| **Local vs non-local completion** | Only buffered mode is local; standard/synchronous completion may depend on the peer [mpi41] | Distinguishing "I have handed this off to the harness" from "the recipient has it" | Agent systems routinely conflate the two (an appended transcript entry is treated as delivered *and* understood). Making the local/non-local distinction explicit is a cheap and defensible contribution of AgentMPI |
| **`MPI_Request` + Wait/Test/Waitany/Waitall/Waitsome** | Handle-based completion; `any` = exactly one (nondeterministic), `all` = every one, `some` = at least one currently completable; a request may be completed only once [mpi41], [mpi22] | Orchestrator await primitives over N in-flight sub-agents: first-to-finish, barrier, harvest-all-ready | MPI requests are opaque and side-effect-free until completed. An in-flight agent task consumes tokens and money continuously, and abandoning a `Waitany` loser is not free — so `Waitany` in AgentMPI implies a cancellation policy that MPI does not need |
| **Persistent requests** (`MPI_Send_init`/`MPI_Start`) | Bind the full argument list once, then start/complete repeatedly; amortises argument checking, datatype processing, registration | A pre-negotiated channel between two agents: schema, role, and transfer mode agreed once and reused per message | MPI amortises *mechanical* setup only; the semantics are identical each time. A pre-negotiated agent channel amortises *shared context*, which means the channel accumulates state — so AgentMPI persistent requests are stateful in a way MPI's never are |
| **`MPI_Cancel`** | Local call, marks for cancellation, must still be completed; either the cancel or the communication succeeds but not both; "no part of the receive buffer is altered" [mpi22] | Retract an in-flight message to another agent | **Structurally impossible on the eager path**: you cannot un-read tokens already inlined into a context. Cancellation is only expressible in the rendezvous case, before materialisation. This is the sharpest asymmetry the eager/rendezvous split creates in AgentMPI, and MPI's own difficulties (locality vs distributed atomicity, `MPI_Ibsend` un-cancellability) are the direct precedent |
| **`MPI_Probe` / `MPI_Iprobe`** | Stateless query: reports the message a `MPI_Recv` *would* match, without consuming or reserving it [mpi22] | Peek at inbox metadata (sender, intent, size) before deciding whether to spend context on the body | The MPI probe returns exact, complete metadata (source, tag, count) with zero ambiguity. Agent metadata is a lossy summary that may itself require inference to produce, so "peek" costs tokens and can be wrong about the body |
| **`MPI_Mprobe`/`MPI_Improbe` + `MPI_Mrecv`/`MPI_Imrecv`** | Atomically match *and* remove the message from the matching stream, returning an `MPI_Message` handle; "no other probe or receive operation may match the message returned"; each handle must be received exactly once [mpi50] | The canonical **rendezvous handle**: a capability granting exclusive right to materialise one specific message on demand | Near-exact fit — this is the strongest part of the analogy, and AgentMPI's handle should be specified as an `MPI_Message`-style capability. It breaks on lifetime: an `MPI_Message` is guaranteed materialisable (the bytes are already committed at the receiver), whereas an agent handle may reference content that has expired, been revised, or become inaccessible |
| **The probe→recv race** | The incoming-message list is global across threads, so between probe and receive another thread can consume the probed message; the later `MPI_Recv` matches a different message, breaking the size computed from `status` [mpi31], [mpi50], [hoefler2010hybrid] | Two concurrent consumers in one harness both peek and then both fetch; one gets a different message than it inspected | Identical in structure, and MPI's fix (make the query stateful) transfers directly. Difference: MPI's failure is loud (truncation error); the agent version fails silently, with the consumer reasoning confidently over the wrong payload |
| **Thread levels `SINGLE/FUNNELED/SERIALIZED/MULTIPLE`** | Monotone contract on which threads may call MPI concurrently, fixed once at init, global to the process; `provided` may differ from `required` [mpi22] | How much concurrency the harness permits inside one agent process (one loop, one designated caller, serialised, or fully concurrent) | MPI's four-point ladder exists because internal locking is expensive. AgentMPI's real constraint is not lock contention but that concurrent writers to one context produce an *incoherent* transcript — a correctness problem the thread-level ladder does not model |
| **"Threads are not separately addressable"** | A rank identifies a process, not a thread; any thread may receive a message sent to the process [mpi22] | An agent id addresses the agent, not a particular reasoning step or subtask within it | This is exactly the defect that made `MPI_Probe` unsafe, and the standard's own workaround — "using distinct communicators at each thread" [mpi22] — is the argument for giving each concurrent agent activity its own communicator in AgentMPI rather than sharing one namespace |
| **Progress rule ("enter the library")** | Liveness is guaranteed relative to calls like `MPI_Wait`; the peer need not call anything, but the local process must enter MPI [mpi22] | A receiving agent only advances protocol state when the harness gives it a turn | The magnitudes make it a different regime: microseconds of deferred progress vs. minutes. In MPI, deferred progress is a tuning problem; in AgentMPI it is the dominant term, which is why receiver-initiated (pull) rendezvous is mandatory rather than an optimisation |
| **Asynchronous progress thread / NIC offload** | Helper thread or match-capable NIC drives protocol state while the application computes; offload gives async progress without a software thread [groves2021bmm], [mvapich2ug] | The harness itself performs matching, handle bookkeeping, and materialisation outside any agent's turn | Strong fit, and the right architecture. Breaks in that a NIC's matching is exact and semantics-free; a harness doing *semantic* matching is making inferential decisions on the agent's behalf, so the "offload engine" is no longer a neutral mechanism |
| **Nonblocking collectives + deferred progress** | A collective is a multi-stage schedule; without async progress the schedule advances only on library entry, so overlap often degenerates to zero; collectives generate deep, bursty match lists [nextplatform2016], [flajslik2016matching] | Fan-out to N sub-agents then aggregate; the aggregation stalls until the orchestrator takes a turn, and unread eager replies pile up | MPI collectives have a fixed, known schedule the library can plan and offload. An agent fan-out/aggregate has a data-dependent shape (a sub-agent may spawn more), so no static schedule exists to progress asynchronously |

## BibTeX

```bibtex
@techreport{mpi41,
  author      = {{MPI Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 4.1},
  institution = {University of Tennessee, Knoxville, Tennessee},
  year        = {2023},
  month       = nov,
  day         = {2},
  note        = {Point-to-point communication is Chapter 4; \S4.2.3 Message
                 Envelope, \S4.2.4 Blocking Receive, \S4.4 Communication Modes,
                 \S4.5 Semantics of Point-to-Point Communication, \S4.6 Buffer
                 Allocation and Usage, \S4.8 Probe and Cancel. HTML edition
                 generated 19 November 2023},
  url         = {https://www.mpi-forum.org/docs/mpi-4.1/mpi41-report.pdf}
}

@techreport{mpi31,
  author      = {{MPI Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 3.1},
  institution = {University of Tennessee, Knoxville, Tennessee},
  year        = {2015},
  month       = jun,
  day         = {4},
  note        = {\S3.4 Communication Modes, \S3.5 Semantics of Point-to-Point
                 Communication, \S3.8.1 Probe, \S3.8.2 Matching Probe},
  url         = {https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report.pdf}
}

@techreport{mpi22,
  author      = {{MPI Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 2.2},
  institution = {University of Tennessee, Knoxville, Tennessee},
  year        = {2009},
  month       = sep,
  day         = {4},
  note        = {\S3.2.3 Message Envelope, \S3.2.4 Blocking Receive, \S3.5
                 Semantics of Point-to-Point Communication, \S3.6 Buffer
                 Allocation and Usage, \S3.6.1 Model Implementation of Buffered
                 Mode, \S3.7 Nonblocking Communication, \S3.8 Probe and Cancel,
                 \S11.7.2 Progress, \S12.4 MPI and Threads},
  url         = {https://www.mpi-forum.org/docs/mpi-2.2/mpi22-report.pdf}
}

@techreport{mpi11,
  author      = {{MPI Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 1.1},
  institution = {University of Tennessee, Knoxville, Tennessee},
  year        = {1995},
  month       = jun,
  day         = {12},
  note        = {\S3.5 Semantics of point-to-point communication already contains
                 the non-overtaking rule in wording essentially identical to
                 MPI-4.1},
  url         = {https://www.mpi-forum.org/docs/mpi-1.1/mpi-11-html/node41.html}
}

@techreport{mpi50,
  author      = {{MPI Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 5.0},
  institution = {University of Tennessee, Knoxville, Tennessee},
  year        = {2025},
  note        = {Cited for the ``Matching Probe'' section, which states that the
                 list of incoming messages is global among the threads of an MPI
                 process. UNVERIFIED: exact release month/day and section number
                 not confirmed; HTML node consulted was
                 mpi-5.0/mpi50-report/node82.htm},
  url         = {https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report/node82.htm}
}

@misc{mpichdiscuss2020,
  author       = {{MPICH discuss mailing list}},
  title        = {In-order messages (thread on the interpretation of the
                  non-overtaking rule: ``The standard specifies the order in
                  which messages match, not complete'')},
  howpublished = {\url{https://lists.mpich.org/pipermail/discuss/2020-March/005890.html}},
  year         = {2020},
  month        = mar
}

@inproceedings{flajslik2016matching,
  author    = {Flajslik, Mario and Dinan, James and Underwood, Keith D.},
  title     = {Mitigating {MPI} Message Matching Misery},
  booktitle = {High Performance Computing --- 31st International Conference,
               ISC High Performance 2016, Frankfurt, Germany, June 19--23, 2016,
               Proceedings},
  editor    = {Kunkel, Julian M. and Balaji, Pavan and Dongarra, Jack},
  series    = {Lecture Notes in Computer Science},
  volume    = {9697},
  pages     = {281--299},
  publisher = {Springer},
  year      = {2016},
  doi       = {10.1007/978-3-319-41321-1_15},
  note      = {Hans Meuer Award, ISC High Performance 2016. UNVERIFIED: DOI
               chapter suffix not independently confirmed}
}

@misc{nextplatform2016,
  author       = {{The Next Platform}},
  title        = {Mitigating {MPI} Message Matching Issues},
  howpublished = {\url{https://www.nextplatform.com/code/2016/06/27/mitigating-mpi-message-matching-issues/}},
  year         = {2016},
  month        = jun,
  day          = {27},
  note         = {Secondary source reporting the quantitative results of
                  \cite{flajslik2016matching}: up to 3.5x speedup on FDS, 10x
                  reduction in match attempts for NAS Integer Sort, up to 50x
                  reduction over the bin-count sweep}
}

@article{groves2021bmm,
  author  = {Groves, Taylor and Ravichandrasekaran, Naveen and Cook, Brandon and
             Keen, Noel and Trebotich, David and Wright, Nicholas J. and
             Alverson, Bob and Roweth, Duncan},
  title   = {Not all applications have boring communication patterns: Profiling
             message matching with {BMM}},
  journal = {Concurrency and Computation: Practice and Experience},
  year    = {2021},
  volume  = {33},
  number  = {23},
  pages   = {e6380},
  doi     = {10.1002/cpe.6380},
  note    = {Received 18 December 2020, accepted 26 April 2021, published
             2 June 2021. Instrumented Cray MPICH on NERSC Cori KNL; AMReX,
             Chombo, E3SM, MILC. UNVERIFIED: volume/issue numbers}
}

@article{levy2019simulation,
  author       = {Levy, Scott and Ferreira, Kurt B. and Schonbein, Whit and
                  Grant, Ryan E. and Dosanjh, Matthew G. F.},
  title        = {Using Simulation to Examine the Effect of {MPI} Message
                  Matching Costs on Application Performance},
  year         = {2019},
  institution  = {Sandia National Laboratories},
  number       = {SAND2019-3029J},
  note         = {Preprint submitted to Elsevier, 25 January 2019. Available via
                  OSTI. UNVERIFIED: final journal of publication, volume and
                  page numbers},
  url          = {https://www.osti.gov/servlets/purl/1502976}
}

@inproceedings{bayatpour2016tagmatching,
  author    = {Bayatpour, Mohammadreza and Subramoni, Hari and
               Chakraborty, Sourav and Panda, Dhabaleswar K.},
  title     = {Adaptive and Dynamic Design for {MPI} Tag Matching},
  booktitle = {2016 IEEE International Conference on Cluster Computing
               (CLUSTER)},
  address   = {Taipei, Taiwan},
  month     = sep,
  pages     = {1--10},
  publisher = {IEEE Computer Society},
  year      = {2016},
  doi       = {10.1109/CLUSTER.2016.69},
  note      = {Best Paper Nominee. HPCG: 20\%, 32\% and up to 2x improvement in
               tag matching time over default, bin-based and rank-based schemes
               at 512 processes}
}

@inproceedings{klenk2017overview,
  author    = {Klenk, Benjamin and Fr{\"o}ning, Holger},
  title     = {An Overview of {MPI} Characteristics of Exascale Proxy
               Applications},
  booktitle = {High Performance Computing --- 32nd International Conference,
               ISC High Performance 2017, Frankfurt, Germany, June 18--22, 2017,
               Proceedings},
  editor    = {Kunkel, Julian M. and Yokota, Rio and Balaji, Pavan and
               Keyes, David E.},
  series    = {Lecture Notes in Computer Science},
  volume    = {10266},
  pages     = {217--236},
  publisher = {Springer},
  year      = {2017},
  note      = {Dynamic MPI characterisation of 18 exascale proxy applications.
               UNVERIFIED: specific numeric results not retrieved; cite only
               qualitative claims until the PDF is consulted}
}

@inproceedings{barrett2013reducing,
  author    = {Barrett, Brian W. and Hemmert, K. Scott},
  title     = {Reducing {MPI} Memory Usage in Exascale Networks},
  year      = {2013},
  institution = {Sandia National Laboratories},
  note      = {Proposes a dual-threshold three-protocol design adding an ``eager
               medium'' protocol; source of the ``typical threshold between eager
               and rendezvous messages is between 4 KB and 32 KB'' statement and
               of the exascale buffering projections. UNVERIFIED: publication
               venue and year; retrieved from OSTI},
  url       = {https://www.osti.gov/servlets/purl/1109242}
}

@misc{barrett2013matching,
  author = {Barrett, Brian W. and others},
  title  = {Measurements of per-entry {MPI} match queue search cost on modern
            multicore and simple-core processors},
  year   = {2013},
  note   = {UNVERIFIED: cited only indirectly, as reference [7] of
            \cite{levy2019simulation}, for the finding that per-entry search
            time is below 10 ns on modern multicore processors while ARM
            Cortex-A9 shows growth with queue length. Full bibliographic
            details not retrieved; resolve before submission}
}

@misc{schonbein2019matching,
  author = {Schonbein, Whit and others},
  title  = {On the effect of simple multithreading on {MPI} match queue lengths},
  year   = {2019},
  note   = {UNVERIFIED: cited only indirectly, as reference [10] of
            \cite{levy2019simulation}. Full bibliographic details not
            retrieved; resolve before submission}
}

@inproceedings{sur2006rdmaread,
  author    = {Sur, Sayantan and Jin, Hyun-Wook and Chai, Lei and
               Panda, Dhabaleswar K.},
  title     = {{RDMA} read based rendezvous protocol for {MPI} over
               {InfiniBand}: design alternatives and benefits},
  booktitle = {Proceedings of the Eleventh ACM SIGPLAN Symposium on Principles
               and Practice of Parallel Programming (PPoPP '06)},
  pages     = {32--39},
  publisher = {ACM},
  address   = {New York, NY, USA},
  year      = {2006},
  doi       = {10.1145/1122971.1122978}
}

@inproceedings{yuan2009maximizing,
  author    = {Yuan, Xin and others},
  title     = {Maximizing {MPI} Point-to-Point Communication Performance},
  booktitle = {Proceedings of the International Conference on Supercomputing
               (ICS)},
  year      = {2009},
  note      = {Defines EAGER, HYBRID, SEND\_RNDV and RECV\_RNDV protocols with
               two thresholds (EAGER\_THRESHOLD, HYBRID\_THRESHOLD).
               UNVERIFIED: full author list, exact title and venue details;
               retrieved as \url{https://www.cs.fsu.edu/~xyuan/paper/09ics.pdf}}
}

@inproceedings{hoefler2010hybrid,
  author    = {Hoefler, Torsten and Bronevetsky, Greg and Barrett, Brian and
               de Supinski, Bronis R. and Lumsdaine, Andrew},
  title     = {Efficient {MPI} Support for Advanced Hybrid Programming Models},
  booktitle = {Recent Advances in the Message Passing Interface --- Proceedings
               of the 17th European MPI Users' Group Meeting (EuroMPI 2010)},
  series    = {Lecture Notes in Computer Science},
  publisher = {Springer},
  year      = {2010},
  note      = {Shows that MPI-2.2 workarounds for the MPI\_Probe/MPI\_Recv race
               ``fail in practice by either limiting the available parallelism
               unnecessarily, consuming resources in a nonscalable way, or
               promoting global deadlocks''; proposes the stateless-to-stateful
               probe extension that became MPI\_Mprobe in MPI-3. UNVERIFIED:
               volume and page numbers; ACM DL id 10.5555/1894122.1894129}
}

@techreport{gregor2009fixingprobe,
  author      = {Gregor, Douglas and Hoefler, Torsten and Barrett, Brian and
                 Lumsdaine, Andrew},
  title       = {Fixing Probe for Multi-Threaded {MPI} Applications
                 (Revision 4)},
  institution = {Indiana University},
  year        = {2009},
  note        = {Cited as reference [29] in MPI-3.1 \S3.8.1 in support of the
                 matched-probe addition}
}

@techreport{mvapich2ug,
  author      = {{Network-Based Computing Laboratory, The Ohio State
                 University}},
  title       = {{MVAPICH2} 2.3.7 User Guide},
  institution = {The Ohio State University},
  year        = {2022},
  note        = {\S11.24 and \S12.5 MV2\_IBA\_EAGER\_THRESHOLD (default: HCA
                 dependent, 12 KB for ConnectX HCAs; older table: architecture
                 dependent, 12 KB for IA-32); \S11.104 MV2\_VBUF\_TOTAL\_SIZE;
                 \S11.105 MV2\_SMP\_EAGERSIZE (architecture dependent); SRQ flow
                 control (MV2\_SRQ\_SIZE default 256, MV2\_SRQ\_MAX\_SIZE default
                 4096, MV2\_SRQ\_LIMIT low-water mark). UNVERIFIED: publication
                 year of the 2.3.7 guide},
  url         = {https://mvapich.cse.ohio-state.edu/static/media/mvapich/mvapich2-userguide.pdf}
}

@misc{mvapich-bestpractice,
  author       = {Choi, Dong Ju},
  title        = {Impact of eager-threshold based tuning on performance of
                  Amber ({MVAPICH} Best Practice)},
  howpublished = {\url{https://mvapich.cse.ohio-state.edu/best_practice/8/}},
  year         = {2016},
  month        = mar,
  note         = {MVAPICH2 2.2b with MV2\_IBA\_EAGER\_THRESHOLD=131072 and
                  MV2\_VBUF\_TOTAL\_SIZE=131072}
}

@misc{mvapich-envvar,
  author       = {{MVAPICH developers}},
  title        = {{MVAPICH2} \texttt{README.envvar}:
                  MPIR\_CVAR\_IBA\_EAGER\_THRESHOLD /
                  MV2\_IBA\_EAGER\_THRESHOLD},
  howpublished = {\url{https://github.com/DDNStorage/mvapich2/blob/master/README.envvar}},
  note         = {Documents default -1, i.e. resolved at runtime, and the
                  recommendation to match MV2\_VBUF\_TOTAL\_SIZE. UNVERIFIED:
                  this is a third-party mirror of the MVAPICH2 source tree}
}

@misc{ompi-faq-of,
  author       = {{Open MPI Project}},
  title        = {FAQ: Tuning the run-time characteristics of {MPI}
                  {InfiniBand}, {RoCE}, and {iWARP} communications},
  howpublished = {\url{https://www.open-mpi.org/faq/?category=openfabrics}},
  note         = {btl\_openib\_eager\_limit default 12k (12288 bytes);
                  btl\_openib\_rndv\_eager\_limit defaults to the same value;
                  btl\_openib\_eager\_rdma\_num default 16;
                  btl\_openib\_free\_list\_max default -1 (unbounded). Consulted
                  via the mirror at
                  \url{https://aws.open-mpi.org/~jsquyres/ompi-unofficial/faq/?category=openfabrics}.
                  UNVERIFIED: the openib BTL is deprecated in current Open MPI
                  releases in favour of UCX}
}

@misc{ompi-mprobe-man,
  author       = {{Open MPI Project}},
  title        = {\texttt{MPI\_Mprobe(3)} --- Blocking matched probe for a
                  message},
  howpublished = {\url{https://docs.open-mpi.org/en/main/man-openmpi/man3/MPI_Mprobe.3.html}},
  note         = {Documents MPI\_MESSAGE\_NO\_PROC for an MPI\_PROC\_NULL source
                  and the handle-based MPI\_Mrecv/MPI\_Imrecv completion}
}

@misc{lccanon2016ompi,
  author       = {Canon, Louis-Claude},
  title        = {Open {MPI} parameters study},
  howpublished = {\url{http://lccanon.free.fr/notes/2016-03-17.html}},
  year         = {2016},
  month        = mar,
  note         = {Independent corroboration that btl\_openib\_eager\_limit
                  defaults to 12288 and btl\_openib\_max\_send\_size to 65536;
                  companion note of 2016-03-23 covers
                  MV2\_IBA\_EAGER\_THRESHOLD tuning}
}

@misc{mpich-cvars,
  author       = {{MPICH developers}},
  title        = {\texttt{MPIR\_CVAR\_CH4\_OFI\_EAGER\_MAX\_MSG\_SIZE} control
                  variable description in
                  \texttt{src/mpid/ch4/netmod/ofi/ofi\_init.c}},
  howpublished = {\url{https://fossies.org/dox/mvapich-4.1/ofi__init_8c_source.html}},
  note         = {Default -1: ``If the number is negative, OFI will init the
                  MPIDI\_OFI\_global.max\_msg\_size using whatever provider gives
                  (which might be unlimited for socket provider).'' Consulted via
                  the MVAPICH 4.1 source tree, which incorporates MPICH CH4}
}

@misc{mpich-pr4791,
  author       = {{MPICH developers}},
  title        = {ch4/generic: remove {CVAR} for overriding eager limit
                  (pull request \#4791)},
  howpublished = {\url{https://github.com/pmodels/mpich/pull/4791}},
  note         = {States that ``OFI now has a provide[r] independent eager limit
                  of 16 KB''}
}

@misc{mpich-news,
  author       = {{MPICH developers}},
  title        = {{MPICH} News and Events (release notes)},
  howpublished = {\url{https://www.mpich.org/about/news/}},
  note         = {CH4:OFI native RNDV feature; MPIR\_CVAR\_CH4\_OFI\_EAGER\_THRESHOLD
                  forces the RNDV send path (pipeline, read, write, direct);
                  MPI\_Session\_init defaults to MPI\_THREAD\_MULTIPLE}
}

@misc{intelmpi-eager,
  author       = {{Intel Corporation}},
  title        = {All the Things You Need to Know About Intel {MPI} Library},
  howpublished = {\url{https://www.intel.com/content/dam/www/public/us/en/documents/presentation/things-mpi-library.pdf}},
  note         = {States that the eager/rendezvous switch point ``is an
                  implementation technique, it is not part of the MPI
                  standard''; lists I\_MPI\_EAGER\_THRESHOLD,
                  MV2\_IBA\_EAGER\_THRESHOLD and btl\_openib\_eager\_limit; notes
                  the default ``could be, by default, platform specific
                  (MVAPICH2, OpenMPI) or identical for all platforms (IMPI)''.
                  UNVERIFIED: publication date}
}

@misc{rwth-impi-tips,
  author       = {{RWTH Aachen University, IT Center}},
  title        = {23 tips for performance tuning with the Intel {MPI} Library},
  howpublished = {\url{https://blog.rwth-aachen.de/hpc_import_20210107/attachments/3475018/59310186.pdf}},
  note         = {I\_MPI\_EAGER\_THRESHOLD controls the high-level
                  eager/rendezvous switch; I\_MPI\_INTRANODE\_EAGER\_THRESHOLD
                  defaults to I\_MPI\_EAGER\_THRESHOLD (approximately 256 kB).
                  UNVERIFIED: publication date}
}

@misc{intel-community-eager,
  author       = {{Intel Community forum}},
  title        = {\texttt{I\_MPI\_EAGER\_THRESHOLD} not supported},
  howpublished = {\url{https://community.intel.com/t5/Intel-oneAPI-HPC-Toolkit/I-MPI-EAGER-THRESHOLD-not-supported/m-p/1277270}},
  note         = {Documents the historical default (``in bytes, default is
                  256 kB'') and quotes the Intel MPI release-note text: ``Since
                  Intel(R) MPI Library version 2019 environment variable
                  I\_MPI\_EAGER\_THRESHOLD is not supported, please use
                  corresponding libfabric controls instead''}
}
```
