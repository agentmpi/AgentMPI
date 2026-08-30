# 02a — MPI Collective Communication Algorithms and Their Published Cost Formulas

**Purpose.** Reference notes for adapting MPI collective algorithms to an LLM multi-agent
message-passing harness ("AgentMPI"). Each section gives the *mechanics* (who talks to whom, in
what round) and the *published cost expression* in the standard α/β/γ model.

## The cost model

The Hockney-style model used throughout the MPICH collectives literature
[thakur2005optimization]:

- **α** — latency (startup) cost per message, independent of size.
- **β** — transfer cost per byte, so a message of `n` bytes costs `α + nβ`.
- **γ** — computation cost per byte for performing the reduction operation locally.
- **P** — number of processes; `n` — message size in bytes (for reductions, the size of the
  vector being reduced).

Assumptions carried by this model in [thakur2005optimization]: a process can send *one* message
and receive *one* message at the same time (bidirectional link), the network is fully connected
(any pair can communicate at cost α + nβ), and there is no congestion/topology term. Chan et al.
[chan2007collective] use the same α/β/γ notation and additionally distinguish
*one-port* vs *bidirectional* communication assumptions and give lower bounds per collective.

Two important structural facts that recur below [chan2007collective]:

- **Latency lower bound** for any collective requiring global data dependence is `⌈log₂ P⌉ α`
  (information must reach P processes, and the number of informed processes can at most double
  per round).
- **Bandwidth lower bound** for reduce-scatter/allgather-shaped collectives is
  `((P−1)/P) n β`, and for allreduce (= reduce-scatter + allgather) it is `2((P−1)/P) n β` with
  a compute lower bound `((P−1)/P) n γ`. Algorithms that achieve both the log-P latency term and
  the (P−1)/P bandwidth term are called *bandwidth-optimal*.

The recurring tension: **latency-optimal** algorithms (log P rounds, but each round moves ~n
bytes, giving `log P · n β`) win for short messages; **bandwidth-optimal** algorithms (more
rounds, but total bytes ~ 2n(P−1)/P) win for long messages. Every section below is an instance
of this trade-off.

MPICH's guiding design principle, stated explicitly in [thakur2005optimization]: *"For each
collective operation, we use multiple algorithms based on message size: The short-message
algorithms aim to minimize latency, and the long-message algorithms aim to minimize bandwidth
use. We use experimentally determined cutoff points to switch between different algorithms
depending on the message size and number of processes."*

### The refined two-sided model (needed for Rabenseifner's non-power-of-two analysis)

For the deeper reduce/allreduce analysis in §5 of [thakur2005optimization], the model is
refined because *"many networks are faster if pairs of processes exchange data with each other,
rather than if a process sends to and receives from different processes"*:

- `α + nβ` — time for **bidirectional** (pairwise exchange) communication between a pair.
- `α_uni + n β_uni` — time for **unidirectional** communication from one process to another.
- `f_α = α_uni/α`, `f_β = β_uni/β`, *"normally in the range 0.5 (simplex network) to 1.0
  (full-duplex network)"* [thakur2005optimization].

This refinement is what produces the `(1+f_α)`, `(1+f_β)` factors in the non-power-of-two cost
expressions in §3 below. **For AgentMPI this distinction is likely to matter more than it does
in MPI**, because an agent that must both send and receive in the same round pays a real
serialization cost (it cannot generate two messages concurrently), i.e. f_α ≈ 1 but the *round*
is effectively twice as long.

---

## 1. Barrier

Barrier moves no payload (n = 0), so cost is purely `(#rounds) × α` plus whatever
serialization the pattern imposes. It is the purest test of the latency term, and therefore the
collective whose ranking transfers most directly to an agent setting.

### 1.1 Linear / centralized ("flat tree") barrier

All P−1 non-root processes send a zero-byte "arrived" message to a designated root; the root
waits for all P−1, then sends P−1 release messages. Total messages `2(P−1)`.

- If the root can only handle one message at a time (single-ported network interface, the
  assumption in [thakur2005optimization]), the root is the bottleneck and cost is
  **`T_linear = 2(P−1) α`**.
- If sends are non-blocking and the network is fully parallel, the depth is 2 rounds but the
  root still serializes P−1 receives, so the `2(P−1)α` term is the realistic one.

The flat tree is a *fan-in + fan-out* of depth 2. It is only competitive for very small P.

### 1.2 Binomial / tree barrier

Fan-in over a binomial (or k-ary) tree to the root, then fan-out. Each phase takes `⌈log₂ P⌉`
rounds, so:

**`T_binomial_barrier = 2 ⌈log₂ P⌉ α`**

For a k-ary tree the depth is `⌈log_k P⌉` but each internal node serializes k−1 messages per
phase, giving roughly `2 ⌈log_k P⌉ (k−1) α` — the classic k-ary trade-off, minimized near k = 2
for pure latency and at larger k when per-node message handling is cheap relative to depth
[chan2007collective].

### 1.3 Tournament barrier (Hensgen, Finkel & Manber 1988)

Both the dissemination and the tournament barrier were introduced in the same paper
[hensgen1988two], as improvements on Brooks' butterfly barrier [brooks1986butterfly]:

> "We describe two new algorithms for implementing barrier synchronization on a shared-memory
> multicomputer. Both algorithms are based on a method due to Brooks. We first improve Brooks'
> algorithm by introducing double buffering. Our dissemination algorithm replaces Brooks'
> communication pattern with an information dissemination algorithm described by Han and Finkel.
> Our tournament algorithm uses a different communication pattern and generally requires fewer
> total instructions. The resulting algorithms improve Brooks' original barrier by a factor of
> two when the number of processes is a power of two. When the number of processes is not a
> power of two, these algorithms improve even more upon Brooks' algorithm because absent
> processes need not be simulated." [hensgen1988two]

Mechanics: processes play a tournament in `⌈log₂ P⌉` rounds. In each round one of each pair is
the *statically predetermined* winner — HFM note this asymmetry, and knowing the winner in
advance, is what makes each game "a fairly simple two-instruction game" (loser sets a flag,
winner reads it). The loser then drops out and spins waiting for release. HFM state: *"There
are ⌈log₂ n⌉ rounds, as in the dissemination algorithm."* In HFM's shared-memory formulation,
the champion releases everyone by setting a single global `SetByChampion` flag that all losers
are already spinning on, so the wake-up costs O(1) *rounds*.

- **Shared-memory (HFM original):** `⌈log₂ P⌉` rounds up + constant-time release.
- **Message-passing:** the release must itself be a broadcast; the standard fix (Mellor-Crummey
  & Scott's wake-up tree [mellorcrummey1991algorithms]) gives
  **`T_tournament = 2 ⌈log₂ P⌉ α`**.

**The key HFM result, and the one most relevant to AgentMPI:** dissemination and tournament
have the *same critical path* (`⌈log₂ P⌉` rounds), but very different *total work*. HFM count
`n⌈log₂ n⌉` synchronization variables and O(n log n) total instructions for dissemination
versus `n−1` games and O(n) total instructions for the tournament. On the bus-based Sequent
Balance 21000, where total traffic — not critical path — is the bottleneck, they report:
*"When the number of processes is less than 16, the dissemination algorithm is competitive with
the tournament algorithm... However, the difference between the number of writes in the
dissemination algorithm (2n⌈log₂ n⌉) and the tournament algorithm (n−1) becomes more
significant for larger n."* [hensgen1988two] So the tournament wins beyond ~16 processes on a
shared/contended medium, and dissemination wins on a network with genuine parallelism. This is
a *total-work vs. critical-path* inversion driven purely by whether the medium is contended —
see §9.

### 1.4 Dissemination barrier (Hensgen, Finkel & Manber 1988)

The latency-optimal barrier for arbitrary P, and the one [thakur2005optimization] singles out.
Quoting the paper directly:

> "In the dissemination algorithm for barrier, in each step k (0 ≤ k < ⌈lg p⌉), process i sends
> a (zero-byte) message to process (i + 2^k) and receives a (zero-byte) message from process
> (i − 2^k) (with wrap-around)." [thakur2005optimization]

And crucially: *"Both algorithms take ⌈lg p⌉ steps in all cases, even for non-power-of-two
numbers of processes."* [thakur2005optimization] (the "both" refers to dissemination barrier
and Bruck allgather, of which the former is the pattern the latter is derived from).

**`T_dissemination = ⌈log₂ P⌉ α`**

Why it works for non-power-of-two P: after step k, each process has (transitively) heard from a
contiguous wrap-around block of `min(2^(k+1), P)` processes. After `⌈log₂ P⌉` steps the block
covers all P. There is no "reduce to nearest power of two, then fix up" phase — this is the
distinguishing property, and the reason dissemination is the *only* barrier algorithm in this
list with no non-power-of-two penalty. Each process sends exactly one and receives exactly one
message per round (asymmetric partners: sends to i+2^k, receives from i−2^k).

Note the direction convention: dissemination sends *forward* (`i + 2^k`); Bruck allgather
reverses this to `i − 2^k` specifically to keep the concatenated data contiguous
[thakur2005optimization] (see §5.3).

### 1.5 Butterfly barrier (pairwise exchange / recursive doubling)

In round k (0 ≤ k < log₂ P), process i exchanges a zero-byte message with process `i XOR 2^k`.
Symmetric, pairwise, and after log₂ P rounds every process has transitively synchronized with
every other.

**`T_butterfly = log₂ P α`** (for P a power of two)

This is the pattern Brooks called the "butterfly barrier" [brooks1986butterfly]. Because it is
built from *pairwise exchanges* rather than asymmetric send/receive pairs, it maps well onto
networks where a pair exchange costs the same as a one-way send (`f_α = 1`) — exactly the
distinction [thakur2005optimization] introduces in its refined model.

**Non-power-of-two P** is the butterfly's weakness: the standard fix reduces to `P' = 2^⌊lg P⌋`
by having `r = P − P'` extra processes pair off with the first r processes, adding 2 extra
rounds:

**`T_butterfly, P≠2^k = (⌊log₂ P⌋ + 2) α`** [UNVERIFIED — this is the standard construction by
analogy with the recursive-halving fix-up in [thakur2005optimization] §4.4, not a formula
quoted from a specific paper.]

### 1.6 Barrier summary

| Algorithm | Rounds | Cost | Non-2^k? | Notes |
|---|---|---|---|---|
| Linear / flat tree | 2 (depth) | `2(P−1) α` | yes | root serializes |
| Binomial tree | `2⌈lg P⌉` | `2⌈lg P⌉ α` | yes | fan-in + fan-out |
| Tournament | `2⌈lg P⌉` | `2⌈lg P⌉ α` | yes | one-way msgs only |
| Dissemination | `⌈lg P⌉` | `⌈lg P⌉ α` | **yes, no penalty** | latency-optimal ∀P |
| Butterfly (rec. doubling) | `lg P` | `lg P α` | needs fix-up | pairwise exchange |

**Winner:** dissemination for all P; butterfly ties it for power-of-two P and is preferable when
pairwise exchange is cheaper than asymmetric send/recv. The `⌈log₂ P⌉ α` figure is the latency
lower bound for any barrier [chan2007collective], so dissemination and butterfly are optimal.

---

## 2. Broadcast

Lower bounds [chan2007collective, Table 1]: latency `⌈log₂ P⌉ α`, bandwidth `n β` (the root must
send at least n items). Note the bandwidth bound for broadcast is `nβ`, **not** `2(P−1)/P nβ` —
so the scatter+allgather algorithm below is only within a factor of ~2 of optimal, not optimal.

### 2.1 Linear (flat) broadcast

Root sends n bytes to each of the P−1 others in turn. **`T_linear = (P−1)(α + nβ)`**. Only
sensible for tiny P, or when the root's outbound sends genuinely overlap (in which case the
depth is 1 but the root serializes P−1 message injections).

### 2.2 Binomial (minimum-spanning) tree broadcast

The classic. Quoting [thakur2005optimization] on MPICH's original algorithm:

> "In the first step, the root sends data to process (root + p/2). This process and the root
> then act as new roots within their own subtrees and recursively continue this algorithm. This
> communication takes a total of ⌈lg p⌉ steps. The amount of data communicated by a process at
> any step is n."

**`T_tree = ⌈log₂ P⌉ (α + nβ)`** [thakur2005optimization]

Chan et al. call this MSTBcast ("minimum-spanning tree broadcast") and give the identical cost
**`T_MSTBcast(p,n) = ⌈log₂ p⌉ (α + nβ)`**, noting it *"achieve[s] the lower bound of ⌈log(p)⌉ α
for the latency component"* [chan2007collective]. Its dual, obtained by reversing every
communication and adding a reduction on receipt, is MSTReduce with
`T_MSTReduce(p,n) = ⌈log₂ p⌉ (α + nβ + nγ)` — see §3.1.

The weakness is the bandwidth term: `⌈log₂ P⌉ n β`, a factor of `log₂ P` above the `nβ` lower
bound. Every long-message broadcast algorithm exists to fix this.

**Regime:** short messages. MPICH uses binomial tree *"for short messages (< 12 KB) or when the
number of processes is less than 8"* [thakur2005optimization].

### 2.3 Pipelined / chain (linear pipeline) broadcast

Arrange the P processes in a chain `0 → 1 → … → P−1` and split the n-byte message into `n_s`
segments of size `n/n_s`. Each process forwards each segment as soon as it arrives. The chain
has depth P−1 and there are `n_s` segments, so the schedule completes in `P + n_s − 2` steps:

**`T_chain(P, n, n_s) = (P + n_s − 2) (α + (n/n_s) β)`** [nuriyev2020accurate]

(This is the form used to model Open MPI's segmented chain-tree broadcast
[nuriyev2020accurate]; the underlying pipelined-broadcast idea is much older and is attributed
to Barnett/van de Geijn-era work on the Intel Delta/Paragon [chan2007collective].)

Minimizing over `n_s` (treating it as continuous) gives the standard pipelining result: with
`n_s* = √(nβ(P−2)/α)`,

**`T_chain* ≈ nβ + (P−2)α + 2√((P−2) α n β)`** [UNVERIFIED — this is the standard closed form
obtained by differentiating the expression above; I did not find it stated in exactly this form
in the sources I read.]

The structure is important: the `nβ` term is **optimal** (equals the broadcast bandwidth lower
bound — the linear pipeline has optimal throughput), but the latency term is `O(Pα)` rather
than `O(α log P)`. So a linear pipeline is the right answer for *very large n and modest P*, and
the wrong answer as P grows. This is exactly the gap the two-tree algorithm closes.

### 2.4 Scatter + allgather (van de Geijn) — the MPICH long-message broadcast

The single most important long-message broadcast idea, and the direct structural analogue of
Rabenseifner's allreduce. Quoting [thakur2005optimization]:

> "In this algorithm, the message to be broadcast is first divided up and scattered among the
> processes, similar to an MPI_Scatter. The scattered data is then collected back to all
> processes, similar to an MPI_Allgather. The time taken by this algorithm is the sum of the
> times taken by the scatter, which is (lg p α + (p−1)/p nβ) for a binomial tree algorithm, and
> the allgather for which we use either recursive doubling or the ring algorithm depending on
> the message size."

**Component costs:**

- Binomial-tree scatter: `log₂ P α + ((P−1)/P) n β`
- Ring allgather: `(P−1) α + ((P−1)/P) n β`
- Recursive-doubling allgather: `log₂ P α + ((P−1)/P) n β`

**Exact published cost (ring allgather variant), the headline formula:**

> **`T_vandegeijn = (lg P + P − 1) α + 2 ((P−1)/P) n β`** [thakur2005optimization]

**Exact published cost (recursive-doubling allgather variant):**

**`T_vandegeijn,rd = 2 lg P α + 2 ((P−1)/P) n β`** [thakur2005optimization] (obtained by summing
the two component costs the paper gives; the paper writes out only the ring form explicitly)

**The published crossover argument, verbatim in substance:** *"Comparing this time with that for
the binomial tree algorithm, we see that for long messages (where the latency term can be
ignored) and when lg p > 2 (or p > 4), the Van de Geijn algorithm is better than binomial tree.
The maximum improvement in performance that can be expected is (lg p)/2. In other words, the
larger the number of processes, the greater the expected improvement in performance."*
[thakur2005optimization]

So: `2n β` vs `log₂ P · n β` ⇒ speedup ceiling `(log₂ P)/2`, break-even at P > 4.

Chan et al. give the same algorithm as "MSTScatter followed by BKTAllgather" with cost
**`T_Scatter−Allgather(p,n) = p α + ⌈log p⌉ α + 2((p−1)/p) n β`**, and comment that *"As n gets
large, and the β term dominates, this cost is approximately ⌈log(p)⌉/2 times faster than the
MST Bcast algorithm and within a factor of two of the lower bound."* [chan2007collective] — an
independent confirmation of both the formula's shape and the `(log P)/2` speedup ceiling.

**MPICH selection rule:** *"we use the binomial tree algorithm for short messages (< 12 KB) or
when the number of processes is less than 8, and the Van de Geijn algorithm otherwise (long
messages and number of processes ≥ 8)."* [thakur2005optimization]

### 2.5 Two-tree / double-tree broadcast (Sanders, Speck & Träff 2009)

The idea: communicate concurrently over **two** binary trees that each span all P processes,
laid out so that a process that is an *interior* node in one tree is a *leaf* in the other. In
the full-duplex model each tree then gets to use half the bandwidth, and the pair together
saturates the link — *"each tree communicates as efficiently as a single tree with exclusive use
of the network. Our algorithms thus achieve up to twice the bandwidth of most previous
algorithms. In particular, our approach beats all previous algorithms for reduction and scan."*
[sanders2009twotree]

Cost derivation: split the message of size n into `2k` blocks; each step costs `α + β n/(2k)`.
With tree height `h = log₂ P`, after `2h` steps the first block has reached every node in both
trees, and thereafter each process receives one block per step, for `2h + 2k` steps total:

**`T_2tree(P, n, k) = (2h + 2k)(α + β n/(2k))`**, with `h = log₂ P` [sanders2009twotree]

Optimizing the block count, `k* = √(β n h / (2α))`, gives the headline result:

> **`T_2tree = n β + 2 α log₂ P + √(8 α β n log₂ P)`** [sanders2009twotree]

This is the best of both worlds: the **bandwidth term `nβ` is optimal** (matches the broadcast
lower bound, unlike van de Geijn's `2(P−1)/P nβ`) *and* the **latency term is `O(α log P)`**
(unlike the linear pipeline's `O(Pα)`). The middle term `√(8αβn log P)` is the pipeline fill
cost. The authors describe the algorithms as *"almost optimal for message sizes n with
nβ ≫ α log p"* [sanders2009twotree].

Relative placement, per [sanders2009twotree] and the standard summary of it: the linear pipeline
also has optimal throughput but `O(Pα)` startup, so the two-tree broadcast is faster for large
P; and since both achieve full bandwidth, the two-tree algorithm's advantage is purely in the
latency term. A crucial extra property: because both trees can use the same in-order numbering,
the scheme *"is general enough to support not only broadcasting but also non-commutative
reduction and scanning"* [sanders2009twotree] — which is why it is the state of the art for
reduce and scan and not just broadcast.

### 2.6 Broadcast regime summary

| n | Best algorithm | Cost |
|---|---|---|
| very short | binomial tree | `⌈lg P⌉(α + nβ)` |
| short/medium, small P | binomial tree | `⌈lg P⌉(α + nβ)` |
| long, P ≥ 8 | scatter + ring allgather (van de Geijn) | `(lg P + P − 1)α + 2((P−1)/P)nβ` |
| very long, large P | two-tree | `nβ + 2α lg P + √(8αβn lg P)` |
| very long, small P | linear pipeline / chain | `≈ nβ + (P−2)α + 2√((P−2)αnβ)` |

---

## 3. Reduce and Allreduce

Lower bounds [chan2007collective, Table 1]:

| Operation | Latency | Bandwidth | Computation |
|---|---|---|---|
| Reduce(-to-one) | `⌈lg P⌉ α` | `n β` | `((P−1)/P) n γ` |
| Reduce-scatter | `⌈lg P⌉ α` | `((P−1)/P) n β` | `((P−1)/P) n γ` |
| Allreduce | `⌈lg P⌉ α` | `2((P−1)/P) n β` | `((P−1)/P) n γ` |

The computation lower bound argument [chan2007collective]: *"The computation involved would
require (p − 1)n operations if executed on a single node or time (p − 1)nγ. Distributing this
computation perfectly among the nodes reduces the time to ((p−1)/p) nγ under ideal
circumstances."* The allreduce bandwidth bound: *"If the lower bound on computation is to be
achieved, one can argue that ((p−1)/p)n items must leave each node, and ((p−1)/p)n items must
be received by each node after the computation is completed for a total cost of at least
2((p−1)/p) n β."* [chan2007collective]

**Why reduce/allreduce deserve the most attention** — the empirical justification given in
[thakur2005optimization]: *"A five-year profiling study of applications running in production
mode on the Cray T3E 900 at the University of Stuttgart revealed that more than 40% of the time
spent in MPI functions was spent in the two functions MPI_Allreduce and MPI_Reduce and that 25%
of all execution time was spent on program runs that involved a non-power-of-two number of
processes."* [thakur2005optimization, citing rabenseifner1999automatic]

### 3.1 Binomial-tree reduce

Reverse of the binomial-tree broadcast, with a local reduction applied at every receipt.

> "The old algorithm for reduce in MPICH uses a binomial tree, which takes lg p steps, and the
> data communicated at each step is n." [thakur2005optimization]

**`T_tree = ⌈log₂ P⌉ (α + nβ + nγ)`** [thakur2005optimization]

Chan et al.'s MSTReduce is the same: `T_MSTReduce(p,n) = ⌈log p⌉(α + nβ + nγ)`
[chan2007collective]. Note the γ term is `⌈lg P⌉ n γ` — a factor of `≈ lg P` above the
`((P−1)/P) n γ` lower bound, because the root (and each interior node) redundantly reduces full
n-byte vectors at every level. **This γ inefficiency is the single most important thing to carry
over to AgentMPI**, because in an agent setting γ is the dominant term (see §9).

Allreduce in old MPICH was simply *"a reduce to rank 0 followed by a broadcast"*
[thakur2005optimization], cost `2⌈lg P⌉α + 2⌈lg P⌉nβ + ⌈lg P⌉nγ`. Chan et al. give the
short-vector reduce-then-broadcast cost as
`T_Reduce−Bcast(p,n) = ⌈lg p⌉(α₁ + α₃) + 2⌈lg p⌉ n β + ⌈lg p⌉ n γ` [chan2007collective].

**Regime:** short messages. MPICH: Rabenseifner for long messages (> 2 KB), binomial tree for
short (≤ 2 KB). Also — importantly — *"In the case of user-defined reduction operations, we use
the binomial tree algorithm for all message sizes because, unlike with predefined reduction
operations, the user may pass derived datatypes, and breaking up derived datatypes to do the
reduce-scatter is tricky."* [thakur2005optimization]

### 3.2 Recursive doubling allreduce

The allgather recursive-doubling pattern with a local reduction each step: in round k, process i
exchanges its *entire current n-byte vector* with `i XOR 2^k` and reduces. Every process ends
with the full result.

> "For allreduce, we use a recursive doubling algorithm for short messages and for long messages
> with user-defined reduction operations. This algorithm is similar to the recursive doubling
> algorithm used in allgather, except that each communication step also involves a local
> reduction." [thakur2005optimization]

**`T_rec_dbl = log₂ P α + n log₂ P β + n log₂ P γ`** [thakur2005optimization]

Chan et al.'s BDEAllreduce ("bidirectional exchange") is the same algorithm with cost
`T_BDEAllreduce(p,n) = log(p)(α + nβ + nγ)`, and they note pointedly: *"This cost attains the
lower bound only for the latency component."* [chan2007collective]

So recursive doubling is **latency-optimal** (`lg P α`, hits the bound) but pays `lg P` on
*both* bandwidth and computation. It is the correct choice for short vectors only. MPICH: *"For
buffer sizes less than or equal to 32 bytes, recursive doubling is the best"*
[thakur2005optimization].

### 3.3 Rabenseifner's algorithm — the headline long-message result

**The idea**, in [thakur2005optimization]'s own framing of the analogy with van de Geijn:

> "Van de Geijn implements the broadcast as a scatter followed by an allgather, which reduces
> the n lg p β bandwidth term in the binomial tree algorithm to a 2nβ term. Rabenseifner's
> algorithm implements a long-message reduce effectively as a reduce-scatter followed by a
> gather to the root, which has the same effect of reducing the bandwidth term from n lg p β to
> 2nβ." [thakur2005optimization]

**Allreduce = reduce-scatter (recursive halving) + allgather (recursive doubling).**
**Reduce = reduce-scatter (recursive halving) + binomial-tree gather to root.**

Component costs, quoted from [thakur2005optimization]:

- Reduce-scatter by recursive halving: `lg P α + ((P−1)/P) n β + ((P−1)/P) n γ`
- Allgather by recursive doubling: `lg P α + ((P−1)/P) n β`

Summing, for **P a power of two**:

> **`T_rabenseifner = 2 lg P α + 2 ((P−1)/P) n β + ((P−1)/P) n γ`** [thakur2005optimization,
> rabenseifner2004optimization]

This is the target formula. Compare against the lower bounds table above: the **bandwidth term
`2((P−1)/P)nβ` is exactly optimal for allreduce**, and the **computation term `((P−1)/P)nγ` is
exactly optimal**. Only the latency term is off, by a factor of 2 (`2 lg P α` vs `⌈lg P⌉ α`).
Rabenseifner's algorithm is therefore *bandwidth- and computation-optimal, latency-suboptimal by
2×* — the mirror image of recursive doubling.

The same expression is given for **reduce** in [thakur2005optimization]:
`T_rabenseifner = 2 lg P α + 2((P−1)/P) n β + ((P−1)/P) n γ` (reduce-scatter + binomial gather,
where the gather contributes `lg P α_uni + ((P−1)/P) n β_uni`).

**Mechanics of the recursive-halving reduce-scatter** — quoting [thakur2005optimization]:

> "In the first step, each process exchanges data with a process that is a distance p/2 away:
> Each process sends the data needed by all processes in the other half, receives the data
> needed by all processes in its own half, and performs the reduction operation on the received
> data. The reduction can be done because the operation is commutative. In the second step, each
> process exchanges data with a process that is a distance p/4 away. This procedure continues
> recursively, halving the data communicated at each step, for a total of lg p steps."

So the volume per round is `n/2, n/4, …, n/P`, summing to `((P−1)/P) n` — the geometric series is
where the `(P−1)/P` comes from. Note **commutativity is required** (see §3.6 and §4).

**Mechanics of the recursive-doubling allgather phase** (the second half), from
[thakur2005optimization] §5.1:

> "To implement allreduce, we do an allgather using recursive vector doubling and distance
> halving. In the first step, process pairs exchange 1/p′ of the buffer to achieve 2/p′ of the
> result vector, in the next step 2/p′ of the buffer is exchanged to get 4/p′ of the result, and
> so forth. After lg p′ steps, the p′ processes receive the total reduction result."

[thakur2005optimization] names the four structural primitives explicitly, and these names are
worth adopting for AgentMPI:

- **Recursive vector halving** — the vector to be reduced is recursively halved each step.
- **Recursive vector doubling** — small pieces scattered across processes are recursively
  gathered/combined into the large vector.
- **Recursive distance halving** — communication distance halves each step (P/2, P/4, …, 1).
- **Recursive distance doubling** — communication distance doubles each step (1, 2, 4, …, P/2).

The reduce-scatter phase is *vector halving + distance doubling*; the allgather phase is *vector
doubling + distance halving*. (Also: *"All algorithms in this section can be implemented without
local copying of data, except if user-defined noncommutative operations are used."*
[thakur2005optimization])

### 3.4 Non-power-of-two handling I: halving-and-doubling with 2-1 elimination

Recursive halving needs `P = 2^k`. [thakur2005optimization]'s fix-up:

> "Since these recursive algorithms require a power-of-two number of processes, if the number of
> processes is not a power of two, we first reduce it to the nearest lower power of two
> (p′ = 2^⌊lg p⌋) by removing r = p − p′ extra processes as follows. In the first 2r processes
> (ranks 0 to 2r − 1), all the even ranks send the second half of the input vector to their
> right neighbor (rank +1), and all the odd ranks send the first half of the input vector to
> their left neighbor (rank − 1)... The even ranks compute the reduction on the first half of
> the vector and the odd ranks compute the reduction on the second half. The odd ranks then send
> the result to their left neighbors (the even ranks)... These odd ranks do not participate in
> the rest of the algorithm, which leaves behind a power-of-two number of processes."

The elimination step costs `(1+f_α)α + (n/2)(1+f_β)β + (n/2)γ` [thakur2005optimization]. The
resulting published costs (`p′ = 2^⌊lg p⌋`):

**Allreduce, halving & doubling:**

- P a power of two:
  **`T_all,h&d,P=2^k = 2 lg P α + 2nβ + nγ − (1/P)(2nβ + nγ) ≃ 2 lg P α + 2nβ + nγ`**
- P not a power of two:
  **`T_all,h&d,P≠2^k = (2 lg P′ + 1 + 2f_α) α + (2 + (1+3f_β)/2) nβ + (3/2) nγ − (1/P′)(2nβ + nγ)`**
  **`≃ (3 + 2⌊lg P⌋) α + 4nβ + (3/2) nγ`** [thakur2005optimization]

**Reduce, halving & doubling:**

- P a power of two:
  **`T_red,h&d,P=2^k = lg P (1+f_α) α + (1+f_β) nβ + nγ − (1/P)((1+f_β)nβ + nγ) ≃ 2 lg P α + 2nβ + nγ`**
- P not a power of two:
  **`T_red,h&d,P≠2^k = lg P′(1+f_α)α + (1+f_α)α + (1 + (1+f_β)/2 + f_β) nβ + (3/2)nγ − (1/P′)((1+f_β)nβ + nγ)`**
  **`≃ (2 + 2⌊lg P⌋) α + 3nβ + (3/2) nγ`** [thakur2005optimization]

The verdict from the paper: *"This algorithm is good for long vectors and power-of-two numbers of
processes. For non-power-of-two numbers of processes, the data transfer overhead is doubled, and
the computation overhead is increased by 3/2."* [thakur2005optimization] The bandwidth term goes
`2nβ → 4nβ` and the compute term `nγ → 1.5nγ`. **This ~2× non-power-of-two cliff is the single
biggest practical wart in Rabenseifner's algorithm**, and it motivates the next two subsections.

There is also a note that the root can be handled for free in reduce: *"if the root happens to be
one of those odd-ranked processes that would normally be removed in the first step, then the role
of this process and its partner in the first step are interchanged after the first reduction in
the reduce-scatter phase, which causes no additional overhead."* [thakur2005optimization]

### 3.5 Non-power-of-two handling II: binary blocks

> "This algorithm reduces some of the load imbalance in the recursive halving and doubling
> algorithm when the number of processes is not a power of two. The algorithm starts with a
> binary-block decomposition of all processes in blocks with power-of-two numbers of processes.
> Each block executes its own reduce-scatter with the recursive vector halving and distance
> doubling algorithm described above. Then, starting with the smallest block, the intermediate
> result (or the input vector in the case of a 2⁰ block) is split into the segments of the
> intermediate result in the next higher block and sent to the processes in that block, and
> those processes compute the reduction on the segment." [thakur2005optimization]

The controlling quantity is the *gap between consecutive exponents* in the binary
representation of P:

> "Let us define δ_expo,max as the maximal difference of two consecutive exponents in the binary
> representation of the number of processes. For example, 100 = 2⁶ + 2⁵ + 2², δ_expo,max =
> max(6 − 5, 5 − 2) = 3. If δ_expo,max is small, the binary blocks algorithm can perform well."
> [thakur2005optimization]

Load imbalance arises because *"in the 2² block, each process receives and reduces two segments
... whereas in the 2⁰ block (P12), each process has to send as many messages as the ratio of the
two block sizes."* [thakur2005optimization] Empirical rule from the Cray T3E 900: *"the binary
blocks algorithm is faster if δ_expo,max < lg(vector length in bytes)/2.0 − 2.5 and vector size
≥ 16 KB and more than 32 processes are used."* [thakur2005optimization] And: *"if the number of
processes is a power of two, the binary blocks algorithm is identical to the recursive halving
and doubling algorithm."* [thakur2005optimization]

No single closed-form cost is given for binary blocks in [thakur2005optimization] — its cost is
data-dependent through δ_expo,max. [UNVERIFIED: closed-form cost for binary blocks.]

### 3.6 Non-power-of-two handling III: Rabenseifner & Träff 3-2 elimination

The refined answer, from the follow-up paper [rabenseifner2004more]. Rather than eliminating
processes 2-at-a-time-down-to-1 (which wastes a process outright and doubles the data volume),
the **3-2 elimination step** absorbs one process out of every group of three while keeping two
alive:

> "For m′ > b the 3-2 elimination step is used on a group of three processes p0, p1, and p2, to
> absorb the vector of process p2 into the partial results of process p0 and p1, which will
> survive for the following rounds. The step is as follows: process p2 sends m′/2 (upper)
> elements to process p1, and simultaneously receives m′/2 (lower) elements from process p1.
> Process p1 and p2 can then perform the reduction operation on their respective part of the
> vector. Next, process p0 receives the m′/2 (lower) elements of the partial result just
> computed from process p2..." [rabenseifner2004more]

Writing `P = q · 2^n` with `2^n` the largest power of two below P and q odd, the paper gives two
protocols — **overlapping** and **non-overlapping** 3-2 elimination — that *"both achiev[e] the
same bounds"*, plus a ring-based variant usable in some rounds for certain small q. The
overlapping protocol *"schedules 3-2-elimination steps for a group of 2^z · 3 processes in each
round z for which the zth bit of p is 1"*, and the round count does not *"increase by more than
1, for a total of n + 1 = ⌈log₂ p⌉ rounds"* [rabenseifner2004more].

So the key claim: **3-2 elimination keeps the butterfly at `⌈log₂ P⌉` rounds for arbitrary P**,
avoiding the `⌊lg P⌋ + 2`/`⌊lg P⌋ + 3` round inflation *and* the doubled data volume of the 2-1
elimination approach. [UNVERIFIED: I did not obtain the paper's exact closed-form α/β/γ cost
expression; the round-count and bandwidth claims above are directly supported by the retrieved
text, but the full cost table is not.]

### 3.7 Ring allreduce

**Mechanics.** Reduce-scatter by pairwise-exchange ring + allgather by ring. Each process's
n-byte buffer is split into P chunks of n/P. Phase 1 (*scatter-reduce*), P−1 steps: in step i,
process j sends chunk `(j − i)` to `j+1` and receives chunk `(j − i − 1)` from `j−1`, adding it
into its own copy. After P−1 steps each process holds one fully-reduced chunk. Phase 2
(*allgather*), another P−1 steps: the reduced chunks circulate the ring. Total per-process
traffic: `2(P−1)/P · n` bytes.

[thakur2005optimization] gives it as its own algorithm in §5.3:

> "This algorithm uses a pairwise-exchange algorithm for the reduce-scatter phase. For allreduce,
> it uses a ring algorithm to do the allgather, and, for reduce, all processes directly send
> their result segment to the root. This algorithm is good in bandwidth use when the number of
> processes is not a power of two, but the latency scales with the number of processes.
> Therefore this algorithm should be used only for small or medium number of processes or for
> large vectors."

**Published costs** [thakur2005optimization]:

> **`T_all,ring = 2(P−1)α + 2nβ + nγ − (1/P)(2nβ + nγ)`**
> **`= 2(P−1)α + 2((P−1)/P)nβ + ((P−1)/P)nγ`**

> **`T_red,ring = (P−1)(α + α_uni) + n(β + β_uni) + nγ − (1/P)(n(β + β_uni) + nγ)`**

**Bandwidth optimality.** The ring achieves exactly the allreduce bandwidth lower bound
`2((P−1)/P) n β` [chan2007collective]. Patarasuk & Yuan [patarasuk2009bandwidth] give the
formal proof that a ring-based schedule is bandwidth-optimal for allreduce on clusters of
workstations. The factor `2(P−1)/P = 2 − 2/P` is **bounded above by 2 regardless of P** — the
per-node traffic is *independent of the process count*, which is the whole point.

**Latency is the price:** `2(P−1)α`, linear in P, versus `2 lg P α` for Rabenseifner and
`lg P α` for recursive doubling. The ring is therefore the *long-vector, moderate-P* algorithm.
[thakur2005optimization] found empirically on the T3E: *"The ring algorithm is faster on 3, 5,
7, 9–11, and 17 processes."*

**Ring allreduce in deep learning.** Ring allreduce became the standard gradient-aggregation
primitive for data-parallel training, because gradient buffers are large (bandwidth-bound
regime, where the ring is optimal) and P is moderate. The lineage:

- **Baidu SVAIL** popularized it for DL and released `baidu-allreduce`
  [gibiansky2017bringing]: *"a ring allreduce is an algorithm for which the communication cost
  is constant and independent of the number of GPUs in the system, and is determined solely by
  the slowest connection between GPUs in the system; in fact, if you only consider bandwidth as
  a factor in your communication cost (and ignore latency), the ring allreduce is an optimal
  communication algorithm."* [gibiansky2017bringing]
- **Horovod** (Uber) packaged it as a drop-in library for TensorFlow/PyTorch
  [sergeev2018horovod]. A practical refinement worth noting: Horovod *fuses* many small gradient
  tensors into a few large buffers precisely so the allreduce runs in the bandwidth-bound regime
  where the ring wins rather than the latency-bound regime where it loses.
- **NCCL** implements ring *and* tree collectives and auto-selects per call based on message
  size, worker count, and topology — i.e. NCCL reproduces exactly the message-size-based
  algorithm-selection strategy that [thakur2005optimization] argues for.

The DL story is a clean confirmation of the α/β/γ model's predictive power: fix n very large and
P moderate, and the model says "use the ring", and that is what the entire field converged on.

### 3.8 Algorithm selection for allreduce — the empirical decision surface

[thakur2005optimization] determined this experimentally on a Cray T3E 900 for
`MPI_SUM`/`MPI_DOUBLE` (their Figure 14), and the resulting rule set is the most detailed
published threshold logic for allreduce:

| Buffer size | Best algorithm |
|---|---|
| ≤ 32 bytes | recursive doubling |
| ≤ 1 KB | vendor's algorithm (power-of-two) / binomial tree (non-power-of-two) — *"but not much better than recursive doubling"* |
| longer | ring for some sizes and P < 32; binary blocks if `δ_expo,max < lg(n)/2.0 − 2.5` and n ≥ 16 KB and P > 32; halving-and-doubling otherwise |

Verbatim: *"For buffer sizes less than or equal to 32 bytes, recursive doubling is the best; for
buffer sizes less than or equal to 1 KB, the vendor's algorithm (for power-of-two) and binomial
tree (for non-power-of-two) are the best, but not much better than recursive doubling; for longer
buffer sizes, the ring algorithm is good for some buffer sizes and some number of processes less
than 32. In general, on a Cray T3E 900, the binary blocks algorithm is faster if δ_expo,max <
lg(vector length in bytes)/2.0 − 2.5 and vector size ≥ 16 KB and more than 32 processes are used.
In a few cases, for example, 33 processes and less than 32 KB, recursive halving and doubling is
the best."* [thakur2005optimization] The paper also reports that halving-and-doubling beat binary
blocks *"on 33, 65, 66, 97, 128–131 processes"* at 32 KB — i.e. exactly the P values where
δ_expo,max is large.

**MPICH's shipped rules** [thakur2005optimization]:

- **Reduce:** Rabenseifner for long messages (> 2 KB) with predefined ops; binomial tree for
  short messages (≤ 2 KB) *and for all sizes with user-defined ops*.
- **Allreduce:** recursive doubling for short messages *and* for long messages with user-defined
  reduction operations; Rabenseifner for long messages with predefined ops.

Measured payoff: *"the new algorithms improve the performance of allreduce by up to 20% and that
of reduce by up to 54%, compared to the vendor's implementation on the T3E"*, and 3–7× versus
old MPICH-1 on a Myrinet cluster [thakur2005optimization].

---

## 4. Reduction reproducibility: what the MPI standard actually says

This section matters disproportionately for AgentMPI, because an LLM applying a reduction
operator is *far* less associative and far less deterministic than floating-point addition, and
MPI has already worked out the vocabulary for reasoning about it.

### 4.1 The normative text on associativity and commutativity

From the MPI standard, `MPI_REDUCE` section (text below is verbatim from MPI-3.1 and is
essentially unchanged through MPI-5.0) [mpiforum2023mpi41]:

> "The operation `op` is always assumed to be associative. All predefined operations are also
> assumed to be commutative. Users may define operations that are assumed to be associative, but
> not commutative. The "canonical" evaluation order of a reduction is determined by the ranks of
> the MPI processes in the group. However, the implementation can take advantage of
> associativity, or associativity and commutativity in order to change the order of evaluation.
> This may change the result of the reduction for operations that are not strictly associative
> and commutative, such as floating point addition."

Four separate things are being said here, and they should be kept distinct:

1. **Associativity is assumed unconditionally.** `MPI_Op` has no "non-associative" mode. There
   is no way to tell MPI that your operator is not associative.
2. **Commutativity is a user-declarable property.** `MPI_Op_create(fn, commute, &op)` takes a
   `commute` flag; all *predefined* ops (`MPI_SUM`, `MPI_MAX`, …) are commutative. This is the
   flag that gates recursive halving: [thakur2005optimization] uses recursive halving for
   commutative ops and recursive doubling for non-commutative ones (§3.1, §7.3), because *"The
   reduction can be done because the operation is commutative"* and *"If the reduction operation
   is not commutative, recursive halving will not work (unless the data is permuted suitably)."*
3. **There is a defined canonical order** — rank order — but implementations are *explicitly
   licensed to depart from it*.
4. **The standard states outright that the result may differ** for operators that are not
   strictly associative/commutative.

### 4.2 Reproducibility: a non-binding recommendation, and a narrow one

*Advice to implementors* [mpiforum2023mpi41]:

> "It is strongly recommended that MPI_REDUCE be implemented so that the same result be obtained
> whenever the function is applied on the same arguments, appearing in the same order. Note that
> this may prevent optimizations that take advantage of the physical location of ranks."

Read the scope carefully. This is:

- **Advice, not a requirement.** [balaji2013reproducibility] states this plainly: *"the MPI
  standard does not guarantee that MPI reduction operations are bitwise reproducible, such
  behavior is strongly recommended by the standard for MPI implementations."*
- **Conditioned on "the same arguments, appearing in the same order."** So the recommendation
  covers **run-to-run reproducibility at fixed P and fixed rank order**. It says *nothing* about
  reproducibility across different process counts, and in fact cannot: at different P the
  arguments are different and the reduction tree has a different shape.
- **Not a guarantee of rank order.** [balaji2013reproducibility]: *"While the MPI standard
  encourages implementors to implement the reduction functionality in such a way that the result
  is the same given the same arguments to the function (including the same order), it does not
  specify that this order needs to be in rank order. In fact, the standard explicitly states that
  an implementation is free to make use of associativity when possible."*

**Direct answer to "is the result reproducible across runs or across process counts?"**

| | Reproducible? |
|---|---|
| Across runs, same P, same rank→node mapping, same binary | **Usually yes** — strongly recommended by the standard, and most implementations comply, sometimes only under a configuration/env setting [balaji2013reproducibility] |
| Across runs, same P, *different* node mapping, with topology-aware collectives enabled | **No.** *"a topology-aware algorithm can have an effect on the reproducibility of the program, since the exact outcome of the reduction depends not only on the parameters passed to the reduce function on each rank but also on the underlying topology and the way each rank is mapped to that topology"* [balaji2013reproducibility] |
| Across **different process counts** | **No, and not even recommended.** The reduction tree shape changes with P, so the evaluation order changes. |
| Across implementations / MPI versions / self-tuning libraries | **No.** *"In the STAR-MPI library, collective routines automatically self-tune during application execution, effectively introducing nondeterministic reduce behavior even when keeping arguments to the reduce function constant"* [balaji2013reproducibility] |
| On machines with hardware collective acceleration, across different node subsets | **No.** *"unless a user runs on exactly the same set of nodes, an MPI reduction will not be reproducible, even if all other factors (such as the compiler and the application binary) remain the same"* [balaji2013reproducibility] |

Note also that MPICH's own algorithm-selection logic is *itself* a reproducibility hazard in
disguise: switching from binomial tree to Rabenseifner at 2 KB changes the evaluation order, so
the same program with a slightly different message size can produce a different answer. As
[balaji2013reproducibility] observes, *"one can implement multiple reduce algorithms and choose
between them based on specific properties of the input arguments, such as the number of ranks in
the communicator or the size of the base type. Doing so does not violate the recommendation of
the standard."*

### 4.3 The standard's prescribed escape hatch

*Advice to users* [mpiforum2023mpi41]:

> "Some applications may not be able to ignore the nonassociative nature of floating-point
> operations or may use user-defined operations ... that require a special reduction order and
> cannot be treated as associative. Such applications should enforce the order of evaluation
> explicitly. For example, in the case of operations that require a strict left-to-right (or
> right-to-left) evaluation order, this could be done by gathering all operands at a single MPI
> process (e.g., with MPI_GATHER), applying the reduction operation in the desired order (e.g.,
> with MPI_REDUCE_LOCAL), and if needed, broadcast or scatter the result to the other MPI
> processes (e.g., with MPI_BCAST)."

**This is the crucial design precedent for AgentMPI.** The standard's own answer to "my operator
is not associative" is: *do not use the collective at all* — gather, reduce locally in a defined
order, then broadcast. That is `⌈lg P⌉α + ((P−1)/P)nβ` for the gather, then `(P−1)nγ`
*sequentially on one process*, then a broadcast. You pay the full undistributed γ term —
precisely the `(P−1)nγ` that [chan2007collective]'s lower-bound argument says a distributed
reduction avoids — in exchange for a defined order.

Also relevant: the standard imposes no such reproducibility recommendation on `MPI_ALLREDUCE`
beyond consistency of the result across processes; and `MPI_SCAN`/`MPI_EXSCAN` inherit the same
associativity assumption (§7).

### 4.4 A distinction worth importing: reproducibility ≠ accuracy

[balaji2013reproducibility]: *"We note that reproducibility does not necessarily mean accuracy.
If multiple runs of an application give the exact same result, it is still considered
reproducible even if the result is not as accurate as what an infinite-precision system can
achieve."* And conversely, *"combining small values first allows us to do so without losing
precision"* — i.e. a *different* order can be strictly *more* accurate. The paper's summary of
the trade-off: enabling topology-aware (non-reproducible) reduction gave *"up to fourfold"*
performance improvement on up to 2,048 cores.

They also note the reason bitwise reproducibility is demanded at all is often non-technical:
*"sometimes just for contractual reasons (e.g., drug design or nuclear reactor design)."*

---

## 5. Allgather, Gather, Scatter

Convention warning: in [thakur2005optimization]'s allgather formulas, **`n` is the *total* amount
of data gathered on each process**, so each process contributes `n/P`. This is why the bandwidth
term is `((P−1)/P) n β` and not `(P−1) n β`. Get this wrong and every allgather formula looks
inconsistent with the broadcast ones.

Lower bounds [chan2007collective]: latency `⌈lg P⌉ α`, bandwidth `((P−1)/P) n β` for allgather,
gather, scatter, and reduce-scatter alike.

### 5.1 Ring allgather

> "The data from each process is sent around a virtual ring of processes. In the first step, each
> process i sends its contribution to process i + 1 and receives the contribution from process
> i − 1 (with wrap-around). From the second step onward each process i forwards to process i+1
> the data it received from process i − 1 in the previous step. If p is the number of processes,
> the entire algorithm takes p − 1 steps." [thakur2005optimization]

**`T_ring = (P−1) α + ((P−1)/P) n β`** [thakur2005optimization]

And the key observation, quoted: *"Note that the bandwidth term cannot be reduced further because
each process must receive n/p data from p − 1 other processes."* So the ring is
**bandwidth-optimal** for allgather and pays `(P−1)α` in latency. Chan et al. call this the
*bucket* or *cyclic* algorithm (BKTAllgather), `T_BKTAllgather = (p−1)α + ((p−1)/p)nβ`, and note
it *"achiev[es] the lower bound for the bandwidth component of the cost"* [chan2007collective].

**Why the ring beats log-P algorithms for long messages despite worse latency** — this is a
*model-violation* effect and worth quoting in full:

> "For long messages, the ring algorithm performs better than recursive doubling. We believe this
> is because it uses a nearest-neighbor communication pattern, whereas in recursive doubling,
> processes that are much farther apart communicate. To confirm this hypothesis, we used the
> b_eff MPI benchmark, which measures the performance of about 48 different communication
> patterns, and found that, for long messages on both the Myrinet cluster and the IBM SP, some
> communication patterns (particularly nearest neighbor) achieve more than twice the bandwidth of
> other communication patterns." [thakur2005optimization]

Both ring and recursive doubling have the *identical* bandwidth term `((P−1)/P)nβ` in the model,
and yet the ring is ~2× faster in practice. **The model does not capture this**; it is a topology
effect. Note this carefully for AgentMPI — the analogous question is whether "nearest-neighbor"
means anything in an agent harness (shared context? co-located on the same inference server?
warm KV cache?).

### 5.2 Recursive doubling allgather

> "In the first step, processes that are a distance 1 apart exchange their data. In the second
> step, processes that are a distance 2 apart exchange their own data as well as the data they
> received in the previous step. In the third step, processes that are a distance 4 apart
> exchange their own data as well the data they received in the previous two steps. In this way,
> for a power-of-two number of processes, all processes get all the data in lg p steps. The
> amount of data exchanged by each process is n/p in the first step, 2n/p in the second step, and
> so forth, up to 2^(lg p − 1) n/p in the last step." [thakur2005optimization]

**`T_rec_dbl = lg P α + ((P−1)/P) n β`** [thakur2005optimization]

The geometric sum `n/P + 2n/P + 4n/P + … + (P/2)n/P = ((P−1)/P) n` is why the bandwidth term is
optimal. So recursive doubling allgather is **simultaneously latency- and bandwidth-optimal for
power-of-two P** — the only algorithm in this whole document with that property. Chan et al.
confirm for their BDE (bidirectional exchange) allgather: *"This cost attains the lower bound for
both the latency and bandwidth components and is thus optimal under these assumptions."*
[chan2007collective]

**Non-power-of-two P is the catch:**

> "Recursive doubling works very well for a power-of-two number of processes but is tricky to get
> right for a non-power-of-two number of processes. We have implemented the non-power-of-two case
> as follows. At each step of recursive doubling, if any set of exchanging processes is not a
> power of two, we do additional communication in the peer (power-of-two) set in a logarithmic
> fashion to ensure that all processes get the data they would have gotten had the number of
> processes been a power of two. This extra communication is necessary for the subsequent steps
> of recursive doubling to work correctly. **The total number of steps for the non-power-of-two
> case is bounded by 2⌊lg p⌋.**" [thakur2005optimization]

So `⌈lg P⌉ → 2⌊lg P⌋` — a 2× latency penalty. This is precisely the gap Bruck closes. Chan et
al. describe the analogous BDE problem more bluntly: *"this solution requires that one node must
send data twice at each step, so the cost of BDE algorithms doubles when not using a power of two
number of nodes... the result is rather haphazard."* [chan2007collective]

### 5.3 Bruck's algorithm for allgather (Bruck et al. 1997)

The short-message, any-P algorithm. It is *"a variant of the dissemination algorithm for
barrier"* [thakur2005optimization, benson2003dissemination] — the same pattern as §1.4 with the
direction reversed:

> "The Bruck algorithm avoids this problem nicely by a simple modification to the dissemination
> algorithm in which, in each step k, process i sends data to process (i − 2^k) and receives data
> from process (i + 2^k), instead of the other way around. The result is that all communication
> is contiguous, except that at the end, the blocks in the output buffer must be shifted locally
> to place them in the right order, which is a local memory-copy operation."
> [thakur2005optimization]

Full mechanics, quoted:

> "The algorithm begins by copying the input data on each process to the top of the output buffer.
> In each step k, process i sends to the destination (i − 2^k) all the data it has so far and
> stores the data it receives (from rank (i + 2^k)) at the end of the data it currently has. This
> procedure continues for ⌊lg p⌋ steps. If the number of processes is not a power of two, an
> additional step is needed in which each process sends the first (p − 2^⌊lg p⌋) blocks from the
> top of its output buffer to the destination and appends the data it receives to the data it
> already has. Each process now has all the data it needs, but the data is not in the right order
> in the output buffer: The data on process i is shifted "up" by i blocks. Therefore, a simple
> local shift of the blocks downwards by i blocks brings the data into the desired order."
> [thakur2005optimization]

**`T_bruck = ⌈log₂ P⌉ α + ((P−1)/P) n β`** [thakur2005optimization, bruck1997efficient]

**Bruck allgather is latency- AND bandwidth-optimal for *arbitrary* P** — `⌈lg P⌉` steps *in all
cases*, matching the [chan2007collective] lower bounds exactly. The cost it pays is not in the
α/β/γ model at all: it is the **local memory permutation** at the end, which is invisible to the
model but real in practice. Hence:

> "The Bruck algorithm has lower latency than recursive doubling for non-power-of-two numbers of
> processes. For power-of-two numbers of processes, however, the Bruck algorithm requires local
> memory permutation at the end, whereas recursive doubling does not. ... As the message size
> increases, the Bruck algorithm suffers because of the memory copies."
> [thakur2005optimization]

### 5.4 Neighbor exchange allgather (Chen et al. 2005)

A linear-latency algorithm designed to halve the ring's step count by making every transfer
pairwise (better for TCP/Ethernet, which is faster on symmetric pair exchanges):

Mechanics: in step s (`0 ≤ s < P/2`), an even-ranked process r sends to neighbor `r + (−1)^s`
and an odd-ranked process r sends to `r − (−1)^s`, both with wrap-around. Two blocks are sent
per step after the first, so `P/2` steps suffice [chen2005performance, loch2021sparbit].

**`T_ne = (P/2) α + ((P−1)/P) n β`** [chen2005performance, as reported in loch2021sparbit]

Same optimal bandwidth term as the ring, half the latency term, **but it only works for even P**
(Open MPI's `ompi_coll_base_allgather_intra_neighborexchange` falls back to the ring for odd P).
Chen et al. report *"our neighbor exchange algorithm performs the best for long messages, the
ring algorithm performs the best for medium-size messages and the recursive doubling algorithm
performs the best for short messages"* on fast Ethernet [chen2005performance]. Not used in
MPICH; used in Open MPI.

### 5.5 MPICH's allgather selection rule

Verbatim [thakur2005optimization]:

- **Bruck** for short messages (**< 80 KB** total data gathered) and non-power-of-two P.
- **Recursive doubling** for power-of-two P and short/medium messages (**< 512 KB** total).
- **Ring** for long messages (**≥ 512 KB**) and any P, *and* for medium messages
  (**≥ 80 KB and < 512 KB**) with non-power-of-two P.

Summarizing the paper's own prose: *"the Bruck algorithm is best for short messages and
non-power-of-two numbers of processes; recursive doubling is best for power-of-two numbers of
processes and short or medium-sized messages; and the ring algorithm is best for long messages
and any number of processes and also for medium-sized messages and non-power-of-two numbers of
processes."* [thakur2005optimization]

### 5.6 Binomial gather and scatter

Gather and scatter are duals: reverse every communication. Both are implemented as a
minimum-spanning/binomial tree in which *"at each step of the recursion only the data that
ultimately must reside in the subnetwork, at which the destination is a member, need to be sent
from the root to the destination"* [chan2007collective]. So the root sends n/2 in the first step,
each of the two sends n/4, and so on.

**`T_MSTScatter = T_MSTGather = ⌈log₂ P⌉ α + ((P−1)/P) n β`** [chan2007collective]

Chan et al.: *"This cost achieves the lower bound for the latency and bandwidth components. Under
the stated assumptions these algorithms are optimal."* [chan2007collective] Binomial
scatter/gather are therefore **optimal at all message sizes** — the only collectives in this
document for which a single algorithm suffices. [thakur2005optimization] uses exactly this
binomial-tree scatter cost, `lg P α + ((P−1)/P) n β`, as the first phase of the van de Geijn
broadcast (§2.4).

The one caveat Chan et al. add for long vectors: *"Sending individual messages from the root to
each of the other nodes. While the cost, (p − 1)α + ((p−1)/p) n β, is clearly worse than the MST
algorithm, in practice the β term has sometimes been observed to be smaller possibly because the
cost of each message can be overlapped with those of other messages. We will call it the simple
(SMPL) algorithm."* [chan2007collective] Another model-violation effect.

**Short-vector allgather as gather + broadcast** [chan2007collective]:
`T_Gather−Bcast(p,n) = ⌈lg p⌉α + ((p−1)/p)nβ + ⌈lg p⌉α + ⌈lg p⌉nβ ≈ 2⌈lg p⌉α + (⌈lg p⌉+1)nβ`.
Latency near-optimal, bandwidth `lg P`× off — a legitimate short-message option.

---

## 6. Alltoall

Alltoall is the collective the α/β/γ model treats least kindly, because the *aggregate* data
volume is `Θ(P²)` blocks and no algorithm can reduce it. Convention: `n` is the total amount of
data a process sends to (or receives from) all others, so each pairwise block is `n/P`.

### 6.1 Pairwise exchange (long messages)

> "For long messages and power-of-two number of processes, we use a pairwise-exchange algorithm,
> which takes p − 1 steps. In each step k, 1 ≤ k < p, each process calculates its target process
> as (rank XOR k) and exchanges data directly with that process. This algorithm, however, does
> not work if the number of processes is not a power of two. For the non-power-of-two case, we use
> an algorithm in which, in step k, each process receives data from rank − k and sends data to
> rank + k. In both these algorithms, data is directly communicated from source to destination,
> with no intermediate steps." [thakur2005optimization]

**`T_long = (P−1) α + n β`** [thakur2005optimization]

The `nβ` term is **optimal** — each process must send exactly n bytes and receive n bytes, and
pairwise exchange sends each byte exactly once with no store-and-forward. The `(P−1)α` is
irreducible for any direct-exchange schedule.

### 6.2 Bruck's index algorithm (short messages)

> "For short messages (≤ 256 bytes per message), we use the index algorithm by Bruck et al. It is
> a store-and-forward algorithm that takes ⌈lg p⌉ steps at the expense of some extra data
> communication ((n/2) lg p β instead of nβ, where n is the total amount of data to be sent or
> received by any process). Therefore, it is a good algorithm for very short messages where
> latency is an issue." [thakur2005optimization]

Mechanics, quoted:

> "The algorithm begins by doing a local copy and "upward" shift of the data blocks from the input
> buffer to the output buffer such that the data block to be sent by each process to itself is at
> the top of the output buffer. To achieve this, process i must rotate its data up by i blocks.
> In each communication step k (0 ≤ k < ⌈lg p⌉), process i sends to rank (i+2^k) (with
> wrap-around) all those data blocks whose kth bit is 1, receives data from rank (i − 2^k), and
> stores the incoming data into blocks whose kth bit is 1 (that is, overwriting the data that was
> just sent). In other words, in step 0, all the data blocks whose least significant bit is 1 are
> sent and received (blocks 1, 3, and 5 in our example). In step 1, all the data blocks whose
> second bit is 1 are sent and received, namely, blocks 2 and 3. After a total of ⌈lg p⌉ steps,
> all the data gets routed to the right destination process, but the data blocks are not in the
> right order in the output buffer. A final step in which each process does a local inverse shift
> of the blocks (memory copies) places the data in the right order."
> [thakur2005optimization, bruck1997efficient]

**Published costs** [thakur2005optimization]:

- P a power of two: **`T_bruck = lg P α + (n/2) lg P β`**
- P not a power of two: **`T_bruck = ⌈lg P⌉ α + ((n/2) lg P + (n/P)(P − 2^⌊lg P⌋)) β`**

The trade: `⌈lg P⌉` rounds instead of `P−1`, at the cost of `(lg P)/2` × the optimal data volume.
Bruck sends each block through `O(log P)` intermediate hops (roughly half the blocks move in each
of `lg P` rounds), so the bandwidth term grows logarithmically. This is the *dual* of the
broadcast trade-off, and it means the crossover is at a *very* small message size — MPICH puts it
at **256 bytes per message**.

[thakur2005optimization]'s appreciation of why it works is worth keeping: *"The beauty of the
Bruck algorithm is that it is a logarithmic algorithm for short-message all-to-all that does not
need any extra bookkeeping or control information for routing the right data to the right
process—that is taken care of by the mathematics of the algorithm."*

### 6.3 Why alltoall does not scale

Three independent reasons, all visible in the formulas:

1. **No algorithm removes the `Θ(P)` latency term at optimal bandwidth.** Pairwise exchange is
   `(P−1)α + nβ`; Bruck is `lg P α + (n/2) lg P β`. You may have `O(log P)` rounds *or* optimal
   `nβ`, never both. Contrast broadcast (two-tree gets both) and allgather (recursive
   doubling/Bruck get both). Alltoall is genuinely harder.
2. **Aggregate volume is `P·n` bytes across `P²` messages** and is irreducible — there is no
   redundancy to exploit, because every (source, destination) pair carries *unique* data. All the
   other collectives in this document have exploitable redundancy (same data to many
   destinations, or reducible data); alltoall has none. This is the structural reason.
3. **The all-pairs pattern maximally stresses the network bisection**, which the model ignores
   entirely. Even old MPICH's unscheduled `Irecv`/`Isend` loop had to be careful about this:
   *"each process calculates the source or destination as (rank + i) % p, which results in a
   scattering of the sources and destinations among the processes. If the loop index were
   directly used as the source or target rank, all processes would try to communicate with rank 0
   first, then with rank 1, and so on, resulting in a bottleneck."* [thakur2005optimization]

**MPICH's four-way alltoall selection rule** [thakur2005optimization]:

| Per-message size | Algorithm |
|---|---|
| ≤ 256 bytes | Bruck index algorithm |
| 256 bytes – 32 KB | `Irecv`/`Isend` with staggered ordering |
| > 32 KB, P = 2^k | pairwise exchange via `rank XOR k` |
| > 32 KB, P ≠ 2^k | send to `rank + k`, receive from `rank − k` |

---

## 7. Scan, Exscan, and Reduce_scatter

### 7.1 Reduce_scatter — the four algorithms

Reduce_scatter is the pivot on which Rabenseifner's allreduce turns, so its algorithms matter
beyond their own use. *"Reduce-scatter is a variant of reduce in which the result, instead of
being stored at the root, is scattered among all processes. It is an irregular primitive: The
scatter in it is a scatterv."* [thakur2005optimization]

**(a) Old MPICH: binomial reduce to rank 0 + linear scatterv.** *"This algorithm takes
lg p + p − 1 steps, and the bandwidth term is (lg p + (p−1)/p) n β."* [thakur2005optimization]

**`T_old = (lg P + P − 1) α + (lg P + (P−1)/P) n β + n lg P γ`** [thakur2005optimization]

**(b) Recursive halving (commutative ops, short/medium messages).** Mechanics as quoted in §3.3:
distance P/2, P/4, …, 1 with the vector halving each step, lg P steps.

- P a power of two:
  **`T_rec_half = lg P α + ((P−1)/P) n β + ((P−1)/P) n γ`** [thakur2005optimization]
- P not a power of two (after the odd/even elimination fix-up):
  **`T_rec_half = (⌊lg P⌋ + 2) α + 2nβ + n(1 + (P−1)/P) γ`** [thakur2005optimization]

The paper flags the second expression as approximate: *"This cost is approximate because some
imbalance exists in the amount of work each process does, since some processes do the work of
their neighbors as well."* [thakur2005optimization] Note again the ~2× non-power-of-two penalty
in both β and γ. MPICH uses recursive halving *"for messages up to 512 KB."*

Chan et al.'s BDEReduce-scatter is the same algorithm (they call recursive halving the reverse of
BDE allgather) [chan2007collective].

**(c) Recursive doubling (NON-commutative ops, very short messages).** Recursive halving requires
commutativity; when the operator is non-commutative you must instead use recursive doubling and
send *more* data:

> "If the reduction operation is not commutative, recursive halving will not work (unless the data
> is permuted suitably). Instead, we use a recursive doubling algorithm similar to the one in
> allgather. ... However, more data is communicated than in allgather. In step 1, processes
> exchange all the data except the data needed for their own result (n − n/p); in step 2,
> processes exchange all data except the data needed by themselves and by the processes they
> communicated with in the previous step (n − 2n/p); in step 3, it is (n − 4n/p); and so forth."
> [thakur2005optimization]

**`T_short = lg P α + n(lg P − (P−1)/P) β + n(lg P − (P−1)/P) γ`** [thakur2005optimization]

MPICH uses this *"for very short messages (< 512 bytes)."* Observe the cost of losing
commutativity: the bandwidth and compute terms go from `((P−1)/P)n ≈ n` to
`(lg P − (P−1)/P)n ≈ (lg P − 1)n`. **Non-commutativity costs a factor of ~lg P in both bandwidth
and compute.** This is the sharpest quantified penalty for operator algebra anywhere in the MPICH
paper, and it is the number to carry into AgentMPI, where operator commutativity is dubious.

**(d) Pairwise exchange (long messages).**

> "In step i, each process sends data to (rank + i), receives data from (rank − i), and performs
> the local reduction. The data exchanged is only the data needed for the scattered result on the
> process (n/p)." [thakur2005optimization]

**`T_long = (P−1) α + ((P−1)/P) n β + ((P−1)/P) n γ`** [thakur2005optimization]

And the crucial empirical note: *"Note that this algorithm has the same bandwidth requirement as
the recursive halving algorithm. Nonetheless, we use this algorithm for long messages because it
performs much better than recursive halving (similar to the results for recursive doubling versus
ring algorithm for long-message allgather)."* [thakur2005optimization] — the same
nearest-neighbor model-violation effect as §5.1. Chan et al.'s BKTReduce-scatter is the same
algorithm: `T = pα + ((p−1)/p) n (β + γ)` [chan2007collective].

**MPICH selection rule for reduce_scatter** [thakur2005optimization]:

| Condition | Algorithm |
|---|---|
| commutative op, < 512 KB | recursive halving |
| commutative op, ≥ 512 KB | pairwise exchange |
| non-commutative op, < 512 bytes | recursive doubling |
| non-commutative op, ≥ 512 bytes | pairwise exchange |

### 7.2 Recursive-doubling scan (`MPI_Scan` / `MPI_Exscan`)

`MPI_Scan` computes a prefix reduction: after the call, process k holds the reduction of the send
buffers of ranks `0…k`. `MPI_Exscan` gives ranks `0…k−1` (rank 0's result is undefined). Both
assume the operator is associative, exactly as `MPI_Reduce` does (§4).

**Recursive-doubling scan (the Kogge–Stone pattern).** In round `k` (`0 ≤ k < ⌈lg P⌉`), process
i sends its current accumulator to process `i + 2^k` (if it exists) and receives from `i − 2^k`
(if it exists), combining the received value *on the left*. After `⌈lg P⌉` rounds every process
holds the correct prefix. Order is preserved throughout — combining strictly on the left is what
makes this work for non-commutative operators.

**`T_scan,rec_dbl = ⌈log₂ P⌉ (α + nβ + nγ)`** [UNVERIFIED as a quoted formula — this is the
direct analogue of the recursive-doubling allreduce cost `lg P(α + nβ + nγ)` given in
[thakur2005optimization] and [chan2007collective], and the MPICH scan is implemented this way,
but I did not find the scan cost written out in the sources I read.]

Structural properties: latency-optimal (`⌈lg P⌉ α`, hitting the lower bound), but `lg P` × off on
both bandwidth and computation. Unlike allreduce, **there is no straightforward
reduce-scatter/allgather decomposition for scan** that recovers the optimal bandwidth term —
which is exactly why Sanders, Speck & Träff single scan out: *"In particular, our approach beats
all previous algorithms for reduction and scan. ... We believe the results achieved for reduction
and parallel prefix to be the theoretically currently best known."* [sanders2009twotree] The
two-tree scan gets `nβ + O(α log P)` (§2.5), and works for non-commutative operators because both
trees share the same in-order numbering.

Sanders & Träff had earlier given a dedicated family of MPI scan algorithms
[sanders2006parallel]. [UNVERIFIED: I did not retrieve the cost expressions from that paper.]

### 7.3 Prefix-network structures: Kogge–Stone vs. Sklansky (vs. Brent–Kung)

These come from the parallel-adder literature and are the *design space* for scan. All are
"valency-2" networks built from 2-input associative operators. The canonical comparison, from
Harris's taxonomy [harris2003taxonomy]:

> "An ideal prefix network would have log₂N stages of logic, a fanout never exceeding 2 at each
> stage, and no more than one horizontal track of wire at each stage. The classic architectures
> deviate from ideal with 2log₂N stages, fanout of N/2+1, and N/2 horizontal tracks,
> respectively." [harris2003taxonomy]

Mapping that sentence onto the three networks (the "respectively" is ordered Brent–Kung,
Sklansky, Kogge–Stone, matching the order they are introduced):

| Network | Stages (depth) | Max fan-out | Wiring tracks | Total operator applications |
|---|---|---|---|---|
| **Kogge–Stone** [koggestone1973parallel] | `log₂ N` (minimal) | 2 (constant) | `N/2` (worst) | `O(N log N)` |
| **Sklansky** [sklansky1960conditional] | `log₂ N` (minimal) | `N/2 + 1` (worst) | low | `O(N log N)` |
| **Brent–Kung** [brentkung1982regular] | `2 log₂ N − 2` (worst) | 2 | low | `O(N)` (minimal) |

- **Kogge–Stone** is the recursive-doubling pattern: in stage k every node combines with the node
  `2^k` to its left. Minimum depth, constant fan-out, but every node is active in every stage, so
  the *total work* is `Θ(N log N)` and the wiring (in MPI terms: the number of concurrent
  messages in flight) is maximal. **This is what MPI scan implementations use**, because in a
  message-passing setting "fan-out" is free (a process can address anyone) and "wiring tracks"
  are free (the network is assumed fully connected), so the only cost that survives into the
  α/β/γ model is depth.
- **Sklansky** is the divide-and-conquer pattern: recursively prefix-sum each half, then
  broadcast the left half's total to *every* node in the right half. Also minimal depth, but the
  fan-out grows to `N/2` — one node must send to `N/2` others in the final stage. **In the α/β/γ
  model this is a disaster** (that node pays `(N/2)α` serially in a single-ported model), which is
  why Sklansky is a hardware structure and not an MPI algorithm. In hardware, fan-out costs
  capacitance rather than serialized time, so the trade is different.
- **Brent–Kung** is work-optimal (`O(N)` operator applications) at double the depth. **If γ is the
  dominant cost, Brent–Kung is the right structure** — this is the important observation for
  AgentMPI (§9).
- Intermediate families interpolate: *"The Han-Carlson family of networks offer tradeoffs in
  stages and wiring between Brent-Kung and Kogge-Stone. The Knowles family similarly offers
  tradeoffs in fanout and wiring between Sklansky and Kogge-Stone and the Ladner-Fischer family
  offers tradeoffs between fanout and stages between Sklansky and Brent-Kung."*
  [harris2003taxonomy]

A theoretical limit worth recording: minimum-depth prefix circuits cannot also be work-optimal.
*"Zero-deficiency prefix circuits do not exist below this boundary, including prefix circuits of
minimal depth log₂ N, such as Sklansky or Kogge–Stone. Therefore, the most span-optimal parallel
algorithm for prefix sum can not achieve a linear work complexity."* [copik2017parallel] So the
depth-vs-work trade in scan is *provably* unavoidable — unlike allreduce, where Rabenseifner gets
optimal bandwidth *and* optimal compute at only 2× the optimal depth.

---

## 8. Summary table: all algorithms with published cost formulas and winning regime

`P` = processes, `n` = bytes (for allgather/alltoall/reduce-scatter, `n` is the *total* data
gathered/sent per process). `P′ = 2^⌊lg P⌋`. Bold = the cost expression is quoted directly from
the cited source; italic = derived by summing quoted component costs.

| # | Collective | Algorithm | Published cost | Winning regime | Source |
|---|---|---|---|---|---|
| 1 | Barrier | linear / flat tree | `2(P−1) α` | tiny P | — |
| 2 | Barrier | binomial tree | `2⌈lg P⌉ α` | any P, simple | [chan2007collective] |
| 3 | Barrier | tournament | `⌈lg P⌉ α` + release (`2⌈lg P⌉ α` w/ wake-up tree) | contended medium, P ≥ 16 | [hensgen1988two] |
| 4 | Barrier | **dissemination** | **`⌈lg P⌉ α`** | **all P — latency-optimal, no 2^k penalty** | [hensgen1988two, thakur2005optimization] |
| 5 | Barrier | butterfly (rec. doubling) | `lg P α` (P=2^k); `(⌊lg P⌋+2)α` otherwise [UNVERIFIED] | P = 2^k, pairwise-cheap networks | [brooks1986butterfly] |
| 6 | Bcast | linear | `(P−1)(α + nβ)` | never (P ≤ 2) | — |
| 7 | Bcast | **binomial tree (MST)** | **`⌈lg P⌉ (α + nβ)`** | **short n (< 12 KB) or P < 8** | [thakur2005optimization, chan2007collective] |
| 8 | Bcast | pipelined chain, `n_s` segs | **`(P + n_s − 2)(α + (n/n_s)β)`**; opt. `≈ nβ + (P−2)α + 2√((P−2)αnβ)` [UNVERIFIED opt. form] | very long n, small P | [nuriyev2020accurate] |
| 9 | Bcast | **scatter + ring allgather (van de Geijn)** | **`(lg P + P − 1) α + 2((P−1)/P) n β`** | **long n, P ≥ 8** | [thakur2005optimization] |
| 10 | Bcast | scatter + rec-dbl allgather | *`2 lg P α + 2((P−1)/P) n β`* | long n, moderate P, P=2^k | [thakur2005optimization] |
| 11 | Bcast | **two-tree (double tree)** | **`nβ + 2α lg P + √(8αβn lg P)`** | **very long n, large P — bw-optimal + log latency** | [sanders2009twotree] |
| 12 | Reduce | **binomial tree (MST)** | **`⌈lg P⌉ (α + nβ + nγ)`** | **short n (≤ 2 KB); user-defined ops at all n** | [thakur2005optimization, chan2007collective] |
| 13 | Reduce | **Rabenseifner (rec-half RS + binomial gather)** | **`2 lg P α + 2((P−1)/P) n β + ((P−1)/P) n γ`** | **long n (> 2 KB), P = 2^k, predefined op** | [thakur2005optimization, rabenseifner2004optimization] |
| 14 | Reduce | Rabenseifner, P ≠ 2^k | **`≃ (2 + 2⌊lg P⌋) α + 3nβ + (3/2) nγ`** | long n, P ≠ 2^k | [thakur2005optimization] |
| 15 | Reduce | ring | **`(P−1)(α+α_uni) + n(β+β_uni) + nγ − (1/P)(n(β+β_uni)+nγ)`** | long n, small/medium P | [thakur2005optimization] |
| 16 | Allreduce | reduce + bcast (binomial) | `2⌈lg P⌉α + 2⌈lg P⌉nβ + ⌈lg P⌉nγ` | ≤ 1 KB, P ≠ 2^k | [thakur2005optimization, chan2007collective] |
| 17 | Allreduce | **recursive doubling (BDE)** | **`lg P α + n lg P β + n lg P γ`** | **very short n (≤ 32 bytes); user-defined ops** | [thakur2005optimization, chan2007collective] |
| 18 | Allreduce | **Rabenseifner (rec-half RS + rec-dbl AG)** | **`2 lg P α + 2((P−1)/P) n β + ((P−1)/P) n γ`** | **long n, P = 2^k — bw- & compute-optimal** | [thakur2005optimization, rabenseifner2004optimization] |
| 19 | Allreduce | Rabenseifner h&d, P = 2^k (refined) | **`2 lg P α + 2nβ + nγ − (1/P)(2nβ + nγ)`** | long n, P = 2^k | [thakur2005optimization] |
| 20 | Allreduce | Rabenseifner h&d, P ≠ 2^k | **`(2 lg P′ + 1 + 2f_α)α + (2 + (1+3f_β)/2)nβ + (3/2)nγ − (1/P′)(2nβ+nγ) ≃ (3 + 2⌊lg P⌋)α + 4nβ + 1.5nγ`** | long n, P ≠ 2^k, large δ_expo,max | [thakur2005optimization] |
| 21 | Allreduce | binary blocks | no closed form; wins if `δ_expo,max < lg(n)/2.0 − 2.5`, n ≥ 16 KB, P > 32 | long n, P ≠ 2^k, small δ_expo,max | [thakur2005optimization] |
| 22 | Allreduce | 3-2 elimination butterfly | `⌈lg P⌉` rounds for arbitrary P; avoids 2× volume penalty | long n, any P | [rabenseifner2004more] |
| 23 | Allreduce | **ring** | **`2(P−1)α + 2((P−1)/P)nβ + ((P−1)/P)nγ`** | **very long n, moderate P — bw-optimal; DL standard** | [thakur2005optimization, patarasuk2009bandwidth] |
| 24 | Allgather | **ring (bucket)** | **`(P−1) α + ((P−1)/P) n β`** | **long n (≥ 512 KB) any P; medium n & P≠2^k** | [thakur2005optimization, chan2007collective] |
| 25 | Allgather | **recursive doubling (BDE)** | **`lg P α + ((P−1)/P) n β`** | **P = 2^k, short/medium n (< 512 KB) — both bounds met** | [thakur2005optimization, chan2007collective] |
| 26 | Allgather | rec. doubling, P ≠ 2^k | steps bounded by `2⌊lg P⌋` | (avoid) | [thakur2005optimization] |
| 27 | Allgather | **Bruck (concatenation)** | **`⌈lg P⌉ α + ((P−1)/P) n β`** | **short n (< 80 KB) and P ≠ 2^k — optimal ∀P** | [thakur2005optimization, bruck1997efficient] |
| 28 | Allgather | neighbor exchange | **`(P/2) α + ((P−1)/P) n β`** | long n, even P, TCP/Ethernet | [chen2005performance] |
| 29 | Allgather | gather + bcast | `≈ 2⌈lg P⌉α + (⌈lg P⌉+1)nβ` | short n | [chan2007collective] |
| 30 | Scatter/Gather | **binomial tree (MST)** | **`⌈lg P⌉ α + ((P−1)/P) n β`** | **all n — optimal in both terms** | [chan2007collective, thakur2005optimization] |
| 31 | Scatter | simple / linear (SMPL) | `(P−1)α + ((P−1)/P) n β` | long n where sends overlap | [chan2007collective] |
| 32 | Alltoall | **pairwise exchange** | **`(P−1) α + n β`** | **long n (> 32 KB) — bw-optimal** | [thakur2005optimization] |
| 33 | Alltoall | **Bruck index, P = 2^k** | **`lg P α + (n/2) lg P β`** | **very short n (≤ 256 B/msg)** | [thakur2005optimization, bruck1997efficient] |
| 34 | Alltoall | Bruck index, P ≠ 2^k | **`⌈lg P⌉ α + ((n/2) lg P + (n/P)(P − 2^⌊lg P⌋)) β`** | very short n, P ≠ 2^k | [thakur2005optimization] |
| 35 | Alltoall | staggered isend/irecv | `≈ (P−1)α + nβ` (unscheduled) | medium n (256 B – 32 KB) | [thakur2005optimization] |
| 36 | Reduce-scatter | reduce + scatterv (old MPICH) | **`(lg P + P − 1)α + (lg P + (P−1)/P)nβ + n lg P γ`** | never | [thakur2005optimization] |
| 37 | Reduce-scatter | **recursive halving, P = 2^k** | **`lg P α + ((P−1)/P)nβ + ((P−1)/P)nγ`** | **commutative op, n < 512 KB — bw- & compute-optimal** | [thakur2005optimization] |
| 38 | Reduce-scatter | recursive halving, P ≠ 2^k | **`(⌊lg P⌋ + 2)α + 2nβ + n(1 + (P−1)/P)γ`** | commutative op, P ≠ 2^k | [thakur2005optimization] |
| 39 | Reduce-scatter | **recursive doubling (non-commutative)** | **`lg P α + n(lg P − (P−1)/P)β + n(lg P − (P−1)/P)γ`** | **non-commutative op, n < 512 B** | [thakur2005optimization] |
| 40 | Reduce-scatter | **pairwise exchange** | **`(P−1)α + ((P−1)/P)nβ + ((P−1)/P)nγ`** | **long n (≥ 512 KB); non-comm. op ≥ 512 B** | [thakur2005optimization, chan2007collective] |
| 41 | Scan/Exscan | recursive doubling (Kogge–Stone) | `⌈lg P⌉ (α + nβ + nγ)` [UNVERIFIED as quoted] | short n | — |
| 42 | Scan | two-tree | `nβ + 2α lg P + √(8αβn lg P)` | long n — best known for scan | [sanders2009twotree] |
| 43 | Scan (structure) | Kogge–Stone | depth `lg N`, fan-out 2, `N/2` tracks, work `O(N log N)` | depth-critical | [koggestone1973parallel, harris2003taxonomy] |
| 44 | Scan (structure) | Sklansky | depth `lg N`, fan-out `N/2+1`, work `O(N log N)` | hardware only (fan-out fatal in α/β model) | [sklansky1960conditional, harris2003taxonomy] |
| 45 | Scan (structure) | Brent–Kung | depth `2 lg N − 2`, fan-out 2, work `O(N)` | **work/γ-critical** | [brentkung1982regular, harris2003taxonomy] |

**Lower bounds for reference** [chan2007collective]: latency `⌈lg P⌉α` for all; bandwidth `nβ`
(bcast, reduce), `((P−1)/P)nβ` (scatter, gather, allgather, reduce-scatter), `2((P−1)/P)nβ`
(allreduce); computation `((P−1)/P)nγ` (all reductions).

---

## 9. Transfer to AgentMPI

### 9.0 The parameter regime, and why it inverts everything

In MPI, [chan2007collective] states the governing ratio: *"Typically, α is four to five orders of
magnitude greater than β where β is on the order of the cost of an instruction."* And γ is *also*
on the order of an instruction — so in MPI, **α ≫ nβ ≈ nγ** for all but the largest vectors, and
γ is never the term you optimize first. Every threshold in [thakur2005optimization] is a
statement about where `nβ` overtakes `α`.

AgentMPI reverses the ordering. With α = agent dispatch latency (~seconds), β = per-token cost
(tiny per token, but n is `10³–10⁵` tokens), and γ = an agent applying the reduction operator
(tens of seconds, *and* consuming irreplaceable context window):

**γ ≫ α ≫ β**, per unit, and per-collective **`n γ` ≫ `α` ≫ `n β`** for any n large enough to
be worth reducing.

This single change is responsible for almost every inversion below. Three consequences that have
no MPI analogue at all:

1. **γ is the term to minimize first.** MPI's entire threshold apparatus is "latency for short
   messages, bandwidth for long messages"; AgentMPI's is *"compute always, then latency."*
   Algorithms are ranked by their γ coefficient before anything else: `((P−1)/P)nγ` (optimal,
   Rabenseifner / recursive halving / pairwise exchange) beats `n lg P γ` (recursive doubling,
   binomial tree) at *every* message size, not just long ones.
2. **γ carries a capacity constraint, not just a time cost.** An agent that reduces an n-token
   vector spends n tokens of context permanently. So the γ coefficient is simultaneously a
   *latency* term and a *feasibility* term: an algorithm whose γ coefficient is `n lg P` may
   simply not fit. MPI has nothing like this — a CPU that reduces a vector twice is not degraded.
   This makes the γ-optimal algorithms *doubly* preferred.
3. **Total work matters, not only critical path.** Every dispatch costs money and tokens, so the
   HFM total-work-vs-critical-path distinction (§1.3) becomes first-order rather than an artifact
   of bus contention.

### 9.1 Barrier

Dissemination remains the critical-path winner at `⌈lg P⌉ α` with no non-power-of-two penalty,
and since a barrier carries no payload (n = 0, so β and γ vanish) it is the one collective whose
MPI ranking transfers almost unchanged. **But the ranking inverts if dispatch cost is charged per
message rather than per round:** dissemination performs `P⌈lg P⌉` total dispatches versus the
tournament's `P−1`, which is exactly the trade HFM measured on a contended bus and found favoured
the tournament beyond ~16 processes [hensgen1988two]. In an agent harness where every dispatch is
billed, AgentMPI should default to the tournament (or a binomial tree) for `P ≳ 16` and reserve
dissemination for latency-critical small-P barriers — reproducing HFM's *shared-medium* result
rather than MPICH's *switched-network* result.

### 9.2 Broadcast

The crossover moves *dramatically* toward the binomial tree. Van de Geijn's algorithm buys a
factor of `(lg P)/2` on the β term while *adding* `P−1` rounds of α [thakur2005optimization];
with α in seconds and β per-token negligible, that trade is almost never worth taking, so the
binomial tree's `⌈lg P⌉(α + nβ)` wins far past where MPICH's 12 KB threshold would put it.
**There is also a non-cost objection MPI does not face:** scatter+allgather requires splitting
the payload into P pieces and reassembling it, and a semantic artifact (a plan, a spec) is not
losslessly splittable the way a byte buffer is — the reassembly is itself a γ-cost operation.
Where n really is enormous (a large shared corpus), prefer the **two-tree** algorithm, whose
`nβ + 2α lg P + √(8αβn lg P)` [sanders2009twotree] keeps the log-P latency term that AgentMPI
actually cares about, unlike van de Geijn's `P−1` term or the chain's `P−2`.

### 9.3 Reduce and Allreduce — the biggest inversion

**Rabenseifner's algorithm wins at all message sizes, including short ones.** In MPI, recursive
doubling is the short-message allreduce winner (MPICH uses it below 32 bytes) and Rabenseifner
takes over only for long vectors [thakur2005optimization]. In AgentMPI the ranking inverts,
because recursive doubling's cost is `lg P α + n lg P β + n lg P γ`: every agent applies the
reduction operator to a *full* n-token vector in *every one* of `lg P` rounds. At γ = tens of
seconds and with each application burning context, that is `lg P` times the γ-optimal work and
`lg P` times the context consumption. Rabenseifner's `2 lg P α + 2((P−1)/P)nβ + ((P−1)/P)nγ`
[thakur2005optimization, rabenseifner2004optimization] pays one extra factor of 2 on α — the
cheap term — to reach the *optimal* γ coefficient `((P−1)/P)nγ ≈ nγ`, which is also the minimum
possible context burn. **The `lg P` factor MPI is willing to pay on γ for short messages is
exactly the factor AgentMPI cannot afford.**

**The ring allreduce loses, inverting deep-learning practice.** Its `2(P−1)α` latency term with
α in seconds is fatal: at P = 64 that is 126 sequential dispatches, versus 12 for Rabenseifner,
for *identical* bandwidth and compute terms [thakur2005optimization, patarasuk2009bandwidth].
The entire reason ring-allreduce won in DL is that gradient buffers are so large that `nβ`
swamps `Pα` [gibiansky2017bringing, sergeev2018horovod] — and in AgentMPI β is the *smallest*
term, so the premise fails. **AgentMPI should not copy the DL playbook here**; it should copy
MPICH's.

**Non-power-of-two handling becomes worth real engineering effort.** In MPI the 2-1 elimination
penalty is *"the data transfer overhead is doubled, and the computation overhead is increased by
3/2"* [thakur2005optimization] — and MPI mostly shrugs, because the β term it doubles is cheap.
For AgentMPI the `3/2 nγ` is the expensive half, so Rabenseifner & Träff's **3-2 elimination**
[rabenseifner2004more], which keeps `⌈lg P⌉` rounds for arbitrary P without doubling the volume,
graduates from a refinement to a requirement — agent counts are rarely powers of two.

### 9.4 Reduction reproducibility

MPI's recommendation — same result for *"the same arguments, appearing in the same order"*
[mpiforum2023mpi41] — is **unachievable in AgentMPI even in principle**, because an LLM operator
is nondeterministic at fixed input under sampling. So AgentMPI cannot inherit MPI's target; it
must weaken it to a distributional or semantic-equivalence criterion, and should adopt
[balaji2013reproducibility]'s explicit separation of *reproducibility* from *accuracy*: a
different reduction order can be strictly more accurate, and for agents that is more likely than
for floating point (reducing similar items together, or reducing short items first, genuinely
improves the output). Worse, an LLM `MPI_Op` is not even *assumed*-associative in the way the
standard demands: MPI's assumption is a harmless fiction for floating point and a load-bearing
falsehood for agents, so AgentMPI needs an operator contract that MPI's `MPI_Op` does not
provide — at minimum a declared-commutativity flag (which MPI does have) plus a declared
*associativity* flag (which MPI conspicuously does not). **Note the cost of losing
commutativity is already quantified in MPI**: reduce-scatter must fall back from recursive
halving's `((P−1)/P)nγ` to recursive doubling's `(lg P − (P−1)/P)nγ`, a factor of ~`lg P`
[thakur2005optimization] — the same `lg P` penalty as §9.3, now triggered by operator algebra
rather than message size.

Finally, MPI's own escape hatch — *"gathering all operands at a single MPI process ... applying
the reduction operation in the desired order ... and if needed, broadcast"* [mpiforum2023mpi41] —
is the natural "one strong agent reduces everything" design, and MPI already tells you its cost:
you pay the full undistributed `(P−1)nγ` on one process. **In AgentMPI this additionally hits a
hard wall MPI does not have**: `(P−1)n` tokens must fit in one context window, which caps P
outright. Sequential-order reduction is therefore feasible only for small P, and the context
window — not time — is what makes it infeasible.

### 9.5 Allgather, Gather, Scatter

Allgather has **no γ term**, so this is the family whose MPI ranking transfers most faithfully,
and the answer is unambiguous: use **Bruck** [bruck1997efficient, thakur2005optimization]. Its
`⌈lg P⌉ α + ((P−1)/P) n β` is simultaneously latency- and bandwidth-optimal for *arbitrary* P,
and the only price MPI pays for it — the final local memory permutation, which is why MPICH caps
Bruck at 80 KB — is *free* in AgentMPI, because reordering a list of retrieved items is a
mechanical harness-side operation, not an agent invocation. **The MPI ranking therefore inverts:
Bruck's disadvantage disappears entirely, so it wins at all message sizes and all P**, and both
the ring (`(P−1)α`) and neighbor exchange (`(P/2)α`) are strictly dominated because their
nearest-neighbor bandwidth advantage [thakur2005optimization, chen2005performance] has no agent
analogue unless co-location/KV-cache locality turns out to be real. Binomial gather and scatter
need no thought: `⌈lg P⌉α + ((P−1)/P)nβ` is optimal in both terms at every size
[chan2007collective], so one algorithm suffices — with the caveat that a *gather* concentrates
`((P−1)/P)n` tokens into the root's context, so the root's window, not its runtime, is the
binding constraint.

### 9.6 Alltoall

Alltoall is the collective AgentMPI should try hardest to avoid, and for a sharper reason than in
MPI: `Θ(P²)` distinct messages means `Θ(P²)` *billed* agent-to-agent transfers with no
exploitable redundancy (§6.3), so the cost is quadratic in money as well as in traffic. Between
the two algorithms the ranking **inverts relative to MPICH's 256-byte rule**: MPI prefers Bruck
for short messages because `lg P α` beats `(P−1)α` and the extra `(lg P)/2` data volume is cheap,
but Bruck is *store-and-forward* — intermediate agents must relay payloads they do not own, and
an agent relaying content is a γ-cost operation with paraphrase-drift risk, not a memcpy. Unless
the harness can relay opaque blobs *without* an agent in the loop (in which case Bruck's `lg P α`
is very attractive and its threshold should move way up), prefer **pairwise exchange**
`(P−1)α + nβ` [thakur2005optimization], which is bandwidth-optimal and moves every payload
exactly once, from author to reader, with no fidelity loss.

### 9.7 Scan / Exscan / Reduce_scatter

**Scan inverts from Kogge–Stone to Brent–Kung.** MPI implements scan by recursive doubling — the
Kogge–Stone structure — because in message passing the only cost that survives into the α/β/γ
model is depth, and Kogge–Stone has minimal depth `lg P` with constant fan-out
[koggestone1973parallel, harris2003taxonomy]. But Kogge–Stone's total operator applications are
`Θ(P log P)` and each application is a full-vector reduction, so its γ cost is `n lg P γ`.
**Brent–Kung** is *work*-optimal at `O(P)` applications for `2 lg P − 2` depth
[brentkung1982regular, harris2003taxonomy]: it doubles the α term and divides the γ term by
`lg P`, which is precisely the trade AgentMPI wants. **Sklansky stays ruled out** for the same
reason as in MPI, only worse — its `P/2 + 1` fan-out means one agent must dispatch to `P/2`
others in the final stage, and unlike a hardware gate driving capacitance, that serializes at α =
seconds. Note the caveat from [copik2017parallel]: minimum-depth prefix networks *provably* cannot
be work-optimal, so unlike allreduce (where Rabenseifner gets optimal β *and* γ at only 2× depth)
scan offers no free lunch, and the two-tree scan [sanders2009twotree] is the best published
compromise.

For **reduce_scatter**, use **recursive halving** (`lg P α + ((P−1)/P)nβ + ((P−1)/P)nγ`) whenever
the operator can be declared commutative, and note that MPI's *long*-message choice —
pairwise exchange, `(P−1)α + ((P−1)/P)nβ + ((P−1)/P)nγ` — has an *identical* γ coefficient and
worse latency, so unlike MPI (which switches to pairwise at 512 KB purely for nearest-neighbour
bandwidth effects that AgentMPI lacks) **AgentMPI should never switch**: recursive halving wins at
every size. Reduce_scatter is also the primitive worth exposing directly in an agent harness, more
so than in MPI: "each agent ends up owning the reduced result for its own slice" is both the
γ-optimal reduction shape and the natural way to keep each agent's context bounded to `n/P`
rather than `n`.

### 9.8 Summary of inversions relative to MPI

| Collective | MPI's choice | AgentMPI's choice | Cause of inversion |
|---|---|---|---|
| Barrier, P ≳ 16 | dissemination (`⌈lg P⌉α`) | tournament (`P−1` total msgs) | dispatches are billed ⇒ total work, not critical path |
| Broadcast | van de Geijn above 12 KB | binomial tree far longer; two-tree at the extreme | β negligible vs α ⇒ latency-optimal wins wider |
| Allreduce, short n | recursive doubling | **Rabenseifner** | γ dominates at all n; `n lg P γ` unaffordable + context burn |
| Allreduce, long n | ring (in DL practice) | **Rabenseifner** | `2(P−1)α` fatal at α ≈ seconds; DL's `nβ`-dominance premise fails |
| Allgather | Bruck only below 80 KB | **Bruck at all n, all P** | Bruck's only penalty (local permutation) is free in a harness |
| Alltoall, short n | Bruck index algorithm | pairwise exchange | store-and-forward relaying is a γ cost with fidelity risk |
| Scan | Kogge–Stone / recursive doubling | **Brent–Kung** | work-optimal beats depth-optimal once γ dominates |
| Reduce-scatter, long n | pairwise exchange | recursive halving (never switch) | nearest-neighbour bandwidth effect has no agent analogue |

**Where the ranking does *not* invert:** binomial gather/scatter remain optimal at all sizes; the
barrier critical-path answer remains dissemination when dispatches are not the binding cost; and
Rabenseifner's structural insight — *decompose the collective so that no participant ever
touches more than its share of the data* — becomes *more* correct in AgentMPI, not less, because
it now optimizes the dominant term (γ) and the binding capacity constraint (context) at the same
time, rather than merely the bandwidth term.

---

## BibTeX

Entries marked `% CHECK:` have one or more fields I could not verify against a primary source and
should be confirmed before submission.

```bibtex
@article{thakur2005optimization,
  author  = {Thakur, Rajeev and Rabenseifner, Rolf and Gropp, William},
  title   = {Optimization of Collective Communication Operations in {MPICH}},
  journal = {International Journal of High Performance Computing Applications},
  volume  = {19},
  number  = {1},
  pages   = {49--66},
  year    = {2005},
  doi     = {10.1177/1094342005051521},
  publisher = {SAGE Publications}
}

@article{chan2007collective,
  author  = {Chan, Ernie and Heimlich, Marcel and Purkayastha, Avi and van de Geijn, Robert},
  title   = {Collective communication: theory, practice, and experience},
  journal = {Concurrency and Computation: Practice and Experience},
  volume  = {19},
  number  = {13},
  pages   = {1749--1783},
  year    = {2007},
  doi     = {10.1002/cpe.1206},
  publisher = {John Wiley \& Sons}
}

@article{hensgen1988two,
  author  = {Hensgen, Debra and Finkel, Raphael and Manber, Udi},
  title   = {Two algorithms for barrier synchronization},
  journal = {International Journal of Parallel Programming},
  volume  = {17},
  number  = {1},
  pages   = {1--17},
  month   = feb,
  year    = {1988},
  doi     = {10.1007/BF01379320}
}

% CHECK: volume/pages taken from the zbMATH/MaRDI record of Hensgen et al., which cites
% "E. D. Brooks [ibid. 15, 295--307 (1986)]"; DOI not independently verified.
@article{brooks1986butterfly,
  author  = {Brooks, Eugene D.},
  title   = {The butterfly barrier},
  journal = {International Journal of Parallel Programming},
  volume  = {15},
  number  = {4},
  pages   = {295--307},
  year    = {1986}
}

@article{mellorcrummey1991algorithms,
  author  = {Mellor-Crummey, John M. and Scott, Michael L.},
  title   = {Algorithms for scalable synchronization on shared-memory multiprocessors},
  journal = {ACM Transactions on Computer Systems},
  volume  = {9},
  number  = {1},
  pages   = {21--65},
  year    = {1991},
  doi     = {10.1145/103727.103729}
}

@article{bruck1997efficient,
  author  = {Bruck, Jehoshua and Ho, Ching-Tien and Kipnis, Shlomo and Upfal, Eli
             and Weathersby, Derrick},
  title   = {Efficient algorithms for all-to-all communications in multiport
             message-passing systems},
  journal = {IEEE Transactions on Parallel and Distributed Systems},
  volume  = {8},
  number  = {11},
  pages   = {1143--1156},
  year    = {1997},
  doi     = {10.1109/71.642949}
}

% CHECK: pages and DOI unverified. This is reference [19] of thakur2005optimization
% ("a better algorithm exists, proposed by Rabenseifner"), which is the origin of the
% reduce-scatter + allgather allreduce decomposition.
@inproceedings{rabenseifner2004optimization,
  author    = {Rabenseifner, Rolf},
  title     = {Optimization of Collective Reduction Operations},
  booktitle = {Computational Science --- ICCS 2004},
  series    = {Lecture Notes in Computer Science},
  volume    = {3036},
  pages     = {1--9},
  year      = {2004},
  publisher = {Springer}
}

@inproceedings{rabenseifner2004more,
  author    = {Rabenseifner, Rolf and Tr{\"a}ff, Jesper Larsson},
  title     = {More Efficient Reduction Algorithms for Non-Power-of-Two Number of
               Processors in Message-Passing Parallel Systems},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing
               Interface, 11th European PVM/MPI Users' Group Meeting},
  editor    = {Kranzlm{\"u}ller, Dieter and Kacsuk, P{\'e}ter and Dongarra, Jack},
  series    = {Lecture Notes in Computer Science},
  volume    = {3241},
  pages     = {36--46},
  address   = {Budapest, Hungary},
  year      = {2004},
  publisher = {Springer}
}

% CHECK: venue and pages unverified. This is reference [20] of thakur2005optimization,
% the five-year Cray T3E 900 MPI profiling study that motivates the reduce/allreduce focus
% (">40% of MPI time in MPI_Allreduce and MPI_Reduce"; "25% of all execution time ...
% non-power-of-two number of processes").
@inproceedings{rabenseifner1999automatic,
  author    = {Rabenseifner, Rolf},
  title     = {Automatic {MPI} counter profiling of all users: First results on a
               {CRAY} {T3E} 900-512},
  booktitle = {Proceedings of the Message Passing Interface Developer's and User's
               Conference (MPIDC'99)},
  pages     = {77--85},
  year      = {1999}
}

@article{sanders2009twotree,
  author  = {Sanders, Peter and Speck, Jochen and Tr{\"a}ff, Jesper Larsson},
  title   = {Two-tree algorithms for full bandwidth broadcast, reduction and scan},
  journal = {Parallel Computing},
  volume  = {35},
  number  = {12},
  pages   = {581--594},
  year    = {2009},
  issn    = {0167-8191},
  doi     = {10.1016/j.parco.2009.09.001}
}

@inproceedings{sanders2006parallel,
  author    = {Sanders, Peter and Tr{\"a}ff, Jesper Larsson},
  title     = {Parallel Prefix (Scan) Algorithms for {MPI}},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing
               Interface, 13th European PVM/MPI Users' Group Meeting},
  series    = {Lecture Notes in Computer Science},
  volume    = {4192},
  pages     = {49--57},
  year      = {2006},
  publisher = {Springer},
  doi       = {10.1007/11846802_15}
}

% CHECK: volume, number, pages and DOI unverified; the paper is cited as the original
% proof that a ring-based schedule is bandwidth-optimal for allreduce.
@article{patarasuk2009bandwidth,
  author  = {Patarasuk, Pitch and Yuan, Xin},
  title   = {Bandwidth optimal all-reduce algorithms for clusters of workstations},
  journal = {Journal of Parallel and Distributed Computing},
  volume  = {69},
  number  = {2},
  pages   = {117--124},
  year    = {2009},
  doi     = {10.1016/j.jpdc.2008.09.002}
}

@misc{gibiansky2017bringing,
  author       = {Gibiansky, Andrew},
  title        = {Bringing {HPC} Techniques to Deep Learning},
  howpublished = {Baidu Research technical blog},
  year         = {2017},
  note         = {Introduces the \texttt{baidu-allreduce} library; reproduced at
                  \url{https://andrew.gibiansky.com/blog/machine-learning/baidu-allreduce/}}
}

@article{sergeev2018horovod,
  author  = {Sergeev, Alexander and Del Balso, Mike},
  title   = {Horovod: fast and easy distributed deep learning in {TensorFlow}},
  journal = {arXiv preprint arXiv:1802.05799},
  year    = {2018},
  eprint  = {1802.05799},
  archivePrefix = {arXiv},
  primaryClass  = {cs.LG}
}

% The MPI_REDUCE associativity/commutativity text and both Advice blocks quoted in Section 4
% are identical in MPI-3.1 (node111), MPI-4.1 and MPI-5.0 (node132). Cite whichever version
% the paper targets; MPI-3.1 is the version whose page 175 lines 9--13 balaji2013reproducibility
% quotes.
@techreport{mpiforum2023mpi41,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 4.1},
  institution = {University of Tennessee, Knoxville, TN, USA},
  year        = {2023},
  month       = nov,
  url         = {https://www.mpi-forum.org/docs/}
}

@techreport{mpiforum2015mpi31,
  author      = {{Message Passing Interface Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 3.1},
  institution = {University of Tennessee, Knoxville, TN, USA},
  year        = {2015},
  month       = jun,
  url         = {https://www.mpi-forum.org/docs/mpi-3.1/mpi31-report.pdf}
}

@inproceedings{balaji2013reproducibility,
  author    = {Balaji, Pavan and Kimpe, Dries},
  title     = {On the Reproducibility of {MPI} Reduction Operations},
  booktitle = {Proceedings of the 15th IEEE International Conference on High
               Performance Computing and Communications (HPCC)},
  year      = {2013},
  publisher = {IEEE}
}

% CHECK: author initials/given names unverified. Open MPI's source attributes the neighbor
% exchange allgather to "Chen et al." with exactly this title and venue; the author list here
% follows the HPCASIA'05 record and may contain name-resolution errors.
@inproceedings{chen2005performance,
  author    = {Chen, Jinzhong and Zhang, Linbo and Zhang, Yunquan and Yuan, Wei},
  title     = {Performance evaluation of Allgather algorithms on terascale Linux
               cluster with fast Ethernet},
  booktitle = {Proceedings of the Eighth International Conference on High-Performance
               Computing in Asia-Pacific Region (HPCASIA'05)},
  pages     = {437--442},
  year      = {2005},
  publisher = {IEEE},
  doi       = {10.1109/HPCASIA.2005.75}
}

@misc{loch2021sparbit,
  author        = {Loch, Wilton Jaciel and Koslovski, Guilherme Piegas},
  title         = {Sparbit: a new logarithmic-cost and data locality-aware
                   {MPI} {Allgather} algorithm},
  year          = {2021},
  eprint        = {2109.08751},
  archivePrefix = {arXiv},
  primaryClass  = {cs.DC},
  note          = {Cited here as a secondary source for the neighbor-exchange allgather
                   cost expression $C_{ne} = p\alpha/2 + (p-1)m\beta/p$}
}

@article{nuriyev2020accurate,
  author        = {Nuriyev, Emin and Lastovetsky, Alexey},
  title         = {Accurate runtime selection of optimal {MPI} collective algorithms
                   using analytical performance modelling},
  year          = {2020},
  eprint        = {2004.11062},
  archivePrefix = {arXiv},
  primaryClass  = {cs.DC},
  note          = {Source of the segmented chain-tree broadcast cost
                   $(P + n_s - 2)(\alpha + (m/n_s)\beta)$}
}

% CHECK: pages unverified.
@inproceedings{harris2003taxonomy,
  author    = {Harris, David},
  title     = {A Taxonomy of Parallel Prefix Networks},
  booktitle = {Proceedings of the 37th Asilomar Conference on Signals, Systems
               and Computers},
  volume    = {2},
  pages     = {2213--2217},
  year      = {2003},
  publisher = {IEEE},
  doi       = {10.1109/ACSSC.2003.1292373}
}

@article{koggestone1973parallel,
  author  = {Kogge, Peter M. and Stone, Harold S.},
  title   = {A Parallel Algorithm for the Efficient Solution of a General Class of
             Recurrence Equations},
  journal = {IEEE Transactions on Computers},
  volume  = {C-22},
  number  = {8},
  pages   = {786--793},
  year    = {1973},
  doi     = {10.1109/TC.1973.5009159}
}

@article{sklansky1960conditional,
  author  = {Sklansky, Jack},
  title   = {Conditional-Sum Addition Logic},
  journal = {IRE Transactions on Electronic Computers},
  volume  = {EC-9},
  number  = {2},
  pages   = {226--231},
  year    = {1960},
  doi     = {10.1109/TEC.1960.5219822}
}

@article{brentkung1982regular,
  author  = {Brent, Richard P. and Kung, H. T.},
  title   = {A Regular Layout for Parallel Adders},
  journal = {IEEE Transactions on Computers},
  volume  = {C-31},
  number  = {3},
  pages   = {260--264},
  year    = {1982},
  doi     = {10.1109/TC.1982.1675982}
}

% CHECK: this is a Master's thesis, cited only for the statement that minimum-depth prefix
% circuits cannot be work-optimal (zero-deficiency lower bound). Prefer citing the primary
% source for that bound if one is needed.
@mastersthesis{copik2017parallel,
  author  = {Copik, Marcin},
  title   = {Parallel Prefix Algorithms for the Registration of Arbitrarily Long
             Electron Micrograph Series},
  school  = {RWTH Aachen University, Aachen Institute for Advanced Study in
             Computational Engineering Science},
  year    = {2017}
}

% CHECK: full author list and pages unverified. This is reference [4] of
% thakur2005optimization ("Benson et al. studied the performance of the allgather operation
% in MPICH on Myrinet and TCP networks and developed a dissemination allgather based on the
% dissemination barrier algorithm").
@inproceedings{benson2003dissemination,
  author    = {Benson, Gregory D. and Chu, Cho-Wai and Huang, Qing and Caglar, Sadik G.},
  title     = {A Comparison of {MPICH} Allgather Algorithms on Switched Networks},
  booktitle = {Recent Advances in Parallel Virtual Machine and Message Passing
               Interface, 10th European PVM/MPI Users' Group Meeting},
  series    = {Lecture Notes in Computer Science},
  volume    = {2840},
  pages     = {335--343},
  year      = {2003},
  publisher = {Springer}
}
```

### Citation keys produced

`thakur2005optimization`, `chan2007collective`, `hensgen1988two`, `brooks1986butterfly`,
`mellorcrummey1991algorithms`, `bruck1997efficient`, `rabenseifner2004optimization`,
`rabenseifner2004more`, `rabenseifner1999automatic`, `sanders2009twotree`,
`sanders2006parallel`, `patarasuk2009bandwidth`, `gibiansky2017bringing`,
`sergeev2018horovod`, `mpiforum2023mpi41`, `mpiforum2015mpi31`, `balaji2013reproducibility`,
`chen2005performance`, `loch2021sparbit`, `nuriyev2020accurate`, `harris2003taxonomy`,
`koggestone1973parallel`, `sklansky1960conditional`, `brentkung1982regular`,
`copik2017parallel`, `benson2003dissemination`
