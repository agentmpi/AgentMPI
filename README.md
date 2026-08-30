# AgentMPI

**Agent Message Passing Interface** — a portable protocol for writing multi-agent harnesses, in the tradition of MPI.

This is not a multi-agent product. It is the interface people use to write their own: point-to-point matching, binomial-tree broadcast, Bruck barrier and allgather, recursive-doubling allreduce, RMA windows and locks, communicator split/spawn, ULFM-shaped revoke/agree/shrink, and a context-token budget so a rank can fail with OOM instead of silently overflowing.

## Repository layout

| Path | What |
|---|---|
| `agentmpi/` | Reference implementation (Python API + CLI) |
| `spec/AGENTMPI.md` | Language-independent protocol |
| `paper/agentmpi.md` | First academic paper |
| `experiments/` | Harnesses: translation, collab kvstore, fault study, 100-rank scale |
| `tests/` | Algorithm and collective tests |
| `web/` | Results dashboard |

## Install and test

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

## Write a harness

```python
from agentmpi import Init, Finalize, COMM_WORLD

Init()
mine = COMM_WORLD.scatter(parts if COMM_WORLD.rank == 0 else None)
COMM_WORLD.gather(work(mine), root=0)
COMM_WORLD.barrier()
Finalize()
```

Or launch ranks as processes:

```bash
python3 -m agentmpi.runtime -n 8 -- python3 my_harness.py
```

A Cursor subagent is a rank. Give it `AMPI_HOME`, `AMPI_RANK`, `AMPI_SIZE` and the CLI:

```bash
python3 -m agentmpi recv --source 0 --tag 1 --out work.json
python3 -m agentmpi send --dest 0 --tag 2 --file result.json
python3 -m agentmpi barrier
```

## Reproduce the experiments

```bash
python3 experiments/data/build_corpus.py   # already shipped as experiments/data/aesop_fables.json
python3 experiments/microbench.py
python3 experiments/translation/harness.py -n 16 --limit 64
python3 experiments/collab/harness.py
python3 experiments/fault/harness.py
python3 experiments/scale/harness.py -n 100
```

## Dashboard

```bash
cd web && npm install && npm run dev -- --port 43147 --hostname 127.0.0.1
```

## Why this exists

MPI is the thing HPC authors write against so they are not also inventing a network stack. Multi-agent authors are still inventing the network stack — in every framework, every time, and usually without a matching rule, a lock, or a story for executor death. AgentMPI is the missing layer. MCP is tools. A2A is pairwise discovery. This is SPMD message passing.

See `paper/agentmpi.md` for the history, the algorithms, and the measurements.
