#!/bin/bash
# Run one ampi collective in the background, recording its output.
# usage: run_step.sh <label> <ampi args...>
export PATH="/workspace/bin:$PATH"
export AMPI_ROOT="/workspace/runs/tinyq/ampi"
export AMPI_RANK=8
export AMPI_SIZE=9
cd /workspace/runs/tinyq || exit 1
label="$1"; shift
out="/tmp/ampi8-${label}.out"
: > "$out"
ampi "$@" >> "$out" 2>&1
echo "EXIT=$?" >> "$out"
