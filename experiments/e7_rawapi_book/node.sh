#!/usr/bin/env bash
# Start this machine's share of an E7 job and return at once.
#
#   experiments/e7_rawapi_book/node.sh <name> <size> <nodes> <node>
#
# Node 0 creates the job and must start first; every other node joins it
# through the gitd daemon.  The driver runs detached (nohup) so the shell that
# started it may exit; its log is work/e7/<name>-node<k>.log and its launch
# record work/e7/<name>/launch/launch-node<k>.json.  Every node passes the same
# flags, which is what the runtime enforces through the job manifest.
set -euo pipefail
NAME=${1:?name}; SIZE=${2:?size}; NODES=${3:?nodes}; NODE=${4:?node}
MODELS=${E7_MODELS:-"deepseek/deepseek-v4-pro-0813,moonshotai/kimi-k3,qwen/qwen3.8-max,google/gemini-3.8-flash,x-ai/grok-4.6,z-ai/glm-5.3,anthropic/claude-sonnet-5,openai/gpt-5.6-sol"}
cd "$(dirname "$0")/../.."
mkdir -p work/e7
export AMPI_GITD_IDLE_S=${AMPI_GITD_IDLE_S:-1800}
nohup .venv/bin/python -m experiments.e7_rawapi_book.harness run \
    --name "$NAME" --size "$SIZE" --nodes "$NODES" --node "$NODE" \
    --executor model --model "$MODELS" --reasoning low --respawn 1 \
    --device gitd --remote https://github.com/agentmpi/AgentMPI \
    --task-timeout 1800 --phase-timeout 7200 --lease 1800 -q \
    > "work/e7/$NAME-node$NODE.log" 2>&1 &
echo "node $NODE of $NODES for $NAME started (pid $!); log work/e7/$NAME-node$NODE.log"
