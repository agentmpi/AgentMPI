# Codex environment for AgentMPI

This directory preserves the repository-specific environment described during
the initial cloud setup: install the AgentMPI development dependencies and the
Codex CLI, then run multiple Codex processes as AgentMPI ranks.

## Codex cloud

Create or edit the repository's environment in Codex settings and use the
contents of [`.codex/setup.sh`](setup.sh) as its setup script. Setup scripts run
before the agent phase and may use the network. Authentication must be supplied
through the environment's supported credential mechanism; never commit an API
key or personal access token.

The hosted environment controls container count, CPU, memory, and agent
concurrency. This repository cannot increase those limits. AgentMPI does not
require one container per rank: the launcher runs ranks as processes and gives
each one an isolated Git worktree.

## Local or terminal use

Run the setup once, then authenticate interactively:

```bash
.codex/setup.sh
codex login
```

Launch four ranks with the checked-in bootstrap prompt:

```bash
scripts/launch_codex_ranks.sh 4
```

Supply a task-specific prompt as the second argument:

```bash
scripts/launch_codex_ranks.sh 4 /absolute/path/to/task.md
```

Optional controls:

```bash
CODEX_EPHEMERAL=1 scripts/launch_codex_ranks.sh 4
CODEX_MODEL=<model-id> scripts/launch_codex_ranks.sh 4
AMPI_CODEX_ROOT=/tmp/my-run scripts/launch_codex_ranks.sh 4
```

Start with four ranks in a small cloud workspace. Increase the number only
after measuring account concurrency, rate limits, token use, memory, and CPU.
The provider-side concurrency limit, rather than AgentMPI, usually determines
how many model-backed ranks can make progress simultaneously.

## Files and cleanup

Every launch creates:

```text
.codex/runs/<UTC timestamp>/
  job/         shared AgentMPI state
  logs/        one Codex log per rank
  worktrees/   one detached Git worktree per rank
```

Git records worktree administrative entries even though the directories are
ignored. After inspecting a completed run, remove it with:

```bash
git worktree list
git worktree remove --force .codex/runs/<timestamp>/worktrees/rank-0
# Repeat for the remaining ranks, then:
rm -rf .codex/runs/<timestamp>
git worktree prune
```

Do not point two writing Codex processes at the same worktree. Do not use
`--dangerously-bypass-approvals-and-sandbox` unless every process is already
isolated by an external container or virtual machine.
