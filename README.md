# AgentMPI

**A message passing interface for multi-agent harness development.**

AgentMPI is not a multi-agent system. It is the layer you write multi-agent
systems *with*, in the sense that MPI is the layer you write parallel programs
with: a small set of composable primitives — communicators, point-to-point
transfer with typed contracts, collectives with explicit algorithms, one-sided
shared state with epochs and locks, and failure mitigation — plus a runtime that
keeps all protocol state durably outside the agents.

- **Specification:** [`docs/spec/agentmpi-spec.md`](docs/spec/agentmpi-spec.md)
- **Paper:** [`paper/agentmpi.tex`](paper/agentmpi.tex) (build with `make paper`)
- **Background research:** [`docs/research/`](docs/research/) — dense, cited memos
  on MPI's history and design philosophy, its collective algorithms and cost
  models, its one-sided and fault-tolerance chapters, and the current multi-agent
  landscape.

## Why

In 1993 there was roughly one message-passing library per parallel machine, and a
program written for one had to be rewritten for the next. The MPI Forum's answer
was a specification rather than a better library, and two decades of scientific
software rest on it.

Multi-agent LLM systems are at the same point. Every framework supplies its own
coordination model, and a harness written against one cannot be moved to another
or composed with another. The protocols that exist solve a different problem: MCP
standardises agent-to-*tool* access, A2A standardises task delegation *across
organisations*. Neither gives the author of a multi-agent *program* a way to name a
subset of the population and talk to it privately, a collective operation, a shared
region with defined concurrency semantics, or a defined behaviour when a
participant disappears.

Those are the problems a communication protocol exists to solve, and in 1993 the
parallel-computing community had all of them too.

## The three things that had to change

MPI's abstractions transfer, with three modifications that are the technical
substance:

1. **Context is the scarce buffer, so transport mode is semantic.** MPI chooses
   between eager and rendezvous transfer invisibly, because both deliver the same
   bytes. For an agent they do not: an eager payload enters the agent's context
   window and a rendezvous envelope does not. Making the choice explicit turns
   MPI's *safe program* discipline into a precise notion of **context safety** —
   and a ring exchange that stalls above a payload threshold under eager transport
   completes at every size under rendezvous.

2. **Reduction is lossy in depth, so algorithm choice is a quality decision.** A
   reduction operator implemented by a language model is not associative, and its
   loss compounds with the *depth* of the reduction tree rather than the number of
   applications. Operators therefore declare their algebraic strength, the runtime
   refuses a tree for a non-associative operator, and every reduction reports its
   fold depth. Because a binomial tree has depth `log2 p` where a serial chain has
   `p-1`, the tree wins on fidelity *and* latency — but only because broadcasts
   forward immutable content-addressed handles, so relaying cannot corrupt.

3. **The dominant failure is silent corruption, not crash.** A rank that returns a
   confident wrong answer is invisible to timeouts, retries and schema checks. It
   is the analogue of silent data corruption, for which HPC's answer is not
   checkpoint/restart — which preserves the corruption faithfully — but carrying
   redundant information that lets the computation check itself.

A fourth decision is structural: **the protocol lives in the harness, not in the
prompt.** An agent holds only handles; every AgentMPI call is made by trusted
host-side code, and the model is invoked as a kernel that transforms artifacts.

## Install

Python 3.11+, no third-party dependencies.

```bash
pip install -e .
pytest                 # 366 tests
```

## A first harness

```python
import agentmpi as ampi

def rank_main(comm):
    # Decompose. Variable-sized blocks, delivered by handle so no rank
    # admits another rank's work into its context.
    mine = comm.scatterv(WORK if comm.rank == 0 else None, root=0)

    # Agree on shared terminology in 2*ceil(log2 p) rounds, with no
    # coordinator reading anyone's section. UNION is exact, associative,
    # commutative and idempotent, so every rank ends with the same glossary.
    terms = comm.agent(extract_prompt(mine), contract=TERMSHEET)
    glossary = comm.allreduce(terms, ampi.UNION)

    out = comm.agent(translate_prompt(mine, glossary), contract=TRANSLATION)

    # Reconcile boundaries with immediate neighbours only: 2p messages,
    # not p^2. This is halo exchange on a 1-D Cartesian topology.
    topo = ampi.cart_create(comm, dims=[comm.size], periods=[False])
    left, right = ampi.halo_exchange(topo, head(out), tail(out))
    out = comm.agent(revise_prompt(out, left, right))

    # Collect by handle: the root never admits p*n tokens.
    return comm.gather(out, root=0, mode=ampi.Mode.RENDEZVOUS)

job = ampi.launch(rank_main, size=8, executor_factory=lambda r: my_agent)
print(job.totals())
```

Everything above is host-side, deterministic code. The only nondeterminism is
inside `comm.agent`, which is why the same harness runs against real agents,
against a simulator, and against a recorded replay without changing a line.

## What the API gives you

| area | operations |
| --- | --- |
| communicators | `split`, `dup`, `create_group`, `spawn`, private contexts |
| point-to-point | `send`/`recv`, `ssend`, `isend`/`irecv`, `sendrecv`, `probe`, `mprobe`/`mrecv`, `fetch`, `release` |
| transport | `EAGER` / `RENDEZVOUS` / `SYNCHRONOUS` / `AUTO`, published unexpected-message budgets, blocking eager credit |
| types | `Contract` (structural + semantic, with MPI-style matching), `View` (`vector`, `indexed`, `keys`, `jsonpath`, `head`, …), `Validator` |
| collectives | `barrier`, `bcast`, `scatter`/`scatterv`, `gather`, `allgather`, `reduce`, `allreduce`, `scan`/`exscan`, `alltoall`, `reduce_scatter` — 27 selectable algorithms, all built over point-to-point |
| topologies | `cart_create`, `dist_graph_create`, `neighbor_allgather`, `neighbor_alltoall`, `halo_exchange` |
| one-sided | `Window` with `get`/`put`/`accumulate`/`compare_and_swap`/`fetch_and_op`, leased `lock`/`unlock`, `fence`, `sync`, `SEPARATE` memory model, staleness detection |
| fault tolerance | lease-based detection, five barrier policies, `revoke`, `shrink`, `agree`, `replicate_and_compare`, OTP-style `Supervisor` |
| cost | calibrated α/β/γ model, closed-form formulas for every algorithm, Amdahl / USL / Karp–Flatt, discrete-event simulator |

## Running an agent population

The protocol is reachable through a process boundary, so any agent with shell
access can *be* a rank. Ranks pull work from a broker queue:

```bash
ampi --root runs/job init --size 8          # fix the world size
# ... start the harness with executor="broker" ...

# each agent runs this loop:
ampi worker --rank 3 next --timeout 240     # blocks for the next task
#   -> {"prompt_file": ..., "result_file": ..., "submit": "<exact command>"}
ampi --root runs/job worker done --rank 3 --aid 17 --file <result>
```

The queue is a *pull* queue and is per rank. Pull, because a pushed invocation
would require the harness to know how to start an agent and would couple it to a
vendor; per rank, because a rank is a durable role whose accumulated state must not
be stolen by another. The exact worker bootstrap prompt used in the experiments is
[`experiments/worker_prompt.md`](experiments/worker_prompt.md) — note that it
mentions neither collectives nor the experiment.

Diagnostics:

```bash
ampi --root runs/job status     # broker queue, token and cost totals
ampi --root runs/job ranks      # per-rank health, lease age, context occupancy
ampi --root runs/job doctor     # who has not arrived, what is blocked, held locks
ampi --root runs/job report     # calibrated cost report
ampi --root runs/job trace      # the full event log
```

`doctor` answers the three questions asked when a run hangs: who has not reported,
what is everyone blocked on, and where did the tokens go. The MPI equivalent is
attaching a debugger to 512 processes, which is why nobody does it.

## Experiments

Two tasks chosen to bracket the coupling spectrum, plus microbenchmarks.

```bash
# weakly coupled: parallel book translation, with real agent ranks
python3 experiments/campaign.py --suite translation --ranks 8 --prefix real

# strongly coupled: build a specified SQL engine against a fixed acceptance suite
python3 experiments/campaign.py --suite software --ranks 8 --rounds 2 --prefix real

# protocol-level results, deterministic and free
python3 experiments/microbench.py --bench collectives --bench faults \
    --bench transport --bench scaling

python3 scripts/make_tables.py   # regenerate every table in the paper
```

Every headline quality number is computed by code from the agents' artifacts —
entity-rendering consistency, verification that a reported rendering actually
occurs in the text that reported it, paragraph fidelity, acceptance-test pass
rate, fact retention — never from a model's opinion of its own output. A
comparison between protocol variants judged by a model would not be replicable.

The `campaign.py` driver exists because for real agents the dominant cost is the
*population*, not the work. Workers follow a campaign pointer, so one pool serves a
whole ablation ladder and the population is launched once — and because every
configuration is served by the same agents in the same session order, differences
between configurations are not confounded by differences in the population.

## Repository layout

```
src/agentmpi/          the reference implementation
  fabric.py            durable protocol state (SQLite WAL + content-addressed store)
  comm.py              groups, communicators, point-to-point, matching
  algorithms.py        every collective, expressed over point-to-point
  ops.py               reduction operators and the algebra that constrains them
  schema.py            contracts and views (the datatype analogue)
  rma.py               windows, epochs, leased locks, staleness detection
  ft.py                detection, revoke/shrink/agree, OTP supervision
  cost.py, sim.py      cost model, calibration, discrete-event simulator
  executor.py          function / simulated / replay / broker executors
  cli.py               the `ampi` command, so an agent can act as a rank
docs/spec/             the protocol specification
docs/research/         cited background memos on MPI and on multi-agent systems
experiments/           the two end-to-end experiments and the microbenchmarks
tests/                 366 tests
paper/                 the paper; tables under paper/generated are generated
results/               experiment output, consumed by scripts/make_tables.py
```

## Reading the source

The modules are written to be read. Each opens with the design argument for what
it contains — what MPI does, why, whether it transfers, and what had to change —
because a protocol whose rationale is not written down gets reimplemented wrongly.
`algorithms.py` and `ops.py` are the two worth reading first.

## Status

Version 0.1, and the spelling is not claimed to be right. MPI's was not right in
1994 either; the value of the Forum was the process, not the first draft. The claim
is that the *shape* of the problem is the one message passing already solved.

## Licence

Apache-2.0.
