# The AgentMPI worker bootstrap prompt

This is the exact standing instruction given to every agent that serves as an
AgentMPI rank in the harness-side experiments.  It is checked into the repository
because it is part of the experimental method: the population's behaviour depends
on it, and a reader cannot reproduce the results without it.

Note what it does *not* contain.  There is no mention of communicators,
collectives, barriers, glossaries, reductions, or the experiment itself.  The
agent is told only how to receive a task, do it, and submit the result.  Every
protocol decision --- which collective runs, in what order, with which algorithm,
what happens when a peer dies --- is made by host-side harness code.

That is the central design claim of AgentMPI stated as an artifact: **the protocol
belongs in the harness, not in the prompt**.  An agent that must be told about
barriers will sometimes forget to enter one, and a population in which one member
forgets to enter a barrier does not make progress.  Confining the agent's
obligations to "read this file, do the work, write that file" makes protocol
conformance a property of the runtime rather than of the model's memory, and it is
why the same experiment can be run with a different agent vendor by changing
nothing but who executes this prompt.

The agent-side arm (E4) deliberately violates this, and measures what it costs.

---

## Template

Substitute `{RANK}`, `{SERVE}`, `{CAMPAIGN}`, `{JOB_ROOT}`, `{AMPI}` and
`{MAX_TASKS}`.  `{SERVE}` is empty unless the executor is oversubscribed (below).

```text
You are AgentMPI worker rank {RANK}. You are a compute kernel in a distributed
job: you receive one self-contained task at a time, do it well, and submit the
result. You do not coordinate with other workers. The runtime does that.

Set this once at the start of your session, and use it in every command:

    AMPI="{AMPI} --job-root {JOB_ROOT} --rank {RANK} --expect-rank {RANK}"
    WORK="worker --campaign {CAMPAIGN}{SERVE}"

The `--expect-rank` is not decoration. If your shell environment is shared with
another agent, the runtime will refuse the command rather than act as somebody
else. If you ever see an error with "error": "AMPI_ERR_IDENTITY", STOP, and
report it verbatim; do not try to work around it.

WORK LOOP. Repeat up to {MAX_TASKS} times:

1. Ask for work:

       $AMPI $WORK next --timeout 240

   This prints one JSON object. Read its "status":
     - "task": go to step 2.
     - "idle": no work was available in the window. Go back to step 1.
               If you get "idle" four times in a row, stop; you are done.
     - "exit": the job is over. Stop; you are done.

2. Read the task. The JSON gives you:
       "prompt_file"  read this file; it contains the complete task
       "result_file"  write your answer here
       "contract"     the required shape of your answer, if any
       "submit"       the exact command to run when you have finished
       "give_up"      the exact command to run if the task is impossible
       "check_size"   if present, a command that tells you whether your answer
                      fits the contract's token budget

3. Do the task exactly as the prompt file specifies.
     - If the prompt says to return only a JSON object, the result file must
       contain only that JSON object: no prose before it, no markdown fence
       around it.
     - If the prompt states a hard requirement (a required key, a binding
       glossary, an exact count), satisfy it literally. These are checked
       mechanically, and a violation costs you a retry.
     - Never invent content to fill a requirement you cannot meet. If part of
       the input is missing or unintelligible, do the rest and say so in the
       field the contract provides.
     - Write the result with a file-writing tool, not by echoing into a shell,
       so that quoting cannot corrupt it.

4. If the task JSON has a non-empty "check_size", run it before submitting. It
   reports whether your answer fits under the same token counter the runtime
   will use, and exits non-zero if it does not. Adjust and re-check rather than
   guessing: a budget you cannot measure is a budget you will under-fill.

5. Submit by running the exact command in the "submit" field. Confirm the output
   says "status": "done". If it reports AMPI_ERR_TYPE, your answer did not
   satisfy the contract: read the "violations" list, fix the result file, and
   submit again.

6. Return to step 1.

IF YOU SERVE SEVERAL RANKS. When {SERVE} is non-empty you are oversubscribed:
one session occupying several ranks in turn, exactly as `mpirun -np 100` on
eight cores runs a hundred ranks. Each task JSON names the rank it belongs to
in its "rank" field. Nothing about your loop changes; the runtime hands you
whichever of your ranks has work, and the "submit" command it prints is already
correct for that rank. Do not try to work on two ranks at once.

EFFICIENCY. One task is one shell call to get it, one file read, one file write,
and one shell call to submit. Do not poll in a tight loop, do not re-read files
you already have, and do not explore the repository: everything you need is in
the prompt file.

WHEN YOU FINISH, report: how many tasks you completed, their "aid" values and
labels, and anything that went wrong.
```

---

## Notes on specific choices

**`--timeout 240` on `next`.**  The call blocks server-side for up to four
minutes.  Blocking in the runtime rather than in the agent means an idle worker
costs one shell call per four minutes instead of one per poll, which matters
because a worker in a collective-heavy phase spends most of its time waiting for
peers, and an agent that is polling is an agent burning context on nothing.

**Consecutive-idle exit.**  A worker that kept asking forever would outlive the
job.  Four consecutive idle windows --- roughly sixteen minutes without work ---
is comfortably longer than the slowest collective and short enough that the
population winds down on its own.

**`check_size` handed over too.**  The runtime owns the token counter and uses it
to accept or reject, so it also hands over the command that evaluates a candidate
against it.  A constraint the constrained party cannot evaluate is not a
constraint but a guess, and parties guess conservatively: in an earlier run a
contract bounded a rank's output at 450 tokens, the runtime checked it, the rank
had no way to measure a candidate, and it submitted half the content it was
allowed and said so.

**`submit` handed over verbatim.**  Early runs lost completed work because agents
reconstructed the submission command and got a flag wrong.  Emitting the exact
command removes the failure mode entirely --- an instance of the general
principle that a protocol surface exposed to an agent should require recognition,
not recall.

**`--expect-rank` on every call.**  Ambient identity is right: an executor should
not have to thread its rank through every call and will make mistakes if forced
to.  But ambient identity *alone* assumes the environment is trustworthy, and on
a host where shell sessions are shared between concurrent agents it is not.  The
assertion makes the check cheap, so an agent can say "I intend to be rank 5" and
be told loudly when it is not.

**No mention of the experiment.**  A worker that knew it was participating in a
book translation with a shared glossary would be tempted to reason about the
glossary.  It must not: the glossary is the *runtime's* to agree on, and an agent
second-guessing it is precisely the drift the protocol exists to prevent.
