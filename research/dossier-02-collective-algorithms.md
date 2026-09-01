# Dossier 02 — Collective communication algorithms: derivations, cost models, and selection

**Status:** source of truth for the AgentMPI paper's algorithm catalogue and for its central
technical argument.
**BibTeX:** `refs/02-algorithms.bib`.
**Scope:** Sections 1–4 survey the HPC literature, with every cost expression re-derived rather
than quoted. Section 5 is *new analysis for the AgentMPI paper* and is marked as such. Section 6
is the master table for adaptation into the paper. Section 7 lists what remains unverified.

---

## 0. Notation and the one-line thesis

| Symbol | Meaning |
| --- | --- |
| `p` | number of ranks (MPI processes / AgentMPI agents) |
| `n` | size of one contribution: bytes in MPI, **tokens** in AgentMPI |
| `α` | per-message latency (startup), independent of size |
| `β` | per-byte / per-token transfer cost |
| `γ` | **MPI:** per-element cost of the reduction operator. One application to an `n`-element vector costs `nγ`. |
| `γ₀` | **AgentMPI:** fixed cost of *one* operator invocation, independent of operand size |
| `γ₁` | **AgentMPI:** marginal per-token cost of an operator invocation |
| `R`, `M`, `V` | rounds (dependent steps), total messages, total data volume |
| `W`, `K` | **total** operator applications over all ranks; applications on the **critical path** |
| `C`, `r`, `U` | context window, reserved tokens, usable tokens `U = C − r` |
| `δ` | operator *expansion*: `\|a ⊕ b\| ≤ max(\|a\|,\|b\|) + δ` |
| `Γ` | operator dominance ratio, `Γ = γ₀ / (α + nβ)` |

`lg` is `log₂`; `lg p` without ceilings assumes `p` is a power of two.

**The thesis.** MPI's α–β–γ model charges a reduction `nγ` per application and has **no `γ₀`
term at all**. Setting `γ₀ = 0` is not an approximation in MPI — a vector add is a stream of
independent element operations with no per-call overhead worth modelling. For an LLM merge `γ₀`
is the entire cost, five to seven orders of magnitude above `α`. Hence:

> **Every MPI selection rule derived from the `α` term survives verbatim under `α → α + γ₀`.
> Every rule derived from the `β` term does not survive at all, because those rules assume the
> operator is divisible over the data and that its cost is proportional to operand size.
> Neither holds for an agent operator.**

Section 5 works this out case by case.

---

## 1. The cost models

### 1.1 Hockney: `α + nβ`

Hockney's two-parameter model comes from the COMMS1 ping-pong benchmark in the Genesis suite,
which fits round-trip time to `T = (n + n_½)/r_∞`; with `α = n_½/r_∞` and `β = 1/r_∞` this is
`T(n) = α + nβ` [`hockney1994communication`]. It asserts that cost is affine in size,
independent of the sender–receiver pair, and independent of how many disjoint pairs communicate
concurrently. Thakur, Rabenseifner and Gropp adopt exactly this for MPICH, adding bidirectional
links and single-ported interfaces, noting that although LogP and LogGP exist "this model is
sufficient for our needs" [`thakur2005optimization`]. Every MPICH expression in §2 is in this
model.

### 1.2 LogP, LogGP, pLogP

LogP [`culler1993logp`] splits `α` into `L` (latency), `o` (processor overhead, during which the
CPU can do nothing else), `g` (minimum gap between transmissions, whose reciprocal is per-processor
bandwidth) and `P`. The decisive move is separating occupancy from delay: only occupancy prevents
overlap. LogP assumes fixed-size small messages. LogGP [`alexandrov1995loggp`] adds `G`, the gap
per byte for long messages, so an `n`-byte message costs `o + (n−1)G + L + o`. Its showcase result
is that the LogGP-optimal single-node scatter is *qualitatively different* from the LogP-optimal
one — the standing demonstration that the cost model determines which algorithm you pick. pLogP
[`kielmann2000plogp`] makes `g` a piecewise-linear `g(n)`, absorbing eager/rendezvous protocol
switches; LogGOPS [`hoefler2010loggopsim`] splits `o` into send and receive overheads and adds a
per-message `O`.

### 1.3 Hoefler's critiques

Three strands matter. **Measurement discipline:** Hoefler and Belli survey 120 papers across three
top venues and find it routinely unclear whether a reported improvement is deterministic or a
sampling artefact [`hoefler2015benchmarking`]. Their collective-specific finding is that using
`MPI_Barrier` to synchronise a timed collective is unreliable, since neither MPI nor the hardware
guarantees a tight barrier; they recommend a broadcast-window scheme where a master distributes a
common future start time, and rank-based statistics over means, because completion times are
heavy-tailed under system noise. See also `hoefler2008measuring` on timing collectives at scale.

**The model must match the machine.** `hoefler2009sparse` and the neighbourhood-collective line
argue that topology-oblivious α–β mispredicts where the pattern interacts with the network. This
is exactly what Thakur et al. hit: recursive doubling and ring allgather have *identical* α–β
bandwidth terms, yet the ring is dramatically faster above 512 KB because it is nearest-neighbour
and recursive doubling is not [`thakur2005optimization`, `thakur2003improving`]. They confirmed it
with `b_eff`, finding nearest-neighbour patterns achieving more than twice the bandwidth of others
on both their Myrinet cluster and the IBM SP. **The model did not predict the crossover; the
measurement did, and the threshold was then hard-coded.** The AgentMPI paper should make the same
admission about its own thresholds.

### 1.4 Where `γ` sits — the crux

MPICH and Rabenseifner define `γ` as the reduction's computation cost **per byte**
[`thakur2005optimization`, `rabenseifner2004optimization`], so one application to an `n`-byte
vector costs `nγ`. Where `γ` appears is entirely determined by how many times the operator is
applied along the critical path and to how much data each time:

| Collective / algorithm | γ-term | Critical-path applications |
| --- | --- | --- |
| Reduce, binomial tree | `n ⌈lg p⌉ γ` | `⌈lg p⌉`, each on full `n` |
| Reduce, Rabenseifner | `((p−1)/p) n γ` | `lg p`, on `n/2, n/4, …` |
| Allreduce, recursive doubling | `n lg p γ` | `lg p`, each on full `n` |
| Allreduce, Rabenseifner | `((p−1)/p) n γ` | `lg p`, on shrinking chunks |
| Allreduce, ring | `n γ − nγ/p` | `p−1`, each on `n/p` |
| Reduce-scatter, recursive halving | `((p−1)/p) n γ` | `lg p`, on shrinking chunks |
| Reduce-scatter, pairwise exchange | `((p−1)/p) n γ` | `p−1`, each on `n/p` |
| Reduce-scatter, recursive doubling (non-commutative) | `n(lg p − (p−1)/p) γ` | `lg p` |
| Allgather, Bcast, Scatter, Gather, Alltoall, Barrier | *absent* | 0 |

Two structural facts, both load-bearing.

**Fact 1: the γ-term is a critical-path quantity, not a work quantity.** Recursive-doubling
allreduce performs `p·lg p` full-vector applications in total, but its γ-term is `n lg p γ` — the
model charges only the `lg p` on the path. The `p×` redundancy is invisible because in MPI it is
genuinely free: those applications run on otherwise-idle CPUs. MPI's model is single-objective.
AgentMPI's cannot be, because a redundant application is a redundant LLM call costing real tokens.

**Fact 2: every algorithm whose γ-term beats `n lg p γ` gets there by splitting the vector.**
Rabenseifner's `≈ nγ` comes from reduce-scatter — rank `i` reduces only chunk `i`. Ring allreduce
likewise. This requires `⊕` to apply independently to disjoint slices: trivial for `MPI_SUM` over
a `double` array, **false for an LLM merge.** You cannot have one agent merge the first half of two
arguments and another the second half and concatenate. So MPI's entire long-message reduction
repertoire is not merely slow for agents — it is *inapplicable*.

### 1.5 The AgentMPI model

Replace `nγ` with `γ₀ + n γ₁`. The critical path becomes

```
T  =  R·α  +  V_cp·β  +  K·γ₀  +  n_cp·γ₁
```

and a second objective, invisible to MPI, is the token bill `$ ∝ W·γ₀ + n_tot·γ₁`. MPI is the
special case `γ₀ = 0`. Concretely, for a frontier model doing a substantive merge: `γ₀ ≈ 30 s`,
`α ≈ 5 ms` when messages carry handles, `nβ ≈ 2 s` for 4,000 inline tokens at 2,000 tok/s. So
`Γ ≈ 6000` in the handle-passing regime and `≈ 15` inline. Both are γ-dominant; §5 needs only
`Γ ≫ 1`.

---

## 2. Algorithm derivations

`n` is the size of **one contribution** unless stated. This differs from Thakur et al., who use
`n` for the *total* gathered in allgather and alltoall; where I quote them I convert and flag it.
Links are bidirectional, nodes single-ported. A round is one dependent step; concurrent messages
within a round cost one `α`.

### 2.1 Barrier

No data, so `β` and `γ` vanish and everything is rounds versus messages.

**Central / counting.** All ranks signal a counter at rank 0, which releases at `p`. `R = 2`,
`M = 2(p−1)`; single-ported, the arrival phase serialises to `R = p−1` each way,
`T = 2(p−1)α`. Mellor-Crummey and Scott catalogue this as Θ(p) critical-path operations with an
unbounded number of remote operations [`mellorcrummey1991algorithms`]. **Linear** is the same
without the counter.

**Butterfly** [`brooks1986butterfly`]. In stage `s = 0 … lg p − 1`, rank `i` synchronises with
`i XOR 2^s`. `R = lg p`, `M = p lg p`, `T = lg p · α`. Brooks showed a barrier needs only reads
and writes — no locked counter, hence no hot spot, and shared-memory bandwidth growing linearly
rather than quadratically. It requires power-of-two `p`; otherwise absent ranks must be simulated
and the worst case degrades to `2⌊lg p⌋` pairwise synchronisations.

**Dissemination** [`hensgen1988barrier`]. In round `k`, rank `i` signals `(i + 2^k) mod p` and
waits on `(i − 2^k) mod p`. `R = ⌈lg p⌉`, `M = p⌈lg p⌉`, `T = ⌈lg p⌉ α`. The decisive property is
`⌈lg p⌉` rounds **for every `p`** — no folding, no simulated ranks. Hensgen, Finkel and Manber took
the pattern from an information-dissemination algorithm of Han and Finkel and applied it to Brooks'
barrier, adding double buffering (later superseded by sense reversal) to avoid re-initialising flags.

**Tournament** [`hensgen1988barrier`]. A fixed single-elimination tournament whose winners are
decided *in advance*, so no atomic operations are needed. Arrival: `⌈lg p⌉` rounds, `p−1` messages
(a loser drops out permanently). Wakeup is a broadcast: `⌈lg p⌉` rounds, `p−1` messages. So
`R = 2⌈lg p⌉`, `M = 2(p−1)`.

The dissemination/tournament trade is the cleanest small instance of the depth-versus-work tension
§5 generalises: **dissemination is half the depth and `log p` times the messages.** Hensgen et al.
measured the crossover on a Sequent Balance and found the tournament faster beyond a modest
processor count, because `Θ(p log p)` bus transactions eventually dominate the `Θ(log p)` path
[`mellorcrummey1991algorithms`]. Note that HPC resolved this *against* the log-depth algorithm once
the shared resource became scarce.

### 2.2 Broadcast

**Flat / linear.** `M = p−1`, `V = (p−1)n`; single-ported root gives `R = p−1`,
`T = (p−1)(α + nβ)`.

**Binomial tree.** Root sends to `root + p/2`; both recurse. `R = ⌈lg p⌉`, `M = p−1`,
`V = (p−1)n`, and since a full `n` crosses each level of the path,
`T_tree = ⌈lg p⌉ (α + nβ)` — MPICH's expression [`thakur2005optimization`]. Latency-optimal to a
constant, bandwidth-*pessimal*: `n lg p β` means the message crosses the path `lg p` times.

**Chain / pipeline.** Unsegmented, `R = p−1`, `T = (p−1)(α + nβ)`. Segmented into `s` chunks the
pipeline fills in `p−2` steps and drains in `s`:

```
T_chain(s) = (p − 2 + s)(α + (n/s)β)
```

Differentiating in `s` gives `s* = sqrt(nβ(p−2)/α)` and
`T* = (sqrt((p−2)α) + sqrt(nβ))² = nβ + 2·sqrt((p−2)αnβ) + (p−2)α`. For large `n` the leading term
is `nβ` — **asymptotically bandwidth-optimal**, since `nβ` is the irreducible cost of getting `n`
bytes into any one rank. The weakness is the `(p−2)α` fill.

**Scatter + allgather (van de Geijn)** [`barnett1994intercom`, `vandegeijn1991combine`,
`chan2007collective`]. Scatter into `p` chunks of `n/p`, then allgather. With binomial scatter and
ring allgather:

```
T_vdg = [lg p · α + ((p−1)/p) nβ] + [(p−1) α + ((p−1)/p) nβ]
      = (lg p + p − 1) α + 2((p−1)/p) n β
```

Thakur et al.'s expression verbatim. The bandwidth term drops from `n lg p β` to `≈ 2nβ`, a factor
`lg p / 2`, at the cost of raising latency from `lg p α` to `(lg p + p − 1)α`. A recursive-doubling
allgather instead gives `2 lg p · α + 2((p−1)/p) n β`, keeping log latency.

**Pipelined tree.** Segmenting a binary/binomial tree gives
`T ≈ (⌈lg p⌉ − 1 + s)(α + (n/s)β)`, interpolating between tree and chain. Open MPI carries
`split_binary_tree`, `binary_tree`, `binomial`, `knomial`, `chain` and `pipeline` as distinct
broadcast algorithms to cover this space [`openmpi2024tuned`].

**The crossover.** Tree beats scatter+allgather when
`⌈lg p⌉(α + nβ) < (lg p + p − 1)α + 2((p−1)/p)nβ`. Dropping `α` (long messages) and taking `p`
large, this reduces to `lg p · nβ < 2nβ`, i.e. `p < 4`. So for long messages and `p ≥ 4`
scatter+allgather wins, with maximum improvement `(lg p)/2` — larger `p`, larger gain, exactly as
Thakur et al. state. In the short-message limit the `α` terms decide and the comparison inverts to
`lg p < lg p + p − 1`, always true, so the tree wins.

### 2.3 Reduce

**Flat.** All ranks send to root, which applies `⊕` `p−1` times. The messages are one round but
the applications serialise: `K = W = p−1`, `M = p−1`, `V = (p−1)n`,
`T = (p−1)(α + nβ + nγ)` single-ported.

**Binomial tree.** In step `k` (`k = 0 … ⌈lg p⌉−1`), ranks with bit `k` set send to the partner
with that bit cleared, which applies `⊕`. `R = ⌈lg p⌉`, `M = p−1`, `V = (p−1)n`.

*Total applications:* each of the `p−1` messages triggers exactly one, so `W = p−1`.
*Critical path:* rank 0 receives once per step, so `K = ⌈lg p⌉`. Check at `p = 8`: rank 0 merges
ranks 1, 2, 4 → 3; rank 4 merges 5, 6 → 2; ranks 2 and 6 merge one each. Total `3+2+1+1 = 7 = p−1`,
critical path 3 = `lg 8`. ✔

```
T_tree = ⌈lg p⌉ (α + nβ + nγ)
```

**Chain.** Rank `i` merges the arrival from `i−1` with its own value and forwards. `R = p−1`,
`M = p−1`, `V = (p−1)n`, `W = p−1`, and crucially `K = p−1`: every application is on the path.

The tree and chain have **identical `W`, `M` and `V`** and differ only in `K` (`⌈lg p⌉` vs `p−1`).
This is the single most important comparison in this dossier for §5.

**Rabenseifner** [`rabenseifner2004optimization`, `rabenseifner1997reduce`]. Reduce-scatter by
recursive vector halving and distance doubling, then a binomial gather. Reduce-scatter costs
`lg p · α + ((p−1)/p)(nβ + nγ)`; the gather `lg p · α + ((p−1)/p) nβ`. Summing:

```
T_rab = 2 lg p · α + 2((p−1)/p) n β + ((p−1)/p) n γ
```

The γ-term falls from `n lg p γ` to `≈ nγ`: **the tree does `lg p` full-vector merges on the path,
Rabenseifner the equivalent of one**, because after step `k` each rank owns only `1/2^k` of the
result. This is the payoff that makes MPICH switch above 2 KB — and exactly the payoff that
requires a divisible operator.

### 2.4 Allreduce

**Recursive doubling.** In step `k`, rank `i` exchanges its full accumulator with `i XOR 2^k` and
applies `⊕`. `R = lg p`, `M = p lg p`, `V = p·n·lg p`,

```
T_recdbl = lg p · α + n lg p · β + n lg p · γ
```

matching the cost comment in MPICH's `allreduce_intra_recursive_doubling.c`
(`lgp.alpha + n.lgp.beta + n.lgp.gamma`) [`mpich2024source`].

*Why `p·lg p` applications, not `p−1`.* In each of the `lg p` steps **all `p` ranks** apply the
operator, because every rank is simultaneously the accumulation point of its own reduction. There
are `p` superimposed reduction trees, one rooted at each rank; they share messages but not operator
applications. So `W = p·lg p`, `K = lg p`, and

```
W_recdbl / W_red+bcast  =  p lg p / (p − 1)  ≈  lg p
```

MPI does not care: the extra `p lg p − (p−1)` vector adds run on stalled CPUs. **This is the largest
inversion in the AgentMPI setting**, tabulated in §5.2.

**Reduce + broadcast.** Binomial each way. `R = 2⌈lg p⌉`, `M = 2(p−1)`, `V = 2(p−1)n`, `W = p−1`,
`K = ⌈lg p⌉` (broadcast applies nothing). MPICH's old allreduce.

**Rabenseifner's allreduce.** Reduce-scatter (recursive halving) then allgather (recursive
doubling): `lg p α + ((p−1)/p)(nβ + nγ)` plus `lg p α + ((p−1)/p) nβ`, giving the same
`2 lg p α + 2((p−1)/p) nβ + ((p−1)/p) nγ`. For power-of-two `p` Rabenseifner also writes this as
`2 lg p α + 2nβ + nγ − (1/p)(2nβ + nγ)` [`rabenseifner2004optimization`].

**Ring allreduce.** Ring reduce-scatter (`p−1` steps, `n/p` sent and merged each) then ring
allgather (`p−1` steps, `n/p` each):

```
T_ring = 2(p−1) α + 2((p−1)/p) n β + ((p−1)/p) n γ
```

`R = 2(p−1)`, `M = 2p(p−1)`, and the per-rank volume `→ 2n` is **bandwidth-optimal**: Patarasuk and
Yuan derive a tight lower bound on communicated data and show this attains it, needing only tree
connectivity and remaining contention-free on SMP/multi-core and multi-switch Ethernet clusters
where the butterfly pattern contends [`patarasuk2009bandwidth`].

**Attribution, carefully.** The right academic citation is Patarasuk & Yuan, JPDC 69(2), 2009 — but
they are explicit that they *combine* three prior ingredients: allreduce as reduce-scatter +
allgather (Rabenseifner); ring reduce-scatter and ring allgather, attributed to earlier work
including Barnett et al.; and contention-free ring embedding on a tree, from their own IPDPS 2007
paper [`patarasuk2007tree`]. Their claimed contribution is the combination plus the optimality
proof. The 2017 "Baidu allreduce" [`gibiansky2017baidu`] is an engineering port to gradient
synchronisation, packaged as Horovod [`sergeev2018horovod`] and absorbed into NCCL. **Cite
Patarasuk & Yuan for the algorithm and Sergeev & Del Balso for the ML adoption; do not cite the
blog post as the origin.** Patarasuk and Yuan also record two caveats worth reusing: the ring is
optimal only in bandwidth, with rounds proportional to `p`; and it brackets the reduction
differently from a butterfly, which "may cause problems in the presence of rounding errors" (§4).

### 2.5 Allgather

Each rank contributes `n`; all end holding `p·n`. (Thakur et al. write `n` for the total `p·n`;
converted here.)

**Ring.** `R = p−1`, `M = p(p−1)`, per-rank volume `(p−1)n`, `T = (p−1)α + (p−1)nβ`. The bandwidth
term is irreducible — every rank must receive `n` from each of `p−1` others.

**Recursive doubling.** In step `k`, ranks a distance `2^k` apart exchange everything they hold:
`n`, `2n`, `4n`, …, `2^{lg p −1}n`. `R = lg p`, `M = p lg p`, per-rank volume
`Σ_{k=0}^{lg p −1} 2^k n = (p−1)n`, so

```
T_recdbl = lg p · α + (p−1) n β
```

**Same bandwidth term as the ring, exponentially better latency term** — yet the ring wins above
512 KB for the topology reason in §1.3. Non-power-of-two handling needs extra intra-step
communication and the step count is bounded by `2⌊lg p⌋`, a factor of two worse.

**Bruck** (the "concatenation" algorithm of [`bruck1997efficient`]). A variant of the dissemination
pattern: in step `k`, rank `i` sends to `(i − 2^k)` and receives from `(i + 2^k)` — the direction
is *reversed* relative to dissemination, which is precisely what makes all communicated data
contiguous. `R = ⌈lg p⌉` **for all `p`**, `M = p⌈lg p⌉`, per-rank volume `(p−1)n`, plus a final
local permutation. So Bruck matches recursive doubling's bandwidth and beats its non-power-of-two
latency (`⌈lg p⌉` vs `2⌊lg p⌋`), at the price of two memory permutations.

**Neighbour exchange.** For even `p`, ranks exchange with alternating left/right neighbours: `p/2`
rounds, two messages each. Purely nearest-neighbour, retaining the ring's topology advantage with
half the rounds; requires even `p`.

### 2.6 Scatter and Gather

**Linear.** `R = p−1` single-ported, `M = p−1`, `V = (p−1)n`; root resident `p·n`, others `n`.

**Binomial.** Root sends the far half (`(p/2)·n`) to the midpoint, which recurses. `R = ⌈lg p⌉`,
`M = p−1`. Total volume at `p = 8` is `4n + 2·2n + 4·n = 12n`, i.e. `V = (p/2)·lg p·n`; the root
sends `(p−1)n`. MPICH's expression with `N = pn` total is `lg p · α + ((p−1)/p) N β`
[`thakur2005optimization`]. Peak residency is the interesting part (§5.4): the rank owning a
subtree of size `s` transiently holds `s·n`, so **binomial gather forces intermediate ranks up to
`(p/2)·n`, whereas linear gather concentrates everything on the root and leaves all others at `n`.**

### 2.7 Alltoall

Each ordered pair exchanges `m`; per-rank total `n = p·m`.

**Linear (isend/irecv).** `R = p−1`, `M = p(p−1)`, `V = p(p−1)m`. MPICH scatters the loop index —
rank `i` communicates with `(i + k) mod p` at step `k` rather than with rank `k` — so ranks do not
all hammer rank 0 first [`thakur2005optimization`].

**Pairwise exchange.** Power-of-two `p`: at step `k`, rank `i` exchanges with `i XOR k`; otherwise
`i` receives from `i − k` and sends to `i + k`. No store-and-forward, so `V` is the `p(p−1)m`
minimum. `T_long = (p−1)α + nβ`.

**Bruck's index algorithm** [`bruck1997efficient`]. Rotate local blocks up by `i`; in step `k` send
every block whose `k`-th bit is set to `(i + 2^k)`, receive from `(i − 2^k)`, store into the same
bit-selected slots; finish with an inverse rotation. Half the blocks move each step, so per-rank
volume is `(n/2)lg p` rather than `n`:

```
T_bruck = lg p · α + (n/2) lg p · β
```

Non-power-of-two `p` moves `(n/p)(p − 2^⌊lg p⌋)` extra in the last step, giving
`⌈lg p⌉α + ((n/2)lg p + (n/p)(p − 2^⌊lg p⌋))β`. The canonical latency-versus-volume trade: `lg p`
rounds bought with `(lg p)/2×` the data, and, as Thakur et al. note, with no bookkeeping — routing
"is taken care of by the mathematics of the algorithm."

### 2.8 Reduce-scatter

Rank `i` ends with `⊕` over all contributions restricted to slice `i` of length `n/p`.

**Recursive halving** (commutative only). Step 1: exchange with the rank `p/2` away, sending the
half the partner needs and reducing the half you need; halve the exchanged volume each step.
`R = lg p`, `M = p lg p`, per-rank volume `((p−1)/p)n`:

```
T_rechalf = lg p · α + ((p−1)/p) n β + ((p−1)/p) n γ
```

**Recursive doubling** (non-commutative). Step 1 exchanges all but the rank's own `n/p`; step 2 all
but what the previous partner covered; and so on:

```
T_short = lg p · α + n(lg p − (p−1)/p) β + n(lg p − (p−1)/p) γ
```

Note the penalty for non-commutativity: bandwidth **and** γ jump from `≈ n` to `≈ n lg p`.

**Ring / pairwise exchange.** `p−1` steps; at step `i` send the slice `rank+i` needs, receive the
slice you need from `rank−i`, reduce locally: `T_long = (p−1)α + ((p−1)/p)nβ + ((p−1)/p)nγ`.
Identical bandwidth and γ to recursive halving with `p−1` rounds instead of `lg p` — and MPICH
still prefers it for long messages, again for the nearest-neighbour reason.

**Non-power-of-two: the `2^⌊lg p⌋ + r` folding trick.** Let `p' = 2^⌊lg p⌋`, `r = p − p'`. In the
first `2r` ranks, even ranks send to `rank+1` (in Rabenseifner's variant, even ranks send the second
half rightward and odd ranks the first half leftward); the odd ranks reduce and stand in for both,
leaving a clean power of two, with results sent back left at the end. MPICH's non-power-of-two
recursive-halving cost is

```
T_rechalf,npot = (⌊lg p⌋ + 2) α + 2nβ + n(1 + (p−1)/p) γ
```

which Thakur et al. call approximate because folded ranks do their neighbours' work too.
Rabenseifner and Träff later reduced this with 3-to-2 ("triple") and 2-to-1 ("double") elimination,
attaining optimal `O(log₂ p)` latency for `p = 2^n` and `p = 2^n·3`, and `O(log₂ p + 1)` otherwise
[`rabenseifner2004more`]. MPICH's recursive-doubling allreduce uses the simpler fold: even ranks
below `2r` send to `rank+1` and drop out, ranks are renumbered (`newrank = rank/2` below `2r`,
`rank − r` above), and the result is returned at the end [`mpich2024source`]. Folding costs one
extra operator application on the path — negligible as `nγ`, decisive as `γ₀` (§5.6).

### 2.9 Scan and Exscan

Rank `i` ends with `x₀ ⊕ … ⊕ xᵢ` (inclusive) or `… ⊕ xᵢ₋₁` (exclusive).

**Serial chain.** `R = p−1`, `M = p−1`, `V = (p−1)n`, `W = p−1`, `K = p−1`.

**Recursive doubling (Hillis–Steele)** [`hillis1986data`]. In round `k`, rank `i ≥ 2^k` receives
from `i − 2^k` and applies `⊕`. `R = ⌈lg p⌉`, and

```
M = Σ_{k=0}^{⌈lg p⌉−1} (p − 2^k) = p⌈lg p⌉ − (2^{⌈lg p⌉} − 1)
```

For power-of-two `p` this is `p lg p − (p−1)`. Each received message triggers one application, so
`W = p lg p − (p−1)` and `K = ⌈lg p⌉`. Check `p = 8`: rounds of 7, 6, 4 receives → `17 = 8·3 − 7`.
✔ Check `p = 6`: `5 + 4 + 2 = 11 = 6·3 − 7`. ✔

Hillis and Steele are careful on a point AgentMPI needs: they write the update as
`x[k] := x[k − 2^j] + x[k]` specifically so it generalises to any associative operator, noting "the
combining functions for all these operations happen to be commutative as well, but the algorithm
does not depend on commutativity. This was no accident." Recursive-doubling scan is therefore
**safe for a non-commutative agent merge** — which matters, since merging two partial
argument-summaries is emphatically not commutative.

**Blelloch's work-efficient up–down sweep** [`blelloch1990prefix`, `blelloch1989scans`, building on
`ladner1980parallel`]. Up-sweep: build a balanced binary tree of partial sums — `p−1` applications,
`lg p` rounds. Set the root to the identity. Down-sweep: at each node pass the incoming value left
and `incoming ⊕ left-child's stored value` right — one application and one swap per internal node,
`p−1` applications, `lg p` rounds. So `R = 2 lg p`, `M = 2(p−1)`, `W = 2(p−1)`, `K = 2 lg p`. This
yields an **exclusive** scan; inclusive adds one local application per rank (`W += p`, `K += 1`).

**The round-versus-work trade, explicitly.** Recursive doubling gives every prefix in `⌈lg p⌉`
rounds at the price of `p lg p − (p−1)` applications. Blelloch gives every prefix in `2 lg p` rounds
— twice the depth — at `2(p−1)` applications, asymptotically `(lg p)/2` times fewer. The serial
chain is cheapest in work (`p−1`) and worst in depth (`p−1`). For bytes, work is free and depth is
everything, so recursive doubling is the default. **For agents this ordering reverses** (§5.5).

---

## 3. Selection in real implementations

### 3.1 MPICH

MPICH's rules are experimentally determined cutoffs. The numbers below are from
[`thakur2005optimization`], cross-checked against control-variable defaults still shipping in the
source [`mpich2024source`].

| Collective | Rule | Threshold | CVAR / default |
| --- | --- | --- | --- |
| **Allgather** | Bruck | short msgs **and** non-power-of-two `p` | — |
| | recursive doubling | power-of-two `p`, short/medium | — |
| | ring | `≥ 512 KB` total gathered, any `p`; also `≥ 80 KB` and `< 512 KB` for non-pof2 `p` | `ALLGATHER_SHORT_MSG_SIZE = 81920`, `ALLGATHER_LONG_MSG_SIZE = 524288` |
| **Bcast** | binomial tree | `< 12 KB` **or** `p < 8` | `BCAST_SHORT_MSG_SIZE = 12288`, `BCAST_MIN_PROCS = 8` |
| | van de Geijn | otherwise | `BCAST_LONG_MSG_SIZE = 524288` picks ring vs rec.-doubling allgather inside |
| **Alltoall** | Bruck index | `≤ 256 B` per message | `ALLTOALL_SHORT_MSG_SIZE = 256` |
| | irecv/isend | `256 B – 32 KB` per message | `ALLTOALL_MEDIUM_MSG_SIZE = 32768` |
| | pairwise exchange | long messages | — |
| **Reduce-scatter** | recursive halving (commutative) | up to `512 KB` | `REDUCE_SCATTER_COMMUTATIVE_LONG_MSG_SIZE = 524288` |
| | recursive doubling (non-commutative) | `< 512 B` | — |
| | pairwise exchange | `≥ 512 KB` commutative, `≥ 512 B` non-commutative | — |
| **Reduce** | binomial tree | `≤ 2 KB`, **or any size if the op is user-defined** | `REDUCE_SHORT_MSG_SIZE = 2048` |
| | Rabenseifner | `> 2 KB` with a predefined op | — |
| **Allreduce** | recursive doubling | short messages, **or any size if the op is user-defined** | `ALLREDUCE_SHORT_MSG_SIZE = 2048` |
| | Rabenseifner | long messages with a predefined op | — |

The headline number: **MPICH switches from a binomial tree to Rabenseifner's reduce-scatter+gather
at 2,048 bytes for `MPI_Reduce`, and from recursive doubling to Rabenseifner's
reduce-scatter+allgather at 2,048 bytes for `MPI_Allreduce`** — but only for predefined operators.

The carve-out's reason, stated in the MPICH source, is the most directly relevant sentence in the
MPI literature for this paper:

> "We use this algorithm in the case of user-defined ops because in this case derived datatypes are
> allowed… Breaking up derived datatypes to do the reduce-scatter is tricky." [`mpich2024source`]

MPICH already has a rule of the form *"if the operator is opaque to the runtime, fall back to the
algorithm that never splits the operand."* AgentMPI's operators are **always** in that class. The
paper should present its reduction repertoire as the limit of MPICH's user-defined-operator branch,
not as a novel departure — a far stronger framing.

Two further data points. Allgather: recursive doubling below 512 KB, ring above, with the crossover
attributed to communication *pattern*, not to the model [`thakur2003improving`]. Allreduce on a Cray
T3E 900 with `MPI_SUM`/`MPI_DOUBLE`: recursive doubling best for buffers `≤ 32 bytes`; up to `1 KB`
the vendor algorithm (power-of-two) and binomial tree (non-power-of-two) are best but "not much
better than recursive doubling"; beyond that halving-and-doubling and binary-blocks take over, with
break-even points the authors give as "size = 1k and 2k and min((size/256)^{9/16}, …)"
[`thakur2005optimization`].

### 3.2 Open MPI's `coll` framework

Open MPI's collectives live in the MCA `coll` framework, with `tuned` the workhorse. It has three
modes [`openmpi2024tuned`, `fagg2006flexible`]: **fixed decision** (default) — a compiled tree of
nested if/else on communicator size and total message size, thresholds baked in from measurements on
real clusters; **forced** — an MCA parameter pins one algorithm; **dynamic** — a rules file supplies
runtime choices. The architecture is deliberately two-level: the first-level function has the MPI
signature so it must both decide and dispatch, then synthesise the extra arguments (tree fan-out,
segment size) the implementations need [`fagg2006flexible`].

The fixed thresholds are fine-grained. From `coll_tuned_decision_fixed.c` on v5.0.x
[`openmpi2024tuned`]:

- **Barrier** depends only on communicator size: recursive doubling for `p < 4`, linear for
  `4 ≤ p < 8`, recursive doubling for `8 ≤ p < 64`, Bruck for `64 ≤ p < 256`, tree for
  `256 ≤ p < 512`, Bruck for `512 ≤ p < 1024`, tree for `1024 ≤ p < 4096`, Bruck beyond.
- **Allreduce** has six algorithms (basic linear, nonoverlapping, recursive doubling, ring, segmented
  ring, Rabenseifner) and branches first on commutativity — "ring, segmented ring, and rabenseifner
  do not support non-commutative operations." For a commutative op with `8 ≤ p < 16`: recursive
  doubling below 8 KB, Rabenseifner at or above. For `128 ≤ p < 256`: nonoverlapping below 128 KB,
  recursive doubling to 256 KB, Rabenseifner beyond.
- **Bcast** carries nine algorithms with roughly a dozen size bands per communicator-size bucket.

The two implementations agree on shape (log-depth short, bandwidth-optimal long, special-case
non-commutative) and disagree substantially on numbers — **the thresholds are properties of the
machine, not of the algebra.**

### 3.3 Automatic tuning

Vadhiyar, Fagg and Dongarra established the empirical approach: run the algorithm space over
(message size, `p`) and tabulate the winner [`vadhiyar2000automatically`]. Pješivac-Grbović et al.
asked whether an analytic model can *predict* the winner, comparing Hockney, LogP/LogGP and PLogP as
predictors and feeding the results into FT-MPI's decision functions [`pjesivac2007performance`].
Their follow-up encodes the measured decision surface as a quadtree over the (size, `p`) plane; a
compiled 3-level quadtree had "comparable performance and higher accuracy than the default Open MPI
decision function," at under 75 ns per decision in-memory [`pjesivac2007quadtree`]. Faraj and Yuan
attack it from the other side: generate *topology-specific* routines from a network description, add
them to a repository alongside topology-oblivious ones, and select empirically
[`faraj2005automatic`].

For AgentMPI the lesson is the cost of a decision. 75 ns is free against a 5 µs collective. Against a
30-second operator application, an AgentMPI runtime can afford to spend *seconds* choosing — it could
run an LLM call to pick the algorithm and still come out ahead. The design space for selection is
qualitatively wider.

### 3.4 Hardware offload and in-network aggregation

SHArP moves the reduction into the switch ASIC [`graham2016sharp`]. An in-network tree is built at
job start; switches act as aggregation nodes, combining children's data into one vector and
forwarding only the result rootward, so data is injected once and volume shrinks monotonically
toward the root. Reported: 8-byte `MPI_Allreduce` on 128 hosts from 6.01 µs to 2.83 µs (2.1×), and
4,096-byte from 46.93 µs to 14.48 µs (3.24×) with pipelining. SHARPv2 added large-message streaming
aggregation [`graham2020streaming`].

The structural point, reused in §5.3: **SHArP only works for operators the switch can execute** —
sum, min, max, bitwise ops over fixed-width types. An arbitrary `MPI_Op` cannot be offloaded.
In-network aggregation is a statement about a *partition of the operator space* into
runtime-applicable and application-only, exactly the partition AgentMPI needs.

Hoefler's nonblocking collectives are the complementary lever: LibNBC gave nonblocking forms of every
collective layered on MPI-1, with a microbenchmark for overlap, and became MPI-3's `MPI_Ibcast` and
friends [`hoefler2007nbc`]. The topology-aware line [`hoefler2009sparse`] became MPI-3 neighbourhood
collectives.

---

## 4. Correctness and reproducibility

### 4.1 Associativity is a requirement, not an assumption

The MPI standard [`mpi41`]: "The operation `op` is always assumed to be associative. All predefined
operations are also assumed to be commutative… The 'canonical' evaluation order of a reduction is
determined by the ranks of the MPI processes in the group. However, the implementation can take
advantage of associativity, or associativity and commutativity in order to change the order of
evaluation. This may change the result of the reduction for operations that are not strictly
associative and commutative, such as floating point addition."

Associativity is a **contract the user must honour**, and the implementation's licence to re-bracket
derives from it. If your operator is not associative, MPI promises nothing.

### 4.2 The `commute` flag and what it forbids

`MPI_Op_create(function, commute, op)` [`mpi41`]: "If `commute = false`, then the order of operands
is fixed and is defined to be in ascending, process rank order, beginning with process zero. The
order of evaluation can be changed, taking advantage of the associativity of the operation."

So the implementation may **re-bracket but not re-order**. That rules out:

- **recursive halving reduce-scatter**, since each rank reduces slices in XOR-partner order, not rank
  order — Thakur et al. state flatly that "if the reduction operation is not commutative, recursive
  halving will not work (unless the data is permuted)";
- **Rabenseifner's algorithm** for reduce and allreduce, being built on reduce-scatter — hence Open
  MPI's "ring, segmented ring, and rabenseifner do not support non-commutative operations";
- **ring reduce-scatter and ring allreduce**, for the same reason;
- **the folding trick** as usually written, since it pairs rank `2i` with `2i+1` and renumbers.

What survives is the binomial tree, the chain, and recursive doubling with rank-ordered operand
placement (Hillis and Steele's care on this point, §2.9). MPICH's non-commutative reduce-scatter
fallback is exactly recursive doubling, at the cost of a bandwidth term inflating from `≈ n` to
`≈ n lg p`.

### 4.3 Why floating-point `MPI_SUM` is not bitwise reproducible

Floating-point addition is commutative but **not associative**. MPI's licence to re-bracket therefore
makes the result a function of the reduction tree shape, which depends on `p` (binomial topology), on
message size (which algorithm the selector picks — Rabenseifner brackets differently from a tree), on
process-to-node mapping, and in some implementations on arrival order. Changing `p` from 16 to 17
changes the answer in the last bits. Patarasuk and Yuan flag the same for the ring: results "are
computed with different 'bracketing,' which may cause problems in the presence of rounding errors"
[`patarasuk2009bandwidth`].

MPI's mitigation is an advice-to-implementors that the same arguments in the same order should give
the same result — conceding this "may prevent optimizations that take advantage of the physical
location of processors" — plus an advice-to-users to gather all operands to one rank, apply
`MPI_Reduce_local` in the desired order, and broadcast [`mpi41`]. That is: *if you need
reproducibility, do not use the collective.*

### 4.4 Reproducibility work

Demmel and Nguyen's reproducible summation makes the result independent of ordering by making the
*error* deterministic rather than controlling the order [`demmel2013fast`, `demmel2015parallel`].
Values are pre-rounded into fixed exponent "bins" so accumulation of binned numbers is exactly
associative; the algorithms are reproducible regardless of computation order, need two reductions
(reducible to one with precomputed bounds), and generalise to any operation built on summation. The
mature form is the binned accumulator of [`ahrens2020reproducible`], designed for reproducibility
under **both** data ordering and reduction-tree shape. ReproBLAS packages this; `binnedMPI.h` supplies
MPI reduction operators and datatypes for binned numbers so a user can sum "regardless of the
reduction tree shape used by MPI" [`reproblas`]. Its four-case taxonomy — fixed partitioning and
ordering, fixed blocks, arbitrary ordering, no control — maps almost exactly onto the AgentMPI
determinism question. Note also that neither compensated (Kahan) summation nor extended-precision
accumulation is offered by any commonly used MPI implementation [`pollard2020nondeterminism`], so
users get non-reproducible behaviour by default.

### 4.5 What this means for AgentMPI

An LLM merge is **not** associative, not even approximately: `merge(merge(a,b),c)` and
`merge(a,merge(b,c))` can differ in content, not merely in the last bits. AgentMPI cannot honestly
borrow MPI's contract. Three defensible positions, in decreasing strength:

1. **Fix the tree.** Make the reduction tree a function of `(p, op)` alone and part of the protocol,
   so the same job on the same `p` gives the same bracketing. This is the "fixed data ordering and
   partitioning" case of the ReproBLAS taxonomy and costs nothing. **Recommended default.**
2. **Declare the bracketing.** Have `AMPI_Op_create` take an associativity *claim* the way MPI takes
   a commutativity claim, and record the realised bracketing in the result's provenance.
3. **Ban re-bracketing.** Force the serial chain — rank order, left-associative — at `K = p−1`
   critical-path applications. This is exactly MPI's advice-to-users.

---

## 5. Analysis for the AgentMPI paper — re-deriving selection when the operator dominates

**This section is new analysis, not a survey.** Numeric illustrations use `γ₀ = 30 s`,
`α + nβ = 5 ms` (handle-passing), so `Γ = 6000`; the qualitative conclusions need only `Γ ≫ 1`.

### 5.1 Expensive reduction: the binomial tree wins, for a different reason

Compare the two reduce algorithms with identical `W`, `M` and `V`:

```
T_tree  = ⌈lg p⌉ (α + nβ + γ₀ + nγ₁)
T_chain = (p−1)   (α + nβ + γ₀ + nγ₁)
```

The ratio is `(p−1)/⌈lg p⌉`, **independent of every parameter**. At `p = 64` the tree is 10.5×
faster; at `p = 128`, 18×. Numerically at `p = 64`: tree `6 × 30.005 s ≈ 180 s`, chain
`63 × 30.005 s ≈ 1,890 s`.

MPI reaches the same conclusion for short messages from `⌈lg p⌉ α < (p−1) α`. AgentMPI reaches it
from `⌈lg p⌉ γ₀ < (p−1) γ₀`. **These are the same inequality with `γ₀` substituted for `α`.** That is
the general principle: `γ₀` occupies the structural position `α` occupies in MPI, so every
latency-derived MPI rule transfers under `α → α + γ₀`. AgentMPI gets to claim continuity rather than
novelty for the easy half of its catalogue — the right posture for an HPC PC.

The disagreement is at the other end. MPI abandons the tree above 2 KB for Rabenseifner, which under
the AgentMPI model gives

```
T_rab = 2 lg p · α + 2((p−1)/p) nβ + lg p · γ₀ + ((p−1)/p) n γ₁
```

— `lg p` invocations on shrinking operands. Against the tree's `⌈lg p⌉γ₀ + ⌈lg p⌉ n γ₁`, Rabenseifner
saves only on the `γ₁` term. When `γ₀ ≫ nγ₁` the two are within a few `α` of each other, and
Rabenseifner additionally demands a divisible operator it cannot have. **The 2 KB switch buys nothing
and costs applicability** — as MPICH's own user-defined-operator carve-out already says.

### 5.2 Recursive-doubling allreduce: badly wrong

MPI prefers recursive doubling for short-message allreduce precisely because redundant arithmetic is
free: it halves the depth (`lg p` rather than `2 lg p`) at zero cost. Under `γ₀ ≫ 0` that becomes a
`lg p`-fold cost multiplier.

| `p` | Rec.-doubling `W = p·lg p` | Reduce+bcast `W = p−1` | Ratio | RD depth `K` | R+B depth `K` | RD wall (γ₀=30 s) | R+B wall |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 24 | 7 | 3.43× | 3 | 6 | 90 s | 180 s |
| 16 | 64 | 15 | 4.27× | 4 | 8 | 120 s | 240 s |
| 32 | 160 | 31 | 5.16× | 5 | 10 | 150 s | 300 s |
| 64 | 384 | 63 | 6.10× | 6 | 12 | 180 s | 360 s |
| 128 | 896 | 127 | 7.06× | 7 | 14 | 210 s | 420 s |

The wall-clock advantage of recursive doubling is **fixed at exactly 2×** for all `p`, since `K` is
`lg p` versus `2 lg p`. The cost penalty is `p lg p/(p−1) ≈ lg p` and **grows without bound**. At
`p = 8` you pay 3.4× the tokens for 2× the speed; at `p = 128`, 7.1× for the same 2×. Under a
cost-latency product objective the crossover is `p lg p/(p−1) = 2`, i.e. `p ≈ 4`.

Two further nails. `M` is `p lg p` against `2(p−1)`, so if messages carry inline tokens the `β` term
is `lg p`-fold worse too. And MPI's *actual* reason for using recursive doubling on user-defined ops
is that it need not split derived datatypes — an availability argument, not a performance one.
AgentMPI inherits the availability argument but not the performance one, and should default to
**reduce+broadcast**, reserving recursive doubling for tiny `p` or hard latency deadlines.

### 5.3 In-network aggregation has an exact analogue

Partition the operator space by *who can execute `⊕`*:

- **Runtime-applicable:** set union, dictionary/JSON merge, max/min over a declared key, counters,
  append-to-list, deduplicating merge on a canonical id, schema-validated record merge. Total
  functions the runtime evaluates in microseconds.
- **Agent-only:** synthesise a coherent argument from two partial arguments; reconcile conflicting
  analyses; summarise-and-compress. These require an LLM call.

For a runtime-applicable `⊕`, each rank appends to a **shared journal** that folds on write:

```
R = 1  ·  M = 0 agent-visible  ·  W_agent = 0  ·  K_agent = 0  ·  W_runtime = p − 1
T ≈ α_write + (p−1)·γ_runtime
```

against a binomial tree's `⌈lg p⌉ γ₀`. At `p = 8`: `≈ 1 ms` versus `90 s`, a factor of about `10⁵`.
The correspondence to SHArP is exact — data injected once, aggregation in the shared substrate rather
than at endpoints, only the folded result propagating — and so is the limitation: it applies **only**
to operators the substrate can execute [`graham2016sharp`].

The design implication is the highest-leverage decision in the protocol: **AgentMPI should require
every `AMPI_Op` to declare whether it is runtime-applicable, and push authors hard toward
runtime-applicable formulations.** An operator written as "have an agent combine these findings" when
it could have been "union these evidence sets" costs `⌈lg p⌉ · 30 s` instead of a millisecond. This
is the SHARP regime, and unlike SHArP it needs no hardware — only a schema.

### 5.4 The context-window constraint: infeasibility, not slowness

This has **no MPI analogue**. In MPI, insufficient memory is a provisioning problem external to the
collective. In AgentMPI the context window `C` is fixed by the model, and an algorithm whose peak
resident data exceeds `U = C − r` is not slow — it **cannot run**. Selection becomes two-stage:
filter for feasibility, then optimise.

Let `n` be per-contribution tokens, `h` a *handle* (URI + digest + one-line descriptor, `h ≈ 80`
tokens), `k` the number of handles a rank dereferences, `δ` the operator's additive expansion.
Running example: `C = 200,000`, `r = 20,000`, `U = 180,000`, `n = 4,000`, `h = 80`, `δ = 500`.

**Allgather, inline.** Ring after step `k` holds `(k+1)n`; recursive doubling `2^{k+1}n`; Bruck
likewise; all reach `p·n`.

```
peak = p·n            feasible iff  p ≤ U/n  =  45
```

**The bound is identical for every allgather algorithm** — a small theorem worth stating: *no choice
of allgather algorithm changes peak residency, because the postcondition pins it.* The only escape is
representational.

**Allgather, handle-based.**

```
peak = p·h + k·n      feasible iff  p ≤ (U − k·n)/h
```

With `k = 5`: `p ≤ (180,000 − 20,000)/80 = 2,000`. A **44× increase in feasible rank count** from one
representational change — the single most important consequence of treating context as the scarce
resource, and one with no MPI counterpart, since bytes cannot be replaced by references to bytes
without changing the collective's semantics.

**Broadcast.** `peak = n`, **independent of `p`**. Broadcast never becomes infeasible as the job
scales; it is the safe collective.

**Gather.** Linear: root `p·n`, every other rank `n`. Binomial: the rank owning a subtree of size `s`
holds `s·n`, so root `p·n`, level-1 child `(p/2)·n`, and so on. Both are bounded by `p·n` at the root,
so under homogeneous `C` they are equally feasible — but the *second*-largest residency differs by
`p/2`, and totals differ: at `p = 8`, linear needs `8n + 7n = 15n` summed across ranks, binomial
`8n+4n+2n+2n+n+n+n+n = 20n`. Under heterogeneous contexts — one small-context worker —
**binomial gather can fail where linear succeeds**, and it always consumes more total context. MPI
prefers binomial for its `lg p` latency; AgentMPI should prefer linear whenever `p·(α + nβ)` is small
relative to `γ₀`, which under handle-passing it always is. Another inversion.

**Reduce, size-preserving `⊕`.** A rank holds its accumulator plus one incoming operand:
`peak = 2n`, **independent of `p`**. Allreduce via reduce+broadcast likewise; recursive-doubling
allreduce also holds `2n`. So **the objection in §5.2 is purely economic, not a feasibility
objection** — the two arguments are independent and should stay separate in the paper.

**Reduce, `δ`-expanding `⊕`.** On a tree of depth `d` the root's result is bounded by `n + δd` and
peak residency by roughly `2(n + δd)`:

```
binomial:  2n + δ⌈lg p⌉    feasible iff  p ≤ 2^((U − 2n)/δ)
chain:     2n + δ(p−1)     feasible iff  p ≤ (U − 2n)/δ + 1
```

With the running numbers the chain fails at `p = 345`; the tree at `p = 2^344`, i.e. never. **The
tree is exponentially better in feasibility, on top of being `(p−1)/lg p` faster and identical in
total cost** — it dominates the chain on all three axes simultaneously. A clean, checkable result and
a good one to lead the evaluation with.

**Alltoall.** `peak = 2pm`, growing linearly in `p` at fixed per-pair `m`; feasible iff
`p ≤ U/(2m)`. **Reduce-scatter.** `2n` during, `n/p` after — the only collective whose output
*shrinks* with `p`. **Scan.** With `δ`-expansion, the serial chain gives `n + δ(p−1)` at the last
rank; recursive doubling and Blelloch give `n + δ·O(lg p)`. Same exponential separation as reduce.

### 5.5 Scan: the round-versus-work trade runs the other way

| `p` | Hillis–Steele `W = p lg p − (p−1)` | Blelloch `W = 2(p−1)` | Serial `W = p−1` | H–S depth | Blelloch depth | Serial depth |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 17 | 14 | 7 | 3 | 6 | 7 |
| 16 | 49 | 30 | 15 | 4 | 8 | 15 |
| 32 | 129 | 62 | 31 | 5 | 10 | 31 |
| 64 | 321 | 126 | 63 | 6 | 12 | 63 |
| 128 | 769 | 254 | 127 | 7 | 14 | 127 |

At `p = 128` with `γ₀ = 30 s`: Hillis–Steele finishes in 210 s for 769 LLM calls; Blelloch in 420 s
for 254; serial in 3,810 s for 127. Blelloch is 2× slower than Hillis–Steele and **3.03× cheaper**,
and 9× faster than serial for 2× the cost. The cost ratio `(p lg p − p + 1)/(2(p−1)) ≈ (lg p)/2` grows
without bound while the latency ratio stays pinned at 2.

So for agents: **Blelloch by default; Hillis–Steele only under a hard deadline; serial only when
non-associativity forces strict left-to-right evaluation.** Recursive doubling — the textbook
parallel-prefix answer, and MPI's — is the *worst* choice on cost at every `p ≥ 4`. This is the
cleanest single example of the thesis, because nothing about the algorithms changes; only which term
is expensive.

One caveat to state honestly: Blelloch's down-sweep applies `⊕` to a partial prefix and a stored
subtree value, so it must respect rank order for a non-commutative `⊕`. It does — the left/right
asymmetry of the down-sweep *is* rank order — but this should be verified in the implementation
rather than asserted.

### 5.6 Non-power-of-two: do not fold

MPI folds `p = 2^⌊lg p⌋ + r` because the extra `2α + 2nβ + nγ` is cheap. Under `γ₀ ≫ α`, folding costs
**one extra operator application on the critical path**:

```
K_fold                   = 1 + ⌊lg p⌋
K_general binomial tree  = ⌈lg p⌉
```

For any non-power-of-two `p`, `⌈lg p⌉ = ⌊lg p⌋ + 1 = K_fold`. **Folding and the general binomial tree
cost exactly the same critical-path applications** — and the general tree is simpler, avoids the load
imbalance Thakur et al. flag ("some processes do the work of their neighbours as well"), and preserves
rank order. At `p = 9` both give `K = 4`, but the folded version also perturbs the bracketing.

Recommendation: **never fold.** Use a general `⌈lg p⌉` binomial tree for reduce-type collectives and
Bruck's `⌈lg p⌉`-for-all-`p` pattern for allgather-type. Both are already the
non-power-of-two-friendly options in MPICH; AgentMPI just makes them unconditional.

### 5.7 Selection collapses from two dimensions to one

MPI's decision functions are surfaces over (message size, `p`) because `α` and `β` compete and the
crossover moves with `n`. Under `γ₀ ≫ α + nβ`, every comparison in §§5.1–5.6 reduced to a ratio in `p`
alone — `(p−1)/lg p`, `p lg p/(p−1)`, `(lg p)/2` — with the size dependence falling out. **AgentMPI's
selection table is one-dimensional in `p`, plus two boolean predicates:** *is `⊕` runtime-applicable?*
and *does peak residency fit in `U`?* That is a much smaller and more defensible artefact than MPICH's
or Open MPI's tuned tables, and it follows from the regime rather than being a shortcut.

---

## 6. Master table

`n` = **one contribution** (tokens); `p` = ranks; `lg = log₂`. Rounds are dependent steps. `W` =
total operator applications; `K` = critical-path applications. Peak resident is per rank at the worst
moment, excluding the reserved region `r`; `δ = 0` unless the row says otherwise. **†** marks
algorithms requiring the operator to be **divisible over the operand**, hence **inapplicable to an
agent operator**. **‡** marks algorithms requiring **commutativity**.

| Collective | Algorithm | Rounds `R` | Messages `M` | Volume `V` | `W` | `K` | Peak resident / rank | Origin |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Barrier** | central/counting | 2 (`p−1` single-port) | `2(p−1)` | 0 | 0 | 0 | 0 | `mellorcrummey1991algorithms` |
| | linear | 2 | `2(p−1)` | 0 | 0 | 0 | 0 | folklore |
| | butterfly | `lg p` (pof2); `2⌊lg p⌋` else | `p lg p` | 0 | 0 | 0 | 0 | `brooks1986butterfly` |
| | dissemination | `⌈lg p⌉` ∀`p` | `p⌈lg p⌉` | 0 | 0 | 0 | 0 | `hensgen1988barrier` |
| | tournament | `2⌈lg p⌉` | `2(p−1)` | 0 | 0 | 0 | 0 | `hensgen1988barrier` |
| **Bcast** | flat/linear | `p−1` | `p−1` | `(p−1)n` | 0 | 0 | `n` | folklore |
| | binomial tree | `⌈lg p⌉` | `p−1` | `(p−1)n` | 0 | 0 | `n` | folklore |
| | chain, `s` segments | `p−2+s` | `(p−1)s` | `(p−1)n` | 0 | 0 | `n` | folklore |
| | pipelined tree, `s` seg. | `⌈lg p⌉−1+s` | `(p−1)s` | `(p−1)n` | 0 | 0 | `n` | `chan2007collective` |
| | scatter+allgather (ring) | `lg p + p − 1` | `(p−1) + p(p−1)` | `≈2(p−1)n` | 0 | 0 | `n` | `barnett1994intercom` |
| **Reduce** | flat | `1` msg + `p−1` merges | `p−1` | `(p−1)n` | `p−1` | `p−1` | `2n` (root) | folklore |
| | binomial tree | `⌈lg p⌉` | `p−1` | `(p−1)n` | `p−1` | `⌈lg p⌉` | `2n` | folklore |
| | chain | `p−1` | `p−1` | `(p−1)n` | `p−1` | `p−1` | `2n` | folklore |
| | Rabenseifner †‡ | `2 lg p` | `2p lg p` | `≈2(p−1)n` | `p lg p` calls / `(p−1)n` elem-work | `lg p` calls / `≈n` elem-work | `2n` | `rabenseifner2004optimization` |
| **Allreduce** | recursive doubling | `lg p` | `p lg p` | `p n lg p` | **`p lg p`** | `lg p` | `2n` | folklore |
| | reduce + bcast | `2⌈lg p⌉` | `2(p−1)` | `2(p−1)n` | **`p−1`** | `⌈lg p⌉` | `2n` | folklore |
| | Rabenseifner †‡ | `2 lg p` | `2p lg p` | `≈2(p−1)n` | `p lg p` calls | `lg p` calls | `2n` | `rabenseifner2004optimization` |
| | ring †‡ | `2(p−1)` | `2p(p−1)` | `≈2(p−1)n` | `p(p−1)` calls | `p−1` calls | `2n` | `patarasuk2009bandwidth` |
| **Allgather** | ring | `p−1` | `p(p−1)` | `p(p−1)n` | 0 | 0 | `p·n` | `thakur2005optimization` |
| | recursive doubling | `lg p` (pof2); `≤2⌊lg p⌋` else | `p lg p` | `p(p−1)n` | 0 | 0 | `p·n` | folklore |
| | Bruck | `⌈lg p⌉` ∀`p` | `p⌈lg p⌉` | `p(p−1)n` | 0 | 0 | `p·n` + perm. buffer | `bruck1997efficient` |
| | neighbour exchange (even `p`) | `p/2` | `p²` | `p(p−1)n` | 0 | 0 | `p·n` | `chan2007collective` |
| | **handle-based (AgentMPI)** | as above | as above | `p(p−1)h` | 0 | 0 | **`p·h + k·n`** | this dossier |
| **Scatter** | linear | `p−1` | `p−1` | `(p−1)n` | 0 | 0 | root `p·n`, else `n` | folklore |
| | binomial | `⌈lg p⌉` | `p−1` | `(p/2)·lg p·n` | 0 | 0 | root `p·n`; subtree-`s` owner `s·n` | folklore |
| **Gather** | linear | `p−1` | `p−1` | `(p−1)n` | 0 | 0 | root `p·n`, else `n` | folklore |
| | binomial | `⌈lg p⌉` | `p−1` | `(p/2)·lg p·n` | 0 | 0 | root `p·n`; level-1 child `(p/2)n` | folklore |
| **Alltoall** (`m`/pair, `n=pm`) | linear isend/irecv | `p−1` | `p(p−1)` | `p(p−1)m` | 0 | 0 | `2n` | `thakur2005optimization` |
| | pairwise exchange | `p−1` | `p(p−1)` | `p(p−1)m` | 0 | 0 | `2n` | folklore |
| | Bruck index | `⌈lg p⌉` | `p⌈lg p⌉` | `p·(n/2)·lg p` | 0 | 0 | `2n` + temp | `bruck1997efficient` |
| **Reduce-scatter** (`n` in, `n/p` out) | recursive halving ‡† | `lg p` | `p lg p` | `(p−1)n` | `p lg p` calls | `lg p` calls | `2n` → `n/p` | `thakur2005optimization` |
| | recursive doubling (non-comm.) † | `lg p` | `p lg p` | `p·n(lg p − (p−1)/p)` | `p lg p` calls | `lg p` calls | `2n` | `thakur2005optimization` |
| | ring / pairwise exchange ‡† | `p−1` | `p(p−1)` | `(p−1)n` | `p(p−1)` calls | `p−1` calls | `2n` | `thakur2005optimization` |
| | non-pof2 fold + halving ‡† | `⌊lg p⌋+2` | `p lg p + 2r` | `≈2pn` | `p lg p + 2r` | `lg p + 1` | `2n` | `rabenseifner2004more` |
| **Scan / Exscan** | serial chain | `p−1` | `p−1` | `(p−1)n` | `p−1` | `p−1` | `2n`; `n+δ(p−1)` if `δ>0` | folklore |
| | recursive doubling (Hillis–Steele) | `⌈lg p⌉` | `p⌈lg p⌉ − (2^{⌈lg p⌉}−1)` | `M·n` | **`p lg p − (p−1)`** | `⌈lg p⌉` | `2n`; `n+δ lg p` if `δ>0` | `hillis1986data` |
| | Blelloch up–down sweep | `2 lg p` | `2(p−1)` | `2(p−1)n` | **`2(p−1)`** (+`p` inclusive) | `2 lg p` | `2n`; `n+δ lg p` if `δ>0` | `blelloch1990prefix` |
| **Any reduction** | **shared journal (SHARP regime)** — runtime-applicable `⊕` only | `1` | `0` agent-visible | `p·n` writes | `0` agent / `p−1` runtime | **`0`** | `n` | this dossier, after `graham2016sharp` |

Rows to read together for the paper's argument: **Allreduce recursive doubling vs reduce+bcast**
(`W`: `p lg p` vs `p−1`); **Scan Hillis–Steele vs Blelloch** (`W`: `p lg p − p + 1` vs `2(p−1)`);
**Reduce binomial vs chain** (`K`: `⌈lg p⌉` vs `p−1`, identical `W`); **Allgather, any algorithm**
(peak `p·n` regardless) versus **handle-based** (`p·h + k·n`).

---

## 7. Verification status and open items

Verified against primary or publisher records: Hockney (Parallel Computing 20(3):389–398, 1994);
Culler et al. (PPoPP'93); Alexandrov et al. (SPAA'95; JPDC 44(1):71–79, 1997); Brooks (IJPP
15(4):295–307, 1986); Hensgen, Finkel & Manber (IJPP 17(1):1–17, Feb 1988); Mellor-Crummey & Scott
(TOCS 9(1):21–65, 1991); Bruck, Ho, Kipnis, Upfal & Weathersby (IEEE TPDS 8(11):1143–1156, Nov 1997);
Thakur, Rabenseifner & Gropp (IJHPCA 19(1), 2005); Rabenseifner (ICCS 2004, LNCS 3036, pp. 1–9);
Patarasuk & Yuan (JPDC 69(2):117–124, 2009); Hillis & Steele (CACM 29(12):1170–1183, 1986); Blelloch
(IEEE TC 38(11):1526–1538, 1989; CMU-CS-90-190, 1990); Ladner & Fischer (JACM 27(4):831–838, 1980);
Chan et al. (CCPE 19(13):1749–1783, 2007); Graham et al. (COM-HPC'16); Demmel & Nguyen (ARITH 21,
2013; IEEE TC 64(7):2060–2070, 2015); Ahrens, Demmel & Nguyen (TOMS 46(3):22, 2020); MPI 4.1/5.0
standard text on `MPI_REDUCE` and `MPI_OP_CREATE`; all MPICH CVAR defaults and Open MPI fixed
thresholds, read from source.

Marked `[UNVERIFIED]` in the `.bib`, to be checked before submission:

1. **InterCom venue.** Thakur et al. cite Barnett et al. as Supercomputing '94; netlib and the UT
   Austin iCC page both say *Scalable High Performance Computing Conference*, May 1994. The `.bib`
   uses SHPCC'94. Resolve against the printed proceedings.
2. **Page ranges** for `kielmann2000plogp`, `faraj2005automatic`, `pjesivac2007quadtree`,
   `hoefler2010loggopsim`, `graham2020streaming`.
3. **DOIs** for `vandegeijn1991combine`, `rabenseifner2004more`, `patarasuk2007tree`,
   `hoefler2007nbc`, `graham2016sharp`.
4. **`rabenseifner1997reduce`** — the 1997 HLRS work cited as "the author's algorithm from 1997"
   needs a report number.
5. **`gibiansky2017baidu`** — the original URL no longer resolves; find an archive or cite only
   `sergeev2018horovod`.
6. **`hoefler2004barriersurvey`** — report series and number.
7. **`pollard2020nondeterminism`** — author list, exact title, venue.

Analytical items to check before the paper claims them:

8. **Blelloch down-sweep and rank order** (§5.5): argued to preserve rank order for non-commutative
   `⊕`; verify in the reference implementation rather than by inspection.
9. **The `δ`-expansion model.** `|a ⊕ b| ≤ max(|a|,|b|) + δ` is an assumption about LLM merge
   behaviour and needs an empirical fit. `δ` may be multiplicative rather than additive, in which case
   the chain/tree separation becomes `n·ρ^{p−1}` versus `n·ρ^{lg p}` — even more extreme.
10. **Reported SHArP figures** (6.01 → 2.83 µs; 46.93 → 14.48 µs) come from the COM-HPC'16 abstract;
    confirm against the paper body.
11. **`Γ = 6000`** assumes handle-passing at 5 ms. Measure `α` for the actual AgentMPI transport; with
    inline token payloads `Γ` drops to roughly 15, which changes no conclusion but does change the
    numbers in §5.
