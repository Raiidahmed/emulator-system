#!/usr/bin/env bash
# start-impl.sh — hand a markdown plan to opencode on a local LM Studio model.
# Reloads the model fresh each run (single slot) and logs real token/context
# usage so you can right-size the context window later.
#
# Usage:
#   ./start-impl.sh docs/plans/favorites.md
#   ./start-impl.sh docs/plans/favorites.md lmstudio/<some-other-model>
#
# Model precedence: $2  >  $OPENCODE_IMPL_MODEL  >  DEFAULT_MODEL below.
# Env knobs: IMPL_CONTEXT IMPL_PARALLEL IMPL_GPU IMPL_TTL IMPL_LOG_DIR

set -euo pipefail

# Local LM Studio model (Qwen3.6 27B) configured in ~/.config/opencode/opencode.jsonc
DEFAULT_MODEL="lmstudio/qwen/qwen3.6-27b"
IMPL_CONTEXT="${IMPL_CONTEXT:-262144}"
IMPL_PARALLEL="${IMPL_PARALLEL:-1}"
IMPL_GPU="${IMPL_GPU:-max}"
IMPL_TTL="${IMPL_TTL:-3600}"
IMPL_LOG_DIR="${IMPL_LOG_DIR:-logs}"

PLAN_FILE="${1:-}"
MODEL="${2:-${OPENCODE_IMPL_MODEL:-$DEFAULT_MODEL}}"

if [[ -z "$PLAN_FILE" ]]; then
  echo "Usage: $0 <plan.md> [provider/model]" >&2
  exit 1
fi
if [[ ! -f "$PLAN_FILE" ]]; then
  echo "Error: plan file not found: $PLAN_FILE" >&2
  exit 1
fi

# --- LM Studio: targeted reload (fresh session, single slot) ----------------
# Reloading with --parallel 1 cuts the KV-cache footprint ~4x vs a parallel-4
# load and clears any stuck/looping session before we start.
if command -v lms >/dev/null 2>&1 && [[ "$MODEL" == lmstudio/* ]]; then
  LMS_KEY="${MODEL#lmstudio/}"   # lmstudio/qwen/qwen3.6-27b -> qwen/qwen3.6-27b
  echo "Ensuring LM Studio server is up..."
  lms server start >/dev/null 2>&1 || true
  echo "Unloading $LMS_KEY (clears any stuck session)..."
  lms unload "$LMS_KEY" >/dev/null 2>&1 || true
  echo "Reloading $LMS_KEY (ctx=$IMPL_CONTEXT, parallel=$IMPL_PARALLEL, gpu=$IMPL_GPU)..."
  lms load "$LMS_KEY" \
    --context-length "$IMPL_CONTEXT" \
    --parallel "$IMPL_PARALLEL" \
    --gpu "$IMPL_GPU" \
    --ttl "$IMPL_TTL" \
    --yes
else
  echo "Note: 'lms' not found or non-LM Studio model; skipping reload." >&2
fi

# --- usage logging ----------------------------------------------------------
# Stream per-request prediction stats (prompt/predicted token counts) to a file
# so you can see the real context high-water mark vs the configured ceiling.
mkdir -p "$IMPL_LOG_DIR"
USAGE_LOG="$IMPL_LOG_DIR/usage-$(date +%Y%m%d-%H%M%S).log"
LOG_PID=""
if command -v lms >/dev/null 2>&1; then
  echo "Streaming model usage stats -> $USAGE_LOG"
  lms log stream --source model --stats >"$USAGE_LOG" 2>&1 &
  LOG_PID=$!
fi

cleanup() {
  [[ -n "$LOG_PID" ]] && kill "$LOG_PID" >/dev/null 2>&1 || true
  if command -v opencode >/dev/null 2>&1; then
    echo
    echo "=== opencode token usage (this project) ==="
    opencode stats --project "" --models 2>/dev/null || true
  fi
  echo "Per-request stats saved in: $USAGE_LOG"
}
trap cleanup EXIT

# --- prompt + launch --------------------------------------------------------
# Keep the preloaded prompt SMALL: reference the plan file, don't inline it,
# so the local model's context stays well under its limit.
read -r -d '' PROMPT <<EOF || true
Implement the plan in the file: ${PLAN_FILE}

Rules:
- Read ${PLAN_FILE} first, then ONLY the source files it references.
- Apply the edits exactly as specified; do not redesign or expand scope.
- When done, run:  python3 -m pytest -q
- Fix any failures, then give a short summary of what changed.
EOF

echo "Plan : $PLAN_FILE"
echo "Model: $MODEL"
# No --continue / --session, so every run starts a NEW conversation.
echo "Starting a NEW opencode conversation — review the prefilled prompt, then press Enter."
echo

opencode --model "$MODEL" --prompt "$PROMPT"
