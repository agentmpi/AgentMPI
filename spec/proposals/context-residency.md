# Context residency: splitting the ledger from the resident set

**Status.** Implemented and normative as of S6.1, S6.2 and Appendix A. This note
is kept as the argument that produced them; where it and the specification differ,
the specification is authoritative.

One departure was made in implementing it, and it is recorded here rather than
quietly. The note proposed that the resident figure inherit the degradation's
"written at once" rule. Taken literally that would publish on every admission,
which is every delivery --- exactly the write-per-read that cost a 128-rank
population half an hour between the research fence and the next reduction. What
was implemented instead: admissions ride the deferred charge, and *evictions* and
*releases* are written at once, since those are the deliberate, rare events that
change what a rank can accept. The rule the note was reaching for holds; the
event it attaches to is the reduction rather than the admission.

Implementing it also found a defect the note did not predict. Because a rank's
own reads fold in its deferred charge, a release or an eviction issued straight
after a delivery was silently undone by the next write that carried the charge
forward; it survived only when an unrelated write had flushed the charge first.
S6.1 now requires a reduction to be authoritative over a deferred charge, and
there is a conformance test for it on every transport.

## The question

S6.1 makes the context ledger cumulative and says why: `used` is "cumulative, not a
high-water mark of live data, because that is what an executor's window actually
is: a transcript that only grows". Appendix A then records the ledger as having
**no MPI analogue**.

Both are right about accounting and neither settles admission control. A real
executor's window can be classified by where each token came from, and it can be
reduced, by dropping material or by replacing it with a summary. If that is true,
then one counter is doing two jobs: saying honestly what a rank has consumed, and
deciding what the next model call may carry. This note argues for separating them,
and observes that most of the machinery is already here.

## Three findings in the current implementation

**1. The category of every charge is computed and then thrown away.**
`Runtime.charge(tokens, *, what="")` in `ampi/core/base.py` takes a label naming
the operation being charged, and the call sites in `ampi/core/collectives.py`
supply `bcast`, `scatter`, `reduce`, `manifest`, `envelope` and `alltoall`. That
label survives in exactly one case: when the delivery must be degraded, it is
written into the `ctx.degrade` trace. On every successful delivery it is
discarded. So the runtime already knows the provenance of every token it charges
and keeps it only when something goes wrong. Per-category accounting is close to
free, and a harness could supply finer labels for tool results and model output.

**2. Cumulative and resident both already exist, and never meet.**
`Ledger.used` in `ampi/core/context.py` is cumulative intake. In the same module,
`ResidencyModel` computes the peak data resident in one rank for each collective
algorithm, and its docstring is the argument for this proposal: an algorithm can
be *infeasible* "because the peak data resident in one rank exceeds a context
window that cannot be enlarged", so "selection must therefore be an admissibility
test before it is an optimisation". But `ResidencyModel` is a static analysis
consulted when choosing an algorithm. Nothing in the running system represents
what is resident *now*.

**3. The backing store already exists, which is what makes eviction different here.**
Payloads are content-addressed with handles (`put_payload`, `get_body`), and views
give bounded projections. Dropping a body from a rank's resident set therefore
need not be forgetting: the handle still resolves, and the rank can re-materialise
the body or take a view of it later. A chat agent has only summarisation because
it has nowhere to put what it drops. AgentMPI has somewhere to put it.

## The proposed shape

A two-level memory hierarchy, of which the ledger is the accounting layer only.

| role | structure | property |
|---|---|---|
| accounting | `Ledger.used` | cumulative, honest, decreases only by explicit `ctx_release` |
| resident set | new | live, reducible, exactly what the next model call will carry |
| backing store | object store plus handles | exists |
| projection | views | exists |
| page table | degradation records | exists; extend to record evictions |

`used` does not change meaning. The reducible quantity is the resident set, and
the two answer different questions: what did this rank spend, and what will the
next call cost.

## Four cautions

**Prefix caching makes middle-eviction expensive in a way a token counter cannot
see.** Providers cache the key-value state of a prompt prefix, and editing
anything invalidates the cache from that point on. Freeing fifty thousand tokens
of budget while forcing a full cache miss on every later call is often the worse
trade. This argues for evicting at the tail or at segment boundaries, and for
ordering immutable shared material first. Note that the E7 commission is
byte-identical across every rank, so it is a natural shared prefix.

**Summarisation breaks replay unless the summary is stored.** The runtime has
durable replay: a returning rank replays finished work from the memo window rather
than recomputing it. A summary is a model call, so it is lossy, costly and not
reproducible. If a resident set is the product of one, a replayed rank is not the
same rank. The remedy is already available: write the summary to the object store,
and the transformation becomes content-addressed and replayable.

**The two numbers have different publication rules, and the code already says so.**
`Runtime.charge` keeps the charge local and defers it to the next write that
happens for another reason, with the comment explaining why: writing it eagerly
made every read a device mutation, and at 128 ranks over the git device a rank
reading forty-eight findings spent ten minutes in forty-eight group commits. A
degradation, by contrast, is written at once, "because a peer deciding how much to
send should see it". Under this proposal the resident figure inherits the second
rule and the cumulative ledger keeps the first. That sharpens the existing rule
rather than contradicting it.

**Eviction is device-aware.** Re-materialising costs a round trip. On the sqlite
device that is cheap; on the git daemon it is the expensive operation, as the
push-contest failure of 5 September showed. A policy that is right on one device
will be wrong on the other, so residency policy belongs with the device profile.

## What this would change

* **S6.1** keeps `used` as it is and gains a resident quantity with its own
  publication rule.
* **S6.2's** eager-versus-rendezvous decision consults residency rather than
  remaining budget alone. The hook is already there: an implementation "MAY also
  choose rendezvous for a payload under the threshold when the receiver's
  remaining budget is small".
* **Appendix A** gains a row. The ledger still has no MPI analogue, but the
  resident set is the unexpected-message buffer's occupancy, and the table already
  records the receiver context window as transferring fully from that buffer. In
  MPI, occupancy is the flow-control quantity and bytes-received is a statistic.
  AgentMPI collapsed the two into one counter; splitting them restores the
  correspondence rather than departing from it.
* **Appendix B** is not contradicted. It omits an *automatic context compaction
  policy*, and this proposal does not ask for one. Eviction against a backing
  store is not compaction: nothing is summarised, nothing is lost, and the
  discarded body remains addressable.

## Why the ledger must stay cumulative regardless

The `Ledger` docstring gives the reason and it should survive any change here: "a
ledger that can be silently zeroed measures nothing". A rank that reads a
4000-token document, is told to forget it, and reads it again has spent 8000
tokens, and the coordination-cost numbers in the run reports are only honest
because the counter says so. Nothing in this proposal makes the ledger reducible.
It makes the *other* number reducible, and gives it a name.

## Provenance

Written from a read of the E6, E7 and E8 production runs of 5 September 2026 by an
observing session that did not run them. The two-structure framing, one honest
record of everything received and one working set of what is actually sent next,
came from the reader of those traces rather than from the runs. Nothing here has
been implemented or tested.
