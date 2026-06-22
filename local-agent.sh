#!/usr/bin/env bash
# Iterate a LOCAL model until the test suite passes. No API, no Claude Code.
#
# Prereqs (see README "Unattended local agent"):
#   - LM Studio server running:  lms server start
#   - The coder model loaded:    lms ps   (should show STATUS LOADED/IDLE)
#   - LM_STUDIO_API_BASE set:    export LM_STUDIO_API_BASE=http://localhost:1234/v1
#   - A task scoped in task.md with a clear definition of done.
#
# Run it from an isolated worktree (see the launch block at the bottom of this file).
set -uo pipefail   # NOT -e: a failing test must not abort the loop

# --- config ---------------------------------------------------------------
# Default to the model currently loaded in LM Studio (the fast MoE).
# Swap to the dense lm_studio/qwen/qwen3.6-27b for harder/less-fenced tasks
# (slower per token, but errors compound less when unsupervised).
MODEL="${MODEL:-lm_studio/qwen/qwen3.6-35b-a3b}"
TEST="${TEST:-python3 -m pytest -q}"   # the pass/fail oracle that drives iteration
FILES="${FILES:-src/ tests/}"          # files/dirs Aider may edit (repo map covers the rest)
MAX="${MAX:-12}"                        # cap iterations so a stuck run can't grind forever

# Aider auto-lint DOES iterate non-interactively (auto-test does not — that's
# why the loop below exists). Lint is a free second oracle. If you install ruff,
# uncomment to fold linting into the loop without spending a test iteration:
#   LINT=(--lint-cmd "ruff check src tests")
LINT=()

export LM_STUDIO_API_BASE="${LM_STUDIO_API_BASE:-http://localhost:1234/v1}"

LOG=~/agent-runs/$(date +%F-%H%M).log
mkdir -p ~/agent-runs

notify() {  # local ping — replaces the guide's Telegram bot
  osascript -e "display notification \"$1\" with title \"local-agent\"" 2>/dev/null || true
  say "agent done" 2>/dev/null || true
}

# --- sanity checks --------------------------------------------------------
if [[ ! -f task.md ]]; then
  echo "ERROR: task.md not found in $(pwd). Scope the task first." | tee -a "$LOG"
  exit 1
fi
echo "=== local-agent start $(date) ===" | tee -a "$LOG"
echo "model=$MODEL  test='$TEST'  files='$FILES'  max=$MAX" | tee -a "$LOG"

# --- first pass: the task itself -----------------------------------------
aider --model "$MODEL" --yes-always --auto-lint "${LINT[@]}" \
  --message-file task.md $FILES 2>&1 | tee -a "$LOG"

# --- loop: run tests, feed failures back, until green or MAX -------------
i=0
for i in $(seq 1 "$MAX"); do
  if $TEST >/tmp/agent-test.out 2>&1; then
    echo "PASS on iteration $i" | tee -a "$LOG"
    break
  fi
  echo "FAIL iteration $i — feeding output back" | tee -a "$LOG"
  aider --model "$MODEL" --yes-always --auto-lint "${LINT[@]}" \
    --message "Tests are failing. Fix the code so they pass. Do not edit the tests unless task.md says to. Test output:
$(cat /tmp/agent-test.out)" $FILES 2>&1 | tee -a "$LOG"
done

# --- report ---------------------------------------------------------------
if $TEST >/tmp/agent-test.out 2>&1; then
  RESULT="GREEN after $i iters"
else
  RESULT="STILL RED after $i iters (cap=$MAX)"
fi
echo "=== local-agent done: $RESULT ===" | tee -a "$LOG"
notify "finished — $RESULT"
