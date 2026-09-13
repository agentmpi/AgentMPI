## Where the time went (e8-rawapi-p16-carry)

| quantity | value |
|---|---|
| wall | 21.4 min (bootstrap 3.8, pool 17.5, tail 0.1) |
| model rank-hours / waiting for work / blocked in collectives | 2.7 / 2.26 / 0.75 |
| busy share / idle share | 47.1% / 52.6% |
| pages per rank (min / mean / max) | 1 / 5.94 / 12 |
| pages stolen / items reclaimed / seams | 24 / 0 / 94 |
| model exchanges / spend | 226 / $6.16 |
| waiting for work, while work existed / for the last item | 0.22 / 2.04 rank-hours (s014-015, 0.1 min) |
| slowest single model call | 601.1 s (glm-5.3, seam:s040-041) |
| transport per page, by node (median / p90 s from translation done to pool done) | node0: 0.0 / 0.0 (43 pages); node1: 0.0 / 0.0 (52 pages); 0.0 rank-hours in total |

### The buffer against the prompt

| quantity | value |
|---|---|
| carry mode / window budget | yes / 32,000 |
| model calls seen / buffer high-water mark | 207 / 21,997 |
| prompt composed / buffer (median, p10-p90, n) | 0.721, 0.561-0.935, 207 |
| prompt billed / buffer (median, p10-p90, n) | 0.612, 0.423-0.777, 207 |
| correlation, buffer vs billed prompt | 0.798 |
| billed / buffer by task (median, n) | survey: 0.667 (16); arbitrate: 0.14 (2); translate: 0.675 (95); seam: 0.466 (94) |
| evictions / tokens evicted / degradations | 664 / 1,540,350 / 0 |
| ledger total, all ranks | 1,543,822 |

### Against e7-rawapi-p16

| | E7 (phases) | E8 (pool) |
|---|---|---|
| wall (min) | 51.4 | 21.4 |
| blocked rank-hours | 9.33 | 3.01 |
| work rank-hours | 4.2 | 2.7 |
| coordination / idle share | 68.0% | 52.6% |
| spend | $7.6078 | $6.16 |

### Ranks

| rank | pages | stolen | seams | model min | waited min | blocked min |
|---|---|---|---|---|---|---|
| 0 | 2 | 0 | 1 | 14.6 | 6.4 | 0.4 |
| 1 | 4 | 0 | 1 | 19.2 | 0.0 | 2.2 |
| 2 | 2 | 0 | 1 | 13.2 | 5.9 | 2.3 |
| 3 | 11 | 5 | 19 | 7.3 | 10.5 | 3.6 |
| 4 | 5 | 0 | 3 | 8.3 | 10.0 | 3.1 |
| 5 | 8 | 1 | 4 | 16.9 | 1.2 | 3.3 |
| 6 | 4 | 1 | 4 | 7.9 | 10.4 | 3.1 |
| 7 | 7 | 1 | 7 | 8.3 | 10.1 | 3.0 |
| 8 | 3 | 0 | 2 | 11.7 | 7.2 | 2.5 |
| 9 | 8 | 2 | 7 | 7.7 | 10.5 | 3.2 |
| 10 | 1 | 0 | 3 | 8.2 | 10.5 | 2.6 |
| 11 | 12 | 6 | 20 | 7.3 | 10.5 | 3.6 |
| 12 | 5 | 0 | 3 | 7.9 | 10.5 | 3.0 |
| 13 | 10 | 5 | 6 | 7.6 | 10.5 | 3.2 |
| 14 | 5 | 0 | 8 | 7.8 | 10.4 | 3.2 |
| 15 | 8 | 3 | 5 | 7.9 | 10.5 | 3.0 |
