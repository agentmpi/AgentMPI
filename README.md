# AgentMPI

**Agent Message Passing Interface** — a portable protocol for writing multi-agent harnesses, in the tradition of MPI.

This is not a multi-agent product. It is the interface people use to write their own: point-to-point matching, collectives, RMA windows and locks, communicator split/spawn, ULFM-shaped revoke/agree/shrink, and a context-token budget so a rank can fail with OOM instead of silently overflowing.

## Implementations

| Package | Path | Fabric | Role |
|---|---|---|---|
| **`ampi`** (v0.2 reference) | `src/ampi/` | abstract device interface over SQLite **or** POSIX mailboxes | The current reference runtime: collective schedules with algorithm selection, semantic operators, one-sided windows, ULFM triad, deadlock detection, PAMPI tracing |
| `agentmpi` (v0.1 prototype) | `agentmpi/` | POSIX mailboxes | Earlier filesystem binding; retained because the v0.1 measurements cited in the paper were produced with it |
| `agentmpi_sql` (v0.1 prototype) | `src/agentmpi_sql/` | WAL database | Earlier SQLite binding, same reason |

The normative specification is `spec/AGENTMPI-v0.2.md`; `SPEC.md` and
`spec/AGENTMPI.md` are the superseded 0.1 profile drafts. The protocol is
transport-neutral: a harness can map the same calls to files, SQLite, NATS,
Kafka, or gRPC.

## Quick start (v0.2 reference runtime)

```bash
python3 -m pip install -e ".[dev,analysis]"
python3 -m pytest tests/ -q            # 113 tests, both transports

ampirun new --job /tmp/job -n 8        # create a job
AMPI_JOB_DIR=/tmp/job AMPI_RANK=0 ampi init --role worker
AMPI_JOB_DIR=/tmp/job AMPI_RANK=0 ampi allreduce --op AMPI_MERGE_JSON \
    --json-file glossary.json --explain
```

`ampi plan` shows the decision function's reasoning without running anything:

```bash
ampi plan --collective allreduce -p 32 -n 127776 \
    --op AMPI_MERGE_JSON --vector --ctx-limit 128000
# -> chosen: rabenseifner
#    recursive_doubling rejected: peak residency of 197033 tokens
#    exceeds the rank context limit of 128000 tokens
```

`ampi doctor` reports wait-for cycles, stalled collectives and suspected
failures; `ampi-analyse <jobdir>` recomputes every published metric from the
job's trace.

## Quick start (filesystem binding)

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

```python
from agentmpi import Init, Finalize, COMM_WORLD

Init()
mine = COMM_WORLD.scatter(parts if COMM_WORLD.rank == 0 else None)
COMM_WORLD.gather(work(mine), root=0)
COMM_WORLD.barrier()
Finalize()
```

```bash
python3 -m agentmpi.runtime -n 8 -- python3 my_harness.py
```

A Cursor subagent is a rank. Give it `AMPI_HOME`, `AMPI_RANK`, `AMPI_SIZE` and the CLI:

```bash
python3 -m agentmpi recv --source 0 --tag 1 --out work.json
python3 -m agentmpi send --dest 0 --tag 2 --file result.json
python3 -m agentmpi barrier
```

## Reproduce the v0.2 experiments

```bash
python3 experiments/ampi/e1_microbench/run.py            # protocol cost, collectives, residency
python3 experiments/ampi/figures.py                      # regenerate the paper figures
python3 experiments/ampi/e2_semantic_allreduce/prepare.py  # write rank cards for real agents
python3 experiments/ampi/e2_semantic_allreduce/collect.py  # analyse the agent runs
```

Agent-driven experiments are launched by giving each rank card in
`<job>/cards/rankN.md` to one Cursor subagent. The cards are committed, because
an experiment whose agent instructions are not archived is not reproducible.

## Reproduce the v0.1 filesystem experiments

```bash
python3 experiments/microbench.py
python3 experiments/translation/harness.py -n 16 --limit 64
python3 experiments/collab/harness.py
python3 experiments/fault/harness.py
python3 experiments/scale/harness.py -n 100
```

SQLite-binding experiments and the 100-process / fail-stop scripts live under `scripts/` and `experiments/translation`, `experiments/software`.
The retained live-agent evidence records 25 distinct Cursor executor IDs in
`experiments/results/live_subagents.json`; deterministic coordinator ranks are
excluded from that count. The 100-rank Aesop file lacks equivalent launch
provenance and is explicitly treated as a dry run in the paper.

## Results from the v0.2 runs

Everything below is recomputed from committed job traces by
`experiments/ampi/*/collect.py`; nothing is taken from what an agent said it
did.

| Experiment | Ranks | Finding |
|---|---|---|
| Microbenchmarks (scripted) | 2–32 | alpha = 2.5 ms/step, beta = 5.4e-7 s/token. Recursive-doubling allreduce holds 1.5n tokens at every p; recursive-halving reduce-scatter falls from 0.68n at p=4 to **0.09n at p=32**, the difference between "cannot run in a 128k window" and "uses 9% of it". |
| Semantic reduction (real agents) | 8 x 2 | Both schedules do p−1 = 7 operator evaluations; the tree puts lg p = 3 on the critical path instead of 7, cutting critical-path model time **324.6 s → 108.2 s**. Identical coverage; the two merged documents share almost no sentences. |
| Software build (real agents) | 8 x 2 | Both delivered working software. The collective run: 68 collectives, 5 communicators, 10 claim grants for 6 modules, 2 implementers left as spares. The window run: **0 collectives**, 6 clean claims, 6 non-overlapping leases, and cross-module tests that found a real defect two reviewers confirmed. |
| Fault tolerance (real agents) | 6 | Two agents killed. Survivors revoked, shrank to a single communicator, and **adopted both dead agents' sections from the window rather than rewriting them**. Zero work lost. |

The runs broke the runtime in eight distinct ways, each fixed with a regression
test: detector oscillation (1091 condemnations in 20 minutes), a declared idle
period that overrode its own heartbeats, a suspicion that could fail a peer's
send, a poisoned collective slot with no recovery, a stale process consuming a
later run's messages, rank 0 logged as the sentinel −1 because it is falsy, a
killed rank resurrecting itself, and a `shrink` whose default naming fragmented
the survivors it was meant to regroup.

## Paper and dashboard

- Narrative paper: `paper/agentmpi.md`
- Conference LaTeX draft: `paper/main.tex`
- Dashboard: `cd web && npm install && npm run dev` (port 43147)

## Why this exists

MPI is the thing HPC authors write against so they are not also inventing a network stack. Multi-agent authors are still inventing the network stack — in every framework, every time, and usually without a matching rule, a lock, or a story for executor death. MCP is tools. A2A is pairwise discovery. AgentMPI is SPMD message passing.
