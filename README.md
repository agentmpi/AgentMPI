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
ampi whoami --expect-rank 4          # am I who I think I am? refuses if not
ampi view o:9f2a --op full --out f   # save a payload to disk, zero context cost
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
python3 experiments/e2_codev/differential.py runs/e2_ampi runs/e2_naive \
    --n 2500 --seed 11 --out results/e2_differential.json
```

Both experiments run an ablated `naive` arm: the same agents, the same task, the
same shared filesystem, still instrumented with AgentMPI — but with no negotiated
agreement, no boundary exchange, and a gather that materialises full bodies.
Comparing against a baseline that also lacked instrumentation would produce
numbers nobody could interpret.

---

## What the experiments found

**E1, parallel book translation** (5 agent ranks per arm, *Flatland* into Chinese).
Identical wall time and output volume; the difference is the artefact and the
context. Of 10 probe terms reported by two or more ranks, the unprotocolled arm
diverged on two (*Equilateral* rendered two ways in a 2–2 split, *Priest* two ways
by two ranks); the AgentMPI arm diverged on none. The glossary was agreed by 4
agent-evaluated merges with 3 on the critical path — exactly `ceil(log2 5)` — and
adherence to it, checked against the collective's own result object rather than
against agent self-report, was 1.00. Four of five ranks revised and republished
their opening after the halo exchange (durable window-cell versions, not
self-report); none did without it. Peak context in one rank: 8,661 tokens versus
897, because the unprotocolled coordinator materialised every translation.

**E2, eight agents building a Scheme interpreter.** Both arms passed **174/174**
held-out cases, so that instrument measured neither. The ablated arm also
*spontaneously recreated the protocol's mechanisms* — unprompted, its agents built
`contract` and `decisions` windows with cells named `interface` and
`eval-protocol` — and reached the same score for a third of the wall time and a
fifth of the context. We take that as evidence the abstraction matches the problem
and as a caution against selling it as a capability.

To separate the arms we used **differential testing**: 2500 programs generated
from a grammar written against the spec, run under both interpreters and compared
— symmetric between arms by construction, since no program is chosen after seeing
a failure. The arms agree on 92.4%. Of 190 disagreements, **111 are attributable
to a specification clause and every one favours the protocol arm**; none favours
the ablated arm. Stated honestly, those 111 observations trace to *one* root cause
(float exponent formatting). What makes that one defect interesting is that it
arose in **both** arms and only one arm's process removed it: in the protocol arm
the integrator raised it in a synchronised integration round, a third rank
seconded it in the shared fix log, and the printer's owner adopted it. In the
ablated arm a rank diagnosed it *more precisely than anyone in the protocol arm
did*, messaged the owner and the integrator twice with a one-line fix, and was
never answered. So the mechanism that paid off was not the negotiated contract —
which the ablated arm reinvented — but the scheduled point at which "what does the
spec say" got adjudicated once and written down.

**E3, mass launch failure.** A job requested at P=22 started only 6 ranks. It ran
to completion: all 6 survivors finalised, 5 of 5 collectives closed, 6 dead
subtrees dropped from the agent-evaluated reduction tree, 6 translated sections
produced.

**Microbenchmarks.** α = 5.74 ms, β = 1.22 µs/token, n½ = 4,720 tokens. Rendezvous
delivery charges a flat ~75 tokens whether the payload is 8 tokens or 32,768.
At P=128 an inlining allgather charges one rank 501,888 context tokens against
3,200 handle-based (157×). Recursive-doubling allreduce moves 36× the payload of
reduce-then-broadcast. A binomial agent-operator reduction beats a linear chain
11.6× at P=64 at identical total work. Receive latency is flat in unexpected-queue
depth out to 2048.

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
9. **A conditional send plus an unconditional receive is a deadlock, and no
   protocol can save you from it.** Our own harness had this bug: the integrator
   was told to send a fix assignment *for each failure*, the others to receive
   one, and when the tests passed four ranks waited on a message nobody would
   send. AgentMPI cannot prevent a program bug — but the deadline-bounded receive
   turned an unbounded hang into a stall that `ampi status` localised in one
   command, naming the missing ranks and the tag. The harness rule is the one MPI
   teaches about zero-length collectives: **send unconditionally, possibly
   empty**.
10. **Ambient identity without an assertion is unsafe, and we had to be shown.**
    We argued in the first draft of the spec that making rank ambient
    "eliminates the entire class" of wrong-rank errors. It eliminated the error
    we designed against — agents *passing* the wrong rank — and replaced it with
    a worse one: agents silently *being* the wrong rank. The host shares shell
    sessions between concurrent agents, so `AMPI_RANK` was rewritten between
    calls; in four of five runs, one rank absorbed 8–9 stray `init` events, one
    from nearly every other agent, and in one run the victim was declared failed
    and fenced *while working*. Nineteen of twenty-two agents reported it
    independently. The fix keeps ambient identity but makes it assertable:
    `--expect-rank`, `--expect-job`, an identity echo on every command, a
    launcher-issued per-rank token, and `--job-root` now overriding `AMPI_ROOT`.
11. **Never truncate a reduction operand.** An operand is the input to a
    function, so clipping it corrupts the result rather than shortening it — and
    clipping JSON mid-string yields something the operator cannot parse at all.
    Agents worked around it by prefix-matching the object store by hand. A
    *result* may be summarised; an *operand* may not.
12. **A canonical tree gives reproducibility, not consistency.** In a real
    eight-rank contract reduction, two branches met the *same* conflict and
    resolved it in *opposite* directions. Both rulings survived into the merged
    result and no merge could have noticed, because each saw a locally consistent
    pair. Pinning the tree shape makes the outcome reproducible; it does not make
    it consistent. Carry unresolved conflicts forward, or verify the closed
    result against a global invariant.
13. **Print only commands that exist.** Our reduction directive told agents to
    run a subcommand spelled with a space where the real one is hyphenated. Ten
    agents reported it, several while peers were blocked behind them.

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
