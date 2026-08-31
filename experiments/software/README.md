# Collaborative software workload

Twelve independent Cursor subagents collaboratively implement `minidag`, a
dependency-free deterministic DAG execution library. This workload is less
embarrassingly parallel than translation: modules share interfaces, tests depend
on implementations, reviews wait on several producers, and one integrator must
resolve cross-module failures.

```
rank 0: coordinator and interface broadcast
ranks 1..5: graph/parser/scheduler/executor/CLI implementations
ranks 6..7: tests
rank 8: docs and example
ranks 9..11: staged implementation, integration, and reliability review
rank 12: final integration and verification
```

Each executor receives its task through `TASK`, waits for prerequisites through
`DONE`, and reports completion through `DONE`, `REVIEW`, or `FINAL`. Before any
file mutation it acquires an AgentMPI lock named `file:<relative-path>` and
releases it with the returned fencing token. The shared workspace provides data
storage; AgentMPI provides control flow, ownership, and an auditable happens-
before trace.

## Reproduce

```bash
python3 experiments/software/prepare.py
```

Start the 12 agents with the rank assignments in
`software_manifest.json`. The versioned interface is embedded in each `TASK`
message; this run uses point-to-point dissemination, while the translation run
exercises `Bcast`/`Scatter`/`Gather`. Implementers run concurrently. Reviewers
block on explicit `DONE` messages from expected producer ranks. The integrator
waits for all completion messages, runs `unittest` and the example, fixes only
integration defects under file locks, and sends a JSON report to rank 0.

```bash
python3 experiments/software/collect.py
```

The collector independently reruns compilation and all tests, inventories the
artifact, and exports protocol metrics and the full trace. Passing tests support
only the stated behavior; they do not establish production readiness or compare
model quality.
