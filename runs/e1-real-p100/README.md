# e1-real-p100 — an attempt that did not finish

This directory holds the launch record of a hundred-rank translation run and
nothing else, because the run's journal was destroyed before it completed. It is
kept rather than deleted, because the reasons are informative and because one
measurement taken from it is cited in the paper.

## What happened

The agent host caps concurrent subagent sessions at ten. A hundred ranks were
therefore served by executors under oversubscription, in waves: each wave's
executors exit when they reach their task limit, and a fresh wave is added
against exactly the ranks that still have queued work. That part worked, and is
the reason the broker is a pull queue rather than a push — 174 of 199 published
tasks completed across 19 distinct executors, with no harness change and no
restart between waves.

Then two operator errors, neither of them the protocol's. A restart command was
sent to a tmux pane whose foreground process was still running, so it sat in the
shell's input buffer and executed much later, when that process finally exited,
deleting the journal and starting a fresh job. Twice afterwards, a `pkill`
pattern intended for the harness also matched the shell issuing it.

## What survived

`executors.json` — the Cursor subagent identifier for every executor launched
against this run, recorded at launch time, with the wave structure.

`launch_plan.json`, `worker_plan.json`, `prompts/` — the set of ranks the
experiment requested and the exact instruction each executor was given, all
written before anything started.

The identity audit in `experiments/results/e1_provenance.json`, computed from the
live journal and committed at `145d727` before the journal was lost. It is the
run's one cited number: across 199 tasks and 19 executors, the *unchecked*
provenance label drifted on 18 of them, while the *asserted* protocol identity
was refused exactly once — and that executor abandoned the task with an accurate
reason instead of doing another rank's work.

## The completed scale point

`runs/e1-real-p32` and `runs/e1-real-p32-nogloss`: 32 ranks over 8 executors at
oversubscription four, both arms complete, sealed evidence under `evidence/`.
