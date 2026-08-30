# AgentMPI

**Agent Message Passing Interface** — a portable protocol for writing multi-agent harnesses, in the tradition of MPI.

This is not a multi-agent product. It is the interface people use to write their own: point-to-point matching, collectives, RMA windows and locks, communicator split/spawn, ULFM-shaped revoke/agree/shrink, and a context-token budget so a rank can fail with OOM instead of silently overflowing.

Two reference bindings ship in this repository:

| Binding | Path | Fabric | Role |
|---|---|---|---|
| Filesystem / algorithmic | `agentmpi/` | POSIX mailboxes, binomial/Bruck/doubling collectives | Default SPMD API (`COMM_WORLD.send`, `bcast`, …) |
| SQLite / durable | `src/agentmpi_sql/` | WAL database, no resident broker | Inspectable semantic oracle, independent processes |

The protocol is transport-neutral (`SPEC.md`, `spec/AGENTMPI.md`). A harness can map the same calls to files, SQLite, NATS, Kafka, or gRPC.

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

## Reproduce the filesystem experiments

```bash
python3 experiments/microbench.py
python3 experiments/translation/harness.py -n 16 --limit 64
python3 experiments/collab/harness.py
python3 experiments/fault/harness.py
python3 experiments/scale/harness.py -n 100
```

SQLite-binding experiments and the 100-process / fail-stop scripts live under `scripts/` and `experiments/translation`, `experiments/software`.

## Paper and dashboard

- Narrative paper: `paper/agentmpi.md`
- Conference LaTeX draft: `paper/main.tex`
- Dashboard: `cd web && npm install && npm run dev` (port 43147)

## Why this exists

MPI is the thing HPC authors write against so they are not also inventing a network stack. Multi-agent authors are still inventing the network stack — in every framework, every time, and usually without a matching rule, a lock, or a story for executor death. MCP is tools. A2A is pairwise discovery. AgentMPI is SPMD message passing.
