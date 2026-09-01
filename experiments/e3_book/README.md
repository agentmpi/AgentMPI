# E3 — book translation as an AgentMPI harness

The production experiment: a long-running, real-agent translation of a Russian
book into three languages at p = 16, 32 and 64, written entirely against the
AgentMPI protocol.

## What this replaces, and why it is the interesting case

The legacy project this experiment supersedes launched sixteen agents against a
coordination scheme written as *instructions*: discover your peers, compute your
stripe, claim pages, review a peer before claiming more. One of the sixteen
ignored the scheme and translated the book by itself. The run was recorded as a
success, because a book came out of it.

That is the characteristic failure, and it is not carelessness. A coordination
scheme an agent can decline is not a mechanism, it is a request; and a population
in which one member declines does not degrade gracefully, it degenerates into one
member. The AgentMPI answer is that the protocol belongs in the harness: an
agent's entire obligation here is to read a prompt file, produce an artifact, and
submit it, and no prompt in this experiment mentions a barrier, a reduction, a
window, or the existence of peers as parties to a protocol.

The task is chosen because it is *unevenly* parallel, which is the only
interesting kind. Rendering a paragraph is independent work. Three things are
not, and each maps onto a different piece of MPI:

| Coupling | Why it is real | Mechanism |
| --- | --- | --- |
| Terminology | a name or period slang term rendered four ways reads as four translators | `allreduce(union)` with conflict lifting, arbitrated once at the root |
| Research | deciding what a 2013 Russian allusion carried is expensive, and the same term recurs across segments | a window plus `compare_and_swap` to claim a term, closed by `win_fence` |
| Seams | adjacent segments must join: pronouns, tense, a sentence continued across a boundary | `neighbor_allgather` on a ring — a halo exchange, not a sequential pass |

## The nine phases

```
0  bcast              the commission: brief, languages, conventions
1  scatter            each rank receives one contiguous segment
2  agent              survey: what must be rendered consistently, and why it is hard
3  allreduce(union)   the term census; disagreements lifted, not merged away
   op_arbitrate       the root settles each lifted conflict exactly once
   bcast              the research agenda
4  win + claim        research, each term claimed by exactly one rank, then agent
5  win_fence          close the research epoch (barrier + visibility)
   allreduce(union)   the binding glossary, lifted and arbitrated
   bcast              the glossary, by handle
6  exscan             assembly offsets, without serialising the assembly
7  agent              translate under the binding glossary
8  neighbor_allgather seam exchange on a ring; each rank revises its own edges
9  gather             a manifest, not a concatenation; the root assembles
```

Every phase is barrier-separated with a declared policy, so a missing executor
degrades the run rather than hanging it.

Some choices are worth stating because the obvious alternative is wrong:

- **`compare_and_swap`, not a lock, to claim a term.** A lock held by an executor
  whose session ended wedges the term until the lease expires; a swapped cell
  cannot be held by a dead rank.
- **`win_fence`, not a bare barrier, to close research.** The fence adds the
  guarantee that the epoch's writes are visible, which is what turns a blackboard
  into a sequence of supersteps.
- **`gather` returns a manifest.** At p = 64 an inlining gather would charge the
  root a six-figure token bill for something it only needs to index.
- **The agenda is rotated per rank.** Every rank scanning claims in the same order
  makes the loop a thundering herd: p ranks swap the same cell, one wins, and the
  rest have burned a round trip to learn they lost.

## Running it

Validate the harness against the surrogate executor first — it is deterministic,
free, and it exercises every collective:

```bash
python -m experiments.e3_book.harness --name e3-stub-p64 --size 64 --executor stub
```

For a real run, start the harness against the broker, then launch executors:

```bash
python -m experiments.e3_book.harness --name e3-real-p16 --size 16 --executor broker &
python experiments/launch.py --name e3-real-p16 --size 16 --executors 8 \
    --job-root work/e3/e3-real-p16/job
# then run each runs/e3-real-p16/prompts/exec*.md as an agent session
```

`--executors` below `--size` oversubscribes: one session occupies several ranks in
turn, exactly as `mpirun -np 100` on eight cores runs a hundred ranks. It works
here for the same reason it works there — a rank is a durable role whose state
lives outside its executor — and it works *only* because the protocol is in the
harness. An executor serving ten ranks agent-side would have to be inside ten
barriers at once.

## Ablations

`--arm` removes one mechanism and changes nothing else, so a comparison isolates
the mechanism rather than the prompt:

| arm | removes |
| --- | --- |
| `full` | nothing |
| `noglossary` | the census, the research, and the binding glossary |
| `noresearch` | external research; ranks reduce their first-pass guesses |
| `noseams` | the halo exchange |

## Watching a run

A run at this length is not something to inspect afterwards. Serve the live
analysis while it goes:

```bash
ampi viewer --job-root work/e3/e3-real-p16/job --campaign e3-real-p16 --port 7842
```

The queue panel is the one to watch. Every stall in the p=16 run was an executor
session ending rather than anything in the protocol, and the signature is always
the same: tasks in `queued` with no `claimed`, which means the fix is another wave
of executors and not a change to the harness.

## Analysis

Per run, from the event log alone:

```bash
ampi analyze --trace runs/e3-real-p16/harness.trace.jsonl --out runs/e3-real-p16/analysis
```

Across the series, which is where the scaling questions live:

```bash
python -m experiments.e3_book.analyze --runs e3-real-p16,e3-real-p32,e3-real-p64
```

## The corpus

In copyright, fetched at run time, and never redistributed by this repository.
What is committed is the protocol evidence and the glossary the population
researched. See [`DATA_POLICY.md`](DATA_POLICY.md), which is enforced in
`corpus.py` rather than left to discipline.
