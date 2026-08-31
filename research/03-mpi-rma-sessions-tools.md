# MPI One-Sided Communication (RMA), Memory Models, Synchronization, Dynamic Processes, Sessions, MPI-IO, and Tools Interfaces

**Research file 03 for the AgentMPI paper.** Scope: everything in MPI that is *not* two-sided
point-to-point or collective communication — the one-sided/RMA chapter, the process-management
chapters, the I/O chapter, the tools chapters, and MPI-4's partitioned communication. The final
section maps each mechanism onto a proposed agent-system analogue.

Standard versions referenced throughout: MPI-2.0 (July 1997) [Dinan et al. 2016, §2], MPI-2.2
(2009), MPI-3.0 (September 2012), MPI-3.1 (June 2015), MPI-4.0 (June 2021), MPI-4.1 (November
2023), MPI-5.0 (a `mpi-5.0/mpi50-report` document tree is published by the MPI Forum; ratification
date `[UNVERIFIED]`). Section citations of the form "MPI-3.1 §11.x" or "MPI-4.1 §13.x" refer to the
Forum's own HTML node pages, which are the versions consulted here.

---

## 1. Why MPI-2 RMA failed, and what MPI-3 changed

### 1.1 The MPI-2 design

MPI-2.0 (1997) introduced one-sided communication as a new chapter. Its goals were explicitly
stated as: "providing a portable interface for one-sided communication; separating data movement
from interprocess synchronization; and supporting cache-coherent and non-cache-coherent systems"
[Dinan et al. 2016, §2]. The interface was minimal:

- **One window-creation call**: `MPI_Win_create(base, size, disp_unit, info, comm, &win)`. The user
  supplies a pre-existing, contiguous local buffer. A *window* binds a memory region at each process
  to the process group of a communicator [Hoefler et al. 2015, §2].
- **Three communication operations**: `MPI_Put`, `MPI_Get`, `MPI_Accumulate` [Dinan et al. 2016,
  §2.1.1]. Origin buffers are specified as `(pointer, count, datatype)`; target buffers as
  `(displacement, count, datatype)`, where the displacement is scaled by the target's `disp_unit`.
- **Two synchronization families**: active target (fence, and post/start/complete/wait) and passive
  target (lock/unlock).
- **One memory model**, retroactively named the *separate* model in MPI-3.

### 1.2 The restrictions that killed it

Five concrete problems, each traceable to the 1997 abstract machine model:

1. **No atomic read-modify-write that returns a value.** `MPI_Accumulate` updates but returns
   nothing. You could not build a distributed lock, a queue, or a shared counter without an
   additional round trip or a software agent at the target. MPI-3 added exactly this
   [Dinan et al. 2016, §2.2.1].
2. **The separate memory model's conflict rules were extremely conservative.** MPI-2 maintained a
   logical distinction between a *public* window copy (target of remote put/get/accumulate) and a
   *private* window copy (target of local load/store), synchronized only by MPI synchronization
   calls. Consequently MPI-2 "forbids concurrent overlapping operations when any of the operations
   writes to the window data; the only exception is that multiple accumulate operations can perform
   concurrent overlapping updates when the same operation is used." Worse, "because the MPI library
   is unaware of which locations are updated when the window buffer is directly accessed by the
   hosting process, local updates cannot be performed concurrently with any other operations"
   [Dinan et al. 2016, §2.1.2]. On a cache-coherent machine with an RDMA NIC this is pure lost
   performance and lost expressivity.
3. **Coarse completion.** All operations were nonblocking but returned no request handle. The only
   way to complete anything was to close the whole epoch (`fence`, `complete`, `unlock`). There was
   no way to complete one message out of many, and no way to complete an operation *remotely*
   without also ending the epoch [Dinan et al. 2016, §2.2.2].
4. **Static, non-scalable window memory.** `MPI_Win_create` lets each process expose an arbitrary
   base address, which "essentially forces an implementation to store all remote addresses
   separately… it requires Ω(p) storage on each of the p processes in the worst case"
   [Gerstenberger et al. 2013, §2.2]. Gerstenberger et al. are blunt: "since traditional windows are
   fundamentally non-scalable, and only included in MPI-3.0 for backwards-compatibility, their use is
   strongly discouraged." There was also no way to attach memory to a window after creation, which
   "posed significant challenges to applications that need to dynamically allocate" memory
   [Dinan et al. 2016, §2.2.3].
5. **No way to exploit intra-node shared memory.** MPI-2 RMA had no notion that some window peers
   are on the same node and could be accessed by plain load/store.

The consequence, in the authors' own words: "The MPI-2 abstract machine model does not reflect
today's RDMA networks well and does not support the exploitation of RDMA's full potential, which
inhibited its adoption" [Hoefler et al. 2015, §1]. Dinan et al. record that "combined, these factors
led some to conclude that MPI-2 RMA was not [adequate]" and that "the MPI-2 RMA interface has been
found to be inadequate for many common one-sided use cases" [Dinan et al. 2016, §2]. Hoefler et al.
note that highly optimized MPI-2 RMA implementations did exist (e.g. for the NEC SX-5
[Träff et al. 2000]) "but the interface has only been adopted in very limited settings"
[Hoefler et al. 2015, §5]. Meanwhile PGAS systems — Co-Array Fortran [Numrich & Reid 1998], UPC
[UPC Consortium 2005], Global Arrays [Nieplocha et al. 1996], Cray/OpenSHMEM — occupied the RMA
niche, because message passing over RDMA "incurs additional overheads in comparison with native
remote memory access (RMA, aka. PGAS) programming," mainly from message matching and the
eager/rendezvous protocol split [Gerstenberger et al. 2013, §1].

### 1.3 What MPI-3 added

**Four window flavors** [Hoefler et al. 2015, §2.1; Gerstenberger et al. 2013, §2.2]:

| Call | Semantics | Cost |
|---|---|---|
| `MPI_Win_create` | legacy; expose existing user buffer | Ω(p) address table per process |
| `MPI_Win_allocate` | MPI allocates the memory; can use a *symmetric heap* so base addresses match on all ranks | O(1) memory, O(log p) time with a randomized `mmap` retry protocol |
| `MPI_Win_create_dynamic` | binds only a process group; memory attached later with `MPI_Win_attach`/`MPI_Win_detach` (non-collective, O(1) per region) | needs a distributed descriptor cache with an id-counter invalidation protocol |
| `MPI_Win_allocate_shared` | collectively allocates memory directly mappable by all peers; enables load/store | see §4 |

**Request-based operations**: `MPI_Rput`, `MPI_Rget`, `MPI_Raccumulate`, `MPI_Rget_accumulate`
return an `MPI_Request` completable with the usual `MPI_Wait`/`MPI_Test`. Completion here means
**local** completion only: for put/accumulate the local buffer may be reused; for get/get_accumulate
the remote data has arrived in the local buffer [Hoefler et al. 2015, §2.4]. In MPI-3.1 these are
usable only inside a passive-target epoch [Dinan et al. 2016, §2.2.2]. Note the asymmetry: there is
no `MPI_Rfetch_and_op` and no `MPI_Rcompare_and_swap`. Bulk completion is generally cheaper —
request management "can also cause additional runtime overhead" [Hoefler et al. 2015, §2.4].

**Three new atomics** [Dinan et al. 2016, §2.2.1; MPI-4.1 §13.3]:

- `MPI_Get_accumulate(origin, ..., result, ..., target, ..., op, win)` — general atomic
  read-modify-write, permits derived datatypes and different parameters per buffer.
- `MPI_Fetch_and_op(&origin, &result, datatype, rank, disp, op, win)` — restricted to a *single
  element of a predefined datatype*; because of the restriction it "offers numerous optimization
  opportunities… permitting direct use of hardware-supported atomic operations."
- `MPI_Compare_and_swap(&origin, &compare, &result, datatype, rank, disp, win)` — restricted to the
  integer subset of predefined datatypes; the prior target value is always returned.

Two idioms follow: `MPI_Get_accumulate` with `MPI_NO_OP` is an **atomic get**, and `MPI_Accumulate`
with `MPI_REPLACE` is an **atomic put** [Hoefler et al. 2015, §2.3]. Crucially, atomicity is only
guaranteed **element-wise at the granularity of the predefined basic datatype** (typically 4 or 8
bytes, whatever the hardware supports). Hoefler et al. give the trap explicitly: if two processes
each `MPI_REPLACE`-accumulate the same pair of integers, the result may be the first process's value
in one integer and the second's in the other. And "the atomic and accumulate operations are not
atomic with respect to put and get operations" [Dinan et al. 2016, §2.2.1] — you must not mix a
`MPI_Put` with a CAS on the same location.

**Accumulate ordering.** By default MPI provides strong ordering between accumulate operations from
the same origin to the same target location: for `x, y` of accumulate type with `x.wl = y.wl` and
`x` before `y` in program order, `x → y` in consistency order [Hoefler et al. 2015, eq. 26]. Expert
users may relax any combination of read-after-read, read-after-write, write-after-read, and
write-after-write ordering via the window's `accumulate_ordering` info key; "the fastest mode is to
require no ordering" [Hoefler et al. 2015, §2.3]. Put and get have *no* specified ordering at all.

### 1.4 The two memory models: unified vs separate

MPI models each window as having a **public copy** (touched by remote put/get/accumulate, i.e. the
NIC's view) and a **private copy** (touched by local load/store, i.e. the CPU-cache view)
[MPI-3.1 §11.4; Hoefler et al. 2015, §2.5].

- **`MPI_WIN_SEPARATE`** — the conservative model, for systems where coherence is managed in
  software. Remote operations target the public copy; loads/stores target the private copy.
  Synchronization operations (fence, lock/unlock, `MPI_Win_sync`) reconcile the two. The standard is
  careful: "The semantics do not prescribe that the windows must be separate, just that they *may* be
  separate" [Hoefler et al. 2015, §2.5]. Programs written for the separate model are always correct
  under the unified model, so separate-model programming is the portable choice.
- **`MPI_WIN_UNIFIED`** — assumes hardware-managed coherence. "public and private copies are
  identical and updates via put or accumulate calls are eventually observed by load operations
  without additional RMA calls. A store access to a window is eventually visible to remote get or
  accumulate calls without additional RMA calls" [MPI-3.1 §11.4]. This is what enables **polling**: a
  consumer may spin on a window location and is guaranteed the value eventually arrives, even though
  no happens-before edge exists [Hoefler et al. 2015, §3.5]. The standard warns in the same breath
  that unsynchronized unified-model accesses "might observe changes to the memory while they are in
  progress," so checking part of a message to infer whole-message arrival is erroneous.

The model is discoverable per window via the `MPI_WIN_MODEL` attribute, whose value is
`MPI_WIN_UNIFIED` or `MPI_WIN_SEPARATE` [MPI-3.1 §11.4]. Support for unified is not mandatory. In
practice, foMPI "only consider[s] the stronger unified model since it is supported by all current
RDMA networks" [Gerstenberger et al. 2013, §1].

**Conflict definitions differ between the models** [Hoefler et al. 2015, §3.3.1]. Both models call
two accesses to overlapping locations at the same process *conflicting* if (a) one is a put, (b)
exactly one is an accumulate, or (c) one is a get and the other a local store. The separate model
adds a much harsher clause: remote writing operations conflict with local stores issued by the
target **regardless of the accessed location**. That single clause is why MPI-2 RMA was so hard to
use.

Hoefler et al. give a semi-formal axiomatic model with two orders: **happens-before** (`hb`, the
transitive closure of program order and synchronization order, i.e. *process* synchronization) and
**consistency order** (`co`, i.e. *memory* visibility). A program is data-race-free iff every pair of
conflicting accesses is ordered by *both*: `x --cohb--> y ∨ y --cohb--> x` [Hoefler et al. 2015,
§3.3.2]. This separation of `hb` from `co` is the single most transferable idea in the whole chapter,
and formalizing it exposed two genuine defects in MPI-3: a loose definition permitting conflicting
readings of the consistency rules, and a missing definition of the interaction between active- and
passive-target mode [Hoefler et al. 2015, §3].

---

## 2. The three synchronization modes

### 2.1 Epochs

"All communication operations are nonblocking and arranged in epochs. An epoch is delineated by
synchronization operations and forms a unit of communication" [Hoefler et al. 2015, §2.6]. Two kinds:

- **Access epoch** — the process may act as *origin* of RMA operations.
- **Exposure epoch** — the process's local window may be accessed as *target*.

A process can be in both simultaneously. Formally, epochs have a total order per process per
`(window, target)` pair; each starts with `fence | lock | lock_shared | lock_all` and ends with a
locally matching `fence | unlock | unlock_all`; a `flush` "has the effect of closing and opening an
epoch" [Hoefler et al. 2015, §3.2.1]. **In passive-target mode the exposure-epoch concept does not
exist at all**: memory is permanently exposed. That buys performance at the cost of safety —
"arbitrary accesses are possible" [Hoefler et al. 2015, §2.6.2].

### 2.2 Fence (active target, collective)

`MPI_Win_fence(assert, win)` is collective over the window group. Semantics [MPI-3.1 §11.5.1]:

- All RMA ops on `win` originating at a process and started before the fence complete **at that
  process** before the fence returns, and complete **at their target** before the fence returns at
  the target.
- Ops started after the fence returns access the target window only after the target has also called
  fence.
- Fence epochs are always *both* access and exposure epochs. "the fence call is equivalent to calls
  to a subset of post, start, complete, wait."
- "A fence call usually entails a barrier synchronization… However, a call to `MPI_WIN_FENCE` that is
  known not to end any epoch (in particular, a call with assert equal to `MPI_MODE_NOPRECEDE`) does
  not necessarily act as a barrier."

Fence is right for bulk-synchronous codes with many or rapidly changing partners: "If each of the p
processes communicates with a large number of neighbors k (k > log(p)), then fence synchronization
may be the best solution" [Hoefler et al. 2015, §4.1]. foMPI implements it with an `mfence`, a DMAPP
bulk `gsync`, and an MPI barrier: O(1) memory, O(log p) time [Gerstenberger et al. 2013, §2.3].

**Assertion flags** [MPI-3.1 §11.5.4]. `assert` is a bit-vector OR of constants; `assert = 0` is
always valid; supplying *incorrect* information is erroneous; implementations may ignore it
entirely. For `MPI_Win_fence` the significant flags are:

| Flag | Meaning | Direction |
|---|---|---|
| `MPI_MODE_NOSTORE` | the local window was not updated by local stores (or local get/receive) since the last synchronization | about the **past** |
| `MPI_MODE_NOPRECEDE` | this fence does not complete any sequence of locally issued RMA calls; **must be given by all processes in the group if given by any** | about the **past** |
| `MPI_MODE_NOPUT` | the local window will not be updated by put or accumulate after this fence until the next one | about the **future** |
| `MPI_MODE_NOSUCCEED` | this fence does not start any sequence of locally issued RMA calls; **must be given by all if given by any** | about the **future** |

The standard's own mnemonic: "the nostore and noprecede flags provide information on what happened
before the call; the noput and nosucceed flags provide information on what will happen after the
call." `MPI_MODE_NOCHECK` is **not** a fence flag; it applies to `MPI_Win_post`, `MPI_Win_start`,
`MPI_Win_lock`, and `MPI_Win_lock_all`. For post it additionally means "the matching calls to
`MPI_WIN_START` have not yet occurred on any origin processes"; for start, "the matching calls to
`MPI_WIN_POST` have already completed on all target processes" — and it must be specified on *both*
sides or neither, which is the ready-send optimization by analogy.

```
   Fence epoch (window group = {P0..P3}); ASCII timeline
   ------------------------------------------------------
   P0: --Fence(NOPRECEDE)--[ Put->P1 ; Get<-P2 ; Acc->P3 ]--Fence(0)--> ...
   P1: --Fence(NOPRECEDE)--[            (passive target of P0's Put) ]--Fence(0)--> ...
   P2: --Fence(NOPRECEDE)--[ Put->P0                                 ]--Fence(0)--> ...
   P3: --Fence(NOPRECEDE)--[                                         ]--Fence(NOSUCCEED)-> ...
                           ^                                          ^
                     epoch opens                                epoch closes:
                  (access + exposure                    every op above is complete at
                   for ALL of P0..P3)                     BOTH origin and target

   Idiomatic skeleton, overlapping compute with communication [Hoefler et al. 2015, Fig. 11]:

     MPI_Win_allocate(sz, 1, MPI_INFO_NULL, comm, &base, &win);
     for (it = 0; it < niters; it++) {
         MPI_Win_fence(MPI_MODE_NOPRECEDE, win);          /* open epoch, no prior ops */
         MPI_Put(&left_halo,  n, T, lnbr, LOFF, n, T, win);
         MPI_Put(&right_halo, n, T, rnbr, ROFF, n, T, win);
         compute_inner();                                 /* independent of halo */
         MPI_Win_fence(MPI_MODE_NOSTORE | MPI_MODE_NOPUT, win); /* close: halos landed */
         compute_outer();                                 /* consumes halo */
     }
```

### 2.3 PSCW / general active target (scalable, neighborhood)

Four calls, expressing a *neighborhood* instead of a global barrier [MPI-3.1 §11.5.2]:

- `MPI_Win_post(group, assert, win)` — opens an **exposure** epoch; `group` is the set of *origin*
  processes allowed to access me. **Does not block.**
- `MPI_Win_start(group, assert, win)` — opens an **access** epoch; `group` is the set of *target*
  processes I will access. "MPI_WIN_START is allowed to block until the corresponding MPI_WIN_POST
  calls are executed, but is not required to."
- `MPI_Win_complete(win)` — closes the access epoch; "All RMA communication calls issued on win
  during this epoch will have completed **at the origin** when the call returns… but not at the
  target."
- `MPI_Win_wait(win)` — closes the exposure epoch, blocking until all matching `MPI_Win_complete`
  calls have occurred. "When the call returns, all these RMA accesses will have completed at the
  target window." `MPI_Win_test(win, &flag)` is its nonblocking form; once it returns `flag = true`
  it must not be invoked again until the window is posted anew.

The groups must match exactly: if i names j in its post group, the next start at j naming i matches
that post. The standard offers a model implementation in terms of nonblocking sends/receives on a
hidden window communicator: post = isend(tag0) to each in group; start = irecv(tag0) from each in
group; complete = isend(tag1); wait = irecv(tag1) + waitall [MPI-3.1 §11.5.2].

The standard's own **rationale** is the interesting part for us: the design "requires the user to
provide complete information on the communication pattern, at each end of a communication link…
This provides maximum flexibility (hence, efficiency) for the implementor: each synchronization can
be initiated by either side, since each 'knows' the identity of the other. This also provides maximum
protection from possible races. On the other hand, the design requires more information than RMA
needs: in general, it is sufficient for the origin to know the rank of the target, but not vice
versa. Users that want more 'anonymous' communication will be required to use the fence or lock
mechanisms." That is an explicit statement of a **safety-vs-anonymity trade-off** paid in
declaration overhead.

foMPI's scalable PSCW protocol: each posting process i announces itself by appending its rank to a
matching list local to each j in its group; each starting j spins until all its named origins appear
in its local list; `complete` first guarantees remote visibility (`gsync`/`mfence`) then atomically
increments a completion counter at each process in the group; `wait` blocks until the counter reaches
the group size. O(k) messages for post and complete, zero for start and wait, O(k) memory, where k is
the max neighbor count — and "we assume that k ∈ O(log p) in scalable programs"
[Gerstenberger et al. 2013, §2.3]. The tricky part is the *remote free-storage management scheme*
needed to append into a remote fixed-size matching list.

```
   PSCW epoch: P0 accesses {P1,P2}; P1 exposes to {P0}; P2 exposes to {P0,P3}
   -------------------------------------------------------------------------
   P0:  Win_start({1,2}) --- Put->P1 --- Get<-P2 --- Win_complete()
             |  (may block until P1,P2 posted)            |
             v                                            v (local completion only)
   P1:  Win_post({0}) ------------------------------ Win_wait()
        (no block)                                   (blocks for P0's complete;
                                                      on return, data IS at target)
   P2:  Win_post({0,3}) ---------------------------- Win_wait()
   P3:  Win_start({2}) --- Acc->P2 ----------------- Win_complete()

   Note: P1 never synchronizes with P2 or P3.  There is no barrier.
   Cost is O(|group|), not O(p) — this is the whole point.

     /* target side */                        /* origin side */
     MPI_Group_incl(g, ni, in,  &ingrp);      MPI_Group_incl(g, no, out, &outgrp);
     MPI_Win_post(ingrp,  0, win);            MPI_Win_start(outgrp, 0, win);
     ... local compute ...                    MPI_Put(...); MPI_Get(...);
     MPI_Win_wait(win);   /* or Win_test */   MPI_Win_complete(win);
```

### 2.4 Passive target

Two sub-modes [MPI-3.1 §11.5.3; Hoefler et al. 2015, §2.6.2]:

**Per-target lock/unlock.** `MPI_Win_lock(lock_type, rank, assert, win)` with `lock_type` ∈
{`MPI_LOCK_SHARED`, `MPI_LOCK_EXCLUSIVE`}, closed by `MPI_Win_unlock(rank, win)`. Multiple
lock/unlock access epochs may be open simultaneously **but each must target a different process**.
The locks behave as reader-writer locks at the *window site*: exclusive-protected accesses are never
concurrent with any other lock-protected access to the same window; shared-protected accesses are
never concurrent with exclusive ones. `MPI_Win_unlock` returns only after the ops "have completed
both at the origin and at the target." `MPI_Win_lock` itself need not block — an implementation may
defer everything to unlock and buffer in between — **except** when locking your *own* window, where
it must block, because the lock protects subsequent local load/store [MPI-3.1 §11.5.3]. It is
erroneous to have a window simultaneously locked and exposed via post; the standard's rationale is
that enforcing mutual exclusion between the two mechanisms "would entail additional overheads," and
its advice is "that a set of windows is used with only one synchronization mechanism at a time."
Portability caveat: implementors may restrict lock-synchronized RMA to memory from `MPI_Alloc_mem`,
`MPI_Win_allocate`, or `MPI_Win_attach`, because passive target without shared memory "may require an
asynchronous software agent."

**Global shared lock.** `MPI_Win_lock_all(assert, win)` / `MPI_Win_unlock_all(win)` opens a shared
access epoch to *every* process in the window. "This routine is not collective — the ALL refers to a
lock on all members of the group of the window" [MPI-3.1 §11.5.3]. There is deliberately **no
exclusive lock_all** [Gerstenberger et al. 2013, §2.3]. This is the mode in which modern MPI-3 RMA
code is actually written: lock_all once at startup, then do everything with atomics and flushes, and
notify peers out-of-band.

**The flush family** — callable only inside passive-target epochs [MPI-3.1 §11.5.4]:

| Call | Completes | Where |
|---|---|---|
| `MPI_Win_flush(rank, win)` | all my outstanding ops to `rank` | origin **and** target |
| `MPI_Win_flush_all(win)` | all my outstanding ops to all targets | origin **and** target |
| `MPI_Win_flush_local(rank, win)` | all my outstanding ops to `rank` | **origin only** (buffers reusable) |
| `MPI_Win_flush_local_all(win)` | all my outstanding ops, any target | **origin only** |
| `MPI_Win_sync(win)` | nothing — synchronizes private and public window copies | local memory only |

`MPI_Win_sync` "has the effect of ending and reopening an access and exposure epoch on the window
(note that it does not actually end an epoch or complete any pending MPI RMA operations)"
[MPI-3.1 §11.5.4]. It is the memory-model barrier, not a completion call. In foMPI, all four flush
variants share one implementation — a DMAPP remote bulk completion plus an x86 `mfence`, adding 78
x86 instructions to the critical path [Gerstenberger et al. 2013, §2.3].

### 2.5 Local vs remote completion — the distinction to steal

This is worth stating precisely, because it is the axis along which every RMA API is designed.

- **Local completion** ⇒ the origin's *buffers* are free. For put/accumulate: the source buffer may
  be overwritten. For get/get_accumulate: the destination buffer holds valid data. Achieved by
  `MPI_Wait` on an `MPI_Rxxx` request, or by `MPI_Win_flush_local[_all]`. Says **nothing** about
  whether the target has seen anything [Hoefler et al. 2015, §2.4].
- **Remote completion** ⇒ the effect is committed in the *target's* public window copy and visible to
  other operations. Achieved by `MPI_Win_flush[_all]`, `MPI_Win_unlock[_all]`, `MPI_Win_fence`, or
  (target-side) `MPI_Win_wait`. In the axiomatic model, a flush or unlock generates a *virtual
  synchronization action* at the target, which is what orders it against the target's own virtual
  communication actions [Hoefler et al. 2015, §3.2.2, eqs. 12–17].
- **Neither is process synchronization.** `MPI_Win_complete` gives origin completion but not target
  notification. `MPI_Win_flush` gives remote completion but the target does not *learn* anything.
  Under the unified model the target will eventually observe the value if it polls, and MPI
  guarantees eventual arrival — but "MPI provides no timing guarantees, and thus the process may need
  to wait for an unbounded number of steps" [Hoefler et al. 2015, §3.5]. Fence and PSCW are the only
  modes that bundle both `hb` and `co`; passive target deliberately separates them.

---

## 3. Atomics, and building higher-level synchronization on RMA

### 3.1 A CAS/FAO-based distributed mutex (MCS over RMA)

"The new atomic operations added in MPI-3 make it possible to build asynchronous, distributed,
lock-free data structures" [Hoefler et al. 2015, §4.3]. The canonical demonstration is the
Mellor-Crummey/Scott queue lock [Mellor-Crummey & Scott 1991] rendered in MPI-3 RMA. The layout, per
Hoefler et al. §4.3: a window in which every process exposes one integer *queue element* holding a
`next` pointer (a rank, initialized `MPI_PROC_NULL`) at `ELEM_DISP = 0`; the process hosting the
mutex exposes a second integer *tail* pointer at `TAIL_DISP = sizeof(int)`, also initialized
`MPI_PROC_NULL`. All processes call `MPI_Win_lock_all` once, "as accesses will be performed by using
only atomic operations."

```
  /* MCS mutex over MPI-3 RMA passive target.  Structure follows
     Hoefler et al. 2015 §4.3 (Listings 2-3) and Mellor-Crummey & Scott 1991.
     Exact code shape adapted; treat line-by-line detail as [UNVERIFIED]. */

  #define ELEM_DISP 0                 /* my next-pointer  */
  #define TAIL_DISP 1                 /* tail, at rank `home` only */
  const int NIL = MPI_PROC_NULL;

  void mcs_acquire(int me, int home, MPI_Win win, MPI_Comm comm) {
      int prev, nil = NIL;
      /* clear my next pointer before enqueuing */
      MPI_Accumulate(&nil, 1, MPI_INT, me, ELEM_DISP, 1, MPI_INT, MPI_REPLACE, win);
      MPI_Win_flush(me, win);

      /* atomically swap myself into the tail; `prev` = old tail */
      MPI_Fetch_and_op(&me, &prev, MPI_INT, home, TAIL_DISP, MPI_REPLACE, win);
      MPI_Win_flush(home, win);        /* remote completion: prev is valid */

      if (prev != NIL) {               /* lock is held; splice in behind prev */
          MPI_Accumulate(&me, 1, MPI_INT, prev, ELEM_DISP, 1, MPI_INT,
                         MPI_REPLACE, win);
          MPI_Win_flush(prev, win);
          /* RMA HAS NO NOTIFICATION: fall back to a two-sided message. */
          MPI_Recv(NULL, 0, MPI_BYTE, prev, MCS_TAG, comm, MPI_STATUS_IGNORE);
      }
      /* prev == NIL  =>  I am the head: I hold the lock. */
  }

  void mcs_release(int me, int home, MPI_Win win, MPI_Comm comm) {
      int next, nil = NIL, old;

      /* fast path: read my own next pointer atomically */
      MPI_Fetch_and_op(NULL, &next, MPI_INT, me, ELEM_DISP, MPI_NO_OP, win);
      MPI_Win_flush(me, win);

      if (next == NIL) {
          /* maybe nobody is queued: try to reset the tail from me -> NIL */
          MPI_Compare_and_swap(&nil, &me, &old, MPI_INT, home, TAIL_DISP, win);
          MPI_Win_flush(home, win);
          if (old != me) {
              /* a successor swapped in but has not yet written my next ptr;
                 spin until it does (guaranteed to arrive: unified model). */
              do {
                  MPI_Fetch_and_op(NULL, &next, MPI_INT, me, ELEM_DISP,
                                   MPI_NO_OP, win);
                  MPI_Win_flush(me, win);
              } while (next == NIL);
          }
      }
      if (next != NIL)
          MPI_Send(NULL, 0, MPI_BYTE, next, MCS_TAG, comm);   /* hand off lock */
  }
```

Three things to notice. (i) The whole algorithm runs inside one `lock_all` shared epoch; MPI's own
locks are used for *epoch* management, not mutual exclusion. (ii) `MPI_Win_flush` after every atomic
is mandatory — atomics are nonblocking, so the returned `prev`/`old` value is not valid until remote
completion. (iii) **The lock handoff uses `MPI_Send`/`MPI_Recv`, not RMA.** That is not a stylistic
choice; it is the notification gap (§3.3).

### 3.2 Atomic counters, work-stealing queues, distributed queues

The same three atomics give the standard lock-free repertoire:

- **Shared work counter / self-scheduling loop.** One rank hosts an `int64` at a known displacement.
  Each worker does `MPI_Fetch_and_op(&one, &my_index, MPI_INT64_T, home, CTR, MPI_SUM, win);
  MPI_Win_flush(home, win);` and processes item `my_index`. One network atomic per item claimed,
  no coordinator, no polling loop. foMPI notes shared locks and lock_all "only take one remote atomic
  update operation" when uncontended [Gerstenberger et al. 2013, §2.3].
- **Claim-by-CAS.** For an array of work-item state words, `MPI_Compare_and_swap(&CLAIMED, &FREE,
  &old, ...)`; the claim succeeded iff `old == FREE`. This is idempotent and safe under arbitrary
  concurrency and requires no lock at all.
- **Distributed queues / work stealing.** A per-victim head/tail pair updated with fetch-and-op for
  the owner's local end and CAS for the thief's steal end; the MCS queue above is a degenerate
  single-slot case. Hoefler et al. present MCS precisely as the demonstration "of a more complex data
  structure in the passive mode model" [Hoefler et al. 2015, §5].
- **Reader-writer locks.** foMPI builds MPI's own `MPI_Win_lock` this way: a 64-bit word per process
  where the high-order bit is the writer flag and the remaining bits count shared holders; a shared
  lock is one atomic fetch-and-add, an exclusive lock is a CAS-with-zero. A second global lock word
  at a designated master splits into a lock_all counter and an exclusive counter, so exclusive
  requests fetch-and-add the writer part, back off if any global shared lock is present, and retry
  with exponential backoff [Gerstenberger et al. 2013, §2.3].

### 3.3 The notification gap

MPI RMA has no way for a target to be told that a remote operation landed. "in contemporary RMA
programming systems, the widely used producer-consumer pattern can only be implemented
inefficiently, incurring the overhead of an additional round trip message" [Belli & Hoefler 2015].
The three workarounds all cost something:

1. **Poll a flag byte in the window.** Correct only in the unified model, burns CPU, and requires an
   extra ordering guarantee between the payload and the flag (put + flush + put-flag + flush).
2. **Send a two-sided message.** Reintroduces exactly the matching overhead RMA was meant to avoid —
   see the `MPI_Send` in the MCS release path above.
3. **Use active target.** Fence/PSCW bundle notification into the epoch close, but at the cost of a
   barrier or of declaring the full neighborhood in advance.

**Notified Access** [Belli & Hoefler 2015] closes the gap: add a notified variant of every RMA
operation carrying an additional integer **tag**, and give the target a queue it can query
(`Notify_init` with source rank, tag, and expected count; matching against an unexpected queue by
source and tag, mirroring MPI message matching). Implemented as **foMPI-NA** on Cray Gemini/Aries by
exploiting uGNI completion queues, which allow a 4-byte integer to be attached to each transfer. The
reported overhead is two cache misses, lower than other point-to-point synchronization mechanisms, up
to 50% speedup on small messages, and up to 2× over message passing for a task-based Cholesky
factorization; the paper argues "Notified Access is a valuable primitive for any RMA system" and gives
design guidance for NICs. `[UNVERIFIED: whether any notified-RMA proposal has been adopted into the
MPI standard as of MPI-4.1/5.0 — I found no such chapter.]`

**foMPI** itself [Gerstenberger et al. 2013] is the existence proof that MPI-3 RMA is not just a nice
specification: a full MPI-3.0 RMA implementation over Cray DMAPP (inter-node) and XPMEM (intra-node),
with O(log p) time and space per process, "no remote software agent," bufferless protocols, and
performance "comparable to, or better than UPC and Fortran Coarrays in terms of latency, bandwidth,
and message rate," demonstrating >13% full-application speedup over MPI-1 on more than half a million
MPI processes. It also provides analytic performance models for every RMA call, which is the kind of
artifact we should imitate for AgentMPI.

---

## 4. MPI-3 shared memory and the "MPI+MPI" model

MPI-3 lets processes on one node map each other's memory directly, giving an alternative to MPI+OpenMP
that keeps a single programming model [Hoefler et al. 2012; Hoefler et al. 2013].

**Step 1 — find the sharing island.** `MPI_Comm_split_type(comm, MPI_COMM_TYPE_SHARED, key, info,
&shmcomm)` splits a communicator into sub-communicators on which a shared-memory region can be
created [Hoefler et al. 2013, §2]. It generalizes `MPI_Comm_split` by taking a *type* instead of a
color. The info argument may carry architecture hints to restrict the split to a NUMA socket or cache
level; MPI-3 defines no specific keys, but implementations are expected to expose NUMA/cache
management through them. `[UNVERIFIED: MPI-4 additionally defines MPI_COMM_TYPE_HW_GUIDED and
MPI_COMM_TYPE_HW_UNGUIDED for hardware-guided splitting.]`

**Step 2 — allocate the shared window.** `MPI_Win_allocate_shared(size, disp_unit, info, comm,
&baseptr, &win)` collectively allocates ≥ `size` bytes per process, "shared among all MPI processes in
comm," returning a pointer usable for **direct load/store** on the caller. "The locally allocated
memory can be the target of load/store accesses by remote MPI processes" [MPI-4.1 §13.2.3]. `size`
may differ per process and may be 0. **It is the user's responsibility** to ensure the communicator is
a genuine shared-memory domain — passing one that is not is erroneous. By default the allocation is
**contiguous across processes in rank order**; the `alloc_shared_noncontig` info key relaxes this,
typically letting the implementation place each rank's segment in its own NUMA domain.

**Step 3 — get peers' pointers.** `MPI_Win_shared_query(win, rank, &size, &disp_unit, &baseptr)`
returns the local address at which `rank`'s segment is mapped [Hoefler et al. 2013]. Note that each
process may pick its own local window address and size independently of the others
[Hoefler et al. 2015, Fig. 3], so pointers are *not* portable between processes — you exchange offsets,
not addresses.

**Step 4 — access with the compiler, not the library.** Ordinary C/Fortran assignments and
expressions; no `MPI_Put`/`MPI_Get` needed, and what the compiler emits "is much faster than if you
call a library routine like MPI_Put or MPI_Get" [HLRS MOOC, week 3–4]. Hoefler et al. report an average
40% performance improvement from the new interface in MPICH2/Open MPI [Hoefler et al. 2013, abstract].

**The consistency caveats.** A shared window still has a memory model. "The consistency of load/store
accesses from/to the shared memory as observed by the user program depends on the architecture. A
consistent view can be created in the unified memory model by utilizing the window synchronization
functions… or explicitly completing outstanding store accesses (e.g., by calling MPI_WIN_FLUSH). **MPI
does not define the semantics for accessing shared window memory in the separate memory model**"
[MPI-4.1 §13.2.3]. And from the formal side: "there are no guarantees about the consistency order of
[local load] and [local store] actions (which can now be observed directly by remote processes), as
this is a function of the architecture's memory model (e.g. x86 or POWER)" [Hoefler et al. 2015, §3.6].
So `MPI_Win_sync` is used as the portable memory-barrier idiom — `MPI_Win_sync` + `MPI_Barrier` +
`MPI_Win_sync` is the standard "publish my stores, then read yours" sequence — and MPI's locks may be
used to protect load/store accesses to a shared window [MPI-3.1 §11.5.3]. This is precisely a
release/acquire discipline expressed in MPI vocabulary.

---

## 5. Dynamic process management (MPI-2)

MPI-2 added a *Dynamic Process Model*, motivated by experience with PVM [MPI-4.1 §12.1].

**Spawning.** `MPI_Comm_spawn(command, argv, maxprocs, info, root, comm, &intercomm, errcodes)` "tries
to start maxprocs identical copies of the MPI program specified by command, establishing communication
with them and returning an intercommunicator" [MPI-4.1 §12.7.4]. Key semantics:

- **The children get their own `MPI_COMM_WORLD`, separate from the parents'.** This is the crucial
  structural fact: there is no global world.
- Spawn is collective over `comm` and "may not return until MPI_INIT has been called in the children";
  symmetrically, children's `MPI_INIT` may not return until all parents have called spawn. "In this
  sense, MPI_COMM_SPAWN in the parents and MPI_INIT in the children form a collective operation over
  the union of parent and child processes."
- The returned intercommunicator has the parents as the local group and the children as the remote
  group, ordered as `comm` in the parents and as the children's `MPI_COMM_WORLD` in the children.
- Children retrieve the same intercommunicator via `MPI_Comm_get_parent(&parent)`; it "is created
  implicitly inside of MPI_INIT."
- If fewer than `maxprocs` can be started, the standard raises `MPI_ERR_SPAWN` unless the `soft` info
  key declares a set of acceptable counts; the actual count is the size of the remote group.
- `MPI_Comm_spawn_multiple` starts several different binaries (or the same binary with different
  argv) and, unlike repeated spawns, places them all in **one** `MPI_COMM_WORLD`, returning one
  intercommunicator [MPI-4.1 §12.7.1].
- `MPI_Intercomm_merge(intercomm, high, &intracomm)` flattens an intercommunicator into a single
  intracommunicator, with `high` selecting group ordering.

**The `info` hint mechanism.** Spawn's `info` argument is the standard's escape hatch to the resource
manager: "it may optionally use an info argument to tell the runtime environment where or how to start
the process. This extra information may be [meaningless] to MPI" [MPI-2.2 §10.3]. Reserved keys
include `host`, `arch`, `wdir`, `path`, `file`, and `soft`. Info objects are the general MPI mechanism
for `(key, value)` string hints and assertions, used identically on windows (`accumulate_ordering`,
`accumulate_ops`, `same_size`, `same_disp_unit`, `alloc_shared_noncontig`), files (ROMIO's `cb_nodes`,
`cb_buffer_size`, `romio_ds_read`), communicators, and sessions.

**`MPI_UNIVERSE_SIZE`.** A predefined attribute on `MPI_COMM_WORLD` giving "the total number of
processes that are expected"; "An application typically subtracts the size of MPI_COMM_WORLD from
MPI_UNIVERSE_SIZE to find out how many processes it should spawn." It is set at `MPI_Init` "by the
application startup mechanism in a way not specified by MPI" (an `mpiexec` argument, a batch-scheduler
query, an environment variable, or spawn info), is never updated by MPI, and may be absent entirely —
"An implementation may not support the ability to set MPI_UNIVERSE_SIZE, in which case the attribute
MPI_UNIVERSE_SIZE is not set." It is "a recommendation, not necessarily a hard limit"
[MPI-4.1 §12.7.9].

```
  /* MPI_Comm_spawn: manager sizes its worker pool from the universe. */
  int main(int argc, char **argv) {
      int wsize, *usize_p, have_usize, nspawn;
      MPI_Comm workers;  MPI_Comm parent;

      MPI_Init(&argc, &argv);
      MPI_Comm_get_parent(&parent);
      if (parent != MPI_COMM_NULL) {           /* ---- I am a child ---- */
          int rank; MPI_Comm_rank(MPI_COMM_WORLD, &rank);   /* CHILD world! */
          /* parent is an INTERcommunicator: remote group = the manager(s) */
          worker_loop(parent);
          MPI_Comm_disconnect(&parent);
          MPI_Finalize(); return 0;
      }
      /* ---- I am the manager ---- */
      MPI_Comm_size(MPI_COMM_WORLD, &wsize);
      MPI_Comm_get_attr(MPI_COMM_WORLD, MPI_UNIVERSE_SIZE, &usize_p, &have_usize);
      nspawn = have_usize ? (*usize_p - wsize) : 4;   /* fallback if unset */

      MPI_Info info; MPI_Info_create(&info);
      MPI_Info_set(info, "soft", "1:16");        /* accept 1..16 children */
      MPI_Info_set(info, "wdir", "/scratch/job");

      MPI_Comm_spawn("./worker", MPI_ARGV_NULL, nspawn, info,
                     /*root=*/0, MPI_COMM_SELF, &workers, MPI_ERRCODES_IGNORE);
      /* `workers` is an INTERcommunicator: local group = me, remote = children */
      MPI_Comm all; MPI_Intercomm_merge(workers, /*high=*/0, &all);  /* optional */
      dispatch(workers);
      MPI_Comm_disconnect(&workers);
      MPI_Info_free(&info); MPI_Finalize(); return 0;
  }
```

**Client/server rendezvous.** For processes not related by spawn, MPI-2 provides a port mechanism
[MPI-4.1 §12.9]. `MPI_Open_port(info, port_name)` returns "a system-supplied string that encodes a
low-level network address at which a server can be contacted. Typically this is an IP address and a
port number, but an implementation is free to use any protocol." The server calls
`MPI_Comm_accept(port_name, info, root, comm, &intercomm)`; the client calls
`MPI_Comm_connect(port_name, ...)`. Because handing the string to the client is awkward, an
implementation *may* offer a name service: `MPI_Publish_name(service_name, info, port_name)` and
`MPI_Lookup_name`. The standard is candid that "MPI does not require a nameserver, so not all
implementations will be able to support all of the above scenarios," and lists three descending
portability tiers: no publishing (most portable, transfer the port name by hand), `MPI_Publish_name`
(portable across implementations that provide it, with a required fallback), or your own out-of-band
mechanism (arbitrary flexibility, no portability). `MPI_Comm_join(fd, &intercomm)` bootstraps an
intercommunicator from an already-connected socket file descriptor — MPI's explicit acknowledgement
that sometimes the rendezvous must happen outside MPI. `MPI_Comm_disconnect` tears connections down.

**Why nobody uses it.** The reasons are structural, not incidental:

- **Batch systems allocate statically.** The standard itself concedes: "Batch queueing systems
  generally allocate resources before an application begins, enforce limits on resource use…, and do
  not allow a change in resource allocation after a job begins" [MPI-2.2 §10.2].
- **No resource-manager integration.** "most RMS operate in ways that make it difficult to add dynamic
  processes support, which prevents its usage in production systems. For this reason, jobs run with a
  fixed number of processes" [Iserte et al., malleability survey, §4]. "most MPI implementations such
  as MPICH and Open MPI support dynamic process creation only in pre-allocated resources to the
  application. This limits the benefits of the added spawn operations" [Chadha, TUM dissertation].
- **Only the application can initiate change.** "a change in the resources of a running MPI
  application can only be initiated by the application. This fact is counterproductive since the
  application, unlike a resource and job management system, does not have a holistic view about the
  pending jobs and free resources" [Chadha]. Optimal dynamic resource management needs a *cooperative*
  protocol, since purely system-driven schemes lack application knowledge (reconfiguration points,
  redistribution cost, phases) and purely application-driven schemes lack global state
  [Design Principles of DRM, 2024].
- **High overhead and blocking collectives.** Spawn is collective and effectively synchronous with the
  children's `MPI_Init`; it is "rarely used due to several limitations such as high-performance
  overhead" [Extending SLURM, 2020].
- **No fault tolerance.** MPI's default error behavior is to abort; a dynamically grown job has no
  standard way to survive losing a member. (ULFM is the research response; malleability work such as
  Invasive MPI's `MPI_Init_adapt` / `MPI_Probe_adapt` / `MPI_Comm_adapt_begin` /
  `MPI_Comm_adapt_commit` and Slurm extensions are the resource-management response
  [Extending SLURM, 2020].)

---

## 6. MPI Sessions (MPI-4) — the most important section for the agent analogy

### 6.1 What the World Model got wrong

MPI-4 states the indictment of the classical model plainly: "MPI cannot be initialized from different
application components without a priori knowledge or coordination; MPI cannot be initialized more than
once; and MPI cannot be reinitialized after MPI_FINALIZE has been called" [MPI-4.1 §12.3]. Add the
scalability argument: `MPI_COMM_WORLD` forces an all-to-all-connected global object to exist at
startup whether or not the application needs it, which "was viewed as a potential scalability
bottleneck" at exascale [Implementing True MPI Sessions, 2026].

The concrete failure mode is *library composition*. In the World Model, a library that wants to use
MPI must either (a) demand that the application call `MPI_Init` and hand it a communicator, (b) call
`MPI_Init` itself and race with every other component that does the same, or (c) call
`MPI_Initialized` and hope. There is no way for two independently authored libraries to each acquire
MPI resources without agreeing in advance. And whoever calls `MPI_Finalize` ends MPI for everybody.

### 6.2 The Sessions Model

MPI-4 adds a second, coexisting initialization model. "With this approach, an MPI application, or
components of the application, can instantiate MPI resources for the specific communication needs of
this component." In the Sessions Model `MPI_COMM_WORLD` is not valid as a communicator, and
`MPI_INFO_ENV` is not valid as an info object; an application may use both models concurrently
[MPI-4.1 §12.3, §12.1].

The pipeline is `Session → process set → Group → Communicator → (Window | File)`:

1. **`MPI_Session_init(info, errhandler, &session)`** — instantiates a session handle usable "to query
   the runtime system about characteristics of the job within which the process is running, as well as
   other system resources." "Session instantiation is intended to be a lightweight operation. An MPI
   process may instantiate multiple Sessions. MPI_SESSION_INIT is always thread safe; multiple threads
   within an application may invoke it concurrently" [MPI-4.1 §12.3.1]. The requested thread-support
   level is passed as the *string-valued* info key `thread_level` (e.g. `"MPI_THREAD_MULTIPLE"`),
   **not** the integer of `MPI_Init_thread`, and it is per-session: "It is possible to specify
   different thread support levels when creating different MPI Session handles. Thus different
   components of an application can use different thread support levels" [MPI-4.1 §12.3]. Sessions can
   be initialized and finalized multiple times in a process [Open MPI 5.0 docs, `MPI_Session_init`].
2. **Process sets.** "Process sets differ from MPI Groups in that they are simply *names* for lists of
   MPI processes." Two are required of every implementation — `mpi://WORLD` and `mpi://SELF` — and the
   runtime may define arbitrarily many more, discovered at runtime via
   `MPI_Session_get_num_psets(session, info, &n)` and
   `MPI_Session_get_nth_pset(session, info, n, &len, name)`, with per-pset metadata from
   `MPI_Session_get_pset_info`. The Open MPI prototype additionally defined `mpi://shared` (the
   processes on the local node) and obtained all other psets from PMIx: "Additional process sets are
   supported and must be provided by PMIx. When a process set is used to create an MPI Group, the
   prototype queries the underlying PMIx implementation to discover the associated MPI processes"
   [Hjelm et al. 2019]. (The MPI-4 standard spells the required names in uppercase, `mpi://WORLD` /
   `mpi://SELF`; the pre-standard proposal used lowercase `mpi://world` / `mpi://self`.)
3. **`MPI_Group_from_session_pset(session, pset_name, &group)`** — "creates a group newgroup using the
   provided session handle and process set… If the pset_name does not exist, MPI_GROUP_NULL will be
   returned." Like all group constructors it is **local** — no communication [MPI-4.1 §7.3.2].
4. **`MPI_Comm_create_from_group(group, stringtag, info, errhandler, &comm)`** — collective over the
   group, not over any pre-existing communicator. "the set of MPI processes involved in the creation of
   the new intra-communicator is specified by a group argument, rather than the group associated with
   a pre-existing communicator." All members must pass the same members in the same order and an
   identical `stringtag`. The `stringtag` is the disambiguator for concurrent creations — "If multiple
   threads at a given MPI process perform concurrent MPI_COMM_CREATE_FROM_GROUP operations, the user
   must distinguish these operations by providing different stringtag arguments" — bounded by
   `MPI_MAX_STRINGTAG_LEN`, and the standard recommends **reverse domain name notation** to keep tags
   globally unique across independently authored components. `MPI_GROUP_EMPTY` makes the call local,
   returning `MPI_COMM_NULL` [MPI-4.1 §7.4.2]. `MPI_Intercomm_create_from_groups` does the same for
   inter-communicators from two disjoint groups.
5. **Isolation.** "MPI objects derived from different MPI Session handles shall not be intermixed with
   each other in a single MPI procedure call," nor with World-Model objects, nor with the
   communicator from `MPI_Comm_get_parent` or `MPI_Comm_join`. Generalized requests are exempt
   [MPI-4.1 §12.3]. Every session must be released with `MPI_Session_finalize`.

```
  /* MPI Sessions: a self-contained library component initializes MPI
     without MPI_Init, without MPI_COMM_WORLD, and without coordinating
     with any other component. */

  static MPI_Session  s     = MPI_SESSION_NULL;
  static MPI_Comm     libcomm = MPI_COMM_NULL;

  int mylib_start(void) {
      MPI_Info info; MPI_Group g;
      int n, i, len; char pset[MPI_MAX_PSET_NAME_LEN];
      const char *want = "mpi://WORLD";     /* default */

      MPI_Info_create(&info);
      MPI_Info_set(info, "thread_level", "MPI_THREAD_MULTIPLE");
      MPI_Session_init(info, MPI_ERRORS_RETURN, &s);   /* LOCAL, lightweight */
      MPI_Info_free(&info);

      /* Ask the runtime what process sets exist.  A resource manager may
         expose job-, node-, or allocation-shaped psets beyond the two
         required names -- this is the malleability hook. */
      MPI_Session_get_num_psets(s, MPI_INFO_NULL, &n);
      for (i = 0; i < n; i++) {
          len = sizeof pset;
          MPI_Session_get_nth_pset(s, MPI_INFO_NULL, i, &len, pset);
          if (strcmp(pset, "mylib://SOLVERS") == 0) { want = pset; break; }
      }

      MPI_Group_from_session_pset(s, want, &g);         /* LOCAL */
      if (g == MPI_GROUP_NULL) return -1;

      /* Collective over g only.  The stringtag makes this creation distinct
         from every other component's -- reverse-DNS per MPI-4.1 advice. */
      MPI_Comm_create_from_group(g, "org.example.mylib.v2",
                                 MPI_INFO_NULL, MPI_ERRORS_RETURN, &libcomm);
      MPI_Group_free(&g);
      return 0;
  }

  void mylib_stop(void) {                  /* does NOT end MPI for anyone else */
      if (libcomm != MPI_COMM_NULL) MPI_Comm_free(&libcomm);
      MPI_Session_finalize(&s);
  }
```

### 6.3 Why this matters

Three distinct payoffs, and they are worth separating:

- **Composability.** Independently authored components each hold their own session, their own thread
  level, their own error handler, and their own communicators, with a standard-mandated isolation rule
  preventing accidental cross-talk. No component's finalize harms another. This is the property MPI
  spent 25 years without.
- **Scalability.** Resources are allocated "based on its communication requirements" rather than
  eagerly for a global world. MPICH's "true Sessions" work reports that decoupling from an internal
  world communicator and building "sparsely connected" topologies is where the initialization
  scalability benefit actually comes from — a naive Sessions layer over an internal world communicator
  satisfies the API but not the intent [Implementing True MPI Sessions, 2026].
- **Malleability.** Because process sets are *runtime-defined names* queried at runtime rather than a
  fixed world fixed at launch, the runtime (PMIx, a scheduler) can publish new psets, and a component
  can materialize a group and communicator for one. This is the standardized seam through which
  dynamic resource sets can enter without the spawn machinery's problems. Sessions is what MPI-2
  dynamic processes should have been: not "make me more processes," but "tell me what sets of
  processes exist, and let me form a communication context over the one I care about."

---

## 7. MPI-IO, briefly: shared persistent state gets its own consistency model

MPI-2 also added parallel I/O, and the structural lesson is that a *durable shared object* needs a
consistency model distinct from the one governing messages.

- **File views.** `MPI_File_set_view(fh, disp, etype, filetype, datarep, info)` gives each process a
  logical, strided window onto the file described with MPI datatypes: `etype` is the unit of access,
  `filetype` the (possibly noncontiguous) per-process pattern, `disp` the byte offset where the view
  begins. A process then reads and writes in terms of `etype` counts and never computes byte offsets.
- **Collective I/O.** `MPI_File_read_all`/`write_all` (and `_at_all`, `_ordered`, plus the split
  collectives `..._begin`/`..._end`) declare that all processes in the file's group are participating,
  which is what licenses global optimization.
- **Two-phase I/O** [del Rosario, Bordawekar & Choudhary 1993], a.k.a. collective buffering, is the
  optimization that pays for the collective declaration: "the collection of independent I/O operations
  that make up the collective operation are analyzed to determine what data regions must be
  transferred. These regions are then split up amongst a set of *aggregator* processes that will
  actually interact with the file system. In the case of a read, these aggregators first read their
  regions from disk and redistribute the data to the final locations, while in the case of a write,
  data is first collected from the processes before being written to disk by the aggregators"
  [ROMIO Users Guide, §Optimizations]. Tunable via `cb_nodes`, `cb_buffer_size`, `romio_cb_read`,
  `romio_cb_write`. The companion technique for *independent* noncontiguous access is **data sieving**
  [Thakur, Gropp & Lusk 1999]: read one large block spanning the wanted regions including the unwanted
  "holes," then extract locally — one I/O call instead of many, at the cost of extra bytes moved.
- **Consistency.** "MPI provides three levels of consistency: sequential consistency among all
  accesses using a single file handle, sequential consistency among all accesses using file handles
  created from a single collective open with atomic mode enabled, and user-imposed consistency among
  accesses other than the above… User-imposed consistency may be obtained using program order and
  calls to `MPI_FILE_SYNC`" [MPI-5.0 §File Consistency]. Atomic mode is requested with
  `MPI_File_set_atomicity`. `MPI_File_sync` is collective, flushes the caller's writes to the storage
  device, and makes other processes' updates visible to the caller's subsequent reads; opening and
  closing implicitly perform one. Note the same public/private-copy shape as RMA: a "sync" call that
  reconciles views, plus a requirement that the user supply non-concurrency by other means.

An important interaction: ROMIO's collective-write algorithm can skip byte-range locking during
read-modify-write of partial blocks precisely because MPI-IO's consistency semantics "do not
automatically guarantee consistency" for writes from outside the collective, so no other participant
can be touching that process's file domain [Thakur et al. 1999, §4.2.2]. The weak default model is
what makes the fast path legal.

---

## 8. Tools interfaces

### 8.1 PMPI: the profiling interface and its mechanics

MPI has required, since MPI-1, that every MPI procedure be callable under a second, name-shifted
entry point `PMPI_Xxx`, so that a tool can define `MPI_Xxx`, do its bookkeeping, and call
`PMPI_Xxx` for the real work. The standard spells out two implementation strategies
[MPI-3.1 §14.2.7]:

```
  /* (a) Systems with weak symbols */
  #pragma weak MPI_Example = PMPI_Example
  int PMPI_Example(/* args */) { /* real implementation */ }
```

"The effect of this #pragma is to define the external symbol MPI_Example as a weak definition. This
means that the linker will not complain if there is another definition of the symbol (for instance in
the profiling library); however if no other definition exists, then the linker will use the weak
definition."

```
  /* (b) Systems without weak symbols: preprocessor name mangling */
  #ifdef PROFILELIB
  #  define FUNCTION(name) P##name
  #else
  #  define FUNCTION(name) name
  #endif
  int FUNCTION(MPI_Example)(/* args */) { /* real implementation */ }

  /* link order matters: */
  %  cc ... -lmyprof -lpmpi -lmpi
```

The standard imposes a real constraint on implementers to make this work: "It is required that the
standard MPI library be built in such a way that the inclusion of MPI functions can be achieved one
at a time… this is necessary so that the author of the profiling library need only define those MPI
functions that she wishes to intercept, references to any others being fulfilled by the normal MPI
library." That is the whole design: **selective, per-symbol interposition with automatic
pass-through.** Its cost is the corresponding limitation: exactly **one** tool can own the `MPI_Xxx`
symbol. "using and linking more than one tool library at a time is not possible. Therefore, all of the
functionality necessary to achieve a profiling goal must be implemented in a single tool"
[Elis, MSc thesis on QMPI].

### 8.2 MPI_T: the MPI Tool Information Interface (MPI-3)

MPI-3 added a second, orthogonal tools interface that exposes the *implementation's internals* rather
than the application's calls. Everything is prefixed `MPI_T_`, is initialized separately with
`MPI_T_init_thread(required, &provided)` and torn down with `MPI_T_finalize()`, and — critically —
uses a **separate handle space** from the rest of MPI "because they must be usable before MPI_INIT and
after MPI_FINALIZE" and "accessing handles, in particular for performance variables, can be time
critical" [MPI-3.1 §14.3.3]. The standard requires only a C binding; Fortran support is optional
[Cornell Virtual Workshop, MPI_T].

**Control variables (cvars)** — knobs. "These variables can typically be used by the user to fine tune
properties and configuration settings of the MPI implementation… A typical example that is available in
several existing MPI implementations is the ability to specify an 'eager limit'"
[MPI-3.1 §14.3.6]. API shape: `MPI_T_cvar_get_num(&n)`, then
`MPI_T_cvar_get_info(i, name, &namelen, &verbosity, &datatype, &enumtype, desc, &desclen, &bind,
&scope)`, then `MPI_T_cvar_handle_alloc` / `MPI_T_cvar_read` / `MPI_T_cvar_write` /
`MPI_T_cvar_handle_free`. `bind` says what kind of MPI object the variable attaches to (none, a
communicator, a window, a datatype…) and `scope` says when a write is legal. Most cvars are read-only
after `MPI_Init`. Open MPI exposes over 1,000 cvars, one per MCA component parameter
[Eberius, ICL 2018].

**Performance variables (pvars)** — meters. Same discovery pattern
(`MPI_T_pvar_get_num`, `MPI_T_pvar_get_info(..., &var_class, ..., &bind, &readonly, &continuous,
&atomic)`), but reads go through an explicit **experiment session**:

```
  MPI_T_init_thread(MPI_THREAD_SINGLE, &provided);
  MPI_T_pvar_get_num(&num);
  for (i = 0; i < num; i++) { MPI_T_pvar_get_info(i, name, ...); /* pick */ }
  MPI_T_pvar_session_create(&sess);
  MPI_T_pvar_handle_alloc(sess, idx, /*obj=*/NULL, &h, &count);
  MPI_T_pvar_start(sess, h);
      do_work();
  MPI_T_pvar_read(sess, h, &value);      /* or _readreset / _reset / _stop  */
  MPI_T_pvar_handle_free(sess, &h);
  MPI_T_pvar_session_free(&sess);
  MPI_T_finalize();
```

Sessions exist "so that accesses to pvars in different sessions won't conflict"
[MPICH Tool_Interfaces design doc] — i.e. two concurrent tools can each hold their own counters over
the same underlying variable. Each pvar has a *class* (counter, state, level, size, percentage,
high/low watermark, aggregate, timer, generic) declaring how to interpret and combine it.

**Categories** group cvars and pvars hierarchically: `MPI_T_category_get_num`,
`..._get_info`, `..._get_cvars`, `..._get_pvars`, `..._get_categories`, and `MPI_T_category_changed`
so a tool can notice when the set changed. MPI-4 further adds a **callback-driven event-notification
interface** (`MPI_T_event_*`), distinct from pvars: pvars are polled state, events are pushed state
transitions, and MPI-4.1 notes the two sets of state transitions "may not be identical"
[MPI-4.1 §15.3.7].

### 8.3 QMPI and the multiple-tools problem

PMPI's single-owner limitation is the motivating gap for **QMPI** [Elis, Yang & Schulz 2019]: PMPI
"does not support modern software design principles nor the composition of multiple monitoring
solutions from multiple agents or sources." QMPI's design chains tools: each tool's interception calls
the *next* function in a chain rather than `PMPI_Xxx` directly, the order is held in a vector data
structure with one instance per tool, and a tool locates its successor via `QMPI_Table_query`. Tools
also need **context separation** — independent per-instance storage — so that the same tool may appear
multiple times in one chain. The prototype is itself implemented as a PMPI tool intercepting all ~360
MPI functions, loaded via a `TOOLS` environment variable listing shared objects, with a refactored
mpiP as the reference context-separated tool. PnMPI [Schulz & de Supinski] is the earlier answer to the
same problem, also built on PMPI. `[UNVERIFIED: QMPI has not, to my knowledge, been adopted into the
MPI standard; a Parallel Computing journal version exists (dblp key journals/pc/ElisYPMS20).]`

### 8.4 Tracing, profiling, and visualization ecosystem

| Tool | What it is |
|---|---|
| **Jumpshot / SLOG2** | MPICH's timeline viewer and its scalable logging format |
| **Vampir** | commercial timeline/statistics viewer; native format OTF, now OTF2 |
| **OTF2** | Open Trace Format 2: a record-based trace format unifying OTF (Vampir) and EPILOG (Scalasca), with a read/write API and selective access [Eschweiler et al. 2011] |
| **Score-P** | joint measurement infrastructure for Periscope, Scalasca, TAU, and Vampir; instrumentation via a `scorep` compiler prefix; outputs OTF2 traces and CUBE4 profiles [Knüpfer et al. 2012] |
| **TAU** | profiling/tracing toolkit, instrumentation + analysis |
| **HPCToolkit** | sampling-based, call-path profiles from unmodified optimized binaries |
| **mpiP** | lightweight statistical MPI profiler (LLNL); the QMPI reference tool |
| **Extrae / Paraver** | BSC's tracing library and its timeline/analysis GUI |
| **Darshan** | low-overhead I/O characterization, always-on in production at some facilities |

**What a trace event record contains.** OTF2 stores, per *location* (thread/process/GPU stream), a
temporally ordered sequence of records, plus separate definition records interned by reference. Every
event record begins with `(OTF2_LocationRef location, OTF2_TimeStamp timestamp)`; the rest is
type-specific [OTF2 event-record reference]:

- `Enter` / `Leave`: `+ OTF2_RegionRef region` — "indicates that the program enters/leaves a code
  region." These two records alone reconstruct the full call stack per location over time.
- `MpiSend` / `MpiIsend`: `+ uint32_t receiver, OTF2_CommRef communicator, uint32_t msgTag,
  uint64_t msgLength`. `MpiRecv` symmetric with `sender`.
- `MpiCollectiveBegin` / `MpiCollectiveEnd`: `+ OTF2_CollectiveOp collectiveOp, OTF2_CommRef
  communicator, uint32_t root, uint64_t sizeSent, uint64_t sizeReceived`.
- `RmaPut` / `RmaGet`: `+ OTF2_RmaWinRef win, uint32_t remote, uint64_t bytes, uint64_t matchingId`,
  where `matchingId` is "ID used for matching the corresponding completion record."
- `RmaOpCompleteBlocking` / `RmaOpCompleteNonBlocking` / **`RmaOpCompleteRemote`**:
  `+ win, matchingId`. Note that OTF2 models local and **remote** completion as *separate records
  matched by id* — exactly the local/remote distinction of §2.5, reified in the trace schema.
- `RmaAcquireLock` / `RmaTryLock` / `RmaReleaseLock`: `+ win, remote, uint64_t lockId,
  OTF2_LockType lockType`, where `remote` is `OTF2_UNDEFINED_UINT32` when all window processes are
  locked (i.e. `lock_all`).
- `Metric`: a sampled counter value referencing a metric class/instance definition.
- `MeasurementOnOff`, `ThreadFork`/`ThreadJoin`, `CommCreate`/`CommDestroy` (the latter valid only if
  the comm definition was flagged for create/destroy events, and required to be enclosed in a
  collective begin/end pair with `OTF2_COLLECTIVE_OP_CREATE_HANDLE`).

A space optimization worth noting for any agent trace format: consecutive events sharing a timestamp
omit it, storing it once per bundle [Eschweiler et al. 2011].

**What the standard timeline/Gantt view shows.** One horizontal track per location (rank, and nested
tracks per thread), time on the x-axis. Within a track, `Enter`/`Leave` pairs render as nested colored
bars whose color encodes the region or region *group* (user code vs MPI vs I/O vs idle), so the eye
reads the call stack vertically and the phase structure horizontally. Communication is overlaid as
lines between tracks: a line from the `MpiSend` on the sender's track to the matching `MpiRecv` on the
receiver's, whose slope visualizes latency and whose near-verticality signals a well-synchronized
transfer. Collectives render as a bracket spanning all participating tracks between
`MpiCollectiveBegin` and `MpiCollectiveEnd`, which makes load imbalance appear as ragged left edges —
early arrivers waiting. RMA renders as a line from the `RmaPut` on the origin track to the
`RmaOpCompleteRemote`, so an epoch appears as a fan of lines terminating at the flush. Counter tracks
(from `Metric` records) sit below as line plots on a shared time axis. Companion views: a flat/call-tree
profile with inclusive/exclusive time, a communication matrix (bytes or messages, source × destination
heat map), and a message-size histogram. The two things practitioners actually look for are *late
sender / late receiver* patterns (a wait-state pattern Scalasca detects automatically by replaying the
trace) and *imbalance at collectives*.

---

## 9. MPI-4 partitioned communication

**Motivation.** MPI's thread support was "designed as a process-level interface," which "has led to
implementations that treat function calls as critical regions and protect them with locks to avoid race
conditions" [Grant et al. 2019]. If 32 threads each produce a piece of one halo message, the choices
under MPI-3 are: serialize them behind a lock into one send (all threads wait for the slowest), or
issue 32 separate messages (32× the matching and header overhead). Partitioned communication is the
"hybrid of MPI models that has the best features of each," and it "leverages new network hardware
features that cannot be exploited with current MPI point-to-point semantics." The same mechanism
serves GPUs: a persistent channel that a kernel can trigger by "flipping a bit or setting a flag,"
avoiding the "large amounts of branching that reduce GPU performance through warp divergence"
[Sandia, PartComm]. Sandia calls the resulting behavior **earlybird** communication — data leaves as
soon as its partition is ready rather than waiting for a lagging worker; a reported model found that
"under ideal circumstances, only eight partitions were needed to save 90% of data transfer time
compared to traditional communication."

**Semantics** [MPI-4.1 §4.2]. Partitioned operations are *persistent*: setup is separated from
transfer, and the request is reused across iterations.

- `MPI_Psend_init(buf, partitions, count, datatype, dest, tag, comm, info, &request)` and
  `MPI_Precv_init(buf, partitions, count, datatype, source, tag, comm, info, &request)` are **local**
  calls. "The partitioned communication initialization includes inputs on the number of user-visible
  partitions on the send-side and receive-side, **which may differ**." At least one partition is
  required. Matching follows ordinary point-to-point rules on `(communicator, tag, source)`.
- `MPI_Start` / `MPI_Startall` activates the operation. "For send-side operations, neither initializing
  nor starting the operation enables transfer of any part of the user buffer." After a start, all
  partitions are *inactive*.
- `MPI_Pready(partition, request)` "notifies the MPI library that a specified portion of the data
  buffer (a specific partition) is ready to be sent," marking it active. Partitions are numbered from
  0 to `partitions-1`; calling `MPI_Pready` on an already-active partition is erroneous, as is naming
  an out-of-range partition. Batch forms: `MPI_Pready_range(low, high, request)` and
  `MPI_Pready_list(length, array_of_partitions, request)`.
- `MPI_Parrived(request, partition, &flag)` tests receive-side partial arrival. "Upon success, the
  receiver becomes free to access the indicated partition (as well as any others that previously
  completed for that operation)." It does **not** mark the request complete, may be called repeatedly,
  and returns `flag = true` for a null or inactive request.
- `MPI_Wait`/`MPI_Test` on the request is still required and means whole-operation completion. On the
  receive side, completion "indicates that the receive buffer contains all of the partitions."
- **The implementation is free to ignore the partitioning.** "MPI is free to choose how many transfers
  to do within a partitioned communication send independent of how many partitions are reported as
  ready… Aggregation of partitions is permitted but not required. Ordering of partitions is permitted
  but not required. A naive implementation can just wait for the entire message buffer to be marked
  ready before any transfer(s) occur." The quality-of-implementation expectation is stated as advice:
  "A high quality implementation will eventually return flag = true from MPI_PARRIVED after all of the
  corresponding MPI_PREADY calls have been made for a receive-side partition, even if other send
  partitions are not yet marked as ready."

```
  /* Sender: T threads each fill one partition of a single logical message. */
  MPI_Psend_init(buf, /*partitions=*/T, count_per_part, MPI_DOUBLE,
                 dst, tag, comm, MPI_INFO_NULL, &req);
  for (it = 0; it < niters; it++) {
      MPI_Start(&req);                       /* all T partitions now inactive */
      #pragma omp parallel for
      for (p = 0; p < T; p++) {
          fill(buf + p * count_per_part);
          MPI_Pready(p, &req);               /* earlybird: this piece can fly now */
      }
      MPI_Wait(&req, MPI_STATUS_IGNORE);     /* whole message done */
  }
  MPI_Request_free(&req);

  /* Receiver: consume partitions as they land; note R may differ from T. */
  MPI_Precv_init(rbuf, /*partitions=*/R, count_per_part, MPI_DOUBLE,
                 src, tag, comm, MPI_INFO_NULL, &rreq);
  MPI_Start(&rreq);
  for (done = 0; done < R; ) {
      for (q = 0; q < R; q++) {
          int flag; MPI_Parrived(&rreq, q, &flag);
          if (flag && !seen[q]) { consume(rbuf + q*count_per_part); seen[q]=1; done++; }
      }
  }
  MPI_Wait(&rreq, MPI_STATUS_IGNORE);
```

---

## Bibliography

```bibtex
@article{hoefler2015rma,
  author    = {Hoefler, Torsten and Dinan, James and Thakur, Rajeev and Barrett, Brian and
               Balaji, Pavan and Gropp, William and Underwood, Keith},
  title     = {Remote Memory Access Programming in {MPI-3}},
  journal   = {ACM Transactions on Parallel Computing},
  volume    = {2}, number = {2}, pages = {9:1--9:26}, year = {2015}, month = jun,
  doi       = {10.1145/2780584}
}

@inproceedings{gerstenberger2013fompi,
  author    = {Gerstenberger, Robert and Besta, Maciej and Hoefler, Torsten},
  title     = {Enabling Highly-Scalable Remote Memory Access Programming with {MPI-3} One Sided},
  booktitle = {Proceedings of the International Conference on High Performance Computing,
               Networking, Storage and Analysis (SC'13)},
  pages     = {53:1--53:12}, year = {2013}, publisher = {ACM},
  doi       = {10.1145/2503210.2503286},
  note      = {Best Paper (1/92); Best Student Paper Finalist}
}

@article{dinan2016rmaimpl,
  author    = {Dinan, James and Balaji, Pavan and Buntinas, Darius and Goodell, David and
               Gropp, William and Thakur, Rajeev},
  title     = {An Implementation and Evaluation of the {MPI 3.0} One-Sided Communication Interface},
  journal   = {Concurrency and Computation: Practice and Experience},
  volume    = {28}, number = {17}, pages = {4385--4404}, year = {2016},
  doi       = {10.1002/cpe.3758}
}

@inproceedings{belli2015notified,
  author    = {Belli, Roberto and Hoefler, Torsten},
  title     = {Notified Access: Extending Remote Memory Access Programming Models for
               Producer-Consumer Synchronization},
  booktitle = {Proceedings of the 29th IEEE International Parallel and Distributed Processing
               Symposium (IPDPS'15)},
  year      = {2015}, publisher = {IEEE}, doi = {10.1109/IPDPS.2015.30},
  note      = {Best Paper at IPDPS'15 (4/108)}
}

@inproceedings{hoefler2012sharedmem,
  author    = {Hoefler, Torsten and Dinan, James and Buntinas, Darius and Balaji, Pavan and
               Barrett, Brian and Brightwell, Ron and Gropp, William and Kale, Vivek and
               Thakur, Rajeev},
  title     = {Leveraging {MPI}'s One-Sided Communication Interface for Shared-Memory Programming},
  booktitle = {Recent Advances in the Message Passing Interface (EuroMPI 2012)},
  series    = {LNCS}, volume = {7490}, pages = {132--141}, year = {2012},
  doi       = {10.1007/978-3-642-33518-1_18}
}

@article{hoefler2013mpiplusmpi,
  author    = {Hoefler, Torsten and Dinan, James and Buntinas, Darius and Balaji, Pavan and
               Barrett, Brian and Brightwell, Ron and Gropp, William and Kale, Vivek and
               Thakur, Rajeev},
  title     = {{MPI + MPI}: A New Hybrid Approach to Parallel Programming with {MPI} Plus
               Shared Memory},
  journal   = {Computing}, volume = {95}, number = {12}, pages = {1121--1136}, year = {2013},
  doi       = {10.1007/s00607-013-0324-2}
}

@article{mellorcrummey1991algorithms,
  author    = {Mellor-Crummey, John M. and Scott, Michael L.},
  title     = {Algorithms for Scalable Synchronization on Shared-Memory Multiprocessors},
  journal   = {ACM Transactions on Computer Systems},
  volume    = {9}, number = {1}, pages = {21--65}, year = {1991},
  doi       = {10.1145/103727.103729}
}

@inproceedings{holmes2016sessions,
  author    = {Holmes, Daniel J. and Mohror, Kathryn and Grant, Ryan E. and Skjellum, Anthony and
               Schulz, Martin and Bland, Wesley and Squyres, Jeffrey M.},
  title     = {{MPI} Sessions: Leveraging Runtime Infrastructure to Increase Scalability of
               Applications at Exascale},
  booktitle = {Proceedings of the 23rd European MPI Users' Group Meeting (EuroMPI 2016)},
  pages     = {121--129}, year = {2016}, publisher = {ACM}
}

@inproceedings{hjelm2019sessions,
  author    = {Hjelm, Nathan and Pritchard, Howard and Guti\'errez, Samuel K. and
               Holmes, Daniel J. and Castain, Ralph and Skjellum, Anthony},
  title     = {{MPI} Sessions: Evaluation of an Implementation in {Open MPI}},
  booktitle = {IEEE International Conference on Cluster Computing (CLUSTER)},
  year      = {2019}, publisher = {IEEE}
}

@inproceedings{grant2019finepoints,
  author    = {Grant, Ryan E. and Dosanjh, Matthew G. F. and Levenhagen, Michael and
               Brightwell, Ron and Skjellum, Anthony},
  title     = {Finepoints: Partitioned Multithreaded {MPI} Communication},
  booktitle = {High Performance Computing (ISC High Performance 2019)},
  series    = {LNCS}, pages = {330--350}, year = {2019},
  doi       = {10.1007/978-3-030-20656-7_17}
}

@inproceedings{elis2019qmpi,
  author    = {Elis, Bengisu and Yang, Dai and Schulz, Martin},
  title     = {{QMPI}: A Next Generation {MPI} Profiling Interface for Modern {HPC} Platforms},
  booktitle = {Proceedings of the 26th European MPI Users' Group Meeting (EuroMPI 2019)},
  year      = {2019}, publisher = {ACM}, doi = {10.1145/3343211.3343215}
}

@inproceedings{knupfer2012scorep,
  author    = {Kn{\"u}pfer, Andreas and R{\"o}ssel, Christian and an Mey, Dieter and others},
  title     = {Score-P: A Joint Performance Measurement Run-Time Infrastructure for
               Periscope, Scalasca, {TAU}, and Vampir},
  booktitle = {Tools for High Performance Computing 2011},
  pages     = {79--91}, year = {2012}, publisher = {Springer}
}

@inproceedings{eschweiler2011otf2,
  author    = {Eschweiler, Dominic and Wagner, Michael and Geimer, Markus and Kn{\"u}pfer, Andreas
               and Nagel, Wolfgang E. and Wolf, Felix},
  title     = {Open Trace Format 2: The Next Generation of Scalable Trace Formats and Support
               Libraries},
  booktitle = {Applications, Tools and Techniques on the Road to Exascale Computing (ParCo 2011)},
  series    = {Advances in Parallel Computing}, volume = {22}, pages = {481--490}, year = {2012}
}

@inproceedings{thakur1999datasieving,
  author    = {Thakur, Rajeev and Gropp, William and Lusk, Ewing},
  title     = {Data Sieving and Collective {I/O} in {ROMIO}},
  booktitle = {Proceedings of the 7th Symposium on the Frontiers of Massively Parallel
               Computation (FRONTIERS'99)},
  pages     = {182--189}, year = {1999}, publisher = {IEEE},
  doi       = {10.1109/FMPC.1999.750599}
}

@article{delrosario1993twophase,
  author    = {del Rosario, Juan Miguel and Bordawekar, Rajesh and Choudhary, Alok},
  title     = {Improved Parallel {I/O} via a Two-Phase Run-Time Access Strategy},
  journal   = {ACM SIGARCH Computer Architecture News},
  volume    = {21}, number = {5}, pages = {31--38}, year = {1993},
  doi       = {10.1145/165660.165667}
}

@article{numrich1998coarray,
  author    = {Numrich, Robert W. and Reid, John},
  title     = {Co-Array {Fortran} for Parallel Programming},
  journal   = {ACM SIGPLAN Fortran Forum},
  volume    = {17}, number = {2}, pages = {1--31}, year = {1998}
}

@techreport{upc2005spec,
  author      = {{UPC Consortium}},
  title       = {{UPC} Language Specifications, v1.2},
  institution = {Lawrence Berkeley National Laboratory},
  number      = {LBNL-59208}, year = {2005}
}

@article{nieplocha1996globalarrays,
  author    = {Nieplocha, Jaroslaw and Harrison, Robert J. and Littlefield, Richard J.},
  title     = {Global Arrays: A Nonuniform Memory Access Programming Model for
               High-Performance Computers},
  journal   = {The Journal of Supercomputing},
  volume    = {10}, number = {2}, pages = {169--189}, year = {1996}
}

@inproceedings{mellorcrummey2009caf2,
  author    = {Mellor-Crummey, John and Adhianto, Laksono and Scherer III, William N. and
               Jin, Guohua},
  title     = {A New Vision for {Coarray Fortran}},
  booktitle = {Proceedings of the 3rd Conference on Partitioned Global Address Space
               Programming Models (PGAS'09)},
  year      = {2009}, publisher = {ACM}, doi = {10.1145/1809961.1809969}
}

@inproceedings{trafffive2000nec,
  author    = {Tr{\"a}ff, Jesper Larsson and Ritzdorf, Hubert and Hempel, Rolf},
  title     = {The Implementation of {MPI-2} One-Sided Communication for the {NEC SX-5}},
  booktitle = {Proceedings of the 2000 ACM/IEEE Conference on Supercomputing},
  year      = {2000}, publisher = {IEEE}
}

@inproceedings{hoefler2011libraries,
  author    = {Hoefler, Torsten and Snir, Marc},
  title     = {Writing Parallel Libraries with {MPI} --- Common Practice, Issues, and Extensions},
  booktitle = {Recent Advances in the Message Passing Interface (EuroMPI 2011)},
  pages     = {345--355}, year = {2011}
}

@inproceedings{manson2005java,
  author    = {Manson, Jeremy and Pugh, William and Adve, Sarita V.},
  title     = {The {Java} Memory Model},
  booktitle = {Proceedings of the 32nd ACM SIGPLAN-SIGACT Symposium on Principles of
               Programming Languages (POPL'05)},
  pages     = {378--391}, year = {2005}, doi = {10.1145/1040305.1040336}
}

@article{boehm2008cpp,
  author    = {Boehm, Hans-J. and Adve, Sarita V.},
  title     = {Foundations of the {C++} Concurrency Memory Model},
  journal   = {ACM SIGPLAN Notices}, volume = {43}, number = {6}, pages = {68--78}, year = {2008},
  doi       = {10.1145/1379022.1375591}
}

@article{iserte2023malleability,
  author  = {Iserte, Sergio and others},
  title   = {A Survey on Malleability Solutions for High-Performance Distributed Computing},
  journal = {Applied Sciences}, year = {2023},
  note    = {Exact venue/volume [UNVERIFIED]; consulted via Universitat Jaume I repository}
}

@inproceedings{chadha2020slurm,
  author    = {Chadha, Mohak and John, Jophin and Gerndt, Michael},
  title     = {Extending {SLURM} for Dynamic Resource-Aware Adaptive Batch Scheduling},
  booktitle = {IEEE International Conference on High Performance Computing, Data, and
               Analytics (HiPC)},
  year      = {2020},
  note      = {arXiv:2009.08289}
}

@misc{huber2024drmprinciples,
  author = {Huber, Dominik and Schreiber, Martin and Schulz, Martin and others},
  title  = {Design Principles of Dynamic Resource Management for High-Performance
            Parallel Programming Models},
  year   = {2024}, note = {arXiv:2403.17107; author list [UNVERIFIED]}
}

@manual{mpi31,
  title  = {{MPI}: A Message-Passing Interface Standard, Version 3.1},
  author = {{MPI Forum}}, year = {2015}, month = jun
}

@manual{mpi41,
  title  = {{MPI}: A Message-Passing Interface Standard, Version 4.1},
  author = {{MPI Forum}}, year = {2023}, month = nov
}

@manual{romio_guide,
  title        = {Users Guide for {ROMIO}: A High-Performance, Portable {MPI-IO}
                  Implementation},
  author       = {Thakur, Rajeev and Ross, Robert and Lusk, Ewing and Gropp, William and
                  Latham, Robert},
  organization = {Argonne National Laboratory},
  note         = {Edition/date [UNVERIFIED]}
}
```

Additional works cited in text without full entries above, all `[UNVERIFIED]` as to exact
bibliographic details: Vetter & McCracken, *mpiP* (2001); Shende & Malony, *The TAU Parallel
Performance System* (IJHPCA 2006); Adhianto et al., *HPCToolkit* (CCPE 2010); Carns et al., *Darshan*
(2011); Chan, Gropp & Lusk on *Jumpshot/SLOG2*; Nagel et al. on *VAMPIR* (1996); Pillet et al. on
*Paraver* (1995); Schulz & de Supinski on *PnMPI* (2007); Bland et al. on *ULFM*; "Implementing True
MPI Sessions and Evaluating MPI Initialization Scalability" (arXiv:2605.03983, MPICH team).

---

## Transfer to agent systems

The mapping below is the paper's contribution surface. Each row is a mechanism from above plus the
proposed AgentMPI analogue and the specific reason the analogy is load-bearing rather than decorative.

### (a) The blackboard / artifact store as an RMA window

An agent system's shared state — the scratchpad, the artifact store, the vector index, the plan
document — is exactly a *window*: a named region of memory exposed by one participant to a *bounded
group* of others. Import the two isolation properties MPI gets for free [Hoefler et al. 2015, §2]:
(i) agents outside the window's group cannot touch it, and (ii) state not attached to a window cannot
be reached remotely at all, even by group members. That gives AgentMPI a capability model that costs
nothing to enforce and lets independently authored agent libraries share a process without corrupting
each other's private state.

Carry over all four window flavors, because they correspond to real agent situations:
`agentmpi_win_create` wraps a store the application already owns (legacy integration, but requires an
O(n) address/handle table — the same scalability tax); `agentmpi_win_allocate` lets the runtime place
the store, enabling *symmetric naming* so an artifact key means the same thing on every agent without
a lookup; `win_create_dynamic` + `attach`/`detach` is the right model for an artifact store that grows
during a run (new documents, new tool outputs), and it needs the same distributed descriptor-cache
protocol with an invalidation counter that foMPI uses [Gerstenberger et al. 2013, §2.2];
`win_allocate_shared` is the co-located case (§d below).

Adopt the **unified/separate memory-model distinction** as a first-class, *queryable* property
(`AGENTMPI_WIN_MODEL`). In an agent system, "separate" is the honest model when the shared store is
behind an eventually consistent database, an object store, or a network cache: an agent's local view
(its context window, its cached retrieval results) is a *private copy* and the store is the *public
copy*, and nothing reconciles them without an explicit call. "Unified" is the honest model when store
and agent share a process or a strongly consistent KV store. The payoff is the same as MPI's: a
protocol written for the separate model runs correctly on both, so agent code becomes portable across
deployment topologies — and under unified, an agent may legitimately *poll* a location for a
collaborator's result, with an eventual-delivery guarantee but no bound on time
[Hoefler et al. 2015, §3.5], which is precisely the semantics of watching a shared document for an
edit.

Most importantly, import the **separation of `hb` from `co`** [Hoefler et al. 2015, §3.3]. Agent
frameworks today conflate "the other agent has been told" with "the artifact is durable and visible,"
and the resulting bugs (an agent reads a half-written plan; an agent is notified about a document that
has not committed) are exactly RMA data races. Define AgentMPI correctness as: *every pair of
conflicting artifact accesses must be ordered by both a process-synchronization edge and a
consistency edge.* That single sentence is a specification, and it is checkable.

Likewise import **local vs remote completion** as distinct, separately named operations
(`agentmpi_flush_local` = "my buffer/prompt is free to reuse"; `agentmpi_flush` = "the artifact is
committed and visible to others"). Conflating them is the single most common bug shape in
tool-calling agent frameworks, where a write is issued and the next agent is dispatched before the
write is durable.

### (b) Epochs and locking for concurrent artifact edits

The three synchronization modes map onto three genuinely different agent orchestration patterns, and
the point of the analogy is that a framework should offer *all three* rather than hard-coding one.

- **Fence ⇒ the round-based / bulk-synchronous orchestrator.** All agents in a group write their
  contributions, then everyone barriers, then everyone reads. `agentmpi_win_fence` closes an epoch
  with the guarantee that every write is visible everywhere. Import the **assertion flags**, because
  they are cheap declarations that unlock large optimizations and cost nothing when ignored:
  `NOPRECEDE` ("this round begins nothing pending" — lets the runtime skip the barrier entirely, a
  real win when a round has no writers), `NOSUCCEED` ("nothing follows"), `NOSTORE` ("I made no local
  edits since last sync" — lets the runtime skip re-uploading my private view), `NOPUT` ("nobody will
  write my region this round" — lets the runtime skip re-fetching it). Preserve the standard's
  discipline that flags are *assertions*, that giving false ones is erroneous, that
  `NOPRECEDE`/`NOSUCCEED` are all-or-nothing across the group, and that an implementation may ignore
  them all [MPI-3.1 §11.5.4]. And preserve the mnemonic: two flags describe the past, two the future.
- **PSCW ⇒ declared-neighborhood orchestration.** The scalability argument transfers verbatim: a
  global barrier over 10,000 agents to let 3 of them exchange a document is absurd. An agent posts an
  exposure epoch naming *who may edit its artifacts this round*, and starts an access epoch naming
  *whose artifacts it intends to edit*. Cost becomes O(|neighborhood|) rather than O(|agents|), and
  the runtime gets an exact dependency graph per round, which is schedulable and cacheable. The MPI
  Forum's own rationale is the honest statement of the trade-off and should be quoted in the paper:
  the design "requires more information than RMA needs… Users that want more 'anonymous' communication
  will be required to use the fence or lock mechanisms" [MPI-3.1 §11.5.2]. So: PSCW buys race
  protection and scalability at the price of declaring intent up front; that is a design dial AgentMPI
  should expose rather than choose. Note also the asymmetry worth stealing: `post` never blocks
  (cheap to offer yourself up), `start` may block (waiting to be granted access), `complete` gives
  only origin completion, `wait` gives target completion — and `test` gives the nonblocking poll for
  an orchestrator that must stay responsive.
- **Passive target ⇒ the always-open shared store.** `lock_all` once at agent startup, then all
  artifact access via atomics and flushes, with the exposure-epoch concept abandoned. This is what a
  real blackboard looks like, and it is where the *actual* locking discipline lives:
  `AGENTMPI_LOCK_SHARED` for readers, `AGENTMPI_LOCK_EXCLUSIVE` for an agent rewriting a section.
  Import the reader-writer guarantee at the *artifact site* (exclusive excludes everything;
  shared excludes only exclusive), the rule that concurrent per-target lock epochs must target
  distinct artifacts, and the standard's blunt advice that mixing active and passive mode on one
  window is erroneous and that "a set of windows is used with only one synchronization mechanism at a
  time" [MPI-3.1 §11.5.3]. Also import `MPI_MODE_NOCHECK`: when the orchestrator already guarantees
  mutual exclusion by construction, the agent should be able to say so and skip the lock traffic
  while keeping the coherence effects.
- **`agentmpi_win_sync` ⇒ the explicit "reconcile my view with the store" call.** Note carefully what
  MPI's version does and does not do: it synchronizes private and public copies and has the *effect*
  of ending and reopening an epoch, but it "does not actually end an epoch or complete any pending
  operations" [MPI-3.1 §11.5.4]. The agent analogue — "invalidate my cached view of the artifact
  store and republish my local edits, without committing anything in flight" — is a distinct primitive
  from both flush and unlock, and agent frameworks currently lack it entirely.

### (c) Atomic compare-and-swap for claiming work items

This is the cleanest transfer in the document. Give AgentMPI exactly MPI's three atomics over the
shared window, and specify their limits as sharply as MPI does:

- `agentmpi_fetch_and_op(&in, &out, op, target, disp, win)` with `op ∈ {SUM, REPLACE, NO_OP, MAX,
  MIN, ...}` restricted to a **single element of a predefined type** — because that restriction is
  what lets an implementation map it onto a database `UPDATE ... RETURNING`, a Redis `INCR`, or a
  hardware atomic instead of a generic transaction.
- `agentmpi_compare_and_swap(&new, &expected, &old, target, disp, win)` restricted to integer-like
  types.
- `agentmpi_get_accumulate` as the general derived-type form.

Then the idioms follow mechanically. **Work claiming**: the task queue is an array of state words;
an agent claims item *i* with a CAS from `FREE` to `CLAIMED_BY(me)` and proceeds iff `old == FREE`.
This is idempotent under retries, safe under arbitrary agent concurrency, needs no coordinator, and —
the point — solves duplicate-work-under-concurrency, which every multi-agent framework rediscovers.
**Self-scheduling**: one `fetch_and_op(SUM, +1)` per item yields a monotone index; one round trip per
task claimed, no polling loop, no leader. **Lease renewal**: `fetch_and_op(MAX, deadline)` for
liveness. **Atomic publish**: accumulate-with-REPLACE as an atomic artifact write; get-accumulate with
NO_OP as an atomic artifact read [Hoefler et al. 2015, §2.3].

Import the caveats too, because they are the correctness cliff. (i) Atomicity holds only at the
granularity of the primitive element — an "atomic" multi-field artifact update may tear, exactly as
MPI's two-integer REPLACE example tears [Hoefler et al. 2015, §2.3]. Agent frameworks that promise
atomic JSON-document updates need to say what the atomic unit is. (ii) Atomics are **not** atomic
against plain puts/gets; mixing them on one location is undefined. (iii) Every atomic is nonblocking:
its returned value is not valid until a flush that gives remote completion. (iv) Ordering between
accumulates to the same location is guaranteed by default and *relaxable* per window via a hint
(`raw`/`war`/`waw`/`rar`); expose that dial, since most agent workloads need far less than total order
and "the fastest mode is to require no ordering."

For higher-level structures, port the **MCS lock over RMA** (§3.1) as the reference construction of a
fair, scalable, queue-based artifact mutex from nothing but CAS and fetch-and-op: no polling storm, at
most one waiter spinning per lock, FIFO fairness, and O(1) network operations per acquisition when
uncontended. Its most instructive feature for us is the handoff: because RMA has no notification, the
lock is passed with a two-sided message. **AgentMPI should not repeat that mistake.** Design in
**notified access** from the start [Belli & Hoefler 2015]: every write to the shared store may carry
an integer (or string) **tag**, and the target may `notify_init(source, tag, count)` and then match
incoming notifications from a queue by source and tag. This is the producer-consumer primitive that
agent systems need constantly — "agent B, the document you are waiting on has been updated" — and
building it in avoids the extra round trip that RMA systems pay, in exchange for the same
matching-queue machinery MPI already understands. Belli & Hoefler's conclusion generalizes: notified
access "is a valuable primitive for any RMA system," and it is a fortiori valuable for a system whose
participants are expensive and latency-dominated.

### (d) Sessions as the way independently authored agent libraries join a running agent job

This is the deepest analogy in the paper and deserves the most careful treatment, because the problem
MPI Sessions solves is *precisely* the problem multi-agent frameworks have today.

The World Model's three named failures translate one-to-one [MPI-4.1 §12.3]:

| MPI World Model failure | Agent framework equivalent |
|---|---|
| "MPI cannot be initialized from different application components without a priori knowledge or coordination" | Two agent libraries in one process each try to own the orchestrator/registry/event bus; whoever imports first wins |
| "MPI cannot be initialized more than once" | The framework is a process-global singleton; you cannot run two agent subsystems with different configurations |
| "MPI cannot be reinitialized after MPI_FINALIZE" | Shutting down one agent subsystem tears down the runtime for every other |
| `MPI_COMM_WORLD` is mandatory and global | A global agent registry / "all agents" broadcast channel that must exist and be fully connected at startup, whether or not anyone needs it |

The Sessions design gives AgentMPI the fix, and the pipeline should be copied structurally:
`Session → process set → Group → communication context`.

- **`agentmpi_session_init(info, errhandler, &session)`** — *local*, lightweight, thread-safe,
  callable many times per process, with a **per-session** configuration (thread/concurrency level,
  error handler, and by extension: model endpoints, token budget, retry policy, tracing sink). MPI's
  insistence that "Session instantiation is intended to be a lightweight operation" and "is always
  thread safe" [MPI-4.1 §12.3.1] is exactly what makes it usable by a library that does not know what
  else is in the process.
- **Agent sets as *runtime-defined names*, not a fixed roster.** This is the key move. MPI requires
  only `mpi://WORLD` and `mpi://SELF` and lets the runtime publish arbitrarily many more, discovered
  at runtime by `get_num_psets`/`get_nth_pset`. AgentMPI should require `agent://ALL` and
  `agent://SELF` and let the orchestrator publish sets like `agent://ROLE/reviewer`,
  `agent://TENANT/acme`, `agent://CAPABILITY/code-exec`, `agent://COLOCATED` (the analogue of Open
  MPI's `mpi://shared`), or `agent://BUDGET/tier2`. In MPI these come from PMIx
  [Hjelm et al. 2019]; in AgentMPI they come from the orchestrator or a service registry. Note the
  standard's careful distinction: **a process set is merely a *name for a list*; a group is a
  materialized, ordered, first-class object**. Names are cheap and may be published, revoked, or
  redefined by the runtime; groups are snapshots. That two-level structure is what makes malleability
  expressible.
- **`agentmpi_group_from_session_pset(session, "agent://ROLE/reviewer", &group)`** — *local*. No
  communication, no coordination. A library can discover and materialize its peer set without any
  global rendezvous.
- **`agentmpi_comm_create_from_group(group, stringtag, info, errhandler, &comm)`** — collective over
  the group *only*, never over a global world. Copy the **`stringtag`** mechanism verbatim, including
  MPI's advice to use **reverse domain name notation** (`org.example.mylib.v2`) so that independently
  authored components' concurrent context creations never collide, and including the rule that
  concurrent creations within one process must use distinct tags [MPI-4.1 §7.4.2]. This is the piece
  agent frameworks are missing: a standard way for two libraries that have never heard of each other
  to form a private communication context over an overlapping agent set without a registry lock.
- **Isolation as a spec rule, not a convention.** "MPI objects derived from different MPI Session
  handles shall not be intermixed with each other in a single MPI procedure call" [MPI-4.1 §12.3].
  The AgentMPI equivalent — artifacts, channels, and windows derived from different sessions may not
  be mixed in one call — is what makes multi-tenant and multi-library agent processes safe by
  construction rather than by discipline.
- **Per-session concurrency and error policy.** MPI lets each session request its own thread-support
  level so "different components of an application can use different thread support levels"
  [MPI-4.1 §12.3]. For agents this generalizes to per-session concurrency limits, timeouts, retry
  semantics, and — the obvious extension — per-session *token budgets* and *tool permissions*. A
  session is the natural unit of resource accounting.

Contrast with what AgentMPI should *not* copy: **MPI-2 dynamic process management**. Spawn is the
obvious analogue of "orchestrator spawns a sub-agent," and it is a cautionary tale. It failed for five
reasons that all have agent equivalents: static allocation upstream (a fixed model-capacity or
token-budget reservation that cannot grow mid-run); no resource-manager integration (the agent runtime
cannot ask the scheduler for more capacity, so it oversubscribes what it already holds); only the
application may initiate change (an agent, unlike a global scheduler, has no view of other jobs'
pending demand — so optimal dynamic resourcing requires a *cooperative* protocol, per
[Huber et al. 2024]); blocking, collective, synchronous startup ("MPI_COMM_SPAWN in the parents and
MPI_INIT in the children form a collective operation over the union of parent and child processes"
[MPI-4.1 §12.7.4] — the analogue is an orchestrator that blocks until every sub-agent has booted); and
no fault tolerance. That said, three pieces of the dynamic-process chapter *are* worth taking:
(1) **the child gets its own world** — a spawned sub-agent should not be injected into the parent's
peer group but should receive an *inter*-context connecting the two groups, with `get_parent` as the
child's handle on its creator and an explicit `merge` if flattening is genuinely wanted; (2) **the
`info` hint mechanism** as a uniform, string-keyed, implementation-extensible way to pass
non-semantic guidance (placement, working directory, model choice, acceptable-count ranges via a
`soft` key) across an API boundary — AgentMPI should have exactly one such mechanism, used identically
on sessions, windows, channels, and spawns; and (3) **`UNIVERSE_SIZE`** as the portable "how much
capacity does this job actually have?" attribute, together with the standard's honesty about it: set
by the launcher in an unspecified way, never updated, possibly absent, and "a recommendation, not
necessarily a hard limit" [MPI-4.1 §12.7.9]. Also worth porting: the **port/publish/lookup/join**
rendezvous ladder for agents *not* related by spawn, and specifically the standard's three-tier
portability framing — hand the address over by hand (most portable), use a name service with a
mandatory fallback, or bring your own — plus `agentmpi_comm_join(fd)` as the explicit admission that
sometimes the rendezvous happens entirely outside the framework.

### (e) MPI_T and PMPI as the hook for token accounting and observability

Agent systems have exactly MPI's two observability needs, and MPI's answer is to keep them in two
separate interfaces. Copy that separation.

**PMPI ⇒ per-call interposition.** Every AgentMPI entry point should have a name-shifted twin
(`PAGENTMPI_Xxx`) so a tool can wrap the public symbol, record, and delegate. The mechanics transfer
even in a dynamic language: weak symbols become decorator registration or import hooks; the
"link order" `-lmytool -lpagentmpi -lagentmpi` becomes a middleware stack. Crucially, copy the
*implementation obligation* that makes it usable: the runtime must be structured so a tool can
intercept **one** entry point and have everything else fall through automatically
[MPI-3.1 §14.2.7]. This is the right shape for a token-accounting middleware: intercept only
`agentmpi_invoke_model` and `agentmpi_tool_call`, and leave the other 300 entry points alone. Copy
also the *limitation*, as a design warning: PMPI's single-owner symbol space means only one tool at a
time, which is why the community needed PnMPI and then QMPI. So build the **QMPI chaining model in
from day one** [Elis et al. 2019]: an ordered vector of tools, each interception resolving its
successor through a lookup (`QMPI_Table_query`-style) rather than calling the base implementation
directly, and mandatory **context separation** so the same tool may appear twice in a chain with
independent state. In an agent system the concurrent tools are obvious and simultaneous — token
accounting, cost attribution, PII redaction, prompt-injection detection, tracing, replay capture,
policy enforcement — and they will *all* want to wrap `invoke_model`. A single-tool interface is dead
on arrival.

**MPI_T ⇒ runtime introspection, split into knobs and meters.** Copy the whole shape, including the
things that look fussy but are not:

- **cvars = knobs.** Discoverable (`cvar_get_num`, `cvar_get_info` returning name, verbosity,
  datatype, description, **bind**, and **scope**), read/write through allocated handles. The agent
  analogues are the eager/rendezvous thresholds of agent systems: model choice, temperature, max
  retries, context-window trim policy, parallel-tool-call limits, cache TTL. The `bind` field —
  which object the variable attaches to (none, a session, a window, a channel) — is what makes
  per-agent and per-session tuning expressible rather than global. The `scope` field — when a write is
  legal — is what prevents a tool from changing a setting mid-flight. And the *self-describing*
  property matters more for agents than for MPI: a control plane (or an agent supervising other
  agents) can enumerate every tunable with its description and legal scope, which is the foundation
  for autotuning.
- **pvars = meters, read through explicit sessions.** `pvar_session_create`, `handle_alloc`,
  `start`/`stop`, `read`/`readreset`/`reset`. Sessions exist "so that accesses to pvars in different
  sessions won't conflict" [MPICH design doc] — which is exactly what a token-accounting middleware
  and a billing exporter and a latency dashboard need when all three want the same counter over
  different intervals. **This is the natural home for token accounting**: prompt tokens, completion
  tokens, cached tokens, cost, tool invocations, retries, context-window occupancy, queue depth,
  time-to-first-token. Import the **pvar classes** as the typing discipline that makes aggregation
  correct: `COUNTER` (tokens consumed — sums), `HIGHWATERMARK` (peak context occupancy — maxes),
  `LEVEL` (current queue depth — samples, never sums), `TIMER` (time in model calls),
  `PERCENTAGE`, `STATE`, `AGGREGATE`. Agent frameworks today emit untyped numbers and then aggregate
  them wrongly; a declared class per variable fixes that at the interface.
- **Categories** for hierarchical organization, with a `category_changed` query so a tool notices new
  variables appearing (e.g. when a new agent library joins a running job via its own session — note
  how this composes with (d)).
- **A separate handle space, usable before init and after finalize** [MPI-3.1 §14.3.3]. For agents:
  accounting must survive session teardown, because the bill is due after the work is done.
- **MPI-4's callback-driven event interface** as the distinct third thing: pvars are polled state,
  events are pushed state transitions, and the standard is explicit that the two sets need not
  coincide [MPI-4.1 §15.3.7]. Agent systems need both — a token counter you sample, and a
  budget-exceeded event you are called back on.

**Tracing.** Define an AgentMPI trace format on OTF2's structure, because OTF2 already solved the
problems we will hit. Per-*location* (per-agent, and nested per-tool-call or per-thread) temporally
ordered event streams; interned definition records referenced by id, so region/agent/prompt-template
names are stored once; and a per-record header of `(location, timestamp)` with the timestamp elided
for events sharing one [Eschweiler et al. 2011]. Then the record set:
`Enter`/`Leave (+ region)` for agent-step and tool-call nesting — these two alone reconstruct the full
agent call stack over time; `Send`/`Recv (+ peer, channel, tag, length)` for inter-agent messages;
`CollectiveBegin`/`CollectiveEnd (+ op, channel, root, sizeSent, sizeReceived)` for
broadcast/gather/consensus rounds; and — importantly — RMA-shaped records for artifact-store access:
`Put`/`Get (+ win, remote, bytes, matchingId)` paired with **separate**
`OpCompleteBlocking` / `OpCompleteNonBlocking` / **`OpCompleteRemote`** records matched by
`matchingId`, plus `AcquireLock`/`TryLock`/`ReleaseLock (+ win, remote, lockId, lockType)`. That
`OpCompleteRemote` record is the observability payoff of §(a): the trace *natively distinguishes*
"the agent issued the write" from "the write became visible," and the gap between them is directly
measurable. `Metric` records carry sampled counters (token rate, cost rate) on the shared time axis.

The visualization transfers directly, and is worth specifying because it is what reviewers will
picture. One horizontal track per agent, time on x; nested colored bars from `Enter`/`Leave` showing
the agent's stack, colored by category (reasoning / tool call / model wait / artifact I/O / idle);
lines between tracks for messages, whose slope is latency and whose near-verticality means tight
coupling; brackets spanning tracks for collective/consensus rounds, whose ragged left edge is
imbalance — some agents arriving late while others burn wall-clock waiting; fans of lines from an
artifact `Put` to its `OpCompleteRemote`; and counter plots (cumulative tokens, cost, queue depth)
below on the shared axis. Companion views: a call-tree profile with inclusive/exclusive time *and
tokens*, a communication matrix (messages or tokens, source agent × destination agent, as a heat
map), and a message/prompt-size histogram. The two pathologies to detect automatically are the direct
analogues of Scalasca's wait-state patterns: **late sender / late receiver** (an agent blocked waiting
on a peer that had not started producing) and **imbalance at a synchronization point** (nine agents
idle while the tenth finishes). Both are computable by replaying the trace, and both are the dominant
inefficiency in real multi-agent runs.

### (f) Two remaining transfers

**MPI-3 shared memory ⇒ the co-located fast path.** When agents share a process or a machine, the
right protocol is not to serialize artifacts through the store. Copy the three-step idiom:
`comm_split_type(AGENTMPI_COMM_TYPE_SHARED)` to discover the co-location island (with an `info`
argument for finer granularity — the analogue of MPI's NUMA/cache hints), `win_allocate_shared` to
obtain a directly accessible region, `win_shared_query(rank)` to obtain a peer's mapping. Then access
by direct reference, not by RPC — the agent analogue of "what the compiler produces is much faster
than calling MPI_Put." Two caveats transfer with it. First, **addresses are not portable between
peers**: each participant may map the region at its own address and choose its own size, so agents
must exchange *offsets/keys*, never pointers [Hoefler et al. 2015, Fig. 3]. Second, and more
important, **the shared fast path still has a memory model**: MPI declines to define shared-window
semantics under the separate model at all [MPI-4.1 §13.2.3], and even under unified there are "no
guarantees about the consistency order of [local loads and stores]… as this is a function of the
architecture's memory model" [Hoefler et al. 2015, §3.6]. The portable idiom is
`win_sync` + barrier + `win_sync` — publish my writes, synchronize, then read yours — i.e. an explicit
release/acquire pair. AgentMPI must specify the same, or co-located agents will silently read torn
artifacts. This is also the model for "MPI+MPI"'s real lesson: one programming model at two scales
beats two models stapled together, which argues that AgentMPI should express intra-process agent
collaboration with the *same* window/epoch vocabulary as cross-machine collaboration, not with a
separate in-memory API.

**MPI-IO ⇒ durable shared state gets its own consistency model, and collective declaration buys
optimization.** Three transfers. (i) **Views**: `set_view(disp, etype, filetype)` lets each
participant address a durable object in *its own logical units* through a strided pattern, never
computing raw offsets; the agent analogue is a per-agent typed projection over a shared corpus,
document, or log — agent *k* addresses "my slice of the transcript" and the runtime handles the
mapping. (ii) **Two-phase I/O**: because a collective operation declares that everyone is
participating, the runtime may globally reorganize the work — designate *aggregators*, have them
perform large contiguous accesses to the backing store, and redistribute in a separate communication
phase [del Rosario et al. 1993; ROMIO guide]. The agent analogue is exact and valuable: when N agents
each want to write a small artifact or embed a small chunk, a collective `write_all` lets the runtime
batch them into a few large store operations and a redistribution — which is precisely the batching
that agent frameworks currently do ad hoc, if at all. The companion trick, **data sieving** — one
oversized read spanning the wanted regions plus the holes between them, filtered locally — is the
right model for over-fetching a document range or an embedding block rather than issuing many small
retrievals [Thakur et al. 1999]. (iii) **Three consistency levels**, and the fact that the *weak*
default is what makes the fast path legal: MPI-IO offers sequential consistency per handle, sequential
consistency across handles from one collective open with atomic mode on, and otherwise user-imposed
consistency via program order plus explicit `sync` [MPI-5.0, File Consistency]. ROMIO's collective
write can skip byte-range locking during read-modify-write *precisely because* the weak default
forbids concurrent outside writers [Thakur et al. 1999, §4.2.2]. AgentMPI should expose the same
ladder over its durable artifact store — per-handle, atomic-mode, and user-imposed — with an explicit
`sync` and an explicit atomicity switch, rather than promising strong consistency it cannot afford.

**Partitioned communication ⇒ streaming and partial delivery.** MPI-4's motivation is a startlingly
good fit: many producers each filling part of one logical message, where serializing them behind a
single send makes everyone wait for the slowest, and splitting them into many messages pays the
per-message overhead N times [Grant et al. 2019]. In agent systems the producers are token streams,
parallel tool calls, or sharded sub-agents contributing to one artifact. Copy the semantics
wholesale: `psend_init(buf, partitions, ..., dest, tag, comm, info, &req)` and
`precv_init(..., partitions may differ, ...)` as *local* setup; `start` to arm; `pready(p)` /
`pready_range(lo,hi)` / `pready_list(...)` to release a piece as soon as it is ready — Sandia's
**earlybird** behavior, where data leaves without waiting for a lagging producer; `parrived(req, p,
&flag)` so the consumer may begin work on partition *p* alone, without marking the operation
complete; and `wait`/`test` for whole-message completion. Copy the send/receive **partition-count
asymmetry** (32 producers, 4 consumer chunks) and, above all, copy the **implementation freedom**:
"MPI is free to choose how many transfers to do… independent of how many partitions are reported as
ready. Aggregation of partitions is permitted but not required. Ordering of partitions is permitted
but not required. A naive implementation can just wait for the entire message buffer to be marked
ready" [MPI-4.1 §4.2]. That is how a specification stays implementable across a range of backends
while allowing a good one to shine — with the quality-of-implementation expectation stated as advice
rather than requirement ("a high quality implementation will eventually return flag = true… even if
other send partitions are not yet marked as ready"). This is the exact template for AgentMPI's
streaming semantics: a strong interface, a weak minimum guarantee, and an explicit statement of what
a good implementation does.
