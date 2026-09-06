## Where the time went (e8-rawapi-p16-run2)

| quantity | value |
|---|---|
| wall | 74.1 min (bootstrap 14.7, pool 57.2, tail 2.2) |
| model rank-hours / waiting for work / blocked in collectives | 4.59 / 7.52 / 3.22 |
| busy share / idle share | 23.2% / 54.4% |
| pages per rank (min / mean / max) | 3 / 5.94 / 12 |
| pages stolen / items reclaimed / seams | 16 / 1 / 94 |
| model exchanges / spend | 232 / $5.96 |
| waiting for work, while work existed / for the last item | 0.41 / 7.1 rank-hours (s089-090, 24.4 min) |
| slowest single model call | 1951.8 s (glm-5.3, seam:s089-090) |
| transport per page, by node (median / p90 s from translation done to pool done) | node0: 31.0 / 38.7 (54 pages); node1: 34.2 / 40.3 (41 pages); 0.81 rank-hours in total |

### Against e7-rawapi-p16

| | E7 (phases) | E8 (pool) |
|---|---|---|
| wall (min) | 51.4 | 74.1 |
| blocked rank-hours | 9.33 | 10.74 |
| work rank-hours | 4.2 | 4.59 |
| coordination / idle share | 68.0% | 54.4% |
| spend | $7.6078 | $5.96 |

### Ranks

| rank | pages | stolen | seams | model min | waited min | blocked min |
|---|---|---|---|---|---|---|
| 0 | 4 | 0 | 4 | 22.1 | 29.1 | 13.4 |
| 1 | 8 | 2 | 8 | 12.2 | 29.9 | 15.3 |
| 2 | 3 | 0 | 5 | 27.2 | 25.2 | 12.4 |
| 3 | 12 | 6 | 8 | 8.6 | 30.9 | 16.0 |
| 4 | 8 | 2 | 6 | 13.3 | 30.7 | 15.6 |
| 5 | 4 | 0 | 4 | 20.8 | 29.7 | 14.6 |
| 6 | 8 | 2 | 7 | 13.1 | 31.0 | 15.8 |
| 7 | 7 | 1 | 8 | 14.7 | 30.1 | 14.6 |
| 8 | 3 | 0 | 4 | 19.0 | 30.9 | 8.6 |
| 9 | 6 | 0 | 6 | 10.7 | 30.8 | 11.2 |
| 10 | 3 | 0 | 4 | 19.0 | 28.5 | 10.1 |
| 11 | 7 | 1 | 8 | 7.8 | 31.0 | 11.5 |
| 12 | 5 | 0 | 7 | 12.4 | 30.0 | 11.0 |
| 13 | 5 | 0 | 4 | 51.5 | 1.3 | 2.0 |
| 14 | 6 | 1 | 6 | 11.3 | 31.6 | 11.1 |
| 15 | 6 | 1 | 5 | 11.9 | 30.3 | 10.3 |
