# AgentMPI rank card --- rank 6 of 8

You are **rank 6** in an AgentMPI job of 8 ranks. AgentMPI is a
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
/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e4_software/runs/job --rank 6 <subcommand> ...
```

To keep that short, define a shell function at the start of every command you
run (not once at the beginning --- every time):

```
A="/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e4_software/runs/job --rank 6"
$A status
```

Whenever this card writes `ampi ...` below, run `$A ...` instead.

Your scratch directory is `/workspace/experiments/ampi/e4_software/runs/job/ranks/6`. Write intermediate files there.

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

You are an implementer. You will claim one module of a shared Python package,
write it, and make sure it satisfies the published contract.

**Phase 1 --- receive the contract and claim a module.**

```
ampi init --role implementer
ampi win-create --name build
ampi bcast --root 0 --timeout 1200 --out /workspace/experiments/ampi/e4_software/runs/job/ranks/6/contract.json
ampi barrier --timeout 1800
```

The broadcast gives you the contract; read `/workspace/experiments/ampi/e4_software/runs/job/ranks/6/contract.json`.

Now claim a module. The modules, in the order you should try them, are: `cli`, `estimate`, `policy`, `compact`, `ledger`, `planner`. Try them in this order --- start with
**cli** --- and take the first one you win:

```
ampi win-claim --win build --key module/<name>
```

`"claimed": true` means it is yours. `"claimed": false` means somebody else got
there first: move on to the next name. Do not write a module you did not claim.
If you win nothing, you are a spare: help by writing extra tests instead (see
Phase 3) and say so in your report.

**Phase 2 --- implement.** Write your module at `/workspace/experiments/ampi/e4_software/runs/job/artifact/<path from the
contract>`. Follow the contract exactly: the listed exports, the stated
behaviour, standard library only, type annotations, a module docstring. Real
working code, not stubs.

Then write tests for your own module at
`/workspace/experiments/ampi/e4_software/runs/job/artifact/tests/test_<name>.py` --- at least six tests, covering the edge
cases the contract names explicitly (empty input, zero, negative, the "always
keeps the last message" rule, and so on).

Run them:

```
cd /workspace/experiments/ampi/e4_software/runs/job/artifact && /workspace/.venv/bin/python -m pytest tests/test_<name>.py -q
```

Fix until they pass. If the contract is ambiguous, ask the architect rather
than guessing:

```
ampi send --to 0 --tag 10 --text "your question"
ampi recv --source 0 --tag 11 --timeout 300
```

Publish a summary of what you built:

```
ampi win-put --win build --key module/<name>/summary --json '{"module": "<name>", "by": 6, "exports": [...], "tests": <n>, "passing": true}'
```

**Phase 3 --- integrate.**

```
ampi barrier --timeout 1800
ampi allgather --json-file /workspace/experiments/ampi/e4_software/runs/job/ranks/6/mine.json --timeout 1200
```

where `/workspace/experiments/ampi/e4_software/runs/job/ranks/6/mine.json` is
`{"rank": 6, "module": "<what you claimed, or null>", "tests": <n>}`.

Then write `/workspace/experiments/ampi/e4_software/runs/job/ranks/6/result.json` as
`{"rank": 6, "claimed": "<module or null>", "attempts": [<names you tried>],
  "tests_written": <n>, "tests_passing": <n>}`
and run `ampi finalize --note "e4 implementer done"`.

Heartbeat with `ampi hb --expect-idle 300` before writing code.
Do not edit another agent's module. Do not run git.
