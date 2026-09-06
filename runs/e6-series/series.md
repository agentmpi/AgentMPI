# E6 series

| p | machines | wall (h) | tasks | work rank-s | blocked rank-s | coord. share | parallelism | efficiency | census conflicts | commits | convicted | stolen | pages |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 4 | 4 | 0.58 | 34 | 4909.6 | 2602.4 | 0.3101 | 2.34 | 0.585 | 7 | 305 | 0 | 0 | 8/8 |
| 16 | 8 | 31.98 | 241 | 42934.2 | 30855813.7 | 16.7498 | 0.37 | 0.0233 | 37 | 6596 | 13 | 5 | 64/64 |
| 32 | 8 | 3.77 | 168 | 43832.5 | 147155.7 | 0.3387 | 3.23 | 0.1009 | 59 | 3820 | 21 | 19 | 60/64 |
| 64 | 8 | 14.75 | 128 | 21005.9 | 6561731.1 | 1.9306 | 0.4 | 0.0062 | 135 | 5829 | 44 | 13 | 14/64 |

## e6-book-p4

| phase | start (s) | end (s) | spread of arrival (s) |
|---|---|---|---|
| launch | 1.8 | 108.9 | 46.9 |
| survey | 74.6 | 287.4 | 34.3 |
| census | 231.8 | 414.9 | 55.6 |
| research | 401.8 | 700.2 | 13.2 |
| glossary | 681.1 | 770.6 | 19.1 |
| translate | 753.9 | 1347.7 | 16.7 |
| review | 1227.8 | 1967.7 | 119.9 |
| seams | 1656.5 | 2035.7 | 311.2 |
| assemble | 1878.2 | 2074.5 | 157.6 |
| done | 2047.5 | 2098.2 | 27.1 |

| task | n | median (s) | max (s) | total rank-s |
|---|---|---|---|---|
| arbitrate | 1 | 56.9 | 56.9 | 56.9 |
| research | 6 | 127.6 | 147.7 | 746.4 |
| review | 8 | 120.0 | 158.8 | 962.7 |
| revise | 3 | 148.1 | 157.9 | 441.1 |
| seam | 4 | 38.3 | 54.8 | 165.3 |
| survey | 4 | 140.2 | 150.0 | 549.7 |
| translate | 8 | 252.1 | 302.6 | 1987.4 |

Most expensive collectives (rank-seconds blocked, worst single wait):

- `neighbor_allgather:seams`: 627.8 rank-s, worst 299.4 s
- `barrier:winfence:pages:drafts`: 293.7 rank-s, worst 125.3 s
- `bcast:agenda`: 293.0 rank-s, worst 101.3 s
- `barrier:winfence:research:research-done`: 273.6 rank-s, worst 132.8 s
- `barrier:winfence:pages:final`: 213.6 rank-s, worst 161.8 s
- `allreduce:census`: 171.4 rank-s, worst 77.4 s

Transport: 305 commits (76.2 per rank), {'cas': 217, 'append': 64, 'flush': 15, 'lease': 4, 'release': 4, 'job': 1}; lock waits total 11.7 s, max 4.2 s; research claims 6/6 won.

Faults: convicted [], executors lost 0, pages stolen []; book {'n_pages': 8, 'expected': 8, 'missing': [], 'failed': [], 'sentences': 330, 'revised': [1, 2, 4, 6, 7]}.

## e6-book-p16

| phase | start (s) | end (s) | spread of arrival (s) |
|---|---|---|---|
| launch | 48.7 | 110756.2 | 110614.3 |
| survey | 401.8 | 110760.7 | 110354.3 |
| census | 613.7 | 111113.6 | 110147.0 |
| research | 1695.5 | 112272.5 | 109418.2 |
| glossary | 3115.9 | 112319.3 | 109156.6 |
| translate | 3692.9 | 112351.9 | 108626.4 |
| review | 15783.3 | 113849.0 | 96568.5 |
| seams | 113158.2 | 114116.3 | 690.9 |
| assemble | 113599.5 | 114534.4 | 516.7 |
| done | 114142.2 | 115134.7 | 392.2 |

| task | n | median (s) | max (s) | total rank-s |
|---|---|---|---|---|
| arbitrate | 1 | 171.1 | 171.1 | 171.1 |
| research | 90 | 156.8 | 337.0 | 15019.3 |
| review | 42 | 131.8 | 228.7 | 5811.5 |
| revise | 14 | 69.1 | 162.5 | 1284.5 |
| seam | 14 | 34.3 | 66.5 | 532.5 |
| survey | 16 | 166.0 | 216.0 | 2626.6 |
| translate | 64 | 274.9 | 540.3 | 17488.8 |

Most expensive collectives (rank-seconds blocked, worst single wait):

- `barrier:launch`: 3517204.8 rank-s, worst 110421.3 s
- `bcast:commission`: 3514425.8 rank-s, worst 110470.7 s
- `scatter:segments`: 3296866.5 rank-s, worst 110411.1 s
- `allreduce:census`: 3055044.3 rank-s, worst 110075.3 s
- `bcast:agenda`: 3042073.0 rank-s, worst 109517.2 s
- `gather:term-meta`: 3034627.2 rank-s, worst 109606.6 s

Transport: 6596 commits (412.2 per rank), {'cas': 5460, 'append': 588, 'flush': 388, 'lease': 99, 'release': 57, 'wipe': 3}; lock waits total 8028.0 s, max 854.7 s; research claims 169/181 won.

Faults: convicted [0, 1, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15], executors lost 0, pages stolen [3, 4, 40, 51, 52]; book {'n_pages': 64, 'expected': 64, 'missing': [], 'failed': [], 'sentences': 3201, 'revised': [7, 9, 11, 13, 14, 15, 33, 34, 37, 42, 45, 46, 52, 55, 59]}.

## e6-book-p32

| phase | start (s) | end (s) | spread of arrival (s) |
|---|---|---|---|
| launch | 85.5 | 1187.9 | 297.3 |
| survey | 467.6 | 2553.8 | 720.3 |
| census | 772.9 | 4393.6 | 1780.9 |
| research | 3720.2 | 5143.8 | 673.4 |
| glossary | 4838.0 | 6041.4 | 305.7 |
| translate | 5708.0 | 10485.2 | 333.3 |
| review | 7006.7 | 13078.6 | 3478.6 |
| seams | 12657.4 | 13209.9 | 421.1 |
| assemble | 12694.1 | 13416.6 | 515.8 |
| done | 13232.1 | 13579.1 | 184.5 |

| task | n | median (s) | max (s) | total rank-s |
|---|---|---|---|---|
| arbitrate | 1 | 561.1 | 561.1 | 561.1 |
| research | 48 | 91.5 | 160.8 | 4413.8 |
| review | 20 | 266.0 | 496.4 | 5522.1 |
| revise | 3 | 202.1 | 223.7 | 592.7 |
| seam | 4 | 39.1 | 64.7 | 179.2 |
| survey | 32 | 176.0 | 735.0 | 6677.8 |
| translate | 60 | 435.3 | 615.7 | 25885.7 |

Most expensive collectives (rank-seconds blocked, worst single wait):

- `allreduce:census`: 39954.5 rank-s, worst 2028.7 s
- `bcast:agenda`: 32636.1 rank-s, worst 1400.7 s
- `barrier:winfence:pages:drafts`: 11926.0 rank-s, worst 3617.3 s
- `allreduce:glossary`: 11837.4 rank-s, worst 760.0 s
- `exscan:offsets`: 10976.0 rank-s, worst 576.3 s
- `barrier:winfence:pages:reviews`: 8876.4 rank-s, worst 1651.3 s

Transport: 3820 commits (119.4 per rank), {'cas': 3167, 'append': 407, 'flush': 156, 'lease': 57, 'release': 32, 'job': 1}; lock waits total 8220.5 s, max 783.2 s; research claims 67/135 won.

Faults: convicted [0, 1, 2, 3, 4, 5, 6, 8, 11, 13, 15, 16, 18, 19, 20, 23, 24, 26, 28, 29, 30], executors lost 21, pages stolen [2, 4, 6, 8, 10, 12, 14, 18, 24, 27, 34, 37, 38, 40, 42, 47, 48, 50, 60]; book {'n_pages': 60, 'expected': 64, 'missing': [2, 14, 24, 48], 'failed': [], 'sentences': 3021, 'revised': [22, 27, 46]}.

## e6-book-p64

| phase | start (s) | end (s) | spread of arrival (s) |
|---|---|---|---|
| launch | 84.7 | 36546.5 | 36153.6 |
| survey | 1069.3 | 37328.3 | 35477.2 |
| census | 1433.4 | 42431.8 | 35894.9 |
| research | 37659.9 | 44800.0 | 4771.9 |
| glossary | 41305.1 | 47842.4 | 3494.9 |
| translate | 45661.3 | 53106.4 | 2181.0 |

| task | n | median (s) | max (s) | total rank-s |
|---|---|---|---|---|
| arbitrate | 1 | 552.2 | 552.2 | 552.2 |
| research | 48 | 140.0 | 296.2 | 7328.8 |
| survey | 64 | 129.9 | 242.7 | 8548.9 |
| translate | 15 | 314.5 | 382.1 | 4576.1 |

Most expensive collectives (rank-seconds blocked, worst single wait):

- `barrier:launch`: 2072213.4 rank-s, worst 35484.3 s
- `bcast:commission`: 1795833.9 rank-s, worst 35397.4 s
- `scatter:segments`: 1741811.4 rank-s, worst 32349.5 s
- `allreduce:census`: 431519.1 rank-s, worst 33519.1 s
- `bcast:agenda`: 166046.3 rank-s, worst 6364.3 s
- `barrier:winfence:research:research-done`: 127465.0 rank-s, worst 4531.6 s

Transport: 5829 commits (91.1 per rank), {'cas': 4308, 'append': 939, 'flush': 382, 'lease': 136, 'release': 63, 'job': 1}; lock waits total 54142.7 s, max 2536.1 s; research claims 61/108 won.

Faults: convicted [2, 4, 6, 7, 9, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 41, 42, 43, 45, 50, 51, 52, 53, 55, 56, 58, 60, 61, 62, 63], executors lost 0, pages stolen [3, 5, 7, 8, 10, 13, 14, 18, 20, 21, 22, 23, 24]; book {'n_pages': 14, 'expected': 64, 'missing': [1, 2, 3, 4, 5, 6, 7, 13, 14, 15, 16, 17, 18, 20, 21, 23, 24, 25, 27, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 50, 51, 52, 53, 54, 56, 57, 58, 59, 61, 62, 63, 64], 'failed': [], 'sentences': 644, 'revised': []}.
