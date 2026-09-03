# Data policy for E6

The source is a commercial book in copyright: N. V. Kononov, *Код Дурова.
Реальная история «ВКонтакте» и ее создателя* (Mann, Ivanov & Ferber, 2013,
ISBN 978-5-91657-546-0), read from the page extraction published by the legacy
translation project this experiment replaces, pinned to one commit.

What this repository carries from a run:

* the protocol evidence — launch plan, configuration, trace, diagnosis, analysis;
* a manifest of the partition — page and paragraph ranges, sizes, a digest of
  each rank's bytes — so a reader can verify that two runs cut the same book;
* the population's glossary, research findings and amendment ledger, which are
  a reference work *about* the book and its own scholarly output;
* a sample of a few paragraphs from page 13, the page the legacy project itself
  published as its worked example, so the two can be compared.

What it does not carry: the source text, the per-call prompts and replies (which
quote it), the per-rank drafts, or the assembled translation. These are written
under `work/`, which is untracked, and `corpus.write_manifest` refuses to
serialise segment text so the rule cannot be violated by adding a field.
