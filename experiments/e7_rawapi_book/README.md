# E7: the production book translation on raw-API ranks, one node to many

E7 is the experiment E3 designed and could not staff. Same task — render
N. V. Kononov's *Код Дурова* (2013) into English, Chinese and Japanese as a
comparative cultural job, not a lexical one — same nine collectives, for the
same reasons. What changes is what a rank *is*.

| | E3 | E7 |
|---|---|---|
| executor | an agent-host session, claimed through the broker | a process calling a chat-completions endpoint, with a tool loop |
| executor supply | capped by the host (10 concurrent sessions) | the provider's rate limit |
| launch | rendered prompts, sessions started by hand | `ampirun`: one OS process per rank, block-distributed over nodes |
| work unit | a page (p ≤ 95) | a paragraph (p ≤ 1232) |
| arbitration | the runtime's default rule at the root | agent-evaluated, one batch per rank, gathered and committed once |
| restart | none | `ampirun --respawn`; results checkpointed; collectives replayed |
| shared state under a lock | none | per-chapter amendment ledger, leased and fenced |

## Running it

```bash
# a surrogate population, to debug the protocol before paying for one
python -m experiments.e7_rawapi_book.harness run --name e7-stub-p16 --size 16 --executor stub

# the real thing, on this machine, sixteen processes
export OPENROUTER_API_KEY=...
python -m experiments.e7_rawapi_book.harness run --name e7-rawapi-p16 --size 16 --executor model \
    --model moonshotai/kimi-k3 --reasoning low --respawn 1

# with a tenth of the executors dying in the translate phase
python -m experiments.e7_rawapi_book.harness run --name e7-rawapi-p32-die --size 32 --executor model \
    --die-fraction 0.1 --die-phase translate --respawn 1

# 256 ranks over four machines (64 processes each): run this on each, with
# --node 0..3.  Node 0 creates the job and must start first; the others join it
# through the gitd daemon.  Four machines is inside the transport's ceiling and
# eight is not (see NODES.md and runs/e7-rawapi-p256-attempt1).  node.sh is the
# one-command form every production node used: the standard flags and model
# pool, the driver detached, its log under work/e7/:
bash experiments/e7_rawapi_book/node.sh e7-rawapi-p256 256 4 $K
# ... and with a fifth argument the node re-enters a job that already exists on
# the branch (the machine was recycled, or every session was frozen at once):
bash experiments/e7_rawapi_book/node.sh e7-rawapi-p256 256 4 $K rejoin
python -m experiments.e7_rawapi_book.harness run --name e7-rawapi-p256 --size 256 --executor model \
    --device gitd --remote https://github.com/agentmpi/AgentMPI --nodes 4 --node $K
# a node whose machine was recycled re-enters the same job (its convicted ranks
# are respawned with fresh epochs):
python -m experiments.e7_rawapi_book.harness run --name e7-rawapi-p256 --size 256 --executor model \
    --device gitd --remote https://github.com/agentmpi/AgentMPI --nodes 4 --node 0 --rejoin
```

The driver writes `runs/<name>/config.json` and the partition, then runs
`ampirun -np <size> -- python -m experiments.e7_rawapi_book.harness rank --name <name>`.
Every rank process reads the same config and the same partition, attaches to the
job named in its environment (`AMPI_ROOT`, `AMPI_RANK`), and runs `rank_main`.
`--launch threads` runs the same `rank_main` in one process, which is what the
tests use.

## What a rank does

```
0  bcast              the commission
1  scatter            its segment, self-identifying (a misrouted slice is loud)
2  agent              survey: what must be rendered consistently
3  allreduce(union)   term census; disagreements lifted, never decided locally
   agent × p          arbitrate one batch of the lifted conflicts
   gather             rulings to the root; op_arbitrate once; bcast the agenda
4  win/claim + agent  research, each term claimed by exactly one rank (CAS)
5  win_fence          close the research epoch
   allreduce(union)   the binding glossary; arbitrated the same way; bcast
6  exscan             paragraph offsets; barrier
7  agent × chunks     translate under the binding glossary, ~1600 source tokens a call
   win_lock           record the terms it settled itself, per chapter, first writer wins
8  cart + neighbor_allgather + agent   seams, on a ring
9  allreduce(sum)     the population's spend; gather the manifest; the root assembles
```

Every agent result is written to a window before the rank moves on, keyed by its
label. A rank restarted by `ampirun` re-enters the same program: each collective
it already joined returns its stored result (traced as `replayed`), each agent
task it already finished is read back (`task.replay`), and it resumes at the
first thing its predecessor did not finish.

## What is in `runs/<name>/`

Evidence, never text. `launch_plan.json` names every rank requested before any
ran; `config.json` is what they all read; `corpus_manifest.json` says which
paragraphs each rank was given and their digest; `harness.trace.jsonl` is the
whole trace; `harness.json` the diagnosis; `report.json` the driver's summary
(coverage, spend, recovered ranks); `glossary.json`, `findings.json` and
`amendments.json` are the population's own scholarly output; `sample_page13.json`
holds a few paragraphs of the page the legacy project itself published as its
example, for comparison; `analysis/` is `ampi analyze`'s output.

The book — source, partition, per-call prompts and replies, per-rank drafts and
the assembled translation in four files — lives under `work/e7/<name>/`, which
is untracked. See `DATA_POLICY.md`.

## Reviewing what a rank said to its model

The trace carries the shape of every conversation without its text: a
`task.start`/`task.done` pair per task, a `task.call` per request to the
provider (message count, prompt size and digest, tokens, cost, finish reason,
the tools it asked for) and a `task.tool` per tool the model used (its
arguments and whether it answered or failed). Fold them back into conversations
with

```bash
python -m ampitools.calls runs/e7-rawapi-p128/harness.trace.jsonl --summary
python -m ampitools.calls runs/e7-rawapi-p128/harness.trace.jsonl --rank 24
python -m ampitools.calls runs/e7-rawapi-p128/harness.trace.jsonl --label research --json
```

On the machine that ran the rank, `work/e7/<name>/calls/<aid>.{prompt.md,
messages.jsonl,result.json}` hold the verbatim exchange; pass `--calls
work/e7/<name>/calls` and the tool locates them by task id and checks their
digests against the trace. The research tools throttle themselves to one request
per host every `AMPI_TOOL_MIN_GAP_S` seconds (default 0.7) across all ranks on a
node, honour `Retry-After`, and percent-encode the URLs a model writes in
Cyrillic; the first 128-rank run showed both failure modes in the trace
(`HTTP Error 429` and `UnicodeEncodeError`) before either was fixed.
