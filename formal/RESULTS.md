# TLC result

Date: 30 August 2026  
TLC: 2.19 (8 August 2024)  
Java: OpenJDK 21.0.10  
Configuration: three agents, two tags, one symbolic payload, two bounded
operations; deadlock checking disabled because an all-members-failed state is a
permitted terminal state.

Command:

```bash
java -XX:+UseParallelGC -cp /tmp/tla2tools.jar tlc2.TLC \
  -workers auto -config AgentMPI.cfg AgentMPI.tla
```

Result:

```text
Model checking completed. No error has been found.
45,073,807 states generated
11,152,584 distinct states found
0 states left on queue
Complete state graph depth: 24
```

The first model attempt exposed an error in the abstraction: `Crash` mutated
the current communicator membership while retaining a collective arrival.
AgentMPI requires immutable generation membership. The corrected model records
failure separately and changes membership only during `Repair`. This
counterexample is evidence that the model is exercising a meaningful invariant,
not proof that the implementation refines it.

This finite run does not establish liveness, unbounded-queue correctness,
transport conformance, or semantic correctness of agent output.

