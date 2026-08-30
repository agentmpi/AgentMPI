# Fault Tolerance in MPI

Research note for *AgentMPI*. Scope: why the MPI standard is essentially fault-intolerant,
the error-handling machinery it does provide, the two major research designs that added
recovery semantics (FT-MPI, ULFM), and the ecosystem of alternatives (Reinit, MPI Stages,
Fenix, LFLR, checkpoint/restart, spawn-based replacement).

Citation markers are inline like `[bland2013ulfm]`; full entries in the `## BibTeX`
section at the end. Claims I could not confirm against a primary source in the time
budget are marked `[UNVERIFIED]`.

---

## 1. Why MPI-1 / MPI-2 / MPI-3 Have Essentially No Fault Tolerance

### 1.1 The normative position of the standard

MPI was specified as an API for *correct* programs running on *reliable* hardware. The
standard says almost nothing about process failure, and what it does say is deliberately
permissive toward implementations. Three normative statements do the work:

**(a) Communication is required to be reliable.** MPI mandates reliable communication
(MPI-1, §1.1 / the "MPI: A Message-Passing Interface Standard" statement of goals): an
implementation that delivers a corrupted message is non-conforming. Consequently the
*implementation*, not the application, owns detection and handling of network faults —
either by retransmission or by informing the application. "Under no circumstances should
an MPI application or library need to verify integrity of data received" [gropp2004ft].

**(b) The default error handler on `MPI_COMM_WORLD` is `MPI_ERRORS_ARE_FATAL`.** The
standard text (identical wording across MPI-1.1 §7.2, MPI-2.2 §8.3, MPI-3.1 §8.3):

> The error handler `MPI_ERRORS_ARE_FATAL` is associated by default with `MPI_COMM_WORLD`
> after initialization. Thus, if the user chooses not to control error handling, every
> error that MPI handles is treated as fatal.
> — MPI-3.1, §8.3 *Error Handling* [mpi31]

and, for `MPI_ERRORS_ARE_FATAL` itself:

> The handler, when called, causes the program to abort on all executing processes. This
> has the same effect as if `MPI_ABORT` was called by the process that invoked the handler.
> — MPI-3.1, §8.3 [mpi31]

**(c) The killer clause: after an error, MPI state is undefined.** This is the sentence
that makes MPI fault-intolerant *by specification* rather than merely by default
configuration. Verbatim, and stable from MPI-1.0 through MPI-3.1:

> After an error is detected, the state of MPI is undefined. That is, using a user-defined
> error handler, or `MPI_ERRORS_RETURN`, does **not** necessarily allow the user to continue
> to use MPI after an error is detected. The purpose of these error handlers is to allow a
> user to issue user-defined error messages and to take actions unrelated to MPI (such as
> flushing I/O buffers) before a program exits. An MPI implementation is free to allow MPI
> to continue after an error but is not required to do so.
> — MPI-1.0 §7.2 / MPI-1.1 §7.2 / MPI-2.2 §8.3 / MPI-3.1 §8.3 [mpi10; mpi22; mpi31]

Paired with an *advice to implementors* that merely encourages best effort:

> A good quality implementation will, to the greatest possible extent, circumscribe the
> impact of an error, so that normal processing can continue after an error handler was
> invoked. — MPI-1.1 §7.2 [mpi11]

The practical consequence: `MPI_ERRORS_RETURN` is **not** a fault-tolerance mechanism. It
buys you a return code, not a usable library. A conforming implementation may return
`MPI_SUCCESS`-less codes and then deadlock, abort, or corrupt state on the next call. This
is the single most important fact for anyone designing a fault-tolerant protocol on the MPI
model: *error reporting and continued operability are orthogonal, and the standard only ever
promised the former, weakly.*

### 1.2 Why the standard is this way

Two structural reasons, both worth transplanting into any protocol design discussion:

1. **Collective semantics couple all ranks.** "Because of collective operations, the failure
   of any one process in a communicator affects all processes in the communicator, even those
   that are not in direct communication with the failed process. This factor contributes to
   the fragility of programs that use `MPI_COMM_WORLD` as their only communicator"
   [gropp2004ft]. Collectives are the transitive-closure amplifier that turns a local fault
   into a global one.
2. **Communicator immutability is load-bearing.** MPI's object model guarantees that the size
   of a communicator and a process's rank in it are constant. Applications decompose data by
   size and index by rank. Any recovery scheme that mutates size or rank in place breaks the
   contract that application code was written against [gropp2004ft].

Gropp & Lusk also correct a widespread misstatement. "`MPI is not fault tolerant`" is,
they argue, "not actually well formed and so is neither true nor false" [gropp2004ft]. The
standard does *not* mandate that all processes die when one dies; it mandates a *default
error handler* with that effect, and the default is changeable. What the standard does not
provide is any guarantee of usable state afterwards.

### 1.3 Gropp & Lusk (IJHPCA 2004): fault tolerance as a program property

W. Gropp and E. Lusk, "Fault Tolerance in Message Passing Interface Programs," *IJHPCA*
18(3):363–372, 2004 [gropp2004ft]. (An earlier version appeared at EuroPVM/MPI 2002 as
"Fault Tolerance in MPI Programs".) The paper's central thesis:

> We claim that fault tolerance is a property of an MPI program coupled with an MPI
> implementation. — [gropp2004ft]

Not a property of the API specification (which is just an interface), and not a property of
an implementation (which cannot immunise an arbitrary program). This framing matters: it
relocates the fault-tolerance obligation to the application/runtime *pair*.

**The taxonomy of "levels of survival"** (their §3), from strongest to weakest:

| Level | Description | Who acts |
|---|---|---|
| 1 (highest) | The implementation transparently recovers from some faults; the program continues without significant behavioural change, regardless of its structure | Implementation |
| 2 | The program is *notified* of the problem and is prepared to take corrective action | Application, on notification |
| 3 | Certain MPI operations (not all) become invalid; the program works around them | Application, restricted API |
| 4 (weakest) | The program aborts and is restarted from a checkpoint; state is held outside the processes, typically on disk | External / storage |

Levels 1–3 all require that "the program arranges for the nonfailing processes to retain
enough of the program state held by the failed process for the overall computation to
proceed" [gropp2004ft]. Combinations are allowed.

**The taxonomy of approaches** (their §5), which is the part usually cited:

1. **Checkpointing (§5.1).** User-directed or system-directed. They derive the classic
   optimal-interval result: with \(k_0\) = cost to write a checkpoint, \(k_1\) = cost to
   read/restore, \(\alpha\) = failure probability per unit time, \(T\) = failure-free run
   time, the optimum interval is \(t_0=\sqrt{2k_0/\alpha}\), giving expected total time
   \(E[T] = T\left(1 + \alpha k_1 + \sqrt{2\alpha k_0}\right)\) [gropp2004ft]. The point of
   the derivation is that for small \(\alpha\) and cheap checkpoints the overhead is
   "quite modest" — which, together with modularity (no algorithm changes), explains
   checkpointing's dominance. They recommend *user-directed* checkpointing because
   system-directed checkpointing must capture in-flight messages and kernel buffer state.
2. **Restructuring with intercommunicators (§5.2).** The most constructive contribution.
   An intercommunicator is MPI's two-party structure; two-party communication is exactly
   the structure that makes non-MPI client/server systems robust, because one party can
   locally notice the other is gone and stop talking to it. They give a manager/worker
   template: the manager builds one intercommunicator per worker via `MPI_Intercomm_create`
   from `MPI_COMM_SELF`, sets `MPI_ERRORS_RETURN` on each, and keeps an *in-progress* task
   list so a failed worker's task can be reissued. Workers hold little state (one task) and
   never talk to each other, so no collectives are needed. If a call returns non-success the
   manager marks that intercommunicator dead and never uses it again. With MPI-2 dynamic
   processes, `MPI_Comm_spawn` both creates workers (returning an intercommunicator directly)
   and *replaces* a dead worker. Per-communicator error handlers let different parts of the
   program run at different fault-tolerance levels.
3. **Modifying MPI semantics (§5.3).** The FT-MPI approach [fagg2000ftmpi] — communicators
   may have holes, ranks may change, collective behaviour changes. Gropp & Lusk are
   explicitly sceptical: "this approach, while intriguing as a way to experiment with
   fault-recovery algorithms, sacrifices too much in the area of time-tested semantics of
   MPI objects and functions to be realistic for writing production applications"
   [gropp2004ft]. Their objection is precisely the rank/size-immutability argument of §1.2.
4. **Extending MPI (§5.4).** Add new objects rather than mutate existing ones. Their sketch
   is `MPE_Process_array` (MPE = "extension"): a communicator-like object where each element
   names a fixed process, the array may grow or shrink but an element once defined always
   names the same process, a failed process's entry becomes **null**, arrays carry contexts
   and error handlers, **no collective operations are defined**, and new send/receive
   operations operate on it. They note it "is reminiscent of the BLANK option" of FT-MPI.

That §5.3-vs-§5.4 split — *mutate the existing object* vs *add a new object with weaker
guarantees* — is the fault line that FT-MPI and ULFM sit on either side of, and it is
directly relevant to AgentMPI's design choice about whether an agent group is mutable.

### 1.4 The MPI Forum Fault Tolerance Working Group

The MPI Forum reconvened for MPI-3 in 2008 and chartered a **Fault Tolerance Working Group**
(FTWG), chaired over its life by (among others) Rich Graham, Wesley Bland and Aurélien
Bouteiller [UNVERIFIED — chair roster and exact dates not confirmed against Forum minutes in
this pass]. Its documented output history:

- **Run-Through Stabilization (RTS)** — the FTWG's first major proposal for MPI-3.0. It
  aimed to let an application continue running through process failures with per-process
  failure notification, `MPI_Comm_validate`-style calls, and failure-handler registration.
  It was **not adopted into MPI-3.0**; it was judged too complex and too invasive on the
  failure-free path, and was withdrawn/failed to reach the required votes [UNVERIFIED —
  precise vote history not confirmed].
- **User Level Failure Mitigation (ULFM)** — the successor proposal, deliberately much
  smaller than RTS: three error classes and a handful of communicator operations, with no
  automatic recovery. Developed by the FTWG and maintained as a standing chapter draft at
  `fault-tolerance.org`; the Open MPI documentation points to the ULFM chapter draft dated
  2017-02-21 [openmpi_ulfm_docs]. See §4.
- MPI-3.0 (2012) and MPI-3.1 (2015) shipped **without** any process-failure semantics; the
  "state of MPI is undefined" clause survived intact.
- MPI-4.0 (June 2021) added `MPI_ERRORS_ABORT`, error-handling on sessions, and made a
  number of clarifications, but **did not** adopt ULFM. See §2 and §4.7.

The FTWG's practical significance for a protocol designer: the Forum's revealed preference,
over roughly fifteen years, is for *small, opt-in, application-driven* fault-tolerance
primitives with near-zero failure-free cost, not for transparent recovery.

---

## 2. The Error-Handling Machinery MPI Actually Provides

Even without recovery semantics, MPI has a well-specified *error reporting* apparatus. It is
worth cataloguing precisely, because a protocol like AgentMPI needs the same layers
(who is notified, with what granularity, and what the default policy is) even if it supplies
stronger continuation guarantees.

### 2.1 `MPI_Errhandler`: an opaque, per-object, purely local policy

An `MPI_Errhandler` is an opaque object accessed by a handle. Key properties [mpi31; mpi41]:

- **Attached per object, not per process.** In MPI-1/2/3, error handlers attach to
  **communicators, windows and files**. MPI-4 adds **sessions**. Calls not associated with
  any object are considered attached to `MPI_COMM_WORLD` (MPI-1/2/3); MPI-4 refines this to
  `MPI_COMM_SELF`, and, when `MPI_COMM_SELF` is not initialised (before `MPI_Init`, after
  `MPI_Finalize`, or in the pure Sessions model), to the **initial error handler** [mpi41].
- **Attachment is purely local.** "Different processes may attach different error handlers to
  corresponding objects" [mpi31, §8.3]. There is no global error policy; error policy is a
  local, per-object attribute. This is a direct antecedent of ULFM's *local notification*
  principle (§4.6).
- **Inheritance.** New communicators inherit the error handler of the parent communicator.
  Because `MPI_COMM_WORLD` defaults to `MPI_ERRORS_ARE_FATAL`, every derived communicator is
  fatal-by-default unless overridden [gropp2004ft].
- **Set/create API.** `MPI_Comm_set_errhandler`, `MPI_Win_set_errhandler`,
  `MPI_File_set_errhandler`, `MPI_Session_set_errhandler` (MPI-4); handlers are created with
  the matching `MPI_XXX_create_errhandler`. MPI-4 also allows an error handler to be passed
  in the `errorhandler` argument of `MPI_Session_init`, and the initial handler to be set via
  the `mpi_initial_errhandler` `mpiexec` CLI argument / `MPI_Comm_spawn` info key [mpi41].

### 2.2 The predefined handlers

| Handler | Since | Semantics (normative) |
|---|---|---|
| `MPI_ERRORS_ARE_FATAL` | MPI-1 | MPI-1/2/3: "causes the program to abort on all executing processes … same effect as if `MPI_ABORT` was called by the process that invoked the handler." MPI-4/4.1/5.0 restates as: "causes the program to abort **all connected MPI processes** … similar to calling `MPI_ABORT` using a communicator containing all connected processes with an implementation-specific errorcode." **Default on `MPI_COMM_WORLD`.** |
| `MPI_ERRORS_RETURN` | MPI-1 | "The handler has no effect other than returning the error code to the user." Critically: this does **not** promise the library remains usable (§1.1c). |
| `MPI_ERRORS_ABORT` | **MPI-4.0** | "invoked on a communicator in a manner similar to calling `MPI_ABORT` on that communicator. If invoked on a window or file, similar to `MPI_ABORT` on a communicator containing the group of MPI processes associated with the window or file. **If the error handler is invoked on a session, the operation aborts only the local MPI process.** In all cases, the errorcode value is implementation-specific." [mpi41, §9.3] |
| `MPI::ERRORS_THROW_EXCEPTIONS` | MPI-2 C++ | C++ binding only; deprecated in MPI-2.2 and removed in MPI-3.0. |

The MPI-4 addition of `MPI_ERRORS_ABORT` is the standard's one real concession in this area:
it narrows the *blast radius* of a fatal error from "all connected processes" to "the group
of this object", and to "just me" for sessions. It is a scoping mechanism, not a recovery
mechanism — the state of MPI is still undefined afterwards. MPI-4 also specifies that, absent
any other setting, the default is `MPI_ERRORS_RETURN` for MPI I/O functions and
`MPI_ERRORS_ABORT` for all other MPI functions in the Sessions model [openmpi_errhandler_man].

### 2.3 Error classes vs error codes

A distinction that is frequently muddled and that AgentMPI should replicate deliberately
[mpi41, §9.5 *Error Codes and Classes*]:

- **Error codes** are "left entirely to the implementation (with the exception of
  `MPI_SUCCESS`)". They are deliberately opaque and information-rich, intended to be passed
  to `MPI_Error_string` for a human-readable message.
- **Error classes** are a *small, standard, portable* set. `MPI_Error_class(errorcode,
  errorclass)` maps any implementation code onto one. The relationship is a subset relation:
  "The error classes are a subset of the error codes: an MPI function may return an error
  class number … The values defined for MPI error classes are valid MPI error codes." Each
  standard error class maps onto itself under `MPI_Error_class`.
- **Ordering invariant:** `0 = MPI_SUCCESS < MPI_ERR_... ≤ MPI_ERR_LASTCODE`.
  `MPI_SUCCESS = 0` for C-idiom compatibility; the class/code split is what makes that
  possible. `MPI_ERR_LASTCODE` gives a sanity bound [mpi41].
- **Extensibility.** Implementations and libraries may add their own: `MPI_Add_error_class`,
  `MPI_Add_error_code(errorclass, errorcode)` (associates a new code with an existing class),
  and `MPI_Add_error_string`. The values are **system-assigned** to guarantee uniqueness —
  a user may not pick a number. This is exactly the hook ULFM uses to introduce
  `MPIX_ERR_PROC_FAILED` etc. as extensions before standardisation [openmpi_ulfm_docs].
- `MPI_Error_class` "must always be thread-safe … one of the few routines that may be called
  before MPI is initialized or after MPI is finalized" [mpi41].

Design lesson for AgentMPI: **a stable, small, portable classification layer over an
open-ended, implementation-specific detail layer.** Applications branch on classes; humans
and logs read codes/strings.

### 2.4 Per-request error reporting: `MPI_Status` and `MPI_ERR_IN_STATUS`

For multi-completion calls (`MPI_Waitall`, `MPI_Waitsome`, `MPI_Testall`, `MPI_Testsome`,
and the `MPI_Wait/Testany` family), a single scalar return code cannot express "request 3 of
17 failed". MPI's answer:

- `MPI_Status` has a public field `MPI_ERROR` (alongside `MPI_SOURCE` and `MPI_TAG`).
- When a multi-completion routine has more than one failing request, the function returns the
  class **`MPI_ERR_IN_STATUS`**, and the *per-request* error codes are placed in the
  `MPI_ERROR` field of each corresponding `MPI_Status`.
- MPI does not otherwise guarantee `MPI_ERROR` is updated on successful single-completion
  calls (setting it unnecessarily costs performance), so applications must not read it
  except in the `MPI_ERR_IN_STATUS` case.

This is the mechanism ULFM extends to report `MPIX_ERR_PROC_FAILED_PENDING` on individual
wildcard receives while other requests in the same `Waitall` complete normally (§4.5).
It is the only place in MPI where failure information is *per-operation* rather than
*per-call*.

A caveat worth quoting for completeness: reduction operations (`MPI_Op` user functions) do
not return an error value, so "if the functions detect an error, all they can do is either
call `MPI_Abort` or silently skip the problem. Thus, if you change the error handler from
`MPI_ERRORS_ARE_FATAL` to something else … then no error may be indicated"
[cray_errors_abort_man].

### 2.5 `MPI_Abort`

`MPI_Abort(comm, errorcode)` makes "a best attempt to abort all tasks in the group of
`comm`". It is the only unconditional escape hatch in MPI-1. Two things about it matter for
protocol design: (i) implementations are permitted (and historically most did) to abort the
whole job regardless of the `comm` argument — the group argument is advisory in practice
[UNVERIFIED — the standard's exact latitude wording on this varies by version]; and (ii)
`MPI_ERRORS_ARE_FATAL` and `MPI_ERRORS_ABORT` are both *specified in terms of* `MPI_Abort`,
so `MPI_Abort` is the semantic primitive and the fatal handlers are sugar over it.

---

## 3. FT-MPI (Fagg & Dongarra, 2000): Modifying MPI Semantics

**Reference:** G. E. Fagg and J. J. Dongarra, "FT-MPI: Fault Tolerant MPI, Supporting Dynamic
Applications in a Dynamic World," EuroPVM/MPI 2000, LNCS 1908, pp. 346–353
[fagg2000ftmpi]; extended in G. E. Fagg, A. Bukovsky, J. J. Dongarra, "HARNESS and fault
tolerant MPI," *Parallel Computing* 27(11):1479–1495, 2001 [fagg2001harness]; a full
specification attempt appears in Fagg, Gabriel, Bosilca, Angskun, Chen, Pjesivac-Grbovic,
London, Dongarra, "Extending the MPI Specification for Process Fault Tolerance on High
Performance Computing Systems" (ISC 2004) [fagg2004extending].

FT-MPI was built inside the **HARNESS** project. It implements all of MPI-1.2, parts of
MPI-2, and *extends the semantics* of MPI so an application can recover from failed
processes [icl_ftmpi_overview]. It is the canonical instance of Gropp & Lusk's §5.3
"modifying MPI semantics" category — and their explicit target of criticism.

### 3.1 The state machine

A communicator can enter an **error state**. While in that state, "a communicator can only
recover by rebuilding it, using a modified version of one of the MPI communicator build
functions such as `MPI_Comm_{create, split, dup}`" [fagg2000ftmpi]; the later description
narrows this to a modified `MPI_Comm_dup` [fagg2003lacsi]. The *recovery* itself is a
collective act; the *mode* determines what the rebuilt communicator looks like.

FT-MPI actually exposes **four** orthogonal attributes, queryable through MPI's cached-
attribute interface [fagg2004extending]:
`FTMPI_RECOVERY_MODE` (automatic vs manual recovery),
`FTMPI_COMM_MODE` (what happens to failed processes),
`FTMPI_MSG_MODE` (what happens to in-flight point-to-point messages),
`FTMPI_COLL_MODE` (what happens to in-flight collectives), plus
`FTMPI_NUM_FAILED_PROCS` (count of failures since last recovery).
Modes are selected when the application is started.

### 3.2 Communicator modes — precise semantics

Terminology note: FT-MPI distinguishes the **extent** of a communicator (number of slots)
from the number of **valid** processes in it. `MPI_Comm_size` returns the *extent*.

| Mode | Failed processes | Ranks of survivors | `MPI_Comm_size` |
|---|---|---|---|
| `FTMPI_COMM_MODE_ABORT` | — | — | — |
| `FTMPI_COMM_MODE_BLANK` | Not replaced; blanked out | **Unchanged** | **Unchanged** (= extent) |
| `FTMPI_COMM_MODE_SHRINK` | Not replaced; removed | **Renumbered**, contiguous | **Reduced** to #survivors |
| `FTMPI_COMM_MODE_REBUILD` | **Respawned** | **Unchanged** | **Unchanged** |

- **ABORT.** "A mode which affects the application immediately an error is detected and
  forces a graceful abort. **The user is unable to trap this.** If the application needs to
  avoid this they must set all communicators to one of the above communicator modes."
  [fagg2003lacsi] Provided for backward compatibility with MPI-1/MPI-2 behaviour.
- **BLANK.** Failed processes are not replaced; the size of `MPI_COMM_WORLD` is unchanged;
  "the failed processes are blanked out and **treated similarly to `MPI_PROC_NULL`**"
  [fagg2004extending]. Crucially: "the communicator can now contain gaps to be filled in
  later. **Communicating with a gap will cause an invalid rank error.** Note also that
  calling `MPI_COMM_SIZE` will return the extent of the communicator, not the number of
  valid processes within it" [fagg2003lacsi]. Surviving processes keep their ranks. This is
  the mode Gropp & Lusk's `MPE_Process_array` sketch was "reminiscent of" [gropp2004ft].
- **SHRINK.** "The communicator is reduced so that the data structure is contiguous. **The
  ranks of the processes are changed, forcing the application to recall `MPI_COMM_RANK`**"
  [fagg2003lacsi]. This is the semantics ULFM later adopts for `MPIX_Comm_shrink` — the
  difference being that ULFM produces a *new* communicator object rather than mutating the
  existing one. FT-MPI's rationale is instructive: "it is best to think of processes having a
  unique process ID. Thus, a communication always occurs between pairs of processes. The rank
  … is in this case just the result of a mapping between process ID and the position of the
  process in the process sequence" [fagg2004extending]. Rank becomes a *view*, identity is
  the process ID.
- **REBUILD.** The default and, per the FT-MPI project page, "the best tested mode"
  [icl_ftmpi_overview]. "Most complex mode that forces the creation of new processes to fill
  any gaps until the size is the same as the extent. The new processes can either be placed
  into the empty ranks, or the communicator can be shrank and the remaining processes filled
  at the end. **This is used for applications that require a certain size to execute as in
  power of two FFT solvers**" [fagg2003lacsi]. Surviving processes retain their rank in
  `MPI_COMM_WORLD`. "No assumptions are made within the FT-MPI specification where the new
  processes are placed" [fagg2004extending]. Note the motivation: some algorithms are
  *size-rigid*, so shrinking is not an option and replacement is mandatory.

### 3.3 Message modes — precise semantics

The formal definition uses a **generation count** for communicators: if `MPI_COMM_WORLD` has
generation count *x* before a failure, it has generation count *y > x* after recovery. Users
never see the generation count; it exists to define matching [fagg2004extending].

- **`FTMPI_MSG_MODE_RESET`** (also written NOP / NOOP in the earlier papers). "A message sent
  from process *a* to process *b* using a communicator with generation count *x* cannot be
  received with any communicator having generation count *y*, **even if the processes *a* and
  *b* are both surviving processes**" [fagg2004extending]. That is: the generation count
  participates in message matching, so recovery flushes the entire in-flight message space.
  "All ongoing messages are dropped. The assumption behind this mode is that on error the
  application returns to its last consistent state, and all currently ongoing operations are
  not of any interest" [icl_ftmpi_overview]. The worked example: an error at iteration 432
  rolls back to the iteration-400 checkpoint, so "any message from iteration 432 would
  disturb and be misplaced" [fagg2004extending]. The earlier NOP framing adds the operational
  effect: "No operations on error. I.e. no user level message operations are allowed and all
  simply return an error code. This is used to allow an application to return from any point
  in the code to a state where it can take appropriate action as soon as possible"
  [fagg2000ftmpi]. **RESET is the checkpoint/rollback-friendly mode.**
- **`FTMPI_MSG_MODE_CONT`** (CONTINUE). "The generation count is **not** used for message
  matching. Thus, a message sent from process *a* to process *b* before a failure occurred
  will be delivered after the recovery operation. **All operations which returned
  `MPI_SUCCESS` to a non-failing process will be finished successfully after recovery**"
  [fagg2004extending]. Operationally: "All communication that is NOT to the affected/failed
  node can continue as normal. Attempts to communicate with a failed node will return errors
  until the communicator state is reset" [fagg2003lacsi]. A blocking send that returned
  `MPI_SUCCESS` will deliver its data even if a failure occurs before the data reaches the
  destination. **CONT is the "survive in place, keep your messages" mode.**

Three consequences of CONT the spec is careful about:

1. *Advice to users*: "If an application would like to receive a message which has been
   initiated before an error occurred after the recovery operation, it has to reconstruct the
   communicators in the very same order like previously" [fagg2004extending].
2. *Advice to implementors*: "An MPI implementation has to ensure that two sequences creating
   communicators in an identical manner in different generation counts will produce the same
   communicator/context IDs" [fagg2004extending]. Context-ID determinism is a hard
   requirement of the CONT mode.
3. **`MPI_ANY_SOURCE` is dangerous under CONT.** "Difficulties can arise … if the application
   has a non-deterministic communication behaviour, e.g. through the usage of
   `MPI_ANY_SOURCE`. It is the responsibility of the application developer to avoid deadlocks
   in this case, since the MPI library cannot recognize and cancel operations as long as it
   cannot determine the destination/source process." Concretely: *a* posts a non-blocking
   receive from `ANY_SOURCE`, *b* fails, and if no other process sends a matching message,
   the application deadlocks. "*Advice to users:* The usage of `MPI_ANY_SOURCE` should be
   avoided to the greatest possible extent when using the message mode
   `FTMPI_MSG_MODE_CONT`" [fagg2004extending]. **ULFM's entire wildcard-acknowledgement
   design (§4.5) exists to solve exactly this problem properly.**

Implementation status note: in the released FT-MPI, "SHRINK mode is fully supported when used
with the CONT message mode" [icl_ftmpi_overview]. New faults are surfaced to the user via the
return code `MPI_ERR_OTHER`, with details available through MPI's cached-attribute interface
[fagg2000ftmpi] — a workaround for not being able to add standard error classes.

### 3.4 Why FT-MPI did not become the standard

Gropp & Lusk's objection (§1.3) is the historically decisive one: mutating rank and size in
place breaks the invariants applications were written against. FT-MPI's SHRINK mode requires
re-calling `MPI_COMM_RANK` and re-deriving every data decomposition; BLANK mode makes
`MPI_Comm_size` mean something different from "number of live peers". ULFM's central design
move is to keep MPI communicators immutable and put the mutation in a *new* communicator
returned by `shrink` — Gropp & Lusk's §5.4 "extend, don't modify" position, applied.

---
