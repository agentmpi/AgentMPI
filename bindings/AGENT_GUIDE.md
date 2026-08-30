# The AgentMPI manual (for agents)

You are one **rank** in an AgentMPI job. AgentMPI is a message passing interface:
you and the other ranks are independent agents that coordinate *only* through the
`ampi` command. Nobody reads your thoughts, your files, or your terminal. If you
did not send it, it does not exist.

This manual is the contract. Read it once, then work.

---

## 0. The ten rules

1. **`ampi init` first, `ampi fini` last.** Nothing works before init.
2. **Your identity is ambient.** `AMPI_RANK` and `AMPI_ROOT` are already set.
   Never pass `--rank`. Never pretend to be another rank.
3. **Call `ampi` often.** Every call renews your *lease*. Go quiet for longer
   than the lease and the job declares you dead and replaces you.
4. **`AMPI_ERR_TIMEOUT` is not a failure.** It means "not yet". **Re-run the
   identical command** to resume the same wait; your place in the queue is
   durable. Do not change the arguments. Do not give up after one timeout.
5. **Name your collectives.** Always pass `--label <name>` to `barrier`,
   `bcast`, `gather`, `reduce`, `allreduce`, `scan`, `scatter`. Every rank must
   use the *same label* for the *same* collective. This is how ranks find each
   other.
6. **Never invent** a rank, a tag, a label, a window name or a key that was not
   given to you.
7. **Watch your context.** Run `ampi ctx`. Large payloads arrive as *handles*
   (`o:1a2b...`), not text. Read them with `ampi view <handle> --budget N`, and
   only read what you need.
8. **When output says `action_required`, do that action next.** Do not proceed
   with anything else first.
9. **Record your progress** with `ampi memo put <key> <value>` and in window
   cells. If you die, your replacement reads exactly that.
10. **Follow your task, then finalize.** Do not stop early, and do not silently
    skip a collective — other ranks are blocked waiting for you.

---

## 1. Identity and lifecycle

```bash
ampi init                 # join. Prints your rank, the world size, your role.
ampi info                 # who am I, which communicator, how big, who has failed
ampi ctx                  # my context budget: used / remaining
ampi fini                 # leave cleanly
```

If `ampi init` tells you `recovery_brief=<path>`, **you are a replacement for a
rank that failed**. Read that file. It lists what your predecessor was assigned,
what it published, what it received, and what it left outstanding. Continue its
work; do not redo committed work.

## 2. Point-to-point

```bash
ampi send --to 3 --tag review --in @notes.md
ampi send --to 3 --tag review --in "a short literal message"
ampi recv --from 3 --tag review --timeout 300
ampi recv --from any --tag any --timeout 300 --materialize
ampi probe                  # what is waiting, and what would it cost me?
ampi inbox                  # list everything pending (costs almost no context)
```

* `--tag` may be a number or a **word** (`review`, `chunk`, `done`). Words are
  hashed consistently, so sender and receiver just have to use the same word.
* `--from any` is a wildcard receive.
* Messages from the same sender to you with the same tag arrive **in the order
  they were sent**. Nothing else about ordering is guaranteed.
* `--idem <key>` makes a send safe to retry: a second send with the same key
  does nothing.

Nonblocking, when you want to overlap waiting with thinking:

```bash
R=$(ampi irecv --from 0 --tag plan --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["request"])')
# ... do real work ...
ampi wait "$R" --timeout 300
```

## 3. Collectives

All ranks in the communicator must call the same collective with the same
`--label`.

```bash
ampi barrier --label phase1-done --timeout 600
ampi barrier --label phase1-done --quorum 0.9      # release at 90% of live ranks

ampi bcast --root 0 --label plan --in @plan.md     # root supplies --in
ampi bcast --root 0 --label plan --materialize     # everyone else just calls it

ampi scatter --root 0 --label work --parts @chunks/    # root only
ampi scatter --root 0 --label work                     # workers receive slice i

ampi gather    --root 0 --label results --in @my_result.md
ampi allgather --label glossary --in @my_terms.json --budget 3000

ampi reduce    --op union   --label terms --in @my_terms.json --root 0
ampi allreduce --op vote    --label answer --in "42"
ampi exscan    --op concat  --label running-summary --in @my_summary.md
```

### Reduction operators

Run `ampi ops` for the list. Runtime operators are free and exact:

| op | meaning |
|---|---|
| `concat` | join in rank order |
| `union` | set union of JSON arrays, or of lines |
| `jsonmerge` | deep merge of JSON objects, recording conflicts |
| `sum` `max` `min` `count` | numeric |
| `and` `or` | logical |
| `vote` | majority vote over normalised answers |
| `maxby` | keep the operand with the largest `score` field |
| `first` | keep the lowest-rank contribution |

**Agent operators** (`--op agent:<label>`) are evaluated by *you*. When you call
a reduction with an agent operator you may be told:

```
action_required=merge  step=s:abc123  round=1
REDUCTION STEP (round 1, operator 'merge_glossary'). ...
  left  (yours, rank 4): /path/left.txt
  right (from rank 6):   /path/right.txt
Write the combined result to /path/merged.txt, then run:
  ampi reduce-commit --step s:abc123 --in @/path/merged.txt
```

Do exactly that. You may be given another step afterwards; keep going until the
output says `complete=true`. The merged result must have the **same shape** as
the inputs, because it may be merged again at the next round.

### Gather is dangerous; that is why it tells you

`gather`/`allgather` do **not** dump every contribution into your context. You
get a manifest: one line per contributor with its rank, token count and summary.
Then either read specific ones (`ampi view <handle>`) or re-run with `--budget N`
to get clipped bodies for all of them.

## 4. Shared state: windows

A window is a named, versioned key/value space every rank can read and write. Use
it for facts that many ranks will need but you cannot know in advance who.

```bash
ampi win create --name shared
ampi win put  --win shared --key decisions/naming --in @naming.md
ampi win get  --win shared --key decisions/naming --budget 800
ampi win ls   --win shared --prefix decisions/          # cheap: no bodies
ampi win hist --win shared --key decisions/naming       # who changed it, when

# atomic merge, no lock needed -- prefer this over get/modify/put
ampi win acc --win shared --key findings --op union --in '["found X"]'

# claim work atomically; exactly one rank wins
ampi win cas --win shared --key task/17 --expect unclaimed --value "rank:4"

# hand out ticket numbers
ampi win faop --win shared --key next_chunk --op sum --value 1

# leased exclusive access when you really need read-modify-write
ampi win lock --win shared --key spec --timeout 120
ampi win put  --win shared --key spec --in @spec.md
ampi win unlock --win shared --key spec

# close a shared-state epoch: barrier + "everyone's writes are in"
ampi win fence --win shared --label after-design
```

Conflict handling: `ampi win put --expect-version N` fails with
`AMPI_ERR_CONFLICT` if someone else wrote first. Re-read, merge, retry. That is
correct behaviour, not an error.

## 5. Sub-teams and topologies

```bash
ampi comm split --color 1 --key 0      # everyone with colour 1 forms a team
ampi comm list                          # what communicators exist
ampi comm cart --dims 4,3               # a 4x3 grid
ampi comm shift --direction 0 --disp 1  # who is upstream / downstream of me
ampi comm neighbors                     # who am I allowed to talk to
ampi neighbor-allgather --in @status.md --label sync
```

After `ampi comm split` you get a new communicator name; pass `--comm <name>` to
subsequent commands to operate inside your team.

## 6. When things fail

Failures are normal. The protocol gives you tools, not magic.

```bash
ampi failed              # who has died, why, and how long ago
ampi ack                 # accept the known failures (re-enables wildcard recv)
ampi comm revoke         # unblock EVERYONE stuck in a collective on this comm
ampi comm shrink         # build a new communicator over the survivors
ampi agree --label phase3-ok --flag true   # fault-tolerant agreement
```

Error classes you will see, and what to do:

| class | meaning | what to do |
|---|---|---|
| `AMPI_ERR_TIMEOUT` | not yet | **re-run the identical command** |
| `AMPI_ERR_PROC_FAILED` | a peer you need is dead | take over its work, or revoke + shrink |
| `AMPI_ERR_PROC_FAILED_PENDING` | someone died, but your wildcard recv may still succeed | `ampi ack`, then retry |
| `AMPI_ERR_REVOKED` | this communicator is dead | `ampi comm shrink`, then use the new one |
| `AMPI_ERR_CTX_EXCEEDED` | reading that would blow your budget | use `ampi view --budget N` |
| `AMPI_ERR_CONFLICT` | you lost a versioned write race | re-read, merge, retry |
| `AMPI_ERR_LOCK_BUSY` | someone holds the lock | retry; locks expire |
| `AMPI_ERR_FENCED` | you were declared dead and replaced | **stop working immediately** and say so |
| `AMPI_ERR_LATE` | a quorum collective closed without you | read the published result |

The recovery pattern, when a peer dies mid-phase:

```bash
ampi failed                       # confirm
ampi comm revoke --reason "rank 7 died in phase 2"
ampi comm shrink                  # -> tells you the new comm name and your new rank
ampi barrier --comm world#g1 --label phase2-retry
```

## 7. Context discipline

Your context window is the scarcest resource in the job. The protocol helps, but
you must cooperate.

* Payloads over the eager threshold arrive as handles. **Do not materialise them
  reflexively.** Ask what you need:
  ```bash
  ampi view o:9f2a --op outline               # just the headings
  ampi view o:9f2a --op grep:TODO --budget 300
  ampi view o:9f2a --op keys:summary,risks
  ampi view o:9f2a --op lines:120-180
  ampi view o:9f2a --op stat                  # size only
  ampi obj  o:9f2a --save ./big.md            # to disk, zero context cost
  ```
* Check `ampi ctx` periodically. Above 80% you will be warned.
* Compact: write your state into a window cell or a memo, then work from that
  instead of from your transcript.

## 8. Recording progress

```bash
ampi memo put phase "translating chapter 12, section 3 of 5"
ampi memo put glossary_version 4
ampi memo get
```

Memos are durable and are handed to your replacement if you die. Update them
whenever you complete something meaningful. This costs one cheap command and is
the difference between a recoverable job and a lost one.

## 9. Quick reference

```
ampi init | fini | info | ctx | man | status
ampi send --to R --tag T --in @f | recv [--from R|any] [--tag T|any] [--timeout S]
ampi probe | inbox | isend | irecv | wait REQ... | test REQ | cancel REQ
ampi barrier|bcast|scatter|gather|allgather|reduce|allreduce|scan|exscan|alltoall
     --label NAME [--root R] [--op OP] [--algo A] [--quorum Q] [--budget N]
ampi reduce-commit --step S --in @f
ampi win create|put|get|acc|cas|faop|ls|hist|lock|unlock|fence
ampi comm list|split|create|dup|cart|shift|graph|neighbors|revoke|shrink
ampi agree --label L | failed | ack | memo put|get | recover
ampi view HANDLE [--op SPEC] [--budget N] | obj HANDLE [--save PATH]
```

Add `--json` to any command for machine-readable output.
