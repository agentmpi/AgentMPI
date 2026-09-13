# e8-stub-carry

E8 production run: *16* ranks over *1* node(s) (0 distinct machine(s) recorded), device `sqlite`, executor `stub`, reasoning `low`.

| quantity | value |
|---|---|
| wall | 1.0 min |
| ranks seen / failed | 16 / 0 |
| restarts (recovered ranks) | 0 (0) |
| tasks done / repairs | None / 0 |
| executor rank-hours | 0.00 |
| blocked rank-hours | 0.02 |
| coordination share | 6.1% |
| achieved parallelism / efficiency | 0.0 / 0.0% |
| collectives (median / max of slowest wait) | 6 (1 s / 4 s) |
| conflicts lifted | 2 |
| prompt / completion tokens | 0 / 0 |
| tool calls | 0 |
| spend | $0.00 |
| coverage of the book | 100.0% (1232 of 1232 paragraphs) |
| glossary / findings / sources | 37 / None / None |
| amendments / clashes | 15 / 0 |

## Ranks

16 rank reports; recovered after a restart: none.

## Files

`launch_plan.json` (every rank requested, before any ran), `config.json`, `corpus_manifest.json` (segment digests), `harness.trace.jsonl`, `harness.json` (diagnosis), `report.json` (the driver's summary), `glossary.json`, `findings.json`, `amendments.json`, `sample_page13.json`, `launch/` (per-node launch records), `ranks/` (per-rank reports), `analysis/` (`ampi analyze`).
