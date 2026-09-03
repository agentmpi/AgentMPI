# Running E7 across machines

A multi-node E7 run is one AgentMPI job whose ranks live on several machines that
share nothing but a git remote. Node 0 creates the job; every other node joins
it. Each node runs the same command with its own `--node`, and the launcher
block-distributes the ranks: with `--size 256 --nodes 8`, node *k* hosts ranks
`32k … 32k+31`.

The transport is the `gitd` device: one daemon per node owns a working tree on
the job's branch and group-commits its ranks' writes, so the number of pushes on
the wire is the number of nodes' bursts, not the number of ranks' operations.
The daemon is started by the first process that opens the device on that node
and exits when it has had no client for a while.

## On every node

```bash
git clone --branch rawapi/production_exp https://github.com/agentmpi/AgentMPI
cd AgentMPI
uv venv .venv -p 3.11 && uv pip install -p .venv/bin/python -e '.[dev,tokens]' matplotlib
export OPENROUTER_API_KEY=...          # the executors' credential
export AMPI_GITD_IDLE_S=900            # the daemon outlives a quiet phase

K=3   # this node's index, 0-based; node 0 must start first
.venv/bin/python -m experiments.e7_rawapi_book.harness run \
    --name e7-rawapi-p256 --size 256 --nodes 8 --node $K \
    --executor model --model moonshotai/kimi-k3 --reasoning low --respawn 1 \
    --device gitd --remote https://github.com/agentmpi/AgentMPI \
    --task-timeout 1800 --phase-timeout 7200 --lease 1800 -q
```

Every node passes the same flags. The flags are what the rank processes read;
the job manifest on the device is what the runtime enforces, and a node that
joined with a different `--size` would be refused by the ranks it tried to run.

The source text is fetched by each node into its untracked `work/` from the
legacy project's page extraction at a pinned commit, so every node cuts the same
bytes; the partition manifest in `runs/<name>/corpus_manifest.json` carries a
digest per segment and node 0's copy is the one committed.

## What node 0 does that the others do not

Node 0 creates the job (`ampirun` does this before starting its own ranks),
waits after its ranks exit until every rank in the population has finalised or
failed, exports the trace and diagnosis to `runs/<name>/`, assembles the book
from the shared window into `work/e7/<name>/out/`, and promotes the evidence.
A joining node only runs its ranks and exits; its launch record
(`work/e7/<name>/launch/launch-node<k>.json`, with the machine's boot id) is
copied into node 0's `runs/<name>/launch/` by the operator so the run carries
proof of how many machines took part.

## Time and cost at p = 256

Job creation over a hosted remote takes a minute (pipelined into a few pushes).
Each rank then makes on the order of a hundred device operations over the run;
grouped by the daemon and paced by the model's own latency, a node's push rate
stays near one every few seconds. The phase that matters is translation, where
every rank is busy for several minutes per chunk; coordination cost shows up at
the phase boundaries, which is where the trace analysis reports it.
