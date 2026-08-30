# Literature survey notes

Each file is a dossier produced by a research subagent, ending in a `## BibTeX`
section. `scripts/build_bib.py` merges those sections (plus the hand-curated
`paper/refs-extra.bib`) into `paper/refs.bib`, preferring the most complete entry
where keys collide and reporting genuine conflicts.

| File | Topic | Status |
|---|---|---|
| `01-mpi-history.md` | MPI's history, standardisation process and design philosophy | complete, 92 entries |
| `02a-collective-algorithms.md` | Collective algorithms with published cost formulas | complete, 26 entries |
| `03a-mpi-matching-protocols.md` | Matching semantics, eager/rendezvous, progress engine | complete, 31 entries |
| `04a-mpi-fault-tolerance.md` | MPI fault tolerance, FT-MPI, ULFM | **partial** (see below) |
| `04b-failure-theory.md` | Failure detectors, leases, rollback recovery, durable execution, BFT, OTP | complete, 41 entries |
| `05a-classical-acls.md` | KQML, FIPA-ACL, contract net, blackboards, tuple spaces, JADE | complete, 76 entries |
| `05b-modern-agent-protocols.md` | MCP, A2A, and the current orchestration frameworks | complete, 61 entries |

## Gaps, stated plainly

**`04a` is incomplete.** Its subagent was terminated partway through and it
carries no BibTeX section, so the ULFM material it was to supply comes instead
from `04b` (which covers ULFM's design principles and cites
`bland2013ulfm`, `losada2020ulfm`) and from `refs-extra.bib` (`fagg2000ftmpi`,
`gropp2004ft`). The paper's fault-tolerance section is cited from those. What is
missing relative to the brief is the detailed comparison of ULFM against Reinit
and MPI Stages, and published performance numbers for revoke/shrink/agree.

**Two further surveys were never produced.** Subagents assigned to (i) parallel
performance models beyond MPI and (ii) LLM-era research multi-agent systems both
exceeded their time budget. Their material is covered by the curated entries in
`refs-extra.bib`, which is smaller and narrower than a survey would have been.
Specifically thinner than we would like: MPI-3 RMA memory models, derived-datatype
performance guidelines, MPI-4 Sessions and partitioned communication, and the
empirical literature on multi-agent LLM failure taxonomies.

**Verification status.** Several entries are flagged in-file as `[UNVERIFIED]` or
carry `% CHECK:` notes on specific fields, and `05a` includes a priority
verification queue. `build_bib.py` reports duplicate works filed under different
keys. None of this is resolved, and it must be before submission.
