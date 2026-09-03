# e7-cloud-smoke: one stub job over two cloud machines

The first E7 run whose ranks lived on two separate machines. Node 0 (ranks 0-3)
was the session that wrote this branch; node 1 (ranks 4-7) was a child session
(`session_012uSouQCma9nbNqaY3EE5hs`, tagged `agentmpi-e7:e7-cloud-smoke`) that
cloned the branch and ran the same command with `--node 1`. The transport was
`gitd` against `https://github.com/agentmpi/AgentMPI`, branch
`ampi-jobs/e7-cloud-smoke`; the executor was the stub, so every second here is
protocol cost.

| quantity | value |
|---|---|
| ranks | 8 over 2 nodes, all finalised |
| wall | 19.4 min |
| events | 511 |
| collectives | 16; slowest participant median 41 s, max 224 s |
| coordination share | 54% (stub executors: nearly all the time is protocol) |
| coverage | 100% of 36 paragraphs (pages 5-8) |

Node 1's own launch record stayed on its machine; from the next run on, every
node announces itself in the job trace (`launch.node` / `launch.exit`), which
is why this directory carries only node 0's record.
