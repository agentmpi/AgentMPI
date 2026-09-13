# e8-rawapi-p16-carry-attempt1

E8 production run: *16* ranks over *1* node(s) (1 distinct machine(s) recorded), device `sqlite`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 34.5 min |
| ranks seen / failed | 16 / 0 |
| restarts (recovered ranks) | 0 (0) |
| tasks done / repairs | 151 / 21 |
| executor rank-hours | 2.78 |
| blocked rank-hours | 4.13 |
| coordination share | 44.9% |
| achieved parallelism / efficiency | 4.8 / 30.3% |
| collectives (median / max of slowest wait) | 6 (5 s / 788 s) |
| conflicts lifted | 29 |
| prompt / completion tokens | 877,671 / 685,294 |
| tool calls | 0 |
| spend | $6.16 |
| coverage of the book | 98.9% (1219 of 1232 paragraphs) |
| glossary / findings / sources | 425 / None / None |
| amendments / clashes | 141 / 0 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| google/gemini-3.8-flash | 38 | $0.46 |
| z-ai/glm-5.3 | 25 | $0.53 |
| openai/gpt-5.6-sol | 20 | $0.79 |
| moonshotai/kimi-k3 | 19 | $2.26 |
| anthropic/claude-sonnet-5 | 18 | $0.87 |
| x-ai/grok-4.6 | 13 | $0.37 |
| deepseek/deepseek-v4-pro-0813 | 10 | $0.65 |
| qwen/qwen3.8-max | 8 | $0.22 |

## Ranks

16 rank reports; recovered after a restart: none.

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).

## What this attempt is

The first real-model run with the harness in carry mode (S6.1: the prompt's
shared material read through the runtime's buffer, a 32,000-token window),
kept because the buffer found two things wrong with the harness before any
model did.

The harness pinned the whole settled glossary in every window --- about
15,000 tokens --- while each prompt used the few dozen terms its page needed,
and it read both whole segments for a seam whose prompt used only their edges.
The buffer reported both: 131 degradations, all of them segment reads that did
not fit beside the glossary, and a billed prompt that was a fifth of what the
buffer carried (median 0.20 for a translation, 0.06 for a seam). Under the
first runtime, whose gate was a counter the harness zeroed after every task,
neither would have shown; the window would have carried five times the prompt
and nobody would have counted.

The rule that fixed it is MPI's: the buffer holds what the call carries, so a
harness that filters a received body before sending it charges the projection
at the body's address, not the body. The run that does that is
`runs/e8-rawapi-p16-carry`. Seams here were mostly skipped on degraded reads
(4 revised of 94), which is why coverage is 98.9% with 13 paragraphs missing.
