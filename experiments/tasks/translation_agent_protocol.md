# Translation executor protocol

This prompt is instantiated with `{rank}`, `{db}`, and `{session}`. Each
executor is a real Cursor subagent and all task/result transfer uses the
AgentMPI SQLite binding.

1. Run:

   ```bash
   /workspace/.venv/bin/agentmpi join \
     --db {db} --session {session} --rank {rank}
   ```

2. Enter the style-guide broadcast as a non-root rank:

   ```bash
   /workspace/.venv/bin/agentmpi bcast \
     --db {db} --session {session} --rank {rank} \
     --root 0 --timeout 900
   ```

3. Enter scatter as a non-root rank to receive exactly one passage:

   ```bash
   /workspace/.venv/bin/agentmpi scatter \
     --db {db} --session {session} --rank {rank} \
     --root 0 --timeout 900
   ```

4. Act as a native French literary translator. Apply the broadcast contract to
   the scattered passage. Write a unique JSON file
   `experiments/results/translation/draft-rank-{rank}.json` with:

   ```json
   {
     "rank": 1,
     "passage_id": "alice-01",
     "translation": "...",
     "terminology": {"source term": "chosen French term"},
     "uncertainties": [],
     "self_check": {
       "meaning_preserved": true,
       "paragraphs_preserved": true,
       "glossary_followed": true
     }
   }
   ```

5. Contribute that file to rank zero:

   ```bash
   /workspace/.venv/bin/agentmpi gather \
     --db {db} --session {session} --rank {rank} \
     --root 0 --json-file experiments/results/translation/draft-rank-{rank}.json \
     --timeout 900
   ```

6. Finalize the rank. Do not edit another rank's files or the source text.

The reviewer prompt follows the same join/broadcast/scatter/gather sequence and
writes `review-rank-{rank}.json`. Reviewers report omissions, additions,
grammar, glossary violations, cross-boundary continuity, and a complete revised
translation for each assigned passage.

