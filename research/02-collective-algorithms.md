# MPI Collective Communication: Semantics, Algorithms, and Cost Models

Research notes for **AgentMPI**. Purpose: establish, with citation-level precision, what MPI's
collective layer actually guarantees, how it is implemented, and what it costs — so that the
analogy we draw to multi-agent LLM systems is load-bearing rather than decorative.

Facts that I could not confirm against a primary source are marked `[UNVERIFIED]`.

---

## 0. Notation and the cost models

### 0.1 The Hockney (alpha–beta) model

Every quantitative statement below, unless noted, is in the model used by Thakur, Rabenseifner and
Gropp [ThakurRabenseifnerGropp05], which they attribute to Hockney [Hockney94] and to van de Geijn
and collaborators. The time to send a message of \(n\) bytes between any two nodes is

\[
T_{p2p}(n) \;=\; \alpha + n\beta
\]

where \(\alpha\) is the per-message startup latency (independent of size) and \(\beta\) is the
per-byte transfer time (the reciprocal of bandwidth). For reduction operations a third parameter
\(\gamma\) is the per-byte cost of applying the operator locally.

The model's standing assumptions, stated explicitly in [ThakurRabenseifnerGropp05, §3], matter a
great deal when we transfer it:

1. Time is independent of *how many* pairs are communicating concurrently (no congestion term).
2. Time is independent of the *distance* between communicating nodes (flat topology).
3. Links are bidirectional: a message costs the same in both directions simultaneously.
4. Each node's interface is **single-ported**: at most one send and one receive at a time.

For the finer analysis of reduction operations, [ThakurRabenseifnerGropp05, §3] refines this into
bidirectional cost \(\alpha + n\beta\) versus unidirectional cost
\(\alpha_{uni} + n\beta_{uni}\), with ratios \(f_\alpha = \alpha_{uni}/\alpha\) and
\(f_\beta = \beta_{uni}/\beta\), "normally in the range 0.5 (simplex network) to 1.0 (full-duplex
network)."

Chan, Heimlich, Purkayastha and van de Geijn [Chan07] further split the latency term by protocol,
writing \(\alpha_1\) for a short (eager, statically buffered) message and \(\alpha_3\) for a long
(rendezvous, three-phase) message, and assume \(\alpha_3 = 3\alpha_1\). This distinction is the
formal home of the eager/rendezvous subtlety discussed in §7.

### 0.2 LogP and LogGP

The Hockney model cannot express overlap of communication and computation, because it charges the
processor for the whole transfer. LogP [Culler93] separates four parameters:

- \(L\): upper bound on **latency** in the network (processor free);
- \(o\): **overhead**, the time the host CPU is occupied injecting or extracting a message
  (processor busy — this is the part that cannot be overlapped);
- \(g\): the **gap**, minimum interval between consecutive message injections (\(g \ge o\));
- \(P\): number of processors.

Sending a message of \(s\) data units costs \(T^{LogP}(s) = 2o + L + (s-1)g\).

LogGP [Alexandrov95] adds \(G\), the **gap per byte** for long messages, giving
\(T^{LogGP}(s) = 2o + L + (s-1)G\). The point of \(G\) is that real networks achieve much higher
bandwidth on bulk transfers than the fixed-packet \(g\) implies. Hoefler et al.'s DSDE analysis
[Hoefler10] uses LogGP and derives, for broadcasting a small message to \(P\) processes,

\[
\log_2(P)\cdot o \;\le\; T_{BC}(P) \;\le\; \log_2(P)\cdot (L + 2o),
\qquad T_{BC}(P) = \Theta(\log P).
\]

and for a personalized census (MPI_Reduce_scatter of small data),

\[
T_{RS}(P) \;\ge\; G(P-1) + (L + 2o - G)\cdot\lceil \log_2 P\rceil, \qquad T_{RS}(P) = \Theta(P).
\]

The \(o\) vs. \(L\) split is exactly what makes nonblocking collectives (§5) analyzable: only the
\(o\) terms are unavoidably serialized with the application.

### 0.3 Lower bounds

Chan et al. [Chan07, Table 1] give the following lower bounds under their model (\(n\) is the total
vector length; conditions in the text apply):

| Operation | Latency | Bandwidth | Computation |
|---|---|---|---|
| Broadcast | \(\lceil\lg p\rceil\alpha\) | \(n\beta\) | — |
| Reduce(-to-one) | \(\lceil\lg p\rceil\alpha\) | \(n\beta\) | \(\frac{p-1}{p}n\gamma\) |
| Scatter | \(\lceil\lg p\rceil\alpha\) | \(\frac{p-1}{p}n\beta\) | — |
| Gather | \(\lceil\lg p\rceil\alpha\) | \(\frac{p-1}{p}n\beta\) | — |
| Allgather | \(\lceil\lg p\rceil\alpha\) | \(\frac{p-1}{p}n\beta\) | — |
| Reduce-scatter | \(\lceil\lg p\rceil\alpha\) | \(\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) |
| Allreduce | \(\lceil\lg p\rceil\alpha\) | \(2\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) |

The latency bound follows from the observation that in one step a node can at best double the set
of nodes holding some datum. The allreduce bandwidth bound of \(2\frac{p-1}{p}n\beta\) is the
theoretical justification for Rabenseifner's reduce-scatter + allgather structure: it is the only
family of algorithms that attains it. Patarasuk and Yuan [PatarasukYuan09] independently derive a
tight lower bound on the *amount of data* each node must send for allreduce and show the ring
algorithm attains it.

---

## 1. The collective taxonomy and exact semantics

MPI's collectives divide into three functional classes (the classification used in the standard's
collective chapter [MPI41] and in Hoefler's tutorials [HoeflerAdvMPI]): **synchronization**
(Barrier), **data movement** (Bcast, Gather(v), Scatter(v), Allgather(v), Alltoall(v/w)), and
**collective computation** (Reduce, Allreduce, Reduce_scatter(_block), Scan, Exscan).

An orthogonal axis is **rootedness**. Rooted collectives name a distinguished rank in the `root`
argument and are asymmetric in their argument structure: the non-root ranks typically ignore some
buffer arguments entirely. Non-rooted collectives are symmetric.

### 1.1 The critical synchronization caveat

This is the single most-misunderstood property of the interface, and it is the property I most want
to preserve in AgentMPI. The MPI standard says [MPI31, §5.1]:

> Collective operations can (but are not required to) complete as soon as the caller's participation
> in the collective communication is finished. ... The completion of a collective operation indicates
> that the caller is free to modify locations in the communication buffer. It does not indicate that
> other processes in the group have completed or even started the operation (unless otherwise
> implied by the description of the operation). Thus, a collective communication operation may, or
> may not, have the effect of synchronizing all calling processes. This statement excludes, of
> course, the barrier operation.

And, as an advice to users:

> It is dangerous to rely on synchronization side-effects of the collective operations for program
> correctness. ... On the other hand, a correct, portable program must allow for the fact that a
> collective call *may* be synchronizing.

So the contract is genuinely two-sided and both sides bite:

- **You may not assume synchronization.** A `MPI_Bcast` on a binomial tree lets the root return
  after \(\lceil\lg p\rceil\) sends; leaf ranks may not have entered the call. Code that follows a
  broadcast with `MPI_Rsend` (ready-send, which requires the matching receive to be already posted)
  is non-portable [Dongarra95].
- **You may not assume non-synchronization.** An implementation is free to insert a barrier, and
  some do for large messages to avoid overrunning receive buffers. A program that depends on the
  root racing ahead is equally broken.

Only `MPI_Barrier` is *guaranteed* to synchronize. Even then, the guarantee is process
synchronization, not memory synchronization: the standard's RMA chapter states that
"MPI_BARRIER provides process synchronization, but not memory synchronization," which is why
shared-memory windows need `MPI_Win_sync`.

In practice a useful heuristic is that the *all-* variants (Allgather, Allreduce, Alltoall) are
much more likely to be de facto synchronizing than the rooted ones, because every rank's output
depends on every rank's input — but this is a statement about algorithms, not about semantics, and
must never be relied upon.

### 1.2 Operation-by-operation

**`MPI_Barrier(comm)`** — Rooted: no. Data: none. Semantics: the call returns at a rank only after
all ranks in `comm` have entered it. This is the only collective with a synchronization guarantee.
It carries zero bytes, so its cost is pure \(\alpha\).

**`MPI_Bcast(buffer, count, datatype, root, comm)`** — Rooted: yes. One-to-all. On entry the root's
`buffer` holds `count` elements; on exit every rank's `buffer` holds a copy. Note the *single*
buffer argument: it is `IN` at the root and `OUT` elsewhere, an asymmetry that has no analogue in
the non-rooted calls. All ranks must supply the same `root` and a compatible type signature.

**`MPI_Gather(sendbuf, sendcount, sendtype, recvbuf, recvcount, recvtype, root, comm)`** — Rooted:
yes. All-to-one. Each rank contributes `sendcount` elements; the root receives them concatenated in
rank order into `recvbuf`. Crucially, `recvcount` is the count received *from each single rank*, not
the total — a perennial bug. `recvbuf`, `recvcount`, `recvtype` are only meaningful at the root.
**`MPI_Gatherv`** generalizes this by replacing `recvcount` with an array `recvcounts[p]` and adding
`displs[p]`, so contributions may be of differing sizes and placed at arbitrary (possibly
non-contiguous, possibly overlapping-free) offsets. Only the root needs valid `recvcounts`/`displs`.

**`MPI_Scatter` / `MPI_Scatterv`** — Rooted: yes. One-to-all, the exact dual of Gather: the root's
`sendbuf` is cut into \(p\) contiguous chunks and chunk \(i\) is delivered to rank \(i\).
`sendcount` is per-destination. `Scatterv` adds `sendcounts[]`/`displs[]`.

**`MPI_Allgather` / `MPI_Allgatherv`** — Rooted: no. All-to-all broadcast (Bruck et al. call this
**concatenation** [Bruck97]). Semantically `Gather` followed by `Bcast`, but implementations do far
better. Every rank ends with the full concatenation in rank order.

**`MPI_Alltoall`** — Rooted: no. All-to-all *personalized* exchange (Bruck et al.'s **index**
operation [Bruck97]). The standard states it exactly [MPI41, §6.5]: "The j-th block sent from MPI
process i is received by MPI process j and is placed in the i-th block of recvbuf." This is a
distributed matrix transpose. **`MPI_Alltoallv`** allows per-peer counts and displacements.
**`MPI_Alltoallw`** is the fully general form: per-peer counts, per-peer *byte* displacements, and
per-peer *datatypes*, which makes it the only collective in which the type map may differ per
communication partner.

**`MPI_Reduce(sendbuf, recvbuf, count, datatype, op, root, comm)`** — Rooted: yes. All-to-one
computation. Elementwise: \(\text{recvbuf}[k] = \bigoplus_{i=0}^{p-1} \text{sendbuf}_i[k]\), result
at the root only. `recvbuf` is only significant at the root.

**`MPI_Allreduce`** — Rooted: no. As Reduce, but the result is delivered to every rank. This is the
single most important collective in practice: a five-year profiling study on the Cray T3E at
Stuttgart found "more than 40% of the time spent in MPI functions was spent in the two functions
MPI Allreduce and MPI Reduce" [ThakurRabenseifnerGropp05, §1, citing Rabenseifner's profiling work].

**`MPI_Reduce_scatter(sendbuf, recvbuf, recvcounts, datatype, op, comm)`** — Rooted: no. Performs an
elementwise reduction over the whole vector, then scatters the result: rank \(i\) receives
`recvcounts[i]` elements of the reduced vector, at the offset implied by the prefix sum of
`recvcounts`. [ThakurRabenseifnerGropp05, §4.4] describes it as "an irregular primitive: the scatter
in it is a scatterv." **`MPI_Reduce_scatter_block`** (MPI-2.2) is the regular case where every rank
receives the same count. Hoefler et al. call this operation a **personalized census** [Hoefler10].

**`MPI_Scan(sendbuf, recvbuf, count, datatype, op, comm)`** — Rooted: no. **Inclusive** prefix
reduction: rank \(i\) receives \(\bigoplus_{j=0}^{i} \text{sendbuf}_j\).

**`MPI_Exscan`** — **Exclusive** prefix reduction: rank \(i\) receives
\(\bigoplus_{j=0}^{i-1} \text{sendbuf}_j\). The value in `recvbuf` at rank 0 is *undefined* (and
with `MPI_IN_PLACE` must not be changed). Scan and Exscan are the only collectives whose result is
rank-dependent in a way that makes non-commutative operators genuinely natural — the segmented scan
in the Open MPI `MPI_Scan` man page is the canonical example of a deliberately non-commutative
user-defined operator.

---

## 2. Reduction operators

### 2.1 Predefined operations

The standard defines twelve [MPI50, §7.9.2]: `MPI_MAX`, `MPI_MIN`, `MPI_SUM`, `MPI_PROD`,
`MPI_LAND`, `MPI_BAND`, `MPI_LOR`, `MPI_BOR`, `MPI_LXOR`, `MPI_BXOR`, `MPI_MAXLOC`, `MPI_MINLOC`.
Each is valid only on specified datatype groups: MAX/MIN on C integer, Fortran integer, floating
point and multi-language types (notably *not* complex, since there is no total order); SUM/PROD
additionally on complex; L\* on C integer and logical types; B\* on C integer, Fortran integer and
`MPI_BYTE`.

`MPI_MAXLOC` and `MPI_MINLOC` are argmax/argmin: they operate on value–index pairs
(`MPI_DOUBLE_INT`, `MPI_2INT`, …) and return both the extremum and the rank (or user-supplied index)
where it occurred. They are associative and commutative provided a deterministic tie-break rule is
used — MPI specifies that on ties the *smaller index* wins, which is what makes the operator
well-defined.

**All predefined operations are assumed commutative** [MPI50, §7.9.1].

### 2.2 User-defined operations and the `commute` flag

`MPI_Op_create(user_fn, commute, op)` binds a callback of signature
`void f(void *invec, void *inoutvec, int *len, MPI_Datatype *dtype)` computing
`inoutvec[i] = invec[i] ⊕ inoutvec[i]`. Note that `inoutvec` is the *right-hand* operand, which is
what lets a non-commutative operator be written correctly.

The `commute` flag is not decoration; it selects the algorithm. Quoting the standard [MPI50, §7.9.5]:

> The user-defined operation is assumed to be associative. If commute = true, then the operation
> should be both commutative and associative. If commute = false, then the order of operands is
> fixed and is defined to be in ascending, process rank order, beginning with MPI process with rank
> 0 in the communicator comm. The order of evaluation can be changed, taking advantage of the
> associativity of the operation. If commute = true then the order of evaluation can be changed,
> taking advantage of commutativity and associativity.

You can watch this decision being made in MPICH's source. In
`MPIR_Allreduce_intra_recursive_doubling`, each exchange step contains:

```
if (is_commutative || (dst < rank)) {
    MPIR_Reduce_local(tmp_buf, recvbuf, ...);   /* op is commutative OR order is already right */
} else {
    MPIR_Reduce_local(recvbuf, tmp_buf, ...);   /* op noncommutative and order is not right */
}
```

That is: recursive doubling *can* support a non-commutative operator, at the price of swapping
operand order on half the exchanges. The bandwidth-optimal reduce-scatter algorithms cannot: a
recursive-*halving* reduce-scatter combines partial results from disjoint rank ranges out of
canonical order, so [ThakurRabenseifnerGropp05, §4.4] falls back to a recursive-*doubling*
reduce-scatter for non-commutative operators, paying
\(n(\lg p - \frac{p-1}{p})\beta\) instead of \(\frac{p-1}{p}n\beta\) — a factor of roughly
\(\lg p\) in bandwidth.

There is a second, subtler reason MPICH refuses to use reduce-scatter for user-defined operators,
documented in the source header: user-defined ops permit *derived datatypes*, and "breaking up
derived datatypes to do the reduce-scatter is tricky." So `MPI_Op_create` with any `commute` value
forces the latency-optimal, bandwidth-suboptimal path in MPICH regardless of message size.

### 2.3 Non-determinism and the absence of bitwise reproducibility

MPI is explicit that it does not promise reproducibility [MPI50, §7.9.1]:

> The "canonical" evaluation order of a reduction is determined by the ranks of the MPI processes in
> the group. However, the implementation can take advantage of associativity, or associativity and
> commutativity in order to change the order of evaluation. This may change the result of the
> reduction for operations that are not strictly associative and commutative, such as floating point
> addition.

There is only an *advice to implementors* that the same arguments in the same order should give the
same result — which, as the advice itself notes, "may prevent optimizations that take advantage of
the physical location of ranks."

The mechanism of the non-determinism is well analyzed by Balaji and Kimpe [BalajiKimpe13]: floating
point addition loses precision when combining values of widely differing magnitude, and different
reduction trees combine values in different magnitude orders. As process counts grow, the number of
orderings the implementation may choose grows too, so the *variance* of the result grows with scale.

Applications that need reproducibility must impose order themselves. The standard's own advice:
gather all operands to one rank with `MPI_Gather`, apply `MPI_Reduce_local` in the required order,
and broadcast the answer. That trades \(\Theta(\log p)\) for \(\Theta(p)\) work at one rank — the
canonical determinism/scalability trade.

**This is the hinge of the AgentMPI analogy.** MPI already contains a fully worked-out theory of a
reduction operator that is *nominally* associative, *not* bitwise deterministic, and whose
non-determinism is a function of the algorithm the runtime chose. An LLM "summarize these \(k\)
artifacts" operator is the same object with the error bars widened by orders of magnitude: it is not
even approximately associative, it is not deterministic under a fixed order, and its output *type*
(a summary) differs from its input type (documents) in a way \(\mathbb{R}\to\mathbb{R}\) reductions
never do. See §10.

---

## 3. Algorithms and their alpha–beta costs

Throughout, \(p\) is the number of processes and \(n\) is the message size *per the convention of
the operation*: for Bcast/Reduce/Allreduce it is the vector length; for Allgather it is the total
data gathered on each process; for Alltoall it is the total data sent (or received) by each process.

### 3.1 Barrier

| Algorithm | Rounds | Cost | Notes |
|---|---|---|---|
| Linear / flat | 2 rounds, \(2(p-1)\) messages at root | \(\approx 2(p-1)\alpha\) at the root | gather-then-broadcast of zero bytes |
| Binomial tree | \(2\lceil\lg p\rceil\) | \(2\lceil\lg p\rceil\alpha\) | up-phase reduce, down-phase bcast |
| Recursive doubling (butterfly) | \(\lg p\) | \(\lg p\,\alpha\) | exact only for \(p = 2^k\) |
| Dissemination [Hensgen88] | \(\lceil\lg p\rceil\) | \(\lceil\lg p\rceil\alpha\) | correct for *all* \(p\) |

The **butterfly barrier** (Brooks) has each process synchronize pairwise with the peer at distance
\(2^k\) in round \(k\), i.e. rank \(i \leftrightarrow i \oplus 2^k\). For non-power-of-two \(p\) this
requires existing processes to stand in for missing ones, and Hensgen, Finkel and Manber
[Hensgen88] show it can take up to \(2\lceil\lg N\rceil\) stages in that case (a later analysis
tightens the bound to \(\lceil\lg N\rceil+1\) [Gupta/network-model]).

The **dissemination barrier** [Hensgen88] fixes this by making the synchronization non-pairwise: in
round \(k\), process \(i\) signals process \((i + 2^k) \bmod p\) and waits on
\((i - 2^k) \bmod p\). Because the pattern is a directed cycle rather than a matching, no
stand-ins are needed and the critical path is exactly \(\lceil\lg p\rceil\) rounds "regardless of
P" [MellorCrummeyScott91]. Hensgen et al.'s companion **tournament barrier** uses only \(O(p)\)
total messages versus dissemination's \(O(p\lg p)\), which matters on a shared bus but not on a
switched network. LibNBC implements `MPI_Ibarrier` with the dissemination algorithm
[HoeflerLibNBCDesign].

Note the deep connection: Bruck's allgather is described by [ThakurRabenseifnerGropp05, §4.1.2] as
"a variant of the dissemination algorithm for barrier." A barrier is an allgather of zero bytes.

### 3.2 Broadcast

**Binomial tree.** The root sends to rank \(root + p/2\); both then recurse in their own subtrees.
Every step moves the full \(n\) bytes.

\[
T_{\text{tree}} = \lceil\lg p\rceil\,(\alpha + n\beta)
\]

```
Binomial tree broadcast, p = 8, root = 0
(each arrow is one step; the label is the step number)

step 1:  P0 ──────────────────────────────▶ P4
step 2:  P0 ──────────▶ P2      P4 ──────────▶ P6
step 3:  P0 ──▶ P1  P2 ──▶ P3   P4 ──▶ P5  P6 ──▶ P7

time ─────────────────────────────────────────────────▶
        step 1        step 2        step 3
P0  [D] ──send──▶ [D] ──send──▶ [D] ──send──▶ [D]
P4      ◀──recv── [D] ──send──▶ [D] ──send──▶ [D]
P2                    ◀──recv── [D] ──send──▶ [D]
P6                    ◀──recv── [D] ──send──▶ [D]
P1                                  ◀──recv── [D]
P3                                  ◀──recv── [D]
P5                                  ◀──recv── [D]
P7                                  ◀──recv── [D]

#active senders doubles each step: 1, 2, 4.  Total steps = lg 8 = 3.
```

The latency term \(\lceil\lg p\rceil\alpha\) meets the lower bound. The bandwidth term
\(\lceil\lg p\rceil n\beta\) is a factor \(\lg p\) above the \(n\beta\) lower bound, because the
data crosses \(\lg p\) levels of the tree serially.

**Linear / flat tree.** The root sends \(p-1\) times: \((p-1)(\alpha + n\beta)\). Optimal only for
tiny \(p\), and MPICH indeed keeps binomial tree for \(p < 8\).

**Scatter + allgather (van de Geijn).** Split the message into \(p\) pieces, `Scatter` them, then
`Allgather`. The scatter costs \(\lg p\,\alpha + \frac{p-1}{p}n\beta\) via a binomial tree; the
allgather costs \((p-1)\alpha + \frac{p-1}{p}n\beta\) via a ring. Hence
[ThakurRabenseifnerGropp05, §4.2]:

\[
T_{\text{vandegeijn}} = (\lg p + p - 1)\alpha + 2\frac{p-1}{p}n\beta
\]

Comparing bandwidth terms, this beats binomial tree whenever \(\lg p > 2\), i.e. \(p > 4\), and the
maximum speedup is \((\lg p)/2\). MPICH uses binomial tree for messages \(< 12\) KB or \(p < 8\),
and van de Geijn otherwise.

**Pipelined / chain broadcast.** Arrange the \(p\) nodes in a chain and cut the message into \(k\)
segments of \(n/k\) bytes. The last segment reaches the last node after \(k + p - 2\) steps:

\[
T_{\text{pipe}}(k) = (k + p - 2)\left(\alpha + \tfrac{n}{k}\beta\right)
\]

Minimizing over \(k\) gives \(k^\star = \sqrt{\dfrac{n\beta(p-2)}{\alpha}}\) and

\[
T_{\text{pipe}}^\star = n\beta + (p-2)\alpha + 2\sqrt{(p-2)\,\alpha\, n\beta}
\]

so for \(n\) large the bandwidth term reaches the \(n\beta\) lower bound. The same segmentation
trick applied to a binary tree gives \(\approx (k + \lceil\lg p\rceil - 1)(\alpha + \frac{n}{k}\beta)\)
[UNVERIFIED — this is the standard textbook form; I have not tied it to a specific primary source].

**Double / two-tree broadcast (Sanders, Speck, Träff).** [SandersSpeckTraff09] construct *two*
binary trees that both span the whole machine, arranged so that the interior nodes of one tree are
the leaves of the other. Half the packets are pipelined down tree A and half down tree B. Because
each PE is interior in exactly one tree, it can send and receive simultaneously without conflict,
and the schedule is a bipartite edge-coloring solvable locally in \(O(\log p)\) time. The result:
"each tree communicates as efficiently as a single tree with exclusive use of the network. Our
algorithms thus achieve up to *twice* the bandwidth of most previous algorithms." The same idea
applies to reduction and scan, where the authors report it "beats all previous algorithms." The
exact closed-form cost constant is `[UNVERIFIED]` — I have the abstract's qualitative claim but not
the paper's own formula.

### 3.3 Reduce

**Binomial tree** — the dual of binomial-tree broadcast, with a local reduction at each combine:

\[
T_{\text{tree}} = \lceil\lg p\rceil(\alpha + n\beta + n\gamma)
\]

**Rabenseifner's algorithm** [Rabenseifner04] — reduce-scatter (recursive halving) followed by a
binomial-tree gather to the root. The insight, per [ThakurRabenseifnerGropp05, §4.5], is exactly the
dual of van de Geijn's for broadcast: it "has the same effect of reducing the bandwidth term from
\(n\lg p\,\beta\) to \(2n\beta\)".

\[
T_{\text{rabenseifner}}^{\text{reduce}} = 2\lg p\,\alpha + 2\frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma
\]

MPICH uses Rabenseifner for predefined ops on messages \(> 2\) KB and binomial tree otherwise (and
always binomial tree for user-defined ops, per §2.2).

**Pipelined reduce** is the reverse of pipelined broadcast and has the same \(k\)-segment cost
structure, with an added \(n\gamma\).

### 3.4 Allreduce

**Recursive doubling.** In step \(k = 0,\dots,\lg p - 1\), rank \(i\) exchanges its *entire* current
accumulator with rank \(i \oplus 2^k\) and reduces locally. After \(\lg p\) steps every rank has the
full result.

\[
T_{\text{rec\_dbl}} = \lg p\,\alpha + n\lg p\,\beta + n\lg p\,\gamma
\]

```
Recursive doubling allreduce, p = 8.  Each rank's box shows the SET of
rank-contributions currently folded into its accumulator.

           P0      P1      P2      P3      P4      P5      P6      P7
init      {0}     {1}     {2}     {3}     {4}     {5}     {6}     {7}
           │╲     ╱│       │╲     ╱│       │╲     ╱│       │╲     ╱│
step 0     │ ╲   ╱ │       │ ╲   ╱ │       │ ╲   ╱ │       │ ╲   ╱ │      distance 1
(xor 1)    │  ╳    │       │  ╳    │       │  ╳    │       │  ╳    │      exchange n bytes
           │ ╱   ╲ │       │ ╱   ╲ │       │ ╱   ╲ │       │ ╱   ╲ │
          {01}   {01}    {23}   {23}     {45}   {45}     {67}   {67}
            │ ╲       ╱    │       │  ╲      ╱    │
step 1      │   ╲   ╱      │       │    ╲  ╱      │                        distance 2
(xor 2)     │     ╳        │       │      ╳       │                        exchange n bytes
            │   ╱   ╲      │       │    ╱  ╲      │
        {0123} {0123}  {0123} {0123}  {4567}{4567} {4567}{4567}
            │        ╲                    ╱   │
step 2      │           ╲              ╱      │                            distance 4
(xor 4)     │              ╲        ╱         │                            exchange n bytes
            │                 ╳╳╳╳            │
      {01234567} ... all eight ranks hold {01234567}

3 steps = lg 8.  Every step moves the FULL n bytes -> bandwidth term n*lg(p)*beta.
```

Note the pattern is a hypercube; Chan et al. call it **bidirectional exchange (BDE)** [Chan07].
Its latency term is optimal; its bandwidth term is a factor \(\approx \frac{p\lg p}{2(p-1)}\) above
the lower bound.

**Non-power-of-two handling.** MPICH reduces to \(p' = 2^{\lfloor\lg p\rfloor}\) with
\(r = p - p'\): the first \(2r\) ranks pair up, each even rank ships its whole vector to its odd
neighbor, which reduces and then participates with a renumbered rank; at the end the odd ranks ship
the result back. Cost: \(+2\alpha + 2n\beta + n\gamma\) roughly, and the \(r\) removed ranks idle
through the core.

**Rabenseifner's algorithm** [Rabenseifner04] — recursive-halving reduce-scatter followed by
recursive-doubling allgather. Reduce-scatter costs
\(\lg p\,\alpha + \frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma\); allgather costs
\(\lg p\,\alpha + \frac{p-1}{p}n\beta\). Total:

\[
T_{\text{rabenseifner}} = 2\lg p\,\alpha + 2\frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma
\]

which meets the Chan et al. bandwidth and computation lower bounds and is within a factor 2 of the
latency bound. MPICH's own source comment gives the non-power-of-two cost as

\[
T = (2\lfloor\lg p\rfloor + 2)\alpha + \left(2\tfrac{p-1}{p} + 2\right)n\beta + \left(1 + \tfrac{p-1}{p}\right)n\gamma
\]

i.e. roughly \(4n\beta\) and \(1.5n\gamma\) — the non-power-of-two penalty doubles the bandwidth
term and adds 50% to computation, which is exactly the imbalance the **binary blocks** algorithm
[Rabenseifner04] attacks.

**Binary blocks.** Decompose \(p\) into its binary representation, e.g.
\(100 = 2^6 + 2^5 + 2^2\). Each block runs its own recursive-halving reduce-scatter, then blocks are
merged from smallest to largest. The load imbalance is governed by
\(\delta_{expo,max}\), the largest gap between consecutive exponents (for \(p=100\),
\(\max(6-5, 5-2) = 3\)). [ThakurRabenseifnerGropp05, §5.4] report the empirical rule on a Cray T3E
900: binary blocks wins if
\(\delta_{expo,max} < \lg(\text{vector length in bytes})/2.0 - 2.5\) with vector \(\ge 16\) KB and
\(p > 32\). Rabenseifner and Träff [RabenseifnerTraff04] later added **3-to-2** and **2-to-1**
elimination steps to shrink an arbitrary \(p\) to a power of two with better balance, achieving
optimal latency for \(p = 2^n\) and \(p = 2^n\cdot 3\).

**Ring allreduce.** Reduce-scatter around a logical ring (\(p-1\) steps, each moving \(n/p\) bytes
and doing \(n/p\) reduction work), then allgather around the ring (\(p-1\) more steps of \(n/p\)):

\[
T_{\text{ring}} = 2(p-1)\alpha + 2\frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma
\]

```
Ring allreduce, p = 4.  Vector is split into 4 chunks A,B,C,D.
Phase 1: reduce-scatter, p-1 = 3 steps.  Rank i sends chunk (i-step) mod p rightward.

              P0            P1            P2            P3
initial    A0 B0 C0 D0   A1 B1 C1 D1   A2 B2 C2 D2   A3 B3 C3 D3

step 1     send A0 ──▶    send B1 ──▶   send C2 ──▶   send D3 ──▶ (wraps to P0)
           recv D3        recv A0       recv B1       recv C2
           A0 B0 C0 D03  A01 B1 C1 D1  A2 B12 C2 D2  A3 B3 C23 D3

step 2     send D03 ──▶  send A01 ──▶  send B12 ──▶  send C23 ──▶
           A0 B0 C0 D03  A01 B1 C1 D013 A012 B12 C2 D2 A3 B3 C23 D023...
           (each rank now owns a 3-way partial for one chunk)

step 3     one more rotation:
           P0 owns A0123   P1 owns B0123   P2 owns C0123   P3 owns D0123
           -> reduce-scatter complete; each rank owns 1/p of the FULL result.

Phase 2: allgather, p-1 = 3 more steps, same rotation, no reduction, just copy.
           after 3 steps every rank holds A0123 B0123 C0123 D0123.

Total 2(p-1) = 6 steps, each moving n/p bytes => 2*(p-1)/p*n*beta.  Bandwidth-optimal.
```

Patarasuk and Yuan [PatarasukYuan09] prove this is bandwidth-optimal — each node sends the minimum
possible volume — and, crucially, that a *contention-free* logical ring can be embedded in any tree
topology, which the butterfly (recursive-halving) pattern cannot do without contention on
SMP/multi-core clusters. This result is the direct ancestor of Baidu's ring-allreduce for deep
learning [Gibiansky17] and thence of Horovod [SergeevDelBalso18], which cites Patarasuk and Yuan by
name. The ring's weakness is the \(2(p-1)\alpha\) latency term: the PICO study [PICO25] observes
that ring allreduce dominates for buffers \(>64\) MiB up to several hundred nodes but is overtaken
once the process count exceeds roughly 512 nodes on Leonardo / 256 on LUMI, "even for very large
buffers."

#### Allreduce algorithm comparison

\(n\) = vector length in bytes; \(p\) = process count. Power-of-two \(p\) assumed unless noted.

| Algorithm | Steps | Latency term | Bandwidth term | Computation term | Best regime |
|---|---|---|---|---|---|
| Reduce + Bcast (binomial, naive) | \(2\lceil\lg p\rceil\) | \(2\lceil\lg p\rceil\alpha\) | \(2\lceil\lg p\rceil n\beta\) | \(\lceil\lg p\rceil n\gamma\) | never optimal; historical MPICH default |
| Recursive doubling (BDE) | \(\lg p\) | \(\lg p\,\alpha\) | \(n\lg p\,\beta\) | \(n\lg p\,\gamma\) | short messages; **required** for user-defined ops in MPICH |
| Rabenseifner (RS+AG), \(p=2^k\) | \(2\lg p\) | \(2\lg p\,\alpha\) | \(2\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) | long messages, power-of-two \(p\) |
| Rabenseifner, \(p \ne 2^k\) | \(2\lfloor\lg p\rfloor + 2\) | \((2\lfloor\lg p\rfloor+2)\alpha\) | \((2\frac{p-1}{p}+2)n\beta\) | \((1+\frac{p-1}{p})n\gamma\) | long messages, awkward \(p\) |
| Binary blocks | \(\approx 2\lg p\) + merge | data-dependent | \(\approx 2n\beta\) when \(\delta_{expo,max}\) small | \(\approx n\gamma\) | \(p\ne 2^k\), \(n \ge 16\)KB, \(p>32\), small \(\delta_{expo,max}\) |
| Ring (RS+AG on a ring) | \(2(p-1)\) | \(2(p-1)\alpha\) | \(2\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) | very long messages, modest \(p\); contention-free on trees |
| Two-tree | \(O(\lg p)\) + pipeline depth | \(O(\lg p)\alpha\) `[UNVERIFIED constant]` | \(\approx n\beta\) (full bandwidth) | \(\approx n\gamma\) | long messages, bidirectional links |
| Lower bound [Chan07] | — | \(\lceil\lg p\rceil\alpha\) | \(2\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) | — |

The **short-vs-long crossover**. Equate recursive doubling with Rabenseifner (power-of-two,
ignoring \(\gamma\)):

\[
\lg p\,\alpha + n\lg p\,\beta \;=\; 2\lg p\,\alpha + 2\tfrac{p-1}{p}n\beta
\quad\Longrightarrow\quad
n^\star \;=\; \frac{\lg p\;\alpha}{\beta\left(\lg p - 2\frac{p-1}{p}\right)}
\]

so for \(p = 1024\), \(n^\star \approx \frac{10\alpha}{8\beta} = 1.25\,\alpha/\beta\). With a typical
\(\alpha/\beta\) ratio of \(10^4\)–\(10^5\) bytes this puts the crossover in the tens of kilobytes —
consistent with the empirical thresholds reported in [ThakurRabenseifnerGropp05, §5.4]: on a Cray
T3E 900, recursive doubling wins for \(\le 32\) bytes, vendor/binomial for \(\le 1\) KB, and the
bandwidth-optimal family thereafter.

### 3.5 Allgather

**Ring.** \(p-1\) steps, each forwarding \(n/p\) bytes to the right neighbor:

\[
T_{\text{ring}} = (p-1)\alpha + \tfrac{p-1}{p}n\beta
\]

The bandwidth term is optimal. [ThakurRabenseifnerGropp05, §4.1.3] measured that for long messages
the ring beats recursive doubling despite identical bandwidth terms, because "it uses a
nearest-neighbor communication pattern, whereas in recursive doubling, processes that are much
farther apart communicate" — they verified with the b_eff benchmark that nearest-neighbor patterns
achieve more than twice the bandwidth of some other patterns.

**Recursive doubling.** \(\lg p\) steps; in step \(k\), partners at distance \(2^k\) exchange
everything they hold (\(2^k n/p\) bytes each):

\[
T_{\text{rec\_dbl}} = \lg p\,\alpha + \tfrac{p-1}{p}n\beta
\]

Optimal in *both* terms for power-of-two \(p\). For non-power-of-two \(p\) MPICH must add extra
logarithmic fix-up communication within each non-power-of-two exchanging set, bounding the step
count at \(2\lfloor\lg p\rfloor\).

**Bruck** [Bruck97]. A modification of the dissemination barrier pattern: in step \(k\), process
\(i\) sends to \((i - 2^k)\) and receives from \((i + 2^k)\) — note the *reversed* direction relative
to dissemination, which is precisely what keeps every send contiguous. Each process appends what it
receives to the end of what it already has. After \(\lfloor\lg p\rfloor\) steps (plus one partial
step of \(p - 2^{\lfloor\lg p\rfloor}\) blocks if \(p\) is not a power of two) every process has all
the data, rotated "up" by \(i\) blocks; a local downward shift fixes the order.

\[
T_{\text{bruck}} = \lceil\lg p\rceil\alpha + \tfrac{p-1}{p}n\beta
\]

```
Bruck allgather, p = 6.  Each column is a process's output buffer, top to bottom.
Digits are the ORIGIN rank of each block.

  initial            after step 0 (send to i-1)   after step 1 (send to i-2)
P0 P1 P2 P3 P4 P5     P0 P1 P2 P3 P4 P5            P0 P1 P2 P3 P4 P5
 0  1  2  3  4  5      0  1  2  3  4  5             0  1  2  3  4  5
                       1  2  3  4  5  0             1  2  3  4  5  0
                                                    2  3  4  5  0  1
                                                    3  4  5  0  1  2

  after step 2 (partial: send first p - 2^floor(lg p) = 6-4 = 2 blocks, to i-4)
P0 P1 P2 P3 P4 P5            after LOCAL downward shift by i blocks
 0  1  2  3  4  5             P0 P1 P2 P3 P4 P5
 1  2  3  4  5  0              0  0  0  0  0  0
 2  3  4  5  0  1              1  1  1  1  1  1
 3  4  5  0  1  2              2  2  2  2  2  2
 4  5  0  1  2  3              3  3  3  3  3  3
 5  0  1  2  3  4              4  4  4  4  4  4
                               5  5  5  5  5  5

All communication is CONTIGUOUS at every step -- that is the whole trick.
ceil(lg 6) = 3 communication steps + 1 local permutation.
```

Bruck's advantage is that it takes \(\lceil\lg p\rceil\) steps *for all* \(p\), where recursive
doubling degrades to \(2\lfloor\lg p\rfloor\). Its cost is the final local memory permutation, which
becomes significant as \(n\) grows. MPICH's selection: Bruck for short messages (\(<80\) KB total
gathered) and non-power-of-two \(p\); recursive doubling for power-of-two \(p\) and \(<512\) KB;
ring otherwise.

### 3.6 Alltoall

**Pairwise exchange.** \(p-1\) steps; in step \(k\), rank \(i\) exchanges directly with
\(i \oplus k\) (power-of-two \(p\)) or sends to \(i+k\) and receives from \(i-k\) (general \(p\)).
No store-and-forward, so no extra bytes:

\[
T_{\text{pairwise}} = (p-1)\alpha + n\beta
\]

**Bruck index algorithm** [Bruck97]. Rotate the local blocks up by \(i\); then in step
\(k = 0,\dots,\lceil\lg p\rceil - 1\), send to rank \((i + 2^k)\) exactly those blocks whose \(k\)-th
bit is 1 and store the incoming blocks in the same positions; finish with a local inverse rotation.
For power-of-two \(p\), each step moves \(n/2\) bytes:

\[
T_{\text{bruck}} = \lg p\,\alpha + \tfrac{n}{2}\lg p\,\beta
\]

and for non-power-of-two \(p\),

\[
T_{\text{bruck}} = \lceil\lg p\rceil\alpha + \left(\tfrac{n}{2}\lg p + \tfrac{n}{p}\left(p - 2^{\lfloor\lg p\rfloor}\right)\right)\beta
\]

[ThakurRabenseifnerGropp05, §4.3] describe the trade-off exactly: Bruck buys \(\lceil\lg p\rceil\)
steps instead of \(p-1\) "at the expense of some extra data communication (\(\frac{n}{2}\lg p\,\beta\)
instead of \(n\beta\))." The extra factor is \(\frac{\lg p}{2}\), so the algorithm is worth it only
when \(\alpha\) dominates. They note its elegance: "it is a logarithmic algorithm for short-message
all-to-all that does not need any extra bookkeeping or control information for routing the right
data to the right process — that is taken care of by the mathematics of the algorithm."

MPICH's four-way selection: Bruck for \(\le 256\) bytes per destination; irecv–isend with a
rank-offset schedule (destination \((rank + i) \bmod p\), to avoid every rank hammering rank 0
first) for 256 B–32 KB; pairwise exchange for long messages.

### 3.7 Reduce-scatter

**Recursive halving** (commutative ops). Step 1: exchange with the peer at distance \(p/2\), sending
the half of the vector the *other* half of the machine needs, receiving the half you need, and
reducing. Halve the data and double the distance each step.

\[
T_{\text{rec\_half}} = \lg p\,\alpha + \tfrac{p-1}{p}n\beta + \tfrac{p-1}{p}n\gamma
\]

Non-power-of-two: \((\lfloor\lg p\rfloor + 2)\alpha + 2n\beta + n(1 + \frac{p-1}{p})\gamma\)
(approximate — some ranks do their neighbors' work too).

**Recursive doubling** (non-commutative ops), which cannot fold out-of-order:

\[
T_{\text{short}} = \lg p\,\alpha + n\left(\lg p - \tfrac{p-1}{p}\right)\beta + n\left(\lg p - \tfrac{p-1}{p}\right)\gamma
\]

**Pairwise exchange** (long messages): \(p-1\) steps, only \(n/p\) bytes each:

\[
T_{\text{long}} = (p-1)\alpha + \tfrac{p-1}{p}n\beta + \tfrac{p-1}{p}n\gamma
\]

Same bandwidth as recursive halving but chosen anyway for long messages, for the same
nearest-neighbor-bandwidth reason as ring allgather.

**The old MPICH algorithm** (reduce to rank 0 + linear scatterv) cost
\((\lg p + p - 1)\alpha + (\lg p + \frac{p-1}{p})n\beta + n\lg p\,\gamma\) — worse in every term,
which is why it was replaced.

### 3.8 Scatter and Gather

Binomial trees in both directions. For scatter, at each level the sender keeps half the data and
forwards the other half; the data volume halves each step:

\[
T_{\text{MST-Scatter}} = \lceil\lg p\rceil\alpha + \tfrac{p-1}{p}n\beta
\]

and identically for gather by reversing the communications [Chan07, §5]. Both meet the latency
*and* bandwidth lower bounds, making scatter/gather the two operations for which a single algorithm
is optimal for all vector lengths.

### 3.9 Scan and Exscan

**Recursive doubling (Hillis–Steele).** \(\lg p\) steps; in step \(k\), rank \(j\) exchanges its
current partial with \(j \oplus 2^k\), accumulating the received partial into its running scan only
if the partner is *below* it, and always into a separate "partial_scan" accumulator that will be
forwarded onward. MVAPICH2's `exscan.c` gives the algorithm verbatim; the essential control is:

```
if (rank > dst) { partial_scan = tmp_buf + partial_scan;  recv_buf += tmp_buf; }
else            { partial_scan = tmp_buf + partial_scan; }   /* not added to result */
```

Cost, roughly:

\[
T_{\text{scan,rec\_dbl}} \;\approx\; \lg p\,\alpha + n\lg p\,\beta + n\lg p\,\gamma
\]

This is *step-efficient* (\(O(\log p)\) depth) but *work-inefficient*: total operations are
\(O(p\log p)\) rather than \(O(p)\).

**Binomial-tree / Blelloch scan.** Two phases, each \(\lg p\) steps: an **up-sweep** in which rank
\(j\) with \(j \wedge (2^{k+1}-1) = 2^{k+1}-1\) receives a partial from \(j - 2^k\), building a
reduction tree; then a **down-sweep** in which partials flow back down so each rank ends with its
prefix. Depth \(2\lg p\), total work \(O(p)\). The classic work-efficiency/step-efficiency trade:
Hillis–Steele minimizes rounds, Blelloch minimizes total operations. In a bandwidth- or
compute-bound regime you want Blelloch; in a latency-bound regime, Hillis–Steele.

Sanders, Speck and Träff [SandersSpeckTraff09] apply the two-tree idea to scan as well — the two
trees have different roots and work independently, and the required communications "resemble a
reduction followed by a broadcast."

---

## 4. Selection logic: the tunable decision function

No single algorithm wins. This is the central empirical result of
[ThakurRabenseifnerGropp05]: "to achieve the best performance for a collective communication
operation, one needs to use a number of different algorithms and select the right algorithm for a
particular message size and number of processes."

The **decision function** is the abstraction: a map

\[
\text{algo} \;=\; f(\text{collective}, \; |{\rm comm}|, \; n, \; \text{op properties}, \; \text{topology}, \; \text{hardware})
\]

evaluated at (or before) each call. The inputs that matter in practice:

1. **Message size thresholds.** The dominant input. Small \(n\) ⇒ minimize the \(\alpha\)
   coefficient; large \(n\) ⇒ minimize the \(\beta\) coefficient. MPICH's published cutoffs (circa
   the paper) include: broadcast switches to van de Geijn at 12 KB; allgather switches Bruck →
   recursive doubling → ring at 80 KB and 512 KB; alltoall switches Bruck → irecv/isend → pairwise
   at 256 B and 32 KB; reduce switches binomial → Rabenseifner at 2 KB; reduce-scatter switches
   recursive halving → pairwise at 512 KB.
2. **Communicator size thresholds.** Broadcast stays binomial for \(p < 8\) regardless of size,
   because van de Geijn only wins for \(p > 4\).
3. **Power-of-two special cases.** Recursive doubling and recursive halving are natural only for
   \(p = 2^k\); Bruck and dissemination are uniform in \(p\). Binary blocks exists purely to reclaim
   the non-power-of-two case.
4. **Operator properties.** Commutativity and predefined-vs-user-defined gate whole algorithm
   families (§2.2).
5. **Hardware collectives / offload.** If the fabric can do it, do not do it in software. NVIDIA
   (Mellanox) SHARP performs reductions inside InfiniBand switch ASICs on in-network aggregation
   trees, so "data from each source is injected into the network only once, and the volume of data
   is reduced as it goes towards the root" [Graham20]. The original SHArP paper [Graham16] reports
   an 8-byte `MPI_Allreduce` on 128 hosts improving 2.1× (6.01 → 2.83 µs) and a 4096-byte one
   improving 3.24× (46.93 → 14.48 µs). SHARP currently supports allreduce, barrier and broadcast
   but not reduce, so MVAPICH2-X implements `MPI_Reduce` on top of the SHARP allreduce primitive by
   ignoring the receive buffer at non-roots [Frontera-SHARP].

**Implementation mechanisms.**

- **Open MPI** `coll/tuned` supports a *fixed decision* mode and a *dynamic decision* mode. In
  dynamic mode the user supplies a rules file: "The rules file effectively defines, for one or more
  collectives, a function of two variables, which given communicator and message size, returns an
  algorithm id to use for the call." Communicator size is matched at communicator-construction time;
  message size is matched per call, using "the rule with the nearest (less than) matching message
  size." Enabled with `--mca coll_tuned_use_dynamic_rules 1 --mca coll_tuned_dynamic_rules_filename
  <path>`. Collectives are numbered (Allgather=0, Allreduce=2, Alltoall=3, Barrier=6, Bcast=7,
  Exscan=8, Gather=9, Reduce=11, Reduce_scatter=12, Reduce_scatter_block=13, Scan=14, Scatter=15),
  and each entry carries `(msg_size, algorithm_id, topo_faninout, segment_size)`. A rule for message
  size 0 must exist or the component falls back to fixed rules. Missing collectives silently use
  fixed rules.
- **MPICH** exposes per-collective control variables (cvars) such as
  `MPIR_CVAR_ALLREDUCE_INTRA_ALGORITHM` with values including `auto`, `recursive_doubling`,
  `reduce_scatter_allgather`, `smp`, `nb`; and `MPIR_CVAR_REDUCE_INTRA_ALGORITHM` with `binomial`,
  `reduce_scatter_gather`, `smp`, `nb`. Above these sits a **collective selection framework**: a
  JSON decision tree supplied via `MPIR_CVAR_COLL_SELECTION_TUNING_JSON_FILE`, parsed at `MPI_Init`
  and applied library-wide. The ACCLAiM autotuner generates such files by profiling.

The conceptual point for AgentMPI: the *interface* is a semantic contract (what the operation
computes), and the *implementation* is a runtime-selected schedule chosen by a cost model
parameterized by machine constants. Callers never name an algorithm. That separation is what makes
MPI programs portable across three decades of hardware, and it is the property I most want the
AgentMPI spec to have.

---

## 5. Nonblocking, persistent, and neighborhood collectives

### 5.1 Nonblocking collectives (MPI-3)

Every collective has an `I`-prefixed variant — `MPI_Ibarrier`, `MPI_Ibcast`, `MPI_Ireduce`,
`MPI_Iallreduce`, `MPI_Ialltoallv`, … — which returns an `MPI_Request` immediately and is completed
by the usual `MPI_Wait`/`MPI_Test` family. The design and first portable implementation are
Hoefler, Lumsdaine and Rehm's LibNBC [HoeflerSC07], which layers a schedule-driven engine on top of
MPI-1 point-to-point.

Why they matter:

- **Overlap.** The LogP decomposition says only the \(o\) terms occupy the CPU; the \(L\) terms are
  free time. A blocking collective forfeits them. [HoeflerSC07] shows large-message collectives on
  large communicators can be efficiently overlapped on InfiniBand.
- **Pipelining across iterations.** Start iteration \(k\)'s allreduce, compute on iteration \(k\)'s
  independent work, complete. Hoefler et al. demonstrated this for a conjugate gradient solver.
- **Semantic split.** This is the deeper reason, and the one Hoefler emphasizes: the *entry* into a
  globally synchronizing operation becomes a local, non-collective act, while the *completion*
  remains collective. `MPI_Ibarrier` means "I have entered the barrier"; the synchronization happens
  later and asynchronously. §7.3 shows what that buys.

**The hard rules** [MPI31, §5.12]:

> Unlike point-to-point operations, nonblocking collective operations do not match with blocking
> collective operations, and collective operations do not have a tag argument. All processes must
> call collective operations (blocking and nonblocking) in the same order per communicator. In
> particular, once a process calls a collective operation, all other processes in the communicator
> must eventually call the same collective operation, and no other collective operation with the
> same communicator in between.

The stated rationale is twofold: the implementation may use *different algorithms* for the blocking
and nonblocking cases ("Blocking collective operations may be optimized for minimal time to
completion, while nonblocking collective operations may balance time to completion with CPU overhead
and asynchronous progression"), and "the use of tags for collective operations can prevent certain
hardware optimizations."

Consequences: matching is **positional**, by order of invocation on a communicator. Since there are
no tags, the *communicator itself is the tag*. `MPI_Cancel` and `MPI_Request_free` are not permitted
on nonblocking collective requests. `MPI_SOURCE` and `MPI_TAG` in the returned status are undefined.
Two threads may not invoke a collective on the same communicator concurrently. And blocking and
nonblocking collectives *can* be interleaved on the same communicator as long as the global order
agrees — a program where rank 0 does `Ibarrier; Barrier` and rank 1 does `Barrier; Ibarrier` is
erroneous, and the standard's advice is to use distinct duplicated communicators when different
orders are genuinely needed. Nonblocking collectives also enable simultaneous progress on multiple
*overlapping* communicators, for which there provably exists no deadlock-free blocking schedule.

### 5.2 Persistent collectives (MPI-4)

`MPI_Allreduce_init(..., info, request)`, `MPI_Bcast_init(...)`, etc. produce a request that is
*initialized once and started many times*. The lifecycle is `Create (Start Complete)* Free`.

Semantics [MPI41, §6.12]:

- The request is **inactive** after initialization; `MPI_Start`/`MPI_Startall` makes it active;
  completion makes it inactive again; `MPI_Request_free` destroys it.
- "Once initialized, persistent collective operations can be started in any order and the order can
  differ among the MPI processes in the communicator." (The `_init` calls themselves must follow the
  usual same-order rule.)
- "Persistent collective operations cannot be matched with blocking or nonblocking collective
  operations."
- Input arrays (counts, displacements, datatypes) must not be modified until the request is freed.

The point is **early binding**. The standard's advice to implementors: an implementation "should do
no worse than duplicating the communicator during the initialization function, caching the input
arguments, and calling the appropriate nonblocking collective function," but "high-quality
implementations should be able to amortize setup costs and further optimize by taking advantage of
early-binding, such as efficient and effective pre-allocation of certain resources and algorithm
selection." Concretely: run the decision function once, allocate scratch buffers once, program the
NIC's triggered-operation schedule once, then replay it every iteration.

### 5.3 Neighborhood collectives (MPI-3)

`MPI_Neighbor_allgather`, `MPI_Neighbor_allgatherv`, `MPI_Neighbor_alltoall`, `_alltoallv`,
`_alltoallw`, plus `I`- and `_init` variants. These run over a communicator that carries a **virtual
topology**, and each process communicates only with its topological neighbors. They are still
collective — every rank in the communicator must call, including ranks with no neighbors.

The neighbor set and, critically, the *buffer ordering* are defined by the topology
[MPI31, §7.6]:

- **Cartesian** (`MPI_Cart_create`): "the sequence of neighbors in the send and receive buffers at
  each process is defined by order of the dimensions, first the neighbor in the negative direction
  and then in the positive direction with displacement 1. The numbers of sources and destinations in
  the communication routines are `2*ndims`." At a non-periodic boundary the missing neighbor is
  `MPI_PROC_NULL`: "the buffer is still part of the sequence of neighbors but it is neither
  communicated nor updated" — a hole in the buffer, not a shortened buffer. This is exactly
  `MPI_Cart_shift` with `disp = 1` in each dimension.
- **Distributed graph** (`MPI_Dist_graph_create`, `MPI_Dist_graph_create_adjacent`): the sequence is
  whatever `MPI_Dist_graph_neighbors` returns for destinations and sources respectively. The graph
  is **directed**, so `indegree` and `outdegree` may differ.

`MPI_Dist_graph_create_adjacent(comm_old, indegree, sources, sourceweights, outdegree,
destinations, destweights, info, reorder, comm_dist_graph)` is the *scalable* constructor: each
process specifies exactly its own in- and out-edges, so each edge is specified twice (once at each
endpoint) and no process ever holds the global graph. It also guarantees the neighbor ordering the
caller supplied, which `MPI_Dist_graph_create` does not.

The rationale the standard gives is precisely the one AgentMPI should care about: "This high-level
specification of data exchange among neighboring processes enables optimizations in the MPI library
because the communication pattern is known statically (the topology). Thus, the implementation can
compute optimized message schedules during creation of the topology." Declaring the topology up
front lets the runtime plan.

---

## 6. Communicators, groups, and topologies

### 6.1 What a communicator is

A communicator binds together (a) a **group** — an ordered set of processes with ranks
\(0..p-1\) — and (b) a **context**, an opaque tag space. Two messages sent on different
communicators can never match, even with identical source, destination and tag. This is what makes
library composition safe: a library that does `MPI_Comm_dup(user_comm, &lib_comm)` and communicates
only on `lib_comm` cannot have its messages intercepted by, or intercept, the user's.

### 6.2 The context ID mechanism

Contexts are implemented as integer IDs carried in the message envelope and required to match. The
hard part is allocating them *consistently and collectively* without a central authority. MPICH's
scheme [MPICH-ctxid, BalajiCtxID12]:

- Each process keeps a **context ID mask**: a bit vector (in MPICH, `MPIR_MAX_CONTEXT_MASK` = 64
  32-bit words = 2048 bits) where bit \(n\) set means "context ID \(n\) is free *here*."
- To allocate, the participating processes copy their local mask and perform an
  `MPI_Allreduce` with `MPI_BAND` over the communicator. The bitwise AND yields the set of IDs free
  *everywhere*; all processes then deterministically pick the first set bit. Every process picks the
  same ID because they all see the same reduction result.
- Freeing sets the bit back. Reuse is delayed, because "reusing context ids can lead to a race
  condition if (as is desirable) `MPI_Comm_free` does not include a barrier" — a late message on the
  old communicator could match a new one.

For **inter-communicators**, two IDs are needed: `recvcontext_id` allocated from the local group's
pool, `context_id` from the remote group's. Each side allocates its own `recvcontext_id`, the two
roots exchange, and each broadcasts the received value into its local group as the send context. The
comment in `commutil.c` — "the send context ID was allocated out of the remote group's bit vector,
not ours" — is the whole design in one line.

Multithreaded allocation is genuinely subtle. [BalajiCtxID12]: only one thread per process may hold
the mask, but a thread that cannot get it "must still participate in the allreduce to prevent
another thread in its communicator creation operation from blocking indefinitely while holding the
mask," contributing an all-zeroes vector to abort the attempt. Livelock is avoided by prioritizing
the thread whose parent communicator has the lowest context ID. There is also an **eager**
allocation protocol reserving part of the space for fast local allocation.

The AgentMPI-relevant abstraction: *a communicator is a capability*. Holding it grants the right to
address a specific group in a specific isolated namespace, and the namespace was established by a
collective agreement protocol.

### 6.3 Constructors

**`MPI_Comm_split(comm, color, key, newcomm)`** [MPI50, §7.4.2]:

> This function partitions the group associated with comm into disjoint subgroups, one for each
> value of color. Each subgroup contains all MPI processes of the same color. Within each subgroup,
> the MPI processes are ranked in the order defined by the value of the argument key, with ties
> broken according to their rank in the old group. ... An MPI process may supply the color value
> MPI_UNDEFINED, in which case newcomm returns MPI_COMM_NULL. This is a collective call, but each
> MPI process is permitted to provide different values for color and key.

Notes: `color` must be non-negative or `MPI_UNDEFINED` (the restriction exists so it cannot collide
with `MPI_UNDEFINED`). Keys need not be unique; setting all keys equal preserves parent rank order.
No cached attributes and no topology information propagate to `newcomm`. On an inter-communicator,
processes on the left with the same color as processes on the right combine into a new
inter-communicator; a color present on only one side yields `MPI_COMM_NULL`.

`MPI_Comm_create(comm, group, newcomm)` is the special case where members supply their group number
as color and their group rank as key, and non-members supply `MPI_UNDEFINED`.

**`MPI_Comm_split_type(comm, split_type, key, info, newcomm)`** splits by a *property of the
process* rather than a user-chosen color. `MPI_COMM_TYPE_SHARED` (MPI-3) groups processes that
"are part of the same shared memory domain and can create a shared memory segment (e.g., with a
successful call to `MPI_WIN_ALLOCATE_SHARED`)" — the standard idiom for discovering node boundaries.
MPI-4 adds `MPI_COMM_TYPE_HW_GUIDED` with the `mpi_hw_resource_type` info key (values like
`"NUMANode"`, `"Package"`, `"L3Cache"`, and the reserved `"mpi_shared_memory"`) and
`MPI_COMM_TYPE_HW_UNGUIDED` for automatic discovery of a strict sub-communicator. A caveat worth
remembering: since processes may migrate, a `MPI_COMM_TYPE_SHARED` communicator built early "may
not reflect an actual ability to share memory between MPI processes after this change."

**`MPI_Comm_dup(comm, newcomm)`** produces a communicator with the same group and a fresh context,
copying cached attributes. `MPI_Comm_idup` is the nonblocking form (essential for libraries that
must not introduce a synchronization point). `MPI_Comm_dup_with_info` re-evaluates hints.

**Group set operations** are purely local — no communication — and operate on ordered sets:
`MPI_Group_union` (first group's order, then the second's non-duplicates),
`MPI_Group_intersection`, `MPI_Group_difference`, plus `MPI_Group_incl`/`excl` and the ranged
`_range_incl`/`_range_excl`, and `MPI_Group_translate_ranks` to map ranks between groups. Because
they are local, the standard pattern is: build the group you want locally, then make one collective
call to turn it into a communicator.

### 6.4 Intra- vs inter-communicators

An **intra-communicator** has one group; ranks address each other within it. An
**inter-communicator** binds two disjoint groups — a *local* group (containing the caller) and a
*remote* group — and every communication crosses the boundary.

Inter-communicator collectives [MPI41, §6.2.2–6.2.3]. The standard reclassifies each collective as
All-To-All, All-To-One, One-To-All or Other, and then:

> if the operation is in the All-To-One or One-To-All categories, then the transfer is
> unidirectional. The direction of the transfer is indicated by a special value of the root
> argument. ... the root process uses the special root value MPI_ROOT; all other processes in the
> same group as the root use MPI_PROC_NULL. All processes in the other group ... must call the
> collective routine and provide the rank of the root. If the operation is in the All-To-All
> category, then the transfer is bidirectional.

So `MPI_Bcast` on an inter-communicator sends from one member of group A to *all of group B*
(nobody in A other than the root receives anything). `MPI_Allgather` collects from all of one group
into all of the other. `MPI_Alltoall` is symmetric and full-duplex: "each MPI process in group A
sends a message to each MPI process in group B, and vice versa," with the \(j\)-th send buffer of
process \(i\) in A matching the \(i\)-th receive buffer of process \(j\) in B. `MPI_IN_PLACE` does
not apply, "since in the inter-communicator case there is no communication from a process to
itself."

### 6.5 Virtual topologies

`MPI_Cart_create(comm_old, ndims, dims, periods, reorder, comm_cart)` maps ranks onto an
\(n\)-dimensional torus/grid. `periods[i]` says whether dimension \(i\) wraps. `reorder = true`
permits the implementation to renumber ranks to match physical locality — which means the caller
must re-query its rank and must not assume data placement survives. If \(\prod \text{dims}[i] < p\),
the surplus processes get `MPI_COMM_NULL`. `MPI_Dims_create` will factor \(p\) into a balanced
`dims` array for you.

`MPI_Cart_shift(comm, direction, disp, &rank_source, &rank_dest)` is the workhorse: it returns the
ranks of the neighbors \(\pm\,\text{disp}\) along `direction`, yielding `MPI_PROC_NULL` at a
non-periodic edge. The pair is exactly what `MPI_Sendrecv` wants. `MPI_Cart_rank` and
`MPI_Cart_coords` convert between coordinates and ranks; `MPI_Cart_sub` splits off lower-dimensional
subgrids (the natural way to get row and column communicators for a 2-D matrix algorithm).

---

## 7. The halo / ghost exchange pattern

### 7.1 The idiom

Domain decomposition splits a mesh across ranks. To apply a stencil at the boundary of my
subdomain I need a strip of my neighbors' cells — the **halo** or **ghost** layer. Every timestep:
exchange halos, then compute. This is the single most common communication pattern in scientific
computing, and it is a *sparse, static, neighbor* exchange — precisely the pattern neighborhood
collectives (§5.3) were introduced to express.

### 7.2 Why naive blocking sends deadlock, and the eager/rendezvous subtlety

The obvious code is wrong:

```c
/* WRONG in general */
MPI_Send(&left_halo,  n, MPI_DOUBLE, left,  0, comm);
MPI_Recv(&right_halo, n, MPI_DOUBLE, right, 0, comm, &st);
```

Every rank is in `MPI_Send` and nobody has posted a receive. Whether this deadlocks depends on
buffering, which the standard deliberately does not specify. Concretely, implementations use two
protocols:

- **Eager**: for small messages the sender pushes the payload immediately into a preallocated
  receive-side buffer and returns without a handshake. The `Send` completes with no matching
  `Recv`, and the naive code *appears* to work.
- **Rendezvous**: above an eager threshold, the sender first transmits a request-to-send and waits
  for a clear-to-send before moving the payload. Now every rank is blocked in its RTS wait, no CTS
  is ever generated, and the program deadlocks.

This is the worst possible failure mode: a program that passes every test at small mesh sizes and
hangs in production at scale, on one machine and not another. The standard's own language
[MPI11, §3.5] is blunt: such a program "is unsafe," and its success "will depend on the amount of
buffer space available in a particular implementation, on the buffer allocation policy used, and on
other concurrent communication occurring in the system." Chan et al.'s \(\alpha_1\) vs
\(\alpha_3 = 3\alpha_1\) distinction is the cost-model shadow of the same protocol split.

Three fixes, in increasing order of quality:

1. **Odd–even ordering.** Even ranks send then receive; odd ranks receive then send. Correct, but
   fiddly at boundaries and it *serializes* the exchange row by row, destroying overlap.
2. **`MPI_Sendrecv(sendbuf, ..., dest, sendtag, recvbuf, ..., source, recvtag, comm, status)`.**
   One call, two operations, and MPI guarantees the runtime resolves the cyclic dependency
   internally. Pairs perfectly with `MPI_Cart_shift`, and `MPI_PROC_NULL` neighbors are handled as
   no-ops. This is the idiomatic fix.
3. **Nonblocking + waitall.** Post all `MPI_Irecv`s first, then all `MPI_Isend`s, then one
   `MPI_Waitall`. Pre-posting receives means the eager protocol always has a landing zone and the
   rendezvous protocol gets its CTS immediately; multiple messages are in flight at once, limited by
   network resources rather than by call ordering; and the gap between posting and waiting is free
   for computation on interior cells. This is the highest-performance form, and it is what
   `MPI_Neighbor_alltoallv` lets the library do for you.

### 7.3 Dynamic sparse data exchange and NBX

Halo exchange assumes you know your neighbors. Many modern workloads do not: graph algorithms,
adaptive meshes, particle methods with migration. Hoefler, Siebert and Lumsdaine [Hoefler10] name
this the **dynamic sparse data exchange (DSDE)** problem: *dynamic* (the pattern changes each
iteration), *sparse* (each process talks to \(k = O(\log P)\) peers), and — the crux — **only the
senders know the pattern**. A receiver does not know who will send to it, or how much, so it cannot
size buffers or post receives.

They analyze three standard protocols and propose a fourth. Let \(S\) be per-process space and \(T\)
be time, in LogGP:

| Protocol | Mechanism | Space | Time |
|---|---|---|---|
| **PEX** — personalized exchange | `MPI_Alltoall` a length-\(P\) vector of send counts, then post receives and send | \(\Theta(P)\) | \(\Theta(P)\) |
| **PCX** — personalized census | Build a \(P\times P\) 0/1 table, `MPI_Reduce_scatter` it row-wise so each rank learns *how many* senders it has | \(\Theta(P)\) | \(\Theta(P)\) |
| **RSX** — remote summation | `MPI_Accumulate` a counter at each target, `MPI_Win_fence`, then post that many wildcard receives | \(\Theta(1)\) | \(\Theta(\log P)\) |
| **NBX** — nonblocking consensus | see below | \(\Theta(1)\) | \(\Theta(\log P)\) |

**NBX** is the contribution, and it is beautiful. The algorithm (their Algorithm 2):

```
foreach destination i:
    start a nonblocking SYNCHRONOUS send (MPI_Issend) to dest(i)
barrier_active = false
while not done:
    probe for an incoming message (MPI_Iprobe with MPI_ANY_SOURCE)
    if found: query its size with MPI_Get_count, allocate, receive, append to output
    if barrier_active:
        if MPI_Test on the Ibarrier request says complete: done = true
    else:
        if all my sends have completed:      # i.e. all my messages were RECEIVED
            MPI_Ibarrier(...)                # plant the distributed marker
            barrier_active = true
```

The two MPI features that make it work:

- **Synchronous-mode send.** `MPI_Issend` completes only when the message has been *received*, not
  merely buffered. So "all my sends are done" is a statement about the receivers, not about my
  buffers.
- **Nonblocking barrier.** Entering the barrier is a purely local act; completion is global.
  `MPI_Ibarrier` therefore acts as a **distributed termination marker**: "each process starts the
  barrier after it finishes its local part and serves the requests of other processes until it
  detects global termination."

Correctness requires that the barrier cannot complete before all messages are received. The paper
gives four sufficient conditions; the practical one (Option 4) is that "the sender can check if a
message reception has at least been started at the receiver side," which is exactly `MPI_Ssend`
semantics. Theorem 4: \(S_{NBX}(P) = \Theta(1)\) and
\(T_{NBX}(P) = T_{BC}(P) + T_{DT}(P) = \Theta(\log P)\), meeting "the lower bound \(T_{BC}(P)\)
imposed by the necessary detection of termination."

Measured results: 5.6× faster sparse exchange on 8,192 BlueGene/P cores, and up to 28.9× on a
parallel breadth-first search. The crossover matters — PEX and PCX are faster at small scale and are
overtaken around \(P \approx 400\)–2,048 depending on the machine and on \(k\) — but PEX simply
*runs out of memory* above 2,048 processes because of its \(\Theta(P)\) footprint.

The honest caveat the authors report: a naive `MPI_Ssend` implementation "would effectively double
the number of messages," since many MPI libraries add a handshake for small messages that the eager
protocol would otherwise skip. (For large messages the rendezvous protocol already *is* a
synchronous send, so there is no penalty.)

**Why this matters enormously for AgentMPI.** In a multi-agent system you frequently do not know a
priori which agents will need to talk to which. NBX is the existence proof that you can run a
sparse, unplanned exchange to completion with \(O(1)\) state per participant and \(O(\log P)\)
termination detection — provided you have (i) a delivery-confirming send and (ii) a split-phase
barrier. Any agent protocol that wants dynamic topology should provide both primitives.

---

## Bibliography

```bibtex
@article{ThakurRabenseifnerGropp05,
  author  = {Rajeev Thakur and Rolf Rabenseifner and William Gropp},
  title   = {Optimization of Collective Communication Operations in {MPICH}},
  journal = {International Journal of High Performance Computing Applications},
  volume  = {19}, number = {1}, pages = {49--66}, year = {2005},
  doi     = {10.1177/1094342005051521}
}

@article{Chan07,
  author  = {Ernie Chan and Marcel Heimlich and Avi Purkayastha and Robert van de Geijn},
  title   = {Collective communication: theory, practice, and experience},
  journal = {Concurrency and Computation: Practice and Experience},
  volume  = {19}, number = {13}, pages = {1749--1783}, year = {2007},
  doi     = {10.1002/cpe.1206}
}

@article{Bruck97,
  author  = {Jehoshua Bruck and Ching-Tien Ho and Shlomo Kipnis and Eli Upfal and Derrick Weathersby},
  title   = {Efficient Algorithms for All-to-All Communications in Multiport Message-Passing Systems},
  journal = {IEEE Transactions on Parallel and Distributed Systems},
  volume  = {8}, number = {11}, pages = {1143--1156}, year = {1997},
  doi     = {10.1109/71.642949}
}

@inproceedings{Rabenseifner04,
  author    = {Rolf Rabenseifner},
  title     = {Optimization of Collective Reduction Operations},
  booktitle = {Computational Science -- ICCS 2004},
  series    = {LNCS}, volume = {3036}, pages = {1--9}, year = {2004},
  doi       = {10.1007/978-3-540-24685-5_1}
}

@inproceedings{RabenseifnerTraff04,
  author    = {Rolf Rabenseifner and Jesper Larsson Tr\"{a}ff},
  title     = {More Efficient Reduction Algorithms for Non-Power-of-Two Number of Processors
               in Message-Passing Parallel Systems},
  booktitle = {Recent Advances in PVM and MPI (EuroPVM/MPI 2004)},
  series    = {LNCS}, volume = {3241}, pages = {36--46}, year = {2004}
}

@inproceedings{HoeflerSC07,
  author    = {Torsten Hoefler and Andrew Lumsdaine and Wolfgang Rehm},
  title     = {Implementation and Performance Analysis of Non-Blocking Collective Operations for {MPI}},
  booktitle = {Proceedings of SC07}, publisher = {IEEE/ACM}, year = {2007},
  doi       = {10.1145/1362622.1362692}
}

@techreport{HoeflerLibNBCDesign,
  author      = {Torsten Hoefler and Andrew Lumsdaine},
  title       = {Design, Implementation, and Usage of {LibNBC}},
  institution = {Open Systems Lab, Indiana University}, year = {2006}
}

@inproceedings{Hoefler10,
  author    = {Torsten Hoefler and Christian Siebert and Andrew Lumsdaine},
  title     = {Scalable communication protocols for dynamic sparse data exchange},
  booktitle = {Proceedings of the 15th ACM SIGPLAN Symposium on Principles and Practice
               of Parallel Programming (PPoPP '10)},
  pages     = {159--168}, year = {2010}
}

@inproceedings{Culler93,
  author    = {David E. Culler and Richard M. Karp and David A. Patterson and Abhijit Sahay
               and Klaus E. Schauser and Eunice Santos and Ramesh Subramonian and Thorsten von Eicken},
  title     = {{LogP}: Towards a Realistic Model of Parallel Computation},
  booktitle = {Proceedings of the Fourth ACM SIGPLAN Symposium on Principles and Practice
               of Parallel Programming (PPoPP '93)},
  pages     = {1--12}, year = {1993}, doi = {10.1145/155332.155333}
}

@inproceedings{Alexandrov95,
  author    = {Albert Alexandrov and Mihai F. Ionescu and Klaus E. Schauser and Chris Scheiman},
  title     = {{LogGP}: Incorporating Long Messages into the {LogP} Model --- One Step Closer
               Towards a Realistic Model for Parallel Computation},
  booktitle = {Proceedings of the 7th ACM Symposium on Parallel Algorithms and Architectures (SPAA)},
  year      = {1995}, doi = {10.1145/215399.215427}
}

@article{Hockney94,
  author  = {Roger W. Hockney},
  title   = {The communication challenge for {MPP}: {Intel Paragon} and {Meiko CS-2}},
  journal = {Parallel Computing}, volume = {20}, number = {3}, pages = {389--398}, year = {1994}
}

@article{Hensgen88,
  author  = {Debra Hensgen and Raphael Finkel and Udi Manber},
  title   = {Two Algorithms for Barrier Synchronization},
  journal = {International Journal of Parallel Programming},
  volume  = {17}, number = {1}, pages = {1--17}, year = {1988},
  doi     = {10.1007/BF01379320}
}

@article{MellorCrummeyScott91,
  author  = {John M. Mellor-Crummey and Michael L. Scott},
  title   = {Algorithms for Scalable Synchronization on Shared-Memory Multiprocessors},
  journal = {ACM Transactions on Computer Systems},
  volume  = {9}, number = {1}, pages = {21--65}, year = {1991}
}

@article{SandersSpeckTraff09,
  author  = {Peter Sanders and Jochen Speck and Jesper Larsson Tr\"{a}ff},
  title   = {Two-tree algorithms for full bandwidth broadcast, reduction and scan},
  journal = {Parallel Computing}, volume = {35}, number = {12}, pages = {581--594}, year = {2009},
  doi     = {10.1016/j.parco.2009.09.001}
}

@article{PatarasukYuan09,
  author  = {Pitch Patarasuk and Xin Yuan},
  title   = {Bandwidth optimal all-reduce algorithms for clusters of workstations},
  journal = {Journal of Parallel and Distributed Computing},
  volume  = {69}, number = {2}, pages = {117--124}, year = {2009},
  doi     = {10.1016/j.jpdc.2008.09.002}
}

@misc{Gibiansky17,
  author = {Andrew Gibiansky},
  title  = {Bringing {HPC} Techniques to Deep Learning},
  year   = {2017},
  note   = {Baidu Research technical blog; origin of the ``ring-allreduce'' framing in DL}
}

@article{SergeevDelBalso18,
  author  = {Alexander Sergeev and Mike Del Balso},
  title   = {Horovod: fast and easy distributed deep learning in {TensorFlow}},
  journal = {arXiv:1802.05799}, year = {2018}
}

@inproceedings{Graham16,
  author    = {Richard L. Graham and Devendar Bureddy and Pak Lui and Hal Rosenstock and
               Gilad Shainer and Gil Bloch and Dror Goldenberg and others},
  title     = {Scalable Hierarchical Aggregation Protocol ({SHArP}): A Hardware Architecture
               for Efficient Data Reduction},
  booktitle = {COM-HPC}, year = {2016}, doi = {10.1109/COMHPC.2016.006}
}

@inproceedings{Graham20,
  author    = {Richard L. Graham and others},
  title     = {{SHARP} Streaming-Aggregation Hardware Design and Evaluation},
  booktitle = {High Performance Computing (ISC 2020)}, series = {LNCS}, year = {2020},
  doi       = {10.1007/978-3-030-50743-5_3}
}

@inproceedings{BalajiCtxID12,
  author    = {James Dinan and David Goodell and William Gropp and Rajeev Thakur and Pavan Balaji},
  title     = {Efficient Multithreaded Context {ID} Allocation in {MPI}},
  booktitle = {EuroMPI 2012}, year = {2012}
}

@inproceedings{BalajiKimpe13,
  author    = {Pavan Balaji and Dries Kimpe},
  title     = {On the Reproducibility of {MPI} Reduction Operations},
  booktitle = {IEEE HPCC}, year = {2013}
}

@techreport{MPI31,
  author      = {{MPI Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 3.1},
  institution = {University of Tennessee}, year = {2015}
}

@techreport{MPI41,
  author      = {{MPI Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 4.1},
  institution = {MPI Forum}, year = {2023}
}

@techreport{MPI50,
  author      = {{MPI Forum}},
  title       = {{MPI}: A Message-Passing Interface Standard, Version 5.0},
  institution = {MPI Forum}, year = {2025}
}

@book{Dongarra95,
  author    = {William Gropp and Ewing Lusk and Anthony Skjellum},
  title     = {Using {MPI}: Portable Parallel Programming with the Message-Passing Interface},
  publisher = {MIT Press}, year = {1994},
  note      = {Portability chapter on collective communication and synchronization}
}

@misc{PICO25,
  title = {{PICO}: Performance Insights for Collective Operations},
  note  = {arXiv:2508.16809; empirical crossover measurements for ring allreduce on Leonardo and LUMI},
  year  = {2025}
}
```

Software artifacts consulted directly (source comments quoted above): MPICH
`src/mpi/coll/allreduce/allreduce_intra_recursive_doubling.c`,
`allreduce_intra_reduce_scatter_allgather.c`, `src/mpi/comm/contextid.c`, `src/mpi/comm/commutil.c`,
`src/mpi/coll/src/coll_impl.c`; MVAPICH2 `src/mpi/coll/exscan.c`; Open MPI `coll/tuned` tuning
documentation.

---

## Transfer to agent systems

The mapping I propose: **processes → agents**, **bytes → tokens**, **the network → the
orchestration substrate**, **memory → context window**. The three scarce resources become

- \(\hat{\alpha}\): per-invocation fixed cost — model queue time, TTFT, tool round-trips, scheduling
  overhead. Wall-clock seconds. Typically 0.5–5 s, and, unlike network \(\alpha\), *stochastic with
  a heavy right tail*.
- \(\hat{\beta}\): per-token cost — generation time and dollars. Note that in LLM systems
  \(\hat{\beta}\) is asymmetric: prefill (reading) is roughly an order of magnitude cheaper per
  token than decode (writing), so the model should really be
  \(\hat{\alpha} + n_{in}\hat{\beta}_{in} + n_{out}\hat{\beta}_{out}\). Most of the analysis below
  survives by taking \(\hat\beta = \hat\beta_{out}\), since decode dominates.
- \(\hat{\gamma}\): per-token cost of *applying an operator* — the extra generation an agent does to
  merge, judge, or reconcile.
- \(C\): the **context window**, a hard capacity constraint with no analogue in MPI. This is the
  most important structural difference. In MPI, \(p\) messages of \(n\) bytes each cost
  \(pn\beta\); in AgentMPI they may be *impossible* if \(pn > C\). Context is a resource that MPI's
  cost model does not have, and it changes which algorithms are admissible, not merely which are
  fast.

A fourth, non-MPI cost: **semantic loss**. Every LLM combine step is lossy. If \(\lambda\) is the
information retained per fold, a depth-\(d\) reduction tree retains \(\approx \lambda^d\). Latency
and fidelity therefore trade against each other in a way bytes never do.

**Barrier.** An AgentBarrier is a genuine commitment point: no agent proceeds to phase \(k+1\)
until all have finished phase \(k\). It carries no content, so its cost is pure
\(\max_i \hat{\alpha}_i\) — and here the heavy tail bites, because the barrier costs the *maximum*
over a stochastic set, not the mean. With \(p=32\) agents whose latency is lognormal, the barrier
routinely costs 3–5× the median agent. The design lesson from §3.1 carries over exactly: prefer the
dissemination pattern's property of being uniform in \(p\) over patterns that special-case
round numbers. The deeper lesson is the one from §1.1: *do not build implicit barriers*. If your
orchestrator's Bcast happens to synchronize today because it fans out serially, agents will come to
depend on it, and the day you parallelize the fan-out you will break them.

**Bcast.** One agent's artifact — a plan, a spec, a shared context document — must reach \(p\)
agents. The binomial-tree structure has a startling interpretation here: intermediate agents
*relay* the artifact. Unlike bytes, a relayed artifact does not arrive unchanged; each hop is a
re-articulation. So the binomial tree costs \(\lceil\lg p\rceil(\hat\alpha + n\hat\beta)\) but
retains only \(\lambda^{\lceil\lg p\rceil}\) fidelity, whereas the flat tree (orchestrator sends to
all \(p\) directly) costs \((p-1)\hat\alpha\) *serially in orchestrator attention* but is
fidelity-lossless. If the substrate can fan out a *verbatim* artifact (a URL, a file handle, a
pinned context block) then the flat tree's \(\hat\beta\) term collapses to zero and it dominates:
the right AgentMPI Bcast for an immutable artifact is not a tree at all, it is a shared reference.
The tree is only right when each recipient needs a *personalized rendering* of the broadcast
content, at which point you are really doing a Scatter. Van de Geijn's scatter+allgather has a
lovely reading: split the document into \(p\) sections, give one section to each agent, and let them
circulate — a distributed reading group. Cost \((\lg p + p - 1)\hat\alpha + 2\frac{p-1}{p}n\hat\beta\),
and critically each agent only ever holds \(n/p\) *new* tokens at a time, so peak context stays
within \(C\) where a monolithic broadcast would not.

**Gather / Gatherv.** \(p\) agents each produce an artifact; one aggregator receives all of them.
This is where the context window makes the cost model diverge most sharply from MPI. Gather costs
\(\lceil\lg p\rceil\hat\alpha + \frac{p-1}{p}n\hat\beta\) in time, but the aggregator's context must
hold \(n\) tokens, and if \(n > C\) the operation does not merely slow down, it *fails*. Gatherv is
the honest version: contributions have wildly unequal sizes and the aggregator must budget
`recvcounts[]` in advance — i.e. impose a token budget per contributor. I think AgentMPI's Gather
should be Gatherv-only, with an explicit per-agent token allowance, because the regular Gather's
implicit assumption of uniform contribution size is exactly the assumption LLM outputs violate.

**Scatter / Scatterv.** Task decomposition. The orchestrator holds a problem and cuts it into \(p\)
sub-problems. Binomial-tree scatter has an appealing agent reading — hierarchical delegation, where
a middle manager receives half the work and re-splits it — and it costs
\(\lceil\lg p\rceil\hat\alpha + \frac{p-1}{p}n\hat\beta\), meeting both lower bounds. But the
delegation interpretation introduces a cost MPI lacks: the middle manager must *understand* the
half it forwards well enough to split it, which is \(\hat\gamma\) work at every interior node. Flat
scatter avoids that at the price of \(p\) sequential orchestrator turns. The crossover is
\(p^\star\) where \(p\hat\alpha_{orch} = \lg p\,(\hat\alpha + n_{sub}\hat\gamma)\).

**Allgather.** Every agent must see every other agent's output — a design review, a mutual-critique
round, a shared world-state sync. The three MPI algorithms map to three recognizable social
protocols. *Ring*: each agent passes what it holds to its neighbor, \(p-1\) rounds — the
"pass your draft to the left" workshop. *Recursive doubling*: pair up, merge, pair up at distance 2,
merge — \(\lg p\) rounds, an elimination-bracket structure. *Bruck*: \(\lceil\lg p\rceil\) rounds
uniform in \(p\). The bandwidth term \(\frac{p-1}{p}n\hat\beta\) is the same for all three and is
irreducible, but the **context** cost is not: in recursive doubling every agent's context grows
\(n/p, 2n/p, 4n/p, \dots, n\), so the last round requires an agent that can hold the entire corpus.
Ring keeps per-round *transmission* at \(n/p\) but still accumulates \(n\) locally. The genuinely
context-efficient variant has no MPI analogue: allgather-with-summarization, where each fold
compresses, giving per-agent context \(O(n/p \cdot \log p)\) at the cost of \(\lambda^{\lg p}\)
fidelity. That is Allreduce with a lossy operator, not Allgather, and conflating the two is a bug I
expect to see often.

**Alltoall.** Every agent sends *something different* to every other agent — a distributed
transpose of responsibility. Concretely: \(p\) agents each analyzed one document along \(p\)
dimensions, and now each agent should own one dimension across all documents. The MPI trade-off
transfers directly and is unusually clean here. Pairwise exchange: \(p-1\) rounds, \(n\hat\beta\)
tokens, no redundancy. Bruck: \(\lceil\lg p\rceil\) rounds but \(\frac{n}{2}\lg p\,\hat\beta\)
tokens — i.e. **you can buy round-count reduction with token redundancy at an exchange rate of
\(\frac{\lg p}{2}\)**, by having intermediate agents relay bundles addressed to others. Whether that
is worth it depends entirely on whether your bottleneck is wall-clock rounds (interactive systems:
yes, use Bruck) or token spend (batch systems: no, use pairwise). Alltoallw — per-peer *datatypes* —
is the case where agent \(i\) sends agent \(j\) a message in a format negotiated pairwise; in an LLM
system that is the norm, not the exception, which suggests AgentMPI's Alltoall should be
Alltoallw-shaped.

**Reduce.** Fold \(p\) contributions into one, at one agent. This is where the analogy is most
productive *and* most dangerous. The binomial tree costs
\(\lceil\lg p\rceil(\hat\alpha + n\hat\beta + n\hat\gamma)\) and retains \(\lambda^{\lg p}\); the
flat reduce costs \(\hat\alpha + n\hat\beta + n\hat\gamma\) at a single agent and retains
\(\lambda^1\), but requires \(n \le C\). So the tree is not a pure optimization: it is the
*only feasible* algorithm when \(n > C\), and it pays for feasibility in fidelity. That is a
genuinely new term in the cost model, and I think it is the most important thing this analogy
surfaces:

\[
T_{\text{reduce}} = d\cdot(\hat\alpha + n_{\text{level}}\hat\beta + n_{\text{level}}\hat\gamma),
\qquad
\text{fidelity} = \lambda^{d}, \qquad
\text{context}_{\max} = k\cdot n_{\text{level}} \le C
\]

for a \(k\)-ary tree of depth \(d = \lceil\log_k p\rceil\). Minimizing \(d\) subject to the context
constraint gives \(k^\star = \lfloor C / n_{\text{level}}\rfloor\): **make the fan-in as wide as the
context window allows, and no wider.** That single rule is the agent-systems analogue of choosing a
radix, and it falls straight out of taking the cost model seriously.

**Allreduce.** Consensus. Every agent must end holding the same merged artifact. Recursive doubling
maps to "everyone pairs off, merges, re-pairs" — \(\lg p\) rounds and, importantly, *no distinguished
agent*, so no single agent needs a context large enough for everything at once beyond a pairwise
merge. Rabenseifner's reduce-scatter + allgather maps to something recognizable from human
organizations: partition the artifact into \(p\) topics, have each agent become the owner and merger
of one topic (reduce-scatter), then circulate the merged topics (allgather). Cost
\(2\lg p\,\hat\alpha + 2\frac{p-1}{p}n\hat\beta + \frac{p-1}{p}n\hat\gamma\) — the \(\hat\gamma\)
term is a factor \(\lg p\) *lower* than recursive doubling's, which in agent terms means each agent
does \(1/p\) of the merging work instead of all of it. For expensive judge-model merges that is the
dominant saving. But the correctness precondition is severe: reduce-scatter requires the operator be
**commutative**, and LLM merge operators are emphatically not — the order in which you present
candidate answers to a judge changes the verdict (position bias is a well-documented effect). By
MPI's own rules, an LLM merge operator must be declared `commute = false`, which forbids
reduce-scatter and forces the recursive-doubling path. The bandwidth-optimal algorithms are simply
unavailable to us unless we first do the work of making the operator order-invariant (e.g. by
canonically sorting candidates before every fold, or by using an operator like "union of distinct
claims" that genuinely is a semilattice join). **Designing order-invariant, idempotent merge
operators is therefore not hygiene; it is what unlocks the entire bandwidth-optimal algorithm
family.** Idempotence buys something MPI does not even have a name for: safe re-execution after a
partial failure.

**Reduce_scatter.** Fold everything, then distribute the folded result by topic, one topic per
agent. In agent terms: \(p\) agents each hold partial findings across \(p\) topics; afterwards each
agent owns the consolidated view of exactly one topic. This is the natural primitive for
**responsibility assignment**, and it is context-optimal in a way Allreduce is not: each agent ends
holding \(n/p\) tokens, not \(n\). If your system is context-bound rather than latency-bound,
Reduce_scatter is the operation you want and Allreduce is the operation you will mistakenly reach
for.

**Scan / Exscan.** Sequential dependency with parallel structure — the operation for pipelines where
agent \(i\)'s output depends on all preceding agents. Chapter-by-chapter narrative generation,
incremental plan refinement, cumulative budget allocation. The Hillis–Steele/Blelloch distinction is
sharp here. Naive sequential scan costs \(p\hat\alpha\): \(p\) round-trips, one per agent, the
classic agent-pipeline latency disaster. Recursive doubling gets it to \(\lg p\) rounds at
\(O(p\log p)\) total merge work — for \(p = 64\) that is 6 rounds instead of 64, at ~6× the token
spend. Blelloch's up-sweep/down-sweep costs \(2\lg p\) rounds at \(O(p)\) work. Given that
\(\hat\alpha\) (wall-clock per LLM turn) is enormous relative to \(\hat\beta\) for short artifacts,
Hillis–Steele is usually right for agents, which inverts the GPU-kernel intuition where Blelloch
usually wins. The catch: scan's operator is *inherently* non-commutative (prefix order is the point),
so the associativity requirement is doing all the work, and an LLM operator that is not associative
will produce a scan whose results depend on the tree shape. The mitigation is to make the operator
associative by construction — e.g. have it merge *sets of facts* rather than *prose* — and render to
prose only at the leaves.

**Nonblocking collectives.** The single highest-value transfer. In an agent system, \(\hat\alpha\)
is seconds, not microseconds, so the overlap opportunity is not marginal — it is the difference
between a system that feels responsive and one that does not. `AgentIallreduce` means: start the
consensus round, keep doing local work, complete later. The MPI-3 restrictions transfer verbatim
and are worth adopting as-is: no tags (the communicator *is* the addressing), same order on all
participants, no matching between blocking and nonblocking forms. The "same order" rule is the one
agent frameworks habitually violate — Horovod had to build an explicit **negotiation phase** for
exactly this reason, because "the tensors can arrive in different orders on different ranks" in a
multi-threaded framework and "we need to determine which tensors are ready on all ranks and have a
deterministic order." Any AgentMPI implementation will need the same thing, and it should be part
of the spec rather than an implementation detail.

**Persistent collectives.** Agent workflows are overwhelmingly *iterative* — the same fan-out/fan-in
runs every turn of a loop. Persistent collectives say: pay the planning cost once. In our setting
"planning" means selecting the aggregation topology, pre-warming caches, pre-negotiating the artifact
schema, reserving model capacity, and — the big one — **pre-caching the shared prompt prefix**. With
prompt caching, an initialized persistent collective can amortize the entire system-prompt prefill
across every iteration, turning a per-iteration \(n_{prefix}\hat\beta_{in}\) into a one-time cost.
That is a far larger win proportionally than early binding gives MPI. The lifecycle
`Create (Start Complete)* Free` is exactly the right shape for an agent workflow's inner loop.

**Neighborhood collectives.** Most agent interaction is sparse: an agent has a handful of
collaborators, not \(p-1\). Declaring that structure up front — via the agent analogue of
`MPI_Dist_graph_create_adjacent`, where each agent names only its own in- and out-neighbors and no
one holds the global graph — lets the runtime plan schedules, batch messages, and co-locate
chatty agents. The `MPI_PROC_NULL` convention is worth stealing verbatim: a missing neighbor leaves
a *hole* in the buffer rather than shortening it, so every agent's message layout is identical
regardless of position. In an LLM system that means every agent gets the same prompt template with
an explicit "no input from this direction" slot, which is both cache-friendlier and less confusing
to the model than a variable-length template.

**Communicators and context IDs.** This is the cleanest transfer of all, and the one I think is most
underexploited in current multi-agent frameworks. A communicator is a *scoped, isolated
conversation*: a group of agents plus a context that guarantees messages cannot leak between
sub-teams. `Comm_split(color, key)` is dynamic sub-team formation — split the swarm by role, by
document shard, by hypothesis being tested — with the elegant property that it is a *single
collective call* in which every agent independently states which team it wants to join.
`Comm_split_type(SHARED)` maps to "which agents share a memory substrate" — the same vector store,
the same scratchpad, the same filesystem — which is exactly the locality question an agent scheduler
must answer. `Comm_dup` is what a *library* agent does before communicating: take the caller's team,
make a private channel over the same membership, and you cannot possibly be confused with the
caller's own traffic. The context-ID allocation protocol — `MPI_BAND` allreduce over a free-slot
bitmask, pick the lowest globally-free bit — is a complete, deployable, leader-free algorithm for
allocating conversation namespaces in a distributed agent system, and I have not seen it used there.
The delayed-reuse caveat transfers too: recycle a conversation ID too quickly and a straggling
message from the old conversation will be delivered into the new one. In an agent system that
failure is not a corrupted buffer, it is a hallucination with a plausible provenance, which is much
worse.

**Halo exchange and NBX.** The static halo pattern maps to agents with fixed collaborators
exchanging boundary state each round, and the deadlock lesson is directly transferable: if every
agent blocks waiting for a peer's reply before sending its own, the system deadlocks — and, exactly
as with eager/rendezvous, it will *appear to work* while artifacts are small enough to be buffered
by the orchestrator, then hang once they are large enough to require a handshake. The agent-level
`MPI_Sendrecv` (a single primitive that atomically posts an outgoing message and an incoming
expectation) is the right fix and should be in the core spec, not left to user code.

NBX is the piece I am most excited to steal. Agent systems have the DSDE property in its purest
form: which agents will need to talk to which is *discovered during execution*, only senders know
their targets, and no one can pre-post receives. NBX's answer — synchronous-mode sends so "sent"
means "received", plus a nonblocking barrier as a distributed termination marker — gives
\(\Theta(1)\) state per agent and \(\Theta(\log p)\) termination detection, versus the \(\Theta(p)\)
state and time of the alltoall-metadata approach that every agent framework I know of uses
implicitly (a central orchestrator polling all agents is precisely PEX, and it has PEX's
\(\Theta(P)\) scaling). The measured 5.6× improvement at 8,192 processes, and the fact that PEX
simply ran out of memory beyond 2,048, is the strongest available argument that centralized
coordination of dynamic sparse agent communication will hit a wall — and that the wall is a memory
wall, not a latency wall. The two primitives AgentMPI must therefore expose are (i) a send whose
completion signals *delivery*, not *dispatch*, and (ii) a split-phase barrier whose entry is local
and whose completion is global. Everything else in NBX is a five-line loop.
