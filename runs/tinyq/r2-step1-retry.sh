#!/usr/bin/env bash
# rank 2: keep attempting the Step 1 bcast until collective #1 is repaired.
# A mismatched attempt fails instantly; a healthy one blocks until release.
export PATH="/workspace/bin:$PATH"
export AMPI_ROOT="/workspace/runs/tinyq/ampi"
export AMPI_RANK=2
export AMPI_SIZE=9
cd /workspace/runs/tinyq || exit 1

: > r2-step1.out
attempt=0
while true; do
    attempt=$((attempt + 1))
    printf '[%s] attempt %d\n' "$(date -u +%H:%M:%S)" "$attempt" >> r2-step1.out
    if ampi bcast --root 0 --out spec.md >> r2-step1.out 2>&1; then
        printf '[%s] BCAST_OK after %d attempts\n' "$(date -u +%H:%M:%S)" "$attempt" >> r2-step1.out
        exit 0
    fi
    sleep 30
done
