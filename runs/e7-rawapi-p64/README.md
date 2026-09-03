# e7-rawapi-p64

E7 production run: *64* ranks over *1* node(s) (1 distinct machine(s) recorded), device `sqlite`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 37.9 min |
| ranks seen / failed | 64 / 20 |
| restarts (recovered ranks) | 20 (18) |
| tasks done / repairs | 366 / 36 |
| executor rank-hours | 6.56 |
| blocked rank-hours | 32.73 |
| coordination share | 81.0% |
| achieved parallelism / efficiency | 10.4 / 16.2% |
| collectives (median / max of slowest wait) | 16 (6 s / 1008 s) |
| conflicts lifted | 104 |
| prompt / completion tokens | 3,096,693 / 1,269,548 |
| tool calls | 257 |
| spend | $11.06 |
| coverage of the book | 96.0% (1183 of 1232 paragraphs) |
| glossary / findings / sources | 1404 / 48 / 120 |
| amendments / clashes | 207 / 4 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| deepseek/deepseek-v4-pro-0813 | 49 | $1.45 |
| google/gemini-3.8-flash | 48 | $0.52 |
| moonshotai/kimi-k3 | 48 | $2.61 |
| z-ai/glm-5.3 | 46 | $0.79 |
| anthropic/claude-sonnet-5 | 45 | $1.76 |
| openai/gpt-5.6-sol | 44 | $1.56 |
| x-ai/grok-4.6 | 44 | $1.07 |
| qwen/qwen3.8-max | 42 | $1.20 |

## Ranks

62 rank reports; recovered after a restart: [16, 18, 23, 24, 25, 29, 31, 32, 33, 34, 40, 41, 42, 44, 47, 48, 50, 52].

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).
