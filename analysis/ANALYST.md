# How to analyse an AgentMPI run

This is the working contract for a per-run analysis. Follow it exactly; a hundred and fifty
analyses are being written against it and they have to be comparable.

Your job is **interpretation**. Every number, table, and figure has already been generated. What
does not exist, and what only you can supply, is the reading: what this run was for, what shape
its timeline has and why, which of the flagged findings are expected for this configuration and
which are genuine defects, and what would make your reading wrong.

## What already exists for your run

For a run named `<RUN>`:

| Path | What it is |
| --- | --- |
| `traces/events/<RUN>.jsonl` | the complete event log, one JSON object per line — the primary source |
| `runs/<RUN>/fabric.sqlite` | the SQLite fabric, if you want to query it (note `__` in a run name is a `/` here) |
| `analysis/runs/<RUN>/metrics.json` | every metric computed from the log |
| `analysis/runs/<RUN>/generated.tex` | the facts as LaTeX: config, headline table, findings, figures, per-rank and collective tables |
| `analysis/runs/<RUN>/figures/*.pdf` | timeline, concurrency, comm matrix, rank profile, collective validation |
| `analysis/runs/<RUN>/viewer.png` | a screenshot of the actual trace viewer dashboard for this run |
| `analysis/runs/<RUN>/analysis.tex` | **the file you write** |

Regenerate the derived artefacts at any time with:

```bash
python3 scripts/analyze_run.py --run <RUN>
```

This never touches `analysis.tex`. Do not edit `generated.tex` or `metrics.json` — they are
outputs, and an edit there is a number that no longer traces to the log.

## Required: analyse the trace on the command line

Read the log yourself. `metrics.json` is a summary and summaries hide things — the point of this
step is to find what the summary did not think to compute. At minimum, establish the event
vocabulary, the per-rank shape, and the time structure:

```bash
RUN=real-tr-p8-full

# What kinds of events, how many of each?
python3 -c "
import json
from collections import Counter
evs=[json.loads(l) for l in open('traces/events/$RUN.jsonl')]
print(Counter(e['kind'] for e in evs).most_common())
"

# The full ordered story, with time relative to the first event
python3 -c "
import json
evs=[json.loads(l) for l in open('traces/events/$RUN.jsonl')]
t0=evs[0]['ts']
for e in evs:
    print(f\"{e['ts']-t0:8.2f} r{str(e['rank']):>4} {e['kind']:26s} {json.dumps(e['payload'])[:150]}\")
" | less

# Headline metrics
python3 -c "
import json; d=json.load(open('analysis/runs/$RUN/metrics.json'))
print({k:v for k,v in d.items() if not isinstance(v,(list,dict))})
"
```

Then go further in whatever direction the run demands. Gaps in the timeline, the longest wait and
what ended it, which rank was the straggler at each barrier, whether a message's tokens were
admitted or deferred, what an agent actually returned when a contract was violated. If a number in
`metrics.json` surprises you, verify it against the log before you write about it.

## Required: review the run in the trace viewer

Look at `analysis/runs/<RUN>/viewer.png` — the real dashboard, not a re-plot. Read the timeline
lanes, the stats row, the collectives panel, and the rank health table. The viewer's encoding:
colour is role (blue work, green messages, amber one-sided window operations, grey lifecycle, red
failure, violet recovery) and glyph is duration (bars have extent, ticks and diamonds are
instants).

You are looking for what the metrics cannot express: a rank idle while its peers work, a fan-in
serialising at a root, a barrier whose last arrival is minutes after its first, a lane that is
empty when it should not be. Say what you see, and tie it to the numbers.

If the screenshot is missing or unreadable, regenerate it (the viewer must be serving on the URL
you pass):

```bash
python3 scripts/shoot_viewer.py --url http://127.0.0.1:43191 --match <RUN>
```

## Required: write `analysis.tex`

Replace every `\TODO{...}` with real prose. Keep the section structure and the `\input{generated}`
line. Four sections:

**Abstract.** One paragraph. What the run was for, what it shows, and the single most important
thing a reader should take away. Write it last.

**Reading the timeline.** The visual structure, referring to `Figure~\ref{fig:timeline}` and the
other figures by label. Not a description of the picture — an explanation of it. Why is the gap
there? What is a rank waiting for?

**Interpretation.** What this run establishes. Separate what is true by construction, because the
harness asked for it, from what emerged. For each flagged finding in the generated list, say
whether it is expected for this configuration or a real defect. If the run is one point in a
sweep or an ablation, say what it contributes relative to its siblings.

**Threats to this reading.** What would make you wrong. Single-run noise, a surrogate executor
standing in for a real model, an artefact of how the run was driven rather than a property of the
protocol, a metric that does not mean what it appears to. Be specific; "more runs would help" is
not a threat, it is a truism.

### A known threat to every ablation: the worker pool was shared

The real-agent runs were executed against one persistent pool of Cursor subagents, and ablations
ran after their baselines. So an agent filling a rank in an ablation may have carried context from
the baseline run, and could reproduce text it had already produced — or revised — there.

This is not hypothetical. In `real-tr-p8-nohalo`, which has no seam-reconciliation stage at all,
three of eight output sections are byte-identical to the *revised* output of `real-tr-p8-full`.
The harness is not at fault: that run issued only `terms:*` and `translate:*` calls, no `revise:*`
calls, and each output file equals its own translate result. Either the reconciled rendering was
what an unreconciled agent would produce anyway — a real result about the mechanism's marginal
value — or the agent remembered. **The trace cannot distinguish these.**

If your run is an ablation, check whether its outputs coincide with its baseline's, and if they
do, present both explanations rather than picking one. Compare `runs/<RUN>/output/` against the
baseline's, and check `runs/<RUN>/spool/` for which stages actually ran. Shared *input* artefacts
(scatter units, glossary, term sheets) are identical by design and are not evidence of anything.

**The test that settles it is a determinism check, and you must run it before concluding either
way.** Convergence requires the executor to be near-deterministic. So find a prompt that is a pure
function of its input — in the translation harness `prompt_terms` does not take the glossary, so a
`terms:u<n>` prompt depends only on the unit — confirm the prompt digests really are identical
across runs, and then compare the *results*.

For the translation scaling series that test comes out decisively against convergence. All four
runs issue byte-identical terms prompts (8 of 8 digests match), yet only 6 of 48 cross-run result
pairs agree: `terms:u4` carries the same prompt digest in every run and returns 36, 38, 38 and 34
terms in 913, 956, 956 and 872 bytes. **Identical input does not give identical output here**, so
determinism cannot explain the identities that do occur. And the identities are not scattered: all
11 output pairs where the unit sat on rank 0 in both runs are byte-identical, and none of the other
37 are. Most pointedly, `real-tr-p8-full`'s *revised* `translate:u0` is byte-identical to the
*unrevised* `translate:u0` of p1, p2 and p4. The reading there is cross-run dependence, not
convergence.

An earlier version of this section generalised from a single ablation pair and told analysts the
evidence favoured convergence. That was too strong: the discriminating evidence in that case (store
overlap confined to inputs, zero shared `gather` digests, and one unit where the ablation produced
text existing only inside the baseline's *prompt*) is real and still worth checking, but it does not
license a blanket conclusion, and on a different family of runs the determinism test points the
other way.

So, in order: check whether identical prompts give identical results; look for shared *output*
digests specifically rather than input ones; identify which pipeline stage each shared artefact
belongs to; and see whether the identities correlate with something structural such as rank
position. Then say which explanation the evidence favours **for your runs** and which you cannot
exclude. Note the confound that limits even the strong version: a contiguous split puts the
lowest-numbered units on rank 0 in every configuration, so "same rank" and "early unit" cannot be
separated within one series.

### The quality bar

- **Every claim traces to a number or to the figure.** Cite the value. If you cannot support it,
  cut it.
- **Never invent a number.** If you want a quantity that does not exist, compute it from the log
  and say how.
- **Distinguish measurement from inference.** "Rank 3 was idle for 41 s" is a measurement. "Rank 3
  was idle because the glossary reduce serialised at the root" is an inference, and needs support.
- **Surrogate executors are not models.** A run whose executor is `simulated` or `function` tells
  you about the protocol, not about agent behaviour. Say which you have; `metrics.json` has
  `executors`.
- **A degraded run is still worth analysing.** If it failed, the analysis is about how it failed
  and what the trace shows about the failure. Do not apologise for it and do not bury it.
- **Length follows substance.** A 50-event collective validation run does not need six pages;
  three or four tight paragraphs beat padding. A 686-event software run needs more.
- Write British-or-American English consistently, in complete sentences, no bullet-fragment prose
  in the interpretation sections.

## Required: build the PDF

```bash
cd analysis/runs/<RUN> && pdflatex -interaction=nonstopmode -halt-on-error analysis.tex \
  && pdflatex -interaction=nonstopmode -halt-on-error analysis.tex
```

Two passes, for `\ref` to resolve. It must exit 0 and produce `analysis.pdf`. Fix any LaTeX error
you introduce — underscores in run names need escaping as `\_` in text mode, and `%` starts a
comment.

## Do not

- **Do not run any git command that changes state.** No `commit`, `add`, `checkout`, `switch`,
  `branch`, `reset`, `stash`, `restore`, `merge`, or `rebase`. Reading is fine: `git log`,
  `git show`, `git status`, `git diff`. The parent agent owns all git operations.

  This is the strictest rule here because violating it has already cost real work. Several
  analyses ran concurrently in one working tree; one created and checked out its own branch, so
  the parent's subsequent commits landed there instead of on the session branch, and a later
  `reset` orphaned eight commits — three of the parent's fixes and four completed analyses. The
  content was recoverable only because a later `git add -A` happened to sweep it back in. You
  share this checkout with up to nine other agents and with the parent; a branch change is not
  local to you.

  If you think you need a git operation, say so in your report instead and let the parent do it.
- Do not modify anything outside `analysis/runs/<RUN>/` (and only `analysis.tex` within it).
- Do not edit `src/`, `scripts/`, `traces/`, or `runs/`. If you find a bug in the tooling or the
  runtime, report it with evidence — that is one of the most valuable things you can return — but
  do not fix it yourself, because a concurrent analyst is reading the same file.
- Do not re-run experiments. The traces are the record; they are not to be regenerated.
- Expect the working tree to be dirty from other agents. That is normal and is not yours to clean
  up. Never `git checkout --` or `git restore` a file you did not write.

## If you were assigned a collective *family* instead of a run

A family is one collective algorithm measured across every process count — `bcast/binomial` at
p = 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 24, 32. Its package lives at
`analysis/families/<op>-<algorithm>/` with the same layout: `metrics.json`, `generated.tex`,
`figures/family.pdf`, and the `analysis.tex` you write. Regenerate the derived parts with:

```bash
python3 scripts/analyze_family.py --family <op>/<algorithm>
```

Everything above still applies, with three additions.

**Derive the cost before you compare it.** Read the algorithm's implementation in
`src/agentmpi/algorithms.py` and its closed form in `src/agentmpi/cost.py` (`FORMULAS`). Explain
why the algorithm costs what it costs — where the round count steps and why there — and only then
compare with the measurements. An analysis that just observes agreement is much less useful than
one that explains what agreement confirms.

**Powers of two are the interesting axis.** Most collectives take a different code path when p is
not a power of two, and that path is where implementation defects live. The figure marks the two
classes with different markers for this reason. Say whether the remainder path behaves, and if the
figure shows red crosses (the collective's self-reported count disagreeing with the logged
traffic), that is a defect — investigate it and report it prominently.

**These runs contain no agents.** Wall times are sub-second and dominated by SQLite writes and
event logging. They measure the protocol, not model latency, and any claim about agent behaviour
from these runs would be wrong. Say so in the threats section.

## Report back

Two to four sentences: the single most interesting thing the run shows, any finding you judged to
be a real defect rather than expected behaviour, and confirmation that the PDF built. If you found
something that looks like a bug in AgentMPI itself, say so explicitly and point at the evidence —
that is the most valuable thing you can return.
