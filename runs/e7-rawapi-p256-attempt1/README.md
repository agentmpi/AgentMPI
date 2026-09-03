# e7-rawapi-p256-attempt1: 256 ranks over eight machines, aborted at 80 minutes

The first 256-rank attempt, launched as eight sessions of 32 rank processes on
the fixed `gitd` daemon (`ampi-jobs/e7-rawapi-p256b`).  Seven of the eight nodes
joined (the eighth session had been interrupted and never received its
redirect); all 224 joined ranks surveyed their segments in 36 minutes; then the
census allreduce never closed.  After 80 minutes 66 ranks had been convicted,
41 more were mid-respawn, and node 0, whose machine had been recycled under the
run and was rejoining, was landing one push a minute against ten rejections.
The run was stopped.

| quantity | value |
|---|---|
| elapsed when aborted | 80 min |
| ranks joined / nodes joined | 224 / 7 of 8 |
| tasks done | 224 surveys |
| events | 11,641; 5,301 `failure.suspect`, 2,321 `failure.convict` |
| convictions by node | node 0: 828, node 3: 524, node 4: 578, node 2: 228, node 5: 100, node 6: 63 |
| node-0 daemon at abort | 45 pushes, 442 rejected pushes, 514 fetches; batch window at its 4 s ceiling |
| device state file | 8.5 MB |

## What happened

This is a different wall from `e7-rawapi-p128-attempt1`.  There the daemon's
readers starved behind its writer; here the writers starve behind each other.
The device is one git ref advanced by compare-and-swap, and eight machines
contend for it.  The remote accepts roughly one commit every four seconds; a
node that loses the race fetches, re-applies its batch and tries again, so a
node's throughput is one push per tens of seconds and any *sequence* of
dependent mutations --- a rank's initialisation, a respawn, a collective's
join-then-poll --- costs one round trip of that size per step.  Node 0's rejoin
had to respawn 32 ranks one mutation at a time and got through nine in forty
minutes.

Of the ranks convicted, 112 had last been seen joining the census: they were
blocked in the collective, their lease renewals queued behind their node's
losing pushes, and the `silent_for` recorded on their suspicions ran from 180 s
to 1898 s.  The runtime again behaved as specified, and the transport again was
the limit.  Eight writers on one ref is past what a hosted git remote will
serialise at the rate a 256-rank population needs.

## What was done instead

The 256-rank run was relaunched as **four** nodes of 64 rank processes
(`e7-rawapi-p256c`): half the writers, twice the operations per push, the same
configuration that carried `e7-rawapi-p128b`.  A machine that hosts 64
processes is well within a 4-CPU, 16 GB session.  The design lesson is recorded
in `experiments/e7_rawapi_book/NODES.md`.

## Files

`launch_plan.json`, `config.json`, `corpus_manifest.json`, `harness.trace.jsonl`
(11,641 events), `harness.json` (the diagnosis at abort), `launch/launch-node0.json`.
No book text.
