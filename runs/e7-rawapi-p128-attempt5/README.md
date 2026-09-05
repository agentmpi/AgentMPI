# e7-rawapi-p128-attempt5: 128 ranks over four machines, a read that was a write

The fifth attempt, launched 05:25 UTC on 5 September on the daemon with the
reader fixes of attempt 4 and the claim-from-absence research agenda. Every
phase up to the research fence went as designed: four nodes joined within
thirty minutes, 128 surveys, 7 arbitration batches, 48 research findings each
claimed by exactly one rank, the fence closed at 06:46. Then nothing happened
for forty-six minutes: no model call, no failure, the provider's dashboard
flat. Aborted at 07:30 when the conviction cascade began.

| quantity | value |
|---|---|
| elapsed when aborted | 128 min |
| tasks done | 183 (128 surveys, 7 arbitrations, 48 research); spend $4.73 |
| fence closed → first glossary arrival | 16 min (nodes 1–3), 46 min and counting (node 0) |
| convictions | 534 events against 23 node-0 ranks, all `lease_expired` (lease 1800 s) |
| trace | `harness.trace.jsonl`, 7849 events |

## What happened

After the fence each rank reads the forty-eight findings back from the research
window. The runtime charges the rank's context ledger for every delivery, which
the specification requires, and it persisted each charge by writing the rank row
— a compare-and-swap through the daemon, one group commit per read, a dozen
seconds each at four nodes and slower on node 0, whose daemon also carried the
job's root. Forty-eight reads per rank, all thirty-two ranks of a node in step,
and no heartbeat in between because the rank was blocked inside its own write:
`py-spy dump` on any node-0 rank showed the same stack,
`_read_findings → get → charge → _write_rank → cas → readinto`. Nodes 1–3
reached the glossary reduction after sixteen minutes; node 0's ranks were at
finding 45 of 48 when their 1800-second leases lapsed and the waiting peers
began convicting them.

The model calls themselves were fine. `python -m ampitools.calls
harness.trace.jsonl --rank 24` shows rank 24's research task: six rounds, ten
tool calls, 405 s of wall time of which 47 s were the provider's, $0.02. It also
shows two tool defects that were fixed with this attempt: Wikipedia answering
`429 Too Many Requests` to a population that shares one egress, and
`fetch_url` failing on every Cyrillic URL with `UnicodeEncodeError`.

## What changed because of it

* `ampi/core/base.py`: a ledger charge is local until the rank row is next
  written for another reason; only a degradation is written at once
  (`spec/AgentMPI-1.0.md` S6.1). A read is never a device mutation.
* `ampitools/model.py`: one `task.call` event per request to the provider and
  arguments and outcome on every `task.tool`, so the shape of every conversation
  is in the trace; `ampitools/calls.py` reads them back.
* `ampitools/tools.py`: URLs are percent-encoded, requests to one host are spaced
  per node, `Retry-After` is honoured.

The next attempt (`e7-rawapi-p128`) ran on this code.
