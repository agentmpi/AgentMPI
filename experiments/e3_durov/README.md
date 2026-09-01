# Durov translation harness

This experiment translates the authorized 99-page Russian source of Nikolai
Kononov's *Код Дурова* into English, Simplified Chinese, and Japanese. It uses
AgentMPI as the protocol layer and `BrokerExecutor` as a vendor-neutral pull
queue. Workers only read a self-contained prompt, write the named result file,
and submit it; every collective and RMA operation stays in trusted host code.

## Prepare the corpus

The legacy checkout must contain `extracted/pages/page_001.txt` through
`page_099.txt`, the six research Markdown files named in `prepare_corpus.py`, and
git provenance:

```bash
python experiments/e3_durov/prepare_corpus.py \
  /path/to/durov_code_translation_multi_agent \
  /secure/source/durov_corpus.json
```

The importer copies source page text and research only. It deliberately never
reads `translations/`. The compact JSON records the source repository URL,
commit, original paths, and SHA-256 hashes. An incomplete 001–099 set is rejected.
Use `--source-repo-url` when the checkout has no `origin`.

## Launch

```bash
python experiments/e3_durov/harness.py \
  --name production-01 \
  --source-dir /secure/source \
  --corpus durov_corpus.json \
  --size 32 \
  --executor broker
```

Supported populations are 16, 32, and 64. `--source-dir` and `--run-dir` keep
copyrighted source and generated translations outside the repository when
desired. Start workers using the command template in `launch_plan.json`; workers
may be tied to any model vendor or agent host.

The default strict run uses full quorum, leased broker claims, bounded response
contracts, a 100,000-token per-rank communication ledger, and self-contained
prompts capped at 180,000 characters. Relevant controls are:

- `--task-timeout`, `--claim-ttl`, and `--max-restarts` for lifecycle policy.
- `--quorum` and `--barrier-policy` for missing/failed rank policy.
- `--failure-policy` records whether orchestration should preserve a degraded
  artifact or fail a rank. It is included in launch and report provenance; worker
  supervisors should apply the matching policy when replacing abandoned tasks.
- `--ctx-budget`, `--research-chars`, and `--max-prompt-chars` for hard bounds.

`stub` is a deterministic protocol fixture, not a translation or quality model.
It is locked behind both `--executor stub` and `--test-stub` and should only be
used by tests.

## Protocol and artifacts

The run performs:

1. `bcast` of immutable project provenance/research and `scatter` of all pages.
2. Agent cultural/terminology research requiring URL-backed evidence.
3. `allreduce(union)` of terminology, lifting conflicts for one root arbitration,
   followed by a binding glossary broadcast.
4. RMA `accumulate` of research; CAS-protected review claims.
5. Agent multilingual literary translation of assigned pages.
6. A fenced RMA draft epoch, then review/revision of another rank's draft.
7. An exclusive leased lock, fencing token, and unlock around each reviewed
   final artifact, followed by another window fence and a policy barrier.
8. A bounded `gather` manifest and host-side JSONL assembly.

Each run contains:

- `provenance.json`: corpus digest, source URL/commit, runtime, executor.
- `launch_plan.json`: page mapping, worker command, bounds, lifecycle policies.
- `out/rank_NNN.json`: that rank's research, draft, peer review, and reviewed work.
- `assembled.jsonl`: one source page per line, ordered 1–99.
- `report.json`, `harness.json`, and `harness.trace.jsonl`: outcomes and trace.
- `broker/`: immutable prompts and worker result files.

No generated translation is committed by this experiment. Keep production run
directories in access-controlled storage appropriate for the licensed source.
