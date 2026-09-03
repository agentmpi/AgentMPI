# AgentMPI

**A message passing interface for multi-agent systems.**

AgentMPI is a *protocol*, not a multi-agent system. It stands to multi-agent
systems as MPI stands to parallel applications: a library that harness authors
call, not a harness. It does not decide how many agents to run, what they should
do, how to prompt them, which model to use, or how to recover from failure. It
provides the mechanisms with which those decisions are expressed.

It is also deliberately semantics-thin. A message is an opaque body plus an
envelope of size and provenance metadata — no ontology, no speech acts, no
commitment semantics. That is a considered rejection of the KQML/FIPA-ACL lineage
in favour of MPI's stance: standardise the mechanism, leave meaning to the
application.

---

## Why this exists

Multi-agent LLM systems today are written the way parallel programs were written
in 1991. Everyone implements their own coordination layer, none of them compose,
and the same failure modes are rediscovered independently by every group. Parallel
computing left that state by agreeing on an interface, and the interface it agreed
on transplants — but not mechanically.

Five properties of an LLM executor differ from an MPI process, and each forces a
deviation:

| | MPI process | AgentMPI executor |
|---|---|---|
| Determinism | deterministic | may produce a different, plausible, wrong result |
| Scarce resource | bandwidth | **context window** |
| Operator cost | nanoseconds; free | seconds to minutes; dominant |
| Latency | tight | heavy-tailed |
| Failure | fail-stop, rare, fatal | frequent, partial, sometimes silent |

Consequences developed in [`spec/AgentMPI-1.0.md`](spec/AgentMPI-1.0.md) and
measured in [`paper/`](paper/): MPI's eager limit becomes a token-denominated
flow-control problem; MPI's "safe program" discipline becomes a mechanically
checkable property; two of MPI's algorithm-selection rules invert; and MPI's
barrier hierarchy collapses because the control plane is shared rather than
point-to-point.

## What is here

The repository is four layers, and each directory belongs to exactly one of
them.

```
1. The concepts            spec/AgentMPI-1.0.md     the normative specification: what a rank,
                                                    a collective, a window and a failure are
                           spec/background/         four dossiers on MPI's history, collective
                                                    algorithms, fault tolerance and the agent
                                                    landscape, with their bibliographies

2. The reference           ampi/                    the runtime: everything the specification
   implementation            core/                  requires, written over the device waist
                             device/                the waist: 6 operations, 5 transports
                                                    (sqlite, journal, memory, git, gitd)
                             cli.py                 the command binding an agent calls
                           conformance/             one suite, run unchanged against every
                                                    transport; the artifact that makes this a
                                                    specification rather than a library
                           tests/                   unit tests of both packages

3. The auxiliary code      ampitools/               what a harness author uses around the
   and harness               harness.py             protocol and the specification does not
                             executor.py            require: the SPMD driver; function,
                             model.py, tools.py     replay and broker executors; the raw-API
                             launcher.py            executor with its tool loop; ampirun,
                             doctor.py              the process manager; the diagnosis;
                             analysis/              trace analysis, figures and the viewer

4. The experiments         experiments/             E0 microbenchmarks, E1 and E2 (session
   and analysis                                     ranks), E5 (machines), E7 (process ranks)
                             tools/                 sealing a run, collecting the suite
                           runs/                    committed evidence: launch plans, traces,
                                                    per-rank reports, analyses; no book text
                           paper/                   the paper; every number is a macro that
                             tools/                 paper/tools/ generates from runs/
```

The dependency runs downward only: `ampi` imports nothing from the layers below
it, `ampitools` imports `ampi`, and the experiments import both.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e .

# Harness-side: the protocol lives in your code.
.venv/bin/python - <<'PY'
from ampitools.harness import Harness
h = Harness(root="/tmp/job", size=4, device="sqlite", force=True)
h.create()

def rank_main(amp, rank):
    amp.barrier("start", timeout=30)
    out = amp.allreduce("glossary",
                        payload={"owner": f"m{rank % 2}", f"own{rank}": rank},
                        op="union", timeout=30)
    return out.get("conflicts")     # disagreements are lifted, never decided locally

for r in h.run(rank_main):
    print(r.rank, r.value)
PY
```

```bash
# Agent-side: anything that can run a shell command is a rank.
export AMPI_ROOT=/tmp/job2
.venv/bin/ampi new --root $AMPI_ROOT --size 4
AMPI_RANK=0 .venv/bin/ampi init
AMPI_RANK=0 .venv/bin/ampi man          # the manual is a command
AMPI_RANK=0 .venv/bin/ampi doctor       # names the rank that has not arrived
```

## The three ideas worth knowing

**The narrow waist.** Six device operations — `append`, `match`, `scan`, `cas`,
`lease`, `clock` — separate portable semantics from transport, exactly as MPICH's
abstract device interface does. Three transports ship (SQLite, a filesystem
journal, memory) and share no code below the interface. One conformance suite runs
against all of them, which is the difference between a specification and a
library. It found a defect that exists on one transport only: SQLite's type
affinity returned integers as strings for four indexed fields, so a wildcard
receive posted as `-1` read back as `"-1"` and silently stopped matching.

**Conflict lifting.** A canonical reduction tree makes an agent-evaluated
reduction *reproducible*. It does not make it *consistent*: two branches can meet
the same conflict and resolve it oppositely, and no node is in a position to
notice, because each merge saw a locally consistent pair. Lifting enlarges the
operator's codomain with a conflict set whose join is a semilattice, so the
conflicts reaching the root are identical for every tree shape and the root
arbitrates each exactly once. Verified exhaustively over every binary fold order
to p=6 and randomly to p=32.

**Context safety.** MPI's advice is to test a program by making every send
synchronous. Here the buffer a harness implicitly relies on is the receiving
executor's context window, so the penalty for getting it wrong is not a deadlock
on someone else's machine but silently worse output as you scale. `ampi.core.safety`
runs the test, names the blocked ranks and the send cycle among them, and reports
whether declaring rendezvous repairs it.

## Reproducing

```bash
make test          # conformance against every device, plus unit tests of both packages
make bench         # E0 microbenchmarks; fits alpha and beta per device
make paper         # regenerate macros from run data, then build the PDF
make check         # lint, plus verify the PDF matches its sources
```

The agent experiments need agents. `experiments/launch.py` renders one worker
prompt per rank and writes a launch plan naming every rank *requested*, before any
of them starts, so the set the experiment intended is recorded independently of
the set that answered. The worker bootstrap prompt is checked in at
[`experiments/worker_prompt.md`](experiments/worker_prompt.md) because it is part
of the method — and note what it does *not* contain: no communicator, no
collective, no barrier, no mention of the experiment. The protocol belongs in the
harness, not in the prompt.

### Ranks as Claude Code sessions

Any agent host that can run a shell command can serve a rank; the one this
repository ships a driver for is the Claude Code CLI. `experiments/claude_ranks.py`
starts one headless `claude -p` session per executor in the launch plan, gives it
nothing but the rendered worker prompt, and records which session served which
ranks in `runs/<name>/executors.json`. Run it alongside a harness that owns the
job with the broker executor:

```bash
pip install -e ".[tokens]"          # `ampi` on PATH: the runtime's printed submit commands call it by name
python experiments/e1_translate/harness.py --name demo --size 8 --executor broker &
python experiments/claude_ranks.py --name demo --size 8 --executors 4 --model sonnet
python experiments/tools/seal_run.py demo     # once the population is gone
```

The launcher waits for the job root to appear, so the two can start in either
order. `--executors` below `--size` is oversubscription; `--concurrency` caps how
many sessions are alive at once, which is the knob for an API rate limit.
Sessions get `Bash`, `Read`, `Write` and `Edit` and nothing else, and every task
they claim is stamped with their worker id, so the sealed journal names the
session behind each artifact.

A session is a Node process of about 160 MB that spends nearly all of its life
blocked on the API or on `ampi worker next`. On a 4-vCPU, 16 GB sandbox VM,
thirty-two sessions serving thirty-two ranks at once completed `E1` end to end
(`runs/claude-scale-p32`); memory, not CPU, is what bounds the count on one VM,
and the account's rate limit is what bounds it across VMs.

### One job across many machines: the git device

The three original transports need a shared filesystem. Cloud sandboxes have no
such thing: each session is its own VM behind NAT, with no inbound port and an
egress policy that admits a handful of hosts. What every sandbox *can* reach is a
git hosting service, and a git ref is a compare-and-swap cell, so
`ampi/device/gitlog.py` implements the six waist operations as "fetch, apply,
commit, push, retry on rejection" over one JSON document on one branch. It passes
the same conformance suite as the others, and nothing above the waist changed.

```bash
export AMPI_DEVICE=git AMPI_GIT_REMOTE=https://github.com/you/repo AMPI_GIT_BRANCH=ampi-jobs/x
python experiments/e5_multihost/rank.py create --name x --size 32 --remote $AMPI_GIT_REMOTE   # once, anywhere
python experiments/e5_multihost/rank.py run    --name x --rank 7 --remote $AMPI_GIT_REMOTE    # on machine 7
python experiments/e5_multihost/rank.py collect --name x --remote $AMPI_GIT_REMOTE            # afterwards
```

`E5` is the experiment that uses it: one rank per cloud VM, each launched as a
Claude Code session, allgathering the kernel boot id that only its own machine can
produce. A mutation is a network round trip and contention serialises at the
remote, so a collective over `p` ranks costs on the order of `p` round trips plus
retries; the cost model in the paper says whether that matters, and for an
executor whose one step costs thirty seconds it does not. The device does not
delete branches (the hosting proxy this was written against refuses deletes) and
does not compact, and its clock is the local wall clock, which is comparable
across NTP-disciplined VMs and nowhere else.

### Ranks as processes, and a job across many machines: `ampirun` and `gitd`

Every production run above sixteen ranks that used an agent host as its executor
spent its wall time waiting for an executor to exist: the host capped concurrent
sessions at ten. `ampitools/model.py` removes the host. A rank is an operating-system
process; its executor is a chat-completions call with a small tool loop
(`ampitools/tools.py`: an encyclopaedia search, an article, a page fetch); its
concurrency limit is the provider's. Contract violations are repaired in the same
conversation, every call's tokens and cost land in the trace as `task.*` events,
and the analysis reads them exactly as it reads the broker's claim/submit pair.

`ampirun` (`ampitools/launcher.py`) is the process manager MPI leaves unspecified: one
process per rank with `AMPI_ROOT`/`AMPI_RANK` set, block-distributed over nodes,
supervised, and restarted through the runtime's `respawn` so a successor gets a
new epoch and re-enters its collectives (traced as `replayed`).

```bash
ampirun -np 64 --root work/job -- python my_rank_program.py                 # one machine
ampirun -np 256 --nodes 8 --node 3 --device gitd --root work/job -- ...      # machine 3 of 8
```

`gitd` (`ampi/device/gitd.py`) is the git device behind a per-node daemon. The
git device's mutations are pure functions of the state, so the daemon can apply
every write its ranks issue within a window as one commit and one push — the
intra-node aggregation every production MPI does before it touches the network.
It passes the same conformance suite as the other four transports. `E7`
(`experiments/e7_rawapi_book/`) is the experiment built on all three: the
production book translation on raw-API ranks: 16, 32 and 64 processes on one
machine to completion, then 128 over four machines and 256 over eight and four,
with checkpointed results, injected executor deaths, an agent-evaluated
arbitration spread over the population, and a locked amendment ledger. Its runs
are under `runs/e7-rawapi-*`; the multi-node attempts are kept as evidence with
what each one found (`runs/e7-rawapi-p128-attempt1` and after).

## Results, including the negative ones

* **E7 across machines.** 16/32/64 raw-API process ranks on one machine
  finished the book (98.5%, 95.5%, 96.0% of 1232 paragraphs; 51, 40, 38 min;
  $7.61, $9.40, $11.06); adding ranks bought little wall time because the run
  ends when its slowest model ends. Across machines the population found two
  transport limits: the daemon's readers starved behind its writer's lock (57
  live ranks convicted at p=128; fixed, spec S15.2), and eight daemons on one
  git ref land one push in ten (p=256 over eight machines). Four machines stay
  inside the ceiling; a recycled node rejoined a 128-rank job and respawned its
  ranks while the others waited. Both four-machine runs were then killed by an
  account-wide usage freeze. `runs/e7-rawapi-*`.
* **Protocol cost.** SQLite transport: α = 0.730 ms, β = 0.480 µs/token,
  half-bandwidth point 1521 tokens. β agrees within 2% across all three
  transports, because the per-token cost is serialisation above the waist rather
  than transport below it. Against a thirty-second operator the entire protocol is
  0.007% of an allreduce at p=128.
* **Selection inverts.** At γ=0 the journal-folding flat schedule wins; at γ=30 s
  it is ten times behind a tree. Recursive-doubling allreduce ties
  reduce-then-broadcast on latency at p=64 and performs 384 operator applications
  against 63.
* **Conflict lifting works on real data, and scales as predicted.** At p=8 the
  glossary reduction agreed 17 terms and lifted 3 conflicts; at p=32 it agreed 8
  and lifted 12. Quadrupling the population quadrupled the disagreement found. The
  root arbitrated each once.
* **And the quality comparison is null at both sizes.** Four arms, 32 distinct
  agents: weighted terminology consistency is 1.0 everywhere, and under the strict
  metric the *control* at p=32 scores marginally higher than the treatment. The
  glossary cost 3.7× the wall clock at p=8 and 1.4× at p=32.
* **The reason is more interesting than a win would have been.** Every one of the
  12 conflicts was the same kind — whether the Spanish definite article belongs in
  the rendering — and in running prose the article is determined by the grammar of
  the sentence, not by a lexical choice. The collective correctly identified a real
  disagreement in what the population *said* it would do, and that disagreement
  never reached what the population *produced*. Before paying a collective to
  remove a coupling, measure whether the coupling is costing anything.
* **A scorer bias we caught.** The first version built its vocabulary of candidate
  renderings from each run's own term sheets, and since only the treatment arm
  produces term sheets, the control was scored against an almost-empty vocabulary
  and flattered by it. The vocabulary is now pooled across arms.

## What this does not show

It does not show that AgentMPI produces better answers than an ad-hoc harness. The
controlled quality comparison is null at both sizes we ran. Every agent experiment
is n=1 per cell, one model family, one language pair, and a corpus whose recurring
terms are famous enough that a strong model has seen them; a task with genuinely
novel terminology is where we would expect a glossary to pay, and we have not run
it.

The largest *completed* agent run is 32 ranks over 8 executors. We attempted 100
and it did not finish: the host's ten-session cap forced several waves of
executors, and an operator error later destroyed that run's journal. The pull
queue handled the first problem — a fresh wave could be added mid-run against
exactly the ranks still queued, with no harness change and no restart — and the
second was ours. We report the partial run's identity measurement, which was
committed before the journal was lost, and we do not report a hundred-rank result,
because we do not have one.

## License

Apache 2.0.
