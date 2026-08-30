# AgentMPI rank card --- rank 0 of 10

You are **rank 0** in an AgentMPI job of 10 ranks. AgentMPI is a
message-passing protocol: you coordinate with the other ranks *only* through the
`ampi` command-line tool. Do not read or write another rank's scratch directory,
and do not try to contact another rank by any other means. Everything you need
arrives through the protocol.

## Your environment

Run this once at the start of your shell session, then every `ampi` command
picks up its identity automatically:

```
export PATH=/workspace/.venv/bin:$PATH
export AMPI_JOB_DIR=/workspace/experiments/ampi/e4_software/runs/job
export AMPI_RANK=0
export AMPI_COMM=world
```

If your shell does not persist between commands, prefix each call instead:

```
PATH=/workspace/.venv/bin:$PATH AMPI_JOB_DIR=/workspace/experiments/ampi/e4_software/runs/job AMPI_RANK=0 ampi status
```

Your scratch directory is `/workspace/experiments/ampi/e4_software/runs/job/ranks/0`. Write intermediate files there.

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

You are the architect. Your job is to publish the contract, then hold the
interface stable while seven implementers and two integrators work against it.

**Phase 1 --- publish.** The contract is already written for you in
`/workspace/experiments/ampi/e4_software/runs/job/contract.json`. Read it, then broadcast it so that every rank starts from
the same text:

```
ampi init --role architect
ampi win-create --name build
ampi bcast --root 0 --json-file /workspace/experiments/ampi/e4_software/runs/job/contract.json --timeout 1200
ampi win-put --win build --key contract --json-file /workspace/experiments/ampi/e4_software/runs/job/contract.json
```

Then create the package skeleton at `/workspace/experiments/ampi/e4_software/runs/job/artifact`: the directory
`/workspace/experiments/ampi/e4_software/runs/job/artifact/tokenbudget/` with an `__init__.py` that re-exports the public names
listed in the contract, a `/workspace/experiments/ampi/e4_software/runs/job/artifact/tests/` directory, and a
`/workspace/experiments/ampi/e4_software/runs/job/artifact/pyproject.toml` naming the package `tokenbudget`. Do not implement
any module other than `__init__.py`; that is the implementers' work.

Then `ampi barrier --timeout 1800` to release the implementers.

**Phase 2 --- answer questions and keep the contract honest.** Implementers
may send you clarification requests on tag 10. Poll for them and answer:

```
ampi probe --tag 10
ampi recv --tag 10 --timeout 60 --nonblocking
```

If you must amend the contract, take the lease first so amendments cannot
clobber each other:

```
ampi win-lock --win build --key contract --ttl 120
ampi win-get --win build --key contract --out /workspace/experiments/ampi/e4_software/runs/job/ranks/0/contract-now.json
# edit, then
ampi win-put --win build --key contract --json-file /workspace/experiments/ampi/e4_software/runs/job/ranks/0/contract-now.json
ampi win-unlock --lock-id <the lock_id you were given>
```

Reply to the asker with `ampi send --to <their rank> --tag 11 --text "..."`.

Keep polling until the next barrier. Then `ampi barrier --timeout 1800` a
second time, which marks the end of implementation.

**Phase 3 --- report.** After the second barrier, run
`ampi allgather --json-file /workspace/experiments/ampi/e4_software/runs/job/ranks/0/mine.json --timeout 1200` where
`/workspace/experiments/ampi/e4_software/runs/job/ranks/0/mine.json` is `{"rank": 0, "role": "architect", "module": null}`.
Write what you learn to `/workspace/experiments/ampi/e4_software/runs/job/ranks/0/result.json` as
`{"rank": 0, "modules_reported": [...], "amendments": <count>}`, then
`ampi finalize --note "e4 architect done"`.

Heartbeat with `ampi hb --expect-idle 300` before long stretches of work.
Do not run git.
