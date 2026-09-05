# e7-rawapi-p128-attempt3: 128 ranks over four machines, lost to a rolling upgrade at 70 minutes

The first attempt on the reviewed code.  All four nodes joined within eleven
minutes; 128 ranks surveyed; the census closed and eight arbitration batches
were ruled on in thirty minutes, the fastest of every attempt.  Then the root
spent twenty minutes posting forty-eight "unclaimed" research cells one push at
a time while 127 ranks waited at a barrier, and the operator found two defects
and fixed them mid-run: lease renewals had been shrinking every rank's lease to
the 180 s default, and the claim cells did not need posting at all.

| quantity | value |
|---|---|
| elapsed when aborted | 70 min |
| ranks / nodes | 128 / 4, all joined by minute 11 |
| tasks done | 152 (128 surveys, 8 arbitrations, 16 research); spend $5.10 |
| suspicions / convictions before the restart | 2479 / 22 (all node 0, whose daemon carried the root's serial pushes) |
| rank errors after the restart | 75, all `TypeError: RankView.__init__() got an unexpected keyword argument 'lease_s'` |

## What happened

The root was restarted onto the fixed code, as a rank restarted after a fix had
been in the 64-rank run.  Its successor wrote its rank row with a new field.
Every other rank still ran the old code, and the old code parsed rank rows with
a constructor that rejects unknown fields; seventy-five ranks on three machines
died inside the failure detector, reading the root's row.  The protocol was not
at fault and neither was the fix.  A job upgraded one process at a time is a job
in which two versions read each other's rows, and the older one must read what
it understands and ignore the rest.  `RankView.from_row` now does that, with a
test.

## Files

`launch_plan.json`, `config.json`, `corpus_manifest.json`, `harness.trace.jsonl`
(7279 events), `harness.json`, `launch/launch-node0.json`.  No book text.
