# AgentMPI 0.1 — Draft Protocol Specification

This document is the language-independent specification. The Python package
`agentmpi` and the `python -m agentmpi` CLI are bindings, not the protocol.
A conforming implementation may use any transport that preserves the
matching, progress, and failure rules below.

## 1. Goals

Copied from the MPI Forum's 1994 goals and rewritten for executors that
think, forget, and die:

| MPI 1.0 goal | AgentMPI 0.1 goal |
|---|---|
| Application programming interface, not a compiler | Interface for harness authors, not a framework or runtime product |
| Efficient communication; overlap compute and transfer | Eager/rendezvous split so large artifacts never enter a prompt |
| Heterogeneous machines | Heterogeneous executors (models, tools, languages, humans) |
| C and Fortran bindings | Python API + JSON/CLI binding (any language can speak the wire format) |
| Reliable communication assumed | **Unreliable executors first-class**; fabric is reliable, ranks are not |
| Close to existing practice (PVM, NX, p4) | Close to MPI so HPC intuition transfers |
| Implementable on many vendors | Implementable on files, sockets, object stores, agent runtimes |
| Language-independent semantics | Same |
| Thread-safe | Concurrent-safe (atomic rename, directory locks) |

AgentMPI is a *protocol people use to write multi-agent systems*, the way
MPI is a protocol people use to write parallel programs. It is not itself
a multi-agent system.

## 2. Execution model

A **job** is an SPMD program: the same harness text runs on every **rank**.
Ranks are integers `0 .. p-1` inside a **communicator**. A communicator is
the pair *(group, context)*:

- **group** — ordered set of executors
- **context** — a name that is part of every envelope, so two harnesses
  sharing a group cannot match each other's messages (MPI's hidden context id)

`COMM_WORLD` is the communicator created by the launcher. `comm_split`,
`spawn`, and `shrink` create new communicators.

An executor has a **lifecycle**: `uninitialized → init → active ⇄ suspended → finalized`,
with a side transition `→ failed` from any non-final state.

## 3. Point-to-point

### 3.1 Envelope and matching

Every message carries an envelope `(source, tag, communicator, cid)`. A posted
receive with `(source, tag)` matches the oldest unmatched message such that:

- `source` equals the envelope source, or `source == ANY_SOURCE`
- `tag` equals the envelope tag, or `tag == ANY_TAG`
- the communicator names are equal

This is MPI matching. Tags are integers in `0 .. 32767` for users; the
runtime reserves higher tags for collectives, heartbeats, and rendezvous.

### 3.2 Eager and rendezvous

Let `E` be the eager threshold (default 8192 bytes).

- **Eager** (`nbytes ≤ E`): payload travels in the mailbox file. Analog of
  MPI eager: low latency, consumes receiver buffer (here: context tokens).
- **Rendezvous** (`nbytes > E`): mailbox file holds only the envelope and an
  artifact path. The receiver materializes the artifact on match. Analog of
  MPI rendezvous: the data stays out of band until the receive is posted,
  which is how AgentMPI avoids blowing a rank's context window.

### 3.3 Progress

A blocking `recv` returns when a matching message is consumed, the
communicator is revoked, a watched source is declared dead, or a timeout
fires. Implementations must make independent progress: posting a send on
rank *i* cannot require rank *j* to call into the library except to receive.

The filesystem binding satisfies this by write-then-rename into the
destination mailbox.

## 4. Collectives

All ranks in a communicator (or its live subset, for resilient variants)
must invoke the same collective in the same order. Collectives are
identified by a per-communicator call id `cid`, not by tags the user sees.

| Call | MPI analog | Algorithm in this implementation | Agent reading |
|---|---|---|---|
| `barrier` | `MPI_Barrier` | Bruck / distance doubling | Sync checkpoint |
| `bcast` | `MPI_Bcast` | Binomial tree | Share a plan, rubric, or style guide |
| `scatter` | `MPI_Scatter` | Binomial subtree slices | Partition work |
| `gather` | `MPI_Gather` | Binomial subtree concat | Collect results |
| `reduce` | `MPI_Reduce` | Binomial combine-on-path | Hierarchical synthesis |
| `allreduce` | `MPI_Allreduce` | Recursive doubling (p=2^k); else reduce+bcast | Consensus / global counters |
| `allgather` | `MPI_Allgather` | Bruck map-union | Everyone sees every partial |
| `alltoall` | `MPI_Alltoall` | Pairwise exchange | Personalized exchange (reviews, diffs) |
| `scan` | `MPI_Scan` | Gather + prefix + scatter | Rank-ordered accumulation |

Reduction operators: `SUM, PROD, MIN, MAX, LAND, LOR, BAND, BOR` (MPI)
plus `CONCAT, MERGE, SYNTHESIZE` (agent-native). `SYNTHESIZE` is an
associative stitch of text; a harness may pass a callable to plug in an
LLM at each internal tree node.

Complexity (α = per-message overhead, β = per-byte, n = payload, p = size):

- Binomial bcast/reduce: `T = ⌈log2 p⌉ (α + nβ)`
- Recursive-doubling allreduce: `T = log2 p (α + nβ)` when p is a power of two
- Bruck allgather: `T = ⌈log2 p⌉ (α + O(n)β)`
- Pairwise alltoall: `T = (p-1)(α + nβ)`

## 5. One-sided communication and locks

`win_create`, `put`, `get`, `win_lock`, `win_unlock` are the MPI-2 RMA
passive-target analog. A **window** is a named shared artifact (design
document, source file, blackboard). Exclusive locks are POSIX directory
creates; they are the programmable form of "don't let two agents edit
the same file."

Shared locks exist for readers. Mixing fence-style epochs with locks is
undefined, as in MPI.

## 6. Context windows (OOM)

Each rank has a **context budget** in estimated tokens (`len/4`). Incoming
messages are charged against the budget. Exceeding it raises
`ContextBudgetExceeded` — the agent analog of `malloc` failure.

`context_compact(summary)` replaces accumulated charge with the cost of a
summary (paging). `context_put` / `context_get` publish summaries into a
shared window so other ranks can recover information without replaying
transcripts. This is the protocol-level answer to "executors cannot share
info" and "avoid OOM."

## 7. Fault model (ULFM analog)

The fabric is reliable. Executors are not.

- Each rank writes a heartbeat status file.
- `probe_failures` declares a rank dead if its state is `failed` or its
  heartbeat is older than `failure_timeout`.
- A blocking receive whose source is dead raises `DeadRankError` instead
  of hanging (the usual multi-agent failure).
- `revoke` marks the communicator unusable (MPI_COMM_REVOKE).
- `agree` is allgather-of-votes over the current world; disagreement is
  reported, not hidden.
- `shrink` intersects per-rank liveness views and creates a new
  communicator on the survivors, remapped to `0 .. p'-1` (MPI_COMM_SHRINK).

Recovery policy is the harness's job, as in ULFM. The protocol only makes
failure *visible and programmable*.

## 8. Sessions, split, spawn

- `comm_split(color, key)` — specialist teams (architects vs. workers).
- `spawn(n)` — advertise new ranks (MPI_Comm_spawn); a launcher fills them.
- Lifecycle `suspended` — an executor is compacting context or waiting on
  a rendezvous; it is not dead.

MPI-4 sessions are the long-term model: an executor should be able to
join a communicator without a world-sized `Init`. The filesystem binding
already allows this via `attach()`.

## 9. Wire format (filesystem binding)

```
$AMPI_HOME/
  job.json
  comms/<name>/
    meta.json
    ranks/<rank>.json          # heartbeat
    mailboxes/<rank>/<ts>_<src>_<tag>_<id>.msg
    artifacts/<id>.json        # rendezvous payloads
    windows/<name>.json
    locks/<name>.exclusive/    # directory lock
    logs/events.jsonl
    shrink/                    # liveness views
```

Message file:

```json
{
  "envelope": {
    "protocol": "agentmpi/1.0",
    "kind": "p2p",
    "src": 2, "dst": 5, "tag": 17,
    "comm": "world", "cid": 0,
    "msg_id": "...", "ts": 0.0,
    "nbytes": 120, "eager": true,
    "artifact": null, "tokens": 30
  },
  "payload": {}
}
```

Delivery is `write tempfile; fsync; rename`, which is atomic on POSIX.

## 10. Bindings

- Python: `from agentmpi import Init, Finalize, COMM_WORLD, Op`
- CLI: `python -m agentmpi --home $AMPI_HOME --rank $R --size $P <verb>`
- Launcher: `ampi-run -n <P> -- python harness.py`

A Cursor subagent is a rank. It is launched with `AMPI_HOME`, `AMPI_RANK`,
`AMPI_SIZE` and speaks through the CLI or the Python API. The harness
author, not the protocol, decides what the rank *thinks*.
