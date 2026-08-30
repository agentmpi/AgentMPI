#!/usr/bin/env bash
# Reproduce every result in the paper that does not require an LLM agent host.
#
# The multi-agent application runs (E1, E2) need a host that can run several
# agents concurrently; this script prepares their jobs and prints the launch
# plans, but cannot start the agents. Everything else -- the conformance suite,
# the microbenchmarks, the figures, the bibliography and the paper -- is fully
# reproducible here.
#
# Usage:  bash scripts/reproduce.sh [--quick]
set -euo pipefail

QUICK=0
[[ "${1:-}" == "--quick" ]] && QUICK=1

cd "$(dirname "$0")/.."
ROOT="$PWD"
export PATH="$HOME/.local/bin:$PATH"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1. Install"
python3 -m pip install -e . --quiet
python3 -m pip install --quiet pytest tiktoken matplotlib numpy || \
  echo "  (optional deps unavailable; the runtime falls back to its structural token estimator)"

say "2. Conformance suite"
# These drive the `ampi` CLI as an agent would, in subprocesses, so they exercise
# the binding rather than the Python API.
python3 -m pytest tests/ -q

say "3. Lint"
python3 -m ruff check ampi/ experiments/ scripts/ tests/ || true

say "4. Protocol walkthrough fixture (stub executors)"
# Exercises every mechanism end to end: broadcast, scatter, an agent-evaluated
# reduction tree, a window with atomic accumulate, an injected failure, and
# revoke/shrink recovery.
python3 scripts/demo.py --np 12 --out runs/demo

say "5. Microbenchmarks"
if [[ $QUICK -eq 1 ]]; then
  python3 -m ampi.cli bench all --np 16 --reps 5 \
    --sizes 8,128,2048,32768 --merge-cost 0.1 \
    --workdir /tmp/ampi-bench --out results/microbench.json
else
  # Full sweep: latency regression, collective volume to P=128, context cost,
  # barrier scaling, matching cost. Takes roughly 20 minutes.
  python3 -m ampi.cli bench all --np 128 --reps 15 \
    --sizes 8,32,128,512,2048,8192,32768 --merge-cost 0.25 --procs 128 \
    --workdir /tmp/ampi-bench --out results/microbench.json
fi

say "6. Fault-tolerance audit"
# Analyses whichever fault-tolerance run is present.
for d in runs/e1_ampi runs/demo; do
  if [[ -f "$d/.ampi/journal.db" ]]; then
    python3 experiments/e3_faults/metrics.py "$d" --out results/e3_metrics.json && break
  fi
done

say "7. Experiment metrics (if agent runs are present)"
if [[ -f runs/e1b_ampi/experiment.json && -f runs/e1b_naive/experiment.json ]]; then
  python3 experiments/e1_translation/metrics.py runs/e1b_ampi runs/e1b_naive \
    --out results/e1_metrics.json
else
  echo "  no E1 agent runs found; skipping"
fi
if [[ -d runs/e2_ampi/project && -d runs/e2_naive/project ]]; then
  python3 experiments/e2_codev/grade.py runs/e2_ampi runs/e2_naive \
    --out results/e2_grade.json
elif [[ -d runs/e2_ampi/project ]]; then
  python3 experiments/e2_codev/grade.py runs/e2_ampi --out results/e2_grade.json
else
  echo "  no E2 agent runs found; skipping"
fi
if [[ -d runs/e2_ampi/project && -d runs/e2_naive/project ]]; then
  # Differential testing: the held-out suite saturated (both arms 174/174), so the
  # arms are separated by generating programs from a grammar written against the
  # spec and comparing. Symmetric in the arms by construction; seeded.
  N=$([[ $QUICK -eq 1 ]] && echo 300 || echo 2500)
  python3 experiments/e2_codev/differential.py runs/e2_ampi runs/e2_naive \
    --n "$N" --seed 11 --out results/e2_differential.json | tail -12
fi

say "8. Paper artefacts"
python3 scripts/build_bib.py
python3 scripts/make_macros.py
python3 scripts/figures.py
python3 scripts/check_tex.py || echo "  (structural problems above; usually a pending experiment)"

say "9. Build the paper"
if command -v latexmk >/dev/null 2>&1; then
  (cd paper && latexmk -pdf -interaction=nonstopmode main.tex >/dev/null && echo "  paper/main.pdf")
else
  echo "  no LaTeX toolchain found. Install texlive-latex-recommended,"
  echo "  texlive-latex-extra, texlive-bibtex-extra and latexmk, then:"
  echo "      cd paper && latexmk -pdf main.tex"
fi

say "10. Prepare the multi-agent experiments (launch requires an agent host)"
if [[ ! -f /tmp/gb97.txt ]]; then
  curl -sL --max-time 60 https://www.gutenberg.org/cache/epub/97/pg97.txt \
    -o /tmp/gb97.txt || echo "  could not fetch the corpus; E1 preparation skipped"
fi
if [[ -f /tmp/gb97.txt ]]; then
  for arm in ampi naive; do
    python3 experiments/e1_translation/prepare.py --arm "$arm" --np 8 \
      --out "runs/repro_e1_$arm" >/dev/null
    echo "  E1 $arm: runs/repro_e1_$arm/launch_plan.json"
  done
fi
for arm in ampi naive; do
  python3 experiments/e2_codev/prepare.py --arm "$arm" --np 8 \
    --out "runs/repro_e2_$arm" >/dev/null
  echo "  E2 $arm: runs/repro_e2_$arm/launch_plan.json"
done

cat <<'EOF'

Each launch plan names one prompt file per rank. To run an experiment, start one
LLM agent per rank with these environment variables set:

    AMPI_ROOT=<the run directory>   AMPI_RANK=<the rank number>

and instruct it to read and execute its prompt file. The agents coordinate only
through `ampi`; there is no orchestrator.

To watch a run:
    ampi status --job-root <run>
    ampi trace  --job-root <run> --timeline
    ampi serve  --runs runs --port 47913     # then: cd viewer && npm run dev
EOF

say "Done"
