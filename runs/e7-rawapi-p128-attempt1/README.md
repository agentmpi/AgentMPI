# e7-rawapi-p128-attempt1: 128 ranks over four machines, aborted at 65 minutes

The first multi-node production attempt, and a defect found by it.  Four cloud
sessions each ran 32 rank processes against the `gitd` device on the branch
`ampi-jobs/e7-rawapi-p128`; every node joined, all 128 ranks surveyed their
segment, the census allreduce lifted its conflicts and seven ranks arbitrated
them.  Then ranks that were alive and blocked in a collective began to be
convicted for silence, 57 of them within twenty minutes, and the run was
stopped.

| quantity | value |
|---|---|
| elapsed when aborted | 65.2 min |
| ranks / nodes | 128 / 4 (all joined) |
| tasks done | 135 (128 surveys, 7 arbitrations), 4 repairs |
| spend | $3.98 |
| suspicions / convictions / retractions | 884 / 452 events on 57 ranks / 0 |
| node-0 daemon at abort | 267 pushes, 560 rejected pushes, 1460 fetches, largest batch 256 |

## What happened

Every convicted rank was blocked in the same place: waiting in the
`census-settled` broadcast for the root, polling the device for the result.
Their processes were alive (blocked on the daemon's socket, no stderr).  They
were convicted because they had not renewed their lease, and they had not
renewed their lease because a lease renewal is a side effect of a runtime call
and their one outstanding call, a read, had not returned for longer than the
lease.

The read had not returned because, inside the daemon, readers took the same
lock as the batching writer.  The writer's CAS loop holds that lock for the
whole contest with the other three machines' pushes (560 rejections against
267 landed pushes), and a lock is not a queue: with thirty polling readers and
a writer that reacquires the moment it releases, an individual reader could
wait a quarter of an hour.  Rank 27 entered the census at minute three and its read was still outstanding at the abort, an hour later.  Nodes 0 and 3, whose
daemons were losing the push contest most often, lost most of their ranks;
nodes 1 and 2 lost none.  Ranks 100-115 on node 3 all died together, which is
what starvation of one daemon's readers looks like from the trace.

The trace records the anatomy exactly: `failure.suspect` with `silent_for`
between 811 s and 1457 s, `failure.convict` with `lease_expired`, and no
`failure.retract`, because a rank that cannot complete a read cannot retract.
The runtime behaved as specified: a silent member was dropped so the rest
could close.  The device was the defect, and the population was the
instrument that found it.

## The fix

`ampi/device/gitlog.py` `_snapshot`: readers take the lock only to fetch, and
when a writer fetched within the read interval, which under load is always,
they read the state file without any lock (the file is replaced atomically and
parsed once per version).  `ampi/device/gitd.py`: the batch window widens
while pushes are rejected and relaxes when they land, so a daemon losing the
push contest pushes less often with more in each push.  Tests:
`tests/test_gitd.py::test_readers_do_not_wait_for_a_busy_writer`,
`test_clients_survive_a_daemon_that_dies_mid_call`,
`test_batch_window_widens_under_rejection`.  The runs `e7-rawapi-p128` and
`e7-rawapi-p256` were relaunched on the fixed daemon.

## Files

`launch_plan.json`, `config.json`, `corpus_manifest.json`, `harness.trace.jsonl`
(4057 events), `harness.json` (the doctor's diagnosis at abort: `wedged`, with the
convicted and suspected ranks and the two open collectives), `launch/launch-node0.json`.
No book text.
