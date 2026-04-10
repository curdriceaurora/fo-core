#!/usr/bin/env bash
##############################################################################
# pr-status.sh - Check PR merge readiness at a glance
#
# Usage:
#   bash .claude/scripts/pr-status.sh [PR_NUMBER]
#
# If PR_NUMBER is omitted, detects from current branch.
# Exits 0 if ready to merge, non-zero otherwise.
##############################################################################

set -euo pipefail

PR_NUMBER="${1:-}"

if [[ -z "$PR_NUMBER" ]]; then
  PR_NUMBER=$(gh pr view --json number -q '.number' 2>/dev/null || true)
  if [[ -z "$PR_NUMBER" ]]; then
    echo "Usage: $0 <PR_NUMBER>" >&2
    echo "Or run from a branch with an associated PR." >&2
    exit 1
  fi
fi

if ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Error: invalid PR number: $PR_NUMBER" >&2
  exit 1
fi

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)

echo "## PR #$PR_NUMBER — Merge Readiness"
echo ""

DATA=$(gh pr view "$PR_NUMBER" \
  --json number,title,mergeStateStatus,mergeable,reviewDecision,statusCheckRollup,headRefName,baseRefName,baseRefOid)

TITLE=$(echo "$DATA" | jq -r '.title')
MERGE_STATE=$(echo "$DATA" | jq -r '.mergeStateStatus')
MERGEABLE=$(echo "$DATA" | jq -r '.mergeable')
REVIEW_DECISION=$(echo "$DATA" | jq -r '.reviewDecision // "NONE"')
HEAD_REF=$(echo "$DATA" | jq -r '.headRefName')
BASE_REF=$(echo "$DATA" | jq -r '.baseRefName')
BASE_OID=$(echo "$DATA" | jq -r '.baseRefOid[0:8]')

echo "**$TITLE**"
echo "Branch: \`$HEAD_REF\` → \`$BASE_REF\`"
echo ""

# Staleness check
MAIN_HEAD=$(git log "$BASE_REF" --oneline -1 2>/dev/null | awk '{print $1}' || echo "unknown")
if [[ "$BASE_OID" != "$MAIN_HEAD" && "$MAIN_HEAD" != "unknown" ]]; then
  echo "⚠️  Branch staleness: PR based on $BASE_OID, $BASE_REF is now at $MAIN_HEAD"
  STALE=true
else
  echo "✅ Branch: up to date with $BASE_REF"
  STALE=false
fi

# Merge state
case "$MERGE_STATE" in
  MERGEABLE|CLEAN)
    echo "✅ Merge state: $MERGE_STATE"
    ;;
  BLOCKED)
    echo "❌ Merge state: BLOCKED"
    ;;
  UNSTABLE)
    echo "⚠️  Merge state: UNSTABLE (checks failing)"
    ;;
  *)
    echo "⚠️  Merge state: $MERGE_STATE"
    ;;
esac

# Mergeable
case "$MERGEABLE" in
  MERGEABLE)
    echo "✅ Conflicts: none"
    ;;
  CONFLICTING)
    echo "❌ Conflicts: merge conflicts detected"
    ;;
  UNKNOWN)
    echo "⚠️  Conflicts: unknown (GitHub still computing)"
    ;;
esac

# Review decision
case "$REVIEW_DECISION" in
  APPROVED)
    echo "✅ Reviews: approved"
    ;;
  CHANGES_REQUESTED)
    echo "❌ Reviews: changes requested"
    ;;
  REVIEW_REQUIRED)
    echo "⚠️  Reviews: approval required"
    ;;
  NONE)
    echo "⚠️  Reviews: no review yet"
    ;;
esac

# Status checks
echo ""
echo "### CI Status Checks"
CHECKS=$(echo "$DATA" | jq -r '.statusCheckRollup // [] | .[] | "\(.status) \(.conclusion // "pending") \(.name)"' 2>/dev/null || echo "")

if [[ -z "$CHECKS" ]]; then
  echo "  (no checks found)"
else
  PASS=0; FAIL=0; PENDING=0
  while IFS= read -r line; do
    STATUS=$(echo "$line" | awk '{print $1}')
    CONCLUSION=$(echo "$line" | awk '{print $2}')
    NAME=$(echo "$line" | awk '{$1=$2=""; print $0}' | sed 's/^ *//')
    if [[ "$STATUS" == "COMPLETED" ]]; then
      if [[ "$CONCLUSION" == "SUCCESS" || "$CONCLUSION" == "NEUTRAL" || "$CONCLUSION" == "SKIPPED" ]]; then
        echo "  ✅ $NAME"
        ((PASS++))
      else
        echo "  ❌ $NAME ($CONCLUSION)"
        ((FAIL++))
      fi
    else
      echo "  ⏳ $NAME ($STATUS)"
      ((PENDING++))
    fi
  done <<< "$CHECKS"
  echo ""
  echo "  Passed: $PASS  Failed: $FAIL  Pending: $PENDING"
fi

# Summary verdict
echo ""
echo "---"
READY=true

[[ "$STALE" == "true" ]] && READY=false
[[ "$MERGE_STATE" == "BLOCKED" || "$MERGE_STATE" == "UNSTABLE" ]] && READY=false
[[ "$MERGEABLE" == "CONFLICTING" ]] && READY=false
[[ "$REVIEW_DECISION" != "APPROVED" ]] && READY=false

if [[ "$READY" == "true" ]]; then
  echo "✅ Ready to merge"
  exit 0
else
  echo "❌ Not ready to merge — resolve issues above"
  if [[ "$STALE" == "true" ]]; then
    echo ""
    echo "Tip: run \`bash .claude/scripts/pr-update-branch.sh $PR_NUMBER\` to rebase on $BASE_REF"
  fi
  exit 1
fi
