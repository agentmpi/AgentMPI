# AgentMPI trace dashboard

This is the Jumpshot-style timeline viewer imported from
`cursor/opus_2` (`bf0da86`) and adapted to the consolidated v0.2 runtime.
Message-passing failures are usually shapes in time—late ranks, serialized
fan-in, collective skew, stalled operators—and the dashboard renders those
shapes as one lane per rank.

It includes recorded traces under `public/traces`, so a fresh clone is useful
without a live runtime.

```bash
cd viz
npm ci
npm run dev
# http://127.0.0.1:43117
```

The viewer first requests a live API and falls back to the committed traces.
To inspect current v0.2 SQLite jobs:

```bash
python3 scripts/trace_server.py \
  --runs experiments/ampi/e3_fault/runs-consolidated-v2 \
  --port 43118
cd viz && npm run dev
```

Vite proxies `/api` to port 43118. To export current runs for offline viewing:

```bash
python3 scripts/export_traces.py \
  --runs experiments/ampi/e3_fault/runs-consolidated-v2
```

Export merges into the existing trace index. Pass `--clean` only when
intentionally replacing all recorded traces.

## Panels

- rank-lane timeline with zoom and hover details;
- message, window, lock, and model-call glyphs;
- run-level tokens, cost, context, failures, and latency cards;
- collective algorithm/round/message/fold-depth summary;
- rank state, call count, context occupancy, and suspicion status.

Recorded Opus 2 traces use its earlier event projection. Live traces are
adapted from the v0.2 `event`, `rank`, and `coll` tables by
`scripts/trace_server.py`; both conform to the viewer's `RunDetail` schema.
