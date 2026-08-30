# AgentMPI

**A message-passing interface for multi-agent harnesses.**

AgentMPI is a *protocol*, not a framework. It specifies how independently
written agent processes name each other, exchange messages, synchronise,
share state, and survive each other's failures — and then gets out of the
way. What the agents do, which models they run, and how they are prompted are
none of the protocol's business, exactly as MPI has no opinion about what
your ranks compute.

The design question this repository answers is: *how much of MPI transfers to
multi-agent LLM systems, and what has to change?* The answer is that most of
it transfers, and that the four things that do not are interesting enough to
be the subject of [a paper](paper/main.tex).

---

## Why

Multi-agent systems today are written the way parallel programs were written
before 1994. Every framework bundles its own orchestration policy, its own
communication mechanism and its own failure handling, and none of them
compose. The agent-interoperability protocols that have appeared since
2024 — MCP, A2A, ACP — standardise how *two* parties exchange a request and a
response. None of them provides what a parallel program needs: group
membership, collective operations, a consistency model for shared state, or
defined semantics when a participant dies.

That gap has a measurable cost. The largest empirical study of multi-agent
LLM failures finds that most failures are not reasoning failures but
coordination failures — agents that disobey a specification, lose track of
what a peer decided, drop a step because they never learned another agent had
finished, or terminate early. Those are, respectively, contract violation,
cache coherence, synchronisation and termination detection. A field that has
been solving them for forty years has names for all of them.

## Anything that can run a shell command is a rank

This is the load-bearing design decision. A coding agent cannot be handed a
Python object or a socket, but every one of them can run a shell command.

```bash
# rank 3 of a 13-rank translation job — a complete rank program
export AMPI_ROOT=/runs/book AMPI_RANK=3

ampi bcast   --root 0 --out spec.md              # receive the task spec
ampi scatter --root 0 --type json --out chunk.json   # receive my chunk
# ... read the chunk, decide terminology, write proposal.json ...
ampi scan --exclusive --op ampi_first \
          --file proposal.json --type json --out prefix.json
# ... translate, adopting every rendering in prefix.json ...
ampi gather  --root 0 --file result.json --type json
ampi progress
```

Every command blocks until it completes, which is what an agent wants:
`ampi recv` returns when the message arrives, and until then the agent is
waiting at a shell prompt. Errors are structured JSON on stderr with a
machine-readable `error` field, so an agent can branch on `ERR_TIMEOUT`
versus `ERR_REVOKED` without parsing prose.

A rank implemented by a coding agent, a shell script and a Python program are
indistinguishable on the wire and interoperate in one job.

## What is in the protocol

| Area | Operations |
|---|---|
| Identity | `Init`, `Finalize`, `Comm_rank/size`, `Comm_dup/split/split_type`, `Intercomm_create`, group algebra |
| Types | `Type_contract` (checkable schema), `Type_bounded` (token bound + digest), `Type_struct`, `Type_contiguous` |
| Point to point | `Send`/`Recv`/`Isend`/`Irecv`, `Ssend` (completes on **ingestion**), `Bsend`, `Mprobe`/`Mrecv`, partitioned sends, `Waitsome` |
| Collectives | `Barrier`, `Bcast` (with optional refracting relay), `Scatter(v)`, `Gather(v)`, `Allgather`, `Reduce`, `Allreduce`, `Reduce_scatter`, `Scan`/`Exscan`, `Alltoall`, neighbourhood collectives |
| One sided | `Win_create`, `Put`, `Get` (returns a **reference**), `Accumulate`, `Fetch_and_op`, `Compare_and_swap`, `Win_query` (bounded retrieval read), fence / PSCW / leased locks |
| I/O | `File_write_at_all` (two-phase aggregation), file views, `File_read_all` |
| Faults | `Comm_revoke`, `Comm_shrink`, `Comm_agree`, `Comm_replace`, `Checkpoint`/`Restore`, supervision policies |
| Tools | `PAMPI_*` profiling interface, `AMPI_T` control and performance variables |

## The four things MPI gets wrong for agents

**Context is consumed, not occupied.** An MPI rank can receive a terabyte
while never holding more than a megabyte. Every token an agent reads stays in
its context forever, so pressure is a function of *cumulative* ingest.
Datatypes therefore carry token bounds, receives are subject to admission
control, and collective selection asks *is this feasible* before *is this
fast*. One consequence took us two attempts: a reduction tree must be planned
against the root's cumulative ingest, `(k-1)·d·m`, not its per-round ingest,
because an agent cannot free a buffer.

**Reduction operators are semantic.** `MPI_SUM` is associative, commutative
and size-preserving; "merge these two drafts" is none of the three. Operators
declare their algebra and the runtime refuses schedules the algebra does not
license. The sharpest instance: a prefix scan whose purpose is *agreement*
needs a **non-commutative** operator, because a commutative merge cannot
express precedence and leaves participants disagreeing about what they
agreed. Using `AMPI_UNION` where `AMPI_FIRST` was needed cost 18 points of
quality in our own experiment.

**Failure is not fail-stop.** Agents crash, but they also stall while looking
perfectly alive, return confidently malformed output, and run out of money.
The failure detector separates liveness from progress, contract violation is
a transport-level error class, and recovery prefers *replacing* a rank over
shrinking the communicator, because an agent's state is a prompt and a
checkpoint rather than gigabytes of application memory.

**The cost model inverts.** A message is a file write; a turn is tens of
seconds and real money. Latency-optimal collective algorithms therefore win
everywhere, and the HPC crossover where bandwidth-optimal ring algorithms
overtake logarithmic ones simply does not appear.

## Results

From `experiments/results/`, all generated by scripts in this repository.

| Measurement | Result |
|---|---|
| Exclusive scan, 128 ranks | 7 rounds vs 127 for a sequential chain |
| Scan wall clock, 64 ranks | 166× faster than the chain |
| Allreduce, 32 ranks | recursive doubling 5 rounds vs ring 31 |
| Allgather feasibility, 12k budget | infeasible beyond 8 ranks; capacity-aware tree fits to 128 |
| Capacity model | measured ingest within the predicted bound at every size |
| **Book translation, 12 agent ranks** | terminology consistency **0.909 with `Exscan` vs 0.500 without** |
| Collective overhead in that run | 0.14 s of 3051 s — one part in twenty thousand |

The translation experiment is a genuine multi-agent run: twelve agents each
translating a chapter of *Alice's Adventures in Wonderland* into French,
coordinating terminology through an exclusive parallel-prefix scan. The
metric is model-free — do the chapters that mention the Mock Turtle call it
the same thing?

## Getting started

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,tokens,plots]"
export PATH="$PWD/bin:$PATH"

# a four-rank job, entirely from the shell
ampi init --root /tmp/demo --ranks 4
AMPI_ROOT=/tmp/demo AMPI_RANK=0 ampi send --dest 1 --tag 1 --text "hello"
AMPI_ROOT=/tmp/demo AMPI_RANK=1 ampi recv --source 0 --tag 1

# or from Python
.venv/bin/python -c "
import agentmpi as ampi
from agentmpi import sim
def body(comm):
    return comm.exscan({f'k{comm.rank}': [comm.rank]}, ampi.FIRST, timeout=30)
print(sim.run(8, body).ordered())
"
```

## Reproducing the experiments

```bash
make test           # 267 tests
make microbench     # collective scaling to 128 ranks
make faults         # fault injection: detect, revoke, shrink, recover
make corpus         # build the translation corpus from Project Gutenberg
make paper          # build paper/main.pdf
make viewer         # trace viewer at http://127.0.0.1:43917
```

The agent experiments need a launcher that can start agents. `make
translation-setup` creates the run directory and writes one prompt per rank
to `runs/<name>/prompts/`; how those prompts become running agents is the
launcher's business, exactly as MPI leaves process launch to `mpiexec`. Every
run's protocol flow can be validated end to end without spending anything by
substituting `experiments/translation/stub_worker.py`, which issues the
identical sequence of `ampi` commands.

## Repository layout

```
src/agentmpi/        the protocol runtime
  transport/         device interface: shared directory, in-process
  matching.py        posted-receive and unexpected queues, ordering, epochs
  comm.py            communicators, contexts, point to point
  collectives.py     collective algorithms and the selection function
  ops.py             reduction operators and their declared algebra
  context.py         token budgets, admission control, capacity planning
  win.py             one-sided windows, the shared blackboard
  ft.py              revoke, shrink, agree, replace, checkpoints, supervision
  cli.py             the `ampi` command-line binding
  sim.py             in-process multi-rank driver for tests and benchmarks
experiments/         corpus, harnesses, microbenchmarks, fault injection
viewer/              trace viewer (Vite + React)
paper/               the paper and its bibliography
research/            the MPI research dossiers this work is grounded in
tests/               267 tests
```

## Trace viewer

`ampi trace export` emits a JSONL event stream in the same shape MPI tools
have consumed for twenty years — states with durations, arrows pairing sends
with receives, counters — plus token counts on every event. The viewer under
`viewer/` renders a Gantt timeline, a communication matrix, a per-operation
breakdown, and a *context pressure* curve that has no counterpart in a
conventional profiler: it only ever rises, because context is spent rather
than occupied, and where it meets the budget the rank stops.

## Status and honesty

This is a research prototype. The agent runs are at 12 ranks and the
microbenchmarks at 128 with scripted executors; we do not claim agent
*quality* holds at 128. Two ranks in one run required manual completion of
protocol steps they had skipped, which we report in the paper rather than
hide. The failure detector is weak for ranks that cannot heartbeat while
they think. See §8 of the paper for the full list.

## License

Apache-2.0. The translation corpus is *Alice's Adventures in Wonderland* by
Lewis Carroll, Project Gutenberg #11, public domain.
