#!/usr/bin/env bash
# Start this machine's share of an E8 job and return at once.
#
#   experiments/e8_adaptive_book/node.sh <name> <size> <nodes> <node> [rejoin]
#
# Node 0 creates the job and must start first; the other node joins it through
# the gitd daemon.  The driver runs detached; its log is work/e8/<name>-node<k>.log.
set -euo pipefail
NAME=${1:?name}; SIZE=${2:?size}; NODES=${3:?nodes}; NODE=${4:?node}
REJOIN=${5:-}
EXTRA=(); if [ "$REJOIN" = "rejoin" ]; then EXTRA=(--rejoin); fi
MODELS=${E8_MODELS:-"deepseek/deepseek-v4-pro-0813,moonshotai/kimi-k3,qwen/qwen3.8-max,google/gemini-3.8-flash,x-ai/grok-4.6,z-ai/glm-5.3,anthropic/claude-sonnet-5,openai/gpt-5.6-sol"}
cd "$(dirname "$0")/../.."
mkdir -p work/e8
export AMPI_GITD_IDLE_S=${AMPI_GITD_IDLE_S:-1800}
nohup .venv/bin/python -m experiments.e8_adaptive_book.harness run \
    --name "$NAME" --size "$SIZE" --nodes "$NODES" --node "$NODE" \
    --executor model --model "$MODELS" --reasoning low --respawn 1 \
    --device gitd --remote https://github.com/agentmpi/AgentMPI \
    --task-timeout 1800 --phase-timeout 7200 --lease 1800 -q "${EXTRA[@]}" \
    >> "work/e8/$NAME-node$NODE.log" 2>&1 &
echo "node $NODE of $NODES for $NAME started (pid $!); log work/e8/$NAME-node$NODE.log"
