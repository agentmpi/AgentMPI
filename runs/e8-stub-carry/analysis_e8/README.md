## Where the time went (e8-stub-carry)

| quantity | value |
|---|---|
| wall | 1.0 min (bootstrap 0.0, pool 0.9, tail 0.1) |
| model rank-hours / waiting for work / blocked in collectives | 0.0 / 0.12 / 0.02 |
| busy share / idle share | 0.0% / 47.9% |
| pages per rank (min / mean / max) | 5 / 5.94 / 7 |
| pages stolen / items reclaimed / seams | 8 / 0 / 94 |
| model exchanges / spend | 0 / $0.0 |
| waiting for work, while work existed / for the last item | 0.09 / 0.02 rank-hours (s079-080, 0.0 min) |
| slowest single model call | 0 s (?, ?) |
| transport per page, by node (median / p90 s from translation done to pool done) | ; 0.0 rank-hours in total |

### The buffer against the prompt

| quantity | value |
|---|---|
| carry mode / window budget | yes / 12,000 |
| model calls seen / buffer high-water mark | 206 / 11,872 |
| prompt composed / buffer (median, p10-p90, n) | 0.691, 0.635-0.909, 206 |
| prompt billed / buffer (median, p10-p90, n) | None, None-None, 0 |
| correlation, buffer vs billed prompt | None |
| billed / buffer by task (median, n) |  |
| evictions / tokens evicted / degradations | 681 / 923,924 / 0 |
| ledger total, all ranks | 926,260 |

### Against e7-rawapi-p16

| | E7 (phases) | E8 (pool) |
|---|---|---|
| wall (min) | 51.4 | 1.0 |
| blocked rank-hours | 9.33 | 0.14 |
| work rank-hours | 4.2 | 0.0 |
| coordination / idle share | 68.0% | 47.9% |
| spend | $7.6078 | $0.0 |

### Ranks

| rank | pages | stolen | seams | model min | waited min | blocked min |
|---|---|---|---|---|---|---|
| 0 | 6 | 0 | 6 | 0.0 | 0.4 | 0.1 |
| 1 | 6 | 1 | 5 | 0.0 | 0.5 | 0.1 |
| 2 | 5 | 0 | 6 | 0.0 | 0.4 | 0.1 |
| 3 | 6 | 0 | 6 | 0.0 | 0.5 | 0.0 |
| 4 | 6 | 0 | 5 | 0.0 | 0.5 | 0.1 |
| 5 | 5 | 1 | 6 | 0.0 | 0.5 | 0.0 |
| 6 | 5 | 0 | 6 | 0.0 | 0.4 | 0.1 |
| 7 | 7 | 1 | 6 | 0.0 | 0.5 | 0.1 |
| 8 | 7 | 1 | 6 | 0.0 | 0.4 | 0.1 |
| 9 | 6 | 0 | 6 | 0.0 | 0.4 | 0.0 |
| 10 | 6 | 0 | 6 | 0.0 | 0.5 | 0.0 |
| 11 | 6 | 0 | 6 | 0.0 | 0.4 | 0.1 |
| 12 | 6 | 1 | 6 | 0.0 | 0.4 | 0.1 |
| 13 | 7 | 2 | 6 | 0.0 | 0.4 | 0.0 |
| 14 | 6 | 1 | 6 | 0.0 | 0.4 | 0.1 |
| 15 | 5 | 0 | 6 | 0.0 | 0.4 | 0.1 |
