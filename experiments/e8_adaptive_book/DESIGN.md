# E8: an adaptive population — design

E7 translated the book in phases separated by barriers: every rank surveyed,
then every rank waited for the census; every rank translated its segment, then
waited at the seam exchange for its neighbours; every rank waited for the
slowest model at every boundary. At p = 16 that idle time was 68% of rank-time.
E8 asks how much of it a different harness recovers on the same protocol, and
what the protocol has to offer for the harness to be writable.

## The question

Sixteen ranks over two machines, each with a *home block* of about six pages.
No rank waits for another after the glossary is bound: a rank that finishes a
page claims the next, from its own block first and then from whichever block has
the most pages left; a rank that settles a new term publishes it at once, and
every later translation, anyone's, reads it; a seam between two pages becomes
work the moment both pages exist, for whichever rank is free. The run ends when
the pool is drained, not when the slowest rank has finished its share.

Measured against E7 at p = 16 (`runs/e7-rawapi-p16`): wall, blocked rank-hours,
coordination share, pages per rank (the balance the pool buys), and what the
extra communication cost.

## What is protocol and what is harness

The protocol is general-purpose and knows nothing about books. The harness
knows everything about books and nothing about devices, leases or replay.

| concern | where | what |
|---|---|---|
| exclusive claim of a work item, reclaim from a dead holder, dependency gating, termination | **protocol** `ampi/core/pool.py`, spec S9.5 | `pool_create`, `pool_add`, `pool_next`, `pool_done`, `pool_release`, `pool_status`, `pool_wait_drained` |
| a root publishing without waiting for its receivers | **protocol** `ampi/core/collectives.py`, spec S7.4 | `ibcast` returning a request; `test`/`wait` complete it |
| one-sided publication of shared state, atomic union, versions, provenance | **protocol** (existing S9) | `put`, `get`, `accumulate`, `win_ls` |
| what a work item is (a page, a seam), which items exist, their order and their dependencies | **harness** | seeds the pool with pages, adds seams as pages complete |
| which glossary terms a page needs, how amendments are merged, what a seam revision is | **harness** | prompts and contracts, unchanged from E7 where possible |
| when a rank prefers its home block and when it steals | **harness** | `prefer=` on `pool_next` |
| model choice, fault injection, budgets, replay of finished work | **harness** (memo window) | as in E7 |

The pool is protocol because every element of it is a general obligation with
no book in it: exclusivity (S9.2's claim), the dead holder (S10's failure
model), the dependency (a task graph), the end (a distributed termination
condition every bag-of-tasks program needs and every one of them gets wrong on
its own). The nonblocking broadcast is MPI_Ibcast. Nothing else was needed.

## The protocol additions

**S9.5 Work pools.** A pool is a window with three prefixes: `item/` (items
added after creation), `claim/` and `done/`. Members call `pool_create` with the
same seed list, as they call `comm_create` with the same group; seeds are not
written. `pool_next` lists the window without bodies, picks the first item
that is not done, not claimed by a live holder, and whose dependencies are done,
in (ascending priority, preferred group, id) order, and claims it by
compare-and-swap from absence; a claim held by a rank the detector has convicted, or by an earlier
epoch of its holder, is taken over by compare-and-swap on the claim record and
traced as `pool.reclaim`. `pool_done` writes the result cell; `pool_release`
removes a claim the holder cannot honour. `pool_wait_drained` polls with the
same progress obligations as a collective wait (touch, detect failures) until
every known item is done. Every step is one read of the key list and one
conditional write, so a pool costs the population one round trip per claim.

**S7.4 Nonblocking collectives.** `ibcast(label, payload, root)` at the root
deposits the body and returns a request; at a member it registers the arrival
and returns a request. `test` reports whether the root's body is present;
`wait` delivers it with the same view and ledger rules as `bcast`. The root's
request is complete on return. A rank may hold several outstanding requests.

## The harness

```
init; commission (bcast, tiny)
survey my home block                                   one call
census allreduce; parallel arbitration                 as E7
root: glossary ibcast, then straight to the pool       nobody waits for the root's receivers
loop:
    item = pool_next("book", prefer=my block, wait=True)
    page  -> read current amendments for its chapter; translate; put seg/<page>;
             accumulate new terms into the amendment window (union); pool_done;
             for each finished neighbour page: pool_add(seam(k, k+1), deps=[k, k+1])
    seam  -> read both pages' edges; revise; put; pool_done
    until pool_wait_drained
spend allreduce; gather manifest to root; finalize
```

Idle time is explicit: the time a rank spends inside `pool_next(wait=True)`
with nothing available is traced as `pool.wait`, and the analysis reports it
per rank beside the blocked time of E7's collectives.

## What would falsify the design

If the pool's round trips cost more than the barriers they replace, wall and
coordination share both rise. If page-level translation loses the segment's
context, quality (seam revisions, missing paragraphs) falls. If stealing breaks
the seam logic, coverage falls. Each is a number in the run's README.
