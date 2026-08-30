# Dossier 04 — Fault Tolerance, Resilience, and Dynamic/Elastic Execution

*Background research for AgentMPI. Scope: what message-passing and distributed-systems research has established about surviving process death, stalls, and wrong answers, and which of those results transfer to a runtime whose "ranks" are LLM agents.*

Verification note: claims sourced to a document I read directly are cited plainly; claims I could not verify against a primary source are tagged `[UNVERIFIED]`. Section 3.5 records a discrepancy between secondary sources and the MPI-5.0 standard text that I resolved by checking the standard itself.

---

## 1. The MPI failure model problem

### 1.1 What the standard actually says

MPI has never specified a recovery model. The default error handler attached to predefined communicators is `MPI_ERRORS_ARE_FATAL`, which "causes the program to abort all connected MPI processes," equivalent to calling `MPI_ABORT` on a communicator containing every connected process [MPIForum 2025]. A user may install `MPI_ERRORS_RETURN`, but the standard immediately qualifies what that buys: an advice-to-implementors merely asks that "a high-quality implementation will, to the greatest possible extent, circumscribe the impact of an error, so that normal processing can continue after an error handler was invoked" [MPIForum 2025]. Open MPI's own manual pages restate the operative consequence bluntly: "MPI does not guarantee that an MPI program can continue past an error" [OpenMPI 2025].

The practical reading, unchanged from MPI-1 through MPI-5.0, is that after a failure the state of MPI is undefined. An implementation may return an error code, but it owes the application no guarantee that any subsequent MPI call will behave sensibly, that communicators remain usable, or even that the call will return at all rather than deadlock.

### 1.2 Why this was deliberate

Gropp and Lusk [Gropp 2004] give the canonical defense, and it is a design argument rather than an oversight. Their central claim is that fault tolerance is a property of a *program–implementation pair*, not of a specification: a specification cannot make a program fault-tolerant, it can only refrain from forbidding fault-tolerant programs. They then evaluate the option of relaxing MPI object semantics — the FT-MPI approach — and reject it, because "MPI objects have properties that the object model normally guarantees to be constant, such as the number of processes in a communicator and a process's rank in it. These properties may be used by the program in nontrivial ways: data may be decomposed according to a communicator's size, and the assignment of part of the data to a given process may be calculated by using its rank" [Gropp 2004]. A communicator whose size can change under you is not a weaker communicator; it is a different abstraction, and every program written against the stronger one becomes wrong.

The second and more load-bearing reason is cost. Bland et al. [Bland 2013b] identify the two pitfalls the ULFM design had to avoid: "jitter prone, permanent monitoring of the health of peers a process is not actively communicating with, and expensive consensus required for returning consistent errors at all ranks." Uniform error reporting — every rank in a collective learning the same outcome — "cannot be possibly achieved in less than the cost of an AllReduce," and would impose that cost on *every* communication in the failure-free case [Bouteiller 2015]. MPI's performance contract is incompatible with a globally consistent view of failure. Given that, the standard's choice was to specify nothing rather than to specify something expensive.

This is the design tension AgentMPI inherits in inverted form. MPI declined to pay for failure consistency because failures are rare and messages are cheap. In an agent harness, failures are common and messages cost seconds of wall-clock and real money, so the ratio flips: consensus overhead that is prohibitive at microsecond latencies is essentially free at LLM-call latencies.

### 1.3 The Fault Tolerance Working Group

The MPI Forum's Fault Tolerance Working Group has now been at this for roughly fifteen years, across two failed and one still-unfinished attempt.

**FT-MPI (pre-Forum, ~2000–2004).** Fagg and Dongarra's FT-MPI, built inside the HARNESS project, implemented MPI-1.2 with extended failure semantics [Fagg 2000]. Four *communicator modes* controlled recovery: `ABORT` (MPI-1 behavior, for backward compatibility); `REBUILD`, in which failed processes are respawned by the runtime and survivors retain their ranks; `BLANK`, in which failed processes are not replaced, `MPI_COMM_WORLD` keeps its size, and dead ranks are blanked out and treated like `MPI_PROC_NULL`; and `SHRINK`, in which failed processes are removed and the communicator is made contiguous, so ranks change and the application must re-call `MPI_Comm_rank` [FTMPI 2004; Fagg 2004]. Two orthogonal *message modes* controlled in-flight traffic: `RESET` cancelled all pending messages (for applications that roll back to a prior consistent state), and `CONT` completed all transfers except those to and from failed processes (for applications tracking per-process state finely enough to minimize rollback) [ICL 2004]. Recovery was triggered by calling a communicator constructor such as `MPI_Comm_dup` on the failed communicator.

FT-MPI was never standardized and, in the ULFM authors' retrospective assessment, was "mostly used as a playground for understanding the fundamental concepts" [Bland 2013a]. Its decisive structural flaw was library composition: "when FT-MPI detects a failure, it repairs the state of MPI internally according to the selected recovery mode, and then only triggers the coordinated user recovery handle at all nodes. Library composition is rendered difficult by the fact that recovery preempts the normal flow of execution and returns to the highest level of the software stack without alerting intermediate layers that a failure happened" [Bland 2013a]. Only `MPI_COMM_WORLD` was repaired; every other communicator was the application's problem.

**Run-Through Stabilization (2011–2012).** The Forum's first serious internal attempt, led by Hursey, Graham, Bronevetsky, Buntinas, Pritchard, and Solt, introduced communicator "validation" as a way to mark failures recognized and keep using the communicator, plus uniform failure handlers [Hursey 2011a]. It reached a first reading in January 2012 and then died: "because of the implementation complexity imposed by resuming operations on failed communicators, this proposal was eventually unsuccessful in its introduction to the MPI Standard" [Bland 2013a]. The lesson the community drew — and it is the lesson most relevant to AgentMPI — is that *repairing a communication object in place is much harder than replacing it*.

**ULFM (2012–present).** Section 2.

---

## 2. ULFM in technical detail

### 2.1 The governing principle

The ULFM specification's key principle is that "no MPI call (point-to-point, collective, RMA, IO, ...) can block indefinitely after a failure, but must either succeed or raise an MPI error" [OpenMPI 2025; FTHub 2025]. That is the entire safety guarantee, and it is deliberately thin. Everything else is a matter of *deadlock freedom plus a toolbox*.

The design philosophy that follows — the library should not recover for you, it should let you recover — is stated in the ULFM evaluation paper as three requirements: simplicity, flexibility ("the API should allow varied fault tolerant models to be built as external libraries"), and absence of deadlock [Bland 2013b]. The consequence is spelled out precisely: "When a failure occurs, the state of MPI is left unchanged, only deliberate actions from the user alter the state of MPI regarding post-failure behavior" [Bland 2013b]. A communicator containing a dead process remains a valid object; operations that do not involve the dead process continue to work normally on it. ULFM never repairs anything on your behalf.

ULFM targets the **fail-stop** model: "a process acts correctly until it stops (as the result of either a resource or a software error) and no subsequent results are delivered. Transient or byzantine errors are outside the scope of this work" [Bland 2013b]. This is a direct and serious limitation for AgentMPI, because an LLM agent's dominant failure mode is Byzantine-lite: it returns, on time, with a wrong answer.

### 2.2 Local, non-uniform error reporting

The single most important semantic decision in ULFM is that errors are **local**. "Errors (`MPI_ERR_PROC_FAILED`) are not indicative of the return status on remote processes, but are raised only at a particular rank, when a particular operation cannot complete because a participating peer has failed" [Bland 2013b]. Further: "any error related to process failure has a local scope... In the general case, the semantic information given by the error code is insufficient to infer which rank has failed, or if that same error has been raised at all ranks. Moreover, the operation may have successfully completed at other ranks, because the failure happened after the necessary internal steps of the operation completed, or because the failed process was not having an active role in the operation" [Bland 2013b].

So a collective can succeed at some ranks and fail at others, and no rank can tell which case it is in. Every consistency property an application wants must be purchased explicitly, with `MPI_Comm_agree`.

### 2.3 Error classes

- **`MPIX_ERR_PROC_FAILED`** — a process failure prevented completion of this operation, at this rank.
- **`MPIX_ERR_PROC_FAILED_PENDING`** — a potential sender matching a *non-blocking wildcard-source* receive has failed. The distinction matters: the receive is not dead, because some other sender might still match it. The request remains pending and can complete later; earlier ULFM releases had bugs where such requests could not correctly complete in a subsequent `MPI_Wait`/`MPI_Test` [OpenMPI 2025]. This is the error class that exists purely because `MPI_ANY_SOURCE` makes "which peers do I depend on?" unanswerable.
- **`MPIX_ERR_REVOKED`** — the communicator has been revoked.

### 2.4 `MPI_Comm_revoke`

Revoke exists to solve a specific deadlock. Bland et al. give the canonical scenario, elaborated in the "Plan B" paper [Bouteiller 2015]: processes communicate in a chain, $P_k$ receives from $P_{k-1}$ then sends to $P_{k+1}$. $P_1$ fails. Only $P_2$ communicates directly with $P_1$, so only $P_2$ detects the failure. $P_3$ is now blocked receiving from $P_2$ — a live process — so no error may legitimately be raised at $P_3$. But $P_2$ knows recovery must begin, and if it branches to the recovery path it stops posting the send $P_3$ is waiting for. "The receive operation is effectively deadlocked" [Bland 2013b].

Revoke's semantics resolve this: "A revoked communicator becomes improper for further communication, and all future or pending communications on this communicator will be interrupted and completed with the new error code `MPI_ERR_REVOKED`. It is notable that although this operation is not collective (a process can enter it alone), it affects remote ranks without a matching call" [Bland 2013b]. Three properties deserve emphasis:

1. **Unilateral.** One process revokes; all others observe it. There is no matching call.
2. **Permanent.** Revocation cannot be undone; the communicator is dead as a communication object forever. Recovery means constructing a *new* communicator, not repairing this one. (This is precisely the lesson Run-Through Stabilization's failure taught.)
3. **Unordered.** Open MPI's manual states there is "no particular ordering between the revocation call at another process and the completion of operations at a local process; for example, a receive operation can raise an error of class `MPIX_ERR_REVOKED`, even if the send operation procedure is called before the revoke procedure at the sender" [OpenMPI 2025].

**Implementation.** Revoke is a *reliable broadcast*, but a deliberately weakened one. Of the four classical reliable-broadcast properties — Termination, Validity, Integrity, Agreement [Hadzilacos 1993] — only non-uniform variants are required, and Integrity is relaxed [Bouteiller 2015]. The relaxation is justified by an elegant observation: "If a failure during the Revoke algorithm kills the initiator as well as all the already notified processes, the Revoke notification is indeed lost, but the observed behavior, from the view of the application, is indiscernible from a failure at the initiator before the propagation started" [Bland 2013b]. Because the properties are non-uniform and unordered, "a process that receives its first Revoke message can perform a single round of emissions to all its neighbors, with a constant message size, and then deliver the Revoke notification immediately, without further verification" [Bouteiller 2015].

The overlay is a **Binomial Graph (BMG)** [Angskun 2007], chosen for simultaneously small degree and high resistance to partition. Each vertex $v$ links to $\{v \pm 1, v \pm 2, \ldots, v \pm 2^k : 2^k \le n\}$, giving a regular degree $\delta = 2\lceil \log_2 n \rceil$, logarithmic diameter, and high node connectivity [Bouteiller 2015]. Early prototypes used a fully connected overlay, which guarantees no disconnected cliques but "scales poorly as, with the number of processes, the graph degree is linear, and the number of exchanged messages is quadratic"; in practice complete flooding "required opening a large number of connections, resulting in crashing the Open IB driver" [Bland 2013b; Bouteiller 2015]. BMG reduces this to $O(n \log n)$ messages. The residual risk is accepted explicitly: if $\log n$ simultaneous failures strike in exactly the right pattern while only one process has detected the failure, revoke can fail to deliver; the authors argue this is a generalization of the birthday problem and will not be observed in practice [Bland 2013b].

Measured cost: the `MPI_Comm_revoke` call itself completes in under 50 µs at up to 512 processes, and the resulting network disturbance appears only after a failure has already occurred [Bland 2013b]. Evaluation at scale on Darter, a Cray XC30, confirms "small latency" and no system noise outside recovery periods [Bouteiller 2015].

### 2.5 `MPI_Comm_agree` — and why it is a real consensus

`MPI_Comm_agree` "performs a consensus (i.e. fault tolerant allreduce operation) on flag" with bitwise AND, absorbs newly detected failures, and propagates knowledge of failures among participants [OpenMPI 2025]. It is collective, it forms an agreement over a boolean "even when failures have happened or the communicator has been revoked," and it is the tool for "uniform completion of an algorithmic phase or collective operation, or as a key building block for strongly consistent failure handling approaches (such as transactions)" [Bland 2013b].

This is genuinely the consensus problem, and it inherits the corresponding theory. Fischer, Lynch, and Paterson proved that "no completely asynchronous consensus protocol can tolerate even a single unannounced process death" [Fischer 1985]. ULFM escapes FLP the standard way, by assuming a failure detector: the ERA algorithm assumes "an eventually perfect failure detector ($\Diamond P$ in the terminology of [Chandra and Toueg])" in a system with reliable asynchronous channels and unknown message-delay bounds [Herault 2015]. Chandra and Toueg's failure-detector hierarchy [Chandra 1996] is exactly the escape hatch, and $\Diamond S$ is the weakest detector sufficient for consensus [ChandraHT 1992].

The point that matters for AgentMPI is the *placement* of this cost. Consensus is not on the critical path of ordinary communication; it is a discretionary operation the application invokes when it needs a globally consistent decision. ULFM's whole architecture is an argument for making consensus opt-in and rare.

**Algorithms.** The first log-scaling implementation was Hursey et al.'s two-phase-commit variant, "Log2phases," a reduction to an elected coordinator followed by a broadcast of the decision, with $O(\log n)$ failure-free complexity matching `MPI_Allreduce` over the live processes [Hursey 2011b; Bland 2013b]. It was superseded by **ERA (Early Returning Agreement)** [Herault 2015]. ERA's contribution is the *early returning* property, distinct from classical early *stopping*: "as soon as a process can determine that the decision value is fixed (except if it fails itself), the process is allowed to return. However, because the process is allowed to return early, later failures may compel that process to participate in additional communications. Therefore, the decision must remain available after the processes return, in order to serve unexpected message exchanges until the stopping condition can be established" [FTHubERA 2015]. Complexity: with tree degree $\delta$ over $n$ nodes, all processes decide in at most $2\log_\delta n$ parallel steps failure-free, and $O(2\log_\delta n + f\delta)$ with $f$ failures [Herault 2015].

ERA also confronts a problem AgentMPI will face directly: **topology deterioration**. As processes die, the agreement tree degrades — nodes are re-parented only within their original ancestry or onto the current root — and "the topology can deteriorate quickly to a star, risking the loss of the per-process-logarithmic message count property." ERA mitigates this by rebalancing the tree *between* agreements, not during one [Herault 2015]. Measured against Log2phases in the Fenix/S3D recovery path, ERA was "almost an order of magnitude faster" and scaled to process counts unreachable before, and it exhibits the desirable property that smaller failures recover faster — behavior Log2phases lacked entirely, taking constant time regardless of failure size [Herault 2015].

`MPI_Comm_iagree` provides the non-blocking form.

### 2.6 `MPI_Comm_shrink` — and why it must be consensus

Shrink "allows the application to create a new communicator by eliminating all failed processes from a revoked communicator. The operation is collective and performs a consensus algorithm to ensure that all participating processes complete the operation with equivalent groups in the new communicator. This function cannot return an error due to process failure. Instead, such errors are absorbed as part of the consensus algorithms and will be excluded from the resulting communicator" [Bland 2013b].

The necessity of consensus here is structural. A communicator is a *distributed* object; every member must have an identical view of its group, otherwise ranks disagree about who rank 3 is and collective algorithms compute different trees. Failure detection in ULFM is deliberately non-uniform, so at the moment shrink is called, different processes know about different failure sets. Turning $n$ divergent local views into one agreed group is definitionally agreement on the set of failed processes — "algorithmically, an agreement on which the consensus is done on the group of failed processes. Hence, the two operations have the same algorithmic complexity. Indeed, in the prototype implementation, `MPI_COMM_AGREE` and `MPI_COMM_SHRINK` share the same internal implementation of the agreement" [Bland 2013b].

Note also that shrink must be non-failing: it is the recovery primitive, so it cannot itself fail when a process dies mid-recovery. That forces failures during shrink to be absorbed into the result rather than reported.

### 2.7 Failure acknowledgement — and an API rename worth noting

The original ULFM specification provided `MPI_Comm_failure_ack` / `MPI_Comm_failure_get_acked`: the ack marks a reference point in time, and get_acked returns the group of processes known locally to have failed as of that point. "After acknowledging failures, the application can resume `MPI_ANY_SOURCE` point-to-point operations between non-failed processes, but operations involving failed processes (such as collective operations) will likely continue to raise errors" [Bland 2013b].

Current Open MPI has replaced this pair with `MPIX_Comm_get_failed(comm, &failedgrp)` and `MPIX_Comm_ack_failed(comm, num_to_ack, &num_acked)`, matching the sliced Forum proposal [OpenMPI 2025]. Papers before roughly 2020 use the old names; the standard track uses the new ones. Anyone citing ULFM's API surface should say which vintage they mean.

Storage cost is negligible: a per-node global list of detected failures whose size grows linearly with the number of failures and is empty absent failures, plus two per-communicator values (a revoked flag and an index into the global failure list) [Bland 2013b].

### 2.8 Failure detection

ULFM deliberately avoids a *total* failure detector: "it is not necessary to implement a total failure detector, or to inject jitter prone heartbeat messages for the proposed model to work... failures are triggered only when communicating directly with a failed processor" [Bland 2013b]. The exception is `MPI_ANY_SOURCE`, where "because all ranks in the communicator are potential sources, a process stalled waiting on such an operation may have to trigger complete error detection on the communicator."

Where a real detector is needed, Bosilca et al. [Bosilca 2016] give a scalable one: alive nodes form a ring, each observing its predecessor via periodic heartbeats, so exactly one observer per node and a minimal message count in the failure-free case. When an observer's suspicion timeout expires it (a) reconnects the ring by finding the first believed-alive predecessor and (b) initiates a non-uniform reliable broadcast over a circulant-graph overlay, guaranteeing logarithmic propagation. The implementation lives in the Byte Transport Layer with a dedicated thread and RDMA-put heartbeats, so heartbeats need no message queue and cannot be starved by application traffic [Bosilca 2016]. Open MPI's production defaults are conservative: PRTE heartbeat period 5 s, timeout 10 s, "tuned for failure-free performance at the expense of fault detection reactivity," with a note that 100 ms is reasonable where faults are common but values below the TCP poll rate (~10 ms) cause false positives [OpenMPI 2025].

ULFM also handles transient errors by **promotion**: "Once a process has been reported as failed to a particular process, this process will consistently ignore and discard further communications with this failed process. As a result the transient error is promoted to a fail-stop error" [Bland 2013b]. A process that comes back is still dead as far as its peers are concerned. This is a clean and reusable trick — it converts an intractable failure model into a tractable one by fiat — and AgentMPI should steal it for stalled agents.

### 2.9 Failure-free overhead

The headline empirical result is that ULFM is essentially free when nothing fails. NetPIPE 1-byte latency, vanilla versus FT-enabled Open MPI [Bland 2013b]:

| Transport | Vanilla (µs) | FT (µs) | Difference |
|---|---|---|---|
| Shared memory (cache hot) | 0.8008 | 0.8016 | 0.0008 |
| TCP | 10.2564 | 10.2776 | 0.0212 |
| OpenIB | 4.9637 | 4.9650 | 0.0013 |

All differences are well inside the run-to-run standard deviation. Intel MPI Benchmarks on a 48-core shared-memory machine showed all point-to-point and collective differences below 5%, within that machine's noise. Sequoia-AMG at up to 512 processes showed negligible difference. The mechanism is cheap by construction: a single predictable branch on a cached revoked-flag, hinted toward the failure-free outcome, and one that can often reuse an error-checking conditional the implementation already has [Bland 2013b].

Recovery costs at up to 512 processes on Smoky: detection within ~30 ms across the two-level detector (against a 1 s link-level timeout); revoke under 50 µs; shrink comparable to `MPI_Intercomm_merge`. The outlier is `MPI_Comm_spawn`, inherited unmodified from MPI-2, which "exhibits poor performance and scalability... mostly historical: `MPI_COMM_SPAWN` has seen little use in the past, and thereby has not been the focus of intensive optimizations" [Bland 2013b]. All three of shrink, spawn, and merge separately pay for context-ID allocation, which the authors flag as a significant overhead at scale and a candidate for fusion into a single operation.

### 2.10 The programmability critique

Laguna et al. [Laguna 2016] provide the most useful counterweight. Applying ULFM to ddcMD, a large bulk-synchronous molecular dynamics code, they concluded that "although ULFM is suitable for master–worker applications, it provides few benefits for more common bulk synchronous MPI applications." The difficulty is that ULFM requires manual tracking of communicators and failure locations throughout the code, and cyclomatic-complexity analysis over 2,300+ MPI-using functions supported the claim that the required restructuring is prohibitive for codes representing decades of investment [Laguna 2016].

This critique should be taken seriously *and* read carefully for AgentMPI's purposes, because the distinction it draws is exactly the one that favors us: ULFM works well for master–worker and for applications with work-decomposition flexibility, and badly for tightly-coupled bulk-synchronous codes. Multi-agent harnesses are overwhelmingly master–worker or DAG-structured. The population of applications ULFM serves well is the population AgentMPI targets.

---

## 3. Alternatives and competitors

### 3.1 Checkpoint-on-Failure (CoF)

Bland et al.'s CoF [Bland 2012] is the cleverest minimal-mechanism design in this space. It requires nothing beyond a "high-quality" MPI-2 implementation with `MPI_ERRORS_RETURN`. The protocol: run normally with no periodic checkpointing at all; when a failure occurs, surviving processes regain control from the error handler, each independently checkpoints its local state, and all exit; the job is relaunched; the checkpoints are reloaded into a fresh MPI world; and the application's ABFT recovery procedure reconstructs the lost data from algorithmic redundancy.

"Compared to periodic checkpointing, in CoF a process pays the cost of creating a checkpoint only when a failure has happened, hence an optimal number" [Bland 2012]. The paper won Best Paper at Euro-Par 2012 and was demonstrated on QR factorization. The generalizable idea for AgentMPI is *reactive rather than periodic state capture* — pay for durability at failure time, not continuously — which is attractive when the state to capture is small (a conversation transcript) and continuous capture would be pure overhead.

### 3.2 Reinit and Reinit++ (global restart)

Reinit takes the opposite tack from ULFM: instead of exposing recovery primitives, it exposes a *rollback point*. The programmer wraps the application in a resilient-main function; on failure, the MPI runtime transparently spawns replacement processes, mends the world communicator, and returns all survivors to that point, "ensuring a consistent, initial MPI state akin to the state after MPI initialization" [Georgakoudis 2020].

Reinit++ reimplemented this in Open MPI 4.0.0 to avoid the original's requirement for job-scheduler modifications [Georgakoudis 2020]. Evaluated on CoMD, LULESH, and HPCCG against both checkpoint-restart and ULFM-based global restart, Reinit++ recovers "up to 6× faster than CR and up to 3× faster than ULFM," with near-constant recovery time as rank count grows. The two sources of advantage are distinct: against CR it avoids job re-deployment; against ULFM it avoids failure-free interference and has less recovery overhead [Georgakoudis 2020]. Both Reinit and ULFM assume the application has checkpointing in place.

Reinit is the "supervisor restarts the whole subtree" strategy — Erlang's `one_for_all`. ULFM is the toolbox that lets you build `one_for_one`.

### 3.3 FA-MPI (transactional)

Hassani, Skjellum, and Brightwell's FA-MPI proposes "per-transaction" rather than "per-operation" failure management [Hassani 2015]. MPI communication is restricted to non-blocking operations, which are wrapped in nestable **TryBlocks** delimited by `MPI_Tryblock_start` and `MPI_Tryblock_finish`. `Tryblock_finish` acts first as a `Waitall` over the enclosed request handles, then — if the transaction is group-wise — as a synchronizing non-blocking collective that broadcasts failures across the group. Transactions come in local, group-wise, and intermediate scopes, and nest to exploit application and system hierarchy. A committed transaction succeeded; a failed one can be soft-retried, rolled backward, rolled forward, or escalated to a checkpoint restart [Hassani 2015; Hassani 2013].

FA-MPI also allows the *application* to inject failures MPI cannot itself detect, via `MPI_Request_raise_error`, and synchronize them at transaction end [Hassani 2015]. That hook is directly relevant to AgentMPI: it is the mechanism by which "this agent returned garbage" becomes a first-class runtime failure rather than an application-level `if`.

The design rationale invokes a complexity argument: "in distributed environments, ad-hoc and non-transactional approaches for reliability are known to encounter a complexity barrier in implementation" [Hassani 2015]. Given Laguna et al.'s complexity findings about ULFM, this is a live concern rather than a rhetorical one.

### 3.4 Frameworks over ULFM: Fenix, LFLR, MPI Stages

**Fenix** [Gamell 2014] is the most complete application-level framework built on ULFM. It provides "online (i.e., without disrupting the job) and transparent" recovery from process, node, blade, and cabinet failures, capturing failures through ULFM return codes (intercepted via PMPI, requiring no MPI runtime changes), respawning processes on spare nodes, fixing communicators, restoring state, and returning control to the application via a longjmp to a single point where all ranks resume. Data recovery uses application-driven, diskless, implicitly-coordinated double in-memory checkpoints. Integrating Fenix into the S3D combustion code required "only 35 new, changed, or rearranged lines" [Fenix 2016]. On Titan (Cray XK7), S3D+Fenix tolerated injected failure rates above one per minute with lower overhead than coordinated checkpoint-restart at a ~2.5-hour MTBF [Gamell 2014; Fenix 2016]. Later work extended Fenix from global to local recovery, rolling back only the failed processes and their neighbors [Gamell 2016].

**LFLR (Local Failure Local Recovery)**, coined by Heroux and built by Teranishi and Heroux on ULFM [Teranishi 2014], attacks what they call the "disproportional recovery" of C/R: losing one process should not cost a global restart. LFLR keeps survivors alive during recovery, using an Application Recovery Layer in which resilient data structures inherit from a recoverable base class with `commit`/`restore` methods, a pool of hot spare processes (chosen partly because vendor MPIs such as Cray's did not support `MPI_Comm_spawn`), and in-memory RAID-like redundant storage requiring no global filesystem. Demonstrated on MiniFE.

**MPI Stages** [Sultana 2018; Sultana 2019] is the outlier: it checkpoints *the MPI library's own internal state* alongside application state. On failure, both are restored from their last synchronous checkpoints and execution continues without restarting the MPI job — live processes roll back only a few iterations of the main loop while a replacement process restarts and reintegrates. The API adds `MPIX_Checkpoint_write`, `MPIX_Checkpoint_read`, `MPIX_Get_fault_epoch`, an `MPIX_TRY_RELOAD` error code, and serialize/deserialize handler registration so applications and libraries can persist their own MPI handles. Implemented in the ExaMPI prototype, it reduced recovery time for LULESH and CoMD relative to both Reinit and checkpoint/restart [Sultana 2019].

The MPI Stages idea generalizes sharply to AgentMPI: *the runtime's own state is part of what must be recoverable*. A harness that checkpoints agent conversations but not its own routing tables, pending-message queues, and group memberships will not survive a supervisor restart.

### 3.5 Where the standard actually stands (verified against the MPI-5.0 text)

Several secondary sources — including a December 2025 talk by Hérault stating that "about 40% of ULFM is now part of the official MPI standard (MPI 5.0)" [Herault 2025] — assert that ULFM landed in MPI-5.0. **This is not correct as of the released MPI-5.0 document.** I downloaded the official MPI-5.0 report (approved 5 June 2025) and searched the extracted text: there are zero occurrences of `revoke`, `shrink`, `agree`, `ack_failed`, `get_failed`, `MPI_ERR_PROC_FAILED`, or `MPI_ERR_REVOKED`, in any case, and no Fault Tolerance chapter. The MPI-5.0 change log's "Changes in MPI-5.0" section lists only fixed-size Fortran logicals, a process-set-name restriction removal, and the new ABI chapter [MPIForum 2025]. The largest change in MPI-5.0 is the standard ABI, not fault tolerance.

What *has* happened is that the monolithic ULFM proposal was sliced, and the slices are progressing unevenly:

- **Slice 1** (chapter structure, error codes, post-error semantics, `MPI_COMM_REVOKE`, `MPI_COMM_GET_FAILED`, `MPI_COMM_ACK_FAILED`) passed a no-no vote 2022-09-30 (28–0–1), a first vote 2022-12-07 (28–0–5), and a final vote 2023-02-08 (25–0–6), and is labeled `mpi-next` [MPIIssue581].
- **Slice 2** (`MPI_COMM_AGREE`) failed to reach ballot quorum on a first vote 2023-12-05 [MPIIssue582].
- **Slice 3** (`MPI_COMM_SHRINK`) passed a no-no vote 2026-06-03 (23–0–4) but failed ballot quorum for a first vote 2026-06-04 (14–2–11) [MPIIssue583].

Separately, the fault model itself remains under-specified even within the proposal. Bouteiller's open issue records that "the fault model in the Fault Tolerance chapter is only alluded to. We had intentionally kept it somewhat blurry to give freedom to implementors in what fault types would manifest as MPI errors, but that has led to the fault model being insufficiently defined." Gropp's proposed remedy is to "specify fault model strictly in terms of user-visible behavior" and relegate implementor freedom to an advice [MPIIssue816]. As of June 2026 this was still in working-group discussion.

Meanwhile MPI-4.0 did make one real error-handling improvement: `MPI_ERRORS_ABORT`, which limits the blast radius of an abort to the object it is invoked on — a communicator, the group behind a window or file, or, when invoked on a *session*, only the local process [MPIForum 2025]. That last case is the standard's first genuine concession to per-component failure isolation. Open MPI notes that with the PRRTE runtime you must additionally pass `--enable-recovery` to stop the runtime from killing everyone else when one process aborts [OpenMPI 2025].

The honest summary for a paper: **fault tolerance has been actively pursued by the MPI Forum for fifteen years and is still not in the standard.** That is itself evidence for the AgentMPI thesis — that fault tolerance must be designed in from the beginning, because retrofitting it into a mature message-passing standard has proven to be a fifteen-year project that has not yet concluded.

---

## 4. Checkpoint/restart

### 4.1 System-level versus application-level

**System-level** checkpointers capture the whole process image transparently. **BLCR** is a GPL kernel module for Linux, aimed at CPU- and memory-intensive batch-scheduled MPI jobs [Hargrove 2006]. **DMTCP** works entirely in user space via `LD_PRELOAD`, injecting a checkpoint thread and wrapping system calls; a central coordinator synchronizes checkpoints across processes and nodes. On 128 cores across 32 nodes, checkpoint and restart times are typically 2 seconds, dropping to 0.2 s with forked checkpointing, with negligible runtime overhead; it handles fork, exec, ssh, sockets, pipes, ptys, shared memory, and PID virtualization [Ansel 2009]. **CRIU** checkpoints from the kernel side, requiring no preloading, and is the de facto standard for container migration [CRIU 2025].

The tradeoffs are instructive. DMTCP's user-space approach makes it portable but incomplete — it cannot capture kernel-only state, and its PID virtualization means a process sees a fake PID, which "is very dangerous, as application might see wrong files in the /proc filesystem if it will try to access one via its PID" [CRIU 2025]. CRIU is complete but kernel-version-dependent. BLCR is largely legacy.

**Application-level** checkpointing writes only what the application knows matters. It is smaller, portable, and restartable at different scales — and it is what every serious HPC resilience framework actually uses. This asymmetry is worth internalizing: transparent whole-image capture keeps losing to semantic, application-directed capture. For agents the asymmetry is even more extreme, since an agent's meaningful state is a transcript and a scratchpad, not a heap image.

### 4.2 Coordinated versus uncoordinated, and the domino effect

Elnozahy et al.'s survey [Elnozahy 2002] is the canonical taxonomy: checkpoint-based protocols (coordinated, uncoordinated, communication-induced) versus log-based protocols that combine checkpointing with logging of nondeterministic events.

**Coordinated** checkpointing takes a globally consistent snapshot; recovery is simple (everyone rolls back to the same line) and garbage collection is trivial (only the last checkpoint is needed). The theoretical foundation is Chandy and Lamport's distributed snapshot algorithm [Chandy 1985], which records a consistent global state — one in which every recorded received message was also recorded as sent — without stopping the computation, by circulating markers along channels and recording in-flight messages.

**Uncoordinated** checkpointing lets each process checkpoint independently, which is cheaper and more flexible but exposes the **domino effect**: because process $A$'s checkpoint may depend on a message from $B$ sent after $B$'s checkpoint, rolling back $A$ forces rolling back $B$, which forces rolling back $A$ further, and in the worst case the whole computation cascades to its initial state. This is why uncoordinated checkpointing is essentially never used alone; it is paired with message logging, which bounds rollback and eliminates the domino effect [Elnozahy 2002].

### 4.3 Multilevel checkpointing

The scaling problem is that system memory grows faster than parallel-filesystem bandwidth, so checkpoint cost comes to dominate runtime. Multilevel checkpointing writes cheap, weakly-resilient checkpoints frequently and expensive, strongly-resilient ones rarely.

**SCR** (Scalable Checkpoint/Restart, LLNL) writes to RAM, flash, or node-local disk in addition to the parallel filesystem, with an arbitrary number of configurable levels [Moody 2010; Mohror 2014]. The headline result: "low-cost checkpoint schemes that are 100×–1000× faster than the parallel file system and effective against 85% of our system failures," yielding up to 35% machine-efficiency gain and halving parallel-filesystem load, with the benefit growing with system size [Moody 2010]. Checkpoint scavenging — writing to the PFS only on application termination — is predicted to reduce PFS load 20× while maintaining high efficiency [Mohror 2014]. **FTI** (Fault Tolerance Interface) [BautistaGomez 2011] and **VeloC** [VeloC 2025], the latter an ANL/LLNL ECP effort refactoring FTI and SCR into one framework, occupy the same design space with fixed level counts.

The transferable insight is the **85% figure**: most failures are handled by the cheapest tier. A tiered durability strategy where the common case is cheap and only rare catastrophes pay full price is the right shape for an agent harness too.

### 4.4 Young's and Daly's optimal-interval formulas

**Young [1974]** gives the first-order approximation, minimizing lost time as a function of checkpoint interval:

$$\tau_{\mathrm{opt}} \;=\; \sqrt{2\,\delta\,M}$$

where $\delta$ is the time to write a checkpoint and $M$ is the mean time to interrupt (MTTI) [Young 1974; Daly 2006].

**Daly [2006]** re-derives this with a first-order cost model that includes restart time $R$:

$$\tau_{\mathrm{opt}} \;=\; \sqrt{2\,\delta\,(M+R)} \qquad \text{for } \tau + \delta \ll M$$

recovering Young exactly when $R=0$. He then relaxes two assumptions — that rework averages half a segment, and that failures never occur during a restart segment — and obtains an exact solution in terms of the Lambert $W$ function. Nondimensionalizing with $\xi = \sqrt{\delta/2M}$ and $\eta = (\tau+\delta)/M$:

$$\eta \;=\; 2\xi^{2} + 1 + W\!\left(-e^{-2\xi^{2}-1}\right)$$

Since this is not an elementary function, Daly gives a perturbation series. The three-term solution, which he shows keeps the relative error in total problem-solution time below **0.2%**, is:

$$\tilde{\tau}_{\mathrm{opt}} \;=\;
\begin{cases}
\sqrt{2\delta M}\left[1 + \dfrac{1}{3}\left(\dfrac{\delta}{2M}\right)^{1/2} + \dfrac{1}{9}\left(\dfrac{\delta}{2M}\right)\right] - \delta, & \delta < 2M \\[2ex]
M, & \delta \ge 2M
\end{cases}$$

and the lowest-order perturbation solution, equivalent to Young's model, with worst-case relative error under **5%**:

$$\tilde{\tau}_{\mathrm{opt}} \;=\;
\begin{cases}
\sqrt{2\delta M} - \delta, & \delta < \tfrac{1}{2}M \\[1ex]
M, & \delta \ge \tfrac{1}{2}M
\end{cases}$$

A result worth flagging: **restart time $R$ disappears from the higher-order optimum.** The first-order model predicts $R$ matters; the higher-order model shows "in fact $R$ has no contribution" once failures during restart segments are modeled [Daly 2006]. Also note the regime change — when checkpointing is expensive relative to MTBF ($\delta \ge M/2$), the optimal interval saturates at $M$ and the whole approach stops paying. That regime is exactly where agent harnesses sit if state capture is expensive, and it is the quantitative argument for making agent state capture cheap.

Benoit et al. [Benoit 2022] give an intuitive derivation: waste from checkpointing is $S_1 \approx C/W$ and waste from lost work is $S_2 \approx W/(2\mu)$; minimizing $S_1+S_2$ gives $W_{\mathrm{YD}} = \sqrt{2\mu C}$, and at the optimum $S_1 = S_2$ — you should spend exactly as much time checkpointing as you lose to rework.

### 4.5 Incremental and differential checkpointing

Incremental checkpointing writes only pages dirtied since the last checkpoint, typically via page-protection tracking, reducing $\delta$ at the cost of a longer restore chain. It composes naturally with multilevel schemes. `[UNVERIFIED]` — I did not read a primary source on incremental checkpointing during this pass; treat quantitative claims about its speedup as needing a citation before use.

---

## 5. Message logging and replay — and why LLM agents break it

### 5.1 The mechanism

Log-based rollback recovery combines checkpointing with logging of nondeterministic events, and its value is that it "is generally not susceptible to the domino effect, thereby allowing processes to use uncoordinated checkpointing if desired," and enables cheap output commit to the outside world without checkpointing before every external interaction [Elnozahy 2002].

Everything rests on the **piecewise-deterministic (PWD) assumption**, due to Strom and Yemini [Strom 1985]. A process's execution is modeled as a sequence of deterministic *state intervals*, each begun by a nondeterministic event. Within an interval, execution is deterministic: "if a process starts from the same state and is subjected to the same nondeterministic events at the same locations within the execution, it will always yield the same output" [Elnozahy 2002]. PWD then asserts that the protocol "can identify all the nondeterministic events executed by each process, and for each such event, logs a **determinant** that contains all information necessary to replay the event should it be necessary during recovery" [Elnozahy 2002].

An **orphan process** is a surviving process whose state depends on a nondeterministic event that cannot be reproduced during recovery. The safety condition all log-based protocols enforce is: for every event $e$ that is not stable, $\mathrm{Depend}(e) \subseteq \mathrm{Log}(e)$.

**Pessimistic logging** writes each determinant to stable storage before the corresponding message is delivered. Advantages: immediate output commit, recovery confined to failed processes, restart from the most recent checkpoint, simple garbage collection. Disadvantage: the synchronous-logging performance penalty on every message.

**Optimistic logging** writes determinants asynchronously, so failures can lose determinants and create orphans; recovery must track dependencies and roll back orphans, and garbage collection is complex.

**Causal logging** [Alvisi 1995] gets pessimistic logging's fast output commit with optimistic logging's low failure-free overhead, by piggybacking determinant information for causally-preceding events onto each message, guaranteeing that determinants of all causally-preceding events are either stable or available at some surviving process. Manetho [Elnozahy 1992] is the canonical implementation, using an antecedence graph.

**Sender-based logging** is the standard optimization for message *contents*, as distinct from determinants [Johnson 1987]. Receiver-side content logging would require writing every message payload to stable storage. Instead the sender keeps sent-but-not-yet-stable messages in volatile memory, "because after a failure it can be deterministically rebuilt using the input streams and determinants" [Clonos 2021]. Determinants (small) go to stable storage; payloads (large) stay in sender RAM.

### 5.2 What breaks when processes are LLM agents

This section matters more for AgentMPI than any other in the dossier, so let me be precise about the failure of the PWD assumption rather than gesturing at it.

PWD makes two separable claims: **(a) identifiability** — all nondeterministic events can be identified; and **(b) replayability** — for each, a bounded determinant suffices to reproduce it. An LLM agent violates both, and violates them differently.

**Replayability fails first and hardest.** For a classical process, the nondeterministic events are message receipt order, wildcard-receive resolution, timer reads, and signal delivery — a small, enumerable set whose determinants are tiny (typically a sender ID, sequence number, and receipt index). For an LLM agent, the *inference call itself* is a nondeterministic event whose determinant is not small. Even fixing the prompt, sampling seed, and decoding parameters, reproducibility is not guaranteed across GPU kernel-scheduling nondeterminism in floating-point reduction order, batch-composition effects in a continuous-batching server, model-version rollovers, and — for hosted models — provider-side changes the client cannot observe. The only determinant that reliably reproduces an LLM step is *the full output of that step*. But if the determinant is the output, message logging degenerates into result caching: you are no longer replaying the computation, you are memoizing it.

That degeneration is not a defeat — it is a design directive, and it inverts the classical economics. In HPC, determinants are small and payloads are large, which is why sender-based logging is the right optimization. For agents, the "determinant" (the model's output) *is* the payload, and recomputation is orders of magnitude more expensive than storage. The correct protocol is therefore the one HPC rejected: **log receiver-side, log everything, log synchronously.** Pessimistic logging's fatal drawback — a synchronous stable-storage write per message — costs microseconds against an operation that costs seconds. A pessimistic, receiver-side, content-logging protocol is essentially free in an agent harness and buys immediate output commit, recovery confined to the failed agent, and no orphan tracking. AgentMPI should adopt it and should say explicitly why the classical tradeoff reverses.

**Identifiability fails second, and more insidiously.** A tool-calling agent's nondeterministic events include not just the model call but every external effect: web fetches against a mutating world, file reads, subprocess execution, timestamps. Some are unloggable in principle, because the world changed. This is precisely the "interaction with the outside world" problem that motivates output commit in classical rollback recovery, but at far higher frequency — an agent may touch the outside world several times per state interval, whereas an HPC process may do so once per run.

**Two consequences follow.** First, **replay is not idempotent, so at-least-once delivery is not safe by default**. Redelivering a message to an agent whose handler performs a side effect — writing a file, posting to an API, spawning a subagent — duplicates the effect. Explicit idempotency keys on every side-effecting operation are mandatory rather than optional (Section 8.4).

Second, and more subtly: **rollback changes the answer.** In classical recovery, a rolled-back and replayed process reaches a state indistinguishable from the one it lost — that is the entire point of PWD. A rolled-back agent re-run from a checkpoint may produce a *different, equally valid* answer. Consistency of the recovered global state is therefore no longer a purely mechanical property; a "consistent" cut in the Chandy-Lamport sense can still be semantically incoherent, because peers hold conclusions derived from a version of the agent's output that the agent will now never produce again. AgentMPI must decide whether recovery targets *state equivalence* (impossible) or *goal equivalence* (achievable, but requires that message contents be treated as revisable and that downstream consumers be able to invalidate derived conclusions). This is closer to transactional compensation than to rollback recovery, and it is the strongest argument in the dossier for a transactional framing à la FA-MPI over a rollback framing.

---

## 6. Algorithm-based fault tolerance and self-stabilization

**ABFT**, introduced by Huang and Abraham [Huang 1984], "encodes data at a high level, and algorithms are designed to operate on encoded data and produce encoded output data." Row, column, and full checksum matrices let matrix addition, multiplication, scalar product, LU decomposition, and transposition detect and correct any single-processor failure in a multiprocessor system. The appeal is that redundancy is *mathematical*, not temporal: the overhead is a checksum row and column rather than a full replica or a periodic snapshot, and for dense factorizations it often shrinks with process count [Du 2012; Bland 2012].

The generalization is the important part: **exploit a structural invariant the computation already satisfies to reconstruct lost state, rather than storing a copy of it.** For AgentMPI, the analogue is not checksums but redundancy of a different kind — overlapping context between agents, derived artifacts that constrain their sources, verifiable outputs (compiles, passes tests, satisfies a schema) that let a replacement agent's work be validated without re-running the original. Where such structure exists, recovery is forward rather than backward.

**Self-stabilizing** methods, in Dijkstra's sense, reach a valid state from an arbitrary state in finitely many steps, which "imbues the system with a natural means of tolerating transient faults" [Sao 2013]. Sao and Vuduc give self-stabilizing steepest descent and conjugate gradients requiring only trivial fault detection — checking for NaNs and infinities — because the iteration's own convergence washes out perturbations [Sao 2013]. Bickson et al. extend this to dynamic linear systems where the right-hand side changes continuously, with quality guarantees once the network stabilizes [Bickson 2009].

This is the cleanest fit of any HPC technique to agent workloads. An iterative agent loop that re-reads shared state each round and converges toward a goal is *naturally* self-stabilizing: a corrupted or lost intermediate result is overwritten by the next iteration. The design directive is to prefer convergent, idempotent, re-derivable agent loops over pipelines where each stage's output is consumed once and never revisited — and to note that "approximate computing under failure" is acceptable precisely when the computation is contractive.

---

## 7. Stragglers, jitter, and slow nodes

### 7.1 OS noise and overdecomposition

HPC's straggler problem at the node level is *jitter*: OS interference and background activity desynchronize ranks, and in a bulk-synchronous code every barrier pays the maximum. MPI communication performance is known to be very sensitive to system noise, which is precisely why ULFM refuses to run a total failure detector [Bouteiller 2015].

At the application level the answer is **overdecomposition plus migration**, the Charm++ model [Kale 2014]. The programmer decomposes into many more migratable objects (*chares*) than processors "without any direct reference to the processor on which any unit resides," which "empowers the runtime system to assign units to processors, and to change the assignment at runtime as necessary" [Charm 2025].

Load balancing is then **measurement-based**, resting on the **principle of persistence**: "empirically, the computational loads and communication patterns of the tasks or objects tend to persist over time, even in dynamically evolving computations. Therefore, the load balancer can use instrumented load information to make load balancing decisions" [Zheng 2010; Bhatele 2014]. The runtime instruments the start and end of every method invocation on every chare, plus chare-to-chare and collective communication patterns, idle time, and background load, into a distributed load database — "an automatic application-independent method to obtain load information without users giving hints or manually predicting the load" [Menon 2012]. Strategies span centralized, distributed, and hierarchical; in AtSync mode all chares pause, statistics are collected, a new mapping is computed, and the framework migrates and resumes [Kale 2014].

Two properties transfer directly. First, overdecomposition is the enabling condition for *everything* — load balancing, migration, and fault tolerance all become possible only once work units are decoupled from execution sites. Second, and less obvious: measurement-based balancing is only sound because of persistence. AgentMPI should ask whether an analogous persistence assumption holds for agents — whether an agent's recent latency and token consumption predict its next step. Prompt length, tool-call depth, and model tier are all plausibly persistent within a task phase and discontinuous across phases, which suggests measurement-based scheduling with phase-change detection rather than a static cost model.

Work stealing (Cilk, and its descendants in Legion, HPX, and other AMT runtimes) is the complementary decentralized mechanism: idle workers pull work from busy ones, requiring no global load view. `[UNVERIFIED]` — I did not read primary sources on Cilk, Legion, or HPX in this pass; cite Blumofe & Leiserson for Cilk work stealing and the Legion/HPX papers directly before using specifics.

### 7.2 Backup tasks and speculative execution

MapReduce's answer to stragglers is the **backup task** [Dean 2004]. Dean and Ghemawat name the causes plainly — a bad disk degrading reads from 30 MB/s to 1 MB/s, contention from co-scheduled tasks, and in one memorable case "a bug in machine initialization code that caused processor caches to be disabled: computations on affected machines slowed down by over a factor of one hundred." The mechanism: "When a MapReduce operation is close to completion, the master schedules backup executions of the remaining in-progress tasks. The task is marked as completed whenever either the primary or the backup execution completes." Tuned to increase resource usage by no more than a few percent, it is decisive: "the sort program... takes 44% longer to complete when the backup task mechanism is disabled" [Dean 2004].

Zaharia et al. [Zaharia 2008] showed the mechanism is fragile under heterogeneity. Hadoop's scheduler assumes homogeneous nodes and linear task progress; in a virtualized data center such as EC2 those assumptions break and the scheduler thrashes, launching excessive speculative copies. Their **LATE** (Longest Approximate Time to End) scheduler speculates on the task estimated to finish farthest in the future, because that task offers the greatest opportunity for a copy to overtake it. Three heuristics keep it stable: cap speculative tasks at 10% of available slots, do not launch backups on nodes below the 25th percentile of progress rate, and only speculate on tasks below the 25th percentile of progress rate. LATE improves Hadoop response time by a factor of 2 on 200-node EC2 clusters [Zaharia 2008].

LATE's failure analysis is the transferable part. Speculation without admission control is self-defeating: duplicate work consumes the capacity that would have relieved the straggler. Any AgentMPI hedging policy needs LATE's three guards — a global budget cap, a "don't hedge onto a slow worker" rule, and a "don't hedge a task that isn't actually slow" rule.

### 7.3 Hedged and tied requests

Dean and Barroso's *The Tail at Scale* [Dean 2013] — note the title precisely; it is **"The Tail at Scale," CACM 56(2), February 2013, pp. 74–80**, not "The Tail at Large" — quantifies tail amplification under fan-out. If a server responds in 10 ms typically with a 99th-percentile of one second, a request touching one server is slow 1% of the time; a request that must collect from 100 such servers in parallel is slow **63%** of the time. Even at one-in-10,000 slow responses, a 2,000-server fan-out leaves almost one user request in five above one second. Real measurements from a Google service: 99th-percentile latency for a single random request measured at the root is 10 ms, but for *all* requests to finish it is 140 ms, and for 95% to finish it is 70 ms — "waiting for the slowest 5% of the requests to complete is responsible for half of the total 99th-percentile latency" [Dean 2013].

**Hedged requests**: send to one replica, and if it has not returned within roughly the 95th-percentile expected latency, send a duplicate to another; take the first response and cancel the rest. Deferring to p95 "limits the additional load to approximately 5%." The measured result is dramatic: reading 1,000 keys from a BigTable spread over 100 servers, "sending a hedging request after a 10ms delay reduces the 99.9th-percentile latency for retrieving all 1,000 values from 1,800ms to 74ms while sending just 2% more requests" [Dean 2013]. Hedged requests can additionally be tagged lower-priority than primaries.

**Tied requests** close hedging's window of vulnerability. Observing that much variability is *queueing* delay before execution begins — "once a request is actually scheduled and begins execution, the variability of its completion time goes down substantially" — tied requests enqueue copies at two servers simultaneously, each tagged with the other's identity; whichever begins execution first sends a cancellation to its counterpart, which aborts if still enqueued [Dean 2013]. In a Google distributed-filesystem benchmark, tied requests with 1 ms delay reduced median read latency by 16% and nearly 40% at the 99.9th percentile. The recommended 1 ms delay between the two sends — roughly twice the average network message delay — exists to prevent both copies from starting when queues are empty and cancellation cannot arrive in time [Dean 2013].

Dean and Barroso explicitly compare against probing queue lengths and submitting to the least-loaded server, and find it inferior for three reasons: load changes between probe and request, service times are hard to estimate, and all clients picking the same least-loaded server creates hot spots [Dean 2013].

The mapping to AgentMPI is close to one-to-one, with one improvement available. Fan-out over agents amplifies tails exactly as fan-out over leaf servers does; hedging an agent call at p95 is cheap in *count* but expensive in *tokens*, so the tied-request refinement — cancel as soon as the winner begins producing tokens — is more valuable here than in Google's setting, because streaming APIs give an early, observable "execution has begun" signal that queueing systems had to synthesize. There is also a second dividend unavailable to Dean and Barroso: two hedged agent responses are not just a latency race but a *cheap correctness signal*, since disagreement between them flags a task where the model is unreliable.

Their **micro-partition** technique is the same insight as Charm++ overdecomposition, arrived at independently: BigTable machines manage 20–1,000 tablets each, so load can be shed in ~5% increments and failure recovery is faster because many machines each pick up one unit of work [Dean 2013].

---

## 8. Distributed-systems fundamentals

### 8.1 FLP and CAP

**FLP** [Fischer 1985]: "no completely asynchronous consensus protocol can tolerate even a single unannounced process death," even with reliable message delivery and only crash (not Byzantine) faults. The proof turns on the impossibility of distinguishing a crashed process from a slow one without timing assumptions. Every practical consensus system therefore buys its way out by adding an assumption: partial synchrony, randomization, or a failure detector.

For AgentMPI the FLP-relevant observation is that agents make the crashed/slow distinction *worse* than in any classical system. An agent can legitimately take seconds or minutes; timeouts calibrated to catch real hangs will fire on healthy long-running work. Any timeout is simultaneously a false-positive and a false-negative generator, and the runtime must be correct under both — which argues for making incorrect failure declarations *safe* (via revoke-style promotion and idempotency) rather than trying to make them rare.

**CAP** [Brewer 2000; Gilbert 2002]: under network partition, a distributed system must sacrifice consistency or availability. `[UNVERIFIED]` — I relied on background knowledge here rather than reading Gilbert & Lynch's proof in this pass; verify the formal statement before citing precisely. The practically useful framing for AgentMPI is that a "partitioned" agent — one that is unreachable or unresponsive but may still be working and may still emit side effects — forces exactly this choice, and the answer should be recorded per message class rather than globally.

### 8.2 Failure detectors, heartbeats, leases

Chandra and Toueg [Chandra 1996] characterize failure detectors by **completeness** (every crashed process is eventually suspected) and **accuracy** (correct processes are not wrongly suspected forever), showing consensus is solvable with detectors that "make an infinite number of mistakes," and that Consensus and Atomic Broadcast are mutually reducible. Chandra, Hadzilacos, and Toueg [ChandraHT 1992] identify $\Diamond S$ (equivalently $\Omega$) as the weakest detector for consensus with a majority of correct processes.

The **$\phi$-accrual failure detector** [Hayashibara 2004] replaces a boolean verdict with a continuous suspicion level, decoupling monitoring from interpretation so different subsystems can apply different thresholds to the same signal:

$$\phi = -\log_{10}\!\big(1 - F(t_{\mathrm{now}} - T_{\mathrm{last}})\big)$$

where $F$ is the CDF of a normal distribution whose mean and standard deviation are estimated from a sliding window of observed heartbeat inter-arrival times. The scale is calibrated in error probability: "assuming that we decide to suspect $p$ when $\phi \ge \Phi = 1$, then the likeliness that we will make a mistake... is about 10%. The likeliness is about 1% with $\Phi = 2$, 0.1% with $\Phi = 3$, and so on" [Hayashibara 2004]. Production implementations (Akka, Cassandra) add an `acceptable-heartbeat-pause` margin to survive GC pauses and transient network drops, and a minimum standard deviation to avoid oversensitivity when the environment has been unusually regular [Akka 2025].

Two properties make $\phi$-accrual the right primitive for agents. It is *adaptive* — the threshold self-calibrates to observed latency rather than being hardcoded, which matters when p50 and p99 for an agent step differ by an order of magnitude and shift with prompt size and model. And it is *graded* — a runtime can hedge at $\phi = 1$ (10% wrong) while only declaring death at $\phi = 4$, using a single monitoring stream for two decisions with very different costs.

Chen et al.'s finding, adopted by Bosilca et al. [Bosilca 2016], is that the push protocol (emitter spontaneously sends heartbeats to its observer) outperforms the pull variant (observer requests, emitter replies).

**Leases** are time-bounded grants of authority that expire unless renewed. They convert the unbounded question "is this process alive?" into the bounded, locally-decidable one "has its lease expired?", which is why they are the standard mechanism for safe failover: after lease expiry the holder can be assumed to have stopped acting, without any communication with it. For AgentMPI a lease on a work item — rather than a heartbeat on a process — is the cleaner primitive, since it makes reassignment safe by construction. `[UNVERIFIED]` — cite Gray & Cheriton (1989) for the original lease paper before publication.

### 8.3 Delivery semantics and idempotency

**At-most-once** never duplicates and may drop. **At-least-once** never drops and may duplicate. **Exactly-once** delivery is not achievable in an asynchronous system with crashes; what is achievable is exactly-once *effect*, built from at-least-once delivery plus receiver-side deduplication. The standard construction is an **idempotency key**: the sender attaches a stable unique identifier, the receiver records processed keys durably and returns the memoized result for repeats.

This is not optional for AgentMPI, for two compounding reasons. Duplicate delivery of a side-effecting agent message duplicates the side effect. And because agents are nondeterministic (Section 5.2), a duplicate that is *re-executed* rather than memoized produces a *different* result, so the system exhibits not just duplicated effects but divergent ones. Deduplication must therefore return the cached result, not re-run the handler.

### 8.4 Two-phase commit versus consensus

**2PC** is a blocking atomic-commitment protocol: if the coordinator fails after prepare and before commit, participants holding locks cannot unilaterally decide and must wait. 3PC removes blocking under stronger synchrony assumptions but is fragile under partition. **Consensus protocols** — Paxos [Lamport 1998] and Raft [Ongaro 2014] — solve the related but distinct problem of agreeing on a value with a majority quorum, and are non-blocking as long as a majority survives.

Hérault et al. explain why ULFM does not simply use Paxos, and the reasoning is directly reusable. Paxos "targets high-availability systems, like distributed databases, storing the state of the different processes in reliable storage to tolerate intermittent failures," and its phase-based, quorum-oriented structure with distinct proposer/acceptor/learner roles does not match an environment where all participants are peers, communication is tightly synchronized, and the required decision is a single boolean over a known group [Herault 2015]. ERA's tree-based single-logarithmic-phase structure is cheaper precisely because it exploits assumptions Paxos declines to make.

The design lesson: match the agreement protocol to the failure model and topology you actually have. AgentMPI has a known participant set, a supervisor that can act as a natural root, and enormous per-message latency budget — closer to ERA's world than to Paxos's.

### 8.5 The end-to-end argument

Saltzer, Reed, and Clark [Saltzer 1984]: a function can be completely and correctly implemented only with the knowledge of the application at the endpoints; providing it in the communication subsystem is either redundant or insufficient, and is justified only as a performance optimization. `[UNVERIFIED]` — quoted from background knowledge; verify against the paper before publication.

The consequence for AgentMPI is sharp and worth stating in the paper. No transport-level mechanism can determine that an agent's answer is *correct*; correctness is an application-level property that only an application-level check can establish. The runtime's job is therefore to make end-to-end verification cheap and composable — carry result provenance, support cheap retry with a different worker, let the application register acceptance predicates — rather than to attempt correctness guarantees it cannot deliver. This is the same conclusion ULFM reached from a different direction ("the library should not recover for you"), and the two arguments reinforce each other.

### 8.6 Logical clocks

**Lamport clocks** [Lamport 1978] assign scalar timestamps consistent with causality: if $a \to b$ then $C(a) < C(b)$. The converse does not hold, so they cannot detect concurrency. **Vector clocks** carry one counter per process and do characterize causality exactly: $a \to b$ iff $V(a) < V(b)$, and incomparable vectors mean genuine concurrency. `[UNVERIFIED]` for the Fidge/Mattern vector-clock attributions specifically; Lamport 1978 is verified by general familiarity but was not re-read in this pass.

Causal metadata is what makes several things above possible at once: causal message logging piggybacks exactly this information [Alvisi 1995]; consistent-cut determination in Chandy-Lamport snapshots depends on it [Chandy 1985]; and for AgentMPI it is what allows the runtime to answer "which agents' conclusions depend on the output this failed agent produced?" — the orphan-detection question of Section 5.2, and the prerequisite for invalidating derived work after a rollback that changes an answer.

### 8.7 Erlang/OTP: supervision trees and "let it crash"

Armstrong's thesis [Armstrong 2003] is the closest existing engineering model for agent lifecycle management, and its argument begins from a premise that transfers exactly: eliminating all software errors in large systems is unsolved, therefore "the only realistic way to build large reliable systems is by partitioning the system into independent parallel processes, and by providing mechanisms for monitoring and restarting these processes."

**Process isolation** is the enabling property, and Armstrong is emphatic about why: "as soon as two processes share any resource — a pointer to memory, or a mutex etc — the possibility exists that a software error in one of the processes will corrupt the shared resource." Erlang processes share nothing and communicate only by message passing, so a crash is contained by construction. Restart is safe because there is no shared state to have been left inconsistent. This is the deepest lesson for AgentMPI: *shared mutable state is what makes crashes uncontainable*, and an agent runtime that lets agents share mutable context loses the ability to restart any of them safely.

**"Let it crash"** follows: rather than defensively handling every error in-line, let the process die and let a supervisor restart it from a known-good state. Defensive error handling scattered through business logic is both incomplete and unreadable; concentrating recovery in supervisors makes the recovery policy explicit and auditable.

**Supervision strategies** [ErlangOTP 2025], each a different blast-radius policy:

- **`one_for_one`** — if a child terminates, only that child is restarted. The default. Correct when children are independent.
- **`one_for_all`** — if any child terminates, all children are terminated and restarted. Correct when children hold mutual invariants that a partial restart would violate.
- **`rest_for_one`** — if a child terminates, the children *after it in start order* are terminated, then the terminated child and those following are restarted. Correct for pipelines with a defined dependency order, where downstream stages depend on upstream ones but not vice versa.
- **`simple_one_for_one`** — a simplified `one_for_one` where all children are dynamically added instances of the same process type running the same code. The worker-pool strategy.

**Restart intensity** is the guard against restart loops: with `intensity` $MaxR$ and `period` $MaxT$, "if more than $MaxR$ restarts occur within $MaxT$ seconds, the supervisor terminates all child processes and then itself," with reason `shutdown`. Defaults are $MaxR = 1$, $MaxT = 5$ [ErlangOTP 2025]. Because the supervisor's own death propagates to *its* supervisor, intensity produces automatic escalation up the tree: a fault that a local restart cannot fix is retried at successively coarser granularity until some level's restart resolves it or the whole application gives up. This is a strictly better structure than a flat retry counter, and AgentMPI should adopt it directly.

**Child restart types** [ErlangOTP 2025]: `permanent` children are always restarted; `temporary` children are never restarted, even when a sibling's death causes them to be terminated; `transient` children are restarted only on abnormal termination (any reason other than `normal`, `shutdown`, or `{shutdown, Term}`). The three-way distinction maps cleanly onto agent roles: a long-lived coordinator is `permanent`, a speculative exploratory agent is `temporary`, a task worker is `transient`.

**Links versus monitors** are the two failure-notification primitives and the distinction is important. Links are *bidirectional*: if either linked process dies, an exit signal propagates to the other, which by default dies too, so links build fate-sharing groups. Monitors are *unidirectional and non-invasive*: the monitoring process receives a `DOWN` message and decides what to do, while the monitored process is unaffected and unaware. Supervisors monitor rather than link to children so that a child's death is an event to handle, not a contagion. `[UNVERIFIED]` in its details — I read the OTP supervisor documentation but not the links/monitors reference page in this pass.

Children are started in the order given by the child specification list and terminated in reverse order [ErlangOTP 2025] — the dependency-ordering discipline that makes `rest_for_one` meaningful.

---

## 9. Elasticity and dynamic resource management

### 9.1 Why MPI dynamic process management failed to catch on

MPI-2 added `MPI_Comm_spawn`, and it has been largely unused. Comprés et al. attribute this to "the performance cost and limitations of the spawn operation" [Compres 2016]. The ULFM measurements corroborate: spawn "exhibits poor performance and scalability... mostly historical: `MPI_COMM_SPAWN` has seen little use in the past, and thereby has not been the focus of intensive optimizations from implementors," though the authors are careful to note this is not a theoretical barrier [Bland 2013b]. LFLR's designers avoided spawn entirely, using pre-allocated hot spares because vendor MPIs such as Cray's did not support it [Teranishi 2014].

There is a deeper structural issue. Spawn produces an *intercommunicator*, not an enlarged `MPI_COMM_WORLD`; reintegrating new processes into a flat world requires merges and a full data redistribution the application must write itself. The abstraction fights the SPMD programming model rather than supporting it.

### 9.2 Elastic MPI and Invasive MPI

Comprés et al.'s **Elastic MPI** [Compres 2016] adds four operations to MPI, backed by coordinated changes to MPICH, the PMI layer, and the SLURM resource manager:

- `MPI_Init_adapt` — initialize in elastic mode, returning a status distinguishing `NEW` from `JOINING` processes.
- `MPI_Probe_adapt` — poll whether the resource manager has initiated a reconfiguration.
- `MPI_Comm_adapt_begin(intercomm, future_comm_world)` — open an adaptation window. The intercomm is spawn-like, connecting parents and children; `future_comm_world` contains all staying parents plus the new children, while departing parents receive `MPI_COMM_NULL`.
- `MPI_Comm_adapt_commit` — close the window, set `MPI_COMM_WORLD` to the new world, and terminate departing processes.

Data redistribution between begin and commit is entirely the application's responsibility. Reported overhead is negligible thanks to a latency-hiding design, "leaving the application's time for data redistribution as the only significant performance cost" [Compres 2016]. The design descends from the Invasive Computing project's Invasive MPI. `[UNVERIFIED]` — I did not read a primary Invasive MPI source; verify the lineage before asserting it.

The negative result is the informative one: the *mechanism* is cheap and the *data redistribution* is expensive. Elasticity is limited by state, not by process management. For AgentMPI this is encouraging, since an agent's state is small and its "redistribution" is a work-queue reassignment.

### 9.3 DMR and DMRlib

Iserte et al.'s **DMR** (Dynamic Management of Resources) and **DMRlib** provide a higher-level abstraction than Elastic MPI, orchestrating application, parallel runtime, and resource manager [Iserte 2021]. Their explicit critique of Elastic MPI is that "in addition to being MPICH-dependent (support for other MPI implementations is not presented), this approach does not assist in data redistribution." DMRlib initiates reconfiguration by talking to SLURM, spawns processes via standard `MPI_Comm_spawn`, and provides predefined data-redistribution functions in addition to point-to-point and collective primitives. Later work refactors DMRlib into a modular Dynamic Resource Manager supporting multiple runtimes and resource managers, integrating the Malleability Module of the Proteo framework for additional spawning strategies and asynchronous reconfiguration [Iserte 2025].

### 9.4 The Sessions + dynamic direction

MPI-4.0's **Sessions** model is the standard's structural opening toward elasticity. Instead of `MPI_Init` and a static `MPI_COMM_WORLD`, an application gets a session handle, queries the runtime for a *process set*, derives an `MPI_Group`, and creates a communicator. The stated effects are to "deliver runtime information of (changing) information to the MPI library," enable resource isolation between sessions, and "eliminate the static resource `MPI_COMM_WORLD`" — which "opens the door to malleability in MPI" [Schulz 2026]. Sessions can be initialized and finalized multiple times within one application, independently and without coordination, which suits workflow-oriented applications orchestrating multiple tasks [Zhou 2026].

Work in progress as of 2026 targets MPI-6: session-friendly spawning (`MPI_spawn`, `MPI_iSpawn`, `MPI_spawn_test_parent`, including the non-blocking spawn MPI has always lacked), and Dynamic Processes with Process Sets (DPP) with operations such as `MPI_Session_dyn_psetop` for creating and modifying process sets in cooperation with the resource manager [MPIForum2026]. Open questions recorded in the Forum's March 2026 notes include integrating parent-process information with process sets, how processes communicate after spawning without sessions, reconciling global and local session state, and whether process-set names must be globally unique [MPIForum2026]. A prototype spanning Open MPI, OpenPMIx, PRRTE, and a Python resource manager demonstrates feasibility [Huber 2024].

One implementation note is worth carrying into the paper: MPICH's maintainers observe that "the true MPI Sessions model breaks the long-standing assumption that a single, global initialization phase always occurs — an assumption that MPICH relies on for efficient global setup," requiring significant redesign of initialization and resource management [Zhou 2026]. Dynamic membership is not a feature that can be layered onto a statically-initialized runtime; it is a foundational choice. AgentMPI should make it at the start.

---

## 10. Failure-model design checklist for a message-passing runtime whose processes are non-deterministic, slow, expensive, and can return wrong answers

1. **No operation may block indefinitely after a peer failure; every call must either complete or return an error within a bounded time.** *(ULFM's core guarantee [Bland 2013b])*
2. **Default to a non-fatal error handler, and make continuing after an error a specified behavior rather than an implementation courtesy.** *(inverts `MPI_ERRORS_ARE_FATAL` and the MPI-5.0 advice-to-implementors [MPIForum 2025])*
3. **Pay for globally consistent failure knowledge, because the per-message consensus cost that MPI refused is negligible against LLM-call latency.** *(inverts the uniform-error-reporting rejection [Bouteiller 2015])*
4. **Provide an explicit, unilateral, permanent `revoke` on any communication scope so one participant can force all peers out of a doomed collective phase.** *(`MPI_Comm_revoke` [Bland 2013b; Bouteiller 2015])*
5. **Never repair a group in place; make recovery produce a new group object, leaving the old one permanently dead.** *(why Run-Through Stabilization failed and `shrink` succeeded [Hursey 2011a; Bland 2013b])*
6. **Provide a fault-tolerant agreement primitive that cannot itself fail due to participant failure, and build group reconstruction on top of it.** *(`MPI_Comm_agree`, and shrink-as-agreement [Bland 2013b; Herault 2015])*
7. **Propagate failure and revocation over a bounded-degree overlay with logarithmic diameter, not by flooding.** *(BMG, degree $2\lceil\log_2 n\rceil$; flooding crashed the OpenIB driver [Angskun 2007; Bland 2013b])*
8. **Let agreement participants return as soon as the decision is fixed, while keeping the decision available to serve late stragglers.** *(ERA's early-returning property [Herault 2015])*
9. **Promote unresponsive-but-possibly-alive agents to fail-stop by fiat, and make that promotion permanent and globally agreed.** *(ULFM's transient-error promotion [Bland 2013b] — the only tractable answer to FLP's crashed-versus-slow ambiguity [Fischer 1985])*
10. **Use a graded, adaptive suspicion signal rather than a fixed timeout, and drive different actions off different thresholds — hedge at low confidence, declare death only at high.** *($\phi$-accrual [Hayashibara 2004])*
11. **Monitor via push heartbeats on a ring with one observer per node, off the application's critical path, rather than all-to-all monitoring.** *(SC16 detector; jitter avoidance [Bosilca 2016])*
12. **Adopt pessimistic, receiver-side, synchronous logging of full message contents, because for agents the determinant *is* the payload and recomputation dwarfs storage.** *(deliberately inverts sender-based logging and the PWD economics [Elnozahy 2002; Johnson 1987])*
13. **Treat every agent step as an unreplayable nondeterministic event: recovery must target goal equivalence, never state equivalence.** *(PWD assumption fails for LLM inference [Strom 1985; Elnozahy 2002])*
14. **Track causal dependencies so the runtime can enumerate which downstream conclusions derive from a rolled-back agent's output and invalidate them.** *(vector clocks and orphan detection [Lamport 1978; Elnozahy 2002])*
15. **Require idempotency keys on every side-effecting message and deduplicate by returning the memoized result, never by re-executing.** *(at-least-once plus exactly-once effect; re-execution diverges under nondeterminism)*
16. **Checkpoint the runtime's own state — routing tables, pending queues, group membership — alongside agent state.** *(MPI Stages [Sultana 2019])*
17. **Capture durable state reactively at failure time rather than on a periodic schedule when the state is small and failures are localized.** *(Checkpoint-on-Failure [Bland 2012])*
18. **Tier durability so the common failure is handled by the cheapest mechanism, reserving expensive global capture for rare catastrophes.** *(multilevel C/R: cheap tiers 100–1000× faster and covered 85% of failures [Moody 2010])*
19. **If any periodic snapshotting is used, set the interval by Young/Daly, $\tau_{\mathrm{opt}} \approx \sqrt{2\delta M}$, and treat $\delta \ge M/2$ as a signal that snapshotting is the wrong mechanism entirely.** *(Young/Daly and its saturation regime [Young 1974; Daly 2006])*
20. **Overdecompose work into far more units than agents, and never let an application name an execution site directly.** *(Charm++ migratable objects; Google micro-partitions [Kale 2014; Dean 2013])*
21. **Schedule from measured per-agent load and latency history rather than a static cost model, with explicit detection of phase changes that break persistence.** *(measurement-based load balancing and the principle of persistence [Zheng 2010; Bhatele 2014])*
22. **Hedge slow agent calls at roughly the p95 of their observed latency, and cancel losers on first token rather than on completion.** *(hedged and tied requests: 1,800 ms → 74 ms for 2% extra requests [Dean 2013])*
23. **Guard every hedge with a global budget cap, a don't-hedge-onto-a-slow-worker rule, and a don't-hedge-a-task-that-isn't-slow rule.** *(LATE's three heuristics: 10% cap, 25th-percentile node and task thresholds [Zaharia 2008])*
24. **Structure agents as a supervision tree with per-node strategies — restart one, restart all, restart the rest of the pipeline, or restart a pool member — and per-child `permanent`/`transient`/`temporary` restart policies.** *(Erlang/OTP supervisors [Armstrong 2003; ErlangOTP 2025])*
25. **Bound restarts by intensity and period, and let supervisor death escalate the fault to the next level up rather than retrying forever at one granularity.** *(OTP restart intensity, defaults $MaxR{=}1$, $MaxT{=}5$ [ErlangOTP 2025])*
26. **Share no mutable state between agents, because shared state is what makes a crash uncontainable and a restart unsafe.** *(Armstrong's isolation argument [Armstrong 2003])*
27. **Prefer convergent, re-derivable agent loops over single-consumption pipelines, so that a lost or corrupt intermediate is washed out by the next iteration.** *(self-stabilizing solvers [Sao 2013]; ABFT forward recovery [Huang 1984])*
28. **Make membership dynamic from the first line of the design, since retrofitting it onto a statically-initialized runtime requires rebuilding the initialization path.** *(MPI Sessions versus `MPI_COMM_WORLD`; MPICH's redesign burden [Schulz 2026; Zhou 2026])*
29. **Expose an application-callable "this result is wrong" error injection that participates in the same agreement and recovery machinery as a crash.** *(FA-MPI's `MPI_Request_raise_error` [Hassani 2015] — the only cited mechanism that admits application-detected semantic failure)*
30. **Do not attempt to guarantee answer correctness in the runtime; instead make end-to-end verification cheap, composable, and able to trigger retry on a different agent.** *(the end-to-end argument [Saltzer 1984], converging with ULFM's "the library should not recover for you")*

---

## References

BibTeX for every entry below is in `refs_04.bib`; keys are these labels with spaces removed (`[Bland 2013b]` → `Bland2013b`).

- [Akka 2025] Lightbend / Apache Pekko. *Phi Accrual Failure Detector documentation and implementation.* Accessed 2026. https://doc.akka.io/ and https://github.com/apache/pekko
- [Alvisi 1995] Alvisi, L., and Marzullo, K. "Message Logging: Pessimistic, Optimistic, and Causal." *15th ICDCS*, 1995, 229–236. doi:10.1109/icdcs.1995.500024
- [Angskun 2007] Angskun, T., Bosilca, G., and Dongarra, J. "Binomial Graph: A Scalable and Fault-Tolerant Logical Network Topology." *ISPA'07*, LNCS 4742, Springer, 2007, 471–482.
- [Ansel 2009] Ansel, J., Arya, K., and Cooperman, G. "DMTCP: Transparent Checkpointing for Cluster Computations and the Desktop." *IPDPS 2009*. doi:10.1109/ipdps.2009.5161063
- [Armstrong 2003] Armstrong, J. *Making Reliable Distributed Systems in the Presence of Software Errors.* PhD thesis, Royal Institute of Technology (KTH), Stockholm, 2003.
- [BautistaGomez 2011] Bautista-Gomez, L., Tsuboi, S., Komatitsch, D., Cappello, F., Maruyama, N., and Matsuoka, S. "FTI: High Performance Fault Tolerance Interface for Hybrid Systems." *SC'11*. doi:10.1145/2063384.2063427
- [Benoit 2022] Benoit, A., Du, Y., Herault, T., Marchal, L., Pallez, G., Perotin, L., Robert, Y., Sun, H., and Vivien, F. "Checkpointing à la Young/Daly: An Overview." *IC3 2022* (invited paper). HAL: hal-03830322
- [Bhatele 2014] Bhatele, A., Fourestier, S., Menon, H., Kale, L. V., and Pellegrini, F. "Applying Graph Partitioning Methods in Measurement-based Dynamic Load Balancing." Charm++ technical report 14-41, University of Illinois, 2014.
- [Bickson 2009] Bickson, D., Tock, Y., Zymnis, A., Boyd, S., and Dolev, D. "Distributed Large Scale Network Utility Maximization / Self-stabilizing Numerical Iterative Computation." arXiv:0901.2682, 2009. doi:10.48550/arxiv.0901.2682
- [Bland 2012] Bland, W., Du, P., Bouteiller, A., Herault, T., Bosilca, G., and Dongarra, J. "A Checkpoint-on-Failure Protocol for Algorithm-Based Recovery in Standard MPI." *Euro-Par 2012*, LNCS 7484, Springer, 477–488 (Best Paper Award). doi:10.1007/978-3-642-32820-6_48
- [Bland 2013a] Bland, W., Bouteiller, A., Herault, T., Bosilca, G., and Dongarra, J. "Post-Failure Recovery of MPI Communication Capability: Design and Rationale." *IJHPCA* 27(3), 2013, 244–254. doi:10.1177/1094342013488238
- [Bland 2013b] Bland, W., Bouteiller, A., Herault, T., Hursey, J., Bosilca, G., and Dongarra, J. "An Evaluation of User-Level Failure Mitigation Support in MPI." *Computing* 95(12), 2013, 1171–1184. doi:10.1007/s00607-013-0331-3
- [Bosilca 2016] Bosilca, G., Bouteiller, A., Guermouche, A., Herault, T., Robert, Y., Sens, P., and Dongarra, J. "Failure Detection and Propagation in HPC Systems." *SC'16*, 27:1–27:11. doi:10.1109/SC.2016.26
- [Bouteiller 2015] Bouteiller, A., Bosilca, G., and Dongarra, J. "Plan B: Interruption of Ongoing MPI Operations to Support Failure Recovery." *EuroMPI '15*. doi:10.1145/2802658.2802668
- [Brewer 2000] Brewer, E. "Towards Robust Distributed Systems." Keynote, *PODC 2000*. `[UNVERIFIED]`
- [Chandra 1996] Chandra, T. D., and Toueg, S. "Unreliable Failure Detectors for Reliable Distributed Systems." *JACM* 43(2), 1996, 225–267. doi:10.1145/226643.226647
- [ChandraHT 1992] Chandra, T. D., Hadzilacos, V., and Toueg, S. "The Weakest Failure Detector for Solving Consensus." *PODC 1992*, 147–158; journal version *JACM* 43(4), 1996, 685–722. doi:10.1145/234533.234549
- [Chandy 1985] Chandy, K. M., and Lamport, L. "Distributed Snapshots: Determining Global States of Distributed Systems." *ACM TOCS* 3(1), 1985, 63–75. doi:10.1145/214451.214456
- [Charm 2025] Charm++ Development Team. *The Charm++ Parallel Programming System Manual.* https://charm.readthedocs.io/
- [Clonos 2021] Silvestre, P. F., Fragkoulis, M., Spinellis, D., and Katsifodimos, A. "Clonos: Consistent Causal Recovery for Highly-Available Streaming Dataflows." *SIGMOD '21*. doi:10.1145/3448016.3457320
- [Compres 2016] Comprés, I., Mo-Hellenbrand, A., Gerndt, M., and Bungartz, H.-J. "Infrastructure and API Extensions for Elastic Execution of MPI Applications." *EuroMPI 2016*. doi:10.1145/2966884.2966917
- [CRIU 2025] CRIU Project. *Comparison to Other CR Projects.* https://criu.org/Comparison_to_other_CR_projects
- [Daly 2006] Daly, J. T. "A Higher Order Estimate of the Optimum Checkpoint Interval for Restart Dumps." *Future Generation Computer Systems* 22(3), 2006, 303–312. doi:10.1016/j.future.2004.11.016
- [Dean 2004] Dean, J., and Ghemawat, S. "MapReduce: Simplified Data Processing on Large Clusters." *OSDI 2004*, 137–150.
- [Dean 2013] Dean, J., and Barroso, L. A. "The Tail at Scale." *Communications of the ACM* 56(2), February 2013, 74–80. doi:10.1145/2408776.2408794
- [Du 2012] Du, P., Bouteiller, A., Bosilca, G., Herault, T., and Dongarra, J. "Algorithm-Based Fault Tolerance for Dense Matrix Factorizations." *PPoPP 2012*, 225–234. doi:10.1145/2145816.2145845
- [Elnozahy 1992] Elnozahy, E. N., and Zwaenepoel, W. "Manetho: Transparent Rollback-Recovery with Low Overhead, Limited Rollback, and Fast Output Commit." *IEEE Trans. Computers* 41(5), 1992, 526–531. doi:10.1109/12.142678
- [Elnozahy 2002] Elnozahy, E. N., Alvisi, L., Wang, Y.-M., and Johnson, D. B. "A Survey of Rollback-Recovery Protocols in Message-Passing Systems." *ACM Computing Surveys* 34(3), 2002, 375–408. doi:10.1145/568522.568525
- [ErlangOTP 2025] Ericsson AB. *Erlang/OTP `supervisor` Behaviour Documentation*, stdlib. https://www.erlang.org/doc/apps/stdlib/supervisor.html
- [Fagg 2000] Fagg, G. E., and Dongarra, J. "FT-MPI: Fault Tolerant MPI, Supporting Dynamic Applications in a Dynamic World." *EuroPVM/MPI 2000*, LNCS 1908, Springer, 346–353.
- [Fagg 2004] Fagg, G. E., and Dongarra, J. "Building and Using a Fault-Tolerant MPI Implementation." *IJHPCA* 18(3), 2004, 353–361. doi:10.1177/1094342004046052
- [Fenix 2016] Gamell, M., Teranishi, K., Heroux, M. A., Mayo, J., Kolla, H., Chen, J., and Parashar, M. "Fenix: An Online Failure Recovery Library for MPI Applications on top of ULFM." Sandia/OSTI 1346118, 2016.
- [FTHub 2025] Fault Tolerance Research Hub. *ULFM Overview.* https://fault-tolerance.org/
- [FTHubERA 2015] Fault Tolerance Research Hub. *Logarithmic Agreement Routine.* 27 August 2015. https://fault-tolerance.org/2015/08/27/logarithmic-agreement-routine/
- [FTMPI 2004] Innovative Computing Laboratory, University of Tennessee. *FT-MPI Overview: Communicator and Message Modes.* https://icl.utk.edu/ftmpi/overview/
- [Fischer 1985] Fischer, M. J., Lynch, N. A., and Paterson, M. S. "Impossibility of Distributed Consensus with One Faulty Process." *JACM* 32(2), 1985, 374–382. doi:10.1145/3149.214121
- [Gamell 2014] Gamell, M., Katz, D. S., Kolla, H., Chen, J., Klasky, S., and Parashar, M. "Exploring Automatic, Online Failure Recovery for Scientific Applications at Extreme Scales." *SC'14*, 895–906. doi:10.1109/SC.2014.78
- [Gamell 2016] Gamell, M., Teranishi, K., Kolla, H., Mayo, J., Heroux, M. A., Chen, J., and Parashar, M. "Evaluating Online Global Recovery with Fenix Using Application-Aware In-Memory Checkpointing Techniques." *ICPP Workshops 2016*. doi:10.1109/ICPPW.2016.56
- [Georgakoudis 2020] Georgakoudis, G., Guo, L., and Laguna, I. "Reinit++: Evaluating the Performance of Global-Restart Recovery Methods for MPI Fault Tolerance." *ISC High Performance 2020*, LNCS 12151, 536–554. arXiv:2102.06896
- [Gilbert 2002] Gilbert, S., and Lynch, N. "Brewer's Conjecture and the Feasibility of Consistent, Available, Partition-Tolerant Web Services." *ACM SIGACT News* 33(2), 2002, 51–59. `[UNVERIFIED]`
- [Gropp 2004] Gropp, W., and Lusk, E. "Fault Tolerance in Message Passing Interface Programs." *IJHPCA* 18(3), 2004, 363–372. doi:10.1177/1094342004046045
- [Hadzilacos 1993] Hadzilacos, V., and Toueg, S. "Fault-Tolerant Broadcasts and Related Problems." In *Distributed Systems* (2nd ed.), ACM Press/Addison-Wesley, 1993, 97–145.
- [Hargrove 2006] Hargrove, P. H., and Duell, J. C. "Berkeley Lab Checkpoint/Restart (BLCR) for Linux Clusters." *Journal of Physics: Conference Series* 46, 2006, 494–499. doi:10.1088/1742-6596/46/1/067
- [Hassani 2013] Hassani, A., Skjellum, A., and Brightwell, R. "A Transactional Model for Fault-Tolerant MPI for Petascale and Exascale Systems." Poster, *SC'13*.
- [Hassani 2015] Hassani, A., Skjellum, A., Bangalore, P. V., and Brightwell, R. "Practical Resilient Cases for FA-MPI, a Transactional Fault-Tolerant MPI." *ExaMPI / SC Workshops*, 2015. OSTI 1340216.
- [Hayashibara 2004] Hayashibara, N., Défago, X., Yared, R., and Katayama, T. "The φ Accrual Failure Detector." *23rd IEEE SRDS*, 2004, 66–78. Also JAIST Research Report IS-RR-2004-010.
- [Herault 2015] Herault, T., Bouteiller, A., Bosilca, G., Gamell, M., Teranishi, K., Parashar, M., and Dongarra, J. "Practical Scalable Consensus for Pseudo-Synchronous Distributed Systems." *SC'15*. doi:10.1145/2807591.2807665
- [Herault 2025] Herault, T. "Fault Tolerant Algorithms for a Fault Tolerant MPI." Cupseli Project Introductory Talks, Inria, December 2025. *(Slide claim that ~40% of ULFM is in MPI-5.0 contradicted by the MPI-5.0 text; see §3.5.)*
- [Huang 1984] Huang, K.-H., and Abraham, J. A. "Algorithm-Based Fault Tolerance for Matrix Operations." *IEEE Transactions on Computers* C-33(6), 1984, 518–528. doi:10.1109/TC.1984.1676475
- [Huber 2024] Huber, D., Streubel, M., Comprés, I., Schulz, M., Schreiber, M., and Pritchard, H. "Towards Dynamic Resource Management with MPI Sessions and PMIx / Design Principles of Dynamic Resource Management for High-Performance Parallel Programming Models." arXiv:2403.17107, 2024.
- [Hursey 2011a] Hursey, J., Graham, R. L., Bronevetsky, G., Buntinas, D., Pritchard, H., and Solt, D. G. "Run-Through Stabilization: An MPI Proposal for Process Fault Tolerance." *EuroMPI 2011*, LNCS 6960, Springer, 329–332. doi:10.1007/978-3-642-24449-0_40
- [Hursey 2011b] Hursey, J., Naughton, T., Vallee, G., and Graham, R. L. "A Log-Scaling Fault Tolerant Agreement Algorithm for a Fault Tolerant MPI." *EuroMPI 2011*, LNCS 6960, Springer, 255–263.
- [ICL 2004] Innovative Computing Laboratory. *Extending the MPI Specification for Process Fault Tolerance on High Performance Computing Systems.* ICL-UTK-202-2004, University of Tennessee, 2004.
- [Iserte 2021] Iserte, S., Mayo, R., Quintana-Ortí, E. S., and Peña, A. J. "DMRlib: Easy-Coding and Efficient Resource Management for Job Malleability." *IEEE Transactions on Computers* 70(9), 2021, 1443–1457. doi:10.1109/TC.2020.3022933
- [Iserte 2025] Iserte, S., et al. "Resource Optimization with MPI Process Malleability for Dynamic Workloads in HPC Clusters." arXiv:2506.14743, 2025.
- [Johnson 1987] Johnson, D. B., and Zwaenepoel, W. "Sender-Based Message Logging." *17th FTCS*, 1987, 14–19.
- [Kale 2014] Kale, L. V., Acun, B., Bhatele, A., et al. "Parallel Programming with Migratable Objects: Charm++ in Practice." *SC'14*. Charm++ technical report 14-07.
- [Laguna 2016] Laguna, I., Richards, D. F., Gamblin, T., Schulz, M., de Supinski, B. R., Mohror, K., and Pritchard, H. "Evaluating and Extending User-Level Fault Tolerance in MPI Applications." *IJHPCA* 30(3), 2016, 305–319. doi:10.1177/1094342015623623
- [Lamport 1978] Lamport, L. "Time, Clocks, and the Ordering of Events in a Distributed System." *CACM* 21(7), 1978, 558–565. doi:10.1145/359545.359563
- [Lamport 1998] Lamport, L. "The Part-Time Parliament." *ACM TOCS* 16(2), 1998, 133–169. doi:10.1145/279227.279229
- [Menon 2012] Menon, H., Jain, N., Zheng, G., and Kale, L. V. "Automated Load Balancing Invocation Based on Application Characteristics." *IEEE Cluster 2012*. Charm++ technical report 12-29.
- [Mohror 2014] Mohror, K., Moody, A., Bronevetsky, G., and de Supinski, B. R. "Detailed Modeling and Evaluation of a Scalable Multilevel Checkpointing System." *IEEE TPDS* 25(9), 2014, 2255–2263. doi:10.1109/TPDS.2013.100
- [Moody 2010] Moody, A., Bronevetsky, G., Mohror, K., and de Supinski, B. R. "Design, Modeling, and Evaluation of a Scalable Multi-level Checkpointing System." *SC'10*. doi:10.1109/SC.2010.18
- [MPIForum 2025] Message Passing Interface Forum. *MPI: A Message-Passing Interface Standard, Version 5.0.* 5 June 2025. https://www.mpi-forum.org/docs/mpi-5.0/mpi50-report.pdf
- [MPIForum2026] Message Passing Interface Forum. *March 2026 Meeting Notes.* https://www.mpi-forum.org/meetings/2026/03/notes
- [MPIIssue581] MPI Forum. *Issue #581: ULFM Fault Tolerance (slice 1: ack_failed, get_failed, revoke).* https://github.com/mpi-forum/mpi-issues/issues/581
- [MPIIssue582] MPI Forum. *Issue #582: ULFM Fault Tolerance (slice 2: agree).* https://github.com/mpi-forum/mpi-issues/issues/582
- [MPIIssue583] MPI Forum. *Issue #583: ULFM Fault Tolerance (slice 3: shrink).* https://github.com/mpi-forum/mpi-issues/issues/583
- [MPIIssue816] MPI Forum. *Issue #816: ULFM — Fault Model Is Not Completely Defined.* https://github.com/mpi-forum/mpi-issues/issues/816
- [Ongaro 2014] Ongaro, D., and Ousterhout, J. "In Search of an Understandable Consensus Algorithm." *USENIX ATC 2014*, 305–319.
- [OpenMPI 2025] Open MPI Development Team. *User-Level Fault Mitigation (ULFM)* and related man pages, Open MPI documentation. https://docs.open-mpi.org/en/main/features/ulfm.html
- [Saltzer 1984] Saltzer, J. H., Reed, D. P., and Clark, D. D. "End-to-End Arguments in System Design." *ACM TOCS* 2(4), 1984, 277–288. doi:10.1145/357401.357402 `[UNVERIFIED]`
- [Sao 2013] Sao, P., and Vuduc, R. "Self-Stabilizing Iterative Solvers." *ScalA '13 (SC Workshops)*. doi:10.1145/2530268.2530272
- [Schulz 2026] Schulz, M. "The State of MPI: Current Standard and Future Plans." FAU HPC PerfLab seminar, June 2026.
- [Strom 1985] Strom, R. E., and Yemini, S. "Optimistic Recovery in Distributed Systems." *ACM TOCS* 3(3), 1985, 204–226. doi:10.1145/3959.3962
- [Sultana 2018] Sultana, N., Skjellum, A., Laguna, I., Farmer, M. S., Mohror, K., and Emani, M. "MPI Stages: Checkpointing MPI State for Bulk Synchronous Applications." *EuroMPI 2018*. doi:10.1145/3236367.3236385
- [Sultana 2019] Sultana, N., Rüfenacht, M., Skjellum, A., Laguna, I., and Mohror, K. "Failure Recovery for Bulk Synchronous Applications with MPI Stages." *Parallel Computing* 84, 2019, 1–14. doi:10.1016/j.parco.2019.02.007
- [Teranishi 2014] Teranishi, K., and Heroux, M. A. "Toward Local Failure Local Recovery Resilience Model Using MPI-ULFM." *EuroMPI/ASIA '14*, 51–56. doi:10.1145/2642769.2642774
- [VeloC 2025] Nicolae, B., et al. *VeloC: Very Low Overhead Transparent Multilevel Checkpoint/Restart.* Argonne National Laboratory / ECP. https://github.com/ECP-VeloC/VELOC
- [Young 1974] Young, J. W. "A First Order Approximation to the Optimum Checkpoint Interval." *Communications of the ACM* 17(9), 1974, 530–531. doi:10.1145/361147.361115
- [Zaharia 2008] Zaharia, M., Konwinski, A., Joseph, A. D., Katz, R., and Stoica, I. "Improving MapReduce Performance in Heterogeneous Environments." *OSDI 2008*, 29–42.
- [Zheng 2010] Zheng, G., Bhatele, A., Meneses, E., and Kale, L. V. "Periodic Hierarchical Load Balancing for Large Supercomputers." *IJHPCA*, 2010. Charm++ technical report 10-08.
- [Zhou 2026] Zhou, H., Raffenetti, K., et al. "Implementing True MPI Sessions and Evaluating MPI Initialization Scalability." arXiv:2605.03983, 2026.
