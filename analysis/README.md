# Analysis of every AgentMPI experiment run

The paper argues from 500 runs. This directory analyses them individually, which is a different
and in some ways more demanding exercise: a paper selects the evidence that supports its claims,
while a per-run analysis has to account for every run including the ones that failed, the ones
that measured nothing interesting, and the ones that contradict something stated elsewhere.

Coverage is complete. All 50 runs outside the collective sweeps have an individual analysis, and
the 450 sweep runs are covered by 23 family analyses — one per `(op, algorithm)` — because a single
sweep run measures one algorithm at one process count and says almost nothing on its own, while the
family says what the sweep was built to establish.

Two kinds of document, because the 500 runs are not 500 comparable things.

## Per-run analyses — `runs/<name>/`

One directory per run, for all 500. Each contains:

| File | What it is |
| --- | --- |
| `analysis.tex` / `analysis.pdf` | the analysis: generated facts plus written interpretation |
| `generated.tex` | derived facts as LaTeX — config, headline metrics, findings, per-rank and collective tables |
| `metrics.json` | every metric computed from the event log |
| `figures/*.pdf` | timeline, concurrency, communication matrix, rank profile, collective validation |
| `viewer.png` | a screenshot of the actual trace viewer dashboard for this run |

## Family analyses — `families/<op>-<algorithm>/`

The 450 collective-validation runs are one algorithm measured at one process count each. Analysed
alone, `coll-bcast-binomial-16` sends seven messages in three rounds in thirty-five milliseconds
and there is nearly nothing to say. Analysed as a family across all measured $p$, the same runs
answer what the sweep was built to ask, so each of the 22 `(op, algorithm)` families also gets a
scaling document.

The family view is not a convenience. It is the only view in which the
`allreduce/recursive_doubling` accounting defect is visible: a constant message shortfall at
exactly the non-power-of-two sizes, invisible in any single run and unmistakable across
twenty-one.

## Reproducing all of it

```bash
make analysis          # metrics, figures, and the generated half of every document
make analysis-shots    # viewer screenshots (needs the viewer serving; see make viz)
make analysis-status   # how many analyses are actually written, not merely present
make analysis-build    # compile the finished documents
```

`make analysis` never overwrites `analysis.tex`, so regenerating derived artefacts after a tooling
change is safe once the prose exists.

## The division of labour, and why

Every number, table, and figure here is generated from the event logs by
`scripts/analyze_run.py` and `scripts/analyze_family.py`. No number in any document was typed by
hand, so each one traces back to `traces/events/<name>.jsonl`.

What is written by hand is the interpretation: what the run was for, why the timeline has the
shape it does, which flagged findings are expected for the configuration and which are defects,
and what would make the reading wrong. `analysis/ANALYST.md` is the contract that specifies this,
and it is worth reading before any individual document, because it explains what these documents
are claiming to be.

`make analysis-status` distinguishes *written* from *unwritten*, and only written counts. A
document that exists, compiles, and still carries its `\TODO` placeholders looks finished in a
directory listing and says nothing, which is worse than one that is absent.

## What the analyses found

Working through the runs individually surfaced defects that analysing them in aggregate did not.
Every one below is now fixed in the code or corrected in the paper, and every one was found by
reading one run's trace closely rather than by scanning all 500.

The pattern is worth naming, because it is the argument for doing this at all. Almost none of these
were wrong *numbers*; they were numbers that meant something other than their name. A share that
could exceed 1. A wall time that included events after the job ended. A rendezvous saving that
counted receives. A round count returned to the caller but never written to the trace. Aggregate
analysis cannot catch that class of error, because the aggregate is computed from the same
mismeasurement.

### Corrections that reached the paper

**A failed run reported as a scaling measurement.** The p=16 translation run died at a timeout with
all sixteen ranks failed and six starved for 5400 s waiting for an agent to claim a task. It sat in
the strong-scaling table contributing a speedup of 0.08 and a Karp–Flatt value of 13.25 — a
serial-*fraction* estimate, which cannot exceed 1. Excluding a non-measurement also raised the USL
fit from R²=0.404 to R²=0.873, which is what the surrounding prose had claimed all along.

**The scaling baseline did less work than the runs divided by it.** The seam-reconciliation phase is
guarded on `comm.size > 1`, so p=1 makes 16 agent calls and p=8 makes 24. The text translated is
identical; the work is not. The reported speedup is therefore conservative — the population achieved
3.46× while doing 1.5× the work — but the fitted σ=0.220 conflated coordination with work
amplification. Normalised, the fit is R²=0.989 at σ=0.025, so about nine tenths of the reported
serial fraction was extra work and only a tenth was coordination.

**Every rendezvous figure in the transport table was doubled**, because `tokens_deferred` counted
both rendezvous sends and non-admitted receives and that benchmark never admits. An earlier check
concluded the table was safe on the strength of the eager rows, which were correctly zero.

**"Every configuration received the same false oracle signal" was false.** Only the two
precise-specification software cells did; the parse was corrected between campaigns. Both had
*already* passed at their first integration, so each spent a second round repairing nothing — which
means the cost columns are not comparable across the pairs even though the pass rates are sound.

### Runtime defects

**A message under-count in recursive-doubling allreduce.** In the non-power-of-two remainder phase
both partners exchange via `sendrecv` but only the odd rank recorded its send. The messages were
delivered correctly; only the count was wrong, which is the more dangerous direction, because the
cost report reads the count and the collective therefore looked cheaper than it was. Visible only in
the family view: exact agreement at 2, 4, 8, 16, 32 and a constant shortfall everywhere else.

**`barrier` never wrote its round count to the trace.** It computed the value and returned it, but
was the only collective of twenty-one to omit the assignment, so every `coll.barrier` event in the
archive reports zero — for the one collective whose payload is a single token and whose entire cost
is latency.

**`shrink_in_place` incremented the communicator generation**, but every survivor calls it, so seven
survivors reached generation 7 with each caching a different value. Since a collective is keyed on
`(ctx, generation, epoch, op)`, the following `agree` split into six collectives of one or two
voters, none reached quorum, and all seven blocked to the timeout.

**`kary` reduce lacked the non-commutativity guard `binomial` has**, so where binomial raised, kary
silently returned a rotated answer at a non-zero root.

**Two quantities shared one name.** `tokens_deferred` accumulated rendezvous sends *and* non-admitted
receives, exceeding `tokens_sent` — impossible for a subset of traffic. Split into `tokens_deferred`
and `tokens_unadmitted`.

**`register` accepted any rank index**, which let a shared worker pool leak ranks into three runs.
The dangerous case is the one that did not happen: registration is an upsert on rank index, so a
worker landing *inside* the world would have taken over the incumbent's lease and mailbox.

**The neighbourhood collectives reported no message count** — the one number that demonstrates why
they exist, given that their whole argument is Θ(degree) rather than Θ(p).

### Cost-model defects

**Operator applications were clamped to the round count**, so flat reduce paid for one fold where its
root performs p−1, putting the time and price terms in contradiction. **`predict_kary` priced p−1
applications** where a k-ary fold performs ⌈(p−1)/(k−1)⌉, erasing k-ary's advantage from the price
axis. **`alltoall/linear` recorded one round**, so the model recommended the schedule the code's own
docstring calls the canonical way to deadlock. With all three corrected the model now recommends
k-ary reduction from p=4 — independently endorsing the paper's own contribution, where before it
contradicted it.

### Two things that were not defects, and one that cannot be settled

Rank identity outliving the agents that fill it is real and measured: the p=8 translation run shows
31 reattachments across incarnations up to 5, so every rank was filled by four or five successive
agent processes while the role, its mailbox, and its context account persisted.

An observability gap let two failed runs look successful: an eager payload refused for exceeding the
unexpected budget emitted nothing, while a *stall* emitted two events, so runs in which all eight
ranks raised `ERR_CONTEXT_OVERFLOW` recorded `ok: true`.

And the runs share a worker pool, so where an ablation's output coincides with its baseline's, the
trace cannot distinguish convergence from an agent remembering. The determinism test — identical
prompts, compared results — points to convergence for one software ablation and to cross-run
dependence for the translation series. `ANALYST.md` records the test and the confound.
