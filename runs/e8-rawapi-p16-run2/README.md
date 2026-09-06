# e8-rawapi-p16-run2

E8 production run: *16* ranks over *2* node(s) (2 distinct machine(s) recorded), device `gitd`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 74.1 min |
| ranks seen / failed | 16 / 0 |
| restarts (recovered ranks) | 0 (0) |
| tasks done / repairs | 212 / 20 |
| executor rank-hours | 4.39 |
| blocked rank-hours | 3.22 |
| coordination share | 16.3% |
| achieved parallelism / efficiency | 3.6 / 22.2% |
| collectives (median / max of slowest wait) | 6 (33 s / 740 s) |
| conflicts lifted | 28 |
| prompt / completion tokens | 864,383 / 738,921 |
| tool calls | 0 |
| spend | $5.96 |
| coverage of the book | 100.0% (1232 of 1232 paragraphs) |
| glossary / findings / sources | 391 / None / None |
| amendments / clashes | 149 / 0 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| google/gemini-3.8-flash | 37 | $0.39 |
| moonshotai/kimi-k3 | 31 | $1.30 |
| anthropic/claude-sonnet-5 | 29 | $1.11 |
| openai/gpt-5.6-sol | 29 | $0.85 |
| x-ai/grok-4.6 | 28 | $0.59 |
| deepseek/deepseek-v4-pro-0813 | 22 | $0.78 |
| z-ai/glm-5.3 | 20 | $0.34 |
| qwen/qwen3.8-max | 16 | $0.46 |

## Ranks

8 rank reports; recovered after a restart: none.

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).

## The second sample of the same configuration

The job's name on the branch is `e8-rawapi-p16r`. Same book, same sixteen ranks
over two machines, same model pool and the same harness as `runs/e8-rawapi-p16`;
the runtime is the one with S6.1's resident set, so this run is also the
regression check for it. It covered the whole book — 1232 of 1232 paragraphs —
where the first sample left 17 paragraphs unrendered.

| | run 1 | run 2 |
|---|---|---|
| wall | 58.3 min | 74.1 min |
| coverage | 98.6% | 100% |
| spend | $6.72 | $5.96 |
| model rank-hours | 4.77 | 4.59 |
| waiting for work, while work existed | 0.74 rank-h | 0.41 rank-h |
| waiting for the last item | 0.46 rank-h | 7.10 rank-h |
| slowest single model call | 380 s | 1,952 s |
| pages stolen / items reclaimed | 15 / 0 | 16 / 1 |

The two totals differ by a factor of two and all of it is in one place. A pool
cannot finish before its last item, so once one item is left the rest of the
population has nothing to do for as long as that item takes. Here the last item
was the seam between pages 89 and 90, whose second model call ran for 1,952
seconds and returned normally — not a failure, not a rate limit, just a model
taking half an hour. Fourteen ranks waited 24 minutes for it, which is 7.10 of
the run's 7.52 rank-hours of waiting and more than the population's entire model
work.

The call also outlasted its rank's 1800-second lease, so a peer convicted the
holder, reclaimed the item and did the seam again. Both renderings were correct
and the later write won. That is the pool's reclaim rule (S9.5) behaving exactly
as specified, and it is also the cost of the rule: a straggler is paid for twice.

What this says about the pool result is that it has to be stated as two numbers.
The idleness that comes from phase structure is gone in both runs, by an order
of magnitude against E7's 9.33 blocked rank-hours. The idleness that comes from
the tail of the executor's latency distribution is untouched, and at this scale
the tail is one call. Getting it back would need speculative replication of the
last items, which is a scheduling policy rather than a protocol mechanism.
