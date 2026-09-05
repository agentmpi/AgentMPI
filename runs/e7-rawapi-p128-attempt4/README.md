# e7-rawapi-p128-attempt4: 128 ranks over four machines, poisoned glossary at 180 minutes

The first attempt in which every phase up to the binding glossary completed on
the fixed daemon: four nodes, 128 surveys, 7 arbitration batches, 48 research
findings each claimed by exactly one rank within a minute of the barrier (the
claim-from-absence fix), the research fence closed.  Two things then went
wrong, one operational and one a defect.

| quantity | value |
|---|---|
| elapsed when aborted | 190 min |
| tasks done | 183 (128 surveys, 7 arbitrations, 48 research); spend $5.17 |
| rank errors | 95, all `JSONDecodeError` on the broadcast body `glossary-settled` |
| node 1 | all 32 ranks convicted after its daemon stopped answering (rolled twice mid-run) |

## What happened

*Operational.*  The daemon's read path was fixed twice while the run was live
(`gitd: a stale reader does not wait for the writer's lock`; `readers defer while
a mutation is in flight`) and rolled onto every node by restarting the daemon
under the running ranks.  Node 0 measured the effect: single reads fell from 22 s
to under half a second, and its push success rose from 12% to 33% once readers
stopped fetching between the writer's attempts.  Node 1's daemon did not survive
its second roll; its 32 ranks stayed blocked in the research fence, their leases
lapsed, and the glossary reduction closed without them.

*The defect.*  The root's context ledger was near exhaustion when the glossary
allreduce returned, so the value the runtime handed back was a degraded view: the
canonical JSON with an elision marker in the middle of a string.  With no
conflicts to arbitrate, the root broadcast that string as the binding glossary.
Every rank's copy was the same poisoned body, so 95 ranks failed to parse it and
so did their respawned successors, whose fresh ledgers did not help because the
stored body itself was the view.  The harness now reads the stored result by
handle when the returned value is degraded, releases the ledger before each
reduction, and names the cause when a delivered body is not JSON.

## Files

`launch_plan.json`, `config.json`, `corpus_manifest.json`, `harness.trace.jsonl`
(8660 events), `harness.json`, `launch/launch-node0.json`.  No book text.
