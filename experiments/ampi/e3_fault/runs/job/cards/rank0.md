# AgentMPI rank card --- rank 0 of 8

You are **rank 0** in an AgentMPI job of 8 ranks. AgentMPI is a
message-passing protocol: you coordinate with the other ranks *only* through the
`ampi` command-line tool. Do not read or write another rank's scratch directory,
and do not try to contact another rank by any other means. Everything you need
arrives through the protocol.

## Your environment

Run this once at the start of your shell session, then every `ampi` command
picks up its identity automatically:

```
export PATH=/workspace/.venv/bin:$PATH
export AMPI_JOB_DIR=/workspace/experiments/ampi/e3_fault/runs/job
export AMPI_RANK=0
export AMPI_COMM=world
```

If your shell does not persist between commands, prefix each call instead:

```
PATH=/workspace/.venv/bin:$PATH AMPI_JOB_DIR=/workspace/experiments/ampi/e3_fault/runs/job AMPI_RANK=0 ampi status
```

Your scratch directory is `/workspace/experiments/ampi/e3_fault/runs/job/ranks/0`. Write intermediate files there.

## The protocol, in one page

Every command prints JSON. A command that fails prints JSON with `"ok": false`
and an `error` field naming an AgentMPI error class, and exits non-zero.

```
ampi init --role "author-abstract"      # join the job. Do this first.
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
   SECONDS`. Otherwise the failure detector may declare you dead.
4. **Claim before you work.** When picking up a shared work item, use `ampi
   win-claim`. If it returns `"claimed": false` somebody else already has it;
   take a different item. Never assume an item is yours.
5. **Retry, do not improvise.** If a command fails, read the `message` field.
   It usually tells you the remedy (`--projection digest`, re-read and retry a
   compare-and-swap, and so on). If a collective returns `"status":
   "op_required"`, that is the library asking *you* to evaluate a reduction
   operator; follow the `next` field exactly.

## Your task

You own one section of a short technical report that the eight of you are
writing together. Some of you will be killed part way through. The report must
be finished anyway.

**Phase 1 --- publish your draft.**

```
ampi init --role "author-0"
ampi win-create --name report
```

Write your section on this topic:

> A 120-word abstract for a systems paper about a message-passing protocol for multi-agent systems.

Aim for 160 words of real prose --- this is a genuine writing task, not a
placeholder. Save it to `/workspace/experiments/ampi/e3_fault/runs/job/ranks/0/abstract.md`, then publish it and record that you are
done:

```
ampi win-put --win report --key section/abstract --file /workspace/experiments/ampi/e3_fault/runs/job/ranks/0/abstract.md
ampi win-put --win report --key status/abstract --json '{"author": 0, "state": "drafted"}'
```

**Phase 2 --- synchronise, and cope with whatever has happened.**

Run `ampi barrier --timeout 900`.

That barrier may fail, and if it does it is not your fault. Handle it like
this:

1. If it reports `AMPI_ERR_PROC_FAILED`, `AMPI_ERR_REVOKED`, or times out, run
   `ampi failures` and `ampi doctor` to see what the runtime knows.
2. If any rank is listed as failed and the communicator is not yet revoked, run
   `ampi revoke`. It is safe and expected for several survivors to do this; the
   operation is idempotent.
3. Every survivor then runs `ampi shrink --name survivors`. This builds a new
   communicator over the ranks that are still alive, renumbered densely. Note
   what your new rank is --- `ampi --comm survivors status` and the shrink
   output will tell you.
4. Every survivor then runs `ampi --comm survivors agree --value true` to
   confirm that the survivors are all in the same place and intend to continue.

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

```
ampi --comm survivors barrier --timeout 900
```

Then write a JSON summary to `/workspace/experiments/ampi/e3_fault/runs/job/ranks/0/result.json`:

```
{"rank": 0, "survived": true, "new_rank": <your rank in survivors>,
  "sections_present": <how many section/* keys exist>,
  "adopted": [<names of sections you found already published by others>],
  "wrote_extra": [<names of sections you wrote to cover for a dead peer>]}
```

Finally `ampi --comm survivors finalize --note "e3 done"`.

Run `ampi hb --expect-idle 300` before you start writing prose, and again
before any other long step.

Do not modify files outside your scratch directory. Do not run git. If you are
killed, you simply stop; that is the experiment working.
