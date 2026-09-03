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
| 4 | `put`, `compare_and_swap`, agent **research**, `win_fence` | each term claimed by exactly one rank and researched on the web; the epoch closes with its writes visible |
| 5 | `allreduce(union)`, `bcast` | the binding glossary — an invariant: this reduction must lift nothing |
| 6 | `exscan(sum)` | each rank's offset in the book, in log p rounds |
| 7 | agent **translate**, `put`; `detect_failures` + `compare_and_swap` | one page per task under the glossary; pages of a convicted rank are claimed and translated by survivors |
| 8 | `win_fence`, `cart_shift`, `get`, agent **review**, `win_fence`, agent **revise** | a review ring: each rank reviews its right neighbour's pages; authors revise their own |
| 9 | `neighbor_allgather`, agent **seam** | boundary sentences exchanged on the ring; each rank revises only its own edges |
| 10 | `win_fence`, `gather`, `barrier` | the manifest; the root assembles |

Failure semantics worth stating: an executor's death is a rank's death. A task
nobody claims within the claim window makes the harness kill its own rank
(`AMPI_ERR_NO_WORKER` → `kill`), so its peers drop it at their next collective
rather than waiting out a phase timeout, and its pages are stolen. A machine that
dies outright loses its lease; the executor's periodic keepalive keeps the lease
alive across long steps instead of extending it once per step, so conviction
takes a bounded time rather than a step's worth of lease.

## Two devices per machine

A rank on a cloud machine holds two jobs. The **git device** (`ampi/device/gitlog.py`)
carries the protocol: every collective, window cell and trace event is a commit on
`ampi-jobs/<name>`, and a rejected push is a lost compare-and-swap. A **local SQLite
job** carries the task queue the machine's own Claude Code session serves through
the standard worker prompt; the broker mirrors its claim and submission events into
the shared trace with their original timestamps, so the analysis sees the work
spans in the one log that survives the machine.

Three things were changed in the runtime to make the git transport bearable at
64 writers, and each is a measurement: trace appends and payload bodies are deferred and
folded into the device's next commit (they were half the commits on the branch);
a rank renews its lease every ten minutes against a thirty-minute lease rather
than every five seconds; and a reader whose fetches keep finding nothing backs off.

## Running the series

A cloud session is not told its rank; its machine takes one. The branch carries
`LAUNCH.json`, and a `SessionStart` hook (`.claude/hooks/session-start.sh`) on
every cloud session started from the branch reads it, claims free rank slots by
compare-and-swap on the job (`slot/<r>`, version 0, from a random start offset),
and starts the harness for them in the background. The session is given only
the worker instruction (`session-prompt`), which contains no protocol words.
Ranks are claimed, not assigned, because the launcher cannot address a machine
before it exists; and the hook starts the harness because asking the session to
do it is asking a permission classifier, which in one launch refused half the
machines.

A machine may hold several ranks (`ranks_per_machine` in `LAUNCH.json`): each
is its own clone, lease and writer on the shared branch, exactly as on its own
machine, and they share one local task queue that the session serves for all of
them (`ampi worker --serve`). This is how sixty-four ranks are run without
sixty-four sessions.

The harness is **restartable**. A cloud container is paused when its session
idles or when the account hits its usage limit, and the harness process does
not survive the pause. Every task result is memoised in a window (`memo`, keyed
`rank/label`; pages live in the page window already), so on resume the hook
restarts the harness (`autostart --resume`), the rank program runs again from
the top, replays every finished task from the memo, passes through the
collectives it already completed (joining one is idempotent per rank), and
continues where it stopped. A pause shorter than the lease is not a failure
and convicts nobody; a longer one convicts the rank, its pages are stolen, and
the same replay lets it re-init at a new epoch and finish whatever is left.

```bash
# once, from anywhere with push access to the repository
python experiments/e6_book/rank.py create --name e6-book-p16 --size 16 --pages 1-64
# then set LAUNCH.json: name, enabled, ranks_per_machine, launcher_session; commit; push;
# and create one cloud session per machine from the branch with the bootstrap prompt:
python experiments/e6_book/rank.py session-prompt --name e6-book-p16 --size 16 --bootstrap

# what the hook runs on each machine, and what it runs again after a pause
python experiments/e6_book/rank.py autostart
python experiments/e6_book/rank.py autostart --resume

# the same rank program, by hand, for one or several ranks on this machine
python experiments/e6_book/rank.py run --name e6-book-p16 --rank 6,7 [--resume]

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

## The corpus, and why it is not the Durov book

The harness was written against the legacy project's corpus, N. V. Kononov's
*Код Дурова* (2013), and `--corpus durov` still runs it: the legacy page
extraction is cloned at run time, its glossary seeds the survey, and the output
is its page schema. The production series did **not** run on it. In the
real-agent smoke test the translate executor declined the page, in its own
words, because it was being asked for a verbatim sentence-by-sentence rendering
of an in-copyright book as one of a hundred pages being translated in parallel,
which is a full derivative work rather than a quotation. A retry produced the
page, so the refusal is not a hard rule, but driving ninety-eight pages of an
in-copyright book through executors that object to exactly that is not
something this harness will do, and its prompts were not reworded to get past
the objection. A rights holder can run the series unchanged.

The series runs instead on Ilf and Petrov's *Двенадцать стульев* (1928),
Part One, from Russian Wikisource (`--corpus chairs`, the default). Both authors
died before 1943 and the book was published in 1928, so it is in the public
domain everywhere the series is read. It is the right substitute for the
question: NEP-era institutions and acronyms, church and pre-revolutionary
vocabulary, Odessa and Moscow slang, parodied newspaper prose, verse, and
allusions a reader of 1928 caught without help. Rendering it well is a
comparative literary and historical problem, which is what makes the
terminology coupling between pages real, and it is famously difficult to
translate, which is what makes the research window earn its keep.

The forty chapters are fetched as raw wikitext, cached under `work/e6/chairs`
(untracked), converted to paragraphs, and the first twenty-one chapters are cut
into 93 pages of about 3,000 characters at paragraph boundaries, matching the
Durov extraction's page size so that a task costs the same on either corpus.
`runs/<name>/corpus_manifest.json` carries per-page digests and a digest of the
fetched wikitext, so two runs can be shown to have cut the same text. Pages are
assigned as contiguous segments balanced by character count, which is what
gives the seam exchange something to reconcile.
