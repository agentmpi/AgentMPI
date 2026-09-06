## Where the time went (e8-rawapi-p16)

| quantity | value |
|---|---|
| wall | 58.3 min (bootstrap 15.6, pool 39.6, tail 3.2) |
| model rank-hours / waiting for work / blocked in collectives | 4.77 / 1.19 / 3.05 |
| busy share / idle share | 30.7% / 27.3% |
| pages per rank (min / mean / max) | 3 / 5.94 / 9 |
| pages stolen / items reclaimed / seams | 15 / 0 / 94 |
| model exchanges / spend | 238 / $6.72 |
| waiting for work, while work existed / for the last item | 0.74 / 0.46 rank-hours (s088-089, 0.6 min) |
| slowest single model call | 379.6 s (glm-5.3, translate:p042) |
| transport per page, by node (median / p90 s from translation done to pool done) | node0: 54.5 / 66.2 (51 pages); node1: 60.0 / 69.9 (44 pages); 1.5 rank-hours in total |

### Against e7-rawapi-p16

| | E7 (phases) | E8 (pool) |
|---|---|---|
| wall (min) | 51.4 | 58.3 |
| blocked rank-hours | 9.33 | 4.24 |
| work rank-hours | 4.2 | 4.77 |
| coordination / idle share | 68.0% | 27.3% |
| spend | $7.6078 | $6.72 |

### Ranks

| rank | pages | stolen | seams | model min | waited min | blocked min |
|---|---|---|---|---|---|---|
| 0 | 4 | 0 | 5 | 28.0 | 5.0 | 6.6 |
| 1 | 7 | 1 | 8 | 13.6 | 5.1 | 13.1 |
| 2 | 3 | 0 | 4 | 26.2 | 4.2 | 12.9 |
| 3 | 9 | 3 | 8 | 8.4 | 6.6 | 14.1 |
| 4 | 7 | 1 | 7 | 14.7 | 5.5 | 13.9 |
| 5 | 6 | 0 | 5 | 19.8 | 3.6 | 13.8 |
| 6 | 7 | 1 | 7 | 15.7 | 4.5 | 13.9 |
| 7 | 8 | 2 | 6 | 14.6 | 4.3 | 14.2 |
| 8 | 5 | 1 | 3 | 26.4 | 1.5 | 8.9 |
| 9 | 5 | 0 | 7 | 15.1 | 5.5 | 10.4 |
| 10 | 4 | 0 | 5 | 22.8 | 3.2 | 9.4 |
| 11 | 8 | 2 | 8 | 8.2 | 4.7 | 11.7 |
| 12 | 6 | 1 | 6 | 13.4 | 6.0 | 10.9 |
| 13 | 3 | 0 | 2 | 31.8 | 3.1 | 7.3 |
| 14 | 6 | 1 | 7 | 14.4 | 4.2 | 11.3 |
| 15 | 7 | 2 | 6 | 13.3 | 4.6 | 10.8 |
