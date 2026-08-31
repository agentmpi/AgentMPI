# AgentMPI rank card --- rank 5 of 8

You are **rank 5** in an AgentMPI job of 8 ranks. AgentMPI is a
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
/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e2_semantic_allreduce/runs-tree/job-binomial --rank 5 <subcommand> ...
```

To keep that short, define a shell function at the start of every command you
run (not once at the beginning --- every time):

```
A="/workspace/.venv/bin/ampi --job /workspace/experiments/ampi/e2_semantic_allreduce/runs-tree/job-binomial --rank 5"
$A status
```

Whenever this card writes `ampi ...` below, run `$A ...` instead.

Your scratch directory is `/workspace/experiments/ampi/e2_semantic_allreduce/runs-tree/job-binomial/ranks/5`. Write intermediate files there.

## The protocol, in one page

Every command prints JSON. A command that fails prints JSON with `"ok": false`
and an `error` field naming an AgentMPI error class, and exits non-zero.

```
ampi init --role "reviewer-5"      # join the job. Do this first.
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

You hold one chapter's partial style guide for a book translation, and the job
is to reduce all eight partial guides into one guide that every translator can
follow.

**Step 1.** Run `ampi init --role "reviewer-5"`.

**Step 2.** Your partial style guide is in the file `/workspace/experiments/ampi/e2_semantic_allreduce/runs-tree/job-binomial/ranks/5/notes.json`. Read it.

**Step 3.** Take part in the reduction:

```
ampi reduce --root 0 --op AMPI_SYNTHESIZE --algo binomial --datatype scalar --json-file /workspace/experiments/ampi/e2_semantic_allreduce/runs-tree/job-binomial/ranks/5/notes.json --timeout 2400
```

Under this schedule most ranks are leaves: you send your contribution once and
the call returns immediately with `"status": "ok"` and a null payload. That is
success, not a failure --- only the ranks that sit above you in the reduction
tree are asked to evaluate the operator. If that is you, go to step 3a. If not,
go straight to step 4.

**Step 3a.** If your call printed `"status": "op_required"` instead:

The runtime has reached a step of the reduction that *you* have to evaluate:
it is handing you two partial style guides and asking for their merge.

* When this happens:
  - read the operands (they are in the JSON under `operands`, or in the file
    named by `operands_file` if they were too large to inline);
  - produce the merged style guide yourself. Merge means: keep every rule that
    both operands agree on; where they **conflict**, pick the better-justified
    rule and add a line starting with `RESOLVED:` that names the conflict and
    says why you chose as you did. Never silently drop a conflicting rule.
    Where two rules say the same thing in different words, state it once.
  - write the merged guide, as a JSON object of the same shape as the operands
    (an object with `chapter` and `notes` keys; set `chapter` to a
    comma-separated list of the chapters covered), to a file in your scratch
    directory;
  - run `ampi op-submit --op-token <the op_token you were given> --json-file <that file>`;
  - then run the **identical** `ampi reduce` command again to resume. Do not
    change any of its arguments.
  - Repeat until it prints `"status": "ok"`. You may be asked several times.

**Step 4.** Write your report to `/workspace/experiments/ampi/e2_semantic_allreduce/runs-tree/job-binomial/ranks/5/result.json` as a JSON object with keys:
`{"rank": 5, "algo": "binomial", "upcalls": <how many times you were asked to
evaluate the operator, which may be 0>, "final": <the payload the reduce
returned, or null if it returned null because you are not the root>}`.

**Step 5.** Run `ampi finalize --note "e2 binomial done"`. Do **not** call
`ampi barrier` --- there is no barrier in this experiment, and calling one
would be a collective the other ranks are not making.

If you are asked to evaluate the operator, run `ampi hb --expect-idle 900`
before you start thinking about the merge. Over-estimating is free.

Do not edit any file outside your scratch directory and the one output file
named above. Do not run git.
