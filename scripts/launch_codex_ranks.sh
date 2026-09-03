#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/launch_codex_ranks.sh SIZE [PROMPT_FILE]

Launch SIZE non-interactive Codex processes as AgentMPI ranks. Each rank gets a
detached Git worktree, while all ranks share one SQLite-backed AgentMPI job.

Environment variables:
  AMPI_CODEX_ROOT   Runtime directory (default: .codex/runs/<timestamp>)
  CODEX_MODEL       Optional model passed to `codex exec --model`
  CODEX_EPHEMERAL   Set to 1 to avoid saving Codex sessions
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
fi

size=${1:-}
prompt_file=${2:-.codex/rank-prompt.md}
if [[ ! $size =~ ^[1-9][0-9]*$ ]]; then
  usage >&2
  exit 2
fi

repo=$(git rev-parse --show-toplevel)
prompt_file=$(realpath "$prompt_file")
if [[ ! -f $prompt_file ]]; then
  echo "Prompt file not found: $prompt_file" >&2
  exit 2
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "Codex is not installed; run .codex/setup.sh first." >&2
  exit 2
fi
if ! codex login status >/dev/null 2>&1; then
  echo "Codex is not authenticated; run 'codex login' first." >&2
  exit 2
fi

venv=${VIRTUAL_ENV:-$repo/.venv}
ampi=$venv/bin/ampi
if [[ ! -x $ampi ]]; then
  echo "AgentMPI is not installed; run .codex/setup.sh first." >&2
  exit 2
fi

run_root=${AMPI_CODEX_ROOT:-$repo/.codex/runs/$(date -u +%Y%m%dT%H%M%SZ)}
job_root=$run_root/job
worktree_root=$run_root/worktrees
log_root=$run_root/logs
mkdir -p "$worktree_root" "$log_root"
"$ampi" new --root "$job_root" --size "$size" --device sqlite

pids=()
for ((rank = 0; rank < size; rank++)); do
  worktree=$worktree_root/rank-$rank
  git -C "$repo" worktree add --detach "$worktree" HEAD >/dev/null

  args=(exec --cd "$worktree" --add-dir "$job_root" --sandbox workspace-write)
  [[ ${CODEX_EPHEMERAL:-0} == 1 ]] && args+=(--ephemeral)
  [[ -n ${CODEX_MODEL:-} ]] && args+=(--model "$CODEX_MODEL")

  (
    export AMPI_ROOT=$job_root AMPI_RANK=$rank AMPI_SIZE=$size
    export PATH="$venv/bin:$PATH"
    codex "${args[@]}" - < "$prompt_file"
  ) >"$log_root/rank-$rank.log" 2>&1 &
  pids+=("$!")
done

printf 'Launched %d ranks\nJob: %s\nLogs: %s\n' "$size" "$job_root" "$log_root"

status=0
for pid in "${pids[@]}"; do
  wait "$pid" || status=1
done

printf 'Ranks finished with aggregate status %d\n' "$status"
exit "$status"
