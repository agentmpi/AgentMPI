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

2. **Reduction is lossy in depth, so algorithm choice is a quality decision — and
   binary is the wrong arity.** A reduction operator implemented by a language model
   is not associative, and its loss compounds with the *depth* of the reduction tree
   rather than the number of applications. Operators therefore declare their
   algebraic strength, the runtime refuses a tree for a non-associative operator, and
   every reduction reports its fold depth.

   The non-obvious part: every `log2 p` factor in MPI's collective layer descends
   from processes being **single-ported**, so a wider tree node buys nothing. An
   agent rank has no port count — a prompt carries eight artifacts as easily as two,
   and a *variadic* operator merges all eight in one application. The binding
   constraint becomes the context budget, giving `k* = floor(C(1-h)/s)` and a fold
   depth of `log_k p`. At `p=64`, widening from `k=2` to `k=8` cuts fold depth from
   6 to 2 **at an identical message count**, since every non-root rank sends exactly
   once whatever the arity. So this setting wants the *widest* feasible tree where
   MPI wants the narrowest.

   None of this works unless broadcast is lossless, which is why artifacts are
   immutable and content-addressed: a tree forwards handles, so relaying cannot
   corrupt.

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

### Looking at a run

A message-passing bug is almost always a shape in time — a rank idle while its peers work,
a fan-in serialising at a root, a barrier whose last arrival is minutes after its first —
and those shapes are invisible in a log and obvious in a picture. The viewer draws one lane
per rank, agent invocations as spans, messages as ticks, window operations as diamonds with
stale writes in red.

```bash
make viz                                          # http://127.0.0.1:43117
python3 scripts/trace_server.py --runs runs       # optional: serve live runs
```

32 recorded traces from the experiments in this repository ship under
`viz/public/traces`, so the viewer works immediately after a clone with no server and
nothing run. `make traces` re-exports from `runs/` after an experiment of your own.

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

## Some measured findings

From 14 harness runs executed by real agent ranks — 431 agent invocations, ~1.0M tokens,
3.8 hours of cumulative harness wall time — all reported in the paper with the code that
produced them:

- **Strong scaling** on parallel translation: 3.46× speedup at `p=8` on identical
  total work. The Universal Scalability Law fit is `σ=0.220, κ=0.0000` (R²=0.873) —
  substantial contention, but a *zero* coherency term, meaning the coordination cost
  is a constant serial fraction rather than a ceiling. A positive `κ` is the
  signature of an all-to-all conversation and the reason group-chat architectures
  stop scaling.
- **Exact operators beat semantic ones by a wide margin.** The same glossary
  agreement performed by an LLM merge instead of the exact, idempotent `UNION` cost
  6.9× the wall time and 1.4× the price, and produced identical measured quality.
- **Coordination has a price worth knowing**: the glossary allreduce plus halo
  exchange cost 76% more wall time and 159% more tokens than doing neither. On our
  text they bought nothing measurable, because the entities were famous enough that
  models render them identically without coordination. We report that rather than
  swapping the metric.
- **A capacity-bound reduction erases whole contributors, silently.** This is the finding
  we nearly missed. Aggregate retention of 0.385 looks like graceful degradation; the
  per-rank breakdown shows ranks 0–2 retained *completely*, rank 3 at 8%, and ranks 4–7
  contributing **nothing at all** — identically under every tree shape. Once the
  accumulator fills the budget it has no room to admit anything, so each later
  contribution is discarded whole. Every merge reported success and the root received a
  well-formed report with no indication that five of its eight inputs were missing. That
  is why `Op` exposes the leaf count an accumulator represents, and why aggregate quality
  metrics are not enough.
- **Fold depth costs latency, not fidelity.** We predicted a deep reduction tree would
  lose more content than a shallow one. It does not: with an incompressible payload and a
  contract-enforced output budget, retention is identical (0.385) at fold depths 2, 3 and
  7, because the root's budget is the binding constraint and losses have no slack to
  compound into. What depth does cost is time — the widest tree finished in 52 s where the
  serial chain took 634 s, a 12× reduction at equal quality, emitting half the tokens.
- **A budget stated in a prompt is not a budget.** Getting to that result took three
  attempts. Asked to fit more than its budget allows, a semantic operator first
  *re-encodes* (it invented a packed notation and preserved every item), and when that is
  not enough it simply *overruns* an advisory limit — 8 of 10 merges exceeded the stated
  budget by up to 55%. Only `Contract(max_tokens=...)`, which the runtime checks and
  retries against, makes a budget real.
- **Context safety has a sharp threshold**: an all-eager ring exchange fails above
  the unexpected-message budget with a reported `ERR_CONTEXT_OVERFLOW`, while
  rendezvous completes at every size.
- **Model and implementation agree exactly** on message counts and fold depths
  across 278 collective configurations — after the cross-check found four real bugs.
- **Interface publication pays when the spec underdetermines boundaries**, and not
  otherwise. With signatures withheld so the shared window is the only channel: green in
  one round versus two, 2.1× less wall time, 2.5× lower cost, 4.6× less defensive
  coupling. With a precise spec the window is redundant, because the spec is already
  doing its job — so measure how much of your interface surface the spec pins, and
  publish only the rest.
- **Convention divergence is a coordination metric that needs no oracle.** Counting
  surviving spellings of the same idea: one author converges on 1, eight ranks with a
  shared window on 3, eight without on 6 — plus the translation code that bridges them.

## Status

Version 0.1, and the spelling is not claimed to be right. MPI's was not right in
1994 either; the value of the Forum was the process, not the first draft. The claim
is that the *shape* of the problem is the one message passing already solved.

## Licence

Apache-2.0.
