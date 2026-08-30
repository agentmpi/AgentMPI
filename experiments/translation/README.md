# Translation workload

This workload translates Chapter I of Lewis Carroll's public-domain *Alice's
Adventures in Wonderland* from English to Spanish. It is a pipeline rather than
independent prompt fan-out:

```
rank 0 coordinator
  | TASK (10 chunks) + Bcast(glossary)
  v
ranks 1..10 translators
  | DRAFT (two per reviewer)
  v
ranks 11..15 bilingual reviewers
  | REVIEW (one ordered pair per reviewer)
  v
rank 16 editor
  | FINAL
  v
rank 0
```

Every role is a separate Cursor subagent. Agents must obtain content through
AgentMPI and return it through AgentMPI; the shared filesystem is used only for
the runtime and experiment log. The glossary uses a strict `Bcast` collective
over all 17 ranks. Drafts and reviews are point-to-point messages.

## Reproduce

```bash
python3 experiments/translation/prepare.py
```

Start rank 0's glossary broadcast, then start all workers with their recorded
prompts. A translator executes:

```bash
python3 -m agentmpi.cli join --db experiments/results/translation.db \
  --session alice-es --rank 1
python3 -m agentmpi.cli bcast --db experiments/results/translation.db \
  --session alice-es --rank 1 --root 0 --timeout 600
python3 -m agentmpi.cli recv --db experiments/results/translation.db \
  --session alice-es --rank 1 --source 0 --tag TASK --timeout 600
```

It sends `{task_id, chunk_index, source, translation, notes, translator_rank}`
to its assigned reviewer with tag `DRAFT`. Each reviewer receives two drafts,
checks fidelity and glossary consistency, and sends an ordered `chunks` array to
rank 16 with tag `REVIEW`. Rank 16 receives five reviews, performs only
consistency and ordering edits, and sends the final object to rank 0.

```bash
python3 experiments/translation/collect.py
```

The collector emits the final translation, full protocol trace, and mechanical
metrics. There is no claim that glossary checks or character ratios replace
expert human translation assessment.
