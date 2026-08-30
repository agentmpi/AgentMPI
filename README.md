# AgentMPI

**A message passing interface for multi-agent systems.**

AgentMPI is a *protocol*, not a multi-agent system. It plays the role MPI plays
in parallel computing: it standardises how independent executors name each other,
exchange information, synchronise, share state and survive each other's failure,
and it leaves what they actually do entirely to you. You use it to *write* your
multi-agent system, the way you use MPI to write a parallel program.

This repository contains:

| | |
|---|---|
| `spec/AgentMPI-0.1.md` | the normative protocol specification |
| `ampi/` | the reference runtime (Python, no required dependencies) and the `ampi` CLI |
| `bindings/AGENT_GUIDE.md` | the protocol manual that agents read |
| `experiments/` | multi-agent experiment harnesses and their metrics |
| `paper/` | the paper source, generated bibliography, and figures |
| `viewer/` | a timeline trace viewer, in the lineage of Jumpshot and Vampir |
| `scripts/` | benchmarks, figure generation, reproducibility helpers |
| `tests/` | 50 end-to-end conformance tests that drive the CLI as an agent would |

---

## The idea in one page

A multi-agent harness has to solve the problems a parallel program solves:
partition work, give executors names, move information between them, agree on
shared decisions, synchronise phases, and survive an executor dying. Today every
harness solves them again, inside its application, incompatibly. That was the
state of parallel computing in 1992, and the answer then was not a better
framework but a standardised interface.

AgentMPI keeps MPI's structure — communicators with private contexts, matched
messaging with a non-overtaking guarantee, the full collective catalogue,
one-sided windows, and ULFM's revoke/shrink/agree — and rederives its cost model
around three properties of LLM executors:

**The scarce resource is the context window, not bandwidth.** MPI's *eager limit*
exists because a receiver's unexpected-message buffer is finite. An agent's
equivalent buffer is its context window. So AgentMPI denominates transfer cost in
**tokens** and applies the same eager/rendezvous split: small payloads are
inlined, large ones arrive as a handle the receiver may decline to read.
Measured at 128 ranks with 4000-token payloads, an allgather that inlines bodies
charges one rank 501,888 tokens; handle-based delivery charges it 3,200.

**Applying an operator costs minutes.** `MPI_Op_create` lets you supply a
reduction function. If that function is a language model, one application costs
more than all the communication in the collective. AgentMPI supports
**agent-evaluated operators** through a continuation protocol, and that makes the
*serialised operator applications* the dominant cost — which changes which
algorithm is right. A binomial tree has depth `⌈log₂P⌉` where a linear chain has
`P−1`, so trees matter *more* than in MPI; but recursive-doubling allreduce, MPI's
standard short-message choice, performs `P·log P` applications against
reduce-then-broadcast's `P−1` and moves 36× more payload at P=128. MPI's
catalogue transfers; its selection rules do not.

**Failure is individual, frequent, and sometimes silent.** Two-phase lease
detection (suspect, then convict), fencing epochs so a zombie cannot corrupt
state, a join deadline so a rank that never starts is still detectable, and a
recovery briefing built by replaying committed commitments rather than restoring
a memory image.

---

## Quick start

```bash
pip install -e .                     # installs the `ampi` CLI
export PATH="$HOME/.local/bin:$PATH"

# create a 6-rank job; this writes a journal and one prompt file per rank
ampi run --np 6 --label demo --job-root runs/demo

# each rank is a separate agent (or, here, a shell) with an ambient identity
export AMPI_ROOT=$PWD/runs/demo AMPI_RANK=0
ampi init
ampi info
ampi man                             # the full protocol manual
```

A rank's own view of the world:

```bash
ampi send --to 3 --tag review --in @notes.md
ampi recv --from any --tag review --timeout 120
ampi barrier   --label phase1 --quorum 0.9
ampi bcast     --root 0 --label plan --in @plan.md
ampi allreduce --op union --label glossary --in @terms.json
ampi allreduce --op agent:merge --label contract --in @proposal.json   # you are the operator
ampi win create --name shared
ampi win acc  --win shared --key findings --op union --in '["found X"]'
ampi win cas  --win shared --key task/17 --expect unclaimed --value rank:4
ampi view o:9f2a --op outline --budget 400   # read a projection, not the payload
ampi failed ; ampi comm revoke ; ampi comm shrink
ampi hb --extend 900                 # "I am alive and about to think for a while"
```

Everything an agent needs is in [`bindings/AGENT_GUIDE.md`](bindings/AGENT_GUIDE.md),
which `ampi man` prints.

## The trace viewer

Every job records a durable event trace, so post-mortem analysis is always
available:

```bash
ampi serve --runs runs --port 47913          # read-only API
cd viewer && npm install && npm run dev      # http://127.0.0.1:47811
```

Rows are ranks; purple bands are time inside a collective; lines are messages
drawn from send to delivery with opacity scaled by payload size. Drag to zoom,
click a message to read its payload, click a band to inspect the collective. The
other tabs show context occupancy per rank, the collective schedule with
straggler gaps, the shared-state window with writer attribution, and the
fault-tolerance timeline.

```bash
ampi trace --timeline      # a terminal Gantt chart, if you prefer
ampi trace --summary       # aggregate metrics as JSON
```

---

## Reproducing the results

```bash
# unit + conformance tests (drives the CLI exactly as an agent does)
python3 -m pytest tests/ -q

# microbenchmarks: latency, collective volume, context cost, scaling, matching
python3 -m ampi.cli bench all --np 128 --reps 15 --merge-cost 0.25 \
    --out results/microbench.json

# a fixture run exercising the whole protocol with stub executors
python3 scripts/demo.py --np 12 --out runs/demo

# paper artefacts, all generated from the results JSON
python3 scripts/build_bib.py        # paper/refs.bib
python3 scripts/make_macros.py      # paper/results.tex -- every number in the paper
python3 scripts/figures.py          # paper/figures/*.pdf
python3 scripts/check_tex.py        # validates citations, refs, macros, figures
cd paper && latexmk -pdf main.tex
```

`scripts/check_tex.py` exists because no number in the paper is typed by hand:
each is a macro generated from a results file, so a claim without a measurement
behind it fails validation instead of quietly going stale.

### The multi-agent experiments

These need an agent host that can run several LLM agents concurrently. Each
harness writes a `launch_plan.json` naming one prompt file per rank; the launcher
starts one agent per rank with `AMPI_RANK` and `AMPI_ROOT` in its environment.

```bash
# E1: parallel book translation (Flatland -> Chinese)
curl -sL https://www.gutenberg.org/cache/epub/97/pg97.txt -o /tmp/gb97.txt
python3 experiments/e1_translation/prepare.py --arm ampi  --np 8 --out runs/e1_ampi
python3 experiments/e1_translation/prepare.py --arm naive --np 8 --out runs/e1_naive
# ... launch one agent per rank from each launch_plan.json ...
python3 experiments/e1_translation/metrics.py runs/e1_ampi runs/e1_naive \
    --out results/e1_metrics.json

# E2: collaborative implementation of a Scheme interpreter
python3 experiments/e2_codev/prepare.py --arm ampi  --np 8 --out runs/e2_ampi
python3 experiments/e2_codev/prepare.py --arm naive --np 8 --out runs/e2_naive
# ... launch one agent per rank ...
python3 experiments/e2_codev/grade.py runs/e2_ampi runs/e2_naive \
    --out results/e2_grade.json     # 174-case held-out suite
```

Both experiments run an ablated `naive` arm: the same agents, the same task, the
same shared filesystem, still instrumented with AgentMPI — but with no negotiated
agreement, no boundary exchange, and a gather that materialises full bodies.
Comparing against a baseline that also lacked instrumentation would produce
numbers nobody could interpret.

---

## What running it with real agents taught us

These are in the paper, but they are the most transferable part, so they are here
too. Each was invisible to the test suite and appeared the first time real LLM
agents executed the protocol.

1. **Executors give up far earlier than instructed.** Told to retry a timed-out
   call up to twenty times, a rank stopped after two and stalled its whole
   reduction tree. A protocol that depends on an executor's persistence is not a
   protocol — blocking calls now retry internally by default.
2. **Blocking is not evidence of death.** An early runtime had ranks make no
   calls while waiting inside a collective, so the lease detector declared each
   rank that arrived *first* to be dead and the job cascaded. Fixing the progress
   engine took a 12-rank demo from 94 s (every barrier timing out) to 8.9 s.
3. **Executors do not volunteer liveness.** Given a lease-extension primitive and
   an instruction to call it, an agent did not, and was declared dead mid-task.
   Hence two-phase detection: suspicion is free, conviction needs corroboration.
4. **Program order is not a reliable collective identifier.** Agents retry, skip
   and reorder. Collectives are identified by an explicit label; this was the
   single largest robustness win.
5. **A rank that never starts must still be detectable.** A launcher that could
   start only 6 of 22 requested ranks produced 16 no-shows that were neither
   alive nor failed. Leases are now granted when a rank is *requested*.
6. **Quorum must release without closing**, or a quorum barrier guarantees that
   precisely the slowest ranks fail.
7. **Errors must prescribe.** Agents act on a hint naming the next command far
   more reliably than they infer a remedy from a description of the fault.
8. **A durable protocol store must migrate on open.** Adding a column
   mid-experiment broke a running 22-rank job.

That 6-of-22 launch failure, incidentally, ran to completion: all six survivors
finalised and every collective closed — scatter, an agent-evaluated allreduce,
gather, a numeric allreduce and the final barrier — with 16 ranks declared failed.

---

## Design notes

**Why a CLI binding.** For an LLM executor the binding has to be a command-line
tool: it cannot hold a handle across turns, cannot link a shared object, and its
function calls are invocations whose output lands in its context window.

**Why a durable journal.** All protocol state lives in a per-job SQLite journal
(WAL) plus a content-addressed object store. This is not durability bolted on for
recovery — it is what makes a *replacement* executor possible at all, and it makes
tracing free. An embedded store rather than a daemon, because matching must
atomically move a message between queues, and a daemon would be a single point of
failure the design would then have to defend.

**Why not a framework.** A framework owns control flow; an interface does not.
AutoGen, LangGraph, CrewAI and the OpenAI Agents SDK supply a coordination
pattern and you write your application inside it. MCP standardises the
agent-to-tool boundary and A2A the agent-to-agent task boundary; neither provides
group membership, collectives, a consistency model for shared state, or a failure
model. AgentMPI sits above them and could be carried over either.

## Status

`AgentMPI/0.1`. The specification marks its deliberate omissions
(`spec/AgentMPI-0.1.md`, Appendix B): automatic context compaction, semantic
verification, cost-aware scheduling, inter-communicators, persistent collectives,
partitioned communication, sessions, capability discovery. Following MPI's
practice, a standard that answers a research question prematurely is worse than
one that leaves a hook.

## License

MIT. The corpus used in Experiment 1 is Edwin A. Abbott's *Flatland* (1884),
public domain, via Project Gutenberg.
