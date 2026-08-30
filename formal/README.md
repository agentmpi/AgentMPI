# AgentMPI formal model

`AgentMPI.tla` is a deliberately bounded abstract model of the protocol safety
kernel. It models three concerns that ordinary unit tests do not exhaust:

- point-to-point matching with source/tag wildcards and non-overtaking streams;
- one communicator-global collective sequence with mismatch-triggered revoke;
- crash/revoke/repair epochs and lock fencing whose high-water mark survives
  release.

It does not model LLM inference, semantic correctness, authentication,
artifact availability, external effects, SQLite transactions, or liveness
under network failure. Passing TLC means only that the listed invariants hold
within the finite constants in `AgentMPI.cfg`.

Run:

```bash
curl -L -o /tmp/tla2tools.jar \
  https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
java -XX:+UseParallelGC -cp /tmp/tla2tools.jar tlc2.TLC \
  -config AgentMPI.cfg AgentMPI.tla
```

The model checks:

1. protocol state remains typed;
2. admitted/delivered message IDs remain unique;
3. old-generation messages cannot survive repair;
4. collective entrants belong to the immutable current membership;
5. a collective descriptor mismatch revokes the generation;
6. a lock owner remains a current member;
7. releasing a lock does not reset its fencing counter.

The next formalization step is a concrete transport model plus a refinement
mapping to this abstract machine. Separate configurations should then cover
posted-receive cancellation, duplicated frames, false suspicion, two repaired
epochs, and liveness under explicit weak-fairness/eventual-detection
assumptions.

