# AgentMPI rank card --- rank 4 of 6

You are **rank 4** in an AgentMPI job of 6 ranks. AgentMPI is a
message-passing protocol: you coordinate with the other ranks *only* through the
`ampi` command-line tool. Do not read or write another rank's scratch directory,
and do not try to contact another rank by any other means. Everything you need
arrives through the protocol.

## How to invoke `ampi`

**Always pass `--job` and `--rank` explicitly, on every single call.** Do not
rely on environment variables: shell state may not survive between your tool
invocations, and a call that silently picks up the wrong rank will corrupt the
run in ways that are hard to see. Every command looks like this:

```
/home/ubuntu/.local/bin/ampi --job /workspace/experiments/ampi/e3_fault/runs-consolidated-v2/job --rank 4 <subcommand> ...
```

To keep that short, define a shell function at the start of every command you
run (not once at the beginning --- every time):

```
A="/home/ubuntu/.local/bin/ampi --job /workspace/experiments/ampi/e3_fault/runs-consolidated-v2/job --rank 4"
$A status
```

Whenever this card writes `ampi ...` below, run `$A ...` instead.

Your scratch directory is `/workspace/experiments/ampi/e3_fault/runs-consolidated-v2/job/ranks/4`. Write intermediate files there.

## The protocol, in one page

Every command prints JSON. A command that fails prints JSON with `"ok": false`
and an `error` field naming an AgentMPI error class, and exits non-zero.

```
ampi init --role "author-recovery"      # join the job. Do this first.
ampi status                      # who else is here and what state they are in
ampi hb --expect-idle 300        # "I am about to think for 5 minutes, do not
                                 #  declare me dead"

ampi send --to R --tag T --file PATH        # point to point
ampi recv --source R --tag T --deref        # blocking receive; --source -1 is
                                            # ANY_SOURCE, --tag -1 is ANY_TAG
ampi probe                                  # is anything waiting for me?

ampi barrier                                # everyone waits for everyone
ampi bcast --root R --file PATH             # one to all
ampi scatter --root R --json-file PATH      # root splits a block map
ampi gather --root R --json-file PATH       # all to one
ampi allgather --json-file PATH             # all to all
ampi allreduce --op OP --json-file PATH     # all to all, combined by OP
ampi reduce --root R --op OP --json-file PATH

ampi win-create --name W                    # a shared artifact space
ampi win-put --win W --key K --file PATH    # write a cell
ampi win-get --win W --key K                # read a cell
ampi win-claim --win W --key K              # atomically claim a work item
ampi win-lock --win W --key K               # take an exclusive lease
ampi win-unlock --lock-id L
ampi win-fetch-add --win W --key K          # atomic counter

ampi ctx                                    # how much context budget you have
ampi finalize --note "..."                  # leave cleanly. Do this last.
```

## Five rules that matter

1. **Collectives are collective.** If the instructions say to call `ampi
   barrier`, every rank must call it, the same number of times, in the same
   order. Skipping one, or calling `bcast` where others call `barrier`, is
   reported as `AMPI_ERR_COLLECTIVE_MISMATCH` and stalls your peers.
2. **Large payloads are passed by reference.** A big message arrives with a
   `handle` and a short `digest` instead of the body. Read the digest first and
   only run `ampi deref --handle H` if you actually need the full text. Your
   context is a budget; `ampi ctx` shows it.
3. **Heartbeat before long work.** Before any step that will take more than a
   couple of minutes without an `ampi` call, run `ampi hb --expect-idle
   SECONDS`, and over-estimate rather than under-estimate. A declared period
   can only lengthen your lease, never shorten it, so guessing high is free.
   A blocking call such as `recv` or a collective heartbeats for you while it
   waits, so you do not need to declare anything before one of those.
4. **Claim before you work.** When picking up a shared work item, use `ampi
   win-claim`. If it returns `"claimed": false` somebody else already has it;
   take a different item. Never assume an item is yours.
5. **Retry, do not improvise.** If a command fails, read the `message` field.
   It usually tells you the remedy (`--projection digest`, re-read and retry a
   compare-and-swap, and so on). If a collective returns `"status":
   "op_required"`, that is the library asking *you* to evaluate a reduction
   operator; follow the `next` field exactly.

## Your task

You own one section of a short technical report that 6 of you are
writing together. Some of you will be killed part way through. The report must
be finished anyway.

**Phase 1 --- publish your draft.**

```
ampi init --role "author-4"
ampi win-create --name report
```

Write your section on this topic:

> A paragraph explaining the difference between backward recovery (restart from a checkpoint) and forward recovery (continue with fewer participants).

Aim for 130 words of real prose --- this is a genuine writing task, not a
placeholder. Save it to `/workspace/experiments/ampi/e3_fault/runs-consolidated-v2/job/ranks/4/recovery.md`, then publish it and record that you are
done:

```
ampi win-put --win report --key section/recovery --file /workspace/experiments/ampi/e3_fault/runs-consolidated-v2/job/ranks/4/recovery.md
ampi win-put --win report --key status/recovery --json '{"author": 4, "state": "drafted"}'
```

Then count yourself done, and wait for the others by polling a counter rather
than by calling a collective --- some of them are about to be killed, and a
barrier would simply hang:

```
ampi win-fetch-add --win report --key drafted
```

Now poll, every 30 seconds, for up to 10 minutes, running
`ampi hb --expect-idle 120` between polls:

```
ampi win-get --win report --key drafted
ampi failures
```

Stop polling as soon as **either** the counter reaches 6 **or**
`ampi failures` reports one or more ranks in its `failed` list. If some ranks
have failed, the counter will never reach 6, which is exactly why
you must watch both.

**Phase 2 --- repair the communicator.**

If `ampi failures` lists any failed rank, the world communicator is no longer
usable for collectives and the survivors have to rebuild it. Do this:

1. Run `ampi revoke`. Several survivors will do this; it is idempotent and
   safe.
2. Run `ampi shrink --name survivors`. This builds a communicator over the
   ranks still alive, renumbered densely. The output tells you your new rank.
3. Run `ampi --comm survivors agree --value true`. This is the one collective
   in this experiment, and it is deliberately over the survivors only: it
   confirms that everyone still standing is in the same place and intends to
   continue. It tolerates further failures while it runs.

From this point on, **use `--comm survivors` on every collective**.

**Phase 3 --- adopt the orphaned work.**

Run `ampi win-list --win report --prefix section/` and
`ampi win-list --win report --prefix status/`. Any section that a dead author
already published is still there: window writes are durable and outlive their
author, so it must be **reused, not rewritten**.

For any section that is missing entirely, the survivors must divide the work.
Claim one atomically before you write it:

```
ampi win-claim --win report --key claim/<missing-section-name>
```

If that returns `"claimed": false` somebody else took it; try another. If it
returns `"claimed": true` it is yours: write it, then
`ampi win-put --win report --key section/<name> --file <your file>`.

**Phase 4 --- finish.**

Write a JSON summary to `/workspace/experiments/ampi/e3_fault/runs-consolidated-v2/job/ranks/4/result.json`:

```
{"rank": 4, "survived": true, "new_rank": <your rank in survivors>,
  "sections_present": <how many section/* keys exist>,
  "failed_ranks": [<what ampi failures reported>],
  "adopted": [<names of sections you found already published by others>],
  "wrote_extra": [<names of sections you wrote to cover for a dead peer>]}
```

Finally `ampi --comm survivors finalize --note "e3 done"`.

Run `ampi hb --expect-idle 300` before you start writing prose, and again
before any other long step.

Do not modify files outside your scratch directory. Do not run git. If you are
killed, you simply stop; that is the experiment working.
