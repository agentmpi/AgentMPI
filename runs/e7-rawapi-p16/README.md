# e7-rawapi-p16

E7 production run: *16* ranks over *1* node(s) (1 distinct machine(s) recorded), device `sqlite`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 51.4 min |
| ranks seen / failed | 16 / 3 |
| restarts (recovered ranks) | 3 (3) |
| tasks done / repairs | 241 / 27 |
| executor rank-hours | 4.20 |
| blocked rank-hours | 9.33 |
| coordination share | 68.0% |
| achieved parallelism / efficiency | 4.9 / 30.6% |
| collectives (median / max of slowest wait) | 16 (1 s / 1704 s) |
| conflicts lifted | 22 |
| prompt / completion tokens | 2,544,365 / 806,824 |
| tool calls | 276 |
| spend | $7.61 |
| coverage of the book | 98.5% (1214 of 1232 paragraphs) |
| glossary / findings / sources | 380 / 48 / 121 |
| amendments / clashes | 360 / 2 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| qwen/qwen3.8-max | 37 | $1.13 |
| deepseek/deepseek-v4-pro-0813 | 32 | $0.75 |
| openai/gpt-5.6-sol | 29 | $1.21 |
| anthropic/claude-sonnet-5 | 29 | $1.41 |
| x-ai/grok-4.6 | 29 | $0.82 |
| moonshotai/kimi-k3 | 29 | $1.48 |
| google/gemini-3.8-flash | 28 | $0.35 |
| z-ai/glm-5.3 | 28 | $0.46 |

## Ranks

16 rank reports; recovered after a restart: [10, 2, 4].

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).
