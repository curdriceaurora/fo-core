#!/usr/bin/env bash
##############################################################################
# pr-merge-safe.sh - Merge a PR safely: check staleness, wait for CI, then merge
#
# Implements the safe merge workflow from pr-merge-troubleshooting.md:
#   1. Check if branch is stale → update if needed
#   2. Wait for CI to pass (with timeout)
#   3. Verify all merge conditions
#   4. Squash merge
#
# Usage:
#   bash .claude/scripts/pr-merge-safe.sh <PR_NUMBER> [--no-wait] [--method squash|merge|rebase]
#
# Options:
#   --no-wait     Skip CI wait; merge immediately if conditions are met
#   --method      Merge method: squash (default), merge, or rebase
##############################################################################

set -euo pipefail

PR_NUMBER="${1:-}"
WAIT=true
METHOD="squash"

if [[ -z "$PR_NUMBER" ]]; then
  echo "Usage: $0 <PR_NUMBER> [--no-wait] [--method squash|merge|rebase]" >&2
  exit 1
fi

shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-wait)  WAIT=false; shift ;;
    --method)   METHOD="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Error: invalid PR number: $PR_NUMBER" >&2
  exit 1
fi

case "$METHOD" in
  squash|merge|rebase) ;;
  *) echo "Error: --method must be squash, merge, or rebase" >&2; exit 1 ;;
esac

echo "## Safe Merge: PR #$PR_NUMBER"
echo ""

# Step 1: Check current state
DATA=$(gh pr view "$PR_NUMBER" --json mergeStateStatus,mergeable,reviewDecision,headRefName,baseRefOid,baseRefName)
MERGE_STATE=$(echo "$DATA" | jq -r '.mergeStateStatus')
MERGEABLE=$(echo "$DATA" | jq -r '.mergeable')
REVIEW_DECISION=$(echo "$DATA" | jq -r '.reviewDecision // "NONE"')
BASE_REF=$(echo "$DATA" | jq -r '.baseRefName')
PR_BASE_OID=$(echo "$DATA" | jq -r '.baseRefOid[0:8]')

MAIN_HEAD=$(git log "$BASE_REF" --oneline -1 2>/dev/null | awk '{print $1}' || echo "unknown")

# Step 2: Handle stale branch
if [[ "$PR_BASE_OID" != "$MAIN_HEAD" && "$MAIN_HEAD" != "unknown" ]]; then
  echo "⚠️  Branch is stale ($PR_BASE_OID vs $MAIN_HEAD) — updating..."
  gh pr update-branch "$PR_NUMBER"
  echo "✅ Branch update queued."

  if [[ "$WAIT" == "true" ]]; then
    echo "⏳ Waiting 30s for CI to start..."
    sleep 30
  else
    echo "Skipping wait (--no-wait). Verify CI manually before merging."
    exit 0
  fi
fi

# Step 3: Check review approval
if [[ "$REVIEW_DECISION" != "APPROVED" ]]; then
  echo "❌ Reviews: $REVIEW_DECISION — approval required before merge."
  exit 1
fi

# Step 4: Wait for CI if requested
if [[ "$WAIT" == "true" ]]; then
  echo "⏳ Waiting for CI checks..."
  MAX_WAIT=600
  INTERVAL=20
  ELAPSED=0

  while [[ $ELAPSED -lt $MAX_WAIT ]]; do
    STATE=$(gh pr view "$PR_NUMBER" --json mergeStateStatus -q '.mergeStateStatus')
    case "$STATE" in
      CLEAN|MERGEABLE)
        echo "✅ CI passed ($STATE)"
        break
        ;;
      BLOCKED)
        echo "❌ Still BLOCKED after ${ELAPSED}s — check CI logs."
        exit 1
        ;;
      UNSTABLE)
        echo "❌ CI failing (UNSTABLE) — cannot merge."
        exit 1
        ;;
      *)
        echo "  ... $STATE (${ELAPSED}s elapsed)"
        sleep $INTERVAL
        ELAPSED=$((ELAPSED + INTERVAL))
        ;;
    esac
  done

  if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    echo "❌ Timed out waiting for CI after ${MAX_WAIT}s."
    exit 1
  fi
fi

# Step 5: Final state check
FINAL_STATE=$(gh pr view "$PR_NUMBER" --json mergeStateStatus -q '.mergeStateStatus')
if [[ "$FINAL_STATE" != "CLEAN" && "$FINAL_STATE" != "MERGEABLE" ]]; then
  echo "❌ Cannot merge: mergeStateStatus=$FINAL_STATE"
  echo "Run: bash .claude/scripts/pr-status.sh $PR_NUMBER"
  exit 1
fi

# Step 6: Merge
echo ""
echo "✅ All conditions met — merging PR #$PR_NUMBER ($METHOD)..."
gh pr merge "$PR_NUMBER" "--$METHOD"
echo "✅ Merged."
