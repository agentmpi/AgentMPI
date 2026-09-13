# e8-rawapi-p16-carry

E8 production run: *16* ranks over *1* node(s) (1 distinct machine(s) recorded), device `sqlite`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 21.4 min |
| ranks seen / failed | 16 / 0 |
| restarts (recovered ranks) | 0 (0) |
| tasks done / repairs | 211 / 15 |
| executor rank-hours | 2.61 |
| blocked rank-hours | 0.75 |
| coordination share | 13.2% |
| achieved parallelism / efficiency | 7.3 / 45.7% |
| collectives (median / max of slowest wait) | 6 (4 s / 130 s) |
| conflicts lifted | 31 |
| prompt / completion tokens | 873,460 / 701,135 |
| tool calls | 0 |
| spend | $6.16 |
| coverage of the book | 99.2% (1222 of 1232 paragraphs) |
| glossary / findings / sources | 400 / None / None |
| amendments / clashes | 150 / 0 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| google/gemini-3.8-flash | 64 | $0.52 |
| z-ai/glm-5.3 | 31 | $0.56 |
| openai/gpt-5.6-sol | 29 | $0.92 |
| anthropic/claude-sonnet-5 | 25 | $0.94 |
| moonshotai/kimi-k3 | 23 | $1.90 |
| x-ai/grok-4.6 | 18 | $0.45 |
| deepseek/deepseek-v4-pro-0813 | 12 | $0.56 |
| qwen/qwen3.8-max | 9 | $0.32 |

## Ranks

16 rank reports; recovered after a restart: none.

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).

## What this run is

The book by the E8 pool harness in **carry mode** (S6.1): sixteen ranks on one
machine over SQLite, a 32,000-token window, the prompt's shared material read
through the runtime's buffer and room made by eviction between pages. Its
purpose is the buffer, not the wall: it holds what the runtime thinks each
prompt carries against what the provider billed for it.

| quantity | value |
|---|---|
| model calls / evictions / degradations | 207 / 664 / 0 |
| buffer high-water mark | 21,997 of 32,000 |
| ledger, all ranks | 1,543,822 tokens consumed; 1,540,350 evicted |
| billed prompt ÷ buffer, median (p10–p90) | 0.61 (0.42–0.78), n = 207 |
| correlation, buffer vs billed | 0.80 |
| by task, median | survey 0.67, translate 0.68, seam 0.47, arbitrate 0.14 |
| coverage / seams revised / spend | 99.2% / 29 of 94 / $6.16 |

The ratio is below one and the reason is measurable: the runtime counts a
body as it was delivered, canonical JSON with its keys and quoting, while the
prompt renders the same units as text; the executor's instructions and the
response format add to the prompt and are not bodies at all. A seam's prompt
uses two edges of the two edge cells it reads, so its ratio is lower; an
arbitration's conflict set is the runtime's structure, so its ratio is lowest.
What the correlation says is that the buffer moves with the prompt, which is
what a flow-control quantity has to do.

The ledger is 48 times the window and no read was ever degraded: the buffer
governed admission and the ledger only counted. The first attempt
(`runs/e8-rawapi-p16-carry-attempt1`) is the same harness before it charged
what it carried, and its README says what the buffer found there.

The pool numbers above are one machine's and are not the E8 pool result;
`runs/e8-rawapi-p16` and `-run2` are. The 2.04 rank-hours waiting for the last
item is one seam's model call of 601 seconds (glm-5.3) with the pool otherwise
empty, the shape `-run2` first reported.
