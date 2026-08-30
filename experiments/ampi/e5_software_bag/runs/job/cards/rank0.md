# AgentMPI rank card --- rank 0 of 8

You are **rank 0** in an AgentMPI job of 8 ranks. AgentMPI is a
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
/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e5_software_bag/runs/job --rank 0 <subcommand> ...
```

To keep that short, define a shell function at the start of every command you
run (not once at the beginning --- every time):

```
A="/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e5_software_bag/runs/job --rank 0"
$A status
```

Whenever this card writes `ampi ...` below, run `$A ...` instead.

Your scratch directory is `/workspace/experiments/ampi/e5_software_bag/runs/job/ranks/0`. Write intermediate files there.

## The protocol, in one page

Every command prints JSON. A command that fails prints JSON with `"ok": false`
and an `error` field naming an AgentMPI error class, and exits non-zero.

```
ampi init --role "architect"      # join the job. Do this first.
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

You are the architect. Publish the contract, create the package skeleton, then
help integrate. You never block waiting for anyone.

**Step 1.** Set up and publish:

```
ampi init --role architect
ampi win-create --name build
ampi win-put --win build --key contract --json-file /workspace/experiments/ampi/e5_software_bag/runs/job/contract.json
ampi win-put --win build --key modules --json '["estimate", "policy", "compact", "ledger", "planner", "cli"]'
```

**Step 2.** Create the package skeleton under `/workspace/experiments/ampi/e5_software_bag/runs/job/artifact`:

* `/workspace/experiments/ampi/e5_software_bag/runs/job/artifact/tokenbudget/__init__.py` re-exporting the public names the
  contract lists, with a real module docstring;
* `/workspace/experiments/ampi/e5_software_bag/runs/job/artifact/tests/` (empty directory is fine);
* `/workspace/experiments/ampi/e5_software_bag/runs/job/artifact/pyproject.toml` naming the package `tokenbudget`, requiring
  Python 3.11, with no third-party dependencies.

Because six implementers will each need to add their module's names to
`__init__.py`, that file is shared. Whenever you edit it, take the lease first:

```
ampi win-lock --win build --key initpy --ttl 180
# ... edit /workspace/experiments/ampi/e5_software_bag/runs/job/artifact/tokenbudget/__init__.py ...
ampi win-unlock --lock-id <the lock_id you were given>
```

Then mark yourself ready: `ampi win-fetch-add --win build --key ready`.

**Step 3.** While the implementers work, poll for questions and answer them.
Do this in a loop, roughly every 30 seconds, for as long as
`ampi win-get --win build --key done` reports fewer than 6:

```
ampi probe --tag 10
ampi recv --tag 10 --timeout 20 --nonblocking     # returns immediately if nothing
ampi send --to <asker> --tag 11 --text "<your answer>"
```

If you must amend the contract, take `ampi win-lock --win build --key contract`
first, re-read the cell, apply your change, write it back, and unlock.

**Step 4.** When `done` reaches 6, run the full suite yourself:

```
cd /workspace/experiments/ampi/e5_software_bag/runs/job/artifact && /workspace/.venv/bin/python -m pytest -q
```

Fix only skeleton-level problems (a missing re-export, an import cycle). Then
write `/workspace/experiments/ampi/e5_software_bag/runs/job/ranks/0/result.json`:

```
{"rank": 0, "role": "architect", "questions_answered": <n>, "amendments": <n>,
  "pytest_exit": <code>, "summary": "<one honest sentence>"}
```

and run `ampi finalize --note "e5 architect done"`.

Run `ampi hb --expect-idle 900` before long stretches of work. Do not run git.
