# MPI Algorithms: Collectives, Point-to-Point Protocols, Progress Engines, and Cost Models

**Research memo for "AgentMPI: A Message Passing Interface for Multi-Agent Harness Development"**
Scope: the algorithmic substrate of MPI at SC/PPoPP/ICS depth. Every algorithm is given (a) a step description, (b) a cost formula in the α–β (Hockney) or LogGP model, (c) the regime in which it wins. Notation is uniform: `p` = number of processes, `n` = message size in bytes (for allgather/allreduce/alltoall, `n` is the *total* data gathered/sent per process unless stated), `lg` = log₂, `α` = per-message startup latency, `β` = per-byte transfer time, `γ` = per-byte local reduction-operator cost. Claims not directly verified against a primary source are marked `[UNVERIFIED]`.

---

## 1. Cost models

### 1.1 Hockney α–β model

The workhorse. Time to send `n` bytes between any two nodes is

```
T(n) = α + nβ
```

`α` is the startup cost, independent of size; `β` is the reciprocal of bandwidth [Hockney94]. Thakur, Rabenseifner and Gropp adopt exactly this and add `γ` as the per-byte cost of applying a reduction operator locally [ThakurRabenseifnerGropp05]. The stated assumptions are load-bearing and each one is a place where a transplant to another domain can fail:

1. Cost is independent of *how many* pairs are communicating concurrently (no contention).
2. Cost is independent of *distance* between nodes (flat network).
3. Links are bidirectional: a message costs the same in one direction as simultaneously in both.
4. The NIC is **single-ported**: at most one send and one receive in flight at a time.

Assumption 4 is the single most important one for algorithm design: nearly every logarithmic collective algorithm exists to work around it.

Because assumption 3 is optimistic on real fabrics, Rabenseifner refines the model with a second pair of parameters: `α + nβ` for *bidirectional* (pairwise-exchange) communication and `α_uni + nβ_uni` for *unidirectional* communication, with ratios `f_α = α_uni/α` and `f_β = β_uni/β` normally in `[0.5, 1.0]` — 0.5 for a simplex network, 1.0 for full duplex [ThakurRabenseifnerGropp05, Rabenseifner04]. This is why pairwise-exchange algorithms sometimes beat asymptotically-identical tree algorithms: they get the better constants.

A useful derived quantity: the **α/β crossover length** `n* = α/β`, the message size at which latency and bandwidth contribute equally. On a mid-2000s cluster this is `O(10³–10⁵)` bytes; it is the reason essentially every collective in MPICH and Open MPI has a "short message" and a "long message" algorithm.

**What Hockney captures:** the latency/bandwidth tradeoff, hence regime-based algorithm selection.
**What it misses:** contention and congestion (explicitly noted as unmodelable in this framework [Pjesivac07]), injection-rate limits, CPU occupancy, protocol switches (eager→rendezvous), topology, and any notion of overlap between computation and communication.

### 1.2 LogP (Culler et al.)

LogP abstracts a machine by four parameters [Culler93]:

- `L` — upper bound on **latency** of a small message from source to target.
- `o` — **overhead**: time the processor is *occupied* transmitting or receiving a message, during which it can do nothing else. Frequently split into `o_s` and `o_r`.
- `g` — **gap**: minimum interval between consecutive message transmissions (or receptions) at a processor. `1/g` is the per-processor bandwidth for small messages.
- `P` — number of processors.

Time to send one small message: `L + 2o`. The network has finite capacity: at most `⌊L/g⌋` messages may be in transit to or from any processor; exceeding it stalls the sender [Culler93]. The model is asynchronous and does *not* guarantee message ordering.

The critical conceptual advance over Hockney is `o`: it separates *network* time from *CPU occupancy*, which makes overlap analyzable. The critical limitation is that all messages are assumed small and of fixed size.

### 1.3 LogGP (Alexandrov et al.)

Adds `G`, the **gap per byte** for long messages: the time per byte for bulk transfer, so `1/G` is the long-message per-processor bandwidth [Alexandrov97, Hoefler10loggpTheory]. Time to send `n` bytes:

```
T(n) = L + 2o + (n − 1)G
```

[Pjesivac07]. LogP is recovered by setting `G = 0`. Roughly, `α ≈ L + 2o` and `β ≈ G`, but the models are not interchangeable: LogGP's `o` and `g` predict pipelining and injection limits that Hockney cannot express.

### 1.4 LogGPS and LogGOPS

- **LogGPS** [Ino01] adds `S`: the **message-size threshold above which sends become synchronizing**. This is a direct model of the rendezvous protocol — above `S`, the sender cannot proceed until the receiver is ready, so the send inherits a network round trip and becomes coupled to receiver scheduling. LogGPS is the first model in the family in which the *eager limit* is a first-class parameter.
- **LogGOPS** [Hoefler10loggopsim] adds `O`: a **per-byte overhead at the host CPU**. Some fabrics are genuinely zero-copy (RDMA with pre-registered memory) and have `O ≈ 0`; others pay per byte on the host (TCP packet processing, InfiniBand memory registration). LogGOPSim simulates LogGOPS with full MPI matching semantics and detailed collectives, and is the standard tool for large-scale collective simulation.
- **PLogP** (parameterized LogP) [Kielmann00] makes `o_s(m)`, `o_r(m)`, `g(m)` *functions* of message size, with `T = L + g(m)`, and requires `g(m) ≥ o_s(m)`, `g(m) ≥ o_r(m)`. It degenerates to LogGP when `g(m)` is linear [Pjesivac07].

Pješivac-Grbović et al. compared Hockney, LogP/LogGP and PLogP decision functions against exhaustive measurement across barrier, broadcast, reduce and alltoall, and found the *choice of model changes the selected algorithm and segment size* — e.g. under Hockney, binomial-tree broadcast is optimal for small messages at all communicator sizes, but the LogP/LogGP decision function selects different segment sizes for intermediate messages [Pjesivac07]. This is a strong empirical caution: cost models are not neutral instrumentation, they are part of the algorithm.

---

## 2. Broadcast

Lower bounds under the single-ported bidirectional model: latency `⌈lg p⌉ α` (each step at most doubles the set of informed nodes), bandwidth `nβ` (the root must send `n` bytes) [Chan07].

| Algorithm | Step description | Cost |
|---|---|---|
| Linear / flat tree | root sends `n` to each of `p−1` peers in turn | `(p−1)(α + nβ)`; segmented: `n_s(p−1)(α + m_sβ)` [Pjesivac07] |
| Binomial tree | root sends to `root + p/2`; both recurse in their halves | `⌈lg p⌉(α + nβ)` [ThakurRabenseifnerGropp05] |
| MST bcast | recursive halving of the rank interval | `⌈lg p⌉(α + nβ)` [Chan07] |
| Pipeline / chain | `n` split into `n_s` segments of `m_s`; each rank forwards to `rank+1` | `(p + n_s − 2)(α + m_sβ)` [Pjesivac07] |
| Pipelined binary tree | as above over a binary tree | `(⌈lg(p+1)⌉ + n_s − 2)(2α + m_sβ)` [Pjesivac07] |
| Split-binary tree | left half of message down left subtree, right half down right, then pairwise exchange | `(⌈lg(p+1)⌉ + ⌈n_s/2⌉ − 2)(2α + m_sβ) + α(n/2) + (n/2)β(n/2)` [Pjesivac07] |
| Scatter + allgather (van de Geijn) | binomial scatter of `n/p` slices, then allgather | ring allgather: `(lg p + p − 1)α + 2·(p−1)/p·nβ`; recursive-doubling allgather: `2 lg p·α + 2·(p−1)/p·nβ` [ThakurRabenseifnerGropp05, Barnett94] |
| Two-tree (23-bcast) | two in-order binary trees, interior nodes of one = leaves of the other, 2-colored edge schedule | `βn + 2α lg p + √(8αβn lg p)` [Sanders09] |
| ESBT (hypercube, `p = 2^d`) | `lg p` edge-disjoint spanning binomial trees, cyclic block distribution | `βn + α lg p + √(4αβn lg p)` [JohnssonHo89] |

**Why long-message broadcast is done as scatter + allgather.** The binomial tree's bandwidth term is `n lg p·β`: every byte crosses the network `lg p` times, because interior nodes forward the *whole* message. Scatter+allgather makes each byte cross a small constant number of times: the scatter moves `(p−1)/p·n` bytes off the root, the allgather moves `(p−1)/p·n` bytes into each node, total `2(p−1)/p·nβ ≈ 2nβ`. This is within a factor of 2 of the `nβ` lower bound. Comparing the two, for long messages (latency negligible) van de Geijn wins whenever `lg p > 2`, i.e. `p > 4`, and the maximum achievable speedup is `(lg p)/2` — so the benefit grows with scale [ThakurRabenseifnerGropp05].

**Pipelining and the optimal segment count.** Minimizing `(p + n_s − 2)(α + (n/n_s)β)` over `n_s` gives `n_s* = √(nβ(p−2)/α)` and `m_s* = √(nα/(β(p−2)))`, i.e. `T* ≈ nβ + (p−2)α + 2√(nβ(p−2)α)`. This `√(αβn·depth)` term is the universal signature of a pipelined tree; the two-tree formula `βn + 2α lg p + √(8αβn lg p)` is the same structure with depth `lg p` instead of `p−2` [Sanders09]. Two-tree is therefore asymptotically bandwidth-optimal *and* logarithmic-latency — its startup term is twice ESBT's, but unlike ESBT it works for arbitrary `p` and supports non-commutative reduction and scan.

**Selection in practice.** MPICH: binomial tree for `n < 12 KB` or `p < 8`; van de Geijn otherwise [ThakurRabenseifnerGropp05]. Open MPI's `tuned` component keeps nine broadcast algorithms (`basic_linear`, `chain`, `pipeline`, `split_binary_tree`, `binary_tree`, `binomial`, `knomial`, `scatter_allgather`, `scatter_allgather_ring`) and dispatches through a hand-tuned nested `if` tree on `(communicator_size, total_dsize)` — e.g. for `p < 4` it walks through algorithms 3, 5, 3, 7, 1, 5, 2, 1, 6, 5 as size crosses 32, 256, 512, 1024, 32768, 131072, 262144, 524288, 1048576 bytes [OpenMPItunedSrc]. The documentation is candid that these thresholds are "baked in… derived by measuring performance on existing clusters" and degrade when hardware differs [OpenMPItunedDoc].

---

## 3. Reduce and Allreduce

Lower bounds [Chan07]: latency `⌈lg p⌉α`; computation `(p−1)/p·nγ` (total work `(p−1)nγ` perfectly divided — note this is *less than* `nγ` and independent of `p` [Rabenseifner04]); bandwidth `nβ` for reduce-to-one and `2(p−1)/p·nβ` for allreduce.

### 3.1 Reduce

| Algorithm | Cost | Regime |
|---|---|---|
| Linear to root | `(p−1)(α + nβ + nγ)` `[UNVERIFIED]` as an exact form; the root serializes `p−1` receives | tiny `p` |
| Binomial tree | `⌈lg p⌉(α + nβ + nγ)` [ThakurRabenseifnerGropp05] | short messages (`≤ 2 KB` in MPICH), user-defined ops, non-power-of-two |
| Rabenseifner: reduce-scatter (recursive halving) + binomial gather | `2 lg p·α + 2·(p−1)/p·nβ + (p−1)/p·nγ` [ThakurRabenseifnerGropp05, Rabenseifner04] | long messages, predefined ops |
| Halving & doubling, `p` not a power of 2 | `≈ (2 + 2⌊lg p⌋)α + 3nβ + (3/2)nγ` [ThakurRabenseifnerGropp05] | — |
| Ring (pairwise reduce-scatter + direct send to root) | `(p−1)(α + α_uni) + n(β + β_uni) + nγ − (1/p)(n(β+β_uni) + nγ)` [ThakurRabenseifnerGropp05] | non-power-of-two, small/medium `p`, large vectors |
| Two-tree reduction | same as two-tree broadcast with direction reversed: `βn + 2α lg p + √(8αβn lg p)`; `2βm` is a lower bound for non-commutative ops with an interior root [Sanders09] | large vectors, non-commutative ops |

Rabenseifner's insight is precisely dual to van de Geijn's: replace the `n lg p·β` term of the binomial tree with `2nβ` by doing a reduce-scatter first and then gathering the scattered results [ThakurRabenseifnerGropp05]. MPICH restricts it to **predefined operations only**, because with user-defined ops the user may pass derived datatypes and "breaking up derived datatypes to do the reduce-scatter is tricky" [ThakurRabenseifnerGropp05]. This is a load-bearing engineering constraint, not a theoretical one.

### 3.2 Allreduce

| Algorithm | Cost | Regime |
|---|---|---|
| Reduce + broadcast | sum of the two | legacy MPICH |
| Recursive doubling (with local reduce each step) | `lg p·α + n lg p·β + n lg p·γ` [ThakurRabenseifnerGropp05] | short messages; user-defined ops at any size |
| Rabenseifner: reduce-scatter (recursive halving) + allgather (recursive doubling) | `2 lg p·α + 2·(p−1)/p·nβ + (p−1)/p·nγ` [ThakurRabenseifnerGropp05] | long messages, predefined ops, `p = 2^k` |
| Vector-halving/distance-doubling, `p` a power of 2 | `2 lg p·α + 2nβ + nγ − (1/p)(2nβ + nγ) ≈ 2 lg p·α + 2nβ + nγ` [ThakurRabenseifnerGropp05] | — |
| Same, `p` **not** a power of 2 | `(2 lg p₀ + 1 + 2f_α)α + (2 + (1+3f_β)/2)nβ + (3/2)nγ − (1/p₀)(2nβ + nγ) ≈ (3 + 2⌊lg p⌋)α + 4nβ + (3/2)nγ` [ThakurRabenseifnerGropp05] | performance cliff: bandwidth term **doubles**, compute term ×1.5 |
| Binary blocks | decompose `p` into power-of-two blocks; reduce-scatter within blocks, then combine smallest→largest | load imbalance governed by `δ_expo,max`, the max gap between consecutive exponents in the binary representation of `p` (e.g. `100 = 2⁶+2⁵+2²` ⟹ `δ = max(1,3) = 3`) [ThakurRabenseifnerGropp05] |
| Ring | `2(p−1)α + 2nβ + nγ − (1/p)(2nβ + nγ)` [ThakurRabenseifnerGropp05] | bandwidth-optimal, latency-poor: small/medium `p` or very large vectors |
| NCCL double binary tree | `≈ 2 lg p·α + β_tree·n` with per-GPU byte count asymptotically matching ring [NCCL24] | large `p`, small–medium messages |
| SHARP / in-network reduction | switch ALUs aggregate en route; per-endpoint receive drops from `B(p−1)/p` to `B/p` for reduce-scatter | `p ≳ 32` nodes, large vectors |

**Crossover, recursive doubling vs Rabenseifner.** Set `T_rd = lg p·α + n lg p·(β+γ)` and `T_rab = 2 lg p·α + n·(p−1)/p·(2β+γ)`. Recursive doubling wins iff

```
n < ( lg p · α ) / ( lg p·(β+γ) − ((p−1)/p)(2β+γ) )
```

which for `p ≥ 8` has a positive denominator, giving a finite threshold that shrinks as `p` grows. This is the analytic form behind MPICH's empirical rules and behind Rabenseifner's 2-D `(p, n) → algorithm` regime map on the Cray T3E: recursive doubling for `n ≤ 32 B`; vendor/binomial for `n ≤ 1 KB`; ring for some `n` and `p < 32`; binary blocks when `δ_expo,max < lg(n)/2.0 − 2.5` and `n ≥ 16 KB` and `p > 32` [ThakurRabenseifnerGropp05].

**Non-power-of-two: the "nearest power of two" trick.** MPICH's canonical prologue/epilogue: let `pof2 = 2^⌊lg p⌋`, `rem = p − pof2`. All *even* ranks `< 2·rem` send their vector to `rank+1` and set `newrank = −1`, dropping out. The odd ranks `< 2·rem` receive, apply `MPIR_Reduce_local` (order is right, so commutativity is not required), and set `newrank = rank/2`; ranks `≥ 2·rem` set `newrank = rank − rem`. The surviving `pof2` ranks run recursive doubling over `newrank` with `dst = (newdst < rem) ? newdst*2+1 : newdst+rem`. An epilogue sends the result back from odd to even ranks [MPICH-AR-src, ThakurRabenseifnerGropp05]. Cost: two extra `α_uni` and one extra `nβ_uni` plus `n/2·γ`; for the halving/doubling variant the penalty is the full doubling of the bandwidth term shown above. Rabenseifner's binary-blocks algorithm exists specifically to soften this cliff, and is identical to halving/doubling when `p` *is* a power of two.

**Segmentation/pipelining of reductions.** All of the above admit segmentation: split the vector into `n_s` chunks and pipeline them through the tree, converting `⌈lg p⌉(α + nβ + nγ)` into `⌈lg p⌉·n_s·(α + m_sβ + m_sγ)` for the binomial case [Pjesivac07] — trading extra startups for overlap of communication with the operator.

### 3.3 Floating-point non-associativity and reproducibility — *the standard's exact language*

This is the crux for any system that transplants MPI's reduction semantics.

MPI-4.1 / MPI-5.0, `MPI_REDUCE`:

> "The operation `op` is always assumed to be associative. All predefined operations are also assumed to be commutative. Users may define operations that are assumed to be associative, but not commutative. The ``canonical'' evaluation order of a reduction is determined by the ranks of the MPI processes in the group. However, the implementation can take advantage of associativity, or associativity and commutativity in order to change the order of evaluation. This may change the result of the reduction for operations that are not strictly associative and commutative, such as floating point addition." [MPI41, MPI50]

> *Advice to implementors.* "It is strongly recommended that `MPI_REDUCE` be implemented so that the same result be obtained whenever the function is applied on the same arguments, appearing in the same order. Note that this may prevent optimizations that take advantage of the physical location of ranks." [MPI41, MPI50; quoted identically from MPI-3.0 p.175 ll.9–13 in Balaji13]

> *Advice to users.* "Some applications may not be able to ignore the nonassociative nature of floating-point operations… Such applications should enforce the order of evaluation explicitly. For example, in the case of operations that require a strict left-to-right (or right-to-left) evaluation order, this could be done by gathering all operands at a single MPI process (e.g., with `MPI_GATHER`), applying the reduction operation in the desired order (e.g., with `MPI_REDUCE_LOCAL`), and if needed, broadcast or scatter the result to the other MPI processes." [MPI41, MPI50]

Read precisely: **MPI does not guarantee bitwise reproducibility across runs, across process counts, or across process placements.** It (i) declares associativity an *assumption* the implementation may exploit, (ii) defines a canonical rank order that the implementation is free to abandon, (iii) issues a non-binding recommendation of determinism-under-identical-argument-order, and (iv) tells users who need a specific order to build it themselves out of gather + `MPI_REDUCE_LOCAL` + broadcast. There is also a documented cost to honouring the recommendation: `MPI_REDUCE_SCATTER` with a **non-commutative** user op cannot use recursive halving at all and must fall back to recursive doubling, which costs `n(lg p − (p−1)/p)β` instead of `(p−1)/p·nβ` — a factor of roughly `lg p` in bandwidth [ThakurRabenseifnerGropp05]. MPI's commutative/non-commutative flag (set at `MPI_Op_create`) is thus already an *algorithm-selection gate*, not merely documentation.

Balaji and Kimpe quantify the performance price of reproducibility: topology-aware reduction (splitting the reduction into intra-node and inter-node phases, so the evaluation order follows the hardware rather than the ranks) improves `MPI_Allreduce` by roughly 50% and stabilizes at about twice the throughput of the topology-unaware algorithm across communicator sizes from 8 to 2,048 ranks — but by construction it changes the summation order and therefore the answer [Balaji13]. Their error analysis follows the standard result that the error bound for summation grows with the *depth* of the summation tree: sequential summation gives a bound proportional to `(p−1)ε·Σ|xᵢ|` while a balanced tree gives `≈ lg(p)·ε·Σ|xᵢ|`, so tree reduction is both faster *and more accurate* than a left fold for floating-point addition [Balaji13].

Pollard et al. formalize the space of admissible results, distinguishing RORA (random order, random association — the default `MPI_Reduce` behaviour, permitted to assume both commutativity and associativity) from ROLA (random order, left-associative) and the canonical order, and note that "the reduction tree shape has a greater effect on the summation error" than other factors for common distributions; they also observe that Open MPI and MPICH in practice do produce the same result for the same arguments in the same order, but that this order "is not necessarily canonical and so may differ from the serial semantics developers might expect" [Pollard20].

Two families of fixes, with fundamentally different guarantees:

- **Intel oneMKL Conditional Numerical Reproducibility (CNR).** Bitwise reproducibility *conditional* on the same executable and a *constant* number of threads, with run-time ISA dispatch pinned to a chosen code branch; a limited routine set (`?gemm`, `?symm`, `?trsm`, and batched/GPU level-3 BLAS) supports "strict CNR" that survives thread-count changes. It does not guarantee reproducibility if data ordering or the number of processors changes, has no distributed-memory implementation, does not guarantee correct rounding, does not reproduce NaN bit patterns, and can degrade performance by up to 2× [IntelCNR, Ahrens20].
- **ReproBLAS / binned summation (Demmel, Nguyen, Ahrens).** Change the *accumulator*, not the order. A "binned number" (default 6 floating-point words) is an order-independent reproducible accumulator built from a subset of IEEE-754-2008 plus bitwise operations. Summation becomes reproducible **regardless of summation order, number of processors, reduction tree shape, or data alignment**, in one pass / one reduction, at roughly `7n`–`9n` flops for `n` values, with error bounds up to `10⁻⁸`× smaller than conventional summation. `binnedMPI.h` supplies MPI reduction operators and datatypes so the result is independent of the tree MPI chooses. Measured slowdown: ~4× vs an optimized dot product on one Sandy Bridge core, but <1.2× for summing 10⁶ doubles across >512 Ivy Bridge cores [ReproBLAS, Ahrens20, Demmel15].

The design lesson: MPI made non-determinism the default and reproducibility the user's problem; ReproBLAS shows the superior move is to make the *operator* order-invariant so the implementation keeps its algorithmic freedom.

---

## 4. Scatter, Gather, Allgather

**Scatter / Gather.**

| Algorithm | Cost |
|---|---|
| Linear (root sends/receives `p−1` messages of `n/p`) | `(p−1)α + (p−1)/p·nβ` |
| Binomial / MST | `⌈lg p⌉α + (p−1)/p·nβ` [Chan07] |

MST scatter and MST gather are unusual: they **achieve the lower bound in both the `α` and the `β` term simultaneously, for all vector lengths** [Chan07]. There is no short/long regime split and no better algorithm to find — which is why van de Geijn's broadcast and Rabenseifner's reduce both use them as building blocks. `MPI_IN_PLACE` (passed as `sendbuf` at the root for gather, or `recvbuf` at the root for scatter) suppresses the root's self-copy; it is a memory-traffic optimization, not an algorithmic one, but it interacts with algorithm choice because it removes the assumption that the root's contribution lives in a separate buffer.

**Allgather.** Here `n` is total data gathered per process, so each process contributes `n/p`.

| Algorithm | Step description | Cost | Regime (MPICH) |
|---|---|---|---|
| Ring | step `k`: send to `i+1`, receive from `i−1`, forwarding what arrived last step; `p−1` steps of `n/p` each | `(p−1)α + (p−1)/p·nβ` | `n ≥ 512 KB` any `p`; `80 KB ≤ n < 512 KB` non-power-of-two |
| Recursive doubling | step `k`: exchange with partner at distance `2^k`, sending everything accumulated so far (`n/p`, `2n/p`, …, `2^{lg p−1}n/p`) | `lg p·α + (p−1)/p·nβ`; non-power-of-two step count bounded by `2⌊lg p⌋` | power-of-two `p`, `n < 512 KB` |
| Bruck (concatenation) | step `k`: send *all* accumulated data to `(i − 2^k)`, receive from `(i + 2^k)`, append; final local downward shift by `i` blocks | `⌈lg p⌉α + (p−1)/p·nβ` | `n < 80 KB` and non-power-of-two `p` |

All three hit the `(p−1)/p·nβ` bandwidth lower bound (each process *must* receive `n/p` from `p−1` others). They differ only in latency and constants. Bruck's contribution is that it takes `⌈lg p⌉` steps **for all `p`, including non-powers of two**, and — by inverting the direction of the dissemination barrier pattern (send to `i − 2^k` rather than `i + 2^k`) — keeps all communication *contiguous*, at the price of a final local block rotation [ThakurRabenseifnerGropp05, Bruck97]. Empirically the ring beats recursive doubling for long messages despite identical formulas, because it is a nearest-neighbour pattern; the `b_eff` benchmark showed some patterns (particularly nearest-neighbour) achieving more than twice the bandwidth of others on both Myrinet and the IBM SP [ThakurRabenseifnerGropp05]. That is Hockney assumption 2 failing, and it is why the formulas alone do not determine the choice.

**Reduce-scatter** (needed above, and the dual of allgather):

| Algorithm | Cost | Regime (MPICH) |
|---|---|---|
| Reduce + scatterv (legacy) | `(lg p + p − 1)α + (lg p + (p−1)/p)nβ + n lg p·γ` | — |
| Recursive halving (commutative) | `lg p·α + (p−1)/p·nβ + (p−1)/p·nγ`; non-power-of-two: `(⌊lg p⌋+2)α + 2nβ + n(1 + (p−1)/p)γ` | `n ≤ 512 KB` |
| Recursive doubling (non-commutative) | `lg p·α + n(lg p − (p−1)/p)β + n(lg p − (p−1)/p)γ` | `n < 512 B` |
| Pairwise exchange | `(p−1)α + (p−1)/p·nβ + (p−1)/p·nγ` | `n ≥ 512 KB` (commutative), `≥ 512 B` (non-commutative) |

Note that pairwise exchange and recursive halving have *identical* bandwidth terms and pairwise has a far worse latency term, yet MPICH prefers pairwise for long messages — again for the nearest-neighbour/pairwise-duplex reason [ThakurRabenseifnerGropp05]. Iannello derived reduce-scatter algorithms directly in LogGP [Iannello97].

---

## 5. Alltoall

Here `n` is the total data a process sends to (or receives from) all others.

| Algorithm | Step description | Cost |
|---|---|---|
| Bruck index algorithm | local upward rotation by `i` blocks; step `k` (`0 ≤ k < ⌈lg p⌉`): send to `(i + 2^k)` all blocks whose `k`-th bit is 1, receive from `(i − 2^k)` into those same slots; final local inverse rotation | power-of-two: `lg p·α + (n/2)lg p·β`; otherwise: `⌈lg p⌉α + ((n/2)lg p + (n/p)(p − 2^⌊lg p⌋))β` [ThakurRabenseifnerGropp05, Bruck97] |
| Irecv/Isend "spread-out" (rotated linear) | post all `p−1` `Irecv`s then all `p−1` `Isend`s, with source/dest computed as `(rank + i) % p` rather than `i`, to scatter targets | `≈ (p−1)α + nβ` `[UNVERIFIED]` as an exact form; the point of the rotation is avoiding a hot spot at rank 0 |
| Pairwise exchange | step `k` (`1 ≤ k < p`): partner `= rank XOR k` (power-of-two) or send to `rank+k`, receive from `rank−k` (general); direct source→destination, no intermediate hops | `(p−1)α + nβ` [ThakurRabenseifnerGropp05] |

**Crossover.** Bruck is a store-and-forward algorithm: it pays `(n/2)lg p·β` instead of `nβ`, i.e. `(lg p)/2` times the bandwidth, to reduce the step count from `p−1` to `⌈lg p⌉`. Equating `lg p·α + (n/2)lg p·β` with `(p−1)α + nβ` gives the crossover

```
n* = 2α(p − 1 − lg p) / (β(lg p − 2))
```

for `p > 4`. Below `n*`, Bruck wins. MPICH's empirical cutoffs: Bruck for `≤ 256 B` per message; irecv-isend for 256 B – 32 KB per message; pairwise exchange for `≥ 32 KB` [ThakurRabenseifnerGropp05]. Thakur et al.'s remark on Bruck is worth quoting for the design lesson: "it is a logarithmic algorithm for short-message all-to-all that does not need any extra bookkeeping or control information for routing the right data to the right process—that is taken care of by the mathematics of the algorithm" [ThakurRabenseifnerGropp05].

Alltoall is also the collective with the worst scaling in practice: it is bisection-bandwidth-bound, and in modern GPU clusters it "scales worst and is usually the first collective to bottleneck" for MoE workloads [NCCLkb] `[UNVERIFIED — secondary source]`.

---

## 6. Barrier

Barrier moves zero bytes, so all costs are pure `α` (or `L`, `o`, `g`) terms.

| Algorithm | Step description | Hockney cost | LogP cost |
|---|---|---|---|
| Linear / flat fan-in-fan-out | all report to root; root releases all | `(p−1)α` (with parallel zero-byte receives collapsing to ~one) | `T_min = (p−2)g + 2(L+2o)`, `T_max = (p−2)(g+o) + 2(L+2o)` [Pjesivac07] |
| Double ring | zero-byte token circulates twice | `2pα` | `2p(L + o + g)` [Pjesivac07] |
| Tree + broadcast (combining tree, tournament) | `⌈lg p⌉` arrival rounds up the tree + `⌈lg p⌉` wakeup rounds down | `≈ 2⌈lg p⌉α` | `Θ(lg p)` critical path, `Θ(p)` total remote writes, larger constant than dissemination [Hensgen88, ScottMellorCrummey92] |
| Butterfly (Brooks) | `lg p` rounds of pairwise exchange; power-of-two only | `lg p·α` | `Θ(lg p)` critical path, `Θ(p lg p)` total remote writes [Brooks86, ScottMellorCrummey92] |
| Recursive doubling | pairwise exchange at distance `2^k` | `lg p·α` if `p = 2^k`, else `(⌊lg p⌋ + 2)α` [Pjesivac07] | `lg p(L+o+g)`, else `(⌊lg p⌋+2)(L+o+g)` |
| **Dissemination** (Hensgen/Finkel/Manber) | round `k`: process `i` signals `(i + 2^k) mod p` and waits for `(i − 2^k) mod p` | `⌈lg p⌉α` **for all `p`** [ThakurRabenseifnerGropp05, Pjesivac07] | `⌈lg p⌉·max{t_r, t_s}`; assuming `o > g`, `= (2o + L)⌈lg p⌉` [HoeflerBarrier05] |

The dissemination barrier is the reference answer: `⌈lg p⌉` rounds regardless of whether `p` is a power of two, no atomic operations beyond load and store, `O(p)` space, `Θ(lg p)` critical path with `Θ(p lg p)` total remote writes [Hensgen88, ScottMellorCrummey92]. Hoefler et al. show it is provably optimal for single-port LogP-compliant systems and measured it at 1904 µs on 128 nodes vs 3642 µs (tournament), 4009 µs (combining tree), 4594 µs (central counter) and 3559 µs (Open MPI's default) [HoeflerBarrier05, HoeflerIBBarrier]. The `n`-way dissemination generalization (`speer_i = (p + i·(n+1)^r) mod P`) exploits multi-port/offloading NICs and is optimal for that model [HoeflerIBBarrier].

The tournament barrier's advantage is *volume*, not depth: `p−1` games and `O(p)` total instructions versus `O(p lg p)` for dissemination, at the cost of a larger constant on the critical path and forced waiting (some processes cannot announce arrival until peers have) [Hensgen88, ScottMellorCrummey92]. Hensgen et al. report dissemination competitive with tournament below 16 processes and the write-count gap (`2p⌈lg p⌉` vs `p−1`) dominating above it [Hensgen88]. MPICH/Open MPI also expose a Bruck-pattern barrier at `⌈lg p⌉α` [Pjesivac07]. Blue Gene/L had a dedicated **global interrupt network** for barriers, and BG/P added rectangular and global algorithms on the collective and interrupt networks — but usable only on `MPI_COMM_WORLD` [Almasi05jrd, Hoefler07BGL, Kumar09].

---

## 7. Scan and Exscan

Lower bounds in the one-ported message-passing model: `⌈lg p⌉` communication rounds for the inclusive scan and `⌈lg₂(p−1)⌉` for the exclusive scan [Traff25].

| Algorithm | Step description | Rounds | Operator applications | Cost |
|---|---|---|---|---|
| **Hillis–Steele / recursive doubling** | step `k` (`0 ≤ k < lg p`): rank `j` exchanges its running partial with `j XOR 2^k`; accumulate only if the source is a lower rank | `⌈lg p⌉` | `⌈lg p⌉` **per rank**, `Θ(p lg p)` total | `⌈lg p⌉(α + nβ + nγ)`; the MPICH default for `MPI_Scan` [ScanFPGA] |
| **Ladner–Fischer / Blelloch (up-sweep + down-sweep)** | up-phase: interior node at step `k` receives from `j − 2^k` when `j & (2^{k+1}−1) = 2^{k+1}−1`, forming `⊕_{i=l}^{r} M_i`; down-phase: `k` from `lg p` to 1, rank `j` with `j & (2^k − 1) = 2^k − 1` sends its partial to `j + 2^{k−1}` | `2⌈lg p⌉` | `Θ(p)` total — **work-efficient** | `≈ 2⌈lg p⌉(α + nβ) + Θ(1)·nγ` per rank [ScanFPGA, Sanders09] |
| **Two-tree scan** | both in-order trees run independently; up-phase ≡ reduction to rank 0, down-phase ≡ broadcast from rank 0 | — | — | `≈ 2×` the two-tree broadcast time, i.e. `≈ 2βn + 4α lg p + 2√(8αβn lg p)` [Sanders09] |
| **Träff exclusive-scan (2025)** | new simultaneous send-receive schedule | `q = ⌈lg(p−1) + lg(4/3)⌉` | `q − 1` | beats both the shift-based (`⌈lg(p−1)⌉+1` rounds) and modified-inclusive (`⌈lg p⌉` rounds, `2⌈lg p⌉ − 1` applications) approaches [Traff25] |

The Hillis–Steele/Ladner–Fischer distinction is the classic depth-vs-work tradeoff, and in MPI it is usually resolved in favour of depth because `γ` is tiny: `lg p` extra operator applications per rank cost almost nothing when the operator is `MPI_SUM` on doubles. Träff makes the opposite assumption explicit — the operator "could be expensive" — and optimizes the *number of applications* of `⊕` subject to a round bound [Traff25]. He also notes that all of these assume small input vectors so that rounds dominate; for large vectors "other (pipelined, fixed-degree tree) algorithms must be used" [Traff25]. `MPI_Exscan` is genuinely harder than `MPI_Scan`, and whether `⌈lg(p−1)⌉` rounds with `⌈lg(p−1)⌉` applications is achievable is stated as open [Traff25].

---

## 8. Point-to-point protocols

### 8.1 Eager vs rendezvous

- **Eager** (short messages): the sender transmits the entire message immediately with no prior synchronization. The receiver must have buffers posted to catch it, because a matching receive may not exist yet; typically one RDMA-Send [rdma-core, Barrett13]. Cost `≈ α + nβ` — one network traversal, minimum latency.
- **Rendezvous** (long messages): the sender transmits only the envelope/header (a **Request to Send**, RTS). When the receiver has a matching posted receive it replies **Clear to Send** (CTS), and only then is the payload moved — commonly as an RDMA-Read *issued by the receiver* directly out of the sender's registered buffer [rdma-core, INFO0939]. Cost `≈ 3` one-way latencies `+ nβ` for the classic three-way handshake [SciNetTuning], or RTS + Read for the RDMA variant. Crucially the round trip is paid **even if the receiver had already posted the receive** [Barrett13].

The tradeoff, stated exactly by Barrett, Hemmert and Underwood: "'eager short' messages require buffers to be made available at the target and the 'rendezvous long' messages incur a round-trip delay before transferring the body of the message. Eager protocols provide the lowest latency message transfers, but longer messages are bandwidth bound and can amortize the latency of a network round-trip" [Barrett13]. In LogGPS terms the switch point *is* the parameter `S` [Ino01].

**The eager limit / eager threshold.** A per-network tunable, chosen by the implementation and overridable by the user:

| Implementation | Knob | Default (version-dependent) |
|---|---|---|
| Open MPI | `btl_sm_eager_limit` / `btl_openib_eager_limit` / `btl_tcp_eager_limit`, `btl_openib_rndv_eager_limit` | 4 KB / 12 KB / 64 KB `[UNVERIFIED — from a tuning tutorial, not release notes]` |
| Intel MPI | `I_MPI_EAGER_THRESHOLD`, `I_MPI_INTRANODE_EAGER_THRESHOLD`, `I_MPI_RDMA_EAGER_THRESHOLD` | 256 KB / 256 KB / 16 KB `[UNVERIFIED — same caveat]` |
| MVAPICH2 | `MV2_IBA_EAGER_THRESHOLD` | — |
| Cray MPICH | `MPICH_GNI_MAX_EAGER_MSG_SIZE` | — |

[SciNetTuning, StackOverflowEager]

**What happens on overflow.** Eager messages arriving with no matching posted receive are "unexpected" and land in the **unexpected message queue (UQ)**; posted receives with no matching arrival sit in the **posted receive queue (PQ)** [rdma-core]. Both are (in classical implementations) linked lists scanned linearly, so matching cost is `O(|UQ|)` on a receive and `O(|PQ|)` on an arrival — which turns a producer/consumer imbalance into quadratic time. Worse, UQ buffering is *memory*: Barrett et al.'s exascale argument is that network bandwidth is growing much faster than latency is shrinking, so the bandwidth–delay product — and hence the buffering needed to keep the eager protocol saturating the wire — is rising in an environment where memory per core is falling [Barrett13]. The practical failure modes are (i) memory exhaustion / allocation failure inside MPI, (ii) latency collapse from queue scanning, (iii) flow-control stalls when the implementation runs out of eager credits.

**Copy-in/copy-out vs zero-copy.** Eager is inherently copy-in/copy-out: sender buffer → network → receiver bounce buffer → user buffer. Rendezvous with RDMA is zero-copy: the payload lands directly in the final user buffer, at the cost of memory registration (which is a per-byte host cost — precisely LogGOPS's `O` parameter [Hoefler10loggopsim]).

### 8.2 Ordering: the non-overtaking guarantee (exact wording)

MPI-4.1 §*Semantics of Point-to-Point Communication*:

> "**Order.** Messages are nonovertaking: If a sender sends two messages in succession to the same destination, and both match the same receive, then this operation cannot receive the second message if the first one is still pending. If a receiver posts two receives in succession, and both match the same message, then the second receive operation cannot be satisfied by this message, if the first one is still pending. This requirement facilitates matching of sends to receives. It guarantees that message-passing code is deterministic, if MPI processes are single-threaded and the wildcard `MPI_ANY_SOURCE` is not used in receives. (Some of the calls described later, such as `MPI_CANCEL` or `MPI_WAITANY`, are additional sources of nondeterminism.)" [MPI41]

> "**Progress.** If a pair of matching send and receive operations have been initiated, then at least one of these two operations will complete, independently of other actions in the system: the send operation will complete, unless the receive is satisfied by another message, and completes; the receive operation will complete, unless the message sent is consumed by another matching receive that was started at the same destination MPI process." [MPI41]

For multi-threaded processes the guarantee weakens sharply: "if the process is multi-threaded, then the semantics of thread execution may not define a relative order between two send operations executed by two distinct threads. The operations are logically concurrent… the two messages sent can be received in any order" [MPI22-order]. For nonblocking operations, order is defined by the *execution order of the initiating calls*, and non-overtaking extends to that ordering [MPI-nonblocking-semantics]. Note also that MPI-4.1 flags one paragraph of this section as ambiguous and slated for future clarification [MPI41] — the ordering guarantee is subtler than it looks.

Note what the guarantee is scoped to: **same (source, destination, communicator, matching receive)**. It is *not* a global ordering, *not* an ordering across communicators, and *not* an ordering when `MPI_ANY_SOURCE` is used. Determinism is a conjunction of three conditions (non-overtaking + single-threaded + no wildcards), and MPI is explicit that the conjunction is what buys it.

### 8.3 Why `MPI_Ssend`, `MPI_Bsend`, `MPI_Rsend` exist

Standard-mode `MPI_Send` deliberately leaves buffering unspecified. The standard's rationale:

> "The reluctance of MPI to mandate whether standard sends are buffering or not stems from the desire to achieve portable programs. Since any system will run out of buffer resources as message sizes are increased, and some implementations may want to provide little buffering, MPI takes the position that correct (and therefore, portable) programs do not rely on system buffering in standard mode. Buffering may improve the performance of a correct program, but it doesn't affect the result of the program." [MPI-modes]

The three explicit modes then pin down what standard mode leaves open:

- **`MPI_Ssend` (synchronous):** completes only after the matching receive has started. Safest and most portable, order of send/receive irrelevant, buffer space irrelevant — at the cost of full synchronization overhead. It is the mode that makes an unsafe program *fail deterministically* instead of intermittently, which is exactly why it is a debugging tool.
- **`MPI_Bsend` (buffered):** completes as soon as the data is copied into a user-supplied buffer attached with `MPI_Buffer_attach`. Decouples sender from receiver and removes synchronization overhead; errors out on buffer overflow; pays an extra memory copy. It makes buffering *explicit and accountable* rather than an implementation accident.
- **`MPI_Rsend` (ready):** may be started only if the matching receive is already posted; otherwise the operation is erroneous and the outcome undefined. "On some systems, this allows the removal of a hand-shake operation that is otherwise required and results in improved performance… In a correct program, therefore, a ready send could be replaced by a standard send with no effect on the behavior of the program other than performance." [MPI-modes] I.e. `Rsend` is a *user assertion* that lets the implementation skip the rendezvous handshake.

The design pattern generalizes: MPI's four send modes are four different *contracts about who owns the buffer and who pays for synchronization*, all with identical data semantics.

---

## 9. Progress and completion

**What "making progress" means.** MPI's progress rule (quoted above) is a liveness guarantee, not a performance guarantee: a matching send/receive pair cannot remain permanently outstanding. For nonblocking operations it is sharpened: "A call to `MPI_WAIT` that completes a receive will eventually terminate and return if a matching send has been started, unless the send is satisfied by another receive. In particular, if the matching send is nonblocking, then the receive should complete even if no call is executed by the sender to complete the send" [MPI-nonblocking-semantics].

The standard does *not* say when progress happens, which produces the industry's central engineering distinction:

- **Weak / polling progress.** The engine advances only when the application calls into MPI. Hoefler et al. are explicit: without a separate progress thread "there is only one way to progress an outstanding MPI operation. This is done by giving the control to the MPI library by calling some MPI function (e.g., `MPI_Test`)" [Hoefler07nbc]. LibNBC therefore requires the user to call `NBC_Test` periodically to advance both its own round schedule and the underlying MPI requests, and `NBC_Test` internally calls `MPI_Testall` on all outstanding requests. The cost is real: `NBC_Test` overhead grows linearly with the number of outstanding requests, so aggressive testing can *reduce* overall performance even while it accelerates the background operation [Hoefler07nbc, Hoefler07nbcdesign].
- **Asynchronous / thread progress.** A dedicated thread or offload engine advances communication. Measured tradeoff: a threaded implementation "allows nearly full overlap (frees nearly 100% CPU) as long as the system is not oversubscribed… However, this implementation fails to achieve any overlap (it shows even negative impact) if all cores are used for computation," whereas the point-to-point-based LibNBC "allows decent overlap in all cases" [Hoefler07NBCcase]. LibNBC deliberately avoided threads because `MPI_THREAD_MULTIPLE` was not efficiently supported [Hoefler07nbc].

**Requests and completion.** `MPI_Test` (local, non-blocking poll), `MPI_Wait` (blocks until complete), and the `*any/*some/*all` variants. Note `MPI_WAITANY` and `MPI_CANCEL` are named by the standard as *additional sources of nondeterminism* [MPI41] — a completion API can break the determinism the ordering rule provides.

**Probe and the matched-probe fix.** `MPI_Probe`/`MPI_Iprobe` inspect an incoming message without receiving it, enabling the idiom "probe → read size from status → allocate buffer → receive." This is **broken under threads**: "If a thread probes for a message and then immediately posts a matching receive, the receive may match a message other than that found by the probe since another thread could concurrently receive that original message" [MPI50-probe]. Gregor, Hoefler, Barrett and Lumsdaine's proposal (accepted for MPI-3.0) adds `MPI_Mprobe`/`MPI_Improbe`, which **atomically match** the message and return a *message handle*; the message "cannot be found by any other probe operation or matched by any other receive" and must be received with `MPI_Mrecv`/`MPI_Imrecv` on that handle [MprobeProposal, MPI31-mprobe]. The proposal originally deprecated `MPI_Probe`/`MPI_Iprobe` outright [MprobeProposal]. Hoefler's summary is blunt: "MPI-2.2 point-to-point communication is not thread safe! Easy to fix: return a message handle from probe!" — and matched probe is *faster* than the user-level workarounds it replaces, which each copied every message twice [Hoefler-MPI3-overview].

**Persistent requests.** `MPI_Send_init`/`MPI_Recv_init` + `MPI_Start` amortize argument checking, matching-structure setup and (on RDMA fabrics) memory registration across repeated identical transfers. The setup/steady-state split is the key idea.

**MPI-4 partitioned communication.** `MPI_Psend_init`/`MPI_Precv_init` register persistent buffers, a partition count and matching parameters once; matching happens *as if at initialization time*, so **wildcards are not supported** — deliberately, because this "avoids matching list overheads when wildcards are allowed (preventing some high performance matching software designs)" [PartitionedICPP22]. Then per epoch: `MPI_Start`, one `MPI_Pready(i, req)` per ready send partition (callable from inside a parallel region, by any thread, or by a GPU kernel in vendor prototypes), optional fine-grained `MPI_Parrived(req, i, &flag)` at the receiver, and a final single-threaded `MPI_Test`/`MPI_Wait` for overall completion [MPI50-partitioned, PartitionedICPP22]. The rationale is explicitly permissive: "MPI is free to choose how many transfers to do within a partitioned communication send independent of how many partitions are reported as ready… Aggregation of partitions is permitted but not required. Ordering of partitions is permitted but not required. A naive implementation can simply wait for the entire message buffer to be marked ready before any transfer(s) occur" [MPI50-partitioned]. That is a *scheduling hint interface*: the user declares data-readiness, the implementation decides transfer granularity.

---

## 10. Derived datatypes

**The formalism.** A **typemap** is a sequence of (basic type, byte displacement) pairs:

```
Typemap = { (type_0, disp_0), (type_1, disp_1), ..., (type_{n-1}, disp_{n-1}) }
```

The **type signature** is the same sequence with displacements erased: `Typesig = {type_0, ..., type_{n-1}}`. Displacements "are not required to be positive, distinct, or in increasing order. Therefore, the order of items need not coincide with their order in store, and an item may appear more than once." A typemap plus a base address specifies a communication buffer of `n` entries, the `i`-th at `buf + disp_i` [MPI-datatypes]. `extent` is the span from lowest to highest byte, rounded up for alignment: if `type_i` requires alignment to a multiple of `k_i`, `extent` is rounded to the next multiple of `max k_i`; `MPI_Type_create_resized` and `MPI_Type_get_true_extent` exist because that automatic rounding is wrong for cases such as unions [MPI-datatypes].

**Constructors:** `MPI_Type_contiguous`, `MPI_Type_vector` (count, blocklength, stride in *elements*), `MPI_Type_create_hvector` (stride in *bytes*), `MPI_Type_indexed` / `create_hindexed` / `create_indexed_block`, `MPI_Type_create_struct` (heterogeneous), `MPI_Type_create_subarray` (an `n`-dimensional slab of an `n`-dimensional array), `MPI_Type_create_darray`, `MPI_Type_create_resized`. Constructors compose recursively, and a type must be committed with `MPI_Type_commit` before use — the commit point is where implementations normalize/compile the representation.

**Type-matching rule.** Matching is on *type signatures*, not typemaps: the signature of the sent message must match the beginning of the signature specified by the receive, and layout on either side is irrelevant. This decouples "what values, in what order" (the contract) from "where they sit in memory" (the layout) — the whole point of the abstraction.

**The performance argument.** The standard states it directly: packing noncontiguous data into a contiguous buffer and unpacking at the receiver "has the disadvantage of requiring additional memory-to-memory copy operations at both sites, even when the communication subsystem has scatter-gather capabilities. Instead, MPI provides mechanisms to specify more general, mixed, and noncontiguous communication buffers. It is up to the implementation to decide whether data should be first packed in a contiguous buffer before being transmitted, or whether it can be collected directly from where it resides" [MPI-datatypes]. `MPI_Pack`/`MPI_Unpack` remain available for the cases where the user must materialize the wire format.

**Whether it works in practice.** The historical answer was often "no." Gropp et al. observed as early as 1999 that users found manual packing faster than the library's derived-type handling, defeating the abstraction [DatatypeStudy25 citing Gropp99]. The response was a line of datatype-engine papers: Träff et al.'s **flattening on the fly**, which replaces expensive recursive parsing of nested types with a stack-based iterative walk [Traff99, Byna03]; Gropp, Ross et al.'s **taxonomy of memory-access patterns** with optimized internal representations, also stack-based (the "dataloop" representation) [Gropp99, Byna03]; Byna, Gropp, Sun and Thakur's **memory-conscious packing**, applying cache-blocking-style transformations to the pack loops with architecture-dependent parameters [Byna03]; and Prabhu and Gropp's **DAME**, a runtime-compiled (JIT) datatype engine [PrabhuGropp15]. Reussner et al. extended SKaMPI to benchmark datatypes systematically and found up to a **16× latency penalty** for certain nested types on the Cray T3E versus only 2–4× on the NEC SX-5 — i.e. the performance of the *same* type varies by nearly an order of magnitude across implementations [Reussner00, DatatypeStudy25]. Thakur et al. independently measured up to **100×** speedups on `MPI_MAXLOC` reductions on the T3E purely because Cray's MPI implemented structured derived datatypes so slowly [ThakurRabenseifnerGropp05]. The design lesson: an abstraction whose benefit is "the implementation may avoid a copy" is only as good as the worst implementation the user will encounter.

---

## 11. Deadlock

**Canonical unsafe patterns.**

1. **Send/Send cycle.** Both ranks call `MPI_Send` to each other before either receives. If both messages fit under the eager limit, both sends complete into remote buffers and the program works. If either exceeds the eager limit, both senders issue RTS and block waiting for a CTS that will never come, "and both processes remain blocked indefinitely — a deadlock" [INFO0939]. The bug is *latent in the message size*: the same source code is correct at 8 KB and deadlocks at 8 MB, on the same machine.
2. **Recv/Recv.** Both ranks receive first. Unconditional deadlock, no buffering can save it.
3. **Buffer-dependent correctness ("accidentally correct").** This is the deep problem. MPI's rationale explicitly refuses to promise buffering so that programs cannot depend on it: "correct (and therefore, portable) programs do not rely on system buffering in standard mode" [MPI-modes]. A program that only works because the implementation happened to buffer is *incorrect but passing*, and the failure surfaces on a different machine, a different fabric, a different `I_MPI_EAGER_THRESHOLD`, or a larger problem size. Formal verification of MPI standardly parameterizes over **buffering mode** — verifying under both "zero buffering" and "infinite buffering" — precisely because of this [SharmaFM18].

**Fixes.** `MPI_Sendrecv` (and `MPI_Sendrecv_replace`) perform a send and a receive in one call, letting the implementation order them safely; nonblocking `Isend`/`Irecv` + `Waitall` breaks the cycle by deferring completion; `MPI_Ssend` converts latent buffering-dependence into a deterministic hang, which makes it a diagnostic.

**Formal work.**

- **ISP** (Vakkalanka, Vo, Gopalakrishnan, Kirby, Thakur, Gropp) — dynamic formal verification by direct model-checking of MPI/C source under a *verification scheduler*, using the POE algorithm (dynamic partial-order reduction). Wildcard receives are *dynamically rewritten* into specific-source receives once the set of possible senders is known; `Irecv(*)` becomes `Irecv(from P0)` and `Irecv(from P2)`, and match-sets are issued into the runtime as "big-step" moves. ISP verified up to 14K lines of MPI/C (ParMETIS, MADRE), producing deadlock and assertion-violation traces in seconds, but uses a **centralized scheduler** and re-executes the program per interleaving, so cost can be exponential [Vo09, Hilbrich13].
- **DAMPI** (Vo, Vakkalanka, Williams, Gopalakrishnan, Kirby, Thakur) — the same coverage goal, decentralized. It replaces ISP's central scheduler with a **Lamport-clock-based distributed algorithm** to compute alternative nondeterministic matches and enforce them on subsequent replays, with heuristics to focus coverage. Demonstrated on >1,000 processes, "an order of magnitude larger than any previously reported results for MPI dynamic verification tools." It uses timeout-based deadlock detection, so it can produce false positives and cannot detect the send/send deadlock [Vo10, Hilbrich13].
- **MUST** (Hilbrich et al.) — a runtime *monitor* rather than a state-space explorer: it analyzes the actual execution, builds wait-for dependency graphs (with AND/OR semantics for wildcard receives whose matching send is unknown), detects deadlocks including the send/send case, and visualizes them. Its hard problem is the converse of ISP's: because it does not rewrite calls, it "needs to adapt its matching decisions to the same decisions that the MPI implementation makes," or its analysis diverges from the real run [Hilbrich13].
- **CIVL** — builds a model of the program by symbolic execution and then model-checks it for deadlocks and MPI-standard conformance; per one assessment it "does not handle non-blocking MPI operations" [SharmaFM18] `[UNVERIFIED — single secondary source; CIVL's coverage may have expanded]`.

The taxonomy generalizes cleanly: *control the schedule and replay* (ISP, DAMPI) versus *observe the schedule and reason* (MUST) versus *abstract the program and prove* (CIVL). Each buys a different point on the coverage/scale/precision triangle.

---

## 12. Topology and mapping

**Interfaces.** `MPI_Cart_create(comm_old, ndims, dims, periods, reorder, comm_cart)` plus `MPI_Dims_create`, `MPI_Cart_rank`, `MPI_Cart_coords`, `MPI_Cart_shift`, `MPI_Cart_sub` for regular grids/tori. MPI-1's `MPI_Graph_create` required every process to specify the *entire* graph and is therefore unscalable. MPI-2.2 added the distributed graph constructors [Hoefler11mpi22topo]:

- `MPI_Dist_graph_create_adjacent(comm_old, indegree, sources, sourceweights, outdegree, destinations, destweights, info, reorder, comm)` — each process declares its own in- and out-neighbours.
- `MPI_Dist_graph_create(comm_old, n, sources, degrees, destinations, weights, info, reorder, comm)` — fully distributed: "a process need not even be a source or destination of the edges it specifies"; the graph handed to the library is the *union* of all locally specified edges. Duplicate nodes and duplicate edges are permitted; isolated processes are allowed [MPI50-distgraph, Hoefler11mpi22topo].

**`reorder`.** "If `reorder = false`, all MPI processes will have the same rank in `comm_dist_graph` as in `comm_old`. If `reorder = true` then the MPI library is free to remap to other MPI processes (of `comm_old`) in order to improve communication on the edges of the communication graph. The weight associated with each edge is a hint to the MPI library about the amount or intensity of communication on that edge, and may be used to compute a 'best' reordering" [MPI50-distgraph]. Formally, `reorder = true` licenses the library to apply a permutation `π` to the rank mapping; such optimizations "must not be attempted if `reorder` is set to false" [Hoefler11mpi22topo].

**The mapping problem.** Given a communication graph and a network graph, minimize congestion (max load on any physical link) and dilation (hops per logical edge). Hoefler and Snir note the field's history — Bokhari's early strategy ignored unmapped edges, which was later shown to hurt congestion and dilation badly; Lee and Aggarwal added greedy assignment plus pairwise swaps over all edges; Bollinger and Midkiff used simulated annealing; Träff gave a strategy for strictly hierarchical SMP-cluster networks — and observe the practical reality that "finding a good mapping is non trivial and MPI implementations tend to use the trivial identity mapping" [Hoefler11generic]. So MPI ships the *interface* for topology awareness while typically declining to exploit it.

**Why topology awareness changes the optimal algorithm.** Three distinct mechanisms:

1. **Bandwidth is not flat (Hockney assumption 2 fails).** Thakur et al. traced the ring-beats-recursive-doubling anomaly for long-message allgather to exactly this and confirmed with `b_eff` that nearest-neighbour patterns can exceed twice the bandwidth of long-distance patterns [ThakurRabenseifnerGropp05]. When `β` depends on distance, the algorithm minimizing `Σβ` changes.
2. **Hierarchy admits algorithm composition.** The standard structure is intra-node aggregation → inter-node collective → intra-node broadcast. Chan et al. formalize the multidimensional version: for a 2-D mesh, short vectors use MST within columns then MST within rows (cost `≈` MST applied successively per dimension); long vectors use MST-scatter within columns, MST-scatter within rows, BKT-allgather within rows, BKT-allgather within columns [Chan07]. Open MPI's HAN framework does this at the software level, using non-blocking collectives (LibNBC, ADAPT) for the inter-node layer to overlap it with shared-memory intra-node collectives (SM, SOLO), pipelining segments through a task graph, with an autotuner over submodule and segment-size choices [HAN21]. HierKNEM integrates the levels more tightly, offloading intra-node memory copies to non-leader processes via KNEM so leaders can dedicate themselves to inter-node forwarding without serialization, explicitly managing memory-bus contention between intra-node copies and NIC DMA [HierKNEM].
3. **Hardware collectives change the cost function entirely.** Blue Gene/L had a dedicated **collective network** whose ALU "can combine incoming and local packets using bitwise and integer operations, and forward the resulting packet along the network," with fixed 256-byte packets, 10 bytes of overhead (`g = 256/266 = 96%` efficiency) and ~350 MB/s raw [Almasi05jrd]. Measured: 1 MB broadcast and integer allreduce both at ~2.98 ms independent of node count (~336 MB/s, near the collective network's payload bandwidth) — but **floating-point reductions were 3–4× slower because the collective network had no FP arithmetic**, and the collective and global-interrupt networks were usable only on `MPI_COMM_WORLD`; other communicators fell back to torus algorithms using the "deposit bit" feature for rectangular subsets [Hoefler07BGL]. BG/P generalized this into three algorithm classes: global algorithms on the collective/interrupt networks for `MPI_COMM_WORLD`, rectangular algorithms on the torus, and binomial algorithms over point-to-point for irregular communicators [Kumar09]. Cray XC systems can offload single-word `MPI_Allreduce`, `MPI_Barrier` and `MPI_Bcast` to the NIC's collective engine when DMAPP is linked (`MPICH_USE_DMAPP_COLL=1`) [CrayXC18]. NVIDIA **SHARP** performs the reduction inside switch silicon: a `sharp_am` daemon alongside the subnet manager builds in-network aggregation trees; for a ring reduce-scatter each endpoint normally receives `B(p−1)/p` bytes over `p−1` hops, but with in-network reduction the switch returns only `B/p`, and NVLink SHARP (NVLS) multicast lets each GPU send its `B/p` segment once while the network replicates it [SHARP, SHARPkb]. Gains are scale-dependent: marginal at 2–4 nodes, substantial at ≥32 nodes [SHARPkb].

The unifying point: **the "optimal algorithm" is a function of the cost model, and topology-aware hardware changes the cost model qualitatively, not just quantitatively.** And per Balaji and Kimpe, taking advantage of it costs you reproducibility [Balaji13].

---

## 13. Summary table: algorithm → cost → regime

| Collective | Algorithm | Cost (α–β model) | Wins when |
|---|---|---|---|
| **Bcast** | linear | `(p−1)(α+nβ)` | `p ≤ 3` |
| | binomial / MST | `⌈lg p⌉(α+nβ)` | short `n` (`<12 KB` MPICH) or `p < 8` |
| | pipeline (chain) | `(p+n_s−2)(α+m_sβ)` | long `n`, small `p`, good segment size |
| | scatter+ring-allgather (vdG) | `(lg p+p−1)α + 2·(p−1)/p·nβ` | long `n`, `p > 4` |
| | scatter+recdbl-allgather | `2 lg p·α + 2·(p−1)/p·nβ` | long `n`, power-of-two `p` |
| | two-tree | `βn + 2α lg p + √(8αβn lg p)` | large `n`, large `p`, arbitrary `p` |
| | ESBT (hypercube) | `βn + α lg p + √(4αβn lg p)` | `p = 2^d` only |
| **Reduce** | binomial tree | `⌈lg p⌉(α+nβ+nγ)` | `n ≤ 2 KB`, user-defined ops, non-pow-2 |
| | Rabenseifner (rs+gather) | `2 lg p·α + 2·(p−1)/p·nβ + (p−1)/p·nγ` | `n > 2 KB`, predefined ops |
| | h&d, non-pow-2 | `≈(2+2⌊lg p⌋)α + 3nβ + (3/2)nγ` | — |
| **Allreduce** | recursive doubling | `lg p·α + n lg p·(β+γ)` | small `n`; user-defined ops |
| | Rabenseifner (rs+allgather) | `2 lg p·α + 2·(p−1)/p·nβ + (p−1)/p·nγ` | large `n`, predefined ops, pow-2 `p` |
| | h&d, non-pow-2 | `≈(3+2⌊lg p⌋)α + 4nβ + (3/2)nγ` | — (cliff) |
| | binary blocks | — | `δ_expo,max < lg n/2 − 2.5`, `n ≥ 16 KB`, `p > 32` |
| | ring | `2(p−1)α + 2nβ + nγ` | non-pow-2, small/medium `p`, huge `n` |
| | double binary tree | `≈2 lg p·α + β_tree·n` | large `p`, small–medium `n` (GPU clusters) |
| | SHARP / in-network | endpoint receive `B/p` not `B(p−1)/p` | `p ≳ 32` nodes, large `n` |
| **Reduce-scatter** | recursive halving | `lg p·α + (p−1)/p·n(β+γ)` | commutative, `n ≤ 512 KB` |
| | recursive doubling | `lg p·α + n(lg p−(p−1)/p)(β+γ)` | **non-commutative**, `n < 512 B` |
| | pairwise exchange | `(p−1)α + (p−1)/p·n(β+γ)` | `n ≥ 512 KB` |
| **Scatter/Gather** | binomial / MST | `⌈lg p⌉α + (p−1)/p·nβ` | **always** (optimal in both terms) |
| **Allgather** | ring | `(p−1)α + (p−1)/p·nβ` | `n ≥ 512 KB` |
| | recursive doubling | `lg p·α + (p−1)/p·nβ` | pow-2 `p`, `n < 512 KB` |
| | Bruck | `⌈lg p⌉α + (p−1)/p·nβ` | `n < 80 KB`, non-pow-2 `p` |
| **Alltoall** | Bruck index | `lg p·α + (n/2)lg p·β` | `≤ 256 B` per message |
| | irecv/isend rotated | `≈(p−1)α + nβ` | 256 B – 32 KB per message |
| | pairwise exchange | `(p−1)α + nβ` | `≥ 32 KB` per message |
| **Barrier** | linear | `(p−1)α` | `p` tiny |
| | recursive doubling | `lg p·α` / `(⌊lg p⌋+2)α` | pow-2 `p` / otherwise |
| | **dissemination** | `⌈lg p⌉α` all `p` | **default choice**; optimal for single-port LogP |
| | tournament | `≈2⌈lg p⌉α`, `Θ(p)` total ops | when total message count, not depth, is scarce |
| **Scan** | Hillis–Steele (recdbl) | `⌈lg p⌉(α+nβ+nγ)`, `Θ(p lg p)` ops | cheap operator (MPICH default) |
| | Ladner–Fischer/Blelloch | `≈2⌈lg p⌉(α+nβ)`, `Θ(p)` ops | **expensive operator** |
| | two-tree scan | `≈2×` two-tree bcast | large vectors |
| **Exscan** | Träff 2025 | `q=⌈lg(p−1)+lg(4/3)⌉` rounds, `q−1` ops | expensive operator |
| **Point-to-point** | eager | `α + nβ` | `n <` eager limit |
| | rendezvous (RTS/CTS) | `≈3α + nβ`; RDMA-read variant `≈2α + nβ` | `n ≥` eager limit; bounded receiver memory |

---

## Transfer notes for an LLM-agent setting

Assume: a "message" is text or a structured artifact; `β` is a per-token cost (money and decode time); `α` is one LLM round trip; a reduction operator is an LLM call (`summarize`, `merge`, `vote`, `critique`) that is **not associative, not commutative, not idempotent, and not deterministic**. Three constants change by orders of magnitude, and the changes are not uniform — which is the whole story.

**1. The α/β ratio collapses, and a third term takes over.** In MPI, `α ≈ 1–10 μs` and `β ≈ 10⁻¹⁰ s/byte`, so `α/β ≈ 10⁴–10⁵` bytes: latency dominates until messages are large. For agents, `α ≈ 1–30 s` (a round trip including queueing) and `β ≈ 10⁻² s/token` (≈100 tok/s decode), so `α/β ≈ 10²–10³ tokens`. The latency-dominated regime is *narrow*: essentially anything longer than a paragraph is already bandwidth-bound. Consequence: the short-message algorithms — recursive doubling, Bruck, dissemination — occupy a regime that barely exists. The long-message algorithms (scatter+allgather, reduce-scatter, pipelining) are the relevant ones almost always.

More important: MPI treats `γ` (per-byte operator cost) as negligible relative to `β`. In the agent setting the operator *is* an LLM call, so `γ` is not per-byte at all — it is **per invocation**, and each invocation costs one `α` plus the tokens it reads and writes. The correct cost model is therefore not `α + nβ + nγ` but something like

```
T = (critical-path LLM calls) × α  +  (prefill tokens) × β_in  +  (decode tokens) × β_out
Cost$ = (total LLM calls) × (fixed) + Σ tokens × price
```

with `β_in ≪ β_out` (prefill is typically an order of magnitude cheaper per token than decode). **Latency is governed by the depth of the call graph; money is governed by the total number of calls and tokens.** This is the LogP insight — separate network time from processor occupancy — reappearing, except now "processor occupancy" is the dominant term. Any transplant that keeps counting bytes and ignores call counts will select the wrong algorithms.

**2. Single-portedness is false, and this kills most of the algorithm zoo.** MPI's `⌈lg p⌉`-step algorithms exist because a NIC can handle one send and one receive at a time. An LLM agent can receive `p−1` "messages" in a *single prompt* — there is no port, only a context window. So the justification for Bruck, recursive doubling, ring, and dissemination evaporates for the *communication* part: the harness can deliver all payloads in one round. What replaces single-portedness as the binding constraint is **context capacity**, a constraint MPI does not have at all (MPI assumes memory ≫ message). Recursive-doubling allgather is the sharpest illustration: at step `k` each participant holds `2^k·n/p` tokens, so the algorithm is only feasible while `n ≤` context budget. In MPI, allgather's cost is `lg p·α + (p−1)/p·nβ`; in agent-land it is `α + n·β_in` if it fits and *impossible* if it does not. Capacity, not latency, is the new asymptotic wall.

**3. Non-associativity is semantic, not numerical — and it inverts the depth argument.** For floating-point sums, a deeper reduction tree is *worse* for latency but *better* for error: sequential summation's bound scales as `(p−1)ε·Σ|xᵢ|`, tree summation's as `lg(p)·ε·Σ|xᵢ|` [Balaji13]. So MPI gets to prefer trees for both reasons at once. For LLM merges, **tree depth is the number of times information is re-encoded**, and each re-encoding is lossy and irreversible: `summarize(summarize(a,b),c)` may omit content that `summarize(a,summarize(b,c))` retains, and the divergence compounds. There is no `ε`; the error is unbounded. This means:

- A **left fold** (linear reduce, "running summary") has depth `p−1` — the worst possible number of re-encodings. Bad.
- A **binary tree** has depth `lg p` — much better, but still `lg p` lossy layers.
- The right answer is a **`k`-nomial tree with `k` as large as context allows**. Let `s` = artifact size in tokens and `C` = usable context for a merge call. Then `k* = ⌊C/s⌋`, critical-path calls `= ⌈log_{k*} p⌉`, total calls `= ⌈(p−1)/(k*−1)⌉`, and
  ```
  T ≈ ⌈log_{k*} p⌉ · ( α + k*·s·β_in + s·β_out )
  ```
  With `p = 64`, `s = 2000` tokens and `C = 100k`, `k* = 50` and depth is `2` — two lossy layers instead of six. **Fan-in should be maximized subject to context, which is the opposite of MPI's guidance** (MPI wants binary/binomial fan-in because ports are scarce). This is the single most transferable *new* result.

**4. Rabenseifner and van de Geijn: one transplants beautifully, the other does not.** Both replace an `n lg p·β` term with `2nβ` by decomposing the vector across processes.

- **Scatter + allgather for broadcast transplants, and is the highest-value transplant in the memo.** Broadcasting a document to `p` agents costs `p·n` prefill tokens. Instead: scatter slices (`n` tokens total), have each agent produce a summary compressing by factor `c`, then allgather the summaries (`p·n/(p·c) = n/c` per agent). Total `≈ n + p·n/c` versus `p·n`, a win of roughly `c` when `c ≪ p`. This is a real, checkable token-cost reduction and it is exactly van de Geijn's argument with `β_in` in place of `β`.
- **Reduce-scatter's recursive halving does *not* transplant to prose.** It presupposes the reduction decomposes elementwise over a vector: you can reduce component 3 without seeing component 1. Text is not such a vector — you cannot summarize the second half of a document independently of the first and then concatenate. Recursive halving is valid *only* when the artifact is genuinely a partitionable collection: a set of extracted claims, a list of candidate answers, a score vector, a set of test results. For those cases it is excellent and should be the default; for free-form prose it is semantically wrong regardless of its cost formula. **AgentMPI should type artifacts as "partitionable collection" vs "atomic document" and gate reduce-scatter on that type**, exactly as MPI gates recursive halving on the commutativity flag.

**5. Store-and-forward is safe only when the harness forwards, never the model.** Bruck (allgather and alltoall), pipelined/chained broadcast, and every multi-hop tree require intermediate participants to *relay payload verbatim*. An LLM asked to relay text is a lossy channel: it paraphrases, truncates, and reorders. Rule: **any algorithm with an intermediate hop is admissible only if the hop is executed by non-LLM code.** If a model must re-emit the payload, the algorithm's correctness proof — which assumes the forwarded bytes are the received bytes — is void. This kills Bruck outright as an LLM-level algorithm (its extra `(lg p)/2` bandwidth factor is also now real money for no latency benefit, since the harness can batch). It preserves pipelining, because pipelining's relays are naturally harness-level.

**6. Reduce-scatter, not allreduce, is the fundamental primitive.** In MPI, allreduce is the most-used collective (Rabenseifner's five-year T3E profiling found >40% of MPI time in `MPI_Allreduce` + `MPI_Reduce` [ThakurRabenseifnerGropp05]) and reduce-scatter is a building block. In a context-limited world the priority inverts: reduce-scatter is the only collective that keeps per-agent state bounded at `n/p` after the operation. Allreduce, by contrast, requires every agent to end up holding the full result — which may not fit, and which is wasteful when each agent only needs part of it. Design consequence: AgentMPI's reduction API should default to reduce-scatter and treat allreduce as the special case, not the reverse.

**7. Scan/Exscan become disproportionately important, and MPI's *unpopular* choice becomes the right one.** The pattern "agent `j` needs the accumulated state of all agents before it" is ubiquitous: writing document section `j` consistent with sections `0..j−1`, applying migration `j` after `0..j−1`, budgeting. MPI uses Hillis–Steele recursive doubling because `⌈lg p⌉` operator applications *per rank* (`Θ(p lg p)` total) is free when the operator is `MPI_SUM`. When each application is an LLM call, `Θ(p lg p)` versus `Θ(p)` is a `lg p` multiplier on the bill. **Ladner–Fischer/Blelloch work-efficient scan is the correct default**, and Träff's exclusive-scan result — `q = ⌈lg(p−1) + lg(4/3)⌉` rounds with only `q−1` operator applications, explicitly motivated by operators that "could be expensive" [Traff25] — is the closest thing in the MPI literature to an algorithm designed for this cost model. It should be cited as prior art for the whole approach.

**8. Barrier: keep the concept, discard every algorithm.** Dissemination, tournament, and butterfly barriers optimize `⌈lg p⌉` rounds of zero-byte messages under single-portedness. In a harness, a barrier is a counter — `O(1)`, free, and obviously implemented in the control plane. Routing a barrier through LLM calls is never correct. But the *cost* of a barrier changes character in a way that matters more than the algorithm: MPI's barrier costs `⌈lg p⌉α`; an agent barrier costs `max_i T_i`, and LLM completion times have heavy tails (variable output length × variable queueing). So the barrier's cost is a **tail statistic**, and the relevant optimizations are stragglers-oriented (speculative duplication, deadline-bounded truncation, quorum instead of full barrier) — none of which appear in the barrier literature, because MPI processes are homogeneous and MPI has no timeouts.

**9. Eager/rendezvous transplants almost perfectly, and the failure mode is identical.** Eager = inline the artifact in the recipient's prompt. Rendezvous = inline a handle (URI, file path, tool-call descriptor) plus a short descriptor, and let the recipient fetch it if it decides to. The eager limit is the token budget you will spend on *unrequested* context, and the crossover is at `α/β ≈ 10²–10³` tokens — a concrete, defensible default (a few hundred tokens inline, handles above that). RTS/CTS = "here is a 200-token abstract; ask me for the full document if you need it." Zero-copy/RDMA = the recipient reads the artifact from shared storage without the sender re-emitting it through a model (which, per point 5, is also the *only* lossless path). And the overflow pathology is the same one Barrett et al. describe: an **unexpected message queue** of artifacts nobody read, consuming context and money, with linear matching cost. AgentMPI should have an explicit unexpected-artifact policy (bounded queue, eviction, backpressure) for exactly MPI's reason, and the bandwidth–delay argument has a direct analogue — as model context grows faster than round-trip latency shrinks, the temptation to be eager grows and so does the blowup.

**10. Derived datatypes → typed artifacts, and the type-matching rule is the valuable part.** The typemap/type-signature split — matching on the *sequence of primitive values*, with layout left to the implementation — is a clean transplant to structured outputs: a schema is a type signature, and the matching rule ("the sender's signature must match the beginning of the receiver's") gives you forward-compatible artifact evolution for free. The performance argument transplants with more force than in MPI: there, an unnecessary pack is a memory copy; here, an unnecessary reformat is *an LLM call that can hallucinate*. Every LLM-mediated reserialization is a lossy pack/unpack, so the harness should project typed artifacts mechanically and never ask a model to reformat data it is merely passing through. The cautionary half also transplants: MPI's datatype abstraction underdelivered for a decade (up to 16× penalties on some implementations [Reussner00], 100× on `MPI_MAXLOC` on the T3E [ThakurRabenseifnerGropp05]) because its benefit was contingent on implementation quality. An AgentMPI schema layer whose payoff is "the harness may avoid a re-encoding" must actually avoid it, or users will hand-pack.

**11. Reproducibility: MPI's contract is the right shape but too weak.** MPI's language — canonical order defined by rank, associativity assumed and exploitable, determinism only "strongly recommended," users told to build strict order themselves out of gather + `MPI_REDUCE_LOCAL` + broadcast — is precisely the set of clauses AgentMPI needs, because it separates *what the user may assume* from *what the implementation may do*. But LLM reductions are worse off than floating point on every axis: non-associativity is unbounded rather than `O(ε)`, the operator is non-deterministic even at fixed order (sampling, batching-dependent kernels, silent model-version drift), and there is no analogue of "the same answer to within `ε`." So a "strongly recommended" clause is not enough. Two concrete positions to take:

- **Adopt the commutativity gate, and extend it.** MPI already makes `MPI_Op_create`'s commute flag an algorithm-selection input (commutative ⟹ recursive halving; non-commutative ⟹ recursive doubling at `lg p`× the bandwidth). AgentMPI should require declaration of **A/C/I** properties (associative, commutative, idempotent) and permit tree reductions only for operators declared ACI, falling back to a strict, canonically-ordered fold otherwise — and it should *charge* for the fallback in the cost model, exactly as MPI does.
- **Prefer ReproBLAS's move over MPI's.** Instead of fixing the *order*, make the *accumulator* order-invariant. The analogue of a binned number is a merge whose state is a canonically-ordered set of atomic claims with provenance, so that `merge = union + dedup + canonical sort`: associative, commutative, idempotent by construction, and independent of the tree shape the harness chooses. Free-form prose summarization has none of these properties; a claim-set representation has all of them, at a token overhead analogous to ReproBLAS's 6-word accumulator and `7n`–`9n` flops. This lets the harness keep full algorithmic freedom (including topology-aware, cheap-model-first hierarchies) *without* sacrificing reproducibility — which is exactly the tradeoff Balaji and Kimpe showed MPI cannot escape (≈50% allreduce speedup from topology awareness, paid for in determinism [Balaji13]).

**12. Matched probe is a design requirement, not a footnote.** The bug that forced `MPI_Mprobe` into MPI-3 — a thread probes, then posts a receive, and a different thread has already taken the message — is the *first* concurrency bug any multi-agent harness with a shared work queue will hit. MPI's fix is the right one and should be adopted wholesale: **only expose matched-probe semantics.** Inspecting a pending item must atomically claim it and return a handle that is the sole means of consuming it. Do not ship the unmatched variant; MPI's own proposal moved to deprecate it [MprobeProposal].

**13. Partitioned communication is the sleeper transplant.** `MPI_Psend_init` / `MPI_Pready` / `MPI_Parrived` is a near-exact model of streaming LLM output with incremental downstream consumption: the producer marks partitions ready as it decodes; the consumer may check `Parrived(i)` and start work on partition `i` before the whole artifact lands; the runtime decides transfer granularity, is free to aggregate, and is free to reorder [MPI50-partitioned]. The persistent setup/steady-state split also matches agent harnesses well, where the topology is stable across many rounds and per-round matching should not be re-derived. Two cautions from the MPI experience: partitioned communication deliberately forbids wildcards to keep matching cheap (a good constraint to copy), and the GPU prototypes document a real deadlock — a kernel that both marks partitions ready and polls `Parrived` can starve, because the standard does not require `Parrived` to return true until all partitions are ready [NVIDIA-mpi-acx]. The analogous agent bug is an agent that awaits its own downstream consumer.

**14. Where AgentMPI should refuse to follow MPI.** MPI's progress rule is a liveness guarantee with **no timeout, no retry, and no fault model**; `MPI_Cancel` on sends is close to unusable in practice; the default failure semantics are "abort the job." That is defensible for a batch scheduler on a reliable fabric and indefensible for a system whose "network" is a rate-limited, occasionally-failing, non-deterministic remote service. The progress rule should be *strengthened* into a scheduler contract with explicit deadlines, cancellation, retry-with-idempotency-keys, and partial-failure semantics for collectives (what does `allreduce` return when one agent times out? MPI has no answer; AgentMPI must). Likewise, MPI's decision to leave standard-send buffering unspecified produced the "accidentally correct" class of bugs that spawned an entire verification literature (ISP, DAMPI, MUST, CIVL) — a deliberate under-specification whose cost was paid by users for twenty years. AgentMPI should specify its buffering, and specify it as bounded with explicit backpressure, so that the size-dependent latent deadlock (`8 KB` works, `8 MB` hangs) is impossible by construction rather than detectable by model checking.

**15. Net assessment.** What does *not* transplant is the algorithm catalogue: the `⌈lg p⌉`-step collectives are artifacts of single-portedness and a large `α/β` ratio, and both premises fail. What transplants powerfully is the *methodology*: (i) an explicit cost model with named parameters, (ii) multiple algorithms per operation with published cost formulas and stated crossovers, (iii) a decision function selecting on `(p, size)` — with Open MPI's candid admission that baked-in thresholds degrade off the profiled hardware [OpenMPItunedDoc] as a warning to autotune rather than hard-code, (iv) properties of the operator (commutativity) as a first-class algorithm-selection input, (v) a written contract about ordering and determinism separating user guarantees from implementation freedom, and (vi) matched-probe-style atomicity in the queue API. And the three genuinely new results this transposition yields: **maximize reduction fan-in subject to context** (opposite of MPI's binary-tree preference), **make reduce-scatter the primitive and allreduce the special case** (opposite of MPI's usage profile), and **count operator applications rather than bytes** (which promotes Ladner–Fischer and Träff's exscan from footnotes to defaults).

---

## References

```bibtex
@article{Hockney94,
  author  = {Roger W. Hockney},
  title   = {The Communication Challenge for MPP: Intel Paragon and Meiko CS-2},
  journal = {Parallel Computing},
  volume  = {20}, number = {3}, pages = {389--398}, year = {1994},
  doi     = {10.1016/S0167-8191(06)80021-9}
}

@inproceedings{Culler93,
  author    = {David E. Culler and Richard M. Karp and David A. Patterson and Abhijit Sahay and
               Klaus E. Schauser and Eunice Santos and Ramesh Subramonian and Thorsten von Eicken},
  title     = {LogP: Towards a Realistic Model of Parallel Computation},
  booktitle = {Proc. 4th ACM SIGPLAN Symp. on Principles and Practice of Parallel Programming (PPoPP)},
  pages     = {1--12}, year = {1993},
  doi       = {10.1145/155332.155333},
  url       = {https://www.cs.umd.edu/class/fall2019/cmsc714/readings/Culler-LogP.pdf}
}

@article{Alexandrov97,
  author  = {Albert Alexandrov and Mihai F. Ionescu and Klaus E. Schauser and Chris Scheiman},
  title   = {LogGP: Incorporating Long Messages into the LogP Model for Parallel Computation},
  journal = {Journal of Parallel and Distributed Computing},
  volume  = {44}, number = {1}, pages = {71--79}, year = {1997},
  doi     = {10.1006/jpdc.1997.1346}
}

@inproceedings{Ino01,
  author    = {Fumihiko Ino and Noriyuki Fujimoto and Kenichi Hagihara},
  title     = {LogGPS: A Parallel Computational Model for Synchronization Analysis},
  booktitle = {Proc. 8th ACM SIGPLAN Symp. on Principles and Practices of Parallel Programming (PPoPP)},
  pages     = {133--142}, year = {2001},
  doi       = {10.1145/379539.379592},
  url       = {http://www-ppl.ist.osaka-u.ac.jp/research/papers/200106_ino_ppopp.pdf}
}

@inproceedings{Hoefler10loggopsim,
  author    = {Torsten Hoefler and Timo Schneider and Andrew Lumsdaine},
  title     = {LogGOPSim -- Simulating Large-Scale Applications in the LogGOPS Model},
  booktitle = {Proc. 19th ACM Int'l Symp. on High Performance Distributed Computing (HPDC), Workshop on
               Large-Scale System and Application Performance},
  pages     = {597--604}, year = {2010},
  doi       = {10.1145/1851476.1851564},
  url       = {https://spcl.inf.ethz.ch/Publications/.pdf/hoefler-loggopsim.pdf}
}

@article{Hoefler10loggpTheory,
  author  = {Torsten Hoefler and Timo Schneider and Andrew Lumsdaine},
  title   = {LogGP in Theory and Practice -- An In-depth Analysis of Modern Interconnection Networks
             and Benchmarking Methods for Collective Operations},
  journal = {Simulation Modelling Practice and Theory},
  volume  = {17}, number = {9}, pages = {1511--1521}, year = {2009},
  doi     = {10.1016/j.simpat.2009.06.007},
  url     = {https://htor.inf.ethz.ch/publications/img/hoefler-elsevier-loggp.pdf}
}

@inproceedings{Kielmann00,
  author    = {Thilo Kielmann and Henri E. Bal and Kees Verstoep},
  title     = {Fast Measurement of LogP Parameters for Message Passing Platforms},
  booktitle = {Proc. 15th IPDPS Workshops, LNCS 1800},
  pages     = {1176--1183}, year = {2000},
  doi       = {10.1007/3-540-45591-4_162},
  note      = {PLogP; see also Kielmann et al., MagPIe, PPoPP'99, doi:10.1145/301104.301116}
}

@article{ThakurRabenseifnerGropp05,
  author  = {Rajeev Thakur and Rolf Rabenseifner and William Gropp},
  title   = {Optimization of Collective Communication Operations in MPICH},
  journal = {International Journal of High Performance Computing Applications (IJHPCA)},
  volume  = {19}, number = {1}, pages = {49--66}, year = {2005},
  doi     = {10.1177/1094342005051521},
  url     = {https://www.cs.umd.edu/class/fall2019/cmsc714/readings/Thakur-MPICH.pdf}
}

@inproceedings{ThakurGropp03,
  author    = {Rajeev Thakur and William D. Gropp},
  title     = {Improving the Performance of Collective Operations in MPICH},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing Interface,
               10th European PVM/MPI Users' Group Meeting, LNCS 2840},
  pages     = {257--267}, year = {2003},
  doi       = {10.1007/978-3-540-45209-6_38},
  url       = {https://wgropp.cs.illinois.edu/bib/papers/pdata/2003/mpicoll-pvmmpi03.pdf}
}

@inproceedings{Rabenseifner04,
  author    = {Rolf Rabenseifner},
  title     = {Optimization of Collective Reduction Operations},
  booktitle = {Computational Science -- ICCS 2004, LNCS 3036},
  pages     = {1--9}, year = {2004},
  doi       = {10.1007/978-3-540-24685-5_1}
}

@inproceedings{Rabenseifner04cug,
  author    = {Rolf Rabenseifner},
  title     = {Collective Reduction Operation on Cray X1 and Other Platforms},
  booktitle = {Proc. Cray User Group (CUG) Conference},
  year      = {2004},
  url       = {https://cug.org/5-publications/proceedings_attendee_lists/2004CD/S04_Proceedings/pages/Authors/Rabenseifner/Rabenseifner.pdf}
}

@article{Bruck97,
  author  = {Jehoshua Bruck and Ching-Tien Ho and Shlomo Kipnis and Eli Upfal and Derrick Weathersby},
  title   = {Efficient Algorithms for All-to-All Communications in Multiport Message-Passing Systems},
  journal = {IEEE Transactions on Parallel and Distributed Systems},
  volume  = {8}, number = {11}, pages = {1143--1156}, year = {1997},
  doi     = {10.1109/71.642949}
}

@inproceedings{Barnett94,
  author    = {M. Barnett and S. Gupta and D. Payne and L. Shuler and R. van de Geijn and J. Watts},
  title     = {Interprocessor Collective Communication Library (InterCom)},
  booktitle = {Proc. Scalable High Performance Computing Conference / Supercomputing '94},
  year      = {1994},
  doi       = {10.1109/SHPCC.1994.296665}
}

@article{Chan07,
  author  = {Ernie W. Chan and Marcel F. Heimlich and Avi Purkayastha and Robert A. van de Geijn},
  title   = {Collective Communication: Theory, Practice, and Experience},
  journal = {Concurrency and Computation: Practice and Experience},
  volume  = {19}, number = {13}, pages = {1749--1783}, year = {2007},
  doi     = {10.1002/cpe.1206},
  url     = {https://www.cs.utexas.edu/~flame/pubs/InterCol_TR.pdf}
}

@article{Sanders09,
  author  = {Peter Sanders and Jochen Speck and Jesper Larsson Tr\"aff},
  title   = {Two-Tree Algorithms for Full Bandwidth Broadcast, Reduction and Scan},
  journal = {Parallel Computing},
  volume  = {35}, number = {12}, pages = {581--594}, year = {2009},
  doi     = {10.1016/j.parco.2009.09.001},
  note    = {Conference version: LNCS 4757, pp. 17--26, 2007, doi:10.1007/978-3-540-75416-9_10}
}

@inproceedings{SandersTraff06,
  author    = {Peter Sanders and Jesper Larsson Tr\"aff},
  title     = {Parallel Prefix (Scan) Algorithms for MPI},
  booktitle = {Recent Advances in PVM and MPI, 13th European PVM/MPI Users' Group Meeting, LNCS 4192},
  pages     = {49--57}, year = {2006},
  doi       = {10.1007/11846802_15}
}

@article{Traff25,
  author  = {Jesper Larsson Tr\"aff},
  title   = {Communication Round and Computation Efficient Exclusive Prefix-Sums Algorithms (for MPI\_Exscan)},
  year    = {2025},
  doi     = {10.34726/10821},
  url     = {https://repositum.tuwien.at/bitstream/20.500.12708/219401/1/Traeff%20Jesper%20Larsson%20-%202025-07-07%20-%20Communication%20Round%20and%20Computation...pdf}
}

@article{JohnssonHo89,
  author  = {S. Lennart Johnsson and Ching-Tien Ho},
  title   = {Optimum Broadcasting and Personalized Communication in Hypercubes},
  journal = {IEEE Transactions on Computers},
  volume  = {38}, number = {9}, pages = {1249--1268}, year = {1989},
  doi     = {10.1109/12.29465}
}

@inproceedings{BruckCypherHo92,
  author    = {Jehoshua Bruck and Robert Cypher and Ching-Tien Ho},
  title     = {Multiple Message Broadcasting with Generalized Fibonacci Trees},
  booktitle = {Proc. 4th IEEE Symp. on Parallel and Distributed Processing},
  pages     = {424--431}, year = {1992},
  doi       = {10.1109/SPDP.1992.242714}
}

@article{Pjesivac07,
  author  = {Jelena Pje\v{s}ivac-Grbovi\'c and Thara Angskun and George Bosilca and Graham E. Fagg and
             Edgar Gabriel and Jack J. Dongarra},
  title   = {Performance Analysis of MPI Collective Operations},
  journal = {Cluster Computing},
  volume  = {10}, number = {2}, pages = {127--143}, year = {2007},
  doi     = {10.1007/s10586-007-0012-0},
  url     = {https://www.netlib.org/utk/people/JackDongarra/PAPERS/coll-perf-analysis-cluster-2005.pdf}
}

@article{Hensgen88,
  author  = {Debra Hensgen and Raphael Finkel and Udi Manber},
  title   = {Two Algorithms for Barrier Synchronization},
  journal = {International Journal of Parallel Programming},
  volume  = {17}, number = {1}, pages = {1--17}, year = {1988},
  doi     = {10.1007/BF01379320},
  url     = {https://homepages.inf.ed.ac.uk/mic/PPLS/BarrierPaper.pdf}
}

@article{Brooks86,
  author  = {Eugene D. Brooks III},
  title   = {The Butterfly Barrier},
  journal = {International Journal of Parallel Programming},
  volume  = {15}, number = {4}, pages = {295--307}, year = {1986},
  doi     = {10.1007/BF01407877}
}

@techreport{ScottMellorCrummey92,
  author      = {Michael L. Scott and John M. Mellor-Crummey},
  title       = {Fast, Contention-Free Combining Tree Barriers},
  institution = {University of Rochester, Dept. of Computer Science},
  number      = {TR-429}, year = {1992},
  url         = {https://www.cs.rochester.edu/u/scott/papers/1992_TR429.pdf}
}

@inproceedings{HoeflerBarrier05,
  author    = {Torsten Hoefler and Torsten Mehlan and Frank Mietke and Wolfgang Rehm},
  title     = {A Practical Approach to the Rating of Barrier Algorithms Using the LogP Model and Open MPI},
  booktitle = {Proc. 34th Int'l Conf. on Parallel Processing Workshops (ICPP Workshops)},
  pages     = {562--569}, year = {2005},
  doi       = {10.1109/ICPPW.2005.14},
  url       = {https://htor.ethz.ch/publications/img/hoefler-icpp05-slides.pdf}
}

@inproceedings{HoeflerIBBarrier,
  author    = {Torsten Hoefler and Torsten Mehlan and Frank Mietke and Wolfgang Rehm},
  title     = {Fast Barrier Synchronization for InfiniBand},
  booktitle = {Proc. 20th IEEE Int'l Parallel and Distributed Processing Symposium (IPDPS)},
  year      = {2006},
  doi       = {10.1109/IPDPS.2006.1639411},
  url       = {https://htor.ethz.ch/publications/img/hoefler-ipdps-ibbarr.pdf}
}

@article{Iannello97,
  author  = {Giulio Iannello},
  title   = {Efficient Algorithms for the Reduce-Scatter Operation in LogGP},
  journal = {IEEE Transactions on Parallel and Distributed Systems},
  volume  = {8}, number = {9}, pages = {970--982}, year = {1997},
  doi     = {10.1109/71.615442}
}

@inproceedings{Hoefler07nbc,
  author    = {Torsten Hoefler and Andrew Lumsdaine and Wolfgang Rehm},
  title     = {Implementation and Performance Analysis of Non-Blocking Collective Operations for MPI},
  booktitle = {Proc. 2007 ACM/IEEE Conference on Supercomputing (SC'07)},
  year      = {2007},
  doi       = {10.1145/1362622.1362692},
  url       = {https://htor.inf.ethz.ch/publications/img/hoefler-sc07.pdf}
}

@techreport{Hoefler07nbcdesign,
  author      = {Torsten Hoefler and Andrew Lumsdaine},
  title       = {Design, Implementation, and Usage of LibNBC},
  institution = {Open Systems Lab, Indiana University},
  year        = {2006},
  url         = {https://spcl.inf.ethz.ch/Publications/.pdf/hoefler-libnbc-design.pdf}
}

@inproceedings{Hoefler07NBCcase,
  author    = {Torsten Hoefler and Prabhanjan Kambadur and Richard L. Graham and Galen Shipman and
               Andrew Lumsdaine},
  title     = {A Case for Standard Non-Blocking Collective Operations},
  booktitle = {Recent Advances in PVM and MPI, 14th European PVM/MPI Users' Group Meeting, LNCS 4757},
  year      = {2007},
  doi       = {10.1007/978-3-540-75416-9_17},
  url       = {https://www.open-mpi.org/papers/euro-pvmmpi-2007-nb-coll/mpi-vs-nbc.pdf}
}

@misc{MprobeProposal,
  author = {Douglas Gregor and Torsten Hoefler and Brian Barrett and Andrew Lumsdaine},
  title  = {Fixing Probe for Multi-Threaded MPI Applications (MPI Forum ticket \#38, rev. 4)},
  year   = {2009},
  url    = {http://unixer.de/publications/img/mprobe-proposal-rev4.pdf}
}

@inproceedings{Hoefler10hybrid,
  author    = {Torsten Hoefler and Greg Bronevetsky and Brian Barrett and Bronis R. de Supinski and
               Andrew Lumsdaine},
  title     = {Efficient MPI Support for Advanced Hybrid Programming Models},
  booktitle = {Recent Advances in the Message Passing Interface (EuroMPI'10), LNCS 6305},
  pages     = {50--61}, year = {2010},
  doi       = {10.1007/978-3-642-15646-5_6}
}

@misc{Hoefler-MPI3-overview,
  author = {Torsten Hoefler},
  title  = {New and Old Features in MPI-3.0: The Past, the Standard, and the Future},
  year   = {2012},
  url    = {https://htor.inf.ethz.ch/publications/img/hoefler-mpi-3.0-overview.pdf}
}

@manual{MPI41,
  title        = {MPI: A Message-Passing Interface Standard, Version 4.1},
  organization = {Message Passing Interface Forum},
  year         = {2023},
  url          = {https://www.mpi-forum.org/docs/mpi-4.1/mpi41-report.pdf},
  note         = {Ordering/progress: node68; MPI\_REDUCE: node130}
}

@manual{MPI50,
  title        = {MPI: A Message-Passing Interface Standard, Version 5.0},
  organization = {Message Passing Interface Forum},
  year         = {2025},
  url          = {https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report/},
  note         = {MPI\_REDUCE: node132; Probe: node81; Partitioned semantics: node89;
                  Dist graph constructor: node229}
}

@manual{MPI-datatypes,
  title        = {MPI Standard, Derived Datatypes (typemap and type signature)},
  organization = {Message Passing Interface Forum},
  year         = {1995--2023},
  url          = {https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report/node77.htm}
}

@manual{MPI-modes,
  title        = {MPI Standard, Communication Modes (rationale for standard/buffered/synchronous/ready sends)},
  organization = {Message Passing Interface Forum},
  url          = {https://www.mpi-forum.org/docs/mpi-2.2/mpi22-report/node53.htm}
}

@manual{MPI22-order,
  title        = {MPI Standard, Semantics of Point-to-Point Communication (Order, Progress)},
  organization = {Message Passing Interface Forum},
  url          = {https://www.mpi-forum.org/docs/mpi-2.2/mpi22-report/node54.htm}
}

@manual{MPI-nonblocking-semantics,
  title        = {MPI Standard, Semantics of Nonblocking Communications},
  organization = {Message Passing Interface Forum},
  url          = {https://netlib.org/mpi/mpi-report/node48.html}
}

@manual{MPI31-mprobe,
  title        = {MPI-3.1 Standard, Matching Probe},
  organization = {Message Passing Interface Forum},
  url          = {https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report/node70.htm}
}

@manual{MPI50-probe,
  title        = {MPI-5.0 Standard, Probe (advice to users on threaded probe)},
  organization = {Message Passing Interface Forum},
  url          = {https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report/node81.htm}
}

@manual{MPI50-partitioned,
  title        = {MPI-5.0 Standard, Semantics of Partitioned Point-to-Point Communication},
  organization = {Message Passing Interface Forum},
  url          = {https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report/node89.htm}
}

@manual{MPI50-distgraph,
  title        = {MPI-5.0 Standard, Distributed Graph Constructor},
  organization = {Message Passing Interface Forum},
  url          = {https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report/node229.htm}
}

@inproceedings{Balaji13,
  author    = {Pavan Balaji and Dries Kimpe},
  title     = {On the Reproducibility of MPI Reduction Operations},
  booktitle = {Proc. 15th IEEE Int'l Conf. on High Performance Computing and Communications (HPCC)},
  pages     = {407--414}, year = {2013},
  doi       = {10.1109/HPCC.and.EUC.2013.62},
  url       = {https://pavanbalaji.github.io/pubs/2013/hpcc/hpcc13.ndreduce.pdf}
}

@article{Ahrens20,
  author  = {Peter Ahrens and James Demmel and Hong Diep Nguyen},
  title   = {Algorithms for Efficient Reproducible Floating Point Summation},
  journal = {ACM Transactions on Mathematical Software},
  volume  = {46}, number = {3}, pages = {22:1--22:49}, year = {2020},
  doi     = {10.1145/3389360},
  url     = {https://people.eecs.berkeley.edu/~demmel/ma221_Fall23/J115_Efficient_Reproducible_Summation_TOMS_2020.pdf}
}

@techreport{Demmel15,
  author      = {Peter Ahrens and Hong Diep Nguyen and James Demmel},
  title       = {Efficient Reproducible Floating Point Summation and BLAS},
  institution = {EECS Department, University of California, Berkeley},
  number      = {UCB/EECS-2015-229}, year = {2015},
  url         = {https://www2.eecs.berkeley.edu/Pubs/TechRpts/2015/Archive/EECS-2015-229.pdf}
}

@misc{ReproBLAS,
  author = {James Demmel and Hong Diep Nguyen and Peter Ahrens},
  title  = {ReproBLAS: Reproducible Basic Linear Algebra Sub-programs},
  url    = {https://bebop.cs.berkeley.edu/reproblas/}
}

@misc{IntelCNR,
  author       = {{Intel Corporation}},
  title        = {oneAPI Math Kernel Library Developer Guide: Obtaining Numerically Reproducible
                  Results / Reproducibility Conditions (Conditional Numerical Reproducibility)},
  year         = {2025},
  url          = {https://www.intel.com/content/www/us/en/docs/onemkl/developer-guide-linux/2025-2/reproducibility-conditions.html}
}

@inproceedings{Pollard20,
  author    = {Samuel D. Pollard and Boyana Norris and others},
  title     = {Evaluation of Floating-Point Reproducibility and Correctness in MPI Reductions},
  year      = {2020},
  url       = {https://sampollard.github.io/research/artifacts/pollard_correctness20.pdf},
  note      = {Full author list and venue [UNVERIFIED]; defines RORA/ROLA/canonical order taxonomy}
}

@inproceedings{Traff99,
  author    = {Jesper Larsson Tr\"aff and Rolf Hempel and Hubert Ritzdorf and Falk Zimmermann},
  title     = {Flattening on the Fly: Efficient Handling of MPI Derived Datatypes},
  booktitle = {Recent Advances in PVM and MPI, 6th European PVM/MPI Users' Group Meeting, LNCS 1697},
  pages     = {109--116}, year = {1999},
  doi       = {10.1007/3-540-48158-3_14}
}

@inproceedings{Gropp99,
  author    = {William Gropp and Ewing Lusk and Deborah Swider},
  title     = {Improving the Performance of MPI Derived Datatypes},
  booktitle = {Proc. Third MPI Developer's and User's Conference (MPIDC'99)},
  pages     = {25--30}, year = {1999},
  note      = {Taxonomy of derived-datatype memory-access patterns; stack-based (dataloop) processing.
               Page numbers [UNVERIFIED]}
}

@inproceedings{Byna03,
  author    = {Surendra Byna and William Gropp and Xian-He Sun and Rajeev Thakur},
  title     = {Improving the Performance of MPI Derived Datatypes by Optimizing Memory-Access Cost},
  booktitle = {Proc. IEEE Int'l Conf. on Cluster Computing},
  year      = {2003},
  doi       = {10.1109/CLUSTR.2003.1253336},
  url       = {http://cs.iit.edu/~scs/assets/files/135_byna_s.pdf},
  note      = {Cited in text as "memory conscious implementation of MPI derived datatypes"}
}

@inproceedings{PrabhuGropp15,
  author    = {Tarun Prabhu and William Gropp},
  title     = {DAME: A Runtime-Compiled Engine for Derived Datatypes},
  booktitle = {Proc. 22nd European MPI Users' Group Meeting (EuroMPI'15)},
  year      = {2015},
  doi       = {10.1145/2802658.2802659},
  note      = {Details [UNVERIFIED]; identified via secondary source DatatypeStudy25}
}

@inproceedings{Reussner00,
  author    = {Ralf Reussner and Jesper Larsson Tr\"aff and Gunnar Hunzelmann},
  title     = {A Benchmark for MPI Derived Datatypes},
  booktitle = {Recent Advances in PVM and MPI, 7th European PVM/MPI Users' Group Meeting, LNCS 1908},
  pages     = {10--17}, year = {2000},
  doi       = {10.1007/3-540-45255-9_4},
  note      = {SKaMPI datatype benchmarks; up to 16x penalty on Cray T3E vs 2--4x on NEC SX-5}
}

@misc{DatatypeStudy25,
  title  = {Do MPI Derived Datatypes Actually Help? A Single-Node Cross-Implementation Study on
            Shared-Memory Communication},
  year   = {2025},
  url    = {https://arxiv.org/html/2511.13804v1},
  note   = {Authors [UNVERIFIED]; used here as a secondary source for the history of datatype-engine work}
}

@inproceedings{Vo09,
  author    = {Anh Vo and Sarvani Vakkalanka and Michael DeLisi and Ganesh Gopalakrishnan and
               Robert M. Kirby and Rajeev Thakur},
  title     = {Formal Verification of Practical MPI Programs},
  booktitle = {Proc. 14th ACM SIGPLAN Symp. on Principles and Practice of Parallel Programming (PPoPP)},
  pages     = {261--270}, year = {2009},
  doi       = {10.1145/1504176.1504214},
  url       = {https://www.cs.rice.edu/~vs3/PDF/ppopp.09/p261-voA.pdf}
}

@inproceedings{Vakkalanka08,
  author    = {Sarvani Vakkalanka and Michael DeLisi and Ganesh Gopalakrishnan and Robert M. Kirby and
               Rajeev Thakur and William Gropp},
  title     = {Implementing Efficient Dynamic Formal Verification Methods for MPI Programs},
  booktitle = {Recent Advances in PVM and MPI, LNCS 5205},
  pages     = {248--256}, year = {2008},
  doi       = {10.1007/978-3-540-87475-1_34}
}

@inproceedings{Vo10,
  author    = {Anh Vo and Sriram Aananthakrishnan and Ganesh Gopalakrishnan and Bronis R. de Supinski and
               Martin Schulz and Greg Bronevetsky},
  title     = {A Scalable and Distributed Dynamic Formal Verifier for MPI Programs},
  booktitle = {Proc. 2010 ACM/IEEE Int'l Conf. for High Performance Computing, Networking, Storage and
               Analysis (SC'10)},
  year      = {2010},
  doi       = {10.1109/SC.2010.7},
  note      = {DAMPI. Exact author list [UNVERIFIED]}
}

@article{Hilbrich13,
  author  = {Tobias Hilbrich and Joachim Protze and Martin Schulz and Bronis R. de Supinski and
             Matthias S. M\"uller},
  title   = {MPI Runtime Error Detection with MUST: Advances in Deadlock Detection},
  journal = {Scientific Programming},
  volume  = {21}, number = {3-4}, pages = {109--121}, year = {2013},
  doi     = {10.1155/2013/314971}
}

@inproceedings{SharmaFM18,
  author    = {Dhriti Khanna and Subodh Sharma and C\'esar Rodr\'iguez and Rahul Purandare},
  title     = {Dynamic Symbolic Verification of MPI Programs},
  booktitle = {Formal Methods (FM 2018), LNCS 10951},
  year      = {2018},
  doi       = {10.1007/978-3-319-95582-7_11},
  url       = {https://subodhvsharma.github.io/publication/fm18/fm18.pdf},
  note      = {Source for the CIVL characterization; author list [UNVERIFIED]}
}

@inproceedings{Hoefler11mpi22topo,
  author    = {Torsten Hoefler and Rolf Rabenseifner and Hubert Ritzdorf and Bronis R. de Supinski and
               Rajeev Thakur and Jesper Larsson Tr\"aff},
  title     = {The Scalable Process Topology Interface of MPI 2.2},
  journal   = {Concurrency and Computation: Practice and Experience},
  volume    = {23}, number = {4}, pages = {293--310}, year = {2011},
  doi       = {10.1002/cpe.1643},
  url       = {https://spcl.inf.ethz.ch/Publications/.pdf/hoefler-process_topology_mpi22.pdf}
}

@inproceedings{Hoefler11generic,
  author    = {Torsten Hoefler and Marc Snir},
  title     = {Generic Topology Mapping Strategies for Large-Scale Parallel Architectures},
  booktitle = {Proc. Int'l Conf. on Supercomputing (ICS'11)},
  pages     = {75--84}, year = {2011},
  doi       = {10.1145/1995896.1995909},
  url       = {https://snir.cs.illinois.edu/listed/C74.pdf}
}

@article{Almasi05jrd,
  author  = {G. Alm\'asi and C. Archer and J. G. Casta\~nos and others},
  title   = {Design and Implementation of Message-Passing Services for the Blue Gene/L Supercomputer},
  journal = {IBM Journal of Research and Development},
  volume  = {49}, number = {2/3}, pages = {393--406}, year = {2005},
  doi     = {10.1147/rd.492.0393},
  url     = {https://www.mirrorservice.org/sites/www.bitsavers.org/pdf/ibm/IBM_Journal_of_Research_and_Development/492/almasi.pdf}
}

@inproceedings{Almasi05ics,
  author    = {George Alm\'asi and Philip Heidelberger and Charles J. Archer and Xavier Martorell and
               C. Chris Erway and Jos\'e E. Moreira and B. Steinmacher-Burow and Yili Zheng},
  title     = {Optimization of MPI Collective Communication on BlueGene/L Systems},
  booktitle = {Proc. 19th Annual Int'l Conf. on Supercomputing (ICS'05)},
  pages     = {253--262}, year = {2005},
  doi       = {10.1145/1088149.1088183}
}

@article{Hoefler07BGL,
  author  = {Torsten Hoefler and Wolfgang Rehm and others},
  title   = {Performance Measurements and Analysis of the BlueGene/L MPI Implementation},
  journal = {Parallel Computing},
  year    = {2007},
  url     = {https://www.tu-chemnitz.de/informatik/PI/forschung/publikationen/download/HR_parco07.pdf},
  note    = {Full author list and volume/pages [UNVERIFIED]}
}

@inproceedings{Kumar09,
  author    = {Sameer Kumar and Gabor Dozsa and Jeremy Berg and Bob Cernohous and Douglas Miller and
               Joseph Ratterman and Brian Smith and Philip Heidelberger},
  title     = {MPI Collective Communications on the Blue Gene/P Supercomputer: Algorithms and Optimizations},
  booktitle = {Proc. 17th IEEE Symp. on High Performance Interconnects (HOTI'09)},
  pages     = {63--72}, year = {2009},
  doi       = {10.1109/HOTI.2009.12}
}

@inproceedings{CrayXC18,
  author    = {{Cray User Group}},
  title     = {Performance Evaluation of MPI on Cray XC40 Xeon Phi Systems},
  booktitle = {Proc. Cray User Group (CUG) 2018},
  year      = {2018},
  url       = {https://cug.org/proceedings/cug2018_proceedings/includes/files/pap131s2-file1.pdf},
  note      = {Authors [UNVERIFIED]; source for DMAPP collective-engine offload
               (MPICH\_USE\_DMAPP\_COLL=1)}
}

@misc{NCCL24,
  author = {Sylvain Jeaugey},
  title  = {Massively Scale Your Deep Learning Training with NCCL 2.4},
  year   = {2019},
  url    = {https://developer.nvidia.com/blog/massively-scale-deep-learning-training-nccl-2-4/},
  note   = {Double binary trees; up to 180x latency improvement over ring at 24,576 GPUs on Summit;
            explicitly credits Sanders/Speck/Traff 2009}
}

@misc{SHARP,
  author = {{NVIDIA Corporation}},
  title  = {NVIDIA Scalable Hierarchical Aggregation and Reduction Protocol (SHARP) Documentation},
  year   = {2025},
  url    = {https://docs.nvidia.com/nvidia-scalable-hierarchical-aggregation-and-reduction-protocol-rev-3-5-2-lts.pdf},
  note   = {See also Graham et al., "Scalable Hierarchical Aggregation Protocol (SHArP)",
            COM-HPC 2016 [UNVERIFIED DOI]}
}

@misc{SHARPkb,
  title = {SHARP In-Network Reduction},
  year  = {2025},
  url   = {https://ai-infrastructure.net/sharp-in-network-reduction/},
  note  = {Secondary source [UNVERIFIED] for per-endpoint byte-count reductions and scale thresholds}
}

@misc{NCCLkb,
  title = {NVIDIA NCCL -- Multi-GPU Collective Communication},
  year  = {2025},
  url   = {https://yobitel.com/knowledge-base/nccl},
  note  = {Secondary source [UNVERIFIED]; used only for qualitative regime statements}
}

@misc{OpenMPItunedDoc,
  author = {{Open MPI Community}},
  title  = {Tuning Collectives: the {\tt tuned} Component (fixed, forced, and dynamic decision modes)},
  year   = {2025},
  url    = {https://docs.open-mpi.org/en/v5.0.10rc1/tuning-apps/coll-tuned.html}
}

@misc{OpenMPItunedSrc,
  author = {{Open MPI Community}},
  title  = {{\tt ompi/mca/coll/tuned/coll\_tuned\_decision\_fixed.c}},
  url    = {https://github.com/open-mpi/ompi/blob/main/ompi/mca/coll/tuned/coll_tuned_decision_fixed.c}
}

@misc{MPICH-AR-src,
  author = {{MPICH Developers}},
  title  = {{\tt src/mpi/coll/allreduce/allreduce\_intra\_recursive\_doubling.c}},
  url    = {https://github.com/pmodels/mpich/blob/main/src/mpi/coll/allreduce/allreduce_intra_recursive_doubling.c},
  note   = {Canonical non-power-of-two prologue/epilogue}
}

@inproceedings{Barrett13,
  author    = {Brian W. Barrett and K. Scott Hemmert and Keith D. Underwood},
  title     = {Reducing MPI Memory Usage in Exascale Networks},
  booktitle = {Sandia National Laboratories / OSTI technical publication},
  year      = {2013},
  url       = {https://www.osti.gov/servlets/purl/1109242},
  note      = {Venue [UNVERIFIED]; source for the bandwidth-delay-product argument on eager buffering}
}

@misc{rdma-core,
  author = {{linux-rdma community}},
  title  = {{\tt Documentation/tag\_matching.md}: MPI Tag Matching, Eager and Rendezvous Protocols},
  url    = {https://github.com/linux-rdma/rdma-core/blob/master/Documentation/tag_matching.md}
}

@misc{INFO0939,
  author = {Christophe Geuzaine},
  title  = {INFO 0939 High Performance Scientific Computing: Point-to-Point Communication},
  year   = {2025},
  url    = {https://people.montefiore.uliege.be/geuzaine/INFO0939/mpi/pointtopoint/},
  note   = {Course notes; used for the RTS/CTS description and the size-dependent Send/Send deadlock}
}

@misc{SciNetTuning,
  author = {{SciNet, University of Toronto}},
  title  = {Tuning Your MPI Application Without Writing Code},
  url    = {https://oldwiki.scinet.utoronto.ca/images/f/f5/Mpi-tuning-parameters.pdf},
  note   = {Source for the eager-threshold default table; values are version-dependent [UNVERIFIED]}
}

@misc{StackOverflowEager,
  title = {Force MPI\_Send to use eager or rendezvous protocol},
  url   = {https://stackoverflow.com/questions/27511961/force-mpi-send-to-use-eager-or-rendezvouz-protocol},
  note  = {Source for the per-implementation eager-limit environment-variable names}
}

@inproceedings{PartitionedICPP22,
  author    = {Yiltan Hassan Temu\c{c}in and Ryan E. Grant and Ahmad Afsahi},
  title     = {Micro-Benchmarking MPI Partitioned Point-to-Point Communication},
  booktitle = {Proc. 51st Int'l Conf. on Parallel Processing (ICPP'22)},
  year      = {2022},
  doi       = {10.1145/3545008.3545088},
  note      = {Author list [UNVERIFIED]}
}

@misc{NVIDIA-mpi-acx,
  author = {{NVIDIA Corporation}},
  title  = {mpi-acx: Prototype GPU-Initiated MPI Accelerator Extensions},
  url    = {https://github.com/nvidia/mpi-acx},
  note   = {Source for the MPIX\_Pready/MPIX\_Parrived intra-kernel deadlock caveat}
}

@inproceedings{HAN21,
  author    = {Xi Luo and Wei Wu and George Bosilca and Yu Pei and Qinglei Cao and
               Thananon Patinyasakdikul and Dong Zhong and Jack Dongarra},
  title     = {HAN: A Hierarchical AutotuNed Collective Communication Framework},
  booktitle = {Proc. IEEE Int'l Conf. on Cluster Computing (CLUSTER)},
  year      = {2020},
  doi       = {10.1109/CLUSTER49012.2020.00013},
  url       = {https://par.nsf.gov/servlets/purl/10303995},
  note      = {Author list and year [UNVERIFIED]}
}

@inproceedings{HierKNEM,
  author    = {Teng Ma and George Bosilca and Aurelien Bouteiller and Jack J. Dongarra},
  title     = {HierKNEM: An Adaptive Framework for Kernel-Assisted and Topology-Aware Collective
               Communications on Many-core Clusters},
  booktitle = {Proc. IEEE 26th Int'l Parallel and Distributed Processing Symposium (IPDPS)},
  year      = {2012},
  doi       = {10.1109/IPDPS.2012.98},
  url       = {https://www.netlib.org/utk/people/JackDongarra/PAPERS/hierknem.pdf}
}

@misc{ScanFPGA,
  title = {Offloading MPI Parallel Prefix Scan (MPI\_Scan) with the NetFPGA},
  year  = {2014},
  url   = {https://arxiv.org/pdf/1408.4939},
  note  = {Authors [UNVERIFIED]; used for the explicit step-index formulations of the recursive-doubling
           and binomial-tree (up-phase/down-phase) scan algorithms}
}

@misc{PCCL25,
  title = {The Big Send-off: Scalable and Performant Collectives for Deep Learning},
  year  = {2025},
  url   = {https://arxiv.org/html/2504.18658v2},
  note  = {Authors [UNVERIFIED]; corroborates recursive halving/doubling vs ring modelling for
           GPU-cluster collectives}
}
```
