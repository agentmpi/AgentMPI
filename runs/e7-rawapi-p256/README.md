# e7-rawapi-p256

E7 production run: *256* ranks over *4* node(s) (4 distinct machine(s) recorded), device `gitd`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 232.4 min |
| ranks seen / failed | 256 / 0 |
| restarts (recovered ranks) | 0 (0) |
| tasks done / repairs | 839 / 74 |
| executor rank-hours | 34.66 |
| blocked rank-hours | 714.31 |
| coordination share | 72.0% |
| achieved parallelism / efficiency | 8.9 / 3.5% |
| collectives (median / max of slowest wait) | 17 (700 s / 3871 s) |
| conflicts lifted | 220 |
| prompt / completion tokens | 4,712,328 / 3,056,596 |
| tool calls | 267 |
| spend | $23.38 |
| coverage of the book | 99.0% (1220 of 1232 paragraphs) |
| glossary / findings / sources | 3483 / 46 / 104 |
| amendments / clashes | 67 / 1 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| deepseek/deepseek-v4-pro-0813 | 112 | $2.80 |
| qwen/qwen3.8-max | 107 | $2.53 |
| moonshotai/kimi-k3 | 106 | $4.70 |
| openai/gpt-5.6-sol | 104 | $2.84 |
| google/gemini-3.8-flash | 103 | $0.84 |
| anthropic/claude-sonnet-5 | 103 | $2.94 |
| x-ai/grok-4.6 | 103 | $1.93 |
| z-ai/glm-5.3 | 101 | $4.42 |

## Ranks

64 rank reports; recovered after a restart: none.

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).

## What happened, in order

The first 256-rank run to finish, on the runtime as fixed by the 128-rank run
(`runs/e7-rawapi-p128`) the same morning: four machines of 64 processes, launched
14:52 UTC on 5 September 2026, finalised 18:42.

| interval (UTC) | what |
|---|---|
| 14:52–14:54 | node 0 creates the job; nodes 1–3 join within two minutes |
| 14:53–15:10 | 256 surveys |
| 15:12–15:26 | census reduction and 10 arbitration batches |
| 15:38–16:08 | 48 research findings; three research ranks run past the 1800 s lease behind Wikipedia's rate limit and are convicted alive |
| 16:20–16:31 | research fence, findings read, glossary reduction and arbitration |
| 16:31–17:05 | offsets and the translate barrier: 34 minutes of pure coordination |
| 17:05–17:26 | 268 translation chunks |
| 17:17–18:03 | 256 seam revisions; nine more ranks convicted alive during long seam tasks |
| 18:03–18:42 | the spend reduction and the manifest gather, 40 minutes; every rank finalises, the convicted ones included |

Every task was done by 18:03; the last 40 minutes and the 34 before translation
were the population polling its collectives. A rank waiting in a collective
reads every member's row and the participant list on every poll, so p ranks
polling cost p² reads, all through one daemon per machine that also parses a
38 MB state file on every commit; node 0's daemon ran at 85% of a core. The
twelve ranks convicted alive were each re-convicted by every polling peer, 355
records for twelve victims. Both are runtime costs, not protocol ones, and both
are named in `NODES.md` with the fixes made (the executor renews the lease
between model rounds; a conviction is one record) and the two still to make.

Against `e7-rawapi-p128` on the same machines: the same book, 839 tasks against
479, $23.38 against $13.70, 99.0% against 99.2% covered, 232 minutes against 137.
Model time did not grow — translation took 21 minutes against 27, the seams 46
against 63 — and coordination took the rest: 72% of rank-time against 34%.

## Reviewing the model exchanges

```bash
python -m ampitools.calls runs/e7-rawapi-p256/harness.trace.jsonl --summary
python -m ampitools.calls runs/e7-rawapi-p256/harness.trace.jsonl --label research
```

1,046 exchanges: 34 ended in the provider's own `error` finish (qwen 20, deepseek
12, glm 2), 22 were cut by `length`, and 7 tasks failed their contract after
three attempts. Of 267 tool calls 74 failed, 66 of them Wikipedia's `429 Too
Many Requests`: forty-eight researchers behind four egress addresses are still
one very busy client to an encyclopaedia, and the per-node throttle is not a
per-population one.
