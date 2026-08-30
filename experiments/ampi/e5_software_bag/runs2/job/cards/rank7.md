# AgentMPI rank card --- rank 7 of 8

You are **rank 7** in an AgentMPI job of 8 ranks. AgentMPI is a
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
/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e5_software_bag/runs2/job --rank 7 <subcommand> ...
```

To keep that short, define a shell function at the start of every command you
run (not once at the beginning --- every time):

```
A="/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e5_software_bag/runs2/job --rank 7"
$A status
```

Whenever this card writes `ampi ...` below, run `$A ...` instead.

Your scratch directory is `/workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/7`. Write intermediate files there.

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

You make the package work as a whole and report honestly on whether it does.
You wait on a counter, never on a rendezvous.

**Step 1.** Join and read the contract:

```
ampi init --role integrator
ampi win-create --name build
ampi win-get --win build --key contract --out /workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/7/contract.json
```

If that reports `"found": false`, wait 20 seconds and retry, up to ten times.

**Step 2.** While the implementers work, write cross-module integration tests
at `/workspace/experiments/ampi/e5_software_bag/runs2/job/artifact/tests/test_integration.py`. These must exercise the modules
*together*, not one at a time: for example plan a fan-out budget, charge a
ledger against it, compact a message list to fit what remains, and assert that
the compacted list really does fit. Write at least five. They will fail until
the implementers finish; that is expected.

**Step 3.** Wait for implementation to finish by polling the counter, not by
calling a collective:

```
ampi win-get --win build --key done
```

Repeat every 30 seconds until it reports 6, or until 25 minutes have passed.
Between polls, run `ampi hb --expect-idle 120`. Also check
`ampi win-list --win build --prefix module/` to see which modules exist.

**Step 4.** Run the whole suite:

```
cd /workspace/experiments/ampi/e5_software_bag/runs2/job/artifact && /workspace/.venv/bin/python -m pytest -q
```

Fix **integration-level** problems only: a missing re-export in
`__init__.py` (take `ampi win-lock --win build --key initpy` first), an import
cycle, a mismatch between two modules' assumptions. If a single module is
simply wrong, do not quietly rewrite it: record the defect and tell its author.

```
ampi win-lock --win build --key defects --ttl 180
ampi win-get --win build --key defects --out /workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/7/defects.json
# append your finding, then write it back
ampi win-put --win build --key defects --json-file /workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/7/defects.json
ampi win-unlock --lock-id <lock_id>
ampi send --to <author rank> --tag 12 --text "defect: ..."
```

**Step 5.** Run the suite one final time and write `/workspace/experiments/ampi/e5_software_bag/runs2/job/ranks/7/result.json`:

```
{"rank": 7, "modules_present": [...], "pytest_exit": <code>,
  "tests_collected": <n>, "tests_passed": <n>, "tests_failed": <n>,
  "defects_filed": <n>, "summary": "<one honest sentence>"}
```

Report what actually happened, including failures. A truthful failure is far
more useful here than a rosy summary. Then
`ampi finalize --note "e5 integrator done"`.

Do not run git.
