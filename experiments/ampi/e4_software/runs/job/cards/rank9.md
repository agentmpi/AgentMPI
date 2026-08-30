# AgentMPI rank card --- rank 9 of 10

You are **rank 9** in an AgentMPI job of 10 ranks. AgentMPI is a
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
export AMPI_RANK=9
export AMPI_COMM=world
```

If your shell does not persist between commands, prefix each call instead:

```
PATH=/workspace/.venv/bin:$PATH AMPI_JOB_DIR=/workspace/experiments/ampi/e4_software/runs/job AMPI_RANK=9 ampi status
```

Your scratch directory is `/workspace/experiments/ampi/e4_software/runs/job/ranks/9`. Write intermediate files there.

## The protocol, in one page

Every command prints JSON. A command that fails prints JSON with `"ok": false`
and an `error` field naming an AgentMPI error class, and exits non-zero.

```
ampi init --role "integrator"      # join the job. Do this first.
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

You are an integrator. You do not write modules; you make the package work as a
whole and you report honestly on whether it does.

**Phase 1 --- wait for the design.**

```
ampi init --role integrator
ampi win-create --name build
ampi bcast --root 0 --timeout 1200 --out /workspace/experiments/ampi/e4_software/runs/job/ranks/9/contract.json
ampi barrier --timeout 1800
```

While the implementers work, write the **cross-module integration tests** at
`/workspace/experiments/ampi/e4_software/runs/job/artifact/tests/test_integration_9.py`. These must exercise the modules
*together* rather than one at a time --- for example: plan a fan-out budget,
charge a ledger against it, compact a message list to fit what remains, and
assert that the compacted list really does fit. Write at least five such tests
against the contract in `/workspace/experiments/ampi/e4_software/runs/job/ranks/9/contract.json`. They will fail until the
implementers are done; that is expected.

**Phase 2 --- integrate after the barrier.**

```
ampi barrier --timeout 1800
```

Now the implementation is complete. Run the whole suite:

```
cd /workspace/experiments/ampi/e4_software/runs/job/artifact && /workspace/.venv/bin/python -m pytest -q
```

Fix **integration-level** problems only: a missing re-export in `__init__.py`,
an import cycle, a mismatch between two modules' assumptions. If a single
module is simply wrong, do not silently rewrite it --- take the interface lease,
record the defect, and message its author:

```
ampi win-lock --win build --key defects --ttl 120
ampi win-get --win build --key defects --out /workspace/experiments/ampi/e4_software/runs/job/ranks/9/defects.json
# append your finding, then
ampi win-put --win build --key defects --json-file /workspace/experiments/ampi/e4_software/runs/job/ranks/9/defects.json
ampi win-unlock --lock-id <lock_id>
ampi send --to <author rank> --tag 12 --text "defect: ..."
```

**Phase 3 --- report.**

```
ampi allgather --json-file /workspace/experiments/ampi/e4_software/runs/job/ranks/9/mine.json --timeout 1200
```

where `/workspace/experiments/ampi/e4_software/runs/job/ranks/9/mine.json` is
`{"rank": 9, "role": "integrator"}`.

Run the suite one last time and write `/workspace/experiments/ampi/e4_software/runs/job/ranks/9/result.json`:

```
{"rank": 9, "pytest_exit": <code>, "tests_collected": <n>, "tests_passed": <n>,
  "tests_failed": <n>, "defects_filed": <n>, "summary": "<one honest sentence>"}
```

Report what actually happened, including failures. Then
`ampi finalize --note "e4 integrator done"`.

Heartbeat with `ampi hb --expect-idle 300` before long stretches.
Do not run git.
