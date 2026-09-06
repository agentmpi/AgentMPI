## Where the time went (e8-rawapi-p16-attempt1)

| quantity | value |
|---|---|
| wall | 77.4 min (bootstrap 21.4, pool 52.7, tail 3.2) |
| model rank-hours / waiting for work / blocked in collectives | 4.13 / 1.38 / 21.91 |
| busy share / idle share | 20.0% / 112.9% |
| pages per rank (min / mean / max) | 0 / 5.94 / 17 |
| pages stolen / items reclaimed / seams | 3 / 0 / 94 |
| model exchanges / spend | 236 / $6.39 |
| transport per page, by node (median / p90 s from translation done to pool done) | node0: 61.0 / 72.5 (7 pages); node1: 30.1 / 39.2 (88 pages) |

### Against e7-rawapi-p16

| | E7 (phases) | E8 (pool) |
|---|---|---|
| wall (min) | 51.4 | 77.4 |
| blocked rank-hours | 9.33 | 23.29 |
| work rank-hours | 4.2 | 4.13 |
| coordination / idle share | 68.0% | 112.9% |
| spend | $7.6078 | $6.39 |

### Ranks

| rank | pages | stolen | seams | model min | waited min | blocked min |
|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 3 | 14.5 | 5.5 | 10.3 |
| 1 | 1 | 0 | 4 | 10.5 | 5.9 | 80.2 |
| 2 | 1 | 0 | 1 | 16.0 | 0.3 | 83.0 |
| 3 | 1 | 0 | 4 | 2.8 | 6.8 | 87.8 |
| 4 | 1 | 0 | 4 | 5.1 | 5.5 | 86.0 |
| 5 | 1 | 0 | 5 | 11.0 | 3.3 | 81.2 |
| 6 | 1 | 0 | 4 | 5.6 | 5.2 | 86.2 |
| 7 | 1 | 0 | 4 | 3.6 | 6.4 | 86.1 |
| 8 | 6 | 0 | 6 | 27.5 | 4.1 | 89.1 |
| 9 | 11 | 1 | 9 | 27.1 | 6.1 | 84.5 |
| 10 | 5 | 0 | 0 | 34.2 | 3.3 | 89.9 |
| 11 | 17 | 0 | 11 | 11.1 | 7.0 | 91.7 |
| 12 | 11 | 0 | 9 | 21.3 | 5.0 | 91.0 |
| 13 | 13 | 1 | 10 | 21.6 | 5.8 | 85.6 |
| 14 | 11 | 0 | 10 | 20.0 | 6.1 | 91.0 |
| 15 | 14 | 1 | 10 | 16.0 | 6.7 | 90.9 |
