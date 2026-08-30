#!/usr/bin/env bash
# rank 2 Step 1: block until rank 0 broadcasts the architecture spec.
export PATH="/workspace/bin:$PATH"
export AMPI_ROOT="/workspace/runs/tinyq/ampi"
export AMPI_RANK=2
export AMPI_SIZE=9
cd /workspace/runs/tinyq || exit 1

: > r2-step1.out
while true; do
    printf '[%s] bcast attempt\n' "$(date -u +%H:%M:%S)" >> r2-step1.out
    if ampi bcast --root 0 --out spec.md >> r2-step1.out 2>&1; then
        printf '[%s] BCAST_OK\n' "$(date -u +%H:%M:%S)" >> r2-step1.out
        exit 0
    fi
    sleep 20
done
