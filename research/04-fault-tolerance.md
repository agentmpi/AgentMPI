# Fault Tolerance in Message-Passing Systems

*Research note for the AgentMPI paper. Part A covers HPC practice (MPI, ULFM, checkpoint/restart, ABFT). Part B covers the distributed-systems theory that makes the HPC practice legible. The closing section maps both onto multi-agent LLM harnesses.*

Claims marked `[UNVERIFIED]` are our own extrapolation or could not be confirmed against a primary source during this pass.

---

## Part A — Fault tolerance in MPI

### A.1 Why MPI-1 through MPI-3 is effectively fault-intolerant

MPI's error model was designed for a world in which a failed process meant a failed job. Three standard-level decisions, taken together, make MPI-1..MPI-3.1 unusable as a substrate for surviving process loss.

**Default handler is fatal.** `MPI_ERRORS_ARE_FATAL` is associated by default with `MPI_COMM_WORLD` after initialization, and "if the user chooses not to control error handling, every error that MPI handles is treated as fatal" [MPI-1.1 §7.2; MPI-3.1 §8.3]. The handler "causes the program to abort on all executing processes... the same effect as if `MPI_ABORT` was called" [MPI-3.1 §8.3]. So the out-of-the-box behaviour on a single-rank crash is a whole-job kill.

**The post-error state is undefined.** Setting `MPI_ERRORS_RETURN` does not buy you recovery, only notification. The standard is explicit: "After an error is detected, the state of MPI is undefined. That is, using a user-defined error handler, or `MPI_ERRORS_RETURN`, does *not* necessarily allow the user to continue to use MPI after an error is detected. The purpose of these error handlers is to allow a user to issue user-defined error messages and to take actions unrelated to MPI (such as flushing I/O buffers) before a program exits. An MPI implementation is free to allow MPI to continue after an error but is not required to do so" [MPI-1.1 §7.2; text unchanged through MPI-3.1 §8.3]. We verified this sentence is present in the MPI-3.1 report and absent from MPI-4.0 and MPI-5.0 (see below).

**Quality-of-implementation is advisory only.** The standard offers only *advice to implementors*: "A high-quality implementation will, to the greatest possible extent, circumscribe the impact of an error, so that normal processing can continue after an error handler was invoked" [MPI-3.1 §8.3]. Advice is not normative. Gropp and Lusk's early survey [Gropp & Lusk 2004] made the same point: nothing in the standard requires an implementation to be able to continue, so no portable program can assume it.

The practical consequence is that a fault-tolerant MPI program written against MPI-3.1 is *not portable*: it depends on undocumented per-implementation behaviour. Worse, even with `MPI_ERRORS_RETURN`, there is no mechanism to (a) learn *which* peers failed, (b) unblock a rank that is sitting in a `MPI_Recv` matched by a now-dead sender, or (c) rebuild a communicator whose group contains dead ranks so that collectives can run again. Collective operations are the sharp edge: a `MPI_Barrier` or `MPI_Allreduce` over a group with a dead member has no defined completion semantics.

MPI-4.0 (June 2021) softened the language without adding recovery. The "state of MPI is undefined" sentence was replaced by: "Some errors might prevent MPI from completing further API calls successfully and those functions will continue to report errors until the cause of the error is corrected or the user terminates the application. The user can make the determination of whether or not to attempt to continue when handling such an error" [MPI-5.0 §10.3]. MPI-4.0 also clarified that `MPI_ERRORS_ARE_FATAL` aborts *all connected* processes, and added `MPI_ERRORS_ABORT`, which narrows abort scope to the communicator (or, when invoked on a *session*, to the local process only) [MPI-4.1 Annex "Changes in MPI-4.0"; MPI-5.0 §10.3]. That is a real improvement in blast radius control, and it is the closest thing to fault tolerance in the ratified standard — but it is still *abort*, not *recover*.

### A.2 ULFM — User Level Failure Mitigation

ULFM is the MPI Forum Fault Tolerance Working Group's proposal, developed principally at ICL/UTK by Bland, Bosilca, Bouteiller, Herault, Hursey, and Dongarra [Bland et al. 2012; Bland et al. 2013]. It is the central prior art for AgentMPI.

#### A.2.1 Design philosophy

The governing principle is stated plainly by the authors: **MPI should not attempt to define the failure recovery model or to repair applications.** "It should inform applications of specific conditions that prevent the successful delivery of messages, and provide constructs and definitions that permit applications to restore MPI objects and communication functionalities" [Bland et al. 2013, §3]. ICL's own project summary puts it as: "The MPI implementation is spared the expense of internally taking protective and corrective automatic actions against failures. Instead, it can prevent any fault-related deadlock situation by reporting operations wherein failures rendered the completions impossible" [ICL ULFM project page].

Three concrete design goals were adopted [Bland et al. 2012, §3; Bland et al. 2013]:

1. **Simplicity** — the API must be usable in common scenarios.
2. **Flexibility** — varied fault-tolerance models (transactions, uncoordinated C/R, ABFT) must be constructible *as libraries on top*, not baked in.
3. **Absence of deadlock** — "no MPI call (point-to-point or collective) can block indefinitely after a failure, but must either succeed or raise an MPI error."

Goal 3 is the load-bearing one, and it is much weaker than it looks. It is a *local liveness* guarantee, not a global consistency guarantee. Two pitfalls were explicitly avoided: (i) jitter-prone permanent health monitoring of peers you are not currently talking to, and (ii) expensive consensus on every operation so that errors are reported uniformly at all ranks. The result is the operative principle that **errors are not indicative of remote return status**: `MPI_ERR_PROC_FAILED` is raised only at the particular rank whose particular operation could not complete [Bland et al. 2012, §3].

#### A.2.2 The uniformity problem — why `revoke` and `agree` must exist

This is the crux, and it is the part most directly transferable to agent systems.

Because failure detection is *not* global to a communicator, and because ULFM deliberately refuses to pay for consensus on the failure-free path, **failure knowledge is non-uniform**: some ranks raise an error for an operation while others do not. The consequence is a *divergent control flow* hazard [Bland et al. 2012, §3]:

> "This inconsistency in error reporting may result in some processes continuing their normal, failure-free execution path, while others have diverged to the recovery execution path. As an example, if a process, unaware of the failure, posts a reception from another process that has switched to the recovery path, the matching send will never be posted. Yet no failed process participates in the operation and it should not raise an error. The receive operation is effectively deadlocked."

Note what has happened: the *no-indefinite-block* guarantee has been satisfied at every individual call site, and the application still deadlocks — because the deadlock is not caused by a failed peer, it is caused by a *live* peer that took a different branch. This is precisely the failure mode that naive retry-on-error cannot fix, and it is the reason ULFM needs two additional primitives beyond error reporting.

`MPI_Comm_revoke` solves it by giving one rank a unilateral, non-collective way to force *every* other rank on that communicator onto the recovery path. `MPI_Comm_agree` solves the dual problem: when the application genuinely needs a uniform decision (did phase *k* complete everywhere? is it safe to commit this output?), agree provides fault-tolerant consensus on a boolean.

#### A.2.3 The interfaces

**Error classes** (three; `MPIX_`-prefixed in shipping implementations because ULFM is not ratified):

| Class | Raised when |
|---|---|
| `MPI_ERR_PROC_FAILED` | a process failure prevents completion of an MPI operation |
| `MPI_ERR_PROC_FAILED_PENDING` | a potential sender matching a non-blocking wildcard-source (`MPI_ANY_SOURCE`) receive has failed; the request itself remains pending and may still complete later |
| `MPI_ERR_REVOKED` | the communicator has been revoked |

The existence of a *separate* `..._PENDING` class is a subtle but important design point: a wildcard receive is not necessarily doomed by one dead potential sender, so ULFM reports "one of your possible partners died, your request is still alive" rather than killing the request. Open MPI's changelog records this as a specification revision in ULFM 1.1, with bug fixes as late as ULFM 2.1 to get any-source completion semantics right [Open MPI ULFM docs, changelog].

**`MPI_Comm_revoke(comm)`** — marks `comm` revoked at *all* processes in its local and remote groups. It is **not collective**: a single rank can call it alone, with no matching call anywhere, and it affects remote ranks. Afterwards, non-local operations on `comm` raise `MPI_ERR_REVOKED`, except for a small set of fault-tolerant operations (`shrink`, `agree`, `is_revoked`). Revocation propagation is itself fault-tolerant — the communicator becomes revoked at every non-failed member *despite* further failures during propagation. There is no ordering guarantee: a receive can raise `MPI_ERR_REVOKED` even if the matching send was posted before the revoke call [Open MPI `MPIX_Comm_revoke(3)`].

Implementation-wise, revoke is a **reliable broadcast** in the sense of Hadzilacos & Toueg [1993], with two of the four properties relaxed: termination and integrity can be weakened, because if a failure kills the initiator *and* every already-notified process, the notification is lost — but that outcome is indistinguishable to the application from the initiator having failed before it started, and *agreement* still holds, so no inconsistent view is observable [Bland et al. 2012, §4.2]. The 2012 prototype used naive flooding; ULFM 1.0 replaced it with a reliable broadcast of fixed maximum output degree, which scales logarithmically in the number of ranks [Open MPI ULFM changelog].

**`MPI_Comm_shrink(comm, &newcomm)`** (and non-blocking `MPI_Comm_ishrink`) — creates a new communicator containing exactly the non-failed processes of `comm`, with ranks renumbered to close the gaps. It is **collective**, and it remains collective even on a revoked communicator. Critically, it **cannot fail due to process failure**: `MPI_ERR_PROC_FAILED` and `MPI_ERR_REVOKED` are never raised by shrink; new failures discovered during the operation are absorbed into the consensus and simply excluded from the result [Bland et al. 2012, §3; Open MPI `MPIX_Comm_shrink(3)`].

Why must shrink be a *collective consensus* rather than a local filter? Because every survivor must end up with the *same group*. If rank 0 believed {1,2,5} were dead and rank 3 believed only {1,2} were dead, the two would construct communicators of different size with different rank mappings, and every subsequent collective would be malformed. Shrink is therefore, algorithmically, *an agreement whose decision value is the group of failed processes* — the same complexity class as `agree`, and in the ULFM prototype literally the same internal agreement code [Bland et al. 2012, §4.2]. MPICH's implementation adds a retry loop: unlike agree, an undetected failure during shrink does not abort the operation; the failure-discovery phase is re-run until all processes form a consistent group [Bland et al. 2015]. The standard-draft guarantee is a *lower bound on exclusion*: `newcomm` must exclude every process whose failure caused `MPI_ERR_PROC_FAILED`/`..._PENDING` at some member before that member initiated the shrink; implementations strive to detect all failures, but `newcomm` may still contain a process that fails later [Open MPI `MPIX_Comm_shrink(3)`].

**`MPI_Comm_agree(comm, &flag)`** / **`MPI_Comm_iagree`** — a fault-tolerant `MPI_Allreduce` over a boolean with bitwise AND. It completes even when failures have occurred *and* even when the communicator is revoked; it absorbs new failures and propagates failure knowledge among participants [Open MPI ULFM docs]. Uses: uniform completion of an algorithmic phase, uniform completion of a collective, uniform "is it safe to commit this output" decisions, and as the building block for stronger models such as transactions [Bland et al. 2012, §3]. The prototype algorithm is a variation of multi-level two-phase commit [Mohan & Lindsay 1985]: reduce input values to an elected coordinator, coordinator decides, coordinator broadcasts back; the complexity is in adapting when the coordinator or participants die mid-protocol [Hursey et al. 2011]. Failure-free complexity is O(log n), matching `MPI_Allreduce` over the live ranks.

**Failure introspection.** The original pair was `MPI_Comm_failure_ack(comm)` + `MPI_Comm_failure_get_acked(comm, &group)`: `failure_ack` marks a point in time; `failure_get_acked` returns the group of locally-known failures as of that point. After acknowledging, the application may resume `MPI_ANY_SOURCE` point-to-point among survivors, though operations involving failed processes (notably collectives) will keep raising errors [Bland et al. 2012, §3].

This pair was replaced in the MPI-4/5-era draft by **`MPI_Comm_get_failed(comm, &failedgrp)`** and **`MPI_Comm_ack_failed(comm, num_to_ack, &num_acked)`** [Bosilca/Bouteiller, fault-tolerance.org 2019; mpi-forum/mpi-issues#20]. The rationale is worth recording because it is a good API lesson: the old `failure_ack` *mutated state* (acknowledging everything detected so far) and gave you no way to know in advance what you had just acknowledged, forcing you to build a whole `MPI_Group` object merely to acknowledge. The new split is side-effect-free inspection (`get_failed`) plus bounded, counted acknowledgement (`ack_failed`, "acknowledge at most *n*, tell me how many"), so a rank can inspect the failure set, decide the condition is correctable, and re-enable wildcard receives for exactly that many failures without accidentally absorbing failures detected in the meantime by another thread or module. `ack_failed` also governs whether currently-known failures affect `agree`'s return value. Open MPI exposes both generations (`MPIX_Comm_failure_ack`/`_get_acked` and `MPIX_Comm_get_failed`/`_ack_failed`) and they may be intermixed; Open MPI additionally provides `MPIX_Comm_is_revoked`, added in ULFM 4.0.1u1.

#### A.2.4 The canonical recovery loop

The following is the standard ULFM recovery idiom: catch `MPI_ERR_PROC_FAILED`, revoke to unify control flow, shrink to rebuild membership, agree to confirm a uniform decision, restore state, continue. Note the ordering constraints in the comments — most ULFM bugs in the wild come from getting them wrong.

```
# ULFM-style recovery loop for an iterative bulk-synchronous computation.
# Names follow the MPI-4/5 draft; shipping implementations prefix MPIX_.
# Sources: Bland et al. 2012 §3-4; Bland et al. 2013; Open MPI ULFM man pages.

MPI_Comm_set_errhandler(comm, MPI_ERRORS_RETURN)   # MUST: the default
                                                   # MPI_ERRORS_ARE_FATAL aborts
                                                   # all connected processes.
state <- load_checkpoint_or_initial()
iter  <- state.iteration

while iter < N_ITERS:

    rc <- run_one_iteration(comm, state)           # p2p + collectives

    # ---- uniform decision: did EVERY survivor finish this iteration? ----
    # agree, not allreduce: agree completes even with failed members and even
    # on a revoked communicator. A plain MPI_Allreduce here would itself fail.
    local_ok <- (rc == MPI_SUCCESS)
    flag     <- local_ok
    MPI_Comm_agree(comm, &flag)                    # bitwise AND over the group

    if flag:
        checkpoint_if_due(state)                   # interval from Daly, below
        iter <- iter + 1
        continue

    # ================= RECOVERY PATH =================
    # We get here when SOMEONE failed -- possibly not us. Our own rc may be
    # MPI_SUCCESS: agree told us a peer is in trouble. This is the whole point
    # of agree, and the reason a bare "if rc != SUCCESS" loop is not enough.

    # 1. REVOKE -- unify control flow before anything else.
    #    Not collective: one rank calling it suffices, and it affects remote
    #    ranks with no matching call. Solves the divergent-branch deadlock:
    #    peers still blocked in a receive matched by a rank that has already
    #    branched to recovery get MPI_ERR_REVOKED instead of hanging forever.
    #    Idempotent, so every rank on the recovery path may safely call it.
    MPI_Comm_revoke(comm)

    # 2. Drain. Every pending op on comm now fails with MPI_ERR_REVOKED.
    #    Cancel/complete outstanding requests and free derived objects
    #    (sub-communicators, windows, files) BEFORE shrinking. ULFM does not
    #    repair objects derived from comm; RMA/FILE/TOPO are not FT at all.
    cancel_all_pending_requests()
    free_derived_communicators()

    # 3. SHRINK -- collective consensus on the surviving group.
    #    Never raises MPI_ERR_PROC_FAILED or MPI_ERR_REVOKED; failures
    #    discovered during the operation are absorbed into the consensus and
    #    excluded from the result. Ranks are renumbered to close the gaps.
    #    Collective *even though comm is revoked* -- all survivors must call it.
    MPI_Comm_shrink(comm, &newcomm)

    # 4. Optionally restore full width. Two policies:
    #      shrinking     : proceed with fewer ranks, redistribute work
    #      non-shrinking : spawn/adopt replacements to keep rank count fixed
    #                      (Fenix/LFLR style, typically from hot spares)
    if POLICY == NON_SHRINKING:
        newcomm <- respawn_and_merge(newcomm, target_size = ORIGINAL_SIZE)

    MPI_Comm_free(&comm)
    comm <- newcomm
    MPI_Comm_set_errhandler(comm, MPI_ERRORS_RETURN)   # new comm, new handler

    # 5. Re-enable wildcard receives if the algorithm uses MPI_ANY_SOURCE.
    #    Inspect first (side-effect free), then acknowledge a bounded count --
    #    this is why ack/get_acked was replaced by get_failed/ack_failed.
    MPI_Comm_get_failed(comm, &failed_grp)
    MPI_Group_size(failed_grp, &n_failed)
    MPI_Comm_ack_failed(comm, n_failed, &n_acked)

    # 6. Restore application state. ULFM restores the SUBSTRATE ONLY -- it
    #    deliberately does not recover data for you. Choose per algorithm:
    #      backward : reload the last checkpoint (SCR / VeloC / Fenix store)
    #      forward  : reconstruct from ABFT checksums, or from neighbours
    if RECOVERY == FORWARD:
        state <- abft_reconstruct(comm, state)     # no rollback: keep iter
    else:
        state <- load_checkpoint(comm)             # rollback
        iter  <- state.iteration

    # 7. Agree that everyone resumes from the same point. Note that agree is
    #    defined only over a BOOLEAN with bitwise AND -- there is no
    #    "agree on an integer". So the idiom is to agree on a predicate and
    #    fall back to shrink-and-retry if it is false. (A plain MPI_Allreduce
    #    MAX over the new, now-clean comm also works, and is what most codes
    #    do, at the cost of being non-FT if a rank dies during recovery.)
    flag <- (state.iteration == expected_checkpoint_id)
    MPI_Comm_agree(comm, &flag)
    if not flag:
        continue                                   # re-enter recovery path
```

#### A.2.5 Cost

ULFM's central empirical claim is that fault-awareness is nearly free when nothing fails. NetPIPE 1-byte latency on ORNL Smoky, vanilla vs. ULFM-enabled Open MPI: shared memory 0.8008 → 0.8016 µs, TCP 10.2564 → 10.2776 µs, OpenIB 4.9637 → 4.9650 µs — all within the standard deviation [Bland et al. 2012, Table 1]. IMB point-to-point and collective differences on a 48-core shared-memory node stayed below 5%, i.e. within run-to-run noise. Sequoia-AMG weak-scaled to 512 ranks showed negligible difference.

Recovery costs from a fault-injection benchmark on the same machine: detection propagated to all ranks within ~30 ms (against a 1 s link-level timeout), `revoke` itself returned in under 50 µs, and `shrink` grew roughly linearly in rank count — dominated not by the agreement (which scales logarithmically) but by the underlying communicator-construction path (`MPI_Comm_split` and context-ID allocation) [Bland et al. 2012, §5]. That is a useful design signal: **the consensus is cheap; rebuilding the communication context is what costs.**

Open MPI's shipping defaults are conservative: PRTE failure detector heartbeat period 5 s, timeout 10 s, tuned "for failure-free performance at the expense of fault detection reactivity," with the docs suggesting values as low as 100 ms where faults are common, and warning that values below the TCP poll rate (~10 ms) cause false positives [Open MPI ULFM docs, §5.3.7.4]. ULFM's coverage is also incomplete: TOPO, FILE, and RMA are documented as *not* fault tolerant, and several PML/MTL/collective components are disabled or untested under FT.

#### A.2.6 Status in MPI-4.x and MPI-5 — verified

**ULFM is not in the ratified standard as of MPI-5.0.** MPI-5.0 was released 5 June 2025, and its headline feature is a standard ABI, not fault tolerance [MPI Forum]. We checked the MPI-5.0 report directly: it contains **no** occurrence of `MPI_ERR_PROC_FAILED`, `MPI_COMM_SHRINK`, or `MPI_COMM_AGREE`. The only hits for "Fault Tolerance" are in the acknowledgements, listing the working-group chairs (Bland, Bouteiller, Graham for one phase; Bouteiller and Laguna for later ones).

The Forum is instead adopting ULFM **in slices**: slice 1 = chapter structure, error codes, post-error semantics, `MPI_COMM_REVOKE`, `MPI_COMM_GET_FAILED`, `MPI_COMM_ACK_FAILED` (issue #581); slice 2 = `agree` (#582); slice 3 = `shrink` (#877/#583). Files and RMA are explicitly deferred, possibly to "MPI 7 or 8" [MPI Forum FT virtual meeting, 2025-11-05]. As of the December 2025 Forum meeting, the *fault model* text (issue #816, PR #947) was still at the no-no-vote stage, with slices 2 and 3 scheduled for reading and the order swapped so that shrink precedes agree; terminology for "non-failed collective" was still under debate [MPI Forum December 2025 meeting notes; mpi-forum mailing list]. One December 2025 conference slide claims "about 40% of ULFM is now part of the official MPI standard (MPI 5.0)" [Inria Cupseli talk]; **that claim is inconsistent with the MPI-5.0 document itself** and we treat it as an error or as a forward-looking statement about slices in flight.

Implementation status is much better than standardization status: ULFM is integrated in Open MPI's main branch and built by default (`--with-ft ulfm` at configure, `mpirun --with-ft ulfm` at runtime, inactive otherwise), and is available in MPICH [Open MPI ULFM docs; Bland et al. 2015]. Downstream adopters cited by ICL include Coarray Fortran and SAP HANA. So the practical situation is: **stable, shipped, `MPIX_`-prefixed, portable across the two major implementations, but not yet normative.**

### A.3 Competing and complementary proposals

**FT-MPI** [Fagg & Dongarra 2000] is the ancestor. It offered recovery *modes*: in BLANK mode failed ranks became `MPI_PROC_NULL` and messages to/from them were silently ignored (requiring significantly modified collective algorithms); in REPLACE mode failed processes were replaced by new ones. Only `MPI_COMM_WORLD` was repaired; the application had to rebuild every derived communicator, which made library composition hard. It was never standardized [Bland et al. 2012, §2].

**Run-Through Stabilization (RTS)** [Hursey et al. 2011] introduced the ability to "validate" a communicator — mark failures as recognized and keep using the *same* communicator — plus uniform failure handlers. It failed in the Forum because resuming operations on a communicator that had contained failures was too complex to implement [Bland et al. 2012, §2]. ULFM's refusal to repair communicators in place (you always get a *new* one from `shrink`) is the direct lesson learned from RTS.

**FA-MPI** [Hassani, Skjellum & Brightwell 2014] takes a *transactional* approach: `MPI_TryBlock_start` / `MPI_TryBlock_finish` delimit nestable "TryBlocks" containing non-blocking communication, computation, and I/O. `TryBlock_finish` is a synchronizing collective that performs a fault-tolerant allreduce/allgather of failures over the block's communicator group. The block commits if all operations succeed; otherwise the application may soft-retry, roll back, roll forward, or restart from a checkpoint. FA-MPI is **per-transaction** fault-awareness as opposed to ULFM's **per-operation** scheme, its granularity is a tuning knob for fault-free overhead, and it is not restricted to process failures. Its limitation is that it requires non-blocking operations, which is a portability barrier for legacy code.

**Reinit** [Laguna et al. 2016] implements a **global-restart** model: on failure, the job rolls back to a known point without re-queueing, with non-shrinking recovery, built directly into the MPI runtime; it changes program structure by replacing `main` with cleanup handlers, and offers no data-recovery facilities.

**Fenix** [Gamell et al. 2016] is a library layered *on* ULFM providing `Fenix_Init` / `Fenix_Finalize` plus `Fenix_Data_member_store` / `Fenix_Data_member_restore`. It transparently repairs communicators, maintains spare ranks, supports **both** shrinking and non-shrinking recovery in one framework, and — importantly — **decouples process recovery from data recovery**, so data can come from Fenix's redundant store, from GVR, from SCR, or from interpolation off topological neighbours. Fenix-enabled codes retain their original structure. Later work extended Fenix to "pseudo-local" recovery, demonstrating ~1000 ranks with ~100 process failures and much better weak scaling than global recovery [Gamell et al. 2024].

**MPI Stages** [Sultana et al. 2018] observes that even ULFM-style recovery makes *live* processes redo work from program start if the app is restarted. MPI Stages checkpoints **MPI's own internal state** alongside application state; on failure both are restored from their last synchronous checkpoint, live processes roll back only a few iterations of the main loop, and a replacement process restarts and reintegrates — no full job reinitialization and no communicator shrink.

**LFLR (Local Failure, Local Recovery)** [Teranishi & Heroux 2014] is the strongest argument against global response. Its motivating statistic: single-node failures are predominant — ~85% of failures on LLNL clusters [Moody et al. 2010] and 60–90% on Jaguar/Titan — so killing all ranks in response is "a disproportionate response." LFLR keeps survivors alive, uses hot spare processes to hold rank count constant (avoiding load-rebalancing complexity), and uses scalable in-memory redundant storage rather than the global filesystem.

**Checkpoint/restart libraries.** *BLCR* is a kernel-module system-level checkpointer; *DMTCP* is userspace (no kernel modules, no root), MPI-agnostic, and empirically faster and more scalable than BLCR for checkpoint/restart time and process scaling [Alsuwaiyan et al. 2023]. *SCR* (LLNL, production since 2007) introduced **multilevel checkpointing**: frequent cheap checkpoints to node-local RAM disk / SSD, infrequent resilient checkpoints to the parallel filesystem, which relieves the PFS bandwidth bottleneck that makes single-level C/R untenable at scale [LLNL SCR]. *VeloC* (ANL + LLNL, ECP) refactored FTI and SCR into one multilevel framework with a simple API and asynchronous flushing.

**Charm++ / AMPI.** FTC-Charm++ provides in-memory (double) checkpointing with disk extension, supports uncoordinated, coordinated, and communication-induced checkpointing, and offers **causal message logging**. Because AMPI ranks are user-level threads, Charm++ can hold rank count constant while redistributing threads over the surviving hardware — recovery as *migration* rather than as group repair.

**ABFT (Algorithm-Based Fault Tolerance).** Huang & Abraham [1984] proposed encoding data at the matrix level (row, column, and full **checksum matrices**) and redesigning algorithms to operate on encoded data and produce encoded output, so that a fault within a single processor of a multiprocessor array can be *detected and corrected*. The recovery relation is arithmetic, not historical: you invert the checksum to recreate missing data. Bosilca, Delmas, Dongarra & Langou [2009] lifted this to distributed memory — the checksum update becomes a *local* operation on an extra process rather than a global reduction, so the failure-free penalty is one additional process rather than a communication phase; their fault-tolerant DGEMM hit 1.4 TFLOP/s on 484 processors while returning a correct result after a process failure. Du, Bouteiller, Bosilca, Herault & Dongarra [2012; extended 2015] handled dense LU/QR/Cholesky under fail-stop failures with no reliable component, protecting the *right* factor with ABFT checksums and the *left* factor with a scalable checkpointing algorithm that reuses the checksum storage. ABFT's known hard limit is **error propagation**: in LU, a single transient fault propagates into an overwhelming number of erroneous results, defeating naive checksum correction; Luk & Park's backward-error framing recovers correctability by casting the error as a rank-one perturbation of the input [Chen 2013, §II].

**Rollback vs. forward recovery.** This is the axis that matters most for AgentMPI.

- *Rollback (backward) recovery* restores a previously saved error-free state and re-executes. It requires no knowledge of the nature of the error, handles arbitrary and unpredictable faults, is application-independent, and is ideal for transient faults. Its cost is the checkpoint, the re-execution, and — crucially — that it is *impossible* for interactions with the outside world that cannot be un-done.
- *Forward recovery* constructs a *new* error-free state from which execution continues, without going back. It requires detecting the error, assessing the damage in detail, and having an application-specific way to reconstruct or compensate. ABFT is the canonical forward-recovery mechanism (reconstruct lost data from checksums); so is "run through with fewer ranks and redistribute work," and so is Erlang's supervisor restarting a stateless worker.

ULFM is *neutral* on this axis by design: `revoke`/`shrink`/`agree` restore the *communication substrate*, and the application chooses whether to then roll back (Fenix + checkpoint) or roll forward (ABFT reconstruction, master–worker re-dispatch).

### A.4 Checkpoint theory

#### A.4.1 Optimal checkpoint interval

Young [1974] derived the first-order approximation

$$\tau_{opt} = \sqrt{2\delta M}$$

where δ is the time to write a checkpoint and M is the mean time to interrupt (MTTI/MTBF). The intuition is the standard two-term waste trade-off: overhead from checkpointing is ≈ δ/τ, expected lost work per failure is ≈ τ/2M, and the sum C/W + W/2μ is minimized at W = √(2μC) [Benoit et al. 2022].

Daly [2003] extended the first-order model to include restart time R, obtaining τ_opt = √(2δ(M+R)) — Young's result with R added under the radical.

Daly [2006] then relaxed the first-order assumptions (in particular allowing failures *during* restart) and derived an **exact** solution in terms of the Lambert W function. Non-dimensionalizing with ξ = √(δ/2M) and η = (τ+δ)/M, the optimality condition e^{−η} + η = 2ξ² + 1 solves to η = 2ξ² + 1 + W(−e^{−2ξ²−1}), where W(z)e^{W(z)} = z. Because that is not an elementary function, Daly gives a three-term perturbation solution guaranteed to keep the relative error in total problem-solution time below **0.2%**:

$$\tilde\tau_{opt} = \begin{cases}\sqrt{2\delta M}\left[1 + \frac{1}{3}\left(\frac{\delta}{2M}\right)^{1/2} + \frac{1}{9}\left(\frac{\delta}{2M}\right)\right] - \delta & \delta < 2M\\ M & \delta \ge 2M\end{cases}$$

Two findings from Daly's higher-order analysis are worth carrying into agent systems. First, **R drops out**: although the first-order model predicts that restart time influences the optimal interval, the higher-order model shows it does not. Second, the *lowest*-order truncation, √(2δM) − δ for δ < M/2 (and M otherwise), is equivalent to Young's model and never exceeds ~5% relative error in solution time even in the worst case δ = M/2 — so Young's rule of thumb is good enough for engineering.

```
# Daly's optimal checkpoint interval (higher-order, 3-term perturbation)
# Daly 2006, Eq. (37). Verified against the paper's Eq. 37 and Eq. 38.
#
#   delta : cost of writing one checkpoint      (same units as M)
#   M     : mean time to interrupt (MTBF/MTTI)  (same units as delta)
#   R     : restart cost -- accepted but UNUSED; the higher-order model
#           proves R does not contribute to tau_opt (Daly 2006, §6).

function DALY_OPTIMAL_INTERVAL(delta, M, R = 0):
    assert delta > 0 and M > 0

    if delta >= 2 * M:
        # Checkpointing is so expensive relative to the failure rate that the
        # best you can do is checkpoint about once per expected failure.
        return M

    x = delta / (2 * M)                        # xi^2, the small parameter
    tau = sqrt(2 * delta * M) * (1 + (1/3) * sqrt(x) + (1/9) * x) - delta

    return max(tau, 0)

# First-order fallbacks, for reference:
#   Young 1974 :  sqrt(2 * delta * M)
#   Daly 2003  :  sqrt(2 * delta * (M + R))
#   Young-equiv:  sqrt(2 * delta * M) - delta   for delta < M/2, else M
#                 (<= ~5% relative error in total solution time)
```

#### A.4.2 Coordinated vs. uncoordinated checkpointing, and the domino effect

Elnozahy, Alvisi, Wang & Johnson's survey [2002] is the canonical taxonomy. Checkpoint-based protocols split into **coordinated** (processes synchronize so that the saved set forms a consistent global state — simple recovery, single checkpoint to retain, but a global synchronization and a PFS bandwidth spike), **uncoordinated** (each process checkpoints independently — cheap and asynchronous, but the recovery line must be *computed* after the fact), and **communication-induced** (piggybacked information forces checkpoints to keep the recovery line advancing).

The **domino effect** is uncoordinated checkpointing's failure mode: because process states are causally entangled by messages, rolling one process back to its last checkpoint can invalidate a *received* message at a peer, forcing that peer to roll back, which invalidates a message at a third process, and so on — potentially cascading all the way to the start of the computation. Uncoordinated checkpointing therefore needs domino-freedom machinery (index-based or model-based communication-induced checkpointing, or message logging) to bound rollback [Elnozahy et al. 2002; Venkatesh et al. 1987].

#### A.4.3 Message logging

Log-based rollback recovery combines checkpointing with logging of nondeterministic events, and rests on the **piecewise deterministic assumption** [Strom & Yemini 1985]: all nondeterministic events a process executes can be identified, and enough information to replay each can be captured in the event's **determinant** [Alvisi & Marzullo 1998]. Replaying determinants in original order lets a process deterministically recreate a pre-failure state that was never checkpointed — so recovery can go *beyond* the last consistent checkpoint set. This matters most for processes that interact with the outside world, which cannot roll back. The three flavours differ in when determinants become stable:

- **Pessimistic** — the application blocks until each determinant is on stable storage before its effects become visible to anyone else. No **orphans** (survivors whose state depends on a message the recovered process will not re-send) can ever exist. Simple recovery, hurts failure-free performance.
- **Optimistic** — determinants are spooled asynchronously, so the application never blocks. Orphans can exist; on failure the protocol must detect and roll them back. Low failure-free overhead, complex recovery.
- **Causal** — disseminates determinants along causal dependency edges, so anyone who could become an orphan already holds the determinants needed to avoid it. Achieves orphan-freedom without synchronous blocking [Alvisi & Marzullo 1998; Elnozahy & Zwaenepoel 1992 (Manetho)].

#### A.4.4 Chandy–Lamport distributed snapshots

Chandy & Lamport [1985] gives the mechanism for taking a consistent global snapshot of a running system without stopping it. Assumptions: unidirectional FIFO channels, reliable delivery, connectivity, no failures during the snapshot. Any process may initiate: it records its local state and sends a **marker** on every outgoing channel. On receiving a marker for the first time, a process records its local state, marks the channel the marker arrived on as *empty*, and forwards markers on all its outgoing channels; on receiving a marker on a channel it has already begun recording, it records that channel's state as exactly the messages received on it since it started recording. Each channel carries exactly one marker, so each process knows locally when it is done.

The property delivered is a **consistent cut**: for every event *e* in the cut and every *f* with *f* → *e* (happens-before), *f* is also in the cut. Equivalently, every message *received* in the past of the cut was *sent* in the past of the cut. That is exactly the invariant coordinated checkpointing needs, and the marker trick — a control message that is *ordered with respect to* application messages on the same channel — is the same structural device ULFM's revoke uses, minus the reliability requirements.

---

## Part B — Distributed-systems theory

### B.1 Failure models

The standard component-level taxonomy [Cachin, Guerraoui & Rodrigues 2011; van Steen & Tanenbaum]:

- **Crash-stop** (fail-stop process behaviour): a process executes correctly, then halts permanently and produces nothing further. Most HPC work, including ULFM, assumes this: ULFM "supports the permanent crash failure mode, where a process acts correctly until it stops... and no subsequent results are delivered. Transient or byzantine errors are outside the scope" [Bland et al. 2013, §3].
- **Crash-recovery**: a process may crash and later restart, possibly many times; requires persistent state and "stubborn" links, and raises the question of whether a process that crashes and recovers infinitely often is *correct*.
- **Omission**: a process (or channel) fails to send or receive some messages it should, while otherwise behaving correctly. Message loss and dropped requests live here.
- **Timing / performance**: a response is produced, but outside the required real-time interval — too slow (or too fast). This is the model for stragglers.
- **Byzantine / arbitrary**: the component may behave arbitrarily, including producing wrong values (*value failure*) or entering a wrong state (*state transition failure*), and multiple such components may collude. Most expensive to tolerate; typically needs cryptographic primitives and 3f+1 replicas.

Layered on top are *system* models that pair a process-failure model with a link model and a failure detector [Cachin et al. 2011]:

| System model | Process behaviour | Detection | Synchrony |
|---|---|---|---|
| **fail-stop** | crash-stop | perfect FD (*P*) | synchronous |
| **fail-silent** | crash-stop | none | asynchronous; needs majority correct |
| **fail-noisy** | crash-stop | eventually perfect FD (◇*P*) | partially synchronous |
| **fail-recovery** | crash-recovery | eventual leader (Ω) | + stubborn/logged links |
| **fail-arbitrary** | Byzantine | — | + authenticated links |

Two naming notes, since the terms are used loosely in the wild. *Fail-silent* in the Cachin et al. sense means crash-stop **without** a failure detector — you cannot tell a dead process from a slow one, so algorithms fall back on majority quorums. *Fail-noisy* means crash-stop **with** an unreliable (eventually perfect) detector, which is the realistic model for a datacenter or an HPC interconnect: the detector will produce false positives, and safety must not depend on its accuracy, only liveness may.

**The distinction that matters for us.** An LLM agent's failure mode is not crash-stop. It can be *slow* (timing failure), *crashed or hung* (crash-stop, or omission if it silently drops a task), or **wrong-but-confident** — a value failure that is byzantine-*ish* without being adversarial. The last case is the one HPC's dominant assumption explicitly excludes, and it is why an agent harness cannot simply lift the ULFM failure model unchanged. A confidently wrong agent still sends well-formed messages on schedule; no failure detector built from heartbeats and timeouts will ever suspect it. Detecting it requires *content*-level checking, i.e. the ABFT side of the design space, not the failure-detector side.

### B.2 FLP impossibility and failure detectors

**FLP** [Fischer, Lynch & Paterson 1985]: in a completely asynchronous message-passing system, **no deterministic protocol solves consensus** (agreement + validity + termination) in the presence of even a single unannounced process death. The theorem is deliberately posed under the *weakest* fault assumption: no Byzantine failures, and a reliable message system that "delivers all messages correctly and exactly once." "Nevertheless, even with these assumptions, the stopping of a single process at an inopportune time can cause any distributed commit protocol to fail to reach agreement."

The mechanism of the proof is a bivalence/indistinguishability argument, but the practically important sentence is the assumption list: processing is completely asynchronous, no assumptions on relative process speeds or message delay, and **no synchronized clocks, so timeout-based algorithms cannot be used**. FLP's real content is therefore: *you cannot distinguish a crashed process from a slow one, and any algorithm that must decide is forced to guess.*

What FLP does **not** say: it does not say consensus is unreachable in practice, nor that it is usually slow. It says no algorithm can *guarantee* termination in *all* executions. Every practical protocol buys termination by weakening one of the three properties or by strengthening the model — randomization (Ben-Or), partial synchrony, or a failure-detector oracle.

**Failure detectors** [Chandra & Toueg 1996] are the cleanest way to encapsulate exactly how much synchrony you need. A failure-detector module at each process outputs a set of suspected processes; the output may be wrong. Detectors are classified by two properties:

- **Completeness** — *strong*: every crashed process is eventually permanently suspected by every correct process; *weak*: by some correct process.
- **Accuracy** — *strong*: no correct process is ever suspected; *weak*: some correct process is never suspected; *eventually strong* / *eventually weak*: the corresponding property holds only after some finite time.

Two completeness levels × four accuracy levels = eight classes, but weak completeness is reducible to strong completeness (given a way to disseminate suspicions), collapsing the hierarchy to four practically distinct classes:

| Class | Completeness | Accuracy |
|---|---|---|
| **P** (perfect) | strong | strong |
| **S** (strong) | strong | weak |
| **◇P** (eventually perfect) | strong | eventually strong |
| **◇S** (eventually strong) | strong | eventually weak |

(**W** is the weakly-complete, weakly-accurate counterpart of **S**; **◇W** the weakly-complete counterpart of **◇S**.) Chandra & Toueg show consensus is solvable with detectors that make *infinitely many* mistakes; **P** and **S** tolerate any number of crashes, while ◇P and ◇S require a majority of correct processes. They also prove consensus and atomic broadcast are reducible to each other.

The companion result [Chandra, Hadzilacos & Toueg 1996] identifies the **weakest** detector for consensus: ◇W is sufficient when n > 2f, and any detector *D* usable for consensus can be transformed into ◇W — so ◇W is weakest, and if n ≤ 2f any usable detector must be strictly stronger. To prove this the authors introduce **Ω**, which outputs a single trusted process ("eventual leader election"): eventually all correct processes permanently trust the same correct process. Ω is trivially transformable into ◇W (suspect everyone but your leader) at no communication cost, though the reverse transformation is nontrivial and requires communication. Ω is the abstraction Paxos and Raft actually rely on.

**How heartbeats implement ◇P.** Under **partial synchrony** [Dwork, Lynch & Stockmeyer 1988] — bounds Δ (message delay) and Φ (relative process speed) exist but are unknown *a priori*, or are known but only guaranteed to hold from some unknown Global Stabilization Time onward — a heartbeat detector with adaptive timeout implements ◇P. The monitored process *p* sends heartbeats every η time units; the monitor *q* suspects *p* if no heartbeat arrives within Δ_to. Before GST, arbitrary false suspicions occur (so only *eventual* accuracy is achievable); after GST, with a timeout that has grown past the true Δ, no correct process is suspected — strong accuracy holds forever after, giving ◇P.

The naive fixed-timeout version has two defects that Chen, Toueg & Aguilera [2002] identify: (i) because the timer for heartbeat m_i starts when m_{i−1} *arrives*, the probability of a premature timeout on m_i depends on how fast m_{i−1} was — an undesirable coupling to the past; and (ii) worst-case detection time becomes maximum message delay + TO, and max delay is often orders of magnitude larger than mean delay. Their fix is **freshness points**: fixed time points τ_i = σ_i + δ, where σ_i is the *send* time of m_i; *q* trusts *p* at time t ∈ [τ_i, τ_{i+1}) iff it has received m_i or later. They give QoS metrics (detection time, mistake rate, mistake duration), show the algorithm is optimal for some of them, and show how to derive η and δ from QoS requirements even when the network's probability distribution is unknown. Accrual detectors [Hayashibara et al. 2004] go further, outputting a continuous suspicion level φ instead of a boolean.

```
# Heartbeat failure detector implementing <>P under partial synchrony.
# Structure: Chandra & Toueg 1996 (classes), Chen/Toueg/Aguilera 2002
# (freshness points + adaptive timeout). Open MPI/PRTE ships a comparable
# detector with period 5s / timeout 10s by default.

# --- monitored side: process p ---
every eta time units:                          # eta = heartbeat period
    seq <- seq + 1
    for q in monitors(p):
        send HEARTBEAT(p, seq, send_time = now()) to q

# --- monitoring side: process q watching p ---
state:
    suspected      : set, initially empty
    last_seq[p]    : highest sequence number seen from p, initially -1
    delta[p]       : safety margin, initially delta_0
    eta[p]         : p's advertised heartbeat period
    rtt_hist[p]    : bounded window of observed inter-arrival gaps

on receive HEARTBEAT(p, seq, send_time):
    if seq <= last_seq[p]: return               # stale / reordered
    last_seq[p] <- seq
    record_arrival(rtt_hist[p], now() - send_time)

    if p in suspected:
        # False positive. Recover trust AND widen the margin, so that the
        # detector's accuracy improves monotonically. This back-off is what
        # makes eventual strong accuracy reachable after GST: delta grows
        # past the true (unknown) message-delay bound and then stops growing.
        suspected <- suspected \ {p}
        delta[p]  <- delta[p] * BACKOFF          # BACKOFF > 1
        emit RESTORE(p)

# Freshness point: the deadline for heartbeat i is derived from its SEND
# time, not from the ARRIVAL of heartbeat i-1. This decouples the premature-
# timeout probability for m_i from the delay of m_{i-1} (Chen et al. 2002).
periodically (granularity << eta[p]):
    expected_send <- eta[p] * (last_seq[p] + 1)
    freshness_point <- expected_send + delta[p]
    if now() > freshness_point and p not in suspected:
        suspected <- suspected + {p}
        emit SUSPECT(p)

# Properties, and their price:
#   strong completeness  : a crashed p sends no more heartbeats, so every
#                          monitor eventually times out permanently.       [free]
#   eventual strong acc.  : requires delta to exceed the true delay bound.
#                          Before GST, SUSPECT may be wrong -- so callers
#                          MUST treat SUSPECT as a liveness hint and never
#                          let SAFETY depend on it.
#   NOT detected          : a process that is alive, punctual, and WRONG.
#                          Value failures are invisible here by construction.
```

### B.3 Consensus and agreement protocols, and why ULFM's `agree` is the right primitive

**Two-phase commit (2PC)** — coordinator sends PREPARE, participants vote, coordinator decides and broadcasts. Its defect is **blocking**: if the coordinator crashes after collecting votes but before broadcasting the decision, every participant that voted COMMIT is *uncertain* — it has promised not to abort unilaterally and cannot know the outcome, so it holds locks until the coordinator recovers [Gray & Lamport 2006, §3.3].

**Three-phase commit (3PC)** [Skeen 1981] inserts a PRE-COMMIT phase so that no participant transitions directly from uncertainty to committed. Skeen's structural result is that a commit protocol can be non-blocking only if no reachable state is simultaneously committable by one node and abortable by another; the pre-commit state removes that ambiguity, and timeouts let participants terminate autonomously. 3PC is non-blocking **under a fail-stop model with reliable failure detection** — and unsafe under network partition (split-brain), which is why it is a textbook fixture and essentially absent from production.

**Paxos** [Lamport 1998] and **Paxos Commit** [Gray & Lamport 2006] fix the blocking problem the right way: replace the single coordinator with a *replicated* decision, so progress requires only a quorum. Paxos Commit runs consensus on each participant's commit/abort decision using 2F+1 coordinators and makes progress if F+1 are working; classic 2PC is exactly the F = 0 case. **Raft** [Ongaro & Ousterhout 2014] produces a result equivalent to multi-Paxos with a strong-leader structure, randomized election timers, and an explicit membership-change mechanism using overlapping majorities, chosen for understandability and implementability.

**Why `MPI_Comm_agree`, and not Paxos.** For AgentMPI this is the key architectural argument, and it has four parts.

1. **The problem is different.** Paxos/Raft solve *state-machine replication*: maintain a durable, totally-ordered log across a membership that outlives any individual participant, with an external client that must see linearizable results. ULFM's `agree` solves *one-shot uniform agreement on a boolean among the current participants of a collective*. There is no log, no persistent term/ballot state, no client visible outside the group, no requirement that decisions survive the death of the whole group.
2. **Everyone participates, so quorum machinery is wasted.** In a Paxos group, a decision is made by a majority and the minority catches up later. In an MPI collective, *all* live ranks must both participate and learn the value, because the very next operation is another collective over that same group. `agree` is an all-to-all reduce-then-broadcast whose failure-free cost is O(log n) — the same as `MPI_Allreduce` [Bland et al. 2012, §4.2]. Paxos would add ballot numbering, leader election, and stable-storage writes to produce a decision that all participants need anyway.
3. **Durability is the wrong goal.** Paxos writes to stable storage so decisions survive process restart. ULFM does not need this: if the whole group dies, the job dies, and the recovery unit is the job. Paying for fsync-per-decision on the recovery path of a tightly-coupled parallel application would be a large cost for a guarantee nobody uses.
4. **`agree` composes with `revoke` and `shrink` to give exactly the three things you need, and no more.** Revoke gives *unilateral, reliable, non-collective* control-flow unification — one participant can force every other onto the recovery branch. Shrink gives *agreed membership*. Agree gives *agreed decisions*. That triad is the minimum sufficient basis for building transactions, uncoordinated C/R, and ABFT on top as libraries [Bland et al. 2013, §3]. Paxos gives you a strict superset at strictly higher cost and complexity — and, notably, *still* would not give you `revoke`, because "interrupt a peer that is blocked in a receive and does not yet know anything is wrong" is not a consensus problem at all.

The honest caveat: `agree`'s cheapness rests on ULFM's crash-stop assumption. Under Byzantine faults, agreement needs 3f+1 participants and cryptographic authentication, and the O(log n) reduce-then-broadcast structure collapses. This is exactly where agent systems diverge from HPC.

### B.4 Exactly-once vs. at-least-once, idempotency, deduplication, and the end-to-end argument

Message-passing systems offer three delivery semantics. **At-most-once** never duplicates but may lose. **At-least-once** never loses but may duplicate — the natural consequence of retrying on timeout, since a timeout cannot distinguish "request lost" from "response lost." **Exactly-once** is what applications want and what no transport can provide alone.

The reason is the **end-to-end argument** [Saltzer, Reed & Clark 1984]:

> "The function in question can completely and correctly be implemented only with the knowledge and help of the application standing at the endpoints of the communication system. Therefore, providing that questioned function as a feature of the communication system itself is not possible. (Sometimes an incomplete version of the function provided by the communication system may be useful as a performance enhancement.)"

Saltzer et al. apply this explicitly to **duplicate message suppression**, message sequencing, guaranteed delivery, host-crash detection, and delivery receipts, alongside the famous reliable-file-transfer case. The argument's force for duplicate suppression is that only the application knows what "the same request" *means*. A lower layer can suppress duplicates it can see (same TCP sequence number), but it cannot know that two syntactically different requests arriving over two different connections after a client-side retry are the same *logical* operation.

The practical consequence is the standard construction: **at-least-once transport + idempotency + end-to-end deduplication = effectively-exactly-once**. The client mints a stable idempotency key that survives its own retry loop, proxy retries, and load-balancer retries; the endpoint that owns the semantics stores keys with results and returns the stored result on replay. Idempotency is doing the work in a form where re-execution is harmless (set-to-value rather than increment-by-one), and deduplication is the fallback for operations that are not naturally idempotent. Both live at the endpoints. "Exactly-once" in Kafka, SQS, or any streaming system is exactly this: at-least-once delivery plus a consumer-side dedup step keyed on something only the consumer can define.

For AgentMPI, note that this is the *same* argument ULFM makes at a different layer. ULFM says: the MPI library cannot recover your application, because only the application knows what a consistent application state is; the library's job is to hand back control in a state you can reason about. Saltzer et al. say: the network cannot make your operations exactly-once, because only the application knows what an operation is. Both are refusals to over-promise at the wrong layer.

### B.5 Timeouts, stragglers, and speculative execution

**Backup tasks** [Dean & Ghemawat 2004]. A *straggler* is a machine that takes unusually long on one of the last few tasks — from a bad disk degrading reads from 30 MB/s to 1 MB/s, from co-scheduled tasks contending for CPU/memory/disk/network, or (their example) from a machine-initialization bug that disabled processor caches and slowed affected machines by over 100×. MapReduce's mitigation: when the operation is close to completion, the master schedules backup executions of the remaining in-progress tasks, and the task is complete when *either* copy finishes. Tuned to add no more than a few percent of resources; disabling it made their sort benchmark take **44% longer**. LATE [Zaharia et al. 2008] later showed naive progress-rate heuristics misbehave in heterogeneous environments (they observed ~80% of reduces backed up, most losing to the originals, thrashing the network) and proposed backing up the task with the longest approximate time to end, with caps and placement constraints.

**The tail at scale** [Dean & Barroso 2013] generalizes this from "batch job with a long tail" to "every fan-out request." If a request touches many backends, the slowest backend dominates, even if 999 of 1000 are instant. Two mechanisms:

- **Hedged requests.** Send to the most appropriate replica; if no answer after a brief delay, send a secondary; cancel outstanding copies once the first result arrives. Waiting for the **95th-percentile expected latency** before hedging caps the extra load at ~5% while substantially shortening the tail — at the price of only benefiting the tail.
- **Tied requests.** Enqueue copies at two servers *simultaneously*, each tagged with the identity of the other. When one begins execution it sends a cancellation to its counterpart, which aborts or heavily deprioritizes the still-queued copy. Because both cancellations can be in flight while both servers start (typically when both queues are empty), the client inserts a small delay of about **twice the average network message delay** (≤1 ms in modern datacenter networks) between the two sends. In Google's cluster-level filesystem, tied requests with cross-server cancellation after 1 ms **reduced median latency by 16% and 99.9th-percentile latency by ~40%**.

The important architectural point: hedging/tying is only *safe* for idempotent operations. A duplicate write, payment authorization, or non-idempotent RPC executed twice corrupts state unless deduplicated upstream by an idempotency key — which puts us right back in §B.4. Speculation and exactly-once semantics are the same design problem viewed from two sides.

### B.6 Supervision trees, let-it-crash, and the actor model

The **actor model** originates with Hewitt, Bishop & Steiger [1973], a position paper for IJCAI proposing actors as a single universal primitive underneath control structures, data structures, and AI reasoning alike. An actor has exactly three capabilities: **create** new actors, **send** messages to actors it knows, and **become** (designate how it will behave on the next message). Agha [1986] separated the model from its AI roots and developed it as a foundation for concurrent object-oriented programming, formalizing create/send/become.

**Erlang/OTP** made failure a first-class structural concern. The core concept is the **supervision tree**: *workers* perform computation, *supervisors* monitor workers and restart them when something goes wrong, and the hierarchical arrangement of supervisors and workers is the fault-tolerance design [Erlang/OTP Design Principles]. Restart strategies [Erlang `supervisor` module]:

| Strategy | Effect on sibling children | When to use |
|---|---|---|
| `one_for_one` (default) | none — only the failed child restarts | children are genuinely independent: no shared state, no ordering dependency |
| `one_for_all` | all children are terminated, then all restarted | children form a cohesive unit; if A holds state B depends on, B's world view is invalid once A dies |
| `rest_for_one` | children started *after* the failed one are terminated and restarted with it; earlier ones continue | sequential start-order dependencies (pool → warmer → handler) |
| `simple_one_for_one` | dynamic children, all instances of the same code; only the failed one restarts | worker pools created at runtime |

Orthogonal to strategy is each child's `restart` type: `permanent` (always restarted), `transient` (restarted only on abnormal exit), `temporary` (never restarted). And every supervisor has a **restart intensity** limit — if more than *MaxR* restarts occur within *MaxT* seconds, the supervisor gives up, terminates, and **escalates the failure to its own supervisor**. That escalation is what turns a tree of local restarts into a global failure policy, and it is the built-in defence against infinite crash loops.

**Let it crash** is the philosophy this enables: avoid defensive coding, handle what genuinely makes sense to handle locally, and allow other errors to terminate the process and propagate to the supervisor. Workers assume their inputs are valid, their dependencies are up, and their state is consistent; when those assumptions break, the process dies and something *outside* it decides what to do. The frequently-missed point is that this is not "write code without error checks" — it is that error *recovery* belongs in a different component from business logic, so that the worker's happy path stays readable and the recovery policy stays inspectable in one place. **Akka** [Bonér, 2009–] carried mailboxes and supervisor hierarchies to the JVM, though as a library it can only ask actors to respect isolation that the shared heap does not enforce.

**The philosophy contrast.** MPI/ULFM and Erlang/OTP take opposite stances on *who* recovers:

| | MPI + ULFM | Erlang/OTP |
|---|---|---|
| Who decides recovery | the application | the supervisor, out-of-band from the worker |
| What the runtime guarantees | no call blocks forever; you get control back in a state you can reason about | a failed process is restarted per a declared strategy |
| Membership | fixed group, repaired by explicit `shrink` | dynamic; children come and go |
| Typical state model | large distributed state, expensive to reconstruct → rollback | small per-process state, cheap to rebuild → restart |
| Failure granularity | rank, with a group-wide consistency obligation | process, isolated by design |
| Programmer burden | high: you write the recovery loop | low: you declare a strategy |

Neither is simply better. ULFM's stance follows from the fact that a rank in a tightly-coupled solver holds an irreplaceable slice of a global distributed state, and "restart it" is meaningless without a story for that data. Erlang's stance follows from actors being small, isolated, and mostly stateless or cheaply rebuildable. **An LLM agent is much closer to an Erlang actor than to an MPI rank in state size, and much closer to an MPI rank in the coupling of its outputs to the group's progress.** That tension is the design space AgentMPI sits in.

---

## Comparison of fault-tolerance approaches

| Approach | Failures tolerated | Recovery cost | Programmer burden | Forward or backward |
|---|---|---|---|---|
| **ULFM** (`revoke`/`shrink`/`agree`) | fail-stop process crash; explicitly **not** transient or Byzantine [Bland et al. 2013]. TOPO/FILE/RMA not covered | near-zero failure-free (latency deltas within noise [Bland et al. 2012]); on failure: detection ~30 ms, `revoke` <50 µs, `shrink` grows ~linearly, dominated by communicator construction not the O(log n) agreement | **High.** ULFM restores the *substrate* only; you write detection handling, the revoke/shrink loop, and all state recovery yourself | **Neutral / enabling.** Provides the mechanism; the app chooses rollback (with C/R) or forward (ABFT, re-dispatch) |
| **Checkpoint / restart** (coordinated; SCR, VeloC, BLCR, DMTCP, Fenix) | fail-stop crash, node loss, and — because it needs no knowledge of the error — arbitrary/transient faults, provided the saved state is clean | Highest steady-state cost: δ per checkpoint at interval τ ≈ √(2δM); on failure, lose up to one interval plus restart R. PFS bandwidth is the scaling wall; multilevel caching mitigates | **Low to medium.** Transparent (DMTCP) to moderate (SCR/VeloC/Fenix APIs); no algorithmic change | **Backward** (rollback) |
| **ABFT** (Huang & Abraham; Bosilca/Dongarra) | fail-stop process loss *and* silent data corruption / bit flips — the only approach here that catches value failures | Very low: checksum maintenance is local and asymptotically negligible; no global checkpoint or rollback. Recovery inverts the checksum relation | **Highest.** Requires redesigning the algorithm to operate on encoded data; error propagation (e.g. in LU) needs extra machinery | **Forward** |
| **Message logging** (pessimistic / optimistic / causal) | fail-stop crash, with recovery *beyond* the last consistent checkpoint set; needed when the process interacts with an un-rollbackable outside world | Pessimistic: high failure-free (blocks on determinant stabilization), simple recovery. Optimistic: low failure-free, complex recovery + orphan rollback. Causal: low overhead + orphan-free. All need garbage collection | **Medium**, but conditional: requires the **piecewise-deterministic assumption** — you must be able to identify and replay every nondeterministic event | **Backward**, but *localized and bounded*: under pessimistic or causal logging only the failed process rolls back, and it can be replayed *past* its last checkpoint, so the domino effect is avoided. Under optimistic logging orphaned survivors must also roll back |
| **Supervision trees** (Erlang/OTP, Akka) | crash of an isolated process; escalation handles repeated crashes. Not designed for value failures or for state that cannot be rebuilt | Very low: restart one process (or a declared subtree) and re-initialize. Cost is whatever the worker must redo | **Lowest.** Declare a strategy (`one_for_one`, etc.), restart type, and intensity limit; recovery logic lives outside the worker | **Forward** (fresh known-good state), unless the worker restores from persisted state |

---

## Transfer to agent systems

A multi-agent LLM system is a message-passing system whose participants have a strictly richer failure spectrum than an MPI rank. Below, each empirical agent failure mode is placed in the theoretical taxonomy of §B.1 and matched to an HPC mechanism.

| Agent failure | Failure model | Detectable by ◇P heartbeats? | Right HPC analogue |
|---|---|---|---|
| **(a) crash / timeout** — process dies, container OOMs, request never returns | crash-stop; timing failure if merely slow | Yes | ULFM `MPI_ERR_PROC_FAILED` + `revoke`/`shrink`; supervisor restart for stateless agents |
| **(b) context exhaustion** — agent hits its window and can no longer make progress | *fail-stop with advance warning*: unlike a crash, it is **predictable** from token accounting | Not by heartbeat, but by explicit resource monitoring — the agent is alive and punctual | Checkpoint/restart with a **Young/Daly-style interval**: summarize-and-persist working state at a computed interval. This is the closest true analogue of a restart dump, because δ (summarization cost) and M (expected steps to exhaustion) are both *measurable* |
| **(c) looping** — agent repeats a tool call / reasoning cycle without progress | timing failure escalating to omission: it never delivers | No — heartbeats look perfect | Erlang's **restart intensity limit** (MaxR restarts in MaxT → terminate and escalate), applied to progress rather than restarts: a monotone progress counter with a bound, then escalate |
| **(d) confidently wrong output** | **value failure** — byzantine-*ish*, non-adversarial. Explicitly *outside* ULFM's model [Bland et al. 2013] | **No, and no timeout-based detector ever will.** This is the fundamental gap | **ABFT** — content-level redundancy and checksum-style verification (see below). Not failure detection |
| **(e) 429 / rate limit / transient provider error** | omission failure on the channel, not the process; the agent is fine, the link is not | Partly — the error is explicit and typed | **Hedged/tied requests** [Dean & Barroso 2013] plus at-least-once retry with backoff, gated by end-to-end **idempotency keys** [Saltzer et al. 1984]. Retry only what is safe to retry twice |
| **(f) going silent mid-task** | crash-stop *or* timing failure — **indistinguishable**, which is exactly FLP's premise [Fischer et al. 1985] | Yes, but unreliably: any suspicion is a guess, so this is a **fail-noisy** (◇P) not fail-stop model | Heartbeat detector with adaptive timeout and **freshness points** [Chen et al. 2002]; treat SUSPECT as a liveness hint only, never as a safety input |

Three structural consequences follow.

**First, the harness is fail-noisy, not fail-stop, and must be built that way.** Because (f) is undecidable in bounded time, every "the agent is dead" conclusion is a *suspicion*. Following Cachin et al.'s discipline: safety properties must not depend on failure-detector accuracy; only liveness may. Concretely, an agent harness must never let a false suspicion cause a *second* side-effecting execution of a non-idempotent action — which is the failure mode of naive retry timeouts in production agent systems today. `[UNVERIFIED]` — we have not measured this rate in the wild.

**Second, ULFM's revoke/shrink/agree triad fits an agent harness better than either naive retry or full Paxos.**

Against **naive retry**: retry does not address the uniformity problem, and the uniformity problem is *the* multi-agent failure mode. Consider a planner that dispatched subtasks to five workers and one worker dies. The planner's `receive` from that worker fails, so the planner branches to recovery and stops sending. The other four workers are now blocked awaiting a planner message that will never come — and *no failed participant is involved in those four waits*, so no per-call error will ever fire and no retry will ever unblock them. This is exactly Bland et al.'s [2012] deadlock example transposed: "a process, unaware of the failure, posts a reception from another process that has switched to the recovery path; the matching send will never be posted. Yet no failed process participates in the operation and it should not raise an error." Retry cannot fix it because there is nothing to retry. **Revoke** fixes it precisely: one unilateral, non-collective, reliably-propagated call forces every peer on the channel group onto the recovery branch, whether or not that peer had any idea anything was wrong. Then **shrink** rebuilds a group over the survivors so that broadcast/gather-style coordination is well-formed again, and **agree** lets the group settle a boolean ("did phase *k* complete? is this artifact safe to commit?") that no single agent can determine locally.

Against **full Paxos/Raft**: all four arguments of §B.3 apply, and one is sharper for agents. Agent groups are typically small (single digits to low tens), short-lived (one task), and *fully participating* — every agent needs the decision, because the next step is another group interaction. Paxos's core value proposition is that a *majority* decides and the *minority* catches up later, backed by durable ballot state so decisions outlive process restarts. In an agent harness, if the group dies the task dies; there is no log to replay to a future incarnation of the group. Paying for leader election, ballot numbering, and durable per-decision state buys a guarantee nobody consumes. Meanwhile Paxos does **not** provide revoke — "unblock a peer that is waiting on a message that will never arrive" is not a consensus problem, and no amount of consensus machinery produces it. `agree`'s O(log n) reduce-then-broadcast is the right cost, and — importantly for agents — its *shape* is right too: `agree` is defined to complete **even on a revoked communicator**, which is the property that makes a recovery protocol possible at all (you must be able to reach agreement *after* you have torn the channel down).

The one place where the analogy must be modified rather than borrowed: `agree`'s cheapness rests on crash-stop. If we want agreement in the presence of failure mode (d) — an agent that is alive, punctual, and wrong — the boolean being agreed cannot be self-reported. It must be the output of a verification step, which brings us to the last point.

**Third, "ABFT for agents" is the missing primitive, and it is a distinct mechanism from everything else in the harness.** ABFT's defining move is not redundancy for its own sake; it is that the *encoding is maintained by the same algebra as the computation*, so a cheap invariant check ("does the checksum relation still hold?") detects and often corrects corruption without rollback [Huang & Abraham 1984]. The transfer to agents is a family of *invariant checks that are cheaper than the work they check*:

- **Checksum-style structural invariants.** Where an agent's output has a checkable arithmetic or structural relation — a total that must equal the sum of parts, a diff that must apply cleanly, code that must compile, a JSON blob that must validate against a schema, a citation that must resolve — verify the relation, not the reasoning. This is the closest true analogue of Huang & Abraham: an invariant maintained *by construction*, checked in time asymptotically smaller than generating the answer, and often *correctable* (recompute the one part that breaks the total).
- **Cross-checking by a second agent (a verifier).** Analogous to duplex comparison, and cheap when verification is asymptotically easier than generation. `[UNVERIFIED]` whether verifier agents materially outperform structural checks in practice; the literature on LLM-as-judge for self-adjudication is mixed.
- **Self-consistency voting.** Sample *m* reasoning paths and take the modal answer [Wang et al. 2023], which improved GSM8K by +17.9% and AQuA by +12.2% over greedy chain-of-thought. Its stated mechanism is error *decorrelation*: incorrect chains disagree with each other (many ways to be wrong) while correct chains converge (one way to be right). Its stated limit is the one that matters here: **voting reduces variance, not bias.** A systematic misreading appears identically in all *m* samples, so voting cannot recover from it — precisely the ABFT analogue of a fault that corrupts the data *and* its checksum, which is why Du et al. [2012] had to design for "the possibility of losing both data and checksum from a single failure." Independent-model or independent-prompt ensembles are the analogue of putting the checksum on a different processor.
- **The layering matters.** Following §A.3's rollback/forward distinction: verification-based recovery is **forward** recovery. It reconstructs a correct state from redundant encoding rather than replaying history, and it is the only mechanism in the table that catches value failures. Checkpointing an agent's context is **backward** recovery and catches (a), (b), (f). The two are complementary, not substitutable, and a complete harness needs both — the same conclusion Du et al. reached for dense factorizations, where ABFT protects the right factor and checkpointing protects the left.

Finally, note the pleasing symmetry that closes the loop between §A.2 and §B.4: ULFM refuses to recover your application because only the application knows what a consistent application state is; Saltzer, Reed & Clark refuse to put duplicate suppression in the network because only the endpoints know what a logical operation is. An agent framework should refuse in the same way — provide revoke/shrink/agree over agent groups, provide idempotency-keyed at-least-once messaging, provide a checkpoint interval computed from measured δ and M, and let the *application* decide what "correct" means for its artifacts. The framework's job is to guarantee that no agent blocks forever and that control returns in a state the application can reason about. That is ULFM's contract, and it is the right contract to steal.

---

## Bibliography

**MPI standard documents**

- MPI Forum. *MPI: A Message-Passing Interface Standard, Version 1.1.* June 1995. §7.2 "Error handling."
- MPI Forum. *MPI: A Message-Passing Interface Standard, Version 3.1.* June 4, 2015. §8.3 "Error handling."
- MPI Forum. *MPI: A Message-Passing Interface Standard, Version 4.0.* June 9, 2021.
- MPI Forum. *MPI: A Message-Passing Interface Standard, Version 4.1.* November 2, 2023. Annex, "Changes in MPI-4.0."
- MPI Forum. *MPI: A Message-Passing Interface Standard, Version 5.0.* June 5, 2025. §10.3 "Error Handling."
- MPI Forum. Fault-tolerance issue tracker: mpi-issues #20 (ULFM), #581 (slice 1: ack_failed/get_failed/revoke), #582 (slice 2: agree), #583/#877 (slice 3: shrink), #816 (fault model). Meeting notes, December 2025; non-voting virtual meeting on fault tolerance, 2025-11-05.

**ULFM**

- Bland, W., Bosilca, G., Bouteiller, A., Herault, T., and Dongarra, J. *A Proposal for User-Level Failure Mitigation in the MPI-3 Standard.* Technical report, Dept. of EECS, University of Tennessee, 2012.
- Bland, W., Bouteiller, A., Herault, T., Hursey, J., Bosilca, G., and Dongarra, J. "An Evaluation of User-Level Failure Mitigation Support in MPI." *EuroMPI 2012*, LNCS; extended in *Computing* 95(12):1171–1184, 2013.
- Bland, W., Bouteiller, A., Herault, T., Bosilca, G., and Dongarra, J. "Post-failure recovery of MPI communication capability: Design and rationale." *IJHPCA* 27(3):244–254, 2013. DOI 10.1177/1094342013488238. *(Canonical ULFM citation.)*
- Bland, W., Raffenetti, K., and Balaji, P. "Lessons Learned Implementing User-Level Failure Mitigation in MPICH." *CCGrid 2015.*
- Bouteiller, A., Bosilca, G., et al. "Failure detection and propagation in HPC systems." *SC 2016.* DOI 10.1109/SC.2016.26.
- Hursey, J., Naughton, T., Vallee, G., and Graham, R. L. "A log-scaling fault tolerant agreement algorithm for a fault tolerant MPI." *EuroMPI 2011*, LNCS 6690:255–263.
- Hursey, J., Graham, R. L., Bronevetsky, G., Buntinas, D., Pritchard, H., and Solt, D. G. "Run-through stabilization: An MPI proposal for process fault tolerance." *EuroMPI 2011*, LNCS 6690:329–332.
- Open MPI documentation. "User-Level Fault Mitigation (ULFM)," and man pages `MPIX_Comm_revoke(3)`, `MPIX_Comm_shrink(3)`, `MPIX_Comm_agree(3)`, `MPIX_Comm_get_failed(3)`, `MPIX_Comm_ack_failed(3)`, `MPIX_Comm_is_revoked(3)`.
- Fault Tolerance Research Hub (fault-tolerance.org). "Simplifying the ACK/GET_ACKED couple," 26 Aug 2019; ULFM specification drafts (2012–2017).
- Innovative Computing Laboratory, University of Tennessee. ULFM project profile.
- Losada, N., Martín, M. J., González, P., et al. "Fault tolerance of MPI applications in exascale systems: The ULFM solution." *Future Generation Computer Systems* 106:467–481, 2020.

**Alternative MPI fault-tolerance models**

- Fagg, G. and Dongarra, J. "FT-MPI: Fault tolerant MPI, supporting dynamic applications in a dynamic world." *EuroPVM/MPI 2000*, LNCS 1908:346–353.
- Hassani, A., Skjellum, A., and Brightwell, R. "Design and Evaluation of FA-MPI, a Transactional Resilience Scheme for Non-blocking MPI." *DSN 2014*, 750–755. DOI 10.1109/DSN.2014.78.
- Laguna, I., Richards, D. F., Gamblin, T., Schulz, M., de Supinski, B. R., Mohror, K., and Pritchard, H. "Evaluating and extending user-level fault tolerance in MPI applications." *IJHPCA*, 2016. *(Reinit.)*
- Gamell, M., Van der Wijngaart, R. F., Teranishi, K., and Parashar, M. *Specification of Fenix MPI Fault Tolerance library version 1.0.1.* Sandia National Laboratories, 2016. DOI 10.2172/1330192.
- Gamell, M., Teranishi, K., Heroux, M. A., Mayo, J., Kolla, H., Chen, J., and Parashar, M. "Local recovery and failure masking for stencil-based applications at extreme scales." *SC 2015.*
- Gamell, M., et al. "Evaluating Online Global Recovery with Fenix Using Application-Aware In-Memory Checkpointing Techniques." *ICPPW 2016.* DOI 10.1109/ICPPW.2016.56.
- Gamell, M., et al. "Asynchrony and Failure Masking via Pseudo-Local Process Recovery in MPI Applications." *IPDPSW 2024.* DOI 10.1109/IPDPSW63119.2024.00193.
- Sultana, N., Skjellum, A., Laguna, I., Farmer, M. S., Mohror, K., and Emani, M. "MPI Stages: Checkpointing MPI State for Bulk Synchronous Applications." *EuroMPI 2018.* DOI 10.1145/3236367.3236385.
- Teranishi, K. and Heroux, M. A. "Toward Local Failure Local Recovery Resilience Model using MPI-ULFM." *EuroMPI/ASIA 2014*, 51–56. DOI 10.1145/2642769.2642774.

**Checkpointing and rollback recovery**

- Young, J. W. "A first order approximation to the optimum checkpoint interval." *Communications of the ACM* 17(9):530–531, 1974. DOI 10.1145/361147.361115.
- Daly, J. T. "A Model for Predicting the Optimum Checkpoint Interval for Restart Dumps." *ICCS 2003*, LNCS 2660:3–12. DOI 10.1007/3-540-44864-0_1.
- Daly, J. T. "A higher order estimate of the optimum checkpoint interval for restart dumps." *Future Generation Computer Systems* 22(3):303–312, 2006. DOI 10.1016/j.future.2004.11.016.
- Benoit, A., Du, Y., Herault, T., Marchal, L., Pallez, G., Perotin, L., Robert, Y., Sun, H., and Vivien, F. "Checkpointing à la Young/Daly: An Overview." *IC3 2022.* DOI 10.1145/3549206.3549328.
- Elnozahy, E. N. M., Alvisi, L., Wang, Y.-M., and Johnson, D. B. "A survey of rollback-recovery protocols in message-passing systems." *ACM Computing Surveys* 34(3):375–408, 2002. DOI 10.1145/568522.568525.
- Alvisi, L. and Marzullo, K. "Message logging: pessimistic, optimistic, causal, and optimal." *IEEE Transactions on Software Engineering* 24(2):149–159, 1998. DOI 10.1109/32.666828.
- Strom, R. E. and Yemini, S. "Optimistic recovery in distributed systems." *ACM TOCS* 3(3):204–226, 1985.
- Elnozahy, E. N. M. and Zwaenepoel, W. "Manetho: transparent rollback-recovery with low overhead, limited rollback, and fast output commit." *IEEE Transactions on Computers* 41(5):526–531, 1992.
- Venkatesh, K., Radhakrishnan, T., and Li, H. F. "Optimal checkpointing and local recording for domino-free rollback recovery." *Information Processing Letters* 25(5):295–303, 1987.
- Chandy, K. M. and Lamport, L. "Distributed Snapshots: Determining Global States of a Distributed System." *ACM TOCS* 3(1):63–75, 1985.
- Moody, A., Bronevetsky, G., Mohror, K., and de Supinski, B. R. "Design, Modeling, and Evaluation of a Scalable Multi-level Checkpointing System." *SC 2010.* *(SCR.)*
- Lawrence Livermore National Laboratory. Scalable Checkpoint/Restart (SCR) project page.
- Argonne National Laboratory / LLNL. VeloC: Very Low Overhead transparent multilevel Checkpoint/restart (ECP). github.com/ECP-VeloC/VELOC.
- Alsuwaiyan, S., et al. "Performance Evaluation of Checkpoint/Restart Techniques." arXiv:2311.17545, 2023. *(DMTCP vs BLCR.)*
- Garg, R., Price, G., and Cooperman, G. "System-level Scalable Checkpoint-Restart for Petascale Computing." *ICPADS 2016.*
- Zheng, G., Shi, L., and Kalé, L. V. "FTC-Charm++: An In-Memory Checkpoint-Based Fault Tolerant Runtime for Charm++ and MPI." *Cluster 2004.*

**ABFT**

- Huang, K.-H. and Abraham, J. A. "Algorithm-Based Fault Tolerance for Matrix Operations." *IEEE Transactions on Computers* C-33(6):518–528, 1984. DOI 10.1109/TC.1984.1676475.
- Bosilca, G., Delmas, R., Dongarra, J., and Langou, J. "Algorithm-based fault tolerance applied to high performance computing." *Journal of Parallel and Distributed Computing* 69(4):410–416, 2009.
- Du, P., Bouteiller, A., Bosilca, G., Herault, T., and Dongarra, J. "Algorithm-based fault tolerance for dense matrix factorizations." *PPoPP 2012*, 225–234. DOI 10.1145/2145816.2145845.
- Bosilca, G., Bouteiller, A., Herault, T., Robert, Y., and Dongarra, J. "Algorithm-Based Fault Tolerance for Dense Matrix Factorizations, Multiple Failures and Accuracy." *ACM TOPC* 1(2), 2015. DOI 10.1145/2686892.
- Chen, Z. "Towards Practical Algorithm-Based Fault Tolerance in Dense Linear Algebra." *HPDC 2013.* *(Discusses Luk & Park's backward-error model.)*
- Gropp, W. and Lusk, E. "Fault tolerance in message passing interface programs." *IJHPCA* 18(3):363–372, 2004.
- Cappello, F., Geist, A., Gropp, B., Kalé, L. V., Kramer, B., and Snir, M. "Toward exascale resilience." *IJHPCA* 23(4):374–388, 2009.

**Distributed-systems theory**

- Fischer, M. J., Lynch, N. A., and Paterson, M. S. "Impossibility of Distributed Consensus with One Faulty Process." *Journal of the ACM* 32(2):374–382, 1985.
- Dwork, C., Lynch, N., and Stockmeyer, L. "Consensus in the presence of partial synchrony." *Journal of the ACM* 35(2):288–323, 1988. DOI 10.1145/42282.42283.
- Chandra, T. D. and Toueg, S. "Unreliable failure detectors for reliable distributed systems." *Journal of the ACM* 43(2):225–267, 1996. DOI 10.1145/226643.226647.
- Chandra, T. D., Hadzilacos, V., and Toueg, S. "The weakest failure detector for solving consensus." *Journal of the ACM* 43(4):685–722, 1996. DOI 10.1145/234533.234549.
- Chen, W., Toueg, S., and Aguilera, M. K. "On the quality of service of failure detectors." *IEEE Transactions on Computers* 51(1):13–32, 2002. DOI 10.1109/TC.2002.1004595.
- Hayashibara, N., Défago, X., Yared, R., and Katayama, T. "The φ Accrual Failure Detector." *SRDS 2004.*
- Hadzilacos, V. and Toueg, S. "Fault-tolerant broadcasts and related problems." In *Distributed Systems* (2nd ed.), 97–145. ACM Press / Addison-Wesley, 1993.
- Cachin, C., Guerraoui, R., and Rodrigues, L. *Introduction to Reliable and Secure Distributed Programming*, 2nd ed. Springer, 2011.
- Skeen, D. "Nonblocking commit protocols." *SIGMOD 1981*, 133–142.
- Mohan, C. and Lindsay, B. "Efficient commit protocols for the tree of processes model of distributed transactions." *ACM SIGOPS OSR* 19(2):40–52, 1985.
- Lamport, L. "The Part-Time Parliament." *ACM TOCS* 16(2):133–169, 1998.
- Gray, J. and Lamport, L. "Consensus on Transaction Commit." *ACM TODS* 31(1):133–160, 2006.
- Ongaro, D. and Ousterhout, J. "In Search of an Understandable Consensus Algorithm." *USENIX ATC 2014.*
- Saltzer, J. H., Reed, D. P., and Clark, D. D. "End-to-end arguments in system design." *ACM TOCS* 2(4):277–288, 1984. DOI 10.1145/357401.357402.

**Stragglers, tail latency, supervision, actors**

- Dean, J. and Ghemawat, S. "MapReduce: Simplified Data Processing on Large Clusters." *OSDI 2004.*
- Zaharia, M., Konwinski, A., Joseph, A. D., Katz, R., and Stoica, I. "Improving MapReduce Performance in Heterogeneous Environments." *OSDI 2008.* *(LATE.)*
- Dean, J. and Barroso, L. A. "The Tail at Scale." *Communications of the ACM* 56(2):74–80, 2013.
- Mitzenmacher, M. "The Power of Two Choices in Randomized Load Balancing." *IEEE TPDS* 12(10):1094–1104, 2001.
- Hewitt, C., Bishop, P., and Steiger, R. "A Universal Modular ACTOR Formalism for Artificial Intelligence." *IJCAI 1973*, 235–245.
- Agha, G. *Actors: A Model of Concurrent Computation in Distributed Systems.* MIT Press, 1986.
- Ericsson AB. *Erlang/OTP System Documentation — Design Principles* (supervision trees, behaviours), and *stdlib* `supervisor` module reference (restart strategies, restart types, restart intensity).
- Bonér, J., et al. *Akka* documentation (supervision and monitoring). Lightbend, 2009–.

**LLM-side references for the transfer section**

- Wang, X., Wei, J., Schuurmans, D., Le, Q., Chi, E., Narang, S., Chowdhery, A., and Zhou, D. "Self-Consistency Improves Chain of Thought Reasoning in Language Models." *ICLR 2023.* arXiv:2203.11171.
