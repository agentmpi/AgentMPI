# e7-rawapi-p256-attempt2: 256 ranks over four machines, killed at 42 minutes by the account's usage freeze

The relaunch of p = 256 as four machines of 64 ranks (`e7-rawapi-p256c`),
after eight machines of 32 saturated the transport.  Three of the four nodes
joined (the fourth session did not act on its instruction before the freeze);
192 ranks surveyed their segments in 30 minutes and had entered the census
allreduce when the account-wide usage freeze at about 20:20 UTC stopped every
container.

| quantity | value |
|---|---|
| elapsed | 42.1 min (19:52 to 20:34 UTC) |
| ranks / nodes joined | 192 / 3 of 4 |
| tasks done | 192 surveys; spend $4.73 |
| convicted before the freeze | 0 |

Sixty-four rank processes per machine ran without incident: 64 clients on one
daemon, 64 model calls in flight, no starvation.  With only three daemons
writing, the push contest was mild, which is the configuration the 256-rank
run should be repeated in.

## Files

`launch_plan.json`, `config.json`, `corpus_manifest.json`, `harness.trace.jsonl`
(5396 events), `harness.json`, `launch/launch-node0.json`.  No book text.
