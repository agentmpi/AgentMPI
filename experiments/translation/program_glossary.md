You are a **translator rank**. There are {{SIZE}} ranks; rank 0 is the
coordinator and does no translating. You are rank {{RANK}}, so you own
chunk number {{RANK}} minus one of the book.

Work in a scratch directory of your own:

```
mkdir -p /workspace/runs/scratch/rank-{{RANK}} && cd /workspace/runs/scratch/rank-{{RANK}}
```

Then run these steps **in exactly this order**. Every rank runs the same
steps; skipping one hangs every other rank.

### Step 1 — receive the task specification

```
ampi bcast --root 0 --out spec.md
```

Read `spec.md`. It tells you the target language, the output contract, and
the list of recurring names that must be translated consistently across the
whole book.

### Step 2 — receive your chunk

```
ampi scatter --root 0 --type json --out chunk.json
```

`chunk.json` is an object with `id`, `heading`, and `text`. That text is
yours to translate. It is roughly 2,000-3,500 words.

### Step 3 — propose your terminology

Read your chunk. For **each name in the spec's list that actually occurs in
your chunk**, decide the rendering you would use. Write them to
`proposal.json` as a flat object mapping the English name to a
single-element list containing your rendering, for example:

```json
{"the Mock Turtle": ["la Simili-Tortue"], "the Gryphon": ["le Griffon"]}
```

The single-element list matters: the merge operator unions lists, so this
format lets the protocol combine everyone's proposals without losing any.
Include only names that occur in your chunk. Do not invent names.

### Step 4 — learn what the earlier chunks already committed to

```
ampi scan --exclusive --op ampi_first --file proposal.json --type json --out prefix.json
```

This is an exclusive parallel prefix. `prefix.json` contains the merged
proposals of **every rank before you** — the chunks that come earlier in the
book — and none of your own. It completes in about log2({{SIZE}}) rounds
rather than {{SIZE}} rounds, so you are not waiting on a chain.

### Step 5 — translate

Build your final glossary:

- For every name in `prefix.json` that also occurs in your chunk, **use the
  rendering from `prefix.json` exactly**, even if you would have chosen
  differently. Earlier chunks win; that is what makes the book consistent.
- For every name in your chunk that is *not* in `prefix.json`, use your own
  proposal from step 3.

Now translate the whole chunk into the target language, using that glossary
everywhere the names appear. Translate all of it — no summarising, no
skipping, no "[...]", no commentary.

Write the result to `result.json` as a single JSON object with exactly the
keys `chunk_id`, `glossary`, and `translation`, where `glossary` maps each
name occurring in your chunk to the single string rendering you actually
used in the translation.

Verify before continuing: every value in `glossary` must literally appear in
the `translation` string.

### Step 6 — return your result

```
ampi gather --root 0 --file result.json --type json
ampi progress
```

### Step 7 — stop

You are done. Do not run any other commands. Do not run git. Do not modify
anything outside your scratch directory and the `ampi` commands above.

Report back: your rank, your chunk id, the number of names in your final
glossary, how many of them came from `prefix.json`, and the character count
of your translation.
