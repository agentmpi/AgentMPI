# e7-rawapi-p256-attempt1: 256 ranks over eight machines, aborted at 80 minutes

The second multi-node attempt, on the daemon with the reader fix
(`runs/e7-rawapi-p128-attempt1` is the first).  Eight cloud sessions each ran 32
rank processes against one git branch.  Seven nodes joined (node 7's session was
waiting on a permission prompt it never got); 224 ranks surveyed their segment
in 36 minutes; the census allreduce never closed.

| quantity | value |
|---|---|
| elapsed when aborted | 79.8 min (18:28 to 19:48 UTC) |
| ranks / nodes joined | 224 / 7 of 8 |
| tasks done | 224 surveys; spend $4.99 |
| convicted ranks | 137 (2321 conviction events, 5301 suspicions) |
| node-0 daemon at abort | 45 pushes, 442 rejected pushes, 514 fetches; batch window at its 4 s ceiling |
| device state at abort | 8.5 MB `state.json` |

## What happened

This time the readers were not the bottleneck: the writers were.  Eight daemons
contended for one compare-and-swap ref, and the ref moved every four seconds
whichever daemon won it.  Node 0's daemon landed one push per minute against
ten rejections; a rank whose sequence of operations needed several dependent
pushes (init, window creation, joining a collective) waited minutes for each.
The 116 convicted ranks whose last operation was `coll.join census` had
entered the allreduce and then failed to renew their leases for longer than the
lease (median silence at suspicion 252 s), because a renewal is itself a push,
and their daemon's pushes were not landing.  Node 0's own 32 ranks died with
their machine (the session's VM was recycled while idle, see attempt 2 of
p128) and were being respawned one push at a time when the run was stopped.

The transport's ceiling is therefore about one global commit per round trip,
shared by every machine, and a machine's share falls with the number of
machines.  Four machines at p = 128 stayed inside it; eight did not.  The
256-rank run was relaunched as four machines of 64 ranks
(`e7-rawapi-p256-attempt2`).

## Files

`launch_plan.json`, `config.json`, `corpus_manifest.json`, `harness.trace.jsonl`
(11641 events), `harness.json` (the doctor's diagnosis at abort),
`launch/launch-node0.json`.  No book text.
