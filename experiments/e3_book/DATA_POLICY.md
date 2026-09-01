# E3 data policy

## The corpus is in copyright and is not redistributed here

E3 translates N. V. Kononov, *Код Дурова. Реальная история «ВКонтакте» и ее
создателя* (Mann, Ivanov & Ferber, Moscow, 2013, ISBN 978-5-91657-546-0), by way
of the page extraction published by the legacy project this experiment replaces.

It is the right corpus for the research question. Rendering it is a comparative
literary and historical problem rather than a lexical one: the text is dense with
period slang, institutional names, internet-culture allusions and metaphors that
a Russian reader of 2013 caught without explanation and that no target-language
reader will. So the terminology coupling between segments is *real* rather than
contrived, and that coupling is exactly what AgentMPI's reductions exist to
manage. A corpus whose recurring terms were easy would make the collective look
free.

It is the wrong corpus to vendor. The book is a commercial in-copyright work, and
a complete four-language translation of it is a derivative work of the whole.
Neither belongs in this repository.

## What is committed, and what is not

| Artifact | Committed | Why |
| --- | --- | --- |
| Source text of the book | **no** | in copyright; fetched at run time |
| Full translation produced by a run | **no** | derivative of the whole work |
| `corpus_manifest.json` — per segment: index, page range, character and token counts, SHA-256 | yes | lets a reader verify a run partitioned what it claimed, and that two runs at different scales cut the same book, while containing none of it |
| `glossary.json` — the terms the population researched: what each denotes, what it connoted, register, rationale, sources, and the agreed rendering | yes | the population's own scholarly output. A reference work *about* the book, not a substitute for it, and the artifact the experiment is actually about |
| `*.trace.jsonl`, `metrics.json`, `report.md`, figures | yes | the protocol evidence — the whole point of the run |
| Short excerpts quoted in analysis prose | yes, sparingly | ordinary scholarly quotation, only where a qualitative claim needs it |

## How the policy is enforced

Not by discipline. `corpus.py` separates `Segment.text` from `Segment.metadata()`,
and `write_manifest` inspects what it is about to write and refuses to serialise a
manifest containing segment text. A later change cannot quietly start committing
the corpus by adding a convenience field.

The harness writes fetched pages, rendered prompts, agent results and the
assembled translation into `work/e3/<run>/`, which is untracked. Only the
evidence is promoted into `runs/<run>/`.

## Reproducing a run

The corpus is fetched from the legacy repository at run time and cached under
`work/e3/pages/`:

```bash
python experiments/e3_book/corpus.py --size 16 --work-dir work/e3
```

`corpus_manifest.json` carries a SHA-256 per segment, so a reader who obtains the
same source can confirm byte-for-byte that they are looking at the same partition
this run used, without this repository ever having distributed it.
