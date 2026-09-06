# e8-rawapi-p16

E8 production run: *16* ranks over *2* node(s) (2 distinct machine(s) recorded), device `gitd`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 58.3 min |
| ranks seen / failed | 16 / 0 |
| restarts (recovered ranks) | 0 (0) |
| tasks done / repairs | 214 / 24 |
| executor rank-hours | 4.36 |
| blocked rank-hours | 3.05 |
| coordination share | 19.6% |
| achieved parallelism / efficiency | 4.5 / 28.0% |
| collectives (median / max of slowest wait) | 6 (70 s / 551 s) |
| conflicts lifted | 30 |
| prompt / completion tokens | 926,483 / 866,708 |
| tool calls | 0 |
| spend | $6.72 |
| coverage of the book | 98.6% (1215 of 1232 paragraphs) |
| glossary / findings / sources | 372 / None / None |
| amendments / clashes | 149 / 0 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| google/gemini-3.8-flash | 36 | $0.35 |
| anthropic/claude-sonnet-5 | 31 | $1.28 |
| moonshotai/kimi-k3 | 30 | $1.30 |
| openai/gpt-5.6-sol | 29 | $0.91 |
| x-ai/grok-4.6 | 28 | $0.59 |
| deepseek/deepseek-v4-pro-0813 | 25 | $0.73 |
| qwen/qwen3.8-max | 19 | $0.57 |
| z-ai/glm-5.3 | 16 | $0.48 |

## Ranks

8 rank reports; recovered after a restart: none.

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).

## What happened, in order

The job's name on the branch is `e8-rawapi-p16c`; two earlier attempts stopped
on defects the run itself found (below). Sixteen ranks, eight on each of two
machines, 6 September 2026.

| interval (UTC) | what |
|---|---|
| 23:12–23:14 | node 0 creates the job; node 1 joins |
| 23:14–23:27 | 16 surveys of the home blocks, then the census reduction and two arbitration batches |
| 23:27 | the root publishes the settled glossary with a nonblocking broadcast and claims its first page without waiting for a single receiver |
| 23:27–00:07 | the pool: 95 pages and 94 seams, claimed 189 times, no barrier between them |
| 00:07–00:10 | the spend reduction, the manifest gather, 16 finalisations |

Every rank was translating within seconds of the glossary being bound, and no
rank waited for another between there and the end. The one page nobody's home
block claimed was picked up by a rank that had run out of its own.

## What the pool bought, and what the comparison costs

| | E7 (phases, 1 machine, sqlite) | E8 (pool, 2 machines, gitd) |
|---|---|---|
| wall | 51.4 min | 58.3 min |
| model rank-hours | 4.2 | 4.77 |
| blocked / waiting rank-hours | 9.33 | 4.24 |
| idle share of rank-time | 68.0% | 27.3% |
| coverage | 98.5% | 98.6% |
| spend | $7.61 | $6.72 |
| pages per rank (min / mean / max) | 1 segment each | 3 / 5.9 / 9 |

Two thirds of E7's idleness at this scale was the phase structure, not the
models. The wall times are not comparable: E8 ran over two machines on a
transport whose every operation is a git round trip, and the trace prices it at
54 to 60 seconds per page between a translation finishing and its pool item
being marked done — 1.5 rank-hours over the run, most of the seven-minute gap.
The idle share is therefore a lower bound on what the pool bought. The clean
control is E7's harness at p=16 over two machines on `gitd`, which has not been
run.

15 pages were translated by a rank other than their block's owner; no item was
reclaimed, because no rank died. `analysis_e8/` has the per-rank table, the
timeline and the macros the paper uses.

## The two attempts before this one

Neither reached the pool's end, and both found defects that are fixed:

* **First** (`runs/e8-rawapi-p16-attempt1`, kept): one machine stalled ten
  minutes with no failure anywhere. Its ranks were blocked appending their own
  trace events while its daemon lost every push race to the other machine, whose
  translators were pushing back to back; the loser's backoff grew with each
  defeat and the winner never paused. A writer now yields after a push and when
  it sees another machine's commits land, and a trace append is acknowledged
  when queued rather than when pushed (spec S13, with `Device.flush` so reading
  a trace lands it).
* **Second**: eight ranks died mid-translation on `http.client.IncompleteRead`.
  It is not an `OSError`, so a provider closing a chunked response early escaped
  the model client's transport handler. Every failure to read a reply is now a
  retryable transport fault.
