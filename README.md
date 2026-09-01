# AgentMPI

**A message passing interface for multi-agent systems.**

AgentMPI is a *protocol*, not a multi-agent system. It stands to multi-agent
systems as MPI stands to parallel applications: a library that harness authors
call, not a harness. It does not decide how many agents to run, what they should
do, how to prompt them, which model to use, or how to recover from failure. It
provides the mechanisms with which those decisions are expressed.

It is also deliberately semantics-thin. A message is an opaque body plus an
envelope of size and provenance metadata — no ontology, no speech acts, no
commitment semantics. That is a considered rejection of the KQML/FIPA-ACL lineage
in favour of MPI's stance: standardise the mechanism, leave meaning to the
application.

---

## Why this exists

Multi-agent LLM systems today are written the way parallel programs were written
in 1991. Everyone implements their own coordination layer, none of them compose,
and the same failure modes are rediscovered independently by every group. Parallel
computing left that state by agreeing on an interface, and the interface it agreed
on transplants — but not mechanically.

Five properties of an LLM executor differ from an MPI process, and each forces a
deviation:

| | MPI process | AgentMPI executor |
|---|---|---|
| Determinism | deterministic | may produce a different, plausible, wrong result |
| Scarce resource | bandwidth | **context window** |
| Operator cost | nanoseconds; free | seconds to minutes; dominant |
| Latency | tight | heavy-tailed |
| Failure | fail-stop, rare, fatal | frequent, partial, sometimes silent |

Consequences developed in [`spec/AgentMPI-1.0.md`](spec/AgentMPI-1.0.md) and
measured in [`paper/`](paper/): MPI's eager limit becomes a token-denominated
flow-control problem; MPI's "safe program" discipline becomes a mechanically
checkable property; two of MPI's algorithm-selection rules invert; and MPI's
barrier hierarchy collapses because the control plane is shared rather than
point-to-point.

## What is here

```
spec/AgentMPI-1.0.md   the normative specification
ampi/                  the reference runtime
  device/              the narrow waist: 6 operations, 3 transports
  core/                everything above the waist, transport independent
  cli.py               the command binding an agent calls
  harness.py           the SPMD driver
  executor.py          function, replay, and broker (pull-queue) executors
  doctor.py            "which rank has not arrived, and what do I do"
conformance/           one suite, run against every transport
experiments/           E0 microbenchmarks, E1 translation, corpus, scoring
runs/                  committed run artifacts: prompts, per-rank output, traces
research/              four scholarly dossiers and their bibliographies
paper/                 the paper; every number is a macro generated from run data
runs/*/analysis/       generated per-run metrics and static trace figures
viz/                   read-only Jumpshot-style interactive trace explorer
```

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e .

# Harness-side: the protocol lives in your code.
.venv/bin/python - <<'PY'
from ampi.harness import Harness
h = Harness(root="/tmp/job", size=4, device="sqlite", force=True)
h.create()

def rank_main(amp, rank):
    amp.barrier("start", timeout=30)
    out = amp.allreduce("glossary",
                        payload={"owner": f"m{rank % 2}", f"own{rank}": rank},
                        op="union", timeout=30)
    return out.get("conflicts")     # disagreements are lifted, never decided locally

for r in h.run(rank_main):
    print(r.rank, r.value)
PY
```

```bash
# Agent-side: anything that can run a shell command is a rank.
export AMPI_ROOT=/tmp/job2
.venv/bin/ampi new --root $AMPI_ROOT --size 4
AMPI_RANK=0 .venv/bin/ampi init
AMPI_RANK=0 .venv/bin/ampi man          # the manual is a command
AMPI_RANK=0 .venv/bin/ampi doctor       # names the rank that has not arrived
```

## The three ideas worth knowing

**The narrow waist.** Six device operations — `append`, `match`, `scan`, `cas`,
`lease`, `clock` — separate portable semantics from transport, exactly as MPICH's
abstract device interface does. Three transports ship (SQLite, a filesystem
journal, memory) and share no code below the interface. One conformance suite runs
against all of them, which is the difference between a specification and a
library. It found a defect that exists on one transport only: SQLite's type
affinity returned integers as strings for four indexed fields, so a wildcard
receive posted as `-1` read back as `"-1"` and silently stopped matching.

**Conflict lifting.** A canonical reduction tree makes an agent-evaluated
reduction *reproducible*. It does not make it *consistent*: two branches can meet
the same conflict and resolve it oppositely, and no node is in a position to
notice, because each merge saw a locally consistent pair. Lifting enlarges the
operator's codomain with a conflict set whose join is a semilattice, so the
conflicts reaching the root are identical for every tree shape and the root
arbitrates each exactly once. Verified exhaustively over every binary fold order
to p=6 and randomly to p=32.

**Context safety.** MPI's advice is to test a program by making every send
synchronous. Here the buffer a harness implicitly relies on is the receiving
executor's context window, so the penalty for getting it wrong is not a deadlock
on someone else's machine but silently worse output as you scale. `ampi.core.safety`
runs the test, names the blocked ranks and the send cycle among them, and reports
whether declaring rendezvous repairs it.

## Reproducing

```bash
make test          # 568 tests: conformance against 3 devices, plus unit tests
make bench         # E0 microbenchmarks; fits alpha and beta per device
make paper         # regenerate macros from run data, then build the PDF
make check         # lint, plus verify the PDF matches its sources
```

Analyze any completed run directly from its append-only event log:

```bash
.venv/bin/pip install -e '.[plots]'
.venv/bin/python scripts/analyze_run.py runs/e1-real-p32
make viz-install && make viz-build
make viz-api       # then run `make viz-dev` in another terminal
```

The production Durov experiment is documented in
[`experiments/e3_durov/`](experiments/e3_durov/README.md). Its importer binds the
authorized 99-page source checkout to a git commit and per-page hashes without
copying legacy translations. The harness runs at 16, 32, or 64 durable ranks and
uses bounded per-page agent calls, evidence-grounded terminology reduction,
peer review, RMA epochs, compare-and-swap claims, leased locks, barriers,
broadcasts, and manifest gathers.

The agent experiments need agents. `experiments/launch.py` renders one worker
prompt per rank and writes a launch plan naming every rank *requested*, before any
of them starts, so the set the experiment intended is recorded independently of
the set that answered. The worker bootstrap prompt is checked in at
[`experiments/worker_prompt.md`](experiments/worker_prompt.md) because it is part
of the method — and note what it does *not* contain: no communicator, no
collective, no barrier, no mention of the experiment. The protocol belongs in the
harness, not in the prompt.

## Results, including the negative ones

* **Protocol cost.** SQLite transport: α = 0.730 ms, β = 0.480 µs/token,
  half-bandwidth point 1521 tokens. β agrees within 2% across all three
  transports, because the per-token cost is serialisation above the waist rather
  than transport below it. Against a thirty-second operator the entire protocol is
  0.007% of an allreduce at p=128.
* **Selection inverts.** At γ=0 the journal-folding flat schedule wins; at γ=30 s
  it is ten times behind a tree. Recursive-doubling allreduce ties
  reduce-then-broadcast on latency at p=64 and performs 384 operator applications
  against 63.
* **Conflict lifting works on real data, and scales as predicted.** At p=8 the
  glossary reduction agreed 17 terms and lifted 3 conflicts; at p=32 it agreed 8
  and lifted 12. Quadrupling the population quadrupled the disagreement found. The
  root arbitrated each once.
* **And the quality comparison is null at both sizes.** Four arms, 32 distinct
  agents: weighted terminology consistency is 1.0 everywhere, and under the strict
  metric the *control* at p=32 scores marginally higher than the treatment. The
  glossary cost 3.7× the wall clock at p=8 and 1.4× at p=32.
* **The reason is more interesting than a win would have been.** Every one of the
  12 conflicts was the same kind — whether the Spanish definite article belongs in
  the rendering — and in running prose the article is determined by the grammar of
  the sentence, not by a lexical choice. The collective correctly identified a real
  disagreement in what the population *said* it would do, and that disagreement
  never reached what the population *produced*. Before paying a collective to
  remove a coupling, measure whether the coupling is costing anything.
* **A scorer bias we caught.** The first version built its vocabulary of candidate
  renderings from each run's own term sheets, and since only the treatment arm
  produces term sheets, the control was scored against an almost-empty vocabulary
  and flattered by it. The vocabulary is now pooled across arms.

## What this does not show

It does not show that AgentMPI produces better answers than an ad-hoc harness. The
controlled quality comparison is null at both sizes we ran. Every agent experiment
is n=1 per cell, one model family, one language pair, and a corpus whose recurring
terms are famous enough that a strong model has seen them; a task with genuinely
novel terminology is where we would expect a glossary to pay, and we have not run
it.

The largest *completed* agent run is 32 ranks over 8 executors. We attempted 100
and it did not finish: the host's ten-session cap forced several waves of
executors, and an operator error later destroyed that run's journal. The pull
queue handled the first problem — a fresh wave could be added mid-run against
exactly the ranks still queued, with no harness change and no restart — and the
second was ours. We report the partial run's identity measurement, which was
committed before the journal was lost, and we do not report a hundred-rank result,
because we do not have one.

## License

Apache 2.0.
