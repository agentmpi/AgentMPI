# AgentMPI: A Message-Passing Interface for Multi-Agent Harness Development

**Shuo Xin**  
Independent / systems note  
30 August 2026

---

## Abstract

The last decade of multi-agent software has repeated a mistake the parallel-computing community made—and then corrected—between 1989 and 1994. Every vendor and research group shipped a *system*: a crew, a graph, a conversation manager, a “society of mind.” None of those systems is wrong, but none of them is what a person writing a new system actually needs. What they need is what Jack Dongarra, Al Geist, William Gropp, Ewing Lusk, and the MPI Forum eventually gave HPC: a small, boring, portable *interface* for moving information among unreliable workers, with matching rules, collectives, one-sided data, and a failure model.

This paper introduces **AgentMPI**, a message-passing interface for multi-agent harnesses. AgentMPI is not a multi-agent system. It is a protocol people use to write multi-agent systems, the way MPI is a protocol people use to write parallel programs. We reconstruct the history and design philosophy of MPI at the level expected of an HPC systems paper—Williamsburg 1992 through MPI-4 sessions and ULFM—and we give every major MPI idea an agent analog that is grounded in a working implementation, not a metaphor. Point-to-point matching, eager versus rendezvous, binomial-tree broadcast, Bruck barrier and allgather, recursive-doubling allreduce, passive-target windows and locks, communicator split, spawn, and User-Level Failure Mitigation (revoke / agree / shrink) are all present as callable operations.

The implementation is a Python library plus a language-neutral CLI and a POSIX filesystem transport. We evaluate it with four jobs written *against the protocol*, not against a framework: (E1) data-parallel translation of a public-domain book; (E2) coupled collaborative development of a small software package; (E3) a fault, lock, lifecycle, and context-OOM study; (E4) a 100-rank corpus map-reduce. On the filesystem binding, a 16-rank Bruck barrier is 92 ms median; a 100-rank scatter–allreduce–gather over 285 Aesop fables finishes in 7.86 s with all-rank agreement on the word count; killing two of eight ranks, shrinking, and allreducing on the survivors succeeds; a dead source unblocks a posted receive in 0.21 s instead of hanging; a 20-token context budget refuses an oversized inbound message.

The contribution is a claim about layering. MCP connects an agent to tools. A2A connects two agents that have discovered each other. AutoGen, CrewAI, LangGraph, MetaGPT, and Magentic-One are *applications* of coordination. AgentMPI is the missing MPI-shaped layer: the portable calls a harness author writes so that information sharing, synchronization, locking, lifecycle, and context overflow are not reinvented—badly—in every new multi-agent project.

---

## 1. Introduction

A person who wants to write a parallel simulation does not start by inventing a new supercomputer. They start by writing against MPI. The program is SPMD: the same text runs on every rank; ranks send, receive, broadcast, reduce, lock windows, and sometimes die. The *physics* is the author’s. The *movement of information* is the interface’s.

A person who wants to write a multi-agent harness today starts by choosing a framework, or by gluing threads to a chat API. The recurring failures are not mysterious:

1. **Executors cannot share information** except by stuffing it into the next prompt.
2. **An executor that dies or goes offline hangs the job**, because nobody programmed a matching receive against failure.
3. **There is no sync, and there is no lock.** Two agents edit the same file. A “reviewer” starts before the code exists.
4. **Lifecycle is implicit.** An agent is a request/response, not a process with init, active, suspended, failed, finalized.
5. **Context overflows.** The analog of OOM is a truncated transcript and a silent quality collapse.

These are not product bugs. They are *missing systems calls*. MPI grew the corresponding calls over thirty years because distributed-memory machines forced the issue. Multi-agent systems are distributed-memory machines whose “memory” is a context window and whose “CPU” is a stochastic executor. The right response is not another framework. It is an interface.

**AgentMPI** is that interface. This paper is the first systematic account of it: history, philosophy, algorithms, specification, implementation, and experiments. The experiments are not toy RPC traces. They are harnesses written in AgentMPI and executed as multi-rank jobs, including a 100-rank run and injected executor death.

We make four claims.

- **C1 (Layering).** Multi-agent software needs an MPI-shaped protocol layer, distinct from tool protocols (MCP), pairwise agent HTTP (A2A), and orchestration frameworks.
- **C2 (Transfer).** The MPI Forum’s design rules—API not compiler; proven ideas only; language-independent semantics; implementable on many fabrics—transfer. One rule must be *inverted*: MPI assumed reliable processes; AgentMPI assumes unreliable, context-bounded executors.
- **C3 (Grounding).** Every concept in the interface is implemented, with the classical collective algorithms, not a central broker pretending to be MPI.
- **C4 (Programmability of failure).** Sharing, death, sync, lock, lifecycle, and context OOM are expressed as calls a harness author writes, and we measure those calls.

---

## 2. A short history of MPI, written for people who will steal from it

### 2.1 Before the Forum

By 1991 the idea of processes communicating with messages was already old. Hoare’s CSP (1978) had given the theory a calculus. Hewitt’s actors had given it a name. What HPC had was a *zoo*. Intel NX/2, nCUBE Vertex, Parasoft Express, P4 (Butler and Lusk), PARMACS, Zipcode (Skjellum), CHIMP, PICL, IBM’s EUI, and—most famously—PVM.

PVM began in 1989 when Vaidy Sunderam visited Oak Ridge and, with Al Geist, built a *parallel virtual machine*: heterogeneous hosts, sockets over TCP/IP, a simple API, a culture of “portability first, performance second.” Jack Dongarra and Bob Manchek made it a public artifact. PVM won the early 1990s because it was a *system you could run on the machines you already had*. It also baked in a philosophy that MPI would later refuse: the library, not the application, would manage the virtual machine, and communication failure was a first-class worry because the Internet was slow and hosts vanished.

In the summer of 1991 a small group met at a mountain retreat in Austria. Out of that discussion came the Workshop on Standards for Message Passing in a Distributed Memory Environment, 29–30 April 1992, Williamsburg, Virginia, sponsored by the Center for Research on Parallel Computing. The workshop did not pick a winner among NX, PVM, and p4. It listed the features a *standard* would need and appointed a working group.

Dongarra, Rolf Hempel, Tony Hey, and David Walker circulated a draft called MPI-1 in November 1992 (revised February 1993). It was deliberately incomplete: point-to-point only, not thread-safe, no collectives. Its job was to force the arguments into the open.

### 2.2 The Forum, and why MPI is a standard rather than a code drop

In November 1992 in Minneapolis the working group adopted the procedures of the High Performance Fortran Forum. Subcommittees, mailing lists, two-day meetings every six weeks through 1993, a draft at Supercomputing ’93, MPI 1.0 in June 1994. About sixty people from forty organizations. Vendors in the room. That last fact is why MPI won and PVM, as a *standard*, did not.

Gropp and Lusk later wrote the comparison that still matters (“Goals Guiding Design: PVM and MPI,” 2002). PVM’s goal was a portable heterogeneous virtual machine. MPI’s goal was a *practical, portable, efficient, flexible application programming interface* that a vendor could implement on an MPP without shame. MPI assumed a reliable fabric so that the user’s program did not contain retry loops. It combined process groups and isolation into a single object, the communicator. It treated datatypes as the way to get both heterogeneity and non-contiguous layouts. It refused to standardize untested ideas.

The MPI 1.0 goal list, paraphrased from the Forum report, is the design document of this paper:

1. An application programming interface (not a compiler, not a runtime product).
2. Efficient communication; overlap of computation and transfer.
3. Heterogeneous implementations.
4. Convenient language bindings.
5. Reliable communication, from the user’s point of view.
6. Close to existing practice, with room to grow.
7. Implementable on many vendors without rewriting the OS.
8. Language-independent semantics.
9. Thread-safety.

AgentMPI keeps 1–4 and 6–9. It *rejects* 5 as a statement about executors. The fabric can be reliable. The worker cannot.

### 2.3 MPI-2, MPI-3, MPI-4, and the ideas we are not allowed to skip

**MPI-2 (1997)** added the three things every agent harness eventually rediscovers: dynamic process management (`MPI_Comm_spawn`), parallel I/O, and one-sided communication (windows, `Put`/`Get`, lock/unlock and fence epochs). Passive-target RMA is the correct model for a shared design document: the writer does not need the reader to post a receive.

**MPI-3 (2012)** added non-blocking collectives, neighborhood collectives, and a usable shared-memory window. Non-blocking collectives are the right shape for “start a reduce of reviews, keep writing code, wait later.”

**MPI-4 (2021)** added large-count support, persistent and partitioned communication, and the **Sessions** model: an application can build communicators from groups without a world-sized `MPI_Init`. Sessions are how an agent should join a job. The standard still does not specify behaviour after process failure.

**ULFM** (User-Level Failure Mitigation; Bland, Bouteiller, Herault, Hursey, Dongarra, and others) is the extension that does. A communicator can be *revoked*; survivors can *agree* on a value; they can *shrink* to a new communicator whose ranks are remapped to `0..p'−1`. ULFM refuses to pick a recovery policy. Checkpoint/restart, replication, and algorithmic inversion are the application’s problem. That refusal is the most important sentence in the fault-tolerance literature for anyone building agent systems. The protocol makes failure *visible*. The harness decides what to do.

### 2.4 The algorithms, not the folklore

A collective in MPI is not “root talks to everyone.” MPICH’s short-message algorithms, documented by Thakur, Rabenseifner, and Gropp (IJHPCA 2005), are the ones AgentMPI implements and the ones a paper is obliged to name.

**Binomial tree.** Rewrite ranks as `(rank − root) mod p`. The parent of a non-root is that relative rank with its lowest set bit cleared. Children are obtained by setting higher zero bits that remain in range. Broadcast, scatter, gather, and reduce are all the same tree. Time is `⌈log2 p⌉ (α + nβ)`. This is optimal in the latency term and wasteful in bandwidth for large `n`.

**Van de Geijn broadcast.** Scatter the payload, then allgather. Bandwidth drops from `n log p` to about `2n`. AgentMPI uses the binomial tree for the default (agent control messages are short) and the rendezvous path for large artifacts, which is the moral equivalent of switching algorithms at an eager threshold.

**Recursive doubling.** At step `k`, rank `r` exchanges with `r XOR 2^k`. Power-of-two allreduce and short allgather. `log2 p` steps.

**Bruck (1997).** Distance-doubling with modular partners, so non-powers of two still finish in `⌈log2 p⌉` steps. AgentMPI’s barrier and allgather use this family.

**Rabenseifner allreduce.** Reduce-scatter (recursive halving) plus allgather (recursive doubling). Bandwidth ≈ `2n` instead of `n log p`. The implementation exposes the decomposition; the default allreduce uses doubling when `p` is a power of two and reduce+broadcast otherwise—the same split MPICH taught a generation of students.

**Pairwise exchange alltoall.** `p−1` steps. Correct, not clever. Personalized review traffic is alltoall whether or not the harness author knows the name.

Eager versus rendezvous, as Gropp taught it: an envelope always travels; data travels with the envelope only if the implementation is willing to buffer it on the unexpected path. Large messages handshake. AgentMPI’s handshake is an artifact file. The envelope in the mailbox is the RTS; matching the receive is the CTS; materializing the file is the data transfer. The point is the same: **do not force the receiver to allocate unbounded memory—or unbounded context—because a sender felt like talking.**

---

## 3. The philosophy, restated for agents

MPI succeeded because it was *less* than PVM in one direction and *more* in another. Less: no virtual-machine daemon as a required personality; no library-owned host list that the application could not see. More: communicators, datatypes, a serious collective set, a written matching rule.

The multi-agent landscape in 2026 is PVM-shaped. AutoGen, CrewAI, LangGraph, MetaGPT, ChatDev, Magentic-One, OpenAI Swarm, and a dozen internal “agent OS” projects are each a virtual machine with a preferred metaphor (conversation, crew, graph, company, society). Google’s A2A (now at the Linux Foundation) is closer to a protocol, but it is a *client/server task protocol with discovery cards*, not an SPMD interface with collectives and a failure model. Anthropic’s MCP is a tool protocol. Those are complementary layers. They are not MPI.

AgentMPI’s philosophical commitments, stated so they can be attacked:

**P1. Interface, not organism.** The library does not have a planner, a memory, or a personality. It has ranks.

**P2. SPMD is the default.** The harness text is the same on every rank. Specialization is `comm_split` and roles the *author* assigns, not a hidden orchestrator.

**P3. Matching is sacred.** Information that is not sent, or is sent with the wrong tag or communicator, is not received. This is how two libraries compose. Tags are not a substitute for communicators; the Forum already had that argument.

**P4. Collectives are the API for “everybody needs to know.”** If the author writes a for-loop of sends from rank 0, they have written the naive broadcast and they will debug it forever.

**P5. Large things do not enter small windows.** Rendezvous and context budgets are the same idea at two layers.

**P6. Failure is a return code, not a vibe.** A dead executor is a `DeadRankError` or a shrunk communicator, not a thread that never comes back.

**P7. Recovery is not our business.** We implement ULFM’s visibility, not someone’s pet checkpoint scheme.

**P8. Proven algorithms only.** Binomial trees and Bruck are in. A learned router is out, until it has been an implementation *behind* the same calls.

---

## 4. The AgentMPI interface

### 4.1 Objects

A **rank** is an integer in `0..p−1`. An **executor** is whatever inhabits a rank: a process, a Cursor subagent, a human at a CLI, a script. A **communicator** is `(group, context-name)`. `COMM_WORLD` is created by the launcher (`ampi-run`, or `Init` after `attach`).

An executor’s **lifecycle** is `uninitialized → init → active ⇄ suspended → finalized`, with `failed` reachable from any non-final state. `suspended` is the agent-specific state: compacting context, or waiting on a rendezvous artifact the way an MPI rank waits on a CTS.

### 4.2 Point-to-point

```
send(obj, dest, tag)
recv(source=ANY_SOURCE, tag=ANY_TAG) -> obj
isend / irecv_probe / probe
```

Matching is MPI matching. The filesystem binding posts by atomic rename into `mailboxes/<dest>/`. Progress does not require the destination to be inside the library except to receive.

### 4.3 Collectives

`barrier`, `bcast`, `scatter`, `gather`, `reduce`, `allreduce`, `allgather`, `alltoall`, `scan`. Operators: the MPI arithmetic/logical set, plus `CONCAT`, `MERGE`, `SYNTHESIZE`. `SYNTHESIZE` is an associative stitch; a harness may pass a callable to run an LLM at each internal node of the binomial reduce—the map-reduce that agent papers keep rediscovering, except it is now a `reduce`.

### 4.4 Windows

`win_create` (collective), `win_ensure` (local), `win_lock` / `win_unlock` (exclusive or shared), `put`, `get`. This is MPI-2 passive-target RMA with POSIX directory locks. The shared object is a design document or a source file, not an array of doubles. The *memory model* is the same: an origin may write without the target posting a receive; a lock defines the epoch.

### 4.5 Context

Each rank has a token budget. Receives are charged (`len(json)/4`). `ContextBudgetExceeded` is the OOM. `context_compact(summary)` pages. `context_put` / `context_get` publish summaries into a window so that “we forgot to tell the other agents” becomes a `get`.

### 4.6 Faults

`heartbeat`, `probe_failures`, `revoke`, `agree`, `shrink`. A receive that names a source will raise `DeadRankError` if that source is declared dead—**the hang is a bug in the protocol if it happens**. `shrink` intersects per-rank liveness views so two survivors cannot disagree on the new rank map, then bootstraps a child communicator. This is ULFM’s agree-then-shrink, implemented without a central daemon.

### 4.7 Groups

`comm_split(color, key)`, `spawn(n)`. Split is how a harness makes an architecture team and an implementation team without inventing “channels.” Spawn advertises new ranks; a launcher fills them. Sessions are represented today by `attach(home, rank, size)`: a Cursor subagent joins a communicator that already exists.

---

## 5. Implementation

The package `agentmpi` is the reference binding. Transport is a directory tree:

```
$AMPI_HOME/comms/<name>/{meta.json,ranks,mailboxes,artifacts,windows,locks,logs,shrink}
```

Delivery is `write tempfile; fsync; rename`, atomic on POSIX. Events append to `logs/events.jsonl` so a dashboard can draw a job without being on the data path. The CLI (`python -m agentmpi`) exposes every call so a rank that is not a Python program can still be a rank. `ampi-run -n P -- cmd` is `mpiexec`.

Collectives are decentralized. There is no broker. A 100-rank allgather is 100 ranks sending to `(r − 2^k) mod p`. That is deliberate. A protocol that requires a coordinator to move data will die the first time the coordinator’s context does.

Tests cover tree neighborhoods, every collective including non-powers of two, eager/rendezvous classification, context trip and compact, recv-unblock on death, and shrink remapping. They are in `tests/` and are part of the claim that the concepts are grounded.

---

## 6. How a person writes a harness

### 6.1 Data-parallel (the “easy” job)

Translation of a book that can be split is the LINPACK of agent systems: everyone understands it, and it still fails if scatter/gather are missing.

```
shard = comm.scatter(shards if comm.rank == 0 else None)
out   = [translate(f, target) for f in shard["fables"]]
book  = comm.gather(out, root=0)
```

Rank 0 is not an orchestrator with a private mailbox. It is the root of a binomial tree.

### 6.2 Coupled (the “hard” job)

Collaborative software development has data dependences. The harness in this repository:

1. `win_create("design")` + barrier
2. Architect `put`s a module list under an exclusive lock
3. Barrier (the sync people skip)
4. Each implementer `win_ensure`s a file window, locks, writes, unlocks
5. Barrier
6. `allgather` a catalog so everyone can see who wrote what
7. Reviewer `get`s each file, `ast.parse`s it, `gather`s the review

That is not a “multi-agent framework.” It is forty lines of SPMD against a protocol. The same text can drive processes today and Cursor subagents tomorrow, because the rank is the abstraction.

---

## 7. Experimental evaluation

All process-mode jobs ran on a single Linux host with the filesystem transport, Python 3.12, and the harnesses in `experiments/`. Cursor-subagent jobs use the same harnesses and the same `$AMPI_HOME`; the executor behind a rank changes, the calls do not.

### 7.1 Microbenchmarks

Median of five iterations, milliseconds:

| Kernel | p=2 | p=4 | p=8 | p=16 |
|---|---:|---:|---:|---:|
| barrier | 2.8 | 11.5 | 31.5 | 92.0 |
| bcast (small) | 0.8 | 1.2 | 10.7 | 21.2 |
| bcast (20 kB, rendezvous) | 1.3 | 4.8 | 17.2 | 32.1 |
| allreduce SUM | 2.5 | 8.8 | 27.1 | 95.2 |
| allgather | 2.8 | 8.1 | 26.8 | 92.6 |
| ping-pong (small) | 9.8 | 11.9 | 9.7 | 11.2 |

Barrier, allreduce, and allgather grow with `⌈log2 p⌉` plus filesystem contention. Ping-pong does not grow with `p`. Broadcast of a large payload uses the rendezvous path (5 of 7 sends at `p=2` were non-eager). These are not InfiniBand numbers and are not trying to be. They are evidence that the *algorithms* are the ones we claimed, running on the transport we shipped.

### 7.2 E1 — Translation of Aesop (data-parallel)

Vernon Jones’s 1912 *Æsop’s Fables* (Project Gutenberg eBook 11339), 285 extracted fables. A 16-rank job scatters 64 fables (four per rank), “translates” them in process mode with a deterministic stand-in, gathers the book, and reduces a table of contents. Result: 64 fables, 45 sends, 6 rendezvous, 0.127 s protocol time, 206 kB moved. The stand-in exists so the *protocol path* is measurable without confounding model latency. Live Cursor ranks replace the stand-in with a real translation; they still call `scatter` / `gather`.

### 7.3 E2 — Collaborative `kvstore` (coupled)

Six ranks: architect, store, cli, tests, docs, reviewer. Exclusive locks on each artifact. Reviewer reports `syntax-ok` on every Python module. The assembled package’s CAS, get/put, and delete tests pass. 95 eager sends, 0.177 s. This is the job that deadlocks if `win_create` is accidentally collective on a per-role file—the implementation therefore distinguishes `win_create` (MPI-shaped, everyone calls it) from `win_ensure` (local). That bug is in the paper because it is the kind of bug a protocol is for.

### 7.4 E3 — Death, locks, lifecycle, OOM

- **Death.** Ranks 3 and 6 of 8 mark `failed`. Survivors shrink to a 6-rank communicator with remapped ranks `{0,1,2,4,5,7} → {0..5}` and allreduce `1` to `6`.
- **Locks.** 8 ranks × 25 exclusive increments on one counter = 200. No lost updates.
- **OOM.** A 32-token budget raises `ContextBudgetExceeded`; compact leaves 5 tokens. A 20-token receiver rejects a 50-word inbound message on the recv path (`['sent', 'oom']`).
- **Hang avoidance.** Rank 0 posted `recv(source=1)`. Rank 1 went `failed`. Unblock in 0.21 s with `DeadRankError([1])`.

These are the five failure modes named in the introduction, each turned into a call and a measurement.

### 7.5 E4 — 100 ranks

285 fables scattered across 100 ranks (~2–3 each). Each rank analyzes word counts and morals; `allreduce` sums words; `gather` assembles reports. Result: 285 reports, 36,823 words, **all 100 ranks agree on the sum**, 396 sends (21 rendezvous), 7.86 s. The 32-rank comparison is 0.73 s. Growth is superlinear in this transport, as expected for a shared filesystem with `O(p log p)` metadata operations; the *correctness* property that matters for agents is the allreduce agreement, not the last millisecond.

### 7.6 Threats to validity

Process-mode executors are threads, not language models. They validate the protocol, not the quality of a translation or a code review. Cursor-subagent runs close that gap but introduce model variance we do not control. The filesystem transport is a reference fabric, not a claim about datacenter RPC. Token estimates are `len/4`, not a vendor tokenizer. Shrink membership uses heartbeat freshness plus explicit `failed`; a partition that keeps heartbeats alive is not detected (the same limitation ULFM has with respect to silent data corruption).

---

## 8. Related work

**Message passing.** MPI Forum reports 1.0–4.1; Gropp, Lusk, Skjellum, *Using MPI*; Snir et al., *MPI: The Complete Reference*; Geist et al. on PVM; Thakur, Rabenseifner, Gropp 2005 on collectives; Bruck 1997; Van de Geijn broadcast; ULFM papers; MPI Sessions (MPI-4) and the 2023 work on fault-aware sessions.

**Classical concurrency.** CSP (Hoare 1978); actors (Hewitt; Agha 1986); Linda tuple spaces (Gelernter); the barrier/reduction literature in PRAM and BSP (Valiant).

**Agent frameworks.** AutoGen (Wu et al.), Magentic-One (Fourney et al.), MetaGPT (Hong et al.), ChatDev (Qian et al.), CrewAI, LangGraph, CAMEL, OpenAI Swarm. These are systems. Several contain an implicit broadcast (the group chat) and an implicit lock (the human). None provide communicator isolation, a matching rule, or shrink.

**Agent protocols.** MCP (Anthropic) is tools. A2A (Google / Linux Foundation) is pairwise tasks and discovery. IBM ACP is in the same pairwise family. AgentMPI does not replace them. A rank may call MCP; two communicators may be bridged by A2A the way two MPI worlds were once bridged by PVMPI.

---

## 9. Discussion

The tempting critique is that agents are not processes: they are slow, stochastic, and expensive, so a protocol designed for microseconds is theatre. We think this has the arrow of history backwards. MPI’s value was never that a send was 4 µs. It was that a *program* could be written once and run on a workstation cluster and an SP-2. Agent executors will change every quarter. The calls `scatter`, `barrier`, `shrink`, and `context_compact` should not.

A second critique is that SPMD is unnatural for agents. People want a boss. MPI programs have bosses too: they are called rank 0, and they are not special in the type system. Making the boss a different *library* is how we got undeclared control planes.

A third critique is that SYNTHESIZE is not associative and an LLM reduce is not a SUM. True. The protocol still needs a reduce *shape*. The operator is the author’s. That is how MPI treated `MPI_Op_create`.

---

## 10. Conclusions

We asked what the MPI Forum would have standardized if the workers were language models. The answer is not a crew. It is an interface: communicators, matching, eager and rendezvous, the classical collectives, windows and locks, sessions, and ULFM-shaped failure, plus one new resource—the context budget—because the analog of memory is now tokens.

AgentMPI is that interface, implemented, specified, and measured. The invitation is the same one the Forum issued in 1994: implement it on your fabric; write your harness against the calls; argue with the matching rule in public, not inside a product.

---

## Acknowledgements

The historical reconstruction draws on the MPI Forum reports, Gropp and Lusk’s retrospective on PVM and MPI, the Thakur–Rabenseifner–Gropp collective paper, and the ULFM line of work. Errors of emphasis are ours.

## References

1. Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard*, versions 1.0–4.1.
2. W. Gropp, E. Lusk. “Goals Guiding Design: PVM and MPI.” 2002.
3. A. Geist et al. *PVM: Parallel Virtual Machine*. MIT Press, 1994.
4. R. Thakur, R. Rabenseifner, W. Gropp. “Optimization of Collective Communication Operations in MPICH.” *IJHPCA*, 2005.
5. J. Bruck et al. “Efficient Algorithms for All-to-All Communications in Multiport Message-Passing Systems.” *IEEE TPDS*, 1997.
6. M. Barnett et al. / Van de Geijn. Broadcast by scatter-allgather.
7. W. Bland et al. “An Evaluation of User-Level Failure Mitigation in MPI.”
8. MPI Forum. MPI-2 (1997), MPI-3 (2012), MPI-4 (2021) summaries: RMA, non-blocking collectives, Sessions.
9. C. A. R. Hoare. “Communicating Sequential Processes.” *CACM*, 1978.
10. G. Agha. *Actors*. MIT Press, 1986.
11. D. Gelernter. “Generative Communication in Linda.” *TOPLAS*, 1985.
12. Q. Wu et al. AutoGen.
13. A. Fourney et al. Magentic-One.
14. S. Hong et al. MetaGPT.
15. C. Qian et al. ChatDev.
16. Anthropic. Model Context Protocol.
17. Linux Foundation / Google. Agent2Agent (A2A) specification.
18. J. Dongarra, R. Hempel, A. Hey, D. Walker. Early MPI-1 drafts, 1992–1993.
19. W. Gropp. Lecture notes on eager and rendezvous protocols.
20. Project Gutenberg. *Æsop’s Fables*, V. S. Vernon Jones, 1912. EBook 11339.
