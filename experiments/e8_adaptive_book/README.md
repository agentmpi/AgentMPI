# E8: the book by an adaptive population

Sixteen process ranks over two machines translate the same book as E7, without
phases after the glossary. Every rank has a home block of about six pages; a
rank that finishes a page claims the next from a **work pool** (spec S9.5),
its own block first and then the block with the most left; a seam between two
finished pages is work for whichever rank is free; a settled term is published
with one atomic union and every later translation reads it. The root hands out
the glossary with a **nonblocking broadcast** (S7.4) and goes straight to work.

`DESIGN.md` says what is protocol and what is harness, and why the pool is the
former. The short version: exclusivity, the dead holder, the dependency and the
end of the job have nothing of the book in them, so the runtime owns them; what
an item is, which items exist and in what order a rank prefers them, the
harness owns.

## Running it

```bash
# a surrogate population, sixteen ranks in threads, a minute
python -m experiments.e8_adaptive_book.harness run --name e8-stub-p16 --size 16 --executor stub --launch threads

# the real thing over two machines: node 0 creates the job, node 1 joins it
bash experiments/e8_adaptive_book/node.sh e8-rawapi-p16 16 2 0
bash experiments/e8_adaptive_book/node.sh e8-rawapi-p16 16 2 1

# afterwards, on node 0
python -m experiments.e7_rawapi_book.seal e8-rawapi-p16 --work-dir work/e8/e8-rawapi-p16
python -m experiments.e8_adaptive_book.analyze e8-rawapi-p16 --against e7-rawapi-p16
```

The analysis writes `runs/<name>/analysis_e8/`: per-rank pages (own and
stolen), seams, model minutes, minutes waiting for work and minutes blocked in
collectives; a timeline with one row per rank; and a table against the E7 run
of the same size. `python -m ampitools.calls runs/<name>/harness.trace.jsonl
--rank 3` folds a rank's model exchanges back into conversations, as for E7.

## What the pool changes in the trace

Every claim is a `pool.claim` event with the item, whether it was the rank's
preferred group, and how long the rank waited for it; a claim taken over from a
convicted holder is preceded by `pool.reclaim`; a rank with nothing to do
records `pool.wait`. Those three are the experiment's instrument: the idle time
E7 spent at barriers is, in E8, either gone or named.
