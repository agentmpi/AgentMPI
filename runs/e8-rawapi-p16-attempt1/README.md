# e8-rawapi-p16-attempt1

## This attempt did not finish

Kept as evidence. Two machines shared one compare-and-swap ref, and the faster
loop won every push race: node 0's eight ranks sat ten minutes blocked appending
their own trace events while its daemon lost every push to node 1, whose eight
translators were pushing back to back. The loser's backoff grew with each defeat
and the winner never paused, so the loser's fetch-commit-push never fitted in a
gap. Both nodes were restarted with `rejoin` twice; the pool returned each rank
the item it still held (`pool.resume` in the trace) and nothing was retranslated.
The run that finished is `runs/e8-rawapi-p16`; the fixes are in its README.

The numbers below are therefore a stalled population's, and the four machines
counted are the restarts, not a four-machine job.


E8 production run: *16* ranks over *2* node(s) (4 distinct machine(s) recorded), device `gitd`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 77.4 min |
| ranks seen / failed | 16 / 0 |
| restarts (recovered ranks) | 0 (0) |
| tasks done / repairs | 212 / 24 |
| executor rank-hours | 3.92 |
| blocked rank-hours | 21.91 |
| coordination share | 106.2% |
| achieved parallelism / efficiency | 3.0 / 19.0% |
| collectives (median / max of slowest wait) | 8 (307 s / 2881 s) |
| conflicts lifted | 22 |
| prompt / completion tokens | 1,004,669 / 834,732 |
| tool calls | 0 |
| spend | $6.39 |
| coverage of the book | 99.4% (1225 of 1232 paragraphs) |
| glossary / findings / sources | 421 / None / None |
| amendments / clashes | 134 / 0 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| google/gemini-3.8-flash | 35 | $0.36 |
| z-ai/glm-5.3 | 32 | $0.68 |
| openai/gpt-5.6-sol | 31 | $0.94 |
| anthropic/claude-sonnet-5 | 29 | $1.14 |
| x-ai/grok-4.6 | 27 | $0.55 |
| moonshotai/kimi-k3 | 27 | $1.59 |
| deepseek/deepseek-v4-pro-0813 | 21 | $0.70 |
| qwen/qwen3.8-max | 10 | $0.43 |

## Ranks

8 rank reports; recovered after a restart: none.

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).
