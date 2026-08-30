# Paper

`main.tex` is the first AgentMPI academic-paper draft. It deliberately labels
the current agent experiments as pilots and the SQLite implementation as a
semantic oracle.

Build:

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex
```

Before external submission:

1. replace the generic article layout with the target venue's template;
2. complete and freeze replicated macrobenchmarks;
3. generate tables/plots directly from result JSON;
4. verify every bibliography entry against the archival publisher;
5. add independent transport implementations and interoperability results;
6. add formal/model-based validation of collective and repair invariants;
7. remove the date and anonymize artifact links as required.

The committed bibliography avoids relying on search-result prose. Entries
marked as non-archival (specifications and arXiv) are identified by venue type.

