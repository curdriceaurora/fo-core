#!/usr/bin/env bash
# save-checkpoint.sh — Save a lightweight progress checkpoint for context-break resilience.
#
# Usage:
#   bash .claude/scripts/save-checkpoint.sh "pr-prep" "phase-3" "Fixing stale doc refs. /audit and /simplify done."
#   bash .claude/scripts/save-checkpoint.sh "implementation" "task-5" "Model registry done. Starting swap semantics."
#   bash .claude/scripts/save-checkpoint.sh --clear   # Remove checkpoint after work completes
#
# The checkpoint is written to .claude/context/checkpoint.md (gitignored).
# On context resume, Claude reads this file to instantly re-orient.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CHECKPOINT_FILE="$REPO_ROOT/.claude/context/checkpoint.md"

if [[ "${1:-}" == "--clear" ]]; then
  rm -f "$CHECKPOINT_FILE"
  echo "✓ Checkpoint cleared"
  exit 0
fi

if [[ $# -lt 3 ]]; then
  echo "Usage: $0 <workflow> <phase> <status-message>"
  echo "       $0 --clear"
  exit 1
fi

WORKFLOW="$1"
PHASE="$2"
STATUS="$3"
BRANCH=$(git branch --show-current)
LAST_COMMIT=$(git log --oneline -1)
DIRTY_FILES=$(git diff --name-only | head -10)
STAGED_FILES=$(git diff --cached --name-only | head -10)

cat > "$CHECKPOINT_FILE" << EOF
# Context Checkpoint

**Saved**: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
**Branch**: $BRANCH
**Last commit**: $LAST_COMMIT

## Current Work

**Workflow**: $WORKFLOW
**Phase**: $PHASE
**Status**: $STATUS

## Working Tree

**Unstaged changes**:
$(if [[ -n "$DIRTY_FILES" ]]; then echo "$DIRTY_FILES"; else echo "(clean)"; fi)

**Staged changes**:
$(if [[ -n "$STAGED_FILES" ]]; then echo "$STAGED_FILES"; else echo "(none)"; fi)

## Resume Instructions

1. Read this file to understand where you left off
2. Check \`git status\` and \`git log --oneline -5\` to confirm state
3. Continue from the phase/step noted above
4. Clear this checkpoint when work completes: \`bash .claude/scripts/save-checkpoint.sh --clear\`
EOF

echo "✓ Checkpoint saved to .claude/context/checkpoint.md"
echo "  Workflow: $WORKFLOW | Phase: $PHASE"
echo "  Status: $STATUS"
