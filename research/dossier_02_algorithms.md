# Dossier 02 — MPI Collective Communication Algorithms and Performance Models

**Purpose.** Citation-grounded reference for the "AgentMPI" paper. Every cost expression below is
either quoted directly from the cited source or derived in the stated model with the derivation shown;
derivations of our own are marked "(der.)" in the summary table. Claims that could not be traced to a
primary source would be marked `[UNVERIFIED]`; **as of this revision none remain**, and the
"Notes on attribution accuracy" section at the end records eight commonly garbled attributions together
with the corrected forms. Where sources disagree on a bibliographic detail (page ranges, an author's
given name), the reference entry says so rather than silently picking one.

**Notation used throughout.** \(p\) = number of processes; \(n\) = message size in bytes (for
rooted collectives, the size of the root's buffer; for allgather/reduce-scatter, the *total* size of
the gathered/reduced vector, following [Thakur 2005]); \(\alpha\) = per-message latency;
\(\beta\) = per-byte transfer cost; \(\gamma\) = per-byte local reduction cost; \(\lg\) = \(\log_2\).

---

## 1. Cost models

### 1.1 The Hockney \(\alpha\)–\(\beta\) model

The model attributed to [Hockney 1994] charges a point-to-point transfer of \(n\) bytes

\[
T(n) = \alpha + n\beta
\]

where \(\alpha\) is the startup cost (message-size independent) and \(\beta = 1/r_\infty\) the
reciprocal asymptotic bandwidth. Hockney's own parameterisation is usually written
\(t = t_0(1 + n_{1/2}/n)\), with \(n_{1/2}\) the message length attaining half of peak bandwidth;
\(n_{1/2} = \alpha/\beta\) is the crossover point separating latency-bound from bandwidth-bound
regimes and is the single most useful derived quantity in the model.

[Thakur 2005, §3] states the assumptions under which this model is used for MPICH's collectives,
and they are worth reproducing because almost every published collective cost expression inherits
them:

1. Transfer time is \(\alpha + n\beta\) between *any* two nodes;
2. time is independent of how many process pairs communicate concurrently;
3. time is independent of the distance between communicating nodes;
4. links are bidirectional (a message costs the same in both directions simultaneously);
5. the NIC is **single-ported** — at most one message may be sent and one received simultaneously;
6. for reductions, \(\gamma\) is the per-byte local reduction cost.

Thakur et al. explicitly flag assumption (2) as the weakest: "many networks are faster if pairs of
processes exchange data with each other, rather than if a process sends to and receives from
different processes" [Thakur 2005, §3]. Their remedy is to refine the model with separate
bidirectional (\(\alpha, \beta\)) and unidirectional (\(\alpha_{uni}, \beta_{uni}\)) parameters plus
ratios \(f_\alpha = \alpha_{uni}/\alpha\), \(f_\beta = \beta_{uni}/\beta\), normally in
\([0.5, 1.0]\) (simplex → full-duplex). This refinement is what makes their §5 analysis of
non-power-of-two allreduce quantitatively different from the naive \(\alpha\)–\(\beta\) result.

[Chan 2007] uses a variant that exposes the *protocol* rather than the duplex ratio. They
distinguish \(\alpha_3\), the latency of a three-pass (rendezvous) protocol, from \(\alpha_1\), the
latency of a one-pass (eager) protocol, and adopt \(\alpha_3 = 3\alpha_1\) [Chan 2007, §2]. This is
the cleanest published bridge between the abstract \(\alpha\)–\(\beta\) model and the eager/rendezvous
protocol reality of §4 below.

**Limitations.** No overlap of computation and communication; no injection-rate limit; no
contention, congestion, or topology; no distinction between sender and receiver CPU occupancy;
no notion of a single process having multiple messages in flight. Every one of these is addressed by
some member of the LogP family.

### 1.2 LogP (Culler et al., PPoPP'93)

[Culler 1993] parameterises a distributed-memory machine by four values, quoted from the paper:

- \(L\): "an upper bound on the latency, or delay, incurred in communicating a message containing a
  word (or small number of words) from its source processor/memory module to its target";
- \(o\): "the overhead, defined as the length of time that a processor is engaged in the transmission
  or reception of each message. During this time, the processor cannot perform other operations";
- \(g\): "the gap, defined as the minimum time interval between consecutive message transmissions or
  consecutive message receptions at a processor. The reciprocal of \(g\) corresponds to the available
  per-processor communication bandwidth";
- \(P\): the number of processor/memory modules.

For a small message the end-to-end cost is

\[
T_{\mathrm{LogP}} = 2o + L,
\]

and for \(s\) *data (fixed-size packets)*

\[
T_{\mathrm{LogP}}(s) = 2o + L + (s-1)g .
\]

[Culler 1993] describes the network as "a pipeline of depth \(L\) with initiation rate \(g\) and a
processor overhead of \(o\) on each end", with network capacity \(\lfloor L/g \rfloor\) messages in
flight per processor. The key modelling advance over \(\alpha\)–\(\beta\) is the separation of *CPU
occupancy* (\(o\)) from *network transit* (\(L\)), which is what permits reasoning about overlap; the
key restriction is that \(g\) is tied to a fixed small packet size.

### 1.3 LogGP (Alexandrov et al.)

[Alexandrov 1995; Alexandrov 1997] add one parameter \(G\), the **Gap per byte** for long messages —
"the time needed to transmit a single byte for the bulk transfer of long messages" — giving

\[
T_{\mathrm{LogGP}}(s) = 2o + L + (s-1)G .
\]

The motivation is empirical: LogP predicts CM-5 performance well because the CM-5 only had short
messages, but the IBM SP-2, Paragon, Meiko CS-2 and nCUBE/2 "have special support for long messages
and achieve a much higher bandwidth for long messages compared to short messages"
[Alexandrov 1995, abstract]. Note \(1/G\) plays the role of \(1/\beta\), while \(g\) remains the
per-*message* injection gap; the two are distinct and both are needed. LogGP's headline algorithmic
result is a provably optimal single-node scatter, "qualitatively different" from the LogP-optimal
solution. Practitioners often split \(o\) into \(o_s\) and \(o_r\) [Hoefler 2007b].

### 1.4 LogGPS (Ino, Fujimoto, Hagihara, PPoPP'01)

[Ino 2001] extends LogGP with a **synchronisation threshold** \(S\) and refines the overheads. Three
modifications, quoted from §3.2:

- (D1) add \(S\), "the threshold for message length, above which synchronous messages are sent";
  for \(k > S\), the sender waits for an ACK from the receiver;
- (D2) split \(o\) into a send overhead \(o + kO_s\) and receive overhead \(o + kO_r\), i.e. make
  CPU occupancy *linear* in message length;
- (D3) add \(s\), the threshold above which messages are packetised, with distinct gaps \(G_s\)
  (\(k \le s\)) and \(G_l\) (\(k > s\)).

For an asynchronous (eager) message of \(k\) bytes with \(k \le S\), LogGPS composes three terms:
\(T_1 = o + kO_s\) (push first byte in), \(T_2 = kG_s + L\) or \(sG_s + (k-s)G_l + L\) (transit),
\(T_3 = o + kO_r\) (pull last byte out). For \(k > S\), the rendezvous handshake contributes
\(T_4 = \max\{o + L,\; t_r - t_s\} + o\) for the REQ (where \(t_s\), \(t_r\) are the call times of the
matching send and receive) and \(T_5 = o + L + o\) for the ACK. The \(\max\{\cdot\}\) with
\(t_r - t_s\) is the essential feature: LogGPS is the first of the family in which *load imbalance
between sender and receiver becomes a first-class cost term*. Ino et al. report the model predicts
Gaussian-elimination runtime to within 7%, with synchronisation accounting for roughly 50% of
predicted time. They deliberately drop \(g\) as having "little effect on the communication costs of
high-level communication routines".

**For AgentMPI this is the most directly transferable model:** an LLM harness has a large per-message
CPU cost linear in token count (serialisation, tokenisation), a rendezvous-like admission-control
handshake for large payloads, and severe sender/receiver skew — exactly \(O_s, O_r, S\), and
\(t_r - t_s\).

### 1.5 The postal model (Bar-Noy and Kipnis)

[Bar-Noy 1992; Bar-Noy 1994] introduce a single latency parameter \(\lambda \ge 1\) that "measures
the inverse of the ratio between the time it takes an originator of a message to send the message and
the time that passes until the recipient of the message receives it". A processor is thus occupied for
1 time unit per send but the message lands \(\lambda\) units later — so a sender can inject
\(\lambda\) messages during one message's flight time. The optimal single-message broadcast time on
\(n\) processors is

\[
\Theta\!\left(\frac{\lambda \log n}{\log(\lambda+1)}\right),
\]

i.e. the optimal tree degree grows with \(\lambda\); this is the theoretical origin of *k-nomial*
rather than binomial trees. For \(m \ge 1\) messages, the lower bound is
\((m-1) + f_\lambda(n)\) with \(f_\lambda(n)\) the one-message optimum; their PARTITION algorithm
achieves \(2m + f_\lambda(n) + O(\lambda)\) and D-D-TREES achieves \(m + 2f_\lambda(n) + O(\lambda)\)
[Bar-Noy 1993]. All algorithms are order-preserving and event-driven. The postal model is
essentially LogP with \(o\) normalised to 1 and \(L = \lambda\), and it is the right model when
*per-message software overhead*, not bandwidth, dominates.

### 1.6 BSP (Valiant)

[Valiant 1990] defines a BSP computer as (i) components performing processing and/or memory
functions, (ii) a router delivering point-to-point messages, and (iii) facilities for synchronising
all or a subset of components at regular intervals of \(L\) time units (the *periodicity*
parameter). Execution is a sequence of **supersteps**. The standard cost of one superstep is

\[
T_{\text{superstep}} = \max_i w_i + \max_i h_i\, g + l \;=\; w + hg + l,
\]

where \(w\) is the maximum local work, \(h\) the maximum number of messages sent or received by any
component (the *h-relation*), \(g\) the per-message-unit router cost, and \(l\) the barrier cost
[Valiant 1990; Skillicorn 1997]. Total program cost is the sum over supersteps:
\(\sum_s w_s + g\sum_s h_s + Sl\). Prescriptively, BSP says: balance \(w\), balance \(h\), and
minimise the number of supersteps.

**Limitations of BSP for our purposes.** The barrier is mandatory and charged \(l\) every superstep,
so BSP systematically over-charges asynchronous, pipelined, or dataflow-structured communication —
precisely the regime that pipelined broadcast and ring allreduce exploit. It also collapses all
message sizes into the \(h\)-relation, hiding latency/bandwidth tradeoffs.

### 1.7 Lower bounds

[Chan 2007, Table I] gives the following lower bounds under the \(\alpha\)–\(\beta\)–\(\gamma\)
model, assuming \(p > 1\) and equal-length subvectors:

| Operation | Latency | Bandwidth | Computation |
|---|---|---|---|
| Broadcast | \(\lceil \lg p \rceil\,\alpha\) | \(n\beta\) | — |
| Reduce(-to-one) | \(\lceil \lg p \rceil\,\alpha\) | \(n\beta\) | \(\frac{p-1}{p}n\gamma\) |
| Scatter | \(\lceil \lg p \rceil\,\alpha\) | \(\frac{p-1}{p}n\beta\) | — |
| Gather | \(\lceil \lg p \rceil\,\alpha\) | \(\frac{p-1}{p}n\beta\) | — |
| Allgather | \(\lceil \lg p \rceil\,\alpha\) | \(\frac{p-1}{p}n\beta\) | — |
| Reduce-scatter | \(\lceil \lg p \rceil\,\alpha\) | \(\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) |
| Allreduce | \(\lceil \lg p \rceil\,\alpha\) | \(2\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) |

The arguments are short: the latency bound follows because "at each step, we can at most double the
number of nodes that get the data"; the computation bound because \((p-1)n\) operations perfectly
distributed cost \(\frac{p-1}{p}n\gamma\); and the allreduce bandwidth bound because achieving the
computation bound forces \(\frac{p-1}{p}n\) items out of *and* into every node
[Chan 2007, §4]. **The critical structural consequence:** no single algorithm attains both the
latency and the bandwidth bound, which is exactly why every production MPI switches algorithms on
message size.

Chan et al. also observe that the collectives form **dual pairs** — broadcast/reduce,
scatter/gather, allgather/reduce-scatter — such that an implementation of one becomes an
implementation of its dual by reversing the communication and adding or deleting reductions.
Allreduce is its own dual. This is the single most useful organising principle in the literature and
we recommend AgentMPI adopt it verbatim.

---

## 2. Algorithms per collective

### 2.1 Broadcast

**Linear / flat tree.** The root sends to each of \(p-1\) peers in turn:
\(T = (p-1)(\alpha + n\beta)\) (derivation in the \(\alpha\)–\(\beta\) model with a single-ported
NIC). Optimal only for very small \(p\) or when \(\alpha\) is negligible; Open MPI retains it as
`basic_linear` (algorithm id 1) [OpenMPI 2024].

**Binomial tree.** The root sends to process \(root + p/2\); both then recurse in their subtrees.
\(\lceil \lg p \rceil\) steps, \(n\) bytes per step:

\[
T_{\text{tree}} = \lceil \lg p \rceil (\alpha + n\beta)
\]

[Thakur 2005, §4.2]. This attains the latency lower bound but its bandwidth term is
\(\lceil \lg p \rceil n\beta\) — a factor \(\lg p\) above the \(n\beta\) bound. It was MPICH's
original broadcast and remains the short-message choice. In [Chan 2007] the same algorithm appears as
*minimum-spanning-tree broadcast*, costed \(T_{\mathrm{MSTBcast}} = \lceil \lg p \rceil(\alpha_3 + n\beta_1)\).

**Binary tree.** A fixed binary tree of depth \(\lceil \lg (p+1) \rceil - 1\) in which each internal
node performs two sequential sends per level gives, by derivation,
\(T \approx 2\left(\lceil \lg(p+1)\rceil - 1\right)(\alpha + n\beta)\) — worse than binomial in the
unsegmented case, but valuable because a *static, in-order* binary tree can be segmented and
pipelined, and because in-order numbering permits non-commutative reduction on the dual side. Open
MPI exposes `binary_tree` (id 5) separately from `binomial` (id 6) [OpenMPI 2024].

**k-nomial tree.** Generalising to fan-out \(k\): \(\lceil \log_k p \rceil\) levels with \(k-1\)
sends per level, so \(T = (k-1)\lceil \log_k p \rceil (\alpha + n\beta)\) (derivation). The latency
term is minimised at \(k=3\) for the pure \(\alpha\) part; the correct \(k\) in practice follows the
postal-model result \(\Theta(\lambda \log n/\log(\lambda+1))\) [Bar-Noy 1994], i.e. larger \(k\) as
the latency-to-overhead ratio grows. Both MPICH (`MPIR_CVAR_BCAST_KNOMIAL_KVAL`, and the `tree`
algorithm family in `src/mpi/coll/algorithms`) and Open MPI (`knomial`, id 7) implement it
[MPICH 2024a; OpenMPI 2024].

**Pipelined / chain (ring) broadcast.** Split \(n\) into \(S\) segments of \(m = n/S\) bytes and push
them along a chain of \(p\) processes. The pipeline drains in \(p - 2 + S\) steps, giving

\[
T_{\text{pipe}}(S) = (p - 2 + S)\left(\alpha + \tfrac{n}{S}\beta\right).
\]

Minimising over \(S\) (derivation) yields \(S^\star = \sqrt{(p-2)n\beta/\alpha}\) and

\[
T_{\text{pipe}}^\star = (p-2)\alpha + n\beta + 2\sqrt{(p-2)n\alpha\beta}
= \left(\sqrt{(p-2)\alpha} + \sqrt{n\beta}\right)^2 .
\]

For \(n\beta \gg p\alpha\) this approaches the \(n\beta\) bandwidth lower bound — pipelining is the
*other* route to bandwidth optimality besides scatter+allgather. [Chan 2007, §1] explicitly excludes
pipelined algorithms from its scope ("we do not consider these practical on current generation
architectures"), citing [Watts 1995] among others; Open MPI implements both `chain` (id 2) and
`pipeline` (id 3) with a tunable `segmentsize` [OpenMPI 2024; Fagg 2006].

**Scatter + allgather ("van de Geijn" algorithm).** Scatter the \(n\)-byte message across \(p\)
processes, then allgather it back. Attributed by [Thakur 2005, §4.2] to van de Geijn and colleagues
[Barnett 1994; Shroff 1999]. With a binomial scatter (cost
\(\lg p\,\alpha + \frac{p-1}{p}n\beta\)) and a ring allgather:

\[
T_{\text{vandegeijn}} = (\lg p + p - 1)\alpha + 2\frac{p-1}{p}n\beta .
\]

Thakur et al. note the bandwidth term drops from \(n \lg p\, \beta\) to \(\approx 2n\beta\), so for
long messages and \(\lg p > 2\) (i.e. \(p > 4\)) it beats the binomial tree, with maximum expected
speedup \((\lg p)/2\). MPICH selects it for messages \(\ge\) 12 KB when \(p \ge 8\)
[Thakur 2005, §4.2; MPICH 2024b]; Open MPI exposes `scatter_allgather` (id 8) and
`scatter_allgather_ring` (id 9) [OpenMPI 2024]. Using a recursive-doubling allgather instead gives
\(2\lg p\,\alpha + 2\frac{p-1}{p}n\beta\) — better latency, but recursive doubling's non-neighbour
pattern loses bandwidth on real networks (see §2.4).

**Double / two-tree broadcast (Sanders, Speck, Träff).** [Sanders 2009] communicate concurrently over
*two* binary trees that both span the entire network, arranged so that "by careful layout and
communication scheduling, each tree communicates as efficiently as a single tree with exclusive use of
the network." A node that is an interior node in one tree is a leaf in the other, so its inbound and
outbound capacity is never idle. The algorithms "achieve up to *twice* the bandwidth of most previous
algorithms" and are "almost optimal for message sizes \(n\) with \(\beta \gg \alpha\)"; crucially,
both trees can carry the same in-order numbering, so the technique extends to **non-commutative
reduction and to scan** — where [Sanders 2009] state their approach "beats all previous algorithms".
The conference version is [Sanders 2007]. This is the algorithm to cite for a full-bandwidth
*pipelined* collective; contrast [Träff 2008], which gives round-optimal broadcast schedules for
fully connected one-ported networks.

**Split-binary tree.** Introduced by [Pjesivac-Grbovic 2007a], who state in a footnote that "to the
best of our knowledge, no other group implemented or discussed this algorithm so far" — so this, not
Open MPI's implementation, is the citable origin. The message is split in two; the left half is sent
down the left subtree of a binary tree and the right half down the right subtree, and in a final
phase every node exchanges its half with its mirror partner on the opposite side of the tree. When
the tree has an unpaired leaf, that leaf receives the second half directly from the root. The
algorithm is *segmented*: with total message size \(m\) split into \(n_s\) segments of size \(m_s\),
their Hockney-model cost is

\[
T_{\mathrm{sb}} = \left(\lceil \lg(P{+}1)\rceil + \left\lceil \tfrac{n_s}{2} \right\rceil - 2\right)
  \bigl(2\alpha(m_s) + m_s\beta(m_s)\bigr)
  \;+\; \alpha\!\left(\tfrac{m}{2}\right) + \tfrac{m}{2}\beta\!\left(\tfrac{m}{2}\right)
\]

[Pjesivac-Grbovic 2007a, Table 2], where the trailing term is the final pairwise half-message
exchange and the \(2\alpha(m_s)\) reflects each interior node both receiving and forwarding a segment.
The two subtrees each carry only \(m/2\), so the pipeline drains in \(\lceil n_s/2\rceil\) rather than
\(n_s\) segment steps — that halving is the whole point of the algorithm. The same paper gives
LogP/LogGP and PLogP variants. It is available in Open MPI as `split_binary_tree` (id 4)
[OpenMPI 2024; Fagg 2006]. Caveat from the authors' own data: [Pjesivac-Grbovic 2007b] report a
measured pathology for split-binary with 1 KB segments at 1448 B messages, where time "jumped to
300+ µs in comparison to 64+ µs" — a reminder that segmented pipelines interact badly with the
network MTU.

### 2.2 Reduce

**Binomial tree reduce.** The dual of binomial broadcast, with a local reduction at each arrival:

\[
T_{\text{tree}} = \lceil \lg p \rceil (\alpha + n\beta + n\gamma)
\]

[Thakur 2005, §4.5]. Latency-optimal; bandwidth and computation terms both a factor \(\lg p\) above
the bound. MPICH uses it for \(n \le\) 2 KB and, importantly, for **all** sizes when the operation is
user-defined, "because … the user may pass derived datatypes, and breaking up derived datatypes to do
the reduce-scatter is tricky" [Thakur 2005, §4.5].

**Rabenseifner's reduce (reduce-scatter + gather).** [Rabenseifner 2004] implements long-message
reduce as a reduce-scatter followed by a gather to the root — the exact dual of van de Geijn's
scatter+allgather broadcast. With recursive-halving reduce-scatter and binomial gather:

\[
T_{\text{rab}} = 2 \lg p\, \alpha + 2\frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma
\]

[Thakur 2005, §4.5]. This attains the computation lower bound and is within \(2\times\) of the
bandwidth bound, reducing the bandwidth term from \(n \lg p\,\beta\) to \(\approx 2n\beta\). MPICH
selects it for \(n >\) 2 KB with predefined operations; Open MPI exposes it as `rabenseifner`
(reduce id 7) [OpenMPI 2024].

Using Thakur et al.'s refined duplex model, the power-of-two cost is
\(T = \lg p(1+f_\alpha)\alpha + (1+f_\beta)n\beta + n\gamma - \frac{1}{p}\big((1+f_\beta)n\beta + n\gamma\big) \approx 2\lg p\,\alpha + 2n\beta + n\gamma\),
and the non-power-of-two cost degrades to
\(\approx (2 + 2\lfloor \lg p \rfloor)\alpha + 3n\beta + \tfrac{3}{2}n\gamma\)
[Thakur 2005, §5.1].

**Butterfly.** A recursive-halving/doubling exchange pattern (hypercube "bidirectional exchange")
underlies both reduce-scatter and allreduce; Open MPI lists `butterfly` explicitly for
`reduce_scatter` (id 4) and `reduce_scatter_block` (id 4) [OpenMPI 2024]. [Patarasuk 2009] call the
recursive-halving-reduce-scatter + recursive-doubling-allgather composition "the widely used
butterfly-like all-reduce algorithm" and note it "is optimal both in the latency term … and in the
bandwidth term" *when the network supports the butterfly pattern without contention* — which is
precisely the assumption that fails on SMP/multicore clusters.

### 2.3 Allreduce

**Recursive doubling.** In step \(k\) each process exchanges its full accumulated vector with the
partner at distance \(2^k\) and reduces:

\[
T_{\text{rec dbl}} = \lg p\,\alpha + n \lg p\,\beta + n \lg p\,\gamma
\]

[Thakur 2005, §4.5]. Latency-optimal, bandwidth- and computation-pessimal. MPICH uses it for short
messages and for long messages with user-defined operations. It is the best algorithm on a Cray T3E
900 for buffers \(\le\) 32 bytes [Thakur 2005, §5.4].

**Rabenseifner's allreduce (reduce-scatter + allgather).** [Rabenseifner 2004]. Recursive-halving
reduce-scatter (\(\lg p\,\alpha + \frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma\)) followed by
recursive-doubling allgather (\(\lg p\,\alpha + \frac{p-1}{p}n\beta\)):

\[
T_{\text{rab}} = 2 \lg p\, \alpha + 2\frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma
\]

[Thakur 2005, §4.5]. **This simultaneously attains the bandwidth and computation lower bounds of
[Chan 2007, Table I] and is within a factor 2 of the latency bound** — it is the reference
bandwidth-optimal allreduce. Used by MPICH, Open MPI (`rabenseifner`, allreduce id 6), and vendor MPIs.

**Recursive halving and doubling, non-power-of-two handling.** [Thakur 2005, §5.1] reduce \(p\) to
\(p' = 2^{\lfloor \lg p \rfloor}\) by eliminating \(r = p - p'\) processes: among the first \(2r\)
ranks, even ranks send the second half of their vector to rank+1 and odd ranks send the first half to
rank−1; each reduces its half; the odd ranks then ship their result back left and drop out. The
resulting costs are

\[
T_{\text{all},h\&d,\,p=2^k} = 2\lg p\,\alpha + 2n\beta + n\gamma - \tfrac{1}{p}(2n\beta + n\gamma)
\]
\[
T_{\text{all},h\&d,\,p\neq 2^k} \approx (3 + 2\lfloor \lg p \rfloor)\alpha + 4n\beta + \tfrac{3}{2}n\gamma
\]

i.e. the data-transfer overhead **doubles** and computation grows by \(3/2\) in the non-power-of-two
case. This is the single most important practical caveat about recursive halving/doubling and it
motivated Rabenseifner's **binary blocks** algorithm: decompose \(p\) into a sum of powers of two,
run recursive halving/doubling within each block, then combine blocks from smallest upward. The load
imbalance is governed by \(\delta_{expo,max}\), the maximal difference between consecutive exponents
in the binary representation of \(p\) (for \(p = 100 = 2^6 + 2^5 + 2^2\),
\(\delta_{expo,max} = \max(6-5, 5-2) = 3\)); binary blocks performs well when \(\delta_{expo,max}\)
is small, and is *identical* to recursive halving/doubling when \(p\) is a power of two
[Thakur 2005, §5.2; Rabenseifner 2004]. That 25% of Cray T3E 900 execution time was spent on runs
with non-power-of-two process counts [Rabenseifner 1999] is the empirical justification for caring.

**Ring allreduce.** Pairwise-exchange reduce-scatter followed by ring allgather:

\[
T_{\text{all,ring}} = 2(p-1)\alpha + 2n\beta + n\gamma - \tfrac{1}{p}(2n\beta + n\gamma)
\]

[Thakur 2005, §5.3]. Bandwidth-optimal in the limit and *independent of \(p\) in the bandwidth term*,
but latency scales linearly, so it "should be used only for small or medium number of processes or
for large vectors." Each process communicates only with its two ring neighbours, \(2(p-1)\) times
total.

**Rediscovery in deep learning.** [Patarasuk 2009] derive a tight lower bound on communicated data
and prove a ring-based allreduce bandwidth-optimal *requiring only tree connectivity*, by combining
three ingredients: (1) reduce-scatter + allgather [Rabenseifner 2004], (2) logical-ring reduce-scatter
and allgather, (3) a contention-free logical ring embedded in the physical tree topology. Their
motivation is explicitly that "the butterfly communication pattern can cause network contention in
many contemporary clusters, such as the widely deployed SMP/multi-core clusters." Baidu published a
TensorFlow fork implementing this in early 2017; Uber's Horovod [Sergeev 2018] adopted Baidu's draft
and later swapped in NCCL, and states plainly that "the algorithm was based on the approach introduced
in the 2009 paper by Patarasuk and Yuan." Each of \(N\) nodes communicates with two peers
\(2(N-1)\) times, sending/receiving \(2(N-1)M/N\) elements for a buffer of \(M\) parameters —
independent of \(N\), hence bandwidth-optimal. **The correct attribution chain for the paper is
therefore [Rabenseifner 2004] → [Patarasuk 2009] → Baidu/[Sergeev 2018], not "invented by Baidu."**

### 2.4 Scatter and gather

**Binomial (minimum-spanning-tree) scatter/gather.** \(\lceil \lg p \rceil\) steps; the root sends
half the data to its partner, which recurses:

\[
T = \lg p\,\alpha + \frac{p-1}{p}n\beta
\]

quoted for the scatter sub-phase of van de Geijn broadcast in [Thakur 2005, §4.2]. This attains
*both* the latency and bandwidth lower bounds of [Chan 2007, Table I] — scatter and gather are the
only collectives for which a single simple algorithm is simultaneously optimal in both terms. Gather
is the exact dual [Chan 2007, §6]. Open MPI: `binomial` (scatter id 2, gather id 2) [OpenMPI 2024].

**Linear scatter/gather.** \(T = (p-1)\alpha + \frac{p-1}{p}n\beta\) (derivation). Bandwidth-optimal
but latency-linear; retained because it needs no intermediate buffering and no store-and-forward, and
because for large \(n\) with a fast NIC the \(p\alpha\) term is irrelevant. Open MPI:
`basic_linear` (id 1), plus `linear_sync` variants that bound the number of outstanding requests
[OpenMPI 2024].

### 2.5 Allgather

Here \(n\) is the *total* gathered size, so each process contributes \(n/p\).

**Ring (bucket/cyclic).** \(p-1\) steps; in step \(i\) each process forwards to its right neighbour
the block it received from its left neighbour:

\[
T_{\text{ring}} = (p-1)\alpha + \frac{p-1}{p}n\beta
\]

[Thakur 2005, §4.1]. The bandwidth term is *exactly* the lower bound and "cannot be reduced further
because each process must receive \(n/p\) data from \(p-1\) other processes." Called the **bucket
(BKT)** algorithm in [Chan 2007, §7], which notes it views the nodes as a ring embedded in a linear
array, exploiting the fact that messages traversing a link in opposite directions do not conflict.

**Recursive doubling.** In step \(k\), processes at distance \(2^k\) exchange everything they hold:

\[
T_{\text{rec dbl}} = \lg p\,\alpha + \frac{p-1}{p}n\beta
\]

[Thakur 2005, §4.1.1]. Optimal in *both* terms for power-of-two \(p\). The catch is non-power-of-two
\(p\): MPICH performs extra logarithmic communication within non-power-of-two exchanging sets, and
"the total number of steps for the non-power-of-two case is bounded by \(2\lfloor \lg p \rfloor\)."
[Chan 2007] call the same pattern **bidirectional exchange (BDE)** and observe that BDE costs
"double when not using a power of two number of nodes," though in practice the doubling is partly
hidden because the node that must send twice differs at each recursion step.

**Bruck's algorithm.** [Bruck 1997], described by [Thakur 2005, §4.1.2] as a variant of the
dissemination barrier [Hensgen 1988]. In step \(k\), process \(i\) sends all data it currently holds
to \((i - 2^k)\) and receives from \((i + 2^k)\), appending at the end — the direction reversal
relative to dissemination is what keeps all communication **contiguous**. After \(\lfloor \lg p
\rfloor\) steps (plus one extra step sending the first \(p - 2^{\lfloor \lg p \rfloor}\) blocks if
\(p\) is not a power of two), a purely local downward shift by \(i\) blocks fixes the ordering:

\[
T_{\text{bruck}} = \lceil \lg p \rceil\,\alpha + \frac{p-1}{p}n\beta
\]

Bruck wins over recursive doubling for **non-power-of-two \(p\) and short messages** because it takes
\(\lceil \lg p \rceil\) steps in all cases; recursive doubling wins for power-of-two \(p\) because it
needs no local permutation. As \(n\) grows, Bruck's memory copies dominate.

**Neighbor exchange.** [Chen 2005] propose an algorithm for **even** \(p\) only: in step 0 each rank
exchanges one block with one neighbour; in every subsequent step it exchanges two blocks with one of
its two neighbours, alternating. It needs \(p/2\) rounds, so
\(T_{ne} = \tfrac{p}{2}\alpha + \frac{p-1}{p}n\beta\) [Chen 2005; Loch 2021] — half the latency of
ring at identical bandwidth. Chen et al. report it is best for long messages over TCP/IP on fast
Ethernet, with ring best for medium and recursive doubling best for short messages. Open MPI exposes
`neighbor` (allgather id 5); MPICH does not use it [Loch 2021].

**MPICH's actual selection** [Thakur 2005, §4.1.3]: Bruck for total gathered data < 80 KB and
non-power-of-two \(p\); recursive doubling for power-of-two \(p\) and < 512 KB; ring for
\(\ge\) 512 KB (any \(p\)) and for 80 KB–512 KB with non-power-of-two \(p\). The reason ring beats
recursive doubling for long messages is measured, not modelled: using the `b_eff` benchmark
[Rabenseifner 1999b] they found "for long messages … some communication patterns (particularly
nearest neighbor) achieve more than twice the bandwidth of other communication patterns" — a direct
violation of \(\alpha\)–\(\beta\) assumption (2).

### 2.6 Alltoall

Here \(n\) is the total data a process sends to (or receives from) all others.

**Bruck's index algorithm.** [Bruck 1997]. After a local upward rotation by \(i\) blocks, in step
\(k\) process \(i\) sends to \((i + 2^k)\) all blocks whose \(k\)-th bit is 1 and receives from
\((i - 2^k)\) into those same slots; a final local inverse shift restores order. For power-of-two
\(p\), \(n/2\) bytes move per step over \(\lg p\) steps:

\[
T_{\text{bruck}} = \lg p\,\alpha + \frac{n}{2}\lg p\,\beta
\]

and for non-power-of-two \(p\), the final step moves \(\frac{n}{p}(p - 2^{\lfloor \lg p \rfloor})\)
extra bytes:

\[
T_{\text{bruck}} = \lceil \lg p \rceil\,\alpha + \left(\frac{n}{2}\lg p + \frac{n}{p}\left(p - 2^{\lfloor \lg p \rfloor}\right)\right)\beta
\]

[Thakur 2005, §4.3]. Thakur et al.'s appraisal is worth quoting: "the beauty of the Bruck algorithm
is that it is a logarithmic algorithm for short-message all-to-all that does not need any extra
bookkeeping or control information for routing the right data to the right process — that is taken
care of by the mathematics of the algorithm."

**Pairwise exchange.** \(p-1\) steps; in step \(k\), process \(i\) exchanges directly with
\(i \oplus k\) (power-of-two \(p\)) or sends to \(i+k\) and receives from \(i-k\) (general \(p\)):

\[
T_{\text{long}} = (p-1)\alpha + n\beta
\]

[Thakur 2005, §4.3]. \(n\beta\) is the bandwidth lower bound for alltoall — no data is ever
store-and-forwarded.

**Spread-out / linear (isend–irecv).** Post all \(p-1\) `MPI_Irecv`s then all \(p-1\) `MPI_Isend`s
then `MPI_Waitall`, using destination \((\text{rank} + i) \bmod p\) rather than \(i\) to avoid all
processes hammering rank 0 first. Cost is nominally the same \((p-1)\alpha + n\beta\) but with
\(2(p-1)\) simultaneously outstanding requests, so real behaviour is governed by NIC queue depth and
congestion, not by the model. MPICH uses it in the *medium* range [Thakur 2005, §4.3]; Open MPI
offers `linear` (id 1) and `linear_sync` (id 4), the latter bounding in-flight requests via
`coll_tuned_alltoall_max_requests` [OpenMPI 2024].

**The \(O(p \log p)\) vs \(O(p)\) tradeoff.** Bruck sends \(\Theta(n \log p)\) bytes in
\(\Theta(\log p)\) messages; pairwise sends \(\Theta(n)\) bytes in \(\Theta(p)\) messages. The
crossover is where \(\tfrac{n}{2}\lg p\,\beta - n\beta = (p - 1 - \lg p)\alpha\), i.e. roughly
\(n \approx \frac{2p\alpha}{\beta(\lg p - 2)}\) (derivation). MPICH's measured thresholds:
Bruck for \(\le\) 256 bytes *per destination message*, isend–irecv for 256 B–32 KB, pairwise
exchange above 32 KB [Thakur 2005, §4.3], matching the current defaults
`MPIR_CVAR_ALLTOALL_SHORT_MSG_SIZE=256` and `MPIR_CVAR_ALLTOALL_MEDIUM_MSG_SIZE=32768`
[MPICH 2024b].

### 2.7 Reduce-scatter

**MPICH's original algorithm** was binomial reduce to rank 0 followed by linear scatterv:

\[
T_{\text{old}} = (\lg p + p - 1)\alpha + \left(\lg p + \frac{p-1}{p}\right)n\beta + n \lg p\,\gamma
\]

[Thakur 2005, §4.4] — bad in every term.

**Recursive halving (commutative operations).** The dual of recursive-doubling allgather. In step 1
each process exchanges with the process \(p/2\) away, sending the data needed by the other half,
receiving the data needed by its own half, and reducing; the exchanged volume halves each step. For
power-of-two \(p\):

\[
T_{\text{rec half}} = \lg p\,\alpha + \frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma
\]

which attains the latency, bandwidth *and* computation lower bounds simultaneously. For
non-power-of-two \(p\), MPICH first folds down to the nearest lower power of two (even ranks among
the first \(2r\) send to rank+1), giving

\[
T_{\text{rec half}} = (\lfloor \lg p \rfloor + 2)\alpha + 2n\beta + n\left(1 + \frac{p-1}{p}\right)\gamma
\]

which Thakur et al. describe as approximate "because some imbalance exists in the amount of work
each process does". Used for \(n \le\) 512 KB.

**Recursive doubling (non-commutative operations).** Recursive halving combines received partial
results out of operand order, so it is only valid when the operator commutes — [Thakur 2005, §4.4]
justify the reduction step with exactly that condition ("the reduction can be done because the
operation is commutative"). MPICH therefore switches to recursive doubling, exchanging in step \(j\)
everything except what is already accounted for (\(n - n/p\), then \(n - 2n/p\), then
\(n - 4n/p\), …):

\[
T_{\text{short}} = \lg p\,\alpha + n\left(\lg p - \frac{p-1}{p}\right)\beta + n\left(\lg p - \frac{p-1}{p}\right)\gamma
\]

Used only for very short messages (< 512 bytes) [Thakur 2005, §4.4]. This is a real cost: the
bandwidth term \(n(\lg p - \frac{p-1}{p})\beta\) is a factor \(\approx \lg p\) worse than the
optimum. [Träff 2005] removes most of that penalty with a butterfly-style reduce-scatter that
handles non-commutative operators for an *arbitrary* number of processes, at a cost of at least one
round beyond the optimal \(\lceil \lg p\rceil\) and \(n/2\) extra data.

**Pairwise exchange.** \(p-1\) steps; in step \(i\) each process sends to \(\text{rank}+i\), receives
from \(\text{rank}-i\), and reduces — moving only the \(n/p\) it actually needs:

\[
T_{\text{long}} = (p-1)\alpha + \frac{p-1}{p}n\beta + \frac{p-1}{p}n\gamma
\]

[Thakur 2005, §4.4]. Note this has **exactly the same bandwidth requirement as recursive halving** —
Thakur et al. nonetheless select it for long messages "because it performs much better than recursive
halving", the same nearest-neighbour bandwidth effect seen in allgather.

**Rabenseifner's contribution** to reduce-scatter is the vector-halving/distance-doubling formulation
and its non-power-of-two handling described in §2.3, plus the observation that reduce-scatter is the
shared bandwidth-optimal kernel of both reduce and allreduce [Rabenseifner 2004]. Open MPI's
`reduce_scatter` algorithm list is `non-overlapping` (1), `recursive_halving` (2), `ring` (3),
`butterfly` (4) [OpenMPI 2024].

### 2.8 Barrier

**Linear / centralised.** Gather notifications at a root, then broadcast release: \(2(p-1)\alpha\)
with a flat tree, or \(2\lceil \lg p \rceil \alpha\) with binomial trees (derivation). Open MPI
`linear` (id 1) and `tree` (id 6) [OpenMPI 2024].

**Butterfly barrier.** [Brooks 1986]. "A symmetric 'butterfly barrier', in which processors
participate as equals, performing the same operations at each step. Each processor in a butterfly
barrier participates in a sequence of \(\lceil \lg P \rceil\) pairwise synchronizations. In round
\(k\) (counting from zero), processor \(i\) synchronizes with processor \(i \oplus 2^k\)"
[Mellor-Crummey 1991, §3.3]. Cost \(\lceil \lg p \rceil \alpha\), but the XOR partnering requires
\(p\) to be a power of two.

**Dissemination barrier.** [Hensgen 1988]. In each step \(k\) (\(0 \le k < \lceil \lg p \rceil\)),
process \(i\) sends a zero-byte message to \((i + 2^k) \bmod p\) and receives from
\((i - 2^k) \bmod p\). Cost \(\lceil \lg p \rceil \alpha\), and — the decisive property — it is
correct for **any** \(p\), not just powers of two. It generalises the butterfly by replacing the
symmetric XOR partner with a directed wrap-around, and it is the direct ancestor of Bruck's allgather
[Thakur 2005, §4.1.2] and of MPICH's/Open MPI's `recursive_doubling` and `bruck` barriers
[OpenMPI 2024]. [Mellor-Crummey 1991] note it requires \(O(P \log P)\) total network transactions.

**Tournament barrier.** Also [Hensgen 1988]. Processes play a single-elimination tournament with
predetermined winners: in round \(k\), the "loser" signals its "winner" and spins; the champion then
releases the tree in reverse. Critical path \(\lceil \lg p \rceil\) rounds up plus wakeup, but only
\(O(P)\) total network transactions rather than \(O(P \log P)\)
[Mellor-Crummey 1991, §3.2, Algorithm quoted verbatim in the TOCS paper]. Mellor-Crummey and Scott's
measurement: "Beyond 16 processors, the additional factor of \(\log P\) in bus traffic for the
dissemination barrier dominates the higher constant of the tournament barrier. However, on scalable
multiprocessors with multistage interconnection networks, many of the network transactions required by
the dissemination barrier algorithm can proceed in parallel without interference." For a
message-passing network — AgentMPI's case — dissemination is therefore the right default.

**MCS tree barrier.** [Mellor-Crummey 1991, §3.5]. Two \(P\)-node trees: an *arrival* tree with
fan-in 4 and a *wakeup* tree with fan-out 2. Each processor spins only on locally accessible flag
variables. Properties, quoted: "spins on locally-accessible flag variables only; requires only
\(O(P)\) space for \(P\) processors; performs the theoretical minimum number of network transactions
(\(2P-2\)) on machines without broadcast; and performs \(O(\log P)\) network transactions on its
critical path." Critical path length \(\propto \lceil \log_4 P \rceil + \lceil \log_2 P \rceil\).
The \(2P-2\) bound is tight: at least \(P-1\) processors must signal arrival and \(P-1\) must be
told of wakeup.

### 2.9 Scan and Exscan

**Hillis–Steele (inclusive, "naive") scan.** [Hillis 1986] give the doubling formulation
`x[k] := x[k - 2^j] + x[k]` for \(j = 0, 1, \ldots\): \(O(\log N)\) steps on \(O(N)\) processors but
\(O(N \log N)\) total work. Hillis and Steele are careful to note the combining function "does not
depend on commutativity" — they wrote the update to preserve operand order deliberately, which
matters for MPI, whose `MPI_Scan` must respect rank order for non-commutative operations.

**Blelloch (work-efficient) scan.** [Blelloch 1989; Blelloch 1990] argue for scans as unit-time PRAM
primitives, showing they "improve the asymptotic running time of many algorithms by an
\(O(\log n)\) factor, greatly simplify the description of many algorithms, and are significantly
easier to implement than memory references." The work-efficient formulation is an up-sweep (reduce)
followed by a down-sweep, \(O(N)\) total work in \(O(\log N)\) depth. In a message-passing setting
this maps to reduce-then-broadcast-partials rather than to all-pairs doubling.

**Recursive doubling scan for MPI.** In step \(k\), process \(i\) exchanges its running partial with
\(i \pm 2^k\) and accumulates only if the partner is on the correct side:

\[
T_{\text{scan}} = \lceil \lg p \rceil (\alpha + n\beta + n\gamma)
\]

(derivation; this is the standard MPICH/Open MPI `recursive_doubling` scan, exscan id 2 and scan
id 2 [OpenMPI 2024]). [Sanders 2009] give the currently best full-bandwidth message-passing scan via
the two-tree construction, and explicitly claim their approach "beats all previous algorithms for
reduction and scan." Note the ordering constraint: `MPI_Scan` with a non-commutative op forbids the
free reassociation that recursive halving relies on, which is why scan has no Rabenseifner-style
bandwidth-optimal variant in MPICH.

---

## Collective algorithm cost summary

Costs are in the Hockney \(\alpha\)–\(\beta\)–\(\gamma\) model with a single-ported bidirectional
NIC. For allgather/alltoall/reduce-scatter, \(n\) is the total gathered/sent/reduced vector size.
Sources: [Thakur 2005] unless noted; entries marked (der.) are derivations in the stated model.

| Collective | Algorithm | Latency term | Bandwidth term | Computation term | Best regime |
|---|---|---|---|---|---|
| **Bcast** | linear / flat tree | \((p-1)\alpha\) | \((p-1)n\beta\) | — | tiny \(p\) only (der.) |
| Bcast | binomial tree | \(\lceil \lg p\rceil \alpha\) | \(\lceil \lg p\rceil n\beta\) | — | short \(n\) (<12 KB) or \(p<8\) |
| Bcast | binary tree | \(2(\lceil \lg(p{+}1)\rceil{-}1)\alpha\) | \(2(\lceil \lg(p{+}1)\rceil{-}1) n\beta\) | — | segmented/in-order variants (der.) |
| Bcast | k-nomial tree | \((k{-}1)\lceil \log_k p\rceil \alpha\) | \((k{-}1)\lceil \log_k p\rceil n\beta\) | — | high \(\lambda\)/overhead ratio (der., cf. [Bar-Noy 1994]) |
| Bcast | pipelined chain (opt. \(S\)) | \((p{-}2)\alpha + 2\sqrt{(p{-}2)n\alpha\beta}\) | \(n\beta\) | — | \(n\beta \gg p\alpha\), deep pipelines (der.) |
| Bcast | scatter + ring allgather (van de Geijn) | \((\lg p + p - 1)\alpha\) | \(2\frac{p-1}{p}n\beta\) | — | long \(n\), \(p \ge 8\) |
| Bcast | scatter + rec-dbl allgather | \(2\lg p\,\alpha\) | \(2\frac{p-1}{p}n\beta\) | — | medium \(n\), power-of-two \(p\) |
| Bcast | two-tree / double tree | \(O(\lg p)\alpha\) | \(\approx n\beta\) | — | \(\beta \gg \alpha\); full bandwidth [Sanders 2009] |
| Bcast | split binary tree (segmented, \(n_s\) segs of \(m_s\)) | \(\left(\lceil \lg(p{+}1)\rceil{+}\lceil \tfrac{n_s}{2}\rceil{-}2\right)2\alpha + \alpha\) | \(\left(\lceil \lg(p{+}1)\rceil{+}\lceil \tfrac{n_s}{2}\rceil{-}2\right)m_s\beta + \tfrac{m}{2}\beta\) | — | large \(n\), moderate \(p\); halves pipeline depth [Pjesivac-Grbovic 2007a] |
| **Reduce** | binomial tree | \(\lceil \lg p\rceil \alpha\) | \(\lceil \lg p\rceil n\beta\) | \(\lceil \lg p\rceil n\gamma\) | \(n \le\) 2 KB; all \(n\) for user-defined ops |
| Reduce | Rabenseifner (red-scat + gather) | \(2\lg p\,\alpha\) | \(2\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) | \(n >\) 2 KB, predefined ops |
| Reduce | Rabenseifner, \(p \ne 2^k\) | \((2{+}2\lfloor \lg p\rfloor)\alpha\) | \(3n\beta\) | \(\tfrac32 n\gamma\) | non-power-of-two; use binary blocks instead |
| **Allreduce** | recursive doubling | \(\lg p\,\alpha\) | \(n \lg p\,\beta\) | \(n \lg p\,\gamma\) | \(n \le\) 2 KB; user-defined ops |
| Allreduce | Rabenseifner (red-scat + allgather) | \(2\lg p\,\alpha\) | \(2\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) | long \(n\), power-of-two \(p\); **BW+comp optimal** |
| Allreduce | halving/doubling, \(p \ne 2^k\) | \((3{+}2\lfloor \lg p\rfloor)\alpha\) | \(4n\beta\) | \(\tfrac32 n\gamma\) | avoid; prefer binary blocks |
| Allreduce | binary blocks | \(\approx 2\lg p\,\alpha\) + block terms | \(\approx 2\frac{p-1}{p}n\beta\) | \(\approx \frac{p-1}{p}n\gamma\) | non-power-of-two \(p\) with small \(\delta_{expo,max}\) |
| Allreduce | ring (pairwise + ring allgather) | \(2(p{-}1)\alpha\) | \(2\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) | very large \(n\); small/medium \(p\); contention-free on trees [Patarasuk 2009] |
| **Scatter** | binomial (MST) | \(\lg p\,\alpha\) | \(\frac{p-1}{p}n\beta\) | — | all regimes; **latency+BW optimal** |
| Scatter | linear | \((p{-}1)\alpha\) | \(\frac{p-1}{p}n\beta\) | — | large \(n\), no intermediate buffering (der.) |
| **Gather** | binomial (MST) | \(\lg p\,\alpha\) | \(\frac{p-1}{p}n\beta\) | — | dual of scatter [Chan 2007] |
| **Allgather** | ring / bucket | \((p{-}1)\alpha\) | \(\frac{p-1}{p}n\beta\) | — | \(n \ge\) 512 KB, any \(p\); nearest-neighbour BW |
| Allgather | recursive doubling | \(\lg p\,\alpha\) | \(\frac{p-1}{p}n\beta\) | — | power-of-two \(p\), \(n <\) 512 KB; **both-term optimal** |
| Allgather | Bruck | \(\lceil \lg p\rceil \alpha\) | \(\frac{p-1}{p}n\beta\) | — | \(n <\) 80 KB and \(p \ne 2^k\) |
| Allgather | neighbor exchange | \(\tfrac{p}{2}\alpha\) | \(\frac{p-1}{p}n\beta\) | — | even \(p\), long \(n\) over TCP [Chen 2005] |
| **Alltoall** | Bruck (index) | \(\lg p\,\alpha\) | \(\tfrac{n}{2}\lg p\,\beta\) | — | \(\le\) 256 B per destination |
| Alltoall | spread-out isend/irecv | \((p{-}1)\alpha\) | \(n\beta\) | — | 256 B – 32 KB per destination |
| Alltoall | pairwise exchange | \((p{-}1)\alpha\) | \(n\beta\) | — | \(>\) 32 KB per destination; **BW optimal** |
| **Reduce-scatter** | reduce + scatterv (old) | \((\lg p{+}p{-}1)\alpha\) | \((\lg p {+} \frac{p-1}{p})n\beta\) | \(n\lg p\,\gamma\) | never (baseline) |
| Reduce-scatter | recursive halving, comm. | \(\lg p\,\alpha\) | \(\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) | \(n \le\) 512 KB, commutative; **all-term optimal** |
| Reduce-scatter | rec. halving, \(p\ne 2^k\) | \((\lfloor \lg p\rfloor{+}2)\alpha\) | \(2n\beta\) | \(n(1{+}\frac{p-1}{p})\gamma\) | non-power-of-two |
| Reduce-scatter | recursive doubling, non-comm. | \(\lg p\,\alpha\) | \(n(\lg p {-} \frac{p-1}{p})\beta\) | \(n(\lg p {-} \frac{p-1}{p})\gamma\) | \(n <\) 512 B, non-commutative |
| Reduce-scatter | pairwise exchange | \((p{-}1)\alpha\) | \(\frac{p-1}{p}n\beta\) | \(\frac{p-1}{p}n\gamma\) | long \(n\); best measured despite equal BW term |
| **Barrier** | linear (flat) | \(2(p{-}1)\alpha\) | — | — | tiny \(p\) (der.) |
| Barrier | binomial tree | \(2\lceil \lg p\rceil \alpha\) | — | — | general fallback (der.) |
| Barrier | butterfly | \(\lceil \lg p\rceil \alpha\) | — | — | power-of-two \(p\) [Brooks 1986] |
| Barrier | dissemination | \(\lceil \lg p\rceil \alpha\) | — | — | **any \(p\)**; \(O(p\lg p)\) transactions [Hensgen 1988] |
| Barrier | tournament | \(\approx 2\lceil \lg p\rceil \alpha\) | — | — | \(O(p)\) transactions; bus-based systems [Hensgen 1988] |
| Barrier | MCS tree | \(O(\lg p)\alpha\) crit. path | — | — | shared memory; \(2p{-}2\) transactions [Mellor-Crummey 1991] |
| **Scan/Exscan** | recursive doubling | \(\lceil \lg p\rceil \alpha\) | \(\lceil \lg p\rceil n\beta\) | \(\lceil \lg p\rceil n\gamma\) | default; order-preserving (der.) |
| Scan | two-tree pipelined | \(O(\lg p)\alpha\) | \(\approx n\beta\) | \(\approx n\gamma\) | large \(n\); full bandwidth [Sanders 2009] |

---

## 3. Algorithm selection and tuning

### 3.1 MPICH

Historically MPICH used **experimentally determined cutoff points** on message size and process
count [Thakur 2005, §1]. The published thresholds and current default control variables agree
closely: `MPIR_CVAR_BCAST_SHORT_MSG_SIZE=12288`, `MPIR_CVAR_BCAST_LONG_MSG_SIZE=524288`,
`MPIR_CVAR_BCAST_MIN_PROCS=8`, `MPIR_CVAR_ALLGATHER_SHORT_MSG_SIZE=81920`,
`MPIR_CVAR_ALLGATHER_LONG_MSG_SIZE=524288`, `MPIR_CVAR_ALLTOALL_SHORT_MSG_SIZE=256`,
`MPIR_CVAR_ALLTOALL_MEDIUM_MSG_SIZE=32768`, `MPIR_CVAR_REDUCE_SHORT_MSG_SIZE=2048`,
`MPIR_CVAR_ALLREDUCE_SHORT_MSG_SIZE=2048`, `MPIR_CVAR_GATHER_INTER_SHORT_MSG_SIZE=2048`,
`MPIR_CVAR_SCATTER_INTER_SHORT_MSG_SIZE=2048` [MPICH 2024b]. The documented broadcast decision reads:
"For short messages or when the number of processes is < `MPIR_CVAR_BCAST_MIN_PROCS`, we do broadcast
using the binomial tree algorithm. Otherwise, for medium messages and with a power-of-two number of
processes, we do broadcast based on a scatter followed by a recursive doubling allgather algorithm.
Otherwise, for long messages or with non power-of-two number of processes, we do broadcast based on a
scatter followed by a ring allgather algorithm."

Modern MPICH has restructured this. The collectives framework
[MPICH 2024a] places algorithm families in `src/mpi/coll/algorithms/` (`tree`, `recexchg`, `dissem`,
`ring`, `shm_gr`, hybrids), each implemented against a **transport API** with explicit task
dependencies, `TSP_task([args], sched, n_invtcs, invtcs)`, so that the same algorithm serves blocking
and nonblocking collectives (`COLL_kick_sched` vs `COLL_kick_sched_nb`) and multiple transports
(MPICH, OFI). Schedules are cached in a hash table keyed on the collective arguments plus algorithm
parameters, for reuse by persistent collectives. Selection is now via per-collective
`MPIR_CVAR_<COLL>_INTRA_ALGORITHM` selectors (with `auto` as default) plus a **JSON-encoded
decision tree** loaded from `MPIR_CVAR_COLL_SELECTION_TUNING_JSON_FILE`, with
`MPIR_CVAR_COLLECTIVE_FALLBACK` controlling behaviour when a forced algorithm cannot serve the
arguments and `MPIR_CVAR_DEVICE_COLLECTIVES` controlling device override [MPICH 2024c].

### 3.2 Open MPI: the `coll` MCA framework

The `coll` framework hosts components — `tuned`, `han`, `libnbc`, `base`, `hcoll`, `ucc`, `sync`,
`xhc`, `accelerator`, `basic`, `ftagree`, `inter`, `portals4`, `self` — selected at run time by
self-reported priority, with `base` always available as fallback [OpenMPI 2024]. The `tuned`
component has three modes:

- **fixed decision** (default): "a decision tree, essentially a large set of nested if-then-else-if
  blocks with baked in comm and message size thresholds derived by measuring performance on existing
  clusters";
- **forced algorithm**: `--mca coll_tuned_use_dynamic_rules 1 --mca coll_tuned_<coll>_algorithm N`,
  short-circuiting the fixed rules;
- **dynamic decision**: an ASCII rules file (`coll_tuned_dynamic_rules_filename`) defining, per
  collective, a function of (communicator size, message size) → (algorithm id, topo, segment size,
  max requests). Message-size rules must be ascending and must include a rule for size 0.

[Fagg 2006] describe the design rationale and note that "most algorithms" were modified to segment
user data into blocks so as to pipeline transfers, with `segmentsize` a tunable that "is however not a
simple factor of network MTU, sender overhead gap etc, and has to be benchmarked to find optimal
values." Additional knobs include tree fan-out and chain fan-in. The full algorithm enumerations per
collective are reproduced in §2 above from [OpenMPI 2024].

### 3.3 Automatically Tuned Collective Communication (ATCC) and STAR-MPI

[Vadhiyar 1999] introduced **automatically tuned collective communication**: run experiments
measuring each candidate algorithm under varying message size and process count, then use the winner
for each condition. [Thakur 2005, §2] cites this as the direct antecedent of their own
threshold-based approach.

[Faraj 2005] generate and tune MPI collective routines automatically. [Faraj 2006] present
**STAR-MPI** (Self Tuned Adaptive Routines for MPI), which differs in kind: rather than tuning
offline, "as an application executes, a STAR-MPI routine applies the Automatic Empirical Optimization
of Software (AEOS) technique at run time to dynamically select the best performing algorithm for the
application on the platform." Each routine keeps a set of candidate algorithms, measures them in
situ, and adapts — so it tunes to the *application workload* as well as the system architecture, and
can respond to conditions no offline benchmark reproduces.

The mechanism is worth stating precisely, because it is the closest existing analogue to what an
adaptive AgentMPI runtime would need. STAR-MPI is a *library layer* above MPI: `MPI_Alltoall` becomes
`STAR_Alltoall`, each routine backed by an algorithm repository (13 alltoall algorithms, four of them
used only for messages \(\le\) 256 bytes) plus a dynamic AEOS module. Tuning proceeds in two phases
[Faraj 2006, Fig. 2]:

1. **`Measure_Select`** — each candidate algorithm is run for `ITER` consecutive invocations of the
   call site and timed in situ; the best is selected.
2. **`Monitor_Adapt`** — the winner is used, but its running average is compared against the
   recorded best; a monitoring factor \(\delta\) (initialised to 2) and a switch threshold \(T\)
   govern when the routine re-enters measurement. \(\delta\) grows so that re-measurement becomes
   geometrically rarer, bounding steady-state overhead.

Critically, tuning is **per program context**, not per operation: STAR-MPI maintains \(N\)
independent instances of each routine so that distinct call sites — and the same call site invoked
with different message sizes — are tuned separately, since "MPI routines used in different program
contexts usually have different program behavior" [Faraj 2006]. The cost of this design is a warm-up
period proportional to the number of candidates, which is only amortised by applications that call
the same collective many times — exactly the assumption an iterative agent harness would also need to
justify runtime self-tuning.

### 3.4 Model- and machine-learning-based selection

[Pjesivac-Grbovic 2007a] compare analytical models (Hockney, LogP/LogGP, PLogP) against measurement
for MPI collectives. [Pjesivac-Grbovic 2007b] encode the two-dimensional (communicator size, message
size) decision surface as a **quadtree**, reporting that a 3-level quadtree costs at most a 12% mean
performance penalty and often under 5%, with the advantage that tree size — hence the maximum number
of predicates evaluated per decision — is directly bounded. [Pjesivac-Grbovic 2007c] instead learn
**C4.5 decision trees** from measured performance: "the Broadcast decision tree with only 21 leaves
was able to achieve a mean performance penalty of 2.08%", and a combined Reduce+Broadcast tree stayed
under 2.5%. The tradeoff they identify is precise: quadtrees give bounded evaluation depth but are
restricted to two dimensions and cannot express one-dimensional rules ("for this communicator size
and all message sizes use method A"); C4.5 handles arbitrary attributes automatically but its depth
cannot be bounded a priori. More recent work applies Bayesian optimisation to the Open MPI
`coll_tuned` parameter space [Jeannot 2024] and microbenchmark-derived models that switch collective
algorithms at runtime on Tianhe-2 [Zheng 2019].

### 3.5 Hierarchical and topology-aware collectives

The premise is that intra-node and inter-node communication have different \(\alpha\) and \(\beta\)
by orders of magnitude, so a flat algorithm is wrong. Early work: [Karonis 2000] exploit network
hierarchy; [Kielmann 1999] (MagPIe) minimise wide-area traffic at the expense of extra LAN traffic.

**Cheetah** [Graham 2011] is "a framework for scalable hierarchical collective operations" that
composes per-level collective components (a subgrouping framework plus a `ML` hierarchical component
in Open MPI) so each level of the machine hierarchy runs its own algorithm.

**HierKNEM** [Ma 2012; Ma 2013] is a kernel-assisted, topology-aware framework whose distinguishing
mechanism is *overlap*: leaders participate in the inter-node topology while intra-node data movement
is offloaded as single-copy KNEM memory copies to non-leader processes, balancing copy load across
cores. Reported results: immune to changes in process–core binding; up to 30× speedup over Open MPI,
MPICH2 and MVAPICH2 for messages between 8 and 256 KB in synthetic benchmarks; up to 3× on a parallel
graph application; and linear speedup with cores per node. IPDPS 2012 Best Paper.

**Mellanox/NVIDIA SHArP** [Graham 2016] offloads reduction to the network. The protocol defines
**aggregation nodes (ANs)** forming an aggregation tree mapped onto the physical topology, with a
Collective Functional Unit in each switch ASIC; data is reduced as it climbs the tree and the result
is distributed back down. Implemented in Mellanox's SwitchIB-2 ASIC, exploiting the switch's 36-port
radix to keep trees shallow, with up to 64 trees per aggregation node so multiple jobs and multiple
in-flight reductions coexist. Reported: 8-byte `MPI_Allreduce` on 128 hosts improved 2.1× (6.01 µs →
2.83 µs); 4096-byte `MPI_Allreduce` improved 3.24× by pipelining (46.93 µs → 14.48 µs). Structurally,
SHArP replaces an \(O(\log p)\)-round algorithm with a **two-phase** one (up to root, down to leaves)
[Graham 2016; Bureddy 2020]. Integration into MPI is normally via Open MPI's `hcoll` component.

**Cray Aries collective engine.** Each Aries NIC contains a CE block providing four virtual CEs, each
with its own reduction tree, supporting **branching ratios up to 32** so that "a tree of depth three
[covers] most system sizes." Reduction trees are built at job initialisation or on demand; partial
results are combined by the NICs on the way up and the result is scattered back down and written to
memory, with completion-queue events on each node. The design "reduces latency by avoiding
unnecessary host interface crossings and CPU-driven memory updates" and "does not require user
processes to be scheduled in order to progress a reduction; the entire network reduction is offloaded
to the NICs, reducing sensitivity to operating system jitter." It supports common integer and
floating-point reductions but **not** `MPI_PROD` [Cray 2014].

---

## 4. Point-to-point protocols and the progress engine

**Eager vs rendezvous.** [Brightwell 2013] state the tradeoff cleanly: "In the 'eager' protocol for
'short' messages, the entire message is sent to the receiver immediately to minimize latency. Since
the receiver may not have posted receives that match the sends, the receiver posts buffers to capture
unexpected messages. … In contrast, the rendezvous protocol for long messages simply sends a header
and transfers the data when a matching receive is known to be posted. This requires a round-trip
across the network to transfer a message — even if the receiver has already posted the matching
receive." The **eager threshold** is the switchover point; it is a memory-vs-latency knob, and
Brightwell et al.'s exascale argument is that rising bandwidth-delay product pushes buffering
requirements up while per-rank memory falls. [Chan 2007]'s \(\alpha_3 = 3\alpha_1\) is the
corresponding cost-model statement: a three-pass rendezvous (RTS → CTS → data) costs about three
times a one-pass eager send. [Ino 2001]'s \(S\) parameter is exactly the eager threshold made a model
parameter, and their \(T_4 = \max\{o + L, t_r - t_s\} + o\) is the rendezvous handshake's sensitivity
to sender/receiver skew.

**Unexpected and posted-receive queues.** Two receive-side queues are required: the **unexpected
message queue (UMQ)** and the **posted receive queue (PRQ)**. On arrival, the PRQ is searched; if no
match, an entry goes on the UMQ. On a receive call, the UMQ is searched; if no match, an entry goes on
the PRQ. "These message queues are required because senders are not required to synchronize with
receivers before sending small (Eager type) or control messages" [Dosanjh 2013]. The minimal search
key is the tuple \(\langle \text{contextId}, \text{rank}, \text{tag}\rangle\); implementations such
as Open MPI use a proper superset. Internal predefined tags are used even for collectives and RMA,
which have no user-visible tag. Queue *depth* is the performance problem: [Underwood 2004;
Brightwell 2005] show application UMQ search depths growing with node count, and
[Underwood 2005] propose a hardware acceleration unit for MPI queue processing. Tag-matching offload
is now shipped in NIC silicon (e.g. Mellanox ConnectX-5 tag matching and rendezvous offload); see
[Ferreira 2019] for a cross-implementation study (MPICH, Open MPI, plus LogGOPSim traces) of what
hardware matching designs actually need in terms of match-list length and memory capacity.

**Matching semantics.** MPI guarantees **non-overtaking** message order: messages between a given
sender/receiver pair on a given communicator with matching tags are matched in the order sent. This
ordering is what makes UMQ/PRQ searches *first-match* rather than best-match. `MPI_ANY_SOURCE`
and `MPI_ANY_TAG` are wildcards; they break the ability to pre-post a receive per peer, force full
queue traversal, defeat much hardware matching, and are the source of the *non-determinism* that
makes dynamic verification (§7) necessary. `MPI_Probe`/`MPI_Iprobe` and `MPI_Mprobe`/`MPI_Mrecv`
exist to inspect the UMQ before committing a buffer.

**The progress rule.** MPI requires that a nonblocking operation make progress once it has been
started and a matching operation posted, but it does not require an implementation to progress
without being entered. In practice, progress is *synchronous* — the application must call into MPI —
unless the implementation runs an asynchronous progress thread. `MPI_Test` is a local, nonblocking
poll that also drives the engine; `MPI_Wait` blocks until completion, freeing the request.

**Nonblocking collectives and the progress problem.** [Hoefler 2007a] built **LibNBC** on top of
MPI-1 to provide nonblocking versions of all MPI collectives, structured as round-based schedules.
The progress problem is inherent: LibNBC "implements the synchronous fashion and performs the
progress in `NBC_PROGRESS` or `NBC_PROGRESS_BLOCK`, which call MPI progress functions … the
transition from one round to another … is currently only implemented in a synchronous fashion, which
means that users should call `NBC_TEST` if they want the operation to progress in the background"
[Hoefler 2007b]. Asynchronous progress would require `MPI_THREAD_MULTIPLE`, "often inefficient in
common MPI implementations." So a nonblocking collective on a \(k\)-round schedule can stall for an
arbitrarily long time if the application does not test — **the overlap that nonblocking collectives
promise is not free; it must be paid for in explicit progress calls or a dedicated thread.** This is
the single most important lesson to carry into AgentMPI's design of nonblocking agent collectives.
Nonblocking collectives entered the standard in MPI-3.

**Persistent requests.** `MPI_Send_init`/`MPI_Recv_init` bind arguments once and produce a reusable
inactive request started by `MPI_Start`/`MPI_Startall`, amortising argument checking, buffer
registration, and (for persistent collectives, MPI-4) schedule construction. MPICH's transport API
caches collective schedules in a hash table precisely so persistent collectives can skip schedule
generation [MPICH 2024a].

**Partitioned communication (MPI-4).** Introduced as **finepoints** [Grant 2019] and standardised in
MPI-4. The standard's own framing: "Partitioned communication extends persistent point-to-point
communication … it allows for multiple contributions of data to be made, potentially, from multiple
actors (e.g., threads or tasks) in an MPI process to a single communication operation"
[MPIForum 2021]. Three fundamental differences from persistent point-to-point are enumerated there:
partitioned test calls can expose *partial* completion; all initialisation needed to enable transfer
may be done in the initialisation phase; and MPI can be notified independently of multiple send-side
contributions to a single buffer. The API is `MPI_Psend_init`/`MPI_Precv_init` → `MPI_Start` →
`MPI_Pready` (per partition, thread-safe, local, nonblocking) → `MPI_Wait`, with `MPI_Parrived` for
receive-side partial completion. Matching is by the order of local initialisation calls. Grant et al.
report up to 12× reduction in send wait time versus multithreaded-optimised Open MPI on a Cray XC40
with Aries, 26.1% communication-time improvement and 4.8% runtime improvement on a neutron-transport
proxy app. The motivation is that MPI's threading model "was designed as a process level interface,"
leading implementations to lock whole function calls.

---

## 5. One-sided / RMA semantics and algorithms

**Windows.** A window is a region of process memory exposed for remote access. MPI-3 provides four
collective creation calls: `MPI_Win_create` (expose existing memory), `MPI_Win_allocate` (allocate and
expose), `MPI_Win_create_dynamic` (attach/detach regions later), and `MPI_Win_allocate_shared`
(shared-memory windows) [Hoefler 2015; Gerstenberger 2013].

**Active target.** Two forms. *Fence*: `MPI_Win_fence` is a collective over the window group opening
and closing an access/exposure epoch — simple, and correct for bulk-synchronous phases.
*Post/start/complete/wait* (PSCW, "general active target"): the target calls `MPI_Win_post` to open an
exposure epoch and `MPI_Win_wait` to close it; the origin calls `MPI_Win_start`/`MPI_Win_complete`
around its accesses. PSCW restricts synchronisation to the actual neighbour set, which matters when
the communication graph is sparse.

**Passive target.** The target does not participate. `MPI_Win_lock`/`MPI_Win_unlock` bracket an
access epoch with `MPI_LOCK_SHARED` or `MPI_LOCK_EXCLUSIVE`; `MPI_Win_lock_all`/`MPI_Win_unlock_all`
open a shared lock on every rank in the window at once, giving the long-lived epoch that RDMA
hardware wants. Within such an epoch, completion is controlled by `MPI_Win_flush` (remote
completion at one target), `MPI_Win_flush_all`, `MPI_Win_flush_local` (local buffer reusable),
`MPI_Win_flush_local_all`, and `MPI_Win_sync` (reconcile public and private copies).

**MPI-3 memory model.** [Hoefler 2015] describe both models precisely. The **separate** model
"assumes systems where coherency is managed by software. In this model, remote updates target the
public copy and loads/stores target the private copy. Synchronization operations, such as
lock/unlock and sync, synchronize the contents of the two copies for a local window. The semantics do
not prescribe that the windows must be separate, just that they may be separate." The **unified**
model "relies on hardware-managed coherence. It assumes that the private and public copies are
identical; that is, the hardware automatically propagates updates from one to the other (without MPI
calls)." Unified places a lower burden on the programmer and permits full exploitation of RDMA
hardware; the window attribute `MPI_WIN_MODEL` reports which applies.

**Accumulate ordering.** Accumulate operations (`MPI_Accumulate`, `MPI_Get_accumulate`) to the same
target location from the same origin are ordered by default, and the `accumulate_ordering` info key
(values drawn from `rar`, `raw`, `war`, `waw`, or `none`) lets a program relax this; `same_op` and
`same_op_no_op` on `accumulate_ops` let the implementation assume operation homogeneity. Relaxing
ordering is what allows an implementation to issue accumulates as independent RDMA atomics rather
than serialising them.

**Atomics.** `MPI_Compare_and_swap`, `MPI_Fetch_and_op`, and the general `MPI_Get_accumulate` provide
element-wise atomicity for predefined datatypes and operations. These map to native RDMA atomic
verbs where the network provides them.

**Mapping to RDMA hardware.** [Gerstenberger 2013] (**foMPI**, SC'13 Best Paper) is the reference
implementation study: a complete MPI-3 RMA library for Cray Gemini (XE6/XK5) and Aries (XC30)
"implemented using scalable protocols requiring \(O(\log p)\) time and space per process." Their
assumptions define what RMA can portably require of hardware: "only small bounded buffer space at
each process, no remote software agent, and only put, get, and some basic atomic operations for
remote memory access, which is true for most RDMA hardware." They use DMAPP for inter-node traffic
and XPMEM for intra-node mapping, and implement **only the unified model** "since it is supported by
all current RDMA networks." The two design pressures worth carrying forward: window creation must
scale sub-linearly in memory, and asynchronous progression is only achievable by interfacing to the
lowest hardware API available.

---

## 6. MPI-IO and ROMIO

**Two-phase I/O.** [delRosario 1993] observed that parallel I/O performance "can vary by several
orders of magnitude as a function of the data access pattern," and proposed decoupling the *storage*
distribution from the *computational* distribution: in phase one, read using a **conforming
distribution** (one in which each processor's local array is contiguous in the file, so each
processor issues a single large request); in phase two, redistribute in-memory across the processor
array to the desired distribution. Because interconnect bandwidth vastly exceeds disk bandwidth, "the
cost of redistribution is a very small fraction of the overall access cost." [Thakur 1996] extended
the method to *sections* of out-of-core arrays — the **extended two-phase method** — adding request
merging, request reordering so the file is accessed in sequence, elimination of duplicate requests
for the same data, and dynamic division of the I/O workload among processors based on the actual
access request.

**Collective I/O in ROMIO.** [Thakur 1999] explain that collective I/O can be done at the disk level,
the server level (disk-directed I/O), or the client level (two-phase I/O); "since ROMIO is a
portable, user-level library with no separate I/O servers, it performs collective I/O at the client
level. For this purpose, it uses a generalized version of the extended two-phase method." The
generalisation matters: "MPI-IO requests can represent any access pattern, not just arrays," so the
array-specific two-phase method must be generalised to arbitrary noncontiguous requests. ROMIO
exposes the two critical parameters as **MPI-IO hints**: the number of processes that directly access
the file (the *aggregators*, `cb_nodes`) and the size of the temporary buffer (`cb_buffer_size`).

**Data sieving.** For a *single* process issuing many small noncontiguous requests, ROMIO reads one
large contiguous block spanning the first to the last byte requested into a temporary buffer and
extracts the needed pieces — trading extra bytes transferred for far fewer I/O operations. For
writes, data sieving requires a read–modify–write and therefore file locking on the sieved region.
The `ind_rd_buffer_size` and `ind_wr_buffer_size` hints bound the temporary buffer.

**File views.** `MPI_File_set_view` gives each process a private, possibly noncontiguous view of the
file expressed with MPI datatypes (an *etype* and a *filetype* tiled over the file from a
displacement). This is what makes a single collective call carry enough global information for the
library to optimise; without views, the library sees only a stream of small independent requests and
has nothing to aggregate.

**Why collective aggregation matters.** The whole point is that noncontiguous, small,
per-process requests are the worst case for any parallel file system, while large contiguous requests
from a bounded number of aggregators are the best case. Collective I/O converts the former into the
latter using interprocess communication, which is orders of magnitude cheaper than the I/O it saves
[Thakur 1999]. The identical argument applies to AgentMPI: aggregate small per-agent artifact writes
through designated aggregators rather than letting every agent touch the store.

**Generalized requests.** MPI-2's `MPI_Grequest_start` lets users add nonblocking operations that
complete through ordinary `MPI_Test`/`MPI_Wait`. [Latham 2007] identify the deficiency: the interface
gives the MPI implementation no way to *poll* the external operation, so "an extra progress thread is
always needed because the external asynchronous runtimes do not interoperate with MPI and have no
mechanisms to complete a generalized MPI request" [Zhou 2024]. They propose `MPIX_Grequest_start`
taking an extra `poll_fn` (and, in the shipped MPICH extension, a `wait_fn` accepting an array of
tasks) so that the MPI progress engine can query external state directly. ROMIO uses this extension.
This is precisely the generic-external-completion problem AgentMPI will face with LLM API calls.

---

## 7. Deadlock and correctness

**The classic unsafe pattern.** Two processes each execute `MPI_Send` to the other and then
`MPI_Recv`. "The message sent by each process must be copied somewhere before the send operation
returns and receive operation starts. For the program to complete, it is necessary that at least one
of the two messages be buffered. Thus this program will succeed only if the communication system will
buffer at least `count` words of data. Otherwise, the program will deadlock" [Snir 1998]. Ring shifts
where every process sends before receiving have the same cyclic dependency graph.

**The MPI "safe program" definition.** Quoted verbatim from the standard [MPIForum 1995, §3.5]:

> A program is "safe" if no message buffering is required for the program to complete. One can
> replace all sends in such program with synchronous sends, and the program will still run correctly.
> This conservative programming style provides the best portability, since program completion does not
> depend on the amount of buffer space available or in the communication protocol used.

The standard's stance on buffering is deliberate: "MPI takes the position that safe programs do not
rely on system buffering, and will complete correctly irrespective of the buffer allocation policy
used by MPI. Buffering may change the performance of a safe program, but it doesn't affect the result
of the program" [Snir 1998]. Lack of buffer space never makes a standard send *fail*; it makes it
*block*, which in well-constructed programs is a useful throttle and in ill-constructed ones is a
deadlock. Crucially, MPI does not enforce safety. The three portable remedies are nonblocking
communication (`MPI_Isend`/`MPI_Irecv` + `MPI_Wait`), buffered mode with an explicit
`MPI_Buffer_attach` (which converts deadlock into a diagnosable buffer-overflow error), and
`MPI_Sendrecv`, "explicitly designed to send to one process while receiving from another in a safe
and portable way" [Snir 1998].

**Why this matters for the eager threshold.** Because a *standard* send may or may not buffer, an
unsafe program routinely works below the eager threshold and deadlocks above it. The failure is
latent rather than introduced: the program is already unsafe under the standard's definition, and
increasing the message size merely exposes it. The standard is explicit that this is the expected
state of affairs — an exchange relying on buffering "can succeed only if the communication system can
buffer at least `count` words of data", while "quality implementations will provide sufficient
buffering so that 'common practice' programs will not deadlock" [MPIForum 1995, §3.5]. The safe
style is portable precisely because "program completion does not depend on the amount of buffer space
available or in the communication protocol used" [MPIForum 1995, §3.5]. Any protocol with a
size-dependent protocol switch — including AgentMPI — inherits this hazard class, and the mitigation
is to define safety as a program property in the [MPIForum 1995] sense, not to raise the threshold.

**Detection tools.** Three complementary points in the design space, characterised by
[Hilbrich 2013]:

- **MUST** [Hilbrich 2010; Hilbrich 2013]: runtime correctness checking with a modular,
  graph-based deadlock detector that scales to large process counts and reports **no false
  positives**, with graphical deadlock visualisation. It builds on a graph-theoretic AND-OR deadlock
  characterisation and on the GTI tools infrastructure, and checks type matching, race conditions and
  portability errors in addition to deadlock. It detects the send-send deadlock.
- **ISP** [Vakkalanka 2008]: dynamic *formal verification*. "ISP investigates all interleavings of
  send/recv pairs to verify deadlock freedom of nondeterministic MPI programs" using a **centralized
  scheduler** that re-executes the application once per interleaving. Complete coverage of
  schedule-dependent bugs — but "communication patterns often produce an exponential number of
  interleavings," and the centralised scheduler is not reported to scale beyond about a hundred
  processes.
- **DAMPI** [Vo 2010; Vo 2011]: removes ISP's central scheduler by distributed causality tracking
  (Lamport-clock-based), rewriting MPI calls according to an enumeration of interleavings to cover.
  Far more scalable. The cost, per [Hilbrich 2013], is that "DAMPI cannot detect the send-send
  deadlock or visualize deadlocks graphically and can give false positives due to the timeout-based
  deadlock detection."

**Non-deterministic replay.** The root cause of the difficulty is wildcard receives:
`MPI_ANY_SOURCE`/`MPI_ANY_TAG` mean the *same* program on the *same* input can take different match
decisions across runs, so a bug may not reproduce and a passing run proves nothing. This is exactly
what dynamic formal verifiers attack — ISP by centrally enumerating match choices, DAMPI by
distributed causality tracking — and it is the reason "it worked in testing" is not evidence of
correctness for an MPI program. AgentMPI, whose payloads come from stochastic LLM calls, has a
*strictly harder* version of this problem: non-determinism in message *content* as well as in match
order, so replay must record both the match decisions and the model outputs.

---

## Notes on attribution accuracy

Several attributions are commonly garbled in the literature; the corrected forms are:

1. **Rabenseifner's algorithm** is reduce-scatter + allgather (allreduce) / reduce-scatter + gather
   (reduce). Its first archival description is [Rabenseifner 2004]; [Thakur 2005] cite an earlier HLRS
   technical note. The bandwidth-optimal ring allreduce used in deep learning descends from
   [Rabenseifner 2004] via [Patarasuk 2009], not from Baidu.
2. **The van de Geijn broadcast** (scatter + allgather) is attributable to the InterCom work
   [Barnett 1994] and to [Shroff 1999], per [Thakur 2005, §4.2]; the CCPE survey [Chan 2007] is the
   canonical modern write-up, where it appears as MST-Scatter + BKT-Allgather.
3. **Bruck's algorithm** is two different algorithms in one paper [Bruck 1997]: a *concatenation*
   variant for allgather and an *index* variant for alltoall, with different costs.
4. **The dissemination barrier and the tournament barrier** are both from [Hensgen 1988]; the
   *butterfly* barrier is [Brooks 1986]; the *tree* barrier with local-only spinning is
   [Mellor-Crummey 1991].
5. **Chan et al.**: the author name is spelled *Purkayastha* in the Wiley record
   (CCPE 19(13):1749–1783, 2007); the earlier conference version is [Chan 2004] and there is a FLAME
   Working Note #22 / UT-Austin TR-06-44 preprint.
6. **LogGP** has two citable venues: SPAA 1995 [Alexandrov 1995] and JPDC 44(1):71–79, 1997
   [Alexandrov 1997]. [Thakur 2005] cite the JPDC version.
7. **Split-binary broadcast** is frequently cited only as an Open MPI implementation detail. It was
   introduced, named, and modelled by [Pjesivac-Grbovic 2007a], who claim originality explicitly.
8. **Non-commutative reduce-scatter** is often said to be impossible with butterfly algorithms.
   [Träff 2005] shows otherwise for arbitrary \(p\), at a cost of one extra round and \(n/2\) extra
   data; MPICH's recursive-doubling fallback [Thakur 2005] is the more expensive alternative.

---

## References

[Alexandrov 1995] Alexandrov, A., Ionescu, M. F., Schauser, K. E., and Scheiman, C. "LogGP:
Incorporating Long Messages into the LogP Model — One Step Closer Towards a Realistic Model for
Parallel Computation." In *Proceedings of the 7th Annual ACM Symposium on Parallel Algorithms and
Architectures (SPAA '95)*, Santa Barbara, CA, pp. 95–105, July 1995. Also UCSB Technical Report
TRCS95-09.

[Alexandrov 1997] Alexandrov, A., Ionescu, M. F., Schauser, K. E., and Scheiman, C. "LogGP:
Incorporating Long Messages into the LogP Model for Parallel Computation." *Journal of Parallel and
Distributed Computing* 44(1):71–79, 1997.

[Bar-Noy 1992] Bar-Noy, A., and Kipnis, S. "Designing Broadcasting Algorithms in the Postal Model for
Message-Passing Systems." In *Proceedings of the 4th Annual ACM Symposium on Parallel Algorithms and
Architectures (SPAA '92)*, pp. 13–22, 1992. DOI: 10.1145/140901.140903.

[Bar-Noy 1993] Bar-Noy, A., and Kipnis, S. "Multiple Message Broadcasting in the Postal Model." In
*Proceedings of the 7th International Parallel Processing Symposium (IPPS '93)*, 1993.
DOI: 10.1109/IPPS.1993.262831.

[Bar-Noy 1994] Bar-Noy, A., and Kipnis, S. "Designing Broadcasting Algorithms in the Postal Model for
Message-Passing Systems." *Mathematical Systems Theory* 27(5):431–452, 1994. DOI: 10.1007/BF01184933.

[Barnett 1994] Barnett, M., Gupta, S., Payne, D., Shuler, L., van de Geijn, R., and Watts, J.
"Interprocessor Collective Communication Library (InterCom)." In *Proceedings of the Scalable High
Performance Computing Conference (SHPCC '94)*, 1994.

[Blelloch 1989] Blelloch, G. E. "Scans as Primitive Parallel Operations." *IEEE Transactions on
Computers* 38(11):1526–1538, November 1989. DOI: 10.1109/12.42122.

[Blelloch 1990] Blelloch, G. E. *Vector Models for Data-Parallel Computing.* MIT Press, Cambridge,
MA, 1990. (See also Blelloch, G. E., "Prefix Sums and Their Applications," Technical Report
CMU-CS-90-190, School of Computer Science, Carnegie Mellon University, November 1990.)

[Brightwell 2005] Brightwell, R., Goudy, S., and Underwood, K. D. "A Preliminary Analysis of the MPI
Queue Characteristics of Several Applications." In *Proceedings of the 2005 International Conference
on Parallel Processing (ICPP '05)*, Oslo, Norway, 2005.

[Brightwell 2013] Brightwell, R., Barrett, B., Hemmert, K. S., et al. "Reducing MPI Memory Usage in
Exascale Networks." Sandia National Laboratories / OSTI 1109242, 2013.

[Brooks 1986] Brooks, E. D., III. "The Butterfly Barrier." *International Journal of Parallel
Programming* 15(4):295–307, 1986.

[Bruck 1997] Bruck, J., Ho, C.-T., Kipnis, S., Upfal, E., and Weathersby, D. "Efficient Algorithms
for All-to-All Communications in Multiport Message-Passing Systems." *IEEE Transactions on Parallel
and Distributed Systems* 8(11):1143–1156, November 1997.

[Bureddy 2020] Graham, R. L., Levi, L., Burredy, D., Bloch, G., Shainer, G., Cho, D., Elias, G.,
Klein, D., Ladd, J., Maor, O., Marelli, A., Petrov, V., Romlet, E., Qin, Y., and Zemah, I. "Scalable
Hierarchical Aggregation and Reduction Protocol (SHARP) Streaming-Aggregation Hardware Design and
Evaluation." In *High Performance Computing (ISC High Performance 2020)*, LNCS 12151, pp. 41–59,
Springer, 2020. DOI: 10.1007/978-3-030-50743-5_3.

[Chan 2004] Chan, E. W., Heimlich, M. F., Purkayastha, A., and van de Geijn, R. A. "On Optimizing
Collective Communication." In *Proceedings of the 2004 IEEE International Conference on Cluster
Computing*, San Diego, CA, pp. 145–155, September 2004. DOI: 10.1109/CLUSTR.2004.1392612.

[Chan 2007] Chan, E., Heimlich, M., Purkayastha, A., and van de Geijn, R. "Collective Communication:
Theory, Practice, and Experience." *Concurrency and Computation: Practice and Experience*
19(13):1749–1783, 2007. DOI: 10.1002/cpe.1206. (Preprint: FLAME Working Note #22, University of Texas
at Austin, Department of Computer Sciences, Technical Report TR-06-44, September 2006.)

[Chen 2005] Chen, J., Zhang, L., Zhang, Y., and Yuan, W. "Performance Evaluation of Allgather
Algorithms on Terascale Linux Cluster with Fast Ethernet." In *Proceedings of the Eighth International
Conference on High-Performance Computing in Asia-Pacific Region (HPC Asia 2005)*, pp. 437–442, 2005.
DOI: 10.1109/HPCASIA.2005.75.

[Cray 2014] Cray Inc. *Cray XC Series Network.* Cray Inc. White Paper WP-Aries01-1112, 2014.

[Culler 1993] Culler, D. E., Karp, R. M., Patterson, D. A., Sahay, A., Schauser, K. E., Santos, E.,
Subramonian, R., and von Eicken, T. "LogP: Towards a Realistic Model of Parallel Computation." In
*Proceedings of the 4th ACM SIGPLAN Symposium on Principles and Practice of Parallel Programming
(PPoPP '93)*, pp. 1–12, 1993. DOI: 10.1145/155332.155333. (See also Culler et al., "LogP: A Practical
Model of Parallel Computation," *Communications of the ACM* 39(11):78–85, 1996,
DOI: 10.1145/240455.240477.)

[delRosario 1993] del Rosario, J. M., Bordawekar, R., and Choudhary, A. "Improved Parallel I/O via a
Two-Phase Run-Time Access Strategy." In *Proceedings of the Workshop on I/O in Parallel Computer
Systems at IPPS '93*, pp. 56–70, April 1993. Also *ACM SIGARCH Computer Architecture News*
21(5):31–38, December 1993. DOI: 10.1145/165660.165667.

[Dosanjh 2013] Zounmevo, J. A., and Afsahi, A. "A Fast and Resource-Conscious MPI Message Queue
Mechanism for Large-Scale Jobs." *Future Generation Computer Systems* 30:265–290, 2014.
DOI: 10.1016/j.future.2013.07.003.

[Fagg 2006] Fagg, G. E., Pjesivac-Grbovic, J., Bosilca, G., Angskun, T., Dongarra, J., and Jeannot,
E. "Flexible Collective Communication Tuning Architecture Applied to Open MPI." In *Proceedings of the
13th European PVM/MPI Users' Group Meeting (EuroPVM/MPI 2006)*, Bonn, Germany, 2006. (Companion:
Fagg, G. E., Bosilca, G., Pjesivac-Grbovic, J., Angskun, T., and Dongarra, J. "Tuned: A Flexible
High-Performance Collective Communication Component Developed for Open MPI." In *Proceedings of
DAPSYS '06*, Innsbruck, Austria, pp. 65–72, Springer, September 2006.)

[Faraj 2005] Faraj, A., and Yuan, X. "Automatic Generation and Tuning of MPI Collective
Communication Routines." In *Proceedings of the 19th Annual International Conference on Supercomputing
(ICS '05)*, pp. 393–402, ACM, 2005. DOI: 10.1145/1088149.1088202.

[Faraj 2006] Faraj, A., Yuan, X., and Lowenthal, D. K. "STAR-MPI: Self Tuned Adaptive Routines for
MPI Collective Operations." In *Proceedings of the 20th Annual International Conference on
Supercomputing (ICS '06)*, Cairns, Australia, pp. 199–208, ACM, 2006. DOI: 10.1145/1183401.1183431.

[Ferreira 2019] Ferreira, K. B., Grant, R. E., Levenhagen, M. J., Levy, S., and Groves, T.
"Hardware MPI Message Matching: Insights into MPI Matching Behavior to Inform Design." *Concurrency
and Computation: Practice and Experience* 32(3):e5150, 2020. DOI: 10.1002/cpe.5150.

[Gerstenberger 2013] Gerstenberger, R., Besta, M., and Hoefler, T. "Enabling Highly-Scalable Remote
Memory Access Programming with MPI-3 One Sided." In *Proceedings of the International Conference on
High Performance Computing, Networking, Storage and Analysis (SC '13)*, Denver, CO, Article 53,
pp. 1–12, ACM, November 2013. DOI: 10.1145/2503210.2503286. (Best Paper.)

[Graham 2011] Graham, R. L., Gorentla Venkata, M., Ladd, J., Shamis, P., Rabinovitz, I., Filipov, V.,
and Shainer, G. "Cheetah: A Framework for Scalable Hierarchical Collective Operations." In
*Proceedings of the 11th IEEE/ACM International Symposium on Cluster, Cloud and Grid Computing
(CCGrid 2011)*, pp. 73–83, 2011. DOI: 10.1109/CCGrid.2011.42.

[Graham 2016] Graham, R. L., Bureddy, D., Lui, P., Rosenstock, H., Shainer, G., Bloch, G.,
Goldenberg, D., Dubman, M., Kotchubievsky, S., Koushnir, V., Levi, L., Margolin, A., Ronen, T.,
Shpiner, A., Wertheim, O., and Zahavi, E. "Scalable Hierarchical Aggregation Protocol (SHArP): A
Hardware Architecture for Efficient Data Reduction." In *Proceedings of the First International
Workshop on Communication Optimizations in HPC (COM-HPC 2016)*, pp. 1–10, IEEE, November 2016.

[Grant 2019] Grant, R. E., Dosanjh, M. G. F., Levenhagen, M. J., Brightwell, R., and Skjellum, A.
"Finepoints: Partitioned Multithreaded MPI Communication." In *High Performance Computing (ISC High
Performance 2019)*, LNCS 11501, pp. 330–350, Springer, 2019. DOI: 10.1007/978-3-030-20656-7_17.

[Hensgen 1988] Hensgen, D., Finkel, R., and Manber, U. "Two Algorithms for Barrier Synchronization."
*International Journal of Parallel Programming* 17(1):1–17, 1988.

[Hilbrich 2010] Hilbrich, T., Schulz, M., de Supinski, B. R., and Müller, M. S. "MUST: A Scalable
Approach to Runtime Error Detection in MPI Programs." In *Tools for High Performance Computing 2009*,
pp. 53–66, Springer, 2010. DOI: 10.1007/978-3-642-11261-4_5.

[Hilbrich 2013] Hilbrich, T., Protze, J., Schulz, M., de Supinski, B. R., and Müller, M. S. "MPI
Runtime Error Detection with MUST: Advances in Deadlock Detection." *Scientific Programming*
21(3–4):109–121, 2013. DOI: 10.1155/2013/314971.

[Hillis 1986] Hillis, W. D., and Steele, G. L., Jr. "Data Parallel Algorithms." *Communications of
the ACM* 29(12):1170–1183, December 1986. DOI: 10.1145/7902.7903.

[Hockney 1994] Hockney, R. W. "The Communication Challenge for MPP: Intel Paragon and Meiko CS-2."
*Parallel Computing* 20(3):389–398, March 1994.

[Hoefler 2007a] Hoefler, T., Lumsdaine, A., and Rehm, W. "Implementation and Performance Analysis of
Non-Blocking Collective Operations for MPI." In *Proceedings of the 2007 ACM/IEEE Conference on
Supercomputing (SC '07)*, ACM, November 2007. DOI: 10.1145/1362622.1362692.

[Hoefler 2007b] Hoefler, T., and Lumsdaine, A. "Design, Implementation, and Usage of LibNBC."
Technical report, Open Systems Laboratory, Indiana University, August 2007.

[Hoefler 2015] Hoefler, T., Dinan, J., Thakur, R., Barrett, B., Balaji, P., Gropp, W., and Underwood,
K. "Remote Memory Access Programming in MPI-3." *ACM Transactions on Parallel Computing* 2(2),
Article 9, 2015. DOI: 10.1145/2780584.

[Ino 2001] Ino, F., Fujimoto, N., and Hagihara, K. "LogGPS: A Parallel Computational Model for
Synchronization Analysis." In *Proceedings of the 8th ACM SIGPLAN Symposium on Principles and Practices
of Parallel Programming (PPoPP '01)*, pp. 133–142, 2001. DOI: 10.1145/379539.379592.

[Jeannot 2024] Jeannot, E., Lemarinier, P., Mercier, G., Robert-Hayek, S., and Sartori, R.
"Application-Agnostic Auto-Tuning of Open MPI Collectives Using Bayesian Optimization." In
*Proceedings of the 2024 IEEE International Parallel and Distributed Processing Symposium Workshops
(IPDPSW)*, San Francisco, CA, pp. 771–781, IEEE, 2024. DOI: 10.1109/IPDPSW63119.2024.00141. (The
fourth author's given name appears as "Sophie" in the IEEE and dblp records and as "Simon" in the
first author's own publication list; the initial is used here.)

[Karonis 2000] Karonis, N. T., de Supinski, B. R., Foster, I., Gropp, W., Lusk, E., and Bresnahan, J.
"Exploiting Hierarchy in Parallel Computer Networks to Optimize Collective Operation Performance." In
*Proceedings of the 14th International Parallel and Distributed Processing Symposium (IPDPS '00)*,
pp. 377–384, 2000.

[Kielmann 1999] Kielmann, T., Hofman, R. F. H., Bal, H. E., Plaat, A., and Bhoedjang, R. A. F.
"MagPIe: MPI's Collective Communication Operations for Clustered Wide Area Systems." In *Proceedings
of the 7th ACM SIGPLAN Symposium on Principles and Practice of Parallel Programming (PPoPP '99)*,
pp. 131–140, ACM, May 1999.

[Latham 2007] Latham, R., Gropp, W., Ross, R., and Thakur, R. "Extending the MPI-2 Generalized
Request Interface." In *Recent Advances in Parallel Virtual Machine and Message Passing Interface:
14th European PVM/MPI Users' Group Meeting*, Paris, France, LNCS 4757, pp. 223–232, Springer, 2007.

[Loch 2021] Loch, W. J., and Koslovski, G. P. "Sparbit: A New Logarithmic-Cost and Data
Locality-Aware MPI Allgather Algorithm." In *Proceedings of the 33rd IEEE International Symposium on
Computer Architecture and High Performance Computing (SBAC-PAD 2021)*, Belo Horizonte, Brazil,
pp. 167–176, IEEE, 2021. Preprint arXiv:2109.08751. (Extended version: "Sparbit: Towards to a
Logarithmic-Cost and Data Locality-Aware MPI Allgather Algorithm," *Journal of Grid Computing*
21(1):18, March 2023.) Cited here for its restatement of the neighbor-exchange cost
\(\tfrac{p}{2}\alpha + \frac{p-1}{p}n\beta\), which it attributes to [Chen 2005].

[Ma 2012] Ma, T., Bosilca, G., Bouteiller, A., and Dongarra, J. "HierKNEM: An Adaptive Framework for
Kernel-Assisted and Topology-Aware Collective Communications on Many-Core Clusters." In *Proceedings
of the 26th IEEE International Parallel and Distributed Processing Symposium (IPDPS 2012)*,
pp. 970–982, 2012. DOI: 10.1109/IPDPS.2012.91. (Best Paper.)

[Ma 2013] Ma, T., Bosilca, G., Bouteiller, A., and Dongarra, J. "Kernel-Assisted and Topology-Aware
MPI Collective Communications on Multicore/Many-Core Platforms." *Journal of Parallel and Distributed
Computing* 73(7):1000–1010, 2013. DOI: 10.1016/j.jpdc.2013.01.015.

[Mellor-Crummey 1991] Mellor-Crummey, J. M., and Scott, M. L. "Algorithms for Scalable
Synchronization on Shared-Memory Multiprocessors." *ACM Transactions on Computer Systems*
9(1):21–65, February 1991. DOI: 10.1145/103727.103729.

[MPICH 2024a] MPICH Project. "Collectives Framework." MPICH design documentation,
`doc/wiki/design/Collectives_framework.md`, pmodels/mpich, 2024.

[MPICH 2024b] MPICH Project. "README.envvar — MPICH Control Variables." MPICH distribution
documentation, 2024.

[MPICH 2024c] MPICH Project. `src/mpi/coll/src/coll_impl.c` — collective control variables
(`MPIR_CVAR_DEVICE_COLLECTIVES`, `MPIR_CVAR_COLLECTIVE_FALLBACK`,
`MPIR_CVAR_COLL_SELECTION_TUNING_JSON_FILE`). MPICH 5.0.1 source, 2024.

[MPIForum 1995] Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard,
Version 1.1.* University of Tennessee, Knoxville, June 1995. §3.5, "Semantics of Point-to-Point
Communication."

[MPIForum 2021] Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard,
Version 4.0.* University of Tennessee, Knoxville, June 2021. (Partitioned communication chapter;
see also MPI 4.1, November 2023.)

[OpenMPI 2024] The Open MPI Community. "11.10. Tuning Collectives (coll-tuned)." *Open MPI 5.0.x
Documentation*, 2024.

[Patarasuk 2009] Patarasuk, P., and Yuan, X. "Bandwidth Optimal All-Reduce Algorithms for Clusters of
Workstations." *Journal of Parallel and Distributed Computing* 69(2):117–124, 2009.
DOI: 10.1016/j.jpdc.2008.09.002.

[Pjesivac-Grbovic 2007a] Pjesivac-Grbovic, J., Angskun, T., Bosilca, G., Fagg, G. E., Gabriel, E.,
and Dongarra, J. "Performance Analysis of MPI Collective Operations." *Cluster Computing*
10(2):127–143, 2007. DOI: 10.1007/s10586-007-0012-0.

[Pjesivac-Grbovic 2007b] Pjesivac-Grbovic, J., Bosilca, G., Fagg, G. E., Angskun, T., and Dongarra,
J. "MPI Collective Algorithm Selection and Quadtree Encoding." *Parallel Computing* 33(9):613–623,
2007. DOI: 10.1016/j.parco.2007.06.005. (Conference version: EuroPVM/MPI 2006, LNCS 4192, pp. 40–48,
DOI: 10.1007/11846802_14.)

[Pjesivac-Grbovic 2007c] Pjesivac-Grbovic, J., Bosilca, G., Fagg, G. E., Angskun, T., and Dongarra,
J. "Decision Trees and MPI Collective Algorithm Selection Problem." In *Euro-Par 2007 Parallel
Processing*, LNCS 4641, pp. 107–117, Springer, 2007. DOI: 10.1007/978-3-540-74466-5_13.

[Rabenseifner 1999] Rabenseifner, R. "Automatic MPI Counter Profiling of All Users: First Results on
a CRAY T3E 900-512." In *Proceedings of the Message Passing Interface Developer's and User's
Conference (MPIDC '99)*, pp. 77–85, March 1999.

[Rabenseifner 1999b] Rabenseifner, R. "Effective Bandwidth (b_eff) Benchmark." High Performance
Computing Center Stuttgart (HLRS), 1999.

[Rabenseifner 2004] Rabenseifner, R. "Optimization of Collective Reduction Operations." In
*Computational Science — ICCS 2004*, LNCS 3036, pp. 1–9, Springer, 2004.
DOI: 10.1007/978-3-540-24685-5_1.

[Sanders 2007] Sanders, P., Speck, J., and Träff, J. L. "Full Bandwidth Broadcast, Reduction and Scan
with Only Two Trees." In *Recent Advances in Parallel Virtual Machine and Message Passing Interface:
14th European PVM/MPI Users' Group Meeting*, LNCS 4757, pp. 17–26, Springer, 2007.
DOI: 10.1007/978-3-540-75416-9_10.

[Sanders 2009] Sanders, P., Speck, J., and Träff, J. L. "Two-Tree Algorithms for Full Bandwidth
Broadcast, Reduction and Scan." *Parallel Computing* 35(12):581–594, December 2009.
DOI: 10.1016/j.parco.2009.09.001.

[Sergeev 2018] Sergeev, A., and Del Balso, M. "Horovod: Fast and Easy Distributed Deep Learning in
TensorFlow." arXiv:1802.05799, 2018.

[Shroff 1999] Shroff, M., and van de Geijn, R. A. "CollMark: MPI Collective Communication Benchmark."
Technical report, Department of Computer Sciences, University of Texas at Austin, December 1999.

[Skillicorn 1997] Skillicorn, D. B., Hill, J. M. D., and McColl, W. F. "Questions and Answers about
BSP." *Scientific Programming* 6(3):249–274, 1997.

[Snir 1998] Snir, M., Otto, S., Huss-Lederman, S., Walker, D. W., and Dongarra, J. *MPI: The Complete
Reference, Volume 1, The MPI Core.* 2nd edition, MIT Press, Cambridge, MA, 1998.

[Thakur 1996] Thakur, R., and Choudhary, A. "An Extended Two-Phase Method for Accessing Sections of
Out-of-Core Arrays." *Scientific Programming* 5(4):301–317, Winter 1996.

[Thakur 1999] Thakur, R., Gropp, W., and Lusk, E. "Data Sieving and Collective I/O in ROMIO." In
*Proceedings of the 7th Symposium on the Frontiers of Massively Parallel Computation (Frontiers '99)*,
pp. 182–189, IEEE Computer Society, February 1999. DOI: 10.1109/FMPC.1999.750599. Also Argonne
National Laboratory Preprint ANL/MCS-P723-0898.

[Thakur 2005] Thakur, R., Rabenseifner, R., and Gropp, W. "Optimization of Collective Communication
Operations in MPICH." *International Journal of High Performance Computing Applications*
19(1):49–66, Spring 2005. DOI: 10.1177/1094342005051521.

[Träff 2005] Träff, J. L. "An Improved Algorithm for (Non-Commutative) Reduce-Scatter with an
Application." In *Recent Advances in Parallel Virtual Machine and Message Passing Interface: 12th
European PVM/MPI Users' Group Meeting*, LNCS 3666, pp. 129–137, Springer, 2005.
DOI: 10.1007/11557265_20. (The author's own publication list gives pp. 130–138.)

[Träff 2008] Träff, J. L., and Ripke, A. "Optimal Broadcast for Fully Connected Processor-Node
Networks." *Journal of Parallel and Distributed Computing* 68(7):887–901, 2008.
DOI: 10.1016/j.jpdc.2007.12.001.

[Underwood 2004] Underwood, K. D., and Brightwell, R. "The Impact of MPI Queue Usage on Message
Latency." In *Proceedings of the 2004 International Conference on Parallel Processing (ICPP '04)*,
Montreal, Canada, pp. 152–160, 2004.

[Underwood 2005] Underwood, K. D., Hemmert, K. S., Rodrigues, A., Murphy, R., and Brightwell, R. "A
Hardware Acceleration Unit for MPI Queue Processing." In *Proceedings of the 19th IEEE International
Parallel and Distributed Processing Symposium (IPDPS '05)*, Denver, CO, 2005.

[Vadhiyar 1999] Vadhiyar, S. S., Fagg, G. E., and Dongarra, J. "Automatically Tuned Collective
Communications." In *Proceedings of the 1999 ACM/IEEE Conference on Supercomputing (SC '99)*,
November 1999. DOI: 10.1109/SC.1999.10006.

[Vakkalanka 2008] Vakkalanka, S. S., Sharma, S., Gopalakrishnan, G., and Kirby, R. M. "ISP: A Tool
for Model Checking MPI Programs." In *Proceedings of the 13th ACM SIGPLAN Symposium on Principles and
Practice of Parallel Programming (PPoPP '08)*, pp. 285–286, ACM, 2008. DOI: 10.1145/1345206.1345258.

[Valiant 1990] Valiant, L. G. "A Bridging Model for Parallel Computation." *Communications of the
ACM* 33(8):103–111, August 1990. DOI: 10.1145/79173.79181.

[Vo 2010] Vo, A., Aananthakrishnan, S., Gopalakrishnan, G., de Supinski, B. R., Schulz, M., and
Bronevetsky, G. "A Scalable and Distributed Dynamic Formal Verifier for MPI Programs." In
*Proceedings of the 2010 ACM/IEEE International Conference for High Performance Computing, Networking,
Storage and Analysis (SC '10)*, pp. 1–10, IEEE, 2010. DOI: 10.1109/SC.2010.7.

[Vo 2011] Vo, A. "Scalable Formal Dynamic Verification of MPI Programs through Distributed Causality
Tracking." Ph.D. dissertation, School of Computing, University of Utah, March 2011.

[Watts 1995] Watts, J., and van de Geijn, R. "A Pipelined Broadcast for Multidimensional Meshes."
*Parallel Processing Letters* 5(2):281–292, 1995.

[Zheng 2019] Zheng, W., Fang, J., Chen, J., Wu, F., Pan, X., Wang, H., Sun, X., Yuan, Y., Xie, M.,
Huang, C., Tang, T., and Wang, Z. "Auto-Tuning MPI Collective Operations on Large-Scale Parallel
Systems." In *Proceedings of the 21st IEEE International Conference on High Performance Computing and
Communications; 17th IEEE International Conference on Smart City; 5th IEEE International Conference
on Data Science and Systems (HPCC/SmartCity/DSS 2019)*, Zhangjiajie, China, pp. 670–677, IEEE, 2019.
DOI: 10.1109/HPCC/SmartCity/DSS.2019.00101.

[Zhou 2024] Zhou, H., Raffenetti, K., Guo, Y., and Thakur, R. "Designing and Prototyping Extensions
to the Message Passing Interface in MPICH." *International Journal of High Performance Computing
Applications* 38(5–6):411–428, 2024. DOI: 10.1177/10943420241263544. (Preprint arXiv:2402.12274.)
