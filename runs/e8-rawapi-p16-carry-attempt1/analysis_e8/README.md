## Where the time went (e8-rawapi-p16-carry-attempt1)

| quantity | value |
|---|---|
| wall | 34.5 min (bootstrap 17.6, pool 16.7, tail 0.2) |
| model rank-hours / waiting for work / blocked in collectives | 2.81 / 2.24 / 4.13 |
| busy share / idle share | 30.6% / 69.3% |
| pages per rank (min / mean / max) | 2 / 5.94 / 13 |
| pages stolen / items reclaimed / seams | 27 / 0 / 94 |
| model exchanges / spend | 172 / $6.16 |
| waiting for work, while work existed / for the last item | 0.29 / 1.95 rank-hours (s059-060, 0.0 min) |
| slowest single model call | 781.7 s (deepseek-v4-pro-0813, translate:p059) |
| transport per page, by node (median / p90 s from translation done to pool done) | node0: 0.0 / 0.0 (45 pages); node1: 0.0 / 0.0 (50 pages); 0.0 rank-hours in total |

### The buffer against the prompt

| quantity | value |
|---|---|
| carry mode / window budget | yes / 32,000 |
| model calls seen / buffer high-water mark | 142 / 31,995 |
| prompt composed / buffer (median, p10-p90, n) | 0.253, 0.063-40.688, 142 |
| prompt billed / buffer (median, p10-p90, n) | 0.202, 0.054-27.237, 142 |
| correlation, buffer vs billed prompt | -0.491 |
| billed / buffer by task (median, n) | survey: 43.335 (16); arbitrate: 0.135 (2); translate: 0.208 (95); seam: 0.055 (29) |
| evictions / tokens evicted / degradations | 532 / 1,969,580 / 131 |
| ledger total, all ranks | 2,292,588 |

### Against e7-rawapi-p16

| | E7 (phases) | E8 (pool) |
|---|---|---|
| wall (min) | 51.4 | 34.5 |
| blocked rank-hours | 9.33 | 6.37 |
| work rank-hours | 4.2 | 2.81 |
| coordination / idle share | 68.0% | 69.3% |
| spend | $7.6078 | $6.16 |

### Ranks

| rank | pages | stolen | seams | model min | waited min | blocked min |
|---|---|---|---|---|---|---|
| 0 | 3 | 0 | 2 | 13.6 | 8.5 | 12.3 |
| 1 | 8 | 2 | 1 | 18.0 | 2.8 | 13.6 |
| 2 | 2 | 0 | 1 | 8.7 | 9.6 | 16.2 |
| 3 | 12 | 6 | 10 | 6.4 | 10.7 | 17.3 |
| 4 | 4 | 0 | 6 | 6.8 | 10.7 | 16.9 |
| 5 | 5 | 0 | 2 | 11.5 | 5.7 | 17.2 |
| 6 | 4 | 1 | 3 | 7.4 | 10.0 | 17.0 |
| 7 | 7 | 1 | 13 | 7.1 | 10.5 | 16.8 |
| 8 | 2 | 0 | 2 | 30.1 | 0.0 | 4.3 |
| 9 | 5 | 0 | 1 | 13.7 | 4.0 | 16.7 |
| 10 | 2 | 1 | 2 | 10.5 | 8.4 | 15.6 |
| 11 | 11 | 5 | 29 | 6.3 | 10.8 | 17.3 |
| 12 | 5 | 0 | 2 | 7.5 | 10.5 | 16.4 |
| 13 | 13 | 8 | 12 | 7.3 | 10.7 | 16.4 |
| 14 | 4 | 0 | 6 | 6.8 | 10.7 | 17.0 |
| 15 | 8 | 3 | 2 | 6.9 | 10.7 | 16.8 |
