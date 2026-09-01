# Analysis of every AgentMPI experiment run

The paper argues from 500 runs. This directory analyses them individually, which is a different
and in some ways more demanding exercise: a paper selects the evidence that supports its claims,
while a per-run analysis has to account for every run including the ones that failed, the ones
that measured nothing interesting, and the ones that contradict something stated elsewhere.

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

Working through the runs individually surfaced four things that analysing them in aggregate did
not, all of which are now fixed in the code or corrected in the paper:

**A message under-count in recursive-doubling allreduce.** In the non-power-of-two remainder
phase both partners exchange via `sendrecv`, but only the odd rank recorded its send, so every
such allreduce under-reported traffic by exactly the remainder. The messages were delivered
correctly; only the count was wrong, which is the more dangerous failure, because the cost report
and calibration read the count and the collective therefore looked cheaper than it was.

**Missing instrumentation on the neighbourhood collectives.** `neighbor_allgather`,
`neighbor_alltoall`, and `halo_exchange` reported their degree but not their message count — the
one number that demonstrates why they exist, given that their entire argument is a cost of
Θ(degree) rather than Θ(p).

**A failed run reported as a scaling measurement.** The p=16 translation run died at the job
timeout with all sixteen ranks failed and six starved for 5400 s waiting for an agent to claim a
task. It was in the paper's strong-scaling table as a data point, contributing a speedup of 0.08
and a Karp–Flatt value of 13.25 — a serial-fraction estimate, which cannot exceed 1. Excluding a
non-measurement also raised the USL fit from R²=0.404 to R²=0.873, which is what the surrounding
prose had claimed all along.

**Rank identity outliving the agents that fill it, measured.** The p=8 translation run shows 31
reattachments across incarnations up to 5: every rank was filled by four or five successive agent
processes while the rank role, its mailbox, and its context account persisted. That is the
project's central lifecycle claim, and it is visible directly in the `rank.init` records rather
than argued for.
