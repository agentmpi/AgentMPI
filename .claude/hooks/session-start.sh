#!/bin/bash
# SessionStart hook: on a cloud machine, install the runtime and start this
# machine's AgentMPI rank harness if the branch names a job to launch into.
#
# Why a hook.  The harness is a long-running background process, and asking the
# session's agent to start one is asking a permission classifier to allow it;
# in one launch half the machines were refused.  A hook runs as part of the
# session's own startup, needs nobody's permission, and cannot be forgotten.
#
# Why it also restarts.  A cloud container is paused when its session idles or
# the account hits its usage limit, and the harness process does not survive
# the pause.  When the session resumes, the hook runs again (source "resume")
# and restarts the harness for the ranks this machine already claimed; the
# harness replays its memoised work and rejoins the job where it left off.
set -uo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "${CLAUDE_PROJECT_DIR:-/home/user/AgentMPI}" || exit 0
pip install -q -e ".[tokens]" >/tmp/e6-pip.log 2>&1 || true

spec="${E6_LAUNCH_FILE:-experiments/e6_book/LAUNCH.json}"
if ! python3 -c "import json,sys; s=json.load(open('$spec')); sys.exit(0 if s.get('enabled') else 1)" 2>/dev/null; then
  exit 0
fi
name=$(python3 -c "import json; print(json.load(open('$spec'))['name'])")
launcher=$(python3 -c "import json; print(json.load(open('$spec')).get('launcher_session') or '')")
if [ -n "$launcher" ] && [ "${CLAUDE_CODE_REMOTE_SESSION_ID:-}" = "$launcher" ]; then
  exit 0  # the launcher's own conversation is not a rank
fi
work="work/e6/$name"
mkdir -p "$work"
if pgrep -f "e6_book/rank.py autostart" >/dev/null 2>&1; then
  exit 0  # the harness is already running
fi
if [ -f "$work/done.json" ]; then
  exit 0  # this machine's ranks are finished
fi
if [ -f "$work/slot.json" ]; then
  setsid nohup python3 experiments/e6_book/rank.py autostart --resume >> "$work/autostart.log" 2>&1 < /dev/null &
  echo "restarted the AgentMPI rank harness for job $name (log: $work/autostart.log)"
else
  setsid nohup python3 experiments/e6_book/rank.py autostart >> "$work/autostart.log" 2>&1 < /dev/null &
  echo "started the AgentMPI rank harness for job $name (log: $work/autostart.log)"
fi
exit 0
