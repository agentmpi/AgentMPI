# AgentMPI

AgentMPI is a protocol proposal and reference runtime for building portable
multi-agent harnesses. It transfers the durable ideas behind the Message Passing
Interface (MPI)—communicators, ranks, tagged point-to-point messages,
collectives, explicit progress, resource bounds, and repair after executor
failure—to heterogeneous AI agents.

This repository contains:

- a transport-neutral protocol specification in [`SPEC.md`](SPEC.md);
- a durable, brokerless SQLite reference runtime in [`src/agentmpi`](src/agentmpi);
- conformance and failure-injection tests in [`tests`](tests);
- reproducible true multi-agent workloads in [`experiments`](experiments);
- the paper source, bibliography, generated data, and PDF build instructions in
  [`paper`](paper).

The protocol is a harness interface, not a prescribed agent framework. AgentMPI
does not choose a model, planning method, prompt format, tool API, or memory
architecture. A harness can map the same calls to SQLite, NATS, Kafka, gRPC,
shared storage, or another transport while preserving observable semantics.

## Quick start

AgentMPI requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'

agentmpi init --db demo.db --session demo --size 2
agentmpi join --db demo.db --session demo --rank 0
agentmpi join --db demo.db --session demo --rank 1
agentmpi send --db demo.db --session demo --rank 0 --dest 1 \
  --tag task --json '{"instruction":"Review module A"}'
agentmpi recv --db demo.db --session demo --rank 1 \
  --source 0 --tag task --timeout 10
```

From Python:

```python
from agentmpi import Runtime

Runtime.initialize("run.db", size=2, session_id="run")
coordinator = Runtime.attach("run.db", "run", 0)
worker = Runtime.attach("run.db", "run", 1)
coordinator.send({"task": "translate section 1"}, dest=1, tag="TASK")
assignment = worker.recv(source=0, tag="TASK", timeout=30)
```

## Core semantics

- A **session** owns isolated protocol resources.
- A **communicator** is an immutable ordered membership plus generation.
- A **rank** names one executor within a communicator.
- A point-to-point message matches on communicator, destination, source, and
  tag; matching messages from one source do not overtake.
- A **collective epoch** requires the same operation order at every live member.
- Large values become content-addressed artifacts; bounded mailboxes and
  per-rank context credits provide backpressure before context-window OOM.
- Heartbeat leases classify dead executors. `revoke` interrupts unsafe work and
  `shrink` creates a fresh generation without failed ranks.
- Lease locks issue monotonic fencing tokens so a resumed stale executor cannot
  overwrite a newer owner's work.
- Every protocol transition enters an append-only event trace.

The complete normative behavior and agent analogies for MPI operations are in
[`SPEC.md`](SPEC.md).

## Verification

```bash
pytest
ruff check .
mypy src/agentmpi
python scripts/run_benchmarks.py
```

Experiment manifests record the subagent prompts, rank assignments, protocol
events, outputs, and metrics. Generated claims in the paper are sourced from
machine-readable files under `experiments/results/`; qualitative examples are
clearly separated from statistically meaningful measurements.

## Scope and maturity

This is a research prototype (v0.1), not a production consensus system.
SQLite gives a transparent executable specification and process-level
durability on one shared filesystem; it is not a claim of cluster-scale
performance. The wire schema, failure detector, authentication, and transport
bindings require independent interoperability implementations before
standardization.

## License

Apache-2.0. The short literary passages in the translation experiment are from
public-domain works and retain source attribution in their manifest.
