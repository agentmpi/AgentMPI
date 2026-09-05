# e7-rawapi-p128

E7 production run: *128* ranks over *4* node(s) (10 distinct machine(s) recorded), device `gitd`, executor `model`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 428.7 min |
| ranks seen / failed | 128 / 27 |
| restarts (recovered ranks) | 0 (28) |
| tasks done / repairs | 479 / 52 |
| executor rank-hours | 18.86 |
| blocked rank-hours | 117.51 |
| coordination share | 12.8% |
| achieved parallelism / efficiency | 2.6 / 2.1% |
| collectives (median / max of slowest wait) | 16 (214 s / 1768 s) |
| conflicts lifted | 160 |
| prompt / completion tokens | 3,020,487 / 1,554,941 |
| tool calls | 252 |
| spend | $13.70 |
| coverage of the book | 99.2% (1222 of 1232 paragraphs) |
| glossary / findings / sources | 2557 / 48 / 129 |
| amendments / clashes | 161 / 5 |

## Executors by model

| model | tasks | spend |
|---|---|---|
| deepseek/deepseek-v4-pro-0813 | 66 | $2.52 |
| z-ai/glm-5.3 | 61 | $0.88 |
| google/gemini-3.8-flash | 59 | $0.59 |
| anthropic/claude-sonnet-5 | 59 | $2.03 |
| x-ai/grok-4.6 | 59 | $1.28 |
| moonshotai/kimi-k3 | 59 | $2.87 |
| openai/gpt-5.6-sol | 58 | $1.89 |
| qwen/qwen3.8-max | 58 | $1.59 |

## Ranks

32 rank reports; recovered after a restart: [0].

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).

## What happened, in order

The job's name on the branch is `e7-rawapi-p128u`; this is the sixth 128-rank
attempt and the first to finish. The wall figure above spans 07:31 to 14:39 UTC
on 5 September 2026 and needs reading in three parts.

| interval (UTC) | what | 
|---|---|
| 07:25–07:37 | node 0 creates the job; nodes 1–3 join |
| 07:32–07:49 | 128 surveys |
| 07:54–07:57 | census reduction, 7 arbitration batches in parallel |
| 08:04–08:16 | 48 research findings, one rank per term |
| 08:19–08:26 | research fence; findings read; glossary reduction (4 min, against 46 min in attempt 5) |
| 08:33–09:00 | 168 translation chunks |
| 08:42–09:45 | 128 seam revisions |
| 09:48 | **every session on the account frozen by its usage limit**, 126 ranks at the spend reduction, rank 80 in its seam task; all model work done, $13.64 spent |
| 10:53–10:57 | the four nodes rejoin from the branch (`node.sh … rejoin`); every rank replays its program from the start, each replayed operation a device round trip |
| 11:21–12:04 | 30 ranks die with `ValueError` in the translate replay: the amendment ledger came back degraded because a replaying rank's context ledger was full — fixed (release at the translate boundary; the amendment ledger is read uncharged); nodes 1 and 2 replaced |
| 12:06 | the spend reduction completes with the two ranks that had exhausted their restarts dropped (96, 108); 61 ranks finalise |
| 12:19 | the root, respawned, dies at the research fence with 6 tokens of ledger left: a successor inherited its predecessor's consumption — fixed in the runtime (a new epoch starts with an empty ledger) |
| 13:19–13:50 | the root replays again and joins the final gather; the machine's disk fills (14 GB of device history) and the daemon fails its append |
| 14:01–14:34 | the root replays a third time, gathers the manifest and finalises; the driver assembles the book |

What the run measures, then, is two things. Up to 09:48 it is the 128-rank
production run the series wanted: 137 minutes from launch to the final
reduction with 4 minutes of coordination between fence and glossary, $13.64,
479 tasks, 650 model exchanges and 252 tool calls in the trace. After 09:48 it
is a recovery experiment nobody designed: a whole population frozen and
brought back, with three defects the replays found and fixed on the way.

## Reviewing the model exchanges

```bash
python -m ampitools.calls runs/e7-rawapi-p128/harness.trace.jsonl --summary
python -m ampitools.calls runs/e7-rawapi-p128/harness.trace.jsonl --rank 24
```

Of 650 exchanges 21 ended with `finish_reason: error` (a provider-side failure,
mostly `deepseek`), 8 tasks failed their contract after three attempts, and 3 of
252 tool calls failed — none of them the `429` or `UnicodeEncodeError` of
attempt 5.
