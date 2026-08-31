# AgentMPI rank card --- rank 1 of 8

You are **rank 1** in an AgentMPI job of 8 ranks. AgentMPI is a
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
/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e5_software_bag/runs2/job --rank 1 <subcommand> ...
```

To keep that short, define a shell function at the start of every command you
run (not once at the beginning --- every time):

```
A="/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e5_software_bag/runs2/job --rank 1"
$A status
```

Whenever this card writes `ampi ...` below, run `$A ...` instead.

Your scratch directory is `/workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/1`. Write intermediate files there.

## The protocol, in one page

Every command prints JSON. A command that fails prints JSON with `"ok": false`
and an `error` field naming an AgentMPI error class, and exits non-zero.

```
ampi init --role "implementer"      # join the job. Do this first.
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

You claim one module of a shared Python package, write it, and prove it works.
You never wait for another rank.

**Step 1.** Join and read the contract:

```
ampi init --role implementer
ampi win-create --name build
ampi win-get --win build --key contract --out /workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/1/contract.json
```

If that reports `"found": false`, the architect has not published yet: wait 20
seconds and try again, up to ten times.

**Step 2.** Claim a module. Try these in order, starting with **estimate**:

1. `estimate`
2. `policy`
3. `compact`
4. `ledger`
5. `planner`
6. `cli`

```
ampi win-claim --win build --key module/<name>
```

`"claimed": true` means it is yours; stop trying. `"claimed": false` means
someone else has it, so move to the next name. **Never write a module you did
not claim.** If you win nothing, all six are taken: say so in your report,
write extra tests for an existing module instead, and skip to step 5.

**Step 3.** Implement your module at `/workspace/experiments/ampi/e5_software_bag/runs2/job/artifact/tokenbudget/<name>.py`,
following the contract exactly: the listed exports, the stated behaviour,
standard library only, type annotations, a module docstring. Real working
code, not stubs.

Then write at least six tests at `/workspace/experiments/ampi/e5_software_bag/runs2/job/artifact/tests/test_<name>.py`, covering the
edge cases the contract names explicitly. Run them until they pass:

```
cd /workspace/experiments/ampi/e5_software_bag/runs2/job/artifact && /workspace/.venv/bin/python -m pytest tests/test_<name>.py -q
```

If the contract is ambiguous, ask rather than guess:
`ampi send --to 0 --tag 10 --text "<question>"` then
`ampi recv --source 0 --tag 11 --timeout 120`. If no answer comes, make a
reasonable choice and record it in your report.

**Step 4.** Add your module's public names to the shared
`/workspace/experiments/ampi/e5_software_bag/runs2/job/artifact/tokenbudget/__init__.py`. That file is shared, so take the lease
first and hold it only while editing:

```
ampi win-lock --win build --key initpy --ttl 180
# re-read the file, add your exports, write it back
ampi win-unlock --lock-id <the lock_id you were given>
```

**Step 5.** Publish what you did and count yourself done:

```
ampi win-put --win build --key module/<name>/summary --json '{"module": "<name>", "by": 1, "exports": [...], "tests": <n>, "passing": true}'
ampi win-fetch-add --win build --key done
```

Then write `/workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/1/result.json`:

```
{"rank": 1, "claimed": "<module or null>", "attempts": [<names you tried>],
  "tests_written": <n>, "tests_passing": <n>, "asked_architect": <n>}
```

and run `ampi finalize --note "e5 implementer done"`.

Run `ampi hb --expect-idle 900` before long stretches of writing. Do not edit
another agent's module. Do not run git.
