# e7-rawapi-p32

E7 production run: *32* ranks over *1* node(s) (1 distinct machine(s) recorded), device `sqlite`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 39.9 min |
| ranks seen / failed | 32 / 2 |
| restarts (recovered ranks) | 2 (1) |
| tasks done / repairs | 280 / 24 |
| executor rank-hours | 5.39 |
| blocked rank-hours | 15.57 |
| coordination share | 73.1% |
| achieved parallelism / efficiency | 8.1 / 25.3% |
| collectives (median / max of slowest wait) | 16 (2 s / 1032 s) |
| conflicts lifted | 53 |
| prompt / completion tokens | 2,567,111 / 933,590 |
| tool calls | 242 |
| spend | $9.40 |
| coverage of the book | 95.5% (1176 of 1232 paragraphs) |
| glossary / findings / sources | 721 / 48 / 121 |
| amendments / clashes | 276 / 1 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| qwen/qwen3.8-max | 43 | $1.26 |
| anthropic/claude-sonnet-5 | 38 | $1.54 |
| z-ai/glm-5.3 | 35 | $0.53 |
| deepseek/deepseek-v4-pro-0813 | 35 | $0.99 |
| openai/gpt-5.6-sol | 34 | $1.37 |
| x-ai/grok-4.6 | 34 | $0.89 |
| google/gemini-3.8-flash | 32 | $0.40 |
| moonshotai/kimi-k3 | 29 | $2.31 |

## Ranks

31 rank reports; recovered after a restart: [12].

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).
