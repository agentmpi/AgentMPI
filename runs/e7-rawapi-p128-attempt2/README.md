# e7-rawapi-p128-attempt2: 128 ranks over four machines, killed at 123 minutes by the account's usage freeze

The relaunch of p = 128 on the fixed daemon (`e7-rawapi-p128b` on the job
branch).  Four nodes joined; all 128 ranks surveyed; the census closed and its
conflicts were arbitrated in parallel; the research agenda was posted and
research had begun (2 findings recorded) when every session on the account was
frozen by its five-hour usage limit at about 20:20 UTC, which stopped every
node's container at once.  The trace ends with the survivors convicting each
other as leases expired.

| quantity | value |
|---|---|
| elapsed | 123.1 min (18:27 to 20:31 UTC) |
| ranks / nodes joined | 128 / 4 |
| tasks done | 137 (128 surveys, 7 arbitrations, 2 research); spend $4.25 |
| restarts | 35 respawns: 32 of them node 0 rejoining after its VM was recycled, 3 executor failures |
| collective re-entries traced as replays | 32 |
| convicted ranks at the end | 70, all after the freeze |

## What this attempt established

*A node can rejoin a running job.*  Node 0's VM was recycled while this
session was idle (between 18:32 and 19:02 UTC), killing its 32 rank processes
and their launcher; the other three nodes convicted them one by one as leases
expired.  `ampirun --rejoin` (added for this) re-entered the job from the
existing branch, respawned every convicted rank with a fresh epoch without
spending the rank's own restart budget, and the successors replayed the closed
collectives (`task.replay` 32) and continued.  The population went on to close
the census, arbitrate and post the agenda with the rejoined ranks
participating.  The rejoin itself took 22 minutes at four-node push contention,
one push per respawn.

*Four machines is inside the transport's ceiling.*  Unlike the eight-machine
attempt, no rank was convicted for a push that would not land: every conviction
before the freeze is a rank whose process was actually dead.

*A session that hosts ranks must never be idle.*  A cloud session's VM is
reclaimed when the session is idle, so node 0 must keep its agent turn alive
for the whole run, as the child sessions do with their polling loops.

## Files

`launch_plan.json`, `config.json`, `corpus_manifest.json`, `harness.trace.jsonl`
(10937 events), `harness.json`, `launch/launch-node0.json` (with
`rejoined_epoch` per rank).  No book text.
