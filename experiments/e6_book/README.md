# E6 — a production book translation, one rank per cloud machine

The experiment the protocol was designed for: a Russian non-fiction book
(N. V. Kononov, *Код Дурова*, 2013) rendered into English, Chinese and Japanese
by a population of 16, 32 and 64 ranks, **each rank a Claude Code session on its
own virtual machine**, with a git branch as the only thing the machines share.
The product is the legacy project's deliverable — one sentence-aligned JSON file
per page in its own schema, which its PDF compiler consumes — and the evidence is
the trace.

## What this replaces

The legacy project launched sixteen agents against a coordination scheme written
as *instructions*: discover your peers, compute your stripe, claim pages, review a
peer before claiming more. One of the sixteen ignored the scheme and translated
the book by itself, and the run was recorded as a success because a book came
out. That is the characteristic failure, and it is not carelessness: a
coordination scheme an agent can decline is a request, not a mechanism, and a
population in which one member declines degenerates into one member.

Here the protocol is in the harness. An agent's whole obligation is to read a
prompt file, produce an artifact and submit it; no prompt mentions a barrier, a
reduction, a window, a lock or a peer as a party to a protocol
(`tests/test_e6_book.py` checks that the session instruction contains none of
those words).

## The rank program

`harness.py: BookHarness.rank_main` runs once per rank. Each phase is a test of
one mechanism:

| phase | mechanism | what it does here |
| --- | --- | --- |
| launch | `barrier` | everyone arrived; the policy names what to do about the rest |
| 0 | `bcast` | the commission: book, languages, per-page digests, segments |
| 1 | `scatter` | each rank its contiguous pages, in a self-identifying slice |
| 2 | agent **survey**; `win_lock` + `put` | the terms to render consistently; chapter titles and conventions merged into a registry under an exclusive lock (read-modify-write) |
| 3 | `allreduce(union)`; `gather`; agent **arbitrate**; `op_arbitrate`; `bcast` | the term census with disagreements *lifted*; the root settles every conflict once; the agenda, contested terms first |
| 4 | `put`, `barrier`, `compare_and_swap`, agent **research**, `win_fence` | each term claimed by exactly one rank and researched on the web; the epoch closes with its writes visible |
| 5 | `allreduce(union)`, `bcast` | the binding glossary — an invariant: this reduction must lift nothing |
| 6 | `exscan(sum)`, `barrier` | each rank's offset in the book, in log p rounds |
| 7 | agent **translate**, `put`; `detect_failures` + `compare_and_swap` | one page per task under the glossary; pages of a convicted rank are claimed and translated by survivors |
| 8 | `win_fence`, `cart_shift`, `get`, agent **review**, `win_fence`, agent **revise** | a review ring: each rank reviews its right neighbour's pages; authors revise their own |
| 9 | `neighbor_allgather`, agent **seam** | boundary sentences exchanged on the ring; each rank revises only its own edges |
| 10 | `win_fence`, `gather`, `barrier` | the manifest; the root assembles |

Failure semantics worth stating: an executor's death is a rank's death. A task
nobody claims within the claim window makes the harness kill its own rank
(`AMPI_ERR_NO_WORKER` → `kill`), so its peers drop it at their next collective
rather than waiting out a phase timeout, and its pages are stolen. A machine that
dies outright loses its lease; the executor's once-a-minute keepalive keeps a
short lease alive across long steps instead of extending a long one once, so
conviction takes minutes, not a step's worth of lease.

## Two devices per machine

A rank on a cloud machine holds two jobs. The **git device** (`ampi/device/gitlog.py`)
carries the protocol: every collective, window cell and trace event is a commit on
`ampi-jobs/<name>`, and a rejected push is a lost compare-and-swap. A **local SQLite
job** carries the task queue the machine's own Claude Code session serves through
the standard worker prompt; the broker mirrors its claim and submission events into
the shared trace with their original timestamps, so the analysis sees the work
spans in the one log that survives the machine.

Three things were changed in the runtime to make the git transport bearable at
64 writers, and each is a measurement: trace appends are deferred and folded into
the next commit (they were half the commits on the branch); a blocked rank renews
its lease once a minute rather than every five seconds; and a reader whose fetches
keep finding nothing backs off.

## Running the series

```bash
# once, from anywhere with push access to the repository
python experiments/e6_book/rank.py create --name e6-book-p16 --size 16

# per rank: the instruction a cloud session is created with
python experiments/e6_book/rank.py session-prompt --name e6-book-p16 --rank 7 --size 16

# what the session runs, in the background, on its machine
python experiments/e6_book/rank.py run --name e6-book-p16 --rank 7

# while it runs, and afterwards
python experiments/e6_book/rank.py status  --name e6-book-p16 --brief
python experiments/e6_book/rank.py kill    --name e6-book-p16 --rank 5   # fault injection
python experiments/e6_book/rank.py collect --name e6-book-p16
```

`collect` reads the branch back and writes `runs/<name>/`: the trace, the
assembled book (`out/pages/page_NNN.json`, `out/book.jsonl`), the glossary the
population researched, a report, and the analysis (`ampi analyze`).

Before paying for machines, validate the harness on one:

```bash
python experiments/e6_book/rank.py local --name e6-stub-p16 --size 16 --executor stub
python experiments/e6_book/rank.py local --name e6-stub-fault --size 16 --executor stub \
    --stub-latency 0.4 --kill-rank 5 --kill-after 6        # pages of rank 5 get stolen
python experiments/e6_book/rank.py local --name e6-claude-p2 --size 2 --executor claude \
    --pages 13-16                                         # real agents, four pages
```

## The corpus

Cloned from the legacy repository at run time into `work/e6/legacy` (untracked)
and never vendored here; `runs/<name>/corpus_manifest.json` carries per-page
digests so two runs can be shown to have cut the same book. The unit of work is
the page because the deliverable is per page; pages are assigned as contiguous
segments balanced by character count, which is what gives the seam exchange
something to reconcile.
